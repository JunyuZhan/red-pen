# -*- coding: utf-8 -*-
"""知识点归一化测试（v0.4.16）"""
import pytest

from backend.models.knowledge_norm import canonical_key, normalize_knowledge_point


class TestNormalize:
    def test_plain_unchanged(self):
        assert normalize_knowledge_point("两位数乘法") == "两位数乘法"

    def test_removes_all_whitespace(self):
        assert normalize_knowledge_point("两位数乘 法") == "两位数乘法"
        assert normalize_knowledge_point(" 两位数乘法 ") == "两位数乘法"
        assert normalize_knowledge_point("两位数\u3000乘法") == "两位数乘法"  # 全角空格

    def test_fullwidth_to_halfwidth(self):
        assert normalize_knowledge_point("１００以内加法") == "100以内加法"

    def test_trailing_punct_stripped(self):
        assert normalize_knowledge_point("两位数乘法。") == "两位数乘法"
        assert normalize_knowledge_point("两位数乘法,") == "两位数乘法"

    def test_alias_mapping(self):
        assert normalize_knowledge_point("二位数乘法") == "两位数乘法"
        assert normalize_knowledge_point("退位减") == "退位减法"

    def test_empty_and_none(self):
        assert normalize_knowledge_point(None) == ""
        assert normalize_knowledge_point("") == ""
        assert normalize_knowledge_point("。") == ""

    def test_canonical_key_lowercase(self):
        assert canonical_key("Fractions") == "fractions"

    def test_distinct_kp_not_merged(self):
        # 相近但不同的知识点绝不能被合并（安全底线）
        assert canonical_key("进位加法") != canonical_key("退位减法")
        assert canonical_key("两位数乘法") != canonical_key("两位数除法")


def test_analytics_variants_merged(auth_client, tmp_db):
    """LLM 写法变体（空格/全角/标点/别名）应聚合为同一知识点。"""
    from backend.database import (Assignment, Class, Student, Submission,
                                  QuestionResult)

    db = tmp_db.SessionLocal()
    try:
        cls = Class(name="三(1)班", teacher_id=1, stage_id=1, subject_id=1); db.add(cls); db.flush()
        asg = Assignment(class_id=cls.id, title="口算", subject_id=1); db.add(asg); db.flush()
        stu = Student(name="小明", class_id=cls.id); db.add(stu); db.flush()
        sub = Submission(assignment_id=asg.id, student_id=stu.id,
                         original_image_path="/tmp/x.jpg", status="completed")
        db.add(sub); db.flush()
        variants = ["两位数乘法", "两位数乘 法", "二位数乘法",
                    "两位数乘法。", "　两位数乘法　"]
        for i, kp in enumerate(variants):
            db.add(QuestionResult(
                submission_id=sub.id, question_no=i + 1,
                question_type="计算",
                knowledge_point=kp,
                judgement_final="wrong" if i % 2 == 0 else "correct"))
        db.commit()
        class_id = cls.id
    finally:
        db.close()

    r = auth_client.get("/api/analytics/analytics")
    assert r.status_code == 200, r.text
    kps = r.json()["knowledge_points"]
    assert len(kps) == 1
    assert kps[0]["name"] == "两位数乘法"
    assert kps[0]["total"] == 5
    assert kps[0]["correct"] == 2
