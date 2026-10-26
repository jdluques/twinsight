"""Tests de la capa WebSocket: gestor de conexiones y puente del bus.

Usan *fakes* en memoria; no requieren Neo4j, broker MQTT ni un servidor real
salvo el test opcional con ``TestClient`` (que solo necesita FastAPI).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from twinsight.bus import EventBus, RuleEvent, StateChangeEvent
from twinsight.ws.manager import ALL_CHANNELS, ConnectionManager
from twinsight.ws.router import start_ws_bridge

UTC = timezone.utc


def _ts() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class FakeWebSocket:
    """WebSocket mínimo con una cola async de mensajes enviados."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send_json(self, data) -> None:
        if self.closed:
            raise RuntimeError("websocket cerrado")
        await self.queue.put(data)

    def drain(self) -> list[dict]:
        """Devuelve todos los mensajes encolados sin bloquear."""
        out = []
        while not self.queue.empty():
            out.append(self.queue.get_nowait())
        return out


# ==========================================================================
# ConnectionManager
# ==========================================================================
async def test_broadcast_llega_solo_a_conexiones_del_proyecto():
    mgr = ConnectionManager()
    a = FakeWebSocket()
    b = FakeWebSocket()
    otro = FakeWebSocket()
    mgr.connect(a, "p1")
    mgr.connect(b, "p1")
    mgr.connect(otro, "p2")

    await mgr.broadcast("p1", {"type": "state_change", "x": 1})

    assert a.drain() == [{"type": "state_change", "x": 1}]
    assert b.drain() == [{"type": "state_change", "x": 1}]
    assert otro.drain() == []  # otro proyecto no recibe


async def test_suscripcion_por_canal_filtra_telemetry():
    mgr = ConnectionManager()
    a = FakeWebSocket()  # suscrito a co2
    b = FakeWebSocket()  # suscrito a "*" (por defecto)
    mgr.connect(a, "p1")
    mgr.connect(b, "p1")
    mgr.set_subscription(a, ["co2"])

    msg_co2 = {"type": "telemetry", "channel_id": "co2", "value": 1}
    msg_temp = {"type": "telemetry", "channel_id": "temp", "value": 2}
    await mgr.broadcast("p1", msg_co2, channel_id="co2")
    await mgr.broadcast("p1", msg_temp, channel_id="temp")

    # 'a' solo quiere co2; 'b' recibe ambos.
    assert a.drain() == [msg_co2]
    assert b.drain() == [msg_co2, msg_temp]


async def test_subscribe_estrella_recibe_todo():
    mgr = ConnectionManager()
    a = FakeWebSocket()
    mgr.connect(a, "p1")
    mgr.set_subscription(a, ["co2"])
    mgr.set_subscription(a, ALL_CHANNELS)  # vuelve a "*"
    assert mgr.subscription(a) == ALL_CHANNELS

    await mgr.broadcast("p1", {"channel_id": "temp"}, channel_id="temp")
    assert a.drain() == [{"channel_id": "temp"}]


async def test_no_telemetria_se_envia_aunque_no_este_suscrito_al_canal():
    mgr = ConnectionManager()
    a = FakeWebSocket()
    mgr.connect(a, "p1")
    mgr.set_subscription(a, ["co2"])  # solo co2 para telemetría

    # Un state_change (sin channel_id) siempre llega.
    await mgr.broadcast("p1", {"type": "state_change"})
    assert a.drain() == [{"type": "state_change"}]


async def test_disconnect_es_idempotente_y_limpia():
    mgr = ConnectionManager()
    a = FakeWebSocket()
    mgr.connect(a, "p1")
    mgr.disconnect(a, "p1")
    mgr.disconnect(a, "p1")  # no revienta
    await mgr.broadcast("p1", {"x": 1})  # ya no hay nadie
    assert a.drain() == []


async def test_conexion_caida_se_descarta_en_broadcast():
    mgr = ConnectionManager()
    bueno = FakeWebSocket()
    malo = FakeWebSocket()
    malo.closed = True  # fallará al enviar
    mgr.connect(bueno, "p1")
    mgr.connect(malo, "p1")

    await mgr.broadcast("p1", {"x": 1})
    assert bueno.drain() == [{"x": 1}]
    # el 'malo' quedó descartado del gestor
    await mgr.broadcast("p1", {"x": 2})
    assert bueno.drain() == [{"x": 2}]
    assert mgr.subscription(malo) == ALL_CHANNELS  # ya no lo conoce -> default


# ==========================================================================
# Puente del bus (start_ws_bridge)
# ==========================================================================
class FakeModelGraph:
    """ModelGraph mínimo: impact() devuelve un conjunto fijo según la clave."""

    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def impact(self, project_id, *, element_id=None, channel_id=None, max_depth=4):
        self.calls.append(
            {"project_id": project_id, "element_id": element_id, "channel_id": channel_id}
        )
        return self.result


def _impact_result():
    return {
        "affected": [{"id": "el1", "label": "Element", "name": "Bomba"}],
        "paths": [["HAS_STM"]],
        "elements": [{"id": "el1", "label": "Element", "name": "Bomba"}],
        "states": [{"id": "s-alarma", "label": "State", "name": "Alarma"}],
        "visual_objects": [{"id": "vo-1", "label": "VisualObject", "name": None}],
        "channels": [{"id": "ch-co2", "label": "Channel", "name": "co2"}],
        "transitions": [],
        "state_machines": [],
    }


async def test_bridge_state_change_emite_state_change_e_impact_highlight():
    bus = EventBus()
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    mgr.connect(ws, "p1")
    graph = FakeModelGraph(_impact_result())

    start_ws_bridge(bus=bus, manager=mgr, graph=graph)

    await bus.publish(
        StateChangeEvent(
            project_id="p1",
            machine_id="m1",
            element_id="el1",
            from_state="nominal",
            to_state="alarma",
            guard="co2_ppm > 2000",
            ts=_ts(),
        )
    )

    msgs = ws.drain()
    tipos = [m["type"] for m in msgs]
    assert tipos == ["state_change", "impact_highlight"]

    sc = msgs[0]
    assert sc["machine_id"] == "m1"
    assert sc["element_id"] == "el1"
    assert sc["from"] == "nominal" and sc["to"] == "alarma"
    assert sc["guard"] == "co2_ppm > 2000"
    assert sc["ts"] == _ts().isoformat()

    hl = msgs[1]
    assert hl["cause"] == {"kind": "state_change", "ref": "el1"}
    assert hl["elements"] == ["el1"]
    assert hl["states"] == ["s-alarma"]
    assert hl["visual_objects"] == ["vo-1"]
    assert hl["channels"] == ["ch-co2"]
    # impact() se resolvió por element_id
    assert graph.calls == [{"project_id": "p1", "element_id": "el1", "channel_id": None}]


async def test_bridge_rule_event_emite_rule_event_e_impact_highlight():
    bus = EventBus()
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    mgr.connect(ws, "p1")
    graph = FakeModelGraph(_impact_result())

    start_ws_bridge(bus=bus, manager=mgr, graph=graph)

    await bus.publish(
        RuleEvent(
            project_id="p1",
            event_id="ev-7",
            rule_id="r1",
            status="fired",
            severity="alarm",
            ts=_ts(),
            payload={"rule": "co2 alto", "values": {"ch-co2": 2100}},
        )
    )

    msgs = ws.drain()
    assert [m["type"] for m in msgs] == ["rule_event", "impact_highlight"]

    re_msg = msgs[0]["event"]
    assert re_msg["id"] == "ev-7"
    assert re_msg["origin_kind"] == "rule"
    assert re_msg["origin_id"] == "r1"
    assert re_msg["status"] == "fired"
    assert re_msg["severity"] == "alarm"

    hl = msgs[1]
    assert hl["cause"] == {"kind": "rule_event", "ref": "r1"}
    assert hl["severity"] == "alarm"
    assert hl["elements"] == ["el1"]
    # impact() se resolvió por el canal del payload
    assert graph.calls == [
        {"project_id": "p1", "element_id": None, "channel_id": "ch-co2"}
    ]


async def test_bridge_telemetry_respeta_filtro_de_canal():
    from twinsight.bus import ReadingEvent

    bus = EventBus()
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    mgr.connect(ws, "p1")
    mgr.set_subscription(ws, ["co2"])
    start_ws_bridge(bus=bus, manager=mgr, graph=FakeModelGraph(_impact_result()))

    await bus.publish(
        ReadingEvent(
            project_id="p1", channel_id="temp", topic="t", metric="m",
            ts=_ts(), value=42.0,
        )
    )
    assert ws.drain() == []  # no suscrito a temp

    await bus.publish(
        ReadingEvent(
            project_id="p1", channel_id="co2", topic="t", metric="m",
            ts=_ts(), value=1500.0,
        )
    )
    msgs = ws.drain()
    assert len(msgs) == 1
    assert msgs[0] == {
        "type": "telemetry",
        "channel_id": "co2",
        "ts": _ts().isoformat(),
        "value": 1500.0,
    }


async def test_highlight_sin_impacto_devuelve_listas_vacias():
    from twinsight.impact.service import highlight_for_state_change

    evt = StateChangeEvent(
        project_id="p1", machine_id="m1", element_id="",
        from_state="a", to_state="b", guard="", ts=_ts(),
    )
    hl = await highlight_for_state_change(evt, graph=FakeModelGraph(_impact_result()))
    assert hl["elements"] == [] and hl["channels"] == []
    assert hl["cause"]["kind"] == "state_change"


# ==========================================================================
# Opcional: endpoint real con TestClient de FastAPI
# ==========================================================================
def test_endpoint_websocket_con_testclient():
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from twinsight.ws import get_manager, reset_manager, router

    reset_manager()
    app = FastAPI()
    app.include_router(router)

    bus = EventBus()
    mgr = get_manager()
    start_ws_bridge(bus=bus, manager=mgr, graph=FakeModelGraph(_impact_result()))

    client = TestClient(app)
    with client.websocket_connect("/ws/projects/p1") as sock:
        async def _publish():
            from twinsight.bus import ReadingEvent

            await bus.publish(
                ReadingEvent(
                    project_id="p1", channel_id="co2", topic="t", metric="m",
                    ts=_ts(), value=1234.0,
                )
            )

        # El TestClient corre la app en su propio loop; publicamos en ese loop
        # vía el portal para que el envío llegue al WebSocket real del servidor.
        sock.portal.call(_publish)

        data = sock.receive_json()
        assert data["type"] == "telemetry"
        assert data["channel_id"] == "co2"
        assert data["value"] == 1234.0

    reset_manager()
