"""scripts/accuracy_check.py 评测逻辑（真相比对打分+批量模式）单元测试——不调真实 API。"""
import importlib.util
import json
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
    hit, rows = mod.score(result, mod.TRUTH)
    assert hit == 4
    assert all(r[4] == "✓" for r in rows)


def test_score_wrong_marked_partial_counts():
    # 第 3 题被判 partial（部分对）也应计入 wrong 期望的命中
    qs = [{"question_no": i + 1, "judgement": t["expect"], "confidence": 0.8}
          for i, t in enumerate(mod.TRUTH)]
    qs[2]["judgement"] = "partial"
    hit, rows = mod.score({"success": True, "questions": qs}, mod.TRUTH)
    assert hit == 4


def test_score_missing_question_not_hit():
    # 模型漏读第 2 题时不应命中
    qs = [{"question_no": i + 1, "judgement": t["expect"], "confidence": 0.8}
          for i, t in enumerate(mod.TRUTH) if i != 1]
    hit, _ = mod.score({"success": True, "questions": qs}, mod.TRUTH)
    assert hit == 3


def test_sample_image_generated(tmp_path):
    p = mod.make_sample_image(tmp_path / "s.png")
    assert p.exists() and p.stat().st_size > 1000
    from PIL import Image
    img = Image.open(p)
    assert img.size == (720, 560)


def test_load_batch(tmp_path):
    # 批量模式：ground_truth.json + 多张图片 -> [(img, truth, answers)]
    (tmp_path / "a.jpg").write_bytes(b"x" * 100)
    (tmp_path / "b.jpg").write_bytes(b"y" * 100)
    gt = [{"image": "a.jpg", "questions": [
              {"question_no": 1, "student_answer": "100", "correct": "100", "expect": "correct"}]},
          {"image": "missing.jpg", "questions": [
              {"question_no": 1, "correct": "9", "expect": "wrong"}]},
          {"image": "b.jpg", "questions": [
              {"question_no": 1, "correct": "9", "expect": "correct"},
              {"question_no": 2, "correct": "7", "expect": "wrong"}]}]
    (tmp_path / "ground_truth.json").write_text(json.dumps(gt), encoding="utf-8")
    cases = mod.load_batch(tmp_path)
    assert [str(c[0].name) for c in cases] == ["a.jpg", "b.jpg"]  # 缺失图片被跳过
    assert cases[0][2] == {"1": "100"}
    assert cases[1][1][1]["expect"] == "wrong"
    assert cases[1][2] == {"1": "9", "2": "7"}


def test_load_batch_requires_truth_file(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        mod.load_batch(tmp_path)


def test_question_stats_and_markdown_report():
    report = {"engines": {"qwen3-vl-plus": {
        "hit": 3, "total": 4, "accuracy": 0.75,
        "cases": [
            {"success": True, "rows": [["1", "25×4=100", "correct", "correct", "✓", "0.9", ""],
                                       ["3", "25×4=96", "correct", "wrong", "✗", "0.5", ""]]},
            {"success": True, "rows": [["3", "25×4=96", "wrong", "wrong", "✓", "0.8", ""]]},
            {"success": False, "error": "boom"},
        ]}}}
    stat = mod.question_stats(report["engines"]["qwen3-vl-plus"]["cases"])
    assert stat == {"1": (1, 1), "3": (1, 2)}  # 失败图不计入
    md = mod.markdown_report(report)
    assert "75.0%" in md and "| 3 | 1/2 | 50% |" in md


def test_engine_reliability_and_md_stats():
    cases = [
        {"success": True, "elapsed_s": 10.0,
         "rows": [["1", "a", "correct", "correct", "✓", "0.9", ""],
                  ["2", "b", "wrong", "wrong", "✓", "0.5", ""]]},
        {"success": True, "elapsed_s": 20.0,
         "rows": [["1", "c", "correct", "correct", "✓", "0.8", ""]]},
        {"success": False, "error": "boom", "elapsed_s": 3.0},
    ]
    avg, low, total = mod.engine_reliability(cases)
    assert avg == 15.0 and low == 1 and total == 3
    md = mod.markdown_report({"engines": {"glm": {
        "hit": 2, "total": 3, "accuracy": 2 / 3, "cases": cases}}})
    assert "平均单图耗时：**15.0s**" in md and "1/3 = 33.3%" in md


def test_engine_reliability_empty_and_bad_conf():
    assert mod.engine_reliability([]) == (None, 0, 0)
    assert mod.engine_reliability([{"success": True, "rows": [["1", "a", "correct", "correct", "✓", None, ""]]},
                                   {"success": True, "rows": [["2", "b", "wrong", "wrong", "✓", "0.6", ""]]}]) \
        == (None, 1, 1)  # 坏置信度不计入，无耗时则 avg=None


def test_engine_reliability_failure_case_timing() -> None:
    assert mod.engine_reliability([{"success": False, "elapsed_s": 5.0}]) == (None, 0, 0)


def test_markdown_report_empty_engine():
    md = mod.markdown_report({"engines": {"glm": {"success": False, "hit": 0, "total": 0,
                                                  "accuracy": 0.0, "cases": []}}})
    assert "无可用结果" in md


def test_error_breakdown_categories():
    # 1: 漏判(期望wrong判成correct)  2: 误判(期望correct判成wrong)  3: OCR读错  4: 正确
    cases = [
        {"success": True,
         "truth": [{"question_no": 1, "student_answer": "96", "correct": "100"}],
         "rows": [["1", "96", "correct", "wrong", "✗", "0.9", "96"]]},
        {"success": True,
         "truth": [{"question_no": 2, "student_answer": "95", "correct": "95"}],
         "rows": [["2", "95", "wrong", "correct", "✗", "0.8", "95"]]},
        {"success": True,
         "truth": [{"question_no": 3, "student_answer": "100", "correct": "100"}],
         "rows": [["3", "100", "correct", "correct", "✓", "0.8", "10O"]]},
        {"success": True,
         "truth": [{"question_no": 4, "student_answer": "9", "correct": "9"}],
         "rows": [["4", "9", "correct", "correct", "✓", "0.8", "9"]]},
        {"success": False, "rows": []},
    ]
    assert mod.error_breakdown(cases) == (1, 1, 1, 4)


def test_error_breakdown_empty_and_md_warning():
    assert mod.error_breakdown([]) == (0, 0, 0, 0)
    md = mod.markdown_report({"engines": {"glm": {"hit": 0, "total": 1, "accuracy": 0.0, "cases": [
        {"success": True, "truth": [{"question_no": 1, "student_answer": "96", "correct": "100"}],
         "rows": [["1", "96", "correct", "wrong", "✗", "0.9", "96"]]}]}}})
    assert "漏判" in md and "不可放心用" in md


def test_error_breakdown_ocr_no_readback_falls_back():
    # 旧格式 rows 只有 6 列时回退用第 2 列（真相学生答案），不算 OCR 错
    cases = [{"success": True,
              "truth": [{"question_no": 1, "student_answer": "96", "correct": "100"}],
              "rows": [["1", "96", "correct", "wrong", "✓", "0.9"]]}]
    assert mod.error_breakdown(cases) == (0, 0, 0, 1)
