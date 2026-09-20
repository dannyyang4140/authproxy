"""审计日志写入。"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .models import AuditLog


def insert_audit(
    db: Session,
    *,
    token_id: str | None,
    token_name: str,
    client_ip: str,
    method: str,
    path: str,
    action: str,
    datasets: list[str],
    decision: str,
    reason: str,
    status_code: int,
) -> None:
    try:
        row = AuditLog(
            token_id=token_id,
            token_name=token_name or "",
            client_ip=(client_ip or "")[:64],
            method=method or "",
            path=(path or "")[:512],
            action=action or "",
            datasets_json=json.dumps(datasets or [], ensure_ascii=False),
            decision=decision,
            reason=(reason or "")[:255],
            status_code=status_code or 0,
        )
        db.add(row)
        db.commit()
    except Exception:  # 审计失败不阻断主流程
        db.rollback()
