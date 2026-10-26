"""Fuentes de datos y canales de telemetría (RF-030..RF-035).

Editar una fuente de datos reconecta su ingesta vía el ``IngestManager``. Los
canales se espejan en el grafo (``upsert_channel``) para que puedan participar
en mapeos tripartitos.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_editor, require_viewer
from ..ingest import get_manager
from ..modelgraph.base import ModelGraph
from ..persistence.db import get_session
from ..persistence.models import Channel, DataSource
from .common import (
    channel_or_404,
    datasource_or_404,
    get_graph,
    project_or_404,
)
from .schemas import (
    ChannelCreate,
    ChannelUpdate,
    DataSourceCreate,
    DataSourceUpdate,
)

router = APIRouter(tags=["datasources"])


# --------------------------------------------------------------------------- #
# Serialización
# --------------------------------------------------------------------------- #
def _ds(ds: DataSource) -> dict:
    return {
        "id": ds.id,
        "project_id": ds.project_id,
        "kind": ds.kind,
        "config": ds.config or {},
    }


def _ch(ch: Channel) -> dict:
    return {
        "id": ch.id,
        "datasource_id": ch.datasource_id,
        "topic_pattern": ch.topic_pattern,
        "metric_key": ch.metric_key,
        "unit": ch.unit,
        "label": ch.label,
    }


# --------------------------------------------------------------------------- #
# Datasources
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/datasources")
async def list_datasources(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await project_or_404(session, project_id)
    rows = (
        await session.execute(
            select(DataSource).where(DataSource.project_id == project_id)
        )
    ).scalars()
    return [_ds(d) for d in rows]


@router.post("/projects/{project_id}/datasources", status_code=201)
async def create_datasource(
    project_id: str,
    body: DataSourceCreate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    await project_or_404(session, project_id)
    ds = DataSource(project_id=project_id, kind=body.kind, config=body.config)
    session.add(ds)
    await session.commit()
    # Arrancar la ingesta (si el broker no responde, reintenta solo).
    try:
        await get_manager().start(ds.id)
    except Exception:  # noqa: BLE001
        pass
    return _ds(ds)


@router.put("/datasources/{datasource_id}")
async def update_datasource(
    datasource_id: str,
    body: DataSourceUpdate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    ds = await datasource_or_404(session, datasource_id)
    if body.kind is not None:
        ds.kind = body.kind
    if body.config is not None:
        ds.config = body.config
    await session.commit()
    # Reconectar con la nueva configuración.
    manager = get_manager()
    try:
        await manager.stop(datasource_id)
        await manager.start(datasource_id)
    except Exception:  # noqa: BLE001
        pass
    return _ds(ds)


@router.delete("/datasources/{datasource_id}")
async def delete_datasource(
    datasource_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    ds = await datasource_or_404(session, datasource_id)
    try:
        await get_manager().stop(datasource_id)
    except Exception:  # noqa: BLE001
        pass
    await session.delete(ds)
    await session.commit()
    return {"status": "deleted", "datasource_id": datasource_id}


# --------------------------------------------------------------------------- #
# Canales
# --------------------------------------------------------------------------- #
@router.get("/datasources/{datasource_id}/channels")
async def list_channels(
    datasource_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await datasource_or_404(session, datasource_id)
    rows = (
        await session.execute(
            select(Channel).where(Channel.datasource_id == datasource_id)
        )
    ).scalars()
    return [_ch(c) for c in rows]


@router.post("/datasources/{datasource_id}/channels", status_code=201)
async def create_channel(
    datasource_id: str,
    body: ChannelCreate,
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    ds = await datasource_or_404(session, datasource_id)
    channel = Channel(
        datasource_id=datasource_id,
        topic_pattern=body.topic_pattern,
        metric_key=body.metric_key,
        unit=body.unit,
        label=body.label,
    )
    session.add(channel)
    await session.commit()
    # Espejar en el grafo para que pueda participar en mapeos.
    await graph.upsert_channel(ds.project_id, channel.id, body.topic_pattern, body.metric_key)
    return _ch(channel)


@router.post("/datasources/{datasource_id}/discover")
async def discover_channels(
    datasource_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Arranca la ingesta (descubrimiento automático) y lista los canales."""
    await datasource_or_404(session, datasource_id)
    started = True
    try:
        await get_manager().start(datasource_id)
    except Exception:  # noqa: BLE001
        started = False
    rows = (
        await session.execute(
            select(Channel).where(Channel.datasource_id == datasource_id)
        )
    ).scalars()
    return {"started": started, "channels": [_ch(c) for c in rows]}


@router.put("/channels/{channel_id}")
async def update_channel(
    channel_id: str,
    body: ChannelUpdate,
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    channel = await channel_or_404(session, channel_id)
    if body.topic_pattern is not None:
        channel.topic_pattern = body.topic_pattern
    if body.metric_key is not None:
        channel.metric_key = body.metric_key
    if body.unit is not None:
        channel.unit = body.unit
    if body.label is not None:
        channel.label = body.label
    ds = await datasource_or_404(session, channel.datasource_id)
    await session.commit()
    await graph.upsert_channel(
        ds.project_id, channel.id, channel.topic_pattern, channel.metric_key
    )
    return _ch(channel)


@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    channel = await channel_or_404(session, channel_id)
    await session.delete(channel)
    await session.commit()
    return {"status": "deleted", "channel_id": channel_id}
