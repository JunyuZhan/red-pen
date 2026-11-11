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
CORNER_MAX_DIM = 400     # 四角检测前最长边降采样上限
PERSP_GAIN_MIN = 0.02    # 透视纠偏收益：最大角点位移低于 bbox 尺寸此比例不做（近正拍）
PERSP_AREA_MIN = 0.25    # 纸面四边形面积占整图比例下限（太小视为没拍到完整纸面）
PERSP_AREA_MAX = 0.985   # 面积占比上限（几乎全屏说明背景未框住，不纠偏）
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


def _paper_quad(gray_small: Image.Image):
    """降采样灰度图 → 纸面四角点（左上/右上/右下/左下，原小图坐标）。

    原理：纸面是图中最大亮区，二值化取亮点，用 x+y / x-y 极值法找四角。
    校验不过（找不到亮区/四边形退化/非凸）返回 None。
    """
    w, h = gray_small.size
    # Otsu 自动阈值：分离亮纸面与暗背景（背景占比大小时也稳）
    hist = gray_small.histogram()
    total = sum(hist)
    if total <= 0:
        return None
    sum_all = sum(v * hist[v] for v in range(256))
    sum_b, w_b, best, thresh = 0.0, 0, -1.0, 127
    for v in range(256):
        w_b += hist[v]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += v * hist[v]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thresh = between, v
    bright = []
    px = gray_small.load()
    for y in range(h):
        for x in range(w):
            if px[x, y] > thresh:
                bright.append((x, y))
    if len(bright) < 20:
        return None
    # 极值法四角
    tl = min(bright, key=lambda p: p[0] + p[1])
    br = max(bright, key=lambda p: p[0] + p[1])
    tr = max(bright, key=lambda p: p[0] - p[1])
    bl = min(bright, key=lambda p: p[0] - p[1])
    # 四点须互异且构成非退化四边形（鞋带公式面积）
    pts = [tl, tr, br, bl]
    if len(set(pts)) < 4:
        return None
    area2 = sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1]
                for i in range(4))
    area = abs(area2) / 2.0
    if area <= 0:
        return None
    # 面积占比校验
    if not (PERSP_AREA_MIN <= area / (w * h) <= PERSP_AREA_MAX):
        return None
    # 非凸/严重退化校验：每点到四边形质心的距离不能过近
    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0
    if min((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in pts) < (min(w, h) * 0.05) ** 2:
        return None
    return pts


def _perspective_gain(pts, w, h):
    """四边形 vs 外接矩形的倾斜程度：最大角点位移占比。"""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    l, t, r, b = min(xs), min(ys), max(xs), max(ys)
    bbox_corners = [(l, t), (r, t), (r, b), (l, b)]
    disp = max(abs(a[0] - c[0]) + abs(a[1] - c[1])
               for a, c in zip(pts, bbox_corners))
    return disp / max(1, min(w, h))


def _rectify(img: Image.Image, pts, small_w: int, small_h: int):
    """把四边形 pts（小图坐标）透视拉正为矩形，返回新图。"""
    sx = img.size[0] / small_w
    sy = img.size[1] / small_h
    # PIL QUAD 要求源四角顺序：NW, SW, SE, NE
    nw, ne, se, sw = pts
    ordered = [nw, sw, se, ne]
    quad = [c for p in ordered for c in (p[0] * sx, p[1] * sy)]
    def _dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    nw, sw, se, ne = (quad[i:i + 2] for i in (0, 2, 4, 6))
    out_w = max(1, round((_dist(nw, ne) + _dist(sw, se)) / 2))
    out_h = max(1, round((_dist(nw, sw) + _dist(ne, se)) / 2))
    return img.transform((out_w, out_h), Image.QUAD, quad, Image.BICUBIC)


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

        # 1) 透视纠偏（拍歪的作业照把纸面四角拉正；近正拍跳过）
        csmall_w, csmall_h = W, H
        cscale = min(CORNER_MAX_DIM / W, CORNER_MAX_DIM / H, 1.0)
        if cscale < 1.0:
            csmall_w, csmall_h = max(1, round(W * cscale)), max(1, round(H * cscale))
            csmall = gray.resize((csmall_w, csmall_h))
        else:
            csmall = gray
        quad_pts = _paper_quad(csmall)
        if quad_pts and _perspective_gain(quad_pts, csmall_w, csmall_h) >= PERSP_GAIN_MIN:
            img = _rectify(img, quad_pts, csmall_w, csmall_h)
            gray = img.convert("L")
            W, H = img.size

        # 2) 对比度增强（仅低对比度图）
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
