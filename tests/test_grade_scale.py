"""待办 n：作业等级换算测试（grade_scale + settings 接口 + 看板/学情/列表联动）"""
import os
import sys
import tempfile

os.environ["RED_PEN_DATA_DIR"] = tempfile.mkdtemp(prefix="rp_grade_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import init_db, SessionLocal, AppConfig, Assignment, Submission, QuestionResult
from backend.models.grade_scale import (
    DEFAULT_THRESHOLDS, get_thresholds, save_thresholds, compute_grade,
    accuracy_for_submission, accuracy_for_assignment, _validate,
)

PASS = []
def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("✅" if cond else "❌"), name)

init_db()

# 1. 默认阈值
db = SessionLocal()
check("默认阈值 90/75/60", get_thresholds(db) == {"A": 90, "B": 75, "C": 60})

# 2. compute_grade 边界
t = DEFAULT_THRESHOLDS
check("0.90→A", compute_grade(0.90, t) == "A")
check("0.899→B", compute_grade(0.899, t) == "B")
check("0.75→B", compute_grade(0.75, t) == "B")
check("0.60→C", compute_grade(0.60, t) == "C")
check("0.59→D", compute_grade(0.59, t) == "D")
check("None→None", compute_grade(None, t) is None)
check("越界→None", compute_grade(1.5, t) is None)

# 3. 阈值校验与保存
check("非法阈值(A<B)被拒", _validate({"A": 70, "B": 80, "C": 60}) is None)
check("非法阈值(超100)被拒", _validate({"A": 120, "B": 75, "C": 60}) is None)
save_thresholds(db, {"A": 85, "B": 70, "C": 50})
check("保存后读取生效", get_thresholds(db) == {"A": 85, "B": 70, "C": 50})
check("新阈值换算", compute_grade(0.72, get_thresholds(db)) == "B")
save_thresholds(db, {"A": 90, "B": 75, "C": 60})  # 恢复默认

# 4. 造数据：一个作业两份提交，判定 correct/wrong/partial
a = Assignment(class_id=1, title="单元测试卷", subject_id=1)
db.add(a); db.commit()
s1 = Submission(assignment_id=a.id, student_id=1, original_image_path="/tmp/x.jpg", status="completed")
s2 = Submission(assignment_id=a.id, student_id=2, original_image_path="/tmp/y.jpg", status="completed")
db.add_all([s1, s2]); db.commit()
db.add_all([
    QuestionResult(submission_id=s1.id, question_no=1, question_type="fill", judgement_final="correct"),
    QuestionResult(submission_id=s1.id, question_no=2, question_type="fill", judgement_final="wrong"),
    QuestionResult(submission_id=s1.id, question_no=3, question_type="fill", judgement_final="correct", needs_review=1),
    QuestionResult(submission_id=s2.id, question_no=1, question_type="fill", judgement_final="correct"),
    QuestionResult(submission_id=s2.id, question_no=2, question_type="fill", judgement_final=None),  # 未判定不计
])
db.commit()

check("单份正确率 2/3", abs(accuracy_for_submission(db, s1.id) - 2/3) < 1e-9)
check("未判定题不计入", accuracy_for_submission(db, s2.id) == 1.0)
acc_a, n_q = accuracy_for_assignment(db, a.id)
check("作业正确率 3/4", abs(acc_a - 0.75) < 1e-9 and n_q == 4)
check("作业等级 B", compute_grade(acc_a, get_thresholds(db)) == "B")

# 5. API 接口（TestClient 不触发 startup，库已 init_db）
from fastapi.testclient import TestClient
from backend.app import app
client = TestClient(app)

# 需要 PIN cookie（settings 全路由 require_admin）
client.post("/api/auth/pin-login", json={"pin": "123456"})

r = client.get("/api/settings/grade")
check("GET /settings/grade", r.status_code == 200 and r.json()["thresholds"]["A"] == 90)

r = client.post("/api/settings/grade", json={"a": 90, "b": 80, "c": 85})
check("非法阈值 POST 400", r.status_code == 400)

r = client.post("/api/settings/grade", json={"a": 88, "b": 72, "c": 55})
check("POST 保存成功", r.status_code == 200 and r.json()["thresholds"] == {"A": 88, "B": 72, "C": 55})
check("持久化生效", get_thresholds(db)["A"] == 88)
save_thresholds(db, {"A": 90, "B": 75, "C": 60})

r = client.get("/api/dashboard/summary")
ag = r.json().get("assignment_grades", [])
check("summary 含作业等级", any(x["assignment_id"] == a.id and x["grade"] == "B" for x in ag))

r = client.get("/api/mobile/list")
row = next((x for x in r.json()["submissions"] if x["id"] == s1.id), None)
check("列表含等级/正确率", row and row["grade"] == "C" and abs(row["accuracy"] - 0.667) < 0.01)

r = client.get("/api/analytics/analytics")
stu = next((x for x in r.json()["students"] if x["student_id"] == 1), None)
check("学情页学生含等级", stu and stu["grade"] == "C")

db.close()
print(f"\n共 {len(PASS)} 项，" + ("全部 PASS ✅" if all(p for _, p in PASS) else "存在失败 ❌"))
sys.exit(0 if all(p for _, p in PASS) else 1)
