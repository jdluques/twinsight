"""Genera el modelo ``.gaphor`` de demostración de TwinSight (aulas PI3).

Produce ``examples/aulas-pi3/modelo.gaphor`` en **Python puro** (sin la librería
de Gaphor, por tanto sin GTK): el ``.gaphor`` es solo XML de datos, así que se
construye con ``xml.etree.ElementTree``. Los helpers de escritura
(``_val``/``_ref``/``_reflist``/``_make_block``/``_make_property``/
``_make_block_item``) están adaptados del prototipo PI3
(``server/gemelo/gaphor_xml.py``).

Contenido del modelo:

* 2 aulas (``aula-A``, ``aula-B``); cada aula es un ``SysML:Block`` que
  "contiene" (composición = part properties) 5 Blocks de sensor:
  INMP441, MH-Z19B, BME280, BH1750 y MLX90640, cada uno con una value property
  por métrica (del catálogo :data:`twinsight.sensors.SENSORS`).
* El Block del sensor de CO2 de ``aula-A`` lleva en su nota la metadata del
  gemelo: ``dt:channel=...`` y ``dt:visual=...``.
* Un Block ``Ventilacion_AulaA`` que posee (``ownedBehavior`` → HAS_STM) una
  ``UML:StateMachine`` "Ventilación Aula A" con estados **Nominal/Forzada/Alarma**
  (más un pseudostado inicial) y transiciones con guardas sobre ``co2_ppm``.
* Dos diagramas: un **BDD** con los bloques en rejilla y un **STM** con los
  estados y transiciones posicionados.

El esquema de máquina de estados sigue el data model real de Gaphor
(``gaphor/UML/uml.py``): ``StateMachine.region`` → ``Region.subvertex`` →
``State``/``Pseudostate``; ``Transition`` con ``source``/``target``/``container``
y ``guard`` → ``UML:Constraint`` → ``specification`` → ``UML:LiteralString``.
Las presentaciones usan las clases reales (``StateItem``, ``PseudostateItem``,
``TransitionItem``), por lo que el archivo pretende abrir en Gaphor sin pérdidas.

Uso::

    uv run python scripts/gen_demo_gaphor.py [ruta_salida]
"""

from __future__ import annotations

import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

# Permitir ejecutar el script directamente (sin instalar el paquete).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from twinsight.sensors import SENSORS  # noqa: E402

# --- Namespaces del formato .gaphor v4 -------------------------------------
NS: dict[str, str] = {
    "": "https://gaphor.org/model",
    "Core": "https://gaphor.org/modelinglanguage/Core",
    "UML": "https://gaphor.org/modelinglanguage/UML",
    "SysML": "https://gaphor.org/modelinglanguage/SysML",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

GAPHOR_FILE_VERSION = "4"
GAPHOR_VERSION_TAG = "3.3.2"

# Aulas de la demo: (id técnico en topics MQTT, nombre legible).
AULAS: list[tuple[str, str]] = [("aula-A", "Aula A"), ("aula-B", "Aula B")]

# Distribución del BDD (rejilla por columnas, como en el prototipo).
_AULA_X_GAP = 320
_SENSOR_Y_GAP = 120
_AULA_Y = 40
_SENSORS_Y0 = 150
_X0 = 60

DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent.parent
    / "examples" / "aulas-pi3" / "modelo.gaphor"
)


# ---------------------------------------------------------------------------
# Helpers de escritura XML (adaptados de server/gemelo/gaphor_xml.py)
# ---------------------------------------------------------------------------

def _Q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _D(local: str) -> str:
    return f"{{{NS['']}}}{local}"


def new_id() -> str:
    return str(uuid.uuid4())


def _val(parent, local: str, text: str):
    f = ET.SubElement(parent, _D(local))
    ET.SubElement(f, _D("val")).text = text
    return f


def _ref(parent, local: str, refid: str):
    f = ET.SubElement(parent, _D(local))
    ET.SubElement(f, _D("ref")).set("refid", refid)
    return f


def _reflist(parent, local: str, refids: list[str]):
    f = ET.SubElement(parent, _D(local))
    rl = ET.SubElement(f, _D("reflist"))
    for rid in refids:
        ET.SubElement(rl, _D("ref")).set("refid", rid)
    return f


def _elem(model, prefix: str, local: str, eid: str | None = None):
    e = ET.SubElement(model, _Q(prefix, local))
    if eid:
        e.set("id", eid)
    return e


def _matrix(x: float, y: float) -> str:
    return f"(1.0, 0.0, 0.0, 1.0, {float(x)}, {float(y)})"


# ---------------------------------------------------------------------------
# Constructor del modelo
# ---------------------------------------------------------------------------

class _Builder:
    """Acumula elementos en ``<model>`` y lleva el índice de presentaciones."""

    def __init__(self) -> None:
        self.root = ET.Element(_D("gaphor"))
        self.root.set("version", GAPHOR_FILE_VERSION)
        self.root.set("gaphor-version", GAPHOR_VERSION_TAG)
        self.model = ET.SubElement(self.root, _D("model"))
        _elem(self.model, "Core", "StyleSheet", new_id())
        self._vt_cache: dict[str, str] = {}

    # -- Elementos del modelo ------------------------------------------------
    def diagram(self, name: str, diagram_type: str) -> ET.Element:
        d = _elem(self.model, "Core", "Diagram", new_id())
        _val(d, "name", name)
        _val(d, "diagramType", diagram_type)
        return d

    def block(self, name: str, note: str | None = None) -> ET.Element:
        b = _elem(self.model, "SysML", "Block", new_id())
        _val(b, "name", name)
        if note:
            _val(b, "note", note)
        return b

    def value_type(self, unit: str) -> str:
        if unit in self._vt_cache:
            return self._vt_cache[unit]
        vid = new_id()
        vt = _elem(self.model, "SysML", "ValueType", vid)
        _val(vt, "name", unit or "—")
        self._vt_cache[unit] = vid
        return vid

    def value_property(self, block_id: str, label: str, unit: str,
                       value: str) -> str:
        """Value property (Property + LiteralString de defaultValue)."""
        pid, lsid = new_id(), new_id()
        p = _elem(self.model, "UML", "Property", pid)
        _val(p, "aggregation", "composite")
        _ref(p, "defaultValue", lsid)
        _val(p, "name", label)
        _ref(p, "structuredClassifier", block_id)
        _ref(p, "type", self.value_type(unit))
        ls = _elem(self.model, "UML", "LiteralString", lsid)
        _val(ls, "name", value)
        _ref(ls, "owningProperty", pid)
        _val(ls, "value", value)
        return pid

    def part_property(self, owner_block: str, part_block: str,
                      name: str) -> str:
        """Part property (composición): el aula "contiene" el sensor."""
        pid = new_id()
        p = _elem(self.model, "UML", "Property", pid)
        _val(p, "aggregation", "composite")
        _val(p, "name", name)
        _ref(p, "structuredClassifier", owner_block)
        _ref(p, "type", part_block)  # tipado por otro Block => composición
        return pid

    def block_item(self, diagram_id: str, block_id: str, x: float, y: float,
                   width: float, height: float, show_values: bool) -> str:
        bid = new_id()
        bi = _elem(self.model, "SysML", "BlockItem", bid)
        _val(bi, "matrix", _matrix(x, y))
        _val(bi, "top-left", "(0.0, 0.0)")
        _val(bi, "width", f"{float(width)}")
        _val(bi, "height", f"{float(height)}")
        _ref(bi, "diagram", diagram_id)
        if show_values:
            _val(bi, "show_values", "1")
        _ref(bi, "subject", block_id)
        return bid

    # -- Máquina de estados --------------------------------------------------
    def state_machine(self, name: str) -> tuple[str, str]:
        """Crea StateMachine + su Region. Devuelve ``(stm_id, region_id)``."""
        stm_id, region_id = new_id(), new_id()
        stm = _elem(self.model, "UML", "StateMachine", stm_id)
        _val(stm, "name", name)
        _reflist(stm, "region", [region_id])
        region = _elem(self.model, "UML", "Region", region_id)
        _ref(region, "stateMachine", stm_id)
        return stm_id, region_id

    def state(self, region_id: str, name: str, *, final: bool = False) -> str:
        sid = new_id()
        local = "FinalState" if final else "State"
        s = _elem(self.model, "UML", local, sid)
        if name:
            _val(s, "name", name)
        _ref(s, "container", region_id)
        return sid

    def pseudostate(self, region_id: str, kind: str = "initial") -> str:
        pid = new_id()
        p = _elem(self.model, "UML", "Pseudostate", pid)
        _val(p, "kind", kind)
        _ref(p, "container", region_id)
        return pid

    def transition(self, region_id: str, source: str, target: str,
                   guard: str | None = None) -> str:
        tid = new_id()
        tr = _elem(self.model, "UML", "Transition", tid)
        _ref(tr, "container", region_id)
        _ref(tr, "source", source)
        _ref(tr, "target", target)
        if guard:
            cid, lsid = new_id(), new_id()
            _ref(tr, "guard", cid)
            c = _elem(self.model, "UML", "Constraint", cid)
            _ref(c, "specification", lsid)
            _ref(c, "transition", tid)
            ls = _elem(self.model, "UML", "LiteralString", lsid)
            _val(ls, "name", guard)
            _val(ls, "value", guard)
            _ref(ls, "owningConstraint", cid)
        return tid

    def _vertex_item(self, local: str, diagram_id: str, subject: str,
                     x: float, y: float, width: float, height: float) -> str:
        iid = new_id()
        it = _elem(self.model, "UML", local, iid)
        _val(it, "matrix", _matrix(x, y))
        _val(it, "top-left", "(0.0, 0.0)")
        _val(it, "width", f"{float(width)}")
        _val(it, "height", f"{float(height)}")
        _ref(it, "diagram", diagram_id)
        _ref(it, "subject", subject)
        return iid

    def state_item(self, diagram_id, subject, x, y, w=120.0, h=60.0) -> str:
        return self._vertex_item("StateItem", diagram_id, subject, x, y, w, h)

    def pseudostate_item(self, diagram_id, subject, x, y) -> str:
        return self._vertex_item("PseudostateItem", diagram_id, subject,
                                 x, y, 20.0, 20.0)

    def transition_item(self, diagram_id: str, subject: str,
                        head_item: str, tail_item: str,
                        points: list[tuple[float, float]]) -> str:
        iid = new_id()
        it = _elem(self.model, "UML", "TransitionItem", iid)
        _val(it, "matrix", _matrix(0.0, 0.0))
        _val(it, "points", str([(float(a), float(b)) for a, b in points]))
        _ref(it, "diagram", diagram_id)
        _ref(it, "subject", subject)
        _ref(it, "head-connection", head_item)
        _ref(it, "tail-connection", tail_item)
        return iid

    def write(self, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(self.root).write(
            out_path, encoding="utf-8", xml_declaration=True
        )
        return out_path


def _set_reflist(el: ET.Element, local: str, refids: list[str]) -> None:
    """Añade/crea un reflist en un elemento ya existente."""
    _reflist(el, local, refids)


# ---------------------------------------------------------------------------
# Modelo de demo
# ---------------------------------------------------------------------------

# Métrica de CO2 (para la metadata dt:* del sensor de aula-A).
_CO2_SENSOR = "mhz19b"
_CO2_METRIC = "co2_ppm"


def build_demo_model(out_path: str | Path | None = None) -> Path:
    """Construye el modelo de demo y lo escribe. Devuelve la ruta."""
    out = Path(out_path) if out_path else DEFAULT_OUT
    b = _Builder()

    bdd = b.diagram("BDD - Aulas PI3", "bdd")
    stm_dgm = b.diagram("STM - Ventilación Aula A", "stm")

    bdd_presentations: list[str] = []

    # --- Aulas y sensores ---------------------------------------------------
    for col, (aula_id, aula_name) in enumerate(AULAS):
        aula_x = _X0 + col * _AULA_X_GAP
        aula_block = b.block(
            f"{aula_name} [{aula_id}]",
            f"Aula monitoreada por el gemelo digital.\nIdentificador MQTT: {aula_id}",
        )
        abi = b.block_item(bdd.get("id"), aula_block.get("id"),
                           aula_x, _AULA_Y, 200, 44, show_values=False)
        _set_reflist(aula_block, "presentation", [abi])
        bdd_presentations.append(abi)

        part_prop_ids: list[str] = []
        for row, (key, spec) in enumerate(SENSORS.items()):
            name = f"{spec.model} ({aula_name})"
            note = _description_note(spec)
            if aula_id == "aula-A" and key == _CO2_SENSOR:
                note = (
                    f"{note}\n"
                    f"dt:channel=gemelo/{aula_id}/{key}:{_CO2_METRIC}\n"
                    f"dt:visual=piso.svg#sensor-co2-aulaA"
                )
            sensor_block = b.block(name, note)

            prop_ids: list[str] = []
            for m in spec.metrics:
                prop_ids.append(
                    b.value_property(sensor_block.get("id"), m.label, m.unit, "—")
                )
            _set_reflist(sensor_block, "ownedAttribute", prop_ids)

            # El aula "contiene" el sensor (part property de composición).
            part_prop_ids.append(
                b.part_property(aula_block.get("id"), sensor_block.get("id"), key)
            )

            bi = b.block_item(
                bdd.get("id"), sensor_block.get("id"), aula_x,
                _SENSORS_Y0 + row * _SENSOR_Y_GAP, 220,
                44 + 18 * len(prop_ids), show_values=True,
            )
            _set_reflist(sensor_block, "presentation", [bi])
            bdd_presentations.append(bi)

        _set_reflist(aula_block, "ownedAttribute", part_prop_ids)

    # --- Block de ventilación + máquina de estados --------------------------
    stm_id, region_id = b.state_machine("Ventilación Aula A")
    vent_block = b.block(
        "Ventilacion_AulaA",
        "Subsistema de ventilación del aula A gobernado por una máquina de estados.",
    )
    _set_reflist(vent_block, "ownedBehavior", [stm_id])
    # Enlace inverso behavioredClassifier (HAS_STM).
    _ref(b.model.find(f".//*[@id='{stm_id}']"), "behavioredClassifier",
         vent_block.get("id"))
    vent_bi = b.block_item(
        bdd.get("id"), vent_block.get("id"),
        _X0 + len(AULAS) * _AULA_X_GAP, _AULA_Y, 220, 44, show_values=False,
    )
    _set_reflist(vent_block, "presentation", [vent_bi])
    bdd_presentations.append(vent_bi)

    _set_reflist(bdd, "ownedPresentation", bdd_presentations)

    # Estados (Nominal es el estado por defecto tras el inicial).
    initial = b.pseudostate(region_id, "initial")
    nominal = b.state(region_id, "Nominal")
    forzada = b.state(region_id, "Forzada")
    alarma = b.state(region_id, "Alarma")

    # Transiciones (co2_ppm en ppm). La inicial no lleva guarda.
    transitions = [
        (initial, nominal, None),
        (nominal, forzada, "co2_ppm > 1000"),
        (forzada, alarma, "co2_ppm > 1600"),
        (forzada, nominal, "co2_ppm < 800"),
        (alarma, forzada, "co2_ppm < 1400"),
    ]
    tr_ids: list[tuple[str, str, str]] = []  # (tid, source, target)
    for source, target, guard in transitions:
        tid = b.transition(region_id, source, target, guard)
        tr_ids.append((tid, source, target))

    # subvertex de la región.
    _set_reflist(b.model.find(f".//*[@id='{region_id}']"), "subvertex",
                 [initial, nominal, forzada, alarma])

    # --- Diagrama STM: geometría de estados y transiciones ------------------
    pos = {
        initial: (70, 40),
        nominal: (40, 140),
        forzada: (260, 140),
        alarma: (480, 140),
    }
    centers: dict[str, tuple[float, float]] = {}
    state_items: dict[str, str] = {}
    stm_presentations: list[str] = []

    # Pseudostado inicial.
    ix, iy = pos[initial]
    ii = b.pseudostate_item(stm_dgm.get("id"), initial, ix, iy)
    state_items[initial] = ii
    centers[initial] = (ix + 10, iy + 10)
    _set_reflist(b.model.find(f".//*[@id='{initial}']"), "presentation", [ii])
    stm_presentations.append(ii)

    for sid in (nominal, forzada, alarma):
        sx, sy = pos[sid]
        si = b.state_item(stm_dgm.get("id"), sid, sx, sy)
        state_items[sid] = si
        centers[sid] = (sx + 60, sy + 30)
        _set_reflist(b.model.find(f".//*[@id='{sid}']"), "presentation", [si])
        stm_presentations.append(si)

    for tid, source, target in tr_ids:
        p0, p1 = centers[source], centers[target]
        ti = b.transition_item(
            stm_dgm.get("id"), tid,
            head_item=state_items[source], tail_item=state_items[target],
            points=[p0, p1],
        )
        _set_reflist(b.model.find(f".//*[@id='{tid}']"), "presentation", [ti])
        stm_presentations.append(ti)

    _set_reflist(stm_dgm, "ownedPresentation", stm_presentations)

    return b.write(out)


def _description_note(spec) -> str:
    metricas = ", ".join(f"{m.label} [{m.unit}]" for m in spec.metrics)
    return (f"Sensor {spec.model} — {spec.category}\n"
            f"Interfaz: {spec.interface}\n"
            f"Mide: {metricas}\n{spec.purpose}")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = Path(argv[0]) if argv else DEFAULT_OUT
    path = build_demo_model(out)
    print(f"Modelo de demo escrito en: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
