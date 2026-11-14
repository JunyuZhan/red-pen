"""
文件上传路由
"""

from typing import Optional

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
from ..models.quality_checker import check_image
from ..models.input_limits import check_name_len, check_title_len

router = APIRouter()


def _corruption_flag(flags: list) -> Optional[str]:
    """从质量提示中取出硬性损坏标记（无法解码/数据不完整）。"""
    for f in flags or []:
        if "损坏" in f:
            return f
    return None


def _validate_upload_file(file: UploadFile) -> str:
    """校验文件名与扩展名，返回小写扩展名。"""
    filename = file.filename or ""
    if not filename or "." not in filename:
        raise HTTPException(status_code=400, detail="缺少文件名或扩展名，无法识别文件类型")
    file_extension = filename.rsplit(".", 1)[-1].lower()
    if file_extension not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_extension}"
        )
    return file_extension


def _read_and_validate(data: bytes) -> None:
    """大小与内容完整性校验；损坏图片一律硬拒绝（不可强行上传）。"""
    if len(data) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大，最大支持 {settings.MAX_FILE_SIZE} 字节"
        )
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="文件内容为空")
    flag = _corruption_flag(check_image(data))
    if flag:
        raise HTTPException(status_code=400, detail=f"文件损坏，无法上传: {flag}")


@router.post("/upload")
async def upload_assignment(
    file: UploadFile = File(...),
    assignment_id: int = Form(...),
    student_id: int = Form(...),
    db: Session = Depends(get_db)
):
    """上传作业图片"""
    # 验证文件类型
    file_extension = _validate_upload_file(file)

    # 验证文件大小（一次性读入，避免流被耗尽）
    data = file.file.read()
    _read_and_validate(data)

    # 生成文件名
    file_id = str(uuid.uuid4())
    filename = f"{file_id}.{file_extension}"
    file_path = settings.UPLOAD_DIR / filename

    # 保存文件
    try:
        with open(file_path, "wb") as buffer:
            buffer.write(data)
        
        # 创建数据库记录
        flags = check_image(data)
        submission = Submission(
            assignment_id=assignment_id,
            student_id=student_id,
            original_image_path=str(file_path),
            status="uploaded",
            quality_flags=flags,
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
            "file_id": file_id,
            "quality_flags": flags
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
    student_id: Optional[int] = Form(None),
    force_quality: int = Form(0),
    db: Session = Depends(get_db)
):
    """移动端上传作业"""
    assignment_name = assignment_name.strip()
    stage, subject = stage.strip(), subject.strip()
    if not assignment_name or not stage or not subject:
        raise HTTPException(status_code=400, detail="作业标题/学段/学科不能为空")
    check_title_len(assignment_name)
    check_name_len(stage, "学段")
    check_name_len(subject, "学科")

    # 验证文件类型
    file_extension = _validate_upload_file(file)

    # 验证文件大小与内容完整性（损坏图片硬拒绝，不可强行上传）
    data = file.file.read()
    _read_and_validate(data)
    
    try:
        # 生成文件名
        file_id = str(uuid.uuid4())
        filename = f"{file_id}.{file_extension}"
        file_path = settings.UPLOAD_DIR / filename
        
        # 保存文件
        with open(file_path, "wb") as buffer:
            buffer.write(data)
        
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
        flags = check_image(data)
        if flags and not force_quality:
            # 质量不合格且未强行上传：清理文件，退回让用户选择重拍/强行上传
            p = Path(file_path)
            if p.exists():
                p.unlink()
            return JSONResponse(status_code=422, content={
                "success": False,
                "quality_flags": flags,
                "message": "照片质量检测未通过"
            })

        submission = Submission(
            assignment_id=assignment.id,
            student_id=student_id if student_id else 1,
            original_image_path=str(file_path),
            status="uploaded",
            quality_flags=flags,
            quality_override=bool(force_quality),
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
            "quality_flags": flags,
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
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"删除失败: {str(e)}"
        )