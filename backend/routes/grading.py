"""
批改路由
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pathlib import Path
from typing import Optional
import asyncio
import json
import os
import time
from ..config import settings
from ..logging_setup import get_logger
from pydantic import BaseModel

from ..database import get_db, Submission, QuestionResult, AnswerTemplate, SessionLocal, AppConfig, Assignment
from ..models.grading_engine import grading_manager, estimate_cost
from ..models.annotator import annotate_image
from .auth import require_admin

router = APIRouter()

MAX_RETRY = 3  # PRD 4.4：最多自动/手动重试 3 次

# ============ 并发批改队列（PRD 待办 e） ============

# 内存态队列状态：submission_id -> {state, duration_seconds, error}
QUEUE: dict = {}
# 批改任务强引用集（asyncio 只持弱引用，无强引用任务可能被 GC 中途回收）
_TASKS: set = set()
# submission_id -> asyncio.Task 映射（第七轮健壮性：支持取消排队/处理中的批改）
_TASK_BY_SID: dict = {}
_sem: Optional[asyncio.Semaphore] = None
_sem_size = 0

# 第七轮健壮性：单份批改整体超时（秒），存 AppConfig，默认 300，范围 60-900
def get_grading_timeout() -> int:
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "grading_timeout").first()
        n = int(row.value) if row else 300
    except Exception:
        n = 300
    finally:
        db.close()
    return max(60, min(900, n))


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
    cur = asyncio.current_task()
    if cur is not None:
        _TASK_BY_SID[submission_id] = cur
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
            # 第七轮健壮性：单份批改整体超时包裹，防止模型/网络挂死卡住队列
            result = await asyncio.wait_for(
                grading_manager.grade(
                    image_path=submission.original_image_path,
                    question_type="default",
                    subject=subject,
                    answers=(template.answers if template else None),
                ),
                timeout=get_grading_timeout(),
            )
        except asyncio.TimeoutError:
            submission.status = "failed"
            submission.error_message = f"批改超时（超过 {get_grading_timeout()} 秒）已中止，请检查模型服务后重试"
            submission.duration_seconds = round(time.monotonic() - t0, 2)
            db.commit()
            q.update(state="failed", error="批改超时已中止",
                     duration_seconds=round(time.monotonic() - t0, 2))
            get_logger().error("批改超时 submission=%s", submission.id)
            return {"success": False, "error": "批改超时已中止，请重试"}
        except asyncio.CancelledError:
            raise
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

        # 去重先行（回归修复）：引擎可能返回重复题号条目，切题/切带/入库
        # 都必须基于去重后的题数，否则 CV 按错误题数找区域，一条 bbox 也对不上。
        # 复用引擎去重（有真实答案的优先于「未作答」）
        try:
            from ..models.grading_engine import _dedupe_split_questions
            questions = _dedupe_split_questions(questions) or questions
        except Exception:
            pass

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

        # 保存逐题批改结果移至 bbox 覆盖之后（见下方"标注题数"前）

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

        # CV切题 / 切带覆盖 bbox 之后才保存逐题结果（回归修复：原先入库在
        # bbox 覆盖之前，DB 里 bbox_answer 永远是引擎原始值甚至为 null）
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
                steps=qd.get("steps"),
                needs_review=1 if qd.get("needs_review") else 0,
                review_note=qd.get("review_note"),
            )
            db.add(qr)
            if first_qr is None:
                first_qr = qr

        get_logger().info("标注题数: 引擎返回%s条/去重%s条/最终%s条", len(questions), len(seen_q), len(uniq_questions))
        annotated = annotate_image(submission.original_image_path, uniq_questions,
                                   result.get("analysis", ""))
        submission.annotated_image_path = annotated
        submission.grading_summary = result.get("analysis")
        if result.get("parse_failed"):
            # 模型回复无法解析为结构化 JSON：判定不可信，显式提示老师重批
            submission.grading_summary = (
                "⚠️ 模型返回内容无法解析为结构化结果，本页逐题判定不可信，建议重新批改。\n\n"
                + (submission.grading_summary or ""))
            get_logger().warning("submission=%s 模型返回解析失败，已标记判定不可信", submission.id)

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

        # 跨页续题（待办 g6）：上一页尾题无答案 + 本页首题有答案 → 拼图重批。
        # 任何异常静默跳过，不影响已保存的批改结果。
        cross_page_stats = None
        try:
            from ..models.cross_page import maybe_cross_page_merge
            cross_page_stats = await maybe_cross_page_merge(
                db, submission, subject=subject,
                answers=(template.answers if template else None))
            if cross_page_stats:
                get_logger().info("跨页续题已合并: %s", cross_page_stats)
        except Exception as e:
            get_logger().warning("跨页续题处理异常: %s", str(e)[:120])

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
            "cross_page": cross_page_stats,
        }
    except asyncio.CancelledError:
        # 第七轮健壮性：老师取消批改（排队中或处理中）——落可重批状态，不卡 processing
        get_logger().info("批改已取消 submission=%s", submission_id)
        q.update(state="cancelled", error="老师已取消批改",
                 duration_seconds=round(time.monotonic() - t0, 2))
        try:
            sub = db.query(Submission).filter(Submission.id == submission_id).first()
            if sub is not None and sub.status == "processing":
                sub.status = "uploaded"
                sub.error_message = "老师已取消批改，可重新发起"
                db.commit()
        except Exception:
            db.rollback()
        return {"success": False, "error": "已取消批改"}
    except Exception as e:
        # 兜底异常（兜底健壮性 v0.5.19）：批改中途任何未捕获异常都不能让提交
        # 永远卡在 processing / 队列卡在 processing——统一落到 failed 状态。
        get_logger().error("批改异常 submission=%s: %s", submission_id, str(e)[:300])
        q.update(state="failed", error=str(e)[:200],
                 duration_seconds=round(time.monotonic() - t0, 2))
        try:
            sub = db.query(Submission).filter(Submission.id == submission_id).first()
            if sub is not None:
                sub.status = "failed"
                sub.error_message = f"批改过程异常: {str(e)[:400]}"
                sub.duration_seconds = round(time.monotonic() - t0, 2)
                db.commit()
        except Exception:
            db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        _TASK_BY_SID.pop(submission_id, None)
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

    # 兜底健壮性 v0.5.19：worker 内兜底捕获异常 + 任务强引用防 GC
    # （asyncio 只持弱引用，无强引用的任务可能在执行中被垃圾回收）
    async def _batch_worker(sid: int, sem_: asyncio.Semaphore, tid: Optional[int]):
        try:
            async with sem_:
                await _grade_one(sid, tid)
        except asyncio.CancelledError:
            # 第七轮健壮性：取消发生在进入 _grade_one 之前（worker 还在排队）——
            # 队列落 cancelled，提交回 uploaded 可重批
            QUEUE.setdefault(sid, {"state": "queued", "duration_seconds": None,
                                   "error": None}).update(
                state="cancelled", error="老师已取消批改")
            db = SessionLocal()
            try:
                sub = db.query(Submission).filter(Submission.id == sid).first()
                if sub is not None and sub.status == "processing":
                    sub.status = "uploaded"
                    sub.error_message = "老师已取消批改，可重新发起"
                    db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            QUEUE.setdefault(sid, {"state": "queued", "duration_seconds": None,
                                   "error": None}).update(
                state="failed", error=f"队列任务异常: {str(e)[:180]}")
        finally:
            _TASKS.discard(asyncio.current_task())

    queued_ids = []
    for s in eligible:
        QUEUE[s.id] = {"state": "queued", "duration_seconds": None, "error": None}
        t = asyncio.create_task(_batch_worker(s.id, sem, data.template_id))
        _TASKS.add(t)
        _TASK_BY_SID[s.id] = t
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


@router.post("/cancel/{submission_id}")
async def cancel_grading(submission_id: int, db: Session = Depends(get_db)):
    """取消一份排队中/处理中的批改（第七轮健壮性）。

    取消后队列状态落 cancelled，提交回到 uploaded 可重新发起批改。
    """
    submission = db.query(Submission).filter(Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="提交记录不存在")

    task = _TASK_BY_SID.get(submission_id)
    state = QUEUE.get(submission_id, {}).get("state")
    if task is None or task.done():
        raise HTTPException(
            status_code=400,
            detail=f"当前没有可取消的批改任务（队列状态: {state or '无'}，提交状态: {submission.status}）")

    task.cancel()
    # 未启动过/被外部杀掉的 task 不会执行任何 except 块，状态由端点直接落：
    # 队列 cancelled、processing 的提交回 uploaded（_grade_one/worker 侧同状态幂等）
    QUEUE.setdefault(submission_id, {"state": "queued", "duration_seconds": None,
                                     "error": None}).update(
        state="cancelled", error="老师已取消批改")
    if submission.status == "processing":
        submission.status = "uploaded"
        submission.error_message = "老师已取消批改，可重新发起"
        db.commit()
    return {"success": True, "submission_id": submission_id,
            "message": "已取消批改"}


@router.get("/timeout")
async def read_grading_timeout():
    return {"success": True, "timeout_seconds": get_grading_timeout()}


class TimeoutIn(BaseModel):
    value: int


@router.post("/timeout")
async def write_grading_timeout(data: TimeoutIn, _: None = Depends(require_admin)):
    if not 60 <= data.value <= 900:
        raise HTTPException(status_code=400, detail="批改超时只能是 60-900 秒")
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == "grading_timeout").first()
        if row:
            row.value = str(data.value)
        else:
            db.add(AppConfig(key="grading_timeout", value=str(data.value)))
        db.commit()
    finally:
        db.close()
    return {"success": True, "timeout_seconds": get_grading_timeout()}


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
                "steps": r.steps,
            }
            for r in results
        ],
    }


# ============ 手动改答案归属（g6 遗留兜底入口） ============

@router.post("/reassign/{submission_id}")
async def reassign_answer(submission_id: int, payload: dict, db: Session = Depends(get_db)):
    """教师手动把一份学生答案从一个题移到另一个题（切题/归属错时的兜底）。

    行为：source 的答案文本+bbox 并入 target；source 答案清空并注明去向；
    target 标记 needs_review=1（合并后的判定不可信，进复核队列）。
    """
    source_id = payload.get("source_id")
    target_id = payload.get("target_id")
    if not source_id or not target_id:
        raise HTTPException(status_code=400, detail="缺少 source_id / target_id")
    if source_id == target_id:
        raise HTTPException(status_code=400, detail="不能把答案移给自己")

    rows = (db.query(QuestionResult)
              .filter(QuestionResult.submission_id == submission_id,
                      QuestionResult.id.in_([source_id, target_id]))
              .all())
    by_id = {r.id: r for r in rows}
    if source_id not in by_id or target_id not in by_id:
        raise HTTPException(status_code=404, detail="题目结果不存在或不属于该提交")

    source, target = by_id[source_id], by_id[target_id]
    if not (source.student_answer_text or source.bbox_answer):
        raise HTTPException(status_code=400, detail=f"第{source.question_no}题没有可移动的答案")

    # 答案文本合并（都有则空格拼接）
    if source.student_answer_text:
        target.student_answer_text = (
            (target.student_answer_text + " " + source.student_answer_text).strip()
            if target.student_answer_text else source.student_answer_text
        )
    # bbox 合并（列表拼接）
    src_boxes = source.bbox_answer if isinstance(source.bbox_answer, list) else []
    tgt_boxes = target.bbox_answer if isinstance(target.bbox_answer, list) else []
    if src_boxes:
        target.bbox_answer = tgt_boxes + src_boxes
        target.confidence_final = None if target.confidence_final is None \
            else min(target.confidence_final, 0.5)

    target.needs_review = 1
    target.review_note = f"已手动并入第{source.question_no}题的答案，判定待复核"

    source.student_answer_text = None
    source.bbox_answer = None
    source.steps = None
    source.review_note = f"答案已手动移至第{target.question_no}题"

    db.commit()
    return {"success": True,
            "source": {"id": source.id, "question_no": source.question_no},
            "target": {"id": target.id, "question_no": target.question_no,
                       "student_answer": target.student_answer_text,
                       "needs_review": target.needs_review}}


# ============ 批改结果导出 CSV（老师导入 Excel/成绩单） ============

CSV_HEADERS = ["题号", "题型", "学生答案", "参考答案", "判定", "错误类型",
               "置信度", "知识点", "需复核", "教师复核"]


def _question_text_of(r):
    """从 llm_raw_output 提取题干摘要（v0.5.5 起 prompt 返回 question_text；老数据留空）"""
    raw = r.llm_raw_output
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if not isinstance(raw, dict):
        return ""
    items = raw.get("questions") or raw.get("choices") or []
    for it in items:
        if isinstance(it, dict) and it.get("question_no") == r.question_no:
            return (it.get("question_text") or "").strip()
    return ""


def _judgement_cn(j: str) -> str:
    return {"correct": "正确", "wrong": "错误", "partial": "部分正确",
            "unclear": "无法辨认"}.get(j, j or "")


# ============ 学生个人错题本（打印发学生自己订正） ============

@router.get("/export-student/{student_id}")
async def export_student_wrong_csv(
    student_id: int,
    days: int = 0,
    db: Session = Depends(get_db),
):
    """单个学生的错题本 CSV：近 N 天（days=0 全部）该生所有作业中判定为
    错误/部分正确/无法辨认或需复核的题目，按知识点分组（错得最多的考点在前）。
    文件名「红笔错题本_姓名.csv」，UTF-8 带 BOM。"""
    from datetime import timedelta
    from ..database import Student as StudentModel
    from ..models.knowledge_norm import canonical_key

    import csv
    import io

    student = db.query(StudentModel).filter(StudentModel.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    now = datetime.utcnow()
    sub_q = db.query(Submission).filter(Submission.student_id == student_id)
    if days and days > 0:
        sub_q = sub_q.filter(Submission.created_at >= now - timedelta(days=days))
    subs = sub_q.all()
    assignments = {a.id: a for a in db.query(Assignment)
                   .filter(Assignment.id.in_([s.assignment_id for s in subs] or [0])).all()}
    sids = [s.id for s in subs]
    results = (db.query(QuestionResult)
               .filter(QuestionResult.submission_id.in_(sids or [0])).all())

    kp_errors = {}
    rows_by_kp = {}
    for r in results:
        if r.judgement_final in ("correct",) and not r.needs_review:
            continue
        if not (r.judgement_final in ("wrong", "partial", "unclear") or r.needs_review):
            continue
        kp = canonical_key(r.knowledge_point) or "（未识别知识点）"
        kp_errors[kp] = kp_errors.get(kp, 0) + 1
        a = assignments.get(next(s.assignment_id for s in subs if s.id == r.submission_id))
        rows_by_kp.setdefault(kp, []).append([
            (a.title or f"作业{a.id}") if a else "-",
            r.question_no,
            _question_text_of(r),
            r.student_answer_text or "",
            r.correct_answer_text or "",
            _judgement_cn(r.judgement_final),
            r.error_type or "",
            r.knowledge_point or "",
            "是" if r.needs_review else "",
        ])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"学生：{student.name}"] +
                    ["作业", "题号", "题干", "学生答案", "参考答案",
                     "判定", "错因", "知识点(原始)", "需复核"])
    for kp in sorted(rows_by_kp, key=lambda k: (-kp_errors[k], k)):
        writer.writerow([f"【{kp}】"])
        for row in sorted(rows_by_kp[kp], key=lambda x: (x[0], x[1])):
            writer.writerow(row)

    from urllib.parse import quote
    from fastapi import Response
    filename = quote(f"红笔错题本_{student.name}.csv")
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# ============ 按作业导出全班成绩单（每行一名学生） ============

CLASS_HEADERS = ["学生", "状态", "批改题数", "正确", "错误", "部分正确", "无法辨认",
                 "正确率", "等级", "需复核题数", "耗时(秒)", "提交时间"]


@router.get("/export-class/{assignment_id}")
async def export_class_csv(assignment_id: int, db: Session = Depends(get_db)):
    """按作业导出全班成绩单 CSV（UTF-8 带 BOM，每行一名学生）。"""
    from ..database import Student as StudentModel
    from ..models.grade_scale import get_thresholds, compute_grade

    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")

    import csv
    import io

    thresholds = get_thresholds(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"作业：{assignment.title}"] + CLASS_HEADERS)

    subs = (db.query(Submission, StudentModel.name)
            .outerjoin(StudentModel, StudentModel.id == Submission.student_id)
            .filter(Submission.assignment_id == assignment_id)
            .order_by(Submission.id).all())
    for sub, student_name in subs:
        results = (db.query(QuestionResult)
                   .filter(QuestionResult.submission_id == sub.id).all())
        counts = {"correct": 0, "wrong": 0, "partial": 0, "unclear": 0}
        needs_review = 0
        for r in results:
            if r.judgement_final in counts:
                counts[r.judgement_final] += 1
            if r.needs_review:
                needs_review += 1
        judged = counts["correct"] + counts["wrong"] + counts["partial"]
        accuracy = counts["correct"] / judged if judged else None
        writer.writerow([
            student_name or f"学生#{sub.student_id}",
            sub.status,
            len(results),
            counts["correct"], counts["wrong"], counts["partial"], counts["unclear"],
            f"{accuracy * 100:.0f}%" if accuracy is not None else "",
            compute_grade(accuracy, thresholds) or "",
            needs_review or "",
            f"{sub.duration_seconds:.1f}" if sub.duration_seconds else "",
            sub.created_at.strftime("%Y-%m-%d %H:%M") if sub.created_at else "",
        ])

    from urllib.parse import quote
    from fastapi import Response
    safe_title = assignment.title.replace('"', "")
    filename = quote(f"红笔成绩单_{safe_title}.csv")
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )




# ============ 按作业导出「学生 × 题号」得分矩阵（讲评：看全班哪道题错得多） ============

MATRIX_CELL = {
    "correct": "√",
    "wrong": "×",
    "partial": "△",
    "unclear": "?",
}


@router.get("/export-question-matrix/{assignment_id}")
async def export_question_matrix_csv(assignment_id: int, db: Session = Depends(get_db)):
    """按作业导出学生×题号得分矩阵 CSV（UTF-8 BOM）。

    行=学生，列=题1..题N；单元格 √/×/△/?/复核；末两行为每题正确数与正确率，
    正确率最低的题在最易讲评位置（列顺序仍按题号，正确率行用百分比标注）。
    """
    from ..database import Student as StudentModel
    import csv
    import io
    from urllib.parse import quote
    from fastapi import Response

    assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="作业不存在")

    subs = (db.query(Submission, StudentModel.name)
            .outerjoin(StudentModel, StudentModel.id == Submission.student_id)
            .filter(Submission.assignment_id == assignment_id)
            .order_by(Submission.id).all())
    max_no = 0
    rows = []  # (student_label, {question_no: cell})
    for sub, student_name in subs:
        results = (db.query(QuestionResult)
                   .filter(QuestionResult.submission_id == sub.id)
                   .order_by(QuestionResult.question_no).all())
        cells = {}
        for r in results:
            no = r.question_no
            max_no = max(max_no, no)
            if r.needs_review:
                cells[no] = "复核"
            elif r.teacher_override:
                cells[no] = MATRIX_CELL.get(r.teacher_override.lower(), r.teacher_override)
            else:
                cells[no] = MATRIX_CELL.get((r.judgement_final or "").lower(), "")
        rows.append((student_name or f"学生#{sub.student_id}", cells))

    if max_no == 0:
        raise HTTPException(status_code=404, detail="该作业暂无批改结果")

    header = ["学生"] + [f"题{i}" for i in range(1, max_no + 1)]
    correct_counts = {i: 0 for i in range(1, max_no + 1)}
    judged_counts = {i: 0 for i in range(1, max_no + 1)}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"得分矩阵：{assignment.title}"] + header[1:])
    writer.writerow(header)
    for label, cells in rows:
        writer.writerow([label] + [cells.get(i, "") for i in range(1, max_no + 1)])
        for i, cell in cells.items():
            if cell == "√":
                correct_counts[i] += 1
            if cell in ("√", "×", "△"):
                judged_counts[i] += 1

    rate_row = ["正确率"]
    count_row = ["正确数"]
    for i in range(1, max_no + 1):
        j = judged_counts[i]
        rate_row.append(f"{correct_counts[i] / j * 100:.0f}%" if j else "")
        count_row.append(correct_counts[i] if j else "")
    writer.writerow(rate_row)
    writer.writerow(count_row)

    safe_title = assignment.title.replace('"', "")
    filename = quote(f"得分矩阵_{safe_title}.csv")
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )

# ============ 跨作业合并导出全班成绩单（学生 × 各作业正确率矩阵） ============

@router.get("/export-class-all")
async def export_class_all_csv(
    days: int = 30,
    class_id: int = 0,
    db: Session = Depends(get_db),
):
    """合并导出：一个 CSV 汇总近 N 天（days=0 为全部）每份作业 × 每名学生的正确率矩阵，
    附平均正确率与平均等级（UTF-8 带 BOM）。class_id>0 时只导出该班级。"""
    from datetime import datetime, timedelta
    from ..database import Student as StudentModel
    from ..models.grade_scale import get_thresholds, compute_grade

    import csv
    import io

    now = datetime.utcnow()
    q = db.query(Assignment)
    if days and days > 0:
        q = q.filter(Assignment.created_at >= now - timedelta(days=days))
    if class_id and class_id > 0:
        q = q.filter(Assignment.class_id == class_id)
    assignments = q.order_by(Assignment.created_at).all()

    thresholds = get_thresholds(db)
    # 每个学生在每份作业的提交 → 正确率
    subs = (db.query(Submission)
            .filter(Submission.assignment_id.in_([a.id for a in assignments] or [0]))
            .all())
    # submission_id → accuracy
    acc_by_sub = {}
    kp_by_sub = {}  # submission_id → {kp: [total, correct]}
    sids = [s.id for s in subs]
    if sids:
        results = (db.query(QuestionResult)
                   .filter(QuestionResult.submission_id.in_(sids)).all())
        by_sub = {}
        from ..models.knowledge_norm import canonical_key
        for r in results:
            by_sub.setdefault(r.submission_id, []).append(r)
            kp = canonical_key(r.knowledge_point)
            if kp:
                st = kp_by_sub.setdefault(r.submission_id, {}).setdefault(kp, [0, 0])
                st[0] += 1
                if r.judgement_final == "correct":
                    st[1] += 1
        for s in subs:
            rs = by_sub.get(s.id, [])
            judged = sum(1 for r in rs if r.judgement_final in ("correct", "wrong", "partial"))
            correct = sum(1 for r in rs if r.judgement_final == "correct")
            if judged:
                acc_by_sub[s.id] = correct / judged

    sub_of = {}  # (student_id, assignment_id) → (submission, accuracy)
    kp_by_student = {}  # student_id → {kp: [total, correct]}
    student_ids = []
    for s in subs:
        if s.student_id is None:
            continue
        if s.student_id not in student_ids:
            student_ids.append(s.student_id)
        sub_of[(s.student_id, s.assignment_id)] = (s, acc_by_sub.get(s.id))
        for kp, st in kp_by_sub.get(s.id, {}).items():
            agg = kp_by_student.setdefault(s.student_id, {}).setdefault(kp, [0, 0])
            agg[0] += st[0]
            agg[1] += st[1]

    # 知识点掌握列：全班出现的知识点求并集，薄弱（正确率低）的排前面，最多 12 列
    kp_union = {}
    for m in kp_by_student.values():
        for kp, st in m.items():
            u = kp_union.setdefault(kp, [0, 0])
            u[0] += st[0]
            u[1] += st[1]
    def _kp_acc(st):
        return st[1] / st[0] if st[0] else 1.0
    kp_columns = sorted(kp_union, key=lambda k: (_kp_acc(kp_union[k]), -kp_union[k][0], k))[:12]

    from ..database import Class as ClassModel
    students = (db.query(StudentModel, ClassModel.name)
                .outerjoin(ClassModel, ClassModel.id == StudentModel.class_id)
                .filter(StudentModel.id.in_(student_ids or [0]))
                .order_by(StudentModel.id).all())

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["班级", "学生"] + [a.title or f"作业{a.id}" for a in assignments]
                    + ["平均正确率", "平均等级"] + [f"知识点:{kp}" for kp in kp_columns])
    for st, class_name in students:
        accs = []
        row = [class_name or "-", st.name]
        for a in assignments:
            pair = sub_of.get((st.id, a.id))
            if pair is not None and pair[1] is not None:
                accs.append(pair[1])
                row.append(f"{pair[1] * 100:.0f}%")
            else:
                row.append("-")
        if accs:
            avg = sum(accs) / len(accs)
            row.append(f"{avg * 100:.0f}%")
            row.append(compute_grade(avg, thresholds) or "")
        else:
            row += ["", ""]
        m = kp_by_student.get(st.id, {})
        for kp in kp_columns:
            st_kp = m.get(kp)
            if st_kp and st_kp[0]:
                row.append(f"{_kp_acc(st_kp) * 100:.0f}%({st_kp[1]}/{st_kp[0]})")
            else:
                row.append("")
        writer.writerow(row)

    from urllib.parse import quote
    from fastapi import Response
    label = f"近{days}天" if days and days > 0 else "全部"
    if class_id and class_id > 0:
        cls = db.query(ClassModel).filter(ClassModel.id == class_id).first()
        if cls:
            label = f"{label}_{cls.name}"
    filename = quote(f"红笔成绩单_汇总_{label}.csv")
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


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


# ============ 全班错题讲评清单导出（按知识点薄弱优先分组） ============

@router.get("/export-wrong-all")
async def export_wrong_all_csv(
    days: int = 30,
    class_id: int = 0,
    db: Session = Depends(get_db),
):
    """全班错题讲评清单：近 N 天（days=0 全部）所有作业中判定为错误/部分正确/无法辨认
    或需复核的题目，一行一题。按知识点错题次数降序分组（全班错误最多的考点排前面），
    同一知识点内按学生、题号排——老师讲评时按考点顺序过一遍即可。UTF-8 带 BOM。"""
    from datetime import timedelta
    from ..database import Student as StudentModel, Class as ClassModel
    from ..models.knowledge_norm import canonical_key

    import csv
    import io

    now = datetime.utcnow()
    q = db.query(Assignment)
    if days and days > 0:
        q = q.filter(Assignment.created_at >= now - timedelta(days=days))
    if class_id and class_id > 0:
        q = q.filter(Assignment.class_id == class_id)
    assignments = {a.id: a for a in q.all()}

    subs = (db.query(Submission)
            .filter(Submission.assignment_id.in_(list(assignments) or [0]))
            .all())
    student_ids = sorted({s.student_id for s in subs if s.student_id is not None})
    students = {st.id: st for st in db.query(StudentModel)
                .filter(StudentModel.id.in_(student_ids or [0])).all()}
    class_names = {c.id: c.name for c in db.query(ClassModel).all()}

    sids = [s.id for s in subs]
    results = (db.query(QuestionResult)
               .filter(QuestionResult.submission_id.in_(sids or [0])).all())
    sub_by_id = {s.id: s for s in subs}

    # 从 llm_raw_output 提取题干摘要（v0.5.5 起 prompt 返回 question_text；老数据留空）
    def _qtext_of(r):
        raw = r.llm_raw_output
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        if not isinstance(raw, dict):
            return ""
        items = raw.get("questions") or raw.get("choices") or []
        for it in items:
            if isinstance(it, dict) and it.get("question_no") == r.question_no:
                return (it.get("question_text") or "").strip()
        return ""

    # 每个知识点的错题总数（错误次数最多的知识点排前面 = 全班最薄弱的考点）
    kp_errors = {}
    for r in results:
        if r.judgement_final in ("correct",) and not r.needs_review:
            continue
        if not (r.judgement_final in ("wrong", "partial", "unclear") or r.needs_review):
            continue
        kp = canonical_key(r.knowledge_point) or "（未识别知识点）"
        kp_errors[kp] = kp_errors.get(kp, 0) + 1

    def _kp_sort_key(kp):
        return (-kp_errors[kp], kp)

    # 收集错题行，按知识点分组
    rows_by_kp = {}
    for r in results:
        if r.judgement_final in ("correct",) and not r.needs_review:
            continue
        if not (r.judgement_final in ("wrong", "partial", "unclear") or r.needs_review):
            continue
        s = sub_by_id.get(r.submission_id)
        if s is None or s.student_id is None or s.student_id not in students:
            continue
        st = students[s.student_id]
        a = assignments.get(s.assignment_id)
        kp = canonical_key(r.knowledge_point) or "（未识别知识点）"
        rows_by_kp.setdefault(kp, []).append([
            (a.title or f"作业{a.id}") if a else "-",
            class_names.get(st.class_id) or "-",
            st.name,
            r.question_no,
            _qtext_of(r),
            r.student_answer_text or "",
            r.correct_answer_text or "",
            _judgement_cn(r.judgement_final),
            r.error_type or "",
            r.knowledge_point or "",
            "是" if r.needs_review else "",
        ])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["作业", "班级", "学生", "题号", "题干", "学生答案", "参考答案",
                     "判定", "错因", "知识点(原始)", "需复核"])
    for kp in sorted(rows_by_kp, key=_kp_sort_key):
        for row in sorted(rows_by_kp[kp], key=lambda x: (x[2], x[3], x[0])):
            writer.writerow(row)

    from urllib.parse import quote
    from fastapi import Response
    filename = quote("红笔错题讲评清单.csv")
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
