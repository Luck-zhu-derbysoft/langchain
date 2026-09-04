import hmac
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated, cast

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from app.config.settings import settings


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    AUDITOR = "auditor"


class TokenPayload(BaseModel):
    sub: str
    role: Role
    exp: int
    tenant_id: str  # 签发时写入，业务侧强制使用


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/admin/token", auto_error=False)


def verify_api_key(user_id: str, api_key: str) -> bool:
    """校验管理端签发 token 的凭证（API Key 方式）。

    使用 hmac.compare_digest 常量时间比较，防止时序攻击。
    """
    expected = settings.admin_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin API key is not configured.",
        )
    return hmac.compare_digest(api_key, expected)


def create_access_token(
    user_id: str, role: Role, tenant_id: str, expires_minutes: int | None = None
) -> str:
    exp_minutes = expires_minutes or 15
    expire_at = datetime.now(tz=UTC) + timedelta(minutes=exp_minutes)
    payload = {
        "sub": user_id,
        "role": role.value,
        "exp": expire_at.timestamp(),
        "tenant_id": tenant_id,
    }
    token = jwt.encode(payload, settings.admin_jwt_secret, algorithm=settings.admin_jwt_algorithm)
    return token


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> TokenPayload:
    if token is None and settings.app_env == "test":
        return TokenPayload(
            sub="test-user",
            role=Role.ADMIN,
            exp=int(datetime.now(tz=UTC).timestamp()) + 3600,
            tenant_id="test-tenant",
        )
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        if token is None:
            raise credentials_exception
        payload = jwt.decode(
            token, settings.admin_jwt_secret, algorithms=[settings.admin_jwt_algorithm]
        )
        sub = payload.get("sub")
        role = payload.get("role")
        exp = payload.get("exp")
        tenant_id = cast(str, payload.get("tenant_id"))
        if not sub or not role or not exp:
            raise credentials_exception
        token_data = TokenPayload(sub=sub, role=Role(role), exp=exp, tenant_id=tenant_id)
        return token_data
    except (jwt.PyJWTError, ValueError):
        raise credentials_exception


def require_auth(token_data: Annotated[TokenPayload, Depends(get_current_user)]) -> TokenPayload:
    """全局认证依赖：挂到 /chat、/ingest 等端点，强制校验 JWT。"""
    return token_data


def require_roles(*roles: Role):
    def role_checker(
        token_data: Annotated[TokenPayload, Depends(get_current_user)],
    ) -> TokenPayload:
        if token_data.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return token_data

    return role_checker
