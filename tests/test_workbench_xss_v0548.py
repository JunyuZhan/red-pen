"""v0.5.48 工作台剩余注入点收口回归测试。

前序 v0.5.47 已转义 AI 识别文本；本轮补齐教师输入侧：作业标题/班级名/学生名/
模板标题/错误类型标签，及 esc 局部函数补引号转义（title 属性含引号会破坏属性）。
"""
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "frontend" / "templates"
INDEX = (TEMPLATES / "index.html").read_text(encoding="utf-8")


def test_assignment_title_escaped_everywhere():
    """作业标题出现在 4 处（最高最低条/作业等级/成绩单/得分矩阵按钮），必须全部 escapeHtml。"""
    assert INDEX.count("${bw.best.title}") == 0
    assert INDEX.count("${bw.worst.title}") == 0
    assert INDEX.count("${a.title") == 0
    assert INDEX.count('escapeHtml(a.title') >= 2


def test_class_and_student_name_escaped():
    """班级名/学生名在班级列表、下拉框、导出按钮、选学生下拉里都要转义。"""
    assert INDEX.count("${c.name}") == 0
    assert INDEX.count("${st.name}") == 0
    assert INDEX.count('escapeHtml(c.name)') >= 2
    assert INDEX.count('escapeHtml(st.name)') >= 1


def test_template_title_and_subject_escaped():
    assert INDEX.count("${t.title}") == 0
    assert INDEX.count("${t.subject}") == 0


def test_open_student_report_passes_id_only():
    """学生名含引号时 '...','${esc(name)}' 会破坏 onclick JS 字符串——改为只传 id，名字走 _stuNameMap。"""
    assert "openStudentReport(${s.student_id},'${esc(s.name)}')" not in INDEX
    assert 'openStudentReport(${s.student_id})' in INDEX
    assert "_stuNameMap[studentId]" in INDEX
    assert "_stuNameMap[s.student_id] = s.name" in INDEX


def test_error_type_keys_escaped():
    assert '([k, v]) => `<span class="tag">${k}' not in INDEX
    assert '([t, n]) => `${t} ${n}次`' not in INDEX


def test_local_esc_functions_escape_quotes():
    """局部 esc 用于 title="..." 属性，必须转义引号；旧的 &/< 两段式不够。"""
    old = 'const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;");'
    assert old not in INDEX
    assert INDEX.count('replace(/\'/g,"&#39;")') >= 3


def test_js_syntax_valid():
    import subprocess
    src = INDEX
    assert "<script>" in src
    scripts = []
    parts = src.split("<script>")
    for part in parts[1:]:
        body = part.split("</script>")[0]
        scripts.append(body)
    assert scripts, "index.html 应包含内联脚本"
    for i, body in enumerate(scripts):
        p = Path("/tmp") / f"rp_v0548_idx_{i}.js"
        p.write_text(body, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"内联脚本语法错误: {r.stderr[:400]}"
