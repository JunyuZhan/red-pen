"""上传目录层级化（PRD 九）+ 旧扁平布局自动迁移测试"""

import os
import pathlib
import tempfile

import pytest

from backend.config import settings
from backend.database import SessionLocal, Class, Assignment, Submission
from backend.storage import upload_rel_path, upload_abs_path, migrate_upload_layout


@pytest.fixture()
def isolated_uploads(monkeypatch):
    updir = pathlib.Path(tempfile.mkdtemp(prefix="rp_layout_")) / "uploads"
    monkeypatch.setattr(settings, "UPLOAD_DIR", updir)
    os.makedirs(updir, exist_ok=True)
    return updir


def _make_records(db, class_id=1):
    if not db.query(Class).filter(Class.id == class_id).first():
        db.add(Class(id=class_id, name=f"布局测试{class_id}班", teacher_id=1, stage_id=1, subject_id=1))
        db.commit()
    a = Assignment(class_id=class_id, title="层级作业", subject_id=1)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_rel_and_abs_path(isolated_uploads):
    rel = upload_rel_path(3, 5, 7, "abc.jpg")
    assert rel == os.path.join("3", "5", "7", "abc.jpg")
    p = upload_abs_path(rel)
    assert str(isolated_uploads) in str(p) and p.parts[-4:] == ("3", "5", "7", "abc.jpg")


def test_abs_path_rejects_traversal(isolated_uploads):
    with pytest.raises(ValueError):
        upload_abs_path(os.path.join("..", "secret.txt"))


def test_migrate_flat_to_nested(isolated_uploads):
    db = SessionLocal()
    a = _make_records(db)
    cid = a.class_id
    aid = a.id
    flat = isolated_uploads / "oldfile.jpg"
    flat.write_bytes(b"fake")
    sub = Submission(assignment_id=a.id, student_id=9,
                     original_image_path=str(flat), status="uploaded")
    db.add(sub)
    db.commit()
    db.refresh(sub)
    sid = sub.id
    db.close()

    moved = migrate_upload_layout()
    assert moved == 1

    db = SessionLocal()
    sub = db.query(Submission).filter(Submission.id == sid).first()
    expect = upload_abs_path(upload_rel_path(cid, aid, 9, "oldfile.jpg"))
    assert pathlib.Path(sub.original_image_path) == expect
    assert expect.is_file()
    assert not flat.exists()
    # 幂等：再跑一遍不再移动
    assert migrate_upload_layout() == 0
    db.close()


def test_migrate_skips_missing_and_nested(isolated_uploads):
    db = SessionLocal()
    a = _make_records(db, class_id=2)
    # 数据库里有但文件已丢：跳过不崩
    db.add(Submission(assignment_id=a.id, student_id=1,
                      original_image_path=str(isolated_uploads / "gone.jpg"), status="uploaded"))
    # 已经在子目录里的（如 demo 图片）：不动
    nested_dir = isolated_uploads / "demo"
    nested_dir.mkdir(parents=True, exist_ok=True)
    nested = nested_dir / "demo.jpg"
    nested.write_bytes(b"x")
    db.add(Submission(assignment_id=a.id, student_id=1,
                      original_image_path=str(nested), status="uploaded"))
    db.commit()
    db.close()
    assert migrate_upload_layout() == 0


def test_migrate_handles_orphan_assignment(isolated_uploads):
    """作业被删的孤儿提交：class_id 取 0 兜底，仍完成迁移不崩溃"""
    db = SessionLocal()
    flat = isolated_uploads / "orphan.jpg"
    flat.write_bytes(b"o")
    sub = Submission(assignment_id=99999, student_id=1,
                     original_image_path=str(flat), status="uploaded")
    db.add(sub)
    db.commit()
    sid = sub.id
    db.close()
    assert migrate_upload_layout() == 1
    db = SessionLocal()
    sub = db.query(Submission).filter(Submission.id == sid).first()
    assert pathlib.Path(sub.original_image_path).is_file()
    db.close()
