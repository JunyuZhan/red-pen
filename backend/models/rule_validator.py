# -*- coding: utf-8 -*-
"""
规则交叉校验层：用确定性规则约束大模型的批改结果，把"模型犯错"拦下来。

设计原则（准确性第一）：
- 有教师参考答案才有资格用规则复核；
- 规则与模型结论一致 → 照常通过；
- 规则与模型结论矛盾 → 以规则为准（可证明的），并打 rule_overridden 标记；
- 规则无法证明的语义类答案 → 不强改，但降低置信度并标 needs_review，进人工复核队列。
"""
import re
from typing import Any, Dict, List, Optional

# 全角转半角映射
_FULL2HALF = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ：；，．（）",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz:;,.()")

_BLANK_WORDS = {"", "未作答", "未答", "无", "空", "未填", "null", "none", "没写", "空白"}

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_CHOICE_RE = re.compile(r"^[A-Ha-h]$")


def _normalize(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).strip().translate(_FULL2HALF)
    s = re.sub(r"[\s,，。;；:：、'\"。]+", "", s)
    return s.lower()


def _numbers(text: Any) -> List[float]:
    """提取文本里的数字（含负数/小数/分数 a/b/百分数）。"""
    s = _normalize(text)
    out: List[float] = []
    for frac in re.finditer(r"(-?\d+)/(\d+)", s):
        out.append(float(frac.group(1)) / float(frac.group(2)))
    s = re.sub(r"-?\d+/\d+", "", s)  # 分数已处理，避免重复
    for m in _NUM_RE.finditer(s):
        out.append(float(m.group(0)))
    return out


def _num_equal(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def answers_equivalent(student: Any, ref: Any) -> Optional[bool]:
    """判断学生答案与参考答案是否等价。返回 True/False；无法判断时 None。

    仅在双方都能可靠解析（数值/分数/单个选项字母）时给出确定结论；
    含文字的语义类答案返回 None，交给模型 + 人工复核。
    """
    ns, nr = _normalize(student), _normalize(ref)
    if not ns or not nr:
        return None
    # 单选项字母
    if _CHOICE_RE.match(ns) and _CHOICE_RE.match(nr):
        return ns.upper() == nr.upper()
    # 纯数值/分数（归一化后只由数字与小数点组成）
    nums_s, nums_r = _numbers(ns), _numbers(nr)
    pure_num_s = re.fullmatch(r"-?\d+(?:\.\d+)?(?:/-?\d+(?:\.\d+)?)?", ns) is not None
    pure_num_r = re.fullmatch(r"-?\d+(?:\.\d+)?(?:/-?\d+(?:\.\d+)?)?", nr) is not None
    if pure_num_s and pure_num_r and nums_s and nums_r:
        if len(nums_s) != len(nums_r):
            return False
        return all(_num_equal(a, b) for a, b in zip(sorted(nums_s), sorted(nums_r)))
    # 字符串完全一致（含单位等）
    if ns == nr:
        return True
    return None  # 语义类：规则不下结论


def _is_blank(student: Any, error_type: Any = None) -> bool:
    if error_type and "未作答" in str(error_type):
        return True
    return _normalize(student) in _BLANK_WORDS


def rule_validate(questions: List[Dict[str, Any]],
                  answers: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """对模型输出的逐题结果做规则复核，就地修正并打标记。

    返回统计：{overridden, forced_blank, downgraded, checked}
    """
    stats = {"overridden": 0, "forced_blank": 0, "downgraded": 0, "checked": 0}
    if not questions or not answers:
        return stats
    key_map = {str(k): v for k, v in answers.items()}
    for q in questions:
        no = str(q.get("question_no", ""))
        ref = key_map.get(no)
        if ref is None:
            continue
        stats["checked"] += 1
        student = q.get("student_answer")
        judgement = str(q.get("judgement", "unclear")).lower()
        conf = q.get("confidence")
        conf = conf if isinstance(conf, (int, float)) else 0.5
        flags: List[str] = []

        # 规则1：学生未作答 → 强判 wrong（模型偶尔会把它判成其他）
        if _is_blank(student, q.get("error_type")):
            if judgement != "wrong":
                flags.append("rule_forced_blank")
                q["judgement"], q["error_type"], q["confidence"] = "wrong", "未作答", 0.98
                stats["forced_blank"] += 1
                judgement = "wrong"
        else:
            eq = answers_equivalent(student, ref)
            # 规则2：学生答案与参考答案可证明等价，但模型判错 → 以规则为准改判 correct
            if eq is True and judgement in ("wrong", "partial"):
                flags.append("rule_overridden_to_correct")
                q["judgement"], q["confidence"] = "correct", max(conf, 0.95)
                q["error_type"] = None
                stats["overridden"] += 1
                judgement = "correct"
            # 规则3：可证明不等价（数值/选项），模型却判对 → 改判 wrong
            elif eq is False and judgement == "correct":
                flags.append("rule_overridden_to_wrong")
                q["judgement"], q["confidence"] = "wrong", max(conf, 0.9)
                stats["overridden"] += 1
                judgement = "wrong"
            # 规则4：语义类答案、模型判对但规则无法验证 → 降置信度进人工复核
            elif eq is None and judgement == "correct" and conf > 0.6:
                q["confidence"] = 0.55
                q["needs_review"] = True
                flags.append("rule_needs_review")
                stats["downgraded"] += 1

        if flags:
            q.setdefault("rule_flags", [])
            q["rule_flags"].extend(flags)
    return stats
