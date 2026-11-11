"""PRD 待办 f：低置信度二次复核 —— 阈值触发、一致采纳、不一致标黄进复核队列、设置接口"""
import os, tempfile, asyncio

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"

from fastapi.testclient import TestClient
from backend.database import init_db, SessionLocal, AppConfig
init_db()

from backend.app import app
client = TestClient(app)
assert client.post("/api/auth/pin-login", json={"pin": "123456"}).status_code == 200

# 配置主引擎为 mock
db = SessionLocal()
for k, v in [("llm_provider", "qwen"), ("llm_api_key", "test-key")]:
    row = db.query(AppConfig).filter(AppConfig.key == k).first()
    if row:
        row.value = v
    else:
        db.add(AppConfig(key=k, value=v))
db.commit(); db.close()

import backend.models.grading_engine as ge
import backend.models.review as rv
from backend.models.review import cross_check_questions, get_review_config, save_review_config

calls = {"n": 0}

class FakeEngine:
    """主引擎：1题高置信 + 1题低置信；复核通道结论可编程"""
    secondary_judgement = "correct"   # 复核判定
    secondary_answer = "5"
    def __init__(self, api_key, model_name, base_url):
        pass
    async def grade(self, image_path, question_type, subject, answers=None):
        calls["n"] += 1
        if calls.get("mode") == "sec" or (calls.get("mode") == "pair" and calls["n"] >= 2):
            # 二次复核调用
            return {"success": True, "judgement": self.secondary_judgement, "confidence": 0.9,
                    "analysis": "review", "prompt_tokens": 10, "completion_tokens": 5,
                    "questions": [
                        {"question_no": 2, "judgement": self.secondary_judgement,
                         "confidence": 0.9, "student_answer": self.secondary_answer},
                    ]}
        # 主批改调用
        return {"success": True, "judgement": "correct", "confidence": 0.9,
                "analysis": "OK", "prompt_tokens": 10, "completion_tokens": 5,
                "questions": [
                    {"question_no": 1, "judgement": "correct", "confidence": 0.95, "student_answer": "5"},
                    {"question_no": 2, "judgement": "correct", "confidence": 0.5, "student_answer": "5"},
                ]}

ge.GradingEngine = FakeEngine
rv.GradingEngine = FakeEngine

result = {"success": True, "questions": [
    {"question_no": 1, "judgement": "correct", "confidence": 0.95, "student_answer": "5"},
    {"question_no": 2, "judgement": "correct", "confidence": 0.5, "student_answer": "5"},
]}

# ---------- 1) 配置读写与校验 ----------
cfg = get_review_config()
assert cfg == {"enabled": True, "threshold": 0.7, "mode": "resample"}, cfg
r = client.get("/api/settings/review")
assert r.status_code == 200 and r.json()["threshold"] == 0.7, r.text
r = client.post("/api/settings/review", json={"enabled": True, "threshold": 0.7, "mode": "resample"})
assert r.status_code == 200 and r.json()["success"], r.text
r = client.post("/api/settings/review", json={"enabled": True, "threshold": 2.0, "mode": "resample"})
assert r.status_code == 400, r.text
r = client.post("/api/settings/review", json={"enabled": True, "threshold": 0.7, "mode": "bogus"})
assert r.status_code == 400, r.text

# ---------- 2) 一致 → 采纳并小幅上调置信度 ----------
calls.update(n=0, mode="sec")
FakeEngine.secondary_judgement = "correct"
FakeEngine.secondary_answer = "5"
stats = asyncio.run(cross_check_questions(dict(result), "img.jpg", "default", "数学"))
assert stats["reviewed"] == 1 and stats["agree"] == 1 and stats["conflict"] == 0, stats
q2 = result["questions"][1]
assert not q2.get("needs_review") and q2["confidence"] == 0.6, q2

# ---------- 3) 不一致 → 标 needs_review 降置信度进复核队列 ----------
calls.update(n=0, mode="sec")
FakeEngine.secondary_judgement = "wrong"
FakeEngine.secondary_answer = "6"
stats = asyncio.run(cross_check_questions(dict(result), "img.jpg", "default", "数学"))
assert stats["conflict"] == 1, stats
q2 = result["questions"][1]
assert q2.get("needs_review") and q2["review"] == "conflict" and q2["confidence"] <= 0.55, q2
assert "不一致" in q2.get("review_note", "")

# ---------- 4) 关闭开关 → 完全不复核，零额外调用 ----------
save_review_config(False, 0.7, "resample")
calls["n"] = 0
stats = asyncio.run(cross_check_questions(dict(result), "img.jpg", "default", "数学"))
assert stats["enabled"] is False and calls["n"] == 0 and stats["reviewed"] == 0, (stats, calls)
save_review_config(True, 0.7, "resample")

# ---------- 5) 无低置信度题 → 不触发第二通道 ----------
calls["n"] = 0
hi = {"success": True, "questions": [{"question_no": 1, "judgement": "correct", "confidence": 0.9}]}
stats = asyncio.run(cross_check_questions(hi, "img.jpg", "default", "数学"))
assert calls["n"] == 0 and stats["reviewed"] == 0, (stats, calls)

# ---------- 6) 复核通道失败 → 保持原结果不阻塞 ----------
calls["n"] = 0
class FailSecond(FakeEngine):
    async def grade(self, image_path, question_type, subject, answers=None):
        calls["n"] += 1
        # 第一跳直接复用主响应（仅让返回结构正确），复核跳始终失败
        return {"success": False, "error": "复核超时"}
ge.GradingEngine = FailSecond
rv.GradingEngine = FailSecond
stats = asyncio.run(cross_check_questions(dict(result), "img.jpg", "default", "数学"))
assert stats["skipped"] == 1 and stats["conflict"] == 0, stats

# ---------- 7) 端到端：mock 引擎批改后复核统计随响应返回 ----------
ge.GradingEngine = FakeEngine
rv.GradingEngine = FakeEngine
calls.update(n=0, mode="pair")
FakeEngine.secondary_judgement = "wrong"
FakeEngine.secondary_answer = "6"
import base64
from backend.database import Student, Assignment
db = SessionLocal()
stu = Student(name="张三", class_id=1); db.add(stu); db.commit()
asg = Assignment(class_id=1, title="测试作业", subject_id=1); db.add(asg); db.commit()
sid_stu, aid = stu.id, asg.id
db.close()
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
r = client.post("/api/upload/upload-mobile", files={"file": ("t.png", png, "image/png")},
                data={"assignment_name": "复核测试", "stage": "小学", "subject": "数学",
                      "teacher_id": "1", "force_quality": "1"})
assert r.status_code == 200, r.text
sid = r.json()["submission_id"]
r = client.post(f"/api/grading/start/{sid}")
assert r.status_code == 200 and r.json()["success"], r.text
rev = r.json().get("review")
assert rev and rev["conflict"] == 1, r.text

# 学情接口把复核不一致的题放进 needs_review
r = client.get("/api/analytics/analytics")
assert r.status_code == 200, r.text
nr = r.json().get("needs_review", [])
assert any(x["submission_id"] == sid and x.get("note") for x in nr), r.text

print("test_review.py: 7/7 全部通过")
