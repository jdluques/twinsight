"""Capa WebSocket de TwinSight (Diseño §4.2).

Reúne el gestor de conexiones, el endpoint ``/ws/projects/{id}`` y el puente que
vuelca los eventos del bus interno a los clientes conectados.
"""

from __future__ import annotations

from .manager import ConnectionManager, get_manager, reset_manager
from .router import router, start_ws_bridge

__all__ = [
    "ConnectionManager",
    "get_manager",
    "reset_manager",
    "router",
    "start_ws_bridge",
]
