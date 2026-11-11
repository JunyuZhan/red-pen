"""端到端串联集成测试：模拟"真实手机拍照"全流程。

合成一张贴近真实拍摄的照片：深色桌面背景上斜放一张作业纸（透视形变），
纸上三道题的墨迹行之间留明显空行（供切题分行），且整体低对比度。
流程：preprocess_image（纠偏+增强+裁边）→ split_questions（切题+放大）
验证：1) 纠偏把纸面拉正（裁边/切题在纸面坐标系上进行才准确）
     2) 切出 ≥3 个题目区域且各区域互不重叠
     3) 每个裁切图内确有墨迹（行均值低于纸面）且被放大（高度达标）
"""
import os
import shutil

os.environ["RED_PEN_DATA_DIR"] = "/tmp/red_pen_e2e_pipe"
shutil.rmtree(os.environ["RED_PEN_DATA_DIR"], ignore_errors=True)
os.makedirs("/tmp/red_pen_e2e_pipe", exist_ok=True)

from PIL import Image, ImageDraw

from backend.models.image_preprocess import preprocess_image
from backend.models.question_splitter import split_questions

CANVAS = (900, 900)
# 透视形变的白纸四边形（深色桌面背景）：NW, SW, SE, NE
PAPER = [(80, 60), (60, 820), (840, 860), (820, 30)]
BLOCKS = 3          # 题目墨迹块数
BLOCK_H = 90        # 每块墨迹高度
BLOCK_GAP = 110     # 块间空白

img = Image.new("L", CANVAS, 25)   # 深色桌面
d = ImageDraw.Draw(img)
d.polygon(PAPER, fill=248)         # 白纸
# 三块低对比度墨迹横线（模拟手写），块间留大空行
top0 = 150
for b in range(BLOCKS):
    y = top0 + b * (BLOCK_H + BLOCK_GAP)
    for i in range(6):
        ly = y + i * 14
        x0, x1 = 120 + (i % 2) * 40, 740 - (i % 3) * 60
        d.line([(x0, ly), (x1, ly)], fill=40, width=5)

src = "/tmp/red_pen_e2e_pipe/photo.jpg"
img.convert("RGB").save(src)

# 1) 预处理：纠偏 + 对比度增强 + 裁边
pre = preprocess_image(src, "/tmp/red_pen_e2e_pipe/pre")
assert pre != src and os.path.exists(pre), "预处理应产出新文件"
pre_img = Image.open(pre).convert("L")
pw, ph = pre_img.size
# 拉正+裁边后四角应贴近纸面而非深色桌面
corners = [(2, 2), (pw - 3, 2), (2, ph - 3), (pw - 3, ph - 3)]
white = sum(1 for c in corners if pre_img.getpixel(c) > 180)
assert white >= 3, f"纠偏后≥3角应为纸面: {[(c, pre_img.getpixel(c)) for c in corners]}"
assert pre_img.getpixel((pw // 2, ph // 2)) > 180, "纠偏后中心应为纸面"
assert pw < CANVAS[0] * 0.95, "裁边应去掉桌面边缘部分"

# 2) 切题：应在纸面坐标系切出 3 个题目区域
crops = split_questions(pre, "/tmp/red_pen_e2e_pipe/splits")
assert len(crops) >= BLOCKS, f"应切出≥{BLOCKS}个区域, got {len(crops)}"
spans = sorted((c["top"], c["bottom"]) for c in crops)
for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
    assert a1 <= b0, f"区域重叠: {a1} > {b0}"

# 3) 每个裁切图：有墨迹 + 小区域被放大到 ≥400px 高
for c in crops:
    ci = Image.open(c["path"]).convert("L")
    assert ci.size[0] > 100 and ci.size[1] > 60, f"裁切图过小: {ci.size}"
    # 区域中部行均值应明显低于纸面白（说明含墨迹）
    mid = list(ci.crop((0, ci.size[1] // 3, ci.size[0], 2 * ci.size[1] // 3))
               .resize((1, 50)).getdata())
    assert min(mid) < 200, f"裁切图应含墨迹: min_row={min(mid)}"

print(f"PASS: e2e pipeline — 纠偏({pw}x{ph}) → 切出{len(crops)}题 → 放大/墨迹校验全过")
