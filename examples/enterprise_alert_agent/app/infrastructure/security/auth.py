

from enum import Enum
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from fastapi.security import OAuth2PasswordBearer
from datetime import datetime, timedelta
import jwt
from app.config.settings import settings

class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    AUDITOR = "auditor"
class TokenPayload(BaseModel):
    sub: str
    role: Role
    exp: int

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/admin/token")
def create_access_token(
        user_id: str, role: Role, expires_minutes: int | None = None
) -> str:
    exp_minutes = expires_minutes or 15
    expire_at = datetime.utcnow() + timedelta(minutes=exp_minutes)
    payload = {
        "sub": user_id,
        "role": role.value,
        "exp": expire_at.timestamp(),
    }
    token = jwt.encode(payload, settings.admin_jwt_secret, algorithm=settings.admin_jwt_algorithm)
    return token
def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenPayload:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.admin_jwt_secret, algorithms=[settings.admin_jwt_algorithm]
        )
        sub = payload.get("sub")
        role = payload.get("role")
        exp = payload.get("exp")
        if not sub or not role or not exp:
            raise credentials_exception
        token_data = TokenPayload(sub=sub, role=Role(role), exp=exp)
        return token_data
    except (jwt.PyJWTError, ValueError):
        raise credentials_exception
def require_roles(*roles: Role):
    def role_checker(token_data: TokenPayload = Depends(get_current_user)):
        if token_data.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return token_data
    return role_checker
