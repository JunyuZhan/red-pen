"""
批改路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pathlib import Path

from ..database import get_db, Submission, QuestionResult
from ..models.grading_engine import grading_manager, estimate_cost
from ..models.annotator import annotate_image

router = APIRouter()


@router.post("/start/{submission_id}")
async def start_grading(submission_id: int, db: Session = Depends(get_db)):
    """对一份提交执行 AI 批改"""

    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if submission.status not in ("uploaded", "failed"):
        raise HTTPException(status_code=400, detail=f"当前状态不允许批改: {submission.status}")

    submission.status = "processing"
    db.commit()

    try:
        result = await grading_manager.grade(
            image_path=submission.original_image_path,
            question_type="default",
            subject="数学",
        )
    except Exception as e:
        submission.status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"批改失败: {e}")

    if not result.get("success"):
        submission.status = "failed"
        db.commit()
        return {"success": False, "error": result.get("error", "批改失败"), "provider": result.get("provider")}

    questions = result.get("questions", [])

    # 保存逐题批改结果
    for q in questions:
        conf = q.get("confidence")
        qr = QuestionResult(
            submission_id=submission.id,
            question_no=int(q.get("question_no") or 0) or 1,
            question_type=q.get("question_type") or "default",
            student_answer_text=q.get("student_answer"),
            correct_answer_text=q.get("correct_answer"),
            llm_raw_output=result.get("raw_output"),
            judgement_raw=q.get("judgement"),
            judgement_final=q.get("judgement"),
            error_type=q.get("error_type"),
            confidence_final=float(conf) if isinstance(conf, (int, float)) else None,
            bbox_answer=q.get("bbox"),
        )
        db.add(qr)

    # 红笔回显
    annotated = annotate_image(submission.original_image_path, questions,
                               result.get("analysis", ""))
    submission.annotated_image_path = annotated
    submission.grading_summary = result.get("analysis")

    # 成本估算
    cost = estimate_cost(result.get("prompt_tokens", 0), result.get("completion_tokens", 0))
    submission.prompt_tokens = cost["prompt_tokens"]
    submission.completion_tokens = cost["completion_tokens"]
    submission.cost_estimate = cost["cost"]

    submission.status = "completed"
    db.commit()

    return {
        "success": True,
        "submission_id": submission.id,
        "result_id": qr.id,
        "judgement": result.get("judgement"),
        "confidence": result.get("confidence"),
        "analysis": result.get("analysis"),
    }


@router.get("/result/{submission_id}")
async def get_result(submission_id: int, db: Session = Depends(get_db)):
    """获取批改结果"""

    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    results = db.query(QuestionResult).filter(QuestionResult.submission_id == submission_id).all()

    annotated_url = None
    if submission.annotated_image_path:
        annotated_url = f"/uploads/{Path(submission.annotated_image_path).name}"

    return {
        "success": True,
        "submission_id": submission_id,
        "status": submission.status,
        "summary": submission.grading_summary,
        "annotated_image_url": annotated_url,
        "results": [
            {
                "id": r.id,
                "question_no": r.question_no,
                "question_type": r.question_type,
                "judgement": r.judgement_final,
                "error_type": r.error_type,
                "confidence": r.confidence_final,
                "student_answer": r.student_answer_text,
                "correct_answer": r.correct_answer_text,
            }
            for r in results
        ],
    }
