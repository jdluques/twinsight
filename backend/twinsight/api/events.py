"""Eventos y alertas (RF-057, RF-060): listado, ack y timeline por elemento."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_operator, require_viewer
from ..persistence.db import get_session
from ..persistence.models import Event
from .common import iso, not_found, parse_dt, project_or_404
from .schemas import AckIn

router = APIRouter(tags=["events"])


def _event(e: Event) -> dict:
    return {
        "id": e.id,
        "project_id": e.project_id,
        "ts": iso(e.ts),
        "origin_kind": e.origin_kind,
        "origin_id": e.origin_id,
        "status": e.status,
        "severity": e.severity,
        "payload": e.payload or {},
        "acknowledged_at": iso(e.acknowledged_at),
    }


@router.get("/projects/{project_id}/events")
async def list_events(
    project_id: str,
    since: str | None = Query(None),
    severity: str | None = Query(None),
    origin: str | None = Query(None, description="origin_kind: rule|transition"),
    acknowledged: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await project_or_404(session, project_id)
    stmt = select(Event).where(Event.project_id == project_id)
    since_dt = parse_dt(since)
    if since_dt is not None:
        stmt = stmt.where(Event.ts >= since_dt)
    if severity:
        stmt = stmt.where(Event.severity == severity)
    if origin:
        stmt = stmt.where(Event.origin_kind == origin)
    if acknowledged is True:
        stmt = stmt.where(Event.acknowledged_at.is_not(None))
    elif acknowledged is False:
        stmt = stmt.where(Event.acknowledged_at.is_(None))
    stmt = stmt.order_by(Event.ts.desc())
    rows = (await session.execute(stmt)).scalars()
    return [_event(e) for e in rows]


@router.post("/events/{event_id}/ack")
async def acknowledge_event(
    event_id: str,
    body: AckIn | None = None,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_operator),
) -> dict:
    """Reconoce (ack) una alerta (requiere nivel operator)."""
    event = await session.get(Event, event_id)
    if event is None:
        raise not_found(f"evento no encontrado: {event_id}")
    if event.acknowledged_at is None:
        event.acknowledged_at = datetime.now(timezone.utc)
        await session.commit()
    return _event(event)


@router.get("/elements/{element_id}/timeline")
async def element_timeline(
    element_id: str,
    project_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    """Eventos cuya causa (``origin_id``) es este elemento, ordenados por tiempo."""
    rows = (
        await session.execute(
            select(Event)
            .where(Event.project_id == project_id, Event.origin_id == element_id)
            .order_by(Event.ts)
        )
    ).scalars()
    return [_event(e) for e in rows]
