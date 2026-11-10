"""
批改路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from ..database import get_db, Submission, QuestionResult
from ..models.grading_engine import grading_manager

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

    # 保存批改结果
    qr = QuestionResult(
        submission_id=submission.id,
        question_no=1,
        question_type="default",
        llm_raw_output=result.get("raw_output"),
        judgement_raw=result.get("judgement"),
        judgement_final=result.get("judgement"),
        confidence_final=result.get("confidence"),
    )
    db.add(qr)
    submission.status = "completed"
    db.commit()
    db.refresh(qr)

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
    return {
        "success": True,
        "submission_id": submission_id,
        "status": submission.status,
        "results": [
            {
                "id": r.id,
                "question_no": r.question_no,
                "question_type": r.question_type,
                "judgement": r.judgement_final,
                "confidence": r.confidence_final,
                "raw_output": r.llm_raw_output,
            }
            for r in results
        ],
    }
