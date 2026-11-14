# -*- coding: utf-8 -*-
"""API Key 加密存储（PRD 4.2）：加密落库 / 读取解密 / 旧明文自动迁移 / 密钥丢失回退。"""
import os

import pytest

from backend.config import settings
import backend.models.secret_box as sb


@pytest.fixture()
def isolated_keys(monkeypatch, tmp_path):
    """独立数据目录 + 重置 secret_box 的密钥缓存，保证测试互不影响。"""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    old = sb._fernet
    sb._fernet = None
    yield tmp_path
    sb._fernet = old


def test_encrypt_decrypt_roundtrip(isolated_keys):
    enc = sb.encrypt_value("sk-test123")
    assert enc != "sk-test123"
    assert sb.is_encrypted(enc)
    assert sb.decrypt_value(enc) == "sk-test123"


def test_key_file_created(isolated_keys):
    sb.encrypt_value("abc")
    assert (isolated_keys / "secret.key").exists()


def test_plaintext_passthrough(isolated_keys):
    assert sb.decrypt_value("") == ""
    assert sb.decrypt_value("plain") == "plain"  # 未迁移明文仍可读
    assert sb.encrypt_value("") == ""


def test_save_and_get_llm_config(tmp_db, isolated_keys):
    from backend.models.grading_engine import get_llm_config, save_llm_config
    from backend.database import SessionLocal, AppConfig

    save_llm_config("custom", "https://api.example.com/v1", "test-model", api_key="sk-live-999")
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "llm_api_key").first()
    finally:
        db.close()
    # 库里必须是密文，不含明文片段
    assert row.value.startswith("enc:v1:")
    assert "sk-live-999" not in row.value
    # 读取时透明解密
    assert get_llm_config()["api_key"] == "sk-live-999"


def test_save_and_get_fallback_config(tmp_db, isolated_keys):
    from backend.models.grading_engine import get_fallback_config, save_fallback_config

    save_fallback_config("qwen", "", "qwen-vl-plus", api_key="sk-fb-1")
    assert get_fallback_config()["api_key"] == "sk-fb-1"
    # 不更新 Key 时保留旧值
    save_fallback_config("qwen", "", "qwen-vl-plus", api_key=None, update_key=False)
    assert get_fallback_config()["api_key"] == "sk-fb-1"


def test_migrate_plaintext_keys(tmp_db, isolated_keys):
    from backend.models.secret_box import migrate_plaintext_keys
    from backend.database import SessionLocal, AppConfig

    db = SessionLocal()
    try:
        db.add(AppConfig(key="llm_api_key", value="sk-old-plain"))
        db.add(AppConfig(key="llm_fallback_api_key", value="sk-old-fb"))
        db.commit()
    finally:
        db.close()

    assert migrate_plaintext_keys() == 2
    # 第二次运行无明文可迁
    assert migrate_plaintext_keys() == 0
    db = SessionLocal()
    try:
        vals = {r.key: r.value for r in db.query(AppConfig).filter(
            AppConfig.key.in_(["llm_api_key", "llm_fallback_api_key"])).all()}
    finally:
        db.close()
    assert all(sb.is_encrypted(v) for v in vals.values())
    assert "sk-old-plain" not in vals["llm_api_key"]

    # 读取端拿到的仍是解密后的 Key
    from backend.models.grading_engine import get_llm_config
    assert get_llm_config()["api_key"] == "sk-old-plain"


def test_decrypt_with_lost_keyfile_returns_empty(isolated_keys):
    enc = sb.encrypt_value("sk-x")
    # 模拟密钥文件丢失/被换：重置缓存并删除文件
    sb._fernet = None
    (isolated_keys / "secret.key").unlink()
    assert sb.decrypt_value(enc) == ""

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
