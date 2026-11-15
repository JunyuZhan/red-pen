#!/usr/bin/env python3
"""真实批改准确率验证脚本（最高优先待办）。

用途：用一张内置生成的标准作业图片（题目与标准答案已知），直接调用
GradingEngine 走真实厂商 API（qwen3-vl-plus / glm-4.6v-flash），
对比模型判卷结果与已知真相，输出逐题判定，作为准确率基线。

用法：
    python3 scripts/accuracy_check.py [--image path/to/homework.jpg]

后续拿到真实学生作业照片后，直接 --image 传入即可复用同一评测逻辑。
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 真相：图中 4 道题，第 3 题故意写错（学生答 96，正确 100）
TRUTH = [
    {"question_no": 1, "student_answer": "25 × 4 = 100", "correct": "100", "expect": "correct"},
    {"question_no": 2, "student_answer": "37 + 58 = 95", "correct": "95", "expect": "correct"},
    {"question_no": 3, "student_answer": "25 × 4 = 96", "correct": "100", "expect": "wrong"},
    {"question_no": 4, "student_answer": "72 ÷ 8 = 9", "correct": "9", "expect": "correct"},
]


def make_sample_image(path: Path) -> Path:
    """PIL 生成一张白底数学口算作业图（ handwriting 模拟：黑字白纸）。"""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for cand in ["/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
                 "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                 "C:/Windows/Fonts/msyh.ttc"]:
        if Path(cand).exists():
            font = ImageFont.truetype(cand, 44)
            break
    if font is None:
        font = ImageFont.load_default()

    img = Image.new("RGB", (720, 560), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 30), "数学口算作业  姓名：测试", fill="black", font=font)
    lines = ["1. 25 × 4 = 100", "2. 37 + 58 = 95",
             "3. 25 × 4 = 96", "4. 72 ÷ 8 = 9"]
    y = 120
    for line in lines:
        d.text((60, y), line, fill="black", font=font)
        y += 100
    img.save(path, quality=95)
    return path


ANSWERS = {str(t["question_no"]): t["correct"] for t in TRUTH}


async def run_engine(name, api_key, base_url, model, image_path):
    from backend.models.grading_engine import GradingEngine
    engine = GradingEngine(api_key=api_key, model_name=model, base_url=base_url)
    result = await engine.grade(str(image_path), question_type="math",
                                subject="数学", answers=ANSWERS)
    return name, result


def score(result):
    qs = {q.get("question_no"): q for q in result.get("questions", [])}
    rows = []
    hit = 0
    for t in TRUTH:
        q = qs.get(t["question_no"]) or {}
        j = q.get("judgement")
        ok = (j == t["expect"]) or (t["expect"] == "wrong" and j in ("wrong", "partial"))
        hit += ok
        rows.append((t["question_no"], t["student_answer"], j, t["expect"], "✓" if ok else "✗",
                     q.get("confidence"), q.get("student_answer", "")[:20]))
    return hit, rows


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="已有作业图片路径（默认生成内置样例）")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()

    if args.image:
        image_path = Path(args.image)
    else:
        image_path = Path(__file__).parent / "accuracy_sample.png"
        make_sample_image(image_path)
    print(f"评测图片: {image_path}")

    cases = []
    if os.environ.get("DASHSCOPE_API_KEY"):
        cases.append(("qwen3-vl-plus", os.environ["DASHSCOPE_API_KEY"],
                      "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3-vl-plus"))
    if os.environ.get("ZHIPU_API_KEY"):
        cases.append(("glm-4.6v-flash", os.environ["ZHIPU_API_KEY"],
                      "https://open.bigmodel.cn/api/paas/v4", "glm-4.6v-flash"))
    if not cases:
        print("未设置 DASHSCOPE_API_KEY / ZHIPU_API_KEY，无法实测")
        sys.exit(1)

    report = {"image": str(image_path), "truth": TRUTH, "engines": {}}
    for name, key, base_url, model in cases:
        print(f"\n=== {name} ({model}) ===")
        try:
            _, result = await run_engine(name, key, base_url, model, image_path)
        except Exception as e:
            result = {"success": False, "error": repr(e)}
        if not result.get("success"):
            print(f"  调用失败: {result.get('error')}")
            report["engines"][name] = {"success": False, "error": result.get("error")}
            continue
        hit, rows = score(result)
        print(f"  判定命中 {hit}/{len(TRUTH)}")
        for r in rows:
            print(f"    题{r[0]} 学生答[{r[1]}] -> 判定={r[2]} 期望={r[3]} {r[4]} 置信度={r[5]} 模型读到[{r[6]}]")
        report["engines"][name] = {"success": True, "hit": hit, "total": len(TRUTH),
                                   "rows": [list(map(str, r)) for r in rows],
                                   "summary": result.get("summary")}
        print(f"  总评: {result.get('summary')}")

    out = args.out or str(Path(__file__).parent / "accuracy_report.json")
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main())
