# -*- coding: utf-8 -*-
"""按作业导出全班成绩单 CSV：每行一名学生、统计正确、404、前端按钮存在"""
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


@pytest.fixture()
def seeded(tmp_db):
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
                      original_image_path="/tmp/a.jpg", status="completed",
                      duration_seconds=12.3)
    sub2 = Submission(assignment_id=asg.id, student_id=s2.id,
                      original_image_path="/tmp/b.jpg", status="completed")
    db.add_all([sub1, sub2]); db.flush()
    db.add_all([
        QuestionResult(submission_id=sub1.id, question_no=1, question_type="选择题", judgement_final="correct", confidence_final=0.9),
        QuestionResult(submission_id=sub1.id, question_no=2, question_type="计算题", judgement_final="wrong", confidence_final=0.9),
        QuestionResult(submission_id=sub1.id, question_no=3, question_type="计算题", judgement_final="correct", needs_review=1, confidence_final=0.5),
        QuestionResult(submission_id=sub2.id, question_no=1, question_type="选择题", judgement_final="correct", confidence_final=0.9),
    ])
    db.commit()
    yield asg.id
    db.close()


def test_export_class_csv(client, seeded):
    aid = seeded
    r = client.get(f"/api/grading/export-class/{aid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    assert body.startswith("作业：口算练习")
    lines = [l for l in body.strip().split("\r\n") if l]
    assert len(lines) == 3  # 表头 + 2 名学生
    assert lines[1].startswith("小明") and lines[2].startswith("小红")
    # 小明：3 题对 2 错 1、正确率 67%、等级 C、需复核 1
    assert ",3,2,1,0,0,67%,C,1,12.3," in lines[1]
    # 小红：1 题对 1、正确率 100%、等级 A
    assert ",1,1,0,0,0,100%,A,," in lines[2]


def test_export_class_csv_404(client):
    assert client.get("/api/grading/export-class/99999").status_code == 404


def test_export_class_csv_empty(client, tmp_db):
    from backend.database import SessionLocal, Assignment
    db = SessionLocal()
    asg = Assignment(class_id=1, title="空作业", subject_id=1)
    db.add(asg); db.commit()
    aid = asg.id
    db.close()
    r = client.get(f"/api/grading/export-class/{aid}")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    assert len(lines) == 1  # 只有表头


def test_frontend_has_class_export():
    html = open(os.path.join(ROOT, "frontend", "templates", "index.html"), encoding="utf-8").read()
    assert "classExportBar" in html and "/api/grading/export-class/" in html


# ============ 跨作业合并导出 ============

def test_export_class_all_csv(client, tmp_db):
    from backend.database import SessionLocal, Submission, QuestionResult, Class as Cls, Student, Assignment
    from datetime import datetime, timedelta
    db = SessionLocal()
    cls = Cls(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.flush()
    s1 = Student(name="小明", class_id=cls.id)
    s2 = Student(name="小红", class_id=cls.id)
    db.add_all([s1, s2]); db.flush()
    a1 = Assignment(class_id=cls.id, title="口算练习", subject_id=1)
    a2 = Assignment(class_id=cls.id, title="单元测", subject_id=1)
    old = Assignment(class_id=cls.id, title="很久以前", subject_id=1,
                     created_at=datetime.utcnow() - timedelta(days=60))
    db.add_all([a1, a2, old]); db.flush()
    for asg, s, acc_res in ((a1, s1, "wrong"), (a2, s1, "correct"), (a1, s2, "correct")):
        sub = Submission(assignment_id=asg.id, student_id=s.id,
                         original_image_path="/tmp/x.jpg", status="completed")
        db.add(sub); db.flush()
        db.add(QuestionResult(submission_id=sub.id, question_no=1, question_type="选择题",
                              judgement_final=acc_res, confidence_final=0.9))
    db.commit()
    r = client.get("/api/grading/export-class-all?days=30")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    assert lines[0].startswith("学生,口算练习,单元测,平均正确率,平均等级")
    # 小明：口算 0%，单元测 100% → 平均 50%
    assert lines[1] == "小明,0%,100%,50%,D"
    # 小红：只有口算 100%
    assert lines[2] == "小红,100%,-,100%,A"
    # days=0 应包含很久以前的作业
    r2 = client.get("/api/grading/export-class-all?days=0")
    body2 = r2.content.decode("utf-8-sig")
    assert "很久以前" in body2
    # days 过滤 30 天内不含旧作业
    assert "很久以前" not in body
    db.close()


def test_export_class_all_csv_empty(client, tmp_db):
    r = client.get("/api/grading/export-class-all")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert body.startswith("学生,平均正确率,平均等级")


def test_frontend_has_class_all_export():
    html = open(os.path.join(ROOT, "frontend", "templates", "index.html"), encoding="utf-8").read()
    assert "export-class-all" in html
