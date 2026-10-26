"""Autenticación por clave de instancia y sesión por cookie (Diseño §4.3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..access import (
    COOKIE_NAME,
    Principal,
    issue_cookie,
    require_editor,
    rotate_key,
    verify_key,
)
from ..access.keys import KEY_LEVELS
from ..config import get_settings
from .common import ApiError, bad_request
from .schemas import LoginIn, RotateIn

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginIn) -> JSONResponse:
    """Verifica la clave y, si es válida, emite la cookie de sesión firmada."""
    result = await verify_key(body.key)
    if result is None:
        raise ApiError(401, "invalid_key", "clave no válida")
    level, version = result
    token = issue_cookie(level, version)
    response = JSONResponse(content={"level": level})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=get_settings().session_ttl_seconds,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/rotate")
async def rotate(
    body: RotateIn, _principal: Principal = Depends(require_editor)
) -> dict:
    """Rota la clave de un nivel (``editor``/``operator``) e invalida sesiones."""
    if body.level not in KEY_LEVELS:
        raise bad_request(
            f"nivel no rotable: {body.level!r} (use {list(KEY_LEVELS)})",
            "bad_level",
        )
    new_key = await rotate_key(body.level)
    return {"level": body.level, "key": new_key}


@router.post("/logout")
async def logout() -> JSONResponse:
    """Cierra la sesión borrando la cookie."""
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie(COOKIE_NAME)
    return response
