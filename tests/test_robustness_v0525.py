# -*- coding: utf-8 -*-
"""第八轮健壮性（v0.5.25）：上传中断路径 + 设置页超时 UI。

- 上传分块读取、超限即停（超大文件不再整体吞进内存，尽早 400 人话提示）
- 空文件（上传被中断）→ 400
- 移动端质量不合格退回时，本轮误建的空作业记录一并清理（不留孤儿作业）
- /api/upload/file/{file_id} 非法标识 400（防路径穿越）
- 设置页含批改超时卡片与 loadTimeout
"""
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_robustness_v0524 import rx_env  # noqa: E402,F401  复用第七轮的测试环境夹具


def test_read_capped_stops_early_on_oversize(monkeypatch):
    """超过上限时立即中止读取，不再把整个文件吞进内存。"""
    from backend.config import settings
    from backend.routes.upload import _read_capped
    from fastapi import HTTPException

    class FakeUp:
        def __init__(self, data, name="a.jpg"):
            self.file = io.BytesIO(data)
            self.filename = name

    monkeypatch.setattr(settings, "MAX_FILE_SIZE", 100)
    big = b"x" * 5000
    with pytest.raises(HTTPException) as e:
        _read_capped(FakeUp(big))
    assert "过大" in e.value.detail

    ok = _read_capped(FakeUp(b"y" * 50))
    assert ok == b"y" * 50


def test_read_capped_empty_raises(monkeypatch):
    """空内容（上传被中断）→ 400 人话提示。"""
    from backend.routes.upload import _read_capped
    from fastapi import HTTPException

    class FakeUp:
        file = io.BytesIO(b"")
        filename = "a.jpg"

    with pytest.raises(HTTPException) as e:
        _read_capped(FakeUp())
    assert "中断" in e.value.detail


def test_upload_endpoint_rejects_empty_file(rx_env):
    """上传端点收到空文件 → 400，不落库不留文件。"""
    client = rx_env["client"]
    r = client.post("/api/upload/upload",
                    files={"file": ("a.jpg", b"", "image/jpeg")},
                    data={"assignment_id": "1", "student_id": "1"})
    assert r.status_code == 400
    assert "为空" in r.json()["detail"]


def test_mobile_quality_reject_cleans_orphan_assignment(rx_env, monkeypatch):
    """移动端质量退回时，本轮误建的作业记录被删除，不留空作业。"""
    import backend.routes.upload as up
    from backend.database import Assignment

    monkeypatch.setattr(up, "check_image", lambda data: ["照片模糊"])

    client = rx_env["client"]
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, format="JPEG")
    r = client.post("/api/upload/upload-mobile",
                    files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")},
                    data={"assignment_name": "孤儿测试", "stage": "小学",
                          "subject": "数学", "teacher_id": "1"})
    assert r.status_code == 422

    db = rx_env["dbmod"].SessionLocal()
    n = db.query(Assignment).filter(Assignment.title == "孤儿测试").count()
    db.close()
    assert n == 0


def test_get_file_rejects_illegal_id(rx_env):
    """/api/upload/file/{file_id} 非法标识 → 400（防路径穿越）。"""
    client = rx_env["client"]
    r = client.get("/api/upload/file/not-hex$")
    assert r.status_code == 400
    assert "非法" in r.json()["detail"]
    r2 = client.get("/api/upload/file/" + "a" * 32)  # 合法 hex 但不存在 → 404
    assert r2.status_code == 404


def test_settings_page_has_timeout_card():
    """设置页包含批改超时卡片与 loadTimeout 逻辑。"""
    html = open(os.path.join(ROOT, "frontend", "templates", "settings.html"),
                encoding="utf-8").read()
    assert "批改超时" in html
    assert "loadTimeout" in html
    assert "/api/grading/timeout" in html
