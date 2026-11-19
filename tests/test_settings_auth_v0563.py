# -*- coding: utf-8 -*-
"""健壮性第三十三轮：敏感写接口补管理员鉴权。

漏洞：保存 API Key 的 /api/settings/llm、/fallback、/cost、发起外呼的
/test-connection、备份/恢复的 /api/backup/create、/restore 均未挂
require_admin——局域网内任何人可改写 Key、借测试连接探内网、覆盖整个
数据库。回归：未带管理员 cookie 一律 401，登录后正常放行。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest


@pytest.fixture()
def adm_env(monkeypatch):
    tmp = os.path.join(ROOT, "data_test_auth33")
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(os.path.join(tmp, "backups"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "uploads"), exist_ok=True)
    from backend.config import settings
    monkeypatch.setattr(settings, "DATA_DIR", type(settings.DATA_DIR)(tmp))
    monkeypatch.setattr(settings, "UPLOAD_DIR", settings.DATA_DIR / "uploads")
    from backend.app import app
    from fastapi.testclient import TestClient
    fresh = TestClient(app)  # 不登录
    logged = TestClient(app)
    assert logged.post("/api/auth/pin-login",
                       json={"pin": settings.ADMIN_PIN}).status_code == 200
    yield {"fresh": fresh, "logged": logged}
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


SENSITIVE_POSTS = [
    ("/api/settings/llm", {"provider": "qwen"}),
    ("/api/settings/fallback", {"provider": None}),
    ("/api/settings/cost", {"cost_input_per_1k": 0.0, "cost_output_per_1k": 0.0}),
    ("/api/settings/test-connection", {"target": "main"}),
    ("/api/backup/create", None),
    ("/api/backup/restore", {"filename": "x.zip"}),
]


@pytest.mark.parametrize("url,payload", SENSITIVE_POSTS)
def test_sensitive_writes_reject_without_admin(adm_env, url, payload):
    """未登录（无管理员 cookie）调敏感写接口必须 401。"""
    r = adm_env["fresh"].post(url, json=payload)
    assert r.status_code == 401, f"{url} 未鉴权: {r.status_code}"
    assert "PIN" in r.json()["detail"] or "登录" in r.json()["detail"]


@pytest.mark.parametrize("url,payload", SENSITIVE_POSTS)
def test_sensitive_writes_pass_after_pin_login(adm_env, url, payload):
    """PIN 登录后（cookie 生效）同一接口放行（不再 401）。"""
    r = adm_env["logged"].post(url, json=payload)
    assert r.status_code != 401, f"{url} 登录后仍被拒: {r.text}"


def test_cost_endpoint_saved_value_persists(adm_env):
    """/cost 鉴权放行后功能正常：保存的单价能读回。"""
    c = adm_env["logged"]
    r = c.post("/api/settings/cost",
               json={"cost_input_per_1k": 0.001, "cost_output_per_1k": 0.002})
    assert r.status_code == 200 and r.json()["success"] is True
    got = c.get("/api/settings/cost").json()
    assert abs(got["input"] - 0.001) < 1e-9
    assert abs(got["output"] - 0.002) < 1e-9
    # 恢复默认单价（测试库跨测试共享，不留污染）
    c.post("/api/settings/cost",
           json={"cost_input_per_1k": 0.001, "cost_output_per_1k": 0.010})
