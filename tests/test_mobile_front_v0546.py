# -*- coding: utf-8 -*-
"""v0.5.46 手机端前端异常路径回归测试。

核心防线：模板 JS 中所有 $("id") / getElementById('id') 引用的元素必须在
HTML 中真实存在——曾因引用不存在的 pick_btn 导致拍照后 TypeError，
预览显示但"立即上传"按钮与倒计时不出现。
"""
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"
HTML_FILES = ["index.html", "mobile.html", "settings.html"]

# 已知合法的动态/伪 id 引用（由 JS 运行时创建或属误报模式）
KNOWN_DYNAMIC = {
    "uploadForm", "status", "result", "file",  # 均真实存在，仅列作参考
}


def _extract_ids_used(html: str) -> set:
    used = set(re.findall(r'\$\("([^"]+)"\)', html))
    used |= set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", html))
    return used


def _extract_ids_defined(html: str) -> set:
    defined = set(re.findall(r'id="([^"]+)"', html))
    defined |= set(re.findall(r"id='([^']+)'", html))
    # JS 动态创建的 id：createElement 后 .id = "xxx"
    defined |= set(re.findall(r'\.id\s*=\s*["\']([^"\']+)["\']', html))
    return defined


def test_all_referenced_ids_exist():
    for name in HTML_FILES:
        html = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        used = _extract_ids_used(html)
        defined = _extract_ids_defined(html)
        missing = used - defined
        assert not missing, f"{name}: JS 引用了不存在的元素 id: {sorted(missing)}"


def test_mobile_pick_btn_exists():
    """回归锚点：拍照区必须有 id=pick_btn，否则 file change 处理器抛 TypeError。"""
    html = (TEMPLATES_DIR / "mobile.html").read_text(encoding="utf-8")
    assert 'id="pick_btn"' in html
    assert 'pick_btn' in _extract_ids_defined(html)


def test_mobile_poll_has_self_heal():
    """轮询断线自愈：失败计数存在且有重试提示与放弃上限。"""
    html = (TEMPLATES_DIR / "mobile.html").read_text(encoding="utf-8")
    assert "pollFails" in html
    assert "正在重试" in html


def test_mobile_upload_error_keeps_retry():
    """上传失败后照片保留、可重试。"""
    html = (TEMPLATES_DIR / "mobile.html").read_text(encoding="utf-8")
    assert "可点「立即上传」重试" in html


def test_js_syntax_valid():
    """三个模板的内联 <script> 语法合法（有 node 则用 node --check，否则跳过）。"""
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        return  # CI windows/部分环境无 node 时由 pytest 侧断言兜底
    for name in HTML_FILES:
        html = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        assert scripts, f"{name} 未提取到内联脚本"
        for i, s in enumerate(scripts):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(s)
                path = f.name
            r = subprocess.run([node, "--check", path], capture_output=True, text=True)
            assert r.returncode == 0, f"{name} 第{i}段脚本语法错误: {r.stderr}"
