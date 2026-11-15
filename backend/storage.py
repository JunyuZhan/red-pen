"""
上传文件存储布局（PRD 九）：uploads/<班级id>/<作业id>/<学生id>/<文件名>

新上传一律按此层级落盘；旧扁平布局（uploads/uuid.jpg）由
migrate_upload_layout() 在启动时自动迁移（移动文件 + 更新数据库路径），
幂等可重复执行。
"""

from pathlib import Path

from .config import settings


def upload_rel_path(class_id: int, assignment_id: int, student_id: int, filename: str) -> str:
    """返回相对 uploads 根目录的层级路径（纯数字目录名，Windows 安全）。"""
    return str(Path(str(class_id)) / str(assignment_id) / str(student_id) / filename)


def upload_abs_path(rel: str) -> Path:
    p = (Path(settings.UPLOAD_DIR) / rel).resolve()
    # 防路径穿越：解析后必须仍在 uploads 根目录内
    if not str(p).startswith(str(Path(settings.UPLOAD_DIR).resolve())):
        raise ValueError("非法的存储路径")
    return p


def migrate_upload_layout() -> int:
    """把旧扁平布局的上传图片迁移到 班级/作业/学生 目录。返回迁移条数。"""
    from .database import SessionLocal, Submission, Assignment

    root = Path(settings.UPLOAD_DIR).resolve()
    db = SessionLocal()
    moved = 0
    try:
        rows = (
            db.query(Submission, Assignment.class_id)
            .outerjoin(Assignment, Submission.assignment_id == Assignment.id)
            .all()
        )
        for sub, class_id in rows:
            try:
                p = Path(sub.original_image_path)
            except (OSError, ValueError):
                continue
            # 只处理位于 uploads 根目录直接之下的旧扁平文件
            try:
                if p.parent.resolve() != root or not p.is_file():
                    continue
            except OSError:
                continue
            cid = class_id if class_id is not None else 0
            rel = upload_rel_path(cid, sub.assignment_id, sub.student_id, p.name)
            dest = upload_abs_path(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():  # 同名冲突（理论不可能，uuid 名）：保底跳过不动数据
                continue
            try:
                p.rename(dest)
            except OSError:
                continue  # 文件被占用/权限问题：留待下次启动重试，不中断启动
            sub.original_image_path = str(dest)
            moved += 1
        if moved:
            db.commit()
    finally:
        db.close()
    return moved
