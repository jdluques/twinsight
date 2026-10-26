"""Cookie de sesión firmada con itsdangerous (Diseño §4.3).

La cookie no se cifra pero sí se firma: lleva ``{level, version, iat}`` y un
sello de tiempo que ``URLSafeTimedSerializer`` usa para hacer cumplir el TTL
(``session_ttl_seconds``). El ``version`` permite invalidar la sesión cuando se
rota la clave del nivel (ver ``deps.require_level``).
"""

from __future__ import annotations

import time
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import get_settings

COOKIE_NAME = "twinsight_session"
# Namespace para la firma (evita colisiones con otros usos de secret_key).
_SALT = "twinsight.session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=_SALT)


def issue_cookie(level: str, version: int) -> str:
    """Emite el valor firmado de la cookie para un nivel y versión de clave."""
    payload: dict[str, Any] = {
        "level": level,
        "version": int(version),
        "iat": int(time.time()),
    }
    return _serializer().dumps(payload)


def read_cookie(token: str) -> dict[str, Any] | None:
    """Valida y decodifica la cookie respetando el TTL.

    Devuelve ``{"level", "version"}`` o ``None`` si la firma es inválida, el
    token expiró o su contenido no tiene la forma esperada.
    """
    ttl = get_settings().session_ttl_seconds
    try:
        data = _serializer().loads(token, max_age=ttl)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "level" not in data or "version" not in data:
        return None
    return {"level": data["level"], "version": int(data["version"])}


__all__ = ["COOKIE_NAME", "issue_cookie", "read_cookie"]
