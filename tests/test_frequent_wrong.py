# -*- coding: utf-8 -*-
"""PRD 七第三层·高频错题：/api/analytics/frequent-wrong 按错误人数降序列题，附典型错误答案。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed_submission(assignment_id, student_id, results):
    """results: [(question_no, judgement, error_type, answer_text)]"""
    from backend.database import QuestionResult, SessionLocal, Submission

    db = SessionLocal()
    sub = Submission(assignment_id=assignment_id, student_id=student_id,
                     original_image_path="/tmp/x.jpg", status="completed")
    db.add(sub)
    db.commit()
    for qno, j, et, ans in results:
        db.add(QuestionResult(
            submission_id=sub.id, question_no=qno, question_type="calculation",
            judgement_raw=j, judgement_final=j, confidence_final=0.9,
            error_type=et, student_answer_text=ans,
        ))
    db.commit()
    db.close()


def test_frequent_wrong_ranking(tmp_db):
    from backend.database import Assignment, Class, SessionLocal, Student

    db = SessionLocal()
    c = Class(name="三年二班", teacher_id=1, stage_id=1, subject_id=1)
    db.add(c); db.commit()
    a1 = Assignment(class_id=c.id, title="口算第5页", subject_id=1)
    a2 = Assignment(class_id=c.id, title="应用题", subject_id=1)
    db.add_all([a1, a2]); db.commit()
    s1 = Student(name="张三", class_id=c.id)
    s2 = Student(name="李四", class_id=c.id)
    s3 = Student(name="王五", class_id=c.id)
    db.add_all([s1, s2, s3]); db.commit()
    a1_id, a2_id = a1.id, a2.id
    sids = (s1.id, s2.id, s3.id)
    db.close()

    # 口算第5页 题3：3个学生都错（错误人数3）→ 应排最前
    for sid in sids:
        _seed_submission(a1_id, sid, [(3, "wrong", "计算错误", "8"),
                                      (1, "correct", None, "9")])
    # 应用题 题1：1个学生错
    _seed_submission(a2_id, s1.id, [(1, "wrong", "概念错误", "12")])
    # 口算第5页 题2：全对 → 不出现在结果里
    _seed_submission(a1_id, s2.id, [(2, "correct", None, "5")])

    import asyncio
    from backend.routes.analytics import frequent_wrong

    db = SessionLocal()
    try:
        d = asyncio.run(frequent_wrong(db))
    finally:
        db.close()

    assert d["success"] is True
    items = d["items"]
    # 只出现错题，题2(全对)被过滤
    assert len(items) == 2
    top = items[0]
    assert (top["assignment_title"], top["question_no"]) == ("口算第5页", 3)
    assert top["wrong_count"] == 3 and top["wrong_students"] == 3
    assert top["total"] == 3
    assert top["error_types"] == {"计算错误": 3}
    assert top["sample_answers"] == ["8", "8", "8"]
    second = items[1]
    assert (second["assignment_title"], second["question_no"]) == ("应用题", 1)
    assert second["wrong_students"] == 1


def test_frequent_wrong_empty(tmp_db):
    import asyncio
    from backend.database import SessionLocal
    from backend.routes.analytics import frequent_wrong

    db = SessionLocal()
    try:
        d = asyncio.run(frequent_wrong(db))
    finally:
        db.close()
    assert d == {"success": True, "items": []}
