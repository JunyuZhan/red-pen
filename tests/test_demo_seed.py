# -*- coding: utf-8 -*-
"""一键体验演示数据：seed 幂等、权限门禁、清除干净、页面按钮存在"""
import os
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in os.sys.path:
    os.sys.path.insert(0, ROOT)

from tests.conftest import auth_client  # noqa: F401


@pytest.fixture()
def demo_dir(tmp_path, monkeypatch):
    from backend.config import settings
    d = tmp_path / "uploads_demo"
    monkeypatch.setattr(settings, "UPLOAD_DIR", d)
    return d


def _seed(client):
    r = client.post("/api/demo/seed")
    assert r.status_code == 200, r.text
    return r.json()


def test_seed_creates_demo_data(auth_client, demo_dir):
    d = _seed(auth_client)
    assert d["success"] and not d["already"]
    from backend.database import SessionLocal, Class, Student, Assignment, Submission, QuestionResult
    db = SessionLocal()
    try:
        cls = db.query(Class).filter(Class.name == "【演示】一年级1班").first()
        assert cls
        assert db.query(Student).filter(Student.class_id == cls.id).count() == 3
        asg = db.query(Assignment).filter(Assignment.class_id == cls.id).first()
        assert asg and asg.title.startswith("【演示】")
        subs = db.query(Submission).filter(Submission.assignment_id == asg.id).all()
        assert len(subs) == 3
        for s in subs:
            assert s.status == "completed"
            assert "非AI批改" in s.grading_summary
            assert os.path.exists(s.original_image_path)
            qr = db.query(QuestionResult).filter(
                QuestionResult.submission_id == s.id).all()
            assert len(qr) == 5
            assert any(q.needs_review for q in qr)
            assert all(q.llm_raw_output and q.llm_raw_output.get("demo") for q in qr)
    finally:
        db.close()
    # 演示图落在 uploads/demo/ 下
    assert (demo_dir / "demo").exists()


def test_seed_idempotent(auth_client, demo_dir):
    first = _seed(auth_client)
    second = _seed(auth_client)
    assert second["already"] is True
    assert second["assignment_id"] == first["assignment_id"]
    from backend.database import SessionLocal, Class, Assignment, Submission
    db = SessionLocal()
    try:
        assert db.query(Assignment).count() == 1
        assert db.query(Submission).count() == 3
        assert db.query(Class).filter(Class.name == "【演示】一年级1班").count() == 1
    finally:
        db.close()


def test_seed_requires_admin(tmp_db, demo_dir):
    from fastapi.testclient import TestClient
    from backend.app import app
    c = TestClient(app)
    r = c.post("/api/demo/seed")
    assert r.status_code == 401


def test_clear_removes_everything(auth_client, demo_dir):
    _seed(auth_client)
    r = auth_client.post("/api/demo/clear")
    assert r.status_code == 200 and r.json()["success"]
    from backend.database import SessionLocal, Class, Student, Assignment, Submission, QuestionResult
    db = SessionLocal()
    try:
        assert db.query(Class).filter(Class.name == "【演示】一年级1班").first() is None
        assert db.query(Assignment).count() == 0
        assert db.query(Submission).count() == 0
        assert db.query(QuestionResult).count() == 0
        assert db.query(Student).count() == 0
    finally:
        db.close()
    # 演示图片文件一并删除
    assert not list((demo_dir / "demo").glob("demo_*.png"))
    # 再清一次：无数据也成功
    r2 = auth_client.post("/api/demo/clear")
    assert r2.status_code == 200


def test_seed_visible_in_dashboard(auth_client, demo_dir):
    _seed(auth_client)
    r = auth_client.get("/api/dashboard/summary")
    assert r.status_code == 200
    s = r.json()
    titles = [a.get("title") for a in s.get("assignment_grades", [])]
    assert any(t and t.startswith("【演示】") for t in titles)


def test_page_has_demo_ui(tmp_db):
    from fastapi.testclient import TestClient
    from backend.app import app
    html = TestClient(app).get("/").text
    assert "生成演示数据" in html and "demoSeed" in html
    assert "非 AI 批改" in html  # 页面必须明示演示数据不是 AI 批改
