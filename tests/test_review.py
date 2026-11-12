# -*- coding: utf-8 -*-
"""PRD 待办 f：低置信度二次复核 —— 阈值触发、一致采纳、不一致标黄进复核队列、设置接口"""
import asyncio
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import backend.models.grading_engine as ge
import backend.models.review as rv
from backend.models.review import cross_check_questions, get_review_config, save_review_config


class FakeEngine:
    """主引擎：1题高置信 + 1题低置信；复核通道结论可编程"""
    calls = {"n": 0}
    secondary_judgement = "correct"
    secondary_answer = "5"

    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        calls = FakeEngine.calls
        calls["n"] += 1
        if calls.get("mode") == "sec" or (calls.get("mode") == "pair" and calls["n"] >= 2):
            return {"success": True, "judgement": self.secondary_judgement, "confidence": 0.9,
                    "analysis": "review", "prompt_tokens": 10, "completion_tokens": 5,
                    "questions": [
                        {"question_no": 2, "judgement": self.secondary_judgement,
                         "confidence": 0.9, "student_answer": self.secondary_answer},
                    ]}
        return {"success": True, "judgement": "correct", "confidence": 0.9,
                "analysis": "OK", "prompt_tokens": 10, "completion_tokens": 5,
                "questions": [
                    {"question_no": 1, "judgement": "correct", "confidence": 0.95, "student_answer": "5"},
                    {"question_no": 2, "judgement": "correct", "confidence": 0.5, "student_answer": "5"},
                ]}


RESULT = {"success": True, "questions": [
    {"question_no": 1, "judgement": "correct", "confidence": 0.95, "student_answer": "5"},
    {"question_no": 2, "judgement": "correct", "confidence": 0.5, "student_answer": "5"},
]}


@pytest.fixture()
def review_env(monkeypatch):
    """独立临时库 + get_db 覆盖 + mock 引擎，结束恢复。"""
    from backend.app import app
    import backend.database as dbmod
    from backend.database import get_db

    tmp = tempfile.mkdtemp(prefix="rp_review_")
    url = "sqlite:///" + os.path.join(tmp, "test_review.db")
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

    db = dbmod.SessionLocal()
    from backend.database import AppConfig
    for k, v in [("llm_provider", "qwen"), ("llm_api_key", "test-key")]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(AppConfig(key=k, value=v))
    db.commit(); db.close()

    old_engines = (ge.GradingEngine, rv.GradingEngine)
    ge.GradingEngine = FakeEngine
    rv.GradingEngine = FakeEngine
    # 批改后台任务/复核模型在模块导入时绑定了原始 SessionLocal，换绑到临时库
    import backend.routes.grading as grading_mod
    patched = [(ge, "SessionLocal"), (rv, "SessionLocal"), (grading_mod, "SessionLocal")]
    saved = []
    for m, k in patched:
        if hasattr(m, k):
            saved.append((m, k, getattr(m, k)))
            setattr(m, k, dbmod.SessionLocal)
    yield dbmod
    for m, k, v in saved:
        setattr(m, k, v)
    ge.GradingEngine, rv.GradingEngine = old_engines
    app.dependency_overrides.pop(get_db, None)
    dbmod.engine.dispose()
    if hasattr(dbmod, "DATABASE_URL"):
        del dbmod.DATABASE_URL
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def client(review_env):
    from backend.app import app
    from backend.config import settings

    c = TestClient(app)
    assert c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN}).status_code == 200
    return c


def test_review_config_defaults():
    assert get_review_config() == {"enabled": True, "threshold": 0.7, "mode": "resample"}


def test_review_settings_api(client):
    r = client.get("/api/settings/review")
    assert r.status_code == 200 and r.json()["threshold"] == 0.7, r.text
    r = client.post("/api/settings/review", json={"enabled": True, "threshold": 0.7, "mode": "resample"})
    assert r.status_code == 200 and r.json()["success"], r.text
    r = client.post("/api/settings/review", json={"enabled": True, "threshold": 2.0, "mode": "resample"})
    assert r.status_code == 400, r.text
    r = client.post("/api/settings/review", json={"enabled": True, "threshold": 0.7, "mode": "bogus"})
    assert r.status_code == 400, r.text


def test_review_agree_adopts_and_bumps_confidence(review_env):
    FakeEngine.calls.update(n=0, mode="sec")
    FakeEngine.secondary_judgement = "correct"
    FakeEngine.secondary_answer = "5"
    stats = asyncio.run(cross_check_questions(dict(RESULT), "img.jpg", "default", "数学"))
    assert stats["reviewed"] == 1 and stats["agree"] == 1 and stats["conflict"] == 0, stats
    q2 = RESULT["questions"][1]
    assert not q2.get("needs_review") and q2["confidence"] == 0.6, q2


def test_review_conflict_flags_needs_review(review_env):
    FakeEngine.calls.update(n=0, mode="sec")
    FakeEngine.secondary_judgement = "wrong"
    FakeEngine.secondary_answer = "6"
    stats = asyncio.run(cross_check_questions(dict(RESULT), "img.jpg", "default", "数学"))
    assert stats["conflict"] == 1, stats
    q2 = RESULT["questions"][1]
    assert q2.get("needs_review") and q2["review"] == "conflict" and q2["confidence"] <= 0.55, q2
    assert "不一致" in q2.get("review_note", "")


def test_review_disabled_no_calls(review_env):
    save_review_config(False, 0.7, "resample")
    FakeEngine.calls["n"] = 0
    stats = asyncio.run(cross_check_questions(dict(RESULT), "img.jpg", "default", "数学"))
    assert stats["enabled"] is False and FakeEngine.calls["n"] == 0 and stats["reviewed"] == 0, stats
    save_review_config(True, 0.7, "resample")


def test_review_no_low_confidence_skips(review_env):
    FakeEngine.calls["n"] = 0
    hi = {"success": True, "questions": [{"question_no": 1, "judgement": "correct", "confidence": 0.9}]}
    stats = asyncio.run(cross_check_questions(hi, "img.jpg", "default", "数学"))
    assert FakeEngine.calls["n"] == 0 and stats["reviewed"] == 0, stats


def test_review_secondary_failure_keeps_result(review_env):
    FakeEngine.calls["n"] = 0

    class FailSecond(FakeEngine):
        async def grade(self, image_path, question_type, subject, answers=None):
            FakeEngine.calls["n"] += 1
            return {"success": False, "error": "复核超时"}

    ge.GradingEngine = FailSecond
    rv.GradingEngine = FailSecond
    stats = asyncio.run(cross_check_questions(dict(RESULT), "img.jpg", "default", "数学"))
    assert stats["skipped"] == 1 and stats["conflict"] == 0, stats


def test_review_e2e_conflict_returns_stats_and_needs_review(client, review_env):
    import base64
    FakeEngine.calls.update(n=0, mode="pair")
    FakeEngine.secondary_judgement = "wrong"
    FakeEngine.secondary_answer = "6"
    from backend.database import Assignment, SessionLocal, Student
    db = SessionLocal()
    stu = Student(name="张三", class_id=1); db.add(stu); db.commit()
    asg = Assignment(class_id=1, title="测试作业", subject_id=1); db.add(asg); db.commit()
    db.close()
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    r = client.post("/api/upload/upload-mobile", files={"file": ("t.png", png, "image/png")},
                    data={"assignment_name": "复核测试", "stage": "小学", "subject": "数学",
                          "teacher_id": "1", "force_quality": "1"})
    assert r.status_code == 200, r.text
    sid = r.json()["submission_id"]
    r = client.post(f"/api/grading/start/{sid}")
    assert r.status_code == 200 and r.json()["success"], r.text
    rev = r.json().get("review")
    assert rev and rev["conflict"] == 1, r.text
    r = client.get("/api/analytics/analytics")
    assert r.status_code == 200, r.text
    nr = r.json().get("needs_review", [])
    assert any(x["submission_id"] == sid and x.get("note") for x in nr), r.text
