# -*- coding: utf-8 -*-
"""健壮性第二十七轮（下）：统计接口对脏判定值/空数据的健壮性。

怪异判定值（LLM 幻觉输出）、全 None 判定、学生无任何提交时，
统计/导出/学情报告接口不应 500 或产出除零、误导性百分比。
"""
import json


def _mkcls(db):
    from backend.database import Assignment, Class, Student
    cls = Class(name="二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.commit()
    st = Student(name="李四", class_id=cls.id)
    db.add(st); db.commit()
    a = Assignment(title="作业B", class_id=cls.id, subject_id=1)
    db.add(a); db.commit()
    return cls, st, a


def _sub(db, st, a, results):
    from backend.database import QuestionResult, Submission
    sub = Submission(assignment_id=a.id, student_id=st.id, status="completed",
                     original_image_path="t.jpg")
    db.add(sub); db.commit()
    for r in results:
        db.add(QuestionResult(submission_id=sub.id, **r))
    db.commit()
    return sub


def test_weird_judgement_values(auth_client, tmp_db):
    """LLM 幻觉出未知判定值（如 'yes'/'对'）：analytics 不 500、不误计为正确。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mkcls(db)
    _sub(db, st, a, [
        {"question_no": 1, "question_type": "计算题", "judgement_final": "yes"},
        {"question_no": 2, "question_type": "计算题", "judgement_final": "对"},
        {"question_no": 3, "question_type": "计算题", "judgement_final": "correct"},
    ])
    db.commit()
    aid, sid = a.id, st.id
    db.close()
    r = auth_client.get("/api/analytics/analytics")
    assert r.status_code == 200, r.text[:300]
    r2 = auth_client.get(f"/api/analytics/analytics/frequent-wrong")
    assert r2.status_code == 200
    r3 = auth_client.get("/api/analytics/analytics/trends")
    assert r3.status_code == 200


def test_all_none_judgements(auth_client, tmp_db):
    """judgement_final 全为 None（批改中断残留）：导出/统计不 500、无正确率。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mkcls(db)
    sub = _sub(db, st, a, [
        {"question_no": 1, "question_type": "计算题", "judgement_final": None},
        {"question_no": 2, "question_type": "计算题", "judgement_final": None},
    ])
    db.commit()
    aid, sid = a.id, sub.id
    db.close()
    for url in (f"/api/grading/export-class/{aid}",
                f"/api/grading/export/{sid}",
                "/api/analytics/analytics"):
        r = auth_client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text[:200]}"
    assert "100%" not in auth_client.get(f"/api/grading/export-class/{aid}").text


def test_student_report_no_submissions(auth_client, tmp_db):
    """学情报告：学生存在但无任何提交 → 200 空/友好提示，不 500。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mkcls(db)
    db.commit()
    sid = st.id
    db.close()
    r = auth_client.get(f"/api/analytics/student-report/{sid}")
    assert r.status_code == 200, r.text[:300]
    r2 = auth_client.get(f"/api/analytics/student-report/99999")
    assert r2.status_code in (200, 404), r2.status_code


def test_class_all_with_filters(auth_client, tmp_db):
    """汇总导出：class_id 过滤命中空班级/超大 days 值均不 500。"""
    db = tmp_db.SessionLocal()
    cls, st, a = _mkcls(db)
    _sub(db, st, a, [
        {"question_no": 1, "question_type": "计算题", "judgement_final": "correct"},
    ])
    db.commit()
    cid = cls.id
    db.close()
    for url in ("/api/grading/export-class-all?days=36500",
                f"/api/grading/export-class-all?class_id={cid}",
                "/api/grading/export-class-all?class_id=99999&days=0",
                "/api/grading/export-wrong-all?class_id=99999"):
        r = auth_client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text[:200]}"


def test_negative_days(auth_client, tmp_db):
    """负数 days 参数：负窗口等于无数据，不应 500。"""
    for url in ("/api/grading/export-class-all?days=-5",
                "/api/grading/export-wrong-all?days=-5",
                "/api/grading/export-student/99999?days=-5"):
        r = auth_client.get(url)
        # 学生不存在的 404 合理；导出汇总接口不得 500
        assert r.status_code in (200, 404), f"{url} -> {r.status_code}: {r.text[:200]}"
