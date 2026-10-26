"""Ingesta de telemetría por MQTT (Diseño §2.4, RF-031..RF-035).

Una conexión por :class:`DataSource` de un proyecto. Cada mensaje se valida
con :func:`twinsight.sensors.parse_payload`; los inválidos se descartan sin
tumbar la conexión (RF-031). Por cada métrica del *Reading* se resuelve (o se
crea, descubrimiento automático RF-033) un :class:`Channel`, se inserta una
fila de :class:`TelemetryReading` (una por métrica, RF-032) y se publica un
:class:`ReadingEvent` en el bus interno. La reconexión es automática con
retroceso exponencial (RF-035).

No bloquea el *event loop*: cada conexión corre como una *task* de asyncio.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field

import aiomqtt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..bus import EventBus, ReadingEvent, SourceStatusEvent, get_bus
from ..config import Settings, get_settings
from ..persistence.db import get_sessionmaker, new_id
from ..persistence.models import Channel, DataSource, TelemetryReading
from ..sensors import ESP32_KEY, SENSORS, PayloadError, Reading, parse_payload

logger = logging.getLogger(__name__)

# Límites del retroceso exponencial ante caídas (segundos), RF-035.
_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 30.0


@dataclass
class _Source:
    """Estado en memoria de una conexión a un DataSource MQTT."""

    id: str
    project_id: str
    host: str
    port: int
    base_topic: str
    username: str | None = None
    password: str | None = None
    connected: bool = False
    task: asyncio.Task | None = field(default=None, repr=False)


async def resolve_channel(
    session: AsyncSession,
    datasource_id: str,
    topic: str,
    metric_key: str,
    sensor_key: str,
) -> Channel:
    """Devuelve el :class:`Channel` de ``(datasource, topic, metric)``.

    Si no existe lo crea (descubrimiento automático RF-033), tomando ``unit``
    y ``label`` del catálogo :data:`SENSORS` cuando el sensor es conocido.
    """
    stmt = select(Channel).where(
        Channel.datasource_id == datasource_id,
        Channel.topic_pattern == topic,
        Channel.metric_key == metric_key,
    )
    channel = (await session.execute(stmt)).scalar_one_or_none()
    if channel is not None:
        return channel

    unit, label = "", ""
    spec = SENSORS.get(sensor_key)
    if spec is not None:
        metric_spec = spec.metric(metric_key)
        if metric_spec is not None:
            unit, label = metric_spec.unit, metric_spec.label

    channel = Channel(
        id=new_id(),
        datasource_id=datasource_id,
        topic_pattern=topic,
        metric_key=metric_key,
        unit=unit,
        label=label,
    )
    session.add(channel)
    await session.flush()  # asegura el id para el ReadingEvent
    logger.info(
        "Canal descubierto: ds=%s topic=%s metric=%s (%s)",
        datasource_id, topic, metric_key, unit or "sin unidad",
    )
    return channel


async def store_reading(
    source: _Source,
    reading: Reading,
    topic: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    bus: EventBus | None = None,
) -> list[ReadingEvent]:
    """Persiste un :class:`Reading` (una fila por métrica) y publica eventos.

    Abre una sesión corta por lote de métricas de un mismo mensaje. Devuelve
    los :class:`ReadingEvent` generados (útil para tests). Si se pasa ``bus``,
    los publica tras el *commit*.
    """
    events: list[ReadingEvent] = []
    async with sessionmaker() as session:
        for metric_key, value in reading.metrics.items():
            channel = await resolve_channel(
                session, source.id, topic, metric_key, reading.sensor
            )
            value = float(value)
            session.add(
                TelemetryReading(channel_id=channel.id, ts=reading.ts, value=value)
            )
            events.append(
                ReadingEvent(
                    project_id=source.project_id,
                    channel_id=channel.id,
                    topic=topic,
                    metric=metric_key,
                    ts=reading.ts,
                    value=value,
                )
            )
        await session.commit()

    if bus is not None:
        for evt in events:
            await bus.publish(evt)
    return events


class IngestManager:
    """Gestiona las conexiones MQTT de ingesta de todos los DataSources.

    Una *task* de asyncio por DataSource; reconexión automática con retroceso
    exponencial. Pensado como singleton de aplicación (ver :func:`get_manager`),
    pero acepta ``sessionmaker``/``bus``/``settings`` inyectados para tests.
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        bus: EventBus | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._bus = bus
        self._settings = settings
        self._sources: dict[str, _Source] = {}

    # --- dependencias perezosas (permiten tests con DB/env temporales) ---
    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker or get_sessionmaker()

    @property
    def bus(self) -> EventBus:
        return self._bus or get_bus()

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    # --- ciclo de vida ---------------------------------------------------
    async def start(self, datasource_id: str) -> None:
        """Arranca (o reusa) la conexión del DataSource indicado."""
        existing = self._sources.get(datasource_id)
        if existing is not None and existing.task is not None and not existing.task.done():
            return  # ya está corriendo

        source = await self._load_source(datasource_id)
        self._sources[datasource_id] = source
        source.task = asyncio.create_task(
            self._run_source(source), name=f"ingest:{datasource_id}"
        )
        logger.info("Ingesta iniciada para datasource %s", datasource_id)

    async def start_all(self) -> None:
        """Arranca todos los DataSources MQTT persistidos."""
        async with self.sessionmaker() as session:
            stmt = select(DataSource.id).where(DataSource.kind == "mqtt")
            ids = list((await session.execute(stmt)).scalars())
        for ds_id in ids:
            await self.start(ds_id)

    async def stop(self, datasource_id: str) -> None:
        """Detiene la conexión del DataSource y libera su task."""
        source = self._sources.pop(datasource_id, None)
        if source is None:
            return
        if source.task is not None:
            source.task.cancel()
            with suppress(asyncio.CancelledError):
                await source.task
        await self._set_connected(source, False)
        logger.info("Ingesta detenida para datasource %s", datasource_id)

    async def stop_all(self) -> None:
        """Detiene todas las conexiones."""
        for datasource_id in list(self._sources):
            await self.stop(datasource_id)

    async def health(self) -> dict:
        """Estado de conexión por datasource (para el endpoint /health)."""
        return {
            sid: {
                "connected": s.connected,
                "running": bool(s.task is not None and not s.task.done()),
                "project_id": s.project_id,
            }
            for sid, s in self._sources.items()
        }

    # --- internos --------------------------------------------------------
    async def _load_source(self, datasource_id: str) -> _Source:
        async with self.sessionmaker() as session:
            ds = await session.get(DataSource, datasource_id)
            if ds is None:
                raise ValueError(f"DataSource desconocido: {datasource_id!r}")
            cfg = ds.config or {}
            return _Source(
                id=ds.id,
                project_id=ds.project_id,
                host=str(cfg.get("host", self.settings.mqtt_host)),
                port=int(cfg.get("port", self.settings.mqtt_port)),
                base_topic=str(cfg.get("base_topic", self.settings.mqtt_base_topic)),
                username=cfg.get("username"),
                password=cfg.get("password"),
            )

    async def _set_connected(self, source: _Source, value: bool) -> None:
        """Actualiza el estado y publica SourceStatusEvent solo si cambia."""
        if source.connected == value:
            return
        source.connected = value
        await self.bus.publish(
            SourceStatusEvent(
                project_id=source.project_id,
                datasource_id=source.id,
                connected=value,
            )
        )

    async def _run_source(self, source: _Source) -> None:
        """Bucle de conexión con reconexión y retroceso exponencial (RF-035)."""
        backoff = _BACKOFF_MIN
        sub_topic = f"{source.base_topic}/#"
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=source.host,
                    port=source.port,
                    username=source.username,
                    password=source.password,
                ) as client:
                    await client.subscribe(sub_topic, qos=1)
                    await self._set_connected(source, True)
                    backoff = _BACKOFF_MIN  # conexión sana: reinicia el retroceso
                    async for message in client.messages:
                        await self._on_message(source, message)
            except asyncio.CancelledError:
                raise
            except aiomqtt.MqttError as exc:
                logger.warning(
                    "Conexión MQTT caída (ds=%s %s:%s): %s",
                    source.id, source.host, source.port, exc,
                )
            except Exception:  # noqa: BLE001 - no debe tumbar la task
                logger.exception("Error inesperado en la ingesta ds=%s", source.id)

            await self._set_connected(source, False)
            logger.info("Reintentando ds=%s en %.0fs", source.id, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _on_message(self, source: _Source, message: aiomqtt.Message) -> None:
        topic = str(message.topic)
        try:
            reading = parse_payload(topic, message.payload, source.base_topic)
        except PayloadError as exc:
            # RF-031: descartar mensajes inválidos sin caer.
            logger.warning("Payload MQTT descartado (%s): %s", topic, exc)
            return
        if reading.sensor == ESP32_KEY:
            return  # heartbeat del ESP32, no es telemetría
        await store_reading(source, reading, topic, self.sessionmaker, self.bus)


_manager: IngestManager | None = None


def get_manager() -> IngestManager:
    """Singleton de aplicación del gestor de ingesta."""
    global _manager
    if _manager is None:
        _manager = IngestManager()
    return _manager


async def health() -> dict:
    """Helper de conveniencia para /health sobre el gestor singleton."""
    return await get_manager().health()
