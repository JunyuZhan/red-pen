# -*- coding: utf-8 -*-
"""回归修复：bbox 全 null / 标注图无标记（v0.5.12/13 hw1 复测）。
两根因：
1) 逐题结果入库发生在 CV 切题覆盖 bbox 之前 → DB bbox_answer 永远是引擎原始值；
2) 去重发生在切题之后 → 引擎返回重复题号时 CV 按错误题数找区域，一条 bbox 都对不上。
本测试驱动真实批改流程，断言 DB bbox_answer 已被 CV 区域覆盖。
"""
import asyncio
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image


class DupEngine:
    """返回重复题号条目（模拟引擎冗余），bbox 全 None"""

    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        qs = []
        for i in range(1, 4):
            qs.append({"question_no": i, "judgement": "correct",
                       "student_answer": f"ans{i}", "confidence": 0.9,
                       "bbox": None, "steps": None})
        qs.append({"question_no": 3, "judgement": "wrong",
                   "student_answer": "未作答", "confidence": 0.5,
                   "bbox": None, "steps": None})  # 冗余条目
        return {"success": True, "analysis": "OK", "questions": qs,
                "prompt_tokens": 10, "completion_tokens": 5}


@pytest.fixture()
def bbox_env(monkeypatch):
    import backend.database as dbmod
    import backend.models.grading_engine as ge
    import backend.models.question_splitter as qsplit
    import backend.routes.grading as gr
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_bbox_")
    url = "sqlite:///" + os.path.join(tmp, "test_bbox.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()

    for mod in (ge, qsplit, gr):
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", dbmod.SessionLocal)
        if getattr(getattr(mod, "AppConfig", None), "__module__", "") == "backend.database":
            monkeypatch.setattr(mod, "AppConfig", dbmod.AppConfig)

    monkeypatch.setattr(ge, "GradingEngine", DupEngine)
    monkeypatch.setattr(gr, "_sem", None, raising=False)
    monkeypatch.setattr(gr, "QUEUE", {}, raising=False)

    # 测试图：8x600 长条，让 CV 切带可运行
    img_path = os.path.join(tmp, "page.jpg")
    Image.new("RGB", (80, 600), (245, 245, 245)).save(img_path)

    from backend.database import SessionLocal, AppConfig
    db = SessionLocal()
    for k, v in [("llm_provider", "qwen"), ("llm_api_key", "test-key")]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(AppConfig(key=k, value=v))
    db.commit()
    db.close()

    # 模拟 CV 切题：3 个区域（与去重后题数一致）
    regions = [{"top": 0, "bottom": 200}, {"top": 200, "bottom": 400},
               {"top": 400, "bottom": 600}]
    monkeypatch.setattr(qsplit, "split_questions", lambda p, d: regions)
    monkeypatch.setattr(qsplit, "find_bands", lambda p, n: regions[:n])
    monkeypatch.setattr(qsplit, "tighten_band_to_ink", lambda p, t, b: [0, t, 80, b])
    # 标注图真实输出到临时目录
    monkeypatch.setattr(gr, "annotate_image", lambda *a, **k: None)

    # 建 submission 记录
    from backend.database import SessionLocal
    from backend.database import Submission, QuestionResult
    db = SessionLocal()
    sub = Submission(assignment_id=0, student_id=0,
                     original_image_path=img_path, status="queued")
    db.add(sub)
    db.commit()
    sid = sub.id
    db.close()
    yield sid, tmp
    dbmod.engine, dbmod.SessionLocal = old


def test_bbox_saved_after_cv_override(bbox_env):
    sid, tmp = bbox_env
    import backend.routes.grading as gr
    stats = asyncio.run(gr._grade_one(sid))
    assert stats.get("success"), stats

    from backend.database import SessionLocal, QuestionResult
    db = SessionLocal()
    rows = db.query(QuestionResult).filter_by(submission_id=sid).all()
    db.close()
    assert len(rows) == 3  # 去重后 3 题（冗余第4条已剔除）
    for r in rows:
        assert r.bbox_answer, f"question {r.question_no} bbox_answer 仍为空: {r.bbox_answer}"
        assert r.bbox_answer[1] is not None and r.bbox_answer[3] > r.bbox_answer[1]
