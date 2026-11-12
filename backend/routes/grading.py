"""
批改路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pathlib import Path
from typing import Optional
import asyncio
import os
import time
from ..config import settings
from ..logging_setup import get_logger
from pydantic import BaseModel

from ..database import get_db, Submission, QuestionResult, AnswerTemplate, SessionLocal, AppConfig
from ..models.grading_engine import grading_manager, estimate_cost
from ..models.annotator import annotate_image
from .auth import require_admin

router = APIRouter()

MAX_RETRY = 3  # PRD 4.4：最多自动/手动重试 3 次

# ============ 并发批改队列（PRD 待办 e） ============

# 内存态队列状态：submission_id -> {state, duration_seconds, error}
QUEUE: dict = {}
_sem: Optional[asyncio.Semaphore] = None
_sem_size = 0


def get_concurrency() -> int:
    """批改并发路数，存 AppConfig，默认 2，范围 1-4"""
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "grading_concurrency").first()
        n = int(row.value) if row else 2
    except Exception:
        n = 2
    finally:
        db.close()
    return max(1, min(4, n))


def _get_semaphore() -> asyncio.Semaphore:
    global _sem, _sem_size
    n = get_concurrency()
    if _sem is None or _sem_size != n:
        _sem, _sem_size = asyncio.Semaphore(n), n
    return _sem


async def _grade_one(submission_id: int, template_id: Optional[int] = None) -> dict:
    """批改单份提交（自带独立 DB 会话，可在线程/任务里安全调用），记录耗时与队列状态"""
    db = SessionLocal()
    q = QUEUE.setdefault(submission_id, {"state": "queued", "duration_seconds": None, "error": None})
    q["state"] = "processing"
    t0 = time.monotonic()
    # 月度预算护栏（PRD 待办 j）：block 模式下超预算停止新批改
    from ..models.budget import check_budget
    budget = check_budget()
    if budget["exceeded"] and budget["budget_action"] == "block":
        q["state"] = "failed"
        q["error"] = f"本月已用 {budget['spent']:.2f} 元，已达预算上限 {budget['monthly_budget']:.2f} 元（设置页可调整）"
        db.close()
        return {"success": False, "error": q["error"], "budget": budget}
    try:
        template = None
        if template_id:
            template = db.query(AnswerTemplate).filter(AnswerTemplate.id == template_id).first()

        submission = db.query(Submission).filter(Submission.id == submission_id).first()
        if not submission:
            q.update(state="failed", error="提交记录不存在")
            return {"success": False, "error": "提交记录不存在"}

        subject = template.subject if template else "数学"
        get_logger().info("开始批改 submission=%s subject=%s", submission.id, subject)
        try:
            result = await grading_manager.grade(
                image_path=submission.original_image_path,
                question_type="default",
                subject=subject,
                answers=(template.answers if template else None),
            )
        except Exception as e:
            submission.status = "failed"
            submission.error_message = str(e)[:500]
            get_logger().error("批改失败 submission=%s: %s", submission.id, str(e)[:300])
            submission.duration_seconds = round(time.monotonic() - t0, 2)
            db.commit()
            q.update(state="failed", error=str(e)[:200],
                     duration_seconds=round(time.monotonic() - t0, 2))
            return {"success": False, "error": str(e)}

        if not result.get("success"):
            submission.status = "failed"
            submission.error_message = str(result.get("error", "批改失败"))[:500]
            submission.duration_seconds = round(time.monotonic() - t0, 2)
            db.commit()
            q.update(state="failed", error=str(result.get("error", "批改失败"))[:200],
                     duration_seconds=round(time.monotonic() - t0, 2))
            return {"success": False, "error": result.get("error", "批改失败")}

        questions = result.get("questions", [])

        # 重批清旧：删除本次提交的历史逐题结果，避免重复累积
        db.query(QuestionResult).filter(QuestionResult.submission_id == submission.id).delete()

        # 置信度融合第四因子（PRD 待办 bb）：知识点历史准确率先调置信度，
        # 使低置信度复核通道（下方）按融合后的置信度选题主
        fusion_stats = None
        try:
            from ..models.confidence_fusion import fuse_knowledge_history
            fusion_stats = fuse_knowledge_history(questions, db, submission.id)
        except Exception:
            fusion_stats = None  # 融合链路任何异常都不能影响正常批改结果

        # 低置信度二次复核（PRD 待办 f）：不一致的题标 needs_review 进复核队列
        review_stats = None
        try:
            from ..models.review import cross_check_questions
            review_stats = await cross_check_questions(
                result, submission.original_image_path, "default", subject,
                answers=(template.answers if template else None))
        except Exception:
            review_stats = None  # 复核链路任何异常都不能影响正常批改结果

        # 保存逐题批改结果
        first_qr = None
        for qd in questions:
            conf = qd.get("confidence")
            qr = QuestionResult(
                submission_id=submission.id,
                question_no=int(qd.get("question_no") or 0) or 1,
                question_type=qd.get("question_type") or "default",
                knowledge_point=(qd.get("knowledge_point") or None) or None,
                student_answer_text=qd.get("student_answer"),
                correct_answer_text=qd.get("correct_answer"),
                llm_raw_output=result.get("raw_output"),
                judgement_raw=qd.get("judgement"),
                judgement_final=qd.get("judgement"),
                error_type=qd.get("error_type"),
                confidence_final=float(conf) if isinstance(conf, (int, float)) else None,
                bbox_answer=qd.get("bbox"),
                needs_review=1 if qd.get("needs_review") else 0,
                review_note=qd.get("review_note"),
            )
            db.add(qr)
            if first_qr is None:
                first_qr = qr

        # 标注坐标以本地 CV 切题为准（PRD 六章：LLM 视觉定位精度不足）：
        # CV 版面分析切出的区域数量与题目数一致时，按顺序用区域 bbox 覆盖引擎返回的坐标
        # g2: 水平带 → 收紧到手写墨迹块外接框（红笔压手写而非整行/空白）
        def _tighten(band_top, band_bottom):
            try:
                from ..models.question_splitter import tighten_band_to_ink
                tight = tighten_band_to_ink(submission.original_image_path,
                                            band_top, band_bottom)
                if tight:
                    return tight
            except Exception as e:
                get_logger().warning("墨迹收紧失败: %s", str(e)[:120])
            return [0, band_top, img_w, band_bottom]

        try:
            from ..models.question_splitter import split_questions
            regions = split_questions(submission.original_image_path,
                                      os.path.join(settings.DATA_DIR, "splits")) or None
            if regions and len(regions) >= len(questions):
                from PIL import Image as _Img
                img_w = _Img.open(submission.original_image_path).size[0]
                for i, qd in enumerate(questions):
                    if i < len(regions):
                        r = regions[i]
                        qd["bbox"] = _tighten(r["top"], r["bottom"])
                get_logger().info("标注坐标改用CV切题区域: %s题/%s区域", len(questions), len(regions))
        except Exception as e:
            get_logger().warning("CV切题失败: %s", str(e)[:120])

        # 兜底定位：CV切不出区域时，用"内容感知切带"——按墨迹分布找题目间的
        # 空白谷把整页切成与题目数相同的水平带（构造性对位），仍优于均匀分布。
        # 注：印刷连续版面可能一带多题；像素级精准待接入版面分析模型（待办 g）。
        if regions is None or len(regions) < len(questions):
            try:
                from ..models.question_splitter import find_bands
                bands = find_bands(submission.original_image_path, len(questions))
                if len(bands) == len(questions):
                    w = None
                    for i, qd in enumerate(questions):
                        if w is None:
                            from PIL import Image as _Img
                            w = _Img.open(submission.original_image_path).size[0]
                            img_w = w
                        qd["bbox"] = _tighten(bands[i]["top"], bands[i]["bottom"])
                    get_logger().info("标注使用内容感知切带定位: %s题/%s带", len(questions), len(bands))
            except Exception as e:
                get_logger().warning("内容感知切带失败: %s", str(e)[:120])

        # 红笔回显（去重防御：任何来源的重复题目条目都只保留一条，标注面板不重复）
        seen_q = set()
        uniq_questions = []
        for qd in questions:
            key = str(qd.get("question_no"))
            if key in seen_q:
                continue
            seen_q.add(key)
            uniq_questions.append(qd)

        # 用户规则：先识别（CV 内容感知）→ 再划分（按真实题数切带）→ 再覆盖回去（逐题标注）。
        # 真实题数以老师录入的参考答案表为准，不信任引擎返回的题数（演示/模型可能漏题）。
        n_before = len(uniq_questions)
        expected = n_before
        if template and getattr(template, "answers", None):
            try:
                import json as _json
                _ans = template.answers if isinstance(template.answers, dict) else _json.loads(template.answers or "{}")
                expected = max(expected, len(_ans))
            except Exception:
                pass
        if expected > len(uniq_questions):
            for i in range(len(uniq_questions), expected):
                uniq_questions.append({"question_no": str(i + 1), "is_correct": None,
                                       "verdict": "待批", "comment": "引擎未返回该题，按答案表占位标注"})
            get_logger().info("题数以答案表为准: 引擎%s条 → 按答案表%s题补齐占位", len(questions), expected)

        # 按真实题数重新切带覆盖坐标（发生过补齐 = 引擎题数不可信，必须重切，否则错位）
        if template and expected > n_before:
            try:
                from ..models.question_splitter import find_bands
                bands2 = find_bands(submission.original_image_path, expected)
                if len(bands2) == expected:
                    from PIL import Image as _Img
                    _w = _Img.open(submission.original_image_path).size[0]
                    img_w = _w
                    for i, qd in enumerate(uniq_questions):
                        qd["bbox"] = _tighten(bands2[i]["top"], bands2[i]["bottom"])
                get_logger().info("按答案表真实题数切带: %s题/%s带", expected, len(bands2))
            except Exception as e:
                get_logger().warning("真实题数切带失败: %s", str(e)[:120])

        get_logger().info("标注题数: 引擎返回%s条/去重%s条/最终%s条", len(questions), len(seen_q), len(uniq_questions))
        annotated = annotate_image(submission.original_image_path, uniq_questions,
                                   result.get("analysis", ""))
        submission.annotated_image_path = annotated
        submission.grading_summary = result.get("analysis")

        # 成本估算
        cost = estimate_cost(result.get("prompt_tokens", 0), result.get("completion_tokens", 0))
        submission.prompt_tokens = cost["prompt_tokens"]
        submission.completion_tokens = cost["completion_tokens"]
        submission.cost_estimate = cost["cost"]

        duration = round(time.monotonic() - t0, 2)
        submission.duration_seconds = duration
        submission.status = "completed"
        get_logger().info("批改完成 submission=%s 耗时%.1fs", submission.id, round(time.monotonic() - t0, 2))
        if template:
            template.usage_count = (template.usage_count or 0) + 1
        db.commit()

        q.update(state="completed", duration_seconds=duration, error=None)
        return {
            "success": True,
            "submission_id": submission.id,
            "result_id": first_qr.id if first_qr else None,
            "judgement": result.get("judgement"),
            "confidence": result.get("confidence"),
            "analysis": result.get("analysis"),
            "duration_seconds": duration,
            "review": review_stats,
            "fusion": fusion_stats,
        }
    finally:
        db.close()


@router.post("/start/{submission_id}")
async def start_grading(submission_id: int, template_id: Optional[int] = None,
                        db: Session = Depends(get_db)):
    """对一份提交执行 AI 批改（可选携带答案模板，PRD 4.4 答案复用）"""

    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if submission.status not in ("uploaded", "failed"):
        raise HTTPException(status_code=400, detail=f"当前状态不允许批改: {submission.status}")
    if submission.status == "failed" and (submission.retry_count or 0) >= MAX_RETRY:
        raise HTTPException(status_code=400, detail=f"重试次数已达上限（{MAX_RETRY} 次），请检查 API 配置后联系管理员处理")

    if submission.status == "failed":
        submission.retry_count = (submission.retry_count or 0) + 1

    submission.status = "processing"
    submission.error_message = None
    db.commit()

    result = await _grade_one(submission_id, template_id)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=f"批改失败: {result.get('error')}")

    return result


class BatchIn(BaseModel):
    submission_ids: Optional[list] = None   # 不传 = 全部待批（uploaded + 未超限的 failed）
    template_id: Optional[int] = None


@router.post("/batch")
async def start_batch(data: BatchIn, db: Session = Depends(get_db)):
    """批量并发批改：按配置的并发路数排队执行，立即返回队列信息"""
    if data.submission_ids:
        subs = db.query(Submission).filter(Submission.id.in_(data.submission_ids)).all()
    else:
        subs = db.query(Submission).filter(
            Submission.status.in_(["uploaded", "failed"])).all()

    eligible = [s for s in subs
                if s.status in ("uploaded", "failed")
                and not (s.status == "failed" and (s.retry_count or 0) >= MAX_RETRY)]

    sem = _get_semaphore()

    async def worker(sid: int):
        async with sem:
            await _grade_one(sid, data.template_id)

    queued_ids = []
    for s in eligible:
        QUEUE[s.id] = {"state": "queued", "duration_seconds": None, "error": None}
        asyncio.create_task(worker(s.id))
        queued_ids.append(s.id)

    return {"success": True, "queued": queued_ids, "count": len(queued_ids),
            "concurrency": get_concurrency()}


@router.get("/queue-status")
async def queue_status(db: Session = Depends(get_db)):
    """工作台队列状态：排队/处理中/已完成/失败的份数与各份耗时"""
    processing_db = db.query(Submission).filter(Submission.status == "processing").count()
    items = [{"submission_id": sid, **info} for sid, info in sorted(QUEUE.items())]
    counts = {"queued": 0, "processing": 0, "completed": 0, "failed": 0}
    for it in items:
        counts[it["state"]] = counts.get(it["state"], 0) + 1
    durations = [it["duration_seconds"] for it in items
                 if it["state"] == "completed" and it["duration_seconds"]]
    return {
        "success": True,
        "concurrency": get_concurrency(),
        "counts": counts,
        "avg_duration_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "items": items[-100:],
        "db_processing": processing_db,
    }


@router.get("/concurrency")
async def read_concurrency():
    return {"success": True, "concurrency": get_concurrency()}


class ConcurrencyIn(BaseModel):
    value: int


@router.post("/concurrency")
async def write_concurrency(data: ConcurrencyIn, _: None = Depends(require_admin)):
    if not 1 <= data.value <= 4:
        raise HTTPException(status_code=400, detail="并发路数只能是 1-4")
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "grading_concurrency").first()
        if row:
            row.value = str(data.value)
        else:
            db.add(AppConfig(key="grading_concurrency", value=str(data.value)))
        db.commit()
    finally:
        db.close()
    return {"success": True, "concurrency": get_concurrency()}


@router.post("/retry/{submission_id}")
async def retry_grading(submission_id: int, db: Session = Depends(get_db)):
    """重试一份失败的提交（复用 start 的状态机，仅限 failed）"""

    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    if submission.status != "failed":
        raise HTTPException(status_code=400, detail=f"仅失败状态可重试，当前状态: {submission.status}")

    return await start_grading(submission_id, db=db)


@router.get("/failed")
async def list_failed(db: Session = Depends(get_db)):
    """失败队列：含失败原因与重试次数，供工作台/手机端展示"""

    subs = db.query(Submission).filter(Submission.status == "failed")\
        .order_by(Submission.id.desc()).limit(100).all()
    return {
        "success": True,
        "max_retry": MAX_RETRY,
        "failed": [
            {
                "id": s.id,
                "student_id": s.student_id,
                "error_message": s.error_message,
                "retry_count": s.retry_count or 0,
                "created_at": str(s.created_at),
            }
            for s in subs
        ],
    }


@router.get("/result/{submission_id}")
async def get_result(submission_id: int, db: Session = Depends(get_db)):
    """获取批改结果"""

    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    results = db.query(QuestionResult).filter(QuestionResult.submission_id == submission_id).all()

    annotated_url = None
    if submission.annotated_image_path:
        annotated_url = f"/uploads/{Path(submission.annotated_image_path).name}"

    return {
        "success": True,
        "submission_id": submission_id,
        "status": submission.status,
        "summary": submission.grading_summary,
        "error_message": submission.error_message,
        "annotated_image_url": annotated_url,
        "results": [
            {
                "id": r.id,
                "question_no": r.question_no,
                "question_type": r.question_type,
                "judgement": r.judgement_final,
                "error_type": r.error_type,
                "confidence": r.confidence_final,
                "student_answer": r.student_answer_text,
                "correct_answer": r.correct_answer_text,
            }
            for r in results
        ],
    }


# ============ 批改结果导出 CSV（老师导入 Excel/成绩单） ============

CSV_HEADERS = ["题号", "题型", "学生答案", "参考答案", "判定", "错误类型",
               "置信度", "知识点", "需复核", "教师复核"]


def _judgement_cn(j: str) -> str:
    return {"correct": "正确", "wrong": "错误", "partial": "部分正确",
            "unclear": "无法辨认"}.get(j, j or "")


@router.get("/export/{submission_id}")
async def export_result_csv(submission_id: int, db: Session = Depends(get_db)):
    """导出单份批改结果为 CSV（UTF-8 带 BOM，Excel 双击直接打开中文不乱码）。"""
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS)
    results = (db.query(QuestionResult)
               .filter(QuestionResult.submission_id == submission_id)
               .order_by(QuestionResult.question_no).all())
    for r in results:
        writer.writerow([
            r.question_no,
            r.question_type,
            r.student_answer_text or "",
            r.correct_answer_text or "",
            _judgement_cn(r.judgement_final),
            r.error_type or "",
            f"{r.confidence_final:.2f}" if r.confidence_final is not None else "",
            r.knowledge_point or "",
            "是" if r.needs_review else "",
            r.teacher_override or "",
        ])

    from urllib.parse import quote
    filename = quote(f"红笔批改_作业{submission_id}.csv")
    from fastapi import Response
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# ============ 答案模板（PRD 4.4/M4：同一练习册答案存一次，之后复用） ============

class TemplateIn(BaseModel):
    title: str
    subject: str = "数学"
    answers: dict  # {"1": "答案", "2": "B", ...}


@router.get("/answer-templates")
async def list_templates(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    templates = db.query(AnswerTemplate).order_by(AnswerTemplate.id.desc()).all()
    return {
        "success": True,
        "templates": [
            {"id": t.id, "title": t.title, "subject": t.subject,
             "answers": t.answers or {}, "usage_count": t.usage_count or 0}
            for t in templates
        ],
    }


@router.post("/answer-templates")
async def create_template(data: TemplateIn, db: Session = Depends(get_db),
                          _: None = Depends(require_admin)):
    if not data.title.strip():
        raise HTTPException(status_code=400, detail="模板名称不能为空")
    cleaned = {str(k).strip(): str(v).strip() for k, v in data.answers.items()
               if str(k).strip() and str(v).strip()}
    if not cleaned:
        raise HTTPException(status_code=400, detail="参考答案不能为空（格式：题号=答案，每行一条）")
    t = AnswerTemplate(title=data.title.strip(), subject=data.subject.strip() or "数学",
                       answers=cleaned)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"success": True, "id": t.id}


@router.delete("/answer-templates/{template_id}")
async def delete_template(template_id: int, db: Session = Depends(get_db),
                          _: None = Depends(require_admin)):
    t = db.query(AnswerTemplate).filter(AnswerTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="答案模板不存在")
    db.delete(t)
    db.commit()
    return {"success": True}
