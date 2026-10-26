"""Pruebas de los routers REST de TwinSight (``twinsight.api``).

Monta una app mínima con ``api_router`` bajo ``/api/v1`` y sobreescribe las
dependencias de acceso para simular un editor (así los tests no dependen del
flujo de login/cookies). Los que tocan el grafo van marcados ``neo4j``.

Ejecutar::

    TWINSIGHT_NEO4J_PASSWORD=twinsight uv run pytest tests/test_api.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from twinsight.access import require_editor, require_operator, require_viewer
from twinsight.access.deps import Principal
from twinsight.api import ApiError, api_error_handler, api_router
from twinsight.persistence.db import init_db

# Ejemplos reales del repo (paper/code/examples/aulas-pi3).
_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "aulas-pi3"
_GAPHOR = _EXAMPLES / "modelo.gaphor"
_SVG = _EXAMPLES / "piso.svg"

API = "/api/v1"


# --------------------------------------------------------------------------- #
# App de prueba
# --------------------------------------------------------------------------- #
def _editor() -> Principal:
    return Principal(level="editor")


@pytest.fixture(scope="module")
def client():
    import asyncio

    asyncio.run(init_db())

    app = FastAPI()
    app.include_router(api_router, prefix=API)
    app.add_exception_handler(ApiError, api_error_handler)

    # Simular un editor para todos los niveles.
    app.dependency_overrides[require_viewer] = _editor
    app.dependency_overrides[require_operator] = _editor
    app.dependency_overrides[require_editor] = _editor

    with TestClient(app) as c:
        yield c


@pytest.fixture
def project(client):
    """Crea un proyecto y lo borra al terminar."""
    import uuid

    name = f"API Test {uuid.uuid4().hex[:8]}"
    r = client.post(f"{API}/projects", json={"name": name, "description": "pruebas"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    yield pid
    client.delete(f"{API}/projects/{pid}", params={"confirm": "true"})


# --------------------------------------------------------------------------- #
# Salud (sin Neo4j estricto; el grafo puede estar o no)
# --------------------------------------------------------------------------- #
def test_health(client):
    r = client.get(f"{API}/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"status", "db", "graph", "broker"}
    assert body["db"] is True


# --------------------------------------------------------------------------- #
# Proyectos
# --------------------------------------------------------------------------- #
def test_crear_listar_y_resumen_de_proyecto(client, project):
    r = client.get(f"{API}/projects")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert project in ids
    mine = next(p for p in r.json() if p["id"] == project)
    assert set(mine["summary"]) == {"sources", "channels", "unacknowledged_alerts"}


def test_borrado_exige_confirm(client, project):
    r = client.delete(f"{API}/projects/{project}")
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "confirmation_required"
    assert "detail" in body


def test_proyecto_inexistente_404_con_code(client):
    r = client.get(f"{API}/projects/no-existe")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


# --------------------------------------------------------------------------- #
# Reglas (no requieren grafo)
# --------------------------------------------------------------------------- #
def test_crear_regla_valida_y_rechazar_invalida(client, project):
    definition = {
        "version": 1,
        "when": {"all": [{"channel": "co2", "op": ">", "value": 1000}]},
        "severity": "alarm",
    }
    r = client.post(
        f"{API}/projects/{project}/rules",
        json={"name": "CO2 alto", "severity": "alarm", "definition": definition},
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]

    # Dry-run: con 1200 dispara, con 500 no.
    r = client.post(f"{API}/rules/{rule_id}/test", json={"last_values": {"co2": 1200}})
    assert r.status_code == 200
    assert r.json()["would_fire"] is True
    r = client.post(f"{API}/rules/{rule_id}/test", json={"last_values": {"co2": 500}})
    assert r.json()["would_fire"] is False

    # Regla inválida -> 422 con code.
    r = client.post(
        f"{API}/projects/{project}/rules",
        json={"name": "mala", "definition": {"version": 1}},  # falta 'when'
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_rule"


# --------------------------------------------------------------------------- #
# Eventos y telemetría vacíos
# --------------------------------------------------------------------------- #
def test_eventos_vacios(client, project):
    r = client.get(f"{API}/projects/{project}/events")
    assert r.status_code == 200
    assert r.json() == []


def test_telemetry_latest_vacio(client, project):
    r = client.get(f"{API}/projects/{project}/telemetry/latest")
    assert r.status_code == 200
    assert r.json() == {}


# --------------------------------------------------------------------------- #
# Importación + modelo + mapeo + impacto + export (requieren Neo4j)
# --------------------------------------------------------------------------- #
@pytest.mark.neo4j
def test_flujo_completo_con_grafo(client, project):
    # 1) Importar el modelo .gaphor.
    with _GAPHOR.open("rb") as fh:
        r = client.post(
            f"{API}/projects/{project}/model-sources",
            files={"file": ("modelo.gaphor", fh, "application/xml")},
        )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["elements"] > 0
    assert "source_id" in report

    # 2) Importar la representación SVG.
    with _SVG.open("rb") as fh:
        r = client.post(
            f"{API}/projects/{project}/representations",
            files={"file": ("piso.svg", fh, "image/svg+xml")},
        )
    assert r.status_code == 200, r.text
    assert r.json()["visual_objects"] > 0

    # 3) Listar fuentes.
    r = client.get(f"{API}/projects/{project}/sources")
    assert r.status_code == 200
    kinds = {s["kind"] for s in r.json()}
    assert {"gaphor", "svg"} <= kinds

    # 4) Elementos / diagramas / máquinas de estado.
    r = client.get(f"{API}/projects/{project}/elements")
    assert r.status_code == 200
    elements = r.json()
    assert len(elements) > 0
    element_id = elements[0]["id"]

    r = client.get(f"{API}/elements/{element_id}", params={"project_id": project})
    assert r.status_code == 200
    assert r.json()["id"] == element_id

    r = client.get(f"{API}/projects/{project}/diagrams")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    r = client.get(f"{API}/projects/{project}/state-machines")
    assert r.status_code == 200  # puede ser lista vacía o con máquinas

    # 5) Servir el SVG saneado.
    r = client.get(f"{API}/projects/{project}/sources")
    svg_id = next(s["id"] for s in r.json() if s["kind"] == "svg")
    r = client.get(f"{API}/representations/{svg_id}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "<script" not in r.text.lower()

    # 6) Crear una fuente de datos y el canal que casa con la metadata dt del
    #    modelo (el elemento "MH-Z19B" trae dt:channel = gemelo/aula-A/mhz19b:co2_ppm).
    r = client.post(
        f"{API}/projects/{project}/datasources",
        json={"kind": "mqtt", "config": {"host": "localhost", "base_topic": "gemelo"}},
    )
    assert r.status_code == 201, r.text
    ds_id = r.json()["id"]
    r = client.post(
        f"{API}/datasources/{ds_id}/channels",
        json={"topic_pattern": "gemelo/aula-A/mhz19b", "metric_key": "co2_ppm"},
    )
    assert r.status_code == 201, r.text

    # Sugerencias de mapeo: la metadata dt:channel resuelve Element↔Channel.
    r = client.get(f"{API}/projects/{project}/mappings/suggestions")
    assert r.status_code == 200
    suggestions = r.json()
    assert len(suggestions) > 0
    ec = next(
        (s for s in suggestions if s["element_id"] and s["channel_id"]),
        None,
    )
    assert ec is not None, "debería haber una sugerencia Element↔Channel"

    # 7) Confirmar esa sugerencia.
    r = client.post(
        f"{API}/mappings/suggestions/confirm",
        params={"project_id": project},
        json={"suggestions": [ec]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1

    r = client.get(f"{API}/projects/{project}/mappings")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # 8) Impacto desde un elemento.
    r = client.get(
        f"{API}/projects/{project}/impact", params={"element_id": element_id}
    )
    assert r.status_code == 200
    assert "affected" in r.json()

    # 9) Export .dtproj.
    r = client.get(f"{API}/projects/{project}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.content[:2] == b"PK"  # firma de ZIP


@pytest.mark.neo4j
def test_import_gaphor_corrupto_422_sin_efectos(client, project):
    # Un .gaphor corrupto -> 422 y NINGUNA fuente registrada.
    r = client.post(
        f"{API}/projects/{project}/model-sources",
        files={"file": ("roto.gaphor", b"<gaphor>no cierra", "application/xml")},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "parse_error"

    r = client.get(f"{API}/projects/{project}/sources")
    assert r.json() == []
