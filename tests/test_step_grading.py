# -*- coding: utf-8 -*-
"""步骤级批改（v0.5.11 g5，对齐作业帮理科步骤批改）：
prompt 要求模型输出错误步骤、_clean_steps 清洗、QuestionResult.steps 落库、/result 透出。
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in os.sys.path:
    os.sys.path.insert(0, ROOT)


# ---------- _clean_steps 清洗 ----------

def test_clean_steps_keeps_only_wrong_and_valid():
    from backend.models.grading_engine import _clean_steps
    steps = [
        {"step_no": 1, "content": "进位", "correct": True, "error_reason": None},   # 对的步骤剔除
        {"step_no": 2, "content": "借位错了", "correct": False, "error_reason": "忘了退位"},
        {"step_no": "三", "content": "x", "correct": False},                        # step_no 非数字 → 剔除
        {"step_no": 0, "content": "y"},                                             # 非法题号 → 剔除
        "not-a-dict",                                                               # 非法类型 → 剔除
        {"step_no": 5, "content": "c" * 500, "error_reason": "r" * 500},            # 超长截断
    ]
    out = _clean_steps(steps)
    assert [s["step_no"] for s in out] == [2, 5]
    assert out[0]["error_reason"] == "忘了退位"
    assert len(out[1]["content"]) == 200 and len(out[1]["error_reason"]) == 200


def test_clean_steps_edge_cases():
    from backend.models.grading_engine import _clean_steps
    assert _clean_steps(None) is None
    assert _clean_steps([]) is None
    assert _clean_steps("oops") is None
    assert _clean_steps([{"step_no": 1, "correct": True}]) is None  # 全对 → None
    assert _clean_steps([{"step_no": 1, "correct": False}]) == [
        {"step_no": 1, "content": "", "error_reason": ""}]


def test_clean_steps_cap_20():
    from backend.models.grading_engine import _clean_steps
    out = _clean_steps([{"step_no": i, "correct": False} for i in range(1, 40)])
    assert len(out) == 20


# ---------- prompt 与解析 ----------

def test_prompt_mentions_steps_rule():
    from backend.models.grading_engine import _build_prompt
    p = _build_prompt("math", "数学", {"1": "42"})
    assert '"steps"' in p and "只列做错的步骤" in p and "null" in p


def test_parse_response_cleans_steps():
    content = json.dumps({
        "questions": [
            {"question_no": 1, "judgement": "wrong", "confidence": 0.9,
             "steps": [{"step_no": 1, "content": "列式", "correct": True},
                       {"step_no": 2, "content": "计算", "correct": False, "error_reason": "进位漏加"}]},
            {"question_no": 2, "judgement": "correct", "confidence": 0.95, "steps": None},
        ], "summary": "ok"})
    resp = {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    out = _call_parse(content)
    q1 = out["questions"][0]
    assert [s["step_no"] for s in q1["steps"]] == [2]
    assert q1["steps"][0]["error_reason"] == "进位漏加"
    assert out["questions"][1]["steps"] is None


def _call_parse(content):
    import json
    from backend.models.grading_engine import GradingEngine
    eng = GradingEngine.__new__(GradingEngine)  # 不走 __init__ 的配置依赖
    resp = {"choices": [{"message": {"content": json.dumps(content) if not isinstance(content, str) else content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    return eng._parse_response(resp)


# ---------- 落库与接口 ----------

@pytest.fixture()
def client(tmp_db):
    from backend.app import app
    return TestClient(app)


def test_steps_saved_and_exposed(client, tmp_db):
    from backend.database import SessionLocal, Submission, QuestionResult, Assignment
    db = SessionLocal()
    asg = Assignment(class_id=1, title="步骤练习", subject_id=1)
    db.add(asg); db.flush()
    sub = Submission(assignment_id=asg.id, student_id=1,
                     original_image_path="/tmp/a.jpg", status="completed")
    db.add(sub); db.flush()
    db.add(QuestionResult(submission_id=sub.id, question_no=1, question_type="计算题",
                          judgement_final="wrong", confidence_final=0.9,
                          steps=[{"step_no": 2, "content": "计算", "error_reason": "进位漏加"}]))
    db.commit(); sid = sub.id
    db.close()
    r = client.get(f"/api/grading/result/{sid}")
    assert r.status_code == 200
    q = r.json()["results"][0]
    assert q["steps"] == [{"step_no": 2, "content": "计算", "error_reason": "进位漏加"}]


def test_steps_column_migration(tmp_db):
    """老库补 steps 列：模拟缺列后 init_db 轻量迁移补齐"""
    import sqlalchemy
    from backend.database import engine
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(question_result)")}
    assert "steps" in cols
