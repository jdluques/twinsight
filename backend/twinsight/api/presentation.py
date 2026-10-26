"""Configuración de presentación por severidad (RF-080, Diseño §4.1).

Un conjunto de estilos ``{severity: style}`` por proyecto. El ``PUT`` reemplaza
la configuración completa del proyecto.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_editor, require_viewer
from ..persistence.db import get_session
from ..persistence.models import PresentationConfig
from .common import project_or_404
from .schemas import PresentationUpdate

router = APIRouter(tags=["presentation"])


def _item(row: PresentationConfig) -> dict:
    return {"id": row.id, "severity": row.severity, "style": row.style or {}}


@router.get("/projects/{project_id}/presentation")
async def get_presentation(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await project_or_404(session, project_id)
    rows = (
        await session.execute(
            select(PresentationConfig).where(
                PresentationConfig.project_id == project_id
            )
        )
    ).scalars()
    return [_item(r) for r in rows]


@router.put("/projects/{project_id}/presentation")
async def set_presentation(
    project_id: str,
    body: PresentationUpdate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> list[dict]:
    await project_or_404(session, project_id)
    # Reemplazo completo: borrar lo anterior y recrear.
    existing = (
        await session.execute(
            select(PresentationConfig).where(
                PresentationConfig.project_id == project_id
            )
        )
    ).scalars()
    for row in existing:
        await session.delete(row)
    created = []
    for item in body.items:
        row = PresentationConfig(
            project_id=project_id, severity=item.severity, style=item.style
        )
        session.add(row)
        created.append(row)
    await session.commit()
    return [_item(r) for r in created]
