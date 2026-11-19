# -*- coding: utf-8 -*-
"""上传接口畸形文件边界（健壮性第三十轮）。

覆盖：
- 截断图片（有效 JPEG 头 + 数据被截断）必须硬拒绝
- 大写扩展名 .JPG 应正常接受（扩展名统一小写化）
- 伪装后缀：内容是合法图片但后缀 .txt → 拒绝
- 像素炸弹（超大尺寸图片头）不得落库：桌面端与移动端（即使 force_quality）
  都必须拒绝——它后续必然无法解码批改，留着只会污染提交记录
"""
import io
import pathlib
import os
import struct
import tempfile

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import settings


@pytest.fixture()
def client(tmp_db, monkeypatch):
    import backend.database as dbmod
    from backend.database import get_db

    updir = os.path.join(tempfile.mkdtemp(prefix="rp_mal_"), "uploads")
    monkeypatch.setattr(settings, "UPLOAD_DIR", pathlib.Path(updir))
    os.makedirs(updir, exist_ok=True)

    def override_get_db():
        s = dbmod.SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"], r.text
    yield c
    app.dependency_overrides.pop(get_db, None)


def _png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), (245, 245, 245)).save(buf, format="PNG")
    return buf.getvalue()


def _truncated_jpeg_bytes():
    """有效 JPEG 头 + 截断的数据流。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), (245, 245, 245)).save(buf, format="JPEG")
    data = buf.getvalue()
    return data[: len(data) // 3]  # 保留头，截掉大部分数据


def _bomb_png_header_bytes():
    """手工构造 16000x16000（2.56 亿像素，超过 2 亿硬上限）的 PNG 头。"""
    def chunk(tag, payload):
        c = tag + payload
        return struct.pack(">I", len(payload)) + c + struct.pack(">I", zlib_crc(c))

    import zlib
    def zlib_crc(c):
        return zlib.crc32(c) & 0xFFFFFFFF

    ihdr = struct.pack(">IIBBBBB", 16000, 16000, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", b"\x78\x9c" + b"\x00" * 8) + chunk(b"IEND", b""))


@pytest.fixture()
def seeded(client):
    import backend.database as dbmod
    s = dbmod.SessionLocal()
    from backend.database import Assignment, Class, Student
    cls = Class(name="畸形文件测试班级", teacher_id=1, stage_id=1, subject_id=1)
    s.add(cls); s.commit()
    asg = Assignment(class_id=cls.id, title="畸形文件测试作业", subject_id=1)
    s.add(asg); s.commit()
    stu = Student(name="畸形测试学生", class_id=cls.id)
    s.add(stu); s.commit()
    aid, sid = asg.id, stu.id
    s.close()
    return aid, sid


def test_upload_accepts_uppercase_extension(client, seeded):
    aid, sid = seeded
    r = client.post(
        "/api/upload/upload",
        files={"file": ("photo.JPG", _png_bytes(), "image/jpeg")},
        data={"assignment_id": aid, "student_id": sid},
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def test_upload_rejects_truncated_jpeg(client, seeded):
    aid, sid = seeded
    r = client.post(
        "/api/upload/upload",
        files={"file": ("trunc.jpg", _truncated_jpeg_bytes(), "image/jpeg")},
        data={"assignment_id": aid, "student_id": sid},
    )
    assert r.status_code == 400
    assert "损坏" in r.json()["detail"]


def test_upload_rejects_mismatched_extension(client, seeded):
    aid, sid = seeded
    r = client.post(
        "/api/upload/upload",
        files={"file": ("notes.txt", _png_bytes(), "text/plain")},
        data={"assignment_id": aid, "student_id": sid},
    )
    assert r.status_code == 400


def test_upload_rejects_pixel_bomb_and_leaves_no_file(client, seeded):
    aid, sid = seeded
    r = client.post(
        "/api/upload/upload",
        files={"file": ("bomb.png", _bomb_png_header_bytes(), "image/png")},
        data={"assignment_id": aid, "student_id": sid},
    )
    assert r.status_code == 400
    assert "损坏" in r.json()["detail"]
    import backend.database as dbmod
    from backend.database import Submission
    s = dbmod.SessionLocal()
    n = s.query(Submission).count()
    s.close()
    assert n == 0


def test_mobile_rejects_pixel_bomb_even_with_force(client):
    r = client.post(
        "/api/upload/upload-mobile",
        files={"file": ("bomb.png", _bomb_png_header_bytes(), "image/png")},
        data={
            "assignment_name": "炸弹测试",
            "stage": "小学",
            "subject": "数学",
            "teacher_id": 1,
            "force_quality": 1,
        },
    )
    assert r.status_code == 400
    assert "损坏" in r.json()["detail"]
