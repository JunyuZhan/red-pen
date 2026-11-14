# -*- coding: utf-8 -*-
"""第十轮健壮性（v0.5.27）：备份/恢复异常路径。

探针覆盖的回归 + 本轮新增修复：
- 恢复前自动快照当前数据为 pre_restore_*.zip（误选错备份可找回）
- 非法文件名（穿越/无后缀/空串）400；不存在的备份 404
- 损坏 zip / 缺 red_pen.db / zip-slip / 损坏库文件 均 400 且原数据不动
- 正常备份→恢复回环，数据一致
"""
import os
import sqlite3
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest


@pytest.fixture()
def bk_env(monkeypatch):
    """独立数据目录 + 登录后的 TestClient。"""
    tmp = tempfile.mkdtemp(prefix="rp_bk_")
    from backend.config import settings
    # backup.py 全部经由 settings.DATA_DIR/UPLOAD_DIR 做文件操作，改属性即可隔离
    monkeypatch.setattr(settings, "DATA_DIR", type(settings.DATA_DIR)(tmp))
    monkeypatch.setattr(settings, "UPLOAD_DIR", settings.DATA_DIR / "uploads")
    os.makedirs(os.path.join(tmp, "backups"), exist_ok=True)
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    from backend.app import app
    from fastapi.testclient import TestClient
    c = TestClient(app)
    assert c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN}).status_code == 200
    yield {"client": c, "tmp": tmp, "settings": settings}


def _write_sqlite(path, marker):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS marker (v TEXT)")
    conn.execute("DELETE FROM marker")
    conn.execute("INSERT INTO marker VALUES (?)", (marker,))
    conn.commit()
    conn.close()


def test_restore_makes_safety_snapshot_before_wipe(bk_env):
    """恢复前自动生成 pre_restore_*.zip，含恢复前的库内容。"""
    c, tmp = bk_env["client"], bk_env["tmp"]
    db = os.path.join(tmp, "red_pen.db")
    _write_sqlite(db, "old-data-123")
    # 造一个合法备份（内容为空库）
    zf = zipfile.ZipFile(os.path.join(tmp, "backups", "good.zip"), "w")
    with tempfile.TemporaryDirectory() as td:
        snap = os.path.join(td, "red_pen.db")
        _write_sqlite(snap, "restored-data-456")
        zf.write(snap, "red_pen.db")
    zf.close()
    r = c.post("/api/backup/restore", json={"filename": "good.zip"})
    assert r.status_code == 200, r.text
    # 数据已恢复为新内容
    conn = sqlite3.connect(db)
    v = conn.execute("SELECT v FROM marker").fetchone()[0]
    conn.close()
    assert v == "restored-data-456"
    # 且存在 pre_restore 快照，内容是恢复前的数据
    pres = [f for f in os.listdir(os.path.join(tmp, "backups")) if f.startswith("pre_restore_")]
    assert len(pres) == 1
    pz = zipfile.ZipFile(os.path.join(tmp, "backups", pres[0]))
    assert "red_pen.db" in pz.namelist()
    with tempfile.TemporaryDirectory() as td:
        pz.extract("red_pen.db", td)
        conn = sqlite3.connect(os.path.join(td, "red_pen.db"))
        v2 = conn.execute("SELECT v FROM marker").fetchone()[0]
        conn.close()
    assert v2 == "old-data-123"


def test_restore_rejects_bad_names_and_missing(bk_env):
    c = bk_env["client"]
    for name in ("../evil.zip", "a/b.zip", "noext", ""):
        assert c.post("/api/backup/restore", json={"filename": name}).status_code == 400
    assert c.post("/api/backup/restore", json={"filename": "ghost.zip"}).status_code == 404


def test_restore_rejects_corrupt_zips_and_keeps_data(bk_env):
    c, tmp, settings = bk_env["client"], bk_env["tmp"], bk_env["settings"]
    bdir = os.path.join(tmp, "backups")
    db = os.path.join(tmp, "red_pen.db")
    _write_sqlite(db, "keep-me")
    with open(os.path.join(bdir, "bad.zip"), "wb") as f:
        f.write(b"not a zip")
    z = zipfile.ZipFile(os.path.join(bdir, "nodb.zip"), "w")
    z.writestr("uploads/x.txt", "hi")
    z.close()
    z = zipfile.ZipFile(os.path.join(bdir, "slip.zip"), "w")
    z.writestr("../../evil.txt", "x")
    z.writestr("red_pen.db", b"")
    z.close()
    z = zipfile.ZipFile(os.path.join(bdir, "corrupt.zip"), "w")
    z.writestr("red_pen.db", b"garbage" * 100)
    z.close()
    for name in ("bad.zip", "nodb.zip", "slip.zip", "corrupt.zip"):
        r = c.post("/api/backup/restore", json={"filename": name})
        assert r.status_code == 400, (name, r.text)
        # 原库未被破坏
        conn = sqlite3.connect(db)
        v = conn.execute("SELECT v FROM marker").fetchone()[0]
        conn.close()
        assert v == "keep-me"
    assert not os.path.exists(os.path.join(tmp, "evil.txt"))


def test_backup_restore_roundtrip(bk_env):
    c, tmp, settings = bk_env["client"], bk_env["tmp"], bk_env["settings"]
    db = os.path.join(tmp, "red_pen.db")
    _write_sqlite(db, "roundtrip-789")
    r = c.post("/api/backup/create")
    assert r.status_code == 200, r.text
    fn = r.json()["filename"]
    # 破坏当前库，再用备份恢复
    _write_sqlite(db, "wrecked")
    r = c.post("/api/backup/restore", json={"filename": fn})
    assert r.status_code == 200, r.text
    conn = sqlite3.connect(db)
    v = conn.execute("SELECT v FROM marker").fetchone()[0]
    conn.close()
    assert v == "roundtrip-789"
    r = c.get("/api/backup")
    assert r.status_code == 200 and any(b["filename"] == fn for b in r.json()["backups"])
