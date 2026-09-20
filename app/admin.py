"""管理 API：由管理员凭证保护，负责访问令牌的签发与审计查询。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AccessToken, AuditLog
from .schemas import (
    LEVEL_SCOPE,
    SCOPE_LEVEL,
    AuditOut,
    TokenCreated,
    TokenCreate,
    TokenOut,
    TokenUpdate,
)
from .security import generate_access_token

router = APIRouter(prefix="/admin", tags=["admin"])


def _dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _normalize_datasets(datasets: list[str]) -> list[str]:
    cleaned = [d.strip() for d in datasets if d and d.strip()]
    if "*" in cleaned:
        return ["*"]
    return list(dict.fromkeys(cleaned))


def _to_out(token: AccessToken) -> TokenOut:
    return TokenOut(
        id=token.id,
        name=token.name,
        token_prefix=token.token_prefix,
        datasets=token.datasets,
        scope=LEVEL_SCOPE.get(token.scope_level, "retrieve"),
        enabled=token.enabled,
        expires_at=_dt(token.expires_at),
        rate_limit_per_min=token.rate_limit_per_min,
        note=token.note,
        created_at=_dt(token.created_at) or datetime.now(timezone.utc),
        last_used_at=_dt(token.last_used_at),
    )


def _get_or_404(db: Session, token_id: str) -> AccessToken:
    token = db.get(AccessToken, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="令牌不存在")
    return token


@router.get("/tokens", response_model=list[TokenOut], summary="列出全部访问令牌")
def list_tokens(db: Session = Depends(get_db)) -> list[TokenOut]:
    rows = db.query(AccessToken).order_by(AccessToken.created_at.desc()).all()
    return [_to_out(t) for t in rows]


@router.post("/tokens", response_model=TokenCreated, status_code=201, summary="签发访问令牌")
def create_token(
    payload: TokenCreate,
    db: Session = Depends(get_db),
) -> TokenCreated:
    settings = get_settings()
    plain, prefix, token_hash = generate_access_token()
    expires_at = payload.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    token = AccessToken(
        name=payload.name.strip(),
        token_hash=token_hash,
        token_prefix=prefix,
        scope_level=SCOPE_LEVEL[payload.scope],
        enabled=True,
        expires_at=expires_at,
        rate_limit_per_min=payload.rate_limit_per_min or settings.default_rate_limit_per_min,
        note=payload.note or "",
    )
    token.datasets = _normalize_datasets(payload.datasets)
    db.add(token)
    db.commit()
    db.refresh(token)

    out = _to_out(token).model_dump()
    return TokenCreated(**out, token=plain)


@router.patch("/tokens/{token_id}", response_model=TokenOut, summary="更新令牌")
def update_token(
    token_id: str,
    payload: TokenUpdate,
    db: Session = Depends(get_db),
) -> TokenOut:
    token = _get_or_404(db, token_id)
    if payload.name is not None:
        token.name = payload.name.strip()
    if payload.datasets is not None:
        token.datasets = _normalize_datasets(payload.datasets)
    if payload.scope is not None:
        token.scope_level = SCOPE_LEVEL[payload.scope]
    if payload.enabled is not None:
        token.enabled = payload.enabled
    if payload.rate_limit_per_min is not None:
        token.rate_limit_per_min = payload.rate_limit_per_min
    if payload.note is not None:
        token.note = payload.note
    if payload.expires_at is not None:
        token.expires_at = (
            payload.expires_at.replace(tzinfo=timezone.utc)
            if payload.expires_at.tzinfo is None
            else payload.expires_at
        )
    db.commit()
    db.refresh(token)
    return _to_out(token)


@router.delete("/tokens/{token_id}", status_code=204, summary="删除令牌")
def delete_token(token_id: str, db: Session = Depends(get_db)) -> Response:
    token = _get_or_404(db, token_id)
    db.delete(token)
    db.commit()
    return Response(status_code=204)


@router.get("/audit", response_model=list[AuditOut], summary="查询审计日志")
def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    token_id: str | None = None,
    decision: str | None = Query(default=None, pattern="^(allow|deny)$"),
    db: Session = Depends(get_db),
) -> list[AuditOut]:
    query = db.query(AuditLog)
    if token_id:
        query = query.filter(AuditLog.token_id == token_id)
    if decision:
        query = query.filter(AuditLog.decision == decision)
    rows = query.order_by(AuditLog.id.desc()).limit(limit).all()
    result = []
    for row in rows:
        try:
            datasets = json.loads(row.datasets_json or "[]")
        except ValueError:
            datasets = []
        result.append(
            AuditOut(
                id=row.id,
                ts=_dt(row.ts) or datetime.now(timezone.utc),
                token_id=row.token_id,
                token_name=row.token_name,
                client_ip=row.client_ip,
                method=row.method,
                path=row.path,
                action=row.action,
                datasets=datasets,
                decision=row.decision,
                reason=row.reason,
                status_code=row.status_code,
            )
        )
    return result


@router.get("/stats", summary="概览统计")
def stats(db: Session = Depends(get_db)) -> dict:
    total = db.query(AccessToken).count()
    enabled = db.query(AccessToken).filter(AccessToken.enabled.is_(True)).count()
    today = datetime.now(timezone.utc).date()
    audit_today = (
        db.query(AuditLog)
        .filter(AuditLog.ts >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc))
        .count()
    )
    denied_today = (
        db.query(AuditLog)
        .filter(
            AuditLog.ts >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
            AuditLog.decision == "deny",
        )
        .count()
    )
    return {
        "tokens_total": total,
        "tokens_enabled": enabled,
        "audit_today": audit_today,
        "denied_today": denied_today,
    }


# 便于其它模块复用过期时间工具。
def utc_now_plus(days: int | None) -> datetime | None:
    if not days:
        return None
    return datetime.now(timezone.utc) + timedelta(days=days)
