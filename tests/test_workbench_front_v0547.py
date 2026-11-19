# -*- coding: utf-8 -*-
"""v0.5.47 工作台异常路径加固回归测试。

背景（健壮性第十轮工作台版）：
- 班级名/学生手写识别文本（来自 OCR/LLM，不可信）曾直接拼进 innerHTML 与
  onclick 字符串——含引号/尖括号会破坏 UI 甚至注入脚本。
- ensureAdmin 网络失败抛未处理 Promise 拒绝，管理区/学情区加载链中断。
- view() 对 404 等非 200 响应静默渲染空详情；pinUnlock 非 JSON 响应提示空白。
"""
import re
import subprocess
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"
INDEX = TEMPLATES_DIR / "index.html"


def _js(html: str) -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))


def test_wizchip_uses_index_not_string_interpolation():
    """引导选项 onclick 必须按索引传值，禁止把班级名拼进 onclick 字符串。"""
    js = _js(INDEX.read_text(encoding="utf-8"))
    assert "wizPick(${idx}" in js, "wizChip 应按索引调用 wizPick"
    assert "wizPick('" not in js, "禁止字符串拼接 onclick（班级名含引号会破坏按钮/注入）"


def test_wizchip_labels_escaped():
    js = _js(INDEX.read_text(encoding="utf-8"))
    assert "function escapeHtml(" in js
    # wizChip 的 label 与学生答案/总评渲染都必须走 escapeHtml
    chip = js[js.index("function wizChip("):js.index("function wizRender")]
    assert "escapeHtml(label)" in chip
    assert "${escapeHtml(x.summary" in js
    assert "${escapeHtml(q.student_answer" in js
    assert "${escapeHtml(d.summary" in js


def test_wizpick_guards_missing_index():
    js = _js(INDEX.read_text(encoding="utf-8"))
    assert "if (value === undefined) return;" in js
    assert "_wizChipValues = [];" in js  # 渲染前重置，防索引错位


def test_ensure_admin_never_breaks_bootstrap():
    """pin-status 拉取失败必须吞掉异常返回 false，不产生未处理 Promise 拒绝。"""
    js = _js(INDEX.read_text(encoding="utf-8"))
    seg = js[js.index("async function ensureAdmin("):js.index("async function pinUnlock(")]
    assert "catch" in seg
    assert "loadManage().catch(() => {})" in js
    assert "loadAnalytics().catch(() => {})" in js


def test_view_alerts_on_non_ok():
    js = _js(INDEX.read_text(encoding="utf-8"))
    seg = js[js.index("async function view("):js.index("function exportCsv(")]
    assert "if (!r.ok)" in seg, "详情接口非 200 必须提示，不得静默渲染空详情"


def test_pin_unlock_tolerates_non_json():
    js = _js(INDEX.read_text(encoding="utf-8"))
    seg = js[js.index("async function pinUnlock("):js.index("// ---------- 首次三步引导")]
    assert "r.json().catch" in seg
    assert "请重试" in seg


def test_inline_js_syntax_ok():
    """全部内联脚本 node --check 通过（沿用 v0.5.46 防线）。"""
    node = "node"
    for name in ("index.html", "mobile.html", "settings.html"):
        html = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        for i, block in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            p = Path(f"/tmp/_jschk_{name}_{i}.js")
            p.write_text(block, encoding="utf-8")
            r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
            assert r.returncode == 0, f"{name} 第{i}段内联脚本语法错误: {r.stderr}"
            p.unlink(missing_ok=True)
