"""Layouts del espacio de trabajo (RF-090): CRUD de disposiciones compartidas.

Simplificación de autorización (por indicación del diseño): crear/editar/borrar
un layout exige nivel ``operator``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_operator, require_viewer
from ..persistence.db import get_session
from ..persistence.models import WorkspaceLayout
from .common import not_found, project_or_404
from .schemas import LayoutCreate, LayoutUpdate

router = APIRouter(tags=["layouts"])


def _layout(row: WorkspaceLayout) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "is_default": row.is_default,
        "layout": row.layout or {},
    }


async def _layout_or_404(session: AsyncSession, layout_id: str) -> WorkspaceLayout:
    row = await session.get(WorkspaceLayout, layout_id)
    if row is None:
        raise not_found(f"layout no encontrado: {layout_id}")
    return row


@router.get("/projects/{project_id}/layouts")
async def list_layouts(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await project_or_404(session, project_id)
    rows = (
        await session.execute(
            select(WorkspaceLayout).where(WorkspaceLayout.project_id == project_id)
        )
    ).scalars()
    return [_layout(r) for r in rows]


@router.post("/projects/{project_id}/layouts", status_code=201)
async def create_layout(
    project_id: str,
    body: LayoutCreate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_operator),
) -> dict:
    await project_or_404(session, project_id)
    row = WorkspaceLayout(
        project_id=project_id,
        name=body.name,
        is_default=body.is_default,
        layout=body.layout,
    )
    session.add(row)
    await session.commit()
    return _layout(row)


@router.put("/layouts/{layout_id}")
async def update_layout(
    layout_id: str,
    body: LayoutUpdate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_operator),
) -> dict:
    row = await _layout_or_404(session, layout_id)
    if body.name is not None:
        row.name = body.name
    if body.is_default is not None:
        row.is_default = body.is_default
    if body.layout is not None:
        row.layout = body.layout
    await session.commit()
    return _layout(row)


@router.delete("/layouts/{layout_id}")
async def delete_layout(
    layout_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_operator),
) -> dict:
    row = await _layout_or_404(session, layout_id)
    await session.delete(row)
    await session.commit()
    return {"status": "deleted", "layout_id": layout_id}
