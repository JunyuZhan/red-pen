"""置信度融合第四因子：知识点历史准确率（PRD 准确性章节，待办 bb）

同一知识点的历史判对率是稳定先验：
- 该知识点历史正确率低（学生易错/模型难读）→ 当前置信度下调，
  更容易落入低置信度复核通道，准确性第一；
- 历史正确率高 → 适度上调，减少不必要的复核调用（成本敏感）。

约束（与既有设计一致）：
- 只有历史样本足够（>= MIN_SAMPLES）的知识点才参与融合，避免小样本噪声；
- 置信度夹在 [0.05, 0.95]，不允许直接推到 1.0 掩盖不确定性；
- 任何异常不阻塞批改（调用方兜底）。
"""

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from ..database import QuestionResult
from .knowledge_norm import canonical_key

MIN_SAMPLES = 5          # 该知识点至少累计 N 条有效判定才启用历史因子
LOW_ACC = 0.5            # 低于此正确率 → 下调置信度
HIGH_ACC = 0.9           # 高于此正确率 → 上调置信度
LOW_ADJUST = -0.10
HIGH_ADJUST = 0.05
CONF_MIN = 0.05
CONF_MAX = 0.95


def knowledge_point_history(db: Session, exclude_submission_id: int) -> Dict[str, Dict[str, int]]:
    """统计各知识点的历史判对情况，排除当前提交（避免自我污染）。

    判定取 teacher_override（老师改判最权威），否则 judgement_final；
    只统计明确的 correct/wrong（needs_review 的未决样本不计入）。
    返回 {知识点名: {"correct": n, "total": n}}
    """
    rows = (db.query(QuestionResult)
            .filter(QuestionResult.submission_id != exclude_submission_id,
                    QuestionResult.knowledge_point.isnot(None),
                    QuestionResult.knowledge_point != "")
            .all())
    stats: Dict[str, Dict[str, int]] = {}
    for r in rows:
        judgement = (r.teacher_override or r.judgement_final or "").lower()
        if judgement not in ("correct", "wrong"):
            continue
        name = canonical_key(r.knowledge_point)
        if not name:
            continue
        s = stats.setdefault(name, {"correct": 0, "total": 0})
        s["total"] += 1
        if judgement == "correct":
            s["correct"] += 1
    return stats


def fuse_knowledge_history(questions: List[Dict[str, Any]], db: Session,
                           submission_id: int) -> Dict[str, Any]:
    """就地在 questions 上按知识点历史准确率调整 confidence，返回统计。"""
    stats = {"considered": 0, "lowered": 0, "raised": 0, "no_history": 0}
    history = knowledge_point_history(db, exclude_submission_id=submission_id)
    if not history:
        return stats
    for q in questions:
        conf = q.get("confidence")
        if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
            continue
        name = canonical_key(q.get("knowledge_point"))
        h = history.get(name)
        if not h or h["total"] < MIN_SAMPLES:
            stats["no_history"] += 1
            continue
        stats["considered"] += 1
        acc = h["correct"] / h["total"]
        if acc < LOW_ACC:
            new = max(round(conf + LOW_ADJUST, 2), CONF_MIN)
            if new < conf:
                q["confidence"] = new
                q["confidence_note"] = (
                    f"知识点「{name}」历史正确率 {acc:.0%}，置信度已下调")
                stats["lowered"] += 1
        elif acc >= HIGH_ACC:
            new = min(round(conf + HIGH_ADJUST, 2), CONF_MAX)
            if new > conf:
                q["confidence"] = new
                q["confidence_note"] = (
                    f"知识点「{name}」历史正确率 {acc:.0%}，置信度已上调")
                stats["raised"] += 1
    return stats
