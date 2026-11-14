# -*- coding: utf-8 -*-
"""手动改答案归属兜底入口（g6 遗留）测试：
答案文本+bbox 合并、source 清空注明去向、target 进复核队列、各类非法入参。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.database import QuestionResult, Submission, init_db  # noqa: E402


@pytest.fixture()
def seeded(tmp_db):
    from backend.database import SessionLocal
    s = SessionLocal()
    sub = Submission(assignment_id=1, student_id=1, original_image_path="x.jpg",
                     status="completed")
    s.add(sub)
    s.flush()
    q1 = QuestionResult(submission_id=sub.id, question_no=1, question_type="default",
                        student_answer_text="3x+2", judgement_final="wrong",
                        confidence_final=0.9, bbox_answer=[[1, 2, 3, 4], [5, 6, 7, 8]])
    q2 = QuestionResult(submission_id=sub.id, question_no=2, question_type="default",
                        student_answer_text="42", judgement_final="correct",
                        confidence_final=0.8, bbox_answer=[[9, 9, 9, 9]])
    q3 = QuestionResult(submission_id=sub.id, question_no=3, question_type="default",
                        student_answer_text=None, judgement_final="correct")
    s.add_all([q1, q2, q3])
    s.commit()
    yield SessionLocal, sub.id, q1.id, q2.id, q3.id
    s.close()


def _post(client, sid, payload):
    return client.post(f"/api/grading/reassign/{sid}", json=payload)


def test_merge_text_and_bbox(auth_client, seeded):
    SessionLocal, sid, q1, q2, _ = seeded
    r = _post(auth_client, sid, {"source_id": q1, "target_id": q2})
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True

    s = SessionLocal()
    src = s.get(QuestionResult, q1)
    tgt = s.get(QuestionResult, q2)
    assert src.student_answer_text is None
    assert src.bbox_answer is None
    assert "第2题" in src.review_note
    assert tgt.student_answer_text == "42 3x+2"
    assert tgt.bbox_answer == [[9, 9, 9, 9], [1, 2, 3, 4], [5, 6, 7, 8]]
    assert tgt.needs_review == 1
    assert "并入" in tgt.review_note
    assert tgt.confidence_final == pytest.approx(0.5)  # bbox 合并 → 置信度压到 ≤0.5
    s.close()


def test_move_to_blank_question(auth_client, seeded):
    """移到无答案的题：文本搬运，置信度不被压（无 bbox 合并）。"""
    SessionLocal, sid, q1, _, q3 = seeded
    r = _post(auth_client, sid, {"source_id": q1, "target_id": q3})
    assert r.status_code == 200
    s = SessionLocal()
    tgt = s.get(QuestionResult, q3)
    assert tgt.student_answer_text == "3x+2"
    assert tgt.bbox_answer == [[1, 2, 3, 4], [5, 6, 7, 8]]
    assert tgt.needs_review == 1
    s.close()


def test_source_without_answer_rejected(auth_client, seeded):
    SessionLocal, sid, q1, _, q3 = seeded
    r = _post(auth_client, sid, {"source_id": q3, "target_id": q1})
    assert r.status_code == 400


def test_same_id_rejected(auth_client, seeded):
    SessionLocal, sid, q1, _, _ = seeded
    r = _post(auth_client, sid, {"source_id": q1, "target_id": q1})
    assert r.status_code == 400


def test_missing_payload(auth_client, seeded):
    SessionLocal, sid, q1, _, _ = seeded
    assert _post(auth_client, sid, {}).status_code == 400
    assert _post(auth_client, sid, {"source_id": q1, "target_id": 99999}).status_code == 404


def test_cross_submission_rejected(auth_client, tmp_db):
    """target 属于别的 submission → 404，不能跨提交搬运。"""
    from backend.database import SessionLocal
    s = SessionLocal()
    a = Submission(assignment_id=1, student_id=1, original_image_path="a.jpg", status="completed")
    b = Submission(assignment_id=1, student_id=1, original_image_path="b.jpg", status="completed")
    s.add_all([a, b])
    s.flush()
    qa = QuestionResult(submission_id=a.id, question_no=1, question_type="default",
                        student_answer_text="x")
    qb = QuestionResult(submission_id=b.id, question_no=1, question_type="default",
                        student_answer_text="y")
    s.add_all([qa, qb])
    s.commit()
    ids = (a.id, qa.id, qb.id)
    s.close()
    r = _post(auth_client, ids[0], {"source_id": ids[1], "target_id": ids[2]})
    assert r.status_code == 404
