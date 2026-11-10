"""PRD 4.4 冒烟测试：失败重试 + 队列持久化"""
import os, tempfile

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal, Submission
init_db()

# 造两条：一条 failed（带原因/重试次数）、一条卡在 processing（模拟服务中断）
db = SessionLocal()
f = Submission(assignment_id=1, student_id=1, original_image_path="/tmp/x.jpg",
               status="failed", error_message="API Key 无效", retry_count=2)
p = Submission(assignment_id=1, student_id=1, original_image_path="/tmp/y.jpg",
               status="processing")
db.add_all([f, p]); db.commit(); fid, pid = f.id, p.id
db.close()

# 模拟重启：startup 恢复逻辑应把 processing → failed
from backend.app import startup_event
import asyncio
asyncio.run(startup_event())

db = SessionLocal()
assert db.query(Submission).get(pid).status == "failed", "中断任务未恢复"
assert db.query(Submission).get(pid).error_message, "中断任务缺失败原因"
db.close()

from backend.app import app
client = TestClient(app)

# 失败队列接口
r = client.get("/api/grading/failed")
assert r.status_code == 200, r.text
d = r.json()
ids = [x["id"] for x in d["failed"]]
assert fid in ids and pid in ids, d
row = next(x for x in d["failed"] if x["id"] == fid)
assert row["error_message"] == "API Key 无效" and row["retry_count"] == 2
assert d["max_retry"] == 3

# 列表带失败原因
lst = client.get("/api/mobile/list").json()["submissions"]
fr = next(x for x in lst if x["id"] == fid)
assert fr["error_message"] == "API Key 无效" and fr["retry_count"] == 2

# 状态接口带失败原因
st = client.get(f"/api/mobile/status/{fid}").json()
assert st["status"] == "failed" and st["error_message"] == "API Key 无效"

# 重试达上限应拒绝（先置为已达 3 次）
db = SessionLocal()
s = db.query(Submission).get(fid); s.retry_count = 3; db.commit(); db.close()
r = client.post(f"/api/grading/retry/{fid}")
assert r.status_code == 400 and "重试次数已达上限" in r.json()["detail"], r.text

# 重试上限内：应走批改流程并失败落原因（无有效 Key，批改必然失败）
db = SessionLocal()
s = db.query(Submission).get(fid); s.retry_count = 0; db.commit(); db.close()
r = client.post(f"/api/grading/retry/{fid}")
db = SessionLocal()
s = db.query(Submission).get(fid)
assert s.status == "failed" and s.error_message and s.retry_count == 1, (s.status, s.error_message)
db.close()

# retry 仅限 failed 状态
db = SessionLocal()
s = db.query(Submission).get(fid); s.status = "completed"; db.commit(); db.close()
r = client.post(f"/api/grading/retry/{fid}")
assert r.status_code == 400 and "仅失败状态可重试" in r.json()["detail"]

# 基本页面
for path in ("/health", "/", "/mobile", "/settings"):
    assert client.get(path).status_code == 200, path

print("ALL SMOKE TESTS PASSED ✅")
