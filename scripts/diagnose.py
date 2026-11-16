#!/usr/bin/env python3
"""一键自检脚本（Windows 部署排障用）。

用途：老师电脑上出问题时，一条命令检查部署环境是否健康，
输出逐项 PASS/WARN/FAIL 与修复建议，避免逐个排查。

用法：
    python scripts/diagnose.py

检查项（全部只读或临时文件，不改动任何配置）：
    1. Python 版本 >= 3.10
    2. 依赖可导入（fastapi / uvicorn / PIL / aiosqlite）
    3. 数据目录可写（创建/删除临时文件）
    4. 数据库文件可打开（SQLite integrity 检查）
    5. 磁盘剩余空间 >= 500MB
    6. 端口未被占用（服务未启动时）
    7. 中文字体可用（Windows/Linux 常见路径， annotator 标注需要）
    8. 模型 API Key 是否已配置（只查存在性，不显示内容）
"""
import socket
import sqlite3
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MIN_PY = (3, 10)
MIN_DISK_MB = 500

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",      # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",    # 黑体
    "C:/Windows/Fonts/simsun.ttc",    # 宋体
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def check_python():
    ok = sys.version_info >= MIN_PY
    msg = "Python %d.%d.%d" % sys.version_info[:3]
    hint = "" if ok else "需要 Python >= 3.10，请升级后重装"
    return ("PASS" if ok else "FAIL"), msg, hint


def check_imports():
    missing = []
    mods = ["fastapi", "uvicorn", "PIL", "httpx", "pydantic_settings", "sqlalchemy", "cryptography"]
    missing = []
    for mod in mods:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return "FAIL", "缺少依赖: %s" % ", ".join(missing), "在项目目录执行 pip install -r requirements.txt"
    return "PASS", "全部依赖可导入 (%d 项)" % len(mods), ""


def _data_dirs():
    from backend.config import settings
    return settings


def check_data_dir_writable():
    try:
        settings = _data_dirs()
        d = Path(settings.DATA_DIR)
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".diagnose_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return "PASS", "数据目录可写: %s" % d, ""
    except Exception as e:
        return "FAIL", "数据目录不可写: %s" % e, "检查目录权限，或用 RED_PEN_DATA_DIR 指定可写目录"


def check_database():
    try:
        settings = _data_dirs()
        db_path = Path(settings.DATABASE_URL.replace("sqlite:///", ""))
        if not db_path.exists():
            return "WARN", "数据库尚未创建（首次启动会自动生成）", ""
        conn = sqlite3.connect(str(db_path))
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = conn.execute(
            "select count(*) from sqlite_master where type='table'").fetchone()[0]
        conn.close()
        if not ok:
            return "FAIL", "数据库完整性检查未通过", "从 data/backups 恢复最近备份，或联系支持"
        return "PASS", "数据库正常 (%d 张表)" % tables, ""
    except Exception as e:
        return "FAIL", "数据库无法打开: %s" % e, "确认没有其他红笔进程占用数据库文件"


def check_disk():
    try:
        settings = _data_dirs()
        total, used, free = shutil.disk_usage(str(Path(settings.DATA_DIR)))
        free_mb = free // (1024 * 1024)
        if free_mb < MIN_DISK_MB:
            return "WARN", "磁盘剩余 %d MB（建议 >= %dMB）" % (free_mb, MIN_DISK_MB), \
                "清理磁盘或更换数据目录；磁盘写满会导致批改失败"
        return "PASS", "磁盘剩余 %d MB" % free_mb, ""
    except Exception as e:
        return "WARN", "磁盘检查失败: %s" % e, ""


def check_port():
    try:
        settings = _data_dirs()
        port = int(settings.PORT)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        occupied = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if occupied:
            return "WARN", "端口 %d 已被占用" % port, \
                "可能红笔已在运行（浏览器访问 http://localhost:%d 即可）；否则请换 PORT" % port
        return "PASS", "端口 %d 空闲" % port, ""
    except Exception as e:
        return "WARN", "端口检查失败: %s" % e, ""


def check_fonts():
    found = [p for p in FONT_CANDIDATES if Path(p).exists()]
    if found:
        from PIL import ImageFont
        ImageFont.truetype(found[0], 24)
        return "PASS", "找到中文字体: %s" % found[0], ""
    return "WARN", "未找到常见中文字体", "批改标注可能显示方块；Windows 一般自带微软雅黑，若缺失请安装"


def check_api_key():
    """只检查 Key 是否配置（不显示内容，也不联网验证）。"""
    try:
        settings = _data_dirs()
        db_path = Path(settings.DATABASE_URL.replace("sqlite:///", ""))
        if not db_path.exists():
            return "WARN", "数据库未创建，无法检查 Key", ""
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "select value from app_config where key='llm_api_key'").fetchone()
        model = conn.execute(
            "select value from app_config where key='llm_model'").fetchone()
        conn.close()
        has_key = bool(row and row[0])
        model = (model[0] if model and model[0] else "")
        if has_key:
            return "PASS", "模型 API Key 已配置（模型: %s）" % model, \
                "真实可用性请在设置页点「测试连接」"
        return "WARN", "模型 API Key 未配置", \
            "批改需要 Key：设置页 → 模型服务商 → 填入 API Key 后点「测试连接」"
    except Exception as e:
        return "WARN", "Key 检查失败: %s" % e, ""


CHECKS = [
    ("Python 版本", check_python),
    ("依赖完整性", check_imports),
    ("数据目录可写", check_data_dir_writable),
    ("数据库", check_database),
    ("磁盘空间", check_disk),
    ("端口", check_port),
    ("中文字体", check_fonts),
    ("模型 API Key", check_api_key),
]


def run_all():
    print("=" * 46)
    print("红笔 · 一键自检")
    print("=" * 46)
    fail = warn = 0
    for name, fn in CHECKS:
        try:
            status, msg, hint = fn()
        except Exception as e:
            status, msg, hint = "FAIL", "检查异常: %s" % e, ""
        mark = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}.get(status, "❌")
        print("%s %s：%s" % (mark, name, msg))
        if hint:
            print("    ↳ %s" % hint)
        if status == "FAIL":
            fail += 1
        elif status == "WARN":
            warn += 1
    print("-" * 46)
    if fail:
        print("结论：%d 项失败，请按上方提示修复后重跑" % fail)
        return 1
    if warn:
        print("结论：环境可用，%d 项提醒见上" % warn)
        return 0
    print("结论：环境全部正常 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
