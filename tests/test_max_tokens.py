# -*- coding: utf-8 -*-
"""单次请求 token 上限（PRD 4.3 成本护栏）"""
import asyncio

import pytest

from backend.models.grading_engine import (
    GradingEngine,
    get_max_tokens,
    save_max_tokens,
)


def test_max_tokens_default_unlimited(tmp_db):
    assert get_max_tokens()["max_tokens"] == 0


def test_save_and_get_roundtrip(tmp_db):
    save_max_tokens(2048)
    assert get_max_tokens()["max_tokens"] == 2048
    save_max_tokens(0)
    assert get_max_tokens()["max_tokens"] == 0


def test_invalid_stored_value_falls_back(tmp_db):
    save_max_tokens(1024)
    # 模拟库里存了脏数据
    from backend.database import AppConfig, SessionLocal
    db = SessionLocal()
    row = db.query(AppConfig).filter(AppConfig.key == "llm_max_tokens").first()
    row.value = "abc"
    db.commit()
    db.close()
    assert get_max_tokens()["max_tokens"] == 0


def _make_engine():
    return GradingEngine(api_key="k", model_name="test-model", base_url="http://mock")


class _FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    @property
    def text(self):
        return ""


def test_grade_payload_includes_max_tokens_when_set(tmp_db, tmp_path, monkeypatch):
    """设置上限后，请求体必须带 max_tokens 字段"""
    from PIL import Image
    p = tmp_path / "hw.png"
    Image.new("L", (64, 64), 200).save(str(p))

    save_max_tokens(1500)
    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return _FakeResp()

    import backend.models.grading_engine as ge
    monkeypatch.setattr(ge.httpx, "AsyncClient", FakeClient)
    asyncio.run(_make_engine().grade(str(p), "choice", "数学"))
    assert captured["payload"]["max_tokens"] == 1500


def test_grade_payload_omits_max_tokens_when_zero(tmp_db, tmp_path, monkeypatch):
    from PIL import Image
    p = tmp_path / "hw.png"
    Image.new("L", (64, 64), 200).save(str(p))

    save_max_tokens(0)
    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return _FakeResp()

    import backend.models.grading_engine as ge
    monkeypatch.setattr(ge.httpx, "AsyncClient", FakeClient)
    asyncio.run(_make_engine().grade(str(p), "choice", "数学"))
    assert "max_tokens" not in captured["payload"]


def test_max_tokens_settings_api(auth_client):
    r = auth_client.get("/api/settings/max-tokens")
    assert r.status_code == 200 and r.json()["max_tokens"] == 0
    r = auth_client.post("/api/settings/max-tokens", json={"max_tokens": 3000})
    assert r.status_code == 200 and r.json()["max_tokens"] == 3000
    assert auth_client.get("/api/settings/max-tokens").json()["max_tokens"] == 3000
    # 非法值被拒
    r = auth_client.post("/api/settings/max-tokens", json={"max_tokens": -5})
    assert r.status_code == 400
    r = auth_client.post("/api/settings/max-tokens", json={"max_tokens": 999999})
    assert r.status_code == 400
