"""Claves de instancia por nivel (RF-120..125, Diseño §4.3).

Las claves de ``operator`` y ``editor`` NO se guardan en claro: se hashean con
argon2id y el hash vive en la tabla ``config`` (``ConfigEntry``) junto a un
número de versión por nivel. Al rotar una clave se incrementa la versión, lo
que invalida las cookies de sesión emitidas con la versión anterior.

Claves usadas en ``ConfigEntry``:
    access.<nivel>.hash     -> hash argon2id de la clave
    access.<nivel>.version  -> versión (entero como texto, empieza en "1")
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from ..config import get_settings
from ..persistence.db import get_sessionmaker
from ..persistence.models import ConfigEntry

# Niveles cuya autenticación es por clave (viewer no tiene clave de instancia).
KEY_LEVELS: tuple[str, ...] = ("editor", "operator")

# Un único hasher argon2id (los parámetros por defecto de argon2-cffi usan la
# variante argon2id, adecuada para contraseñas).
_HASHER = PasswordHasher()


def _hash_key(key: str) -> str:
    """Hashea una clave con argon2id."""
    return _HASHER.hash(key)


async def ensure_instance_keys() -> None:
    """Crea los hashes iniciales si aún no existen (arranque / demo).

    Hashea ``settings.editor_key`` y ``settings.operator_key`` y deja la
    versión en "1". Idempotente: si ya existen, no toca nada.
    """
    settings = get_settings()
    sm = get_sessionmaker()
    initial = {
        "editor": settings.editor_key,
        "operator": settings.operator_key,
    }
    async with sm() as session:
        changed = False
        for level, raw_key in initial.items():
            existing = await session.get(ConfigEntry, f"access.{level}.hash")
            if existing is None:
                session.add(ConfigEntry(key=f"access.{level}.hash", value=_hash_key(raw_key)))
                session.add(ConfigEntry(key=f"access.{level}.version", value="1"))
                changed = True
        if changed:
            await session.commit()


async def verify_key(key: str) -> tuple[str, int] | None:
    """Verifica ``key`` contra las claves de editor y operator.

    Devuelve ``(nivel, versión)`` del primer nivel que coincida, o ``None`` si
    la clave no corresponde a ningún nivel.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        for level in KEY_LEVELS:
            entry = await session.get(ConfigEntry, f"access.{level}.hash")
            if entry is None:
                continue
            try:
                _HASHER.verify(entry.value, key)
            except VerificationError:
                # VerifyMismatchError e InvalidHash heredan de VerificationError.
                continue
            version = await session.get(ConfigEntry, f"access.{level}.version")
            return level, (int(version.value) if version is not None else 1)
    return None


async def rotate_key(level: str, new_key: str | None = None) -> str:
    """Rota la clave de un nivel (solo ``editor`` u ``operator``).

    Re-hashea la clave (una nueva aleatoria si no se pasa ``new_key``) e
    incrementa ``access.<nivel>.version`` para invalidar las sesiones vigentes.
    Devuelve la clave nueva en claro (única oportunidad de conocerla).
    """
    if level not in KEY_LEVELS:
        raise ValueError(f"nivel no rotable: {level!r} (use {KEY_LEVELS})")
    if new_key is None:
        new_key = secrets.token_urlsafe(24)

    sm = get_sessionmaker()
    async with sm() as session:
        hash_entry = await session.get(ConfigEntry, f"access.{level}.hash")
        version_entry = await session.get(ConfigEntry, f"access.{level}.version")

        new_hash = _hash_key(new_key)
        if hash_entry is None:
            session.add(ConfigEntry(key=f"access.{level}.hash", value=new_hash))
        else:
            hash_entry.value = new_hash

        current = int(version_entry.value) if version_entry is not None else 0
        new_version = current + 1
        if version_entry is None:
            session.add(ConfigEntry(key=f"access.{level}.version", value=str(new_version)))
        else:
            version_entry.value = str(new_version)

        await session.commit()
    return new_key


async def current_version(level: str) -> int:
    """Versión vigente de la clave de un nivel (0 si no existe)."""
    sm = get_sessionmaker()
    async with sm() as session:
        entry = await session.get(ConfigEntry, f"access.{level}.version")
        return int(entry.value) if entry is not None else 0


__all__ = [
    "KEY_LEVELS",
    "ensure_instance_keys",
    "verify_key",
    "rotate_key",
    "current_version",
]
