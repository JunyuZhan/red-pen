# -*- coding: utf-8 -*-
"""学情可视化趋势数据（待办 l）测试：逐生正确率序列、班级平均正确率、路由"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed_two_classes(tmp_db):
    """两个班：一班张三2次作业（1.0/0.33），二班王五1次全对"""
    from backend.database import Assignment, Class, QuestionResult, SessionLocal, Student, Submission

    db = SessionLocal()
    c1 = Class(name="三年一班", teacher_id=1, stage_id=1, subject_id=1)
    c2 = Class(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add_all([c1, c2])
    db.commit()
    a1 = Assignment(class_id=c1.id, title="一班口算", subject_id=1)
    a2 = Assignment(class_id=c2.id, title="二班口算", subject_id=1)
    db.add_all([a1, a2])
    db.commit()
    z = Student(name="张三", class_id=c1.id)
    w = Student(name="王五", class_id=c2.id)
    db.add_all([z, w])
    db.commit()
    for accs in ([1.0, 1.0, 1.0], [0.0, 0.5, 0.0]):  # 张三两次：3/3, 1/3
        sub = Submission(assignment_id=a1.id, student_id=z.id,
                         original_image_path="/tmp/x.jpg", status="completed")
        db.add(sub)
        db.commit()
        for i, acc in enumerate(accs):
            db.add(QuestionResult(submission_id=sub.id, question_no=i + 1,
                                  question_type="calculation",
                                  judgement_raw="correct" if acc else "wrong",
                                  judgement_final="correct" if acc else "wrong",
                                  confidence_final=0.9))
    sub2 = Submission(assignment_id=a2.id, student_id=w.id,
                      original_image_path="/tmp/y.jpg", status="completed")
    db.add(sub2)
    db.commit()
    for i in range(2):
        db.add(QuestionResult(submission_id=sub2.id, question_no=i + 1,
                              question_type="calculation",
                              judgement_raw="correct", judgement_final="correct",
                              confidence_final=0.9))
    db.commit()
    ids = {"c1": c1.id, "c2": c2.id, "z": z.id, "w": w.id}
    db.close()
    return ids


def test_trends_series_and_classes(tmp_db):
    _seed_two_classes(tmp_db)
    import asyncio
    from backend.database import SessionLocal
    from backend.routes.analytics import analytics_trends

    db = SessionLocal()
    try:
        d = asyncio.run(analytics_trends(db))
    finally:
        db.close()
    assert d["success"] is True

    by_name = {s["name"]: s for s in d["series"]}
    # 张三：两次作业，正确率 1.0 -> 0.33
    assert "张三" in by_name
    z = by_name["张三"]
    assert len(z["history"]) == 2
    assert z["history"][0]["accuracy"] == 1.0
    assert abs(z["history"][1]["accuracy"] - 0.33) < 0.01
    assert z["history"][0]["date"]  # 有日期标签
    # 序列按时间有序（created_at 相同时按 id 升序）
    assert z["history"][0]["total"] == 3

    # 班级平均正确率：一班 4/6≈0.667，二班 1.0
    by_cls = {c["name"]: c for c in d["classes"]}
    assert "三年一班" in by_cls and "三年二班" in by_cls
    assert abs(by_cls["三年一班"]["accuracy"] - 0.667) < 0.01
    assert by_cls["三年二班"]["accuracy"] == 1.0
    # 排序：正确率高的班在前
    assert d["classes"][0]["accuracy"] == 1.0


def test_trends_route(tmp_db, auth_client):
    _seed_two_classes(tmp_db)
    r = auth_client.get("/api/analytics/analytics/trends")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] is True
    assert len(d["series"]) == 2
    assert len(d["classes"]) == 2


def test_trends_empty_db(tmp_db):
    import asyncio
    from backend.database import SessionLocal
    from backend.routes.analytics import analytics_trends

    db = SessionLocal()
    try:
        d = asyncio.run(analytics_trends(db))
    finally:
        db.close()
    assert d["success"] is True
    assert d["series"] == []
    assert d["classes"] == []
