"""Grafo e impacto (RF-103..RF-105): cierre de impacto y vecindario."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..access import Principal, require_viewer
from ..modelgraph.base import ModelGraph
from .common import bad_request, get_graph

router = APIRouter(tags=["graph"])


@router.get("/projects/{project_id}/impact")
async def impact(
    project_id: str,
    element_id: str | None = Query(None),
    channel_id: str | None = Query(None),
    max_depth: int = Query(4, ge=1, le=12),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> dict:
    if not element_id and not channel_id:
        raise bad_request("indica element_id o channel_id", "missing_endpoint")
    return await graph.impact(
        project_id,
        element_id=element_id,
        channel_id=channel_id,
        max_depth=max_depth,
    )


@router.get("/projects/{project_id}/graph/neighborhood")
async def neighborhood(
    project_id: str,
    node_id: str = Query(...),
    depth: int = Query(1, ge=1, le=12),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> dict:
    return await graph.neighborhood(project_id, node_id, depth=depth)
