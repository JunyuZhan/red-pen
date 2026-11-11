# -*- coding: utf-8 -*-
"""手机端顶部队列计数（待办 q）— pytest 原生版：
- /api/mobile/queue-counts 空库返回全 0
- 灌数据后 total/processing/completed/failed 计数正确
- 接口公开只读、响应字段齐全
- 移动端页面含徽章与轮询脚本
"""
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def queue_env():
    """独立临时库，结束恢复原状。"""
    import backend.database as dbmod
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_qc_")
    url = "sqlite:///" + os.path.join(tmp, "test_qc.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()
    yield dbmod
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def client(queue_env):
    from backend.app import app

    return TestClient(app)


def _seed_submissions(db):
    from backend.database import Assignment, Submission

    asg = Assignment(class_id=1, subject_id=1, title="测试作业")
    db.add(asg)
    db.commit()
    statuses = ["pending", "queued", "processing", "processing",
                "completed", "completed", "completed", "failed"]
    db.add_all([
        Submission(assignment_id=asg.id, student_id=1,
                   original_image_path=f"u{i}.jpg", status=s)
        for i, s in enumerate(statuses)
    ])
    db.commit()


def test_queue_counts_empty_db(client):
    r = client.get("/api/mobile/queue-counts")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("success") is True
    assert d.get("total") == 0
    assert d.get("processing") == 0
    assert d.get("completed") == 0
    assert d.get("failed") == 0


def test_queue_counts_with_data(queue_env, client):
    _seed_submissions(queue_env.SessionLocal())

    r = client.get("/api/mobile/queue-counts")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 8
    assert d["processing"] == 2
    assert d["completed"] == 3
    assert d["failed"] == 1


def test_queue_counts_public_and_fields(client):
    r1 = client.get("/api/mobile/queue-counts")
    r2 = client.get("/api/mobile/queue-counts")
    assert r1.status_code == 200 and r2.status_code == 200, "接口应公开只读无需登录"
    assert all(k in r1.json() for k in ("total", "processing", "completed", "failed"))


def test_mobile_page_has_badge_and_polling(client):
    html = client.get("/mobile").text
    assert "queue_badge" in html
    assert "refreshQueueBadge" in html
    assert "已传" in html and "批改" in html and "完成" in html
