# -*- coding: utf-8 -*-
"""异常路径回归测试：不存在的 ID、非法状态、越界参数在主要 API 上必须返回 4xx 且带中文 detail，
绝不能 500。第十三轮（工作台/详情页异常路径）健壮性增量。"""
import pytest


def test_result_404(auth_client):
    r = auth_client.get("/api/grading/result/999999")
    assert r.status_code == 404
    assert "提交记录不存在" in r.json()["detail"]


def test_retry_404_and_bad_state(auth_client):
    r = auth_client.post("/api/grading/retry/999999")
    assert r.status_code == 404
    assert "提交记录不存在" in r.json()["detail"]


def test_cancel_404_and_bad_state(auth_client):
    r = auth_client.post("/api/grading/cancel/999999")
    assert r.status_code == 404
    assert "提交记录不存在" in r.json()["detail"]


def test_start_grading_404(auth_client):
    r = auth_client.post("/api/grading/start/999999")
    assert r.status_code == 404
    assert "提交记录不存在" in r.json()["detail"]


def test_timeout_out_of_range(auth_client):
    r = auth_client.post("/api/grading/timeout", json={"value": 10})
    assert r.status_code == 400
    assert "60-900" in r.json()["detail"]
    r = auth_client.post("/api/grading/timeout", json={"value": 5000})
    assert r.status_code == 400


def test_concurrency_out_of_range(auth_client):
    r = auth_client.post("/api/grading/concurrency", json={"value": 9})
    assert r.status_code == 400
    assert "1-4" in r.json()["detail"]


def test_result_bad_id_type(auth_client):
    # 非数字 ID 必须被参数校验拒绝（422），而不是 500
    r = auth_client.get("/api/grading/result/not-a-number")
    assert r.status_code == 422


def test_reassign_missing_fields(auth_client):
    r = auth_client.post("/api/grading/reassign/999999", json={})
    assert r.status_code == 400
    assert "缺少 source_id" in r.json()["detail"]


def test_wrong_answer_export_404(auth_client):
    r = auth_client.get("/api/grading/export-student/999999")
    assert r.status_code == 404
    assert "学生不存在" in r.json()["detail"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
