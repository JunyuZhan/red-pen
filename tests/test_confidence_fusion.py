# -*- coding: utf-8 -*-
"""PRD 待办 bb：置信度融合第四因子（知识点历史准确率）"""
import os
import tempfile

import pytest

from backend.models.confidence_fusion import (
    fuse_knowledge_history, knowledge_point_history)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def db():
    """独立临时 SQLite 库"""
    import backend.database as dbmod
    tmp = tempfile.mkdtemp(prefix="rp_fusion_")
    url = "sqlite:///" + os.path.join(tmp, "test_fusion.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    dbmod.Base.metadata.create_all(dbmod.engine)
    s = dbmod.SessionLocal()
    yield s
    s.close()
    dbmod.engine, dbmod.SessionLocal = old


def _seed(db, kp, judgement, submission_id=1, override=None):
    from backend.database import QuestionResult
    db.add(QuestionResult(
        submission_id=submission_id, question_no=1, question_type="default",
        knowledge_point=kp, judgement_raw=judgement, judgement_final=judgement,
        confidence_final=0.8, teacher_override=override))
    db.commit()


# ---------- knowledge_point_history ----------

def test_history_counts_correct_wrong(db):
    for _ in range(7):
        _seed(db, "两位数乘法", "correct")
    for _ in range(3):
        _seed(db, "两位数乘法", "wrong")
    h = knowledge_point_history(db, exclude_submission_id=-1)
    assert h["两位数乘法"] == {"correct": 7, "total": 10}


def test_history_teacher_override_wins(db):
    _seed(db, "进位加法", "wrong", override="correct")
    h = knowledge_point_history(db, exclude_submission_id=-1)
    assert h["进位加法"] == {"correct": 1, "total": 1}


def test_history_excludes_current_submission(db):
    _seed(db, "拼音声调", "correct", submission_id=42)
    assert knowledge_point_history(db, exclude_submission_id=42) == {}
    assert knowledge_point_history(db, exclude_submission_id=1)["拼音声调"]["total"] == 1


def test_history_skips_unresolved_needs_review(db):
    for _ in range(3):
        _seed(db, "分数化简", "correct")
    from backend.database import QuestionResult
    db.add(QuestionResult(submission_id=1, question_no=2, question_type="default",
                          knowledge_point="分数化简", judgement_raw="correct",
                          judgement_final=None, confidence_final=0.5, needs_review=1))
    db.commit()
    h = knowledge_point_history(db, exclude_submission_id=-1)
    assert h["分数化简"]["total"] == 3


# ---------- fuse_knowledge_history ----------

def test_low_accuracy_kp_lowers_confidence(db):
    for _ in range(2):
        _seed(db, "应用题", "correct")
    for _ in range(6):
        _seed(db, "应用题", "wrong")  # 正确率 2/8 = 25%
    qs = [{"question_no": 1, "confidence": 0.8, "knowledge_point": "应用题"}]
    stats = fuse_knowledge_history(qs, db, submission_id=-1)
    assert qs[0]["confidence"] == pytest.approx(0.70)
    assert "置信度已下调" in qs[0]["confidence_note"]
    assert stats["lowered"] == 1


def test_high_accuracy_kp_raises_confidence(db):
    for _ in range(9):
        _seed(db, "口算", "correct")
    qs = [{"question_no": 1, "confidence": 0.8, "knowledge_point": "口算"}]
    stats = fuse_knowledge_history(qs, db, submission_id=-1)
    assert qs[0]["confidence"] == pytest.approx(0.85)
    assert stats["raised"] == 1


def test_small_sample_kp_untouched(db):
    for _ in range(2):
        _seed(db, "新知识点", "correct")
    qs = [{"question_no": 1, "confidence": 0.8, "knowledge_point": "新知识点"}]
    stats = fuse_knowledge_history(qs, db, submission_id=-1)
    assert qs[0]["confidence"] == 0.8
    assert stats["no_history"] == 1


def test_no_knowledge_point_untouched(db):
    for _ in range(9):
        _seed(db, "口算", "correct")
    qs = [{"question_no": 1, "confidence": 0.8, "knowledge_point": None}]
    stats = fuse_knowledge_history(qs, db, submission_id=-1)
    assert qs[0]["confidence"] == 0.8
    assert stats["no_history"] == 1


def test_confidence_clamped_not_above_0_95(db):
    for _ in range(9):
        _seed(db, "口算", "correct")
    qs = [{"question_no": 1, "confidence": 0.94, "knowledge_point": "口算"}]
    fuse_knowledge_history(qs, db, submission_id=-1)
    assert qs[0]["confidence"] == pytest.approx(0.95)


def test_floor_at_0_05(db):
    for _ in range(6):
        _seed(db, "应用题", "wrong")
    qs = [{"question_no": 1, "confidence": 0.1, "knowledge_point": "应用题"}]
    fuse_knowledge_history(qs, db, submission_id=-1)
    assert qs[0]["confidence"] == pytest.approx(0.05)


def test_invalid_confidence_ignored(db):
    for _ in range(9):
        _seed(db, "口算", "correct")
    qs = [{"question_no": 1, "confidence": None, "knowledge_point": "口算"},
          {"question_no": 2, "confidence": 1.5, "knowledge_point": "口算"}]
    stats = fuse_knowledge_history(qs, db, submission_id=-1)
    assert qs[0]["confidence"] is None
    assert qs[1]["confidence"] == 1.5
    assert stats["considered"] == 0
