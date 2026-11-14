# -*- coding: utf-8 -*-
"""看板第一层 PRD 缺口测试：与上次对比(delta/prev_accuracy) + 最高/最低作业(best_worst)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed(tmp_db):
    """一班张三两份作业：a1 全对(1.0)，a2 对2/3(0.667)；二班王五一份全对"""
    from backend.database import (Assignment, Class, QuestionResult, SessionLocal,
                                  Student, Submission)

    db = SessionLocal()
    c1 = Class(name="三年一班", teacher_id=1, stage_id=1, subject_id=1)
    c2 = Class(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add_all([c1, c2])
    db.commit()
    a1 = Assignment(class_id=c1.id, title="口算一", subject_id=1)
    a2 = Assignment(class_id=c1.id, title="口算二", subject_id=1)
    a3 = Assignment(class_id=c2.id, title="二班练习", subject_id=1)
    db.add_all([a1, a2, a3])
    db.commit()
    z = Student(name="张三", class_id=c1.id)
    w = Student(name="王五", class_id=c2.id)
    db.add_all([z, w])
    db.commit()

    def _mk(assignment_id, student_id, n_correct, n_total):
        sub = Submission(assignment_id=assignment_id, student_id=student_id,
                         original_image_path="/tmp/x.jpg", status="completed")
        db.add(sub)
        db.commit()
        for i in range(n_total):
            ok = i < n_correct
            db.add(QuestionResult(submission_id=sub.id, question_no=i + 1,
                                  question_type="calculation",
                                  judgement_raw="correct" if ok else "wrong",
                                  judgement_final="correct" if ok else "wrong",
                                  confidence_final=0.9))
        db.commit()
        return sub.id

    _mk(a1.id, z.id, 3, 3)
    _mk(a2.id, z.id, 2, 3)
    _mk(a3.id, w.id, 2, 2)
    ids = {"a1": a1.id, "a2": a2.id, "a3": a3.id}
    db.close()
    return ids


def test_summary_delta_and_best_worst(tmp_db):
    _seed(tmp_db)
    import asyncio
    from backend.database import SessionLocal
    from backend.routes.dashboard import dashboard_summary

    db = SessionLocal()
    try:
        s = asyncio.run(dashboard_summary(db))
    finally:
        db.close()

    grades = {g["assignment_id"]: g for g in s["assignment_grades"]}

    # 与上次对比：同班级上一份有成绩的作业
    g1 = grades[1]
    assert g1["prev_accuracy"] is None and g1["delta"] is None  # 班内首份无对比
    g2 = grades[2]
    assert g2["prev_accuracy"] == 1.0
    assert g2["delta"] == round(2 / 3 - 1.0, 3)  # 下降
    g3 = grades[3]
    assert g3["prev_accuracy"] is None  # 二班首份，不跨班对比

    # 最高/最低（并列满分 1.0 时取哪份是实现细节，只验准确率；最低唯一）
    bw = s["best_worst"]
    assert bw["best"]["accuracy"] == 1.0
    assert bw["worst"]["title"] == "口算二" and bw["worst"]["accuracy"] == round(2 / 3, 3)


def test_summary_empty_db_best_worst_none(tmp_db):
    import asyncio
    from backend.database import SessionLocal
    from backend.routes.dashboard import dashboard_summary

    db = SessionLocal()
    try:
        s = asyncio.run(dashboard_summary(db))
    finally:
        db.close()
    assert s["best_worst"] == {"best": None, "worst": None}
    assert s["assignment_grades"] == []


def test_summary_route_endpoint(tmp_db):
    _seed(tmp_db)
    from fastapi.testclient import TestClient
    from backend.app import app

    client = TestClient(app)
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    s = r.json()
    bw = s["best_worst"]
    assert bw["best"] and bw["worst"]
    by_id = {g["assignment_id"]: g for g in s["assignment_grades"]}
    assert by_id[2]["delta"] is not None
