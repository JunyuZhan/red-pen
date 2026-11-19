# -*- coding: utf-8 -*-
"""v0.5.54 详情页 onclick 注入修复回归测试。

reassignAnswer 按钮此前把 q.question_no（模型输出，可能含非数字/引号字符）
未加引号直接内插进 onclick 属性——含引号会破坏/注入 JS，含非数字会抛
ReferenceError。修复后 onclick 只传 id，题号经 _qrNoMap 全局映射按 id 读取。
"""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"
INDEX = TEMPLATES_DIR / "index.html"


def _index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_reassign_onclick_no_question_no_interpolation():
    """onclick 里不得再出现 ${q.question_no} 内插。"""
    html = _index_html()
    assert "reassignAnswer(${q.id},${d.submission_id},${q.question_no})" not in html, \
        "题号（模型输出）直接内插进 onclick——含引号可注入，含非数字会抛错"
    assert "onclick=\"reassignAnswer(${q.id},${d.submission_id})\"" in html


def test_qr_no_map_defined_and_populated():
    """_qrNoMap 必须定义并在渲染结果时按 q.id 填充。"""
    html = _index_html()
    assert "const _qrNoMap = {};" in html, "缺少 _qrNoMap 全局映射"
    assert "_qrNoMap[q.id] = q.question_no;" in html, "渲染详情时未填充 _qrNoMap"


def test_reassign_reads_from_map():
    """reassignAnswer 必须从 _qrNoMap 取题号，函数签名只收 id。"""
    html = _index_html()
    m = re.search(r"async function reassignAnswer\((.*?)\)", html)
    assert m, "缺少 reassignAnswer 函数"
    assert {a.strip() for a in m.group(1).split(",")} == {"sourceId", "submissionId"}, \
        "reassignAnswer 签名应只收 sourceId, submissionId"
    assert "_qrNoMap[sourceId]" in html, "题号必须经 _qrNoMap 按 id 读取"
