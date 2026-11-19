# -*- coding: utf-8 -*-
"""v0.5.51 工作台加载失败友好降级回归测试。

异常路径走查发现：loadTemplates / loadManage / loadAnalytics / loadAdvice /
openStudentReport 五条加载链 fetch 抛异常时无兜底——页面永远停在"加载中…"
且控制台 Unhandled Rejection。现全部 catch 后渲染"加载失败，请刷新重试"提示。
另补 loadAnalytics 学生等级 ${s.grade} 转义（工作台 v0.5.47 漏网）。
"""
import re
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "frontend" / "templates" / "index.html"


def _func(name: str) -> str:
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"async function " + name + r"\(.*?\{(.*?)\n\}", html, re.S)
    assert m, f"index.html 中找不到 {name}()"
    return m.group(1)


def test_load_functions_have_catch_and_friendly_hint():
    for fn in ("loadTemplates", "loadManage", "loadAnalytics", "loadAdvice"):
        body = _func(fn)
        assert "catch (e)" in body, f"{fn}() 缺少异常兜底"
        assert "加载失败" in body, f"{fn}() 缺少加载失败提示"


def test_open_student_report_guarded():
    body = _func("openStudentReport")
    assert "catch (e)" in body
    assert "加载失败" in body


def test_load_advice_still_renders_on_success():
    body = _func("loadAdvice")
    assert "renderAdvice(d.advice)" in body


def test_student_grade_escaped():
    body = _func("loadAnalytics")
    assert "${esc(s.grade)}" in body
    assert "${s.grade}" not in body
