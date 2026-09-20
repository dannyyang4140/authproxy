"""FastAPI 入口：挂载管理接口、管理台、健康检查与兜底反向代理。"""
from __future__ import annotations

import logging
import os
import pathlib
import secrets
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .admin import router as admin_router
from .config import get_settings
from .db import init_db
from .proxy import router as proxy_router
from .ragflow import RagflowClient
from .security import require_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ragflow-auth-proxy")

_STATIC_DIR = pathlib.Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()

    if not settings.admin_token:
        generated = secrets.token_urlsafe(24)
        settings.admin_token = generated
        try:
            token_file = pathlib.Path(settings.admin_token_file)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(generated, encoding="utf-8")
            os.chmod(token_file, 0o600)
            logger.warning("未设置 ADMIN_TOKEN，已随机生成并写入 %s", settings.admin_token_file)
        except OSError as exc:
            logger.warning("无法写入管理员令牌文件：%s；本次启动管理员令牌为 %s", exc, generated)

    if not settings.ragflow_api_key:
        logger.warning("未配置 RAGFLOW_API_KEY：转发请求将无法通过 RAGFlow 鉴权，请尽快在 .env 中配置")

    app.state.ragflow = RagflowClient(
        base_url=settings.ragflow_base_url,
        api_key=settings.ragflow_api_key,
        timeout=settings.ragflow_timeout_seconds,
        cache_ttl=settings.binding_cache_ttl,
    )
    logger.info("RAGFlow 鉴权代理 v%s 已启动，上游：%s", __version__, settings.ragflow_base_url)
    yield
    await app.state.ragflow.aclose()


app = FastAPI(title="RAGFlow Auth Proxy", version=__version__, lifespan=lifespan)

_settings = get_settings()
if _settings.cors_origins.strip():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health", tags=["system"], summary="健康检查")
def health() -> dict:
    return {"status": "ok", "version": __version__, "upstream": _settings.ragflow_base_url}


@app.get("/", tags=["system"], summary="服务信息")
def index() -> JSONResponse:
    return JSONResponse(
        {
            "service": "ragflow-auth-proxy",
            "version": __version__,
            "admin_ui": "/admin/ui",
            "docs": "/docs",
            "health": "/health",
            "usage": "携带请求头 X-Proxy-Token 调用 /api/v1/... 即会鉴权并转发到 RAGFlow",
        }
    )


# 管理台页面（静态页面本身不鉴权，页面内的数据请求需输入管理员令牌）。
@app.get("/admin/ui", include_in_schema=False)
def admin_ui() -> HTMLResponse:
    html_path = _STATIC_DIR / "admin.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# 管理 API（整体受管理员凭证保护）。
app.include_router(admin_router, dependencies=[Depends(require_admin)])
# 兜底：其余所有路径按 RAGFlow API 反向代理处理（必须最后注册）。
app.include_router(proxy_router)


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=_settings.host, port=_settings.port, proxy_headers=True)
