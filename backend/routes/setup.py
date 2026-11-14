"""
首次启动三步引导 + 工作台上下文切换（学段/学科/班级，记住默认）
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from .auth import require_admin
from ..database import get_db, AppConfig, StageConfig, SubjectConfig, Class
from ..models.input_limits import check_name_len

router = APIRouter()

DEFAULT_KEYS = ("default_stage", "default_subject", "default_class")


def _get_cfg(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    return row.value if row else default


def _set_cfg(db: Session, key: str, value: str) -> None:
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppConfig(key=key, value=value))


def _resolve_ids(db: Session, stage: str, subject: str):
    """按名称取学段/学科 id，不存在则创建（enabled=True）"""
    st = db.query(StageConfig).filter(StageConfig.name == stage).first()
    if not st:
        st = StageConfig(name=stage, enabled=True)
        db.add(st)
        db.flush()
    su = db.query(SubjectConfig).filter(
        SubjectConfig.name == subject, SubjectConfig.stage_id == st.id).first()
    if not su:
        su = SubjectConfig(name=subject, stage_id=st.id, enabled=True)
        db.add(su)
        db.flush()
    return st.id, su.id


class SetupIn(BaseModel):
    stage: str
    subject: str
    class_name: str


class ContextIn(BaseModel):
    stage: Optional[str] = None
    subject: Optional[str] = None
    class_name: Optional[str] = None


@router.get("/status")
async def setup_status(db: Session = Depends(get_db)):
    """公开只读：工作台判断是否弹引导、渲染上下文切换器"""
    subjects = [
        {"id": s.id, "name": s.name, "stage_id": s.stage_id}
        for s in db.query(SubjectConfig).filter(SubjectConfig.enabled == True).all()  # noqa: E712
    ]
    stages = [
        {"id": s.id, "name": s.name}
        for s in db.query(StageConfig).filter(StageConfig.enabled == True).all()  # noqa: E712
    ]
    classes = [
        {"id": c.id, "name": c.name}
        for c in db.query(Class).all()
    ]
    defaults = {
        "stage": _get_cfg(db, "default_stage", "小学"),
        "subject": _get_cfg(db, "default_subject", "数学"),
        "class_name": _get_cfg(db, "default_class", ""),
    }
    return {
        "setup_completed": _get_cfg(db, "setup_completed", "false") == "true",
        "defaults": defaults,
        "stages": stages,
        "subjects": subjects,
        "classes": classes,
    }


@router.post("/complete")
async def setup_complete(data: SetupIn, db: Session = Depends(get_db),
                         _: None = Depends(require_admin)):
    """首次引导保存：学段/学科/班级记住为默认，标记 setup_completed"""
    return _apply(db, data.stage, data.subject, data.class_name, mark_complete=True)


@router.post("/context")
async def setup_context(data: ContextIn, db: Session = Depends(get_db),
                        _: None = Depends(require_admin)):
    """工作台上下文切换器：更新默认学段/学科/班级（不重复引导）"""
    cur = {k: _get_cfg(db, k) for k in DEFAULT_KEYS}
    stage = (data.stage or cur["default_stage"] or "小学").strip()
    subject = (data.subject or cur["default_subject"] or "数学").strip()
    class_name = (data.class_name or cur["default_class"] or "").strip()
    if not class_name:
        raise HTTPException(status_code=400, detail="班级名称不能为空")
    return _apply(db, stage, subject, class_name, mark_complete=False)


def _apply(db: Session, stage: str, subject: str, class_name: str,
           mark_complete: bool):
    stage, subject, class_name = stage.strip(), subject.strip(), class_name.strip()
    if not stage or not subject or not class_name:
        raise HTTPException(status_code=400, detail="学段/学科/班级都不能为空")
    check_name_len(stage, "学段")
    check_name_len(subject, "学科")
    check_name_len(class_name, "班级名称")
    stage_id, subject_id = _resolve_ids(db, stage, subject)
    cls = db.query(Class).filter(Class.name == class_name).first()
    if not cls:
        cls = Class(name=class_name, teacher_id=1,
                    stage_id=stage_id, subject_id=subject_id)
        db.add(cls)
        db.flush()
    _set_cfg(db, "default_stage", stage)
    _set_cfg(db, "default_subject", subject)
    _set_cfg(db, "default_class", class_name)
    if mark_complete:
        _set_cfg(db, "setup_completed", "true")
    db.commit()
    return {
        "success": True,
        "defaults": {"stage": stage, "subject": subject, "class_name": class_name},
        "class_id": cls.id,
    }
