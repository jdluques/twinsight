"""Pruebas del servicio de mapeo tripartito (sugerencias, YAML, resolución).

Usan un ``ModelGraph`` falso en memoria que registra las llamadas relevantes,
para no depender de Neo4j ni de ningún driver.
"""

from __future__ import annotations

import pytest

from twinsight.mapping import (
    MappingEntry,
    MappingService,
    export_yaml,
    import_yaml,
)


# --------------------------------------------------------------------------- #
# ModelGraph falso
# --------------------------------------------------------------------------- #
class FakeGraph:
    """Implementación mínima en memoria de las operaciones que usa el servicio."""

    def __init__(self, elements: list[dict] | None = None) -> None:
        self._elements = elements or []
        self._mappings: list[dict] = []
        self.create_calls: list[dict] = []
        self._counter = 0

    async def get_elements(self, project_id: str, kind=None, q=None) -> list[dict]:
        return list(self._elements)

    async def create_mapping(
        self, project_id, *, element_id=None, visual_object_id=None, channel_id=None
    ) -> str:
        self._counter += 1
        mapping_id = f"m{self._counter}"
        call = {
            "id": mapping_id,
            "element_id": element_id,
            "visual_object_id": visual_object_id,
            "channel_id": channel_id,
        }
        self.create_calls.append(call)
        self._mappings.append(call)
        return mapping_id

    async def delete_mapping(self, project_id, mapping_id) -> None:
        self._mappings = [m for m in self._mappings if m["id"] != mapping_id]

    async def list_mappings(self, project_id) -> list[dict]:
        return list(self._mappings)

    async def resolve(self, project_id, *, element_id=None, visual_object_id=None,
                      channel_id=None) -> dict:
        return {"resolved": True}

    async def resolution_map(self, project_id) -> dict:
        return {"map": True}

    def seed_mapping(self, **kwargs) -> None:
        """Inyecta un mapeo directamente (como si viniera del grafo)."""
        self._counter += 1
        self._mappings.append({"id": f"seed{self._counter}", **kwargs})


PROJECT = "proj-1"


# --------------------------------------------------------------------------- #
# Sugerencias
# --------------------------------------------------------------------------- #
def test_sugerencia_por_metadata_no_se_crea_sola():
    """Element con dt_channel → sugerencia score 1.0, confianza alta, sin crear."""
    graph = FakeGraph()
    service = MappingService(graph=graph)

    elements = [
        {
            "origin_id": "SensorCO2_AulaA",
            "name": "SensorCO2_AulaA",
            "properties": {"dt_channel": "gemelo/aula-A/mhz19b:co2_ppm"},
        }
    ]
    channels = [
        {"id": "ch-co2", "topic": "gemelo/aula-A/mhz19b", "metric": "co2_ppm"}
    ]

    suggestions = service.suggest(PROJECT, elements, [], channels)

    assert len(suggestions) == 1
    sug = suggestions[0]
    assert sug.element_id == "SensorCO2_AulaA"
    assert sug.channel_id == "ch-co2"
    assert sug.score == 1.0
    assert sug.confidence == "alta"
    assert "metadata" in sug.evidence
    # RF-042: sugerir NO crea mapeos.
    assert graph.create_calls == []


def test_sugerencia_por_convencion_de_nombres():
    """Nombres coincidentes → sugerencia score 0.6, confianza media, sin crear."""
    graph = FakeGraph()
    service = MappingService(graph=graph)

    elements = [{"origin_id": "e-temp", "name": "Temperatura"}]
    visual_objects = [{"id": "plano.svg#temperatura", "label": "Temperatura"}]
    channels = [{"id": "ch-temp", "topic": "gemelo/aula/dht22", "metric": "temperatura"}]

    suggestions = service.suggest(PROJECT, elements, visual_objects, channels)

    # Element↔Visual y Element↔Channel por nombre.
    assert len(suggestions) == 2
    assert all(s.score == 0.6 and s.confidence == "media" for s in suggestions)
    assert all("nombre" in s.evidence for s in suggestions)
    pares = {(s.element_id, s.visual_object_id, s.channel_id) for s in suggestions}
    assert ("e-temp", "plano.svg#temperatura", None) in pares
    assert ("e-temp", None, "ch-temp") in pares
    assert graph.create_calls == []


def test_metadata_tiene_prioridad_sobre_convencion():
    """Si metadata y nombre proponen el mismo par, gana metadata (score 1.0)."""
    graph = FakeGraph()
    service = MappingService(graph=graph)

    elements = [
        {
            "origin_id": "temp",
            "name": "temp",
            "properties": {"dt_channel": "ch-temp"},
        }
    ]
    channels = [{"id": "ch-temp", "topic": "gemelo/aula/dht22", "metric": "temp"}]

    suggestions = service.suggest(PROJECT, elements, [], channels)

    assert len(suggestions) == 1
    assert suggestions[0].score == 1.0
    assert suggestions[0].confidence == "alta"


# --------------------------------------------------------------------------- #
# Confirmación
# --------------------------------------------------------------------------- #
async def test_confirm_crea_el_mapeo():
    """confirm crea el mapeo vía create_mapping (el fake registra la llamada)."""
    graph = FakeGraph()
    service = MappingService(graph=graph)

    elements = [
        {
            "origin_id": "SensorCO2",
            "name": "SensorCO2",
            "properties": {"dt_channel": "ch-co2"},
        }
    ]
    channels = [{"id": "ch-co2", "topic": "gemelo/a/mhz19b", "metric": "co2"}]

    suggestions = service.suggest(PROJECT, elements, [], channels)
    created = await service.confirm(PROJECT, suggestions)

    assert created == ["m1"]
    assert len(graph.create_calls) == 1
    assert graph.create_calls[0]["element_id"] == "SensorCO2"
    assert graph.create_calls[0]["channel_id"] == "ch-co2"


async def test_confirm_ignora_sugerencia_con_un_solo_extremo():
    """Una sugerencia con <2 extremos se ignora (no crea nada)."""
    from twinsight.mapping import Suggestion

    graph = FakeGraph()
    service = MappingService(graph=graph)

    created = await service.confirm(
        PROJECT, [Suggestion(element_id="solo", score=0.6)]
    )

    assert created == []
    assert graph.create_calls == []


async def test_create_rechaza_menos_de_dos_extremos():
    graph = FakeGraph()
    service = MappingService(graph=graph)
    with pytest.raises(ValueError):
        await service.create(PROJECT, element_id="solo")


async def test_list_anota_completitud_y_origen():
    graph = FakeGraph()
    service = MappingService(graph=graph)

    await service.create(
        PROJECT, element_id="e", visual_object_id="v", channel_id="c", origin="manual"
    )
    await service.create(PROJECT, element_id="e2", channel_id="c2", origin="auto")

    listed = await service.list(PROJECT)
    by_id = {m["id"]: m for m in listed}

    assert by_id["m1"]["completeness"] == "3/3"
    assert by_id["m1"]["origin"] == "manual"
    assert by_id["m2"]["completeness"] == "2/3"
    assert by_id["m2"]["origin"] == "auto"


# --------------------------------------------------------------------------- #
# YAML
# --------------------------------------------------------------------------- #
def test_roundtrip_yaml():
    """export → import produce las mismas entradas (Apéndice C)."""
    conventions = [
        {"model": "{name}", "visual": "#{name}",
         "channel": "gemelo/{grupo}/{name}:{metric}"}
    ]
    entries = [
        MappingEntry(
            model="SensorCO2_AulaA",
            visual="piso.svg#sensor-co2-aulaA",
            channel="gemelo/aula-A/mhz19b:co2_ppm",
            note="sensor principal",
        ),
        MappingEntry(model="SensorTemp", channel="gemelo/aula-A/dht22:temp"),
    ]

    text = export_yaml("mi-proyecto", entries, conventions=conventions)
    out_conventions, out_entries = import_yaml(text)

    assert out_conventions == conventions
    assert out_entries == entries


def test_import_rechaza_entrada_con_un_solo_extremo():
    text = """
version: 1
project: mi-proyecto
mappings:
  - {model: SoloModelo, note: "le falta otro extremo"}
"""
    with pytest.raises(ValueError):
        import_yaml(text)


def test_import_rechaza_version_no_soportada():
    text = "version: 99\nproject: x\nmappings: []\n"
    with pytest.raises(ValueError):
        import_yaml(text)


# --------------------------------------------------------------------------- #
# Huérfanos (RF-047)
# --------------------------------------------------------------------------- #
async def test_detect_orphans_extremo_inexistente():
    """Un mapeo cuyo Element ya no existe se reporta como huérfano."""
    graph = FakeGraph(elements=[{"origin_id": "e-ok", "name": "Vivo"}])
    service = MappingService(graph=graph)

    # Mapeo válido (apunta a e-ok) y mapeo huérfano (apunta a e-ghost).
    graph.seed_mapping(element_id="e-ok", channel_id="ch-1")
    graph.seed_mapping(element_id="e-ghost", channel_id="ch-2")

    orphans = await service.detect_orphans(PROJECT)

    assert len(orphans) == 1
    assert ("element", "e-ghost") in orphans[0]["missing"]
