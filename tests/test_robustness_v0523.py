# -*- coding: utf-8 -*-
"""第六轮健壮性（v0.5.23）：模板/静态资源异常路径 + 全局兜底异常处理。

- 页面模板渲染失败 → 友好 500 HTML，不暴露堆栈
- 设置页模板文件缺失 → 友好 500 HTML
- API 路由未捕获异常 → JSON {"success": false, ...}（HTTPException 不受影响）
"""
import pytest
from fastapi.testclient import TestClient


def test_unhandled_api_exception_returns_json(tmp_db, monkeypatch):
    """API 路由抛未捕获异常 → 500 JSON，success=False，不再是裸文本/堆栈。"""
    from backend.app import app
    from backend.routes import dashboard

    def boom(db):
        raise RuntimeError("boom")
    monkeypatch.setattr(dashboard, "get_thresholds", boom)

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/dashboard/summary")
    assert r.status_code == 500
    data = r.json()
    assert data["success"] is False and "服务器内部错误" in data["error"]


def test_httpexception_not_swallowed(tmp_db):
    """HTTPException 仍按原状态码/detail 返回，不被兜底处理器吞掉。"""
    from backend.app import app

    c = TestClient(app)
    r = c.get("/api/grading/export/999999999")
    assert r.status_code == 404
    assert "提交记录不存在" in r.json()["detail"]


def test_unhandled_page_exception_returns_html(tmp_db, monkeypatch):
    """页面路由抛未捕获异常 → 500 友好 HTML，不含异常类名。"""
    from backend.app import app

    import backend.app as appmod

    def boom(*a, **k):
        raise RuntimeError("page boom")
    monkeypatch.setattr(appmod, "_render", boom)

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/")
    assert r.status_code == 500
    assert "RuntimeError" not in r.text and "服务器内部错误" in r.text


def test_render_template_missing_returns_friendly_page(tmp_db, monkeypatch):
    """模板缺失 → _render 返回友好 500 页，不抛异常。"""
    import backend.app as appmod

    def boom(*a, **k):
        raise Exception("TemplateNotFound: index.html")
    monkeypatch.setattr(appmod.templates, "TemplateResponse", boom)

    c = TestClient(appmod.app)
    r = c.get("/")
    assert r.status_code == 500
    assert "页面资源加载失败" in r.text


def test_settings_page_template_missing(tmp_db, monkeypatch):
    """设置页模板文件缺失 → 友好 500 HTML。"""
    import backend.app as appmod
    from pathlib import Path as _P

    real_exists = _P.exists

    def fake_exists(self):
        if self.name == "settings.html":
            return False
        return real_exists(self)
    monkeypatch.setattr(_P, "exists", fake_exists)

    c = TestClient(appmod.app)
    r = c.get("/settings")
    assert r.status_code == 500
    assert "设置页面资源缺失" in r.text
