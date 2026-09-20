"""RAGFlow 上游客户端：转发用的请求头构造，以及 document/chat 归属反查。

反查用于把请求里只给 document_id / chat_id 的调用，映射回其所属数据集，
从而仍能用数据集白名单判权。反查结果带 TTL 缓存；查不到时由策略层按
strict_resource_binding 决定是否拒绝（默认拒绝）。

不同 RAGFlow 版本的列表字段可能略有差异，下面对返回结构做了多种形态兼容；
如你的版本路径不同，只需调整本文件中的两个端点，鉴权与转发逻辑无需改动。
"""
from __future__ import annotations

import time
from typing import Any

import httpx


class TTLCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> tuple[bool, Any]:
        item = self._store.get(key)
        if item is None:
            return False, None
        expires_at, value = item
        if time.monotonic() - expires_at > self.ttl:
            self._store.pop(key, None)
            return False, None
        return True, value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)


def _unwrap(payload: Any) -> Any:
    """RAGFlow 通常返回 {"code":0,"data":...}；取出 data。"""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _extract_docs(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("docs", "documents", "items", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


class RagflowClient:
    def __init__(self, base_url: str, api_key: str, timeout: float, cache_ttl: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
            follow_redirects=True,
        )
        self._chat_cache = TTLCache(cache_ttl)
        self._doc_cache = TTLCache(cache_ttl)

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        """供反向代理转发复用（已携带 base_url 与 RAGFlow 授权头）。"""
        return self._client

    async def _get_json(self, path: str, params: dict | None = None) -> tuple[int, Any]:
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError:
            return 0, None
        if resp.status_code >= 400:
            return resp.status_code, None
        try:
            return resp.status_code, _unwrap(resp.json())
        except ValueError:
            return resp.status_code, None

    async def get_chat_dataset_ids(self, chat_id: str) -> list[str] | None:
        """返回聊天助手绑定的数据集 ID；查不到返回 None。"""
        hit, cached = self._chat_cache.get(chat_id)
        if hit:
            return cached
        result: list[str] | None = None

        status, data = await self._get_json(f"/api/v1/chats/{chat_id}")
        if status and isinstance(data, dict) and isinstance(data.get("dataset_ids"), list):
            result = [str(x) for x in data["dataset_ids"]]

        # 详情端点不可用时，回退到助手列表里匹配。
        if result is None:
            status, data = await self._get_json("/api/v1/chats", {"page_size": 200})
            for chat in _extract_docs(data):
                if str(chat.get("id")) == chat_id and isinstance(chat.get("dataset_ids"), list):
                    result = [str(x) for x in chat["dataset_ids"]]
                    break

        self._chat_cache.set(chat_id, result)
        return result

    async def resolve_document_owner(self, doc_id: str, candidate_datasets: list[str]) -> str | None:
        """在候选（已授权）数据集内定位文档归属；都找不到返回 None。"""
        hit, cached = self._doc_cache.get(doc_id)
        if hit:
            return cached
        owner: str | None = None
        for ds_id in candidate_datasets:
            if owner:
                break
            status, data = await self._get_json(
                f"/api/v1/datasets/{ds_id}/documents",
                {"id": doc_id, "page": 1, "page_size": 10},
            )
            if not status:
                continue
            for doc in _extract_docs(data):
                if str(doc.get("id")) == doc_id:
                    owner = ds_id
                    break
        self._doc_cache.set(doc_id, owner)
        return owner
