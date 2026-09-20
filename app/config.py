"""集中读取环境变量配置。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- RAGFlow 上游（真实凭证只在网关服务端持有）----
    ragflow_base_url: str = "http://ragflow:9380"
    ragflow_api_key: str = ""
    ragflow_timeout_seconds: float = 120.0

    # ---- 管理员（管理 API / 管理台）----
    # 留空时启动会随机生成，并写入该文件且在日志中提示一次。
    admin_token: str = ""
    admin_token_file: str = "/data/admin_token.txt"

    # ---- 存储 ----
    database_url: str = "sqlite:////data/proxy.db"

    # ---- 安全策略 ----
    # 无法把 document_id / chat_id 反查到归属数据集时是否拒绝（true=默认拒绝，更安全）。
    strict_resource_binding: bool = True
    # document/chat 归属反查结果的缓存秒数。
    binding_cache_ttl: int = 300
    # 新签发 Token 的默认每分钟限流（0 表示不限）。
    default_rate_limit_per_min: int = 0
    # 是否允许跨域（管理台与服务同源，默认关闭；浏览器直连场景再配置）。
    cors_origins: str = ""

    # ---- 服务 ----
    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
