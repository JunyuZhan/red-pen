# -*- coding: utf-8 -*-
"""学生个人错题本导出：按知识点分组（错误多的在前）、correct 排除、days 过滤、学生不存在 404"""
import os

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in os.sys.path:
    os.sys.path.insert(0, ROOT)


@pytest.fixture()
def client(tmp_db):
    from backend.app import app
    return TestClient(app)


def _seed(db):
    from backend.database import Submission, QuestionResult, Class as Cls, Student, Assignment
    cls = Cls(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.flush()
    st = Student(name="小明", class_id=cls.id)
    db.add(st); db.flush()
    asg = Assignment(class_id=cls.id, title="口算练习", subject_id=1)
    db.add(asg); db.flush()
    sub = Submission(assignment_id=asg.id, student_id=st.id,
                     original_image_path="/tmp/a.jpg", status="completed")
    db.add(sub); db.flush()
    db.add_all([
        # 进位加法 1 错；退位减法 2 错 → 退位减法组在前
        QuestionResult(submission_id=sub.id, question_no=1, question_type="计算题",
                       judgement_final="correct", confidence_final=0.9, knowledge_point="退位减法"),
        QuestionResult(submission_id=sub.id, question_no=2, question_type="计算题",
                       judgement_final="wrong", confidence_final=0.9,
                       knowledge_point="进位加法", error_type="计算错误"),
        QuestionResult(submission_id=sub.id, question_no=3, question_type="计算题",
                       judgement_final="wrong", confidence_final=0.9,
                       knowledge_point="退位减法", error_type="借位遗漏"),
        QuestionResult(submission_id=sub.id, question_no=4, question_type="计算题",
                       judgement_final="wrong", confidence_final=0.9,
                       knowledge_point="退位减法", error_type="借位遗漏"),
    ])
    sid = st.id  # noqa
    db.commit(); db.close()
    return sid


def test_export_student_basic(client, tmp_db):
    from backend.database import SessionLocal
    db = SessionLocal()
    sid = _seed(db)
    r = client.get(f"/api/grading/export-student/{sid}?days=0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    # 表头 + 2 个知识点分组行 + 3 行错题
    assert lines[0].startswith("学生：小明")
    assert lines[1] == "【退位减法】" and lines[4] == "【进位加法】"
    assert len(lines) == 6
    assert body.count("口算练习") == 3  # correct 的退位减法行不在


def test_export_student_days_filter(client, tmp_db):
    from backend.database import SessionLocal, Submission, QuestionResult, Class as Cls, Student, Assignment
    from datetime import datetime, timedelta
    db = SessionLocal()
    cls = Cls(name="班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.flush()
    st = Student(name="小红", class_id=cls.id)
    db.add(st); db.flush()
    new_a = Assignment(class_id=cls.id, title="新作业", subject_id=1)
    old_a = Assignment(class_id=cls.id, title="旧作业", subject_id=1,
                       created_at=datetime.utcnow() - timedelta(days=60))
    db.add_all([new_a, old_a]); db.flush()
    for asg in (new_a, old_a):
        sub = Submission(assignment_id=asg.id, student_id=st.id,
                         original_image_path="/tmp/x.jpg", status="completed",
                         created_at=asg.created_at)
        db.add(sub); db.flush()
        db.add(QuestionResult(submission_id=sub.id, question_no=1, question_type="计算题",
                              judgement_final="wrong", confidence_final=0.9))
    sid = st.id
    db.commit(); db.close()
    body30 = client.get(f"/api/grading/export-student/{sid}?days=30").content.decode("utf-8-sig")
    assert "新作业" in body30 and "旧作业" not in body30
    body0 = client.get(f"/api/grading/export-student/{sid}?days=0").content.decode("utf-8-sig")
    assert "旧作业" in body0


def test_export_student_not_found(client, tmp_db):
    r = client.get("/api/grading/export-student/99999")
    assert r.status_code == 404


def test_export_student_all_correct(client, tmp_db):
    """全对的学生导出只有表头"""
    from backend.database import SessionLocal
    db = SessionLocal()
    sid = _seed(db)
    db2 = SessionLocal()
    from backend.database import QuestionResult
    for r0 in db2.query(QuestionResult).all():
        r0.judgement_final = "correct"
    db2.commit(); db2.close()
    body = client.get(f"/api/grading/export-student/{sid}").content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    assert len(lines) == 1 and lines[0].startswith("学生：小明")


def test_dashboard_summary_includes_students(client, tmp_db):
    from backend.database import SessionLocal
    db = SessionLocal()
    sid = _seed(db)
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    students = r.json().get("students") or []
    assert any(s["id"] == sid and s["name"] == "小明" for s in students)


def test_frontend_has_student_wrong_button():
    html = open(os.path.join(ROOT, "frontend", "templates", "index.html"), encoding="utf-8").read()
    assert "export-student/" in html and "学生错题本" in html and "exportStudentWrong" in html
