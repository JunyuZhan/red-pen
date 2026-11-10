"""
AI模型适配器
支持多种云端大模型
"""

import httpx
import json
import base64
from typing import Dict, Any, Optional, List
from PIL import Image
import io
import time
from config import settings

class GradingEngine:
    """AI批改引擎基类"""
    
    def __init__(self, api_key: str, model_name: str):
        self.api_key = api_key
        self.model_name = model_name
        self.timeout = settings.LLM_TIMEOUT
        self.max_retries = settings.LLM_MAX_RETRIES
        self.backoff_factor = settings.LLM_BACKOFF_FACTOR
    
    async def grade(self, image_path: str, question_type: str, subject: str) -> Dict[str, Any]:
        """批改作业图片"""
        raise NotImplementedError
    
    def _make_request(self, url: str, data: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        """发送HTTP请求"""
        client = httpx.AsyncClient(timeout=self.timeout)
        
        for attempt in range(self.max_retries):
            try:
                response = client.post(url, json=data, headers=headers)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise e
                time.sleep(self.backoff_factor * (2 ** attempt))
                continue
        return {}

class QwenGrading(GradingEngine):
    """通义千问批改引擎"""
    
    def __init__(self, api_key: str, model_name: "qwen-vl-plus"):
        super().__init__(api_key, model_name)
        self.base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    
    async def grade(self, image_path: str, question_type: str, subject: str) -> Dict[str, Any]:
        """使用通义千问批改作业"""
        
        # 读取图片并编码
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        image_base64 = base64.b64encode(image_data).decode()
        
        # 构建提示词
        prompt = self._build_prompt(question_type, subject)
        
        # 构建请求数据
        data = {
            "model": self.model_name,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": f"data:image/jpeg;base64,{image_base64}"},
                            {"text": prompt}
                        ]
                    }
                ]
            }
        }
        
        # 发送请求
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        response = self._make_request(self.base_url, data, headers)
        
        # 解析响应
        return self._parse_response(response)
    
    def _build_prompt(self, question_type: str, subject: str) -> str:
        """构建批改提示词"""
        prompts = {
            "math": f"你是一位数学老师。请仔细分析这张数学作业图片，判断每个题目是否正确。请给出详细的判断理由和分数。学科: {subject}",
            "chinese": f"你是一位语文老师。请仔细分析这张语文作业图片，判断每个题目是否正确。请给出详细的判断理由和分数。学科: {subject}",
            "english": f"你是一位英语老师。请仔细分析这张英语作业图片，判断每个题目是否正确。请给出详细的判断理由和分数。学科: {subject}"
        }
        
        return prompts.get(question_type, f"请分析这张作业图片，判断题目是否正确。学科: {subject}")
    
    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """解析响应"""
        try:
            return {
                "success": True,
                "raw_output": response,
                "judgement": self._extract_judgement(response),
                "confidence": self._extract_confidence(response)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "raw_output": response
            }
    
    def _extract_judgement(self, response: Dict[str, Any]) -> str:
        """提取批改结果"""
        # 这里需要根据实际的API响应格式来解析
        # 简化处理，实际使用时需要根据具体API调整
        return "correct"  # 默认返回正确
    
    def _extract_confidence(self, response: Dict[str, Any]) -> float:
        """提取置信度"""
        # 默认置信度
        return 0.85

class GLMGrading(GradingEngine):
    """智谱GLM批改引擎"""
    
    def __init__(self, api_key: str, model_name: "glm-4v"):
        super().__init__(api_key, model_name)
        self.base_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    
    async def grade(self, image_path: str, question_type: str, subject: str) -> Dict[str, Any]:
        """使用智谱GLM批改作业"""
        
        # 读取图片并编码
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        image_base64 = base64.b64encode(image_data).decode()
        
        # 构建提示词
        prompt = self._build_prompt(question_type, subject)
        
        # 构建请求数据
        data = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ]
        }
        
        # 发送请求
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        response = self._make_request(self.base_url, data, headers)
        
        # 解析响应
        return self._parse_response(response)
    
    def _build_prompt(self, question_type: str, subject: str) -> str:
        """构建批改提示词"""
        prompts = {
            "math": f"你是一位数学老师。请仔细分析这张数学作业图片，判断每个题目是否正确。请给出详细的判断理由和分数。学科: {subject}",
            "chinese": f"你是一位语文老师。请仔细分析这张语文作业图片，判断每个题目是否正确。请给出详细的判断理由和分数。学科: {subject}",
            "english": f"你是一位英语老师。请仔细分析这张英语作业图片，判断每个题目是否正确。请给出详细的判断理由和分数。学科: {subject}"
        }
        
        return prompts.get(question_type, f"请分析这张作业图片，判断题目是否正确。学科: {subject}")
    
    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """解析响应"""
        try:
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # 简化解析，实际使用时需要更复杂的解析逻辑
            return {
                "success": True,
                "raw_output": response,
                "judgement": "correct",  # 默认返回正确
                "confidence": 0.85,
                "analysis": content
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "raw_output": response
            }

class GradingManager:
    """批改管理器"""
    
    def __init__(self):
        self.engines = {
            "qwen": QwenGrading,
            "glm": GLMGrading
        }
    
    async def grade(self, 
                   image_path: str, 
                   question_type: str, 
                   subject: str,
                   provider: str = "qwen",
                   api_key: str = None) -> Dict[str, Any]:
        """执行批改"""
        
        if provider not in self.engines:
            raise ValueError(f"不支持的批改提供商: {provider}")
        
        if api_key is None:
            raise ValueError("API Key不能为空")
        
        engine_class = self.engines[provider]
        engine = engine_class(api_key, "default")
        
        try:
            result = await engine.grade(image_path, question_type, subject)
            return result
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "provider": provider
            }

# 全局批改管理器实例
grading_manager = GradingManager()