"""敏感信息自动遮盖（PRD 4.2 / 待办 aa，P1）

批改送模型前，把作业照片顶部区域（姓名栏/学号栏通常所在位置）
涂黑遮盖，避免学生姓名等个人信息随图片发给第三方模型服务商。

- 开关 + 遮盖比例可配，存 AppConfig（privacy_mask_enabled /
  privacy_mask_percent），默认开启、默认 12%
- 只遮盖"送模型的副本"（data/masked/ 下另存 JPEG），原图不动，
  老师在工作台/标注图里看到的仍是完整原图
- 任何异常（图片损坏等）返回原图路径，绝不阻塞批改
"""

import os
from typing import Any, Dict

from PIL import Image, ImageOps

from ..config import settings
from ..database import AppConfig, SessionLocal

DEFAULT_ENABLED = True
DEFAULT_PERCENT = 12          # 遮盖顶部 12%（试卷头：姓名/学号/班级栏）
PERCENT_MIN = 3
PERCENT_MAX = 40              # 不允许遮掉太多，防止误把题目全遮掉


def get_mask_config() -> Dict[str, Any]:
    """读取隐私遮盖配置：enabled（默认开）、percent（3-40，默认 12）"""
    db = SessionLocal()
    try:
        rows = {r.key: r.value for r in
                db.query(AppConfig).filter(AppConfig.key.in_(
                    ["privacy_mask_enabled", "privacy_mask_percent"])).all()}
    finally:
        db.close()

    enabled = str(rows.get("privacy_mask_enabled", "")).lower()
    enabled = (enabled != "false")  # 缺省/脏值 → 默认开启

    try:
        percent = int(float(rows.get("privacy_mask_percent", DEFAULT_PERCENT)))
    except (TypeError, ValueError):
        percent = DEFAULT_PERCENT
    if not (PERCENT_MIN <= percent <= PERCENT_MAX):
        percent = DEFAULT_PERCENT
    return {"enabled": enabled, "percent": percent}


def save_mask_config(enabled: bool, percent: int) -> Dict[str, Any]:
    if not (PERCENT_MIN <= percent <= PERCENT_MAX):
        raise ValueError(f"遮盖比例须在 {PERCENT_MIN}-{PERCENT_MAX} 之间")
    db = SessionLocal()
    try:
        for key, value in (("privacy_mask_enabled", "true" if enabled else "false"),
                           ("privacy_mask_percent", str(int(percent)))):
            row = db.query(AppConfig).filter(AppConfig.key == key).first()
            if row:
                row.value = value
            else:
                db.add(AppConfig(key=key, value=value))
        db.commit()
    finally:
        db.close()
    return get_mask_config()


def mask_image(image_path: str, percent: int) -> str:
    """对图片顶部 percent% 区域涂黑，另存到 data/masked/，返回新路径。"""
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img)
    w, h = img.size
    band = max(1, int(h * percent / 100))
    draw = Image.new("RGB", (w, band), (0, 0, 0))
    img = img.convert("RGB")
    img.paste(draw, (0, 0))

    out_dir = os.path.join(str(settings.DATA_DIR), "masked")
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(image_path))[0] + "_masked.jpg"
    out_path = os.path.join(out_dir, name)
    img.save(out_path, "JPEG", quality=90)
    return out_path


def apply_privacy_mask(image_path: str) -> str:
    """按配置遮盖；关闭/异常一律返回原图路径，不阻塞批改。"""
    try:
        cfg = get_mask_config()
        if not cfg["enabled"]:
            return image_path
        return mask_image(image_path, cfg["percent"])
    except Exception:
        return image_path
