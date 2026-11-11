"""
红笔 · 教师单机自用版
主程序入口
"""

import socket
import sys
import threading
import webbrowser
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from backend.app import app
from backend.config import settings


def _lan_ip() -> str:
    """探测本机局域网IP（不真正发包，仅路由探测）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _open_browser_when_ready(url: str):
    """等服务就绪后自动打开浏览器（子线程轮询 /health，最多等15秒）"""
    import time
    from urllib.request import urlopen

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            urlopen(url + "/health", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    webbrowser.open(url)


if __name__ == "__main__":
    ip = _lan_ip()
    local_url = f"http://localhost:{settings.PORT}"
    lan_url = f"http://{ip}:{settings.PORT}"

    print("🚀 启动红笔系统...")
    print(f"💻 本机访问:  {local_url}")
    print(f"📱 手机访问:  {lan_url}/mobile  （手机需连同一 WiFi，浏览器打开此地址或扫设置页二维码）")
    print("🛑 关闭此窗口即退出系统")
    print()

    # 打包版/非调试模式：启动后自动打开浏览器进工作台
    from backend.config import FROZEN
    if FROZEN or not settings.DEBUG:
        threading.Thread(target=_open_browser_when_ready, args=(local_url,), daemon=True).start()

    # 启动FastAPI服务器（打包后禁用 reload）
    import uvicorn
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG and not FROZEN,
        log_level="info"
    )
