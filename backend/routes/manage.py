"""
班级与学生管理路由
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db, Class, Student

router = APIRouter()


class ClassIn(BaseModel):
    name: str
    stage_id: int = 1
    subject_id: int = 1


class StudentIn(BaseModel):
    name: str
    class_id: int


class StudentUpdate(BaseModel):
    name: Optional[str] = None
    class_id: Optional[int] = None


# ---------- 班级 ----------

@router.get("/classes")
async def list_classes(db: Session = Depends(get_db)):
    classes = db.query(Class).all()
    result = []
    for c in classes:
        count = db.query(Student).filter(Student.class_id == c.id).count()
        result.append({"id": c.id, "name": c.name, "student_count": count})
    return {"success": True, "classes": result}


@router.post("/classes")
async def create_class(data: ClassIn, db: Session = Depends(get_db)):
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="班级名称不能为空")
    c = Class(name=data.name.strip(), teacher_id=1,
              stage_id=data.stage_id, subject_id=data.subject_id)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"success": True, "id": c.id, "name": c.name}


@router.delete("/classes/{class_id}")
async def delete_class(class_id: int, db: Session = Depends(get_db)):
    c = db.query(Class).filter(Class.id == class_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="班级不存在")
    n = db.query(Student).filter(Student.class_id == class_id).count()
    if n:
        raise HTTPException(status_code=400, detail=f"班级下还有 {n} 名学生，请先删除学生")
    db.delete(c)
    db.commit()
    return {"success": True}


# ---------- 学生 ----------

@router.get("/students")
async def list_students(class_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Student)
    if class_id:
        q = q.filter(Student.class_id == class_id)
    students = [{"id": s.id, "name": s.name, "class_id": s.class_id}
                for s in q.order_by(Student.id).all()]
    return {"success": True, "students": students}


@router.post("/students")
async def create_student(data: StudentIn, db: Session = Depends(get_db)):
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="学生姓名不能为空")
    if not db.query(Class).filter(Class.id == data.class_id).first():
        raise HTTPException(status_code=404, detail="班级不存在")
    s = Student(name=data.name.strip(), class_id=data.class_id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"success": True, "id": s.id, "name": s.name}


@router.put("/students/{student_id}")
async def update_student(student_id: int, data: StudentUpdate, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="学生不存在")
    if data.name and data.name.strip():
        s.name = data.name.strip()
    if data.class_id:
        s.class_id = data.class_id
    db.commit()
    return {"success": True}


@router.delete("/students/{student_id}")
async def delete_student(student_id: int, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="学生不存在")
    db.delete(s)
    db.commit()
    return {"success": True}
