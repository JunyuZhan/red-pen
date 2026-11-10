"""
FastAPI应用程序主入口
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import json
from pathlib import Path
import os

# 导入路由
from .routes import auth, upload, grading, dashboard, mobile, settings as settings_routes
from .routes import manage, analytics, backup, network
from .routes.auth import require_admin
from fastapi import Depends
from .database import init_db
from .config import settings

# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="教师单机自用版作业批改系统",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（目录可能尚不存在，不能让启动崩溃）
from pathlib import Path as _Path
if _Path("frontend/static").exists():
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# 模板引擎
templates = Jinja2Templates(directory="frontend/templates")

import inspect as _inspect

def _render(template_name: str, request, **ctx):
    """兼容新旧 starlette 的 TemplateResponse 参数顺序"""
    params = list(_inspect.signature(templates.TemplateResponse).parameters)
    if params and params[0] == "request":  # starlette >= 0.29
        return templates.TemplateResponse(request, template_name, {"request": request, **ctx})
    return templates.TemplateResponse(template_name, {"request": request, **ctx})

# 包含路由
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(upload.router, prefix="/api/upload", tags=["上传"])
app.include_router(grading.router, prefix="/api/grading", tags=["批改"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["看板"])
app.include_router(mobile.router, prefix="/api/mobile", tags=["手机端"])
app.include_router(settings_routes.router, prefix="/api/settings", tags=["设置"],
                   dependencies=[Depends(require_admin)])
app.include_router(manage.router, prefix="/api/manage", tags=["班级学生"],
                   dependencies=[Depends(require_admin)])
app.include_router(analytics.router, prefix="/api/analytics", tags=["学情分析"],
                   dependencies=[Depends(require_admin)])
app.include_router(backup.router, prefix="/api/backup", tags=["备份恢复"],
                   dependencies=[Depends(require_admin)])
# 网络信息：公开（不落敏感数据，仅本机局域网 IP/端口），手机端扫码前无需登录
app.include_router(network.router, prefix="/api/network", tags=["网络"])

# 根路径
from fastapi import Request

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return _render("index.html", request,
        app_name=settings.APP_NAME,
        version=settings.VERSION)

# 手机端页面
@app.get("/mobile", response_class=HTMLResponse)
async def mobile_page(request: Request):
    return _render("mobile.html", request,
        app_name=settings.APP_NAME,
        version=settings.VERSION)

# 设置页面
@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    with open("frontend/templates/settings.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# 健康检查
@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "红笔系统运行正常"}

# 启动时初始化数据库
@app.on_event("startup")
async def startup_event():
    init_db()
    # 队列持久化恢复（PRD 4.4）：服务重启后，卡在 processing 的任务转回 failed，可重试
    from .database import SessionLocal, Submission
    db = SessionLocal()
    try:
        stuck = db.query(Submission).filter(Submission.status == "processing").all()
        for s in stuck:
            s.status = "failed"
            s.error_message = "服务重启导致批改中断，请重试"
        if stuck:
            db.commit()
            print(f"♻️ 恢复队列：{len(stuck)} 条中断任务已转回失败状态，可重试")
    finally:
        db.close()
    print("🗄️ 数据库初始化完成")

if __name__ == "__main__":
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, reload=True)