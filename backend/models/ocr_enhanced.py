"""g7: OCR 增强开关（默认关）

设置页开启后，切题链路的页面文字行识别（题号锚点 + 说明段落检测）改用
DeepSeek 服务端 OCR 接口 `POST /v1/tools/ocr`（1.5 元/千页，识别失败不计费），
识别质量优于本地 RapidOCR。任何失败（未配 Key / 非 DeepSeek / 网络错误）
静默回退本地引擎，绝不阻塞批改。

服务端 OCR 只返回纯文本（无坐标），行位置用「文本行序 ↔ 墨迹行带」对齐
还原：行投影找出墨迹行带（与切题用同一投影），按顺序把文本行均分到行带，
得到每行的 top/bottom 像素坐标。行带数少于文本行数时按比例抽样映射。
"""

import io
from typing import Any, Dict, List, Optional

CONFIG_KEY = "ocr_enhanced"

# 仅 DeepSeek 官方端点支持 /v1/tools/ocr；其它 base_url 不做服务端 OCR
DEEPSEEK_HOST_MARK = "api.deepseek.com"


def _app_config_model():
    from ..database import AppConfig, SessionLocal

    return AppConfig, SessionLocal


def get_ocr_enhanced_config() -> Dict[str, Any]:
    AppConfig, SessionLocal = _app_config_model()
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == CONFIG_KEY).first()
        val = row.value if row else "false"
    finally:
        db.close()
    return {"enabled": str(val).lower() == "true"}


def save_ocr_enhanced(enabled: bool) -> None:
    AppConfig, SessionLocal = _app_config_model()
    db = SessionLocal()
    try:
        row = db.query(AppConfig).filter(AppConfig.key == CONFIG_KEY).first()
        value = "true" if enabled else "false"
        if row:
            row.value = value
        else:
            db.add(AppConfig(key=CONFIG_KEY, value=value))
        db.commit()
    finally:
        db.close()


def is_enabled() -> bool:
    return get_ocr_enhanced_config()["enabled"]


def server_ocr_lines(image_path: str) -> Optional[List[str]]:
    """调用 DeepSeek /v1/tools/ocr 识别整页文本 → 文本行列表（按出现顺序）。

    返回 None 表示「不可用/失败」（调用方回退本地 OCR）。识别成功但整页
    无文字返回 []（合法结果，不回退）。
    """
    if not is_enabled():
        return None
    try:
        from ..models.grading_engine import get_llm_config

        cfg = get_llm_config()
        base_url = (cfg.get("base_url") or "").strip().rstrip("/")
        api_key = (cfg.get("api_key") or "").strip()
        if not api_key or DEEPSEEK_HOST_MARK not in base_url:
            return None
        url = f"{base_url}/v1/tools/ocr"
        with open(image_path, "rb") as f:
            data = f.read()
        import httpx

        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (image_path.rsplit("/", 1)[-1] or "page.png", data)},
            data={"purpose": "text_recognition"},
            timeout=180.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        pages = payload.get("pages") or []
        if not pages:
            return None
        text = "\n".join(str(p.get("text") or "") for p in pages)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines
    except Exception:
        return None


def align_text_to_ink(image_path: str, texts: List[str]) -> Optional[List[Dict]]:
    """把纯文本行按顺序映射到墨迹行带，还原每行像素坐标。

    行带用切题同款行投影（_row_ink_fractions）找连续墨迹段；行带数 >= 文本
    行数时按顺序一一对应（多余的行带丢弃），不足时按比例抽样映射。
    页面找不到 >=2 个行带（空白/纯图页）返回 None → 调用方回退本地 OCR。
    """
    if not texts:
        return []
    try:
        from PIL import Image, ImageOps

        from .question_splitter import _row_ink_fractions, INK_FRAC_MIN

        img = ImageOps.exif_transpose(Image.open(image_path))
        gray = img.convert("L")
        w, h = gray.size
        fracs = _row_ink_fractions(gray)
        # 连续墨迹行（frac >= INK_FRAC_MIN）合并为行带
        bands, start = [], None
        for i, f in enumerate(fracs):
            if f >= INK_FRAC_MIN:
                if start is None:
                    start = i
            else:
                if start is not None:
                    bands.append((start, i))
                    start = None
        if start is not None:
            bands.append((start, len(fracs)))
        if len(bands) < 2:
            return None
        scale = h / len(fracs)
        n = len(texts)
        lines = []
        for k, text in enumerate(texts):
            # 文本行 k → 行带下标（行带多于文本行时均匀抽样；反之比例映射）
            bi = round(k * (len(bands) - 1) / max(1, n - 1)) if n > 1 else len(bands) // 2
            bi = min(max(bi, 0), len(bands) - 1)
            top, bottom = bands[bi]
            lines.append({
                "text": text,
                "top": round(top * scale),
                "bottom": round(bottom * scale),
            })
        return lines
    except Exception:
        return None
