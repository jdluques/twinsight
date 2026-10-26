"""Dependencias de autorización para FastAPI (RF-120..125, Diseño §4.3).

Resuelve un ``Principal`` a partir de la petición, con dos fuentes:

1. Cookie de sesión firmada (niveles ``operator`` / ``editor``). Su ``version``
   debe coincidir con la versión vigente de la clave del nivel; si no, la
   sesión se considera inválida (clave rotada) y se responde 401.
2. Token viewer (query ``viewer_token`` o cabecera ``X-Viewer-Token``), que
   concede nivel ``viewer`` sobre un proyecto concreto.

``require_level(min_level)`` devuelve una dependencia que exige al menos ese
nivel (401 sin credencial válida, 403 si el nivel es insuficiente).
``optional_principal`` nunca lanza: devuelve el ``Principal`` o ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from .keys import KEY_LEVELS, current_version
from .session import COOKIE_NAME, read_cookie
from .viewer import verify_viewer_token

# Niveles ordenados de menor a mayor privilegio (viewer < operator < editor).
LEVELS: tuple[str, ...] = ("viewer", "operator", "editor")
_RANK = {level: i for i, level in enumerate(LEVELS)}

# Fuentes del token viewer.
VIEWER_QUERY_PARAM = "viewer_token"
VIEWER_HEADER = "X-Viewer-Token"


def level_ge(a: str, b: str) -> bool:
    """¿El nivel ``a`` es mayor o igual (más o igual de privilegiado) que ``b``?"""
    return _RANK[a] >= _RANK[b]


@dataclass
class Principal:
    """Identidad resuelta de una petición."""

    level: str
    project_id: str | None = None


async def _resolve_principal(request: Request, *, strict: bool) -> Principal | None:
    """Resuelve el Principal desde la cookie o el token viewer.

    Con ``strict=True`` una cookie con versión obsoleta (clave rotada) lanza
    401; con ``strict=False`` se trata como ausencia de credencial.
    """
    # 1) Cookie de sesión.
    raw_cookie = request.cookies.get(COOKIE_NAME)
    if raw_cookie:
        data = read_cookie(raw_cookie)
        if data is not None:
            level = data["level"]
            # Los niveles por clave exigen que la versión siga vigente.
            if level in KEY_LEVELS:
                if data["version"] == await current_version(level):
                    return Principal(level=level)
                if strict:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="sesión inválida (clave rotada)",
                    )
                return None
            else:
                # Nivel sin versión de clave (p. ej. viewer): se acepta tal cual.
                return Principal(level=level)

    # 2) Token viewer (query param o cabecera).
    token = request.query_params.get(VIEWER_QUERY_PARAM) or request.headers.get(VIEWER_HEADER)
    if token:
        project_id = await verify_viewer_token(token)
        if project_id is not None:
            return Principal(level="viewer", project_id=project_id)

    return None


def require_level(min_level: str):
    """Fábrica de dependencia FastAPI que exige al menos ``min_level``."""
    if min_level not in _RANK:
        raise ValueError(f"nivel desconocido: {min_level!r}")

    async def dependency(request: Request) -> Principal:
        principal = await _resolve_principal(request, strict=True)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="no autenticado",
            )
        if not level_ge(principal.level, min_level):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="nivel insuficiente",
            )
        return principal

    return dependency


async def optional_principal(request: Request) -> Principal | None:
    """Dependencia que resuelve el Principal sin lanzar (o ``None``)."""
    return await _resolve_principal(request, strict=False)


# Dependencias preconstruidas para los tres niveles.
require_viewer = require_level("viewer")
require_operator = require_level("operator")
require_editor = require_level("editor")


__all__ = [
    "LEVELS",
    "level_ge",
    "Principal",
    "require_level",
    "optional_principal",
    "require_viewer",
    "require_operator",
    "require_editor",
]
