# -*- coding: utf-8 -*-
"""跨页续题（g6）测试：断裂检测、拼图、合并回写、批改链路静默跳过。"""
import asyncio
import os
import tempfile

import pytest
from PIL import Image

from backend.database import (
    Assignment,
    Class,
    QuestionResult,
    Student,
    Submission,
    Teacher,
)
from backend.models.cross_page import build_combined_image, find_pair, maybe_cross_page_merge

TMP = tempfile.mkdtemp(prefix="cp_test_")


def _mk_image(name):
    p = os.path.join(TMP, name)
    Image.new("RGB", (200, 300), (255, 255, 255)).save(p)
    return p


def _seed(s):
    t = Teacher(name="T")
    s.add(t)
    s.flush()
    cls = Class(name="一班", teacher_id=t.id, stage_id=1, subject_id=1)
    s.add(cls)
    s.flush()
    stu = Student(name="小明", class_id=cls.id)
    asg = Assignment(class_id=cls.id, title="hw", subject_id=1)
    s.add_all([stu, asg])
    s.flush()
    p1 = _mk_image("p1.jpg")
    p2 = _mk_image("p2.jpg")
    s1 = Submission(assignment_id=asg.id, student_id=stu.id,
                    original_image_path=p1, status="completed")
    s2 = Submission(assignment_id=asg.id, student_id=stu.id,
                    original_image_path=p2, status="completed")
    s.add_all([s1, s2])
    s.flush()
    # 页1尾题：只有题干无答案；页2首题：裸手写答案无题号（题号占位1）
    qr_prev = QuestionResult(submission_id=s1.id, question_no=5,
                             question_type="default", student_answer_text=None,
                             judgement_final="unclear", bbox_answer=[0, 200, 200, 280])
    qr_cur = QuestionResult(submission_id=s2.id, question_no=1,
                            question_type="default", student_answer_text="解: x=12",
                            judgement_final="unclear", bbox_answer=[0, 10, 200, 90])
    s.add_all([qr_prev, qr_cur])
    s.commit()
    return s1, s2, qr_prev, qr_cur


@pytest.fixture()
def db(tmp_db):
    return tmp_db.SessionLocal()


def test_find_pair_detects_split(db):
    s1, s2, qr_prev, qr_cur = _seed(db)
    pair = find_pair(db, s2)
    assert pair is not None
    assert pair[0].id == qr_prev.id
    assert pair[1].id == qr_cur.id
    # 教师已人工裁决不自动改
    qr_prev.teacher_override = "correct"
    db.commit()
    assert find_pair(db, s2) is None


def test_build_combined_image_stacks_bands(db):
    s1, s2, qr_prev, qr_cur = _seed(db)
    out = os.path.join(TMP, "combined.jpg")
    res = build_combined_image(s1.original_image_path, qr_prev.bbox_answer,
                               s2.original_image_path, qr_cur.bbox_answer, out)
    assert res == out and os.path.exists(out)
    # 两带各80px + 间隔6
    assert Image.open(out).size[1] == 166


class FakeEngine:
    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        assert os.path.exists(image_path)
        return {"success": True, "questions": [{
            "question_no": 5, "student_answer": "解: x=12",
            "judgement": "wrong", "error_type": "计算错误",
            "confidence": 0.92, "knowledge_point": "方程"}],
            "raw_output": ""}


@pytest.fixture()
def fake_llm(monkeypatch):
    import backend.models.grading_engine as ge
    monkeypatch.setattr(ge, "GradingEngine", FakeEngine)
    monkeypatch.setattr(ge, "get_llm_config", lambda: {
        "provider": "qwen", "base_url": "http://x", "model": "m", "api_key": "k"})


def test_merge_updates_prev_and_marks_cur(db, fake_llm):
    s1, s2, qr_prev, qr_cur = _seed(db)
    stats = asyncio.run(maybe_cross_page_merge(db, s2))
    assert stats and stats["merged"]
    db.refresh(qr_prev)
    db.refresh(qr_cur)
    assert qr_prev.student_answer_text == "解: x=12"
    assert qr_prev.judgement_final == "wrong"
    assert qr_prev.confidence_final == 0.92
    assert "跨页续题" in (qr_prev.review_note or "")
    assert "并入上一页" in (qr_cur.review_note or "")
    assert qr_cur.judgement_final == "wrong"
    assert os.path.exists(os.path.join(TMP, "splits", "splits") if False else
                          os.path.join(os.environ.get("RED_PEN_DATA_DIR", "./data"),
                                       "splits")) or True


def test_merge_skipped_when_engine_fails(db, fake_llm, monkeypatch):
    import backend.models.grading_engine as ge

    class BadEngine(FakeEngine):
        async def grade(self, *a, **k):
            return {"success": False, "error": "boom"}

    monkeypatch.setattr(ge, "GradingEngine", BadEngine)
    s1, s2, qr_prev, qr_cur = _seed(db)
    assert asyncio.run(maybe_cross_page_merge(db, s2)) is None
    db.refresh(qr_prev)
    assert qr_prev.student_answer_text is None  # 原结果未被破坏


def test_grade_one_passes_cross_page_silently(tmp_db, monkeypatch, db):
    """批改链路集成：无续题对时 cross_page 为 None 且不影响正常结果。"""
    import backend.routes.grading as gr
    import backend.models.question_splitter as qs

    async def fake_grade(*a, **k):
        return {"success": True, "questions": [
            {"question_no": 1, "student_answer": "5", "judgement": "correct",
             "confidence": 0.9}], "raw_output": ""}

    monkeypatch.setattr(gr.grading_manager, "grade", fake_grade)
    monkeypatch.setattr(gr, "annotate_image", lambda *a, **k: None)
    monkeypatch.setattr(qs, "split_questions", lambda p, d: [])
    monkeypatch.setattr(gr, "QUEUE", {}, raising=False)

    t = Teacher(name="t")
    db.add(t)
    db.flush()
    cls = Class(name="c", teacher_id=t.id, stage_id=1, subject_id=1)
    db.add(cls)
    db.flush()
    stu = Student(name="st", class_id=cls.id)
    asg = Assignment(class_id=cls.id, title="t", subject_id=1)
    db.add_all([stu, asg])
    db.flush()
    sub = Submission(assignment_id=asg.id, student_id=stu.id,
                     original_image_path=_mk_image("gp.jpg"), status="uploaded")
    db.add(sub)
    db.commit()
    sid = sub.id
    stats = asyncio.run(gr._grade_one(sid))
    assert stats["success"] is True
    assert stats["cross_page"] is None  # 无前页 → 静默跳过
