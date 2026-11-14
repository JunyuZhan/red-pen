"""上传路由健壮性：损坏图片硬拒绝、文件名校验、删除404不吞成500。"""
import io
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import settings


@pytest.fixture()
def client(tmp_db, monkeypatch):
    import os, tempfile
    updir = os.path.join(tempfile.mkdtemp(prefix="rp_up_"), "uploads")
    monkeypatch.setattr(settings, "UPLOAD_DIR", __import__("pathlib").Path(updir))
    os.makedirs(updir, exist_ok=True)
    from backend.database import get_db, SessionLocal as _sl  # noqa: F401
    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"], r.text
    yield c
    app.dependency_overrides.pop(get_db, None) if hasattr(app, "dependency_overrides") else None


def _png_bytes():
    img = Image.new("RGB", (600, 400), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_upload_rejects_corrupt_image(client):
    r = client.post(
        "/api/upload/upload",
        files={"file": ("broken.jpg", b"\x00\x01not-an-image", "image/jpeg")},
        data={"assignment_id": 1, "student_id": 1},
    )
    assert r.status_code == 400
    assert "损坏" in r.json()["detail"]


def test_upload_mobile_rejects_corrupt_even_with_force(client):
    r = client.post(
        "/api/upload/upload-mobile",
        files={"file": ("broken.jpg", b"\x00\x01not-an-image", "image/jpeg")},
        data={
            "assignment_name": "测试",
            "stage": "小学",
            "subject": "数学",
            "teacher_id": 1,
            "force_quality": 1,
        },
    )
    assert r.status_code == 400
    assert "损坏" in r.json()["detail"]


def test_upload_rejects_empty_file(client):
    r = client.post(
        "/api/upload/upload",
        files={"file": ("empty.jpg", b"", "image/jpeg")},
        data={"assignment_id": 1, "student_id": 1},
    )
    assert r.status_code == 400


def test_upload_rejects_missing_extension(client):
    r = client.post(
        "/api/upload/upload",
        files={"file": ("noext", _png_bytes(), "application/octet-stream")},
        data={"assignment_id": 1, "student_id": 1},
    )
    assert r.status_code == 400


def test_upload_rejects_bad_extension(client):
    r = client.post(
        "/api/upload/upload",
        files={"file": ("evil.exe", _png_bytes(), "application/x-exe")},
        data={"assignment_id": 1, "student_id": 1},
    )
    assert r.status_code == 400


def test_upload_accepts_valid_image(client):
    r = client.post(
        "/api/upload/upload",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={"assignment_id": 1, "student_id": 1},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_delete_nonexistent_returns_404_not_500(client):
    r = client.delete("/api/file/definitely-missing-id")
    assert r.status_code == 404
