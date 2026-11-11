"""低置信度二次复核（PRD 待办 f）

confidence 低于阈值的题目，自动用第二个通道交叉验证：
- mode=resample：同模型重采样再批一次（无需备用厂商，成本只在低置信度题上多一次调用）
- mode=fallback：用已配置的备用 provider 复核（未配置则自动退回 resample）

复核一致 → 采纳主判定，标记 review=agree；
复核不一致 → 不擅自改判，标黄 needs_review=True 进人工复核队列（准确性第一：规则/模型都拿不准的交老师）。
复核通道失败 → 保持原结果，不阻塞批改。
"""

from typing import Any, Dict, List, Optional

from .grading_engine import GradingEngine, get_llm_config, get_fallback_config
from .rule_validator import answers_equivalent
from ..database import AppConfig, SessionLocal

DEFAULT_THRESHOLD = 0.7


def get_review_config() -> Dict[str, Any]:
    """读取复核配置：enabled / threshold / mode，存 AppConfig"""
    db = SessionLocal()
    vals = {}
    try:
        rows = db.query(AppConfig).filter(AppConfig.key.in_(
            ["review_enabled", "review_threshold", "review_mode"])).all()
        vals = {r.key: r.value for r in rows}
    finally:
        db.close()
    enabled = str(vals.get("review_enabled", "true")).lower() == "true"
    try:
        threshold = float(vals.get("review_threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        threshold = DEFAULT_THRESHOLD
    threshold = min(0.95, max(0.3, threshold))
    mode = vals.get("review_mode", "resample")
    if mode not in ("resample", "fallback"):
        mode = "resample"
    return {"enabled": enabled, "threshold": threshold, "mode": mode}


def save_review_config(enabled: bool, threshold: float, mode: str) -> None:
    db = SessionLocal()
    try:
        updates = {"review_enabled": "true" if enabled else "false",
                   "review_threshold": str(threshold), "review_mode": mode}
        for key, value in updates.items():
            row = db.query(AppConfig).filter(AppConfig.key == key).first()
            if row:
                row.value = value
            else:
                db.add(AppConfig(key=key, value=value))
        db.commit()
    finally:
        db.close()


def _agree(primary: Dict[str, Any], secondary: Dict[str, Any]) -> bool:
    """两通道对同一题的结论是否一致：判定相同且答案可证明等价（或规则无法证伪）"""
    if str(primary.get("judgement", "")).lower() != str(secondary.get("judgement", "")).lower():
        return False
    eq = answers_equivalent(primary.get("student_answer"), secondary.get("student_answer"))
    return eq is not False


async def _second_pass(image_path: str, question_type: str, subject: str,
                       answers: Optional[Dict[str, str]], mode: str) -> Optional[Dict[str, Any]]:
    """执行第二通道批改，返回 {question_no: result}；失败返回 None"""
    fb = get_fallback_config()
    if mode == "fallback" and fb.get("provider") and fb.get("api_key") \
            and fb.get("base_url") and fb.get("model"):
        engine = GradingEngine(fb["api_key"], fb["model"], fb["base_url"])
    else:
        cfg = get_llm_config()
        if not cfg.get("api_key"):
            return None
        engine = GradingEngine(cfg["api_key"], cfg["model"], cfg["base_url"])
    result = await engine.grade(image_path, question_type, subject, answers=answers)
    if not result.get("success"):
        return None
    return {str(q.get("question_no")): q for q in result.get("questions", [])
            if q.get("question_no") is not None}


async def cross_check_questions(result: Dict[str, Any], image_path: str,
                                question_type: str, subject: str,
                                answers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """就地对低置信度题目做二次复核，返回统计。

    只复核 confidence < threshold 的题；复核不一致的题标 needs_review 并降置信度。
    """
    cfg = get_review_config()
    stats = {"enabled": cfg["enabled"], "reviewed": 0, "agree": 0, "conflict": 0, "skipped": 0}
    if not cfg["enabled"] or not result.get("success"):
        return stats
    questions = result.get("questions") or []
    low = [q for q in questions
           if isinstance(q.get("confidence"), (int, float)) and q["confidence"] < cfg["threshold"]]
    if not low:
        return stats

    secondary = await _second_pass(image_path, question_type, subject, answers, cfg["mode"])
    if secondary is None:
        stats["skipped"] = len(low)
        return stats

    for q in low:
        sec = secondary.get(str(q.get("question_no")))
        if sec is None:
            stats["skipped"] += 1
            continue
        stats["reviewed"] += 1
        if _agree(q, sec):
            q["review"] = "agree"
            # 两通道一致，置信度适度上调（上限仍低于阈值，避免掩盖不确定性）
            q["confidence"] = round(min(q["confidence"] + 0.1, cfg["threshold"] - 0.01), 2)
            stats["agree"] += 1
        else:
            q["review"] = "conflict"
            q["needs_review"] = True
            q["confidence"] = min(q["confidence"], 0.55)
            q["review_note"] = "二次复核结论不一致（复核判定: %s / 学生答案: %s），请老师人工确认" % (
                sec.get("judgement"), sec.get("student_answer"))
            stats["conflict"] += 1
    return stats
