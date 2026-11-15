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


def load_batch(batch_dir: Path):
    """批量模式：目录下照片 + ground_truth.json（用法见 --batch 帮助）。"""
    gt = batch_dir / "ground_truth.json"
    if not gt.exists():
        raise SystemExit(f"批量目录缺少 {gt}")
    items = json.loads(gt.read_text(encoding="utf-8"))
    cases = []
    for item in items:
        img = batch_dir / item["image"]
        if not img.exists():
            print(f"跳过缺失图片: {img}")
            continue
        truth = item["questions"]
        answers = {str(t["question_no"]): t["correct"] for t in truth}
        cases.append((img, truth, answers))
    if not cases:
        raise SystemExit("批量目录没有可用图片")
    return cases


async def run_engine(name, api_key, base_url, model, image_path, answers):
    from backend.models.grading_engine import GradingEngine
    engine = GradingEngine(api_key=api_key, model_name=model, base_url=base_url)
    result = await engine.grade(str(image_path), question_type="math",
                                subject="数学", answers=answers)
    return name, result


def score(result, truth):
    qs = {q.get("question_no"): q for q in result.get("questions", [])}
    rows = []
    hit = 0
    for t in truth:
        q = qs.get(t["question_no"]) or {}
        j = q.get("judgement")
        ok = (j == t["expect"]) or (t["expect"] == "wrong" and j in ("wrong", "partial"))
        hit += ok
        rows.append((t["question_no"], t["student_answer"], j, t["expect"], "✓" if ok else "✗",
                     q.get("confidence"), q.get("student_answer", "")[:20]))
    return hit, rows


def markdown_report(report: dict) -> str:
    """把 JSON 报告转成教师可读的 Markdown：总准确率 + 逐题命中率。"""
    lines = ["# 批改准确率评测报告", ""]
    for name, eng in report.get("engines", {}).items():
        if not eng.get("total"):
            lines += [f"## {name}", "- 无可用结果（API 调用失败）", ""]
            continue
        acc = eng["accuracy"]
        lines += [f"## {name}",
                  f"- 总命中：**{eng['hit']}/{eng['total']} = {acc:.1%}**"]
        avg_s, low_n, conf_n = engine_reliability(eng.get("cases", []))
        if avg_s is not None:
            lines.append(f"- 平均单图耗时：**{avg_s:.1f}s**")
        if conf_n:
            lines.append(f"- 低置信度（<0.7）题占比：**{low_n}/{conf_n} = {low_n / conf_n:.1%}**（建议人工复核）")
        lines += ["", "| 题号 | 命中 | 命中率 |", "|---|---|---|"]
        stat = question_stats(eng.get("cases", []))
        for qno in sorted(stat):
            hit, tot = stat[qno]
            lines.append(f"| {qno} | {hit}/{tot} | {hit / tot:.0%} |")
        lines.append("")
    return "\n".join(lines)


def engine_reliability(cases):
    """可靠性统计：平均单图耗时（成功图）、低置信度(<0.7)题数/总判定题数。"""
    elapsed = [c["elapsed_s"] for c in cases if c.get("success") and c.get("elapsed_s")]
    avg = sum(elapsed) / len(elapsed) if elapsed else None
    low = total = 0
    for c in cases:
        if not c.get("success"):
            continue
        for row in c.get("rows", []):
            try:
                conf = float(row[5])
            except (TypeError, ValueError, IndexError):
                continue
            total += 1
            low += conf < 0.7
    return avg, low, total


def question_stats(cases):
    """逐题命中统计：{question_no: (hit, total)}，跨图聚合。"""
    stat = {}
    for c in cases:
        if not c.get("success"):
            continue
        for row in c.get("rows", []):
            qno, ok = row[0], row[4] == "✓"
            h, t = stat.get(qno, (0, 0))
            stat[qno] = (h + int(ok), t + 1)
    return stat


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="已有作业图片路径（默认生成内置样例）")
    ap.add_argument("--batch", help="批量模式：目录，内含多张照片 + ground_truth.json")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()

    if args.batch:
        eval_cases = load_batch(Path(args.batch))
    else:
        if args.image:
            image_path = Path(args.image)
        else:
            image_path = Path(__file__).parent / "accuracy_sample.png"
            make_sample_image(image_path)
        eval_cases = [(image_path, TRUTH, ANSWERS)]
    print(f"共 {len(eval_cases)} 份待评测图片")

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

    report = {"mode": "batch" if args.batch else "single", "engines": {}}
    for name, key, base_url, model in cases:
        print(f"\n=== {name} ({model}) ===")
        total_hit = total_q = 0
        engine_ok = True
        report_cases = []
        for image_path, truth, answers in eval_cases:
            print(f"-- {image_path.name} ({len(truth)} 题)")
            import time
            t0 = time.monotonic()
            try:
                _, result = await run_engine(name, key, base_url, model, image_path, answers)
            except Exception as e:
                result = {"success": False, "error": repr(e)}
            elapsed_s = round(time.monotonic() - t0, 2)
            if not result.get("success"):
                print(f"  调用失败: {result.get('error')}")
                report_cases.append({"image": image_path.name, "success": False,
                                     "error": str(result.get("error")), "elapsed_s": elapsed_s})
                engine_ok = False
                continue
            hit, rows = score(result, truth)
            total_hit += hit
            total_q += len(truth)
            print(f"  判定命中 {hit}/{len(truth)}")
            for r in rows:
                print(f"    题{r[0]} 学生答[{r[1]}] -> 判定={r[2]} 期望={r[3]} {r[4]} 置信度={r[5]} 模型读到[{r[6]}]")
            report_cases.append({"image": image_path.name, "success": True, "hit": hit,
                                 "total": len(truth), "elapsed_s": elapsed_s,
                                 "rows": [list(map(str, r)) for r in rows],
                                 "summary": result.get("summary")})
        acc = total_hit / total_q if total_q else 0.0
        print(f"  引擎汇总: {total_hit}/{total_q} = {acc:.1%}")
        report["engines"][name] = {"success": engine_ok, "hit": total_hit,
                                   "total": total_q, "accuracy": round(acc, 4),
                                   "cases": report_cases}

    out = args.out or str(Path(__file__).parent / "accuracy_report.json")
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = str(Path(out).with_suffix(".md"))
    Path(md_path).write_text(markdown_report(report), encoding="utf-8")
    print(f"报告已写入 {out} / {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
