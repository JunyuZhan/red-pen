"""
AI模型适配器
支持多种云端大模型：通义千问(DashScope)、智谱GLM、自定义OpenAI兼容接口。
配置（服务商/模型/API Key）由教师在设置页填写，存储于 AppConfig 表。
"""

import httpx
import base64
import mimetypes
from typing import Dict, Any, Optional

from ..config import settings
from ..database import AppConfig, SessionLocal


# ---------- 配置存取 ----------

CONFIG_KEYS = ["llm_provider", "llm_base_url", "llm_model", "llm_api_key"]

PROVIDER_DEFAULTS = {
    # OpenAI 兼容 Chat Completions 接口
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-plus",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v",
    },
    "custom": {
        "base_url": "",
        "model": "",
    },
}


def get_llm_config() -> Dict[str, str]:
    """从数据库读取老师的 LLM 配置，缺省值来自 PROVIDER_DEFAULTS"""
    db = SessionLocal()
    try:
        rows = db.query(AppConfig).filter(AppConfig.key.in_(CONFIG_KEYS)).all()
        stored = {r.key: r.value for r in rows}
    finally:
        db.close()

    provider = stored.get("llm_provider") or "qwen"
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["custom"])
    return {
        "provider": provider,
        "base_url": stored.get("llm_base_url") or defaults["base_url"],
        "model": stored.get("llm_model") or defaults["model"],
        "api_key": stored.get("llm_api_key") or "",
    }


def save_llm_config(provider: str, base_url: str, model: str, api_key: Optional[str] = None,
                    update_key: bool = True) -> None:
    """保存老师的 LLM 配置。api_key 为 None 或 update_key=False 时不覆盖已有 Key"""
    db = SessionLocal()
    try:
        values = {
            "llm_provider": provider,
            "llm_base_url": base_url,
            "llm_model": model,
        }
        if update_key and api_key:
            values["llm_api_key"] = api_key
        for key, value in values.items():
            row = db.query(AppConfig).filter(AppConfig.key == key).first()
            if row:
                row.value = value
            else:
                db.add(AppConfig(key=key, value=value))
        db.commit()
    finally:
        db.close()


# ---------- 批改引擎 ----------

def _build_prompt(question_type: str, subject: str) -> str:
    """构建批改提示词"""
    prompts = {
        "math": f"你是一位数学老师。请仔细分析这张数学作业图片，逐题判断对错，指出错误原因，并给出分数。学科: {subject}",
        "chinese": f"你是一位语文老师。请仔细分析这张语文作业图片，逐题判断对错，指出错误原因，并给出分数。学科: {subject}",
        "english": f"你是一位英语老师。请仔细分析这张英语作业图片，逐题判断对错，指出错误原因，并给出分数。学科: {subject}",
    }
    return prompts.get(
        question_type,
        f"请分析这张作业图片，逐题判断对错，指出错误原因，并给出分数。学科: {subject}",
    )


class GradingEngine:
    """基于 OpenAI 兼容 Chat Completions 接口的批改引擎（三家服务商均兼容）"""

    def __init__(self, api_key: str, model_name: str, base_url: str):
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")

    def _chat_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    async def grade(self, image_path: str, question_type: str, subject: str) -> Dict[str, Any]:
        """批改作业图片"""
        with open(image_path, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode()
        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"

        data = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_base64}"}},
                        {"type": "text", "text": _build_prompt(question_type, subject)},
                    ],
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
            last_error = None
            for attempt in range(settings.LLM_MAX_RETRIES):
                try:
                    resp = await client.post(self._chat_url(), json=data, headers=headers)
                    resp.raise_for_status()
                    return self._parse_response(resp.json())
                except Exception as e:
                    last_error = e
                    if attempt < settings.LLM_MAX_RETRIES - 1:
                        import asyncio
                        await asyncio.sleep(settings.LLM_BACKOFF_FACTOR * (2 ** attempt))
            return {"success": False, "error": str(last_error)}

    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        try:
            content = response["choices"][0]["message"]["content"]
            return {
                "success": True,
                "raw_output": response,
                "judgement": "correct",  # 默认；细粒度解析见后续版本
                "confidence": 0.85,
                "analysis": content,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "raw_output": response}


class GradingManager:
    """批改管理器：根据老师配置选择服务商"""

    async def grade(self, image_path: str, question_type: str, subject: str) -> Dict[str, Any]:
        cfg = get_llm_config()

        if not cfg["api_key"]:
            return {
                "success": False,
                "error": "尚未配置 API Key，请先到设置页填写",
                "provider": cfg["provider"],
            }
        if not cfg["base_url"] or not cfg["model"]:
            return {
                "success": False,
                "error": "LLM 配置不完整：缺少 base_url 或 model",
                "provider": cfg["provider"],
            }

        engine = GradingEngine(cfg["api_key"], cfg["model"], cfg["base_url"])
        try:
            result = await engine.grade(image_path, question_type, subject)
            result.setdefault("provider", cfg["provider"])
            return result
        except Exception as e:
            return {"success": False, "error": str(e), "provider": cfg["provider"]}


# 全局批改管理器实例
grading_manager = GradingManager()
