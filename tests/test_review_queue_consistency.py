# -*- coding: utf-8 -*-
"""健壮性第26轮：教师复核后复核队列条目状态一致性 — pytest 原生版

Bug：/api/analytics/override 教师改判后不清 needs_review，
复核队列里同一题永远留着（低置信度题尤其如此），count 虚高。
"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def review_env():
    """独立临时库 + get_db 覆盖，结束恢复原状。"""
    import backend.database as dbmod
    from backend.app import app
    from backend.database import get_db

    tmp = tempfile.mkdtemp(prefix="rp_review_q_")
    url = "sqlite:///" + os.path.join(tmp, "test_review_queue.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.Base.metadata.create_all(dbmod.engine)
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)

    def override_get_db():
        s = dbmod.SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield dbmod
    app.dependency_overrides.pop(get_db, None)
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def client(review_env):
    from backend.app import app
    from backend.config import settings

    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200
    return c


def _seed(dbmod):
    from backend.database import Assignment, Class, QuestionResult, Student, Submission

    db = dbmod.SessionLocal()
    dbmod.Base.metadata.drop_all(dbmod.engine)
    dbmod.Base.metadata.create_all(dbmod.engine)
    cls = Class(name="一班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls)
    db.commit()
    stu = Student(name="张三", class_id=cls.id)
    db.add(stu)
    db.commit()
    asg = Assignment(class_id=cls.id, title="测试作业", subject_id=1)
    db.add(asg)
    db.commit()
    sub = Submission(assignment_id=asg.id, student_id=stu.id,
                     original_image_path="test.jpg", status="done")
    db.add(sub)
    db.commit()
    # q1: needs_review=1 低置信度；q2: 低置信度但无标记
    q1 = QuestionResult(submission_id=sub.id, question_no=1, question_type="choice",
                        judgement_raw="wrong", judgement_final="wrong",
                        confidence_final=0.4, needs_review=1)
    q2 = QuestionResult(submission_id=sub.id, question_no=2, question_type="choice",
                        judgement_raw="correct", judgement_final="correct",
                        confidence_final=0.5)
    db.add_all([q1, q2])
    db.commit()
    ids = (q1.id, q2.id, sub.id)
    db.close()
    return ids


def _queue(client):
    r = client.get("/api/analytics/analytics")
    assert r.status_code == 200
    data = r.json()
    return data["needs_review_count"], {x["id"] for x in data["needs_review"]}


def test_override_clears_needs_review_flag(client, review_env):
    dbmod = review_env
    q1_id, q2_id, _ = _seed(dbmod)

    count, ids = _queue(client)
    assert count == 2 and q1_id in ids and q2_id in ids

    # 教师复核 q1 改判
    r = client.post("/api/analytics/override",
                    json={"question_result_id": q1_id, "judgement": "correct"})
    assert r.status_code == 200

    count, ids = _queue(client)
    assert q1_id not in ids, "教师已复核的题不应再出现在复核队列"
    assert q2_id in ids, "未复核的低置信度题应保留"
    assert count == 1, "needs_review_count 应随之下降"

    from backend.database import QuestionResult
    db = dbmod.SessionLocal()
    qr = db.query(QuestionResult).filter(QuestionResult.id == q1_id).first()
    assert qr.needs_review == 0
    assert qr.teacher_override == "correct"
    assert qr.judgement_final == "correct"
    assert qr.review_note and "教师复核" in qr.review_note
    db.close()


def test_override_low_confidence_only_also_leaves_queue(client, review_env):
    """无 needs_review 标记、仅低置信度（<0.6）的题，教师改判后也应出队。"""
    dbmod = review_env
    q1_id, q2_id, _ = _seed(dbmod)

    r = client.post("/api/analytics/override",
                    json={"question_result_id": q2_id, "judgement": "wrong",
                          "error_type": "calculation"})
    assert r.status_code == 200

    count, ids = _queue(client)
    assert q2_id not in ids
    assert q1_id in ids
    assert count == 1


def test_legacy_override_row_not_in_queue(client, review_env):
    """历史遗留：needs_review=1 但已有 teacher_override 的行不应进队列。"""
    dbmod = review_env
    q1_id, _, _ = _seed(dbmod)

    from backend.database import QuestionResult
    db = dbmod.SessionLocal()
    qr = db.query(QuestionResult).filter(QuestionResult.id == q1_id).first()
    qr.teacher_override = "correct"  # 模拟旧数据：已复核但标记没清
    db.commit()
    db.close()

    count, ids = _queue(client)
    assert q1_id not in ids


def test_override_validation_still_works(client, review_env):
    """改判参数校验不受影响。"""
    dbmod = review_env
    q1_id, _, _ = _seed(dbmod)

    r = client.post("/api/analytics/override",
                    json={"question_result_id": q1_id, "judgement": "bad"})
    assert r.status_code == 400

    r = client.post("/api/analytics/override",
                    json={"question_result_id": 999999, "judgement": "correct"})
    assert r.status_code == 404
