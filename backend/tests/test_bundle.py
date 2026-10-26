"""Pruebas del bundle portable ``.dtproj`` (``twinsight.bundle``).

Dos niveles:

* **Unit** (sin Neo4j): siembra un proyecto mínimo en SQLite temporal y verifica
  que :func:`export_project` produce un ZIP con todas las piezas esperadas, que
  NO lleva credenciales ni datos de ejecución y que los ``sha256`` del manifest
  cuadran. Usa un ``ModelGraph`` falso para los mapeos.

* **Integración** (marca ``neo4j``): puebla el grafo de un proyecto real,
  exporta, importa en un proyecto nuevo y comprueba que los elementos se
  reproducen, que las fuentes de datos quedan sin credenciales, que el roundtrip
  export→import→export es estable (mismos ``sha256``) y que un bundle manipulado
  es rechazado. Limpia los proyectos creados.

Ejecutar::

    uv run pytest tests/test_bundle.py -q
    uv run pytest tests/test_bundle.py -q -m neo4j
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from twinsight.bundle import export_project, import_project
from twinsight.importers.registry import get_translator
from twinsight.persistence.db import Base
from twinsight.persistence.models import (
    DataSource,
    ImportRecord,
    PresentationConfig,
    Project,
    RuleDef,
    WorkspaceLayout,
)

# Raíz del repo (paper/code) para localizar los ejemplos.
_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "aulas-pi3"
_GAPHOR = _EXAMPLES / "modelo.gaphor"
_SVG = _EXAMPLES / "piso.svg"

# Credenciales de prueba: valores distintivos para buscarlos en el ZIP.
_USUARIO = "usuario-mqtt-distintivo"
_CLAVE = "clave-super-secreta-distintiva"


# --------------------------------------------------------------------------- #
# Infraestructura común
# --------------------------------------------------------------------------- #
async def _make_sessionmaker(tmp_path: Path) -> async_sessionmaker:
    """Crea un ``sessionmaker`` sobre una SQLite temporal con las tablas creadas."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'bundle.sqlite3'}", future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_project(
    sm: async_sessionmaker,
    *,
    name: str,
    sources: list[tuple[str, str, bytes]],  # (kind, filename, raw)
    with_credentials: bool = True,
) -> tuple[str, list[tuple[str, str, bytes]]]:
    """Siembra un proyecto con fuentes, 1 regla, 1 presentación, 1 layout y 1 datasource.

    Devuelve ``(project_id, import_records)`` donde cada import record es
    ``(record_id, kind, raw)`` para poder poblar el grafo después.
    """
    config = {"host": "broker.local", "port": 1883, "base_topic": "gemelo"}
    if with_credentials:
        config["username"] = _USUARIO
        config["password"] = _CLAVE

    records: list[tuple[str, str, bytes]] = []
    async with sm() as session:
        project = Project(name=name, description="proyecto de prueba")
        session.add(project)
        await session.flush()
        pid = project.id

        for kind, filename, raw in sources:
            import hashlib

            rec = ImportRecord(
                project_id=pid,
                kind=kind,
                filename=filename,
                file_hash=hashlib.sha256(raw).hexdigest(),
                raw_file=raw,
                status="ok",
            )
            session.add(rec)
            await session.flush()
            records.append((rec.id, kind, raw))

        session.add(
            RuleDef(
                project_id=pid,
                name="CO2 alto",
                enabled=True,
                severity="alarm",
                definition={"expr": "co2_ppm > 1000", "schema": 1},
            )
        )
        session.add(
            PresentationConfig(
                project_id=pid,
                severity="alarm",
                style={"color": "#ff0000", "blink": True, "icon": "alert"},
            )
        )
        session.add(
            WorkspaceLayout(
                project_id=pid,
                name="Panel principal",
                is_default=True,
                layout={"dockview": {"panels": ["plano", "alarmas"]}},
            )
        )
        session.add(
            DataSource(project_id=pid, kind="mqtt", config=config)
        )
        await session.commit()

    return pid, records


# --------------------------------------------------------------------------- #
# ModelGraph falso para el unit test (no toca Neo4j)
# --------------------------------------------------------------------------- #
class FakeGraph:
    """Grafo en memoria: solo necesita ``list_mappings`` para el export."""

    def __init__(self, mappings: list[dict] | None = None) -> None:
        self._mappings = mappings or []

    async def list_mappings(self, project_id: str) -> list[dict]:
        return list(self._mappings)


# =========================================================================== #
# UNIT (sin Neo4j)
# =========================================================================== #
@pytest_asyncio.fixture
async def sessionmaker_tmp(tmp_path):
    return await _make_sessionmaker(tmp_path)


async def test_export_arma_bundle_completo_y_sin_secretos(sessionmaker_tmp):
    raw = _GAPHOR.read_bytes()
    pid, _ = await _seed_project(
        sessionmaker_tmp,
        name="Aulas PI3",
        sources=[("gaphor", "modelo.gaphor", raw)],
        with_credentials=True,
    )

    data = await export_project(
        pid, sessionmaker=sessionmaker_tmp, graph=FakeGraph()
    )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())

        # 1) Todas las piezas esperadas están presentes.
        assert "manifest.yaml" in names
        assert "sources/modelo.gaphor" in names
        assert "mappings.yaml" in names
        assert "rules.yaml" in names
        assert "presentation.yaml" in names
        assert "datasources.yaml" in names
        assert any(n.startswith("layouts/") and n.endswith(".json") for n in names)

        # 2) Ni telemetría ni eventos ni estado en vivo.
        assert not any("telemetry" in n.lower() or "event" in n.lower() for n in names)
        manifest = yaml.safe_load(zf.read("manifest.yaml"))
        assert manifest["version"] == 1
        assert manifest["contains_execution_data"] is False

        # 3) Ninguna credencial viaja en NINGÚN archivo del bundle.
        for n in names:
            blob = zf.read(n).decode("utf-8", errors="ignore")
            assert _USUARIO not in blob, f"usuario filtrado en {n}"
            assert _CLAVE not in blob, f"clave filtrada en {n}"

        # 4) Los sha256 del manifest cuadran con los bytes almacenados.
        for src in manifest["sources"]:
            stored = zf.read(f"sources/{src['filename']}")
            import hashlib

            assert hashlib.sha256(stored).hexdigest() == src["sha256"]
        # La fuente del gaphor conserva sus bytes exactos.
        assert zf.read("sources/modelo.gaphor") == raw

        # 5) datasources.yaml tiene la config SIN claves de credenciales.
        ds_doc = yaml.safe_load(zf.read("datasources.yaml"))
        assert len(ds_doc["sources"]) == 1
        cfg = ds_doc["sources"][0]["config"]
        assert "username" not in cfg and "password" not in cfg
        assert cfg["host"] == "broker.local"  # el resto del config se conserva

        # 6) Reglas, presentación y layout se exportan con su contenido.
        rules_doc = yaml.safe_load(zf.read("rules.yaml"))
        assert rules_doc["rules"][0]["name"] == "CO2 alto"
        assert rules_doc["rules"][0]["severity"] == "alarm"
        pres_doc = yaml.safe_load(zf.read("presentation.yaml"))
        assert pres_doc["presentation"][0]["severity"] == "alarm"
        import json

        layout_name = next(n for n in names if n.startswith("layouts/"))
        layout_payload = json.loads(zf.read(layout_name))
        assert layout_payload["name"] == "Panel principal"
        assert layout_payload["is_default"] is True


async def test_export_proyecto_inexistente(sessionmaker_tmp):
    with pytest.raises(ValueError):
        await export_project(
            "no-existe", sessionmaker=sessionmaker_tmp, graph=FakeGraph()
        )


# =========================================================================== #
# INTEGRACIÓN (Neo4j)
# =========================================================================== #
@pytest_asyncio.fixture
async def graph():
    from twinsight.modelgraph import Neo4jModelGraph

    g = Neo4jModelGraph()
    await g.connect()
    yield g
    await g.close()


@pytest.mark.neo4j
async def test_roundtrip_import_reproduce_y_rechaza_manipulacion(tmp_path, graph):
    sm = await _make_sessionmaker(tmp_path)
    gaphor_raw = _GAPHOR.read_bytes()
    svg_raw = _SVG.read_bytes()

    name = f"Aulas-{uuid.uuid4().hex[:8]}"
    pid, records = await _seed_project(
        sm,
        name=name,
        sources=[
            ("gaphor", "modelo.gaphor", gaphor_raw),
            ("svg", "piso.svg", svg_raw),
        ],
        with_credentials=True,
    )

    created_pids: list[str] = [pid]
    try:
        # Poblar el grafo re-ejecutando los traductores de cada fuente.
        for record_id, kind, raw in records:
            batch = get_translator(kind).translate(raw, source_id=record_id)
            await graph.apply_batch(pid, batch)

        # Un mapeo Element<->VisualObject (ambos se reproducen en el import).
        elements = await graph.get_elements(pid)
        assert elements, "el gaphor debería producir elementos"
        element_id = elements[0]["id"]
        svg_batch = get_translator("svg").translate(svg_raw, source_id=records[1][0])
        visual_id = next(n.origin_id for n in svg_batch.nodes if n.label == "VisualObject")
        await graph.create_mapping(
            pid, element_id=element_id, visual_object_id=visual_id
        )

        elements_before = {e["id"] for e in elements}

        # --- Export ---
        data = await export_project(pid, sessionmaker=sm, graph=graph)

        # --- Import en un proyecto nuevo ---
        result = await import_project(data, sessionmaker=sm, graph=graph)
        new_pid = result["project_id"]
        created_pids.append(new_pid)

        # Los elementos se reproducen (mismos ids en el grafo).
        elements_after = {e["id"] for e in await graph.get_elements(new_pid)}
        assert elements_after == elements_before

        # El mapeo se re-aplica sin quedar sin resolver.
        assert result["report"]["unresolved_mappings"] == []
        assert result["report"]["mappings_created"] >= 1
        new_mappings = await graph.list_mappings(new_pid)
        assert any(
            {ep["id"] for ep in m["endpoints"]} >= {element_id, visual_id}
            for m in new_mappings
        )

        # Las fuentes de datos quedan DESCONECTADAS (sin credenciales).
        async with sm() as session:
            dsources = list(
                (
                    await session.execute(
                        select(DataSource).where(DataSource.project_id == new_pid)
                    )
                ).scalars()
            )
        assert len(dsources) == 1
        assert "username" not in dsources[0].config
        assert "password" not in dsources[0].config

        # Reglas / presentación / layouts restaurados.
        assert result["report"]["rules"] == 1
        assert result["report"]["presentation"] == 1
        assert result["report"]["layouts"] == 1
        assert result["report"]["datasources"] == 1

        # --- Roundtrip estable: mismos sha256 de fuentes ---
        data2 = await export_project(new_pid, sessionmaker=sm, graph=graph)

        def _source_hashes(blob: bytes) -> set:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                manifest = yaml.safe_load(zf.read("manifest.yaml"))
                return {s["sha256"] for s in manifest["sources"]}

        assert _source_hashes(data) == _source_hashes(data2)

        # --- Bundle manipulado: alterar una fuente sin tocar su hash ---
        tampered = _tamper_first_source(data)
        with pytest.raises(ValueError, match="manipulado"):
            await import_project(tampered, sessionmaker=sm, graph=graph)

    finally:
        for p in created_pids:
            await graph.delete_project(p)


def _tamper_first_source(data: bytes) -> bytes:
    """Reempaqueta el ZIP cambiando los bytes de la primera fuente (hash inválido)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        entries = [(info.filename, zf.read(info.filename)) for info in zf.infolist()]

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        tampered_done = False
        for fname, blob in entries:
            if fname.startswith("sources/") and not tampered_done:
                blob = blob + b"<!-- manipulado -->"
                tampered_done = True
            zf.writestr(fname, blob)
    return out.getvalue()
