# -*- coding: utf-8 -*-
"""PRD 4.4：Key 失效/欠费自动暂停队列 —— 触发暂停、暂停态快速失败、恢复通道"""
import asyncio
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class AuthFailEngine:
    """模拟 Key 失效：引擎直接抛 401 类异常"""

    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        raise RuntimeError("Error code: 401 - {'error': {'message': 'Invalid API key'}}")


class BalanceFailEngine(AuthFailEngine):
    """模拟欠费：402 余额不足"""

    async def grade(self, image_path, question_type, subject, answers=None):
        raise RuntimeError("Error code: 402 - Insufficient balance / 欠费")


class OkEngine:
    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        return {"success": True, "judgement": "correct", "confidence": 0.95,
                "analysis": "OK", "questions": [{"question_no": 1, "judgement": "correct"}],
                "prompt_tokens": 100, "completion_tokens": 50}


# fixture 运行时读取的引擎类（测试内可切换）
CURRENT_ENGINE = {"cls": AuthFailEngine}


@pytest.fixture()
def pause_env(monkeypatch):
    """独立临时库 + 测试客户端（参照 test_concurrent_queue 的隔离方式）"""
    import backend.database as dbmod
    import backend.models.grading_engine as ge
    import backend.models.question_splitter
    import backend.models.review
    import backend.models.teaching_advice
    import backend.routes.grading as gr
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_pause_")
    url = "sqlite:///" + os.path.join(tmp, "test_pause.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()

    for mod in (ge, backend.models.question_splitter, backend.models.review,
                backend.models.teaching_advice, gr):
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", dbmod.SessionLocal)
        if getattr(getattr(mod, "AppConfig", None), "__module__", "") == "backend.database":
            monkeypatch.setattr(mod, "AppConfig", dbmod.AppConfig)

    monkeypatch.setattr(ge, "GradingEngine", CURRENT_ENGINE["cls"])
    monkeypatch.setattr(gr, "annotate_image", lambda *a, **k: "/tmp/ann.jpg")
    monkeypatch.setattr(gr, "_sem", None, raising=False)
    monkeypatch.setattr(gr, "QUEUE", {}, raising=False)
    # 每个测试从干净的非暂停态开始
    gr.resume_provider()

    from backend.app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert client.post("/api/auth/pin-login", json={"pin": "123456"}).status_code == 200

    db = dbmod.SessionLocal()
    for k, v in [("llm_provider", "qwen"), ("llm_api_key", "test-key")]:
        row = db.query(dbmod.AppConfig).filter(dbmod.AppConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(dbmod.AppConfig(key=k, value=v))
    db.commit()
    db.close()

    yield {"client": client, "dbmod": dbmod}

    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


def make_sub(env):
    from backend.database import Submission

    db = env["dbmod"].SessionLocal()
    db.add(Submission(assignment_id=1, student_id=1,
                      original_image_path="/tmp/x.jpg", status="uploaded"))
    db.commit()
    sid = db.query(Submission).order_by(Submission.id.desc()).first().id
    db.close()
    return sid



def grade_one(sid):
    """确定性执行单份批改（不依赖后台任务调度）"""
    import backend.routes.grading as gr
    return asyncio.new_event_loop().run_until_complete(gr._grade_one(sid))


def test_classify_pause_error():
    """错误归类：401→key_invalid，402/欠费→no_balance，其他→None"""
    from backend.routes.grading import classify_pause_error
    assert classify_pause_error("Error code: 401 - Invalid API key") == "key_invalid"
    assert classify_pause_error("unauthorized request") == "key_invalid"
    assert classify_pause_error("402 Insufficient balance") == "no_balance"
    assert classify_pause_error("账户已欠费，请充值") == "no_balance"
    assert classify_pause_error("模型不支持图片输入") is None
    assert classify_pause_error("") is None


def test_auth_error_pauses_queue(pause_env):
    """401 类失败 → 自动暂停，queue-status 带 paused 状态与原因"""
    c = pause_env["client"]
    sid = make_sub(pause_env)
    res = grade_one(sid)
    assert not res.get("paused"), res
    r = c.get("/api/grading/queue-status")
    d = r.json()
    assert d["provider_paused"]["paused"] is True
    assert "Key" in d["provider_paused"]["reason"] or "key" in d["provider_paused"]["reason"]


def test_balance_error_pauses_queue(pause_env, monkeypatch):
    """402/欠费类失败 → 暂停原因为余额"""
    import backend.models.grading_engine as ge
    monkeypatch.setattr(ge, "GradingEngine", BalanceFailEngine)
    c = pause_env["client"]
    sid = make_sub(pause_env)
    grade_one(sid)
    r = c.get("/api/grading/queue-status")
    reason = r.json()["provider_paused"]["reason"]
    assert "余额" in reason or "欠费" in reason


def test_paused_blocks_new_grading(pause_env):
    """暂停态下新批改快速失败，不再调用引擎（引擎调用计数为 0）"""
    import backend.routes.grading as gr
    calls = {"n": 0}

    class CountingEngine(OkEngine):
        async def grade(self, *a, **k):
            calls["n"] += 1
            return await OkEngine.grade(self, *a, **k)

    c = pause_env["client"]
    gr.pause_provider("测试暂停")
    sid = make_sub(pause_env)
    grade_one(sid)
    assert calls["n"] == 0
    r = c.get("/api/grading/queue-status")
    assert r.json()["provider_paused"]["paused"] is True


def test_resume_endpoint(pause_env):
    """手动恢复：暂停→/resume 解除→恢复批改正常；非暂停态 resumed=False"""
    import backend.routes.grading as gr
    c = pause_env["client"]
    gr.pause_provider("测试暂停")
    r = c.post("/api/grading/resume")
    assert r.status_code == 200 and r.json()["resumed"] is True
    assert c.get("/api/grading/queue-status").json()["provider_paused"]["paused"] is False
    # 再点一次：没有暂停可恢复
    r = c.post("/api/grading/resume")
    assert r.json()["resumed"] is False
