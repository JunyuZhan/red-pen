"""
作业等级换算（PRD 待办 n）：作业/学生维度正确率 → 等级（A/B/C/D）。
阈值可在设置页配置，默认 A≥90 / B≥75 / C≥60 / 其余 D。
判定以 judgement_final 为准（教师复核修正后自动跟随）。
"""

import json
from typing import Optional

from ..database import AppConfig

CONFIG_KEY = "grade_thresholds"
DEFAULT_THRESHOLDS = {"A": 90, "B": 75, "C": 60}  # D 为兜底档，低于 C 阈值即 D


def _validate(thresholds) -> Optional[dict]:
    """校验阈值字典：A/B/C 为 0-100 整数且严格递减。非法返回 None。"""
    try:
        vals = {k: int(thresholds[k]) for k in ("A", "B", "C")}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(0 <= v <= 100 for v in vals.values()):
        return None
    if not (vals["A"] > vals["B"] > vals["C"]):
        return None
    return vals


def get_thresholds(db) -> dict:
    """读取阈值配置（AppConfig），无配置或非法时回退默认值。"""
    row = db.query(AppConfig).filter(AppConfig.key == CONFIG_KEY).first()
    if row:
        try:
            vals = _validate(json.loads(row.value))
            if vals:
                return vals
        except (ValueError, TypeError):
            pass
    return dict(DEFAULT_THRESHOLDS)


def save_thresholds(db, thresholds: dict) -> dict:
    """保存阈值（先校验），返回生效的阈值。非法抛 ValueError。"""
    vals = _validate(thresholds)
    if not vals:
        raise ValueError("阈值须为 0-100 整数且满足 A > B > C")
    row = db.query(AppConfig).filter(AppConfig.key == CONFIG_KEY).first()
    if row:
        row.value = json.dumps(vals)
    else:
        db.add(AppConfig(key=CONFIG_KEY, value=json.dumps(vals)))
    db.commit()
    return vals


def compute_grade(accuracy, thresholds: dict) -> Optional[str]:
    """正确率(0-1) → 等级。accuracy 为 None 或越界返回 None。"""
    if accuracy is None:
        return None
    if not (0 <= accuracy <= 1):
        return None
    pct = accuracy * 100
    if pct >= thresholds["A"]:
        return "A"
    if pct >= thresholds["B"]:
        return "B"
    if pct >= thresholds["C"]:
        return "C"
    return "D"


def accuracy_for_submission(db, submission_id: int):
    """单份提交的正确率（基于 judgement_final）。无题或全为非判定返回 None。"""
    from ..database import QuestionResult

    rows = db.query(QuestionResult).filter(QuestionResult.submission_id == submission_id).all()
    judged = [r for r in rows if r.judgement_final in ("correct", "wrong", "partial")]
    if not judged:
        return None
    correct = sum(1 for r in judged if r.judgement_final == "correct")
    return correct / len(judged)


def accuracy_for_assignment(db, assignment_id: int):
    """作业维度正确率：该作业全部提交的题目合并统计。"""
    from ..database import QuestionResult, Submission

    sub_ids = [s.id for s in db.query(Submission.id).filter(Submission.assignment_id == assignment_id)]
    if not sub_ids:
        return None, 0
    rows = db.query(QuestionResult).filter(QuestionResult.submission_id.in_(sub_ids)).all()
    judged = [r for r in rows if r.judgement_final in ("correct", "wrong", "partial")]
    if not judged:
        return None, 0
    correct = sum(1 for r in judged if r.judgement_final == "correct")
    return correct / len(judged), len(judged)
