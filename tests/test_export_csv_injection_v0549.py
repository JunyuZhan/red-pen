# -*- coding: utf-8 -*-
"""CSV 导出防公式注入（健壮性第十三轮）：
= / + / @ 开头或 +/-数字开头的单元格必须加前导单引号，
防止 Excel/WPS 打开导出文件时被当公式执行（学生名/作业标题/AI 识别答案都可能以这些字符开头）。
合法负数与普通文本不受影响。"""
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
def seeded_evil(tmp_db):
    from backend.database import SessionLocal, Submission, QuestionResult, Class as Cls, Student, Assignment
    db = SessionLocal()
    cls = Cls(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.flush()
    # 学生名/作业标题都带公式头字符
    s1 = Student(name="=HYPERLINK(\"http://evil\")", class_id=cls.id)
    s2 = Student(name="-2号学生", class_id=cls.id)
    db.add_all([s1, s2]); db.flush()
    asg = Assignment(class_id=cls.id, title="@cmd测试", subject_id=1)
    db.add(asg); db.flush()
    sub1 = Submission(assignment_id=asg.id, student_id=s1.id,
                      original_image_path="/tmp/a.jpg", status="completed")
    db.add_all([sub1]); db.flush()
    db.add(QuestionResult(submission_id=sub1.id, question_no=1, question_type="计算题",
                          judgement_final="wrong", student_answer_text="+SUM(A1)",
                          confidence_final=0.9))
    db.commit()
    yield asg.id, s1.id
    db.close()


def test_unit_csv_cell_sanitizes_and_keeps_negatives():
    from backend.routes.grading import _csv_cell
    assert _csv_cell("=cmd|'/c calc'") == "'=cmd|'/c calc'"
    assert _csv_cell("+SUM(A1)") == "'+SUM(A1)"
    assert _csv_cell("@cmd") == "'@cmd"
    assert _csv_cell("\tTAB") == "'\tTAB"
    # - / + 开头（含负数形态）一律拦截，OWASP 建议；统计数字列是数值类型不受影响
    assert _csv_cell("-3.5") == "'-3.5"
    assert _csv_cell("正常文本") == "正常文本"
    # 非字符串原样返回
    assert _csv_cell(123) == 123
    assert _csv_cell(None) is None
    # ±字母开头（如 +86 手机号场景的变体）也拦截
    assert _csv_cell("+abc") == "'+abc"
    assert _csv_cell("-abc") == "'-abc"


def test_export_class_csv_formula_injection(client, seeded_evil):
    aid, _sid = seeded_evil
    r = client.get(f"/api/grading/export-class/{aid}")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    lines = [l for l in body.strip().split("\r\n") if l]
    data_row = [l for l in lines if "HYPERLINK" in l][0]
    # csv 引号包裹后单元格内容仍以 '= 开头（Excel/WPS 不会当公式执行）
    assert '"\'=' in data_row


def test_export_student_wrong_csv_sanitized(client, seeded_evil):
    _aid, sid = seeded_evil
    r = client.get(f"/api/grading/export-student/{sid}")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert "'+SUM(A1)" in body  # 学生答案 +SUM(A1) 不再以裸 + 开头


def test_export_question_matrix_csv_sanitized(client, seeded_evil):
    aid, _sid = seeded_evil
    r = client.get(f"/api/grading/export-question-matrix/{aid}")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    # 学生名出现在列头，须被中和
    assert "'=HYPERLINK" in body


def test_export_result_csv_sanitized(client, seeded_evil):
    from backend.database import SessionLocal, Submission
    db = SessionLocal()
    sub = db.query(Submission).first()
    sid = sub.id
    db.close()
    r = client.get(f"/api/grading/export/{sid}")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert "'+SUM(A1)" in body
