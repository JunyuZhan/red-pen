# -*- coding: utf-8 -*-
"""第十一轮健壮性（v0.5.28）：设置页 / 模型连接异常路径。

探针结论：设置路由鉴权由 app.include_router 的 dependencies=[require_admin] 统一覆盖；
各配置读取函数对脏值（非数字字符串）已有默认值兜底；GradingEngine 走 settings.LLM_TIMEOUT
不会挂死「测试连接」。本轮修复两个真问题：
- POST /api/settings/cost 接受负数单价 → 污染成本统计与月度预算护栏，现 400
- POST /api/settings/test-connection 非法 target 静默按 main 处理 → 现 400
另补：test-connection 未配置时返回 kind=config 人话提示（回归保护）、写接口鉴权回归保护。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest


@pytest.fixture()
def st_env(auth_client):
    """登录后的 TestClient + 换绑后的数据库会话工厂。"""
    from backend.database import SessionLocal
    yield {"client": auth_client, "SessionLocal": SessionLocal}


@pytest.fixture()
def clean_cost(st_env):
    """清掉单价配置，保证测试互不影响。"""
    SessionLocal = st_env["SessionLocal"]
    from backend.database import AppConfig
    db = SessionLocal()
    db.query(AppConfig).filter(AppConfig.key.like("cost_%_per_1k")).delete()
    db.commit()
    db.close()
    yield st_env


def test_cost_rejects_negative_price(clean_cost):
    c = clean_cost["client"]
    r = c.post("/api/settings/cost", json={"cost_input_per_1k": -5, "cost_output_per_1k": 2})
    assert r.status_code == 400
    r = c.post("/api/settings/cost", json={"cost_input_per_1k": 1, "cost_output_per_1k": -0.1})
    assert r.status_code == 400


def test_cost_valid_price_roundtrip(clean_cost):
    c = clean_cost["client"]
    r = c.post("/api/settings/cost", json={"cost_input_per_1k": 0.003, "cost_output_per_1k": 0.009})
    assert r.status_code == 200 and r.json()["success"]
    g = c.get("/api/settings/cost").json()
    assert abs(g["input"] - 0.003) < 1e-9 and abs(g["output"] - 0.009) < 1e-9


def test_test_connection_rejects_bad_target(st_env):
    c = st_env["client"]
    r = c.post("/api/settings/test-connection", json={"target": "bogus"})
    assert r.status_code == 400


def test_test_connection_unconfigured_friendly(st_env):
    """未配置 base_url/model/key 时返回 success=False + 配置类人话提示，不 500。"""
    c = st_env["client"]
    r = c.post("/api/settings/test-connection", json={"target": "main"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["kind"] == "config"
    assert isinstance(body.get("recommendations"), list)


def test_settings_write_requires_admin(tmp_db):
    """设置写接口未登录一律 401（路由级依赖回归保护）。"""
    from fastapi.testclient import TestClient
    from backend.app import app
    anon = TestClient(app, raise_server_exceptions=False)
    for method, url, payload in [
        ("post", "/api/settings/llm", {"provider": "qwen"}),
        ("post", "/api/settings/fallback", {"provider": "qwen"}),
        ("post", "/api/settings/cost", {"cost_input_per_1k": 1, "cost_output_per_1k": 1}),
        ("post", "/api/settings/test-connection", {}),
        ("post", "/api/settings/budget", {"monthly_budget": 10}),
        ("post", "/api/settings/review", {"enabled": True, "threshold": 0.7, "mode": "resample"}),
    ]:
        r = getattr(anon, method)(url, json=payload)
        assert r.status_code == 401, f"{url} 未登录应 401，实际 {r.status_code}"
