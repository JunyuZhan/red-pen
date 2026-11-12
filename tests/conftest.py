# -*- coding: utf-8 -*-
"""pytest 公共夹具：独立临时数据库隔离。

仓库早期多数 tests/*.py 是模块级脚本（print + sys.exit），现已全部原生化；
新测试直接使用本文件提供的 tmp_db 夹具，不要在模块级 init_db()。
"""
import os
import tempfile

import pytest


@pytest.fixture()
def tmp_db(monkeypatch):
    """独立临时库 + get_db 覆盖 + 各模块 SessionLocal 换绑，结束恢复。

    背景：models/ 下多个模块（grading_engine/review/question_splitter/budget/
    teaching_advice/image_preprocess）与 routes/grading 在导入时 `from
    ..database import SessionLocal` 绑定了原始 SessionLocal，只换
    database.SessionLocal 会导致写入落旧库。这里逐一换绑并恢复。
    """
    from fastapi.testclient import TestClient

    from backend.app import app
    import backend.database as dbmod
    from backend.database import get_db

    tmp = tempfile.mkdtemp(prefix="rp_test_")
    url = "sqlite:///" + os.path.join(tmp, "test.db")
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

    import backend.models.grading_engine as ge
    import backend.models.review as rv
    import backend.models.question_splitter as qs
    import backend.models.budget as budget
    import backend.models.teaching_advice as ta
    import backend.models.study_report as sr
    import backend.models.image_preprocess as ip
    import backend.models.privacy as privacy_mod
    import backend.routes.grading as grading_mod

    modules = [ge, rv, qs, budget, ta, sr, ip, privacy_mod, grading_mod]
    saved = [(m, getattr(m, "SessionLocal")) for m in modules if hasattr(m, "SessionLocal")]
    for m in modules:
        if hasattr(m, "SessionLocal"):
            m.SessionLocal = dbmod.SessionLocal

    old_engines = (ge.GradingEngine, rv.GradingEngine)
    yield dbmod

    ge.GradingEngine, rv.GradingEngine = old_engines
    for m, v in saved:
        m.SessionLocal = v
    app.dependency_overrides.pop(get_db, None)
    dbmod.engine.dispose()
    if hasattr(dbmod, "DATABASE_URL"):
        del dbmod.DATABASE_URL
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def auth_client(tmp_db):
    """已 PIN 登录（CI 同款 123456）的 TestClient。"""
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.config import settings

    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"], r.text
    return c
