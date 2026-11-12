# -*- coding: utf-8 -*-
"""AI 学生学情报告（待办 k）测试：统计收集、prompt 约束、生成入库、路由行为"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models import study_report
from backend.models.study_report import build_prompt, collect_student_stats, get_latest_report


def _seed(tmp_db, n_subs=2):
    """给临时库灌一个学生 2 次提交、另一个学生 1 次提交的数据"""
    from backend.database import Assignment, QuestionResult, SessionLocal, Student, Submission

    db = SessionLocal()
    a = Assignment(class_id=1, title="口算练习", subject_id=1)
    db.add(a)
    db.commit()
    s1 = Student(name="张三", class_id=1)
    s2 = Student(name="李四", class_id=1)
    db.add_all([s1, s2])
    db.commit()
    plan = []
    for i in range(n_subs):
        plan.append((s1.id, [("1", "correct", "两位数加法", None),
                             ("2", "wrong", "两位数乘法", "calculation"),
                             ("3", "wrong", "两位数乘法", "concept")]))
    plan.append((s2.id, [("1", "correct", "两位数加法", None),
                         ("2", "correct", "两位数乘法", None),
                         ("3", "correct", "两位数乘法", None)]))
    for sid, items in plan:
        sub = Submission(assignment_id=a.id, student_id=sid,
                         original_image_path="/tmp/x.jpg", status="completed")
        db.add(sub)
        db.commit()
        for no, judge, kp, et in items:
            db.add(QuestionResult(
                submission_id=sub.id, question_no=int(no), question_type="calculation",
                knowledge_point=kp, judgement_raw=judge, judgement_final=judge,
                error_type=et, confidence_final=0.9))
    db.commit()
    ids = {"s1": s1.id, "s2": s2.id}
    db.close()
    return ids


def test_collect_student_stats(tmp_db):
    ids = _seed(tmp_db)
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        stats = collect_student_stats(db, ids["s1"])
    finally:
        db.close()
    assert stats["student_name"] == "张三"
    assert stats["submissions_count"] == 2
    assert stats["total_questions"] == 6
    assert stats["overall_accuracy"] == 0.33
    assert len(stats["accuracy_history"]) == 2
    assert stats["error_types"] == {"calculation": 2, "concept": 2}
    assert stats["weak_knowledge_points"][0]["name"] == "两位数乘法"
    assert len(stats["wrong_questions"]) == 4


def test_collect_student_stats_empty_and_missing(tmp_db):
    ids = _seed(tmp_db)
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        assert collect_student_stats(db, 99999) is None  # 不存在的学生
    finally:
        db.close()
    # 无任何批改数据的新学生
    from backend.database import SessionLocal as SL, Student
    db = SL()
    s = Student(name="王五", class_id=1)
    db.add(s)
    db.commit()
    sid = s.id
    db.close()
    db = SL()
    try:
        assert collect_student_stats(db, sid) is None
    finally:
        db.close()


def test_prompt_locks_to_data(tmp_db):
    ids = _seed(tmp_db)
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        stats = collect_student_stats(db, ids["s1"])
    finally:
        db.close()
    prompt = build_prompt(stats)
    assert "只允许引用输入数据" in prompt
    assert "两位数乘法" in prompt
    assert "张三" in prompt


def test_generate_without_key(tmp_db):
    ids = _seed(tmp_db)
    from backend.database import AppConfig, SessionLocal
    from backend.models.grading_engine import save_llm_config
    save_llm_config(provider="custom", base_url="https://example.com/v1",
                    model="m", api_key="tmp")
    db = SessionLocal()
    row = db.query(AppConfig).filter(AppConfig.key == "llm_api_key").first()
    row.value = ""
    db.commit()
    db.close()
    res = asyncio.run(study_report.generate_student_report(ids["s1"]))
    assert not res["success"]
    assert "API Key" in res["error"]


def test_generate_no_data(tmp_db):
    from backend.database import SessionLocal, Student
    from backend.models.grading_engine import save_llm_config
    save_llm_config(provider="custom", base_url="https://example.com/v1",
                    model="m", api_key="k")
    db = SessionLocal()
    s = Student(name="无数据生", class_id=1)
    db.add(s)
    db.commit()
    sid = s.id
    db.close()
    res = asyncio.run(study_report.generate_student_report(sid))
    assert not res["success"]
    assert "暂无批改数据" in res["error"]


def test_generate_success_persists(tmp_db):
    ids = _seed(tmp_db)
    from backend.database import SessionLocal, StudyReport
    from backend.models.grading_engine import save_llm_config
    save_llm_config(provider="custom", base_url="https://example.com/v1",
                    model="test-model", api_key="k")

    class FakeResp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content":
                '{"summary": "张三两次作业正确率33%，两位数乘法掌握薄弱。",'
                ' "strengths": ["两位数加法已掌握"],'
                ' "weaknesses": ["两位数乘法计算与概念均有错误"],'
                ' "advice": ["加强两位数乘法专项练习", "关注计算过程规范"]}'}}]}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, url, **kw):
            return FakeResp()

    orig = study_report.httpx.AsyncClient
    study_report.httpx.AsyncClient = FakeClient
    try:
        res = asyncio.run(study_report.generate_student_report(ids["s1"]))
    finally:
        study_report.httpx.AsyncClient = orig
    assert res["success"]
    assert res["student_id"] == ids["s1"]
    assert "两位数乘法" in res["report"]["summary"]

    db = SessionLocal()
    try:
        rows = db.query(StudyReport).filter(StudyReport.student_id == ids["s1"]).all()
        assert len(rows) == 1
    finally:
        db.close()
    latest = get_latest_report(ids["s1"])
    assert latest["student_id"] == ids["s1"]
    assert latest["model"] == "test-model"
    assert get_latest_report(99999) is None


def test_routes(tmp_db, auth_client):
    ids = _seed(tmp_db)
    # GET 无报告
    r = auth_client.get(f"/api/analytics/student-report/{ids['s1']}")
    assert r.status_code == 200
    assert r.json()["report"] is None
    # POST 生成：未配 Key → 明确错误，不 500
    r = auth_client.post(f"/api/analytics/student-report/{ids['s1']}/generate")
    assert r.status_code == 200
    body = r.json()
    assert not body["success"] and "API Key" in body["error"]
    # 不存在的学生
    r = auth_client.post("/api/analytics/student-report/99999/generate")
    assert r.status_code == 200
    assert not r.json()["success"]
