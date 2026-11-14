# -*- coding: utf-8 -*-
"""健壮性第三轮（v0.5.20）：管理路由孤儿防护
1. 删除有作业的班级 -> 400
2. 删除有提交记录的学生 -> 400
3. 改学生到不存在的班级 -> 404
4. 正常空班级/空学生删除仍成功
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import tmp_db, auth_client  # noqa: F401
from backend.database import SessionLocal, Class, Student, Assignment, Submission


def _mk_class(tmp_db, name="测试班"):
    db = tmp_db.SessionLocal()
    c = Class(name=name, teacher_id=1, stage_id=1, subject_id=1)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c.id


def test_delete_class_with_assignment_blocked(tmp_db, auth_client):
    cid = _mk_class(tmp_db, "有作业班")
    db = tmp_db.SessionLocal()
    db.add(Assignment(class_id=cid, title="作业1", subject_id=1))
    db.commit()
    db.close()
    r = auth_client.delete(f"/api/manage/classes/{cid}")
    assert r.status_code == 400, r.text
    assert "作业" in r.json()["detail"]


def test_delete_class_empty_ok(tmp_db, auth_client):
    cid = _mk_class(tmp_db, "空班")
    r = auth_client.delete(f"/api/manage/classes/{cid}")
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def test_delete_student_with_submissions_blocked(tmp_db, auth_client):
    cid = _mk_class(tmp_db, "学生班")
    db = tmp_db.SessionLocal()
    s = Student(name="张三", class_id=cid)
    db.add(s)
    db.commit()
    db.refresh(s)
    db.add(Submission(assignment_id=1, student_id=s.id,
                      original_image_path="/tmp/x.jpg"))
    db.commit()
    sid = s.id
    db.close()
    r = auth_client.delete(f"/api/manage/students/{sid}")
    assert r.status_code == 400, r.text
    assert "作业记录" in r.json()["detail"]


def test_delete_student_clean_ok(tmp_db, auth_client):
    cid = _mk_class(tmp_db, "干净班")
    db = tmp_db.SessionLocal()
    s = Student(name="李四", class_id=cid)
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()
    r = auth_client.delete(f"/api/manage/students/{sid}")
    assert r.status_code == 200, r.text


def test_update_student_to_missing_class_404(tmp_db, auth_client):
    cid = _mk_class(tmp_db, "改班级")
    db = tmp_db.SessionLocal()
    s = Student(name="王五", class_id=cid)
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()
    r = auth_client.put(f"/api/manage/students/{sid}", json={"class_id": 999999})
    assert r.status_code == 404, r.text
    # 名字改 + 合法班级改仍正常
    db = tmp_db.SessionLocal()
    c2 = Class(name="合法班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(c2)
    db.commit()
    db.refresh(c2)
    db.close()
    r = auth_client.put(f"/api/manage/students/{sid}",
                        json={"name": "王五二", "class_id": c2.id})
    assert r.status_code == 200, r.text
