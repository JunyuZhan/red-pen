# -*- coding: utf-8 -*-
"""PRD 复审缺口3：知识点掌握热力图 —— /api/analytics 知识点聚合带错误类型分布（热力图悬浮用）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed(tmp_db):
    from backend.database import Assignment, Class, QuestionResult, SessionLocal, Student, Submission

    db = SessionLocal()
    c = Class(name="三年一班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(c)
    db.commit()
    a = Assignment(class_id=c.id, title="口算", subject_id=1)
    db.add(a)
    db.commit()
    s = Student(name="张三", class_id=c.id)
    db.add(s)
    db.commit()
    sub = Submission(assignment_id=a.id, student_id=s.id,
                     original_image_path="/tmp/x.jpg", status="completed")
    db.add(sub)
    db.commit()
    rows = [
        # (judgement, knowledge_point, error_type)
        ("correct", "两位数加法", None),
        ("correct", "两位数加法", None),
        ("wrong", "两位数加法", "计算错误"),
        ("wrong", "两位数加法", "计算错误"),
        ("wrong", "进位加法", "概念错误"),
        ("wrong", "进位加法", None),  # 无错误类型不计入分布
    ]
    for j, kp, et in rows:
        db.add(QuestionResult(submission_id=sub.id, question_no=1,
                              question_type="calculation",
                              judgement_raw=j, judgement_final=j,
                              confidence_final=0.9, knowledge_point=kp,
                              error_type=et))
    db.commit()
    db.close()


def test_kp_heatmap_error_types(tmp_db):
    _seed(tmp_db)
    import asyncio
    from backend.database import SessionLocal
    from backend.routes.analytics import analytics

    db = SessionLocal()
    try:
        d = asyncio.run(analytics(db))
    finally:
        db.close()
    kps = {k["name"]: k for k in d["knowledge_points"]}
    assert kps["两位数加法"]["accuracy"] == 0.5
    assert kps["两位数加法"]["error_types"] == {"计算错误": 2}
    assert kps["进位加法"]["error_types"] == {"概念错误": 1}  # error_type 为空不进分布
    # 薄弱排前面
    assert d["knowledge_points"][0]["name"] == "进位加法"
