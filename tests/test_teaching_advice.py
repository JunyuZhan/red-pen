"""LLM 教学建议（待办 t）测试：统计收集、prompt 约束、存取、路由行为"""

import asyncio
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["RED_PEN_DATA_DIR"] = "/tmp/redpen_test_advice"

from fastapi.testclient import TestClient

from backend.database import (
    AppConfig, Assignment, QuestionResult, SessionLocal, Student, Submission,
    TeachingAdvice, init_db,
)
from backend.app import app
from backend.models import teaching_advice
from backend.models.teaching_advice import build_prompt, collect_stats, get_latest_advice

init_db()


def _seed(db):
    a = Assignment(class_id=1, title="口算练习", subject_id=1)
    db.add(a)
    db.commit()
    s1 = Student(name="张三", class_id=1)
    s2 = Student(name="李四", class_id=1)
    db.add_all([s1, s2])
    db.commit()
    rows = []
    for sub_i, sid, items in [
        (1, s1.id, [("1", "correct", "两位数加法", None),
                    ("2", "wrong", "两位数乘法", "calculation"),
                    ("3", "wrong", "两位数乘法", "concept")]),
        (2, s2.id, [("1", "correct", "两位数加法", None),
                    ("2", "correct", "两位数乘法", None),
                    ("3", "wrong", "两位数乘法", "calculation")]),
    ]:
        sub = Submission(assignment_id=a.id, student_id=sid,
                         original_image_path="/tmp/x.jpg",
                         grading_summary="", status="completed")
        db.add(sub)
        db.commit()
        for no, judge, kp, et in items:
            rows.append(QuestionResult(
                submission_id=sub.id, question_no=int(no), question_type="calculation",
                knowledge_point=kp, judgement_raw=judge, judgement_final=judge,
                error_type=et, confidence_final=0.9))
    db.add_all(rows)
    db.commit()
    return a.id


class TestTeachingAdvice(unittest.TestCase):
    def setUp(self):
        from backend.database import engine
        for t in (TeachingAdvice, QuestionResult, Submission, Student, Assignment):
            t.__table__.drop(engine, checkfirst=True)
        for t in (Assignment, Student, Submission, QuestionResult, TeachingAdvice):
            t.__table__.create(engine, checkfirst=True)
        db = SessionLocal()
        _seed(db)
        db.close()

    def test_collect_stats(self):
        db = SessionLocal()
        try:
            stats = collect_stats(db)
        finally:
            db.close()
        self.assertEqual(stats["total_questions"], 6)
        self.assertEqual(stats["error_types"].get("calculation"), 2)
        # 两位数乘法 2/4 = 0.5 < 0.7，是薄弱知识点且排前面
        kps = stats["weak_knowledge_points"]
        self.assertTrue(kps and kps[0]["name"] == "两位数乘法")
        # 高频错题：两位数乘法错 3 次（张三 2 + 李四 1）
        self.assertTrue(any(h["knowledge_point"] == "两位数乘法" and h["wrong_count"] == 3
                            for h in stats["hot_wrong"]))
        # 学生按正确率升序：张三 1/3 在前
        self.assertEqual(stats["students"][0]["name"], "张三")

    def test_collect_stats_empty(self):
        from backend.database import engine
        from backend.database import QuestionResult as QR
        QR.__table__.drop(engine, checkfirst=True)
        QR.__table__.create(engine)
        db = SessionLocal()
        try:
            self.assertIsNone(collect_stats(db))
        finally:
            db.close()
        db = SessionLocal()
        _seed(db)
        db.close()

    def test_prompt_locks_to_data(self):
        db = SessionLocal()
        try:
            stats = collect_stats(db)
        finally:
            db.close()
        prompt = build_prompt(stats)
        self.assertIn("只允许引用输入数据", prompt)
        self.assertIn("两位数乘法", prompt)
        self.assertIn("张三", prompt)

    def test_generate_without_key(self):
        # 未配置 API Key 时返回明确错误，不写库
        from backend.models.grading_engine import save_llm_config
        save_llm_config(provider="custom", base_url="https://example.com/v1",
                        model="m", api_key="tmp")
        db = SessionLocal()
        row = db.query(AppConfig).filter(AppConfig.key == "llm_api_key").first()
        row.value = ""
        db.commit()
        db.close()
        res = asyncio.run(teaching_advice.generate_advice())
        self.assertFalse(res["success"])
        self.assertIn("API Key", res["error"])
        self.assertIsNone(get_latest_advice())

    def test_generate_parses_and_stores(self):
        """mock LLM 返回合法 JSON → 入库，可读回"""
        from backend.models.grading_engine import save_llm_config
        save_llm_config(provider="custom", base_url="https://example.com/v1",
                        model="mock", api_key="sk-test")

        calls = {"n": 0}

        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                calls["n"] += 1
                return {"choices": [{"message": {"content":
                    '{"summary": "乘法薄弱", "suggestions": ["加强乘法练习"], '
                    '"focus_students": [{"name": "张三", "reason": "正确率33%"}]}'}}]}

        class FakeClient:
            def __init__(self, *a, **k):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, *a, **k):
                return FakeResp()

        orig = teaching_advice.httpx.AsyncClient
        teaching_advice.httpx.AsyncClient = FakeClient
        try:
            res = asyncio.run(teaching_advice.generate_advice())
        finally:
            teaching_advice.httpx.AsyncClient = orig
        self.assertTrue(res["success"], res)
        self.assertEqual(calls["n"], 1)
        latest = get_latest_advice()
        self.assertEqual(latest["advice"]["summary"], "乘法薄弱")
        self.assertEqual(latest["model"], "mock")
        self.assertEqual(latest["advice"]["focus_students"][0]["name"], "张三")

    def test_generate_bad_llm_output(self):
        from backend.models.grading_engine import save_llm_config
        save_llm_config(provider="custom", base_url="https://example.com/v1",
                        model="mock", api_key="sk-test")

        class FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"choices": [{"message": {"content": "不是JSON"}}]}

        class FakeClient:
            def __init__(self, *a, **k):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def post(self, *a, **k):
                return FakeResp()

        orig = teaching_advice.httpx.AsyncClient
        teaching_advice.httpx.AsyncClient = FakeClient
        try:
            res = asyncio.run(teaching_advice.generate_advice())
        finally:
            teaching_advice.httpx.AsyncClient = orig
        self.assertFalse(res["success"])
        self.assertIn("格式", res["error"])

    def test_routes(self):
        with TestClient(app) as client:
            # PIN 解锁（analytics 路由统一 require_admin）
            self.assertEqual(client.post("/api/auth/pin-login", json={"pin": "123456"}).status_code, 200)
            # 预置一条建议（setUp 会清表）
            db = SessionLocal()
            db.add(TeachingAdvice(scope="class", content={"summary": "s", "suggestions": ["a"], "focus_students": []}))
            db.commit()
            db.close()
            # GET 最新
            r = client.get("/api/analytics/teaching-advice")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["success"])
            self.assertIsNotNone(r.json()["advice"])
            # POST generate（无真实 LLM → 返回业务错误而非 500）
            r3 = client.post("/api/analytics/teaching-advice/generate")
            self.assertEqual(r3.status_code, 200)
            # 未解锁时被拒
            client.post("/api/auth/pin-logout")
            r4 = client.post("/api/analytics/teaching-advice/generate")
            self.assertIn(r4.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
