"""Enlaces de solo-lectura (viewer) por proyecto (RF-121, Diseño §4.3).

Un enlace viewer es un token aleatorio de alta entropía que concede nivel
``viewer`` sobre un proyecto concreto. En la base solo se guarda su hash
(sha256), de modo que un volcado de ``viewer_links`` no revela los tokens. El
hash sha256 permite además buscar el enlace por token sin iterar todos.
"""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select

from ..persistence.db import get_sessionmaker, new_id
from ..persistence.models import ViewerLink


def _hash_token(token: str) -> str:
    """Hash estable y buscable del token (sha256 hex)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_viewer_link(project_id: str) -> str:
    """Crea un enlace viewer para el proyecto y devuelve el token en claro.

    El token solo se conoce aquí; en la base queda su hash.
    """
    raw_token = secrets.token_urlsafe(32)
    sm = get_sessionmaker()
    async with sm() as session:
        link = ViewerLink(
            id=new_id(),
            project_id=project_id,
            token_hash=_hash_token(raw_token),
        )
        session.add(link)
        await session.commit()
    return raw_token


async def verify_viewer_token(token: str) -> str | None:
    """Devuelve el ``project_id`` del enlace no revocado, o ``None``."""
    if not token:
        return None
    token_hash = _hash_token(token)
    sm = get_sessionmaker()
    async with sm() as session:
        stmt = select(ViewerLink).where(
            ViewerLink.token_hash == token_hash,
            ViewerLink.revoked_at.is_(None),
        )
        link = (await session.execute(stmt)).scalar_one_or_none()
        return link.project_id if link is not None else None


async def revoke_viewer_link(link_id: str) -> None:
    """Marca un enlace como revocado (idempotente)."""
    from datetime import datetime, timezone

    sm = get_sessionmaker()
    async with sm() as session:
        link = await session.get(ViewerLink, link_id)
        if link is not None and link.revoked_at is None:
            link.revoked_at = datetime.now(timezone.utc)
            await session.commit()


__all__ = ["create_viewer_link", "verify_viewer_token", "revoke_viewer_link"]
