"""
学情分析路由：错误类型分布、逐题正确率、学生表现
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from collections import Counter, defaultdict

from ..database import get_db, QuestionResult, Submission, Student

router = APIRouter()

SUBMISSION_STUDENT = Submission.student_id


@router.get("/analytics")
async def analytics(db: Session = Depends(get_db)):
    """学情分析汇总"""

    results = db.query(QuestionResult).all()

    # 错误类型分布
    error_types = Counter()
    for r in results:
        if r.judgement_final == "wrong" and r.error_type:
            error_types[r.error_type] += 1

    # 判定分布
    judgements = Counter(r.judgement_final or "unclear" for r in results)

    # 按学生统计（student_id 即 Submission.student_id）
    sub_map = {s.id: s for s in db.query(Submission).all()}
    stu_map = {s.id: s.name for s in db.query(Student).all()}
    per_student = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        sub = sub_map.get(r.submission_id)
        if not sub:
            continue
        key = sub.student_id
        per_student[key]["total"] += 1
        if r.judgement_final == "correct":
            per_student[key]["correct"] += 1

    students_stat = []
    for sid, st in sorted(per_student.items(), key=lambda x: -x[1]["total"]):
        acc = st["correct"] / st["total"] if st["total"] else None
        students_stat.append({
            "student_id": sid,
            "name": stu_map.get(sid, f"学生{sid}"),
            "total": st["total"],
            "correct": st["correct"],
            "accuracy": round(acc, 3) if acc is not None else None,
        })

    # 置信度偏低的题目（需要老师复核）
    needs_review = [
        {"id": r.id, "submission_id": r.submission_id, "question_no": r.question_no,
         "judgement": r.judgement_final, "confidence": r.confidence_final,
         "note": r.review_note}
        for r in results
        if (r.needs_review and int(r.needs_review)) or
           (r.confidence_final is not None and r.confidence_final < 0.6)
    ]

    return {
        "success": True,
        "total_questions": len(results),
        "judgement_distribution": dict(judgements),
        "error_type_distribution": dict(error_types.most_common()),
        "students": students_stat,
        "needs_review_count": len(needs_review),
        "needs_review": needs_review[:20],
    }


VALID_JUDGEMENTS = ("correct", "wrong", "partial")


@router.post("/override")
async def teacher_override(payload: dict, db: Session = Depends(get_db)):
    """教师复核修正：改写判定并记录 teacher_override（保留原始判定 judgement_raw）"""

    qr_id = payload.get("question_result_id")
    judgement = payload.get("judgement")
    error_type = payload.get("error_type")

    if not qr_id:
        raise HTTPException(status_code=400, detail="缺少 question_result_id")
    if judgement not in VALID_JUDGEMENTS:
        raise HTTPException(status_code=400, detail=f"判定必须是 {'/'.join(VALID_JUDGEMENTS)} 之一")

    qr = db.query(QuestionResult).filter(QuestionResult.id == qr_id).first()
    if not qr:
        raise HTTPException(status_code=404, detail="题目结果不存在")

    qr.judgement_final = judgement
    qr.teacher_override = judgement
    if error_type is not None:
        if error_type not in ("concept", "calculation", "reading", "format", ""):
            raise HTTPException(status_code=400, detail="error_type 取值非法")
        qr.error_type = error_type or None
    db.commit()

    return {"success": True, "id": qr.id, "judgement_final": qr.judgement_final,
            "teacher_override": qr.teacher_override, "error_type": qr.error_type}
