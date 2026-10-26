"""Exploración del modelo canónico: elementos, diagramas y máquinas de estado.

Son lecturas delgadas sobre :class:`ModelGraph`. Los endpoints de detalle por
id (que el grafo particiona por proyecto) reciben el proyecto como query param.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..access import Principal, require_viewer
from ..modelgraph.base import ModelGraph
from .common import get_graph, not_found

router = APIRouter(tags=["model"])


@router.get("/projects/{project_id}/elements")
async def list_elements(
    project_id: str,
    kind: str | None = Query(None),
    q: str | None = Query(None),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    return await graph.get_elements(project_id, kind=kind, q=q)


@router.get("/elements/{element_id}")
async def get_element(
    element_id: str,
    project_id: str = Query(..., description="proyecto del elemento"),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> dict:
    element = await graph.get_element(project_id, element_id)
    if element is None:
        raise not_found(f"elemento no encontrado: {element_id}")
    return element


@router.get("/projects/{project_id}/diagrams")
async def list_diagrams(
    project_id: str,
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    return await graph.get_diagrams(project_id)


@router.get("/diagrams/{diagram_id}")
async def get_diagram(
    diagram_id: str,
    project_id: str = Query(...),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> dict:
    diagram = await graph.get_diagram(project_id, diagram_id)
    if diagram is None:
        raise not_found(f"diagrama no encontrado: {diagram_id}")
    return diagram


@router.get("/projects/{project_id}/state-machines")
async def list_state_machines(
    project_id: str,
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    return await graph.get_state_machines(project_id)


@router.get("/state-machines/{machine_id}")
async def get_state_machine(
    machine_id: str,
    project_id: str = Query(...),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_viewer),
) -> dict:
    machine = await graph.get_state_machine(project_id, machine_id)
    if machine is None:
        raise not_found(f"máquina de estados no encontrada: {machine_id}")
    return machine
