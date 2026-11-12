# -*- coding: utf-8 -*-
"""切题前置图像预处理 测试 — pytest 原生版

覆盖：配置默认关/读写 / 墨迹包围盒 / 输出文件生成 / 低对比度增强 /
裁边去空白 / 密集图不误裁 / 损坏文件与缺失文件回退原图 / EXIF 转正 /
设置 API 读写 / 切题链路接入预处理。
"""
import io
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageStat  # noqa: E402

from backend.models.image_preprocess import (  # noqa: E402
    _ink_bbox, get_preprocess_config, preprocess_image,
    save_preprocess_config)


@pytest.fixture()
def preprocess_env():
    """独立临时库，结束恢复原状（配置存取走 backend.database.SessionLocal）。"""
    import backend.database as dbmod
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_preproc_")
    url = "sqlite:///" + os.path.join(tmp, "test_preprocess.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()
    yield dbmod
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def tmpdir_img(tmp_path):
    """存放测试图片的临时目录。"""
    d = tmp_path / "imgs"
    d.mkdir()
    return str(d)


def _save(directory, name, img, **kw):
    p = os.path.join(directory, name)
    img.save(p, **kw)
    return p


# ---------- 纯图像逻辑（不依赖数据库） ----------

def test_ink_bbox():
    img = Image.new("L", (100, 80), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([20, 10, 60, 70], fill=0)
    bbox = _ink_bbox(img)
    assert bbox is not None
    assert bbox[0] <= 20 and bbox[2] >= 61 and bbox[1] <= 10 and bbox[3] >= 71


def test_ink_bbox_blank():
    assert _ink_bbox(Image.new("L", (50, 50), 255)) is None


def test_output_created(tmpdir_img):
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([50, 50, 350, 250], outline=0, width=5)
    p = _save(tmpdir_img, "a.png", img)
    out = preprocess_image(p, os.path.join(tmpdir_img, "out"))
    assert os.path.exists(out) and out != p
    assert os.path.basename(out).startswith("pre_")


def test_low_contrast_enhanced(tmpdir_img):
    img = Image.new("RGB", (200, 200), (120, 120, 120))
    ImageDraw.Draw(img).rectangle([20, 20, 180, 180], fill=130)
    p = _save(tmpdir_img, "lowc.png", img)
    out = preprocess_image(p, tmpdir_img)
    std_b = ImageStat.Stat(Image.open(p).convert("L")).stddev[0]
    std_a = ImageStat.Stat(Image.open(out).convert("L")).stddev[0]
    assert std_a >= std_b


# ---------- CLAHE 增强（v0.4.17） ----------

def test_clahe_improves_local_contrast():
    """半边阴影的低对比图：CLAHE 后阴影侧手写（暗字）应与纸面拉开。"""
    from backend.models.image_preprocess import _clahe_enhance
    img = Image.new("RGB", (300, 200), (200, 200, 200))
    d = ImageDraw.Draw(img)
    # 阴影侧整体压暗（模拟拍照半边背光），暗字写在阴影侧
    d.rectangle([0, 0, 150, 200], fill=(160, 160, 160))
    d.text((30, 90), "12", fill=(140, 140, 140))
    std_b = ImageStat.Stat(img.convert("L")).stddev[0]
    out = _clahe_enhance(img)
    std_a = ImageStat.Stat(out.convert("L")).stddev[0]
    assert std_a > std_b
    # 尺寸与模式不变
    assert out.size == img.size and out.mode == "RGB"


def test_clahe_fallback_no_cv2(monkeypatch):
    """cv2 导入失败时回退 autocontrast，不抛异常。"""
    import builtins
    from backend.models import image_preprocess as ip
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "cv2":
            raise ImportError("no cv2")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    img = Image.new("RGB", (100, 100), (120, 120, 120))
    out = ip._clahe_enhance(img)
    assert out.size == (100, 100)


def test_crop_blank(tmpdir_img):
    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([400, 400, 600, 600], outline=0, width=5)
    out = preprocess_image(_save(tmpdir_img, "blank.png", img), tmpdir_img)
    w, h = Image.open(out).size
    assert w * h < 1000 * 1000 * 0.9


def test_dense_not_cropped(tmpdir_img):
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([5, 5, 295, 295], outline=0, width=10)
    out = preprocess_image(_save(tmpdir_img, "dense.png", img), tmpdir_img)
    assert Image.open(out).size == (300, 300)


def test_fallback_corrupt_file(tmpdir_img):
    bad = os.path.join(tmpdir_img, "bad.png")
    with open(bad, "wb") as f:
        f.write(b"not-an-image")
    assert preprocess_image(bad, tmpdir_img) == bad


def test_fallback_missing_file(tmpdir_img):
    missing = os.path.join(tmpdir_img, "nope.png")
    assert preprocess_image(missing, tmpdir_img) == missing


def test_exif_rotated(tmpdir_img):
    img = Image.new("RGB", (100, 200), (255, 255, 255))
    ImageDraw.Draw(img).text((10, 80), "hi", fill=0)
    exif = Image.Exif()
    exif[274] = 6
    bio = io.BytesIO()
    img.save(bio, format="JPEG", exif=exif.tobytes())
    p = os.path.join(tmpdir_img, "rot.jpg")
    with open(p, "wb") as f:
        f.write(bio.getvalue())
    out = preprocess_image(p, tmpdir_img)
    assert os.path.exists(out)


def test_split_integration():
    from backend.models.grading_engine import GradingManager
    import inspect
    src = inspect.getsource(GradingManager._grade_with_split)
    assert "preprocess_image" in src and "get_preprocess_config" in src


# ---------- 依赖数据库的配置/API ----------

def test_config_default_off(preprocess_env):
    assert get_preprocess_config()["enabled"] is False


def test_config_roundtrip(preprocess_env):
    save_preprocess_config(True)
    assert get_preprocess_config()["enabled"] is True
    save_preprocess_config(False)
    assert get_preprocess_config()["enabled"] is False


def test_settings_api(preprocess_env):
    from fastapi.testclient import TestClient
    from backend.app import app
    c = TestClient(app)
    # 未带 PIN cookie：GET/POST 均被拒绝（设置接口统一 admin 保护）
    assert c.get("/api/settings/preprocess").status_code in (401, 403)
    assert c.post("/api/settings/preprocess",
                  json={"enabled": True}).status_code in (401, 403)
    c.post("/api/auth/pin-login", json={"pin": "123456"})
    r = c.get("/api/settings/preprocess")
    d = r.json()
    assert r.status_code == 200 and d["success"]
    r3 = c.post("/api/settings/preprocess", json={"enabled": True})
    d3 = r3.json()
    assert r3.status_code == 200 and d3["success"] and d3["enabled"] is True
    save_preprocess_config(False)
