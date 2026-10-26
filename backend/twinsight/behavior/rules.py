"""Motor de reglas simples con histéresis, *debounce* y *stale* (RF-055..RF-059).

Las reglas se definen con un esquema JSON versionado y se evalúan reutilizando
el evaluador trivaluado de :mod:`twinsight.behavior.guard`. Cada regla tiene una
máquina de estados interna ``inactive → pending → firing`` que:

* **Debounce** (``for_seconds``): la condición debe sostenerse ese tiempo antes
  de disparar (``pending``), evitando falsos positivos por picos puntuales.
* **Histéresis** (``clear_when``): una vez disparada, la regla permanece en
  ``firing`` hasta que se cumple ``clear_when`` (umbral de salida distinto al de
  entrada), evitando *flapping* con valores que oscilan alrededor del umbral.
* **Stale**: las condiciones ``stale`` se evalúan en el *tick* periódico
  (:meth:`RuleEngine.tick`), no en la llegada de una lectura.

Esquema (``version: 1``)::

    {
      "version": 1,
      "when": {"all"|"any": [cond, ...]},
      "for_seconds": 0,                # opcional, debounce
      "clear_when": {"all"|"any": [...]},  # opcional, histéresis
      "severity": "warning"
    }

Donde ``cond`` es una de::

    {"channel": "<id>", "op": ">"|">="|"<"|"<="|"=="|"!=", "value": <num>}
    {"channel": "<id>", "op": "between", "value": [lo, hi]}
    {"op": "stale", "channel": "<id>", "seconds": <num>}
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..bus import RuleEvent
from .guard import (
    COMPARADORES,
    Comparacion,
    Entre,
    GuardNode,
    OpBooleana,
    Stale,
    evaluate,
)

_BETWEEN = "between"
_STALE = "stale"


# --------------------------------------------------------------------------
# Validación del esquema (RF-059)
# --------------------------------------------------------------------------
def _validate_condition(cond: Any, known_channels: set[str] | None) -> None:
    if not isinstance(cond, dict):
        raise ValueError("cada condición debe ser un objeto")
    op = cond.get("op")
    channel = cond.get("channel")
    if not channel or not isinstance(channel, str):
        raise ValueError("cada condición requiere 'channel'")
    if known_channels is not None and channel not in known_channels:
        raise ValueError(f"canal inexistente: {channel!r}")

    if op == _STALE:
        seconds = cond.get("seconds")
        if not isinstance(seconds, (int, float)) or seconds < 0:
            raise ValueError("stale requiere 'seconds' >= 0")
        return

    if op == _BETWEEN:
        value = cond.get("value")
        if not (isinstance(value, (list, tuple)) and len(value) == 2):
            raise ValueError("between requiere 'value': [lo, hi]")
        lo, hi = value
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise ValueError("between: lo y hi deben ser numéricos")
        if lo > hi:
            raise ValueError(f"between invertido: lo={lo} > hi={hi}")
        return

    if op in COMPARADORES:
        if not isinstance(cond.get("value"), (int, float)):
            raise ValueError(f"la condición {op!r} requiere 'value' numérico")
        return

    raise ValueError(f"operador desconocido: {op!r}")


def _validate_block(block: Any, known_channels: set[str] | None) -> None:
    if not isinstance(block, dict):
        raise ValueError("'when'/'clear_when' debe ser un objeto {all|any: [...]}")
    claves = [k for k in ("all", "any") if k in block]
    if len(claves) != 1:
        raise ValueError("el bloque debe tener exactamente una clave 'all' o 'any'")
    condiciones = block[claves[0]]
    if not isinstance(condiciones, list) or not condiciones:
        raise ValueError("el bloque debe contener una lista no vacía de condiciones")
    for cond in condiciones:
        _validate_condition(cond, known_channels)


def validate_rule(definition: dict, known_channels: set[str] | None = None) -> None:
    """Valida una definición de regla. Lanza ``ValueError`` con mensaje claro."""
    if not isinstance(definition, dict):
        raise ValueError("la definición debe ser un objeto JSON")
    if definition.get("version") != 1:
        raise ValueError("version no soportada (se espera 1)")
    if "when" not in definition:
        raise ValueError("falta el bloque 'when'")
    _validate_block(definition["when"], known_channels)

    if "clear_when" in definition and definition["clear_when"] is not None:
        _validate_block(definition["clear_when"], known_channels)

    for_seconds = definition.get("for_seconds", 0)
    if not isinstance(for_seconds, (int, float)) or for_seconds < 0:
        raise ValueError("'for_seconds' debe ser un número >= 0")

    severity = definition.get("severity", "warning")
    if not isinstance(severity, str):
        raise ValueError("'severity' debe ser una cadena")


# --------------------------------------------------------------------------
# Compilación JSON -> AST reutilizable de guard.py
# --------------------------------------------------------------------------
def _compile_condition(cond: dict) -> GuardNode:
    op = cond.get("op")
    channel = cond["channel"]
    if op == _STALE:
        return Stale(ref=channel, segundos=float(cond["seconds"]))
    if op == _BETWEEN:
        lo, hi = cond["value"]
        return Entre(ref=channel, lo=float(lo), hi=float(hi))
    return Comparacion(ref=channel, op=op, valor=float(cond["value"]))


def _compile_block(block: dict) -> GuardNode:
    clave = "all" if "all" in block else "any"
    condiciones = [_compile_condition(c) for c in block[clave]]
    if len(condiciones) == 1:
        return condiciones[0]
    return OpBooleana(op="and" if clave == "all" else "or", terminos=tuple(condiciones))


def _channels_of(node: GuardNode) -> set[str]:
    if isinstance(node, OpBooleana):
        s: set[str] = set()
        for t in node.terminos:
            s |= _channels_of(t)
        return s
    if isinstance(node, (Comparacion, Entre, Stale)):
        return {node.ref}
    return set()


def _stale_channels(node: GuardNode) -> dict[str, float]:
    """Canales con condición ``stale`` y sus segundos (mínimo si repetido)."""
    out: dict[str, float] = {}
    if isinstance(node, OpBooleana):
        for t in node.terminos:
            for ch, sec in _stale_channels(t).items():
                out[ch] = min(out.get(ch, sec), sec)
    elif isinstance(node, Stale):
        out[node.ref] = node.segundos
    return out


# --------------------------------------------------------------------------
# Regla compilada + estado en vivo
# --------------------------------------------------------------------------
@dataclass
class _CompiledRule:
    id: str
    name: str
    severity: str
    for_seconds: float
    when: GuardNode
    clear_when: GuardNode | None
    channels: set[str]
    stale_secs: dict[str, float]
    # estado en vivo
    state: str = "inactive"  # inactive | pending | firing
    pending_since: datetime | None = None


# tipos de callbacks inyectables
Publish = Callable[[RuleEvent], Awaitable[None]]
# persist(origin_kind, origin_id, status, severity, payload, ts) -> event_id
Persist = Callable[..., Awaitable[str]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_rule(definition: dict, last_values: dict[str, float]) -> dict:
    """*Dry-run* de una regla contra ``last_values`` sin efectos (RF-058).

    Valida la definición y evalúa ``when`` (y ``clear_when`` si existe) con los
    valores dados. Devuelve ``{would_fire, when, clear_when}`` con resultados
    trivaluados (``True``/``False``/``None``).
    """
    validate_rule(definition)
    when = _compile_block(definition["when"])
    when_res = evaluate(when, last_values, {})
    clear_res = None
    clear_raw = definition.get("clear_when")
    if clear_raw:
        clear_res = evaluate(_compile_block(clear_raw), last_values, {})
    return {
        "would_fire": when_res is True,
        "when": when_res,
        "clear_when": clear_res,
    }


class RuleEngine:
    """Evalúa reglas de un proyecto de forma selectiva por canal (RF-056).

    ``publish`` y ``persist`` son opcionales: si no se inyectan, los eventos se
    acumulan en :attr:`emitted` (útil para tests y *dry-run* sin broker/DB).
    """

    def __init__(
        self,
        project_id: str,
        rules: list[dict] | None = None,
        *,
        publish: Publish | None = None,
        persist: Persist | None = None,
    ) -> None:
        self.project_id = project_id
        self._publish = publish
        self._persist = persist
        self.emitted: list[RuleEvent] = []

        self._rules: dict[str, _CompiledRule] = {}
        self._by_channel: dict[str, set[str]] = {}  # índice canal -> ids de regla
        self._values: dict[str, float] = {}
        self._last_ts: dict[str, datetime] = {}

        for raw in rules or []:
            self.add_rule(raw)

    # -- gestión de reglas -------------------------------------------------
    def add_rule(self, raw: dict, rule_id: str | None = None) -> str:
        """Compila y registra una regla ya validada. Devuelve su id."""
        validate_rule(raw)
        rid = rule_id or raw.get("id") or str(uuid.uuid4())
        when = _compile_block(raw["when"])
        clear_raw = raw.get("clear_when")
        clear = _compile_block(clear_raw) if clear_raw else None
        channels = _channels_of(when) | (_channels_of(clear) if clear else set())
        stale_secs = dict(_stale_channels(when))
        if clear:
            for ch, sec in _stale_channels(clear).items():
                stale_secs[ch] = min(stale_secs.get(ch, sec), sec)
        rule = _CompiledRule(
            id=rid,
            name=raw.get("name", rid),
            severity=raw.get("severity", "warning"),
            for_seconds=float(raw.get("for_seconds", 0) or 0),
            when=when,
            clear_when=clear,
            channels=channels,
            stale_secs=stale_secs,
        )
        self._rules[rid] = rule
        for ch in channels:
            self._by_channel.setdefault(ch, set()).add(rid)
        return rid

    # -- entradas ----------------------------------------------------------
    async def on_reading(self, channel_id: str, value: float, ts: datetime) -> None:
        """Procesa una lectura: actualiza el valor y reevalúa sus reglas."""
        self._values[channel_id] = value
        self._last_ts[channel_id] = ts
        for rid in list(self._by_channel.get(channel_id, ())):
            await self._evaluate_rule(self._rules[rid], ts)

    async def tick(self, now: datetime | None = None) -> None:
        """*Tick* periódico: reevalúa todas las reglas (debounce y stale)."""
        now = now or _now()
        for rule in list(self._rules.values()):
            await self._evaluate_rule(rule, now)

    # -- dry-run (RF-058) --------------------------------------------------
    def test_rule(self, definition: dict, last_values: dict[str, float]) -> dict:
        """Evalúa ``when``/``clear_when`` contra ``last_values`` sin efectos."""
        return test_rule(definition, last_values)

    # -- núcleo ------------------------------------------------------------
    def _stale_flags(self, rule: _CompiledRule, now: datetime) -> dict[str, bool]:
        flags: dict[str, bool] = {}
        for ch, secs in rule.stale_secs.items():
            ts = self._last_ts.get(ch)
            flags[ch] = ts is None or (now - ts).total_seconds() >= secs
        return flags

    async def _evaluate_rule(self, rule: _CompiledRule, now: datetime) -> None:
        stale = self._stale_flags(rule, now)
        when_res = evaluate(rule.when, self._values, stale)
        met = when_res is True

        if rule.state == "inactive":
            if met:
                if rule.for_seconds > 0:
                    rule.state = "pending"
                    rule.pending_since = now
                else:
                    await self._fire(rule, now)

        elif rule.state == "pending":
            if not met:
                rule.state = "inactive"
                rule.pending_since = None
            else:
                elapsed = (now - (rule.pending_since or now)).total_seconds()
                if elapsed >= rule.for_seconds:
                    await self._fire(rule, now)

        elif rule.state == "firing":
            if rule.clear_when is not None:
                clear_res = evaluate(rule.clear_when, self._values, stale)
                if clear_res is True:
                    await self._clear(rule, now)
            else:
                # Sin clear_when explícito: se limpia cuando 'when' deja de
                # cumplirse de forma resoluble (resultado False, no None).
                if when_res is False:
                    await self._clear(rule, now)

    async def _fire(self, rule: _CompiledRule, now: datetime) -> None:
        rule.state = "firing"
        rule.pending_since = None
        await self._emit(rule, "fired", now)

    async def _clear(self, rule: _CompiledRule, now: datetime) -> None:
        rule.state = "inactive"
        rule.pending_since = None
        await self._emit(rule, "cleared", now)

    async def _emit(self, rule: _CompiledRule, status: str, now: datetime) -> None:
        payload = {
            "rule": rule.name,
            "values": {ch: self._values.get(ch) for ch in rule.channels},
        }
        event_id = str(uuid.uuid4())
        if self._persist is not None:
            event_id = await self._persist(
                origin_kind="rule",
                origin_id=rule.id,
                status=status,
                severity=rule.severity,
                payload=payload,
                ts=now,
            )
        event = RuleEvent(
            project_id=self.project_id,
            event_id=event_id,
            rule_id=rule.id,
            status=status,
            severity=rule.severity,
            ts=now,
            payload=payload,
        )
        self.emitted.append(event)
        if self._publish is not None:
            await self._publish(event)
