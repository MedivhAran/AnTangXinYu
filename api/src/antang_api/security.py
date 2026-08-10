from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from antang_api.settings import settings

ALGORITHM = "HS256"
ISSUER = "antang-api"
AUDIENCE = "antang-mobile"

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """将用户的明文密码转换为单向哈希值"""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """将用户输入的明文密码password与数据库中存储的哈希值进行比对，确认是否一致。"""
    return password_hasher.verify(password, password_hash)


def create_access_token(user_id: UUID, login_session_id: UUID) -> str:
    """创建一个JWT访问令牌"""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_minutes)

    # 符合 JWT 标准规范
    payload = {
        "sub": str(user_id),
        "sid": str(login_session_id),
        "type": "access",
        "jti": str(uuid4()),  # 访问token的唯一编号
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> tuple[UUID, UUID]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={
                "require": [
                    "sub",
                    "sid",
                    "type",
                    "jti",
                    "iss",
                    "aud",
                    "iat",
                    "exp",
                ]
            },
        )

        if payload["type"] != "access":
            raise InvalidTokenError("Invalid token type")

        return UUID(payload["sub"]), UUID(payload["sid"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as error:
        raise InvalidTokenError("Invalid access token") from error


def create_refresh_token() -> str:
    """生成一个 48 字节长度的、适合 URL 传输的、密码学安全的随机字符串作为refresh token"""
    return token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def access_token_expires_in() -> int:
    return settings.access_token_minutes * 60
