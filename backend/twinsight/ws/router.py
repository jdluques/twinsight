"""Endpoint WebSocket y puente del bus de TwinSight (Diseño §4.2).

Expone ``/ws/projects/{project_id}``: un canal de solo lectura por el que el
servidor empuja telemetría, cambios de estado, eventos de regla, resaltados de
impacto y estado de las fuentes.

El cliente solo puede enviar mensajes ``{"type":"subscribe", "channels":[...]|"*"}``
para acotar qué telemetría recibe; el resto de tipos se envían siempre. No hay
operaciones de escritura sobre el modelo por este canal.

Autenticación: el *handshake* es de solo lectura. Se acepta un query param
``token`` opcional pero por ahora **no bloquea** (la demo puede ir abierta); se
deja el punto de enganche para validarlo más adelante.

``start_ws_bridge`` conecta el bus interno con el gestor de conexiones: suscribe
handlers que traducen cada evento a su mensaje JSON y hacen *broadcast* al
proyecto. Para ``StateChangeEvent`` y ``RuleEvent`` emite además un
``impact_highlight`` calculado por el servicio de impacto. La app la llama en el
*lifespan*.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..bus import (
    EventBus,
    ReadingEvent,
    RuleEvent,
    SourceStatusEvent,
    StateChangeEvent,
    get_bus,
)
from ..impact import service as impact_service
from ..modelgraph.base import ModelGraph
from .manager import ConnectionManager, get_manager

logger = logging.getLogger(__name__)

router = APIRouter()

__all__ = ["router", "start_ws_bridge"]


@router.websocket("/ws/projects/{project_id}")
async def ws_project(
    websocket: WebSocket, project_id: str, token: str | None = None
) -> None:
    """Conexión de solo lectura a los eventos de un proyecto."""
    # El token es opcional y hoy no bloquea la conexión (canal read-only).
    await websocket.accept()
    manager = get_manager()
    manager.connect(websocket, project_id)
    logger.info("WS conectado (proyecto=%s)", project_id)
    try:
        while True:
            data = await websocket.receive_json()
            if isinstance(data, dict) and data.get("type") == "subscribe":
                manager.set_subscription(websocket, data.get("channels", "*"))
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - robustez ante clientes defectuosos
        logger.exception("error en la conexión WS (proyecto=%s)", project_id)
    finally:
        manager.disconnect(websocket, project_id)
        logger.info("WS desconectado (proyecto=%s)", project_id)


# ----------------------------------------------------------------------------
# Puente bus -> gestor de conexiones
# ----------------------------------------------------------------------------
def start_ws_bridge(
    *,
    bus: EventBus | None = None,
    manager: ConnectionManager | None = None,
    graph: ModelGraph | None = None,
) -> None:
    """Suscribe los handlers que vuelcan los eventos del bus al WebSocket.

    Parámetros inyectables para tests; por defecto usa los singletons del
    proceso. ``graph`` se pasa al servicio de impacto (si es ``None``, el
    servicio resuelve el ``ModelGraph`` configurado de forma perezosa).
    """
    bus = bus or get_bus()
    manager = manager or get_manager()

    async def on_reading(evt: ReadingEvent) -> None:
        message = {
            "type": "telemetry",
            "channel_id": evt.channel_id,
            "ts": evt.ts.isoformat(),
            "value": evt.value,
        }
        await manager.broadcast(evt.project_id, message, channel_id=evt.channel_id)

    async def on_state_change(evt: StateChangeEvent) -> None:
        message = {
            "type": "state_change",
            "machine_id": evt.machine_id,
            "element_id": evt.element_id,
            "from": evt.from_state,
            "to": evt.to_state,
            "guard": evt.guard,
            "ts": evt.ts.isoformat(),
        }
        await manager.broadcast(evt.project_id, message)
        highlight = await impact_service.highlight_for_state_change(evt, graph=graph)
        await manager.broadcast(evt.project_id, highlight)

    async def on_rule_event(evt: RuleEvent) -> None:
        message = {
            "type": "rule_event",
            "event": {
                "id": evt.event_id,
                "origin_kind": "rule",
                "origin_id": evt.rule_id,
                "status": evt.status,
                "severity": evt.severity,
                "ts": evt.ts.isoformat(),
                "payload": evt.payload,
            },
        }
        await manager.broadcast(evt.project_id, message)
        highlight = await impact_service.highlight_for_rule_event(evt, graph=graph)
        await manager.broadcast(evt.project_id, highlight)

    async def on_source_status(evt: SourceStatusEvent) -> None:
        message = {
            "type": "source_status",
            "datasource_id": evt.datasource_id,
            "connected": evt.connected,
        }
        await manager.broadcast(evt.project_id, message)

    bus.subscribe(ReadingEvent, on_reading)
    bus.subscribe(StateChangeEvent, on_state_change)
    bus.subscribe(RuleEvent, on_rule_event)
    bus.subscribe(SourceStatusEvent, on_source_status)
    logger.info("Puente WS conectado al bus de eventos")
