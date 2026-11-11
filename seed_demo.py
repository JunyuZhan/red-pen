# -*- coding: utf-8 -*-
"""官网演示数据：一个班级8名学生×4次作业×逐题结果，覆盖3周，学情数据真实可信"""
import os, sys, random, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timedelta

random.seed(42)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "red_pen.db")
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"), exist_ok=True)
for f in (DB,):
    if os.path.exists(f): os.remove(f)

from backend.database import init_db, SessionLocal, StageConfig, SubjectConfig, Teacher, Class, Student, Assignment, Submission, QuestionResult

init_db()
db = SessionLocal()

stage = db.query(StageConfig).first() or StageConfig(name="小学"); db.add(stage); db.flush()
subject = db.query(SubjectConfig).first() or SubjectConfig(name="数学", stage_id=stage.id); db.add(subject); db.flush()
teacher = Teacher(name="张老师"); db.add(teacher); db.flush()
klass = Class(name="三年级(2)班", teacher_id=teacher.id, stage_id=stage.id, subject_id=subject.id); db.add(klass); db.flush()

names = ["王小明","李小红","张子轩","陈雨桐","刘一诺","赵浩然","孙悦然","周思远"]
students = []
for n in names:
    s = Student(name=n, class_id=klass.id, created_at=datetime.utcnow()-timedelta(days=30))
    db.add(s); students.append(s); db.flush()

titles = ["第1单元随堂练习","口算专项训练","应用题练习(一)","第2单元测验"]
base = datetime.utcnow() - timedelta(days=21)
assign_ids = []
for i, t in enumerate(titles):
    a = Assignment(class_id=klass.id, title=t, subject_id=subject.id,
                   created_at=base + timedelta(days=i*7))
    db.add(a); db.flush(); assign_ids.append(a.id)

# 每个学生能力系数 + 进步/退步趋势
ability = {s.id: random.uniform(0.55, 0.92) for s in students}
trend = {s.id: random.uniform(-0.03, 0.08) for s in students}
ERR = ["计算错误","概念错误","粗心","未作答","审题错误"]
KP = ["两位数乘法","分数初步认识","应用题-和差问题","口算速算","单位换算"]
qtypes = ["计算","计算","应用题","选择","填空"]

n_sub = 0
for ai, aid in enumerate(assign_ids):
    for s in students:
        acc = min(0.96, max(0.35, ability[s.id] + trend[s.id] * (ai+1) + random.uniform(-0.08, 0.08)))
        sub = Submission(
            assignment_id=aid, student_id=s.id, uploader_user_id=1,
            original_image_path="demo/seed.jpg", annotated_image_path="demo/seed.jpg",
            status="completed",
            grading_summary=f"共10题，对{round(acc*10)}题，错{10-round(acc*10)}题。{'整体掌握较好，注意审题。' if acc>0.8 else '计算粗心较多，建议错题重做。' if acc>0.6 else '本单元掌握薄弱，需专项练习。'}",
            prompt_tokens=random.randint(1400,1900), completion_tokens=random.randint(350,520),
            cost_estimate=round(random.uniform(1.3,2.1),2),
            created_at=base + timedelta(days=ai*7, hours=random.randint(1,10)),
        )
        db.add(sub); db.flush()
        for qno in range(1, 11):
            wrong = random.random() > acc
            j = "wrong" if wrong else "correct"
            qr = QuestionResult(
                submission_id=sub.id, question_no=qno,
                question_type=qtypes[(qno-1) % len(qtypes)],
                student_answer_text="24" if wrong else "48",
                correct_answer_text="48",
                judgement_raw=j, judgement_final=j,
                error_type=random.choice(ERR) if wrong else None,
                confidence_final=round(random.uniform(0.55,0.75) if (wrong and random.random()<0.3) else random.uniform(0.85,0.99), 2),
                bbox_question=[80+ (qno%2)*420, 90+((qno-1)//2)*150, 480+(qno%2)*420, 200+((qno-1)//2)*150],
                created_at=sub.created_at,
            )
            db.add(qr)
        n_sub += 1

db.commit()
total_q = db.query(QuestionResult).count()
print(f"SEEDED: {len(students)}学生 × {len(assign_ids)}作业 = {n_sub}份提交, {total_q}道题")
db.close()
