# -*- coding: utf-8 -*-
"""规则交叉校验层专项测试：准确性保障的核心防线（pytest 原生化）"""
from backend.models.rule_validator import (
    answers_equivalent, rule_validate, _numbers,
)


def _q(**kw):
    base = {"question_no": 1, "judgement": "correct", "student_answer": "",
            "confidence": 0.9}
    base.update(kw)
    return [base]


class TestAnswersEquivalent:
    def test_numeric_equal_3_0_vs_3(self):
        assert answers_equivalent("3.0", "3") is True

    def test_fraction_1_2_vs_0_5(self):
        assert answers_equivalent("1/2", "0.5") is True

    def test_numeric_not_equal(self):
        assert answers_equivalent("5", "6") is False

    def test_choice_case_insensitive(self):
        assert answers_equivalent("A", "a") is True

    def test_choice_different(self):
        assert answers_equivalent("A", "B") is False

    def test_fullwidth_digit(self):
        assert answers_equivalent("３", "3") is True

    def test_comma_thousands(self):
        assert answers_equivalent("1,234", "1234") is True

    def test_semantic_returns_none(self):
        assert answers_equivalent("因为三角形内角和180度", "180") is None

    def test_negative(self):
        assert answers_equivalent("-3", "-3.0") is True

    def test_empty_returns_none(self):
        assert answers_equivalent("", "3") is None

    def test_numbers_fraction(self):
        assert _numbers("3/4") == [0.75]

    def test_numbers_decimal_suffix(self):
        assert _numbers("12.50") == [12.5]


class TestBlankForcedWrong:
    def test_blank_answer_forced_wrong(self):
        qs = _q(judgement="unclear", student_answer="", confidence=0.3)
        rule_validate(qs, {"1": "42"})
        assert qs[0]["judgement"] == "wrong"
        assert qs[0]["error_type"] == "未作答"
        assert qs[0]["confidence"] == 0.98
        assert "rule_forced_blank" in qs[0]["rule_flags"]

    def test_placeholder_text_forced_wrong(self):
        qs = _q(judgement="correct", student_answer="没写", confidence=0.9)
        rule_validate(qs, {"1": "42"})
        assert qs[0]["judgement"] == "wrong"


class TestOverride:
    def test_wrong_overridden_to_correct(self):
        qs = _q(judgement="wrong", student_answer="3.0", confidence=0.7)
        rule_validate(qs, {"1": "3"})
        assert qs[0]["judgement"] == "correct"
        assert "rule_overridden_to_correct" in qs[0]["rule_flags"]

    def test_correct_numeric_mismatch_to_wrong(self):
        qs = _q(question_no=2, judgement="correct", student_answer="56")
        rule_validate(qs, {"2": "65"})
        assert qs[0]["judgement"] == "wrong"

    def test_choice_mismatch_to_wrong(self):
        qs = _q(question_no=3, judgement="correct", student_answer="C",
                confidence=0.85)
        rule_validate(qs, {"3": "A"})
        assert qs[0]["judgement"] == "wrong"

    def test_wrong_but_equal_to_correct(self):
        qs = _q(judgement="wrong", student_answer="120", confidence=0.6)
        rule_validate(qs, {"1": "120"})
        assert qs[0]["judgement"] == "correct"

    def test_decimal_equivalent_keeps_correct(self):
        qs = _q(judgement="correct", student_answer="12.50", confidence=0.8)
        rule_validate(qs, {"1": "12.5"})
        assert qs[0]["judgement"] == "correct"


class TestSemanticNeedsReview:
    def test_semantic_lowered_confidence_and_review(self):
        qs = [{"question_no": 4, "judgement": "correct",
               "student_answer": "因为两边之和大于第三边", "confidence": 0.9}]
        rule_validate(qs, {"4": "两点之间线段最短"})
        assert qs[0]["judgement"] == "correct"
        assert qs[0]["confidence"] == 0.55
        assert qs[0].get("needs_review") is True
        assert "rule_needs_review" in qs[0]["rule_flags"]


class TestEdgeCases:
    def test_no_reference_answer_skipped(self):
        qs = _q(question_no=9, student_answer="7", confidence=0.8)
        s = rule_validate(qs, {})
        assert s["checked"] == 0
        assert qs[0]["judgement"] == "correct"
