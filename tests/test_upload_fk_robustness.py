"""上传外键引用健壮性（第十四轮）：不存在 assignment_id/student_id 不得静默创建孤儿提交。"""
import io
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import settings


@pytest.fixture()
def client(tmp_db, monkeypatch):
    import os, tempfile
    updir = os.path.join(tempfile.mkdtemp(prefix="rp_up14_"), "uploads")
    monkeypatch.setattr(settings, "UPLOAD_DIR", __import__("pathlib").Path(updir))
    os.makedirs(updir, exist_ok=True)
    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"], r.text
    yield c


def _png_bytes():
    img = Image.new("RGB", (600, 400), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_upload_rejects_missing_assignment(client):
    r = client.post(
        "/api/upload/upload",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={"assignment_id": 999999, "student_id": 1},
    )
    assert r.status_code == 404, r.text
    assert "作业" in r.json()["detail"]


def test_upload_rejects_missing_student(client):
    from backend.database import SessionLocal, Assignment, Class
    db = SessionLocal()
    cls = Class(name="验证班级", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.commit()
    asg = Assignment(class_id=cls.id, title="验证作业", subject_id=1)
    db.add(asg); db.commit()
    aid = asg.id
    db.close()

    r = client.post(
        "/api/upload/upload",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={"assignment_id": aid, "student_id": 999999},
    )
    assert r.status_code == 404, r.text
    assert "学生" in r.json()["detail"]
