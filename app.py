#!/usr/bin/env python3
"""
红笔系统完整版本
基于FastAPI的全功能实现
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import uuid
import json
import base64
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
import uvicorn

# 创建FastAPI应用
app = FastAPI(
    title="红笔 · 教师单机自用版",
    description="教师单机自用版作业批改系统",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# 配置
DATA_DIR = Path("./data")
UPLOAD_DIR = DATA_DIR / "uploads"
TEMPLATES_DIR = Path("./frontend/templates")

# 创建目录
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# 模板引擎
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 模拟数据库
demo_db = {
    "submissions": [],
    "assignments": [],
    "teachers": [{"id": 1, "name": "默认教师"}],
    "classes": [{"id": 1, "name": "默认班级", "stage": "小学", "subject": "数学"}]
}

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 根路径
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "app_name": "红笔 · 教师单机自用版",
        "version": "V0.4"
    })

# 手机端页面
@app.get("/mobile", response_class=HTMLResponse)
async def mobile_page(request: Request):
    return templates.TemplateResponse("mobile.html", {
        "request": request,
        "app_name": "红笔 · 教师单机自用版", 
        "version": "V0.4"
    })

# 健康检查
@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "红笔系统运行正常"}

# 系统状态
@app.get("/api/status")
async def get_status():
    return {
        "success": True,
        "status": "running",
        "version": "V0.4",
        "features": {
            "mobile_upload": True,
            "auto_grading": True,
            "dashboard": True,
            "cost_control": True
        },
        "demo_data": {
            "submissions": len(demo_db["submissions"]),
            "assignments": len(demo_db["assignments"]),
            "classes": len(demo_db["classes"])
        }
    }

# 移动端上传作业
@app.post("/api/mobile/upload")
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
            "id": len(demo_db["assignments"]) + 1,
            "name": assignment_name,
            "stage": stage,
            "subject": subject,
            "created_at": datetime.now().isoformat(),
            "teacher_id": teacher_id
        }
        
        demo_db["assignments"].append(assignment)
        
        # 创建提交记录
        submission = {
            "id": len(demo_db["submissions"]) + 1,
            "assignment_id": assignment["id"],
            "file_id": file_id,
            "file_path": str(file_path),
            "status": "uploaded",
            "created_at": datetime.now().isoformat(),
            "teacher_id": teacher_id,
            "grading_url": f"http://localhost:8080/api/grading/start/{len(demo_db['submissions']) + 1}"
        }
        
        demo_db["submissions"].append(submission)
        
        return {
            "success": True,
            "message": "作业上传成功",
            "submission_id": submission["id"],
            "assignment_id": assignment["id"],
            "file_id": file_id,
            "grading_url": submission["grading_url"],
            "status": "uploaded"
        }
        
    except Exception as e:
        # 清理已上传的文件
        if os.path.exists(file_path):
            os.remove(file_path)
        
        raise HTTPException(
            status_code=500,
            detail=f"上传失败: {str(e)}"
        )

# 开始批改
@app.post("/api/grading/start/{submission_id}")
async def start_grading(submission_id: int):
    """开始批改作业"""
    
    # 查找提交记录
    submission = next((s for s in demo_db["submissions"] if s["id"] == submission_id), None)
    
    if not submission:
        raise HTTPException(
            status_code=404,
            detail="提交记录不存在"
        )
    
    # 更新状态
    submission["status"] = "processing"
    
    return {
        "success": True,
        "message": "批改开始",
        "submission_id": submission_id,
        "status": "processing",
        "estimated_time": "30-60秒"
    }

# 获取批改结果
@app.get("/api/grading/result/{submission_id}")
async def get_grading_result(submission_id: int):
    """获取批改结果"""
    
    # 查找提交记录
    submission = next((s for s in demo_db["submissions"] if s["id"] == submission_id), None)
    
    if not submission:
        raise HTTPException(
            status_code=404,
            detail="提交记录不存在"
        )
    
    # 根据状态返回结果
    if submission["status"] == "uploaded":
        return {
            "success": True,
            "message": "等待批改",
            "status": "uploaded",
            "submission_id": submission_id
        }
    elif submission["status"] == "processing":
        return {
            "success": True,
            "message": "批改中",
            "status": "processing",
            "submission_id": submission_id
        }
    else:
        # 模拟批改完成的结果
        grading_result = {
            "submission_id": submission_id,
            "status": "completed",
            "assignment_name": submission.get("assignment_name", "数学作业"),
            "results": [
                {
                    "question_no": 1,
                    "question_type": "数学计算",
                    "knowledge_point": "加减法运算",
                    "student_answer": "45 + 23 = 68",
                    "correct_answer": "45 + 23 = 68",
                    "judgement": "correct",
                    "confidence": 0.95,
                    "explanation": "计算完全正确"
                },
                {
                    "question_no": 2,
                    "question_type": "数学应用",
                    "knowledge_point": "问题解决",
                    "student_answer": "100 ÷ 4 = 20",
                    "correct_answer": "100 ÷ 4 = 25",
                    "judgement": "incorrect",
                    "confidence": 0.88,
                    "explanation": "思路正确，但计算结果错误"
                },
                {
                    "question_no": 3,
                    "question_type": "数学思维",
                    "knowledge_point": "逻辑推理",
                    "student_answer": "正确",
                    "correct_answer": "正确",
                    "judgement": "correct",
                    "confidence": 0.92,
                    "explanation": "逻辑推理正确"
                }
            ],
            "summary": {
                "total_questions": 3,
                "correct_count": 2,
                "incorrect_count": 1,
                "accuracy": "66.7%"
            },
            "cost": {
                "total_tokens": 1500,
                "estimated_cost": "¥0.15"
            },
            "created_at": datetime.now().isoformat()
        }
        
        return grading_result

# 获取历史记录
@app.get("/api/mobile/history")
async def get_history():
    """获取历史记录"""
    
    history = []
    for submission in demo_db["submissions"]:
        assignment = next((a for a in demo_db["assignments"] if a["id"] == submission["assignment_id"]), None)
        if assignment:
            history.append({
                "id": submission["id"],
                "assignment_name": assignment["name"],
                "stage": assignment["stage"],
                "subject": assignment["subject"],
                "created_at": submission["created_at"],
                "status": submission["status"],
                "file_id": submission["file_id"]
            })
    
    return {
        "success": True,
        "history": history,
        "total": len(history)
    }

# 获取班级列表
@app.get("/api/classes")
async def get_classes():
    """获取班级列表"""
    
    return {
        "success": True,
        "classes": demo_db["classes"]
    }

# 创建班级
@app.post("/api/classes")
async def create_class(name: str = Form(...), stage: str = Form(...), subject: str = Form(...)):
    """创建班级"""
    
    class_obj = {
        "id": len(demo_db["classes"]) + 1,
        "name": name,
        "stage": stage,
        "subject": subject
    }
    
    demo_db["classes"].append(class_obj)
    
    return {
        "success": True,
        "message": "班级创建成功",
        "class": class_obj
    }

# 获取所有作业
@app.get("/api/assignments")
async def get_assignments():
    """获取作业列表"""
    
    assignments = []
    for assignment in demo_db["assignments"]:
        class_obj = next((c for c in demo_db["classes"] if c["id"] == assignment.get("class_id", 1)), None)
        assignments.append({
            "id": assignment["id"],
            "name": assignment["name"],
            "stage": assignment.get("stage", "小学"),
            "subject": assignment.get("subject", "数学"),
            "class_name": class_obj["name"] if class_obj else "默认班级",
            "created_at": assignment["created_at"],
            "submission_count": len([s for s in demo_db["submissions"] if s["assignment_id"] == assignment["id"]])
        })
    
    return {
        "success": True,
        "assignments": assignments
    }

# 启动服务器
if __name__ == "__main__":
    print("🚀 启动红笔系统完整版...")
    print(f"📡 访问地址: http://localhost:8080")
    print(f"📱 手机端地址: http://localhost:8080/mobile")
    print(f"📊 API文档: http://localhost:8080/api/docs")
    print(f"🗄️ 数据目录: {DATA_DIR}")
    print(f"📁 上传目录: {UPLOAD_DIR}")
    
    # 启动FastAPI服务器
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )