"""Implementación de :class:`ModelGraph` sobre Neo4j (Diseño §3.1).

Es el único módulo que habla con el driver de Neo4j. Toda la plataforma
depende de la interfaz abstracta ``ModelGraph``; este backend es sustituible.

Convenciones del esquema:
- Todo nodo lleva ``project_id`` (aislamiento por proyecto) y un identificador
  estable: ``origin_id`` para la mayoría de las etiquetas y ``channel_id`` para
  ``Channel`` (donde además guardamos ``origin_id == channel_id`` para poder
  casar aristas de forma uniforme por ``origin_id``).
- La idempotencia se consigue con ``MERGE`` por ``(project_id, <id>)``.
- Neo4j no almacena dicts/listas anidados: los serializamos a JSON *string*
  (claves ``attrs``, ``variants``, ``items``) y los deserializamos al leer.
- Los mapeos tripartitos se materializan como aristas ``MAPPED_TO`` directas
  entre los extremos, todas compartiendo una propiedad ``mapping_id`` (clique).
"""

from __future__ import annotations

import json
import re
import uuid

from neo4j import AsyncDriver, AsyncGraphDatabase

from ..config import get_settings
from ..importers.base import GraphBatch
from .base import ModelGraph

# Claves cuyos valores se guardan serializados a JSON string.
_JSON_KEYS = frozenset({"attrs", "variants", "items", "waypoints"})

# Defaults por etiqueta principal (Diseño §3.1).
_DEFAULTS: dict[str, dict] = {
    "Element": {"severity": "normal"},
    "State": {"is_failure_mode": False},
    "Transition": {"guard_status": "ok"},
    "Channel": {"stale": False},
    "VisualObject": {"severity": "normal"},
}

# Conjuntos controlados (se validan de todos modos antes de inyectar en Cypher).
_EDGE_TYPES = frozenset({
    "CONTAINS", "RELATES", "HAS_STM", "HAS_STATE", "FROM", "TO",
    "GUARD_DEPENDS_ON", "MAPPED_TO", "FAILURE_MODE_OF",
})

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    """Valida y escapa un identificador (etiqueta/tipo) para Cypher."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Identificador inválido para Cypher: {name!r}")
    return f"`{name}`"


def _depth(value: int, *, maximum: int = 12) -> int:
    """Normaliza una profundidad a un entero positivo acotado."""
    d = int(value)
    if d < 1:
        d = 1
    if d > maximum:
        d = maximum
    return d


def _serialize(props: dict) -> dict:
    """Serializa a JSON string los valores dict/list de las propiedades."""
    out: dict = {}
    for key, val in props.items():
        if isinstance(val, (dict, list)):
            out[key] = json.dumps(val, ensure_ascii=False)
        else:
            out[key] = val
    return out


def _deserialize(props: dict) -> dict:
    """Deserializa las claves JSON conocidas; el resto se deja intacto."""
    out = dict(props)
    for key in _JSON_KEYS:
        val = out.get(key)
        if isinstance(val, str):
            try:
                out[key] = json.loads(val)
            except (ValueError, TypeError):
                pass
    return out


def _node_id(node) -> str:
    """Identificador de negocio de un nodo (``channel_id`` para Channel)."""
    if "Channel" in node.labels:
        return node.get("channel_id") or node.get("origin_id")
    return node.get("origin_id") or node.get("channel_id")


def _primary_label(node) -> str:
    """Etiqueta principal conocida de un nodo (el resto son sub-etiquetas)."""
    known = {
        "Element", "StateMachine", "State", "Transition",
        "VisualObject", "Channel", "Diagram", "Mapping",
    }
    labels = set(node.labels)
    for lab in known:
        if lab in labels:
            return lab
    return next(iter(labels), "Node")


def _node_view(node) -> dict:
    """Representación plana y deserializada de un nodo para el cliente."""
    data = _deserialize(dict(node))
    data["labels"] = sorted(node.labels)
    data["label"] = _primary_label(node)
    data["id"] = _node_id(node)
    return data


class Neo4jModelGraph(ModelGraph):
    """Acceso al grafo canónico respaldado por Neo4j."""

    def __init__(self) -> None:
        self._driver: AsyncDriver | None = None

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    async def connect(self) -> None:
        """Crea el driver (perezoso) y asegura índices de búsqueda."""
        if self._driver is not None:
            return
        cfg = get_settings()
        self._driver = AsyncGraphDatabase.driver(
            cfg.neo4j_uri, auth=(cfg.neo4j_user, cfg.neo4j_password)
        )
        await self._driver.verify_connectivity()
        await self._ensure_indexes()

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def ping(self) -> bool:
        try:
            await self._ensure()
            await self._driver.verify_connectivity()  # type: ignore[union-attr]
            return True
        except Exception:
            return False

    async def _ensure(self) -> AsyncDriver:
        if self._driver is None:
            await self.connect()
        assert self._driver is not None
        return self._driver

    async def _ensure_indexes(self) -> None:
        stmts = [
            "CREATE INDEX twinsight_element IF NOT EXISTS "
            "FOR (n:Element) ON (n.project_id, n.origin_id)",
            "CREATE INDEX twinsight_channel IF NOT EXISTS "
            "FOR (n:Channel) ON (n.project_id, n.channel_id)",
            "CREATE INDEX twinsight_diagram IF NOT EXISTS "
            "FOR (n:Diagram) ON (n.project_id, n.origin_id)",
            "CREATE INDEX twinsight_stm IF NOT EXISTS "
            "FOR (n:StateMachine) ON (n.project_id, n.origin_id)",
        ]
        async with self._driver.session() as session:  # type: ignore[union-attr]
            for stmt in stmts:
                try:
                    await session.run(stmt)
                except Exception:
                    # Índices no soportados por la edición: no es crítico.
                    pass

    # ------------------------------------------------------------------ #
    # Helpers de consulta
    # ------------------------------------------------------------------ #
    async def _fetch(self, query: str, **params) -> list:
        """Ejecuta una consulta de lectura y materializa los registros."""
        driver = await self._ensure()
        async with driver.session() as session:
            result = await session.run(query, **params)
            return [rec async for rec in result]

    async def _run(self, query: str, **params) -> None:
        driver = await self._ensure()
        async with driver.session() as session:
            await (await session.run(query, **params)).consume()

    # ------------------------------------------------------------------ #
    # Importación idempotente (dual-store, Diseño §3.3)
    # ------------------------------------------------------------------ #
    async def apply_batch(self, project_id: str, batch: GraphBatch) -> list[str]:
        driver = await self._ensure()

        # --- Preparar filas de nodos agrupadas por firma de etiquetas ---
        node_groups: dict[tuple, list[dict]] = {}
        origin_ids: set[str] = set()
        for node in batch.nodes:
            props = dict(node.properties)
            props.pop("project_id", None)
            props.pop("origin_id", None)
            # Defaults por etiqueta.
            for key, default in _DEFAULTS.get(node.label, {}).items():
                props.setdefault(key, default)
            # Channel: el origin_id actúa como channel_id.
            if node.label == "Channel":
                props.setdefault("channel_id", node.origin_id)
            props = _serialize(props)
            subs = tuple(sorted(node.sub_labels))
            key = (node.label, subs)
            node_groups.setdefault(key, []).append(
                {"origin_id": node.origin_id, "props": props}
            )
            origin_ids.add(node.origin_id)

        # --- Preparar aristas agrupadas por tipo ---
        edge_groups: dict[str, list[dict]] = {}
        for edge in batch.edges:
            props = dict(edge.properties)
            row = {
                "src": edge.source_origin_id,
                "dst": edge.target_origin_id,
                "kind": props.get("kind", "association"),
            }
            if edge.type == "MAPPED_TO":
                # mapping_id determinista para que reaplicar no duplique.
                props.setdefault(
                    "mapping_id",
                    f"import:{edge.source_origin_id}|{edge.target_origin_id}",
                )
            row["props"] = _serialize(props)
            row["mapping_id"] = props.get("mapping_id")
            edge_groups.setdefault(edge.type, []).append(row)

        # --- Preparar diagramas ---
        diagram_rows = []
        for diagram in batch.diagrams:
            diagram_rows.append({
                "origin_id": diagram.origin_id,
                "name": diagram.name,
                "diagram_type": diagram.diagram_type,
                "items": json.dumps(
                    [it.model_dump() for it in diagram.items], ensure_ascii=False
                ),
            })
            origin_ids.add(diagram.origin_id)

        ids = list(origin_ids)
        sid = batch.source_id

        async def _work(tx):
            # 1) MERGE de nodos (una consulta por firma de etiquetas).
            for (label, subs), rows in node_groups.items():
                set_subs = ""
                if subs:
                    set_subs = "SET n:" + ":".join(_ident(s) for s in subs) + "\n"
                query = (
                    "UNWIND $rows AS row\n"
                    f"MERGE (n:{_ident(label)} "
                    "{project_id: $pid, origin_id: row.origin_id})\n"
                    "SET n += row.props\n"
                    "SET n.source_id = $sid\n"
                    f"{set_subs}"
                )
                await tx.run(query, rows=rows, pid=project_id, sid=sid)

            # 2) MERGE de aristas (una consulta por tipo).
            for etype, rows in edge_groups.items():
                if etype not in _EDGE_TYPES:
                    raise ValueError(f"Tipo de arista desconocido: {etype}")
                rel = _ident(etype)
                if etype == "RELATES":
                    merge = f"MERGE (a)-[r:{rel} {{kind: row.kind}}]->(b)\n"
                elif etype == "MAPPED_TO":
                    merge = (
                        f"MERGE (a)-[r:{rel} "
                        "{mapping_id: row.mapping_id}]->(b)\n"
                    )
                else:
                    merge = f"MERGE (a)-[r:{rel}]->(b)\n"
                query = (
                    "UNWIND $rows AS row\n"
                    "MATCH (a {project_id: $pid, origin_id: row.src})\n"
                    "MATCH (b {project_id: $pid, origin_id: row.dst})\n"
                    f"{merge}"
                    "SET r += row.props\n"
                    "SET r.source_id = $sid, r.project_id = $pid\n"
                )
                await tx.run(query, rows=rows, pid=project_id, sid=sid)

            # 3) Diagramas.
            if diagram_rows:
                await tx.run(
                    "UNWIND $rows AS row\n"
                    "MERGE (d:Diagram {project_id: $pid, origin_id: row.origin_id})\n"
                    "SET d.name = row.name, d.diagram_type = row.diagram_type,\n"
                    "    d.items = row.items, d.source_id = $sid\n",
                    rows=diagram_rows, pid=project_id, sid=sid,
                )

            # 4) Detectar mapeos que quedarían huérfanos al borrar.
            orphan_res = await tx.run(
                "MATCH (a)-[r:MAPPED_TO]-(b)\n"
                "WHERE r.project_id = $pid AND (\n"
                "  (a.source_id = $sid AND NOT a.origin_id IN $ids) OR\n"
                "  (b.source_id = $sid AND NOT b.origin_id IN $ids)\n"
                ")\n"
                "RETURN DISTINCT r.mapping_id AS mid",
                pid=project_id, sid=sid, ids=ids,
            )
            orphans = sorted({
                rec["mid"] async for rec in orphan_res if rec["mid"] is not None
            })

            # 5) Borrar los nodos de esta fuente ausentes del lote.
            await tx.run(
                "MATCH (n {project_id: $pid, source_id: $sid})\n"
                "WHERE NOT n.origin_id IN $ids\n"
                "DETACH DELETE n",
                pid=project_id, sid=sid, ids=ids,
            )
            return orphans

        async with driver.session() as session:
            return await session.execute_write(_work)

    async def delete_project(self, project_id: str) -> None:
        await self._run(
            "MATCH (n {project_id: $pid}) DETACH DELETE n", pid=project_id
        )

    # ------------------------------------------------------------------ #
    # Exploración del modelo
    # ------------------------------------------------------------------ #
    async def get_elements(
        self, project_id: str, kind: str | None = None, q: str | None = None
    ) -> list[dict]:
        clauses = ["n.project_id = $pid"]
        params: dict = {"pid": project_id}
        if kind:
            clauses.append("n.kind = $kind")
            params["kind"] = kind
        if q:
            clauses.append("toLower(n.name) CONTAINS toLower($q)")
            params["q"] = q
        where = " AND ".join(clauses)
        recs = await self._fetch(
            f"MATCH (n:Element) WHERE {where} "
            "RETURN n ORDER BY n.name", **params
        )
        return [_node_view(r["n"]) for r in recs]

    async def get_element(self, project_id: str, origin_id: str) -> dict | None:
        recs = await self._fetch(
            "MATCH (e:Element {project_id: $pid, origin_id: $oid})\n"
            "OPTIONAL MATCH (e)-[:CONTAINS]->(child:Element)\n"
            "OPTIONAL MATCH (e)-[rel:RELATES]->(other:Element)\n"
            "OPTIONAL MATCH (e)-[:HAS_STM]->(stm:StateMachine)\n"
            "OPTIONAL MATCH (e)-[:MAPPED_TO]-(mv:VisualObject)\n"
            "OPTIONAL MATCH (e)-[:MAPPED_TO]-(mc:Channel)\n"
            "RETURN e,\n"
            "  collect(DISTINCT child) AS children,\n"
            "  collect(DISTINCT {kind: rel.kind, target: other.origin_id,"
            " name: other.name}) AS relates,\n"
            "  collect(DISTINCT stm) AS machines,\n"
            "  collect(DISTINCT mv) AS visuals,\n"
            "  collect(DISTINCT mc) AS channels",
            pid=project_id, oid=origin_id,
        )
        if not recs or recs[0]["e"] is None:
            return None
        rec = recs[0]
        view = _node_view(rec["e"])
        view["contains"] = [_node_view(c) for c in rec["children"] if c is not None]
        view["relates"] = [r for r in rec["relates"] if r.get("target")]
        view["state_machines"] = [_node_view(m) for m in rec["machines"] if m is not None]
        view["visual_objects"] = [_node_view(v) for v in rec["visuals"] if v is not None]
        view["channels"] = [_node_view(c) for c in rec["channels"] if c is not None]
        return view

    async def get_diagrams(self, project_id: str) -> list[dict]:
        recs = await self._fetch(
            "MATCH (d:Diagram {project_id: $pid}) "
            "RETURN d ORDER BY d.name", pid=project_id
        )
        out = []
        for r in recs:
            view = _node_view(r["d"])
            items = view.get("items") or []
            view["item_count"] = len(items) if isinstance(items, list) else 0
            out.append(view)
        return out

    async def get_diagram(self, project_id: str, diagram_id: str) -> dict | None:
        recs = await self._fetch(
            "MATCH (d:Diagram {project_id: $pid, origin_id: $did}) RETURN d",
            pid=project_id, did=diagram_id,
        )
        if not recs:
            return None
        return _node_view(recs[0]["d"])

    async def get_state_machines(self, project_id: str) -> list[dict]:
        recs = await self._fetch(
            "MATCH (sm:StateMachine {project_id: $pid}) "
            "RETURN sm ORDER BY sm.name", pid=project_id
        )
        return [_node_view(r["sm"]) for r in recs]

    async def get_state_machine(
        self, project_id: str, machine_id: str
    ) -> dict | None:
        recs = await self._fetch(
            "MATCH (sm:StateMachine {project_id: $pid, origin_id: $mid})\n"
            "OPTIONAL MATCH (owner:Element)-[:HAS_STM]->(sm)\n"
            "OPTIONAL MATCH (sm)-[:HAS_STATE]->(s:State)\n"
            "OPTIONAL MATCH (sm)-[:HAS_STATE]->(:State)<-[:FROM]-(t:Transition)\n"
            "OPTIONAL MATCH (t)-[:FROM]->(fs:State)\n"
            "OPTIONAL MATCH (t)-[:TO]->(ts:State)\n"
            "RETURN sm, owner.origin_id AS element_id,\n"
            "  collect(DISTINCT s) AS states,\n"
            "  collect(DISTINCT {id: t.origin_id, guard_text: t.guard_text,"
            " guard_status: t.guard_status, from: fs.origin_id,"
            " to: ts.origin_id}) AS transitions",
            pid=project_id, mid=machine_id,
        )
        if not recs or recs[0]["sm"] is None:
            return None
        rec = recs[0]
        view = _node_view(rec["sm"])
        view["element_id"] = rec["element_id"]
        view["states"] = [_node_view(s) for s in rec["states"] if s is not None]
        view["transitions"] = [t for t in rec["transitions"] if t.get("id")]
        return view

    # ------------------------------------------------------------------ #
    # Canales (espejo del relacional)
    # ------------------------------------------------------------------ #
    async def upsert_channel(
        self, project_id: str, channel_id: str, topic: str, metric: str
    ) -> None:
        await self._run(
            "MERGE (c:Channel {project_id: $pid, channel_id: $cid})\n"
            "SET c.origin_id = $cid, c.topic = $topic, c.metric = $metric,\n"
            "    c.stale = coalesce(c.stale, false)",
            pid=project_id, cid=channel_id, topic=topic, metric=metric,
        )

    # ------------------------------------------------------------------ #
    # Estado discreto en vivo (RF-102)
    # ------------------------------------------------------------------ #
    async def set_active_state(
        self, project_id: str, machine_id: str, state_id: str
    ) -> None:
        await self._run(
            "MATCH (sm:StateMachine {project_id: $pid, origin_id: $mid}) "
            "SET sm.active_state_id = $sid",
            pid=project_id, mid=machine_id, sid=state_id,
        )

    async def set_element_severity(
        self, project_id: str, element_id: str, severity: str
    ) -> None:
        await self._run(
            "MATCH (e:Element {project_id: $pid, origin_id: $eid}) "
            "SET e.severity = $sev",
            pid=project_id, eid=element_id, sev=severity,
        )

    async def set_channel_stale(
        self, project_id: str, channel_id: str, stale: bool
    ) -> None:
        await self._run(
            "MATCH (c:Channel {project_id: $pid, channel_id: $cid}) "
            "SET c.stale = $stale",
            pid=project_id, cid=channel_id, stale=bool(stale),
        )

    # ------------------------------------------------------------------ #
    # Mapeo tripartito (RF-040, RF-046, RF-048)
    # ------------------------------------------------------------------ #
    async def create_mapping(
        self,
        project_id: str,
        *,
        element_id: str | None = None,
        visual_object_id: str | None = None,
        channel_id: str | None = None,
    ) -> str:
        # Extremos solicitados (etiqueta, campo-id, valor).
        requested = []
        if element_id is not None:
            requested.append(("Element", "origin_id", element_id))
        if visual_object_id is not None:
            requested.append(("VisualObject", "origin_id", visual_object_id))
        if channel_id is not None:
            requested.append(("Channel", "channel_id", channel_id))
        if len(requested) < 2:
            raise ValueError("Un mapeo requiere al menos 2 extremos")

        mapping_id = uuid.uuid4().hex
        driver = await self._ensure()

        async def _work(tx):
            # Verificar existencia de cada extremo.
            present = []
            for label, field, value in requested:
                res = await tx.run(
                    f"MATCH (n:{_ident(label)} {{project_id: $pid, "
                    f"{field}: $val}}) RETURN elementId(n) AS eid",
                    pid=project_id, val=value,
                )
                row = await res.single()
                if row is not None:
                    present.append((label, field, value))
            if len(present) < 2:
                raise ValueError(
                    "Se necesitan al menos 2 extremos existentes para el mapeo"
                )
            # Crear la clique MAPPED_TO entre los extremos presentes.
            for i in range(len(present)):
                for j in range(i + 1, len(present)):
                    la, fa, va = present[i]
                    lb, fb, vb = present[j]
                    await tx.run(
                        f"MATCH (a:{_ident(la)} {{project_id: $pid, {fa}: $va}})\n"
                        f"MATCH (b:{_ident(lb)} {{project_id: $pid, {fb}: $vb}})\n"
                        "MERGE (a)-[r:MAPPED_TO {mapping_id: $mid}]->(b)\n"
                        "SET r.project_id = $pid",
                        pid=project_id, va=va, vb=vb, mid=mapping_id,
                    )
            return mapping_id

        async with driver.session() as session:
            return await session.execute_write(_work)

    async def delete_mapping(self, project_id: str, mapping_id: str) -> None:
        await self._run(
            "MATCH ()-[r:MAPPED_TO {project_id: $pid, mapping_id: $mid}]-() "
            "DELETE r",
            pid=project_id, mid=mapping_id,
        )

    async def list_mappings(self, project_id: str) -> list[dict]:
        recs = await self._fetch(
            "MATCH (a)-[r:MAPPED_TO]->(b) WHERE r.project_id = $pid\n"
            "WITH r.mapping_id AS mid, collect(DISTINCT a) + collect(DISTINCT b) AS ns\n"
            "RETURN mid, ns",
            pid=project_id,
        )
        out = []
        for rec in recs:
            # Deduplicar extremos por su id de negocio.
            endpoints: dict[str, dict] = {}
            for node in rec["ns"]:
                nid = _node_id(node)
                if nid is None:
                    continue
                endpoints[nid] = {
                    "id": nid,
                    "label": _primary_label(node),
                    "name": node.get("name"),
                }
            size = len(endpoints)
            out.append({
                "mapping_id": rec["mid"],
                "endpoints": list(endpoints.values()),
                "size": size,
                "complete": size >= 3,
            })
        out.sort(key=lambda m: m["mapping_id"] or "")
        return out

    async def resolution_map(self, project_id: str) -> dict:
        recs = await self._fetch(
            "MATCH (a)-[r:MAPPED_TO]->(b) WHERE r.project_id = $pid RETURN a, b",
            pid=project_id,
        )
        bucket = {"Element": "elements", "VisualObject": "visual", "Channel": "channels"}
        result: dict = {"elements": {}, "visual_objects": {}, "channels": {}}
        top = {"Element": "elements", "VisualObject": "visual_objects", "Channel": "channels"}

        def _entry(node) -> dict | None:
            label = _primary_label(node)
            section = top.get(label)
            if section is None:
                return None
            nid = _node_id(node)
            return result[section].setdefault(
                nid, {"elements": [], "visual": [], "channels": []}
            )

        def _link(src, dst) -> None:
            entry = _entry(src)
            if entry is None:
                return
            target_bucket = bucket.get(_primary_label(dst))
            tid = _node_id(dst)
            if target_bucket and tid not in entry[target_bucket]:
                entry[target_bucket].append(tid)

        for rec in recs:
            a, b = rec["a"], rec["b"]
            _link(a, b)
            _link(b, a)
        return result

    async def resolve(
        self,
        project_id: str,
        *,
        element_id: str | None = None,
        visual_object_id: str | None = None,
        channel_id: str | None = None,
    ) -> dict:
        if element_id is not None:
            label, field, value = "Element", "origin_id", element_id
        elif visual_object_id is not None:
            label, field, value = "VisualObject", "origin_id", visual_object_id
        elif channel_id is not None:
            label, field, value = "Channel", "channel_id", channel_id
        else:
            raise ValueError("resolve requiere un extremo (element/visual/channel)")

        recs = await self._fetch(
            f"MATCH (n:{_ident(label)} {{project_id: $pid, {field}: $val}})\n"
            "MATCH (n)-[:MAPPED_TO]-(m) WHERE m.project_id = $pid\n"
            "RETURN collect(DISTINCT m) AS counterparts",
            pid=project_id, val=value,
        )
        out: dict = {"elements": [], "visual": [], "channels": []}
        if not recs:
            return out
        section = {"Element": "elements", "VisualObject": "visual", "Channel": "channels"}
        for node in recs[0]["counterparts"]:
            key = section.get(_primary_label(node))
            if key:
                out[key].append(_node_view(node))
        return out

    # ------------------------------------------------------------------ #
    # Dependencias de guardas
    # ------------------------------------------------------------------ #
    async def set_guard_dependencies(
        self, project_id: str, transition_id: str, channel_ids: list[str]
    ) -> None:
        await self._run(
            "MATCH (t:Transition {project_id: $pid, origin_id: $tid})\n"
            "CALL (t) { MATCH (t)-[r:GUARD_DEPENDS_ON]->() DELETE r }\n"
            "WITH t\n"
            "UNWIND $cids AS cid\n"
            "MATCH (c:Channel {project_id: $pid, channel_id: cid})\n"
            "MERGE (t)-[:GUARD_DEPENDS_ON]->(c)",
            pid=project_id, tid=transition_id, cids=channel_ids,
        )

    async def set_guard_status(
        self, project_id: str, transition_id: str, status: str
    ) -> None:
        await self._run(
            "MATCH (t:Transition {project_id: $pid, origin_id: $tid}) "
            "SET t.guard_status = $status",
            pid=project_id, tid=transition_id, status=status,
        )

    # ------------------------------------------------------------------ #
    # Grafo e impacto (RF-103, RF-104, RF-105)
    # ------------------------------------------------------------------ #
    async def neighborhood(
        self, project_id: str, node_id: str, depth: int = 1
    ) -> dict:
        d = _depth(depth)
        recs = await self._fetch(
            "MATCH (start {project_id: $pid})\n"
            "WHERE start.origin_id = $nid OR start.channel_id = $nid\n"
            f"OPTIONAL MATCH p = (start)-[*1..{d}]-(m)\n"
            "WHERE all(x IN nodes(p) WHERE x.project_id = $pid)\n"
            "RETURN start, collect(p) AS paths",
            pid=project_id, nid=node_id,
        )
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        if not recs or recs[0]["start"] is None:
            return {"nodes": [], "edges": []}

        def _add_node(node) -> None:
            nodes[node.element_id] = {
                "id": _node_id(node),
                "label": _primary_label(node),
                "labels": sorted(node.labels),
                "name": node.get("name"),
            }

        start = recs[0]["start"]
        _add_node(start)
        for path in recs[0]["paths"]:
            if path is None:
                continue
            for n in path.nodes:
                _add_node(n)
            for rel in path.relationships:
                edges[rel.element_id] = {
                    "id": rel.element_id,
                    "source": _node_id(rel.start_node),
                    "target": _node_id(rel.end_node),
                    "type": rel.type,
                }
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    async def impact(
        self, project_id: str, *, element_id: str | None = None,
        channel_id: str | None = None, max_depth: int = 4,
    ) -> dict:
        if element_id is None and channel_id is None:
            raise ValueError("impact requiere element_id o channel_id")

        affected: dict[str, dict] = {}
        paths: list[list[str]] = []
        cat: dict[str, list] = {
            "elements": [], "states": [], "visual_objects": [],
            "channels": [], "transitions": [], "state_machines": [],
        }
        cat_label = {
            "Element": "elements", "State": "states",
            "VisualObject": "visual_objects", "Channel": "channels",
            "Transition": "transitions", "StateMachine": "state_machines",
        }

        def _mark(node) -> None:
            nid = _node_id(node)
            label = _primary_label(node)
            if nid in affected:
                return
            entry = {"id": nid, "label": label, "name": node.get("name")}
            affected[nid] = entry
            bucket = cat_label.get(label)
            if bucket:
                cat[bucket].append(entry)

        if element_id is not None:
            d = _depth(max_depth)
            recs = await self._fetch(
                "MATCH (start:Element {project_id: $pid, origin_id: $eid})\n"
                "MATCH p = (start)-[:MAPPED_TO|RELATES|CONTAINS|HAS_STM|HAS_STATE"
                f"*1..{d}]-(m)\n"
                "WHERE m.project_id = $pid AND m <> start\n"
                "RETURN DISTINCT m AS node, [r IN relationships(p) | type(r)] AS types",
                pid=project_id, eid=element_id,
            )
            for rec in recs:
                _mark(rec["node"])
                if rec["types"]:
                    paths.append(rec["types"])
        else:
            # Canal → guardas → transiciones → estados → bloques (máquinas).
            recs = await self._fetch(
                "MATCH (c:Channel {project_id: $pid, channel_id: $cid})\n"
                "MATCH (t:Transition)-[:GUARD_DEPENDS_ON]->(c) WHERE t.project_id = $pid\n"
                "OPTIONAL MATCH (t)-[:FROM|TO]->(s:State) WHERE s.project_id = $pid\n"
                "OPTIONAL MATCH (sm:StateMachine)-[:HAS_STATE]->(s) WHERE sm.project_id = $pid\n"
                "OPTIONAL MATCH (b:Element)-[:HAS_STM]->(sm) WHERE b.project_id = $pid\n"
                "RETURN collect(DISTINCT t) AS transitions,\n"
                "  collect(DISTINCT s) AS states,\n"
                "  collect(DISTINCT sm) AS machines,\n"
                "  collect(DISTINCT b) AS blocks",
                pid=project_id, cid=channel_id,
            )
            if recs:
                rec = recs[0]
                for t in rec["transitions"]:
                    _mark(t)
                    paths.append(["GUARD_DEPENDS_ON"])
                for s in rec["states"]:
                    if s is not None:
                        _mark(s)
                        paths.append(["GUARD_DEPENDS_ON", "FROM"])
                for sm in rec["machines"]:
                    if sm is not None:
                        _mark(sm)
                for b in rec["blocks"]:
                    if b is not None:
                        _mark(b)
                        paths.append(["GUARD_DEPENDS_ON", "FROM", "HAS_STATE", "HAS_STM"])

        return {
            "affected": list(affected.values()),
            "paths": paths,
            "elements": cat["elements"],
            "states": cat["states"],
            "visual_objects": cat["visual_objects"],
            "channels": cat["channels"],
            "transitions": cat["transitions"],
            "state_machines": cat["state_machines"],
        }

    async def failure_modes(
        self, project_id: str, mode: str | None = None
    ) -> list[dict]:
        extra = ""
        if mode == "active":
            # Solo los modos de fallo actualmente activos en su máquina.
            extra = "AND sm.active_state_id = s.origin_id\n"
        recs = await self._fetch(
            "MATCH (s:State {project_id: $pid}) WHERE s.is_failure_mode = true\n"
            "OPTIONAL MATCH (sm:StateMachine {project_id: $pid})-[:HAS_STATE]->(s)\n"
            "OPTIONAL MATCH (s)-[:FAILURE_MODE_OF]->(e:Element)\n"
            f"WITH s, sm, e WHERE true {extra}"
            "RETURN s, sm.origin_id AS machine_id, sm.active_state_id AS active,\n"
            "  e.origin_id AS element_id, e.name AS element_name",
            pid=project_id,
        )
        out = []
        for rec in recs:
            view = _node_view(rec["s"])
            view["machine_id"] = rec["machine_id"]
            view["is_active"] = rec["active"] == view.get("origin_id")
            view["element_id"] = rec["element_id"]
            view["element_name"] = rec["element_name"]
            out.append(view)
        return out
