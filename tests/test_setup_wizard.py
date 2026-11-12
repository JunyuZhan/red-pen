# -*- coding: utf-8 -*-
"""待办 p：首次启动三步引导 + 工作台上下文切换（/api/setup/*）"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def setup_env():
    """独立临时库 + get_db 覆盖，结束恢复。"""
    from backend.app import app
    import backend.database as dbmod
    from backend.database import Base, get_db

    tmp = tempfile.mkdtemp(prefix="rp_setup_")
    url = "sqlite:///" + os.path.join(tmp, "test_setup.db")
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
def client(setup_env):
    from backend.app import app
    from backend.config import settings

    c = TestClient(app)
    assert c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN}).status_code == 200
    return c


def _reset(dbmod):
    from backend.database import Base, StageConfig, SubjectConfig

    db = dbmod.SessionLocal()
    Base.metadata.drop_all(dbmod.engine)
    Base.metadata.create_all(dbmod.engine)
    db.add_all([StageConfig(name="小学", enabled=True), StageConfig(name="中学", enabled=True)])
    db.add_all([
        SubjectConfig(name="数学", stage_id=1, enabled=True),
        SubjectConfig(name="语文", stage_id=1, enabled=True),
        SubjectConfig(name="英语", stage_id=1, enabled=True),
    ])
    db.commit(); db.close()


def test_status_before_setup(client, setup_env):
    _reset(setup_env)
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    d = r.json()
    assert d["setup_completed"] is False
    assert d["defaults"]["stage"] == "小学"
    assert any(s["name"] == "中学" for s in d["stages"])
    assert any(s["name"] == "数学" for s in d["subjects"])
    assert d["classes"] == []


def test_anonymous_cannot_save(client, setup_env):
    _reset(setup_env)
    anon = TestClient(client.app)
    r = anon.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
    assert r.status_code == 401
    r = anon.post("/api/setup/context", json={"class_name": "x"})
    assert r.status_code == 401


def test_complete_setup(client, setup_env):
    _reset(setup_env)
    r = client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
    assert r.status_code == 200
    d = r.json()
    assert d["defaults"]["class_name"] == "三年二班"
    assert isinstance(d["class_id"], int)
    from backend.database import AppConfig, Class
    db = setup_env.SessionLocal()
    assert db.query(AppConfig).filter_by(key="setup_completed", value="true").count() == 1
    assert db.query(Class).filter_by(name="三年二班").count() == 1
    assert db.query(AppConfig).filter_by(key="default_stage", value="小学").count() == 1
    db.close()


def test_status_after_setup(client, setup_env):
    _reset(setup_env)
    client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
    r = client.get("/api/setup/status")
    assert r.json()["setup_completed"] is True
    assert any(c["name"] == "三年二班" for c in r.json()["classes"])


def test_complete_validation(client, setup_env):
    _reset(setup_env)
    r = client.post("/api/setup/complete", json={"stage": "", "subject": "数学", "class_name": "x"})
    assert r.status_code == 400
    r = client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "  "})
    assert r.status_code == 400


def test_context_switch_creates_stage_subject(client, setup_env):
    _reset(setup_env)
    client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
    r = client.post("/api/setup/context", json={"stage": "中学", "subject": "物理", "class_name": "三年二班"})
    assert r.status_code == 200
    from backend.database import AppConfig, StageConfig, SubjectConfig
    db = setup_env.SessionLocal()
    assert db.query(StageConfig).filter_by(name="中学").count() >= 1
    assert db.query(SubjectConfig).filter_by(name="物理").count() == 1
    assert db.query(AppConfig).filter_by(key="default_subject", value="物理").count() == 1
    assert db.query(AppConfig).filter_by(key="setup_completed").first().value == "true"
    db.close()


def test_context_switch_class_only_inherits(client, setup_env):
    _reset(setup_env)
    client.post("/api/setup/context", json={"stage": "中学", "subject": "物理", "class_name": "三年二班"})
    r = client.post("/api/setup/context", json={"class_name": "三年二班"})
    assert r.status_code == 200
    from backend.database import AppConfig
    db = setup_env.SessionLocal()
    assert db.query(AppConfig).filter_by(key="default_stage", value="中学").count() == 1
    db.close()


def test_context_empty_class_inherits_default(client, setup_env):
    _reset(setup_env)
    client.post("/api/setup/context", json={"stage": "中学", "subject": "物理", "class_name": "三年二班"})
    r = client.post("/api/setup/context", json={"class_name": ""})
    assert r.status_code == 200


def test_recomplete_no_duplicate_class(client, setup_env):
    _reset(setup_env)
    from backend.database import Class
    client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
    n_before = setup_env.SessionLocal().query(Class).count()
    r = client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
    assert r.status_code == 200
    assert setup_env.SessionLocal().query(Class).count() == n_before


def test_workbench_page_has_wizard_elements(client, setup_env):
    _reset(setup_env)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "wizGate" in html
    assert "ctxBtn" in html
    assert "/api/setup/status" in html
