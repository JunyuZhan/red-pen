"""模板完整性检查：script 标签外不允许出现游离的 JS 代码（会被当纯文本渲染）。

背景 bug：mobile.html 曾把 friendlyError 函数放在 </script> 之后，
浏览器把整段 JS 源码当正文渲染在页面底部。
"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "frontend" / "templates"


def test_templates_no_orphan_code_outside_script():
    for tpl in TEMPLATES.glob("*.html"):
        html = tpl.read_text(encoding="utf-8")
        # 移除所有 <script>...</script> 块后，body 内不应残留 JS 语法痕迹
        stripped = re.sub(r"<script[\s\S]*?</script>", "", html)
        body = re.search(r"<body[\s\S]*?(?:</body>|$)", stripped)
        assert body, f"{tpl.name}: 缺少 body"
        text = body.group(0)
        # 游离 JS 的典型特征：function 定义 / 以 // 注释开头的行
        assert not re.search(r"^\s*function\s+\w+\s*\(", text, re.M), \
            f"{tpl.name}: script 标签外存在游离的 function 定义"
        assert not re.search(r"^\s*//\s", text, re.M), \
            f"{tpl.name}: script 标签外存在游离的 JS 注释行"


def test_mobile_friendly_error_inside_script():
    html = (TEMPLATES / "mobile.html").read_text(encoding="utf-8")
    m = re.search(r"function friendlyError", html)
    assert m, "mobile.html 缺少 friendlyError"
    # 该定义必须位于某个 <script> 块内
    for sm in re.finditer(r"<script[\s\S]*?</script>", html):
        if sm.start() < m.start() < sm.end():
            return
    raise AssertionError("friendlyError 定义不在任何 <script> 块内")
