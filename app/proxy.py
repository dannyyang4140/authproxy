"""反向代理：认证 -> 数据集级授权 -> 改写 -> 转发 RAGFlow（支持 SSE 透传）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from . import policy
from .audit import insert_audit
from .config import Settings, get_settings
from .db import get_db
from .models import AccessToken
from .ratelimit import SlidingWindowRateLimiter
from .security import extract_access_token, hash_token
from .ragflow import RagflowClient

router = APIRouter()
rate_limiter = SlidingWindowRateLimiter()

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
# 这些请求头不透传：由网关重新生成或与上游凭证相关。
_DROP_REQUEST = _HOP_BY_HOP | {
    "host",
    "content-length",
    "accept-encoding",
    "authorization",
    "cookie",
    "x-proxy-token",
    "x-admin-token",
}
# 这些响应头不透传：分帧 / 压缩交由 ASGI 与客户端重新协商。
_DROP_RESPONSE = _HOP_BY_HOP | {"content-encoding", "content-length"}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _error(status: int, code: str, message: str, extra: dict | None = None) -> JSONResponse:
    payload: dict = {"error": code, "message": message}
    if extra:
        payload.update(extra)
    return JSONResponse(status_code=status, content=payload)


def _audit(db: Session, **kwargs) -> None:
    insert_audit(db, **kwargs)


def _filter_dataset_listing(payload: object, allowed: set[str]) -> object:
    """只保留白名单内的数据集，防止列举接口枚举到无权知识库。"""
    def keep(items: list) -> list:
        return [x for x in items if isinstance(x, dict) and str(x.get("id")) in allowed]

    if isinstance(payload, dict) and "data" in payload:
        data = payload["data"]
        if isinstance(data, list):
            payload["data"] = keep(data)
        elif isinstance(data, dict):
            for key in ("datasets", "docs", "items", "list"):
                if isinstance(data.get(key), list):
                    data[key] = keep(data[key])
        return payload
    if isinstance(payload, list):
        return keep(payload)
    return payload


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
    response_model=None,
)
async def reverse_proxy(
    request: Request,
    full_path: str,
    db: Session = Depends(get_db),
    x_proxy_token: str | None = Header(default=None, alias="X-Proxy-Token"),
    authorization: str | None = Header(default=None),
) -> JSONResponse | StreamingResponse:
    settings: Settings = get_settings()
    rag: RagflowClient = request.app.state.ragflow
    method = request.method
    path = request.url.path
    ip = _client_ip(request)

    # ---------- 1. 认证 ----------
    plain_token = extract_access_token(x_proxy_token, authorization)
    if not plain_token:
        _audit(db, token_id=None, token_name="-", client_ip=ip, method=method, path=path,
               action="", datasets=[], decision="deny", reason="缺少访问令牌", status_code=401)
        return _error(401, "unauthorized", "缺少 X-Proxy-Token 访问令牌")

    token = db.query(AccessToken).filter(AccessToken.token_hash == hash_token(plain_token)).first()
    if token is None:
        _audit(db, token_id=None, token_name="-", client_ip=ip, method=method, path=path,
               action="", datasets=[], decision="deny", reason="访问令牌无效", status_code=401)
        return _error(401, "unauthorized", "访问令牌无效")
    if not token.enabled:
        _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
               action="", datasets=[], decision="deny", reason="令牌已停用", status_code=403)
        return _error(403, "forbidden", "访问令牌已停用")
    if token.is_expired():
        _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
               action="", datasets=[], decision="deny", reason="令牌已过期", status_code=403)
        return _error(403, "forbidden", "访问令牌已过期")

    # ---------- 2. 限流 ----------
    limit = token.rate_limit_per_min or settings.default_rate_limit_per_min
    if not rate_limiter.allow(token.id, limit):
        _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
               action="", datasets=[], decision="deny", reason="触发限流", status_code=429)
        return _error(429, "rate_limited", f"超过每分钟 {limit} 次的调用限额")

    # ---------- 3. 读取并解析请求体 ----------
    raw = await request.body()
    content_type = request.headers.get("content-type", "")
    body_obj = None
    is_json = "application/json" in content_type.lower()
    if is_json and raw:
        try:
            body_obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body_obj = None
            is_json = False

    # ---------- 4. 反查 document / chat 的数据集归属 ----------
    allowed = token.datasets
    wildcard = "*" in allowed
    bound: list[str] = []
    unresolved = False
    if not wildcard:
        body_ds = policy.body_dataset_ids(body_obj)
        doc_ids = policy.body_document_ids(body_obj)
        # 仅当没有显式 dataset_ids 时，才需要按 document_id 反查归属。
        if doc_ids and not body_ds:
            for doc_id in doc_ids:
                owner = await rag.resolve_document_owner(doc_id, allowed)
                if owner:
                    bound.append(owner)
                else:
                    unresolved = True
        chat_id = policy.get_chat_id(path)
        if chat_id:
            chat_ds = await rag.get_chat_dataset_ids(chat_id)
            if chat_ds:
                bound.extend(chat_ds)
            else:
                unresolved = True

    # ---------- 5. 授权决策 ----------
    decision = policy.decide(
        method=method,
        path=path,
        body=body_obj,
        scope_level=token.scope_level,
        allowed_datasets=allowed,
        bound_datasets=bound,
        unresolved_resource=unresolved,
        strict=settings.strict_resource_binding,
    )
    if not decision.allow:
        _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
               action=decision.action, datasets=decision.required, decision="deny",
               reason=decision.reason, status_code=decision.status_code)
        return _error(decision.status_code, "forbidden", decision.reason,
                      {"action": decision.action, "datasets": decision.required})

    # ---------- 6. 请求体 dataset_ids 白名单交集改写（纵深防御）----------
    if is_json and body_obj is not None and decision.final_dataset_ids is not None:
        body_obj["dataset_ids"] = decision.final_dataset_ids
        raw = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")

    # ---------- 7. 构造上游请求 ----------
    url = path
    if request.url.query:
        url += "?" + request.url.query

    upstream_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST
    }
    # 网关自身凭证（RAGFlow Key）由共享 httpx client 的默认头注入；补充转发链信息。
    upstream_headers["X-Forwarded-Proto"] = request.url.scheme
    if ip:
        upstream_headers["X-Forwarded-For"] = ip

    content: bytes | None = raw if method.upper() not in ("GET", "HEAD", "OPTIONS") else None

    def _touch() -> None:
        token.last_used_at = datetime.now(timezone.utc)

    # ---------- 8. 列举知识库：缓冲并过滤无权数据集 ----------
    if decision.is_dataset_listing and not wildcard:
        try:
            resp = await rag.client.request(method, url, headers=upstream_headers, content=content)
        except httpx.HTTPError as exc:
            _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
                   action=decision.action, datasets=[], decision="allow",
                   reason=f"上游错误: {exc}", status_code=502)
            return _error(502, "bad_gateway", "无法连接 RAGFlow 上游")
        _touch()
        _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
               action=decision.action, datasets=[], decision="allow", reason="", status_code=resp.status_code)
        if resp.status_code < 400 and "application/json" in resp.headers.get("content-type", ""):
            try:
                payload = resp.json()
                payload = _filter_dataset_listing(payload, set(allowed))
                return JSONResponse(status_code=resp.status_code, content=payload)
            except ValueError:
                pass
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE}
        return Response(content=resp.content, status_code=resp.status_code, headers=out_headers)

    # ---------- 9. 其余请求：流式透传（含 SSE / 二进制）----------
    try:
        upstream_req = rag.client.build_request(method, url, headers=upstream_headers, content=content)
        upstream_resp = await rag.client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
               action=decision.action, datasets=decision.required, decision="allow",
               reason=f"上游错误: {exc}", status_code=502)
        return _error(502, "bad_gateway", "无法连接 RAGFlow 上游")

    _touch()
    _audit(db, token_id=token.id, token_name=token.name, client_ip=ip, method=method, path=path,
           action=decision.action, datasets=decision.required, decision="allow",
           reason="", status_code=upstream_resp.status_code)

    out_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in _DROP_RESPONSE}

    async def _aiter():
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(_aiter(), status_code=upstream_resp.status_code, headers=out_headers)
