"""上传异常路径健壮性（健壮性第二十轮）：

1. upload-mobile 在建班级/建作业阶段抛错时，file_path 尚未赋值——
   except 里引用未定义变量会二次 NameError，老师只看到无解释 500。
   现预置 None + 守卫清理，500 详情为人话且不泄漏原始异常。
2. 失败时清理本轮误建的作业记录（不留孤儿 Assignment）。
"""
import io
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import settings


@pytest.fixture()
def client(tmp_db, monkeypatch):
    updir = os.path.join(tempfile.mkdtemp(prefix="rp_up2_"), "uploads")
    monkeypatch.setattr(settings, "UPLOAD_DIR", Path(updir))
    os.makedirs(updir, exist_ok=True)
    from backend.database import get_db
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"], r.text
    _seed_default_student()
    yield c


def _png_bytes():
    img = Image.new("RGB", (600, 400), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _post_mobile(client, force_quality=0):
    return client.post(
        "/api/upload/upload-mobile",
        files={"file": ("good.png", _png_bytes(), "image/png")},
        data={
            "assignment_name": "异常路径测试作业",
            "stage": "小学",
            "subject": "数学",
            "teacher_id": 1,
            "force_quality": force_quality,
        },
    )


def _seed_default_student():
    """upload-mobile 要求默认学生 1 存在（无外键，防孤儿提交）。"""
    from backend.database import SessionLocal, Student
    db = SessionLocal()
    if not db.query(Student).filter(Student.id == 1).first():
        db.add(Student(name="默认学生", class_id=1))
        db.commit()
    db.close()


def test_mobile_upload_failure_before_file_path(monkeypatch, client, tmp_db):
    """保存路径计算先抛错：不得 NameError，500 详情人话，无孤儿作业。"""
    from backend.routes import upload as up

    _seed_default_student()

    def boom(rel):
        raise OSError("disk full simulated")

    monkeypatch.setattr(up, "upload_abs_path", boom)
    r = _post_mobile(client)
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "上传失败" in detail and "file_path" not in detail and "OSError" not in detail

    from backend.database import SessionLocal, Assignment
    db = SessionLocal()
    try:
        orphans = db.query(Assignment).filter(Assignment.title == "异常路径测试作业").all()
        assert orphans == [], f"失败后留下孤儿作业记录: {orphans}"
    finally:
        db.close()


def test_mobile_upload_failure_after_file_saved(monkeypatch, client, tmp_db):
    """文件已落盘但建提交记录阶段抛错：文件与作业记录都要清掉。"""
    from backend.routes import upload as up
    from backend.database import SessionLocal, Assignment

    real_check = up.check_image

    def boom_then(data):
        # 先让正常的质量检测跑一遍拿到 flags，再在提交前抛错
        return real_check(data)

    monkeypatch.setattr(up, "check_image", boom_then)

    real_rel = up.upload_rel_path
    calls = {"n": 0}

    def rel_then_boom(*a, **kw):
        calls["n"] += 1
        return real_rel(*a, **kw)

    monkeypatch.setattr(up, "upload_rel_path", rel_then_boom)

    # 让 Submission 构造后 commit 抛错：monkeypatch db.add? 更简单——patch settings 使 mkdir 成功但 open 抛错
    import builtins
    real_open = builtins.open

    def open_fail(path, mode="r", *a, **kw):
        if str(path).endswith(".png") and "wb" in str(mode):
            raise OSError("write failed simulated")
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", open_fail)

    r = _post_mobile(client)
    assert r.status_code == 500
    assert "上传失败" in r.json()["detail"]

    db = SessionLocal()
    try:
        orphans = db.query(Assignment).filter(Assignment.title == "异常路径测试作业").all()
        assert orphans == [], f"失败后留下孤儿作业记录: {orphans}"
    finally:
        db.close()


def test_mobile_upload_success_still_works(client, tmp_db):
    """正常上传不受影响（回归保护）。"""
    r = _post_mobile(client, force_quality=1)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
