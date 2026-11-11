# -*- coding: utf-8 -*-
"""待办 o：AI 一致率统计（teacher_override 与 judgement_raw 对账）— pytest 原生版"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def agreement_env():
    """独立临时库 + get_db 覆盖，结束恢复原状。"""
    import backend.database as dbmod
    from backend.app import app
    from backend.database import get_db

    tmp = tempfile.mkdtemp(prefix="rp_agree_")
    url = "sqlite:///" + os.path.join(tmp, "test_agreement.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.DATABASE_URL = url
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
    if hasattr(dbmod, "DATABASE_URL"):
        del dbmod.DATABASE_URL
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def client(agreement_env):
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
    rows = [
        # (raw, final, override) → 期望一致口径
        ("correct", "correct", None),        # 未复核，不计
        ("wrong", "wrong", "wrong"),         # 复核确认一致
        ("correct", "wrong", "wrong"),       # 复核改判（不一致）
        ("wrong", "correct", "correct"),     # 复核改判（不一致）
        ("partial", "partial", "partial"),   # 确认一致
    ]
    for raw, fin, ov in rows:
        db.add(QuestionResult(submission_id=sub.id, question_no=1, question_type="choice",
                              judgement_raw=raw, judgement_final=fin, teacher_override=ov,
                              confidence_final=0.9))
    db.commit()
    sub_id = sub.id
    db.close()
    return sub_id


def test_ai_agreement_stats(client, agreement_env):
    dbmod = agreement_env
    _seed(dbmod)

    r = client.get("/api/analytics/analytics")
    assert r.status_code == 200
    ag = r.json().get("ai_agreement")
    assert ag is not None
    assert ag["reviewed_total"] == 4
    assert ag["agree"] == 2
    assert ag["disagree"] == 2
    assert ag["rate"] == 0.5


def test_ai_agreement_no_overrides(client, agreement_env):
    from backend.database import QuestionResult

    dbmod = agreement_env
    _seed(dbmod)
    db = dbmod.SessionLocal()
    db.query(QuestionResult).update({QuestionResult.teacher_override: None})
    db.commit()
    db.close()
    ag = client.get("/api/analytics/analytics").json()["ai_agreement"]
    assert ag["rate"] is None
    assert ag["reviewed_total"] == 0


def test_ai_agreement_all_agree(client, agreement_env):
    from backend.database import QuestionResult

    dbmod = agreement_env
    _seed(dbmod)
    db = dbmod.SessionLocal()
    db.query(QuestionResult).update({
        QuestionResult.judgement_final: QuestionResult.judgement_raw,
        QuestionResult.teacher_override: QuestionResult.judgement_raw})
    db.commit()
    db.close()
    ag = client.get("/api/analytics/analytics").json()["ai_agreement"]
    assert ag["rate"] == 1.0


def test_ai_agreement_override_endpoint(client, agreement_env):
    from backend.database import QuestionResult

    dbmod = agreement_env
    _seed(dbmod)
    db = dbmod.SessionLocal()
    qr = db.query(QuestionResult).first()
    qr_id = qr.id
    db.close()
    r = client.post("/api/analytics/override",
                    json={"question_result_id": qr_id, "judgement": "wrong"})
    assert r.status_code == 200
    db = dbmod.SessionLocal()
    qr = db.query(QuestionResult).get(qr_id)
    assert qr.judgement_raw is not None
    raw0 = qr.judgement_raw
    db.close()
    client.post("/api/analytics/override",
                json={"question_result_id": qr_id,
                      "judgement": raw0 if raw0 in ("correct", "wrong", "partial") else "correct"})
    ag = client.get("/api/analytics/analytics").json()["ai_agreement"]
    assert ag["reviewed_total"] >= 1
