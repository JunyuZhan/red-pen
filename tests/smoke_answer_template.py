"""答案模板复用（PRD 4.4/M4）冒烟测试"""
import os, tempfile

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal, Submission, AnswerTemplate

init_db()
c = TestClient(app := __import__("backend.app", fromlist=["app"]).app)

# 未登录 → 模板接口 401
r = c.get("/api/grading/answer-templates")
assert r.status_code == 401, (r.status_code, r.text)

# PIN 登录（CI 同款：123456）
r = c.post("/api/auth/pin-login", json={"pin": "123456"})
assert r.status_code == 200 and r.json()["success"], r.text

# 空 answers 拒绝
r = c.post("/api/grading/answer-templates", json={"title": "空", "answers": {"1": "  "}})
assert r.status_code == 400, (r.status_code, r.text)

# 创建模板
r = c.post("/api/grading/answer-templates",
           json={"title": "数学练习册P23", "subject": "数学", "answers": {"1": "3/4", "2": "B", "3": "24"}})
assert r.status_code == 200 and r.json()["success"], r.text
tid = r.json()["id"]

# 列表回读
r = c.get("/api/grading/answer-templates")
ts = r.json()["templates"]
assert any(t["id"] == tid and t["answers"] == {"1": "3/4", "2": "B", "3": "24"} for t in ts), r.text

# prompt 注入验证（单元级）
from backend.models.grading_engine import _build_prompt
p = _build_prompt("default", "数学", {"1": "3/4"})
assert "第1题：3/4" in p and "参考答案" in p
p0 = _build_prompt("default", "数学")
assert "参考答案" not in p0

# start 带不存在的模板 → 404
r = c.post("/api/grading/start/999999?template_id=999999")
assert r.status_code in (404,), (r.status_code, r.text)  # 提交或模板不存在均 404

# start 带有效模板：无 API Key → 批改失败但不因模板参数崩溃（先造一条 submitted 记录）
db = SessionLocal()
s = Submission(assignment_id=1, student_id=1, original_image_path="/tmp/x.jpg", status="uploaded")
db.add(s); db.commit(); sid = s.id
db.close()
r = c.post(f"/api/grading/start/{sid}?template_id={tid}")
assert r.status_code in (200, 500), (r.status_code, r.text)

# usage_count 仍为 0（未成功批改）
r = c.get("/api/grading/answer-templates")
assert all(t["usage_count"] == 0 for t in r.json()["templates"]), r.text

# 删除
r = c.delete(f"/api/grading/answer-templates/{tid}")
assert r.status_code == 200 and r.json()["success"], r.text
r = c.delete("/api/grading/answer-templates/999999")
assert r.status_code == 404, r.text

# 页面含模板面板
r = c.get("/")
assert "答案模板" in r.text and "gradeTplSel" in r.text, "工作台缺少答案模板面板"

print("🎉 答案模板冒烟测试全部通过")
