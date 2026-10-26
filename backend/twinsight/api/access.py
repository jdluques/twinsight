"""Enlaces de solo-lectura por proyecto (RF-121, Diseño §4.3).

El token en claro solo se devuelve al crear el enlace; en la base queda su hash.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, create_viewer_link, require_editor, require_viewer, revoke_viewer_link
from ..persistence.db import get_session
from ..persistence.models import ViewerLink
from .common import iso, not_found, project_or_404

router = APIRouter(tags=["viewer-links"])


@router.get("/projects/{project_id}/viewer-links")
async def list_viewer_links(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    """Lista los enlaces vigentes (no se expone el token, solo metadatos)."""
    await project_or_404(session, project_id)
    rows = (
        await session.execute(
            select(ViewerLink).where(
                ViewerLink.project_id == project_id,
                ViewerLink.revoked_at.is_(None),
            )
        )
    ).scalars()
    return [
        {"id": r.id, "project_id": r.project_id, "created_at": iso(r.created_at)}
        for r in rows
    ]


@router.post("/projects/{project_id}/viewer-links", status_code=201)
async def create_link(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Crea un enlace viewer y devuelve el token en claro (única vez)."""
    await project_or_404(session, project_id)
    token = await create_viewer_link(project_id)
    # Recuperar el id recién creado (el más reciente de este proyecto).
    link = (
        await session.execute(
            select(ViewerLink)
            .where(ViewerLink.project_id == project_id)
            .order_by(ViewerLink.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "id": link.id if link else None,
        "project_id": project_id,
        "token": token,
        "viewer_token": token,
    }


@router.delete("/viewer-links/{link_id}")
async def delete_link(
    link_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    link = await session.get(ViewerLink, link_id)
    if link is None:
        raise not_found(f"enlace viewer no encontrado: {link_id}")
    await revoke_viewer_link(link_id)
    return {"status": "revoked", "link_id": link_id}
