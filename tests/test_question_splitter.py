# -*- coding: utf-8 -*-
"""PRD 待办 g：CV 切题 + 裁切放大 测试 — pytest 原生版

覆盖：区域切分 / 单块图不切 / EXIF 旋转校正 / 裁切放大 /
配置读写 / 开关关闭不触发切分 / 切题失败回退整页 / 损坏图回退。
"""
import asyncio
import os
import sys
import tempfile

import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def split_env():
    """独立临时库 + 临时输出目录，结束恢复原状。"""
    import backend.database as dbmod
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_split_")
    url = "sqlite:///" + os.path.join(tmp, "test_split.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()
    # 下游模块在 import 时绑定了 SessionLocal，需一并替换才真正隔离
    import backend.models.grading_engine as _ge
    import backend.models.question_splitter as _qs
    old_bound = {}
    for mod in (_ge, _qs):
        if hasattr(mod, "SessionLocal"):
            old_bound[mod.__name__] = mod.SessionLocal
            mod.SessionLocal = dbmod.SessionLocal
    yield tmp
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old
    for name, sm in old_bound.items():
        sys.modules[name].SessionLocal = sm


def make_page(path, lines=5, line_h=60, w=800):
    """白底黑字：lines 行墨迹，行间大空白。"""
    h = lines * line_h * 2
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for i in range(lines):
        y = i * line_h * 2 + 20
        d.rectangle([40, y, w - 40, y + line_h - 20], fill=30)
    img.save(path)
    return img


def test_multi_line_page_splits_into_regions(split_env):
    from backend.models.question_splitter import split_questions

    page = os.path.join(split_env, "page.jpg")
    make_page(page, lines=5)
    crops = split_questions(page, os.path.join(split_env, "out"))
    assert len(crops) >= 2
    orig_h = Image.open(page).size[1]
    for c in crops:
        assert os.path.exists(c["path"])
        assert Image.open(c["path"]).size[1] < orig_h


def test_single_line_zoom_or_skip(split_env):
    """单行小字：要么放大成单题裁切，要么不强切。"""
    from backend.models.question_splitter import split_questions

    single = os.path.join(split_env, "one.jpg")
    make_page(single, lines=1)
    c1 = split_questions(single, os.path.join(split_env, "out1"))
    if len(c1) == 1:
        assert Image.open(c1[0]["path"]).size[1] >= 40
    else:
        assert c1 == []


def test_exif_rotated_image_still_splits(split_env):
    from backend.models.question_splitter import split_questions

    src = make_page(os.path.join(split_env, "rot_src.jpg"), lines=4)
    rot = src.rotate(-90, expand=True)
    rot.save(os.path.join(split_env, "rot_tmp.jpg"))
    exif = Image.open(os.path.join(split_env, "rot_tmp.jpg")).getexif()
    exif[274] = 6  # Orientation: 需顺时针转90°才是正向
    rot.save(os.path.join(split_env, "rot.jpg"), exif=exif.tobytes())
    crops = split_questions(os.path.join(split_env, "rot.jpg"),
                            os.path.join(split_env, "out2"))
    assert len(crops) >= 2


def test_row_projection_groups_five_lines(split_env):
    from backend.models.question_splitter import _find_regions, _row_means

    img = make_page(os.path.join(split_env, "proj.jpg"), lines=5)
    assert len(_find_regions(_row_means(img.convert("L")))) == 5


def test_split_config_roundtrip(split_env):
    from backend.models.question_splitter import get_split_config, save_split_config

    save_split_config(True)
    assert get_split_config()["enabled"] is True
    save_split_config(False)
    assert get_split_config()["enabled"] is False


def test_corrupt_image_returns_empty(split_env):
    from backend.models.question_splitter import split_questions

    bad = os.path.join(split_env, "bad.jpg")
    with open(bad, "wb") as f:
        f.write(b"not an image")
    assert split_questions(bad, os.path.join(split_env, "out3")) == []


class _FakeEngine:
    def __init__(self, *a, **k):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        self.calls = getattr(self, "calls", [])
        return {"success": True, "questions": []}


def _configure_mock_llm():
    from backend.database import AppConfig, SessionLocal

    db = SessionLocal()
    for k, v in [("llm_provider", "custom"), ("llm_base_url", "http://mock/v1"),
                 ("llm_model", "mock-vl"), ("llm_api_key", "sk-mock")]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(AppConfig(key=k, value=v))
    db.commit()
    db.close()


def test_integration_split_mode_and_fallback(split_env):
    """切题模式逐区域调用并合并；全失败不崩溃；关闭开关只整页调用 1 次。"""
    from backend.models import grading_engine as ge
    from backend.models.question_splitter import save_split_config

    _configure_mock_llm()
    calls = []

    class FakeEngine:
        def __init__(self, *a, **k):
            pass

        async def grade(self, image_path, question_type, subject, answers=None):
            calls.append(image_path)
            return {"success": True, "questions": [
                {"no": len(calls), "judgement": "correct", "confidence": 0.9}]}

    orig_engine = ge.GradingEngine
    ge.GradingEngine = FakeEngine
    try:
        save_split_config(True)
        page2 = os.path.join(split_env, "page2.jpg")
        make_page(page2, lines=4)
        mgr = ge.GradingManager()
        res = asyncio.run(mgr.grade(page2, "default", "数学"))
        assert len(calls) >= 2
        assert res.get("success") and res.get("split_used") is True
        assert len(res.get("questions", [])) == len(calls)

        class FailEngine(FakeEngine):
            async def grade(self, *a, **k):
                return {"success": False, "error": "mock fail"}

        calls.clear()
        ge.GradingEngine = FailEngine
        res2 = asyncio.run(mgr.grade(page2, "default", "数学"))
        assert isinstance(res2, dict)

        save_split_config(False)
        calls.clear()
        ge.GradingEngine = FakeEngine
        asyncio.run(mgr.grade(page2, "default", "数学"))
        assert len(calls) == 1
    finally:
        ge.GradingEngine = orig_engine
