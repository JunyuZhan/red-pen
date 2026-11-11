"""图像质量门禁测试：模糊/太暗/过曝/过小/正常 各场景 + 上传接口行为。"""
import io
import os
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFilter, ImageFont

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.models.quality_checker import check_image  # noqa: E402


def make_image(size=(1600, 1200), text=True, blur=False, brightness=None):
    # 灰色纸面更接近真实作业照片（纯白背景会误触过曝阈值）
    img = Image.new("RGB", size, "#e0e0e0")
    if text:
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 72)
        except OSError:
            font = None
        if font:
            for i, line in enumerate(["1+1=2  3+4=7", "5+6=11 8+9=17", "12-5=7  20-8=12"]):
                d.text((120, 200 + i * 280), line, fill="black", font=font)
        else:
            # 无字体环境兜底：画黑色笔迹条模拟手写（保证清晰图方差足够高）
            for i in range(3):
                d.rectangle([120, 220 + i * 280, 1400, 320 + i * 280], fill="black")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(8))
    if brightness is not None:
        img = img.point(lambda p: int(p * brightness))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestQualityChecker(unittest.TestCase):
    def test_good_image_passes(self):
        self.assertEqual(check_image(make_image()), [])

    def test_blur_detected(self):
        flags = check_image(make_image(blur=True))
        self.assertTrue(any("模糊" in f for f in flags), flags)

    def test_dark_detected(self):
        flags = check_image(make_image(brightness=0.1))
        self.assertTrue(any("太暗" in f for f in flags), flags)

    def test_overexposed_detected(self):
        flags = check_image(make_image(brightness=1.3))
        self.assertTrue(any("过曝" in f for f in flags), flags)

    def test_too_small_detected(self):
        flags = check_image(make_image(size=(300, 200)))
        self.assertTrue(any("过小" in f for f in flags), flags)

    def test_corrupt_detected(self):
        flags = check_image(b"not-an-image")
        self.assertTrue(any("损坏" in f or "无法解码" in f for f in flags), flags)


class TestUploadQualityGate(unittest.TestCase):
    """上传接口：422 退回 + force_quality 放行。"""

    @classmethod
    def setUpClass(cls):
        from backend.database import init_db, SessionLocal, Class
        init_db()
        db = SessionLocal()
        cls.db = db
        if not db.query(Class).first():
            db.add(Class(name="测试班", teacher_id=1, stage_id=1, subject_id=1))
            db.commit()
        cls.class_id = db.query(Class).first().id

    def test_gate_and_force(self):
        from fastapi.testclient import TestClient
        from backend.app import app
        client = TestClient(app)

        blurred = make_image(blur=True, brightness=0.05)
        r = client.post("/api/upload/upload-mobile", data={
            "assignment_name": "质量门禁测试", "stage": "小学", "subject": "数学",
            "teacher_id": "1",
        }, files={"file": ("bad.jpg", blurred, "image/jpeg")})
        self.assertEqual(r.status_code, 422, r.text)
        flags = r.json()["quality_flags"]
        self.assertTrue(any("模糊" in f or "太暗" in f for f in flags))

        # force_quality=1 放行
        r2 = client.post("/api/upload/upload-mobile", data={
            "assignment_name": "质量门禁测试", "stage": "小学", "subject": "数学",
            "teacher_id": "1", "force_quality": "1",
        }, files={"file": ("bad.jpg", blurred, "image/jpeg")})
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertTrue(r2.json()["quality_flags"])

        # 正常图无 flag
        r3 = client.post("/api/upload/upload-mobile", data={
            "assignment_name": "质量门禁测试", "stage": "小学", "subject": "数学",
            "teacher_id": "1",
        }, files={"file": ("good.jpg", make_image(), "image/jpeg")})
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertEqual(r3.json()["quality_flags"], [])


if __name__ == "__main__":
    unittest.main()
