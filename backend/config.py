"""
系统配置管理
"""

from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # 基础配置
    APP_NAME: str = "红笔 · 教师单机自用版"
    VERSION: str = "V0.4"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    
    # 数据库配置
    DATA_DIR: Path = Path("./data")
    DATABASE_URL: str = "sqlite:///./data/red_pen.db"
    
    # 上传配置
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