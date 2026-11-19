# -*- coding: utf-8 -*-
"""第二十四轮健壮性（v0.5.55）：批改队列重复入队。

探针发现的问题与回归覆盖：
- /api/grading/batch 此前入队时**不把提交置为 processing**（/start 会置）：
  老师快速双击「批量批改」会把同一份 uploaded 提交重复入队——并发双重批改、
  双倍烧 token、两个 worker 互删对方刚写入的 question_result。
  现入队即落 processing（服务重启恢复机制已有：processing → failed 可重试）。
- /batch 对 failed 提交重试不递增 retry_count，与 /start 口径不一致，
  可绕过 MAX_RETRY 无限批量重试；现同样递增。
"""
import sys
import os
import asyncio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_robustness_v0524 import rx_env, make_sub  # noqa: E402,F401


def _db(env):
    return env["dbmod"].SessionLocal()


def _call_batch(env, **kw):
    """调 start_batch 并等 worker 全部跑完（避免 asyncio.run 关循环时
    取消排队任务触发 CancelledError 把状态回写）。"""
    import backend.routes.grading as gr
    from backend.database import get_db

    async def run():
        before = set(gr._TASKS)
        db = next(get_db())
        try:
            res = await gr.start_batch(gr.BatchIn(**kw), db=db)
        finally:
            db.close()
        mine = [t for t in gr._TASKS - before if not t.done()]
        if mine:
            await asyncio.gather(*mine, return_exceptions=True)
        return res

    return asyncio.run(run())


def _make_failed_sub(env, retry_count):
    from backend.database import Submission
    db = _db(env)
    db.add(Submission(assignment_id=1, student_id=1,
                      original_image_path="/tmp/x.jpg",
                      status="failed", retry_count=retry_count))
    db.commit()
    sid = db.query(Submission).order_by(Submission.id.desc()).first().id
    db.close()
    return sid


def _status_of(env, sid):
    from backend.database import Submission
    db = _db(env)
    try:
        return db.query(Submission).filter(Submission.id == sid).first()
    finally:
        db.close()


def _fast_env(rx_env, monkeypatch):
    """rx_env 的 SlowEngine 挂死 600s；这里换成即时返回的假引擎，
    让 worker 能真实跑完（验证状态落库而非被取消回写）。"""
    import backend.models.grading_engine as ge

    class FastEngine:
        def __init__(self, *a, **k):
            pass

        async def grade(self, *a, **k):
            return {"success": True, "questions": [
                {"question_no": "1", "question_type": "fill",
                 "student_answer": "A", "correct_answer": "A",
                 "judgement": "correct", "confidence": 0.9}]}

    monkeypatch.setattr(ge, "GradingEngine", FastEngine)
    return rx_env


def test_batch_marks_processing_on_enqueue(rx_env, monkeypatch):
    """入队后提交立刻是 processing（双击第二次 batch 时会被 eligible 过滤掉）。"""
    _fast_env(rx_env, monkeypatch)
    sid = make_sub(rx_env, status="uploaded")
    res = _call_batch(rx_env, submission_ids=[sid])
    assert res["queued"] == [sid]
    s = _status_of(rx_env, sid)
    # 假引擎跑得快时可能已完成；关键是绝不能停留在 uploaded（重复入队根源）
    assert s.status in ("processing", "completed"), s.status


def test_double_batch_does_not_duplicate_queue(rx_env, monkeypatch):
    """快速双击「批量批改」：第二次调用不再入队同一份提交（skipped 上报）。"""
    _fast_env(rx_env, monkeypatch)
    sid = make_sub(rx_env, status="uploaded")
    first = _call_batch(rx_env, submission_ids=[sid])
    assert first["queued"] == [sid]
    second = _call_batch(rx_env, submission_ids=[sid])
    assert second["queued"] == [], second
    assert sid in second["skipped"], second


def test_batch_retries_increment_retry_count(rx_env, monkeypatch):
    """failed 提交经批量重试也递增 retry_count，与 /start 口径一致。"""
    _fast_env(rx_env, monkeypatch)
    sid = _make_failed_sub(rx_env, retry_count=1)
    res = _call_batch(rx_env, submission_ids=[sid])
    assert res["queued"] == [sid]
    s = _status_of(rx_env, sid)
    assert (s.retry_count or 0) == 2, s.retry_count


def test_batch_over_retry_limit_skipped(rx_env):
    """retry_count 已达上限的 failed 提交批量时被 skipped，不入队。"""
    sid = _make_failed_sub(rx_env, retry_count=3)
    res = _call_batch(rx_env, submission_ids=[sid])
    assert res["queued"] == []
    assert sid in res["skipped"], res
