"""
系统设置路由：老师在这里选择服务商、填写 API Key
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..models.grading_engine import get_llm_config, save_llm_config, PROVIDER_DEFAULTS

router = APIRouter()


class LLMConfigIn(BaseModel):
    provider: str                       # qwen / glm / custom
    base_url: Optional[str] = None      # custom 时必填
    model: Optional[str] = None         # custom 时必填
    api_key: Optional[str] = None       # 留空 = 不修改已有 Key


class CostConfigIn(BaseModel):
    cost_input_per_1k: float
    cost_output_per_1k: float


@router.get("/cost")
async def get_cost_settings():
    from ..models.grading_engine import get_cost_config
    cfg = get_cost_config()
    return {"success": True, **cfg}


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
