"""
红笔回显：在作业原图上绘制批改标注
- correct  → 红色对勾 ✓
- wrong    → 红色叉 ✗
- partial  → 红色圆圈 + "部分"
- unclear  → 红色圆圈 + "?"
有 bbox 时标在题目位置；没有 bbox 时按题号在图片右侧边栏列出。
"""

from pathlib import Path
from typing import Dict, Any, List, Optional

from PIL import Image, ImageDraw, ImageFont

RED = (211, 47, 47)
GREEN_DARK = (46, 125, 50)
FONT_SIZE_RATIO = 0.035  # 相对图片宽度的字号


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        # Windows 中文字体
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        # Linux 中文字体
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def annotate_image(image_path: str, questions: List[Dict[str, Any]],
                   summary: str = "") -> Optional[str]:
    """在原图上绘制标注，返回标注图路径；失败返回 None"""
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return None

    width, height = img.size
    font_size = max(18, int(width * FONT_SIZE_RATIO))
    font = _load_font(font_size)
    line_w = max(3, int(width * 0.004))

    draw = ImageDraw.Draw(img)
    marked = []

    for q in questions:
        judgement = (q.get("judgement") or "unclear").lower()
        bbox = q.get("bbox")
        no = q.get("question_no", "?")

        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                x1, y1, x2, y2 = [float(v) for v in bbox]
                # 模型可能返回 0~1 归一化坐标
                if all(0 <= v <= 1 for v in (x1, y1, x2, y2)):
                    x1, y2, x2, y1 = x1 * width, y2 * height, x2 * width, y1 * height
                    x1, y2 = x1, y2  # noqa
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
                if x2 > x1 and y2 > y1:
                    color = RED if judgement == "wrong" else GREEN_DARK
                    if judgement == "correct":
                        draw.ellipse([x1, y1, x2, y2], outline=color, width=line_w)
                        _draw_check(draw, x2, y1, font_size, color, line_w)
                    elif judgement == "wrong":
                        draw.ellipse([x1, y1, x2, y2], outline=color, width=line_w)
                        _draw_cross(draw, x2, y1, font_size, color, line_w)
                    else:
                        draw.ellipse([x1, y1, x2, y2], outline=RED, width=line_w)
                        draw.text((x2 + 4, y1), "?" if judgement == "unclear" else "部分",
                                  fill=RED, font=font)
                    marked.append((no, judgement))
                    continue
            except (TypeError, ValueError):
                pass
        marked.append((no, judgement))

    # 未定位的题目画到右侧边栏
    unplaced = [m for m in marked if True]  # 全部列出，便于快速浏览
    if unplaced:
        bar_w = max(120, int(width * 0.18)) + font_size * 2
        bar_x = width - bar_w
        bar = Image.new("RGB", (bar_w, height), (255, 251, 250))
        bar_draw = ImageDraw.Draw(bar)
        y = 10
        bar_draw.text((10, y), "批改结果", fill=RED, font=font)
        y += font_size * 2
        symbol_font = _load_font(int(font_size * 1.2))
        for no, judgement in unplaced:
            if y > height - font_size * 2:
                break
            if judgement == "correct":
                bar_draw.text((10, y), "√", fill=GREEN_DARK, font=symbol_font)
            elif judgement == "wrong":
                bar_draw.text((10, y), "×", fill=RED, font=symbol_font)
            else:
                bar_draw.text((10, y), "?", fill=RED, font=symbol_font)
            bar_draw.text((10 + symbol_font.size + 8, y), f"第{no}题",
                          fill=(60, 60, 60), font=font)
            y += int(font_size * 1.8)
        if summary:
            bar_draw.text((10, max(y, height - font_size * 2)), summary[:18],
                          fill=RED, font=font)
        img.paste(bar, (bar_x, 0))
        draw.line([(bar_x, 0), (bar_x, height)], fill=RED, width=line_w)

    out_path = str(Path(image_path).with_name(
        Path(image_path).stem + "_annotated.jpg"))
    try:
        img.save(out_path, quality=90)
        return out_path
    except Exception:
        return None


def _draw_check(draw: ImageDraw.ImageDraw, x: float, y: float,
                size: int, color, width: int) -> None:
    """在 (x, y) 右上角画对勾"""
    s = size
    draw.line([(x + s * 0.1, y + s * 0.5), (x + s * 0.4, y + s * 0.8),
               (x + s * 0.9, y - s * 0.1)], fill=color, width=width, joint="curve")


def _draw_cross(draw: ImageDraw.ImageDraw, x: float, y: float,
                size: int, color, width: int) -> None:
    """在 (x, y) 右上角画叉"""
    s = size
    draw.line([(x + s * 0.1, y + s * 0.1), (x + s * 0.7, y + s * 0.7)],
              fill=color, width=width)
    draw.line([(x + s * 0.7, y + s * 0.1), (x + s * 0.1, y + s * 0.7)],
              fill=color, width=width)
