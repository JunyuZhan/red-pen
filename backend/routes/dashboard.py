"""
教师看板路由
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db, Submission, Assignment, QuestionResult

router = APIRouter()


@router.get("/summary")
async def dashboard_summary(db: Session = Depends(get_db)):
    """看板汇总数据"""

    total_submissions = db.query(Submission).count()
    completed = db.query(Submission).filter(Submission.status == "completed").count()
    failed = db.query(Submission).filter(Submission.status == "failed").count()
    total_assignments = db.query(Assignment).count()
    total_questions = db.query(QuestionResult).count()
    correct = db.query(QuestionResult).filter(QuestionResult.judgement_final == "correct").count()

    subs = db.query(Submission).all()
    total_tokens = sum((s.prompt_tokens or 0) + (s.completion_tokens or 0) for s in subs)
    total_cost = round(sum(s.cost_estimate or 0 for s in subs), 4)

    return {
        "success": True,
        "total_submissions": total_submissions,
        "completed": completed,
        "failed": failed,
        "total_assignments": total_assignments,
        "total_questions": total_questions,
        "correct_questions": correct,
        "accuracy": round(correct / total_questions, 3) if total_questions else None,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
    }
