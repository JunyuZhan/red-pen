# -*- coding: utf-8 -*-
"""规则交叉校验层专项测试：准确性保障的核心防线"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.rule_validator import (
    answers_equivalent, rule_validate, _numbers,
)

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f"  ✅ {name}")
    else: failed += 1; print(f"  ❌ {name}")

print("== 等价判断 ==")
check("数值相等 3.0==3", answers_equivalent("3.0", "3") is True)
check("分数 1/2==0.5", answers_equivalent("1/2", "0.5") is True)
check("数值不等 5≠6", answers_equivalent("5", "6") is False)
check("选项 A==a", answers_equivalent("A", "a") is True)
check("选项 A≠B", answers_equivalent("A", "B") is False)
check("全角３==3", answers_equivalent("３", "3") is True)
check("带逗号 1,234==1234", answers_equivalent("1,234", "1234") is True)
check("语义类返回None", answers_equivalent("因为三角形内角和180度", "180") is None)
check("负数 -3", answers_equivalent("-3", "-3.0") is True)
check("空串None", answers_equivalent("", "3") is None)

print("== 未作答强判 ==")
qs = [{"question_no": 1, "judgement": "unclear", "student_answer": "", "confidence": 0.3}]
s = rule_validate(qs, {"1": "42"})
check("空白→wrong", qs[0]["judgement"] == "wrong")
check("error_type=未作答", qs[0]["error_type"] == "未作答")
check("高置信0.98", qs[0]["confidence"] == 0.98)
check("标记rule_forced_blank", "rule_forced_blank" in qs[0]["rule_flags"])
qs2 = [{"question_no": 1, "judgement": "correct", "student_answer": "没写", "confidence": 0.9}]
rule_validate(qs2, {"1": "42"})
check("'没写'也判wrong", qs2[0]["judgement"] == "wrong")

print("== 规则推翻模型 ==")
# 模型判错但学生答案与参考等价 → 改判correct
qs3 = [{"question_no": 1, "judgement": "wrong", "student_answer": "3.0",
        "correct_answer": "3", "confidence": 0.7}]
rule_validate(qs3, {"1": "3"})
check("3.0被误判wrong→correct", qs3[0]["judgement"] == "correct")
check("标记overridden", "rule_overridden_to_correct" in qs3[0]["rule_flags"])
# 模型判对但数值不等 → 改判wrong
qs4 = [{"question_no": 2, "judgement": "correct", "student_answer": "56",
        "confidence": 0.9}]
rule_validate(qs4, {"2": "65"})
check("56≠65被误判correct→wrong", qs4[0]["judgement"] == "wrong")
# 选项不等
qs5 = [{"question_no": 3, "judgement": "correct", "student_answer": "C",
        "confidence": 0.85}]
rule_validate(qs5, {"3": "A"})
check("选C参考A→wrong", qs5[0]["judgement"] == "wrong")

print("== 语义类降置信进复核 ==")
qs6 = [{"question_no": 4, "judgement": "correct",
        "student_answer": "因为两边之和大于第三边", "confidence": 0.9}]
s6 = rule_validate(qs6, {"4": "两点之间线段最短"})
check("语义类不强改judgement", qs6[0]["judgement"] == "correct")
check("置信度降到0.55", qs6[0]["confidence"] == 0.55)
check("needs_review标记", qs6[0].get("needs_review") is True)
check("标记rule_needs_review", "rule_needs_review" in qs6[0]["rule_flags"])

print("== 边界 ==")
qs7 = [{"question_no": 9, "judgement": "correct", "student_answer": "7", "confidence": 0.8}]
s7 = rule_validate(qs7, {})  # 无参考答案
check("无参考答案不检查", s7["checked"] == 0 and qs7[0]["judgement"] == "correct")
qs8 = [{"question_no": 1, "judgement": "correct", "student_answer": "12.50", "confidence": 0.8}]
rule_validate(qs8, {"1": "12.5"})
check("12.50==12.5", qs8[0]["judgement"] == "correct")
qs9 = [{"question_no": 1, "judgement": "wrong", "student_answer": "120", "confidence": 0.6}]
rule_validate(qs9, {"1": "120"})
check("模型判错但实际相等→correct", qs9[0]["judgement"] == "correct")
check("_numbers分数", _numbers("3/4") == [0.75])

print(f"\n结果: {passed} 通过, {failed} 失败")
sys.exit(1 if failed else 0)
