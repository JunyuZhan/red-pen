"""scripts/accuracy_check.py 评测逻辑（真相比对打分）单元测试——不调真实 API。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "accuracy_check", ROOT / "scripts" / "accuracy_check.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_truth_uses_str_keys():
    # ANSWERS 以字符串题号为键，匹配 GradingEngine 的 answers 约定
    assert mod.ANSWERS == {"1": "100", "2": "95", "3": "100", "4": "9"}
    assert len(mod.TRUTH) == 4
    assert mod.TRUTH[2]["expect"] == "wrong"


def test_score_perfect():
    result = {"success": True, "questions": [
        {"question_no": i + 1, "judgement": t["expect"], "confidence": 0.9,
         "student_answer": t["student_answer"]}
        for i, t in enumerate(mod.TRUTH)],
        "summary": "全对"}
    hit, rows = mod.score(result)
    assert hit == 4
    assert all(r[4] == "✓" for r in rows)


def test_score_wrong_marked_partial_counts():
    # 第 3 题被判 partial（部分对）也应计入 wrong 期望的命中
    qs = [{"question_no": i + 1, "judgement": t["expect"], "confidence": 0.8}
          for i, t in enumerate(mod.TRUTH)]
    qs[2]["judgement"] = "partial"
    hit, rows = mod.score({"success": True, "questions": qs})
    assert hit == 4


def test_score_missing_question_not_hit():
    # 模型漏读第 2 题时不应命中
    qs = [{"question_no": i + 1, "judgement": t["expect"], "confidence": 0.8}
          for i, t in enumerate(mod.TRUTH) if i != 1]
    hit, _ = mod.score({"success": True, "questions": qs})
    assert hit == 3


def test_sample_image_generated(tmp_path):
    p = mod.make_sample_image(tmp_path / "s.png")
    assert p.exists() and p.stat().st_size > 1000
    from PIL import Image
    img = Image.open(p)
    assert img.size == (720, 560)
