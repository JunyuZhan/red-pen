# -*- coding: utf-8 -*-
"""月度预算护栏 + 价格预设表测试（PRD 待办 j）— pytest 原生版"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def budget_env():
    """独立临时库，结束恢复原状（模块级函数直接用 backend.database.SessionLocal）。"""
    import backend.database as dbmod
    from backend.database import init_db

    tmp = tempfile.mkdtemp(prefix="rp_budget_")
    url = "sqlite:///" + os.path.join(tmp, "test_budget.db")
    old = (dbmod.engine, dbmod.SessionLocal)
    dbmod.engine = dbmod.create_engine(url, connect_args={"check_same_thread": False})
    dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
    init_db()
    yield dbmod
    dbmod.engine.dispose()
    dbmod.engine, dbmod.SessionLocal = old


@pytest.fixture()
def seeded_db(budget_env):
    """清理干扰数据并写入本月 2.0 元 + 上月 99 元的花费记录。"""
    from backend.database import AppConfig, Submission

    db = budget_env.SessionLocal()
    db.query(Submission).delete()
    db.query(AppConfig).filter(AppConfig.key.in_(
        ["monthly_budget", "budget_action"])).delete(synchronize_session=False)
    db.commit()
    db.close()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s = budget_env.SessionLocal()
    s.add(Submission(assignment_id=1, student_id=1, original_image_path="/tmp/a.jpg",
                     cost_estimate=1.5, created_at=now))
    s.add(Submission(assignment_id=1, student_id=2, original_image_path="/tmp/b.jpg",
                     cost_estimate=0.5, created_at=now))
    s.add(Submission(assignment_id=1, student_id=3, original_image_path="/tmp/c.jpg",
                     cost_estimate=99.0, created_at=now - timedelta(days=45)))  # 上月
    s.commit()
    s.close()
    return now


def test_monthly_spent_only_this_month(budget_env):
    """本月花费只累计本月记录（上月 99 元不计入）。"""
    from backend.database import AppConfig, Submission
    from backend.models.budget import get_monthly_spent

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = budget_env.SessionLocal()
    db.add(Submission(assignment_id=1, student_id=1, original_image_path="/tmp/a.jpg",
                      cost_estimate=1.5, created_at=now))
    db.add(Submission(assignment_id=1, student_id=2, original_image_path="/tmp/b.jpg",
                      cost_estimate=0.5, created_at=now))
    db.add(Submission(assignment_id=1, student_id=3, original_image_path="/tmp/c.jpg",
                      cost_estimate=99.0, created_at=now - timedelta(days=45)))
    db.commit()
    db.close()
    spent = get_monthly_spent(now)
    assert abs(spent - 2.0) < 1e-6, spent


def test_default_budget_zero_unlimited(budget_env):
    """默认预算 0 = 不限，不超。"""
    from backend.models.budget import check_budget

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    b = check_budget(now)
    assert b["monthly_budget"] == 0 and b["exceeded"] is False and b["percent"] is None, b


def test_budget_exceeded_detection(seeded_db):
    """设预算 1 元，本月已用 2 元 → exceeded，percent=200。"""
    from backend.models.budget import check_budget, save_budget_config

    save_budget_config(1.0, "warn")
    b = check_budget(seeded_db)
    assert b["exceeded"] and b["percent"] == 200.0 and b["remaining"] == -1.0, b


def test_block_mode_not_blocking_when_under(seeded_db):
    """block 模式未超不阻止，返回可判断的 action。"""
    from backend.models.budget import check_budget, save_budget_config

    save_budget_config(5.0, "block")
    b = check_budget(seeded_db)
    assert b["exceeded"] is False and b["budget_action"] == "block", b


def test_budget_config_roundtrip(budget_env):
    """配置读取/保存往返。"""
    from backend.models.budget import get_budget_config, save_budget_config

    save_budget_config(5.0, "block")
    assert get_budget_config() == {"monthly_budget": 5.0, "budget_action": "block"}


def test_price_presets_cover_recommended():
    """价格预设表：包含推荐清单全部模型，单价非负且免费模型为 0。"""
    from backend.models.grading_engine import MODEL_PRICE_PRESETS

    models = {p["model"] for p in MODEL_PRICE_PRESETS}
    expected = {"qwen-vl-plus", "qwen-vl-max", "qwen3-vl-plus", "glm-4v-flash", "glm-4v-plus"}
    assert expected <= models, models
    assert all(p["input"] >= 0 and p["output"] >= 0 for p in MODEL_PRICE_PRESETS)
    flash = next(p for p in MODEL_PRICE_PRESETS if p["model"] == "glm-4v-flash")
    assert flash["input"] == 0 and flash["output"] == 0


def test_default_unit_price_and_estimate():
    """默认单价修正：qwen-vl-plus 每百万 0.8/2 元 → 每1K 0.0008/0.002；估算费用随之。"""
    from backend.models.grading_engine import estimate_cost, get_cost_config

    c = get_cost_config()
    assert abs(c["input"] - 0.0008) < 1e-9 and abs(c["output"] - 0.002) < 1e-9, c
    est = estimate_cost(1000, 1000)
    assert abs(est["cost"] - 0.0028) < 1e-6, est
