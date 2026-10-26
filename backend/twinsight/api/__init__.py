"""Ensamblado de la API REST de TwinSight (Diseño §4.1).

Expone ``api_router``: un único :class:`APIRouter` que agrupa todos los
sub-routers. La app lo monta con ``app.include_router(api_router, prefix="/api/v1")``.

Para que los errores salgan con el formato ``{"detail", "code"}``, la app debe
registrar el manejador de :class:`ApiError`::

    from twinsight.api import ApiError, api_error_handler
    app.add_exception_handler(ApiError, api_error_handler)
"""

from __future__ import annotations

from fastapi import APIRouter

from .common import ApiError, api_error_handler
from . import (
    access,
    auth,
    behavior,
    bundle,
    datasources,
    elements,
    events,
    graph,
    health,
    layouts,
    mappings,
    presentation,
    projects,
    sources,
    telemetry,
)

api_router = APIRouter()

# El orden importa poco (las rutas son disjuntas), pero agrupamos por dominio.
for module in (
    health,
    auth,
    projects,
    sources,
    elements,
    datasources,
    mappings,
    graph,
    behavior,
    presentation,
    events,
    telemetry,
    layouts,
    bundle,
    access,
):
    api_router.include_router(module.router)

__all__ = ["api_router", "ApiError", "api_error_handler"]
