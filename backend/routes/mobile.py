"""
手机端路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db, Submission, Assignment
from ..models.grade_scale import get_thresholds, compute_grade, accuracy_for_submission

router = APIRouter()


@router.get("/queue-counts")
async def queue_counts(db: Session = Depends(get_db)):
    """手机端顶部队列计数：已传N / 批改M / 完成K（全库统计）"""
    total = db.query(Submission).count()
    processing = db.query(Submission).filter(Submission.status == "processing").count()
    completed = db.query(Submission).filter(Submission.status == "completed").count()
    failed = db.query(Submission).filter(Submission.status == "failed").count()
    return {"success": True, "total": total, "processing": processing,
            "completed": completed, "failed": failed}


@router.get("/list")
async def list_submissions(db: Session = Depends(get_db)):
    """提交记录列表（工作台用）"""
    subs = db.query(Submission).order_by(Submission.id.desc()).limit(100).all()
    thresholds = get_thresholds(db)
    return {
        "success": True,
        "submissions": [
            {
                "id": s.id,
                "status": s.status,
                "summary": (s.grading_summary or "")[:30] or None,
                "error_message": s.error_message,
                "retry_count": s.retry_count or 0,
                **(
                    (lambda a: {"accuracy": round(a, 3) if a is not None else None,
                                "grade": compute_grade(a, thresholds)})(
                        accuracy_for_submission(db, s.id))
                    if s.status == "completed" else {"accuracy": None, "grade": None}
                ),
            }
            for s in subs
        ],
    }


@router.get("/classes-students")
async def classes_students(db: Session = Depends(get_db)):
    """手机端上传用：班级及学生列表"""
    from ..database import Class, Student
    classes = db.query(Class).all()
    result = []
    for c in classes:
        students = db.query(Student).filter(Student.class_id == c.id).order_by(Student.id).all()
        result.append({
            "id": c.id,
            "name": c.name,
            "students": [{"id": s.id, "name": s.name} for s in students],
        })
    return {"success": True, "classes": result}


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
        "error_message": submission.error_message,
        "retry_count": submission.retry_count or 0,
    }
