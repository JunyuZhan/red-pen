"""
红笔 · 教师单机自用版
主程序入口
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from backend.app import app
from backend.config import settings

if __name__ == "__main__":
    print(f"🚀 启动红笔系统...")
    print(f"📡 访问地址: http://{settings.HOST}:{settings.PORT}")
    print(f"📱 手机扫码访问: http://{settings.HOST}:{settings.PORT}/mobile")
    
    # 启动FastAPI服务器（打包后禁用 reload）
    import uvicorn
    from backend.config import FROZEN
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG and not FROZEN,
        log_level="info"
    )