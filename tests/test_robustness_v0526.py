# -*- coding: utf-8 -*-
"""第九轮健壮性（v0.5.26）：工作台/详情页/分析页异常路径。

探针发现的问题与回归覆盖：
- 工作台汇总 /api/dashboard/summary 在脏数据（cost_estimate/prompt_tokens 被
  写成字符串，如历史库/手工导入）时不再 500，坏字段按 0 计入
- 批量批改 /api/grading/batch 明确上报 not_found / skipped，
  不再对不存在的提交静默返回 queued:[]
- 分析页在孤儿 QuestionResult（对应提交已删）+ 脏 confidence 字符串下仍 200
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_robustness_v0524 import rx_env  # noqa: E402,F401  复用第七轮的测试环境夹具


def _raw_sql(env, sql):
    from sqlalchemy import text
    db = env["dbmod"].SessionLocal()
    try:
        db.execute(text(sql))
        db.commit()
    finally:
        db.close()


def test_dashboard_summary_survives_dirty_numeric_fields(rx_env):
    """cost_estimate/prompt_tokens 被写坏成字符串 → 汇总按 0 计入，不 500。"""
    c = rx_env["client"]
    _raw_sql(rx_env,
             "INSERT INTO submission (assignment_id, student_id, original_image_path,"
             " status, cost_estimate, prompt_tokens, completion_tokens)"
             " VALUES (1, 1, '/tmp/x.jpg', 'completed', 'not-a-number', 'abc', 'oops')")
    r = c.get("/api/dashboard/summary")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["success"] is True
    assert data["total_cost"] == 0
    assert data["total_tokens"] == 0
    assert data["total_submissions"] >= 1


def test_batch_reports_not_found_and_skipped(rx_env):
    """批量批改：不存在的 id 进 not_found；状态不允许的进 skipped，不静默。"""
    c = rx_env["client"]
    from test_robustness_v0524 import make_sub
    done_id = make_sub(rx_env, status="completed")  # 状态不允许 → skipped

    r = c.post("/api/grading/batch", json={"submission_ids": [done_id, 99999]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["queued"] == []
    assert 99999 in data["not_found"]
    assert done_id in data["skipped"]


def test_batch_queued_still_counts(rx_env):
    """正常 uploaded 提交仍能入队，count/queued 语义不变。"""
    c = rx_env["client"]
    from test_robustness_v0524 import make_sub
    sid = make_sub(rx_env, status="uploaded")
    r = c.post("/api/grading/batch", json={"submission_ids": [sid]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["queued"] == [sid]
    assert data["count"] == 1
    assert data["not_found"] == []
    assert data["skipped"] == []


def test_analytics_survives_orphan_results_and_dirty_confidence(rx_env):
    """孤儿 QuestionResult（提交已删）+ confidence 存成字符串 → 分析/趋势仍 200。"""
    c = rx_env["client"]
    _raw_sql(rx_env,
             "INSERT INTO question_result (submission_id, question_no, question_type,"
             " judgement_final, confidence_final)"
             " VALUES (99999, 1, 'fill', 'wrong', 0.2)")
    _raw_sql(rx_env,
             "INSERT INTO submission (assignment_id, student_id, original_image_path, status)"
             " VALUES (1, 1, '/tmp/y.jpg', 'completed')")
    sid = rx_env["dbmod"].SessionLocal().query(
        rx_env["dbmod"].Submission).order_by(
        rx_env["dbmod"].Submission.id.desc()).first().id
    _raw_sql(rx_env,
             "INSERT INTO question_result (submission_id, question_no, question_type,"
             " judgement_final, confidence_final)"
             f" VALUES ({sid}, 1, 'choice', 'correct', '0.777')")

    for path in ("/api/analytics/analytics", "/api/analytics/analytics/trends"):
        r = c.get(path)
        assert r.status_code == 200, f"{path}: {r.text}"
        assert r.json()["success"] is True
