"""Servicio de comportamiento: orquesta STM + reglas sobre el bus (Diseño §2.3).

``BehaviorService`` se suscribe a los ``ReadingEvent`` del bus interno, mantiene
los últimos valores y flags de *stale* por canal, y delega la evaluación al
:class:`~twinsight.behavior.interpreter.StateMachineInterpreter` y al
:class:`~twinsight.behavior.rules.RuleEngine`. Un bucle de *tick* periódico cubre
las condiciones temporales (``stale`` y ``for_seconds`` sin nuevas lecturas).

Al arrancar carga las máquinas de estado del grafo y las reglas del relacional
del proyecto. Persiste cada disparo como un ``Event`` y vuelve a publicar los
``StateChangeEvent``/``RuleEvent`` en el bus para que los consuma la capa de
WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..bus import EventBus, ReadingEvent, RuleEvent, StateChangeEvent, get_bus
from ..modelgraph.base import ModelGraph
from ..persistence.db import get_sessionmaker
from ..persistence.models import Channel, DataSource, Event, RuleDef
from .interpreter import StateMachineInterpreter
from .rules import RuleEngine

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BehaviorService:
    """Orquestador del motor de comportamiento de un proyecto."""

    def __init__(
        self,
        project_id: str,
        modelgraph: ModelGraph,
        *,
        bus: EventBus | None = None,
        sessionmaker: async_sessionmaker | None = None,
        tick_interval: float = 1.0,
    ) -> None:
        self.project_id = project_id
        self.graph = modelgraph
        self.bus = bus or get_bus()
        self._sessionmaker = sessionmaker or get_sessionmaker()
        self.tick_interval = tick_interval

        self._ref_to_channel: dict[str, str] = {}
        self._channel_to_refs: dict[str, set[str]] = {}

        self.interpreter: StateMachineInterpreter | None = None
        self.rules: RuleEngine | None = None
        self._tick_task: asyncio.Task | None = None
        self._running = False

    # -- persistencia de eventos ------------------------------------------
    async def _persist_event(
        self,
        *,
        origin_kind: str,
        origin_id: str,
        status: str,
        severity: str,
        payload: dict,
        ts: datetime,
    ) -> str:
        async with self._sessionmaker() as session:
            event = Event(
                project_id=self.project_id,
                ts=ts,
                origin_kind=origin_kind,
                origin_id=origin_id,
                status=status,
                severity=severity,
                payload=payload,
            )
            session.add(event)
            await session.commit()
            return event.id

    # -- resolución referencia <-> canal ----------------------------------
    async def _build_channel_index(self) -> None:
        """Mapea referencias de guarda/regla a ``channel_id`` por métrica.

        Las guardas y reglas referencian canales por su ``metric_key`` (p.ej.
        ``co2_ppm``). Se construye el índice a partir de los canales del
        proyecto; puede ampliarse con el mapeo del grafo en el futuro.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Channel)
                .join(DataSource, Channel.datasource_id == DataSource.id)
                .where(DataSource.project_id == self.project_id)
            )
            for channel in result.scalars():
                self._ref_to_channel[channel.metric_key] = channel.id
                self._channel_to_refs.setdefault(channel.id, set()).add(
                    channel.metric_key
                )

    async def _load_rules(self) -> list[dict]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(RuleDef).where(
                    RuleDef.project_id == self.project_id,
                    RuleDef.enabled.is_(True),
                )
            )
            raw: list[dict] = []
            for rd in result.scalars():
                definition = dict(rd.definition or {})
                definition.setdefault("name", rd.name)
                definition.setdefault("severity", rd.severity)
                definition["id"] = rd.id
                raw.append(definition)
            return raw

    # -- ciclo de vida -----------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        await self._build_channel_index()

        self.interpreter = StateMachineInterpreter(
            self.project_id,
            self.graph,
            ref_to_channel=lambda ref: self._ref_to_channel.get(ref),
            channel_to_refs=lambda ch: self._channel_to_refs.get(ch, set()),
            publish=self.bus.publish,
            persist=self._persist_event,
        )
        machines = await self.graph.get_state_machines(self.project_id)
        await self.interpreter.load(machines)

        self.rules = RuleEngine(
            self.project_id,
            publish=self.bus.publish,
            persist=self._persist_event,
        )
        for raw in await self._load_rules():
            try:
                self.rules.add_rule(raw, rule_id=raw.get("id"))
            except ValueError as exc:
                logger.warning("regla inválida %s: %s", raw.get("id"), exc)

        self.bus.subscribe(ReadingEvent, self._on_reading)
        self._running = True
        self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info(
            "BehaviorService iniciado (proyecto=%s, máquinas=%d)",
            self.project_id,
            len(machines),
        )

    async def stop(self) -> None:
        self._running = False
        if self._tick_task is not None:
            self._tick_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None

    # -- manejadores -------------------------------------------------------
    async def _on_reading(self, event: ReadingEvent) -> None:
        if event.project_id != self.project_id:
            return
        if self.interpreter is not None:
            await self.interpreter.on_reading(event.channel_id, event.value, event.ts)
        if self.rules is not None:
            await self.rules.on_reading(event.channel_id, event.value, event.ts)

    async def _tick_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.tick_interval)
                now = _now()
                if self.interpreter is not None:
                    await self.interpreter.tick(now)
                    for ch, stale in self._stale_snapshot(now).items():
                        await self.graph.set_channel_stale(self.project_id, ch, stale)
                if self.rules is not None:
                    await self.rules.tick(now)
            except asyncio.CancelledError:  # pragma: no cover - apagado
                raise
            except Exception:  # pragma: no cover - robustez del loop
                logger.exception("fallo en el tick del BehaviorService")

    def _stale_snapshot(self, now: datetime) -> dict[str, bool]:
        """Marca stale un canal si no reporta hace > tick_interval*5 (heurística)."""
        umbral = self.tick_interval * 5
        out: dict[str, bool] = {}
        if self.interpreter is None:
            return out
        for ch, refs in self._channel_to_refs.items():
            last = max(
                (self.interpreter._last_ts.get(r) for r in refs if r in self.interpreter._last_ts),
                default=None,
            )
            if last is not None:
                out[ch] = (now - last).total_seconds() > umbral
        return out


# tipo auxiliar para anotaciones externas
PersistEvent = Callable[..., Awaitable[str]]
