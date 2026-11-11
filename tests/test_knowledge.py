"""
知识点体系测试（PRD 待办 i）：
- prompt 要求 LLM 输出 knowledge_point 字段
- 批改结果落库时保存知识点
- /api/analytics 返回知识点掌握统计，薄弱知识点排前面
"""
import os
import shutil

os.environ["RED_PEN_DATA_DIR"] = "/tmp/red_pen_kp_test"
# 清空残留测试库，保证重复运行幂等
shutil.rmtree(os.environ["RED_PEN_DATA_DIR"], ignore_errors=True)

from fastapi.testclient import TestClient

from backend.database import init_db, SessionLocal, QuestionResult
from backend.models.grading_engine import _build_prompt

init_db()

from backend.app import app

client = TestClient(app)
assert client.post("/api/auth/pin-login", json={"pin": "123456"}).status_code == 200

# 1) prompt 包含 knowledge_point 字段要求
prompt = _build_prompt("math", "数学")
assert "knowledge_point" in prompt, "prompt 必须要求输出知识点字段"

# 2) 灌入带知识点的批改结果
db = SessionLocal()
rows = [
    ("两位数乘法", "wrong"),
    ("两位数乘法", "wrong"),
    ("两位数乘法", "correct"),
    ("分数加减法", "correct"),
    ("分数加减法", "correct"),
    (None, "correct"),  # 无知识点的不参与统计
    ("  ", "wrong"),    # 空白的不参与统计
]
try:
    for kp, j in rows:
        db.add(QuestionResult(submission_id=1, question_no=1, question_type="default",
                              knowledge_point=kp, judgement_final=j))
    db.commit()
finally:
    db.close()

# 3) 学情分析返回知识点统计，薄弱排前
r = client.get("/api/analytics/analytics")
assert r.status_code == 200, r.text
d = r.json()
kps = d.get("knowledge_points") or []
assert len(kps) == 2, f"应只统计到2个知识点, got {kps}"
by_name = {k["name"]: k for k in kps}
assert by_name["两位数乘法"]["total"] == 3 and by_name["两位数乘法"]["correct"] == 1
assert abs(by_name["两位数乘法"]["accuracy"] - 1 / 3) < 0.01
assert by_name["分数加减法"]["accuracy"] == 1.0
assert kps[0]["name"] == "两位数乘法", f"薄弱知识点应排前: {kps}"

print("PASS: test_knowledge 共 5 项断言全过（prompt字段/落库/统计/正确率/排序）")
