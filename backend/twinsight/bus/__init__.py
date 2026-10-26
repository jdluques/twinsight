"""Bus de eventos interno (pub/sub sobre asyncio, Diseño §2.3).

Desacopla productores (ingest, behavior) de consumidores (behavior, ws)
sin colas externas (RNF-011).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ReadingEvent:
    project_id: str
    channel_id: str
    topic: str
    metric: str
    ts: datetime
    value: float


@dataclass
class StateChangeEvent:
    project_id: str
    machine_id: str
    element_id: str
    from_state: str
    to_state: str
    guard: str
    ts: datetime


@dataclass
class RuleEvent:
    project_id: str
    event_id: str
    rule_id: str
    status: str  # fired | cleared
    severity: str
    ts: datetime
    payload: dict = field(default_factory=dict)


@dataclass
class SourceStatusEvent:
    project_id: str
    datasource_id: str
    connected: bool


BusEvent = ReadingEvent | StateChangeEvent | RuleEvent | SourceStatusEvent
Handler = Callable[[BusEvent], Awaitable[None]]


class EventBus:
    """Bus pub/sub mínimo por tipo de evento."""

    def __init__(self) -> None:
        self._subs: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Handler) -> None:
        self._subs[event_type].append(handler)

    async def publish(self, event: BusEvent) -> None:
        handlers = list(self._subs.get(type(event), []))
        if handlers:
            await asyncio.gather(*(h(event) for h in handlers), return_exceptions=True)


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
