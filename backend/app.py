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
from .routes import manage, analytics, backup, network, setup as setup_routes, demo
from .routes.auth import require_admin
from fastapi import Depends
from .database import init_db
from .logging_setup import get_logger, log_path, open_logs_folder, recent_lines
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

# 静态文件（目录可能尚不存在，不能让启动崩溃）；PyInstaller 打包后资源在 _MEIPASS
from pathlib import Path as _Path
from backend.config import RESOURCE_DIR as _RES
if (_Path("frontend/static")).exists():
    app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
elif (_RES / "frontend" / "static").exists():
    app.mount("/static", StaticFiles(directory=str(_RES / "frontend" / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# 模板引擎
if (_RES / "frontend" / "templates").exists():
    templates = Jinja2Templates(directory=str(_RES / "frontend" / "templates"))
else:
    templates = Jinja2Templates(directory="frontend/templates")

import inspect as _inspect

def _render(template_name: str, request, **ctx):
    """兼容新旧 starlette 的 TemplateResponse 参数顺序"""
    params = list(_inspect.signature(templates.TemplateResponse).parameters)
    try:
        if params and params[0] == "request":  # starlette >= 0.29
            return templates.TemplateResponse(request, template_name, {"request": request, **ctx})
        return templates.TemplateResponse(template_name, {"request": request, **ctx})
    except Exception as e:  # noqa: BLE001  模板缺失/损坏时不给用户看 500 堆栈
        logger = get_logger()
        logger.error("页面模板渲染失败 %s: %s", template_name, e)
        return HTMLResponse(
            "<h2 style='font-family:sans-serif'>页面资源加载失败</h2>"
            "<p style='font-family:sans-serif'>请重新安装程序，或在设置页使用备份恢复。"
            "详情见日志文件。</p>",
            status_code=500,
        )

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
app.include_router(setup_routes.router, prefix="/api/setup", tags=["首次引导"])
# 网络信息：公开（不落敏感数据，仅本机局域网 IP/端口），手机端扫码前无需登录
app.include_router(network.router, prefix="/api/network", tags=["网络"])
# 演示数据：需要管理员权限（会写入/清除班级数据）
app.include_router(demo.router, prefix="/api/demo", tags=["演示数据"],
                   dependencies=[Depends(require_admin)])

# 根路径
from fastapi import Request

# ---------- 全局兜底异常处理（第六轮健壮性）：未捕获异常不返回裸 500 ----------
import traceback as _tb
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    get_logger().error("未捕获异常 %s %s: %s\n%s", request.method, request.url.path,
                       exc, _tb.format_exc())
    if request.url.path.startswith("/api"):
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "服务器内部错误，请重试；若反复出现请反馈日志文件"},
        )
    return HTMLResponse(
        "<h2 style='font-family:sans-serif'>服务器内部错误</h2>"
        "<p style='font-family:sans-serif'>操作未完成，请重试。详情见日志文件。</p>",
        status_code=500,
    )

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
    _tpl = _RES / "frontend" / "templates" / "settings.html"
    _tpl_path = _tpl if _tpl.exists() else _Path("frontend/templates/settings.html")
    if not _tpl_path.exists():
        get_logger().error("设置页模板缺失: %s", _tpl_path)
        return HTMLResponse(
            "<h2 style='font-family:sans-serif'>设置页面资源缺失</h2>"
            "<p style='font-family:sans-serif'>请重新安装程序。详情见日志文件。</p>",
            status_code=500,
        )
    with open(_tpl_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())

# 健康检查
@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "红笔系统运行正常"}

# 启动时初始化数据库
@app.get("/api/logs/recent")
def api_logs_recent(n: int = 100):
    return {"success": True, "lines": recent_lines(min(max(n, 10), 500))}


@app.post("/api/logs/open-folder")
def api_logs_open_folder():
    d = open_logs_folder()
    return {"success": not str(d).startswith("打开失败"), "path": d}


@app.on_event("startup")
async def startup_event():
    logger = get_logger()
    logger.info("红笔启动，日志文件: %s", log_path())
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