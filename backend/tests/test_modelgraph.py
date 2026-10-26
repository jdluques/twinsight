"""Pruebas de la capa de acceso al grafo Neo4j (``twinsight.modelgraph``).

Requieren una instancia de Neo4j en marcha (marca ``neo4j``). Cada prueba usa
un ``project_id`` único (uuid) y limpia su proyecto en el teardown, de modo que
puedan correr en paralelo o sobre una base con otros datos sin interferir.

Ejecutar con::

    uv run pytest tests/test_modelgraph.py -m neo4j -q
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from twinsight.importers.base import (
    Diagram,
    DiagramItem,
    GraphBatch,
    GraphEdge,
    GraphNode,
)
from twinsight.modelgraph import Neo4jModelGraph

pytestmark = pytest.mark.neo4j


def _sample_batch(source_id: str = "src-1") -> GraphBatch:
    """Lote de ejemplo: 2 bloques, 1 STM (2 estados, 1 transición con guarda
    dependiente de un canal), 1 objeto visual y 1 diagrama con geometría."""
    nodes = [
        GraphNode(label="Element", sub_labels=["Block"], origin_id="blk-bomba",
                  properties={"name": "Bomba", "kind": "block",
                              "attrs": {"potencia_kw": 7.5}}),
        GraphNode(label="Element", sub_labels=["Block"], origin_id="blk-motor",
                  properties={"name": "Motor", "kind": "block"}),
        GraphNode(label="StateMachine", origin_id="stm-bomba",
                  properties={"name": "FSM Bomba", "active_state_id": "st-off"}),
        GraphNode(label="State", origin_id="st-off",
                  properties={"name": "Apagada", "is_failure_mode": False}),
        GraphNode(label="State", origin_id="st-falla",
                  properties={"name": "Falla", "is_failure_mode": True}),
        GraphNode(label="Transition", origin_id="tr-1",
                  properties={"guard_text": "presion < 1.0", "guard_status": "ok"}),
        GraphNode(label="VisualObject", origin_id="vo-bomba",
                  properties={"representation_id": "svg-123", "kind": "shape",
                              "variants": ["nominal", "alarma"]}),
        GraphNode(label="Channel", origin_id="ch-presion",
                  properties={"topic": "gemelo/presion", "metric": "presion"}),
    ]
    edges = [
        GraphEdge(type="CONTAINS", source_origin_id="blk-bomba",
                  target_origin_id="blk-motor"),
        GraphEdge(type="RELATES", source_origin_id="blk-bomba",
                  target_origin_id="blk-motor", properties={"kind": "association"}),
        GraphEdge(type="HAS_STM", source_origin_id="blk-bomba",
                  target_origin_id="stm-bomba"),
        GraphEdge(type="HAS_STATE", source_origin_id="stm-bomba",
                  target_origin_id="st-off"),
        GraphEdge(type="HAS_STATE", source_origin_id="stm-bomba",
                  target_origin_id="st-falla"),
        GraphEdge(type="FROM", source_origin_id="tr-1", target_origin_id="st-off"),
        GraphEdge(type="TO", source_origin_id="tr-1", target_origin_id="st-falla"),
        GraphEdge(type="GUARD_DEPENDS_ON", source_origin_id="tr-1",
                  target_origin_id="ch-presion"),
        GraphEdge(type="FAILURE_MODE_OF", source_origin_id="st-falla",
                  target_origin_id="blk-bomba"),
    ]
    diagrams = [
        Diagram(origin_id="dia-1", name="BDD Principal", diagram_type="bdd", items=[
            DiagramItem(subject_origin_id="blk-bomba", item_type="BlockItem",
                        x=10, y=20, width=120, height=60, label="Bomba"),
            DiagramItem(subject_origin_id="blk-motor", item_type="BlockItem",
                        x=200, y=20, width=120, height=60, label="Motor"),
        ]),
    ]
    return GraphBatch(source_id=source_id, kind="gaphor", nodes=nodes,
                      edges=edges, diagrams=diagrams)


@pytest_asyncio.fixture
async def graph():
    g = Neo4jModelGraph()
    await g.connect()
    yield g
    await g.close()


@pytest_asyncio.fixture
async def project(graph):
    pid = f"test-{uuid.uuid4().hex}"
    await graph.apply_batch(pid, _sample_batch())
    yield pid
    await graph.delete_project(pid)


# --------------------------------------------------------------------------- #
# Ciclo de vida
# --------------------------------------------------------------------------- #
async def test_ping(graph):
    assert await graph.ping() is True


# --------------------------------------------------------------------------- #
# Importación idempotente
# --------------------------------------------------------------------------- #
async def _counts(graph, pid):
    recs = await graph._fetch(
        "MATCH (n {project_id: $pid}) RETURN labels(n) AS labels, count(*) AS c",
        pid=pid,
    )
    total_nodes = sum(r["c"] for r in recs)
    rel = await graph._fetch(
        "MATCH ({project_id: $pid})-[r]->({project_id: $pid}) RETURN count(r) AS c",
        pid=pid,
    )
    return total_nodes, rel[0]["c"]


async def test_apply_batch_poblado(graph, project):
    elems = await graph.get_elements(project)
    assert {e["id"] for e in elems} == {"blk-bomba", "blk-motor"}
    bomba = next(e for e in elems if e["id"] == "blk-bomba")
    assert "Block" in bomba["labels"]
    # attrs se deserializa a dict.
    assert bomba["attrs"] == {"potencia_kw": 7.5}
    assert bomba["severity"] == "normal"  # default aplicado


async def test_idempotencia(graph, project):
    before = await _counts(graph, project)
    orphans = await graph.apply_batch(project, _sample_batch())
    after = await _counts(graph, project)
    assert before == after
    assert orphans == []


async def test_borrado_de_ausentes_y_huerfanos(graph, project):
    # Mapeo element<->visual que quedará huérfano al borrar el visual.
    await graph.create_mapping(project, element_id="blk-bomba",
                               visual_object_id="vo-bomba")
    # Lote reducido de la MISMA fuente: sin el VisualObject ni el segundo bloque.
    reduced = _sample_batch()
    reduced.nodes = [n for n in reduced.nodes
                     if n.origin_id not in {"vo-bomba", "blk-motor"}]
    reduced.edges = [e for e in reduced.edges
                     if "vo-bomba" not in (e.source_origin_id, e.target_origin_id)
                     and "blk-motor" not in (e.source_origin_id, e.target_origin_id)]
    orphans = await graph.apply_batch(project, reduced)
    # El visual fue borrado -> su mapeo queda huérfano y se reporta.
    assert len(orphans) == 1
    assert (await graph.get_element(project, "blk-motor")) is None
    recs = await graph._fetch(
        "MATCH (n:VisualObject {project_id: $pid}) RETURN count(n) AS c", pid=project
    )
    assert recs[0]["c"] == 0


# --------------------------------------------------------------------------- #
# Mapeo tripartito
# --------------------------------------------------------------------------- #
async def test_create_mapping_valida_minimo(graph, project):
    with pytest.raises(ValueError):
        await graph.create_mapping(project, element_id="blk-bomba")


async def test_mapping_2_y_3_extremos(graph, project):
    m2 = await graph.create_mapping(project, element_id="blk-bomba",
                                    visual_object_id="vo-bomba")
    m3 = await graph.create_mapping(project, element_id="blk-bomba",
                                    visual_object_id="vo-bomba",
                                    channel_id="ch-presion")
    mappings = {m["mapping_id"]: m for m in await graph.list_mappings(project)}
    assert mappings[m2]["size"] == 2 and mappings[m2]["complete"] is False
    assert mappings[m3]["size"] == 3 and mappings[m3]["complete"] is True

    # resolve desde el elemento ve ambas contrapartes.
    res = await graph.resolve(project, element_id="blk-bomba")
    assert {v["id"] for v in res["visual"]} == {"vo-bomba"}
    assert {c["id"] for c in res["channels"]} == {"ch-presion"}

    # resolución O(1) precargable.
    rmap = await graph.resolution_map(project)
    assert "vo-bomba" in rmap["elements"]["blk-bomba"]["visual"]
    assert "ch-presion" in rmap["elements"]["blk-bomba"]["channels"]
    assert "blk-bomba" in rmap["channels"]["ch-presion"]["elements"]

    await graph.delete_mapping(project, m2)
    remaining = {m["mapping_id"] for m in await graph.list_mappings(project)}
    assert m2 not in remaining and m3 in remaining


# --------------------------------------------------------------------------- #
# Estado discreto en vivo
# --------------------------------------------------------------------------- #
async def test_estado_discreto(graph, project):
    await graph.set_active_state(project, "stm-bomba", "st-falla")
    await graph.set_element_severity(project, "blk-bomba", "critical")
    await graph.upsert_channel(project, "ch-presion", "gemelo/presion", "presion")
    await graph.set_channel_stale(project, "ch-presion", True)

    stm = await graph.get_state_machine(project, "stm-bomba")
    assert stm["active_state_id"] == "st-falla"
    assert {s["id"] for s in stm["states"]} == {"st-off", "st-falla"}
    tr = next(t for t in stm["transitions"] if t["id"] == "tr-1")
    assert tr["from"] == "st-off" and tr["to"] == "st-falla"
    assert tr["guard_status"] == "ok"

    elem = await graph.get_element(project, "blk-bomba")
    assert elem["severity"] == "critical"

    chan = await graph._fetch(
        "MATCH (c:Channel {project_id: $pid, channel_id: 'ch-presion'}) RETURN c",
        pid=project,
    )
    assert chan[0]["c"]["stale"] is True


# --------------------------------------------------------------------------- #
# Diagramas
# --------------------------------------------------------------------------- #
async def test_diagrama_con_geometria(graph, project):
    diags = await graph.get_diagrams(project)
    assert len(diags) == 1 and diags[0]["item_count"] == 2
    dia = await graph.get_diagram(project, "dia-1")
    assert dia["diagram_type"] == "bdd"
    assert isinstance(dia["items"], list) and len(dia["items"]) == 2
    assert dia["items"][0]["subject_origin_id"] == "blk-bomba"


# --------------------------------------------------------------------------- #
# Impacto y vecindario
# --------------------------------------------------------------------------- #
async def test_impact_canal(graph, project):
    # Canal sin datos -> transición, estados y bloque afectados.
    imp = await graph.impact(project, channel_id="ch-presion")
    affected_ids = {a["id"] for a in imp["affected"]}
    assert "tr-1" in affected_ids          # transición dependiente de la guarda
    assert {"st-off", "st-falla"} & affected_ids  # estados de la transición
    assert "blk-bomba" in affected_ids     # bloque dueño de la máquina
    assert imp["paths"]  # hay rutas de dependencia


async def test_impact_element(graph, project):
    imp = await graph.impact(project, element_id="blk-bomba", max_depth=4)
    affected_ids = {a["id"] for a in imp["affected"]}
    # Alcanza el bloque contenido, la máquina y sus estados.
    assert "blk-motor" in affected_ids
    assert "stm-bomba" in affected_ids
    assert "st-off" in affected_ids


async def test_neighborhood(graph, project):
    sub = await graph.neighborhood(project, "blk-bomba", depth=2)
    node_ids = {n["id"] for n in sub["nodes"]}
    assert "blk-bomba" in node_ids
    assert "blk-motor" in node_ids
    assert "stm-bomba" in node_ids
    assert sub["edges"]
    # Vecindario desde un canal (match por channel_id).
    sub_c = await graph.neighborhood(project, "ch-presion", depth=1)
    assert any(n["id"] == "tr-1" for n in sub_c["nodes"])


# --------------------------------------------------------------------------- #
# Modos de fallo
# --------------------------------------------------------------------------- #
async def test_failure_modes(graph, project):
    fms = await graph.failure_modes(project)
    ids = {f["id"] for f in fms}
    assert ids == {"st-falla"}
    assert fms[0]["element_id"] == "blk-bomba"

    # Sin estado activo de fallo todavía.
    assert await graph.failure_modes(project, mode="active") == []
    await graph.set_active_state(project, "stm-bomba", "st-falla")
    active = await graph.failure_modes(project, mode="active")
    assert {f["id"] for f in active} == {"st-falla"}
