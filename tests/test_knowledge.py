# -*- coding: utf-8 -*-
"""知识点体系测试（PRD 待办 i）— pytest 原生版：
- prompt 要求 LLM 输出 knowledge_point 字段
- 批改结果落库保存知识点
- /api/analytics 返回知识点掌握统计，薄弱知识点排前面
"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def grade_env():
    """独立临时库，结束恢复原状。"""
    import backend.database as dbmod
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_kp_")
    url = "sqlite:///" + os.path.join(tmp, "test_kp.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()
    yield dbmod
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def client(grade_env):
    from backend.app import app

    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": "123456"})
    assert r.status_code == 200
    return c


def test_prompt_requires_knowledge_point():
    from backend.models.grading_engine import _build_prompt

    prompt = _build_prompt("math", "数学")
    assert "knowledge_point" in prompt, "prompt 必须要求输出知识点字段"


def _seed_knowledge_rows(db):
    from backend.database import QuestionResult

    rows = [
        ("两位数乘法", "wrong"),
        ("两位数乘法", "wrong"),
        ("两位数乘法", "correct"),
        ("分数加减法", "correct"),
        ("分数加减法", "correct"),
        (None, "correct"),   # 无知识点的不参与统计
        ("  ", "wrong"),     # 空白的不参与统计
    ]
    for kp, j in rows:
        db.add(QuestionResult(submission_id=1, question_no=1, question_type="default",
                              knowledge_point=kp, judgement_final=j))
    db.commit()


def test_analytics_knowledge_stats(grade_env, client):
    _seed_knowledge_rows(grade_env.SessionLocal())

    r = client.get("/api/analytics/analytics")
    assert r.status_code == 200, r.text
    kps = r.json().get("knowledge_points") or []
    assert len(kps) == 2, f"应只统计到2个知识点, got {kps}"
    by_name = {k["name"]: k for k in kps}
    assert by_name["两位数乘法"]["total"] == 3
    assert by_name["两位数乘法"]["correct"] == 1
    assert abs(by_name["两位数乘法"]["accuracy"] - 1 / 3) < 0.01
    assert by_name["分数加减法"]["accuracy"] == 1.0


def test_analytics_weak_knowledge_first(grade_env, client):
    _seed_knowledge_rows(grade_env.SessionLocal())

    r = client.get("/api/analytics/analytics")
    assert r.status_code == 200, r.text
    kps = r.json().get("knowledge_points") or []
    assert kps and kps[0]["name"] == "两位数乘法", f"薄弱知识点应排前: {kps}"
