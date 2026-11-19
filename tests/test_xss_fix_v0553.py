# -*- coding: utf-8 -*-
"""v0.5.53 工作台列表 XSS 补漏回归测试。

loadRows 把 friendlyError(error_message) 的结果直接 innerHTML 插入且未转义：
当错误信息不命中任何已知模式时 friendlyError 原文返回，API/上游错误文本可能
携带 HTML/JS，注入失败原因提示区。修复后所有插入点一律经 escapeHtml。
grade 徽章同理补转义。
"""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"
INDEX = TEMPLATES_DIR / "index.html"


def _index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_loadrows_error_message_escaped():
    """失败原因 friendlyError 输出必须经 escapeHtml 后再插入 innerHTML。"""
    html = _index_html()
    m = re.search(r"friendlyError\((.*?)\)", html)
    assert m, "loadRows 缺少 friendlyError 调用"
    line_start = html.rfind("\n", 0, m.start()) + 1
    line = html[line_start:html.find("\n", m.start())]
    assert "escapeHtml(friendlyError" in line, \
        "friendlyError 输出未转义——未知错误原文（可能含 HTML）直接注入 DOM"


def test_loadrows_grade_badge_escaped():
    """grade 徽章必须转义，不允许 ${x.grade} 裸插。"""
    html = _index_html()
    assert "${x.grade}</span>" not in html, "grade 徽章未转义"
    assert "escapeHtml(x.grade)" in html


def test_loadrows_summary_escaped():
    """summary 已转义（历史修复保持不回退）。"""
    html = _index_html()
    assert "escapeHtml(x.summary" in html
