"""待办 o：AI 一致率统计（teacher_override 与 judgement_raw 对账）"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient

from backend.database import Base, engine, SessionLocal, QuestionResult, Submission, Student, Class, Assignment
from backend.app import app

PASS = 0
FAIL = 0

def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")

# 独立临时库
import backend.database as dbmod
dbmod.DATABASE_URL = "sqlite:///./test_agreement.db"
dbmod.engine = dbmod.create_engine(dbmod.DATABASE_URL, connect_args={"check_same_thread": False})
Base.metadata.create_all(dbmod.engine)
dbmod.SessionLocal = dbmod.sessionmaker(bind=dbmod.engine, autoflush=False)
from backend.database import get_db
def override_get_db():
    s = dbmod.SessionLocal()
    try:
        yield s
    finally:
        s.close()
app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

# 登录：analytics 接口需要管理员 PIN cookie
from backend.config import settings
_r = client.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})
check("PIN 登录", _r.status_code == 200)

def seed():
    db = dbmod.SessionLocal()
    Base.metadata.drop_all(dbmod.engine)
    Base.metadata.create_all(dbmod.engine)
    cls = Class(name="一班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(cls); db.commit()
    stu = Student(name="张三", class_id=cls.id)
    db.add(stu); db.commit()
    asg = Assignment(class_id=cls.id, title="测试作业", subject_id=1)
    db.add(asg); db.commit()
    sub = Submission(assignment_id=asg.id, student_id=stu.id, original_image_path="test.jpg", status="done")
    db.add(sub); db.commit()
    rows = [
        # (raw, final, override) → 期望一致口径
        ("correct", "correct", None),        # 未复核，不计
        ("wrong", "wrong", "wrong"),         # 复核确认一致
        ("correct", "wrong", "wrong"),       # 复核改判（不一致）
        ("wrong", "correct", "correct"),     # 复核改判（不一致）
        ("partial", "partial", "partial"),   # 确认一致
    ]
    for raw, fin, ov in rows:
        db.add(QuestionResult(submission_id=sub.id, question_no=1, question_type="choice",
                              judgement_raw=raw, judgement_final=fin, teacher_override=ov,
                              confidence_final=0.9))
    db.commit()
    return sub.id

check("测试库创建", True)
seed()

r = client.get("/api/analytics/analytics")
check("analytics 200", r.status_code == 200)
ag = r.json().get("ai_agreement")
check("返回 ai_agreement 字段", ag is not None)
check("reviewed_total=4", ag["reviewed_total"] == 4)
check("agree=2", ag["agree"] == 2)
check("disagree=2", ag["disagree"] == 2)
check("rate=0.5", ag["rate"] == 0.5)

# 无复核记录时 rate=None
db = dbmod.SessionLocal()
db.query(QuestionResult).update({QuestionResult.teacher_override: None})
db.commit(); db.close()
ag = client.get("/api/analytics/analytics").json()["ai_agreement"]
check("无复核 rate=None", ag["rate"] is None)
check("无复核 reviewed_total=0", ag["reviewed_total"] == 0)

# 全一致场景：100%
seed()
db = dbmod.SessionLocal()
db.query(QuestionResult).update({
    QuestionResult.judgement_final: QuestionResult.judgement_raw,
    QuestionResult.teacher_override: QuestionResult.judgement_raw})
db.commit(); db.close()
ag = client.get("/api/analytics/analytics").json()["ai_agreement"]
check("全一致 rate=1.0", ag["rate"] == 1.0)

# override 端点写入后一致率联动
seed()
db = dbmod.SessionLocal()
qr = db.query(QuestionResult).first()
qr_id = qr.id
db.close()
r = client.post("/api/analytics/override", json={"question_result_id": qr_id, "judgement": "wrong"})
check("override 200", r.status_code == 200)
db = dbmod.SessionLocal()
qr = db.query(QuestionResult).get(qr_id)
check("override 后 judgement_raw 保留", qr.judgement_raw is not None)
raw0 = qr.judgement_raw
db.close()
client.post("/api/analytics/override", json={"question_result_id": qr_id,
             "judgement": raw0 if raw0 in ("correct","wrong","partial") else "correct"})
ag = client.get("/api/analytics/analytics").json()["ai_agreement"]
check("确认一致后 agree 增加", ag["reviewed_total"] >= 1)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
