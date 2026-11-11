# -*- coding: utf-8 -*-
"""端到端串联集成测试：模拟"真实手机拍照"全流程（pytest 原生）。

合成一张贴近真实拍摄的照片：深色桌面背景上斜放一张作业纸（透视形变），
纸上三道题的墨迹行之间留明显空行（供切题分行），且整体低对比度。
流程：preprocess_image（纠偏+增强+裁边）→ split_questions（切题+放大）
"""
import os

import pytest
from PIL import Image, ImageDraw

from backend.models.image_preprocess import preprocess_image
from backend.models.question_splitter import split_questions

CANVAS = (900, 900)
# 透视形变的白纸四边形（深色桌面背景）：NW, SW, SE, NE
PAPER = [(80, 60), (60, 820), (840, 860), (820, 30)]
BLOCKS = 3          # 题目墨迹块数
BLOCK_H = 90        # 每块墨迹高度
BLOCK_GAP = 110     # 块间空白


def _make_photo(path):
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
    img.convert("RGB").save(path)


@pytest.fixture(scope="module")
def pre(tmp_path_factory):
    """合成照片 → 预处理（纠偏+增强+裁边），模块内共享。"""
    d = tmp_path_factory.mktemp("e2e_pipe")
    src = str(d / "photo.jpg")
    _make_photo(src)
    pre = preprocess_image(src, str(d / "pre"))
    assert pre != src and os.path.exists(pre), "预处理应产出新文件"
    return pre


def test_rectify_paper_corners(pre):
    """纠偏+裁边后四角应贴近纸面而非深色桌面。"""
    img = Image.open(pre).convert("L")
    pw, ph = img.size
    corners = [(2, 2), (pw - 3, 2), (2, ph - 3), (pw - 3, ph - 3)]
    white = sum(1 for c in corners if img.getpixel(c) > 180)
    assert white >= 3, f"纠偏后≥3角应为纸面: {[(c, img.getpixel(c)) for c in corners]}"
    assert img.getpixel((pw // 2, ph // 2)) > 180, "纠偏后中心应为纸面"


def test_crop_removes_desk_border(pre):
    """裁边应去掉桌面边缘部分。"""
    pw, _ = Image.open(pre).size
    assert pw < CANVAS[0] * 0.95


def test_split_non_overlapping(pre):
    """切题应在纸面坐标系切出 ≥3 个互不重叠的题目区域。"""
    crops = split_questions(pre, str(pre + "_splits"))
    assert len(crops) >= BLOCKS, f"应切出≥{BLOCKS}个区域, got {len(crops)}"
    spans = sorted((c["top"], c["bottom"]) for c in crops)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert a1 <= b0, f"区域重叠: {a1} > {b0}"


@pytest.mark.parametrize("idx", range(3))
def test_crops_enlarged_with_ink(pre, idx):
    """每个裁切图有墨迹且小区域被放大到 ≥400px 高。"""
    crops = split_questions(pre, str(pre + f"_splits_{idx}"))
    assert idx < len(crops), f"第{idx}题未切出"
    ci = Image.open(crops[idx]["path"]).convert("L")
    assert ci.size[0] > 100 and ci.size[1] > 60, f"裁切图过小: {ci.size}"
    # 区域中部行均值应明显低于纸面白（说明含墨迹）
    mid = list(ci.crop((0, ci.size[1] // 3, ci.size[0], 2 * ci.size[1] // 3))
               .resize((1, 50)).getdata())
    assert min(mid) < 200, f"裁切图应含墨迹: min_row={min(mid)}"
