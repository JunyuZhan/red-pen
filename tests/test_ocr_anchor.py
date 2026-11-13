# -*- coding: utf-8 -*-
"""PRD 待办 g1：题号 OCR 锚点 + 页眉剔除 测试

RapidOCR 本体不进 CI 断言（依赖体积大），OCR 检测函数用 monkeypatch 假引擎测；
正则解析 / 锚点过滤 / 区域重建 / 页眉剔除 / split_questions 集成为纯函数断言。
"""
import os
import sys

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.models import question_splitter as qs


# ---- parse_qno：题号行识别 ----
@pytest.mark.parametrize("text,expected", [
    ("1. x+1=5", 1),
    ("12、回答下列问题", 12),
    ("(3) 计算下面各题", 3),
    ("（5）解方程", 5),
    ("1．应用题", 1),
    ("姓名：张三", None),
    ("一、填空题", None),        # 中文数字可能是章节标题，不作锚点
    ("2024学年第一学期", None),
    ("99. 上限", 99),
    ("100. 超上限", None),
])
def test_parse_qno(text, expected):
    assert qs.parse_qno(text) == expected


# ---- detect_anchors_monotonic：假 OCR 引擎集成测试 ----
def test_detect_anchors_monotonic(monkeypatch):
    """假 OCR 引擎：验证排序 + 递增过滤 + 页眉伪锚点剔除。"""
    tmp = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(tmp, exist_ok=True)
    path = os.path.join(tmp, "g1_white.png")
    Image.new("RGB", (800, 1200), (255, 255, 255)).save(path)

    fake_lines = [
        ([[0, 950, 100, 980]], "5. solve", 0.9),
        ([[0, 110, 100, 140]], "1 / 2", 0.9),     # 页眉伪锚点
        ([[0, 500, 100, 530]], "3. question", 0.9),
    ]
    monkeypatch.setattr(qs, "_get_ocr", lambda: (lambda img: (fake_lines, None)))
    anchors = qs.detect_question_anchors(path)
    # 排序后递增过滤：1(页眉) → 3 → 5，页眉行 "1 / 2" 不匹配题号正则
    assert [a["no"] for a in anchors] == [3, 5]
    assert [a["top"] for a in anchors] == [500, 950]


def test_detect_anchors_unavailable(monkeypatch):
    """无 OCR 引擎 → 空列表，不抛异常。"""
    monkeypatch.setattr(qs, "_get_ocr", lambda: None)
    path = os.path.join(os.path.dirname(__file__), "data", "g1_white.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        Image.new("RGB", (100, 100), (255, 255, 255)).save(path)
    assert qs.detect_question_anchors(path) == []


# ---- regions_from_anchors：锚点重建区域，页眉天然剔除 ----
def test_regions_from_anchors():
    anchors = [{"no": 1, "top": 300}, {"no": 2, "top": 700},
               {"no": 3, "top": 1100}]
    regions = qs.regions_from_anchors(anchors, 1500)
    assert regions == [(300, 700), (700, 1100), (1100, 1500)]  # 页眉 0-300 被剔除


def test_regions_from_anchors_drops_tiny():
    anchors = [{"no": 1, "top": 300}, {"no": 2, "top": 305},  # 5px 噪声题
               {"no": 3, "top": 800}]
    regions = qs.regions_from_anchors(anchors, 1200)
    assert (305, 800) in regions
    assert all(b - t >= qs.MIN_REGION_H for t, b in regions)


def test_regions_from_anchors_single():
    assert qs.regions_from_anchors([{"no": 1, "top": 100}], 800) == []


# ---- _strip_header：单锚点剔除行投影页眉区域 ----
def test_strip_header():
    regions = [(10, 80), (200, 400), (500, 700)]   # (10,80) 是页眉
    out = qs._strip_header(regions, 150)
    assert out == [(200, 400), (500, 700)]


# ---- split_questions 集成：锚点模式产出裁切图，页眉不进任何裁切 ----
@pytest.fixture()
def split_env(tmp_path):
    import backend.database as dbmod
    from backend.database import init_db
    url = "sqlite:///" + str(tmp_path / "t.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()
    import backend.models.grading_engine as ge
    old_qs, old_ge = qs.SessionLocal, getattr(ge, "SessionLocal", None)
    qs.SessionLocal = dbmod.SessionLocal
    if old_ge:
        ge.SessionLocal = dbmod.SessionLocal
    yield tmp_path
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old
    qs.SessionLocal = old_qs
    if old_ge:
        ge.SessionLocal = old_ge


def _make_page(path, n_questions=3):
    """画一张带页眉 + n 道题的模拟作业页（每题一道墨迹横线块）。"""
    W, H = 900, 2400
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((60, 40), "Math Homework Page 1/2", fill=(0, 0, 0))  # 页眉
    for i in range(n_questions):
        top = 400 + i * 600
        d.text((60, top), f"{i + 1}.", fill=(0, 0, 0))
        for r in range(12):  # 手写区：连续墨迹（笔画密集，间距小于投影空白阈值）
            d.rectangle([120, top + 30 + r * 28, 700, top + 52 + r * 28], fill=(40, 40, 40))
    img.save(path)
    return path


def test_split_with_anchor_ocr(split_env, monkeypatch):
    path = _make_page(str(split_env / "page.png"))
    # 假 OCR：题号锚点在 400/1000/1600（模拟识别出题号），页眉文字被正则过滤
    fake_lines = [
        ([[0, 60, 400, 90]], "Math Homework Page 1/2", 0.9),
        ([[0, 420, 100, 450]], "1.", 0.9),
        ([[0, 1020, 100, 1050]], "2.", 0.9),
        ([[0, 1620, 100, 1650]], "3.", 0.9),
    ]
    monkeypatch.setattr(qs, "_get_ocr", lambda: (lambda img: (fake_lines, None)))
    out_dir = str(split_env / "splits")
    crops = qs.split_questions(path, out_dir)
    assert len(crops) == 3
    tops = [c["top"] for c in crops]
    assert all(t > 100 for t in tops), "页眉不得进入任何裁切区域"
    assert crops[0]["bottom"] <= crops[1]["top"] + 20
    assert crops[1]["bottom"] <= crops[2]["top"] + 20
    for c in crops:
        assert os.path.exists(c["path"])


def test_split_header_strip_single_anchor(split_env, monkeypatch):
    path = _make_page(str(split_env / "page.png"), n_questions=2)
    # 单锚点：只剔除页眉以上区域，行投影区域仍用于切分
    monkeypatch.setattr(qs, "_get_ocr",
                        lambda: (lambda img: ([([[0, 430, 100, 460]], "1.", 0.9)], None)))
    out_dir = str(split_env / "splits")
    crops = qs.split_questions(path, out_dir)
    assert crops, "单锚点模式下仍应切出区域"
    assert all(c["top"] >= 400 for c in crops), "页眉以上区域应被剔除"


def test_split_fallback_no_ocr(split_env, monkeypatch):
    """OCR 不可用 → 回退纯行投影，原有行为不变。"""
    path = _make_page(str(split_env / "page.png"))
    monkeypatch.setattr(qs, "_get_ocr", lambda: None)
    out_dir = str(split_env / "splits")
    crops = qs.split_questions(path, out_dir)
    assert len(crops) >= 2


# ---- 遗留①：说明段落误圈剔除 ----
def test_instruction_spans_seed_merge():
    """关键词种子行向上下合并邻近行，成段说明 → 一个竖直区间。"""
    lines = [
        {"text": "1. 计算 3+5", "top": 1000, "bottom": 1040},
        {"text": "注意事项：", "top": 200, "bottom": 240},
        {"text": "答题前请填写姓名", "top": 250, "bottom": 290},
        {"text": "保持卷面整洁", "top": 300, "bottom": 340},
        {"text": "2. 解方程 x+1=2", "top": 1500, "bottom": 1540},
    ]
    lines.sort(key=lambda l: l["top"])
    spans = qs._instruction_spans(lines)
    assert len(spans) == 1
    assert spans[0][0] <= 200 and spans[0][1] >= 340


def test_instruction_spans_far_apart():
    """两处说明相距远 → 两个独立区间。"""
    lines = [
        {"text": "注意事项：", "top": 100, "bottom": 140},
        {"text": "答题要求：写在答题卡上", "top": 1200, "bottom": 1240},
    ]
    spans = qs._instruction_spans(lines)
    assert len(spans) == 2


def test_instruction_spans_no_seed():
    assert qs._instruction_spans([]) == []
    assert qs._instruction_spans([{"text": "3. 计算", "top": 10, "bottom": 50}]) == []


def test_drop_instruction_regions_full_overlap():
    """行投影区域完全落在说明段落内 → 剔除；真实题目区域 → 保留。"""
    spans = [(200, 400)]
    regions = [(210, 390), (600, 900)]  # (210,390) 完全落在说明段落内
    out = qs._drop_instruction_regions(regions, spans, coords_scale=1.0)
    assert out == [(600, 900)]


def test_drop_instruction_regions_partial_kept():
    """说明只占题目区域一小部分 → 不剔除（只删不裁，防误伤）。"""
    spans = [(200, 260)]
    regions = [(150, 500)]
    assert qs._drop_instruction_regions(regions, spans, coords_scale=1.0) == \
        [(150, 500)]


def test_drop_instruction_regions_projection_scale():
    """行投影坐标换算：投影行号 / scale = 原图坐标。"""
    spans = [(200, 400)]  # 原图坐标
    regions = [(166, 330), (600, 900)]  # 投影行号，scale=0.5 → (332,660) 原图
    out = qs._drop_instruction_regions(regions, spans, coords_scale=2.0)
    # (166,330)→原图(332,660)，与说明段 200-400 重叠 68/328 <80% 保留？不，
    # 重叠 200-332=132，区域高 328，比例 40% <80% → 保留
    assert (166, 330) in out
    # 构造真正命中的：投影 (90,210)→原图(180,420)，与 200-400 重叠 220/240
    out2 = qs._drop_instruction_regions([(90, 210)], spans, coords_scale=2.0)
    assert out2 == []


def _make_page_with_instructions(path):
    """页首放说明段落墨迹块 + 两道题的手写块。"""
    W, H = 900, 2400
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # 说明段落：三行密集墨迹（150-330）
    for y in (150, 210, 270):
        d.rectangle([60, y, 700, y + 45], fill=(40, 40, 40))
    # 两道题：600-900 / 1300-1600
    for top in (600, 1300):
        d.text((60, top), "1.", fill=(0, 0, 0))
        for r in range(10):
            d.rectangle([120, top + 30 + r * 26, 700, top + 50 + r * 26],
                        fill=(40, 40, 40))
    img.save(path)
    return path


def test_split_drops_instruction_block(split_env, monkeypatch):
    """无锚点（行投影兜底）时，说明段落块被剔除，不出现在裁切结果。"""
    path = _make_page_with_instructions(str(split_env / "page2.png"))
    fake_lines = [
        ([[0, 150, 400, 190]], "注意事项：", 0.9),
        ([[0, 200, 400, 240]], "答题前请填写姓名", 0.9),
        ([[0, 250, 400, 290]], "保持卷面整洁", 0.9),
    ]
    monkeypatch.setattr(qs, "_get_ocr", lambda: (lambda img: (fake_lines, None)))
    crops = qs.split_questions(path, str(split_env / "splits2"))
    assert crops, "题目区域仍应切出"
    assert all(c["top"] > 400 for c in crops), \
        "说明段落（y<400）不得出现在任何裁切区域"
    assert len(crops) == 2


def test_split_anchor_mode_keeps_instruction_inside_region(split_env, monkeypatch):
    """锚点模式下说明夹在两题之间，区域只含部分说明 → 保留，不误删题目。"""
    path = _make_page_with_instructions(str(split_env / "page3.png"))
    # 说明行只覆盖 150-330，题1锚点在 600、题2在 1300（说明整体在题1区域内）
    fake_lines = [
        ([[0, 150, 400, 190]], "注意事项：", 0.9),
        ([[0, 620, 100, 650]], "1.", 0.9),
        ([[0, 1320, 100, 1350]], "2.", 0.9),
    ]
    monkeypatch.setattr(qs, "_get_ocr", lambda: (lambda img: (fake_lines, None)))
    crops = qs.split_questions(path, str(split_env / "splits3"))
    assert len(crops) == 2
    tops = [c["top"] for c in crops]
    assert tops[0] <= 650 and tops[1] >= 1300


def test_drop_instruction_regions_cluster():
    """同一段说明被行投影切成多个子区域 → 聚簇后按整簇计算，整簇剔除。"""
    spans = [(150, 290)]  # 原图坐标
    # scale=0.8333，coords_scale=1.2：三个子区域 orig ≈(150,196)(210,256)(270,316)
    regions = [(125, 163), (175, 213), (225, 263), (600, 900)]
    out = qs._drop_instruction_regions(regions, spans, coords_scale=1.2)
    # 关键断言：说明簇三个子区域全部被剔除，题目区域保留
    assert out == [(600, 900)]
