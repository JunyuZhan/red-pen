# -*- coding: utf-8 -*-
"""批改结果导出 CSV：UTF-8 BOM、表头/行内容、题号排序、404、前端按钮存在"""
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
    stu = Student(name="小明", class_id=cls.id)
    db.add(stu); db.flush()
    asg = Assignment(class_id=cls.id, title="口算练习", subject_id=1)
    db.add(asg); db.flush()
    sub = Submission(assignment_id=asg.id, student_id=stu.id,
                     original_image_path="/tmp/fake.jpg", status="completed")
    db.add(sub); db.flush()
    db.add_all([
        QuestionResult(submission_id=sub.id, question_no=2, question_type="计算题",
                       student_answer_text="6", correct_answer_text="7",
                       judgement_final="wrong", error_type="进位错误",
                       confidence_final=0.92, knowledge_point="两位数加法", needs_review=0),
        QuestionResult(submission_id=sub.id, question_no=1, question_type="选择题",
                       student_answer_text="B", correct_answer_text="B",
                       judgement_final="correct", confidence_final=0.95),
    ])
    db.commit()
    yield sub.id
    db.close()


def test_export_csv_content(client, seeded):
    sid = seeded
    r = client.get(f"/api/grading/export/{sid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")  # BOM 应存在
    assert body.startswith("题号,题型,学生答案,参考答案,判定,错误类型,置信度,知识点,需复核,教师复核")
    lines = [l for l in body.strip().split("\r\n") if l]
    assert len(lines) == 3
    assert lines[1].startswith("1,")  # 题号升序
    assert "正确" in lines[1] and "B" in lines[1]
    assert lines[2].startswith("2,")
    assert "错误" in lines[2] and "进位错误" in lines[2] and "两位数加法" in lines[2]


def test_export_csv_404(client):
    assert client.get("/api/grading/export/99999").status_code == 404


def test_frontend_has_export_button():
    html = open(os.path.join(ROOT, "frontend", "templates", "index.html"), encoding="utf-8").read()
    assert "exportCsvBtn" in html and "/api/grading/export/" in html
