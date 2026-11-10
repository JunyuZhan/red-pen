#!/usr/bin/env python3
"""
红笔系统演示版本
简化版FastAPI应用
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import json
from pathlib import Path
import os
import uuid
from datetime import datetime

# 创建FastAPI应用
app = FastAPI(
    title="红笔 · 教师单机自用版",
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

# 模板引擎
templates = Jinja2Templates(directory="frontend/templates")

# 数据目录
DATA_DIR = Path("./data")
UPLOAD_DIR = DATA_DIR / "uploads"

# 创建必要的目录
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 全局变量存储演示数据
demo_data = {
    "submissions": [],
    "assignments": []
}

# 根路径
@app.get("/", response_class=HTMLResponse)
async def root():
    return templates.TemplateResponse("index.html", {
        "request": {},
        "app_name": "红笔 · 教师单机自用版",
        "version": "V0.4"
    })

# 手机端页面
@app.get("/mobile", response_class=HTMLResponse)
async def mobile_page():
    return templates.TemplateResponse("mobile.html", {
        "request": {},
        "app_name": "红笔 · 教师单机自用版", 
        "version": "V0.4"
    })

# 健康检查
@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "红笔系统运行正常"}

# 上传作业
@app.post("/api/upload-mobile")
async def upload_mobile(
    file: UploadFile = File(...),
    assignment_name: str = Form(...),
    stage: str = Form(...),
    subject: str = Form(...),
    teacher_id: int = Form(...),
):
    """移动端上传作业"""
    
    # 验证文件类型
    file_extension = file.filename.split('.')[-1].lower()
    allowed_extensions = ["jpg", "jpeg", "png", "webp"]
    
    if file_extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_extension}"
        )
    
    # 生成文件名
    file_id = str(uuid.uuid4())
    filename = f"{file_id}.{file_extension}"
    file_path = UPLOAD_DIR / filename
    
    try:
        # 保存文件
        with open(file_path, "wb") as buffer:
            for chunk in file.file:
                buffer.write(chunk)
        
        # 创建作业记录
        assignment = {
            "id": len(demo_data["assignments"]) + 1,
            "name": assignment_name,
            "stage": stage,
            "subject": subject,
            "created_at": datetime.now().isoformat()
        }
        
        demo_data["assignments"].append(assignment)
        
        # 创建提交记录
        submission = {
            "id": len(demo_data["submissions"]) + 1,
            "assignment_id": assignment["id"],
            "file_id": file_id,
            "file_path": str(file_path),
            "status": "uploaded",
            "created_at": datetime.now().isoformat(),
            "teacher_id": teacher_id
        }
        
        demo_data["submissions"].append(submission)
        
        return {
            "success": True,
            "message": "作业上传成功",
            "submission_id": submission["id"],
            "assignment_id": assignment["id"],
            "file_id": file_id,
            "grading_url": f"http://localhost:8080/api/grading/start/{submission['id']}"
        }
        
    except Exception as e:
        # 清理已上传的文件
        if os.path.exists(file_path):
            os.remove(file_path)
        
        raise HTTPException(
            status_code=500,
            detail=f"上传失败: {str(e)}"
        )

# 获取批改结果
@app.get("/api/grading/start/{submission_id}")
async def get_grading_result(submission_id: int):
    """获取批改结果"""
    
    # 查找提交记录
    submission = next((s for s in demo_data["submissions"] if s["id"] == submission_id), None)
    
    if not submission:
        raise HTTPException(
            status_code=404,
            detail="提交记录不存在"
        )
    
    # 模拟批改过程
    grading_result = {
        "submission_id": submission_id,
        "status": "completed",
        "results": [
            {
                "question_no": 1,
                "question_type": "数学计算",
                "judgement": "correct",
                "confidence": 0.95,
                "explanation": "计算正确"
            },
            {
                "question_no": 2,
                "question_type": "数学应用",
                "judgement": "incorrect",
                "confidence": 0.88,
                "explanation": "思路正确，计算错误"
            }
        ],
        "summary": {
            "total_questions": 2,
            "correct_count": 1,
            "incorrect_count": 1,
            "accuracy": "50%"
        },
        "created_at": datetime.now().isoformat()
    }
    
    return grading_result

# 获取历史记录
@app.get("/api/mobile/history")
async def get_history():
    """获取历史记录"""
    
    return {
        "success": True,
        "history": [
            {
                "assignment_name": "数学作业第3课",
                "created_at": "2026-11-10T10:30:00",
                "status": "completed"
            },
            {
                "assignment_name": "语文作业第2课",
                "created_at": "2026-11-09T15:20:00",
                "status": "processing"
            }
        ]
    }

# 启动服务器
if __name__ == "__main__":
    print(f"🚀 启动红笔系统演示版...")
    print(f"📡 访问地址: http://localhost:8080")
    print(f"📱 手机端地址: http://localhost:8080/mobile")
    print(f"📊 API文档: http://localhost:8080/api/docs")
    
    # 启动FastAPI服务器
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )