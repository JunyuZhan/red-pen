"""
健壮性第二十五轮（同班学生重名防护）回归测试：
- 同班创建同名学生 → 400
- 改名成班里已有名字 → 400
- 转班到已有同名学生的班级 → 400
- 不同班同名允许；自己改名回自己的名字允许
"""
import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import settings


@pytest.fixture()
def client(tmp_db, monkeypatch):
    updir = os.path.join(tempfile.mkdtemp(prefix="rp_up55_"), "uploads")
    monkeypatch.setattr(settings, "UPLOAD_DIR", pathlib.Path(updir))
    os.makedirs(updir, exist_ok=True)
    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"], r.text
    return c


def _mk_class(c, name="测试班"):
    r = c.post("/api/manage/classes", json={"name": name, "stage_id": 1, "subject_id": 1})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_student(c, class_id, name):
    return c.post("/api/manage/students", json={"name": name, "class_id": class_id})


def test_create_duplicate_name_same_class_rejected(client):
    cid = _mk_class(client)
    r1 = _mk_student(client, cid, "张三")
    assert r1.status_code == 200, r1.text
    r2 = _mk_student(client, cid, "张三")
    assert r2.status_code == 400
    assert "张三" in r2.json()["detail"]


def test_create_same_name_other_class_allowed(client):
    c1 = _mk_class(client, "一班")
    c2 = _mk_class(client, "二班")
    assert _mk_student(client, c1, "李四").status_code == 200
    r = _mk_student(client, c2, "李四")
    assert r.status_code == 200, r.text


def test_rename_to_existing_name_rejected(client):
    cid = _mk_class(client)
    sid_a = _mk_student(client, cid, "甲").json()["id"]
    _mk_student(client, cid, "乙")
    r = client.put(f"/api/manage/students/{sid_a}", json={"name": "乙"})
    assert r.status_code == 400
    assert "乙" in r.json()["detail"]


def test_rename_to_own_name_allowed(client):
    cid = _mk_class(client)
    sid = _mk_student(client, cid, "丙").json()["id"]
    r = client.put(f"/api/manage/students/{sid}", json={"name": "丙"})
    assert r.status_code == 200, r.text


def test_move_to_class_with_same_name_rejected(client):
    c1 = _mk_class(client, "甲班")
    c2 = _mk_class(client, "乙班")
    sid = _mk_student(client, c1, "丁").json()["id"]
    _mk_student(client, c2, "丁")
    r = client.put(f"/api/manage/students/{sid}", json={"class_id": c2})
    assert r.status_code == 400
    assert "丁" in r.json()["detail"]


def test_move_and_rename_together_checks_target_class(client):
    c1 = _mk_class(client, "一")
    c2 = _mk_class(client, "二")
    sid = _mk_student(client, c1, "戊").json()["id"]
    _mk_student(client, c2, "己")
    # 同时改名+转班：目标班里已有"己"，改成"己"应被拒
    r = client.put(f"/api/manage/students/{sid}", json={"name": "己", "class_id": c2})
    assert r.status_code == 400
    # 改成目标班里没有的名字则成功
    r2 = client.put(f"/api/manage/students/{sid}", json={"name": "庚", "class_id": c2})
    assert r2.status_code == 200, r2.text
