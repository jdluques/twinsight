"""CRUD de proyectos (RF-001..RF-003, Diseño §4.1).

El borrado es en dos mundos: cascade en el relacional y ``delete_project`` en
el grafo (exige ``?confirm=true``). El listado incluye un resumen barato
(nº de fuentes, canales y alertas sin ack).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_editor, require_viewer
from ..modelgraph.base import ModelGraph
from ..persistence.db import get_session
from ..persistence.models import (
    Channel,
    DataSource,
    Event,
    ImportRecord,
    Project,
)
from .common import bad_request, get_graph, iso, project_or_404
from .schemas import ProjectCreate, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


def _serialize(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "created_at": iso(project.created_at),
    }


@router.get("")
async def list_projects(
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    """Lista los proyectos con un resumen de tamaño y alertas pendientes."""
    projects = list((await session.execute(select(Project).order_by(Project.name))).scalars())

    # Conteos agregados en una pasada por tabla.
    sources = dict(
        (
            await session.execute(
                select(ImportRecord.project_id, func.count())
                .group_by(ImportRecord.project_id)
            )
        ).all()
    )
    channels = dict(
        (
            await session.execute(
                select(DataSource.project_id, func.count())
                .join(Channel, Channel.datasource_id == DataSource.id)
                .group_by(DataSource.project_id)
            )
        ).all()
    )
    alerts = dict(
        (
            await session.execute(
                select(Event.project_id, func.count())
                .where(Event.acknowledged_at.is_(None))
                .group_by(Event.project_id)
            )
        ).all()
    )

    out = []
    for p in projects:
        data = _serialize(p)
        data["summary"] = {
            "sources": int(sources.get(p.id, 0)),
            "channels": int(channels.get(p.id, 0)),
            "unacknowledged_alerts": int(alerts.get(p.id, 0)),
        }
        out.append(data)
    return out


@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    exists = (
        await session.execute(select(Project).where(Project.name == body.name))
    ).scalar_one_or_none()
    if exists is not None:
        raise bad_request(f"ya existe un proyecto llamado {body.name!r}", "duplicate_name")
    project = Project(name=body.name, description=body.description)
    session.add(project)
    await session.commit()
    return _serialize(project)


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> dict:
    project = await project_or_404(session, project_id)
    return _serialize(project)


@router.put("/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    project = await project_or_404(session, project_id)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await session.commit()
    return _serialize(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    confirm: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    graph: ModelGraph = Depends(get_graph),
    _p: Principal = Depends(require_editor),
) -> dict:
    project = await project_or_404(session, project_id)
    if not confirm:
        raise bad_request(
            "el borrado es irreversible; repite con ?confirm=true",
            "confirmation_required",
        )
    # Grafo primero (si falla, no perdemos el relacional).
    await graph.delete_project(project_id)
    await session.delete(project)  # cascade borra fuentes, canales, eventos...
    await session.commit()
    return {"status": "deleted", "project_id": project_id}
