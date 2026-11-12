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

from PIL import Image, ImageChops, ImageFilter, ImageOps

from ..config import settings
from ..database import AppConfig, SessionLocal

# ---- 参数（经验值） ----
PROJ_MAX_H = 2000        # 行投影前降采样高度上限
INK_FRAC_MIN = 0.004      # 行内墨迹像素占比高于此视为该行有字（0.4%，一道题的一行字足够）
BG_BLUR_RATIO = 0.06      # 背景估计的高斯半径 = 图宽 × 此比例（去除光照不均/阴影）
INK_CONTRAST_MIN = 28     # 像素比局部背景暗超过此值视为墨迹（对夜拍暗纸/阴影鲁棒）
MIN_REGION_H = 30        # 区域最小高度（投影坐标），过小视为噪声
MIN_GAP_ROWS = 8         # 区域间至少连续空白行数（投影坐标）
MIN_REGIONS_TO_SPLIT = 2 # 少于 2 个区域不值得切，回退整页
MAX_REGIONS = 20         # 防止极端图切出几十个区域
SCALE_UP_MIN_H = 400     # 裁出高度低于此放大 2 倍
MAX_CROP_DIM = 2000      # 裁切图最长边上限
MARGIN_RATIO = 0.01      # 裁切四周留边（相对原图）

# ---- g1: 题号 OCR 锚点 + 页眉剔除 ----
# RapidOCR（onnxruntime）可选依赖：识别页面文字行，匹配题号锚点。
# 未安装 / 识别失败 / 无锚点时自动回退纯行投影，绝不阻塞批改。
QNO_RE = None  # 延迟编译（见 _qno_re）
_ocr_engine = None


def _qno_re():
    """题号锚点正则：行首 "1." / "12、" / "(3)" / "1．"（仅阿拉伯数字，
    中文数字"一、二、"可能是章节标题如"一、填空题"，不作为锚点）。"""
    global QNO_RE
    if QNO_RE is None:
        import re
        QNO_RE = re.compile(r"^[\(\(（]?(\d{1,2})[\)）\)\.、．,，:：]")
    return QNO_RE


def _get_ocr():
    """惰性加载 RapidOCR 引擎单例；不可用返回 None（只尝试一次）。"""
    global _ocr_engine
    if _ocr_engine == "unavailable":
        return None
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        except Exception:
            _ocr_engine = "unavailable"
    return _ocr_engine


def parse_qno(text):
    """从 OCR 文本行提取题号；非题号行返回 None。
    "3. x+1=5" → 3；"13、回答下列问题" → 13；"姓名：张三" → None。"""
    m = _qno_re().match(str(text).strip())
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if 1 <= n <= 99 else None


def detect_question_anchors(image_path: str) -> list:
    """OCR 找题号锚点 → [{"no", "top"}]，top 为原图像素坐标，按 top 排序。

    过滤：只保留"自上而下题号递增"的锚点（页眉里的"1 / 3 页"、
    倒序引用等伪锚点被丢弃）。失败返回 []。
    """
    ocr = _get_ocr()
    if ocr is None:
        return []
    try:
        img = ImageOps.exif_transpose(Image.open(image_path))
        result, _ = ocr(img)
        if not result:
            return []
        H = img.size[1]
        raw = []
        for box, text, _conf in result:
            no = parse_qno(text)
            if no is None:
                continue
            top = min(p[1] for p in box)
            if top < 0 or top > H:
                continue
            raw.append({"no": no, "top": round(top)})
        raw.sort(key=lambda a: a["top"])
        anchors, last_no = [], 0
        for a in raw:
            if a["no"] > last_no:
                anchors.append(a)
                last_no = a["no"]
        return anchors
    except Exception:
        return []


def regions_from_anchors(anchors, total_h):
    """锚点 → 题目区域 [(top, bottom)]：每题从自身锚点到下一锚点。
    首锚点之前的区域（页眉/标题/姓名栏）被天然剔除。"""
    if len(anchors) < 2:
        return []
    out = []
    tops = [a["top"] for a in anchors]
    for i, top in enumerate(tops):
        bottom = tops[i + 1] if i + 1 < len(tops) else total_h
        if bottom - top >= MIN_REGION_H:
            out.append((top, bottom))
    return out[:MAX_REGIONS]


def _strip_header(regions, header_top):
    """行投影区域 × 单个锚点：剔除锚点以上（页眉）的区域。"""
    return [(t, b) for (t, b) in regions if b > header_top]


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


def _row_ink_fractions(gray: Image.Image) -> List[float]:
    """每行"墨迹占比"投影：先二值化(暗=墨迹)再缩到 1×H，每像素≈该行暗像素比例。

    不能用行平均灰度：手写字只占行宽一小部分，整行均值仍接近纸色，
    会让真实照片一行墨迹都检测不到（真实作业测试发现的问题）。
    """
    w, h = gray.size
    if h > PROJ_MAX_H:
        new_h = PROJ_MAX_H
        new_w = max(1, round(w * new_h / h))
        gray = gray.resize((new_w, new_h))
    # 光照归一化二值化：大半径模糊估计局部背景(纸面亮度)，与原图相减。
    # 笔迹"比周围纸面暗"才保留——夜拍阴影/泛黄是整片缓变，相减后抵消（真实作业测试发现）。
    radius = max(15, gray.size[0] // 16)
    bg = gray.filter(ImageFilter.GaussianBlur(radius))
    diff = ImageChops.subtract(bg, gray)
    bw = diff.point(lambda v: 255 if v > INK_CONTRAST_MIN else 0)
    return [v / 255.0 for v in bw.resize((1, bw.size[1])).getdata()]


def _find_regions(row_means: List[float]) -> List[tuple]:
    """墨迹行分组 → [(top, bottom)]，空白行间隔 >= MIN_GAP_ROWS 才切开。"""
    ink = [m > INK_FRAC_MIN for m in row_means]
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
    regions = _find_regions(_row_ink_fractions(gray))
    if len(regions) < MIN_REGIONS_TO_SPLIT:
        return []

    # g1: OCR 题号锚点优先——≥2 个锚点时直接按锚点重建区域（页眉天然剔除，
    # 且比行投影更准）；仅 1 个锚点时用它剔除页眉以上的行投影区域；
    # 无 OCR / 无锚点回退纯行投影。
    try:
        anchors = detect_question_anchors(image_path)
    except Exception:
        anchors = []
    if len(anchors) >= 2:
        regions = regions_from_anchors(anchors, H)
    elif len(anchors) == 1:
        regions = _strip_header(regions, anchors[0]["top"])
    if len(regions) < MIN_REGIONS_TO_SPLIT:
        return []

    os.makedirs(out_dir, exist_ok=True)
    margin = max(4, round(min(W, H) * MARGIN_RATIO))
    crops = []
    for idx, (top, bottom) in enumerate(regions):
        if len(anchors) >= 2:
            o_top = max(0, round(top) - margin)
            o_bot = min(H, round(bottom) + margin)
        else:
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


# ---- g2: 手写墨迹块聚类 bbox ----
# 在题目带内把墨迹像素聚成连通块（8-邻接），取并集得到"手写块"的外接框，
# 红笔标注压在手写墨迹上而非整行题干/空白。纯 PIL + 纯 Python，无新依赖。
BLOCK_GRID_MAX = 520     # 墨迹块检测前降采样的最长边（格子数）
BLOCK_MERGE_GAP = 6      # 相邻块间距 ≤ 此格子数则合并（同一行字/连笔画）
BLOCK_MIN_CELLS = 4      # 块最小像素格数（滤纸纹/噪点）
BLOCK_PAD_RATIO = 0.02   # 结果外扩（相对带高），标注别贴死笔画


def detect_ink_blocks(image_path: str, region=None) -> List[tuple]:
    """墨迹连通块聚类 → [(x1, y1, x2, y2)]（原图像素坐标，按 y 排序）。

    region=(x1,y1,x2,y2) 时只在该区域内检测，返回坐标已加回偏移。
    任何失败返回 []（调用方回退原有 bbox，绝不阻塞）。
    """
    try:
        img = ImageOps.exif_transpose(Image.open(image_path))
        if region:
            ox, oy = int(region[0]), int(region[1])
            img = img.crop((ox, oy, int(region[2]), int(region[3])))
        else:
            ox, oy = 0, 0
    except Exception:
        return []
    gray = img.convert("L")
    w, h = gray.size
    if w < 2 or h < 2:
        return []
    s = min(1.0, BLOCK_GRID_MAX / max(w, h))
    gw, gh = max(2, round(w * s)), max(2, round(h * s))
    gray = gray.resize((gw, gh))
    radius = max(15, gw // 16)
    bg = gray.filter(ImageFilter.GaussianBlur(radius))
    diff = ImageChops.subtract(bg, gray)
    bw = diff.point(lambda v: 255 if v > INK_CONTRAST_MIN else 0)
    px = bw.load()

    # 8-邻接连通域标记（迭代栈，避免深递归）
    seen = bytearray(gw * gh)
    raw = []  # [minx, miny, maxx, maxy, count]（格子坐标）
    for y0 in range(gh):
        row0 = y0 * gw
        for x0 in range(gw):
            if px[x0, y0] and not seen[row0 + x0]:
                stack = [(x0, y0)]
                seen[row0 + x0] = 1
                minx = maxx = x0
                miny = maxy = y0
                cnt = 0
                while stack:
                    x, y = stack.pop()
                    cnt += 1
                    if x < minx:
                        minx = x
                    if x > maxx:
                        maxx = x
                    if y < miny:
                        miny = y
                    if y > maxy:
                        maxy = y
                    for dy in (-1, 0, 1):
                        ny = y + dy
                        if ny < 0 or ny >= gh:
                            continue
                        base = ny * gw
                        for dx in (-1, 0, 1):
                            nx = x + dx
                            if 0 <= nx < gw and px[nx, ny] and not seen[base + nx]:
                                seen[base + nx] = 1
                                stack.append((nx, ny))
                if cnt >= BLOCK_MIN_CELLS:
                    raw.append([minx, miny, maxx, maxy, cnt])
    if not raw:
        return []

    # 近邻块合并：间距 ≤ BLOCK_MERGE_GAP 视为同一手写块（迭代直到稳定）
    gap = BLOCK_MERGE_GAP
    merged = True
    while merged and len(raw) > 1:
        merged = False
        out = []
        while raw:
            b = raw.pop()
            hit = None
            for o in out:
                if (b[0] - gap <= o[2] and o[0] - gap <= b[2] and
                        b[1] - gap <= o[3] and o[1] - gap <= b[3]):
                    hit = o
                    break
            if hit:
                hit[0] = min(hit[0], b[0])
                hit[1] = min(hit[1], b[1])
                hit[2] = max(hit[2], b[2])
                hit[3] = max(hit[3], b[3])
                hit[4] += b[4]
                merged = True
            else:
                out.append(b)
        raw = out
    # 并回原图坐标
    inv = 1.0 / s
    blocks = []
    for b in raw:
        x1 = max(0, ox + round(b[0] * inv))
        y1 = max(0, oy + round(b[1] * inv))
        x2 = min(ox + w, ox + round((b[2] + 1) * inv))
        y2 = min(oy + h, oy + round((b[3] + 1) * inv))
        if x2 > x1 and y2 > y1:
            blocks.append((x1, y1, x2, y2))
    blocks.sort(key=lambda b: (b[1], b[0]))
    return blocks[:MAX_REGIONS]


def tighten_band_to_ink(image_path: str, top: int, bottom: int):
    """把一道题的水平带 [top, bottom] 收紧为带内墨迹（手写块）的外接框。

    返回 [x1, y1, x2, y2]；带内无墨迹/失败返回 None（调用方回退整带 bbox）。
    """
    try:
        from PIL import Image as _Img
        W, H = _Img.open(image_path).size
    except Exception:
        return None
    top = max(0, int(top))
    bottom = min(H, int(bottom))
    if bottom - top < MIN_REGION_H:
        return None
    try:
        blocks = detect_ink_blocks(image_path, (0, top, W, bottom))
    except Exception:
        return None
    if not blocks:
        return None
    x1 = min(b[0] for b in blocks)
    y1 = min(b[1] for b in blocks)
    x2 = max(b[2] for b in blocks)
    y2 = max(b[3] for b in blocks)
    pad = max(4, round((bottom - top) * BLOCK_PAD_RATIO))
    return [max(0, x1 - pad), max(top - pad, y1 - pad),
            min(W, x2 + pad), min(bottom + pad, y2 + pad)]


def find_bands(image_path: str, n: int) -> List[Dict[str, Any]]:
    """内容感知切分：把整页按墨迹分布切成 n 条水平带，每带对应一道题。

    原理：行墨迹投影平滑后，找 n-1 条"最深的空白谷"作为切分线
    （在真实练习册上题目间总有相对空白带，哪怕印刷内容连续）。
    用于标注定位：题 i 的标注带 = 第 i 条带（构造性对位，比均匀分布准）。
    返回 [{"top","bottom"}] 原图像素坐标。
    """
    try:
        img = ImageOps.exif_transpose(Image.open(image_path))
    except Exception:
        return []
    if n < 1:
        return []
    gray = img.convert("L")
    W, H = img.size
    frac = _row_ink_fractions(gray)          # 长度 = 投影高（≤2000）
    ph = len(frac)
    scale = H / ph
    # 平滑（滑动平均，窗 ≈ 页高 1.5%）
    win = max(3, ph // 70)
    sm = []
    for i in range(ph):
        seg = frac[max(0, i - win):(i + win + 1)]
        sm.append(sum(seg) / len(seg))
    # 首尾 margin 排除：页眉/页脚空白不能浪费切分线（真实测试发现切分线落到页尾）
    margin = max(10, ph // 8)
    cuts = []
    candidates = list(range(margin, ph - margin))
    # 依次选最深的谷，且与已选切分线保持最小间距（≈页高/n 的 35%）
    min_sep = max(20, int(ph / n * 0.35))
    for _ in range(n - 1):
        best = None
        for i in candidates:
            if any(abs(i - c) < min_sep for c, _ in cuts):
                continue
            if best is None or sm[i] < sm[best]:
                best = i
        if best is None:
            break
        cuts.append((best, sm[best]))
    cuts.sort()
    # 谷太深没有区分度时（整页都很满）也照切——总比均匀分布强
    bands = []
    prev = 0
    for c, _ in cuts:
        bands.append((prev, c))
        prev = c
    bands.append((prev, ph))
    out = []
    for top, bottom in bands:
        o_top = max(0, round(top * scale))
        o_bot = min(H, round(bottom * scale))
        if o_bot - o_top < H * 0.04:
            continue
        out.append({"top": o_top, "bottom": o_bot})
    return out
