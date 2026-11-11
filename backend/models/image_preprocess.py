"""切题前置图像预处理（PRD 待办 g 增强）

切题模式的前置步骤：把整页作业照先做规范化，再交给切题器，
提升手写小字识别率。

- EXIF 校正：手机照片带旋转标记时 exif_transpose 转正
- 对比度增强：整图灰度标准差低于阈值（拍照偏灰/曝光不足）时做
  自动对比度拉伸（cutoff=1% 防噪点放大；纯 PIL，无 numpy）
- 墨迹边界裁边：按列/行墨迹投影找内容包围盒，裁掉四周大块空白
  桌面/反光区，让切题投影更聚焦、送模型的有效分辨率更高
- 可开关：设置页 AppCfg: preprocess_enabled，默认关闭；任何异常
  返回原图路径，不阻塞批改
"""

import os
from typing import Any, Dict

from PIL import Image, ImageOps, ImageStat

from ..config import settings
from ..database import AppConfig, SessionLocal

# ---- 参数（经验值） ----
PROJ_MAX_DIM = 1500      # 投影分析前最长边降采样上限
INK_MAX = 200            # 单行/列均值低于此视为有墨迹（含中等灰背景）
STD_ENHANCE_BELOW = 40.0 # 整图灰度标准差低于此才做对比度增强
AUTOCONTRAST_CUTOFF = 1  # 对比度拉伸裁掉 1% 极值，防噪点放大
CROP_GAIN_MIN = 0.10     # 裁边收益：面积缩减低于此不裁（避免误裁）


def get_preprocess_config() -> Dict[str, Any]:
    """读取预处理开关配置：enabled，存 AppConfig"""
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "preprocess_enabled").first()
        val = row.value if row else "false"
    finally:
        db.close()
    return {"enabled": str(val).lower() == "true"}


def save_preprocess_config(enabled: bool) -> None:
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "preprocess_enabled").first()
        value = "true" if enabled else "false"
        if row:
            row.value = value
        else:
            db.add(AppConfig(key="preprocess_enabled", value=value))
        db.commit()
    finally:
        db.close()


def _ink_bbox(gray_small: Image.Image) -> tuple:
    """降采样灰度图 → 墨迹内容包围盒 (left, top, right, bottom)。

    逐像素二值化后按行/列取暗像素占比，占比高于 INK_FRACTION_MIN
    视为该行/列有内容（均值投影对细笔画竖线会失效，故用占比）。
    无内容返回 None。
    """
    w, h = gray_small.size
    binimg = gray_small.point(lambda v: 255 if v < INK_MAX else 0)
    INK_FRACTION_MIN = 0.003
    row_ink = [m / 255.0 > INK_FRACTION_MIN
               for m in binimg.resize((1, h)).getdata()]
    col_ink = [m / 255.0 > INK_FRACTION_MIN
               for m in binimg.resize((w, 1)).getdata()]

    def _span(flags):
        try:
            return flags.index(True), len(flags) - 1 - flags[::-1].index(True)
        except ValueError:
            return (None, None)

    rs, re = _span(row_ink)
    cs, ce = _span(col_ink)
    if rs is None or cs is None:
        return None
    return (cs, rs, ce + 1, re + 1)


def preprocess_image(image_path: str, out_dir: str) -> str:
    """预处理整页作业照，返回处理后文件路径。

    任何一步失败均返回原路径（预处理不阻塞批改）。
    """
    try:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        W, H = img.size
        gray = img.convert("L")

        # 1) 对比度增强（仅低对比度图）
        std = ImageStat.Stat(gray).stddev[0]
        if std < STD_ENHANCE_BELOW:
            img = ImageOps.autocontrast(img, cutoff=AUTOCONTRAST_CUTOFF)
            gray = img.convert("L")

        # 2) 墨迹边界裁边（降采样图上算投影，映射回原坐标）
        scale = min(PROJ_MAX_DIM / W, PROJ_MAX_DIM / H, 1.0)
        if scale < 1.0:
            small = gray.resize((max(1, round(W * scale)), max(1, round(H * scale))))
        else:
            small = gray
        bbox = _ink_bbox(small)
        if bbox:
            l, t, r, b = bbox
            inv = 1.0 / scale if scale else 1.0
            ol, ot = round(l * inv), round(t * inv)
            orr, ob = min(W, round(r * inv)), min(H, round(b * inv))
            if orr > ol and ob > ot:
                gain = 1 - (orr - ol) * (ob - ot) / max(1, W * H)
                if gain >= CROP_GAIN_MIN:
                    img = img.crop((ol, ot, orr, ob))

        os.makedirs(out_dir, exist_ok=True)
        name = os.path.splitext(os.path.basename(image_path))[0]
        out_path = os.path.join(out_dir, "pre_" + name + ".jpg")
        img.save(out_path, "JPEG", quality=92)
        return out_path
    except Exception:
        return image_path
