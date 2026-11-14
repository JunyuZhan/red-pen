"""
数据备份 / 恢复路由：
- GET  /            列出已有备份
- POST /create      打包 data 目录（red_pen.db + uploads）为 zip 存到 data/backups/
- POST /restore     用指定备份文件恢复 data 目录（先解压到暂存区校验，再原子替换）

健壮性约定（v0.5.21）：
- create 用 SQLite 在线备份 API 生成一致性快照，不直接拷贝打开中的库文件；
- restore 前置校验 zip 完整性、条目路径（防 zip-slip）、必须含 red_pen.db；
- 校验全部通过后才清空旧数据，暂存区解压失败/库文件损坏都原数据不动；
- 清空旧数据前自动做一次当前数据快照（pre_restore_*.zip），误恢复可找回。
"""

import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings

router = APIRouter()

DB_NAME = "red_pen.db"


def _backups_dir() -> Path:
    d = settings.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_name(name: str) -> str:
    """只允许字母数字、下划线、连字符和点，防止路径穿越"""
    if not name or "/" in name or "\\" in name or ".." in name or not name.endswith(".zip"):
        raise HTTPException(status_code=400, detail="非法备份文件名")
    return name


def _validate_entries(zf: zipfile.ZipFile) -> None:
    """拒绝绝对路径 / .. / 盘符等危险条目名（zip-slip 防护）。"""
    for info in zf.infolist():
        name = info.filename
        p = Path(name)
        if p.is_absolute() or name.startswith(("/", "\\")) or ".." in p.parts or (len(name) > 1 and name[1] == ":"):
            raise HTTPException(status_code=400, detail=f"备份含非法路径条目: {name}")


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


def _snapshot_into(zf: zipfile.ZipFile) -> None:
    """把当前数据库（在线一致性快照）+ 上传文件写入 zf。"""
    db_file = settings.DATA_DIR / DB_NAME
    upload_dir = settings.UPLOAD_DIR
    if db_file.exists():
        # 在线备份 API 生成一致性快照，避免拷到打开中的库文件导致损坏
        src = sqlite3.connect(str(db_file))
        try:
            with tempfile.TemporaryDirectory() as td:
                snap = Path(td) / DB_NAME
                dst = sqlite3.connect(str(snap))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                zf.write(snap, DB_NAME)
        finally:
            src.close()
    if upload_dir.exists():
        for f in upload_dir.rglob("*"):
            if f.is_file():
                zf.write(f, str(f.relative_to(upload_dir.parent)))


def _has_data() -> bool:
    db_file = settings.DATA_DIR / DB_NAME
    upload_dir = settings.UPLOAD_DIR
    return db_file.exists() or (upload_dir.exists() and any(upload_dir.rglob("*")))


@router.post("/create")
async def create_backup():
    if not _has_data():
        raise HTTPException(status_code=400, detail="暂无数据可备份（数据库与上传目录均为空）")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _backups_dir() / f"backup_{ts}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        _snapshot_into(zf)
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

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="备份文件损坏（不是有效的 zip），拒绝恢复")

    staging = Path(tempfile.mkdtemp(prefix="rp_restore_", dir=str(settings.DATA_DIR)))
    try:
        with zf:
            _validate_entries(zf)
            names = zf.namelist()
            if DB_NAME not in names:
                raise HTTPException(status_code=400, detail="备份缺少 red_pen.db，拒绝恢复")
            zf.extractall(staging)

        db_snap = staging / DB_NAME
        # 库文件完整性校验：SQLite 文件头 + 完整性检查，失败则不动现有数据
        try:
            if db_snap.read_bytes()[:16] != b"SQLite format 3\x00":
                raise sqlite3.DatabaseError("bad header")
            chk = sqlite3.connect(str(db_snap))
            try:
                chk.execute("PRAGMA integrity_check").fetchone()
            finally:
                chk.close()
        except Exception:
            raise HTTPException(status_code=400, detail="备份中的数据库文件损坏，拒绝恢复")

        db_file = settings.DATA_DIR / DB_NAME
        upload_dir = settings.UPLOAD_DIR
        # 恢复前自动快照当前数据（pre_restore_*.zip），误选错备份也能找回来
        if _has_data():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safety = _backups_dir() / f"pre_restore_{ts}.zip"
            with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as szf:
                _snapshot_into(szf)
        if db_file.exists():
            db_file.unlink()
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        # 暂存区内容已通过校验，移动到位（条目为 red_pen.db 与 uploads/... 前缀）
        shutil.move(str(db_snap), str(db_file))
        for item in staging.iterdir():
            if item.name == DB_NAME:
                continue
            if item.is_dir():
                for f in item.rglob("*"):
                    if f.is_file():
                        dest = upload_dir / f.relative_to(item)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(dest))
            else:
                shutil.move(str(item), str(settings.DATA_DIR / item.name))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="恢复过程出错，原数据未受影响，请重试或换备份文件")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {"success": True, "message": f"已从 {name} 恢复"}
