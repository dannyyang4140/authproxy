"""ORM 模型：访问 Token 与审计日志。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class AccessToken(Base):
    """管理员分配给成员 / AI Agent 的访问令牌。"""

    __tablename__ = "access_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 只存 SHA-256，明文仅在创建时返回一次。
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # 仅保存明文前缀，便于后台辨认，不泄露完整令牌。
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    # 允许访问的数据集 ID 列表，JSON 数组；["*"] 表示全部。
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    # 权限层级：1=retrieve 检索/对话，2=write 上传/改文档，3=admin 建删知识库。
    scope_level: Mapped[int] = mapped_column(Integer, default=1)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=0)

    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def datasets(self) -> list[str]:
        try:
            value = json.loads(self.datasets_json or "[]")
            return value if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []

    @datasets.setter
    def datasets(self, value: list[str]) -> None:
        self.datasets_json = json.dumps(value, ensure_ascii=False)

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or _utcnow()
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now >= exp


class AuditLog(Base):
    """每次请求的鉴权与转发审计记录。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    token_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    token_name: Mapped[str] = mapped_column(String(128), default="")
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    method: Mapped[str] = mapped_column(String(16), default="")
    path: Mapped[str] = mapped_column(String(512), default="")
    action: Mapped[str] = mapped_column(String(16), default="")
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    decision: Mapped[str] = mapped_column(String(8), default="")  # allow / deny
    reason: Mapped[str] = mapped_column(String(255), default="")
    status_code: Mapped[int] = mapped_column(Integer, default=0)
