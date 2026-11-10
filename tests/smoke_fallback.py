"""PRD 第三节冒烟测试：主备 provider 自动切换"""
import os, tempfile

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"

from fastapi.testclient import TestClient
from backend.database import init_db, AppConfig, SessionLocal
init_db()

from backend.app import app
client = TestClient(app)

# PIN 登录（设置接口受保护）
assert client.get("/api/settings/fallback").status_code == 401
assert client.post("/api/auth/pin-login", json={"pin": "123456"}).status_code == 200

# 未配置备用时 provider 为 None
r = client.get("/api/settings/fallback")
assert r.status_code == 200 and r.json()["success"] and r.json()["provider"] is None, r.text

# 保存备用配置（glm，key 留空沿用默认 base_url/model）
r = client.post("/api/settings/fallback", json={"provider": "glm", "api_key": "fb-key-0001"})
assert r.status_code == 200 and r.json()["success"], r.text
d = client.get("/api/settings/fallback").json()
assert d["provider"] == "glm", d
assert d["base_url"] == "https://open.bigmodel.cn/api/paas/v4", d
assert d["model"] == "glm-4v", d
assert d["has_api_key"] and d["api_key_masked"].startswith("fb-k"), d

# 非法服务商拒绝
r = client.post("/api/settings/fallback", json={"provider": "bad"})
assert r.status_code == 400, r.text

# GradingManager：主用失败 → 自动切备用成功，标记 fallback_used
from backend.models.grading_engine import grading_manager
from backend.database import SessionLocal

# 主用 qwen + 有效 key；备用 glm 已配置
db = SessionLocal()
for k, v in [("llm_provider", "qwen"), ("llm_api_key", "main-key")]:
    row = db.query(AppConfig).filter(AppConfig.key == k).first()
    if row:
        row.value = v
    else:
        db.add(AppConfig(key=k, value=v))
db.commit(); db.close()

import backend.models.grading_engine as ge
calls = []

class FakeEngine:
    def __init__(self, api_key, model_name, base_url):
        self.api_key = api_key
    async def grade(self, image_path, question_type, subject, answers=None):
        calls.append(self.api_key)
        if self.api_key == "main-key":
            return {"success": False, "error": "限流 429"}
        return {"success": True, "judgement": "correct", "questions": [{"question_no": 1}]}

orig = ge.GradingEngine
ge.GradingEngine = FakeEngine
try:
    import asyncio
    res = asyncio.run(grading_manager.grade("/tmp/nonexist.jpg", "math", "数学"))
finally:
    ge.GradingEngine = orig

assert res.get("success"), res
assert res.get("fallback_used") is True, res
assert res.get("provider") == "glm", res
assert res.get("main_provider") == "qwen" and "429" in (res.get("main_error") or ""), res
assert calls == ["main-key", "fb-key-0001"], calls

# 主备均失败：错误信息含双方
class BothFailEngine(FakeEngine):
    async def grade(self, image_path, question_type, subject, answers=None):
        return {"success": False, "error": "down"}

ge.GradingEngine = BothFailEngine
try:
    res2 = asyncio.run(grading_manager.grade("/tmp/nonexist.jpg", "math", "数学"))
finally:
    ge.GradingEngine = orig
assert not res2.get("success") and "主用" in res2["error"] and "备用" in res2["error"], res2

# 关闭备用：直接返回主用失败
r = client.post("/api/settings/fallback", json={"provider": ""})
assert r.status_code == 200 and "关闭" in r.json()["message"], r.text
assert client.get("/api/settings/fallback").json()["provider"] is None

ge.GradingEngine = FakeEngine
try:
    res3 = asyncio.run(grading_manager.grade("/tmp/nonexist.jpg", "math", "数学"))
finally:
    ge.GradingEngine = orig
assert not res3.get("success") and "429" in res3["error"], res3

# 页面含备用引擎卡片
r = client.get("/settings")
for token in ["备用引擎（自动切换）", "备用服务商", "saveFallback"]:
    assert token in r.text, token

print("ALL SMOKE TESTS PASSED ✅")
