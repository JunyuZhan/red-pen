# -*- coding: utf-8 -*-
"""v0.5.50 手机端结果页 XSS 回归测试。

核心防线：手机端 show() 将 AI 识别文本（学生答案/参考答案/错误类型）
innerHTML 渲染，此前未转义——工作台 v0.5.47 已转义但手机端漏网。
"""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"
MOBILE = TEMPLATES_DIR / "mobile.html"


def _show_body() -> str:
    html = MOBILE.read_text(encoding="utf-8")
    m = re.search(r"function show\(d\) \{(.*?)\n\}", html, re.S)
    assert m, "mobile.html 中找不到 show() 函数"
    return m.group(1)


def test_mobile_show_escapes_ai_text():
    body = _show_body()
    for field in ("q.question_no", "q.error_type", "q.student_answer", "q.correct_answer"):
        # 每个字段插值处必须经 esc() 包裹
        assert re.search(r"esc\(" + re.escape(field) + r"\)", body), f"show() 中 {field} 未转义"
    assert "${q.student_answer}" not in body and "${q.correct_answer}" not in body


def test_mobile_show_has_esc_helper():
    body = _show_body()
    assert "const esc = s =>" in body
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in body


def test_mobile_show_annotated_image_preserved():
    """回归锚点：防转义修复误删批注图展示。"""
    body = _show_body()
    assert "annotated_image_url" in body
    assert "annotated" in body


def test_workbench_ai_text_escaped_still():
    """工作台 AI 识别文本转义防线仍在（v0.5.47 引入）。"""
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    assert html.count("esc(s) =>") >= 1 or html.count("const esc = s =>") >= 3
