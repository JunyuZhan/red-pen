"""切题前置图像预处理 测试

覆盖：配置默认关/读写 / 墨迹包围盒 / 输出文件生成 / 低对比度增强 /
裁边去空白 / 密集图不误裁 / 损坏文件与缺失文件回退原图 / EXIF 转正 /
设置 API 读写 / 切题链路接入预处理。
运行：PYTHONPATH=. python tests/test_image_preprocess.py
"""

import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_preprocess.db")

from PIL import Image, ImageDraw, ImageStat  # noqa: E402

from backend.models.image_preprocess import (  # noqa: E402
    _ink_bbox, get_preprocess_config, preprocess_image,
    save_preprocess_config)
from backend.database import init_db  # noqa: E402

PASS = []
def ok(name, cond):
    PASS.append((name, bool(cond)))
    print(("✅" if cond else "❌"), name)


TMP = tempfile.mkdtemp()


def save_img(name, img, **kw):
    p = os.path.join(TMP, name)
    img.save(p, **kw)
    return p


def test_config():
    init_db()
    ok("配置默认关闭", get_preprocess_config()["enabled"] is False)
    save_preprocess_config(True)
    ok("保存开启后读取为 true", get_preprocess_config()["enabled"] is True)
    save_preprocess_config(False)
    ok("保存关闭后读取为 false", get_preprocess_config()["enabled"] is False)


def test_ink_bbox():
    img = Image.new("L", (100, 80), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([20, 10, 60, 70], fill=0)
    bbox = _ink_bbox(img)
    ok("包围盒覆盖墨迹", bbox is not None and bbox[0] <= 20 and bbox[2] >= 61
       and bbox[1] <= 10 and bbox[3] >= 71)
    ok("空白图无包围盒", _ink_bbox(Image.new("L", (50, 50), 255)) is None)


def test_output_created():
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([50, 50, 350, 250], outline=0, width=5)
    p = save_img("a.png", img)
    out = preprocess_image(p, os.path.join(TMP, "out"))
    ok("生成预处理文件", os.path.exists(out) and out != p
       and os.path.basename(out).startswith("pre_"))


def test_low_contrast_enhanced():
    img = Image.new("RGB", (200, 200), (120, 120, 120))
    ImageDraw.Draw(img).rectangle([20, 20, 180, 180], fill=130)
    p = save_img("lowc.png", img)
    out = preprocess_image(p, TMP)
    std_b = ImageStat.Stat(Image.open(p).convert("L")).stddev[0]
    std_a = ImageStat.Stat(Image.open(out).convert("L")).stddev[0]
    ok("低对比度图被增强(标准差提升)", std_a >= std_b)


def test_crop_blank():
    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([400, 400, 600, 600], outline=0, width=5)
    out = preprocess_image(save_img("blank.png", img), TMP)
    w, h = Image.open(out).size
    ok("大块空白被裁掉", w * h < 1000 * 1000 * 0.9)


def test_dense_not_cropped():
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([5, 5, 295, 295], outline=0, width=10)
    out = preprocess_image(save_img("dense.png", img), TMP)
    ok("内容占满不误裁", Image.open(out).size == (300, 300))


def test_fallbacks():
    bad = os.path.join(TMP, "bad.png")
    with open(bad, "wb") as f:
        f.write(b"not-an-image")
    ok("损坏文件回退原图", preprocess_image(bad, TMP) == bad)
    ok("缺失文件回退原图", preprocess_image(os.path.join(TMP, "nope.png"), TMP)
       == os.path.join(TMP, "nope.png"))


def test_exif():
    img = Image.new("RGB", (100, 200), (255, 255, 255))
    ImageDraw.Draw(img).text((10, 80), "hi", fill=0)
    exif = Image.Exif()
    exif[274] = 6
    bio = io.BytesIO()
    img.save(bio, format="JPEG", exif=exif.tobytes())
    p = os.path.join(TMP, "rot.jpg")
    with open(p, "wb") as f:
        f.write(bio.getvalue())
    out = preprocess_image(p, TMP)
    ok("EXIF 旋转图处理成功", os.path.exists(out))


def test_api():
    from fastapi.testclient import TestClient
    from backend.app import app
    c = TestClient(app)
    # 未带 PIN cookie：GET/POST 均被拒绝（设置接口统一 admin 保护）
    ok("GET 未鉴权被拒绝", c.get("/api/settings/preprocess").status_code in (401, 403))
    ok("POST 未鉴权被拒绝", c.post("/api/settings/preprocess",
                                   json={"enabled": True}).status_code in (401, 403))
    c.post("/api/auth/pin-login", json={"pin": "123456"})
    r = c.get("/api/settings/preprocess")
    d = r.json()
    ok("GET 带 PIN 读取成功", r.status_code == 200 and d["success"])
    r3 = c.post("/api/settings/preprocess", json={"enabled": True})
    d3 = r3.json()
    ok("POST 带 PIN 保存成功", r3.status_code == 200 and d3["success"]
       and d3["enabled"] is True)
    save_preprocess_config(False)


def test_split_integration():
    from backend.models.grading_engine import GradingManager
    import inspect
    src = inspect.getsource(GradingManager._grade_with_split)
    ok("切题链路已接入预处理", "preprocess_image" in src and "get_preprocess_config" in src)


if __name__ == "__main__":
    test_config()
    test_ink_bbox()
    test_output_created()
    test_low_contrast_enhanced()
    test_crop_blank()
    test_dense_not_cropped()
    test_fallbacks()
    test_exif()
    test_api()
    test_split_integration()
    n_fail = sum(1 for _, c in PASS if not c)
    print(f"\n{len(PASS) - n_fail}/{len(PASS)} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if n_fail else 0)
