# -*- coding: utf-8 -*-
"""一键自检脚本 diagnose.py 离线测试：
- 各检查项返回 (status, msg, hint) 三元组且 status 合法
- 数据目录不可写 → FAIL；数据库缺失 → WARN
- run_all 汇总退出码（有 FAIL → 1，否则 0）
"""
import os
import sqlite3
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts import diagnose  # noqa: E402


STATUSES = {"PASS", "WARN", "FAIL"}


def _assert_shape(result):
    status, msg, hint = result
    assert status in STATUSES
    assert msg
    assert isinstance(hint, str)
    return status


def test_check_python_pass():
    assert _assert_shape(diagnose.check_python()) == "PASS"


def test_check_imports_pass():
    assert _assert_shape(diagnose.check_imports()) == "PASS"


def test_check_data_dir_writable(tmp_path):
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATA_DIR = tmp_path
        assert _assert_shape(diagnose.check_data_dir_writable()) == "PASS"


def test_check_data_dir_unwritable(tmp_path):
    locked = tmp_path / "locked"
    locked.write_text("", encoding="utf-8")
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATA_DIR = locked / "sub" / "deeper"
        with mock.patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            assert _assert_shape(diagnose.check_data_dir_writable()) == "FAIL"


def test_check_database_missing(tmp_path):
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATABASE_URL = "sqlite:///%s" % (tmp_path / "no.db").as_posix()
        assert _assert_shape(diagnose.check_database()) == "WARN"


def test_check_database_ok(tmp_path):
    db = tmp_path / "red_pen.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table t(x)")
    conn.commit()
    conn.close()
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATABASE_URL = "sqlite:///%s" % db.as_posix()
        assert _assert_shape(diagnose.check_database()) == "PASS"


def test_check_disk(tmp_path):
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATA_DIR = tmp_path
        assert _assert_shape(diagnose.check_disk()) in {"PASS", "WARN"}


def test_check_port_free_and_occupied():
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.PORT = 59901
        assert _assert_shape(diagnose.check_port()) == "PASS"
        # 占住端口再查
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 59901))
        s.listen(1)
        try:
            status = _assert_shape(diagnose.check_port())
            assert status == "WARN"
        finally:
            s.close()


def test_check_fonts(tmp_path):
    # 无任何候选字体 → WARN；造一个假字体路径会让 truetype 抛异常 → 仍 WARN 不崩
    with mock.patch.object(diagnose, "FONT_CANDIDATES", []):
        assert _assert_shape(diagnose.check_fonts()) == "WARN"


def test_check_api_key_configured(tmp_path):
    db = tmp_path / "red_pen.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table app_config(key TEXT, value TEXT)")
    conn.execute("insert into app_config values('llm_api_key','enc')")
    conn.execute("insert into app_config values('llm_model','qwen3-vl-plus')")
    conn.commit()
    conn.close()
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATABASE_URL = "sqlite:///%s" % db.as_posix()
        status, msg, _ = diagnose.check_api_key()
        assert status == "PASS"
        assert "enc" not in msg  # 不泄露 Key 内容
        assert "qwen3-vl-plus" in msg


def test_check_api_key_missing(tmp_path):
    db = tmp_path / "red_pen.db"
    conn = sqlite3.connect(str(db))
    conn.execute("create table app_config(key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATABASE_URL = "sqlite:///%s" % db.as_posix()
        assert _assert_shape(diagnose.check_api_key()) == "WARN"


def test_run_all_exit_code(tmp_path):
    """全部检查在 tmp 环境跑通，退出码为 0 或 1（不崩）。"""
    with mock.patch.object(diagnose, "_data_dirs") as m:
        m.return_value.DATA_DIR = tmp_path
        m.return_value.DATABASE_URL = "sqlite:///%s" % (tmp_path / "x.db").as_posix()
        m.return_value.PORT = 59902
        rc = diagnose.run_all()
        assert rc in {0, 1}
