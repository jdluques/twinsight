"""Intérprete de máquinas de estado (STM) (RF-050, RF-052, RF-102).

Mantiene en memoria el estado activo de cada máquina y lo sincroniza con el
grafo canónico vía :class:`~twinsight.modelgraph.base.ModelGraph`. Cuando llega
una lectura de un canal, resuelve qué referencias de guarda corresponden a ese
canal y reevalúa **solo** las transiciones salientes del estado activo de las
máquinas afectadas. Si una guarda se cumple, ejecuta la transición: actualiza el
estado activo, emite ``StateChangeEvent`` al bus, persiste un ``Event`` y ajusta
la severidad del elemento dueño.

Estructura esperada de una máquina (la que devuelve ``ModelGraph``)::

    {
      "id": "<machine_id>",
      "element_id": "<id del Element dueño>",
      "active_state": "<state_id>" | None,
      "states": [{"id": "s1", "name": "Nominal", "severity"?: "normal"}, ...],
      "transitions": [
        {"id": "t1", "from": "s1", "to": "s2", "guard": "co2_ppm > 1000"},
        ...
      ],
    }

Las resoluciones referencia↔canal se inyectan (``ref_to_channel`` y
``channel_to_refs``), de modo que el intérprete no depende del mapeo concreto.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..bus import StateChangeEvent
from ..modelgraph.base import ModelGraph
from .guard import (
    GuardNode,
    GuardParseError,
    evaluate,
    parse_guard,
    referenced_names,
    stale_map,
)

# referencia (value property/canal) -> channel_id | None
RefToChannel = Callable[[str], str | None]
# channel_id -> conjunto de referencias
ChannelToRefs = Callable[[str], set[str]]
Publish = Callable[[StateChangeEvent], Awaitable[None]]
# persist(origin_kind, origin_id, status, severity, payload, ts) -> event_id
Persist = Callable[..., Awaitable[str]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def severity_for_state(state: dict) -> str:
    """Deriva la severidad del elemento a partir del estado destino.

    Respeta ``state['severity']`` si viene explícita; si no, la infiere del
    nombre: Alarma→``alarm``, Forzada→``warning``, Nominal→``normal``.
    """
    explicit = state.get("severity")
    if explicit:
        return str(explicit)
    name = str(state.get("name", "")).strip().lower()
    if "alarm" in name:
        return "alarm"
    if "forz" in name or "forced" in name or "warn" in name:
        return "warning"
    if "nominal" in name or "normal" in name or name == "ok":
        return "normal"
    return "info"


@dataclass
class _Transition:
    id: str
    frm: str
    to: str
    guard_text: str | None
    ast: GuardNode | None = None
    refs: set[str] = field(default_factory=set)
    inert: bool = False  # guarda no resoluble o no parseable (RF-052)


@dataclass
class _Machine:
    id: str
    element_id: str | None
    states: dict[str, dict]
    transitions: list[_Transition]
    active_state: str | None


class StateMachineInterpreter:
    """Mantiene y hace avanzar el estado activo de las STM de un proyecto."""

    def __init__(
        self,
        project_id: str,
        modelgraph: ModelGraph,
        *,
        ref_to_channel: RefToChannel,
        channel_to_refs: ChannelToRefs,
        publish: Publish | None = None,
        persist: Persist | None = None,
    ) -> None:
        self.project_id = project_id
        self.graph = modelgraph
        self._ref_to_channel = ref_to_channel
        self._channel_to_refs = channel_to_refs
        self._publish = publish
        self._persist = persist

        self._machines: dict[str, _Machine] = {}
        # referencias -> máquinas que las usan (índice de reevaluación)
        self._ref_to_machines: dict[str, set[str]] = {}
        self._values: dict[str, float] = {}
        self._last_ts: dict[str, datetime] = {}
        self.warnings: list[str] = []
        self.emitted: list[StateChangeEvent] = []

    # -- carga / inicialización -------------------------------------------
    async def load(self, machines: list[dict]) -> None:
        """Carga las máquinas, fija su estado activo y registra dependencias."""
        for raw in machines:
            await self._load_machine(raw)

    async def _load_machine(self, raw: dict) -> None:
        states = {s["id"]: s for s in raw.get("states", [])}
        transitions: list[_Transition] = []

        for t in raw.get("transitions", []):
            tr = _Transition(
                id=t["id"],
                frm=t.get("from") or t.get("frm"),
                to=t["to"],
                guard_text=t.get("guard"),
            )
            await self._prepare_transition(tr)
            transitions.append(tr)

        active = raw.get("active_state")
        if active is None:
            active = self._initial_state(raw, states)

        machine = _Machine(
            id=raw["id"],
            element_id=raw.get("element_id"),
            states=states,
            transitions=transitions,
            active_state=active,
        )
        self._machines[machine.id] = machine

        # índice referencia -> máquina
        for tr in transitions:
            for ref in tr.refs:
                self._ref_to_machines.setdefault(ref, set()).add(machine.id)

        # sincroniza estado activo inicial con el grafo
        if active is not None:
            await self.graph.set_active_state(self.project_id, machine.id, active)

    async def _prepare_transition(self, tr: _Transition) -> None:
        """Parsea la guarda y registra GUARD_DEPENDS_ON / guard_status."""
        if not tr.guard_text:
            # Transición sin guarda: resoluble, sin dependencias de canal.
            await self.graph.set_guard_status(self.project_id, tr.id, "ok")
            return
        try:
            tr.ast = parse_guard(tr.guard_text)
        except GuardParseError as exc:
            tr.inert = True
            self.warnings.append(f"guarda no parseable en {tr.id}: {exc}")
            await self.graph.set_guard_status(self.project_id, tr.id, "inert")
            return

        tr.refs = referenced_names(tr.ast)
        channel_ids: list[str] = []
        resoluble = True
        for ref in tr.refs:
            ch = self._ref_to_channel(ref)
            if ch is None:
                resoluble = False
            else:
                channel_ids.append(ch)

        if not resoluble:
            tr.inert = True
            self.warnings.append(
                f"guarda no resoluble en {tr.id}: referencias sin canal "
                f"({tr.guard_text!r})"
            )
            await self.graph.set_guard_status(self.project_id, tr.id, "inert")
            return

        await self.graph.set_guard_dependencies(self.project_id, tr.id, channel_ids)
        await self.graph.set_guard_status(self.project_id, tr.id, "ok")

    @staticmethod
    def _initial_state(raw: dict, states: dict[str, dict]) -> str | None:
        # Estado marcado como inicial, si existe; si no, el primero declarado.
        for sid, s in states.items():
            kind = str(s.get("kind", "")).lower()
            if kind in ("initial", "inicial") or s.get("initial"):
                return sid
        order = raw.get("states", [])
        return order[0]["id"] if order else None

    # -- entradas ----------------------------------------------------------
    async def on_reading(self, channel_id: str, value: float, ts: datetime) -> None:
        """Procesa una lectura y reevalúa las máquinas afectadas."""
        refs = self._channel_to_refs(channel_id)
        afectadas: set[str] = set()
        for ref in refs:
            self._values[ref] = value
            self._last_ts[ref] = ts
            afectadas |= self._ref_to_machines.get(ref, set())
        for mid in afectadas:
            await self._evaluate_machine(self._machines[mid], ts)

    async def tick(self, now: datetime | None = None) -> None:
        """*Tick* periódico para guardas basadas en ``stale``."""
        now = now or _now()
        for machine in list(self._machines.values()):
            await self._evaluate_machine(machine, now)

    # -- núcleo ------------------------------------------------------------
    async def _evaluate_machine(self, machine: _Machine, now: datetime) -> None:
        if machine.active_state is None:
            return
        # Solo transiciones salientes del estado activo, en orden de declaración.
        for tr in machine.transitions:
            if tr.inert or tr.ast is None:
                continue
            if tr.frm != machine.active_state:
                continue
            stale = stale_map(tr.ast, now, self._last_ts)
            if evaluate(tr.ast, self._values, stale) is True:
                await self._fire(machine, tr, now)
                return  # una transición por evaluación

    async def _fire(self, machine: _Machine, tr: _Transition, now: datetime) -> None:
        old = machine.active_state
        new = tr.to
        machine.active_state = new

        await self.graph.set_active_state(self.project_id, machine.id, new)

        dest = machine.states.get(new, {"id": new})
        severity = severity_for_state(dest)
        if machine.element_id is not None:
            await self.graph.set_element_severity(
                self.project_id, machine.element_id, severity
            )

        payload = {
            "machine_id": machine.id,
            "from": old,
            "to": new,
            "guard": tr.guard_text,
            "values": {ref: self._values.get(ref) for ref in tr.refs},
        }
        event_id = None
        if self._persist is not None:
            event_id = await self._persist(
                origin_kind="transition",
                origin_id=tr.id,
                status="transitioned",
                severity=severity,
                payload=payload,
                ts=now,
            )
        payload["event_id"] = event_id

        event = StateChangeEvent(
            project_id=self.project_id,
            machine_id=machine.id,
            element_id=machine.element_id or "",
            from_state=old or "",
            to_state=new,
            guard=tr.guard_text or "",
            ts=now,
        )
        self.emitted.append(event)
        if self._publish is not None:
            await self._publish(event)

    # -- introspección -----------------------------------------------------
    def active_state(self, machine_id: str) -> str | None:
        m = self._machines.get(machine_id)
        return m.active_state if m else None
