# -*- coding: utf-8 -*-
"""v0.5.61 前端降级冒烟回归测试（健壮性第三十一轮，上轮计划①）。

覆盖两个降级场景：
A) 后端只读 API 在"空库 / 坏参数"下必须 200 或 4xx，绝不 500——
   前端各面板的 catch 降级提示只在 fetch 抛错或非 200 时展示，
   若后端 500，前端虽有提示但用户拿不到任何数据且日志难看。
B) 三个页面模板必须有 <noscript> 降级横幅——浏览器禁用 JS 或
   脚本加载失败时给出人话指引，而不是白屏。
"""
import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "templates"

# 空库下应当 200 且可解析 JSON 的只读接口
READ_OK = [
    "/api/dashboard/summary",
    "/api/grading/failed",
    "/api/grading/queue-status",
    "/api/grading/answer-templates",
    "/api/grading/concurrency",
    "/api/grading/timeout",
    "/api/manage/classes",
    "/api/manage/students",
    "/api/mobile/list",
    "/api/mobile/classes-students",
    "/api/mobile/queue-counts",
    "/api/analytics/analytics",
    "/api/analytics/analytics/frequent-wrong",
    "/api/analytics/analytics/trends",
    "/api/analytics/teaching-advice",
    "/api/settings/llm",
    "/api/settings/fallback",
    "/api/settings/budget",
    "/api/settings/cost",
    "/api/settings/grade",
    "/api/settings/split",
    "/api/settings/review",
    "/api/settings/grading-cache",
    "/api/settings/max-tokens",
    "/api/settings/ocr-enhanced",
    "/api/settings/preprocess",
    "/api/settings/privacy-mask",
    "/api/setup/status",
    "/api/auth/pin-status",
    "/api/logs/recent",
]

# 坏参数（不存在的 id / 非法格式）下允许 4xx 或安全 200，禁止 5xx
BAD_PARAMS = [
    "/api/grading/result/99999",
    "/api/grading/export/99999",
    "/api/grading/export-class/99999",
    "/api/grading/export-question-matrix/99999",
    "/api/grading/export-student/99999",
    "/api/analytics/student-report/99999",
    "/api/mobile/status/99999",
    "/api/upload/file/99999",
    "/api/grading/result/not-a-number",
    "/api/mobile/status/not-a-number",
]


@pytest.fixture()
def client(auth_client):
    return auth_client


@pytest.mark.parametrize("url", READ_OK)
def test_read_api_empty_db_ok(client, url):
    """空库下只读接口必须 200 且返回合法 JSON。"""
    r = client.get(url)
    assert r.status_code == 200, f"{url} 空库返回 {r.status_code}: {r.text[:200]}"
    r.json()  # 解析失败会抛异常


@pytest.mark.parametrize("url", BAD_PARAMS)
def test_bad_param_no_5xx(client, url):
    """坏参数绝不 500（4xx 或安全 200 均可）。"""
    r = client.get(url)
    assert r.status_code < 500, f"{url} 返回 {r.status_code}: {r.text[:300]}"


@pytest.mark.parametrize("name", ["index.html", "mobile.html", "settings.html"])
def test_noscript_banner_present(name):
    """禁用 JS 时三端页面必须显示人话降级横幅。"""
    html = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
    m = re.search(r"<noscript>(.*?)</noscript>", html, re.S)
    assert m, f"{name} 缺少 <noscript> 降级横幅"
    assert "JavaScript" in m.group(1), "横幅必须说明是 JS 被禁用"
    assert "请检查浏览器设置" in m.group(1), "横幅必须给可操作的指引"


@pytest.mark.parametrize("name", ["index.html", "mobile.html", "settings.html"])
def test_pages_still_render_and_scripts_ok(name):
    """加横幅不破坏页面：路由可渲染、内联脚本语法仍过 node --check。"""
    import subprocess

    html_path = TEMPLATES_DIR / name
    html = html_path.read_text(encoding="utf-8")
    assert "</noscript>" in html
    for i, block in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        p = Path(f"/tmp/_jschk_deg_{name}_{i}.js")
        p.write_text(block, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name} 第{i}段内联脚本语法错误: {r.stderr}"
        p.unlink(missing_ok=True)


@pytest.mark.parametrize("path", ["/", "/mobile", "/settings"])
def test_pages_render_200(client, path):
    """三端页面路由在空库下正常渲染且包含横幅。"""
    r = client.get(path)
    assert r.status_code == 200, f"{path} 返回 {r.status_code}"
    assert "<noscript>" in r.text
