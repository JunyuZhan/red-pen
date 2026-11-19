# -*- coding: utf-8 -*-
"""v0.5.52 设置页前端异常路径回归测试。

核心防线一：settings.html 必须定义 $ 帮助函数——index/mobile 各自定义了
`const $ = id => document.getElementById(id)`，settings 用了 $ 却从未定义，
ensureAdmin() 一执行就 ReferenceError，整个设置页加载链全挂（PIN 弹层不出、
表单全空）。

核心防线二：所有加载链 fetch 异常必须有兜底（loadFail 横幅/局部 catch），
不再静默卡死或 Unhandled Rejection。
"""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"
SETTINGS = TEMPLATES_DIR / "settings.html"


def _settings_html() -> str:
    return SETTINGS.read_text(encoding="utf-8")


def test_settings_defines_dollar_helper():
    """回归锚点：settings.html 曾使用 $() 却未定义，导致整页 JS 崩溃。"""
    html = _settings_html()
    assert re.search(r"const \$\s*=\s*id\s*=>\s*document\.getElementById\(id\)", html), \
        "settings.html 缺少 $ 帮助函数定义，所有用 $() 的 JS 都会 ReferenceError"


def test_settings_has_load_fail_banner():
    """所有加载链失败统一亮起横幅提示，不允许静默卡死。"""
    html = _settings_html()
    assert 'id="loadErrBanner"' in html
    assert "function loadFail()" in html
    assert "加载失败：服务未响应，请刷新页面重试" in html


def test_settings_ensure_admin_catches_fetch_failure():
    """pin-status 拉取失败必须走 catch（失败关闭显示 PIN 弹层），不能裸 await。"""
    html = _settings_html()
    m = re.search(r"async function ensureAdmin\(\) \{(.*?)\n\}", html, re.S)
    assert m, "settings.html 缺少 ensureAdmin 函数"
    body = m.group(1)
    assert "try {" in body and "catch" in body, "ensureAdmin 的 pin-status fetch 无异常兜底"
    assert "请刷新页面重试" in body


def test_settings_loader_chain_all_guarded():
    """初始化加载链每个 loader 都要挂 .catch(loadFail)，链本身也要兜底。"""
    html = _settings_html()
    m = re.search(r"ensureAdmin\(\)\.then\(.*?\.catch\(loadFail\)", html, re.S)
    assert m, "ensureAdmin 初始化链缺少 loadFail 兜底"
    chain = m.group(0)
    assert "forEach" in chain and "catch(loadFail)" in chain, "链上 loader 未逐个挂 .catch(loadFail)"


def test_settings_save_refresh_calls_have_catch():
    """保存成功后刷新加载器的调用必须带 .catch，否则服务异常时 Unhandled Rejection。"""
    html = _settings_html()
    assert "load().catch(loadFail)" in html
    assert "loadBudget().catch(loadFail)" in html
    assert "loadFallback().catch(loadFail)" in html
    # 不允许存在任何裸的 loadXxx(); 调用（分号结尾且无 .catch）
    bare = [b for b in re.findall(r"\b(load[A-Z]\w*)\(\);\s*$", html, re.M) if b != "loadNetwork"]
    assert not bare, f"存在未挂 .catch 的裸加载调用: {bare}"


def test_settings_grade_pin_logs_have_catch():
    """saveGrade / pinUnlock / loadLogs / openLogFolder 必须有 try/catch 人话提示。"""
    html = _settings_html()
    for fn in ("saveGrade", "pinUnlock", "loadLogs", "openLogFolder"):
        m = re.search(r"async function " + fn + r"\(\)\s*\{(.*?)\n\}", html, re.S)
        assert m, f"缺少 {fn}"
        assert "catch" in m.group(1), f"{fn} 无异常兜底"
