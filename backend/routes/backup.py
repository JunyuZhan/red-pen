"""
数据备份 / 恢复路由：
- GET  /            列出已有备份
- POST /create      打包 data 目录（red_pen.db + uploads）为 zip 存到 data/backups/
- POST /restore     用指定备份文件恢复 data 目录（先清空 db 与 uploads 再解压）
"""

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

router = APIRouter()


def _backups_dir() -> Path:
    d = settings.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_name(name: str) -> str:
    """只允许字母数字、下划线、连字符和点，防止路径穿越"""
    if not name or "/" in name or "\\" in name or ".." in name or not name.endswith(".zip"):
        raise HTTPException(status_code=400, detail="非法备份文件名")
    return name


@router.get("")
async def list_backups():
    files = sorted(_backups_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "success": True,
        "backups": [
            {"filename": p.name, "size": p.stat().st_size,
             "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat()}
            for p in files
        ],
    }


@router.post("/create")
async def create_backup():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _backups_dir() / f"backup_{ts}.zip"
    db_file = settings.DATA_DIR / "red_pen.db"
    upload_dir = settings.UPLOAD_DIR
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_file.exists():
            zf.write(db_file, "red_pen.db")
        if upload_dir.exists():
            for f in upload_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, str(f.relative_to(upload_dir.parent)))
    return {
        "success": True,
        "message": f"备份已创建: {out_path.name}",
        "filename": out_path.name,
        "size": out_path.stat().st_size,
    }


class RestoreIn(BaseModel):
    filename: str


@router.post("/restore")
async def restore_backup(payload: RestoreIn):
    name = _validate_name(payload.filename)
    path = _backups_dir() / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "red_pen.db" not in names:
            raise HTTPException(status_code=400, detail="备份缺少 red_pen.db，拒绝恢复")
        # 校验通过后才清空并解压，避免半途失败丢数据
        db_file = settings.DATA_DIR / "red_pen.db"
        if db_file.exists():
            db_file.unlink()
        upload_dir = settings.UPLOAD_DIR
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        zf.extractall(settings.DATA_DIR)
    return {"success": True, "message": f"已从 {name} 恢复"}
