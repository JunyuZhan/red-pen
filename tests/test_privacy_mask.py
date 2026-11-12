# -*- coding: utf-8 -*-
"""敏感信息自动遮盖（PRD 4.2 / 待办 aa）"""
import os

from PIL import Image

from backend.models.privacy import (
    apply_privacy_mask,
    get_mask_config,
    mask_image,
    save_mask_config,
)


def test_default_config_enabled(tmp_db):
    cfg = get_mask_config()
    assert cfg["enabled"] is True
    assert cfg["percent"] == 12


def test_save_and_get_roundtrip(tmp_db):
    save_mask_config(False, 20)
    cfg = get_mask_config()
    assert cfg["enabled"] is False
    assert cfg["percent"] == 20


def test_invalid_stored_percent_falls_back(tmp_db):
    save_mask_config(True, 15)
    from backend.database import AppConfig, SessionLocal
    db = SessionLocal()
    row = db.query(AppConfig).filter(AppConfig.key == "privacy_mask_percent").first()
    row.value = "abc"
    db.commit()
    db.close()
    assert get_mask_config()["percent"] == 12


def test_save_rejects_out_of_range(tmp_db):
    import pytest
    with pytest.raises(ValueError):
        save_mask_config(True, 90)
    with pytest.raises(ValueError):
        save_mask_config(True, 1)


def _make_paper(path, w=800, h=1000):
    """顶部白底黑字'姓名'栏 + 下方题目区"""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for y in range(20, 80):
        for x in range(50, 200):
            img.putpixel((x, y), (0, 0, 0))
    for y in range(500, 560):
        for x in range(100, 500):
            img.putpixel((x, y), (0, 0, 0))
    img.save(path, "JPEG")


def test_mask_image_covers_top_band_only(tmp_db, tmp_path):
    p = str(tmp_path / "paper.jpg")
    _make_paper(p)
    out = mask_image(p, 12)
    assert out != p and os.path.exists(out)
    masked = Image.open(out)
    assert masked.size == (800, 1000)
    # 顶部 12%（120px）应为纯黑
    assert masked.getpixel((100, 50)) == (0, 0, 0)
    assert masked.getpixel((400, 100)) == (0, 0, 0)
    # 120px 以下保持原内容（题目区墨迹仍在）
    assert masked.getpixel((300, 530))[0] < 128
    # 原图未被修改
    orig = Image.open(p)
    assert orig.getpixel((100, 50)) == (0, 0, 0)


def test_apply_privacy_mask_disabled_returns_original(tmp_db, tmp_path):
    save_mask_config(False, 12)
    p = str(tmp_path / "paper.jpg")
    _make_paper(p)
    assert apply_privacy_mask(p) == p


def test_apply_privacy_mask_corrupt_image_returns_original(tmp_db, tmp_path):
    p = str(tmp_path / "bad.jpg")
    with open(p, "wb") as f:
        f.write(b"not an image")
    assert apply_privacy_mask(p) == p


def test_privacy_mask_settings_api(auth_client):
    r = auth_client.get("/api/settings/privacy-mask")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] and d["enabled"] is True and d["percent"] == 12
    r = auth_client.post("/api/settings/privacy-mask", json={"enabled": False, "percent": 25})
    assert r.status_code == 200 and r.json()["percent"] == 25
    assert auth_client.get("/api/settings/privacy-mask").json()["enabled"] is False
    r = auth_client.post("/api/settings/privacy-mask", json={"enabled": True, "percent": 90})
    assert r.status_code == 400
