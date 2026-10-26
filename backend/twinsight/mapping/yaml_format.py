"""Import/export del fichero ``mappings.yaml`` (Apéndice C de la ERS).

El fichero es la forma versionable y editable a mano de los mapeos tripartitos
de un proyecto. Estructura::

    version: 1
    project: <nombre>
    conventions:
      - {model: "{name}", visual: "#{name}", channel: "gemelo/{grupo}/{name}:{metric}"}
    mappings:
      - {model: SensorCO2_AulaA, visual: piso.svg#sensor-co2-aulaA,
         channel: "gemelo/aula-A/mhz19b:co2_ppm", note: "..."}

Cada entrada de ``mappings`` debe tener **al menos dos** extremos (model,
visual, channel). Este módulo solo *parsea, valida y serializa* la estructura:
la resolución de cada extremo (nombre/id) contra el grafo la hace el API.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

VERSION = 1


@dataclass
class MappingEntry:
    """Una entrada de ``mappings.yaml`` ya parseada y validada.

    Al menos dos de ``model``/``visual``/``channel`` son no nulos (2 de 3).
    """

    model: str | None = None
    visual: str | None = None
    channel: str | None = None
    note: str | None = None

    def endpoints(self) -> int:
        return sum(1 for value in (self.model, self.visual, self.channel) if value)


def _entry_to_dict(entry: MappingEntry | dict) -> dict:
    """Normaliza una entrada (dict de mapeo o :class:`MappingEntry`) a sus claves YAML.

    Acepta tanto las claves del grafo (``element_id``/``visual_object_id``/
    ``channel_id``) como las del YAML (``model``/``visual``/``channel``).
    """
    if isinstance(entry, MappingEntry):
        model, visual, channel, note = (
            entry.model, entry.visual, entry.channel, entry.note
        )
    else:
        model = entry.get("model") or entry.get("element_id")
        visual = entry.get("visual") or entry.get("visual_object_id")
        channel = entry.get("channel") or entry.get("channel_id")
        note = entry.get("note")

    data: dict = {}
    if model:
        data["model"] = model
    if visual:
        data["visual"] = visual
    if channel:
        data["channel"] = channel
    if note:
        data["note"] = note
    return data


def export_yaml(
    project_id: str,
    mappings: list[MappingEntry | dict],
    *,
    conventions: list[dict] | None = None,
) -> str:
    """Serializa los mapeos de un proyecto a texto ``mappings.yaml`` (Apéndice C)."""
    document: dict = {"version": VERSION, "project": project_id}
    if conventions:
        document["conventions"] = conventions
    document["mappings"] = [_entry_to_dict(m) for m in mappings]
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def import_yaml(text: str) -> tuple[list[dict], list[MappingEntry]]:
    """Parsea y valida ``mappings.yaml``.

    Devuelve ``(conventions, entries)``. Cada entrada se valida con ≥2 extremos;
    si alguna tiene menos, se lanza :class:`ValueError`.
    """
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError("mappings.yaml debe ser un mapa en el nivel superior")

    version = document.get("version")
    if version != VERSION:
        raise ValueError(f"versión no soportada: {version!r} (se espera {VERSION})")

    conventions = document.get("conventions") or []
    if not isinstance(conventions, list):
        raise ValueError("'conventions' debe ser una lista")

    raw_mappings = document.get("mappings")
    if not isinstance(raw_mappings, list):
        raise ValueError("'mappings' debe ser una lista")

    entries: list[MappingEntry] = []
    for index, raw in enumerate(raw_mappings):
        if not isinstance(raw, dict):
            raise ValueError(f"la entrada {index} no es un mapa")
        entry = MappingEntry(
            model=raw.get("model"),
            visual=raw.get("visual"),
            channel=raw.get("channel"),
            note=raw.get("note"),
        )
        if entry.endpoints() < 2:
            raise ValueError(
                f"la entrada {index} necesita al menos 2 extremos "
                "(model/visual/channel)"
            )
        entries.append(entry)

    return conventions, entries
