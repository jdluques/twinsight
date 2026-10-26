"""Control de acceso por niveles de TwinSight (RF-120..125, Diseño §4.3).

Niveles ordenados: ``viewer`` < ``operator`` < ``editor``.

- ``keys``: claves de instancia (hash argon2id + versión por nivel).
- ``session``: cookie de sesión firmada (itsdangerous).
- ``viewer``: enlaces de solo-lectura por proyecto.
- ``deps``: dependencias FastAPI (``require_level`` y compañía).
"""

from __future__ import annotations

from .deps import (
    LEVELS,
    Principal,
    level_ge,
    optional_principal,
    require_editor,
    require_level,
    require_operator,
    require_viewer,
)
from .keys import (
    KEY_LEVELS,
    current_version,
    ensure_instance_keys,
    rotate_key,
    verify_key,
)
from .session import COOKIE_NAME, issue_cookie, read_cookie
from .viewer import (
    create_viewer_link,
    revoke_viewer_link,
    verify_viewer_token,
)

__all__ = [
    # keys
    "KEY_LEVELS",
    "ensure_instance_keys",
    "verify_key",
    "rotate_key",
    "current_version",
    # session
    "COOKIE_NAME",
    "issue_cookie",
    "read_cookie",
    # viewer
    "create_viewer_link",
    "verify_viewer_token",
    "revoke_viewer_link",
    # deps
    "LEVELS",
    "Principal",
    "level_ge",
    "require_level",
    "optional_principal",
    "require_viewer",
    "require_operator",
    "require_editor",
]
