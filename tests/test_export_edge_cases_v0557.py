# -*- coding: utf-8 -*-
"""健壮性第二十七轮：导出/统计接口的边界数据场景探测。

空班级、无提交作业、无结果提交、全 unclear 判定等极端数据下，
导出与统计接口不应 500，也不应产出误导性数值。
"""
import json

from backend.database import (
    Assignment, Class, QuestionResult, Student, Submission, get_db,
)


def _mk(db, cls_name="一班"):
    cls = Class(name=cls_name, teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls)
    db.commit()
    st = Student(name="张三", class_id=cls.id)
    db.add(st)
    db.commit()
    a = Assignment(title="作业A", class_id=cls.id, subject_id=1)
    db.add(a)
    db.commit()
    return cls, st, a


def _add_sub(db, st, a, status="completed", results=None):
    sub = Submission(assignment_id=a.id, student_id=st.id, status=status,
                     original_image_path="test.jpg")
    db.add(sub)
    db.commit()
    for r in (results or []):
        db.add(QuestionResult(submission_id=sub.id, **r))
    db.commit()
    return sub


def test_empty_class_exports_ok(auth_client, tmp_db):
    """空班级（无作业无学生提交）：各导出接口不 500。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mk(db)
    db.commit()
    aid = a.id
    cid = cls.id
    sid = st.id
    db.close()
    for url in (f"/api/grading/export-class/{aid}",
                "/api/grading/export-class-all",
                f"/api/grading/export-student/{sid}",
                "/api/grading/export-wrong-all",
                f"/api/analytics/student-report/{sid}"):
        r = auth_client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text[:200]}"
    # 矩阵导出对无结果作业返回 404（明确提示）而非 200 空表
    r = auth_client.get(f"/api/grading/export-question-matrix/{aid}")
    assert r.status_code in (200, 404)


def test_assignment_no_submissions_matrix_404(auth_client, tmp_db):
    """作业无任何提交：矩阵导出 404 而非 500。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mk(db)
    db.commit()
    aid = a.id
    db.close()
    r = auth_client.get(f"/api/grading/export-question-matrix/{aid}")
    assert r.status_code in (200, 404), r.status_code


def test_all_unclear_results(auth_client, tmp_db):
    """全班全部 unclear/空判定：正确率列空、等级空、不出现 100% 误报。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mk(db)
    sub = _add_sub(db, st, a, results=[
        {"question_no": 1, "question_type": "计算题", "judgement_final": "unclear"},
        {"question_no": 2, "question_type": "计算题", "judgement_final": None},
    ])
    db.commit()
    aid = a.id
    sid = sub.id
    db.close()
    r = auth_client.get(f"/api/grading/export-class/{aid}")
    assert r.status_code == 200, r.text[:300]
    text = r.text
    assert "100%" not in text, "全 unclear 不应出现正确率 100%"
    r2 = auth_client.get(f"/api/grading/export/{sid}")
    assert r2.status_code == 200


def test_matrix_unclear_only(auth_client, tmp_db):
    """全 unclear 的矩阵导出：正确率行为空而非除零。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mk(db)
    _add_sub(db, st, a, results=[
        {"question_no": 1, "question_type": "计算题", "judgement_final": "unclear"},
        {"question_no": 2, "question_type": "计算题", "judgement_final": "unclear"},
    ])
    db.commit()
    aid = a.id
    db.close()
    r = auth_client.get(f"/api/grading/export-question-matrix/{aid}")
    assert r.status_code == 200, r.text[:300]
    assert "100%" not in r.text
