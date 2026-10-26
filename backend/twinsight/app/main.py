"""Ensamblado de la aplicación TwinSight (FastAPI).

El *lifespan* arranca, en orden: base relacional, claves de instancia, grafo,
puente WebSocket, seed de demo (opcional), motores de comportamiento por
proyecto y, por último, la ingesta MQTT (para que el comportamiento ya esté
suscrito al bus cuando empiecen a llegar lecturas).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from ..access import ensure_instance_keys
from ..behavior import BehaviorService
from ..bus import get_bus
from ..config import get_settings
from ..ingest import get_manager as get_ingest_manager
from ..modelgraph import get_model_graph
from ..persistence.db import get_sessionmaker, init_db
from ..persistence.models import Project
from ..ws import router as ws_router, start_ws_bridge
from .seed import seed_demo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("twinsight")

_behavior_services: dict[str, BehaviorService] = {}


async def _start_behavior_for_all(graph) -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        projects = (await session.execute(select(Project.id))).scalars().all()
    for pid in projects:
        if pid in _behavior_services:
            continue
        svc = BehaviorService(pid, graph)
        with suppress(Exception):
            await svc.start()
            _behavior_services[pid] = svc


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()
    await ensure_instance_keys()

    graph = get_model_graph()
    await graph.connect()
    app.state.graph = graph

    start_ws_bridge(bus=get_bus(), graph=graph)

    if os.getenv("TWINSIGHT_SEED_DEMO", "0") in ("1", "true", "True"):
        with suppress(Exception):
            await seed_demo(get_sessionmaker(), graph)

    await _start_behavior_for_all(graph)

    with suppress(Exception):
        await get_ingest_manager().start_all()

    logger.info("TwinSight iniciado en %s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        with suppress(Exception):
            await get_ingest_manager().stop_all()
        for svc in _behavior_services.values():
            with suppress(Exception):
                await svc.stop()
        with suppress(Exception):
            await graph.close()


def create_app() -> FastAPI:
    app = FastAPI(title="TwinSight", version="0.1.0", lifespan=lifespan)

    # API REST.
    try:
        from ..api import ApiError, api_error_handler, api_router
        app.include_router(api_router, prefix="/api/v1")
        app.add_exception_handler(ApiError, api_error_handler)
    except Exception as exc:  # noqa: BLE001 - la API puede no estar lista en dev
        logger.warning("No se pudo montar la API: %s", exc)

    # WebSocket.
    app.include_router(ws_router)

    # Frontend estático (SPA) con fallback a index.html.
    settings = get_settings()
    static_dir = settings.static_dir or _default_static_dir()
    if static_dir and Path(static_dir).exists():
        _mount_spa(app, Path(static_dir))
    else:
        @app.get("/")
        async def _root():
            return JSONResponse({
                "name": "TwinSight", "status": "ok",
                "docs": "/docs", "api": "/api/v1", "note": "frontend no compilado",
            })

    return app


def _default_static_dir() -> str | None:
    for c in [Path("/app/static"), Path(__file__).resolve().parents[3] / "frontend" / "dist"]:
        if c.exists():
            return str(c)
    return None


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    if (static_dir / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def _spa(full_path: str):
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
