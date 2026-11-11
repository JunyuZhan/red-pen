"""待办 p：首次启动三步引导 + 工作台上下文切换（/api/setup/*）"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient

import backend.database as dbmod
from backend.database import Base, AppConfig, StageConfig, SubjectConfig, Class

dbmod.DATABASE_URL = "sqlite:///./test_setup.db"
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
from backend.config import settings
client.post("/api/auth/pin-login", json={"pin": settings.ADMIN_PIN})

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
    db.add_all([StageConfig(name="小学", enabled=True), StageConfig(name="中学", enabled=True)])
    db.add_all([
        SubjectConfig(name="数学", stage_id=1, enabled=True),
        SubjectConfig(name="语文", stage_id=1, enabled=True),
        SubjectConfig(name="英语", stage_id=1, enabled=True),
    ])
    db.commit(); db.close()

reset()

# --- 状态接口（公开） ---
r = client.get("/api/setup/status")
check("状态接口 200", r.status_code == 200)
d = r.json()
check("未完成引导 setup_completed=false", d["setup_completed"] is False)
check("默认学段存在", d["defaults"]["stage"] == "小学")
check("返回学段列表", any(s["name"] == "中学" for s in d["stages"]))
check("返回学科列表", any(s["name"] == "数学" for s in d["subjects"]))
check("初始无班级(空库)", d["classes"] == [])

# --- 未登录不能保存 ---
anon = TestClient(app)  # 未携带 PIN cookie
r = anon.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
check("未登录 401", r.status_code == 401)
r = anon.post("/api/setup/context", json={"class_name": "x"})
check("未登录切换 401", r.status_code == 401)

# --- 完成引导 ---
r = client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
check("完成引导 200", r.status_code == 200)
d = r.json()
check("返回默认值", d["defaults"]["class_name"] == "三年二班")
check("返回班级 id", isinstance(d["class_id"], int))
db = dbmod.SessionLocal()
check("setup_completed 已标记", db.query(AppConfig).filter_by(key="setup_completed", value="true").count() == 1)
check("新班级已创建", db.query(Class).filter_by(name="三年二班").count() == 1)
check("默认学段写入", db.query(AppConfig).filter_by(key="default_stage", value="小学").count() == 1)
db.close()

# --- 状态更新 ---
r = client.get("/api/setup/status")
check("完成后 setup_completed=true", r.json()["setup_completed"] is True)
check("班级出现在列表", any(c["name"] == "三年二班" for c in r.json()["classes"]))

# --- 空值校验 ---
r = client.post("/api/setup/complete", json={"stage": "", "subject": "数学", "class_name": "x"})
check("空学段 400", r.status_code == 400)
r = client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "  "})
check("空班级 400", r.status_code == 400)

# --- 上下文切换：切换到已有班级 + 新学科（自动创建） ---
r = client.post("/api/setup/context", json={"stage": "中学", "subject": "物理", "class_name": "三年二班"})
check("切换 200", r.status_code == 200)
db = dbmod.SessionLocal()
check("中学学段自动创建", db.query(StageConfig).filter_by(name="中学").count() >= 1)
check("物理学科自动创建", db.query(SubjectConfig).filter_by(name="物理").count() == 1)
check("切换后 default_subject=物理", db.query(AppConfig).filter_by(key="default_subject", value="物理").count() == 1)
check("切换不重复标记完成(仍为true)", db.query(AppConfig).filter_by(key="setup_completed").first().value == "true")
db.close()

# --- 上下文切换：只改班级，学段学科继承 ---
r = client.post("/api/setup/context", json={"class_name": "三年二班"})
check("只改班级 200", r.status_code == 200)
db = dbmod.SessionLocal()
check("学段仍为中学", db.query(AppConfig).filter_by(key="default_stage", value="中学").count() == 1)
db.close()

# --- 空班级切换：继承当前默认 ---
r = client.post("/api/setup/context", json={"class_name": ""})
check("切换空班级继承默认 200", r.status_code == 200)

# --- 重复完成引导：不重复建班 ---
n_before = dbmod.SessionLocal().query(Class).count()
r = client.post("/api/setup/complete", json={"stage": "小学", "subject": "数学", "class_name": "三年二班"})
check("重复完成 200", r.status_code == 200)
check("班级不重复创建", dbmod.SessionLocal().query(Class).count() == n_before)

# --- 工作台页面包含引导元素 ---
r = client.get("/")
check("工作台 200", r.status_code == 200)
html = r.text
check("包含引导浮层", "wizGate" in html)
check("包含上下文切换器", "ctxBtn" in html)
check("包含 setup 接口调用", "/api/setup/status" in html)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
