"""设置页数据合规提示冒烟测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from backend.database import init_db
from backend.app import app

init_db()
client = TestClient(app)

r = client.get("/settings")
assert r.status_code == 200, r.status_code
html = r.text
for token in ["数据合规提示", "模型服务商", "数据用于模型训练", "敏感信息遮盖", "本地存储"]:
    assert token in html, f"设置页缺少合规提示内容：{token}"
print("✅ 设置页数据合规提示卡片齐全")
