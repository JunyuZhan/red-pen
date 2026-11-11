"""
月度预算护栏 + 价格预设表测试（PRD 待办 j）
运行：cd ~/red-pen && PYTHONPATH=. python3 tests/test_budget.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta

from backend.database import init_db, SessionLocal, Submission, AppConfig
from backend.models.budget import (get_budget_config, save_budget_config,
                                   get_monthly_spent, check_budget)
from backend.models.grading_engine import MODEL_PRICE_PRESETS

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + " - " + name + ((" :: " + str(detail)) if detail and not cond else ""))


def main():
    init_db()
    db = SessionLocal()
    # 清理干扰数据（仅测试库的 submission/config）
    db.query(Submission).delete()
    db.query(AppConfig).filter(AppConfig.key.in_(
        ["monthly_budget", "budget_action"])).delete(synchronize_session=False)
    db.commit()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(Submission(assignment_id=1, student_id=1, original_image_path="/tmp/a.jpg",
                      cost_estimate=1.5, created_at=now))
    db.add(Submission(assignment_id=1, student_id=2, original_image_path="/tmp/b.jpg",
                      cost_estimate=0.5, created_at=now))
    db.add(Submission(assignment_id=1, student_id=3, original_image_path="/tmp/c.jpg",
                      cost_estimate=99.0, created_at=now - timedelta(days=45)))  # 上月
    db.commit()
    db.close()

    # 1) 本月花费只累计本月记录（上月 99 元不计入）
    spent = get_monthly_spent(now)
    check("get_monthly_spent 只算本月", abs(spent - 2.0) < 1e-6, spent)

    # 2) 默认预算 0 = 不限，不超
    b = check_budget(now)
    check("默认预算0=不限", b["monthly_budget"] == 0 and b["exceeded"] is False and b["percent"] is None, b)

    # 3) 设预算 1 元，本月已用 2 元 → exceeded
    save_budget_config(1.0, "warn")
    b = check_budget(now)
    check("超预算检测", b["exceeded"] and b["percent"] == 200.0 and b["remaining"] == -1.0, b)

    # 4) warn 模式不阻止（由调用方决定），block 模式返回可判断 exceeded
    save_budget_config(5.0, "block")
    b = check_budget(now)
    check("block模式未超不阻止", b["exceeded"] is False and b["budget_action"] == "block", b)

    # 5) 配置读取/保存往返
    cfg = get_budget_config()
    check("预算配置往返", cfg == {"monthly_budget": 5.0, "budget_action": "block"}, cfg)

    # 6) 价格预设表：包含推荐清单全部模型，单价非负且免费模型为 0
    models = {p["model"] for p in MODEL_PRICE_PRESETS}
    expected = {"qwen-vl-plus", "qwen-vl-max", "qwen3-vl-plus", "glm-4v-flash", "glm-4v-plus"}
    check("预设覆盖推荐模型", expected <= models, models)
    check("预设单价合法", all(p["input"] >= 0 and p["output"] >= 0 for p in MODEL_PRICE_PRESETS))
    flash = next(p for p in MODEL_PRICE_PRESETS if p["model"] == "glm-4v-flash")
    check("glm-4v-flash 免费", flash["input"] == 0 and flash["output"] == 0)

    # 7) 默认单价修正：qwen-vl-plus 每百万 0.8/2 元 → 每1K 0.0008/0.002
    from backend.models.grading_engine import get_cost_config
    c = get_cost_config()
    check("默认单价为官方价量级", abs(c["input"] - 0.0008) < 1e-9 and abs(c["output"] - 0.002) < 1e-9, c)
    est = __import__("backend.models.grading_engine", fromlist=["estimate_cost"]).estimate_cost(1000, 1000)
    check("估算费用随新单价", abs(est["cost"] - 0.0028) < 1e-6, est)

    # 清理
    db = SessionLocal()
    db.query(Submission).delete()
    db.query(AppConfig).filter(AppConfig.key.in_(
        ["monthly_budget", "budget_action"])).delete(synchronize_session=False)
    db.commit()
    db.close()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
