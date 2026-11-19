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


def _valid_ids(client):
    from backend.database import SessionLocal
    db = SessionLocal()
    aid, sid = _seed_assignment_student(db)
    db.close()
    return aid, sid


def _png_bytes():
    # 灰色纸面（纯白会触发过曝质量门禁 422）
    img = Image.new("RGB", (600, 400), (224, 224, 224))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _seed_assignment_student(db):
    from backend.database import Assignment, Class, Student
    cls = Class(name="上传测试班级", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.commit()
    asg = Assignment(class_id=cls.id, title="上传测试作业", subject_id=1)
    db.add(asg); db.commit()
    stu = Student(name="测试学生", class_id=cls.id)
    db.add(stu); db.commit()
    return asg.id, stu.id


def test_upload_rejects_corrupt_image(client):
    aid, sid = _valid_ids(client)
    r = client.post(
        "/api/upload/upload",
        files={"file": ("broken.jpg", b"\x00\x01not-an-image", "image/jpeg")},
        data={"assignment_id": aid, "student_id": sid},
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
    aid, sid = _valid_ids(client)
    r = client.post(
        "/api/upload/upload",
        files={"file": ("empty.jpg", b"", "image/jpeg")},
        data={"assignment_id": aid, "student_id": sid},
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
    aid, sid = _valid_ids(client)
    r = client.post(
        "/api/upload/upload",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={"assignment_id": aid, "student_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_delete_nonexistent_returns_404_not_500(client):
    r = client.delete("/api/file/definitely-missing-id")
    assert r.status_code == 404


def test_upload_mobile_rejects_nonexistent_student(client):
    """移动端上传传不存在的学生 ID：404，不留孤儿提交（submission 无外键约束）。"""
    from backend.database import SessionLocal, Submission
    r = client.post(
        "/api/upload/upload-mobile",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={
            "assignment_name": "孤儿学生测试",
            "stage": "小学", "subject": "数学", "teacher_id": 1,
            "student_id": 99999,
        },
    )
    assert r.status_code == 404, r.text
    db = SessionLocal()
    try:
        assert db.query(Submission).filter(Submission.student_id == 99999).count() == 0
    finally:
        db.close()


def test_upload_mobile_requires_default_student_when_unselected(client):
    """未选学生且默认学生 1 不存在：400 明确提示，不留孤儿提交。"""
    from backend.database import SessionLocal, Submission
    r = client.post(
        "/api/upload/upload-mobile",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={
            "assignment_name": "无学生测试",
            "stage": "小学", "subject": "数学", "teacher_id": 1,
        },
    )
    assert r.status_code == 400, r.text
    assert "学生" in r.json()["detail"]
    db = SessionLocal()
    try:
        assert db.query(Submission).count() == 0
    finally:
        db.close()


def test_upload_mobile_accepts_existing_student(client):
    """传真实存在的学生 ID：正常上传成功。"""
    from backend.database import Class, SessionLocal, Student
    db = SessionLocal()
    cls = Class(name="移动端学生班级", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.commit()
    stu = Student(name="真实学生", class_id=cls.id)
    db.add(stu); db.commit()
    sid = stu.id
    db.close()
    r = client.post(
        "/api/upload/upload-mobile",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={
            "assignment_name": "存在学生测试",
            "stage": "小学", "subject": "数学", "teacher_id": 1,
            "student_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
