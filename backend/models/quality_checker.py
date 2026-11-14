"""
图像质量门禁：上传时检测模糊/亮度/尺寸，产出 quality_flags（中文列表）。

检测项：
- 模糊：灰度图拉普拉斯算子方差，低于阈值视为模糊
- 亮度过低/过曝：灰度均值
- 尺寸过小：最短边像素数
- 图像损坏：无法解码
- 像素总量过大：解压炸弹防护

仅用 PIL（直方图统计），不引入 numpy 新依赖。
"""

import io
from PIL import Image, ImageOps, ImageFilter

# 阈值（经验值，宁可多提醒不误杀；可强行上传）
BLUR_VARIANCE_MIN = 250.0     # 拉普拉斯方差低于此视为模糊（清晰作业照通常>300）
DARK_MEAN_MAX = 45.0          # 平均亮度低于此视为太暗
BRIGHT_MEAN_MIN = 246.0
OVEREXPOSE_STD_MAX = 28.0     # 高亮且低对比才判过曝（白纸黑字对比大属正常）       # 平均亮度高于此视为过曝（白纸照片均值高，阈值需留余量）
MIN_DIMENSION = 400           # 最短边低于此视为过小
MAX_PIXELS = 100_000_000      # 像素总量上限（约 1 亿），防解压炸弹拖垮内存
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # PIL 解压炸弹硬上限与我们对齐
EVAL_MAX_DIM = 1200           # 评估时降采样上限，控制耗时

_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1)


def _hist_stats(gray):
    """降采样后的灰度图 → (平均亮度, 拉普拉斯方差, 标准差)。"""
    gray.thumbnail((EVAL_MAX_DIM, EVAL_MAX_DIM))
    hist = gray.histogram()
    total = sum(hist)
    mean = sum(i * c for i, c in enumerate(hist)) / total
    var_gray = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / total
    std = var_gray ** 0.5

    lap = gray.filter(_LAPLACIAN)
    lhist = lap.histogram()  # 256 bin，中心 128
    ltotal = sum(lhist)
    lmean = sum(i * c for i, c in enumerate(lhist)) / ltotal
    var = sum((i - lmean) ** 2 * c for i, c in enumerate(lhist)) / ltotal
    return mean, var, std


def check_image(data: bytes) -> list:
    """返回中文质量提示列表，空列表表示合格。"""
    flags = []
    try:
        img = Image.open(io.BytesIO(data))
    except Exception:
        return ["图片无法解码，文件可能损坏"]

    try:
        img = ImageOps.exif_transpose(img)  # EXIF 方向校正后再评估
        img.load()
    except Image.DecompressionBombError:
        return ["图片像素总量过大，请用手机常规模式拍摄"]
    except Exception:
        return ["图片数据不完整或已损坏"]

    w, h = img.size
    if w * h > MAX_PIXELS:
        return ["图片像素总量过大（{}×{}），请用手机常规模式拍摄".format(w, h)]

    if min(w, h) < MIN_DIMENSION:
        flags.append(f"图片尺寸过小（{w}×{h}），手写字可能看不清，请靠近拍摄")

    gray = img.convert("L")
    mean, lap_var, std = _hist_stats(gray)

    if mean < DARK_MEAN_MAX:
        flags.append("照片太暗，建议开灯或补光后重拍")
    elif mean > BRIGHT_MEAN_MIN and std < OVEREXPOSE_STD_MAX:
        flags.append("照片过曝发白，请避免阳光直射或闪光灯")
    elif lap_var < BLUR_VARIANCE_MIN:
        # 模糊只在亮度正常时评估，过暗图拉普拉斯方差天然偏低
        flags.append("照片模糊，请对焦后保持稳定再拍")

    return flags
