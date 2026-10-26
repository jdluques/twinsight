"""Pruebas del importador ``.gaphor`` y del generador del modelo de demo.

Estrategia: generar el modelo de demo con ``scripts/gen_demo_gaphor`` en un
archivo temporal, importarlo con :class:`GaphorTranslator` y verificar que el
``GraphBatch` resultante contiene lo esperado (bloques, value properties,
geometría, máquina de estados con sus guardas y la metadata ``dt:*``). Además
se comprueba que un XML corrupto lanza un error claro.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from twinsight.importers.base import GraphBatch
from twinsight.importers.gaphor import GaphorTranslator

# Cargar el generador (vive en scripts/, fuera del paquete instalable).
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "gen_demo_gaphor", _SCRIPTS / "gen_demo_gaphor.py"
)
gen_demo_gaphor = importlib.util.module_from_spec(_spec)
sys.modules["gen_demo_gaphor"] = gen_demo_gaphor
_spec.loader.exec_module(gen_demo_gaphor)


# --- Fixtures ---------------------------------------------------------------

@pytest.fixture(scope="module")
def model_bytes(tmp_path_factory) -> bytes:
    out = tmp_path_factory.mktemp("gaphor") / "modelo.gaphor"
    gen_demo_gaphor.build_demo_model(out)
    return out.read_bytes()


@pytest.fixture(scope="module")
def batch(model_bytes) -> GraphBatch:
    return GaphorTranslator().translate(model_bytes, source_id="demo")


def _by(nodes, **filters):
    out = []
    for n in nodes:
        if "label" in filters and n.label != filters["label"]:
            continue
        if "sub" in filters and filters["sub"] not in n.sub_labels:
            continue
        out.append(n)
    return out


# --- can_import -------------------------------------------------------------

def test_can_import_by_extension():
    t = GaphorTranslator()
    assert t.can_import("modelo.gaphor", b"")
    assert not t.can_import("foto.png", b"\x89PNG")


def test_can_import_by_header():
    t = GaphorTranslator()
    assert t.can_import("sin-extension", b'<?xml version="1.0"?><gaphor version="4">')


# --- Bloques y value properties --------------------------------------------

def test_block_count(batch):
    """2 aulas + 10 sensores + 1 ventilación = 13 Blocks."""
    blocks = _by(batch.nodes, label="Element", sub="Block")
    assert len(blocks) == 13
    names = {b.properties["name"] for b in blocks}
    assert "Ventilacion_AulaA" in names


def test_value_properties_present(batch):
    blocks = _by(batch.nodes, label="Element", sub="Block")
    bme = next(b for b in blocks if "BME280" in b.properties["name"])
    labels = {vp["name"] for vp in bme.properties["value_properties"]}
    assert {"Temperatura", "Humedad relativa", "Presión"} <= labels
    # También se emiten como nodos Property independientes.
    props = _by(batch.nodes, label="Element", sub="Property")
    assert len(props) >= 20  # suma de métricas de todos los sensores


def test_contains_aula_sensor(batch):
    """La composición aula→sensor produce aristas CONTAINS."""
    contains = [e for e in batch.edges if e.type == "CONTAINS"]
    blocks = {b.origin_id: b for b in _by(batch.nodes, label="Element", sub="Block")}
    aula_to_sensor = [
        e for e in contains
        if e.source_origin_id in blocks and e.target_origin_id in blocks
    ]
    assert len(aula_to_sensor) == 10  # 2 aulas x 5 sensores


# --- Geometría de diagramas -------------------------------------------------

def test_diagrams_and_geometry(batch):
    types = {d.diagram_type for d in batch.diagrams}
    assert {"bdd", "stm"} <= types
    bdd = next(d for d in batch.diagrams if d.diagram_type == "bdd")
    block_items = [it for it in bdd.items if it.item_type == "BlockItem"]
    assert block_items
    it = block_items[0]
    assert it.width > 0 and it.height > 0
    assert (it.x, it.y) != (0.0, 0.0)
    assert it.subject_origin_id is not None


def test_stm_diagram_has_transition_waypoints(batch):
    stm = next(d for d in batch.diagrams if d.diagram_type == "stm")
    tis = [it for it in stm.items if it.item_type == "TransitionItem"]
    assert tis
    assert all(len(it.waypoints) >= 2 for it in tis)


# --- Máquina de estados -----------------------------------------------------

def test_state_machine(batch):
    stms = _by(batch.nodes, label="StateMachine")
    assert len(stms) == 1
    assert stms[0].properties["name"] == "Ventilación Aula A"
    # HAS_STM: el Block de ventilación posee la STM.
    has_stm = [e for e in batch.edges if e.type == "HAS_STM"]
    assert len(has_stm) == 1
    vent = next(
        b for b in _by(batch.nodes, label="Element", sub="Block")
        if b.properties["name"] == "Ventilacion_AulaA"
    )
    assert has_stm[0].source_origin_id == vent.origin_id
    assert has_stm[0].target_origin_id == stms[0].origin_id


def test_three_simple_states(batch):
    states = _by(batch.nodes, label="State")
    simple = [s for s in states if s.properties.get("kind") == "state"]
    assert {s.properties["name"] for s in simple} == {"Nominal", "Forzada", "Alarma"}
    assert len(simple) == 3
    # HAS_STATE conecta la STM con cada vértice (3 estados + inicial).
    assert len([e for e in batch.edges if e.type == "HAS_STATE"]) == len(states)


def test_four_guarded_transitions(batch):
    trans = _by(batch.nodes, label="Transition")
    guarded = [t for t in trans if t.properties.get("guard_text")]
    assert len(guarded) == 4
    guards = {t.properties["guard_text"] for t in guarded}
    assert guards == {
        "co2_ppm > 1000",
        "co2_ppm > 1600",
        "co2_ppm < 800",
        "co2_ppm < 1400",
    }
    # Cada transición tiene origen (FROM) y destino (TO).
    assert len([e for e in batch.edges if e.type == "FROM"]) == len(trans)
    assert len([e for e in batch.edges if e.type == "TO"]) == len(trans)


def test_transition_guard_directions(batch):
    """Verifica guardas en las transiciones correctas (origen→destino)."""
    trans = {t.origin_id: t for t in _by(batch.nodes, label="Transition")}
    states = {s.origin_id: s for s in _by(batch.nodes, label="State")}
    froms = {e.source_origin_id: e.target_origin_id
             for e in batch.edges if e.type == "FROM"}
    tos = {e.source_origin_id: e.target_origin_id
           for e in batch.edges if e.type == "TO"}
    found = set()
    for tid, t in trans.items():
        guard = t.properties.get("guard_text")
        if not guard:
            continue
        src = states[froms[tid]].properties["name"]
        dst = states[tos[tid]].properties["name"]
        found.add((src, dst, guard))
    assert found == {
        ("Nominal", "Forzada", "co2_ppm > 1000"),
        ("Forzada", "Alarma", "co2_ppm > 1600"),
        ("Forzada", "Nominal", "co2_ppm < 800"),
        ("Alarma", "Forzada", "co2_ppm < 1400"),
    }


# --- Metadata dt:* ----------------------------------------------------------

def test_dt_metadata_extracted(batch):
    blocks = _by(batch.nodes, label="Element", sub="Block")
    with_dt = [b for b in blocks if b.properties.get("dt_channel")]
    assert len(with_dt) == 1
    co2 = with_dt[0]
    assert "MH-Z19B" in co2.properties["name"]
    assert co2.properties["dt_channel"] == "gemelo/aula-A/mhz19b:co2_ppm"
    assert co2.properties["dt_visual"] == "piso.svg#sensor-co2-aulaA"


# --- Robustez ---------------------------------------------------------------

def test_corrupt_xml_raises():
    t = GaphorTranslator()
    with pytest.raises(ValueError):
        t.translate(b"<gaphor version='4'><model><SysML:Block", source_id="x")


def test_not_a_gaphor_root_raises():
    t = GaphorTranslator()
    with pytest.raises(ValueError):
        t.translate(b"<?xml version='1.0'?><otracosa/>", source_id="x")


def test_unknown_types_tolerated():
    """Un tipo desconocido no aborta: se importa como Unknown (RF-017)."""
    xml = (
        b"<?xml version='1.0'?>"
        b'<gaphor xmlns="https://gaphor.org/model"'
        b' xmlns:UML="https://gaphor.org/modelinglanguage/UML" version="4">'
        b'<model>'
        b'<UML:Marciano id="m1"><name><val>ET</val></name></UML:Marciano>'
        b'</model></gaphor>'
    )
    batch = GaphorTranslator().translate(xml, source_id="x")
    unknown = _by(batch.nodes, label="Element", sub="Unknown")
    assert len(unknown) == 1
    assert batch.report.unconverted_entities  # anotado en el reporte


# --- El archivo de ejemplo versionado importa correctamente -----------------

def test_example_file_imports():
    example = (
        Path(__file__).resolve().parents[2]
        / "examples" / "aulas-pi3" / "modelo.gaphor"
    )
    if not example.exists():
        pytest.skip("examples/aulas-pi3/modelo.gaphor no generado todavía")
    batch = GaphorTranslator().translate(example.read_bytes(), source_id="ejemplo")
    assert len(_by(batch.nodes, label="Element", sub="Block")) == 13
