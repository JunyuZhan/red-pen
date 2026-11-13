"""切题模式合并去重（遗留项②）：跨裁剪区的冗余「未作答」记录清理"""
from backend.models.grading_engine import _dedupe_split_questions, _is_blank_answer


def _q(no, ans):
    return {"question_no": no, "judgement": "wrong", "student_answer": ans,
            "confidence": 0.9, "bbox": None, "steps": None}


def test_same_no_blank_duplicate_dropped():
    """同一题号：有真实答案的优先，冗余未作答剔除"""
    qs = [_q(1, "3+4=7"), _q(2, "12"), _q(1, "未作答")]
    out = _dedupe_split_questions(qs)
    nos = [q["question_no"] for q in out]
    assert nos == [1, 2]


def test_all_blank_same_no_keeps_first():
    """同一题号全是空白（学生真没写跨两区）：保留一条，不丢判"""
    qs = [_q(1, "未作答"), _q(1, "未作答"), _q(2, "")]
    out = _dedupe_split_questions(qs)
    assert len(out) == 2
    assert [q["question_no"] for q in out] == [1, 2]


def test_unnumbered_blank_fragment_dropped_when_numbered_exist():
    """无题号 + 空答案 = 裁剪碎片（页眉残留），已有题号结果时剔除"""
    qs = [_q(1, "5"), {"question_no": None, "student_answer": "未作答"}, _q(2, "8")]
    out = _dedupe_split_questions(qs)
    assert [q.get("question_no") for q in out] == [1, 2]


def test_unnumbered_answered_kept():
    """无题号但有真实答案（模型实在读不出题号）：保留，不丢批改"""
    qs = [_q(1, "5"), {"question_no": None, "student_answer": "x=3"}]
    out = _dedupe_split_questions(qs)
    assert len(out) == 2


def test_all_unnumbered_conservative():
    """整卷都无题号：保守不动，原样保留"""
    qs = [{"question_no": None, "student_answer": "未作答"},
          {"question_no": None, "student_answer": ""}]
    assert _dedupe_split_questions(qs) == qs


def test_empty_and_non_dict():
    assert _dedupe_split_questions([]) == []
    out = _dedupe_split_questions(["bad", _q(1, "9")])
    assert len(out) == 1 and out[0]["question_no"] == 1


def test_blank_words_consistent():
    assert _is_blank_answer(None) is True
    assert _is_blank_answer(" 未填 ") is True
    assert _is_blank_answer("NULL") is True
    assert _is_blank_answer("3.14") is False
