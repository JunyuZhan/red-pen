"""
API认证路由
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import JWTError, jwt
from pydantic import BaseModel
from ..database import get_db
from ..config import settings
import bcrypt

router = APIRouter()

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str

class UserCreate(BaseModel):
    username: str
    password: str
    email: str
    name: str

class User(BaseModel):
    id: int
    username: str
    email: str
    name: str
    is_active: bool

    class Config:
        from_attributes = True

# 简化版认证，实际使用时需要实现完整的用户管理
@router.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """获取访问令牌"""
    # 这里简化处理，实际应该查询数据库验证用户
    if form_data.username != "teacher" or form_data.password != "password":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username}, 
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

def create_access_token(data: dict, expires_delta: timedelta = None):
    """创建访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(OAuth2PasswordBearer(tokenUrl="auth/token"))):
    """获取当前用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    
    # 这里简化处理，实际应该查询数据库获取用户信息
    if token_data.username != "teacher":
        raise credentials_exception
    
    return User(
        id=1,
        username="teacher",
        email="teacher@example.com",
        name="教师用户",
        is_active=True
    )


# ============ 管理员 PIN 登录（保护设置页与管理接口） ============

ADMIN_COOKIE_NAME = "admin_session"
ADMIN_TOKEN_EXPIRE_HOURS = 12


class PinLoginIn(BaseModel):
    pin: str


def _make_admin_token() -> str:
    expire = datetime.utcnow() + timedelta(hours=ADMIN_TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": "admin", "exp": expire},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def require_admin(request: Request) -> None:
    """FastAPI 依赖：校验 HttpOnly cookie 中的管理员会话，无效则 401。"""
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录，请先输入管理员 PIN")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("sub") != "admin":
            raise HTTPException(status_code=401, detail="登录状态无效")
    except JWTError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新输入管理员 PIN")


@router.post("/pin-login")
async def pin_login(data: PinLoginIn, response: Response):
    """校验管理员 PIN，成功后设置 HttpOnly 会话 cookie"""
    if data.pin != settings.ADMIN_PIN:
        raise HTTPException(status_code=401, detail="PIN 错误")
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        _make_admin_token(),
        httponly=True,
        samesite="lax",
        max_age=ADMIN_TOKEN_EXPIRE_HOURS * 3600,
    )
    return {"success": True, "message": "登录成功"}


@router.post("/pin-logout")
async def pin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"success": True, "message": "已退出登录"}


@router.get("/pin-status")
async def pin_status(request: Request):
    """前端用于判断是否已解锁管理功能"""
    try:
        require_admin(request)
        return {"success": True, "unlocked": True}
    except HTTPException:
        return {"success": True, "unlocked": False}