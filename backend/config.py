"""
系统配置管理
"""

from pydantic_settings import BaseSettings
from pathlib import Path
import os
import sys

# PyInstaller 打包(frozen)时：资源解包目录 sys._MEIPASS，可写数据放 exe 同目录
FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(".")
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", "."))

class Settings(BaseSettings):
    # 基础配置
    APP_NAME: str = "红笔 · 教师单机自用版"
    VERSION: str = "V0.5.1"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    
    # 数据库配置（打包后数据目录放在 exe 同级，保证可写）
    if FROZEN:
        DATA_DIR: Path = APP_DIR / "data"
        DATABASE_URL: str = f"sqlite:///{(APP_DIR / 'data' / 'red_pen.db').as_posix()}"
    else:
        # RED_PEN_DATA_DIR 可覆盖数据目录（测试隔离用）
        DATA_DIR: Path = Path(os.environ.get("RED_PEN_DATA_DIR", "./data"))
        DATABASE_URL: str = f"sqlite:///{(DATA_DIR / 'red_pen.db').as_posix()}"

    # 上传配置
    if FROZEN:
        UPLOAD_DIR: Path = DATA_DIR / "uploads"
    else:
        UPLOAD_DIR: Path = Path("./data/uploads")
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    ALLOWED_EXTENSIONS: list = ["jpg", "jpeg", "png", "webp"]
    
    # AI模型配置
    LLM_TIMEOUT: int = 30
    LLM_MAX_RETRIES: int = 3
    LLM_BACKOFF_FACTOR: float = 1.0
    
    # 安全配置
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ADMIN_PIN: str = "123456"  # 管理员 PIN，可用环境变量 ADMIN_PIN 覆盖
    
    # 默认配置
    DEFAULT_STAGE: str = "小学"
    DEFAULT_SUBJECT: str = "数学"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

# 创建数据目录
def create_directories():
    directories = [
        settings.DATA_DIR,
        settings.UPLOAD_DIR,
        settings.DATA_DIR / "images",
        settings.DATA_DIR / "backups"
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

# 初始化配置
settings = Settings()
create_directories()

print(f"🔧 配置已加载: {settings.APP_NAME} {settings.VERSION}")
print(f"📡 服务器地址: {settings.HOST}:{settings.PORT}")
print(f"🗄️  数据目录: {settings.DATA_DIR}")