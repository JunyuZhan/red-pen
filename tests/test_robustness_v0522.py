"""
第五轮健壮性加固（v0.5.22）：
- 超长输入限长（班级/学生/学段/学科/作业标题）
- 解压炸弹防护（像素总量上限）
- 并发上传压测（20 路线程上传，隔离无串扰）
"""
import io
import os
import pathlib
import tempfile
import threading

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import settings
from backend.database import get_db, Submission
from backend.models.quality_checker import MAX_PIXELS, check_image


@pytest.fixture()
def client(tmp_db, monkeypatch):
    updir = os.path.join(tempfile.mkdtemp(prefix="rp_up22_"), "uploads")
    monkeypatch.setattr(settings, "UPLOAD_DIR", pathlib.Path(updir))
    os.makedirs(updir, exist_ok=True)
    c = TestClient(app)
    r = c.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
    assert r.status_code == 200 and r.json()["success"], r.text
    return c


def _jpeg(w=800, h=600):
    # 随机噪声图：亮度居中、对比足够，避开模糊/过暗/过曝/过小的质量门禁
    import random
    rng = random.Random(42)
    img = Image.new("RGB", (w, h))
    img.putdata([(rng.randint(60, 200),) * 3 for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mk_class(client, name="并发班"):
    r = client.post("/api/manage/classes",
                    json={"name": name, "stage_id": 1, "subject_id": 1})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- 超长输入限长 ----------

def test_oversize_class_name_rejected(client):
    r = client.post("/api/manage/classes",
                    json={"name": "超" * 51, "stage_id": 1, "subject_id": 1})
    assert r.status_code == 400
    assert "过长" in r.json()["detail"]


def test_oversize_student_name_rejected_boundary_ok(client):
    cls = _mk_class(client, "边界班")
    r = client.post("/api/manage/students", json={"name": "名" * 51, "class_id": cls})
    assert r.status_code == 400
    assert "过长" in r.json()["detail"]
    # 合法边界：50 字应通过
    r2 = client.post("/api/manage/students", json={"name": "名" * 50, "class_id": cls})
    assert r2.status_code == 200


def test_student_update_length_rejected(client):
    cls = _mk_class(client, "改长班")
    st = client.post("/api/manage/students",
                     json={"name": "小明", "class_id": cls}).json()["id"]
    r = client.put(f"/api/manage/students/{st}", json={"name": "名" * 51})
    assert r.status_code == 400


def test_setup_oversize_class_name_rejected(client):
    r = client.post("/api/setup/complete",
                    json={"stage": "小学", "subject": "数学", "class_name": "班" * 51})
    assert r.status_code == 400


def test_mobile_upload_oversize_title_rejected(client):
    r = client.post("/api/upload/upload-mobile",
                    files={"file": ("a.jpg", _jpeg(), "image/jpeg")},
                    data={"assignment_name": "题" * 101, "stage": "小学",
                          "subject": "数学", "teacher_id": 1})
    assert r.status_code == 400
    assert "过长" in r.json()["detail"]


def test_mobile_upload_blank_fields_rejected(client):
    r = client.post("/api/upload/upload-mobile",
                    files={"file": ("a.jpg", _jpeg(), "image/jpeg")},
                    data={"assignment_name": "  ", "stage": "小学",
                          "subject": "数学", "teacher_id": 1})
    assert r.status_code == 400


# ---------- 解压炸弹防护 ----------

def test_decompression_bomb_flagged():
    # 小 PNG 声明超大画布（稀疏压缩），解码后像素总量超上限
    img = Image.new("RGB", (11000, 11000), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    assert len(data) < 1_000_000  # 文件本身很小
    flags = check_image(data)
    assert flags and any("过大" in f for f in flags)


def test_normal_image_not_bomb_flagged():
    assert check_image(_jpeg()) == []


# ---------- 并发上传压测 ----------

def test_concurrent_uploads_isolated(client, tmp_db):
    """20 路线程并发上传不同学生：全部成功，提交数与文件一一对应。"""
    cls = _mk_class(client, "并发压测班")
    students = [client.post("/api/manage/students",
                            json={"name": f"生{i:02d}", "class_id": cls}).json()["id"]
                for i in range(20)]
    results = [None] * 20

    def work(i):
        files = {"file": (f"h{i}.jpg", _jpeg(), "image/jpeg")}
        r = client.post("/api/upload/upload",
                        files=files,
                        data={"assignment_id": 1, "student_id": students[i]})
        results[i] = (r.status_code, r.json().get("submission_id"))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(code == 200 for code, _ in results), results
    assert all(sid is not None for _, sid in results)
    assert len(set(sid for _, sid in results)) == 20  # 提交 id 全部不同
    s = tmp_db.SessionLocal()
    try:
        n = s.query(Submission).filter(Submission.student_id.in_(students)).count()
        assert n == 20
        # 每位学生恰好 1 份提交，无串扰
        from sqlalchemy import func
        counts = dict(s.query(Submission.student_id, func.count(Submission.id))
                      .filter(Submission.student_id.in_(students))
                      .group_by(Submission.student_id).all())
        assert all(counts.get(st) == 1 for st in students), counts
    finally:
        s.close()
