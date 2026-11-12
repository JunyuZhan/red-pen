"""g2 手写墨迹块聚类 bbox 测试：detect_ink_blocks / tighten_band_to_ink"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw

from backend.models.question_splitter import detect_ink_blocks, tighten_band_to_ink


def _make_page(path, blobs, W=1000, H=1400):
    """白纸 + 指定位置的密集墨迹块（笔画间距小于 MIN_GAP_ROWS，模拟手写）"""
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for (x1, y1, x2, y2) in blobs:
        # 稠密斜线填充模拟字迹（间距3px，保证连通/近邻合并）
        for xx in range(x1, x2, 3):
            d.line([(xx, y1), (min(xx + 6, x2), y2)], fill=(20, 20, 20), width=3)
    img.save(path)
    return path


def test_detect_ink_blocks_finds_blobs(tmpdir):
    p = _make_page(str(tmpdir.join("page.jpg")),
                   [(100, 200, 400, 300), (150, 700, 500, 800)])
    blocks = detect_ink_blocks(p)
    assert len(blocks) == 2, blocks
    # 按 y 排序，第一块对应上面的 blob
    b1, b2 = blocks
    assert abs(b1[0] - 100) < 40 and abs(b1[1] - 200) < 40
    assert abs(b1[2] - 400) < 40 and abs(b1[3] - 300) < 40
    assert b2[1] > b1[3]  # 第二块在下方
    assert abs(b2[1] - 700) < 40


def test_detect_ink_blocks_region_offset(tmpdir):
    p = _make_page(str(tmpdir.join("page2.jpg")), [(100, 200, 400, 300)])
    blocks = detect_ink_blocks(p, region=(0, 100, 1000, 500))
    assert len(blocks) == 1
    x1, y1, x2, y2 = blocks[0]
    # 坐标含 region 偏移
    assert 90 <= x1 <= 140 and 190 <= y1 <= 240
    assert 360 <= x2 <= 440 and 290 <= y2 <= 340


def test_detect_ink_blocks_blank_page(tmpdir):
    p = _make_page(str(tmpdir.join("blank.jpg")), [])
    assert detect_ink_blocks(p) == []
    assert tighten_band_to_ink(p, 100, 500) is None


def test_tighten_band_to_ink_hugs_handwriting(tmpdir):
    """带是整行宽，收紧后 bbox 应明显小于整带并压在墨迹上"""
    p = _make_page(str(tmpdir.join("page3.jpg")), [(600, 460, 900, 540)])
    band_top, band_bottom = 400, 600  # 带：0~1000 宽 × 200 高
    tight = tighten_band_to_ink(p, band_top, band_bottom)
    assert tight is not None
    x1, y1, x2, y2 = tight
    # 不再是整带（宽 1000 → 收紧到墨迹附近）
    assert x1 > 300, tight
    assert x2 < 1000
    # 高度收紧：200 高的带 → 接近墨迹高度
    assert y2 - y1 < 160, tight
    # 收紧框完全在带内（允许少量外扩余量）
    assert y1 >= band_top - 10 and y2 <= band_bottom + 10


def test_tighten_band_to_ink_fallbacks(tmpdir):
    p = _make_page(str(tmpdir.join("page4.jpg")), [(600, 460, 900, 540)])
    # 带太窄 → None
    assert tighten_band_to_ink(p, 100, 110) is None
    # 带内无墨迹 → None
    assert tighten_band_to_ink(p, 1000, 1300) is None
    # 文件不存在 → None（不抛异常）
    assert tighten_band_to_ink(str(tmpdir.join("nope.jpg")), 0, 200) is None


def test_tighten_bounds_clamped_to_page(tmpdir):
    p = _make_page(str(tmpdir.join("page5.jpg")),
                   [(20, 20, 300, 120)], W=800, H=800)
    tight = tighten_band_to_ink(p, 0, 200)
    assert tight is not None
    assert tight[0] >= 0 and tight[1] >= 0
    assert tight[2] <= 800 and tight[3] <= 210
