"""The FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ...bootstrap import Container, container_from_env
from ..logs import configure_logging
from .accesslog import AccessLog
from .asks import documents_router, install_ask_handlers
from .asks import router as asks_router
from .auth import admin_router
from .auth import router as auth_router
from .circuits import router as circuits_router
from .guard import BindGuard, loopback_only, loopback_or_address
from .hosts import HostGuard, hosts_allowed_by
from .nostore import NoStore
from .origin import OriginGuard, origin_allowed_by
from .problems import install_problem_handlers, use_problem_media_in_openapi
from .routes import router
from .webapp import WebApp

API_PREFIX = "/api/v1"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    container: Container = app.state.container
    await container.asks.start()  # the GPU lane, and the engine warming up in the background
    try:
        yield
    finally:
        await container.asks.stop()
        container.close()


def build_app(container: Container) -> FastAPI:
    # No docs UI and no served schema: the UI pages load scripts from a CDN, and the contract
    # is exported with `research-api openapi`.
    app = FastAPI(
        title="Research engine API",
        version=container.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.container = container
    fake = container.mode == "fake"
    # Added innermost first. A request meets, in order: the access log (so refusals are logged
    # too), the bind check (which also covers a start that skips `research-api serve`), the Host
    # check, the Origin check on writes, then the routes.
    app.add_middleware(NoStore, prefixes=(f"{API_PREFIX}/asks", f"{API_PREFIX}/documents"))
    app.add_middleware(OriginGuard, allowed=origin_allowed_by(container.settings, fake=fake))
    app.add_middleware(HostGuard, allowed=hosts_allowed_by(container.settings, fake=fake))
    app.add_middleware(
        BindGuard,
        allowed=loopback_only if fake else loopback_or_address(container.settings.bind_host),
    )
    app.add_middleware(AccessLog)
    app.include_router(router, prefix=API_PREFIX)
    app.include_router(circuits_router, prefix=API_PREFIX)
    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(admin_router, prefix=API_PREFIX)
    app.include_router(asks_router, prefix=API_PREFIX)
    app.include_router(documents_router, prefix=API_PREFIX)
    install_problem_handlers(app)
    install_ask_handlers(app)
    if container.settings.web_dir is not None:
        # The router's fallback, not a mount: Starlette calls it only when no route matched, not
        # even by path, so a wrong method on an API route still gets its 405 with `Allow`. It is
        # also outside the schema.
        app.router.default = WebApp(container.settings.web_dir)

    build_schema = app.openapi

    def openapi() -> dict[str, object]:
        if app.openapi_schema is None:
            use_problem_media_in_openapi(build_schema())
        assert app.openapi_schema is not None
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def create_app() -> FastAPI:
    """The no-argument factory: ``uvicorn research_api.entrypoints.http:create_app --factory``.

    Sets up the service's log like ``research-api serve`` does, and silences uvicorn's own access
    log, which would hold raw paths and query strings. uvicorn has set its logging up before it
    calls this, so the logger is switched off here instead of in its options.
    """
    container = container_from_env()
    configure_logging(None if container.mode == "fake" else container.settings.log_dir)
    logging.getLogger("uvicorn.access").disabled = True
    return build_app(container)
