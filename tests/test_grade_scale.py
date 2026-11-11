# -*- coding: utf-8 -*-
"""待办 n：作业等级换算测试（grade_scale + settings 接口 + 看板/学情/列表联动）— pytest 原生版"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def grade_env():
    """独立临时库，结束恢复原状（模块级函数直接用 backend.database.SessionLocal）。"""
    import backend.database as dbmod
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_grade_")
    url = "sqlite:///" + os.path.join(tmp, "test_grade.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()
    yield dbmod
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def seeded_db(grade_env):
    """一个作业两份提交：s1 2/3 正确率（含一题 needs_review），s2 一题未判定。"""
    from backend.database import Assignment, QuestionResult, Submission

    db = grade_env.SessionLocal()
    a = Assignment(class_id=1, title="单元测试卷", subject_id=1)
    db.add(a)
    db.commit()
    s1 = Submission(assignment_id=a.id, student_id=1,
                    original_image_path="/tmp/x.jpg", status="completed")
    s2 = Submission(assignment_id=a.id, student_id=2,
                    original_image_path="/tmp/y.jpg", status="completed")
    db.add_all([s1, s2])
    db.commit()
    db.add_all([
        QuestionResult(submission_id=s1.id, question_no=1, question_type="fill",
                       judgement_final="correct"),
        QuestionResult(submission_id=s1.id, question_no=2, question_type="fill",
                       judgement_final="wrong"),
        QuestionResult(submission_id=s1.id, question_no=3, question_type="fill",
                       judgement_final="correct", needs_review=1),
        QuestionResult(submission_id=s2.id, question_no=1, question_type="fill",
                       judgement_final="correct"),
        QuestionResult(submission_id=s2.id, question_no=2, question_type="fill",
                       judgement_final=None),  # 未判定不计
    ])
    db.commit()
    ids = (a.id, s1.id, s2.id)
    db.close()
    return ids  # close 后对象过期，返回主键


@pytest.fixture()
def client(grade_env):
    """TestClient + PIN cookie（settings 全路由 require_admin），TestClient 不触发 startup。"""
    from backend.app import app

    c = TestClient(app)
    c.post("/api/auth/pin-login", json={"pin": "123456"})
    return c


# ---------- 1. 默认阈值 ----------
def test_default_thresholds(grade_env):
    from backend.models.grade_scale import get_thresholds

    db = grade_env.SessionLocal()
    try:
        assert get_thresholds(db) == {"A": 90, "B": 75, "C": 60}
    finally:
        db.close()


# ---------- 2. compute_grade 边界 ----------
def test_compute_grade_boundaries():
    from backend.models.grade_scale import DEFAULT_THRESHOLDS, compute_grade

    t = DEFAULT_THRESHOLDS
    assert compute_grade(0.90, t) == "A"
    assert compute_grade(0.899, t) == "B"
    assert compute_grade(0.75, t) == "B"
    assert compute_grade(0.60, t) == "C"
    assert compute_grade(0.59, t) == "D"
    assert compute_grade(None, t) is None
    assert compute_grade(1.5, t) is None


# ---------- 3. 阈值校验、保存与持久化 ----------
def test_threshold_validation_and_save(grade_env):
    from backend.models.grade_scale import _validate, get_thresholds, save_thresholds

    assert _validate({"A": 70, "B": 80, "C": 60}) is None  # A<B 非法
    assert _validate({"A": 120, "B": 75, "C": 60}) is None  # 超 100 非法

    db = grade_env.SessionLocal()
    try:
        save_thresholds(db, {"A": 85, "B": 70, "C": 50})
        assert get_thresholds(db) == {"A": 85, "B": 70, "C": 50}
        assert _validate({"A": 70, "B": 80, "C": 60}) is None
        from backend.models.grade_scale import compute_grade
        assert compute_grade(0.72, get_thresholds(db)) == "B"
        save_thresholds(db, {"A": 90, "B": 75, "C": 60})  # 恢复默认
    finally:
        db.close()


# ---------- 4. 正确率与等级换算 ----------
def test_accuracy_and_grade(seeded_db, grade_env):
    from backend.models.grade_scale import (
        accuracy_for_assignment, accuracy_for_submission, compute_grade, get_thresholds,
    )

    a_id, s1_id, s2_id = seeded_db
    db = grade_env.SessionLocal()
    try:
        assert abs(accuracy_for_submission(db, s1_id) - 2 / 3) < 1e-9
        assert accuracy_for_submission(db, s2_id) == 1.0  # 未判定题不计入
        acc_a, n_q = accuracy_for_assignment(db, a_id)
        assert abs(acc_a - 0.75) < 1e-9 and n_q == 4
        assert compute_grade(acc_a, get_thresholds(db)) == "B"
    finally:
        db.close()


# ---------- 5. API 接口联动 ----------
def test_settings_grade_api(client, grade_env):
    from backend.models.grade_scale import get_thresholds, save_thresholds

    r = client.get("/api/settings/grade")
    assert r.status_code == 200
    assert r.json()["thresholds"]["A"] == 90

    r = client.post("/api/settings/grade", json={"a": 90, "b": 80, "c": 85})
    assert r.status_code == 400  # A<B 非法

    r = client.post("/api/settings/grade", json={"a": 88, "b": 72, "c": 55})
    assert r.status_code == 200
    assert r.json()["thresholds"] == {"A": 88, "B": 72, "C": 55}

    db = grade_env.SessionLocal()
    try:
        assert get_thresholds(db)["A"] == 88  # 持久化生效
        save_thresholds(db, {"A": 90, "B": 75, "C": 60})
    finally:
        db.close()


def test_dashboard_summary_grade(client, seeded_db):
    a_id, _, _ = seeded_db
    r = client.get("/api/dashboard/summary")
    ag = r.json().get("assignment_grades", [])
    assert any(x["assignment_id"] == a_id and x["grade"] == "B" for x in ag)


def test_mobile_list_grade(client, seeded_db):
    _, s1_id, _ = seeded_db
    r = client.get("/api/mobile/list")
    row = next((x for x in r.json()["submissions"] if x["id"] == s1_id), None)
    assert row is not None
    assert row["grade"] == "C"
    assert abs(row["accuracy"] - 0.667) < 0.01


def test_analytics_student_grade(client, seeded_db):
    r = client.get("/api/analytics/analytics")
    stu = next((x for x in r.json()["students"] if x["student_id"] == 1), None)
    assert stu is not None
    assert stu["grade"] == "C"
