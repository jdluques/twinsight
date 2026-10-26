"""Tests del motor de comportamiento: guardas, reglas y STM.

Usan *fakes* en memoria; no requieren Neo4j ni un broker MQTT.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from twinsight.behavior import (
    GuardParseError,
    RuleEngine,
    StateMachineInterpreter,
    evaluate,
    parse_guard,
    referenced_names,
    validate_rule,
)
from twinsight.behavior import test_rule as dry_run_rule
from twinsight.behavior.guard import Comparacion, Entre, OpBooleana, Stale

UTC = timezone.utc


def _t(seg: float = 0.0) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seg)


# ==========================================================================
# Guardas (Apéndice D)
# ==========================================================================
def test_parse_ejemplos_apendice_d():
    a = parse_guard("co2_ppm > 1000")
    assert a == Comparacion(ref="co2_ppm", op=">", valor=1000.0)

    b = parse_guard("co2_ppm > 1000 and personas >= 1")
    assert isinstance(b, OpBooleana) and b.op == "and"
    assert b.terminos == (
        Comparacion(ref="co2_ppm", op=">", valor=1000.0),
        Comparacion(ref="personas", op=">=", valor=1.0),
    )

    c = parse_guard("stale(co2_ppm, 30)")
    assert c == Stale(ref="co2_ppm", segundos=30.0)


def test_parse_between_y_precedencia():
    assert parse_guard("temp between 10 and 20") == Entre(ref="temp", lo=10.0, hi=20.0)
    # 'and' enlaza más fuerte que 'or'
    g = parse_guard("a > 1 or b < 2 and c == 3")
    assert isinstance(g, OpBooleana) and g.op == "or"
    assert g.terminos[1].op == "and"


def test_evaluate_true_false_none():
    g = parse_guard("co2_ppm > 1000")
    assert evaluate(g, {"co2_ppm": 1200}) is True
    assert evaluate(g, {"co2_ppm": 800}) is False
    assert evaluate(g, {}) is None  # no resoluble


def test_evaluate_and_or_trivaluado():
    g = parse_guard("a > 1 and b > 2")
    assert evaluate(g, {"a": 5}) is None  # falta b
    assert evaluate(g, {"a": 0, "b": 5}) is False  # a falso domina
    g2 = parse_guard("a > 1 or b > 2")
    assert evaluate(g2, {"a": 5}) is True  # a verdadero domina
    assert evaluate(g2, {"a": 0}) is None  # falta b, a falso


def test_evaluate_stale():
    g = parse_guard("stale(co2_ppm, 30)")
    assert evaluate(g, {}, {"co2_ppm": True}) is True
    assert evaluate(g, {}, {"co2_ppm": False}) is False
    assert evaluate(g, {}, {}) is False


def test_referenced_names():
    g = parse_guard("co2_ppm > 1000 and personas >= 1 or stale(temp, 5)")
    assert referenced_names(g) == {"co2_ppm", "personas", "temp"}


def test_guarda_invalida_lanza_error():
    with pytest.raises(GuardParseError):
        parse_guard("co2_ppm >> 1000")
    with pytest.raises(GuardParseError):
        parse_guard("")


# ==========================================================================
# Motor de reglas
# ==========================================================================
async def test_debounce_no_dispara_antes_de_for_seconds():
    rule = {
        "version": 1,
        "name": "co2 alto",
        "when": {"all": [{"channel": "co2", "op": ">", "value": 1000}]},
        "for_seconds": 10,
        "severity": "warning",
    }
    eng = RuleEngine("p1", [rule])
    await eng.on_reading("co2", 1200, _t(0))  # entra en pending
    assert eng.emitted == []
    await eng.tick(_t(5))  # aún no pasaron 10 s
    assert eng.emitted == []
    await eng.tick(_t(10))  # se sostuvo 10 s -> firing
    assert len(eng.emitted) == 1
    assert eng.emitted[0].status == "fired"
    assert eng.emitted[0].severity == "warning"


async def test_debounce_cancela_si_condicion_cae():
    rule = {
        "version": 1,
        "when": {"all": [{"channel": "co2", "op": ">", "value": 1000}]},
        "for_seconds": 10,
        "severity": "warning",
    }
    eng = RuleEngine("p1", [rule])
    await eng.on_reading("co2", 1200, _t(0))  # pending
    await eng.on_reading("co2", 500, _t(3))  # cae -> inactive
    await eng.tick(_t(20))
    assert eng.emitted == []


async def test_histeresis_sin_flapping_con_clear_when():
    rule = {
        "version": 1,
        "when": {"all": [{"channel": "co2", "op": ">", "value": 1000}]},
        "clear_when": {"all": [{"channel": "co2", "op": "<", "value": 800}]},
        "severity": "alarm",
    }
    eng = RuleEngine("p1", [rule])
    await eng.on_reading("co2", 1050, _t(0))  # fired
    await eng.on_reading("co2", 950, _t(1))  # entre umbrales: sigue firing
    await eng.on_reading("co2", 1050, _t(2))  # no vuelve a disparar
    await eng.on_reading("co2", 850, _t(3))  # aún no limpia (>=800)
    assert [e.status for e in eng.emitted] == ["fired"]
    await eng.on_reading("co2", 700, _t(4))  # cruza clear_when -> cleared
    assert [e.status for e in eng.emitted] == ["fired", "cleared"]


async def test_stale_por_tick():
    rule = {
        "version": 1,
        "when": {"all": [{"op": "stale", "channel": "co2", "seconds": 30}]},
        "severity": "stale",
    }
    eng = RuleEngine("p1", [rule])
    await eng.on_reading("co2", 500, _t(0))  # fresco
    await eng.tick(_t(10))  # 10 s < 30 s
    assert eng.emitted == []
    await eng.tick(_t(31))  # 31 s >= 30 s -> stale
    assert len(eng.emitted) == 1 and eng.emitted[0].status == "fired"


def test_validate_rechaza_between_invertido():
    bad = {
        "version": 1,
        "when": {"all": [{"channel": "t", "op": "between", "value": [20, 10]}]},
    }
    with pytest.raises(ValueError, match="invertido"):
        validate_rule(bad)


def test_validate_rechaza_op_desconocido():
    bad = {
        "version": 1,
        "when": {"all": [{"channel": "t", "op": "~=", "value": 1}]},
    }
    with pytest.raises(ValueError, match="desconocido"):
        validate_rule(bad)


def test_validate_canal_inexistente_opcional():
    ok = {"version": 1, "when": {"all": [{"channel": "co2", "op": ">", "value": 1}]}}
    validate_rule(ok)  # sin known_channels no se comprueba existencia
    validate_rule(ok, known_channels={"co2"})
    bad = {"version": 1, "when": {"all": [{"channel": "x", "op": ">", "value": 1}]}}
    with pytest.raises(ValueError, match="inexistente"):
        validate_rule(bad, known_channels={"co2"})


def test_dry_run():
    rule = {
        "version": 1,
        "when": {"all": [{"channel": "co2", "op": ">", "value": 1000}]},
        "severity": "warning",
    }
    assert dry_run_rule(rule, {"co2": 1200})["would_fire"] is True
    assert dry_run_rule(rule, {"co2": 500})["would_fire"] is False
    assert dry_run_rule(rule, {})["when"] is None


# ==========================================================================
# Intérprete STM (con fake ModelGraph)
# ==========================================================================
class FakeModelGraph:
    """Implementa solo lo que usa el intérprete; registra las llamadas."""

    def __init__(self, machines):
        self._machines = machines
        self.active_state = {}
        self.element_severity = {}
        self.guard_status = {}
        self.guard_deps = {}
        self.channel_stale = {}

    async def get_state_machines(self, project_id):
        return self._machines

    async def set_active_state(self, project_id, machine_id, state_id):
        self.active_state[machine_id] = state_id

    async def set_element_severity(self, project_id, element_id, severity):
        self.element_severity[element_id] = severity

    async def set_guard_status(self, project_id, transition_id, status):
        self.guard_status[transition_id] = status

    async def set_guard_dependencies(self, project_id, transition_id, channel_ids):
        self.guard_deps[transition_id] = channel_ids

    async def set_channel_stale(self, project_id, channel_id, stale):
        self.channel_stale[channel_id] = stale


def _machine_co2():
    return {
        "id": "m1",
        "element_id": "el1",
        "active_state": "nominal",
        "states": [
            {"id": "nominal", "name": "Nominal"},
            {"id": "forzada", "name": "Forzada"},
            {"id": "alarma", "name": "Alarma"},
        ],
        "transitions": [
            {"id": "t1", "from": "nominal", "to": "forzada", "guard": "co2_ppm > 1000"},
            {"id": "t2", "from": "forzada", "to": "alarma", "guard": "co2_ppm > 2000"},
        ],
    }


async def test_stm_transiciona_y_emite_evento():
    graph = FakeModelGraph([_machine_co2()])
    emitted = []
    persisted = []

    async def persist(**kw):
        persisted.append(kw)
        return "ev-1"

    interp = StateMachineInterpreter(
        "p1",
        graph,
        ref_to_channel=lambda ref: "ch-co2" if ref == "co2_ppm" else None,
        channel_to_refs=lambda ch: {"co2_ppm"} if ch == "ch-co2" else set(),
        publish=_collect(emitted),
        persist=persist,
    )
    await interp.load(await graph.get_state_machines("p1"))

    # estado inicial sincronizado y dependencias de guarda registradas
    assert graph.active_state["m1"] == "nominal"
    assert graph.guard_status["t1"] == "ok"
    assert graph.guard_deps["t1"] == ["ch-co2"]

    await interp.on_reading("ch-co2", 1200, _t(0))

    assert interp.active_state("m1") == "forzada"
    assert graph.active_state["m1"] == "forzada"
    assert graph.element_severity["el1"] == "warning"  # Forzada -> warning
    assert len(emitted) == 1
    ev = emitted[0]
    assert ev.from_state == "Nominal" and ev.to_state == "Forzada"
    assert ev.guard == "co2_ppm > 1000"
    assert len(persisted) == 1
    assert persisted[0]["origin_kind"] == "transition"
    assert persisted[0]["status"] == "transitioned"
    assert persisted[0]["severity"] == "warning"


async def test_stm_no_transiciona_bajo_umbral():
    graph = FakeModelGraph([_machine_co2()])
    emitted = []
    interp = StateMachineInterpreter(
        "p1",
        graph,
        ref_to_channel=lambda ref: "ch-co2" if ref == "co2_ppm" else None,
        channel_to_refs=lambda ch: {"co2_ppm"} if ch == "ch-co2" else set(),
        publish=_collect(emitted),
    )
    await interp.load(await graph.get_state_machines("p1"))
    await interp.on_reading("ch-co2", 800, _t(0))
    assert interp.active_state("m1") == "nominal"
    assert emitted == []


async def test_stm_guarda_no_resoluble_marca_inert():
    machine = {
        "id": "m2",
        "element_id": "el2",
        "active_state": "s0",
        "states": [{"id": "s0", "name": "Nominal"}, {"id": "s1", "name": "Alarma"}],
        "transitions": [
            {"id": "tx", "from": "s0", "to": "s1", "guard": "presion_bar > 5"},
        ],
    }
    graph = FakeModelGraph([machine])
    emitted = []
    interp = StateMachineInterpreter(
        "p1",
        graph,
        ref_to_channel=lambda ref: None,  # nada resuelve
        channel_to_refs=lambda ch: set(),
        publish=_collect(emitted),
    )
    await interp.load(await graph.get_state_machines("p1"))
    assert graph.guard_status["tx"] == "inert"
    assert any("no resoluble" in w for w in interp.warnings)
    assert "tx" not in graph.guard_deps


def _collect(sink):
    async def _publish(ev):
        sink.append(ev)

    return _publish
