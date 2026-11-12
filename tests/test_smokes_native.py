# -*- coding: utf-8 -*-
"""原生化收尾：6 个 smoke 脚本改写为 pytest 原生测试。

覆盖：答案模板复用 / 设置页合规提示 / 手机端预览倒计时 / 主备 provider 自动切换 /
失败重试+队列持久化 / 设置页测试连接。
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


# ---------- 设置页数据合规提示 ----------

def test_settings_compliance_notice(auth_client):
    html = auth_client.get("/settings").text
    for token in ["数据合规提示", "模型服务商", "数据用于模型训练", "敏感信息遮盖", "本地存储"]:
        assert token in html, f"设置页缺少合规提示内容：{token}"


# ---------- 手机端预览倒计时 ----------

def test_mobile_preview_countdown(auth_client):
    html = auth_client.get("/mobile").text
    for token in ["preview_ctl", "startCountdown", "cancelShot", "撤销重拍", "立即上传", "秒后自动上传"]:
        assert token in html, f"mobile 页缺少 {token}"


# ---------- 答案模板复用（PRD 4.4/M4） ----------

def test_answer_template_unauthorized(tmp_db):
    from fastapi.testclient import TestClient
    from backend.app import app

    c = TestClient(app)
    r = c.get("/api/grading/answer-templates")
    assert r.status_code == 401, r.text


def test_answer_template_crud_and_prompt(auth_client):
    c = auth_client
    # 空 answers 拒绝
    r = c.post("/api/grading/answer-templates", json={"title": "空", "answers": {"1": "  "}})
    assert r.status_code == 400, r.text

    answers = {"1": "3/4", "2": "B", "3": "24"}
    r = c.post("/api/grading/answer-templates",
               json={"title": "数学练习册P23", "subject": "数学", "answers": answers})
    assert r.status_code == 200 and r.json()["success"], r.text
    tid = r.json()["id"]

    # 列表回读
    ts = c.get("/api/grading/answer-templates").json()["templates"]
    assert any(t["id"] == tid and t["answers"] == answers for t in ts), ts

    # prompt 注入验证（单元级）
    from backend.models.grading_engine import _build_prompt
    p = _build_prompt("default", "数学", {"1": "3/4"})
    assert "第1题：3/4" in p and "参考答案" in p
    assert "参考答案" not in _build_prompt("default", "数学")

    # start 带不存在的模板 → 404
    assert c.post("/api/grading/start/999999?template_id=999999").status_code == 404

    # start 带有效模板：无 API Key → 批改失败但不因模板参数崩溃
    from backend.database import SessionLocal, Submission
    db = SessionLocal()
    s = Submission(assignment_id=1, student_id=1, original_image_path="/tmp/x.jpg", status="uploaded")
    db.add(s); db.commit(); sid = s.id; db.close()
    assert c.post(f"/api/grading/start/{sid}?template_id={tid}").status_code in (200, 500)

    # usage_count 仍为 0（未成功批改）
    r = c.get("/api/grading/answer-templates")
    assert all(t["usage_count"] == 0 for t in r.json()["templates"]), r.text

    # 删除
    assert c.delete(f"/api/grading/answer-templates/{tid}").json()["success"]
    assert c.delete("/api/grading/answer-templates/999999").status_code == 404

    # 工作台含模板面板
    html = c.get("/").text
    assert "答案模板" in html and "gradeTplSel" in html


# ---------- 主备 provider 自动切换（PRD 第三节） ----------

class _FakeEngine:
    def __init__(self, api_key, model_name, base_url):
        self.api_key = api_key

    async def grade(self, image_path, question_type, subject, answers=None):
        if self.api_key == "main-key":
            return {"success": False, "error": "限流 429"}
        return {"success": True, "judgement": "correct",
                "questions": [{"question_no": 1}]}


def _set_main_provider(dbmod, provider="qwen", key="main-key"):
    from backend.database import AppConfig
    db = dbmod.SessionLocal()
    for k, v in [("llm_provider", provider), ("llm_api_key", key)]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(AppConfig(key=k, value=v))
    db.commit(); db.close()


def test_fallback_switch_and_disable(auth_client, monkeypatch):
    c = auth_client
    import backend.models.grading_engine as ge
    from backend.models.grading_engine import grading_manager

    # 未配置备用时 provider 为 None
    r = c.get("/api/settings/fallback")
    assert r.json()["provider"] is None, r.text

    # 保存备用配置（glm，key 留空沿用默认 base_url/model）
    r = c.post("/api/settings/fallback", json={"provider": "glm", "api_key": "fb-key-0001"})
    assert r.status_code == 200 and r.json()["success"], r.text
    d = c.get("/api/settings/fallback").json()
    assert d["provider"] == "glm", d
    assert d["base_url"] == "https://open.bigmodel.cn/api/paas/v4", d
    assert d["model"] == "glm-4v", d
    assert d["has_api_key"] and d["api_key_masked"].startswith("fb-k"), d

    # 非法服务商拒绝
    assert c.post("/api/settings/fallback", json={"provider": "bad"}).status_code == 400

    # 主用失败 → 自动切备用成功
    import backend.database as dbmod
    _set_main_provider(dbmod)

    calls = []

    class RecordingEngine(_FakeEngine):
        async def grade(self, *a, **kw):
            calls.append(self.api_key)
            return await _FakeEngine.grade(self, *a, **kw)

    monkeypatch.setattr(ge, "GradingEngine", RecordingEngine)
    res = asyncio.run(grading_manager.grade("/tmp/nonexist.jpg", "math", "数学"))
    assert res.get("success") and res.get("fallback_used") is True, res
    assert res.get("provider") == "glm", res
    assert res.get("main_provider") == "qwen" and "429" in (res.get("main_error") or ""), res
    assert calls == ["main-key", "fb-key-0001"], calls

    # 主备均失败：错误信息含双方
    class BothFailEngine(_FakeEngine):
        async def grade(self, *a, **kw):
            return {"success": False, "error": "down"}

    monkeypatch.setattr(ge, "GradingEngine", BothFailEngine)
    res2 = asyncio.run(grading_manager.grade("/tmp/nonexist.jpg", "math", "数学"))
    assert not res2.get("success") and "主用" in res2["error"] and "备用" in res2["error"], res2

    # 关闭备用：直接返回主用失败
    r = c.post("/api/settings/fallback", json={"provider": ""})
    assert r.status_code == 200 and "关闭" in r.json()["message"], r.text
    assert c.get("/api/settings/fallback").json()["provider"] is None

    monkeypatch.setattr(ge, "GradingEngine", RecordingEngine)
    res3 = asyncio.run(grading_manager.grade("/tmp/nonexist.jpg", "math", "数学"))
    assert not res3.get("success") and "429" in res3["error"], res3

    # 设置页含备用引擎卡片
    html = c.get("/settings").text
    for token in ["备用引擎（自动切换）", "备用服务商", "saveFallback"]:
        assert token in html, token


# ---------- 失败重试 + 队列持久化（PRD 4.4） ----------

def test_retry_and_queue_persistence(tmp_db):
    from backend.database import Submission, init_db
    init_db()
    from backend.database import SessionLocal

    # 造两条：一条 failed（带原因/重试次数）、一条卡在 processing（模拟服务中断）
    db = SessionLocal()
    f = Submission(assignment_id=1, student_id=1, original_image_path="/tmp/x.jpg",
                   status="failed", error_message="API Key 无效", retry_count=2)
    p = Submission(assignment_id=1, student_id=1, original_image_path="/tmp/y.jpg",
                   status="processing")
    db.add_all([f, p]); db.commit(); fid, pid = f.id, p.id; db.close()

    # 模拟重启：startup 恢复逻辑应把 processing → failed
    from backend.app import startup_event
    asyncio.run(startup_event())
    db = SessionLocal()
    assert db.query(Submission).get(pid).status == "failed", "中断任务未恢复"
    assert db.query(Submission).get(pid).error_message, "中断任务缺失败原因"
    db.close()

    from fastapi.testclient import TestClient
    from backend.app import app
    c = TestClient(app)

    # 失败队列接口
    d = c.get("/api/grading/failed").json()
    ids = [x["id"] for x in d["failed"]]
    assert fid in ids and pid in ids, d
    row = next(x for x in d["failed"] if x["id"] == fid)
    assert row["error_message"] == "API Key 无效" and row["retry_count"] == 2
    assert d["max_retry"] == 3

    # 列表与状态接口带失败原因
    fr = next(x for x in c.get("/api/mobile/list").json()["submissions"] if x["id"] == fid)
    assert fr["error_message"] == "API Key 无效" and fr["retry_count"] == 2
    st = c.get(f"/api/mobile/status/{fid}").json()
    assert st["status"] == "failed" and st["error_message"] == "API Key 无效"

    # 重试达上限应拒绝
    db = SessionLocal()
    db.query(Submission).get(fid).retry_count = 3; db.commit(); db.close()
    r = c.post(f"/api/grading/retry/{fid}")
    assert r.status_code == 400 and "重试次数已达上限" in r.json()["detail"], r.text

    # 上限内：应走批改流程并失败落原因（无有效 Key，批改必然失败）
    db = SessionLocal()
    db.query(Submission).get(fid).retry_count = 0; db.commit(); db.close()
    c.post(f"/api/grading/retry/{fid}")
    db = SessionLocal()
    s = db.query(Submission).get(fid)
    assert s.status == "failed" and s.error_message and s.retry_count == 1, (s.status, s.error_message)
    db.close()

    # retry 仅限 failed 状态
    db = SessionLocal()
    s = db.query(Submission).get(fid); s.status = "completed"; db.commit(); db.close()
    r = c.post(f"/api/grading/retry/{fid}")
    assert r.status_code == 400 and "仅失败状态可重试" in r.json()["detail"], r.text

    # 基本页面
    for path in ("/health", "/", "/mobile", "/settings"):
        assert c.get(path).status_code == 200, path


# ---------- 设置页"测试连接" ----------

_GOOD_CONTENT = json.dumps({"questions": [{"question_number": 1, "judgement": "correct",
                                           "confidence": 0.95, "comment": "ok"}], "summary": "好"})


class _MockOpenAIHandler(BaseHTTPRequestHandler):
    mode = {"v": "ok"}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        content = body["messages"][0]["content"]
        has_image = any(isinstance(c, dict) and c.get("type") == "image_url" for c in content)
        if self.mode["v"] == "ok":
            assert has_image, "测试请求必须包含图片输入"
            self._send(200, {"choices": [{"message": {"content": _GOOD_CONTENT}}],
                             "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
        elif self.mode["v"] == "auth":
            self._send(401, {"error": {"message": "Invalid API key provided", "type": "auth_error"}})
        else:
            self._send(400, {"error": {"message": "This model does not support image input",
                                       "type": "invalid_request_error"}})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture()
def mock_openai_server():
    srv = HTTPServer(("127.0.0.1", 0), _MockOpenAIHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def test_test_connection_all_scenarios(auth_client, mock_openai_server):
    c = auth_client
    port = mock_openai_server.server_address[1]

    r = c.post("/api/settings/llm", json={"provider": "custom", "base_url": f"http://127.0.0.1:{port}/v1",
                                          "model": "mock-vl", "api_key": "sk-test"})
    assert r.status_code == 200 and r.json()["success"], r.text

    # 场景 1：连接成功（Key 有效 + 图片真的发出去且被接受）
    d = c.post("/api/settings/test-connection", json={"target": "main"}).json()
    assert d["success"] and "连接成功" in d["message"] and d["recommendations"], d

    # 场景 2：Key 无效 → 明确提示
    _MockOpenAIHandler.mode["v"] = "auth"
    d = c.post("/api/settings/test-connection", json={"target": "main"}).json()
    assert not d["success"] and d["kind"] == "auth" and "API Key" in d["message"], d

    # 场景 3：纯文本模型 → 明确"不支持图片输入"提示
    _MockOpenAIHandler.mode["v"] = "not_vision"
    d = c.post("/api/settings/test-connection", json={"target": "main"}).json()
    assert not d["success"] and d["kind"] == "not_vision" and "不支持图片" in d["message"], d

    # 场景 4：配置不完整 → config 提示
    from backend.models.grading_engine import test_connection as _tc
    loop = asyncio.new_event_loop()
    try:
        d = loop.run_until_complete(_tc("custom", "", "", ""))
        assert not d["success"] and d["kind"] == "config", d
        d = loop.run_until_complete(_tc("custom", "http://x/v1", "m", ""))
        assert not d["success"] and d["kind"] == "config", d
    finally:
        loop.close()
