from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
)

Username = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=32)
]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]


class UsernameRequest(BaseModel):
    username: Username

    @field_validator("username")
    @classmethod
    def validate_normalized_length(cls, username: str) -> str:
        if len(username.casefold()) > 32:
            raise ValueError("用户名标准化后不能超过 32 个字符")
        return username


class RegisterRequest(UsernameRequest):
    password: Password


class LoginRequest(UsernameRequest):
    password: Password


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: UUID
    username: str

    model_config = ConfigDict(from_attributes=True)


class TokenPair(BaseModel):
    """标准的 OAuth2 Token 响应格式。"""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class AuthResponse(TokenPair):
    """当用户登录成功或注册成功时，服务器会返回这个模型。它不仅包含了新生成的 Access/Refresh Token，还附带了当前登录用户的基本信息，方便前端展示。"""

    user: UserResponse
