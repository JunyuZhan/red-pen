"""
手机端路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db, Submission, Assignment

router = APIRouter()


@router.get("/status/{submission_id}")
async def submission_status(submission_id: int, db: Session = Depends(get_db)):
    """手机端查询批改进度"""

    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    return {
        "success": True,
        "submission_id": submission_id,
        "status": submission.status,
    }
