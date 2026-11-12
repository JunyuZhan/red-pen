# -*- coding: utf-8 -*-
"""PRD 3.3 测试外网检查（/api/network/check-internet）— pytest 原生版：
- 任一探测站可达 → reachable=True，含延迟与人话提示
- 全部探测失败 → reachable=False，提示含防火墙引导
- 设置页含「测试外网」按钮与 JS 函数（模板完整性）
"""
import os
import sys
from unittest import mock

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.routes import network as net  # noqa: E402


@pytest.fixture()
def client(tmp_db):
    from backend.app import app

    # TestClient 不触发 startup，显式建表
    from backend.database import init_db

    init_db()
    return TestClient(app)


def _patch_open(side_effect):
    return mock.patch("urllib.request.urlopen", side_effect=side_effect)


def test_internet_ok_first_probe(client):
    """第一个探测站可达：reachable=True、带延迟。"""
    with _patch_open(mock.mock_open(read_data=b"")) as m:
        m.return_value.__enter__ = lambda s: s
        r = client.get("/api/network/check-internet").json()
    assert r["success"] and r["reachable"] is True
    assert r["probe"] == net.INTERNET_PROBES[0]
    assert isinstance(r["latency_ms"], int)
    assert "外网" in r["message"]


def test_internet_ok_second_probe(client):
    """第一个失败、第二个可达：仍判定外网正常，probe 指向第二个。"""
    import urllib.error

    calls = {"n": 0}

    def fake_open(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("timeout")
        return mock.MagicMock()

    with _patch_open(fake_open):
        r = client.get("/api/network/check-internet").json()
    assert r["reachable"] is True
    assert r["probe"] == net.INTERNET_PROBES[1]


def test_internet_unreachable(client):
    """全部失败：reachable=False，提示引导查网络/防火墙。"""
    import urllib.error

    with _patch_open(mock.Mock(side_effect=urllib.error.URLError("no route"))):
        r = client.get("/api/network/check-internet").json()
    assert r["reachable"] is False
    assert r["probe"] is None
    assert "防火墙" in r["message"]
    assert r["error"]


def test_settings_page_has_button(client):
    """设置页含「测试外网」按钮、checkInternet 函数与同一 WiFi 防火墙提示。"""
    html = client.get("/settings").text
    assert "测试外网" in html
    assert "checkInternet" in html
    assert "同一个 WiFi" in html
