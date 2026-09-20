"""管理接口的请求 / 响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ScopeName = Literal["retrieve", "write", "admin"]

SCOPE_LEVEL: dict[str, int] = {"retrieve": 1, "write": 2, "admin": 3}
LEVEL_SCOPE: dict[int, str] = {1: "retrieve", 2: "write", 3: "admin"}


class TokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="成员或 Agent 名称")
    datasets: list[str] = Field(default_factory=list, description="允许的数据集 ID；['*'] 表示全部")
    scope: ScopeName = "retrieve"
    expires_at: Optional[datetime] = None
    rate_limit_per_min: int = Field(default=0, ge=0)
    note: str = Field(default="", max_length=255)


class TokenUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    datasets: Optional[list[str]] = None
    scope: Optional[ScopeName] = None
    enabled: Optional[bool] = None
    expires_at: Optional[datetime] = None
    rate_limit_per_min: Optional[int] = Field(default=None, ge=0)
    note: Optional[str] = Field(default=None, max_length=255)


class TokenOut(BaseModel):
    id: str
    name: str
    token_prefix: str
    datasets: list[str]
    scope: ScopeName
    enabled: bool
    expires_at: Optional[datetime] = None
    rate_limit_per_min: int
    note: str
    created_at: datetime
    last_used_at: Optional[datetime] = None


class TokenCreated(TokenOut):
    # 仅创建时返回一次明文令牌。
    token: str


class AuditOut(BaseModel):
    id: int
    ts: datetime
    token_id: Optional[str] = None
    token_name: str
    client_ip: str
    method: str
    path: str
    action: str
    datasets: list[str]
    decision: str
    reason: str
    status_code: int
