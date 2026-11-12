"""
教师看板路由
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db, Submission, Assignment, QuestionResult, Student as StudentModel
from ..models.grade_scale import get_thresholds, compute_grade, accuracy_for_assignment

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

    # 作业等级换算（PRD 待办 n）：每个作业的正确率 → A/B/C/D
    thresholds = get_thresholds(db)
    from ..database import Class as ClassModel
    assignment_grades = []
    for a in db.query(Assignment).order_by(Assignment.id.desc()).all():
        acc, n_q = accuracy_for_assignment(db, a.id)
        assignment_grades.append({
            "assignment_id": a.id,
            "title": a.title,
            "questions": n_q,
            "accuracy": round(acc, 3) if acc is not None else None,
            "grade": compute_grade(acc, thresholds),
        })

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
        "assignment_grades": assignment_grades,
        "classes": [{"id": c.id, "name": c.name}
                    for c in db.query(ClassModel).order_by(ClassModel.id).all()],
        # 学生列表（工作台「学生错题本」下拉用）
        "students": [{"id": st.id, "name": st.name}
                     for st in db.query(StudentModel).order_by(StudentModel.id).all()],
    }
