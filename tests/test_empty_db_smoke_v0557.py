# -*- coding: utf-8 -*-
"""健壮性第二十七轮（三）：空库下 dashboard/mobile 统计接口冒烟。"""


def test_empty_db_dashboard_mobile(auth_client, tmp_db):
    """全新空库：dashboard summary、mobile 各只读接口不 500、无除零。"""
    for url in ("/api/dashboard/summary",
                "/api/mobile/queue-counts",
                "/api/mobile/list",
                "/api/mobile/classes-students"):
        r = auth_client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text[:200]}"
    r = auth_client.get("/api/mobile/status/99999")
    assert r.status_code in (200, 404), r.status_code


def test_dashboard_with_only_unclear(auth_client, tmp_db):
    """只有 unclear/needs_review 数据时 summary 正确率不虚报 100%。"""
    from backend.database import (Assignment, Class, QuestionResult,
                                  Student, Submission)
    db = tmp_db.SessionLocal()
    cls = Class(name="三班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.commit()
    st = Student(name="王五", class_id=cls.id)
    db.add(st); db.commit()
    a = Assignment(title="作业C", class_id=cls.id, subject_id=1)
    db.add(a); db.commit()
    sub = Submission(assignment_id=a.id, student_id=st.id, status="completed",
                     original_image_path="t.jpg")
    db.add(sub); db.commit()
    for no, j in ((1, "unclear"), (2, None), (3, "partial")):
        db.add(QuestionResult(submission_id=sub.id, question_no=no,
                              question_type="计算题", judgement_final=j,
                              needs_review=1 if no == 2 else 0))
    db.commit()
    db.close()
    r = auth_client.get("/api/dashboard/summary")
    assert r.status_code == 200, r.text[:300]
    text = r.text
    assert '"100"' not in text and "100%" not in text, \
        "全 unclear/空判定不应报正确率 100%: " + text[:300]
