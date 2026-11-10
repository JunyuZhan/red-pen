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

# 包含路由
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(upload.router, prefix="/api/upload", tags=["上传"])
app.include_router(grading.router, prefix="/api/grading", tags=["批改"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["看板"])
app.include_router(mobile.router, prefix="/api/mobile", tags=["手机端"])
app.include_router(settings_routes.router, prefix="/api/settings", tags=["设置"])

# 根路径
@app.get("/", response_class=HTMLResponse)
async def root():
    return templates.TemplateResponse("index.html", {
        "request": {},
        "app_name": settings.APP_NAME,
        "version": settings.VERSION
    })

# 手机端页面
@app.get("/mobile", response_class=HTMLResponse)
async def mobile_page():
    return templates.TemplateResponse("mobile.html", {
        "request": {},
        "app_name": settings.APP_NAME,
        "version": settings.VERSION
    })

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
    print("🗄️ 数据库初始化完成")

if __name__ == "__main__":
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, reload=True)