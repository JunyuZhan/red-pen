# -*- coding: utf-8 -*-
"""健壮性第二十八轮：备份/恢复脏数据场景。

实测发现并修复的两个 bug 的回归测试：
- create：当前库文件损坏（合法头部但内容损坏）时 connect/backup 抛
  DatabaseError 未捕获 → 500；现 400 人话提示，且不产出半截 zip。
- restore：PRAGMA integrity_check 只执行不查结果值，合法头部但内容
  损坏的库被放行恢复；现校验结果必须为 "ok"，拒绝恢复且原数据不动。
"""
import os
import sqlite3
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

HEAD = b"SQLite format 3\x00"


@pytest.fixture()
def bk_env(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="rp_bk28_")
    from backend.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", type(settings.DATA_DIR)(tmp))
    monkeypatch.setattr(settings, "UPLOAD_DIR", settings.DATA_DIR / "uploads")
    os.makedirs(os.path.join(tmp, "backups"), exist_ok=True)
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    from backend.app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    assert c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN}).status_code == 200
    yield {"client": c, "tmp": tmp, "settings": settings}


def _corrupt_db(path):
    """合法头部 + 垃圾内容：能通过头部校验，但 SQLite 无法读页。"""
    with open(path, "wb") as f:
        f.write(HEAD + b"GARBAGE" * 64)


def test_create_with_corrupt_db_returns_400_not_500(bk_env):
    """库文件损坏时 /create 应 400 人话提示，不产出备份 zip。"""
    c, tmp = bk_env["client"], bk_env["tmp"]
    _corrupt_db(os.path.join(tmp, "red_pen.db"))
    r = c.post("/api/backup/create")
    assert r.status_code == 400, r.text
    assert "损坏" in r.json()["detail"]
    # 不留半截/空 zip
    assert os.listdir(os.path.join(tmp, "backups")) == []


def test_restore_rejects_integrity_check_failure(bk_env):
    """备份内库内容损坏（头部合法）：integrity_check 非 ok → 400，原数据不动。"""
    c, tmp = bk_env["client"], bk_env["tmp"]
    _write_old_db = os.path.join(tmp, "red_pen.db")
    conn = sqlite3.connect(_write_old_db)
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('live-data')")
    conn.commit()
    conn.close()
    # 构造含损坏库的备份 zip
    with tempfile.TemporaryDirectory() as td:
        bad = os.path.join(td, "red_pen.db")
        _corrupt_db(bad)
        zf = zipfile.ZipFile(os.path.join(tmp, "backups", "bad.zip"), "w")
        zf.write(bad, "red_pen.db")
        zf.close()
    r = c.post("/api/backup/restore", json={"filename": "bad.zip"})
    assert r.status_code == 400, r.text
    assert "损坏" in r.json()["detail"]
    # 原库未被替换
    conn = sqlite3.connect(_write_old_db)
    assert conn.execute("SELECT v FROM marker").fetchone()[0] == "live-data"
    conn.close()


def test_restore_still_accepts_healthy_backup(bk_env):
    """修复后健康备份恢复回环不回归。"""
    c, tmp = bk_env["client"], bk_env["tmp"]
    db = os.path.join(tmp, "red_pen.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE marker (v TEXT)")
    conn.execute("INSERT INTO marker VALUES ('old')")
    conn.commit()
    conn.close()
    with tempfile.TemporaryDirectory() as td:
        good = os.path.join(td, "red_pen.db")
        conn = sqlite3.connect(good)
        conn.execute("CREATE TABLE marker (v TEXT)")
        conn.execute("INSERT INTO marker VALUES ('restored')")
        conn.commit()
        conn.close()
        zf = zipfile.ZipFile(os.path.join(tmp, "backups", "good.zip"), "w")
        zf.write(good, "red_pen.db")
        zf.close()
    r = c.post("/api/backup/restore", json={"filename": "good.zip"})
    assert r.status_code == 200, r.text
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT v FROM marker").fetchone()[0] == "restored"
    conn.close()
