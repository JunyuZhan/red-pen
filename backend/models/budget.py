"""
月度预算护栏（PRD 待办 j）：
- 老师设置每月预算上限（元），0 = 不限
- 超预算时按配置 warn（只提示）/ block（停止批改新任务）处理
- 本月花费按 Submission.cost_estimate 累计（UTC 自然月）
"""

from datetime import datetime, timezone
from typing import Any, Dict

from .. import database as _db  # 调用期取 SessionLocal：惰性 import 时不能快照旧引擎
from ..database import AppConfig, Submission

BUDGET_KEYS = ("monthly_budget", "budget_action")


def get_budget_config() -> Dict[str, Any]:
    db = _db.SessionLocal()
    try:
        rows = db.query(AppConfig).filter(AppConfig.key.in_(BUDGET_KEYS)).all()
        stored = {r.key: r.value for r in rows}
    finally:
        db.close()
    try:
        budget = float(stored.get("monthly_budget", 0) or 0)
    except ValueError:
        budget = 0.0
    action = stored.get("budget_action", "warn")
    if action not in ("warn", "block"):
        action = "warn"
    return {"monthly_budget": budget, "budget_action": action}


def save_budget_config(monthly_budget: float, budget_action: str) -> None:
    db = _db.SessionLocal()
    try:
        for key, value in [("monthly_budget", str(round(monthly_budget, 2))),
                           ("budget_action", budget_action)]:
            row = db.query(AppConfig).filter(AppConfig.key == key).first()
            if row:
                row.value = value
            else:
                db.add(AppConfig(key=key, value=value))
        db.commit()
    finally:
        db.close()


def get_monthly_spent(now: datetime = None) -> float:
    """本月（UTC 自然月）已批改花费合计（元）"""
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0,
                              tzinfo=None)
    db = _db.SessionLocal()
    try:
        rows = db.query(Submission.cost_estimate).filter(
            Submission.created_at >= month_start).all()
    finally:
        db.close()
    return round(sum((r[0] or 0.0) for r in rows), 4)


def check_budget(now: datetime = None) -> Dict[str, Any]:
    """返回本月花费 vs 预算状态；exceeded=True 且 action=block 时应阻止新批改"""
    cfg = get_budget_config()
    spent = get_monthly_spent(now)
    budget = cfg["monthly_budget"]
    return {
        "monthly_budget": budget,
        "budget_action": cfg["budget_action"],
        "spent": spent,
        "remaining": round(budget - spent, 4) if budget > 0 else None,
        "exceeded": budget > 0 and spent >= budget,
        "percent": round(spent / budget * 100, 1) if budget > 0 else None,
    }
