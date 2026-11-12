# -*- coding: utf-8 -*-
"""批改结果缓存（PRD 4.3：同题图+同答案重复提交不重复计费）"""
import json
import os

import pytest

from backend.models.grading_engine import (
    _cache_get,
    _cache_key,
    _cache_put,
    get_cache_config,
    save_cache_config,
)


@pytest.fixture()
def sample_image(tmp_db, tmp_path):
    from PIL import Image
    p = tmp_path / "hw.png"
    Image.new("L", (64, 64), 200).save(str(p))
    return str(p)


def test_cache_key_deterministic_and_sensitive(sample_image):
    k1 = _cache_key(sample_image, "choice", "数学", {"1": "A"}, "qwen", "qwen-vl-plus")
    k2 = _cache_key(sample_image, "choice", "数学", {"1": "A"}, "qwen", "qwen-vl-plus")
    assert k1 == k2 and k1
    # 换答案/题型/模型 → 键不同
    assert _cache_key(sample_image, "choice", "数学", {"1": "B"}, "qwen", "qwen-vl-plus") != k1
    assert _cache_key(sample_image, "fill", "数学", {"1": "A"}, "qwen", "qwen-vl-plus") != k1
    assert _cache_key(sample_image, "choice", "数学", {"1": "A"}, "glm", "glm-4v") != k1
    # 图片不存在 → None（不缓存）
    assert _cache_key("/nonexistent/x.png", "choice", "数学", None, "qwen", "m") is None


def test_cache_put_get_roundtrip(tmp_db, sample_image):
    key = _cache_key(sample_image, "choice", "数学", None, "qwen", "qwen-vl-plus")
    result = {"success": True, "questions": [{"question_no": 1, "judgement": "correct"}]}
    _cache_put(key, result, "qwen-vl-plus")
    got = _cache_get(key)
    assert got == result
    # 不存在的键 → None
    assert _cache_get("deadbeef" * 8) is None


def test_cache_config_default_on_and_toggle(tmp_db):
    assert get_cache_config()["enabled"] is True
    save_cache_config(False)
    assert get_cache_config()["enabled"] is False
    save_cache_config(True)
    assert get_cache_config()["enabled"] is True


def test_grade_uses_cache_and_skips_llm(tmp_db, sample_image, monkeypatch):
    """第二次同参数 grade 不再调用 LLM，返回 cache_hit=True"""
    import asyncio

    import backend.models.grading_engine as ge
    import backend.models.question_splitter as qs
    monkeypatch.setattr(ge, "get_llm_config", lambda: {
        "provider": "mock", "api_key": "k", "base_url": "http://x", "model": "m"})
    monkeypatch.setattr(qs, "get_split_config", lambda: {"enabled": False})

    calls = []

    async def fake_run(provider, api_key, model, base_url):
        calls.append(1)
        return {"success": True, "questions": [{"question_no": 1, "judgement": "correct"}]}

    eng = ge.GradingEngine.__new__(ge.GradingEngine)

    async def fake_grade(self, image_path, question_type, subject, answers=None):
        return {"success": True, "questions": [{"question_no": 1, "judgement": "correct"}]}

    monkeypatch.setattr(ge.GradingEngine, "grade", fake_grade)
    mgr = ge.GradingManager()
    r1 = asyncio.run(mgr.grade(sample_image, "choice", "数学"))
    assert r1["success"] and calls == []  # fake_grade 直接管
    assert "cache_hit" not in r1

    r2 = asyncio.run(mgr.grade(sample_image, "choice", "数学"))
    assert r2.get("cache_hit") is True
    assert r2["questions"] == r1["questions"]


def test_grade_cache_disabled_calls_llm_again(tmp_db, sample_image, monkeypatch):
    """缓存关闭时同一参数也重新调 LLM"""
    import asyncio

    import backend.models.grading_engine as ge
    save_cache_config(False)
    monkeypatch.setattr(ge, "get_llm_config", lambda: {
        "provider": "mock", "api_key": "k", "base_url": "http://x", "model": "m"})
    import backend.models.question_splitter as qs
    monkeypatch.setattr(qs, "get_split_config", lambda: {"enabled": False})

    n = {"c": 0}

    def fake_grade(self, image_path, question_type, subject, answers=None):
        n["c"] += 1
        async def coro():
            return {"success": True, "questions": []}
        return coro()

    monkeypatch.setattr(ge.GradingEngine, "grade", fake_grade)
    mgr = ge.GradingManager()
    asyncio.run(mgr.grade(sample_image, "choice", "数学"))
    asyncio.run(mgr.grade(sample_image, "choice", "数学"))
    assert n["c"] == 2
    save_cache_config(True)


def test_cache_settings_api(auth_client):
    """设置页缓存开关 API：GET 默认开，POST 关闭后再 GET 为关"""
    r = auth_client.get("/api/settings/grading-cache")
    assert r.status_code == 200 and r.json()["enabled"] is True
    r = auth_client.post("/api/settings/grading-cache", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert auth_client.get("/api/settings/grading-cache").json()["enabled"] is False
    auth_client.post("/api/settings/grading-cache", json={"enabled": True})
