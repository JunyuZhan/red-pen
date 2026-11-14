# -*- coding: utf-8 -*-
"""健壮性第四轮（v0.5.21）：备份/恢复路由加固
1. 损坏 zip 恢复 -> 400，原数据不动
2. 缺 red_pen.db 的 zip 恢复 -> 400
3. 含 ../ 路径穿越条目的 zip -> 400，且文件未写到 data 目录外
4. 备份空数据 -> 400
5. create+restore 全链路：备份含库与上传文件，恢复后数据一致
注：备份路由读写 settings.DATA_DIR/red_pen.db（与 tmp_db 的测试库无关），
断言直接用 sqlite3 打开该文件。
"""
import os
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tests.conftest import tmp_db, auth_client  # noqa: F401,F402


@pytest.fixture()
def backup_env(tmp_db, monkeypatch):
    """DATA_DIR/UPLOAD_DIR 指向临时目录，并预置一个含一张学生表的库。"""
    from backend.config import settings
    data = tempfile.mkdtemp(prefix="rp_bk_")
    os.makedirs(os.path.join(data, "uploads"), exist_ok=True)
    db_path = os.path.join(data, "red_pen.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO students (name) VALUES ('张三')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "DATA_DIR", type(settings.DATA_DIR)(data))
    monkeypatch.setattr(settings, "UPLOAD_DIR", type(settings.UPLOAD_DIR)(os.path.join(data, "uploads")))
    return data


def _data_dir():
    from backend.config import settings
    return settings.DATA_DIR


def _students(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT name FROM students").fetchall()
    conn.close()
    return [r[0] for r in rows]


def test_restore_corrupt_zip_rejected(backup_env, auth_client):
    bdir = _data_dir() / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "bad.zip").write_bytes(b"not a zip at all")
    (_data_dir() / "uploads" / "keep.txt").write_text("原文件", encoding="utf-8")
    r = auth_client.post("/api/backup/restore", json={"filename": "bad.zip"})
    assert r.status_code == 400, r.text
    assert _students(_data_dir() / "red_pen.db") == ["张三"]
    assert (_data_dir() / "uploads" / "keep.txt").exists()


def test_restore_missing_db_rejected(backup_env, auth_client):
    bdir = _data_dir() / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bdir / "nodb.zip", "w") as zf:
        zf.writestr("uploads/x.txt", "hello")
    r = auth_client.post("/api/backup/restore", json={"filename": "nodb.zip"})
    assert r.status_code == 400, r.text
    assert "red_pen.db" in r.json()["detail"]


def test_restore_zip_slip_blocked(backup_env, auth_client):
    bdir = _data_dir() / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bdir / "evil.zip", "w") as zf:
        zf.writestr("../evil.txt", "pwned")
        zf.writestr("red_pen.db", b"junk")
    r = auth_client.post("/api/backup/restore", json={"filename": "evil.zip"})
    assert r.status_code == 400, r.text
    assert not (_data_dir().parent / "evil.txt").exists()
    assert not (_data_dir() / "evil.txt").exists()


def test_restore_corrupt_db_rejected(backup_env, auth_client):
    """zip 结构合法但库文件内容不是 SQLite -> 400，原数据不动。"""
    bdir = _data_dir() / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bdir / "baddb.zip", "w") as zf:
        zf.writestr("red_pen.db", b"definitely not sqlite")
    r = auth_client.post("/api/backup/restore", json={"filename": "baddb.zip"})
    assert r.status_code == 400, r.text
    assert _students(_data_dir() / "red_pen.db") == ["张三"]


def test_create_backup_empty_rejected(backup_env, auth_client):
    conn = sqlite3.connect(str(_data_dir() / "red_pen.db"))
    conn.close()
    os.remove(_data_dir() / "red_pen.db")
    r = auth_client.post("/api/backup/create")
    assert r.status_code == 400, r.text


def test_backup_restore_roundtrip(backup_env, auth_client):
    (_data_dir() / "uploads" / "a.txt").write_text("作业图占位", encoding="utf-8")
    r = auth_client.post("/api/backup/create")
    assert r.status_code == 200, r.text
    fname = r.json()["filename"]
    # 备份后改动现场，恢复应回到备份点
    conn = sqlite3.connect(str(_data_dir() / "red_pen.db"))
    conn.execute("DELETE FROM students")
    conn.commit()
    conn.close()
    os.remove(_data_dir() / "uploads" / "a.txt")
    r2 = auth_client.post("/api/backup/restore", json={"filename": fname})
    assert r2.status_code == 200, r2.text
    assert _students(_data_dir() / "red_pen.db") == ["张三"]
    assert (_data_dir() / "uploads" / "a.txt").exists()
    conn = sqlite3.connect(str(_data_dir() / "red_pen.db"))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
