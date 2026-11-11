"""PRD 待办 e：并发批改吞吐 —— 队列、并发上限、耗时记录、队列状态接口"""
import os, tempfile, asyncio

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal, Submission, AppConfig, QuestionResult, get_db
init_db()

from backend.app import app
client = TestClient(app)

# PIN 登录（并发设置接口受保护）
assert client.post("/api/auth/pin-login", json={"pin": "123456"}).status_code == 200

# ---------- 1) 并发路数配置：默认 2，可设 1-4，越界拒绝 ----------
r = client.get("/api/grading/concurrency")
assert r.status_code == 200 and r.json()["concurrency"] == 2, r.text

r = client.post("/api/grading/concurrency", json={"value": 4})
assert r.status_code == 200 and r.json()["concurrency"] == 4, r.text
r = client.post("/api/grading/concurrency", json={"value": 0})
assert r.status_code == 400, r.text
r = client.post("/api/grading/concurrency", json={"value": 5})
assert r.status_code == 400, r.text
client.post("/api/grading/concurrency", json={"value": 2})

# mock 引擎（记录并发峰值）
db = SessionLocal()
for k, v in [("llm_provider", "qwen"), ("llm_api_key", "test-key")]:
    row = db.query(AppConfig).filter(AppConfig.key == k).first()
    if row:
        row.value = v
    else:
        db.add(AppConfig(key=k, value=v))
db.commit(); db.close()

import backend.models.grading_engine as ge
import backend.routes.grading as gr

state = {"active": 0, "peak": 0, "done": 0}

class FakeEngine:
    def __init__(self, api_key, model_name, base_url):
        pass
    async def grade(self, image_path, question_type, subject, answers=None):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        state["done"] += 1
        return {"success": True, "judgement": "correct", "confidence": 0.95,
                "analysis": "OK", "questions": [{"question_no": 1, "judgement": "correct"}],
                "prompt_tokens": 100, "completion_tokens": 50}

class FailEngine(FakeEngine):
    async def grade(self, image_path, question_type, subject, answers=None):
        return {"success": False, "error": "模拟失败"}

def make_subs(n, status="uploaded", **kw):
    db = SessionLocal()
    for i in range(n):
        db.add(Submission(assignment_id=1, student_id=1,
                          original_image_path="/tmp/x.jpg", status=status, **kw))
    db.commit()
    ids = [s.id for s in db.query(Submission)
           .filter(Submission.status == status).all() if s.id]
    db.close()
    return ids

async def run_batch(payload):
    """直接驱动批量端点：执行后收集全部后台任务跑完"""
    db = next(get_db())
    try:
        res = await gr.start_batch(gr.BatchIn(**payload), db=db)
    finally:
        db.close()
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return res

orig_engine = ge.GradingEngine
orig_annotate = gr.annotate_image
gr.annotate_image = lambda *a, **k: "/tmp/ann.jpg"

# ---------- 2) 并发上限生效：6 份、并发 2，峰值不得超过 2 ----------
ids = make_subs(6)
ge.GradingEngine = FakeEngine
try:
    res = asyncio.run(run_batch({"submission_ids": ids}))
    assert res["success"] and res["count"] == 6 and res["concurrency"] == 2, res
finally:
    ge.GradingEngine = orig_engine

assert state["done"] == 6, state
assert state["peak"] == 2, f"并发峰值应为 2（受信号量限制），实际 {state['peak']}"

# ---------- 3) 队列状态与耗时 ----------
st = client.get("/api/grading/queue-status").json()
assert st["success"] and st["counts"]["completed"] == 6, st
assert st["avg_duration_seconds"] and st["avg_duration_seconds"] > 0, st
for it in st["items"]:
    assert it["state"] == "completed" and it["duration_seconds"] > 0, it

db = SessionLocal()
subs = {s.id: s for s in db.query(Submission).all()}
assert all(s.status == "completed" for s in subs.values())
assert all(s.duration_seconds and s.duration_seconds > 0 for s in subs.values())
assert db.query(QuestionResult).count() == 6  # 每份 1 题
db.close()

# ---------- 4) 单份批改也记录耗时（start 接口，走 HTTP） ----------
(sid,) = make_subs(1)
ge.GradingEngine = FakeEngine
try:
    r = client.post(f"/api/grading/start/{sid}")
    assert r.status_code == 200 and r.json()["success"], r.text
    assert r.json()["duration_seconds"] > 0, r.json()
finally:
    ge.GradingEngine = orig_engine

db = SessionLocal()
s = db.query(Submission).filter(Submission.id == sid).first()
assert s.status == "completed" and s.duration_seconds > 0
db.close()

# ---------- 5) 批量入选规则：超限 failed 与已完成不入队 ----------
make_subs(1, status="failed", retry_count=3)
make_subs(1, status="completed")
db = SessionLocal()
db_res = client.get("/api/grading/queue-status").json()  # 触发一次确保无异常
db.close()

ge.GradingEngine = FailEngine
try:
    res = asyncio.run(run_batch({}))  # 全部待批
    assert res["count"] == 0, f"超限 failed 不应入队: {res}"
finally:
    ge.GradingEngine = orig_engine

# 失败的份进队列 failed
(sid2,) = make_subs(1)
ge.GradingEngine = FailEngine
try:
    res = asyncio.run(run_batch({"submission_ids": [sid2]}))
    assert res["count"] == 1
finally:
    ge.GradingEngine = orig_engine

st = client.get("/api/grading/queue-status").json()
assert st["counts"]["failed"] == 1, st
db = SessionLocal()
s = db.query(Submission).filter(Submission.id == sid2).first()
assert s.status == "failed" and s.error_message == "模拟失败" and s.duration_seconds is not None
db.close()

# ---------- 6) HTTP 层：batch 立即返回队列信息（不阻塞） ----------
(sid3,) = make_subs(1)
ge.GradingEngine = FakeEngine
gr.annotate_image = lambda *a, **k: "/tmp/ann.jpg"
try:
    r = client.post("/api/grading/batch", json={"submission_ids": [sid3]})
    d = r.json()
    assert r.status_code == 200 and d["success"] and d["count"] == 1 and sid3 in d["queued"], d
    st = client.get("/api/grading/queue-status").json()
    assert any(it["submission_id"] == sid3 for it in st["items"]), st
finally:
    ge.GradingEngine = orig_engine
    gr.annotate_image = orig_annotate

# ---------- 7) 设置页含并发卡片，工作台含批量按钮 ----------
assert "批改并发路数" in client.get("/settings").text
assert "startBatch" in client.get("/").text

print("ALL CONCURRENCY QUEUE TESTS PASSED ✅")
