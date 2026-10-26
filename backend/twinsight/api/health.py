"""Endpoint de salud (RNF-004): estado del relacional, el grafo y el broker."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ingest import get_manager
from ..modelgraph.base import ModelGraph
from ..persistence.db import get_session
from .common import get_graph

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
) -> dict:
    """Comprobación de vida de las tres piezas de infraestructura."""
    # Relacional.
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    # Grafo.
    try:
        graph_ok = await graph.ping()
    except Exception:  # noqa: BLE001
        graph_ok = False

    # Broker / ingesta.
    try:
        broker = await get_manager().health()
    except Exception:  # noqa: BLE001
        broker = {}

    status = "ok" if (db_ok and graph_ok) else "degraded"
    return {
        "status": status,
        "db": db_ok,
        "graph": graph_ok,
        "broker": broker,
    }
