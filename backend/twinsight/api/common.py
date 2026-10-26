"""Utilidades compartidas por los routers REST de TwinSight (Diseño §4.1).

Reúne: el error HTTP con formato ``{"detail", "code"}``, las dependencias de
infraestructura (grafo, servicio de mapeo) y un puñado de ayudantes para
localizar filas relacionales (o responder 404) y para reunir los canales y
objetos visuales de un proyecto (que el servicio de mapeo necesita como listas).

Los routers deben quedar delgados: validan, llaman a estas utilidades o a los
servicios ya existentes y serializan la respuesta.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..importers.registry import get_translator
from ..mapping import MappingService
from ..modelgraph import get_model_graph
from ..modelgraph.base import ModelGraph
from ..persistence.models import Channel, DataSource, ImportRecord, Project


# --------------------------------------------------------------------------- #
# Error con formato {"detail", "code"}
# --------------------------------------------------------------------------- #
class ApiError(HTTPException):
    """HTTPException que además transporta un ``code`` legible por máquina.

    El cuerpo final ``{"detail", "code"}`` lo produce :func:`api_error_handler`;
    la app lo registra con ``app.add_exception_handler(ApiError, api_error_handler)``.
    """

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    """Serializa un :class:`ApiError` como ``{"detail", "code"}``."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
    )


def not_found(detail: str = "recurso no encontrado") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "not_found", detail)


def bad_request(detail: str, code: str = "bad_request") -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, code, detail)


def unprocessable(detail: str, code: str = "unprocessable") -> ApiError:
    return ApiError(422, code, detail)


# --------------------------------------------------------------------------- #
# Dependencias de infraestructura
# --------------------------------------------------------------------------- #
def get_graph() -> ModelGraph:
    """Dependencia FastAPI: el singleton de :class:`ModelGraph`."""
    return get_model_graph()


def get_mapping_service() -> MappingService:
    """Dependencia FastAPI: un :class:`MappingService` sobre el grafo."""
    return MappingService(graph=get_model_graph())


# --------------------------------------------------------------------------- #
# Localizadores relacionales (o 404)
# --------------------------------------------------------------------------- #
async def project_or_404(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise not_found(f"proyecto no encontrado: {project_id}")
    return project


async def record_or_404(session: AsyncSession, record_id: str) -> ImportRecord:
    record = await session.get(ImportRecord, record_id)
    if record is None:
        raise not_found(f"fuente/representación no encontrada: {record_id}")
    return record


async def datasource_or_404(session: AsyncSession, ds_id: str) -> DataSource:
    ds = await session.get(DataSource, ds_id)
    if ds is None:
        raise not_found(f"fuente de datos no encontrada: {ds_id}")
    return ds


async def channel_or_404(session: AsyncSession, channel_id: str) -> Channel:
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise not_found(f"canal no encontrado: {channel_id}")
    return channel


# --------------------------------------------------------------------------- #
# Serialización de utilidades
# --------------------------------------------------------------------------- #
def iso(value: datetime | None) -> str | None:
    """Fecha en ISO-8601 o ``None``."""
    return value.isoformat() if value is not None else None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_dt(value: str | None) -> datetime | None:
    """Parsea un instante ISO-8601 (acepta el sufijo ``Z``) o ``None``."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise bad_request(f"fecha inválida: {value!r} ({exc})", "bad_datetime")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------- #
# Reunir canales y objetos visuales de un proyecto
# --------------------------------------------------------------------------- #
async def project_channels(session: AsyncSession, project_id: str) -> list[dict]:
    """Canales del proyecto (desde el relacional) como dicts para el mapeo.

    Las claves ``id``/``channel_id``/``topic``/``metric`` son las que entiende
    :class:`~twinsight.mapping.service.MappingService`.
    """
    rows = (
        await session.execute(
            select(Channel)
            .join(DataSource, Channel.datasource_id == DataSource.id)
            .where(DataSource.project_id == project_id)
        )
    ).scalars()
    out: list[dict] = []
    for ch in rows:
        out.append(
            {
                "id": ch.id,
                "channel_id": ch.id,
                "datasource_id": ch.datasource_id,
                "topic": ch.topic_pattern,
                "metric": ch.metric_key,
                "unit": ch.unit,
                "label": ch.label,
            }
        )
    return out


async def project_visual_objects(
    session: AsyncSession, project_id: str
) -> list[dict]:
    """Objetos visuales del proyecto reconstruidos de las representaciones.

    El contrato de :class:`ModelGraph` no expone un listado global de
    ``VisualObject``; como son deterministas por fuente, se recuperan
    re-ejecutando el traductor de cada representación importada (SVG/imagen) y
    tomando sus nodos ``VisualObject`` (con su metadata ``dt:*``).
    """
    records = (
        await session.execute(
            select(ImportRecord).where(
                ImportRecord.project_id == project_id,
                ImportRecord.status == "ok",
            )
        )
    ).scalars()

    out: list[dict] = []
    seen: set[str] = set()
    for record in records:
        if record.kind not in ("svg", "image"):
            continue
        try:
            translator = get_translator(record.kind)
            batch = translator.translate(
                bytes(record.raw_file or b""), source_id=record.id
            )
        except Exception:  # noqa: BLE001 - una fuente rota no debe tumbar el listado
            continue
        for node in batch.nodes:
            if node.label != "VisualObject" or node.origin_id in seen:
                continue
            seen.add(node.origin_id)
            props = dict(node.properties)
            out.append(
                {
                    "id": node.origin_id,
                    "origin_id": node.origin_id,
                    "label": props.get("label") or node.origin_id,
                    "properties": props,
                }
            )
        # Anclas añadidas por API sobre una imagen (guardadas en el report).
        for anchor in (record.report or {}).get("anchors", []) or []:
            oid = anchor.get("origin_id") or f"{record.id}#{anchor.get('name')}"
            if oid in seen:
                continue
            seen.add(oid)
            out.append(
                {
                    "id": oid,
                    "origin_id": oid,
                    "label": anchor.get("name") or oid,
                    "properties": {"kind": "anchor", **anchor},
                }
            )
    return out


def jsonable(value: Any) -> Any:
    """Convierte recursivamente ``datetime`` a ISO para respuestas JSON."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
