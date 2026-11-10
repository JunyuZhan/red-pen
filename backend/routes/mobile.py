"""
手机端路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db, Submission, Assignment

router = APIRouter()


@router.get("/list")
async def list_submissions(db: Session = Depends(get_db)):
    """提交记录列表（工作台用）"""
    subs = db.query(Submission).order_by(Submission.id.desc()).limit(100).all()
    return {
        "success": True,
        "submissions": [
            {
                "id": s.id,
                "status": s.status,
                "summary": (s.grading_summary or "")[:30] or None,
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
    }
