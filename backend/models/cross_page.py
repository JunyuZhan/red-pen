"""
跨页续题（待办 g6，2026-11-14 用户提出）：
作业拍 2-3 页时题干/答案会被页边界切开——上一页尾题只有题干没有答案，
下一页首部只有裸手写答案没有题号。

检测：上一页最后一题「学生答案为空」+ 当前页第一题「学生答案非空」→ 判定为续题对。
合并：取两题的水平带（各自 bbox），纵向拼接成一张图送 AI 重批，用合并结果
回写上一页尾题（判定/答案/置信度），下一页首题保留标注（红笔画回各自原页），
review_note 注明已并入上一页。

任何异常都只跳过合并，不影响正常批改结果。
"""

import logging
import os
from typing import Optional, Tuple

from ..config import settings
from ..database import QuestionResult, Submission

logger = logging.getLogger("redpen.cross_page")


def _is_blank(ans) -> bool:
    return not (ans or "").strip()


def find_pair(db, submission: Submission) -> Optional[Tuple[QuestionResult, QuestionResult]]:
    """在同学业的更早提交里找「页尾题无答案 + 本页首题有答案」的续题对。

    返回 (上一页尾题 QuestionResult, 本页首题 QuestionResult)，找不到返回 None。
    """
    try:
        prev_sub = (
            db.query(Submission)
            .filter(
                Submission.assignment_id == submission.assignment_id,
                Submission.id < submission.id,
                Submission.status == "completed",
            )
            .order_by(Submission.id.desc())
            .first()
        )
        if not prev_sub:
            return None
        prev_last = (
            db.query(QuestionResult)
            .filter(QuestionResult.submission_id == prev_sub.id)
            .order_by(QuestionResult.id.desc())
            .first()
        )
        cur_first = (
            db.query(QuestionResult)
            .filter(QuestionResult.submission_id == submission.id)
            .order_by(QuestionResult.id.asc())
            .first()
        )
        if not prev_last or not cur_first:
            return None
        # 教师已人工裁决的题不自动改
        if prev_last.teacher_override:
            return None
        if _is_blank(prev_last.student_answer_text) and not _is_blank(cur_first.student_answer_text):
            return prev_last, cur_first
    except Exception as e:
        logger.warning("跨页续题检测失败: %s", str(e)[:200])
    return None


def _band_from_bbox(img_path: str, bbox, fallback_ratio: float = 0.25):
    """按 bbox 取全宽水平带；bbox 缺失/非法时取页面下部 fallback_ratio 比例作兜底。"""
    from PIL import Image
    img = Image.open(img_path).convert("RGB")
    w, h = img.size
    top, bottom = None, None
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            ys = [float(bbox[1]), float(bbox[3])]
            # 兼容四角点格式 [[x,y],...]
            if all(isinstance(v, (list, tuple)) for v in bbox[:4]):
                ys = [float(p[1]) for p in bbox[:4]]
            top, bottom = max(0, int(min(ys))), min(h, int(max(ys)))
        except Exception:
            top = bottom = None
    if top is None or bottom - top < 8:
        top, bottom = int(h * (1 - fallback_ratio)), h
    return img.crop((0, top, w, bottom))


def build_combined_image(prev_img: str, prev_bbox, cur_img: str, cur_bbox,
                         out_path: str) -> Optional[str]:
    """上一页尾题带 + 本页首题带 纵向拼成一张图（白底分隔线），供 AI 重批。"""
    from PIL import Image
    try:
        band_a = _band_from_bbox(prev_img, prev_bbox)
        band_b = _band_from_bbox(cur_img, cur_bbox)
        w = max(band_a.size[0], band_b.size[0])
        gap = 6
        canvas = Image.new("RGB", (w, band_a.size[1] + gap + band_b.size[1]), (255, 255, 255))
        canvas.paste(band_a, (0, 0))
        canvas.paste(band_b, (0, band_a.size[1] + gap))
        canvas.save(out_path, quality=92)
        return out_path
    except Exception as e:
        logger.warning("跨页拼图失败: %s", str(e)[:200])
        return None


def _apply_result(qr: QuestionResult, q: dict, note: str) -> None:
    conf = q.get("confidence")
    qr.student_answer_text = q.get("student_answer") or qr.student_answer_text
    qr.judgement_raw = q.get("judgement")
    qr.judgement_final = q.get("judgement")
    qr.error_type = q.get("error_type")
    qr.knowledge_point = q.get("knowledge_point") or qr.knowledge_point
    if isinstance(conf, (int, float)):
        qr.confidence_final = float(conf)
    qr.needs_review = 1 if q.get("needs_review") else 0
    qr.review_note = note


async def maybe_cross_page_merge(db, submission: Submission, subject: str = "数学",
                                 answers=None) -> Optional[dict]:
    """批改完成后调用：检测并合并跨页续题。失败静默跳过。"""
    pair = find_pair(db, submission)
    if not pair:
        return None
    prev_last, cur_first = pair
    try:
        from .grading_engine import GradingEngine, get_llm_config
        cfg = get_llm_config()
        if not cfg.get("api_key"):
            return None

        out_dir = os.path.join(str(settings.DATA_DIR), "splits")
        os.makedirs(out_dir, exist_ok=True)
        combined_path = os.path.join(out_dir, f"crosspage_{submission.id}_q{cur_first.question_no}.jpg")
        prev_sub = db.query(Submission).filter(Submission.id == prev_last.submission_id).first()
        combined = build_combined_image(
            prev_sub.original_image_path, prev_last.bbox_answer,
            submission.original_image_path, cur_first.bbox_answer,
            combined_path)
        if not combined:
            return None

        engine = GradingEngine(cfg["api_key"], cfg["model"], cfg["base_url"])
        result = await engine.grade(combined, "default", subject, answers=answers)
        if not result.get("success"):
            logger.info("跨页续题重批未成功: %s", str(result.get("error", ""))[:120])
            return None

        merged_qs = result.get("questions", [])
        target = None
        for q in merged_qs:
            if str(q.get("question_no")) == str(prev_last.question_no):
                target = q
                break
        if target is None and merged_qs:
            target = merged_qs[0]
        if not target:
            return None

        note_a = f"跨页续题：与下一页第{cur_first.question_no}题合并批改"
        note_b = f"跨页续题：此答案已并入上一页第{prev_last.question_no}题合并判定"
        _apply_result(prev_last, target, note_a)
        cur_first.review_note = note_b
        # 下一页首题的判定与合并结果同步，红笔画回各自原页
        conf = target.get("confidence")
        if isinstance(conf, (int, float)):
            cur_first.confidence_final = float(conf)
        cur_first.judgement_final = target.get("judgement") or cur_first.judgement_final
        cur_first.judgement_raw = target.get("judgement") or cur_first.judgement_raw
        db.commit()
        logger.info("跨页续题合并完成: prev_q%s + cur_q%s (submission=%s)",
                    prev_last.question_no, cur_first.question_no, submission.id)
        return {"merged": True, "prev_question_no": prev_last.question_no,
                "cur_question_no": cur_first.question_no}
    except Exception as e:
        logger.warning("跨页续题合并失败: %s", str(e)[:200])
        db.rollback()
        return None
