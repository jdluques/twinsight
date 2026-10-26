"""Mapeo tripartito: CRUD, sugerencias, resolución y YAML (RF-040..RF-048).

Delega en :class:`MappingService`. Como el grafo particiona por proyecto, los
endpoints por id de mapeo reciben el proyecto como query param.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_editor, require_viewer
from ..mapping import MappingService, Suggestion
from ..mapping.yaml_format import export_yaml, import_yaml
from ..persistence.db import get_session
from .common import (
    bad_request,
    get_mapping_service,
    project_channels,
    project_or_404,
    project_visual_objects,
    unprocessable,
)
from .schemas import ConfirmSuggestions, MappingCreate

router = APIRouter(tags=["mappings"])


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/mappings")
async def list_mappings(
    project_id: str,
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    return await service.list(project_id)


@router.post("/projects/{project_id}/mappings", status_code=201)
async def create_mapping(
    project_id: str,
    body: MappingCreate,
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_editor),
) -> dict:
    try:
        mapping_id = await service.create(
            project_id,
            element_id=body.element_id,
            visual_object_id=body.visual_object_id,
            channel_id=body.channel_id,
            origin="manual",
        )
    except ValueError as exc:
        raise bad_request(str(exc), "invalid_mapping")
    return {"mapping_id": mapping_id}


@router.put("/mappings/{mapping_id}")
async def replace_mapping(
    mapping_id: str,
    body: MappingCreate,
    project_id: str = Query(...),
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Reemplaza un mapeo: borra el anterior y crea el nuevo con los extremos dados."""
    await service.delete(project_id, mapping_id)
    try:
        new_id = await service.create(
            project_id,
            element_id=body.element_id,
            visual_object_id=body.visual_object_id,
            channel_id=body.channel_id,
            origin="manual",
        )
    except ValueError as exc:
        raise bad_request(str(exc), "invalid_mapping")
    return {"mapping_id": new_id}


@router.delete("/mappings/{mapping_id}")
async def delete_mapping(
    mapping_id: str,
    project_id: str = Query(...),
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_editor),
) -> dict:
    await service.delete(project_id, mapping_id)
    return {"status": "deleted", "mapping_id": mapping_id}


# --------------------------------------------------------------------------- #
# Sugerencias (RF-041, RF-042)
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/mappings/suggestions")
async def suggest_mappings(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await project_or_404(session, project_id)
    # ``get_elements`` devuelve los nodos planos (propiedades en el nivel
    # superior); el servicio de mapeo lee la metadata ``dt:*`` de
    # ``element["properties"]``, así que la exponemos ahí también.
    elements = [{**e, "properties": e} for e in await service.graph.get_elements(project_id)]
    visuals = await project_visual_objects(session, project_id)
    channels = await project_channels(session, project_id)
    suggestions = service.suggest(project_id, elements, visuals, channels)
    return [
        {
            "element_id": s.element_id,
            "visual_object_id": s.visual_object_id,
            "channel_id": s.channel_id,
            "score": s.score,
            "confidence": s.confidence,
            "evidence": s.evidence,
            "endpoints": s.endpoints(),
        }
        for s in suggestions
    ]


@router.post("/mappings/suggestions/confirm")
async def confirm_suggestions(
    body: ConfirmSuggestions,
    project_id: str = Query(...),
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_editor),
) -> dict:
    suggestions = [
        Suggestion(
            element_id=s.element_id,
            visual_object_id=s.visual_object_id,
            channel_id=s.channel_id,
            score=s.score,
            confidence=s.confidence,
            evidence=s.evidence,
        )
        for s in body.suggestions
    ]
    created = await service.confirm(project_id, suggestions, origin=body.origin)
    return {"created": created, "count": len(created)}


# --------------------------------------------------------------------------- #
# Resolución (RF-046)
# --------------------------------------------------------------------------- #
@router.get("/mappings/resolve")
async def resolve_mapping(
    project_id: str = Query(...),
    element_id: str | None = Query(None),
    visual_object_id: str | None = Query(None),
    channel_id: str | None = Query(None),
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_viewer),
) -> dict:
    if not any((element_id, visual_object_id, channel_id)):
        raise bad_request(
            "indica element_id, visual_object_id o channel_id", "missing_endpoint"
        )
    return await service.resolve(
        project_id,
        element_id=element_id,
        visual_object_id=visual_object_id,
        channel_id=channel_id,
    )


# --------------------------------------------------------------------------- #
# YAML (Apéndice C)
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/mappings/export")
async def export_mappings_yaml(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_viewer),
) -> PlainTextResponse:
    project = await project_or_404(session, project_id)
    raw = await service.graph.list_mappings(project_id)
    entries: list[dict] = []
    for mapping in raw:
        entry: dict = {}
        for endpoint in mapping.get("endpoints", []):
            label, nid = endpoint.get("label"), endpoint.get("id")
            if not nid:
                continue
            if label == "Element":
                entry["element_id"] = nid
            elif label == "VisualObject":
                entry["visual_object_id"] = nid
            elif label == "Channel":
                entry["channel_id"] = nid
        if entry:
            entries.append(entry)
    text = export_yaml(project.name, entries)
    return PlainTextResponse(content=text, media_type="application/x-yaml")


@router.post("/projects/{project_id}/mappings/import")
async def import_mappings_yaml(
    project_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    service: MappingService = Depends(get_mapping_service),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Importa ``mappings.yaml`` (cuerpo YAML en texto plano) y crea los mapeos.

    Cada extremo se resuelve contra el grafo por id o por nombre; las entradas
    con menos de 2 extremos resolubles se reportan sin crear nada.
    """
    await project_or_404(session, project_id)
    text = (await request.body()).decode("utf-8")
    try:
        _conventions, entries = import_yaml(text)
    except ValueError as exc:
        raise unprocessable(f"mappings.yaml inválido: {exc}", "invalid_yaml")

    elements = await service.graph.get_elements(project_id)
    by_id = {e.get("id") for e in elements}
    by_name = {}
    for e in elements:
        name = (e.get("name") or "").strip().lower()
        if name:
            by_name.setdefault(name, e.get("id"))

    created: list[str] = []
    unresolved: list[dict] = []
    for entry in entries:
        element_id = None
        if entry.model:
            element_id = entry.model if entry.model in by_id else by_name.get(
                entry.model.strip().lower(), entry.model
            )
        try:
            mapping_id = await service.create(
                project_id,
                element_id=element_id,
                visual_object_id=entry.visual,
                channel_id=entry.channel,
                origin="yaml",
            )
            created.append(mapping_id)
        except ValueError:
            unresolved.append(
                {"model": entry.model, "visual": entry.visual, "channel": entry.channel}
            )
    return {"created": created, "count": len(created), "unresolved": unresolved}
