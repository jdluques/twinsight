"""Importación de fuentes y representaciones (RF-010..RF-027, Diseño §3.3).

Protocolo dual-store: se traduce la fuente a un ``GraphBatch`` y se aplica al
grafo; recién entonces se registra el ``ImportRecord``. Si el parseo falla se
responde 422 **sin efectos** (ni en el grafo ni en el relacional).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_editor, require_viewer
from ..importers.image.translator import anchor_to_node
from ..importers.base import GraphBatch
from ..importers.registry import detect_translator, get_translator
from ..importers.svg.sanitize import sanitize_svg
from ..modelgraph.base import ModelGraph
from ..persistence.db import get_session, new_id
from ..persistence.models import ImportRecord
from .common import (
    bad_request,
    get_graph,
    iso,
    project_or_404,
    record_or_404,
    sha256,
    unprocessable,
)
from .schemas import AnchorsCreate

router = APIRouter(tags=["sources"])


# --------------------------------------------------------------------------- #
# Importación
# --------------------------------------------------------------------------- #
async def _import(
    *,
    project_id: str,
    file: UploadFile,
    kind: str | None,
    session: AsyncSession,
    graph: ModelGraph,
) -> dict:
    """Traduce, aplica al grafo y registra la fuente (protocolo dual-store)."""
    await project_or_404(session, project_id)
    data = await file.read()
    filename = file.filename or "sin-nombre"

    # 1) Elegir traductor (por kind explícito o por detección).
    translator = None
    if kind:
        try:
            translator = get_translator(kind)
        except KeyError:
            raise bad_request(f"tipo de fuente desconocido: {kind!r}", "unknown_kind")
    else:
        translator = detect_translator(filename, data)
    if translator is None:
        raise unprocessable(
            f"no se reconoció el formato de {filename!r}", "unsupported_format"
        )

    # 2) Traducir (parseo). Un fallo aquí es 422 SIN efectos.
    record_id = new_id()
    try:
        batch = translator.translate(data, source_id=record_id)
    except Exception as exc:  # noqa: BLE001 - cualquier error de parseo
        raise unprocessable(
            f"la fuente no se pudo interpretar: {exc}", "parse_error"
        )

    # 3) Aplicar al grafo y recién entonces registrar en el relacional.
    orphaned = await graph.apply_batch(project_id, batch)

    report = batch.report.model_dump()
    report["orphaned_mappings"] = orphaned
    record = ImportRecord(
        id=record_id,
        project_id=project_id,
        kind=translator.kind,
        filename=filename,
        file_hash=sha256(data),
        raw_file=data,
        status="ok",
        report=report,
    )
    session.add(record)
    await session.commit()

    return {"source_id": record_id, "kind": translator.kind, **report}


@router.post("/projects/{project_id}/model-sources")
async def import_model_source(
    project_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Importa un modelo ``.gaphor`` (fuente estructural del proyecto)."""
    return await _import(
        project_id=project_id, file=file, kind="gaphor", session=session, graph=graph
    )


@router.post("/projects/{project_id}/representations")
async def import_representation(
    project_id: str,
    file: UploadFile = File(...),
    kind: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Importa una representación (SVG/imagen); ``kind`` es opcional (se detecta)."""
    return await _import(
        project_id=project_id, file=file, kind=kind, session=session, graph=graph
    )


# --------------------------------------------------------------------------- #
# Listado / borrado de fuentes
# --------------------------------------------------------------------------- #
def _serialize_record(record: ImportRecord) -> dict:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "kind": record.kind,
        "filename": record.filename,
        "file_hash": record.file_hash,
        "status": record.status,
        "imported_at": iso(record.imported_at),
        "report": record.report or {},
    }


@router.get("/projects/{project_id}/sources")
async def list_sources(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await project_or_404(session, project_id)
    records = (
        await session.execute(
            select(ImportRecord)
            .where(ImportRecord.project_id == project_id)
            .order_by(ImportRecord.imported_at)
        )
    ).scalars()
    return [_serialize_record(r) for r in records]


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    record = await record_or_404(session, source_id)
    # Borrar del grafo los nodos de esta fuente (lote vacío con su source_id).
    await graph.apply_batch(
        record.project_id, GraphBatch(source_id=record.id, kind=record.kind)
    )
    await session.delete(record)
    await session.commit()
    return {"status": "deleted", "source_id": source_id}


@router.post("/projects/{project_id}/resync")
async def resync_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Re-aplica todas las fuentes del proyecto al grafo (idempotente)."""
    await project_or_404(session, project_id)
    records = list(
        (
            await session.execute(
                select(ImportRecord).where(
                    ImportRecord.project_id == project_id,
                    ImportRecord.status == "ok",
                )
            )
        ).scalars()
    )
    results: list[dict] = []
    for record in records:
        try:
            translator = get_translator(record.kind)
        except KeyError:
            results.append({"source_id": record.id, "status": "sin-traductor"})
            continue
        batch = translator.translate(bytes(record.raw_file or b""), source_id=record.id)
        orphaned = await graph.apply_batch(project_id, batch)
        results.append(
            {
                "source_id": record.id,
                "status": "ok",
                "elements": batch.report.elements,
                "orphaned_mappings": orphaned,
            }
        )
    return {"project_id": project_id, "sources": results}


# --------------------------------------------------------------------------- #
# Representación: anclas y servido
# --------------------------------------------------------------------------- #
@router.post("/representations/{representation_id}/anchors")
async def add_anchors(
    representation_id: str,
    body: AnchorsCreate,
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Añade anclas ``(x, y)`` sobre una representación de imagen (RF-026)."""
    record = await record_or_404(session, representation_id)

    report = dict(record.report or {})
    anchors = list(report.get("anchors", []))
    existing = {a.get("name") for a in anchors}
    for anchor in body.anchors:
        if anchor.name in existing:
            # Reemplaza las coordenadas si el ancla ya existía.
            for a in anchors:
                if a.get("name") == anchor.name:
                    a["x"], a["y"] = anchor.x, anchor.y
        else:
            anchors.append({"name": anchor.name, "x": anchor.x, "y": anchor.y})
            existing.add(anchor.name)

    # Materializar TODAS las anclas como VisualObjects de una fuente dedicada.
    nodes = [
        anchor_to_node(record.id, a["name"], a["x"], a["y"]) for a in anchors
    ]
    batch = GraphBatch(
        source_id=f"anchors:{record.id}", kind="image", nodes=nodes
    )
    await graph.apply_batch(record.project_id, batch)

    report["anchors"] = anchors
    record.report = report
    await session.commit()
    return {"representation_id": representation_id, "anchors": anchors}


_MEDIA = {
    "svg": "image/svg+xml",
    "image": "application/octet-stream",
}


@router.get("/representations/{representation_id}")
async def get_representation(
    representation_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
):
    """Sirve la representación: SVG saneado (texto) o binario de la imagen."""
    from fastapi.responses import Response

    record = await record_or_404(session, representation_id)
    raw = bytes(record.raw_file or b"")

    if record.kind == "svg":
        return Response(content=sanitize_svg(raw), media_type="image/svg+xml")

    # Imagen raster: devolver el binario con su tipo sniffeado.
    media = "application/octet-stream"
    if raw.startswith(b"\x89PNG"):
        media = "image/png"
    elif raw.startswith(b"\xff\xd8"):
        media = "image/jpeg"
    return Response(content=raw, media_type=media)
