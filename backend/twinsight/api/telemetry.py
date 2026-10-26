"""Telemetría (RF-032, RF-085): series por canal y último valor por proyecto."""

from __future__ import annotations

from statistics import fmean

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_viewer
from ..persistence.db import get_session
from ..persistence.models import Channel, DataSource, TelemetryReading
from .common import channel_or_404, iso, parse_dt, project_or_404

router = APIRouter(tags=["telemetry"])


def _aggregate(values: list[float], agg: str) -> float:
    if agg == "min":
        return min(values)
    if agg == "max":
        return max(values)
    if agg == "sum":
        return sum(values)
    if agg == "count":
        return float(len(values))
    return fmean(values)  # avg por defecto


@router.get("/channels/{channel_id}/telemetry")
async def channel_telemetry(
    channel_id: str,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    limit: int = Query(1000, ge=1, le=100000),
    agg: str | None = Query(None, description="avg|min|max|sum|count"),
    bucket: int | None = Query(None, ge=1, description="tamaño de bucket en segundos"),
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    """Lecturas del canal en ``[from, to]``; si ``agg``+``bucket``, agrega por ventana."""
    await channel_or_404(session, channel_id)
    from_dt, to_dt = parse_dt(from_), parse_dt(to)

    stmt = select(TelemetryReading).where(TelemetryReading.channel_id == channel_id)
    if from_dt is not None:
        stmt = stmt.where(TelemetryReading.ts >= from_dt)
    if to_dt is not None:
        stmt = stmt.where(TelemetryReading.ts <= to_dt)

    if agg and bucket:
        # Agregación por ventana temporal fija (portable, en memoria).
        stmt = stmt.order_by(TelemetryReading.ts)
        rows = list((await session.execute(stmt)).scalars())
        buckets: dict[int, list[float]] = {}
        for r in rows:
            win = int(r.ts.timestamp()) // bucket
            buckets.setdefault(win, []).append(r.value)
        out = [
            {"ts_bucket": win * bucket, "value": _aggregate(vals, agg)}
            for win, vals in sorted(buckets.items())
        ]
        return out[:limit]

    stmt = stmt.order_by(TelemetryReading.ts.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars()
    return [{"ts": iso(r.ts), "value": r.value} for r in rows]


@router.get("/projects/{project_id}/telemetry/latest")
async def latest_telemetry(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> dict:
    """Último valor de cada canal del proyecto (para precargar el cliente)."""
    await project_or_404(session, project_id)

    channel_ids = list(
        (
            await session.execute(
                select(Channel.id)
                .join(DataSource, Channel.datasource_id == DataSource.id)
                .where(DataSource.project_id == project_id)
            )
        ).scalars()
    )
    if not channel_ids:
        return {}

    # Última lectura por canal: max(ts) agrupado y luego el valor en ese ts.
    latest = {}
    for cid in channel_ids:
        row = (
            await session.execute(
                select(TelemetryReading)
                .where(TelemetryReading.channel_id == cid)
                .order_by(TelemetryReading.ts.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            latest[cid] = {"ts": iso(row.ts), "value": row.value}
    return latest
