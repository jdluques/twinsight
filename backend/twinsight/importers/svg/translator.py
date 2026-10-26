"""``SvgTranslator`` — importa planos 2D en SVG como objetos visuales.

Recorre los grupos ``<g>`` del documento y, según las convenciones del
Apéndice F de la ERS, produce:

* Un ``VisualObject`` tipo ``"shape"`` por cada ``<g>`` con ``id`` que no sea
  una capa de estado.
* Las variantes por estado de un objeto (capas hijas con
  ``inkscape:label="estado:<nombre>"`` o ``id`` que empiece con ``estado:``),
  normalizando el nombre del estado.
* La metadata de mapeo embebida (``data-dt-element`` / ``data-dt-channel``).

Usa únicamente la biblioteca estándar (``xml.etree.ElementTree``) para no
añadir dependencias.
"""

from __future__ import annotations

import unicodedata
from xml.etree import ElementTree as ET

from twinsight.importers.base import GraphBatch, GraphNode, ImportReport

# Espacios de nombres relevantes.
_NS_INKSCAPE = "http://www.inkscape.org/namespaces/inkscape"

# Prefijo que identifica una capa de variante por estado.
_PREFIJO_ESTADO = "estado:"


def _local(tag: str) -> str:
    """Nombre local de una etiqueta, sin el ``{namespace}`` de ElementTree."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _inkscape_label(elem: ET.Element) -> str | None:
    """Devuelve el ``inkscape:label`` del elemento, si existe."""
    directo = elem.get(f"{{{_NS_INKSCAPE}}}label")
    if directo is not None:
        return directo
    # Tolerancia a documentos sin el namespace declarado correctamente.
    for clave, valor in elem.attrib.items():
        if _local(clave) == "label":
            return valor
    return None


def normalizar_estado(nombre: str) -> str:
    """Normaliza el nombre de un estado (minúsculas, sin acentos, unificado).

    * Pasa a minúsculas y elimina acentos (NFKD).
    * Unifica espacios y guiones (``-``/``_``) en un único ``-``.
    * Recorta espacios sobrantes.

    Ej.: ``"Forzada "`` -> ``"forzada"``; ``"en-alarma"`` y ``"en alarma"`` ->
    ``"en-alarma"``.
    """
    texto = nombre.strip().lower()
    # Descompone y descarta las marcas diacríticas (acentos).
    texto = "".join(
        c for c in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(c)
    )
    # Unifica separadores (espacios, guiones, guiones bajos) en "-".
    partes = texto.replace("_", " ").replace("-", " ").split()
    return "-".join(partes)


def _estado_de_capa(elem: ET.Element) -> str | None:
    """Si ``elem`` es una capa ``estado:*``, devuelve el estado normalizado."""
    label = _inkscape_label(elem)
    if label and label.strip().lower().startswith(_PREFIJO_ESTADO):
        return normalizar_estado(label.split(":", 1)[1])
    gid = elem.get("id")
    if gid and gid.strip().lower().startswith(_PREFIJO_ESTADO):
        return normalizar_estado(gid.split(":", 1)[1])
    return None


class SvgTranslator:
    """Traductor de planos 2D en formato SVG (Diseño §6.1)."""

    kind = "svg"

    def can_import(self, filename: str, head: bytes) -> bool:
        """Reconoce por extensión ``.svg`` o por cabecera ``<svg``/``<?xml``."""
        if filename and filename.lower().endswith(".svg"):
            return True
        muestra = head[:512].lstrip()
        return muestra.startswith(b"<svg") or muestra.startswith(b"<?xml")

    def translate(self, data: bytes, *, source_id: str) -> GraphBatch:
        """Convierte los bytes del SVG en un :class:`GraphBatch`."""
        report = ImportReport()
        nodes: list[GraphNode] = []

        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            report.warnings.append(f"SVG no parseable: {exc}")
            return GraphBatch(source_id=source_id, kind=self.kind, report=report)

        for elem in root.iter():
            if _local(elem.tag) != "g":
                continue

            # Las capas de estado se procesan como variantes de su padre,
            # nunca como objetos visuales independientes.
            if _estado_de_capa(elem) is not None:
                continue

            gid = elem.get("id")
            if not gid:
                # Grupo de decoración/arquitectura sin identidad: se ignora
                # pero se reporta para transparencia (RF-027).
                etiqueta = _inkscape_label(elem)
                report.unconverted_entities.append(
                    f"<g>{f' ({etiqueta})' if etiqueta else ' sin id'}"
                )
                continue

            nodes.append(self._objeto_visual(elem, gid, source_id))

        report.visual_objects = len(nodes)
        return GraphBatch(
            source_id=source_id, kind=self.kind, nodes=nodes, report=report
        )

    # ------------------------------------------------------------------
    def _objeto_visual(
        self, elem: ET.Element, gid: str, source_id: str
    ) -> GraphNode:
        """Construye el ``GraphNode`` (VisualObject shape) de un grupo con id."""
        variantes = self._variantes(elem)

        properties: dict = {
            "representation_id": source_id,
            "kind": "shape",
            "severity": "normal",
            "variants": variantes,
            "label": _inkscape_label(elem) or gid,
        }

        dt_element = elem.get("data-dt-element")
        if dt_element:
            properties["dt_element"] = dt_element
        dt_channel = elem.get("data-dt-channel")
        if dt_channel:
            properties["dt_channel"] = dt_channel

        return GraphNode(
            label="VisualObject",
            origin_id=gid,
            properties=properties,
        )

    def _variantes(self, elem: ET.Element) -> list[dict]:
        """Lista de ``{estado, selector}`` de las capas hijas ``estado:*``."""
        variantes: list[dict] = []
        for indice, hijo in enumerate(list(elem)):
            estado = _estado_de_capa(hijo)
            if estado is None:
                continue
            selector = hijo.get("id") or str(indice)
            variantes.append({"estado": estado, "selector": selector})
        return variantes
