"""Bundle portable ``.dtproj`` (Apéndice E): exportar e importar un proyecto."""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_editor, require_viewer
from ..bundle import export_project, import_project
from ..persistence.db import get_session
from .common import project_or_404, unprocessable

router = APIRouter(tags=["bundle"])


@router.get("/projects/{project_id}/export")
async def export_bundle(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> StreamingResponse:
    """Descarga la definición del proyecto como ``.dtproj`` (ZIP)."""
    project = await project_or_404(session, project_id)
    data = await export_project(project_id)
    filename = f"{project.name}.dtproj".replace("/", "_")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/projects/import", status_code=201)
async def import_bundle(
    file: UploadFile = File(...),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Importa un ``.dtproj`` como un proyecto NUEVO."""
    data = await file.read()
    try:
        result = await import_project(data)
    except ValueError as exc:
        raise unprocessable(f"bundle inválido: {exc}", "invalid_bundle")
    return result
