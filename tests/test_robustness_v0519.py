# -*- coding: utf-8 -*-
"""健壮性加固第二轮（v0.5.19）：
1. _grade_one 兜底异常 -> 提交落 failed（不再永远卡 processing）
2. 队列 worker 异常 -> QUEUE 落 failed
3. 模型 JSON 解析失败 -> parse_failed 标记 + 摘要警示
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tests.conftest import tmp_db  # noqa: F401


# ---------- 1. _grade_one 兜底异常 ----------

def test_grade_one_exception_marks_failed(tmp_db, monkeypatch):
    import backend.routes.grading as g
    from backend.database import SessionLocal, Submission
    import datetime

    db = SessionLocal()
    sub = Submission(assignment_id=1, student_id=1,
                     original_image_path="/tmp/definitely_missing.jpg",
                     status="processing")
    db.add(sub)
    db.commit()
    sid = sub.id
    db.close()

    async def boom(**kwargs):
        raise RuntimeError("模拟引擎崩溃")

    monkeypatch.setattr(g.grading_manager, "grade", boom)

    result = asyncio.run(g._grade_one(sid, None))

    assert result["success"] is False
    assert "模拟引擎崩溃" in result["error"]
    assert g.QUEUE[sid]["state"] == "failed"

    db = SessionLocal()
    sub = db.query(Submission).filter(Submission.id == sid).first()
    assert sub.status == "failed"
    assert sub.error_message  # 引擎层异常也必须落 failed 并带原因
    db.close()


def test_grade_one_uncaught_exception_marks_failed(tmp_db, monkeypatch):
    """批改中段（标注/保存阶段）的未捕获异常也必须落 failed，不能卡 processing"""
    import backend.routes.grading as g
    from backend.database import SessionLocal, Submission

    db = SessionLocal()
    sub = Submission(assignment_id=1, student_id=1,
                     original_image_path="/tmp/missing2.jpg", status="processing")
    db.add(sub)
    db.commit()
    sid = sub.id
    db.close()

    async def ok(**kwargs):
        return {"success": True, "judgement": "correct", "confidence": 0.9,
                "analysis": "ok", "questions": [
                    {"question_no": "1", "judgement": "correct", "confidence": 0.9}]}
    monkeypatch.setattr(g.grading_manager, "grade", ok)

    def boom_annot(*a, **k):
        raise RuntimeError("标注阶段崩溃")
    monkeypatch.setattr(g, "annotate_image", boom_annot)

    result = asyncio.run(g._grade_one(sid, None))
    assert result["success"] is False
    assert "标注阶段崩溃" in result["error"]
    assert g.QUEUE[sid]["state"] == "failed"

    db = SessionLocal()
    sub = db.query(Submission).filter(Submission.id == sid).first()
    assert sub.status == "failed"
    assert "批改过程异常" in (sub.error_message or "")
    db.close()


# ---------- 2. 批量 worker 异常兜底 ----------

def test_batch_worker_exception_marks_queue_failed(tmp_db, monkeypatch):
    import backend.routes.grading as g
    from backend.database import SessionLocal, Submission

    db = SessionLocal()
    sub = Submission(assignment_id=1, student_id=1,
                     original_image_path="/tmp/x.jpg", status="uploaded")
    db.add(sub)
    db.commit()
    sid = sub.id
    db.close()

    async def boom(submission_id, template_id=None):
        raise RuntimeError("worker 内部炸了")

    monkeypatch.setattr(g, "_grade_one", boom)

    resp = asyncio.run(g.start_batch(g.BatchIn(submission_ids=[sid]),
                                     db=SessionLocal()))
    assert resp["success"] is True
    assert sid in resp["queued"]
    pending = [t for t in g._TASKS if not t.done()]
    if pending:
        asyncio.run(asyncio.gather(*pending, return_exceptions=True))

    assert g.QUEUE[sid]["state"] == "failed"
    assert "队列任务异常" in (g.QUEUE[sid]["error"] or "")
    # 任务结束后强引用集合应清空
    assert all(t.done() for t in g._TASKS)


# ---------- 3. 模型 JSON 解析失败标记 ----------

def test_parse_response_marks_parse_failed():
    from backend.models.grading_engine import GradingEngine
    engine = GradingEngine.__new__(GradingEngine)
    resp = {"choices": [{"message": {"content": "这不是JSON，模型跑题了"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    out = engine._parse_response(resp)
    assert out["success"] is True
    assert out.get("parse_failed") is True
    assert out["judgement"] == "unclear"


def test_parse_response_no_false_flag_on_valid_json():
    from backend.models.grading_engine import GradingEngine
    engine = GradingEngine.__new__(GradingEngine)
    content = ('{"summary":"ok","questions":[{"question_no":"1","judgement":"correct",'
               '"confidence":0.9,"student_answer":"42","correct_answer":"42"}]}')
    resp = {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    out = engine._parse_response(resp, answers={"1": "42"})
    assert out["success"] is True
    assert not out.get("parse_failed")
    assert out["questions"]
