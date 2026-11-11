# -*- coding: utf-8 -*-
"""透视纠偏测试（PRD 审计缺口 r 补全）：拍歪的作业照纸面四角检测 + 拉正。

合成"照片"：深色背景 + 白纸用 QUAD 变换摆成倾斜四边形 + 墨迹线条，
验证四角检测、拉正效果、近正拍跳过、异常回退。
"""
import os

import pytest
from PIL import Image, ImageDraw

from backend.models.image_preprocess import (
    _paper_quad, _perspective_gain, _rectify, preprocess_image,
)


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


# 倾斜四边形（NW, SW, SE, NE）与其期望检测顺序（tl, tr, br, bl）
TILTED_QUAD = [(60, 30), (50, 350), (370, 380), (350, 10)]
TILTED_EXPECT = [(60, 30), (350, 10), (370, 380), (50, 350)]


def test_tilted_paper_quad_detection():
    photo = make_photo(TILTED_QUAD)
    small = photo.resize((200, 200))
    pts = _paper_quad(small)
    assert pts is not None, "倾斜纸面应检测出四角"
    w_s, h_s = small.size
    for (px, py), (ex, ey) in zip(pts, TILTED_EXPECT):
        assert abs(px - ex / 400 * w_s) < w_s * 0.08
        assert abs(py - ey / 400 * h_s) < h_s * 0.08


def test_tilted_gain_above_threshold():
    photo = make_photo(TILTED_QUAD)
    small = photo.resize((200, 200))
    pts = _paper_quad(small)
    gain = _perspective_gain(pts, *small.size)
    assert gain >= 0.02, f"倾斜位移占比应≥0.02: {gain}"


def test_rectified_corners_are_paper():
    photo = make_photo(TILTED_QUAD)
    small = photo.resize((200, 200))
    pts = _paper_quad(small)
    rect = _rectify(photo, pts, *small.size)
    g = rect.convert("L")
    for corner in [(2, 2), (rect.size[0] - 3, 2), (2, rect.size[1] - 3),
                   (rect.size[0] - 3, rect.size[1] - 3)]:
        assert g.getpixel(corner) > 180, f"拉正后四角应为纸面: {corner}={g.getpixel(corner)}"


def test_near_straight_skipped():
    straight_pts = [(5, 5), (195, 5), (195, 195), (5, 195)]
    assert _perspective_gain(straight_pts, 200, 200) < 0.02, "近正拍不应触发纠偏"


def test_preprocess_e2e_rectifies(tmp_path):
    src = tmp_path / "in.jpg"
    make_photo(TILTED_QUAD).convert("RGB").save(src)
    out = preprocess_image(str(src), str(tmp_path / "out"))
    assert os.path.exists(out) and out != str(src), "预处理应产出新文件"
    op = Image.open(out)
    assert op.size[0] > 100 and op.size[1] > 100, f"纠偏输出尺寸异常: {op.size}"
    oc = op.convert("L")
    for corner in [(2, 2), (op.size[0] - 3, 2), (2, op.size[1] - 3),
                   (op.size[0] - 3, op.size[1] - 3)]:
        assert oc.getpixel(corner) > 180, f"端到端纠偏四角应为纸面: {corner}"


def test_preprocess_missing_file_fallback(tmp_path):
    bad = tmp_path / "不存在.jpg"
    assert preprocess_image(str(bad), str(tmp_path / "out")) == str(bad), "异常应回退原路径"
