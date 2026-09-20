"""令牌生成、哈希与管理员校验。"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException, status

from .config import get_settings

TOKEN_PREFIX = "rfp_"


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def generate_access_token() -> tuple[str, str, str]:
    """返回 (明文令牌, 明文前缀, sha256 哈希)。"""
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    # rfp_ 加前 6 位随机字符用于后台辨认。
    prefix = plain[: len(TOKEN_PREFIX) + 6]
    return plain, prefix, hash_token(plain)


def extract_access_token(
    x_proxy_token: str | None,
    authorization: str | None,
) -> str | None:
    """优先取 X-Proxy-Token；也接受 Authorization: Bearer rfp_...（与上游 RAGFlow Key 区分）。"""
    if x_proxy_token:
        return x_proxy_token.strip()
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].startswith(TOKEN_PREFIX):
            return parts[1].strip()
    return None


async def require_admin(
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """管理接口鉴权：X-Admin-Token，或 Bearer 且不是访问令牌前缀。"""
    settings = get_settings()
    candidate = x_admin_token
    if not candidate and authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            candidate = parts[1]
    if not candidate or not hmac.compare_digest(candidate, settings.admin_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的管理员凭证")
