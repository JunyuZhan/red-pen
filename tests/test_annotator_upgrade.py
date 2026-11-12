# -*- coding: utf-8 -*-
"""g3 标注渲染升级测试：错号+短错因放 bbox 外侧，不遮挡原字"""
import os
import tempfile

from PIL import Image

from backend.models.annotator import (
    annotate_image,
    place_chip,
    short_reason,
)


def _make_page(tmpdir, w=800, h=600):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for y in range(200, 260):  # 模拟手写墨迹横条
        for x in range(100, 500):
            img.putpixel((x, y), (40, 40, 40))
    path = os.path.join(tmpdir, "page.jpg")
    img.save(path)
    return path


def test_short_reason_prefers_error_type():
    q = {"error_type": "计算错误", "analysis": "进位加错了"}
    assert short_reason(q) == "计算错误"
    assert short_reason({"analysis": "概念混淆，单位不统一"}) == "概念混淆，单"
    assert short_reason({}) == ""
    assert short_reason({"error_type": "null"}) == ""


def test_place_chip_right_then_left_then_below():
    # 右侧有空间 → 右侧
    assert place_chip((100, 100, 200, 200), 60, 20, 800, 600)[0] > 200
    # 右侧贴边 → 左侧
    pos = place_chip((100, 100, 760, 200), 60, 20, 800, 600)
    assert pos[0] + 60 < 100  # 文字整体在 bbox 左侧
    # 左右都贴边 → 下方
    pos = place_chip((50, 100, 760, 200), 60, 20, 800, 300)
    assert pos[1] > 200


def test_wrong_answer_does_not_cover_handwriting_center():
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_page(tmp)
        qs = [{"question_no": 1, "judgement": "wrong",
               "error_type": "计算错误", "bbox": [100, 190, 500, 270]}]
        out = annotate_image(src, qs)
        assert out and os.path.exists(out)
        orig = Image.open(src)
        ann = Image.open(out)
        # bbox 中心区域（远离右上角符号处）应保持墨迹原样，无红色
        cx, cy = 250, 240
        assert ann.getpixel((cx, cy)) == orig.getpixel((cx, cy))
        # 无整框红椭圆：bbox 左边缘中部应为原墨迹色而非红色
        assert ann.getpixel((110, 245)) == orig.getpixel((110, 245))


def test_correct_marks_small_check_no_ellipse():
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_page(tmp)
        qs = [{"question_no": 1, "judgement": "correct", "bbox": [100, 190, 500, 270]}]
        out = annotate_image(src, qs)
        assert out and os.path.exists(out)
        ann = Image.open(out)
        # 墨迹中段保持原样（无整框圈画）
        assert ann.getpixel((300, 230)) == (40, 40, 40)


def test_chip_reason_text_present_for_wrong():
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_page(tmp, 1000, 800)
        qs = [{"question_no": 2, "judgement": "wrong",
               "error_type": "概念错误", "bbox": [100, 150, 400, 300]}]
        out = annotate_image(src, qs)
        ann = Image.open(out)
        # 右侧错因条应含红色文字像素（bbox 右缘之外）
        region = ann.crop((410, 140, 560, 220))
        assert any(p[0] > 150 and p[1] < 90 and p[2] < 90
                   for p in region.getdata())


def test_partial_and_unclear_render():
    with tempfile.TemporaryDirectory() as tmp:
        src = _make_page(tmp)
        qs = [
            {"question_no": 1, "judgement": "partial", "error_type": "粗心",
             "bbox": [100, 190, 500, 270]},
            {"question_no": 2, "judgement": "unclear", "error_type": None,
             "analysis": "无法辨认", "bbox": [100, 400, 500, 480]},
        ]
        out = annotate_image(src, qs)
        assert out and os.path.exists(out)
