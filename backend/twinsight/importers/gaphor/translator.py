"""Traductor ``.gaphor`` → :class:`GraphBatch` (contrato ``Translator``).

Extrae del modelo SysML/UML:

* **Elements**: ``SysML:Block`` (sub_label ``Block``), ``UML:Property``
  (value properties, sub_label ``Property``) y ``UML:Package`` si existe.
  De cada Block se leen nombre, value properties (métricas con unidad y valor)
  y la metadata ``dt:*`` incrustada en la nota (``dt:channel`` / ``dt:visual``).
* **Relaciones**: ``CONTAINS`` (aula→sensores por composición: part properties
  del Block del aula tipadas con el Block del sensor; y Package→miembros).
* **Máquina de estados**: ``StateMachine`` + ``State`` (incl. pseudostados y
  estados finales) + ``Transition`` con ``guard_text``; aristas ``HAS_STM``
  (Block→StateMachine por ``ownedBehavior``), ``HAS_STATE`` (StateMachine→State)
  y ``FROM``/``TO`` (Transition→vértice origen/destino).
* **Diagramas**: con geometría de cada ítem (matriz→x,y, width/height, y
  ``points``→waypoints para los conectores).

NO resuelve ``GUARD_DEPENDS_ON`` (lo hará el módulo *behavior* cuando existan
canales). Tipos no reconocidos no abortan la importación: se emiten como
``Element`` con sub_label ``Unknown`` y se anotan en el reporte (RF-017).
"""

from __future__ import annotations

import ast

from ..base import (
    Diagram,
    DiagramItem,
    GraphBatch,
    GraphEdge,
    GraphNode,
    ImportReport,
)
from . import parser
from .parser import GaphorModel, GElement

# Tipos de elemento del *modelo* (no presentaciones) que sabemos traducir.
_MODEL_KNOWN = {
    "Block", "Property", "Package", "StateMachine", "Region",
    "State", "FinalState", "Pseudostate", "Transition",
}
# Helpers consumidos por otros elementos; no generan nodo propio.
_MODEL_HELPERS = {"StyleSheet", "ValueType", "LiteralString", "Constraint", "Diagram"}

# Tipos de diagrama válidos en el grafo canónico.
_DIAGRAM_TYPES = {"bdd", "ibd", "stm", "req", "pkg"}


class GaphorTranslator:
    """Implementa :class:`~twinsight.importers.base.Translator` para ``.gaphor``."""

    kind = "gaphor"

    # -- Detección -----------------------------------------------------------
    def can_import(self, filename: str, head: bytes) -> bool:
        """Reconoce por extensión ``.gaphor`` o por cabecera ``<gaphor``."""
        if filename and filename.lower().endswith(".gaphor"):
            return True
        return b"<gaphor" in (head or b"")[:4096]

    # -- Traducción ----------------------------------------------------------
    def translate(self, data: bytes, *, source_id: str) -> GraphBatch:
        model = parser.parse(data)  # lanza ValueError si el XML está corrupto

        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        report = ImportReport()

        # Índices auxiliares -------------------------------------------------
        block_ids = {e.id for e in model.of_kind("Block")}
        value_type_names = {
            e.id: (e.val("name") or "") for e in model.of_kind("ValueType")
        }

        self._translate_blocks(model, block_ids, value_type_names, nodes, edges)
        self._translate_packages(model, nodes, edges)
        self._translate_state_machines(model, block_ids, nodes, edges, report)
        self._translate_unknown(model, nodes, report)

        diagrams = self._translate_diagrams(model)

        report.elements = sum(
            1 for n in nodes
            if n.label in ("Element", "StateMachine", "State", "Transition")
        )
        report.diagrams = len(diagrams)

        return GraphBatch(
            source_id=source_id,
            kind=self.kind,
            nodes=nodes,
            edges=edges,
            diagrams=diagrams,
            report=report,
        )

    # -- Blocks + value/part properties -------------------------------------
    def _translate_blocks(self, model, block_ids, value_type_names,
                          nodes, edges) -> None:
        for block in model.of_kind("Block"):
            if not block.id:
                continue
            name = block.val("name") or ""
            note = block.val("note")
            props: dict = {"name": name, "kind": "Block"}
            if note:
                props["note"] = note
                dt = _parse_dt_metadata(note)
                props.update(dt)

            value_properties: list[dict] = []
            for pid in block.reflist("ownedAttribute"):
                prop = model.get(pid)
                if prop is None or prop.local != "Property":
                    continue
                type_ref = prop.ref("type")
                if type_ref in block_ids:
                    # Part property (composición): el aula "contiene" el sensor.
                    edges.append(GraphEdge(
                        type="CONTAINS",
                        source_origin_id=block.id,
                        target_origin_id=type_ref,
                        properties={"kind": "composite", "via": "part-property"},
                    ))
                    continue
                # Value property (métrica): tipada por un ValueType (o sin tipo).
                pname = prop.val("name") or ""
                unit = value_type_names.get(type_ref, "") if type_ref else ""
                value = self._property_value(model, prop)
                value_properties.append(
                    {"name": pname, "unit": unit, "value": value}
                )
                nodes.append(GraphNode(
                    label="Element",
                    sub_labels=["Property"],
                    origin_id=prop.id,
                    properties={"name": pname, "kind": "Property",
                                "unit": unit, "value": value},
                ))
                edges.append(GraphEdge(
                    type="CONTAINS",
                    source_origin_id=block.id,
                    target_origin_id=prop.id,
                    properties={"kind": "value-property"},
                ))

            props["value_properties"] = value_properties
            nodes.append(GraphNode(
                label="Element",
                sub_labels=["Block"],
                origin_id=block.id,
                properties=props,
            ))

    @staticmethod
    def _property_value(model: GaphorModel, prop: GElement) -> str | None:
        """Valor de una value property vía su ``defaultValue`` → LiteralString."""
        ls = model.get(prop.ref("defaultValue"))
        if ls is None:
            return None
        return ls.val("value") or ls.val("name")

    # -- Packages ------------------------------------------------------------
    def _translate_packages(self, model, nodes, edges) -> None:
        for pkg in model.of_kind("Package"):
            if not pkg.id:
                continue
            nodes.append(GraphNode(
                label="Element",
                sub_labels=["Package"],
                origin_id=pkg.id,
                properties={"name": pkg.val("name") or "", "kind": "Package"},
            ))
            for mid in pkg.reflist("packagedElement"):
                edges.append(GraphEdge(
                    type="CONTAINS",
                    source_origin_id=pkg.id,
                    target_origin_id=mid,
                    properties={"kind": "package-member"},
                ))

    # -- Máquinas de estado --------------------------------------------------
    def _translate_state_machines(self, model, block_ids, nodes, edges,
                                  report) -> None:
        # region_id -> state_machine_id (una región pertenece a una STM).
        region_to_stm: dict[str, str] = {}
        for stm in model.of_kind("StateMachine"):
            for rid in stm.reflist("region"):
                region_to_stm[rid] = stm.id

        # HAS_STM: un Block "posee" la STM vía ownedBehavior.
        for block in model.of_kind("Block"):
            for bid in block.reflist("ownedBehavior"):
                tgt = model.get(bid)
                if tgt is not None and tgt.local == "StateMachine":
                    edges.append(GraphEdge(
                        type="HAS_STM",
                        source_origin_id=block.id,
                        target_origin_id=bid,
                    ))

        for stm in model.of_kind("StateMachine"):
            if not stm.id:
                continue
            nodes.append(GraphNode(
                label="StateMachine",
                origin_id=stm.id,
                properties={"name": stm.val("name") or "", "kind": "StateMachine"},
            ))

        # Vértices: State / FinalState / Pseudostate.
        vertex_kind = {"State": "state", "FinalState": "final"}
        for vtx in model.of_kind("State", "FinalState", "Pseudostate"):
            if not vtx.id:
                continue
            if vtx.local == "Pseudostate":
                kind = vtx.val("kind") or "initial"
            else:
                kind = vertex_kind[vtx.local]
            stm_id = region_to_stm.get(vtx.ref("container"))
            name = vtx.val("name") or ("" if kind != "initial" else "inicial")
            sub = ["Pseudostate"] if vtx.local == "Pseudostate" else (
                ["FinalState"] if vtx.local == "FinalState" else [])
            nodes.append(GraphNode(
                label="State",
                sub_labels=sub,
                origin_id=vtx.id,
                properties={"name": name, "kind": kind},
            ))
            if stm_id:
                edges.append(GraphEdge(
                    type="HAS_STATE",
                    source_origin_id=stm_id,
                    target_origin_id=vtx.id,
                ))

        # Transiciones con guarda.
        for tr in model.of_kind("Transition"):
            if not tr.id:
                continue
            guard_text = self._guard_text(model, tr)
            source = tr.ref("source")
            target = tr.ref("target")
            props: dict = {"kind": "Transition"}
            if guard_text:
                props["guard_text"] = guard_text
            name = tr.val("name")
            if name:
                props["name"] = name
            nodes.append(GraphNode(
                label="Transition", origin_id=tr.id, properties=props,
            ))
            if source:
                edges.append(GraphEdge(
                    type="FROM", source_origin_id=tr.id, target_origin_id=source,
                ))
            if target:
                edges.append(GraphEdge(
                    type="TO", source_origin_id=tr.id, target_origin_id=target,
                ))
            if tr.has_field("guard") and not guard_text:
                report.guard_warnings.append(
                    f"Transición {tr.id}: guarda sin texto resoluble."
                )

    @staticmethod
    def _guard_text(model: GaphorModel, tr: GElement) -> str | None:
        """guard → Constraint → specification → LiteralString.value."""
        constraint = model.get(tr.ref("guard"))
        if constraint is None:
            return None
        spec = model.get(constraint.ref("specification"))
        if spec is not None:
            text = spec.val("value") or spec.val("name")
            if text:
                return text
        # Compatibilidad: guarda como texto directo en el Constraint.
        return constraint.val("specification") or constraint.val("name")

    # -- Tipos no reconocidos (no abortar — RF-017) --------------------------
    def _translate_unknown(self, model, nodes, report) -> None:
        for e in model.elements:
            if e.local in _MODEL_KNOWN or e.local in _MODEL_HELPERS:
                continue
            if e.is_presentation:
                continue  # las presentaciones se tratan como ítems de diagrama
            if not e.id:
                continue
            nodes.append(GraphNode(
                label="Element",
                sub_labels=["Unknown"],
                origin_id=e.id,
                properties={"name": e.val("name") or "", "gaphor_type": e.qname},
            ))
            if e.qname not in report.unconverted_entities:
                report.unconverted_entities.append(e.qname)
            report.warnings.append(
                f"Tipo no reconocido {e.qname} (id={e.id}); importado como Unknown."
            )

    # -- Diagramas con geometría --------------------------------------------
    def _translate_diagrams(self, model) -> list[Diagram]:
        diagrams: list[Diagram] = []
        for dgm in model.of_kind("Diagram"):
            if not dgm.id:
                continue
            items: list[DiagramItem] = []
            has_state_item = False
            for pres in model.elements:
                if not pres.is_presentation or pres.ref("diagram") != dgm.id:
                    continue
                if pres.local in ("StateItem", "StateMachineItem",
                                   "PseudostateItem", "FinalStateItem"):
                    has_state_item = True
                items.append(self._diagram_item(pres))

            diagram_type = (dgm.val("diagramType") or "").strip().lower()
            if diagram_type not in _DIAGRAM_TYPES:
                diagram_type = "stm" if has_state_item else "bdd"

            diagrams.append(Diagram(
                origin_id=dgm.id,
                name=dgm.val("name") or "",
                diagram_type=diagram_type,
                items=items,
            ))
        return diagrams

    @staticmethod
    def _diagram_item(pres: GElement) -> DiagramItem:
        x, y = _parse_matrix_xy(pres.val("matrix"))
        width = _to_float(pres.val("width"))
        height = _to_float(pres.val("height"))
        waypoints = _parse_points(pres.val("points"), x, y)
        return DiagramItem(
            subject_origin_id=pres.ref("subject"),
            item_type=pres.local,
            x=x,
            y=y,
            width=width,
            height=height,
            waypoints=waypoints,
            label=pres.val("name"),
        )


# ---------------------------------------------------------------------------
# Utilidades de parseo de geometría / metadata
# ---------------------------------------------------------------------------

def _to_float(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_matrix_xy(matrix: str | None) -> tuple[float, float]:
    """``(a, b, c, d, e, f)`` → traslación ``(e, f)`` = posición en el diagrama."""
    if not matrix:
        return 0.0, 0.0
    try:
        tup = ast.literal_eval(matrix)
        if isinstance(tup, (tuple, list)) and len(tup) >= 6:
            return float(tup[4]), float(tup[5])
    except (ValueError, SyntaxError):
        pass
    return 0.0, 0.0


def _parse_points(points: str | None, x0: float, y0: float) -> list[list[float]]:
    """``[(px, py), ...]`` (relativos a la matriz) → waypoints absolutos."""
    if not points:
        return []
    try:
        seq = ast.literal_eval(points)
    except (ValueError, SyntaxError):
        return []
    out: list[list[float]] = []
    for p in seq or []:
        try:
            out.append([x0 + float(p[0]), y0 + float(p[1])])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _parse_dt_metadata(note: str) -> dict:
    """Extrae líneas ``dt:channel=...`` / ``dt:visual=...`` de una nota.

    Devuelve ``{'dt_channel': ..., 'dt_visual': ...}`` con solo las claves
    presentes. Permite el mapeo tripartito (Element↔Channel↔VisualObject).
    """
    out: dict = {}
    for raw_line in note.splitlines():
        line = raw_line.strip()
        if line.startswith("dt:channel="):
            out["dt_channel"] = line.split("=", 1)[1].strip()
        elif line.startswith("dt:visual="):
            out["dt_visual"] = line.split("=", 1)[1].strip()
    return out
