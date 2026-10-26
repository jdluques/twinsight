"""Tests de la ingesta MQTT (twinsight.ingest).

Unitarios: resolución/creación de canales y persistencia dado un Reading, más
el descarte de payloads inválidos. Usan una base SQLite temporal (archivo),
porque ``:memory:`` no comparte datos entre conexiones del pool.

Integración (``-m integration``): requiere un Mosquitto en localhost:1883.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

import pytest

# La URL de la BD debe fijarse ANTES de importar la capa de persistencia.
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
_TMP_DB.close()
os.environ["TWINSIGHT_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB.name}"

from sqlalchemy import func, select  # noqa: E402

from twinsight.bus import EventBus, ReadingEvent  # noqa: E402
from twinsight.config import get_settings  # noqa: E402
from twinsight.ingest import IngestManager, store_reading  # noqa: E402
from twinsight.ingest.mqtt import _Source  # noqa: E402
from twinsight.persistence import db as db_mod  # noqa: E402
from twinsight.persistence.db import get_sessionmaker, init_db, new_id  # noqa: E402
from twinsight.persistence.models import (  # noqa: E402
    Channel,
    DataSource,
    Project,
    TelemetryReading,
)
from twinsight.sensors import Reading  # noqa: E402


@pytest.fixture
async def sessionmaker():
    """Reconfigura el engine a la BD temporal y recrea el esquema (aislamiento)."""
    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._sessionmaker = None
    engine = db_mod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(db_mod.Base.metadata.drop_all)
    await init_db()
    yield get_sessionmaker()
    await engine.dispose()


async def _make_datasource(sm, *, base_topic: str = "gemelo", host: str = "localhost",
                           port: int = 1883) -> _Source:
    """Crea un Project + DataSource en la BD y devuelve el _Source asociado."""
    async with sm() as session:
        project = Project(id=new_id(), name=f"proj-{uuid.uuid4().hex[:8]}")
        ds = DataSource(
            id=new_id(),
            project_id=project.id,
            kind="mqtt",
            config={"host": host, "port": port, "base_topic": base_topic},
        )
        session.add_all([project, ds])
        await session.commit()
        return _Source(
            id=ds.id, project_id=project.id, host=host, port=port, base_topic=base_topic
        )


# --------------------------------------------------------------------------
# Unitarios (sin broker)
# --------------------------------------------------------------------------
async def test_resolve_create_channel_and_persist(sessionmaker):
    sm = sessionmaker
    source = await _make_datasource(sm)
    topic = "gemelo/aula-A/bme280"
    ts = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    reading = Reading(
        aula="aula-A", sensor="bme280", ts=ts,
        metrics={"temp_c": 22.5, "hum_pct": 48.0, "pres_hpa": 1013.2},
    )

    bus = EventBus()
    events = await store_reading(source, reading, topic, sm, bus)

    # Una fila por métrica (RF-032) y un evento por métrica.
    assert len(events) == 3
    assert {e.metric for e in events} == {"temp_c", "hum_pct", "pres_hpa"}
    assert all(e.project_id == source.project_id for e in events)
    assert all(e.topic == topic for e in events)

    async with sm() as session:
        channels = (await session.execute(select(Channel))).scalars().all()
        assert len(channels) == 3
        by_metric = {c.metric_key: c for c in channels}
        # Descubrimiento automático con unit/label del catálogo (RF-033).
        assert by_metric["temp_c"].unit == "°C"
        assert by_metric["temp_c"].label == "Temperatura"
        assert by_metric["hum_pct"].unit == "%"

        n_readings = (
            await session.execute(select(func.count()).select_from(TelemetryReading))
        ).scalar_one()
        assert n_readings == 3

        # El channel_id del evento coincide con el canal persistido.
        assert {e.channel_id for e in events} == {c.id for c in channels}

    # Segundo mensaje mismo topic/métricas: reutiliza canales (no crea nuevos).
    reading2 = Reading(
        aula="aula-A", sensor="bme280",
        ts=datetime(2026, 10, 26, 12, 1, tzinfo=timezone.utc),
        metrics={"temp_c": 23.0},
    )
    await store_reading(source, reading2, topic, sm, bus)
    async with sm() as session:
        assert (await session.execute(select(func.count()).select_from(Channel))).scalar_one() == 3


async def test_invalid_payload_is_discarded(sessionmaker):
    sm = sessionmaker
    source = await _make_datasource(sm)
    mgr = IngestManager(sessionmaker=sm, bus=EventBus())

    class _FakeMessage:
        topic = "gemelo/aula-A/bme280"
        payload = b"esto no es json"

    # No debe lanzar excepción (RF-031) ni persistir nada.
    await mgr._on_message(source, _FakeMessage())

    async with sm() as session:
        n = (await session.execute(select(func.count()).select_from(TelemetryReading))).scalar_one()
        assert n == 0


async def test_esp32_heartbeat_ignored(sessionmaker):
    sm = sessionmaker
    source = await _make_datasource(sm)
    mgr = IngestManager(sessionmaker=sm, bus=EventBus())

    class _FakeMessage:
        topic = "gemelo/aula-A/esp32"
        payload = json.dumps({"metrics": {"uptime_s": 10}}).encode()

    await mgr._on_message(source, _FakeMessage())
    async with sm() as session:
        n = (await session.execute(select(func.count()).select_from(TelemetryReading))).scalar_one()
        assert n == 0


# --------------------------------------------------------------------------
# Integración (requiere broker en localhost:1883)
# --------------------------------------------------------------------------
@pytest.mark.integration
async def test_ingest_end_to_end_with_broker(sessionmaker):
    import asyncio

    import paho.mqtt.publish as paho_publish

    sm = sessionmaker
    # Base topic único para aislar de mensajes retenidos de otras corridas.
    base_topic = f"gemelo-it-{uuid.uuid4().hex[:8]}"
    topic = f"{base_topic}/aula-A/bme280"

    async with sm() as session:
        project = Project(id=new_id(), name=f"proj-{uuid.uuid4().hex[:8]}")
        ds = DataSource(
            id=new_id(), project_id=project.id, kind="mqtt",
            config={"host": "localhost", "port": 1883, "base_topic": base_topic},
        )
        session.add_all([project, ds])
        await session.commit()
        ds_id = ds.id

    bus = EventBus()
    received: list[ReadingEvent] = []

    async def _capture(evt):
        received.append(evt)

    bus.subscribe(ReadingEvent, _capture)

    mgr = IngestManager(sessionmaker=sm, bus=bus)
    await mgr.start(ds_id)
    try:
        # Espera a que la suscripción esté activa (QoS 1).
        for _ in range(50):
            health = await mgr.health()
            if health.get(ds_id, {}).get("connected"):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.skip("No se pudo conectar al broker MQTT en localhost:1883")

        payload = json.dumps({"metrics": {"temp_c": 24.0, "hum_pct": 50.0}})
        await asyncio.to_thread(
            paho_publish.single, topic, payload=payload,
            hostname="localhost", port=1883, qos=1,
        )

        for _ in range(50):
            if len(received) >= 2:
                break
            await asyncio.sleep(0.1)
    finally:
        await mgr.stop_all()

    assert {e.metric for e in received} == {"temp_c", "hum_pct"}
    assert all(e.topic == topic for e in received)

    async with sm() as session:
        rows = (await session.execute(select(TelemetryReading))).scalars().all()
        assert len(rows) == 2
        values = sorted(r.value for r in rows)
        assert values == [24.0, 50.0]
