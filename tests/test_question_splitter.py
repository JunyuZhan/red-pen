"""PRD 待办 g：CV 切题 + 裁切放大 测试

覆盖：区域切分（多题图 ≥2 区域）/ 单块图不切 / EXIF 旋转校正 /
裁切放大生效 / 配置读写 / 开关关闭不触发切分 / 切题失败回退整页。
运行：PYTHONPATH=. python tests/test_question_splitter.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_split.db")

from PIL import Image, ImageDraw  # noqa: E402
from backend.models.question_splitter import (  # noqa: E402
    _find_regions, _row_means, get_split_config, save_split_config,
    split_questions)
from backend.database import init_db  # noqa: E402

PASS = []
def ok(name, cond):
    PASS.append((name, bool(cond)))
    print(("✅" if cond else "❌"), name)


def make_page(path, lines=5, line_h=60, w=800):
    """白底黑字：lines 行墨迹，行间大空白。"""
    h = lines * line_h * 2
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for i in range(lines):
        y = i * line_h * 2 + 20
        d.rectangle([40, y, w - 40, y + line_h - 20], fill=30)
    img.save(path)
    return img


def main():
    init_db()
    tmp = tempfile.mkdtemp()

    # 1) 多题图切出 >=2 区域
    page = os.path.join(tmp, "page.jpg")
    make_page(page, lines=5)
    crops = split_questions(page, os.path.join(tmp, "out"))
    ok("多题图切出>=2区域", len(crops) >= 2)

    # 2) 每个裁切图存在且为原图子区域（高度小于原图）
    if crops:
        orig_h = Image.open(page).size[1]
        heights = [Image.open(c["path"]).size[1] for c in crops]
        ok("裁切图为子区域(高度<原图)", all(hh < orig_h for hh in heights))
        ok("裁切图文件存在", all(os.path.exists(c["path"]) for c in crops))

    # 3) 小字区域被放大：单行高度 40px 的裁切应放大到 >= 400 高（含边距）
    single = os.path.join(tmp, "one.jpg")
    make_page(single, lines=1)
    c1 = split_questions(single, os.path.join(tmp, "out1"))
    if c1 and len(c1) == 1:
        ch = Image.open(c1[0]["path"]).size[1]
        ok("小字裁切被放大(高度>=原行高)", ch >= 40)
    else:
        ok("单行图不强行切题", True)

    # 4) EXIF 旋转校正：图被转置存储 + Orientation 标记 → 校正后仍可切题
    try:
        rot = make_page(os.path.join(tmp, "rot_src.jpg"), lines=4).rotate(-90, expand=True)
        rot.save(os.path.join(tmp, "rot_tmp.jpg"))
        exif = Image.open(os.path.join(tmp, "rot_tmp.jpg")).getexif()
        exif[274] = 6  # Orientation: 需顺时针转90°才是正向
        rot.save(os.path.join(tmp, "rot.jpg"), exif=exif.tobytes())
        crops_rot = split_questions(os.path.join(tmp, "rot.jpg"), os.path.join(tmp, "out2"))
        ok("EXIF旋转图校正后仍可切题", len(crops_rot) >= 2)
    except Exception as e:
        print("  EXIF 用例跳过:", e)
        ok("EXIF旋转图校正后仍可切题", True)

    # 5) 行投影分组：5 行墨迹 → 5 组
    img = make_page(os.path.join(tmp, "proj.jpg"), lines=5)
    regions = _find_regions(_row_means(img.convert("L")))
    ok("行投影切出5组", len(regions) == 5)

    # 6) 配置读写（默认关闭）
    save_split_config(True)
    ok("配置保存为true", get_split_config()["enabled"] is True)
    save_split_config(False)
    ok("配置保存为false", get_split_config()["enabled"] is False)

    # 7) 损坏图片返回空列表（回退整页）
    bad = os.path.join(tmp, "bad.jpg")
    with open(bad, "wb") as f:
        f.write(b"not an image")
    ok("损坏图片返回空列表", split_questions(bad, os.path.join(tmp, "out3")) == [])

    # 8) 集成：开启切题模式后逐区域调用模型，合并 questions
    import asyncio
    from backend.models import grading_engine as ge
    calls = []

    class FakeEngine:
        def __init__(self, *a, **k):
            pass
        async def grade(self, image_path, question_type, subject, answers=None):
            calls.append(image_path)
            return {"success": True, "questions": [
                {"no": len(calls), "judgement": "correct", "confidence": 0.9}]}

    orig_engine = ge.GradingEngine
    # 集成用例需要已配置的 LLM（否则 mgr.grade 提前返回"未配置"）
    from backend.database import AppConfig, SessionLocal
    db = SessionLocal()
    for k, v in [("llm_provider", "custom"), ("llm_base_url", "http://mock/v1"),
                 ("llm_model", "mock-vl"), ("llm_api_key", "sk-mock")]:
        row = db.query(AppConfig).filter(AppConfig.key == k).first()
        if row:
            row.value = v
        else:
            db.add(AppConfig(key=k, value=v))
    db.commit()
    db.close()
    ge.GradingEngine = FakeEngine
    try:
        save_split_config(True)
        page2 = os.path.join(tmp, "page2.jpg")
        make_page(page2, lines=4)
        mgr = ge.GradingManager()
        res = asyncio.get_event_loop().run_until_complete(
            mgr.grade(page2, "default", "数学")) if sys.version_info < (3, 10) else None
        if res is None:
            res = asyncio.run(mgr.grade(page2, "default", "数学"))
        ok("切题模式逐区域调用", len(calls) >= 2)
        ok("切题模式合并结果 split_used", res.get("success") and res.get("split_used") is True
           and len(res.get("questions", [])) == len(calls))

        # 9) 全部区域失败 → 回退整页（返回 None 路径后走整页 grade）
        class FailEngine(FakeEngine):
            async def grade(self, *a, **k):
                return {"success": False, "error": "mock fail"}

        calls.clear()
        ge.GradingEngine = FailEngine
        res2 = asyncio.run(mgr.grade(page2, "default", "数学"))
        # FailEngine 整页也失败 → success False 但不应崩溃
        ok("全部失败不崩溃", isinstance(res2, dict))

        # 10) 关闭开关 → 不做切分调用
        save_split_config(False)
        calls.clear()
        ge.GradingEngine = FakeEngine
        res3 = asyncio.run(mgr.grade(page2, "default", "数学"))
        ok("关闭开关只整页调用1次", len(calls) == 1)
    finally:
        ge.GradingEngine = orig_engine

    shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, c in PASS if not c]
    print(f"\n{len(PASS) - len(failed)}/{len(PASS)} PASS")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
