"""Gestor de conexiones WebSocket por proyecto (Diseño §4.2).

``ConnectionManager`` agrupa las conexiones abiertas por ``project_id`` y guarda,
por cada conexión, su suscripción de canales. El *fan-out* envía cada mensaje a
todas las conexiones del proyecto; para la telemetría respeta el filtro de
canales de cada cliente.

Suscripción de canales:
- ``"*"`` (valor por defecto al conectar): recibe la telemetría de todos los
  canales.
- un conjunto de ``channel_id``: recibe solo la telemetría de esos canales.

Los mensajes que no son telemetría (``state_change``, ``rule_event``,
``impact_highlight``, ``source_status``) se envían siempre, sin filtrar.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Centinela de "todos los canales".
ALL_CHANNELS = "*"


class WebSocketLike(Protocol):
    """Mínimo que el gestor necesita de un WebSocket (facilita los fakes)."""

    async def send_json(self, data: Any) -> None: ...


class ConnectionManager:
    """Registra WebSockets por proyecto y hace *fan-out* de mensajes."""

    def __init__(self) -> None:
        # project_id -> conexiones abiertas de ese proyecto.
        self._conns: dict[str, set[WebSocketLike]] = defaultdict(set)
        # conexión -> suscripción ("*" o conjunto de channel_id).
        self._subs: dict[WebSocketLike, set[str] | str] = {}

    # -- ciclo de vida de una conexión ------------------------------------
    def connect(self, ws: WebSocketLike, project_id: str) -> None:
        """Registra una conexión ya aceptada bajo su proyecto.

        Por defecto queda suscrita a todos los canales (``"*"``); el cliente
        puede acotarla luego con un mensaje ``subscribe``.
        """
        self._conns[project_id].add(ws)
        self._subs[ws] = ALL_CHANNELS

    def disconnect(self, ws: WebSocketLike, project_id: str | None = None) -> None:
        """Elimina una conexión del gestor de forma idempotente."""
        self._subs.pop(ws, None)
        if project_id is not None:
            conns = self._conns.get(project_id)
            if conns is not None:
                conns.discard(ws)
                if not conns:
                    self._conns.pop(project_id, None)
            return
        # Sin project_id: barrido por si no lo conocemos.
        for pid in list(self._conns):
            self._conns[pid].discard(ws)
            if not self._conns[pid]:
                self._conns.pop(pid, None)

    # -- suscripción -------------------------------------------------------
    def set_subscription(self, ws: WebSocketLike, channels: list[str] | str) -> None:
        """Fija la suscripción de telemetría de una conexión.

        ``channels`` es ``"*"`` (todos) o una lista de ``channel_id``.
        """
        if channels == ALL_CHANNELS:
            self._subs[ws] = ALL_CHANNELS
        elif isinstance(channels, (list, tuple, set)):
            self._subs[ws] = {str(c) for c in channels}
        else:
            # Entrada inesperada: la tratamos como "ningún canal".
            self._subs[ws] = set()

    def subscription(self, ws: WebSocketLike) -> set[str] | str:
        """Devuelve la suscripción actual de una conexión (para inspección)."""
        return self._subs.get(ws, ALL_CHANNELS)

    def _wants(self, ws: WebSocketLike, channel_id: str | None) -> bool:
        """¿Debe recibir esta conexión un mensaje de telemetría de ese canal?"""
        if channel_id is None:
            return True  # no es telemetría filtrable
        sub = self._subs.get(ws, ALL_CHANNELS)
        if sub == ALL_CHANNELS:
            return True
        return channel_id in sub

    # -- fan-out -----------------------------------------------------------
    async def broadcast(
        self, project_id: str, message: dict, channel_id: str | None = None
    ) -> None:
        """Envía ``message`` a las conexiones del proyecto.

        Si ``channel_id`` viene dado (telemetría), omite las conexiones que no
        tengan ese canal suscrito (salvo que estén en ``"*"``). Las conexiones
        que fallan al enviar se descartan limpiamente.
        """
        targets = [
            ws for ws in list(self._conns.get(project_id, ()))
            if self._wants(ws, channel_id)
        ]
        if not targets:
            return

        results = await asyncio.gather(
            *(ws.send_json(message) for ws in targets),
            return_exceptions=True,
        )
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                logger.debug("conexión caída durante broadcast: %r", result)
                self.disconnect(ws, project_id)


_manager: ConnectionManager | None = None


def get_manager() -> ConnectionManager:
    """Devuelve el ``ConnectionManager`` singleton del proceso."""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager


def reset_manager() -> None:
    """Olvida el singleton (útil en tests)."""
    global _manager
    _manager = None
