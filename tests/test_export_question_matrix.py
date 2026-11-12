# -*- coding: utf-8 -*-
"""按作业导出学生×题号得分矩阵 CSV：单元格符号、正确率行、404、前端按钮"""
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
                      original_image_path="/tmp/a.jpg", status="completed")
    sub2 = Submission(assignment_id=asg.id, student_id=s2.id,
                      original_image_path="/tmp/b.jpg", status="completed")
    db.add_all([sub1, sub2]); db.flush()
    db.add_all([
        # 小明：题1对、题2错、题3复核
        QuestionResult(submission_id=sub1.id, question_no=1, question_type="选择题", judgement_final="correct", confidence_final=0.9),
        QuestionResult(submission_id=sub1.id, question_no=2, question_type="计算题", judgement_final="wrong", confidence_final=0.9),
        QuestionResult(submission_id=sub1.id, question_no=3, question_type="计算题", judgement_final="correct", needs_review=1, confidence_final=0.5),
        # 小红：题1对、题2部分正确（无题3 → 空格）
        QuestionResult(submission_id=sub2.id, question_no=1, question_type="选择题", judgement_final="correct", confidence_final=0.9),
        QuestionResult(submission_id=sub2.id, question_no=2, question_type="计算题", judgement_final="partial", confidence_final=0.9),
    ])
    db.commit()
    yield asg.id
    db.close()


def test_matrix_cells_and_rates(client, seeded):
    aid = seeded
    r = client.get(f"/api/grading/export-question-matrix/{aid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "filename*" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    # 标题行 + 表头行 + 2 名学生 + 正确率行 + 正确数行
    assert len(lines) == 6
    assert lines[0].startswith("得分矩阵：口算练习")
    assert lines[1] == "学生,题1,题2,题3"
    # 小明：题1√ 题2× 题3复核
    assert "小明,√,×,复核" in lines[2]
    # 小红：题1√ 题2△ 题3空
    assert "小红,√,△," in lines[3]
    # 正确率：题1 100%；题2 分母=√+△+×=2、正确0 → 0%；题3 复核不计入 → 空
    assert lines[4] == "正确率,100%,0%,"
    assert lines[5] == "正确数,2,0,"


def test_matrix_404_without_assignment(client, tmp_db):
    r = client.get("/api/grading/export-question-matrix/9999")
    assert r.status_code == 404


def test_matrix_404_without_results(client, tmp_db):
    from backend.database import SessionLocal, Class as Cls, Assignment
    db = SessionLocal()
    cls = Cls(name="班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.flush()
    asg = Assignment(class_id=cls.id, title="空作业", subject_id=1)
    db.add(asg); db.commit()
    aid = asg.id
    db.close()
    r = client.get(f"/api/grading/export-question-matrix/{aid}")
    assert r.status_code == 404
    assert "暂无批改结果" in r.json()["detail"]


def test_frontend_matrix_button(client, seeded):
    r = client.get("/")
    assert r.status_code == 200
    assert "export-question-matrix" in r.text
