"""
网络信息路由：展示本机局域网访问地址 + 二维码（PRD 第三节）
"""

import base64
import io
import socket

import qrcode
from fastapi import APIRouter

from ..config import settings

router = APIRouter()


def get_lan_ip() -> str:
    """探测本机局域网 IP（不实际发包，仅 UDP connect 确定路由出口）。失败回退 127.0.0.1。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def qr_data_url(text: str) -> str:
    """生成二维码 PNG 的 data URL（纯内存，不落盘）。"""
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


INTERNET_PROBES = ["https://www.baidu.com", "https://www.qq.com"]


@router.get("/check-internet")
async def check_internet():
    """PRD 3.3：安装/连接引导里的"测试外网"检查——电脑需能上外网调云端 API。

    依次探测常见站点（带超时），任一可达即视为外网正常；全部失败给出人话引导。
    """
    import time
    import urllib.request

    last_err = ""
    for url in INTERNET_PROBES:
        start = time.time()
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "RedPen/1.0"})
            urllib.request.urlopen(req, timeout=5)
            return {
                "success": True,
                "reachable": True,
                "probe": url,
                "latency_ms": int((time.time() - start) * 1000),
                "message": "外网连接正常，可以调用云端 AI 批改。",
            }
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    return {
        "success": True,
        "reachable": False,
        "probe": None,
        "latency_ms": None,
        "message": "连不上外网。请检查电脑网络，或确认防火墙没有拦截红笔；外网不通时无法调用云端 AI。",
        "error": last_err,
    }


@router.get("/info")
async def network_info():
    """返回局域网访问地址与二维码，供设置页"手机连接引导"展示。"""
    ip = get_lan_ip()
    port = settings.PORT
    url = f"http://{ip}:{port}"
    return {
        "success": True,
        "ip": ip,
        "port": port,
        "url": url,
        "qr": qr_data_url(url),
        "is_loopback": ip == "127.0.0.1",
    }
