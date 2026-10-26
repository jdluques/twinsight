"""Contrato común de los traductores de importación.

Toda fuente (``.gaphor``, SVG, DXF, imagen; IFC/glTF en fase 2) entra por un
**traductor** que produce un :class:`GraphBatch` — la representación intermedia
que ``modelgraph/`` aplica al grafo canónico mediante upsert idempotente por
``origin_id`` (Diseño §6.1). Soportar un formato nuevo = implementar
:class:`Translator`; el resto de la plataforma no cambia (RF-018, RF-027).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """Un nodo del grafo canónico (Diseño §3.1)."""

    label: str  # Element | StateMachine | State | Transition | VisualObject | Channel | Diagram
    sub_labels: list[str] = Field(default_factory=list)  # p.ej. ["Block"] para un Element
    origin_id: str  # identificador estable de la fuente (gaphor_id, id SVG, ...)
    properties: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """Una arista del grafo canónico (Diseño §3.1)."""

    type: str  # CONTAINS | RELATES | HAS_STM | HAS_STATE | FROM | TO | GUARD_DEPENDS_ON | MAPPED_TO | FAILURE_MODE_OF
    source_origin_id: str
    target_origin_id: str
    properties: dict = Field(default_factory=dict)  # p.ej. {"kind": "association"}


class DiagramItem(BaseModel):
    """Presentación de un elemento en un diagrama (geometría para el render)."""

    subject_origin_id: str | None = None
    item_type: str  # BlockItem | PropertyItem | StateItem | TransitionItem | ...
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    waypoints: list[list[float]] = Field(default_factory=list)  # conectores
    label: str | None = None


class Diagram(BaseModel):
    """Un diagrama importado con sus ítems posicionados (RF-011, RF-012)."""

    origin_id: str
    name: str
    diagram_type: str = "bdd"  # bdd | ibd | stm | req | pkg
    items: list[DiagramItem] = Field(default_factory=list)


class ImportReport(BaseModel):
    """Resumen de una importación (se devuelve al cliente y se guarda)."""

    elements: int = 0
    diagrams: int = 0
    visual_objects: int = 0
    guard_warnings: list[str] = Field(default_factory=list)
    unconverted_entities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    orphaned_mappings: list[str] = Field(default_factory=list)


class GraphBatch(BaseModel):
    """Lote idempotente producido por un traductor para una fuente."""

    source_id: str
    kind: str  # gaphor | svg | dxf | image | ifc | gltf
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    diagrams: list[Diagram] = Field(default_factory=list)
    report: ImportReport = Field(default_factory=ImportReport)


@runtime_checkable
class Translator(Protocol):
    """Interfaz de un traductor de importación (Diseño §6.1)."""

    kind: str

    def can_import(self, filename: str, head: bytes) -> bool:
        """¿Este traductor reconoce el archivo (por extensión/cabecera)?"""
        ...

    def translate(self, data: bytes, *, source_id: str) -> GraphBatch:
        """Convierte los bytes de la fuente en un :class:`GraphBatch`."""
        ...
