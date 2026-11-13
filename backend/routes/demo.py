"""
一键体验演示数据（PRD 待办全部完成后的口碑向增量）：
没有真实 API Key 时，老师也能在工作台一键看到完整的批改产物
（作业列表 / 题目判定 / 学情统计 / 导出），用于熟悉软件流程。

纪律（必须遵守）：
- 所有演示数据带「【演示】」标记，可一键清除，不会污染真实班级；
- 演示判定是内置的假数据，不是 AI 批改结果；接口与页面必须如实说明。
"""

from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db, Class, Student, Assignment, Submission, QuestionResult
from ..config import settings

router = APIRouter()

DEMO_CLASS_NAME = "【演示】一年级1班"
DEMO_ASG_TITLE = "【演示】20以内加减法练习"
DEMO_STUDENTS = ["王小明", "李小红", "张小刚"]

# (题号, 题型, 学生答案, 正确答案, 判定, 知识点, 置信度, 待复核, 错误类型)
DEMO_QUESTIONS = [
    (1, "口算", "15", "15", "correct", "20以内进位加法", 0.95, 0, None),
    (2, "口算", "12", "13", "wrong", "20以内进位加法", 0.92, 0, "计算错误"),
    (3, "口算", "9", "9", "correct", "20以内退位减法", 0.94, 0, None),
    (4, "口算", "16", "16", "correct", "20以内进位加法", 0.91, 0, None),
    (5, "填空", "7", "6", "partial", "加减法应用", 0.55, 1, "部分正确"),
]

# 每个学生的作答微调：第2题小红写对、第5题小刚判 unclear
STUDENT_VARIANTS = {
    "王小明": {},
    "李小红": {2: ("14", "wrong")},
    "张小刚": {5: ("6", "wrong")},
}

DEMO_DIR = None  # 运行时由 settings 推导


def _demo_dir() -> Path:
    d = Path(settings.UPLOAD_DIR) / "demo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_demo_image(name: str) -> Path:
    """生成一张简单的演示作业图（印刷题干+学生名），让详情页有图可看。"""
    from PIL import Image, ImageDraw

    path = _demo_dir() / f"demo_{name}.png"
    if path.exists():
        return path
    img = Image.new("L", (600, 800), 255)
    draw = ImageDraw.Draw(img)
    lines = [
        f"姓名：{name}    【演示数据·非真实批改】",
        "",
        "一、口算。",
        "  1) 8+7=      2) 5+8=      3) 14-5=",
        "  4) 9+7=      5) 13-6=",
        "",
        "（此图为演示占位图，仅用于展示软件流程）",
    ]
    y = 40
    for ln in lines:
        draw.text((40, y), ln, fill=30)
        y += 46
    img.save(path)
    return path


def _find_demo(db: Session):
    return db.query(Class).filter(Class.name == DEMO_CLASS_NAME).first()


@router.post("/seed")
async def seed_demo(db: Session = Depends(get_db)):
    existing = _find_demo(db)
    if existing:
        asg = db.query(Assignment).filter(
            Assignment.class_id == existing.id).first()
        if asg:
            return {"success": True, "already": True,
                    "assignment_id": asg.id, "class_id": existing.id}
    cls = Class(name=DEMO_CLASS_NAME, teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls)
    db.commit()
    db.refresh(cls)

    students = {}
    for name in DEMO_STUDENTS:
        st = Student(name=name, class_id=cls.id)
        db.add(st)
        db.commit()
        db.refresh(st)
        students[name] = st

    asg = Assignment(class_id=cls.id, title=DEMO_ASG_TITLE, subject_id=1)
    db.add(asg)
    db.commit()
    db.refresh(asg)

    for name, st in students.items():
        img_path = _make_demo_image(name)
        sub = Submission(
            assignment_id=asg.id, student_id=st.id,
            original_image_path=str(img_path),
            status="completed",
            grading_summary="演示数据：4题正确 1题待复核（非AI批改）",
            prompt_tokens=0, completion_tokens=0, cost_estimate=0.0,
            duration_seconds=1.0,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        variants = STUDENT_VARIANTS.get(name, {})
        for (no, qtype, ans, correct, judge, kp, conf, review, err) in DEMO_QUESTIONS:
            if no in variants:
                ans, judge = variants[no]
            db.add(QuestionResult(
                submission_id=sub.id, question_no=no, question_type=qtype,
                knowledge_point=kp,
                student_answer_text=ans, correct_answer_text=correct,
                judgement_raw=judge, judgement_final=judge,
                error_type=err, confidence_final=conf,
                needs_review=review,
                review_note="演示数据：置信度低，进复核队列" if review else None,
                llm_raw_output={"demo": True, "question_text": f"第{no}题（演示）"},
            ))
    db.commit()
    return {"success": True, "already": False,
            "assignment_id": asg.id, "class_id": cls.id}


@router.post("/clear")
async def clear_demo(db: Session = Depends(get_db)):
    cls = _find_demo(db)
    if not cls:
        return {"success": True, "removed": 0}
    removed = 0
    for asg in db.query(Assignment).filter(Assignment.class_id == cls.id).all():
        subs = db.query(Submission).filter(
            Submission.assignment_id == asg.id).all()
        for sub in subs:
            removed += db.query(QuestionResult).filter(
                QuestionResult.submission_id == sub.id).delete()
            p = Path(sub.original_image_path)
            if p.exists() and "demo" in p.parts:
                p.unlink()
            db.delete(sub)
        db.delete(asg)
    removed += db.query(Student).filter(
        Student.class_id == cls.id).delete()
    db.delete(cls)
    db.commit()
    return {"success": True, "removed": removed}
