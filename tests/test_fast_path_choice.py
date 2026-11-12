# -*- coding: utf-8 -*-
"""选择题快路径（PRD 4.3）：纯选择题只让模型读选项，判题由本地规则完成"""
import asyncio

import pytest

from backend.models.grading_engine import (
    _build_prompt,
    _is_choice_only,
    _questions_from_choices,
    GradingEngine,
)


def test_is_choice_only_variants():
    assert _is_choice_only({"1": "A", "2": "B", "3": "D"})
    assert _is_choice_only({"1": "(A)", "2": "B.", "3": "（C）"})  # 容忍包裹
    assert _is_choice_only({"1": "a", "2": "h"})
    assert not _is_choice_only(None)
    assert not _is_choice_only({"1": "A"})  # 单题不启用
    assert not _is_choice_only({"1": "A", "2": "对"})  # 含非选项
    assert not _is_choice_only({"1": "AB", "2": "C"})  # 多选字母不行
    assert not _is_choice_only({"1": "12", "2": "13"})  # 数值题不走快路径


def test_read_prompt_is_read_only():
    p = _build_prompt("choice", "数学", {"1": "A", "2": "C"})
    assert "不要判题" in p and "student_choice" in p
    # 快路径不把参考答案发给模型（本地判题，省 token 且不泄漏答案影响读图）
    assert "参考答案" not in p


def test_full_prompt_not_triggered_for_mixed_answers():
    p = _build_prompt("choice", "数学", {"1": "A", "2": "3.5"})
    assert "逐题批改" in p and "student_choice" not in p


def test_questions_from_choices_rule_judging():
    answers = {"1": "A", "2": "C", "3": "B"}
    parsed = {"choices": [
        {"question_no": 1, "student_choice": "A"},
        {"question_no": 2, "student_choice": "D"},
        {"question_no": 3, "student_choice": None},
    ]}
    qs = _questions_from_choices(parsed, answers)
    assert [q["judgement"] for q in qs] == ["correct", "wrong", "unclear"]
    assert qs[0]["confidence"] == 0.95 and qs[2]["confidence"] == 0.5
    assert qs[1]["correct_answer"] == "C"
    assert qs[2]["student_answer"] == "未作答"


def test_questions_from_choices_ignores_garbage_items():
    qs = _questions_from_choices({"choices": ["bad", 42]}, {"1": "A"})
    assert qs == []


def _make_engine():
    return GradingEngine(api_key="k", model_name="test-model", base_url="http://mock")


def test_grade_end_to_end_fast_path(tmp_db, tmp_path, monkeypatch):
    """整条链路：纯选择题参考答案 → 读选项 prompt → mock 返回 choices → 规则判题"""
    from PIL import Image
    p = tmp_path / "hw.png"
    Image.new("L", (64, 64), 200).save(str(p))

    content = ('{"choices": [{"question_no": 1, "student_choice": "A"},'
               ' {"question_no": 2, "student_choice": "B"}]}')

    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

        @property
        def text(self):
            return ""

    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return FakeResp()

    import backend.models.grading_engine as ge
    monkeypatch.setattr(ge.httpx, "AsyncClient", FakeClient)
    answers = {"1": "A", "2": "C"}
    result = asyncio.run(_make_engine().grade(str(p), "choice", "数学", answers=answers))
    assert result["success"] and result["fast_path"] is True
    assert result["judgement"] == "wrong"  # 第2题不一致
    assert result["questions"][0]["judgement"] == "correct"
    assert result["questions"][1]["judgement"] == "wrong"
    assert result["rule_validation"]["fast_path"] is True
    # 请求体里的 prompt 是只读 prompt
    text_part = captured["payload"]["messages"][0]["content"][1]["text"]
    assert "不要判题" in text_part


def test_fast_path_unclear_overall(tmp_db, tmp_path, monkeypatch):
    """全部读不出来 → 整卷 unclear，进人工复核路径"""
    from PIL import Image
    p = tmp_path / "hw.png"
    Image.new("L", (64, 64), 200).save(str(p))

    content = '{"choices": [{"question_no": 1, "student_choice": null}]}'

    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": content}}], "usage": {}}

        @property
        def text(self):
            return ""

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return FakeResp()

    import backend.models.grading_engine as ge
    monkeypatch.setattr(ge.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(_make_engine().grade(str(p), "choice", "数学", answers={"1": "A", "2": "B"}))
    assert result["fast_path"] is True and result["judgement"] == "unclear"
