# -*- coding: utf-8 -*-
"""单题重批（regrade-question）测试：
mock 引擎返回、只覆盖目标题、bbox/teacher_override 保留、
超时/失败/题号未匹配/状态与预算护栏各分支。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.database import QuestionResult, Submission, init_db  # noqa: E402


def _mk_img(tmp_path):
    """造一张 1x1 PNG 当原图（os.path.exists 要过）。"""
    from PIL import Image
    p = tmp_path / "orig.png"
    Image.new("RGB", (4, 4), "white").save(p)
    return str(p)


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    from backend.database import SessionLocal
    img = _mk_img(tmp_path)
    s = SessionLocal()
    sub = Submission(assignment_id=1, student_id=1, original_image_path=img,
                     status="completed")
    s.add(sub)
    s.flush()
    qr = QuestionResult(submission_id=sub.id, question_no=1, question_type="default",
                        student_answer_text="3x+2", judgement_final="wrong",
                        judgement_raw="wrong", error_type="计算错误",
                        confidence_final=0.9, bbox_answer=[[1, 2, 3, 4]],
                        teacher_override="correct", knowledge_point="一次方程")
    s.add(qr)
    s.commit()
    # 预算护栏默认关掉（block+未超支），个别测试再单独 mock
    from backend.models import budget as budget_mod
    monkeypatch.setattr(budget_mod, "check_budget",
                        lambda: {"exceeded": False, "budget_action": "block",
                                 "spent": 0.0, "monthly_budget": 100.0})
    yield SessionLocal, sub.id, qr.id, img
    s.close()


def _mock_engine(monkeypatch, questions, success=True):
    """替换路由模块里的 grading_manager.grade。"""
    import backend.routes.grading as g

    class _Fake:
        async def grade(self, **kwargs):
            if not success:
                return {"success": False, "error": "HTTP 401: bad key"}
            return {"success": True, "questions": questions, "raw_output": {"raw": 1}}

    monkeypatch.setattr(g, "grading_manager", _Fake())


Q1_OK = {"question_no": "1", "judgement": "correct", "error_type": None,
         "confidence": 0.95, "student_answer": "3x+3", "correct_answer": "3x+3",
         "steps": [], "knowledge_point": "一次方程", "needs_review": 0}


def test_regrade_updates_target_only(auth_client, seeded, monkeypatch):
    SessionLocal, sid, qr_id, _ = seeded
    _mock_engine(monkeypatch, [Q1_OK,
                               {"question_no": "2", "judgement": "wrong"}])
    r = auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True and d["changed"] is True
    assert d["question"]["judgement"] == "correct"

    s = SessionLocal()
    qr = s.get(QuestionResult, qr_id)
    assert qr.judgement_final == "correct"
    assert qr.student_answer_text == "3x+3"
    assert qr.bbox_answer == [[1, 2, 3, 4]]          # bbox 保留
    assert qr.teacher_override == "correct"          # 教师改判保留
    assert "重批前判定: wrong" in qr.review_note      # 留痕
    s.close()


def test_regrade_unchanged_reports_changed_false(auth_client, seeded, monkeypatch):
    SessionLocal, _, qr_id, _ = seeded
    _mock_engine(monkeypatch, [dict(Q1_OK, judgement="wrong")])
    d = auth_client.post(f"/api/grading/regrade-question/{qr_id}").json()
    assert d["success"] and d["changed"] is False


def test_regrade_engine_failure(auth_client, seeded, monkeypatch):
    SessionLocal, _, qr_id, _ = seeded
    _mock_engine(monkeypatch, [], success=False)
    r = auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    assert r.status_code == 500
    assert "bad key" in r.json()["detail"]


def test_regrade_question_no_missing(auth_client, seeded, monkeypatch):
    SessionLocal, _, qr_id, _ = seeded
    _mock_engine(monkeypatch, [{"question_no": "9", "judgement": "correct"}])
    r = auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    assert r.status_code == 404


def test_regrade_non_completed_rejected(auth_client, seeded):
    SessionLocal, sid, qr_id, _ = seeded
    s = SessionLocal()
    s.get(Submission, sid).status = "processing"
    s.commit()
    s.close()
    r = auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    assert r.status_code == 400


def test_regrade_missing_image_rejected(auth_client, seeded):
    SessionLocal, sid, qr_id, _ = seeded
    s = SessionLocal()
    s.get(Submission, sid).original_image_path = "no_such_file.jpg"
    s.commit()
    s.close()
    assert auth_client.post(f"/api/grading/regrade-question/{qr_id}").status_code == 400


def test_regrade_budget_block(auth_client, seeded, monkeypatch):
    SessionLocal, _, qr_id, _ = seeded
    from backend.models import budget as budget_mod
    monkeypatch.setattr(budget_mod, "check_budget",
                        lambda: {"exceeded": True, "budget_action": "block",
                                 "spent": 12.0, "monthly_budget": 10.0})
    r = auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    assert r.status_code == 400
    assert "预算" in r.json()["detail"]


def test_regrade_timeout(auth_client, seeded, monkeypatch):
    import asyncio
    SessionLocal, _, qr_id, _ = seeded
    import backend.routes.grading as g

    class _Slow:
        async def grade(self, **kwargs):
            await asyncio.sleep(30)

    monkeypatch.setattr(g, "grading_manager", _Slow())
    monkeypatch.setattr(g, "get_grading_timeout", lambda: 1)
    assert auth_client.post(f"/api/grading/regrade-question/{qr_id}").status_code == 504


def test_regrade_qr_not_found(auth_client, tmp_path):
    assert auth_client.post("/api/grading/regrade-question/99999").status_code == 404


def test_regrade_bad_question_no_formats(auth_client, seeded, monkeypatch):
    """引擎返回的题号是整数/带空白等格式也要能匹配。"""
    SessionLocal, _, qr_id, _ = seeded
    _mock_engine(monkeypatch, [{"question_no": 1, "judgement": "partial",
                                "confidence": "0.6"}])
    d = auth_client.post(f"/api/grading/regrade-question/{qr_id}").json()
    assert d["success"] and d["question"]["judgement"] == "partial"
    assert d["question"]["confidence"] == pytest.approx(0.6)
