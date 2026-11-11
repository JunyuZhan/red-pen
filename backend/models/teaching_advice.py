"""LLM 教学建议（PRD 待办 t / 第七章）

把结构化统计（高频错题、错误类型分布、薄弱知识点、学生正确率）交给 LLM，
生成自然语言教学建议，存表供学情页展示，可重新生成。

安全约束：prompt 锁定"只允许引用输入数据，不得虚构学生/题目/数据"；
纯文本调用（不传图），一次只有几 KB 文本，成本可控。
LLM 失败/未配 Key → 返回明确错误，不影响已有建议展示。
"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..database import AppConfig, QuestionResult, SessionLocal, Student, Submission, TeachingAdvice
from .grading_engine import get_llm_config, _parse_llm_json

WEAK_KP_THRESHOLD = 0.7


def collect_stats(db: Session) -> Optional[Dict[str, Any]]:
    """从批改结果收集结构化统计；无数据返回 None"""
    results = db.query(QuestionResult).all()
    if not results:
        return None

    sub_map = {s.id: s for s in db.query(Submission).all()}
    stu_map = {s.id: s.name for s in db.query(Student).all()}

    # 错误类型分布
    error_types = Counter(r.error_type for r in results
                          if r.judgement_final == "wrong" and r.error_type)

    # 薄弱知识点（正确率 < 阈值，按正确率升序）
    kp_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    wrong_by_kp: Counter = Counter()
    for r in results:
        kp = (r.knowledge_point or "").strip()
        if not kp:
            continue
        kp_stats[kp]["total"] += 1
        if r.judgement_final == "correct":
            kp_stats[kp]["correct"] += 1
        elif r.judgement_final == "wrong":
            wrong_by_kp[kp] += 1
    weak_kps = []
    for kp, st in kp_stats.items():
        acc = st["correct"] / st["total"]
        if acc < WEAK_KP_THRESHOLD:
            weak_kps.append({"name": kp, "total": st["total"], "accuracy": round(acc, 2)})
    weak_kps.sort(key=lambda x: x["accuracy"])

    # 学生正确率
    per_student: Dict[int, Dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        sub = sub_map.get(r.submission_id)
        if not sub:
            continue
        per_student[sub.student_id]["total"] += 1
        if r.judgement_final == "correct":
            per_student[sub.student_id]["correct"] += 1
    students = []
    for sid, st in per_student.items():
        acc = st["correct"] / st["total"] if st["total"] else None
        students.append({
            "name": stu_map.get(sid, f"学生{sid}"),
            "total": st["total"], "correct": st["correct"],
            "accuracy": round(acc, 2) if acc is not None else None,
        })
    students.sort(key=lambda s: (s["accuracy"] if s["accuracy"] is not None else -1))

    # 高频错题（同一知识点错 ≥2 次的列出来源）
    hot = [{"knowledge_point": kp, "wrong_count": n}
           for kp, n in wrong_by_kp.most_common(5) if n >= 2]

    return {
        "total_questions": len(results),
        "error_types": dict(error_types.most_common()),
        "weak_knowledge_points": weak_kps[:8],
        "hot_wrong": hot,
        "students": students[:30],
    }


def build_prompt(stats: Dict[str, Any]) -> str:
    data = json.dumps(stats, ensure_ascii=False, indent=1)
    return (
        "你是一位经验丰富的小学教师助手。下面是某班级作业批改后的结构化统计数据"
        "（JSON）。请基于且仅基于这些数据，生成教学建议。\n\n"
        "硬性规则：\n"
        "1. 只允许引用输入数据中出现的知识点、错误类型、学生姓名和数字，禁止虚构任何学生、题目或数据；\n"
        "2. 数据不足以得出的结论不要写；\n"
        "3. 语言简洁务实，面向小学教师。\n\n"
        "输出 JSON（不要输出其它文字）：\n"
        '{"summary": "2-3句总体情况", '
        '"suggestions": ["针对薄弱知识点的教学建议", ...3-5条], '
        '"focus_students": [{"name": "学生名", "reason": "基于数据的关注原因"}, ...0-3个]}\n\n'
        "统计数据：\n" + data
    )


async def generate_advice() -> Dict[str, Any]:
    """调用 LLM 生成教学建议并入库；返回 {success, ...}"""
    cfg = get_llm_config()
    if not cfg.get("api_key"):
        return {"success": False, "error": "未配置 AI 模型，请先到设置页配置 API Key"}

    db = SessionLocal()
    try:
        stats = collect_stats(db)
        if stats is None:
            return {"success": False, "error": "暂无批改数据，请先批改作业"}
    finally:
        db.close()

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "你是严谨的教学数据分析助手，只依据给定数据作答。"},
            {"role": "user", "content": build_prompt(stats)},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}

    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"模型接口返回错误 HTTP {e.response.status_code}"}
    except Exception as e:  # 网络/超时/解析
        return {"success": False, "error": f"调用模型失败: {e}"}

    advice = _parse_llm_json(text)
    if not advice or not isinstance(advice.get("suggestions"), list):
        return {"success": False, "error": "模型返回格式异常，未能解析出建议"}

    row = TeachingAdvice(
        scope="class",
        stats_snapshot=stats,
        content={
            "summary": str(advice.get("summary", ""))[:1000],
            "suggestions": [str(s)[:500] for s in advice["suggestions"][:8]],
            "focus_students": [
                {"name": str(f.get("name", ""))[:100],
                 "reason": str(f.get("reason", ""))[:500]}
                for f in (advice.get("focus_students") or [])[:5]
                if isinstance(f, dict) and f.get("name")
            ],
        },
        model=cfg["model"],
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return {"success": True, "id": row.id, "advice": row.content, "created_at": row.created_at.isoformat()}


def get_latest_advice() -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.query(TeachingAdvice).order_by(TeachingAdvice.id.desc()).first()
        if not row:
            return None
        return {"id": row.id, "advice": row.content, "model": row.model,
                "created_at": row.created_at.isoformat() if row.created_at else None}
    finally:
        db.close()
