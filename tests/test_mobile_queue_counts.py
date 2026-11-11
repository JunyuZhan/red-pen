"""待办 q：手机端顶部队列计数（已传N/批改M/完成K，/api/mobile/queue-counts）"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient

import backend.database as dbmod
from backend.database import Base, Submission

dbmod.DATABASE_URL = "sqlite:///./test_queue_counts.db"
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

from backend.app import app
app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")

def reset():
    db = dbmod.SessionLocal()
    Base.metadata.drop_all(dbmod.engine)
    Base.metadata.create_all(dbmod.engine)
    db.close()

reset()

# 1. 空库返回全 0
r = client.get("/api/mobile/queue-counts")
d = r.json()
check("接口200", r.status_code == 200)
check("success=True", d.get("success") is True)
check("空库total=0", d.get("total") == 0)
check("空库processing=0", d.get("processing") == 0)
check("空库completed=0", d.get("completed") == 0)
check("空库failed=0", d.get("failed") == 0)

# 2. 灌数据后计数正确
db = dbmod.SessionLocal()
from backend.database import Assignment
asg = Assignment(class_id=1, subject_id=1, title="测试作业")
db.add(asg); db.commit()
rows = [Submission(assignment_id=asg.id, student_id=1, original_image_path=f"u{i}.jpg", status=s) for i, s in enumerate(
        ["pending", "queued", "processing", "processing", "completed", "completed", "completed", "failed"])]
db.add_all(rows)
db.commit()
db.close()

d = client.get("/api/mobile/queue-counts").json()
check("total=8", d["total"] == 8)
check("processing=2", d["processing"] == 2)
check("completed=3", d["completed"] == 3)
check("failed=1", d["failed"] == 1)

# 3. 接口无需登录（公开只读，手机端老师/家长可看）
r2 = client.get("/api/mobile/queue-counts")
check("重复请求仍200", r2.status_code == 200)
check("响应含全部字段", all(k in r2.json() for k in ("total", "processing", "completed", "failed")))

# 4. 移动端页面包含徽章与轮询脚本
html = client.get("/mobile").text
check("页面含queue_badge", "queue_badge" in html)
check("页面含轮询逻辑", "refreshQueueBadge" in html)
check("页面含已传/批改/完成文案", "已传" in html and "批改" in html and "完成" in html)

print(f"PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
