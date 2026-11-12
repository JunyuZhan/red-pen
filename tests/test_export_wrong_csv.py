# -*- coding: utf-8 -*-
"""全班错题讲评清单导出：一行一题、知识点薄弱优先分组、排除 correct、days/class_id 过滤"""
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


def test_export_wrong_all_basic(client, tmp_db):
    """错题/部分正确/无法辨认/需复核进清单，correct 排除；薄弱知识点（错误多）排前"""
    from backend.database import SessionLocal, Submission, QuestionResult, Class as Cls, Student, Assignment
    db = SessionLocal()
    cls = Cls(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.flush()
    s1 = Student(name="小明", class_id=cls.id)
    s2 = Student(name="小红", class_id=cls.id)
    db.add_all([s1, s2]); db.flush()
    asg = Assignment(class_id=cls.id, title="口算练习", subject_id=1)
    db.add(asg); db.flush()
    sub1 = Submission(assignment_id=asg.id, student_id=s1.id,
                      original_image_path="/tmp/a.jpg", status="completed")
    sub2 = Submission(assignment_id=asg.id, student_id=s2.id,
                      original_image_path="/tmp/b.jpg", status="completed")
    db.add_all([sub1, sub2]); db.flush()
    db.add_all([
        # 进位加法：1 错（小明）；退位减法：2 错（小明 wrong + 小红 partial）→ 退位减法更薄弱排前
        QuestionResult(submission_id=sub1.id, question_no=1, question_type="选择题",
                       judgement_final="correct", confidence_final=0.9, knowledge_point="进位加法"),
        QuestionResult(submission_id=sub1.id, question_no=2, question_type="计算题",
                       judgement_final="wrong", confidence_final=0.9,
                       knowledge_point="进位加法", error_type="计算错误"),
        QuestionResult(submission_id=sub1.id, question_no=3, question_type="计算题",
                       judgement_final="wrong", confidence_final=0.9,
                       knowledge_point="退位减法", error_type="借位遗漏"),
        QuestionResult(submission_id=sub2.id, question_no=1, question_type="计算题",
                       judgement_final="partial", confidence_final=0.9,
                       knowledge_point="退位减法", error_type="步骤不全"),
        # correct 的退位减法不计入
        QuestionResult(submission_id=sub2.id, question_no=2, question_type="计算题",
                       judgement_final="correct", confidence_final=0.9,
                       knowledge_point="退位减法"),
    ])
    db.commit(); db.close()
    r = client.get("/api/grading/export-wrong-all?days=0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    body = r.content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    assert lines[0] == ("作业,班级,学生,题号,学生答案,参考答案,判定,错因,"
                        "知识点(原始),需复核")
    # 3 行错题，退位减法（2 错）组在前、进位加法（1 错）在后
    assert len(lines) == 4
    assert "退位减法" in lines[1] and "退位减法" in lines[2]
    assert "进位加法" in lines[3]
    # correct 的那行不在清单里
    assert body.count("口算练习") == 3
    # 组内按学生、题号排：小明(题3) 在 小红(题1) 前
    assert "小明" in lines[1] and "小红" in lines[2]


def test_export_wrong_all_days_filter(client, tmp_db):
    from backend.database import SessionLocal, Submission, QuestionResult, Class as Cls, Student, Assignment
    from datetime import datetime, timedelta
    db = SessionLocal()
    cls = Cls(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.flush()
    s1 = Student(name="小明", class_id=cls.id)
    db.add(s1); db.flush()
    new_a = Assignment(class_id=cls.id, title="新作业", subject_id=1)
    old_a = Assignment(class_id=cls.id, title="旧作业", subject_id=1,
                       created_at=datetime.utcnow() - timedelta(days=60))
    db.add_all([new_a, old_a]); db.flush()
    for asg in (new_a, old_a):
        sub = Submission(assignment_id=asg.id, student_id=s1.id,
                         original_image_path="/tmp/x.jpg", status="completed")
        db.add(sub); db.flush()
        db.add(QuestionResult(submission_id=sub.id, question_no=1, question_type="计算题",
                              judgement_final="wrong", confidence_final=0.9))
    db.commit(); db.close()
    r = client.get("/api/grading/export-wrong-all?days=30")
    body = r.content.decode("utf-8-sig")
    assert "新作业" in body and "旧作业" not in body
    r2 = client.get("/api/grading/export-wrong-all?days=0")
    assert "旧作业" in r2.content.decode("utf-8-sig")


def test_export_wrong_all_class_filter(client, tmp_db):
    from backend.database import SessionLocal, Submission, QuestionResult, Class as Cls, Student, Assignment
    db = SessionLocal()
    c1 = Cls(name="一班", teacher_id=1, stage_id=1, subject_id=1)
    c2 = Cls(name="二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add_all([c1, c2]); db.flush()
    s1 = Student(name="甲", class_id=c1.id)
    s2 = Student(name="乙", class_id=c2.id)
    db.add_all([s1, s2]); db.flush()
    a1 = Assignment(class_id=c1.id, title="一班作业", subject_id=1)
    a2 = Assignment(class_id=c2.id, title="二班作业", subject_id=1)
    db.add_all([a1, a2]); db.flush()
    for asg, s in ((a1, s1), (a2, s2)):
        sub = Submission(assignment_id=asg.id, student_id=s.id,
                         original_image_path="/tmp/x.jpg", status="completed")
        db.add(sub); db.flush()
        db.add(QuestionResult(submission_id=sub.id, question_no=1, question_type="计算题",
                              judgement_final="wrong", confidence_final=0.9))
    c1_id = c1.id
    db.commit(); db.close()
    r = client.get(f"/api/grading/export-wrong-all?days=0&class_id={c1_id}")
    body = r.content.decode("utf-8-sig")
    assert "甲" in body and "乙" not in body


def test_export_wrong_all_empty(client, tmp_db):
    """没有错题（全对）时只有表头"""
    r = client.get("/api/grading/export-wrong-all")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    assert len(lines) == 1 and lines[0].startswith("作业,班级,学生")


def test_frontend_has_wrong_export_button():
    html = open(os.path.join(ROOT, "frontend", "templates", "index.html"), encoding="utf-8").read()
    assert "export-wrong-all" in html and "错题讲评清单" in html
