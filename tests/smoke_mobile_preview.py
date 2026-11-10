"""手机端 5 秒预览倒计时冒烟测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from backend.database import init_db
from backend.app import app

init_db()
client = TestClient(app)

r = client.get("/mobile")
assert r.status_code == 200, r.status_code
html = r.text
for token in ["preview_ctl", "startCountdown", "cancelShot", "撤销重拍", "立即上传", "秒后自动上传"]:
    assert token in html, f"mobile 页缺少 {token}"
print("✅ mobile 预览倒计时元素齐全")
