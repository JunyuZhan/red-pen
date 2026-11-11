"""冒烟测试：设置页"测试连接"按钮（内置测试图实测视觉支持 + Key 有效性）"""
import json, os, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"

from fastapi.testclient import TestClient
from backend.database import init_db
init_db()

from backend.app import app
client = TestClient(app)
assert client.post("/api/auth/pin-login", json={"pin": "123456"}).status_code == 200

# ---- mock OpenAI 兼容服务：可切换行为 ----
MODE = {"v": "ok"}
GOOD_CONTENT = json.dumps({"questions": [{"question_number": 1, "judgement": "correct",
                                          "confidence": 0.95, "comment": "ok"}], "summary": "好"})


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        # 校验请求确实带图片（视觉输入）
        content = body["messages"][0]["content"]
        has_image = any(isinstance(c, dict) and c.get("type") == "image_url" for c in content)
        if MODE["v"] == "ok":
            assert has_image, "测试请求必须包含图片输入"
            resp = {"choices": [{"message": {"content": GOOD_CONTENT}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
            self._send(200, resp)
        elif MODE["v"] == "auth":
            self._send(401, {"error": {"message": "Invalid API key provided", "type": "auth_error"}})
        else:  # not_vision
            self._send(400, {"error": {"message": "This model does not support image input", "type": "invalid_request_error"}})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), MockHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

# 保存主引擎配置指向 mock
r = client.post("/api/settings/llm", json={"provider": "custom", "base_url": f"http://127.0.0.1:{port}/v1",
                                           "model": "mock-vl", "api_key": "sk-test"})
assert r.status_code == 200 and r.json()["success"], r.text

# 场景 1：连接成功（Key 有效 + 图片真的发出去且被接受）
r = client.post("/api/settings/test-connection", json={"target": "main"})
d = r.json()
assert r.status_code == 200 and d["success"], d
assert "连接成功" in d["message"] and d["recommendations"], d

# 场景 2：Key 无效 → 明确提示
MODE["v"] = "auth"
d = client.post("/api/settings/test-connection", json={"target": "main"}).json()
assert not d["success"] and d["kind"] == "auth" and "API Key" in d["message"], d

# 场景 3：纯文本模型 → 明确"不支持图片输入"提示
MODE["v"] = "not_vision"
d = client.post("/api/settings/test-connection", json={"target": "main"}).json()
assert not d["success"] and d["kind"] == "not_vision" and "不支持图片" in d["message"], d

# 场景 4：配置不完整 → config 提示（直接测底层函数，接口层 custom 缺必填项会被 400 拒绝）
import asyncio
from backend.models.grading_engine import test_connection as _tc
d = asyncio.new_event_loop().run_until_complete(_tc("custom", "", "", ""))
assert not d["success"] and d["kind"] == "config", d
d = asyncio.new_event_loop().run_until_complete(_tc("custom", "http://x/v1", "m", ""))
assert not d["success"] and d["kind"] == "config", d

print("✅ 测试连接冒烟测试全部通过（成功/Key无效/纯文本模型/配置缺失 4 场景）")
