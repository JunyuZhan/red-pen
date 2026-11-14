# -*- coding: utf-8 -*-
"""第十二轮健壮性（v0.5.29）：并发上传+批改混合压测。
场景：老师一边用手机收作业（HTTP 并发上传），一边对已收到的提交发起批量批改。
验证：互不干扰、无串扰、全部完成、并发峰值守约束、每份耗时落库。
"""
import asyncio
import io
import os
import pathlib
import random
import tempfile
import threading

import pytest
from PIL import Image
from fastapi.testclient import TestClient

import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from test_concurrent_queue import cq_env, make_subs, run_batch  # noqa: E402,F401


class SlowEngine:
    """稍慢的 mock 引擎：足够慢以与上传线程重叠，并记录并发峰值。"""

    state = {"active": 0, "peak": 0}

    def __init__(self, api_key, model_name, base_url):
        pass

    async def grade(self, image_path, question_type, subject, answers=None):
        st = SlowEngine.state
        st["active"] += 1
        st["peak"] = max(st["peak"], st["active"])
        await asyncio.sleep(0.05)
        st["active"] -= 1
        return {"success": True, "judgement": "correct", "confidence": 0.95,
                "analysis": "OK", "questions": [{"question_no": 1, "judgement": "correct"}],
                "prompt_tokens": 100, "completion_tokens": 50}


@pytest.fixture()
def mx_env(cq_env, monkeypatch):
    """在 cq_env（临时库+mock引擎）基础上补上传目录与慢引擎。"""
    import backend.config as cfg
    import backend.models.grading_engine as ge

    updir = os.path.join(tempfile.mkdtemp(prefix="rp_mx_"), "uploads")
    os.makedirs(updir, exist_ok=True)
    monkeypatch.setattr(cfg.settings, "UPLOAD_DIR", pathlib.Path(updir))
    monkeypatch.setattr(ge, "GradingEngine", SlowEngine)
    SlowEngine.state = {"active": 0, "peak": 0}

    client = cq_env["client"]
    # 建班级 + 10 名学生
    r = client.post("/api/manage/classes",
                    json={"name": "混合压测班", "stage_id": 1, "subject_id": 1})
    assert r.status_code == 200, r.text
    cls = r.json()["id"]
    students = [client.post("/api/manage/students",
                            json={"name": f"混{i:02d}", "class_id": cls}).json()["id"]
                for i in range(10)]
    yield {"client": client, "dbmod": cq_env["dbmod"], "students": students}


def _jpeg(w=800, h=600):
    rng = random.Random(7)
    img = Image.new("RGB", (w, h))
    img.putdata([(rng.randint(60, 200),) * 3 for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_mixed_upload_and_grading(mx_env):
    """上传线程与批量批改同时跑：互不干扰，全部完成，串扰为零。"""
    client, dbmod, students = mx_env["client"], mx_env["dbmod"], mx_env["students"]
    # 第一批：先收 6 份，立刻发起批量批改
    first_ids = []
    for i in range(6):
        r = client.post("/api/upload/upload",
                        files={"file": (f"f{i}.jpg", _jpeg(), "image/jpeg")},
                        data={"assignment_id": 1, "student_id": students[i]})
        assert r.status_code == 200, r.text
        first_ids.append(r.json()["submission_id"])

    # 上传与批改重叠：批改在前台 asyncio.run 里跑，上传在后台线程继续
    upload_results = [None] * 4

    def work(i):
        r = client.post("/api/upload/upload",
                        files={"file": (f"l{i}.jpg", _jpeg(), "image/jpeg")},
                        data={"assignment_id": 1, "student_id": students[6 + i]})
        upload_results[i] = r.status_code

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    res = asyncio.run(run_batch({"submission_ids": first_ids}))
    for t in threads:
        t.join()

    assert res["success"] and res["queued"] == first_ids, res
    assert upload_results == [200] * 4, upload_results

    # 第二批：把后 4 份也批掉
    s = dbmod.SessionLocal()
    try:
        late = [x.id for x in s.query(dbmod.Submission)
                .filter(dbmod.Submission.student_id.in_(students[6:])).all()]
    finally:
        s.close()
    res2 = asyncio.run(run_batch({"submission_ids": late}))
    assert res2["success"] and res2["count"] == 4, res2

    # 终态校验：10 份全部 completed，逐题结果各归各的提交（无串扰），耗时已记录
    from backend.database import QuestionResult
    s = dbmod.SessionLocal()
    try:
        subs = {x.id: x for x in s.query(dbmod.Submission)
                .filter(dbmod.Submission.student_id.in_(students)).all()}
        assert len(subs) == 10
        assert all(x.status == "completed" for x in subs.values())
        assert all(x.duration_seconds is not None and x.duration_seconds > 0
                   for x in subs.values())
        rows = [(r[0], r[1], r[2]) for r in
                s.query(QuestionResult.id, QuestionResult.submission_id,
                        QuestionResult.question_no).all()]
        from collections import Counter
        qr_counts = Counter(r[1] for r in rows)
        assert all(qr_counts.get(sid) == 1 for sid in subs), (qr_counts, rows)
    finally:
        s.close()
    # 并发峰值不超过默认 2
    assert SlowEngine.state["peak"] <= 2, SlowEngine.state
