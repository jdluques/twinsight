"""Parseo del XML ``.gaphor`` (versión de formato 4) a estructuras planas.

El formato es muy regular: la raíz es ``<gaphor version="4">`` con un hijo
``<model>`` que contiene una **lista plana** de elementos. Cada elemento tiene
un atributo ``id`` y sus datos/relaciones se expresan como:

* valor escalar: ``<campo><val>texto</val></campo>``
* referencia simple: ``<campo><ref refid="ID"/></campo>``
* lista de referencias: ``<campo><reflist><ref refid="ID"/>...</reflist></campo>``

Las relaciones son bidireccionales en el modelo real de Gaphor, pero aquí solo
necesitamos leerlas. Este módulo no interpreta la semántica (eso lo hace
``translator``): únicamente envuelve cada elemento en :class:`GElement` con
helpers de lectura y construye el índice ``by_id``.

Los helpers ``_Q/_D/_get_val/_single_refid/_reflist_ids`` y el patrón
``by_id = {el.get('id'): el for el in model}`` están adaptados del prototipo
PI3 (``server/gemelo/gaphor_xml.py``).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# --- Namespaces del formato .gaphor (idénticos al prototipo) ---------------
NS: dict[str, str] = {
    "": "https://gaphor.org/model",
    "Core": "https://gaphor.org/modelinglanguage/Core",
    "UML": "https://gaphor.org/modelinglanguage/UML",
    "SysML": "https://gaphor.org/modelinglanguage/SysML",
}
# Mapa inverso URI -> prefijo, para resolver la "familia" de cada tag.
_URI_TO_PREFIX: dict[str, str] = {uri: prefix for prefix, uri in NS.items()}


def _Q(prefix: str, local: str) -> str:
    """Nombre cualificado ``{uri}local`` para un prefijo conocido."""
    return f"{{{NS[prefix]}}}{local}"


def _D(local: str) -> str:
    """Nombre cualificado en el namespace por defecto (``model``)."""
    return f"{{{NS['']}}}{local}"


def _split_tag(tag: str) -> tuple[str, str]:
    """Separa un tag ``{uri}local`` en ``(prefijo, local)``."""
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        return _URI_TO_PREFIX.get(uri, uri), local
    return "", tag


# ---------------------------------------------------------------------------
# Helpers de lectura sobre un ET.Element (reutilizables)
# ---------------------------------------------------------------------------

def _get_val(el: ET.Element, local: str) -> str | None:
    node = el.find(f"{_D(local)}/{_D('val')}")
    return node.text if node is not None else None


def _single_refid(el: ET.Element, local: str) -> str | None:
    node = el.find(f"{_D(local)}/{_D('ref')}")
    return node.get("refid") if node is not None else None


def _reflist_ids(el: ET.Element, local: str) -> list[str]:
    field_el = el.find(_D(local))
    if field_el is None:
        return []
    refs = field_el.findall(f"{_D('reflist')}/{_D('ref')}")
    return [r.get("refid") for r in refs if r.get("refid")]


@dataclass
class GElement:
    """Un elemento del modelo ``.gaphor`` con helpers de lectura.

    ``prefix`` es la familia del tag (``SysML``/``UML``/``Core``/``""``) y
    ``local`` el nombre local (``Block``, ``Transition``, ``BlockItem``...).
    """

    id: str | None
    prefix: str
    local: str
    el: ET.Element = field(repr=False)

    @property
    def qname(self) -> str:
        return f"{self.prefix}:{self.local}" if self.prefix else self.local

    def val(self, local: str) -> str | None:
        return _get_val(self.el, local)

    def ref(self, local: str) -> str | None:
        return _single_refid(self.el, local)

    def reflist(self, local: str) -> list[str]:
        return _reflist_ids(self.el, local)

    def has_field(self, local: str) -> bool:
        return self.el.find(_D(local)) is not None

    @property
    def is_presentation(self) -> bool:
        """Un ítem de diagrama tiene siempre un campo ``diagram`` (ref)."""
        return self.el.find(_D("diagram")) is not None


@dataclass
class GaphorModel:
    """Modelo ``.gaphor`` parseado: lista plana + índice por ``id``."""

    version: str
    gaphor_version: str | None
    elements: list[GElement]
    by_id: dict[str, GElement]

    def of_kind(self, *locals_: str) -> list[GElement]:
        """Elementos cuyo nombre local esté en ``locals_``."""
        wanted = set(locals_)
        return [e for e in self.elements if e.local in wanted]

    def get(self, oid: str | None) -> GElement | None:
        return self.by_id.get(oid) if oid else None


def parse(data: bytes) -> GaphorModel:
    """Parsea los bytes de un ``.gaphor`` a :class:`GaphorModel`.

    Ante XML corrupto o una raíz que no sea ``<gaphor>`` lanza ``ValueError``
    sin dejar estado parcial (RF-017: fallo claro, no silencioso).
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f".gaphor con XML corrupto: {exc}") from exc

    _, root_local = _split_tag(root.tag)
    if root_local != "gaphor":
        raise ValueError(
            f"La raíz del documento no es <gaphor> (es <{root_local}>); "
            "no parece un modelo .gaphor."
        )

    version = root.get("version") or ""
    model = root.find(_D("model"))
    if model is None:
        raise ValueError(".gaphor sin elemento <model>: archivo incompleto.")

    elements: list[GElement] = []
    by_id: dict[str, GElement] = {}
    for child in model:
        prefix, local = _split_tag(child.tag)
        ge = GElement(id=child.get("id"), prefix=prefix, local=local, el=child)
        elements.append(ge)
        if ge.id:
            by_id[ge.id] = ge

    return GaphorModel(
        version=version,
        gaphor_version=root.get("gaphor-version"),
        elements=elements,
        by_id=by_id,
    )
