# -*- coding: utf-8 -*-
"""PRD 待办 e：并发批改吞吐 —— 队列、并发上限、耗时记录、队列状态接口（pytest 原生版）"""
import asyncio
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class FakeEngine:
    """mock 引擎（记录并发峰值）"""

    state = {"active": 0, "peak": 0, "done": 0}

    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        state = FakeEngine.state
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        state["done"] += 1
        return {"success": True, "judgement": "correct", "confidence": 0.95,
                "analysis": "OK", "questions": [{"question_no": 1, "judgement": "correct"}],
                "prompt_tokens": 100, "completion_tokens": 50}


class FailEngine(FakeEngine):
    async def grade(self, image_path, question_type, subject, answers=None):
        return {"success": False, "error": "模拟失败"}


@pytest.fixture()
def cq_env(monkeypatch):
    """独立临时库 + 测试客户端，结束恢复原状。"""
    import backend.database as dbmod
    import backend.models.grading_engine as ge
    import backend.routes.grading as gr
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_cq_")
    url = "sqlite:///" + os.path.join(tmp, "test_cq.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()

    # 已 import 的 backend 模块持有各自的 SessionLocal 引用，统一指向临时库
    # （用 monkeypatch 设置，teardown 自动恢复，避免污染后续测试）
    import backend.models.grading_engine as ge
    import backend.models.question_splitter
    import backend.models.review
    import backend.models.teaching_advice
    import backend.routes.grading as gr
    for mod in (ge, backend.models.question_splitter, backend.models.review,
                backend.models.teaching_advice, gr):
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", dbmod.SessionLocal)
        if getattr(getattr(mod, "AppConfig", None), "__module__", "") == "backend.database":
            monkeypatch.setattr(mod, "AppConfig", dbmod.AppConfig)

    monkeypatch.setattr(ge, "GradingEngine", FakeEngine)
    monkeypatch.setattr(gr, "annotate_image", lambda *a, **k: "/tmp/ann.jpg")
    # 重置路由层运行时缓存：信号量绑定旧事件循环会导致新 loop 里 worker 卡死；
    # QUEUE 残留会污染队列统计
    monkeypatch.setattr(gr, "_sem", None, raising=False)
    monkeypatch.setattr(gr, "QUEUE", {}, raising=False)

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


def make_subs(env, n, status="uploaded", **kw):
    from backend.database import Submission

    db = env["dbmod"].SessionLocal()
    for i in range(n):
        db.add(Submission(assignment_id=1, student_id=1,
                          original_image_path="/tmp/x.jpg", status=status, **kw))
    db.commit()
    ids = [s.id for s in db.query(Submission)
           .filter(Submission.status == status).all() if s.id]
    db.close()
    return ids


async def run_batch(payload):
    """直接驱动批量端点：执行后收集全部后台任务跑完"""
    import backend.routes.grading as gr
    from backend.database import SessionLocal, get_db

    db = next(get_db())
    try:
        res = await gr.start_batch(gr.BatchIn(**payload), db=db)
    finally:
        db.close()
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return res


def test_concurrency_config_default_and_bounds(cq_env):
    """并发路数：默认 2，可设 1-4，越界拒绝。"""
    c = cq_env["client"]
    r = c.get("/api/grading/concurrency")
    assert r.status_code == 200 and r.json()["concurrency"] == 2, r.text

    r = c.post("/api/grading/concurrency", json={"value": 4})
    assert r.status_code == 200 and r.json()["concurrency"] == 4, r.text
    assert c.post("/api/grading/concurrency", json={"value": 0}).status_code == 400
    assert c.post("/api/grading/concurrency", json={"value": 5}).status_code == 400
    c.post("/api/grading/concurrency", json={"value": 2})


def test_concurrency_limit_respected(cq_env):
    """6 份、并发 2，峰值不得超过 2，全部完成。"""
    FakeEngine.state = {"active": 0, "peak": 0, "done": 0}
    ids = make_subs(cq_env, 6)
    res = asyncio.run(run_batch({"submission_ids": ids}))
    assert res["success"] and res["count"] == 6 and res["concurrency"] == 2, res
    assert FakeEngine.state["done"] == 6, FakeEngine.state
    assert FakeEngine.state["peak"] == 2, (
        f"并发峰值应为 2（受信号量限制），实际 {FakeEngine.state['peak']}")


def test_queue_status_and_durations(cq_env):
    """队列状态与耗时：全部 completed，含平均耗时与单份耗时。"""
    FakeEngine.state = {"active": 0, "peak": 0, "done": 0}
    ids = make_subs(cq_env, 6)
    asyncio.run(run_batch({"submission_ids": ids}))

    c = cq_env["client"]
    st = c.get("/api/grading/queue-status").json()
    assert st["success"] and st["counts"]["completed"] == 6, st
    assert st["avg_duration_seconds"] and st["avg_duration_seconds"] > 0, st
    for it in st["items"]:
        assert it["state"] == "completed" and it["duration_seconds"] > 0, it

    from backend.database import QuestionResult, Submission
    db = cq_env["dbmod"].SessionLocal()
    subs = {s.id: s for s in db.query(Submission).all()}
    assert all(s.status == "completed" for s in subs.values())
    assert all(s.duration_seconds and s.duration_seconds > 0 for s in subs.values())
    assert db.query(QuestionResult).count() == 6  # 每份 1 题
    db.close()


def test_single_start_records_duration(cq_env):
    """单份批改（HTTP start 接口）也记录耗时。"""
    FakeEngine.state = {"active": 0, "peak": 0, "done": 0}
    c = cq_env["client"]
    (sid,) = make_subs(cq_env, 1)
    r = c.post(f"/api/grading/start/{sid}")
    assert r.status_code == 200 and r.json()["success"], r.text
    assert r.json()["duration_seconds"] > 0, r.json()

    from backend.database import Submission
    db = cq_env["dbmod"].SessionLocal()
    s = db.query(Submission).filter(Submission.id == sid).first()
    assert s.status == "completed" and s.duration_seconds > 0
    db.close()


def test_batch_selection_rules(cq_env):
    """批量入选规则：超限 failed 与已完成不入队；失败的进 failed 队列。"""
    c = cq_env["client"]
    make_subs(cq_env, 1, status="failed", retry_count=3)
    make_subs(cq_env, 1, status="completed")

    monkeypatched = FailEngine
    import backend.models.grading_engine as ge
    orig = ge.GradingEngine
    ge.GradingEngine = monkeypatched
    try:
        res = asyncio.run(run_batch({}))  # 全部待批
        assert res["count"] == 0, f"超限 failed 不应入队: {res}"
    finally:
        ge.GradingEngine = orig

    (sid2,) = make_subs(cq_env, 1)
    ge.GradingEngine = monkeypatched
    try:
        res = asyncio.run(run_batch({"submission_ids": [sid2]}))
        assert res["count"] == 1
    finally:
        ge.GradingEngine = orig

    st = c.get("/api/grading/queue-status").json()
    assert st["counts"]["failed"] == 1, st
    from backend.database import Submission
    db = cq_env["dbmod"].SessionLocal()
    s = db.query(Submission).filter(Submission.id == sid2).first()
    assert s.status == "failed" and s.error_message == "模拟失败" and s.duration_seconds is not None
    db.close()


def test_batch_http_returns_immediately(cq_env):
    """HTTP 层：batch 立即返回队列信息（不阻塞）。"""
    FakeEngine.state = {"active": 0, "peak": 0, "done": 0}
    c = cq_env["client"]
    (sid3,) = make_subs(cq_env, 1)
    r = c.post("/api/grading/batch", json={"submission_ids": [sid3]})
    d = r.json()
    assert r.status_code == 200 and d["success"] and d["count"] == 1 and sid3 in d["queued"], d
    st = c.get("/api/grading/queue-status").json()
    assert any(it["submission_id"] == sid3 for it in st["items"]), st


def test_settings_and_workbench_ui_elements(cq_env):
    """设置页含并发卡片，工作台含批量按钮。"""
    c = cq_env["client"]
    assert "批改并发路数" in c.get("/settings").text
    assert "startBatch" in c.get("/").text
