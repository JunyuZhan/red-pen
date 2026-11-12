"""AI 学生学情报告（PRD 待办 k / 第七章）

把单个学生的历史批改数据（各次作业正确率序列、错误类型分布、错题与薄弱
知识点）交给 LLM，生成自然语言学情分析，存 study_report 表供学情页展示，
可重新生成。

安全约束与 teaching_advice 相同：prompt 锁定"只允许引用输入数据，不得虚构"；
纯文本调用（不传图），成本一次几 KB。LLM 失败/未配 Key → 明确错误，不影响
已有报告展示。
"""

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Optional

from .knowledge_norm import canonical_key

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, StudyReport, Student, Submission, QuestionResult
from .grading_engine import get_llm_config, _parse_llm_json

MAX_WRONG_QUESTIONS = 20   # 传给 LLM 的错题上限（控制成本）


def collect_student_stats(db: Session, student_id: int) -> Optional[Dict[str, Any]]:
    """收集单个学生的历史批改统计；该学生没有任何批改数据返回 None"""
    stu = db.query(Student).filter(Student.id == student_id).first()
    if not stu:
        return None

    subs = (db.query(Submission)
            .filter(Submission.student_id == student_id)
            .order_by(Submission.created_at, Submission.id).all())
    sub_ids = [s.id for s in subs]
    if not sub_ids:
        return None

    results = (db.query(QuestionResult)
               .filter(QuestionResult.submission_id.in_(sub_ids))
               .order_by(QuestionResult.created_at, QuestionResult.id).all())

    # 各次作业正确率序列（趋势用）
    by_sub: Dict[int, Dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        by_sub[r.submission_id]["total"] += 1
        if r.judgement_final == "correct":
            by_sub[r.submission_id]["correct"] += 1
    history = []
    for s in subs:
        st = by_sub.get(s.id, {"total": 0, "correct": 0})
        if st["total"]:
            history.append({
                "date": s.created_at.strftime("%Y-%m-%d") if s.created_at else "",
                "total": st["total"],
                "correct": st["correct"],
                "accuracy": round(st["correct"] / st["total"], 2),
            })

    # 错误类型分布
    error_types = Counter(r.error_type for r in results
                          if r.judgement_final == "wrong" and r.error_type)

    # 错题明细（含知识点），取最近的
    wrong_questions = [
        {"question_no": r.question_no,
         "knowledge_point": (r.knowledge_point or "").strip(),
         "error_type": r.error_type or ""}
        for r in results if r.judgement_final == "wrong"
    ][-MAX_WRONG_QUESTIONS:]

    # 知识点掌握
    kp: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        name = canonical_key(r.knowledge_point)
        if not name:
            continue
        kp[name]["total"] += 1
        if r.judgement_final == "correct":
            kp[name]["correct"] += 1
    weak_kps = [{"name": n, "total": st["total"],
                 "accuracy": round(st["correct"] / st["total"], 2)}
                for n, st in kp.items()
                if st["correct"] / st["total"] < 0.7]
    weak_kps.sort(key=lambda x: x["accuracy"])

    total = len(results)
    correct = sum(1 for r in results if r.judgement_final == "correct")
    return {
        "student_name": stu.name,
        "submissions_count": len(subs),
        "total_questions": total,
        "overall_accuracy": round(correct / total, 2) if total else None,
        "accuracy_history": history,
        "error_types": dict(error_types.most_common()),
        "weak_knowledge_points": weak_kps[:8],
        "wrong_questions": wrong_questions,
    }


def build_prompt(stats: Dict[str, Any]) -> str:
    data = json.dumps(stats, ensure_ascii=False, indent=1)
    return (
        "你是一位经验丰富的小学教师助手。下面是某一位学生的历史作业批改统计数据"
        "（JSON）。请基于且仅基于这些数据，为这位学生生成一份学情报告，供教师"
        "了解该学生的学习情况。\n\n"
        "硬性规则：\n"
        "1. 只允许引用输入数据中出现的知识点、错误类型、日期和数字，禁止虚构任何题目、考试或数据；\n"
        "2. 数据不足以得出的结论不要写（例如只有一次作业就不要写\"趋势\"）；\n"
        "3. 语言客观温和、简洁务实，面向小学教师，不评判学生。输出不超过 400 字。\n\n"
        "输出 JSON（不要输出其它文字）：\n"
        '{"summary": "2-3句总体情况（含正确率与趋势，如数据支持）", '
        '"strengths": ["掌握较好的方面", ...0-3条], '
        '"weaknesses": ["薄弱的方面（基于错题/错误类型/薄弱知识点）", ...1-4条], '
        '"advice": ["给教师的具体建议", ...2-4条]}\n\n'
        "统计数据：\n" + data
    )


async def generate_student_report(student_id: int) -> Dict[str, Any]:
    """调用 LLM 生成该学生的学情报告并入库；返回 {success, ...}"""
    cfg = get_llm_config()
    if not cfg.get("api_key"):
        return {"success": False, "error": "未配置 AI 模型，请先到设置页配置 API Key"}

    db = SessionLocal()
    try:
        stats = collect_student_stats(db, student_id)
        if stats is None:
            return {"success": False, "error": "该学生暂无批改数据，请先批改其作业"}
        name = stats["student_name"]
    finally:
        db.close()

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "你是严谨的学生学情分析助手，只依据给定数据作答。"},
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

    report = _parse_llm_json(text)
    if not report or not isinstance(report.get("summary"), str):
        return {"success": False, "error": "模型返回格式异常，未能解析出报告"}

    def _lst(key, limit):
        return [str(s)[:500] for s in (report.get(key) or [])[:limit] if str(s).strip()]

    row = StudyReport(
        student_id=student_id,
        stats_snapshot=stats,
        content={
            "summary": report["summary"][:2000],
            "strengths": _lst("strengths", 4),
            "weaknesses": _lst("weaknesses", 5),
            "advice": _lst("advice", 5),
        },
        model=cfg["model"],
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return {"success": True, "id": row.id, "student_id": student_id,
            "report": row.content, "created_at": row.created_at.isoformat()}


def get_latest_report(student_id: int) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        row = (db.query(StudyReport)
               .filter(StudyReport.student_id == student_id)
               .order_by(StudyReport.id.desc()).first())
        if not row:
            return None
        return {"id": row.id, "student_id": student_id, "report": row.content,
                "model": row.model,
                "created_at": row.created_at.isoformat() if row.created_at else None}
    finally:
        db.close()
