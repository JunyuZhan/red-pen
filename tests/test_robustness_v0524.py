# -*- coding: utf-8 -*-
"""第七轮健壮性（v0.5.24）：批改取消 + 批改整体超时。

- POST /api/grading/cancel/{id}：排队/处理中任务可取消，取消后提交回到 uploaded 可重批
- 单份批改整体超时包裹：模型挂死不再卡死队列，落 failed 带友好提示
- 超时配置 GET/POST /api/grading/timeout，默认 300，范围 60-900
- 无任务可取消时 400，不存在的提交 404
"""
import asyncio
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class SlowEngine:
    """挂死的引擎（永不返回，供超时/取消测试）"""

    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        await asyncio.sleep(600)
        return {"success": True, "questions": []}


@pytest.fixture()
def rx_env(monkeypatch):
    import backend.database as dbmod
    import backend.models.grading_engine as ge
    import backend.models.question_splitter
    import backend.models.review
    import backend.models.teaching_advice
    import backend.routes.grading as gr
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_rx_")
    url = "sqlite:///" + os.path.join(tmp, "test_rx.db")
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

    monkeypatch.setattr(ge, "GradingEngine", SlowEngine)
    monkeypatch.setattr(gr, "annotate_image", lambda *a, **k: "/tmp/ann.jpg")
    monkeypatch.setattr(gr, "_sem", None, raising=False)
    monkeypatch.setattr(gr, "QUEUE", {}, raising=False)
    monkeypatch.setattr(gr, "_TASK_BY_SID", {}, raising=False)

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

    # 清理可能仍在运行的批改任务（无运行事件循环时跳过）
    try:
        strays = [t for t in asyncio.all_tasks()
                  if t is not asyncio.current_task() and not t.done()]
        for t in strays:
            t.cancel()
    except RuntimeError:
        pass
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


def make_sub(env, status="uploaded"):
    from backend.database import Submission

    db = env["dbmod"].SessionLocal()
    db.add(Submission(assignment_id=1, student_id=1,
                      original_image_path="/tmp/x.jpg", status=status))
    db.commit()
    sid = db.query(Submission).order_by(Submission.id.desc()).first().id
    db.close()
    return sid


def test_cancel_queued_batch_task(rx_env):
    """排队中的批改任务可取消：队列状态 cancelled，提交保持 uploaded 可重批。"""
    import asyncio
    import backend.routes.grading as gr
    from backend.database import Submission, get_db

    sid = make_sub(rx_env)

    async def drive():
        db = next(get_db())
        try:
            res = await gr.start_batch(gr.BatchIn(submission_ids=[sid]), db=db)
        finally:
            db.close()
        assert res["queued"] == [sid]
        # 在同一事件循环内直接调取消端点协程
        db = next(get_db())
        try:
            cres = await gr.cancel_grading(sid, db=db)
        finally:
            db.close()
        assert cres["success"] is True, cres
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(drive())

    assert gr.QUEUE[sid]["state"] == "cancelled", gr.QUEUE
    db = rx_env["dbmod"].SessionLocal()
    sub = db.query(Submission).filter(Submission.id == sid).first()
    assert sub.status == "uploaded", sub.status
    db.close()


def test_cancel_no_task_returns_400(rx_env):
    """没有运行中任务时取消 → 400；提交不存在 → 404。"""
    c = rx_env["client"]
    sid = make_sub(rx_env)
    r = c.post(f"/api/grading/cancel/{sid}")
    assert r.status_code == 400 and "没有可取消" in r.json()["detail"]
    assert c.post("/api/grading/cancel/999999999").status_code == 404


def test_grading_timeout_aborts_hang(rx_env, monkeypatch):
    """模型挂死 → 整体超时触发，提交落 failed，提示包含超时字样，可重试。"""
    import asyncio
    import backend.routes.grading as gr
    from backend.database import Submission

    # 绕过 60s 下限，用 0.2s 快速触发真实超时路径
    monkeypatch.setattr(gr, "get_grading_timeout", lambda: 0.2)

    sid = make_sub(rx_env)
    db = rx_env["dbmod"].SessionLocal()
    sub = db.query(Submission).filter(Submission.id == sid).first()
    sub.status = "processing"
    db.commit()
    db.close()

    res = asyncio.run(gr._grade_one(sid))
    assert res["success"] is False and "超时" in res["error"], res
    assert gr.QUEUE[sid]["state"] == "failed"
    assert gr.QUEUE[sid]["error"] == "批改超时已中止"

    db = rx_env["dbmod"].SessionLocal()
    sub = db.query(Submission).filter(Submission.id == sid).first()
    assert sub.status == "failed", sub.status
    assert "超时" in (sub.error_message or "")
    db.close()

    # 超时失败后允许重试（failed 可重新发起批改）
    c = rx_env["client"]
    r = c.post(f"/api/grading/retry/{sid}")
    # 引擎仍挂死，会再次超时/被拒——但状态机应允许进入重试（非 400 拒绝）
    assert r.status_code != 400, r.text


def test_timeout_config_bounds(rx_env):
    """超时配置：默认 300，可设 60-900，越界拒绝。"""
    c = rx_env["client"]
    r = c.get("/api/grading/timeout")
    assert r.status_code == 200 and r.json()["timeout_seconds"] == 300, r.text

    r = c.post("/api/grading/timeout", json={"value": 120})
    assert r.status_code == 200 and r.json()["timeout_seconds"] == 120, r.text
    assert c.post("/api/grading/timeout", json={"value": 30}).status_code == 400
    assert c.post("/api/grading/timeout", json={"value": 1000}).status_code == 400
    c.post("/api/grading/timeout", json={"value": 300})
