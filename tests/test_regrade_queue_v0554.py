# -*- coding: utf-8 -*-
"""单题重批 → 复核队列(needs_review)联动测试（健壮性第二十九轮）：
重批后 needs_review 标志与学情分析复核队列必须同步更新，不能出现
"重批已修正但队列里永远留着"或"重批引入新可疑题却不在队列"。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.database as dbmod  # noqa: E402
from backend.database import QuestionResult, Submission  # noqa: E402


def _mk_img(tmp_path):
    from PIL import Image
    p = tmp_path / "orig.png"
    Image.new("RGB", (4, 4), (255, 255, 255)).save(p)
    return str(p)


@pytest.fixture()
def seeded(tmp_path, monkeypatch, tmp_db):
    # tmp_db 必须显式声明：与 auth_client 共用同一临时库，
    # 否则 seeded 写真实 data/red_pen.db、analytics 读 tmp 库，两边对不上。
    img = _mk_img(tmp_path)
    s = dbmod.SessionLocal()
    sub = Submission(assignment_id=1, student_id=1, original_image_path=img,
                     status="completed")
    s.add(sub)
    s.flush()
    qr = QuestionResult(submission_id=sub.id, question_no=1, question_type="default",
                        student_answer_text="3x+2", judgement_final="wrong",
                        judgement_raw="wrong", error_type="计算错误",
                        confidence_final=0.5, needs_review=1, bbox_answer=[[1, 2, 3, 4]])
    s.add(qr)
    s.commit()
    from backend.models import budget as budget_mod
    monkeypatch.setattr(budget_mod, "check_budget",
                        lambda: {"exceeded": False, "budget_action": "block",
                                 "spent": 0.0, "monthly_budget": 100.0})
    yield dbmod, sub.id, qr.id
    s.close()


def _mock_engine(monkeypatch, questions, success=True):
    import backend.routes.grading as g

    class _Fake:
        async def grade(self, **kwargs):
            if not success:
                return {"success": False, "error": "boom"}
            return {"success": True, "questions": questions, "raw_output": {}}

    monkeypatch.setattr(g, "grading_manager", _Fake())


def _queue(auth_client):
    d = auth_client.get("/api/analytics/analytics").json()
    return d["needs_review_count"], [x["id"] for x in d["needs_review"]]


def test_regrade_clean_result_removes_from_queue(auth_client, seeded, monkeypatch):
    """回归：重批修成高置信度且无需复核 → 复核队列必须放行，不能永久滞留。"""
    dbmod, _, qr_id = seeded
    _mock_engine(monkeypatch, [{"question_no": "1", "judgement": "correct",
                                "confidence": 0.95, "needs_review": 0}])
    assert _queue(auth_client)[0] == 1                     # 重批前在队列
    assert auth_client.post(f"/api/grading/regrade-question/{qr_id}").status_code == 200
    count, ids = _queue(auth_client)
    assert count == 0 and qr_id not in ids


def test_regrade_suspicious_result_enters_queue(auth_client, seeded, monkeypatch):
    """回归：重批引入 needs_review=1（或低置信度）→ 必须进复核队列。"""
    dbmod, _, qr_id = seeded
    _mock_engine(monkeypatch, [{"question_no": "1", "judgement": "correct",
                                "confidence": 0.55, "needs_review": 1}])
    # 先把旧行修干净，队列清零
    _mock_engine(monkeypatch, [{"question_no": "1", "judgement": "correct",
                                "confidence": 0.95, "needs_review": 0}])
    auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    assert _queue(auth_client)[0] == 0
    # 再重批出可疑结果
    _mock_engine(monkeypatch, [{"question_no": "1", "judgement": "unclear",
                                "confidence": 0.55, "needs_review": 1}])
    auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    count, ids = _queue(auth_client)
    assert count == 1 and qr_id in ids


def test_regrade_clears_override_then_reenters_queue(auth_client, seeded, monkeypatch):
    """回归：重批清空 teacher_override 后，若新结果仍可疑，必须重新进队列——
    队列过滤排除 teacher_override 行，override 清空后不能继续被"豁免"。"""
    dbmod, _, qr_id = seeded
    s = dbmod.SessionLocal()
    qr = s.get(QuestionResult, qr_id)
    qr.teacher_override = "correct"    # 历史遗留：教师改判过但 needs_review=1 还挂着
    s.commit()
    s.close()
    assert _queue(auth_client)[0] == 0                 # override 在，被豁免
    _mock_engine(monkeypatch, [{"question_no": "1", "judgement": "unclear",
                                "confidence": 0.5, "needs_review": 1}])
    auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    s = dbmod.SessionLocal()
    assert s.get(QuestionResult, qr_id).teacher_override is None
    s.close()
    count, ids = _queue(auth_client)
    assert count == 1 and qr_id in ids                 # 重批后又可疑 → 重新进队列


def test_queue_note_reflects_regrade_note(auth_client, seeded, monkeypatch):
    """队列条目的 note 应来自重批留痕，教师能看到"这题是重批后变可疑的"。"""
    dbmod, _, qr_id = seeded
    _mock_engine(monkeypatch, [{"question_no": "1", "judgement": "unclear",
                                "confidence": 0.5, "needs_review": 1}])
    auth_client.post(f"/api/grading/regrade-question/{qr_id}")
    d = auth_client.get("/api/analytics/analytics").json()
    assert any("单题重批" in (x.get("note") or "") for x in d["needs_review"])
