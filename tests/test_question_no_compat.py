"""题号兼容回归：口算1 / 1(1) / 2. 等答案表格式不再 int 崩溃 (v0.5.42 fix)。"""
from backend.routes.grading import _parse_question_no


def test_mixed_format_takes_digits():
    assert _parse_question_no("口算1", 9) == 1
    assert _parse_question_no("1(1)", 9) == 1
    assert _parse_question_no("2.", 9) == 2
    assert _parse_question_no(" 第10题 ", 9) == 10


def test_no_digits_falls_back_to_index():
    assert _parse_question_no(None, 3) == 3
    assert _parse_question_no("", 5) == 5
    assert _parse_question_no("口算题", 7) == 7


def test_long_digits_truncated_to_3():
    assert _parse_question_no("2024-15", 1) == 202
