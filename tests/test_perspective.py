"""透视纠偏测试（PRD 审计缺口 r 补全）：拍歪的作业照纸面四角检测 + 拉正。

合成"照片"：深色背景 + 白纸用 QUAD 变换摆成倾斜四边形 + 墨迹线条，
验证四角检测、拉正效果、近正拍跳过、异常回退。
"""
import os
import shutil

os.environ["RED_PEN_DATA_DIR"] = "/tmp/red_pen_persp_test"
shutil.rmtree(os.environ["RED_PEN_DATA_DIR"], ignore_errors=True)

from PIL import Image, ImageDraw

from backend.models.image_preprocess import (
    _paper_quad, _perspective_gain, _rectify, preprocess_image,
)

os.makedirs("/tmp/red_pen_persp_test", exist_ok=True)


def make_photo(quad, canvas=(400, 400)):
    """深色画布 + 白色四边形纸面（quad: NW,SW,SE,NE）+ 弯曲墨迹横线"""
    img = Image.new("L", canvas, 20)
    d = ImageDraw.Draw(img)
    d.polygon([tuple(p) for p in quad], fill=255)
    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    nw, sw, se, ne = [tuple(p) for p in quad]
    for t in (0.15, 0.35, 0.55, 0.75, 0.9):
        l1 = lerp(nw, sw, t)
        l2 = lerp(ne, se, t)
        for u0, u1 in ((0.06, 0.3), (0.4, 0.94)):
            d.line([lerp(l1, l2, u0), lerp(l1, l2, u1)], fill=60, width=2)
    return img


# 1) 倾斜四边形四角检测
tilted = [(60, 30), (50, 350), (370, 380), (350, 10)]  # NW, SW, SE, NE
photo = make_photo(tilted)
small = photo.resize((200, 200))
pts = _paper_quad(small)
assert pts is not None, "倾斜纸面应检测出四角"
nw, ne, se, sw = pts
w_s, h_s = small.size
# 检测角点应接近真实角点（允许 8% 误差）
expect = [(60, 30), (350, 10), (370, 380), (50, 350)]  # 检测顺序 tl,tr,br,bl
for (px, py), (ex, ey) in zip(pts, expect):
    assert abs(px - ex / 400 * w_s) < w_s * 0.08 and abs(py - ey / 400 * h_s) < h_s * 0.08, \
        f"角点偏差过大: got {pts} expect~{expect}"

# 2) 倾斜程度：位移明显超过 2% 阈值
gain = _perspective_gain(pts, w_s, h_s)
assert gain >= 0.02, f"倾斜位移占比应≥0.02: {gain}"

# 3) 拉正后纸面应铺满输出（边缘不应是大片深色背景）
rect = _rectify(photo, pts, w_s, h_s)
g = rect.convert("L")
# 四角像素应为白纸（>200）
for corner in [(2, 2), (rect.size[0] - 3, 2), (2, rect.size[1] - 3), (rect.size[0] - 3, rect.size[1] - 3)]:
    assert g.getpixel(corner) > 180, f"拉正后四角应为纸面: {corner}={g.getpixel(corner)}"

# 4) 近正拍跳过：几乎无倾斜的四边形 gain 低于阈值
straight_pts = [(5, 5), (195, 5), (195, 195), (5, 195)]
assert _perspective_gain(straight_pts, 200, 200) < 0.02, "近正拍不应触发纠偏"

# 5) preprocess_image 端到端：倾斜照片纠偏后输出
src = "/tmp/red_pen_persp_test/in.jpg"
photo.convert("RGB").save(src)
out = preprocess_image(src, "/tmp/red_pen_persp_test/out")
assert os.path.exists(out) and out != src, "预处理应产出新文件"
op = Image.open(out)
assert op.size[0] > 100 and op.size[1] > 100, f"纠偏输出尺寸异常: {op.size}"
oc = op.convert("L")
for corner in [(2, 2), (op.size[0] - 3, 2), (2, op.size[1] - 3), (op.size[0] - 3, op.size[1] - 3)]:
    assert oc.getpixel(corner) > 180, f"端到端纠偏四角应为纸面: {corner}"

# 6) 异常输入：文件不存在等异常返回原路径不阻塞
bad = "/tmp/red_pen_persp_test/不存在.jpg"
assert preprocess_image(bad, "/tmp/red_pen_persp_test/out") == bad, "异常应回退原路径"

print("PASS: test_perspective 6 项全过（四角检测/倾斜阈值/拉正/近正拍跳过/端到端/异常回退）")
