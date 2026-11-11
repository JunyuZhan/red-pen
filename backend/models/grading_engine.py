"""
AI模型适配器
支持多种云端大模型：通义千问(DashScope)、智谱GLM、自定义OpenAI兼容接口。
配置（服务商/模型/API Key）由教师在设置页填写，存储于 AppConfig 表。
"""

import os

import httpx
import base64
import mimetypes
from typing import Dict, Any, Optional

from ..config import settings
from ..database import AppConfig, SessionLocal


# ---------- 配置存取 ----------

CONFIG_KEYS = ["llm_provider", "llm_base_url", "llm_model", "llm_api_key",
               "llm_fallback_provider", "llm_fallback_base_url", "llm_fallback_model", "llm_fallback_api_key"]

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


def get_fallback_config() -> Dict[str, Any]:
    # 读取备用引擎配置；未配置时 provider 为 None（不启用备用切换）
    db = SessionLocal()
    try:
        rows = db.query(AppConfig).filter(AppConfig.key.in_(CONFIG_KEYS)).all()
        stored = {r.key: r.value for r in rows}
    finally:
        db.close()

    provider = stored.get("llm_fallback_provider") or None
    cfg: Dict[str, Any] = {"provider": None, "base_url": "", "model": "", "api_key": ""}
    if provider:
        defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["custom"])
        cfg = {
            "provider": provider,
            "base_url": stored.get("llm_fallback_base_url") or defaults["base_url"],
            "model": stored.get("llm_fallback_model") or defaults["model"],
            "api_key": stored.get("llm_fallback_api_key") or "",
        }
    return cfg


def save_fallback_config(provider, base_url: str, model: str,
                         api_key=None, update_key: bool = True) -> None:
    # 保存备用引擎配置。provider 为空/None 表示不启用备用切换
    keys = ["llm_fallback_provider", "llm_fallback_base_url",
            "llm_fallback_model", "llm_fallback_api_key"]
    db = SessionLocal()
    try:
        if not provider:
            for key in keys:
                db.query(AppConfig).filter(AppConfig.key == key).delete()
            db.commit()
            return
        values = {
            "llm_fallback_provider": provider,
            "llm_fallback_base_url": base_url,
            "llm_fallback_model": model,
        }
        if update_key and api_key:
            values["llm_fallback_api_key"] = api_key
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

def _build_prompt(question_type: str, subject: str, answers: Optional[Dict[str, str]] = None) -> str:
    """构建批改提示词，要求模型输出结构化 JSON"""
    base = (
        f"你是一位{subject}老师。请仔细分析这张作业图片，逐题批改。\n"
        "严格按以下 JSON 格式输出，不要输出任何其他内容：\n"
        "{\n"
        '  "questions": [\n'
        '    {"question_no": 1, "judgement": "correct|wrong|partial|unclear", '
        '"error_type": "计算错误|概念错误|粗心|未作答|其他 或 null", '
        '"student_answer": "学生答案摘要", '
        '"correct_answer": "正确答案摘要", '
        '"analysis": "简要分析", '
        '"knowledge_point": "该题考查的知识点（如：两位数乘法、拼音声调），一句话，拿不准就留空", '
        '"confidence": 0.0到1.0, '
        '"bbox": [x1, y1, x2, y2] 或 null}\n'
        "  ],\n"
        '  "summary": "整体点评一句话"\n'
        "}\n"
        "bbox 是该题在图片中的大致位置（像素坐标，基于图片实际尺寸估计，拿不准就给 null）。"
    )
    prompts = {
        "math": f"{base}\n学科: 数学",
        "chinese": f"{base}\n学科: 语文",
        "english": f"{base}\n学科: 英语",
    }
    prompt = prompts.get(question_type, base)
    if answers:
        lines = "\n".join(f"第{no}题：{ans}" for no, ans in answers.items())
        prompt += (
            f"\n\n以下是教师提供的参考答案，判题时必须以参考答案为准：\n{lines}\n"
            "若学生答案与参考答案语义等价（数值相等、选项一致）判 correct；"
            "正确答案（correct_answer）字段请填写参考答案。"
        )
    return prompt


def _parse_llm_json(text: str) -> Optional[dict]:
    """从模型回复中鲁棒地提取 JSON（容忍 markdown 代码块等）"""
    import json as _json
    import re as _re
    if not text:
        return None
    # 去掉 ```json ... ``` 包裹
    m = _re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, _re.DOTALL)
    raw = m.group(1) if m else text
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return _json.loads(raw[start:end + 1])
    except Exception:
        return None


def get_cost_config() -> Dict[str, float]:
    """读取单价配置（元/1K tokens），默认 qwen-vl-plus 官方价（¥0.8输入/¥2.0 每百万tokens）"""
    db = SessionLocal()
    try:
        rows = db.query(AppConfig).filter(
            AppConfig.key.in_(["cost_input_per_1k", "cost_output_per_1k"])).all()
        stored = {r.key: r.value for r in rows}
    finally:
        db.close()
    try:
        inp = float(stored.get("cost_input_per_1k", 0.0008))
    except ValueError:
        inp = 0.0008
    try:
        out = float(stored.get("cost_output_per_1k", 0.002))
    except ValueError:
        out = 0.002
    return {"input": inp, "output": out}


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> Dict[str, Any]:
    cfg = get_cost_config()
    cost = prompt_tokens / 1000 * cfg["input"] + completion_tokens / 1000 * cfg["output"]
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "cost": round(cost, 4)}


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

    async def grade(self, image_path: str, question_type: str, subject: str,
                    answers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
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
                        {"type": "text", "text": _build_prompt(question_type, subject, answers)},
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
                    if resp.status_code >= 400:
                        # 保留响应体里的厂商错误信息（如 Invalid API key / 不支持图片），便于给出明确提示
                        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
                    return self._parse_response(resp.json(), answers=answers)
                except Exception as e:
                    last_error = e
                    if attempt < settings.LLM_MAX_RETRIES - 1:
                        import asyncio
                        await asyncio.sleep(settings.LLM_BACKOFF_FACTOR * (2 ** attempt))
            return {"success": False, "error": str(last_error)}

    def _parse_response(self, response: Dict[str, Any],
                        answers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            content = response["choices"][0]["message"]["content"]
            usage = response.get("usage") or {}
            usage_info = {
                "prompt_tokens": usage.get("input_tokens") or usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("output_tokens") or usage.get("completion_tokens") or 0,
            }
            parsed = _parse_llm_json(content)
            if parsed and parsed.get("questions"):
                questions = parsed.get("questions", [])
                # 规则交叉校验层：用教师参考答案约束模型结论（准确性第一）
                from backend.models.rule_validator import rule_validate
                rule_stats = rule_validate(questions, answers)
                judgement_counts: Dict[str, int] = {}
                for q in questions:
                    j = q.get("judgement", "unclear")
                    judgement_counts[j] = judgement_counts.get(j, 0) + 1
                confidences = [q.get("confidence") for q in questions if isinstance(q.get("confidence"), (int, float))]
                return {
                    "success": True,
                    "raw_output": response,
                    "judgement": "correct" if judgement_counts.get("wrong", 0) == 0 and judgement_counts.get("correct", 0) > 0 else ("wrong" if judgement_counts.get("wrong", 0) > 0 else "unclear"),
                    "confidence": round(sum(confidences) / len(confidences), 2) if confidences else 0.5,
                    "analysis": parsed.get("summary") or content,
                    "questions": questions,
                    "rule_validation": rule_stats,
                    **usage_info,
                }
            # 无法结构化时降级为原始文本
            return {
                "success": True,
                "raw_output": response,
                "judgement": "unclear",
                "confidence": 0.5,
                "analysis": content,
                **usage_info,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "raw_output": response}


class GradingManager:
    """批改管理器：根据老师配置选择服务商"""

    async def _grade_with_split(self, image_path: str, cfg: Dict[str, Any],
                                question_type: str, subject: str,
                                answers: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        """切题模式（PRD 待办 g）：逐区域裁切放大后送模型，合并结果。

        返回 None 表示回退整页批改（切题失败/区域不足/全部区域批改失败）。
        """
        from .question_splitter import split_questions
        try:
            out_dir = os.path.join(str(settings.DATA_DIR), "splits")
            # 切题前置预处理（设置页可开关；失败/关闭均用原图）
            from .image_preprocess import get_preprocess_config, preprocess_image
            if get_preprocess_config()["enabled"]:
                image_path = preprocess_image(image_path, out_dir)
            crops = split_questions(image_path, out_dir)
        except Exception:
            return None
        if len(crops) < 2:
            return None

        engine = GradingEngine(cfg["api_key"], cfg["model"], cfg["base_url"])
        merged_questions, used_regions = [], 0
        for crop in crops:
            try:
                part = await engine.grade(crop["path"], question_type, subject, answers=answers)
            except Exception:
                part = {"success": False}
            if part.get("success"):
                used_regions += 1
                part.setdefault("region_index", crop["index"])
                merged_questions.extend(part.get("questions", []))

        if not merged_questions:
            return None  # 一个区域都没批出来，回退整页模式
        return {
            "success": True,
            "questions": merged_questions,
            "provider": cfg["provider"],
            "split_used": True,
            "regions_total": len(crops),
            "regions_graded": used_regions,
        }

    async def grade(self, image_path: str, question_type: str, subject: str,
                    answers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
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

        # PRD 待办 g：CV 切题 + 裁切放大（设置页可开关；失败自动回退整页）
        from .question_splitter import get_split_config
        if get_split_config()["enabled"]:
            split_result = await self._grade_with_split(
                image_path, cfg, question_type, subject, answers)
            if split_result is not None:
                return split_result

        async def _run(provider_name: str, api_key: str, model_name: str, base_url: str) -> Dict[str, Any]:
            engine = GradingEngine(api_key, model_name, base_url)
            result = await engine.grade(image_path, question_type, subject, answers=answers)
            result.setdefault("provider", provider_name)
            return result

        try:
            result = await _run(cfg["provider"], cfg["api_key"], cfg["model"], cfg["base_url"])
        except Exception as e:
            result = {"success": False, "error": str(e), "provider": cfg["provider"]}

        # PRD 第三节：主 provider 失败自动切备用
        if not result.get("success"):
            fb = get_fallback_config()
            if fb["provider"] and fb["api_key"] and fb["base_url"] and fb["model"]:
                try:
                    fb_result = await _run(fb["provider"], fb["api_key"], fb["model"], fb["base_url"])
                except Exception as e:
                    fb_result = {"success": False, "error": str(e), "provider": fb["provider"]}
                if fb_result.get("success"):
                    fb_result["fallback_used"] = True
                    fb_result["main_provider"] = cfg["provider"]
                    fb_result["main_error"] = result.get("error")
                    return fb_result
                return {
                    "success": False,
                    "error": "主用(%s)与备用(%s)均失败: %s / %s" % (
                        cfg["provider"], fb["provider"], result.get("error"), fb_result.get("error")),
                    "provider": cfg["provider"],
                }
        return result


# 全局批改管理器实例
grading_manager = GradingManager()


# ---------- 连接测试（设置页"测试连接"按钮，PRD 待办 c） ----------

# 推荐视觉模型清单：这些都是 OpenAI 兼容接口、明确支持图片输入的模型
RECOMMENDED_VISION_MODELS = [
    {"provider": "qwen", "model": "qwen-vl-plus", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "note": "通义千问视觉，性价比高，日常批改首选"},
    {"provider": "qwen", "model": "qwen-vl-max", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "note": "通义千问视觉旗舰，手写识别更准"},
    {"provider": "glm", "model": "glm-4v-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4", "note": "智谱视觉，有免费额度"},
    {"provider": "glm", "model": "glm-4v-plus", "base_url": "https://open.bigmodel.cn/api/paas/v4", "note": "智谱视觉旗舰"},
]

# 模型价格预设表（PRD 待办 j）：单价 元/1K tokens，2026-11-11 按官方定价页核实（元/百万tokens 换算）
# qwen-vl-plus 国际 $0.115/$0.287 每百万 ≈ ¥0.82/¥2.05；qwen3-vl-plus ¥1/¥10；glm-4v-flash 免费；glm-4v-plus ¥4/¥4
MODEL_PRICE_PRESETS = [
    {"model": "qwen-vl-plus", "input": 0.0008, "output": 0.002},
    {"model": "qwen-vl-max", "input": 0.003, "output": 0.009},
    {"model": "qwen3-vl-plus", "input": 0.001, "output": 0.010},
    {"model": "glm-4v-flash", "input": 0.0, "output": 0.0},
    {"model": "glm-4v-plus", "input": 0.004, "output": 0.004},
]

# 错误信息中出现这些关键词 → 大概率是模型不支持视觉/图片输入
_NOT_VISION_HINTS = (
    "does not support image", "not support image", "image input",
    "image_url", "invalid image", "unsupported content", "multimodal",
    "content must be", "text-only", "不支持图片", "不支持图像", "多模态",
)


def _make_test_image(path: str) -> None:
    """生成一张内置测试图：手写风格的 1+1=2，用于实测视觉输入链路"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (320, 160), "white")
    d = ImageDraw.Draw(img)
    font = None
    for fp in ("C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(fp, 48)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    d.text((60, 50), "1+1=2", fill="black", font=font)
    img.save(path, "JPEG")


def _explain_error(error: str) -> Dict[str, Any]:
    """把底层报错翻译成老师能看懂的提示"""
    e = error.lower()
    if "401" in e or "unauthorized" in e or "invalid api key" in e or "api key" in e and "invalid" in e:
        return {"kind": "auth", "message": "API Key 无效或已过期，请检查 Key 是否填对。"}
    if "404" in e or "not found" in e or "no such model" in e or "model not exist" in e:
        return {"kind": "model", "message": "模型名称不存在，请检查模型名是否拼写正确（注意区分字母和横线）。"}
    if "429" in e or "rate limit" in e:
        return {"kind": "rate_limit", "message": "请求太频繁或余额/额度不足，请稍后重试或检查账户余额。"}
    if "timeout" in e or "timed out" in e or "connect" in e:
        return {"kind": "network", "message": "连不上服务商服务器：请检查网络、Base URL 是否填对（一般以 /v1 结尾）。"}
    if any(h in e for h in _NOT_VISION_HINTS):
        return {"kind": "not_vision", "message": "当前模型不支持图片输入（纯文本模型）。请改用视觉模型，如 qwen-vl-plus、glm-4v-flash 等。"}
    return {"kind": "unknown", "message": error}


async def test_connection(provider: str, base_url: str, model: str, api_key: str) -> Dict[str, Any]:
    """用一张内置测试图实测：Key 是否有效、模型是否支持视觉输入"""
    if not base_url or not model:
        return {"success": False, "kind": "config", "message": "配置不完整：请先填写 Base URL 和模型名称并保存。"}
    if not api_key:
        return {"success": False, "kind": "config", "message": "尚未配置 API Key，请先填写并保存。"}
    try:
        import tempfile, os
        fd, img_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            _make_test_image(img_path)
            engine = GradingEngine(api_key, model, base_url)
            result = await engine.grade(img_path, "short_answer", "数学")
        finally:
            try:
                os.unlink(img_path)
            except OSError:
                pass
    except Exception as e:
        explanation = _explain_error(str(e))
        return {"success": False, "kind": explanation["kind"], "message": explanation["message"], "detail": str(e)}
    if result.get("success"):
        return {"success": True, "message": f"连接成功：模型 {model} 可正常接收图片并返回批改结果，视觉输入链路畅通。"}
    explanation = _explain_error(result.get("error", ""))
    return {"success": False, "kind": explanation["kind"], "message": explanation["message"], "detail": result.get("error", "")}
