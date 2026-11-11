"""CV 切题 + 裁切放大（PRD 待办 g）

老师整页拍照后，手写小字在整页图里分辨率不足，识别率低。本模块把整页
作业照按"墨迹行"做简单版面分析，切出题目区域，逐题裁切并放大后送模型。

- EXIF 校正：手机照片可能带旋转标记，先 exif_transpose 转正
- 版面分析：纯 PIL 实现（不引入 numpy）。灰度图按行投影（resize 到 1×H
  取每行均值），连续墨迹行合并为区域，区域间空白行作为分隔
- 裁切放大：每个区域四周留边距裁出；高度不足 SCALE_UP_MIN_H 时 LANCZOS
  放大 2 倍（上限 MAX_CROP_DIM，避免超大图浪费 token）
- 模式可切换：设置页开关（AppConfig: split_enabled），默认关闭整页模式
- 兜底：切出区域 <2 或切题链路任何异常，自动回退整页批改，不阻塞

成本提示：开启后每页调用次数 ≈ 题目区域数，仅建议小字密集的作业开启。
"""

import os
import time
from typing import Any, Dict, List

from PIL import Image, ImageOps

from ..config import settings
from ..database import AppConfig, SessionLocal

# ---- 参数（经验值） ----
PROJ_MAX_H = 2000        # 行投影前降采样高度上限
INK_MEAN_MAX = 240       # 行均值低于此视为该行有墨迹（白纸均值≈250+）
MIN_REGION_H = 30        # 区域最小高度（投影坐标），过小视为噪声
MIN_GAP_ROWS = 8         # 区域间至少连续空白行数（投影坐标）
MIN_REGIONS_TO_SPLIT = 2 # 少于 2 个区域不值得切，回退整页
MAX_REGIONS = 20         # 防止极端图切出几十个区域
SCALE_UP_MIN_H = 400     # 裁出高度低于此放大 2 倍
MAX_CROP_DIM = 2000      # 裁切图最长边上限
MARGIN_RATIO = 0.01      # 裁切四周留边（相对原图）


def get_split_config() -> Dict[str, Any]:
    """读取切题模式配置：enabled，存 AppConfig"""
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "split_enabled").first()
        val = row.value if row else "false"
    finally:
        db.close()
    return {"enabled": str(val).lower() == "true"}


def save_split_config(enabled: bool) -> None:
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "split_enabled").first()
        value = "true" if enabled else "false"
        if row:
            row.value = value
        else:
            db.add(AppConfig(key="split_enabled", value=value))
        db.commit()
    finally:
        db.close()


def _row_means(gray: Image.Image) -> List[float]:
    """灰度图按行均值投影：resize 到 1×H，每像素即该行平均灰度。"""
    w, h = gray.size
    if h > PROJ_MAX_H:
        new_h = PROJ_MAX_H
        new_w = max(1, round(w * new_h / h))
        gray = gray.resize((new_w, new_h))
    return list(gray.resize((1, gray.size[1])).getdata())


def _find_regions(row_means: List[float]) -> List[tuple]:
    """墨迹行分组 → [(top, bottom)]，空白行间隔 >= MIN_GAP_ROWS 才切开。"""
    ink = [m < INK_MEAN_MAX for m in row_means]
    regions = []
    start = None
    gap = 0
    for i, has_ink in enumerate(ink):
        if has_ink:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= MIN_GAP_ROWS:
                end = i - gap + 1  # 最后一个墨迹行的下一行
                if end - start >= MIN_REGION_H:
                    regions.append((start, end))
                start, gap = None, 0
    if start is not None and len(ink) - start >= MIN_REGION_H:
        regions.append((start, len(ink)))
    return regions[:MAX_REGIONS]


def split_questions(image_path: str, out_dir: str) -> List[Dict[str, Any]]:
    """整页作业照 → 题目区域裁切图列表。

    返回 [{"index", "path", "top", "bottom"}]，top/bottom 为原图像素坐标。
    失败/切不出区域返回空列表（调用方回退整页批改）。
    """
    try:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
    except Exception:
        return []
    gray = img.convert("L")
    W, H = img.size

    proj_h = min(PROJ_MAX_H, H)
    scale = proj_h / H
    regions = _find_regions(_row_means(gray))
    if len(regions) < MIN_REGIONS_TO_SPLIT:
        return []

    os.makedirs(out_dir, exist_ok=True)
    margin = max(4, round(min(W, H) * MARGIN_RATIO))
    crops = []
    for idx, (top, bottom) in enumerate(regions):
        o_top = max(0, round(top / scale) - margin)
        o_bot = min(H, round(bottom / scale) + margin)
        if o_bot - o_top < MIN_REGION_H / scale:
            continue
        crop = img.crop((0, o_top, W, o_bot))
        cw, ch = crop.size
        if ch < SCALE_UP_MIN_H:
            factor = min(2.0, MAX_CROP_DIM / max(cw, ch))
            if factor > 1.05:
                crop = crop.resize(
                    (min(MAX_CROP_DIM, round(cw * factor)),
                     min(MAX_CROP_DIM, round(ch * factor))), Image.LANCZOS)
        elif max(crop.size) > MAX_CROP_DIM:
            factor = MAX_CROP_DIM / max(crop.size)
            crop = crop.resize((round(cw * factor), round(ch * factor)), Image.LANCZOS)
        path = os.path.join(out_dir, f"split_{int(time.time())}_{idx}.jpg")
        crop.convert("RGB").save(path, "JPEG", quality=92)
        crops.append({"index": idx, "path": path, "top": o_top, "bottom": o_bot})
    return crops
