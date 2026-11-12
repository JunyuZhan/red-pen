"""
系统设置路由：老师在这里选择服务商、填写 API Key
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from .auth import require_admin

from ..models.grading_engine import (get_llm_config, save_llm_config,
                                     get_fallback_config, save_fallback_config, PROVIDER_DEFAULTS)

router = APIRouter()


class LLMConfigIn(BaseModel):
    provider: str                       # qwen / glm / custom
    base_url: Optional[str] = None      # custom 时必填
    model: Optional[str] = None         # custom 时必填
    api_key: Optional[str] = None       # 留空 = 不修改已有 Key


class FallbackConfigIn(BaseModel):
    provider: Optional[str] = None        # qwen / glm / custom / None(关闭备用)
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None         # 留空 = 不修改已有 Key


class CostConfigIn(BaseModel):
    cost_input_per_1k: float
    cost_output_per_1k: float


class TestConnectionIn(BaseModel):
    target: str = "main"                # main / fallback
    base_url: Optional[str] = None      # 留空 = 用已保存的配置
    model: Optional[str] = None
    api_key: Optional[str] = None       # 留空 = 用已保存的 Key


@router.post("/test-connection")
async def test_llm_connection(config: TestConnectionIn):
    """设置页"测试连接"按钮：用内置测试图实测当前引擎是否支持视觉输入、Key 是否有效"""
    from ..models.grading_engine import test_connection as run_test, get_llm_config
    if config.target == "fallback":
        saved = get_fallback_config()
    else:
        saved = get_llm_config()
    base_url = (config.base_url or "").strip() or saved.get("base_url", "")
    model = (config.model or "").strip() or saved.get("model", "")
    api_key = (config.api_key or "").strip() or saved.get("api_key", "")
    result = await run_test(saved.get("provider", ""), base_url, model, api_key)
    from ..models.grading_engine import RECOMMENDED_VISION_MODELS
    return {"success": result.get("success", False), **result,
            "recommendations": RECOMMENDED_VISION_MODELS}


# ---------- 低置信度二次复核（PRD 待办 f） ----------

class ReviewConfigIn(BaseModel):
    enabled: bool = True
    threshold: float = 0.7
    mode: str = "resample"   # resample=同模型重采样 / fallback=备用provider


# ---------- CV 切题模式（PRD 待办 g） ----------

class SplitConfigIn(BaseModel):
    enabled: bool = False


@router.get("/split")
async def get_split_settings():
    from ..models.question_splitter import get_split_config
    return {"success": True, **get_split_config()}


class CacheConfigIn(BaseModel):
    enabled: bool


@router.get("/grading-cache")
async def get_cache_settings():
    from ..models.grading_engine import get_cache_config
    return {"success": True, **get_cache_config()}


@router.post("/grading-cache")
async def update_cache_settings(config: CacheConfigIn, _: None = Depends(require_admin)):
    from ..models.grading_engine import save_cache_config, get_cache_config
    save_cache_config(config.enabled)
    return {"success": True, "message": "批改缓存设置已保存", **get_cache_config()}


@router.post("/split")
async def update_split_settings(config: SplitConfigIn, _: None = Depends(require_admin)):
    from ..models.question_splitter import save_split_config, get_split_config
    save_split_config(config.enabled)
    return {"success": True, "message": "切题模式设置已保存", **get_split_config()}


# ---------- 切题前置图像预处理 ----------

class PreprocessConfigIn(BaseModel):
    enabled: bool = False


@router.get("/preprocess")
async def get_preprocess_settings():
    from ..models.image_preprocess import get_preprocess_config
    return {"success": True, **get_preprocess_config()}


@router.post("/preprocess")
async def update_preprocess_settings(config: PreprocessConfigIn, _: None = Depends(require_admin)):
    from ..models.image_preprocess import save_preprocess_config, get_preprocess_config
    save_preprocess_config(config.enabled)
    return {"success": True, "message": "预处理设置已保存", **get_preprocess_config()}


@router.get("/review")
async def get_review_settings():
    from ..models.review import get_review_config
    return {"success": True, **get_review_config()}


# ---------- 作业等级换算（PRD 待办 n） ----------
class GradeThresholdsIn(BaseModel):
    a: int = 90
    b: int = 75
    c: int = 60


@router.get("/grade")
async def get_grade_settings():
    from ..models.grade_scale import get_thresholds
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        t = get_thresholds(db)
    finally:
        db.close()
    return {"success": True, "thresholds": t}


@router.post("/grade")
async def update_grade_settings(config: GradeThresholdsIn, _: None = Depends(require_admin)):
    from ..models.grade_scale import save_thresholds, get_thresholds
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        try:
            save_thresholds(db, {"A": config.a, "B": config.b, "C": config.c})
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        t = get_thresholds(db)
    finally:
        db.close()
    return {"success": True, "message": "等级阈值已保存", "thresholds": t}


@router.post("/review")
async def update_review_settings(config: ReviewConfigIn, _: None = Depends(require_admin)):
    if not (0.3 <= config.threshold <= 0.95):
        raise HTTPException(status_code=400, detail="阈值须在 0.3-0.95 之间")
    if config.mode not in ("resample", "fallback"):
        raise HTTPException(status_code=400, detail="mode 须为 resample 或 fallback")
    from ..models.review import save_review_config
    save_review_config(config.enabled, round(config.threshold, 2), config.mode)
    from ..models.review import get_review_config
    return {"success": True, "message": "复核设置已保存", **get_review_config()}


@router.get("/cost")
async def get_cost_settings():
    from ..models.grading_engine import get_cost_config, MODEL_PRICE_PRESETS
    cfg = get_cost_config()
    return {"success": True, **cfg, "presets": MODEL_PRICE_PRESETS}


@router.post("/cost")
async def update_cost_settings(config: CostConfigIn):
    from ..models.grading_engine import get_cost_config
    from ..database import AppConfig, SessionLocal
    db = SessionLocal()
    try:
        for key, value in [("cost_input_per_1k", config.cost_input_per_1k),
                           ("cost_output_per_1k", config.cost_output_per_1k)]:
            row = db.query(AppConfig).filter(AppConfig.key == key).first()
            if row:
                row.value = str(value)
            else:
                db.add(AppConfig(key=key, value=str(value)))
        db.commit()
    finally:
        db.close()
    cfg = get_cost_config()
    return {"success": True, "message": "单价已保存", **cfg}


@router.get("/budget")
async def get_budget_settings():
    """月度预算护栏（PRD 待办 j）：预算配置 + 本月已用"""
    from ..models.budget import check_budget
    return {"success": True, **check_budget()}


class BudgetConfigIn(BaseModel):
    monthly_budget: float = 0.0     # 元/月，0 = 不限
    budget_action: str = "warn"     # warn = 只提示 / block = 超预算停止批改


@router.post("/budget")
async def update_budget_settings(config: BudgetConfigIn, _: None = Depends(require_admin)):
    if config.monthly_budget < 0:
        raise HTTPException(status_code=400, detail="预算不能为负数（0 = 不限）")
    if config.budget_action not in ("warn", "block"):
        raise HTTPException(status_code=400, detail="budget_action 须为 warn 或 block")
    from ..models.budget import save_budget_config, check_budget
    save_budget_config(config.monthly_budget, config.budget_action)
    return {"success": True, "message": "预算设置已保存", **check_budget()}


@router.get("/fallback")
async def get_fallback_settings():
    """读取备用引擎配置（Key 脱敏返回）"""
    cfg = get_fallback_config()
    key = cfg["api_key"]
    masked = (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("已设置" if key else "")
    return {
        "success": True,
        "provider": cfg["provider"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_masked": masked,
        "has_api_key": bool(key),
    }


@router.post("/fallback")
async def update_fallback_settings(config: FallbackConfigIn):
    """保存备用引擎配置；provider 为空表示关闭备用切换"""
    provider = (config.provider or "").strip() or None
    if provider and provider not in PROVIDER_DEFAULTS:
        raise HTTPException(status_code=400, detail=f"不支持的备用服务商: {provider}（可选 qwen/glm/custom）")

    current = get_fallback_config()
    if provider:
        defaults = PROVIDER_DEFAULTS[provider]
        base_url = (config.base_url or "").strip() or defaults["base_url"]
        model = (config.model or "").strip() or defaults["model"]
        if provider == "custom" and (not base_url or not model):
            raise HTTPException(status_code=400, detail="自定义备用服务商必须填写 Base URL 和模型名称")
        update_key = bool((config.api_key or "").strip())
        save_fallback_config(provider, base_url, model,
                             api_key=(config.api_key or "").strip(), update_key=update_key)
    else:
        save_fallback_config(None, "", "")

    saved = get_fallback_config()
    return {
        "success": True,
        "message": "已关闭备用切换" if not saved["provider"] else "备用配置已保存",
        "provider": saved["provider"],
        "base_url": saved["base_url"],
        "model": saved["model"],
        "has_api_key": bool(saved["api_key"]) or bool(current["api_key"]),
    }


@router.get("/llm")
async def get_llm_settings():
    """读取当前 LLM 配置（Key 脱敏返回）"""
    cfg = get_llm_config()
    key = cfg["api_key"]
    masked = (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("已设置" if key else "")
    return {
        "success": True,
        "provider": cfg["provider"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_masked": masked,
        "has_api_key": bool(key),
        "provider_defaults": PROVIDER_DEFAULTS,
    }


@router.post("/llm")
async def update_llm_settings(config: LLMConfigIn):
    """保存 LLM 配置"""
    if config.provider not in PROVIDER_DEFAULTS:
        raise HTTPException(status_code=400, detail=f"不支持的服务商: {config.provider}（可选 qwen/glm/custom）")

    current = get_llm_config()
    base_url = (config.base_url or "").strip() or PROVIDER_DEFAULTS[config.provider]["base_url"]
    model = (config.model or "").strip() or PROVIDER_DEFAULTS[config.provider]["model"]

    if config.provider == "custom" and (not base_url or not model):
        raise HTTPException(status_code=400, detail="自定义服务商必须填写 Base URL 和模型名称")

    update_key = bool((config.api_key or "").strip())
    save_llm_config(
        provider=config.provider,
        base_url=base_url,
        model=model,
        api_key=(config.api_key or "").strip(),
        update_key=update_key,
    )

    saved = get_llm_config()
    return {
        "success": True,
        "message": "配置已保存",
        "provider": saved["provider"],
        "base_url": saved["base_url"],
        "model": saved["model"],
        "has_api_key": bool(saved["api_key"]) or bool(current["api_key"]),
    }
