"""使用Argon2存储密码，以固定算法签发和校验身份令牌。"""
from datetime import datetime, timedelta, timezone
import secrets
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from .settings import Settings

_hasher = PasswordHasher()
_dummy_hash = _hasher.hash(secrets.token_urlsafe(32))

def hash_password(password: str) -> str:
    """生成带独立盐的Argon2密码哈希。"""
    return _hasher.hash(password)

def verify_password(password: str, stored_hash: str | None) -> bool:
    """未知账号仍进行哈希校验，并将不匹配统一视作失败。"""
    try:
        matched = _hasher.verify(stored_hash or _dummy_hash, password)
        return bool(stored_hash) and matched
    except (VerificationError, InvalidHashError):
        return False

def issue_token(user_id: int, token_version: int, settings: Settings) -> str:
    """签发仅含身份及安全版本的限时令牌。"""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "ver": token_version, "iat": now,
                       "exp": now + timedelta(minutes=settings.token_minutes),
                       "iss": "knowledge-base-platform", "aud": "knowledge-base-web"},
                      settings.jwt_secret, algorithm="HS256")

def decode_token(token: str, settings: Settings) -> dict:
    """限定签名算法、签发者、受众与必须存在的过期字段。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"],
                      issuer="knowledge-base-platform", audience="knowledge-base-web",
                      options={"require": ["sub", "ver", "iat", "exp"]})
