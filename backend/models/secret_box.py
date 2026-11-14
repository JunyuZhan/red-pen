"""
API Key 静态加密（PRD 4.2：Key 加密存储本地）。

方案：Fernet 对称加密，密钥文件 data/secret.key（首次使用自动生成，
权限 600，Windows 下 chmod 无效但不影响功能）。AppConfig 中的
llm_api_key / llm_fallback_api_key 以 "enc:v1:<fernet-token>" 形式落库；
读取时自动解密。旧明文 Key 在应用启动时自动迁移为密文，无需老师操作。
"""

import os
from pathlib import Path
from typing import List

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

_PREFIX = "enc:v1:"
KEY_CONFIG_ITEMS = ["llm_api_key", "llm_fallback_api_key"]

_fernet: Fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key_path = Path(settings.DATA_DIR) / "secret.key"
        if key_path.exists():
            key = key_path.read_bytes().strip()
            if not key:
                key = Fernet.generate_key()
                key_path.write_bytes(key)
        else:
            key = Fernet.generate_key()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass  # Windows 无 Unix 权限位
        _fernet = Fernet(key)
    return _fernet


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_value(value: str) -> str:
    """明文 → enc:v1:<token>。空串原样返回。"""
    if not value or is_encrypted(value):
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_value(value: str) -> str:
    """enc:v1:<token> → 明文；明文/空串原样返回（兼容迁移前数据）。"""
    if not value or not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # 密钥文件丢失/被换：Key 已不可恢复，返回空串让老师重新填写
        return ""


def migrate_plaintext_keys() -> int:
    """启动时把 AppConfig 中仍是明文的 API Key 加密落库，返回迁移条数。"""
    from ..database import AppConfig, SessionLocal

    db = SessionLocal()
    migrated = 0
    try:
        rows: List[AppConfig] = (
            db.query(AppConfig).filter(AppConfig.key.in_(KEY_CONFIG_ITEMS)).all()
        )
        for row in rows:
            if row.value and not is_encrypted(row.value):
                row.value = encrypt_value(row.value)
                migrated += 1
        if migrated:
            db.commit()
    finally:
        db.close()
    return migrated
