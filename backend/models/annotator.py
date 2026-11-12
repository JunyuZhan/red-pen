"""
红笔回显：在作业原图上绘制批改标注
- correct  → 绿色对勾 ✓（贴角小标）
- wrong    → 红色叉 ✗ + 短错因文字条（bbox 外侧，不遮挡原字）
- partial  → 红色 "部分" + 短错因
- unclear  → 红色 "?" + 短错因
标注原则：只在 bbox 右上角画小符号，错因文字条放 bbox 外侧（右侧优先，
放不下换左侧，再放下方），绝不整框圈住学生手写内容。
有 bbox 时标在题目位置；没有 bbox 时按题号在图片右侧边栏列出。
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

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
    placed_chips: List[Tuple[float, float, float, float]] = []  # 防文字条压相邻题带
    other_bboxes = []
    for q in questions:
        b = q.get("bbox")
        if isinstance(b, (list, tuple)) and len(b) == 4:
            try:
                bx1, by1, bx2, by2 = [float(v) for v in b]
                if all(0 <= v <= 1 for v in (bx1, by1, bx2, by2)):
                    bx1, by2, bx2, by1 = bx1 * width, by2 * height, bx2 * width, by1 * height
                if bx2 > bx1 and by2 > by1:
                    other_bboxes.append((bx1, by1, bx2, by2))
            except (TypeError, ValueError):
                pass

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
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
                if x2 > x1 and y2 > y1:
                    reason = short_reason(q)
                    if judgement == "correct":
                        _draw_check(draw, x2, y1, font_size, GREEN_DARK, line_w)
                    else:
                        color = RED
                        if judgement == "wrong":
                            _draw_cross(draw, x2, y1, font_size, color, line_w)
                        else:
                            tag = "?" if judgement == "unclear" else "部分"
                            draw.text((x2 + 2, y1 - font_size), tag,
                                      fill=RED, font=font)
                        if reason:
                            avoid = [r for r in other_bboxes + placed_chips
                                     if r != (x1, y1, x2, y2)]
                            chip_rect = _draw_reason_chip(draw, (x1, y1, x2, y2),
                                                          reason, width, height,
                                                          font, line_w, avoid)
                            if chip_rect:
                                placed_chips.append(chip_rect)
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


def short_reason(q: Dict[str, Any], max_chars: int = 6) -> str:
    """错题的短错因文字：优先 error_type，其次 analysis 截断"""
    et = (q.get("error_type") or "").strip()
    if et and et.lower() != "null":
        return et[:max_chars]
    an = (q.get("analysis") or "").strip()
    if an and an.lower() != "null":
        return an.replace("\n", " ")[:max_chars]
    return ""


def _rects_overlap(a, b, gap: float = 2.0) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or
                a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _overlap_area(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def place_chip(bbox: Tuple[float, float, float, float], text_w: int, text_h: int,
               img_w: int, img_h: int, pad: int = 4,
               avoid: Optional[List[Tuple[float, float, float, float]]] = None
               ) -> Tuple[float, float]:
    """错因文字条放置位置（左上角）：右侧优先 → 左侧 → 上方 → 下方。
    avoid 中的矩形（其它题框/已放置文字条）尽量避开；全部避不开时
    选与它们重叠面积最小的候选位。"""
    x1, y1, x2, y2 = bbox
    chip_w = text_w + pad * 2
    chip_h = text_h + pad * 2
    below_y = min(y2 + pad, max(0, img_h - chip_h))
    candidates = [
        (x2 + pad, y1),                    # 右侧
        (x1 - pad - chip_w, y1),           # 左侧
        (x1, min(y1 - pad - chip_h, y1)),  # 上方
        (x1, below_y),                     # 下方
    ]
    avoid = avoid or []

    def rect_at(cx, cy):
        return (cx - pad, cy - pad, cx + text_w + pad, cy + text_h + pad)

    in_bounds = [c for c in candidates
                 if 0 <= c[0] and c[0] + chip_w <= img_w
                 and 0 <= c[1] and c[1] + chip_h <= img_h]
    if not in_bounds:
        in_bounds = [candidates[-1]]
    for c in in_bounds:
        r = rect_at(*c)
        if not any(_rects_overlap(r, av) for av in avoid):
            return c
    # 全部相交：选重叠面积最小的
    best = min(in_bounds, key=lambda c: sum(
        _overlap_area(rect_at(*c), av) for av in avoid))
    return best


def _draw_reason_chip(draw: ImageDraw.ImageDraw,
                      bbox: Tuple[float, float, float, float],
                      reason: str, img_w: int, img_h: int,
                      font: ImageFont.FreeTypeFont, line_w: int,
                      avoid: Optional[List[Tuple[float, float, float, float]]] = None
                      ) -> Optional[Tuple[float, float, float, float]]:
    """在 bbox 外侧画错因文字条（白底红字），不遮挡原字；
    尽量避开 avoid 中的矩形。返回文字条实际矩形，失败返回 None"""
    try:
        tw, th = draw.textbbox((0, 0), reason, font=font)[2:]
    except Exception:
        tw, th = len(reason) * font.size, font.size
    if tw <= 0:
        return None
    cx, cy = place_chip(bbox, tw, th, img_w, img_h, avoid=avoid)
    pad = 4
    bg = [cx - pad, cy - pad, cx + tw + pad, cy + th + pad]
    bg[0] = max(0, bg[0]); bg[1] = max(0, bg[1])
    bg[2] = min(img_w, bg[2]); bg[3] = min(img_h, bg[3])
    draw.rectangle(bg, fill=(255, 253, 250), outline=RED, width=max(1, line_w // 2))
    draw.text((bg[0] + pad, bg[1] + pad), reason, fill=RED, font=font)
    return tuple(bg)


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
