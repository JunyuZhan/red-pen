"""学情页错误类型占比饼图（PRD 七章）。

条形图展示绝对次数，饼图补占比视图；无错误记录时不渲染饼图。
"""
import math
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "frontend" / "templates"


def _index_html():
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


def test_pie_markup_present():
    html = _index_html()
    assert 'aria-label="错误类型占比饼图"' in html
    # 有错误时才渲染：条件包裹 total_err > 0
    assert "total_err > 0" in html


def test_pie_uses_dasharray_arcs():
    html = _index_html()
    # 饼图由 circle stroke-dasharray 扇区构成（与判定分布环形图同法）
    m = re.search(r"errArcs[\s\S]{0,600}", html)
    assert m and "stroke-dasharray" in m.group(0)
    assert "stroke-dashoffset" in m.group(0)


def test_pie_legend_percent():
    html = _index_html()
    assert "errLegend" in html
    assert "Math.round(v/total_err*100)" in html


def test_pie_math_sanity():
    # 与前端一致的角度逻辑：各扇区 dashoffset 累加，总量不超过周长
    errs = [("计算错误", 3), ("粗心", 1)]
    total = sum(v for _, v in errs)
    r, C = 40, 2 * math.pi * 40
    off = 0.0
    segs = []
    for k, v in errs:
        frac = v / total
        segs.append((frac * C, -off * C))
        off += frac
    assert abs(off - 1.0) < 1e-9
    assert all(0 <= -o <= C for _, o in segs)
