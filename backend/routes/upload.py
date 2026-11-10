"""
文件上传路由
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from PIL import Image
import os
import uuid
from datetime import datetime
from pathlib import Path

from ..database import get_db, Submission, Assignment, Class
from ..config import settings
from ..models.grading_engine import grading_manager

router = APIRouter()

@router.post("/upload")
async def upload_assignment(
    file: UploadFile = File(...),
    assignment_id: int = Form(...),
    student_id: int = Form(...),
    db: Session = Depends(get_db)
):
    """上传作业图片"""
    
    # 验证文件类型
    file_extension = file.filename.split('.')[-1].lower()
    if file_extension not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_extension}"
        )
    
    # 验证文件大小
    file_size = 0
    for chunk in file.file:
        file_size += len(chunk)
        if file_size > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大，最大支持 {settings.MAX_FILE_SIZE} 字节"
            )
    
    # 生成文件名
    file_id = str(uuid.uuid4())
    filename = f"{file_id}.{file_extension}"
    file_path = settings.UPLOAD_DIR / filename
    
    # 保存文件
    try:
        with open(file_path, "wb") as buffer:
            for chunk in file.file:
                buffer.write(chunk)
        
        # 创建数据库记录
        submission = Submission(
            assignment_id=assignment_id,
            student_id=student_id,
            original_image_path=str(file_path),
            status="uploaded",
            created_at=datetime.utcnow()
        )
        
        db.add(submission)
        db.commit()
        db.refresh(submission)
        
        return {
            "success": True,
            "message": "文件上传成功",
            "submission_id": submission.id,
            "file_path": str(file_path),
            "file_id": file_id
        }
        
    except Exception as e:
        # 清理已上传的文件
        if os.path.exists(file_path):
            os.remove(file_path)
        
        raise HTTPException(
            status_code=500,
            detail=f"文件上传失败: {str(e)}"
        )

@router.post("/upload-mobile")
async def upload_mobile(
    file: UploadFile = File(...),
    assignment_name: str = Form(...),
    stage: str = Form(...),
    subject: str = Form(...),
    teacher_id: int = Form(...),
    db: Session = Depends(get_db)
):
    """移动端上传作业"""
    
    # 验证文件类型
    file_extension = file.filename.split('.')[-1].lower()
    if file_extension not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_extension}"
        )
    
    # 验证文件大小
    file_size = 0
    for chunk in file.file:
        file_size += len(chunk)
        if file_size > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大，最大支持 {settings.MAX_FILE_SIZE} 字节"
            )
    
    try:
        # 生成文件名
        file_id = str(uuid.uuid4())
        filename = f"{file_id}.{file_extension}"
        file_path = settings.UPLOAD_DIR / filename
        
        # 保存文件
        with open(file_path, "wb") as buffer:
            for chunk in file.file:
                buffer.write(chunk)
        
        # 获取或创建班级
        class_result = db.query(Class).filter(Class.name == f"{stage}{subject}班级").first()
        if not class_result:
            class_result = Class(
                name=f"{stage}{subject}班级",
                teacher_id=teacher_id,
                stage_id=1,  # 默认小学
                subject_id=1  # 默认数学
            )
            db.add(class_result)
            db.commit()
            db.refresh(class_result)
        
        # 创建作业记录
        assignment = Assignment(
            class_id=class_result.id,
            title=assignment_name,
            subject_id=1,  # 默认数学
            created_at=datetime.utcnow()
        )
        
        db.add(assignment)
        db.commit()
        db.refresh(assignment)
        
        # 创建提交记录
        submission = Submission(
            assignment_id=assignment.id,
            student_id=1,  # 默认学生ID，实际需要从登录信息获取
            original_image_path=str(file_path),
            status="uploaded",
            created_at=datetime.utcnow()
        )
        
        db.add(submission)
        db.commit()
        db.refresh(submission)
        
        return {
            "success": True,
            "message": "作业上传成功",
            "submission_id": submission.id,
            "assignment_id": assignment.id,
            "file_id": file_id,
            "grading_url": f"http://{settings.HOST}:{settings.PORT}/api/grading/start/{submission.id}"
        }
        
    except Exception as e:
        # 清理已上传的文件
        if os.path.exists(file_path):
            os.remove(file_path)
        
        raise HTTPException(
            status_code=500,
            detail=f"上传失败: {str(e)}"
        )

@router.get("/file/{file_id}")
async def get_file(file_id: str):
    """获取文件"""
    # 在实际应用中，这里应该进行权限检查
    file_path = settings.UPLOAD_DIR / f"{file_id}.jpg"  # 简化处理
    
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail="文件不存在"
        )
    
    return JSONResponse(content={
        "success": True,
        "file_path": str(file_path),
        "file_id": file_id
    })

@router.delete("/file/{file_id}")
async def delete_file(file_id: str, db: Session = Depends(get_db)):
    """删除文件"""
    try:
        # 查找对应的文件记录
        submission = db.query(Submission).filter(
            Submission.original_image_path.like(f"%{file_id}%")
        ).first()
        
        if submission:
            # 删除文件
            file_path = Path(submission.original_image_path)
            if file_path.exists():
                file_path.unlink()
            
            # 删除数据库记录
            db.delete(submission)
            db.commit()
            
            return {"success": True, "message": "文件删除成功"}
        
        raise HTTPException(
            status_code=404,
            detail="文件不存在"
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"删除失败: {str(e)}"
        )