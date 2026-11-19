# -*- coding: utf-8 -*-
"""健壮性第三十二轮：SQLite 引擎加固回归测试。

覆盖三点（针对 Windows 单机断电/杀进程/并发批改的真实风险）：
1. 全局引擎每个连接启用 WAL + busy_timeout —— 并发批改不再 "database is locked"；
2. 库文件损坏时启动自愈 selfheal_corrupt_db：留档 .corrupt-* + 清 WAL 残留 + 返回留档路径；
3. 完好库文件 quick_check 通过、返回 None，绝不误伤正常库。
"""
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def fresh_engine():
    from sqlalchemy import create_engine, event

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
        finally:
            cur.close()

    return eng


def test_global_engine_wal_and_busy_timeout():
    """全局引擎新连接必须是 WAL + busy_timeout=5000（只读 pragma 探测，不写数据）。"""
    from backend.database import engine

    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert str(mode).lower() == "wal"
    assert timeout == 5000


def test_pragma_listener_sets_wal(tmp_path):
    """模拟与 backend/database 相同的 connect 钩子：WAL 落到磁盘文件。"""
    from sqlalchemy import create_engine, event

    db_file = tmp_path / "w.db"
    eng = create_engine("sqlite:///" + str(db_file), connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
        finally:
            cur.close()

    with eng.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE t(x)")
        conn.commit()
    raw = sqlite3.connect(str(db_file))
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        raw.close()


def test_selfheal_renames_corrupt_db(tmp_path):
    """损坏库：quick_check 报错 → 留档 .corrupt-*、原文件消失、WAL 残留被清。"""
    from backend.database import selfheal_corrupt_db

    db_file = tmp_path / "red_pen.db"
    # 头部合法但内容损坏：truncate 到半页，quick_check 会抛 DatabaseError
    db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    (tmp_path / "red_pen.db-wal").write_bytes(b"junk")
    (tmp_path / "red_pen.db-shm").write_bytes(b"junk")

    eng = sqlite3  # 占位防误用
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///" + str(db_file), connect_args={"check_same_thread": False})

    saved = selfheal_corrupt_db(db_file, eng)
    assert saved is not None and saved.name.startswith("red_pen.db.corrupt-")
    assert saved.exists() and not db_file.exists()
    assert not (tmp_path / "red_pen.db-wal").exists()
    assert not (tmp_path / "red_pen.db-shm").exists()


def test_selfheal_leaves_healthy_db_alone(tmp_path):
    """完好库：quick_check 通过，返回 None，文件原封不动。"""
    from backend.database import selfheal_corrupt_db

    db_file = tmp_path / "ok.db"
    raw = sqlite3.connect(str(db_file))
    raw.execute("CREATE TABLE t(x)")
    raw.commit()
    raw.close()

    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///" + str(db_file), connect_args={"check_same_thread": False})
    assert selfheal_corrupt_db(db_file, eng) is None
    assert db_file.exists()
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_selfheal_after_rename_db_reusable(tmp_path):
    """留档后同路径可立刻重建新库（模拟 init_db 后续 create_all）。"""
    from backend.database import selfheal_corrupt_db

    db_file = tmp_path / "red_pen.db"
    db_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///" + str(db_file), connect_args={"check_same_thread": False})
    assert selfheal_corrupt_db(db_file, eng) is not None

    # 重建：同路径新库可正常建表写入
    eng2 = create_engine("sqlite:///" + str(db_file), connect_args={"check_same_thread": False})
    with eng2.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE t(x)")
        conn.exec_driver_sql("INSERT INTO t VALUES (1)")
        conn.commit()
        assert conn.exec_driver_sql("SELECT count(*) FROM t").scalar() == 1
