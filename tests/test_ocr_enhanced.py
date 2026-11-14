# -*- coding: utf-8 -*-
"""待办 g7：OCR 增强开关（DeepSeek /v1/tools/ocr）— pytest 原生版。

- 默认关闭；get/save roundtrip
- server_ocr_lines：关闭/无 Key/非 DeepSeek base_url 一律返回 None
- server_ocr_lines：mock httpx 成功 → 返回文本行
- align_text_to_ink：文本行按顺序映射到墨迹行带，top 递增
- _ocr_lines：开启且 mock 服务端成功 → 用服务端行；服务端失败 → 回退本地
- 路由 GET/POST /api/settings/ocr-enhanced
"""
import os
import sys
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.models.ocr_enhanced as ocr_enhanced  # noqa: E402
from backend.models import grading_engine  # noqa: E402


@pytest.fixture()
def client(tmp_db):
    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.database import init_db

    init_db()
    c = TestClient(app)
    from backend.config import settings as app_settings
    r = c.post("/api/auth/pin-login", json={"pin": app_settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"]
    return c


def _mk_page(path, w=600, h=800):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for i, y in enumerate(range(80, h - 80, 100)):
        d.text((40, y), f"line {i} " + "字" * 10, fill=(0, 0, 0))
    img.save(path)
    return path


def test_default_off(client):
    assert ocr_enhanced.get_ocr_enhanced_config() == {"enabled": False}


def test_save_roundtrip(client):
    ocr_enhanced.save_ocr_enhanced(True)
    assert ocr_enhanced.is_enabled() is True
    ocr_enhanced.save_ocr_enhanced(False)
    assert ocr_enhanced.is_enabled() is False


def _cfg(base_url="", api_key="k"):
    return {"base_url": base_url, "api_key": api_key,
            "provider": "custom", "model": "deepseek-flash"}


def test_disabled_returns_none(client):
    with mock.patch.object(grading_engine, "get_llm_config",
                           return_value=_cfg("https://api.deepseek.com")):
        assert ocr_enhanced.server_ocr_lines("/tmp/x.png") is None


def test_no_key_returns_none(client):
    ocr_enhanced.save_ocr_enhanced(True)
    with mock.patch.object(grading_engine, "get_llm_config",
                           return_value=_cfg("https://api.deepseek.com", api_key="")):
        assert ocr_enhanced.server_ocr_lines("/tmp/x.png") is None


def test_non_deepseek_returns_none(client):
    ocr_enhanced.save_ocr_enhanced(True)
    with mock.patch.object(grading_engine, "get_llm_config",
                           return_value=_cfg("https://open.bigmodel.cn/api")):
        assert ocr_enhanced.server_ocr_lines("/tmp/x.png") is None


def test_server_success(client):
    ocr_enhanced.save_ocr_enhanced(True)
    payload = {"pages": [{"text": "1. 第一题\n2. 第二题\n\n注意事项：满分100分"}]}
    resp = mock.Mock(status_code=200)
    resp.raise_for_status = mock.Mock()
    resp.json = mock.Mock(return_value=payload)
    with mock.patch.object(grading_engine, "get_llm_config",
                           return_value=_cfg("https://api.deepseek.com")), \
         mock.patch("httpx.post", return_value=resp) as mp:
        lines = ocr_enhanced.server_ocr_lines("/tmp/page.png")
    assert lines == ["1. 第一题", "2. 第二题", "注意事项：满分100分"]
    url = mp.call_args.args[0]
    assert url == "https://api.deepseek.com/v1/tools/ocr"


def test_server_http_error_returns_none(client):
    import httpx

    ocr_enhanced.save_ocr_enhanced(True)
    with mock.patch.object(grading_engine, "get_llm_config",
                           return_value=_cfg("https://api.deepseek.com")), \
         mock.patch("httpx.post", side_effect=httpx.ConnectError("boom")):
        assert ocr_enhanced.server_ocr_lines("/tmp/x.png") is None


def test_align_text_to_ink(client, tmp_path):
    p = _mk_page(str(tmp_path / "page.png"))
    out = ocr_enhanced.align_text_to_ink(p, ["1. a", "2. b", "3. c"])
    assert out is not None and len(out) == 3
    tops = [l["top"] for l in out]
    assert tops == sorted(tops)
    assert all(set(l) >= {"text", "top", "bottom"} for l in out)
    # 空文本 → 空列表（合法结果）
    assert ocr_enhanced.align_text_to_ink(p, []) == []


def test_ocr_lines_uses_server(client, tmp_path):
    from backend.models import question_splitter as qs

    p = _mk_page(str(tmp_path / "page.png"))
    server_rows = [{"text": "1. mock", "top": 50, "bottom": 80}]
    ocr_enhanced.save_ocr_enhanced(True)
    with mock.patch.object(ocr_enhanced, "server_ocr_lines", return_value=["1. mock"]), \
         mock.patch.object(ocr_enhanced, "align_text_to_ink",
                           return_value=server_rows):
        assert qs._ocr_lines(p) == server_rows


def test_ocr_lines_falls_back_when_server_fails(client, tmp_path):
    from backend.models import question_splitter as qs

    p = _mk_page(str(tmp_path / "page.png"))
    ocr_enhanced.save_ocr_enhanced(True)
    with mock.patch.object(ocr_enhanced, "server_ocr_lines", return_value=None), \
         mock.patch.object(qs, "_get_ocr", return_value=None):
        assert qs._ocr_lines(p) == []


def test_route_get_post(client):
    r = client.get("/api/settings/ocr-enhanced").json()
    assert r["success"] and r["enabled"] is False
    r = client.post("/api/settings/ocr-enhanced", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert client.get("/api/settings/ocr-enhanced").json()["enabled"] is True
