"""
运行日志：滚动文件 + 控制台双输出。
日志位置：<数据目录>/logs/redpen.log（exe 模式为 exe 同目录下 data/logs/）。
"""
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import APP_DIR

_logger = None


def _logs_dir() -> Path:
    d = APP_DIR / "data" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_logger() -> logging.Logger:
    global _logger
    if _logger:
        return _logger
    logger = logging.getLogger("redpen")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = RotatingFileHandler(_logs_dir() / "redpen.log",
                             maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.propagate = False
    _logger = logger
    return logger


def log_path() -> Path:
    return _logs_dir() / "redpen.log"


def recent_lines(n: int = 100) -> list[str]:
    p = log_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def open_logs_folder() -> str:
    """跨平台打开日志文件夹（Windows 用 explorer）。"""
    d = str(_logs_dir())
    try:
        if sys.platform == "win32":
            os.startfile(d)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])
        return d
    except Exception as e:  # noqa: BLE001
        return f"打开失败: {e}"
