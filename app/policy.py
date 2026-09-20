"""策略核心（纯函数，便于单元测试）。

职责：
1. 按 HTTP 方法 + 路径判定动作级别 retrieve / write / admin；
2. 从 URL 路径与 JSON 请求体中提取数据集、文档、聊天助手等资源标识；
3. 依据 Token 的数据集白名单与权限级别给出 allow / deny 决策；
4. 给出需要重写回请求体的 dataset_ids（白名单交集，纵深防御）。

路径中数据集 ID、请求体 dataset_ids、document/chat 反查归属三处会合并校验。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .schemas import SCOPE_LEVEL

Action = Literal["retrieve", "write", "admin"]

_DATASET_ID_RE = re.compile(r"/datasets/(?P<id>[^/?#]+)")
_CHAT_OPENAI_RE = re.compile(r"/(?:openai|chats_openai)/(?P<id>[^/?#]+)/chat/completions")
_CHAT_ID_RE = re.compile(r"/chats/(?P<id>[^/?#]+)")
_DATASET_COLLECTION_RE = re.compile(r"/datasets/?$")
_RETRIEVAL_RE = re.compile(r"/retrieval/?$")
_SESSION_COMPLETION_RE = re.compile(r"/sessions/[^/?#]+/completion/?$")


def normalize_path(path: str) -> str:
    """去掉查询串与结尾斜杠差异，保留完整路径用于匹配。"""
    return path.split("?", 1)[0]


def get_path_dataset_id(path: str) -> str | None:
    match = _DATASET_ID_RE.search(normalize_path(path))
    return match.group("id") if match else None


def get_chat_id(path: str) -> str | None:
    p = normalize_path(path)
    m = _CHAT_OPENAI_RE.search(p)
    if m:
        return m.group("id")
    m = _CHAT_ID_RE.search(p)
    return m.group("id") if m else None


def is_dataset_listing(method: str, path: str) -> bool:
    return method.upper() == "GET" and bool(_DATASET_COLLECTION_RE.search(normalize_path(path)))


def classify_action(method: str, path: str) -> Action:
    """把 RAGFlow REST 调用映射到 retrieve / write / admin 三级。"""
    m = method.upper()
    p = normalize_path(path)

    # ---- 读取 / 检索 / 对话 ----
    if _RETRIEVAL_RE.search(p):
        return "retrieve"
    if _CHAT_OPENAI_RE.search(p) or _SESSION_COMPLETION_RE.search(p):
        return "retrieve"
    if m in ("GET", "HEAD", "OPTIONS"):
        return "retrieve"

    # ---- 知识库集合：创建为 admin ----
    if _DATASET_COLLECTION_RE.search(p) and m == "POST":
        return "admin"

    # ---- 具体知识库：删除 / 改配置为 admin ----
    ds_id = get_path_dataset_id(p)
    if ds_id:
        if m == "DELETE":
            # /datasets/{id} 删库为 admin；/datasets/{id}/documents/{doc} 删文档为 write
            if re.search(rf"/datasets/{re.escape(ds_id)}/?$", p):
                return "admin"
            return "write"
        if m in ("PUT", "PATCH"):
            if re.search(rf"/datasets/{re.escape(ds_id)}/?$", p):
                return "admin"
            return "write"
        if m == "POST":
            return "write"  # 上传文档、创建分块等

    # ---- 聊天助手：创建 / 删除 / 修改为 admin，使用为 retrieve ----
    if "/chats" in p:
        if p.rstrip("/").endswith("/chats") and m == "POST":
            return "admin"
        if get_chat_id(p) and m in ("DELETE", "PUT", "PATCH"):
            return "admin"

    # ---- 兜底：修改类操作至少需要 write ----
    return "write"


def body_dataset_ids(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    value = body.get("dataset_ids")
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    return []


def body_document_ids(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    value = body.get("document_ids")
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    return []


@dataclass
class Decision:
    allow: bool
    action: Action = "retrieve"
    status_code: int = 403
    reason: str = ""
    required: list[str] = field(default_factory=list)
    is_dataset_listing: bool = False
    # 当请求体含 dataset_ids 时，用这里的结果重写（白名单交集）；None 表示无需改写。
    final_dataset_ids: list[str] | None = None


def decide(
    *,
    method: str,
    path: str,
    body: Any,
    scope_level: int,
    allowed_datasets: list[str],
    bound_datasets: list[str] | None = None,
    unresolved_resource: bool = False,
    strict: bool = True,
) -> Decision:
    """纯鉴权决策。

    bound_datasets: 通过 RAGFlow 反查 document/chat 得到的归属数据集 ID。
    unresolved_resource: 存在无法反查归属的 document/chat（strict 下拒绝）。
    """
    action = classify_action(method, path)
    listing = is_dataset_listing(method, path)
    required: set[str] = set()

    path_ds = get_path_dataset_id(path)
    if path_ds:
        required.add(path_ds)

    body_ds = body_dataset_ids(body)
    required.update(body_ds)
    required.update(bound_datasets or [])

    # 1) 权限级别
    if scope_level < SCOPE_LEVEL[action]:
        return Decision(
            allow=False,
            action=action,
            status_code=403,
            reason=f"权限不足：该操作需要 '{action}' 权限",
            required=sorted(required),
            is_dataset_listing=listing,
        )

    wildcard = "*" in allowed_datasets
    allowed = set(allowed_datasets)

    # 2) 无法确认资源归属（document/chat 反查失败）
    if unresolved_resource and strict and not wildcard:
        return Decision(
            allow=False,
            action=action,
            status_code=403,
            reason="无法确认所请求文档/助手归属的数据集，已按严格模式拒绝",
            required=sorted(required),
            is_dataset_listing=listing,
        )

    # 3) 写操作必须能定位数据集（拦截旧版无数据集上下文的上传等）
    if action in ("write", "admin") and not listing and not required:
        #集合级创建（建库 / 建助手）属于预期内的无 ID 操作
        collection_create = (
            (action == "admin" and _DATASET_COLLECTION_RE.search(normalize_path(path)) and method.upper() == "POST")
            or (normalize_path(path).rstrip("/").endswith("/chats") and method.upper() == "POST")
        )
        if not collection_create and strict:
            return Decision(
                allow=False,
                action=action,
                status_code=403,
                reason="写操作未能定位目标数据集，已按严格模式拒绝",
                is_dataset_listing=listing,
            )

    # 4) 数据集白名单
    if not wildcard:
        forbidden = sorted(required - allowed)
        if forbidden:
            preview = ", ".join(forbidden[:3]) + ("…" if len(forbidden) > 3 else "")
            return Decision(
                allow=False,
                action=action,
                status_code=403,
                reason=f"访问了未授权的数据集：{preview}",
                required=sorted(required),
                is_dataset_listing=listing,
            )

    # 5) 放行；给出请求体 dataset_ids 的交集改写结果（纵深防御，正常情况与原值一致）
    final_ids: list[str] | None = None
    if body_ds:
        if wildcard:
            final_ids = list(dict.fromkeys(body_ds))
        else:
            intersect = [d for d in body_ds if d in allowed]
            final_ids = list(dict.fromkeys(intersect))

    return Decision(
        allow=True,
        action=action,
        status_code=200,
        required=sorted(required),
        is_dataset_listing=listing,
        final_dataset_ids=final_ids,
    )
