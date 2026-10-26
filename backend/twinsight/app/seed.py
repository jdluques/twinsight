"""Siembra del proyecto de demo "Aulas PI3".

Idempotente: si el proyecto ya existe, no hace nada. Importa el modelo SysML
(con la máquina de estados de ventilación) y el plano 2D, crea la fuente MQTT y
sus canales, establece los mapeos tripartitos clave (sensor de CO2 y ventilación)
y los del resto por convención, y siembra una regla, la presentación por
severidad y un layout de workspace por defecto.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy import select

from ..importers.registry import get_translator
from ..mapping import MappingService
from ..modelgraph.base import ModelGraph
from ..persistence.models import (
    Channel,
    DataSource,
    ImportRecord,
    PresentationConfig,
    Project,
    RuleDef,
    WorkspaceLayout,
)
from ..sensors import SENSORS
from ..config import get_settings

logger = logging.getLogger(__name__)

DEMO_NAME = "Aulas PI3"
AULAS = ["aula-A", "aula-B"]

# Paleta de severidades por defecto (RF-057).
PRESENTATION_DEFAULTS = {
    "normal": {"color": "#16a34a", "blink": False, "icon": "ok"},
    "info": {"color": "#2563eb", "blink": False, "icon": "info"},
    "warning": {"color": "#d97706", "blink": True, "icon": "warning"},
    "alarm": {"color": "#dc2626", "blink": True, "icon": "alarm", "sound": True},
    "stale": {"color": "#6b7280", "blink": False, "icon": "stale"},
}


def find_demo_dir() -> Path | None:
    candidates = [
        Path(get_settings().data_dir).parent / "examples" / "aulas-pi3",
        Path("/app/examples/aulas-pi3"),
        Path(__file__).resolve().parents[3] / "examples" / "aulas-pi3",
        Path.cwd().parent / "examples" / "aulas-pi3",
    ]
    for c in candidates:
        if (c / "modelo.gaphor").exists():
            return c
    return None


async def _import_source(session, graph: ModelGraph, project_id: str, path: Path, kind: str) -> dict:
    data = path.read_bytes()
    batch = get_translator(kind).translate(data, source_id=f"{kind}:{path.name}")
    orphans = await graph.apply_batch(project_id, batch)
    rec = ImportRecord(
        project_id=project_id, kind=kind, filename=path.name,
        file_hash=hashlib.sha256(data).hexdigest(), raw_file=data,
        status="ok", report=batch.report.model_dump(),
    )
    session.add(rec)
    await session.flush()
    return {"batch": batch, "orphans": orphans}


async def seed_demo(sessionmaker, graph: ModelGraph) -> str | None:
    demo_dir = find_demo_dir()
    if demo_dir is None:
        logger.warning("No se encontraron los assets de demo; se omite el seed.")
        return None

    async with sessionmaker() as session:
        existing = (await session.execute(
            select(Project).where(Project.name == DEMO_NAME)
        )).scalar_one_or_none()
        if existing is not None:
            logger.info("Proyecto demo ya existe (%s); seed omitido.", existing.id)
            return existing.id

        project = Project(name=DEMO_NAME, description="Gemelo digital de dos aulas con IoT (demo)")
        session.add(project)
        await session.flush()
        pid = project.id

        # 1. Importar modelo SysML (con STM) y plano 2D.
        model = await _import_source(session, graph, pid, demo_dir / "modelo.gaphor", "gaphor")
        svg = await _import_source(session, graph, pid, demo_dir / "piso.svg", "svg")

        # 2. Fuente MQTT + canales (uno por aula/sensor/métrica).
        settings = get_settings()
        ds = DataSource(project_id=pid, kind="mqtt", config={
            "host": settings.mqtt_host, "port": settings.mqtt_port, "base_topic": "gemelo",
        })
        session.add(ds)
        await session.flush()

        channel_by_key: dict[tuple[str, str, str], str] = {}
        for aula in AULAS:
            for skey, spec in SENSORS.items():
                for metric in spec.metrics:
                    topic = f"gemelo/{aula}/{skey}"
                    ch = Channel(
                        datasource_id=ds.id, topic_pattern=topic, metric_key=metric.key,
                        unit=metric.unit, label=f"{metric.label} · {aula}",
                    )
                    session.add(ch)
                    await session.flush()
                    channel_by_key[(aula, skey, metric.key)] = ch.id
                    await graph.upsert_channel(pid, ch.id, topic, metric.key)

        await session.commit()

    # 3. Mapeos. Tripartito explícito para lo crítico de la demo + convención
    #    para el resto (fuera de la sesión: usa el grafo).
    elements = await graph.get_elements(pid)
    by_name = {e.get("name"): e for e in elements}
    visual_objects = [n.model_dump() if hasattr(n, "model_dump") else n
                      for n in svg["batch"].nodes if getattr(n, "label", None) == "VisualObject"]

    def visual_id(fragment: str) -> str | None:
        for v in visual_objects:
            oid = v.get("origin_id", "")
            if fragment in oid:
                return oid
        return None

    co2_channel = channel_by_key.get(("aula-A", "mhz19b", "co2_ppm"))

    # 3a. Sensor de CO2 de aula-A: elemento ↔ canal ↔ forma del plano.
    co2_elem = next((e for e in elements
                     if "MH-Z19B" in str(e.get("name", "")) and "Aula A" in str(e.get("name", ""))), None)
    co2_visual = visual_id("sensor-co2-aulaA")
    if co2_elem and co2_channel:
        try:
            await graph.create_mapping(pid, element_id=co2_elem["origin_id"],
                                       visual_object_id=co2_visual, channel_id=co2_channel)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mapeo CO2 falló: %s", exc)

    # 3b. Ventilación de aula-A: elemento (dueño de la STM) ↔ rejilla ↔ canal CO2.
    sms = await graph.get_state_machines(pid)
    if sms:
        full = await graph.get_state_machine(pid, sms[0].get("origin_id"))
        ventil_elem = full.get("element_id") if full else None
        rejilla_visual = visual_id("rejilla-vent-aulaA")
        if ventil_elem and co2_channel:
            try:
                await graph.create_mapping(pid, element_id=ventil_elem,
                                           visual_object_id=rejilla_visual, channel_id=co2_channel)
            except Exception as exc:  # noqa: BLE001
                logger.warning("mapeo ventilación falló: %s", exc)

    # 3c. Resto de sensores (todos): elemento (por modelo) ↔ forma ↔ canal.
    #     tipo_visual, clave_sensor, modelo_en_nombre, métrica principal
    SENSOR_MAP = [
        ("co2", "mhz19b", "MH-Z19B", "co2_ppm"),
        ("temp", "bme280", "BME280", "temp_c"),
        ("ruido", "inmp441", "INMP441", "spl_db"),
        ("luz", "bh1750", "BH1750", "lux"),
        ("ocupacion", "mlx90640", "MLX90640", "occupancy_est"),
    ]
    for aula, suffix in [("aula-A", "A"), ("aula-B", "B")]:
        for vtype, skey, model, metric in SENSOR_MAP:
            if vtype == "co2" and suffix == "A":
                continue  # ya mapeado explícitamente en 3a
            elem = next((e for e in elements
                         if model in str(e.get("name", "")) and f"Aula {suffix}" in str(e.get("name", ""))), None)
            vis = visual_id(f"sensor-{vtype}-aula{suffix}")
            ch = channel_by_key.get((aula, skey, metric))
            if elem and (vis or ch):
                try:
                    await graph.create_mapping(pid, element_id=elem["origin_id"],
                                               visual_object_id=vis, channel_id=ch)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("mapeo %s/%s falló: %s", aula, vtype, exc)

    # 4. Regla, presentación y layout por defecto.
    async with sessionmaker() as session:
        session.add(RuleDef(
            project_id=pid, name="CO2 alto en Aula A", enabled=True, severity="warning",
            definition={"version": 1, "when": {"all": [
                {"channel": co2_channel, "op": ">", "value": 1000}]},
                "for_seconds": 10, "clear_when": {"all": [
                    {"channel": co2_channel, "op": "<", "value": 800}]},
                "severity": "warning"},
        ))
        for sev, style in PRESENTATION_DEFAULTS.items():
            session.add(PresentationConfig(project_id=pid, severity=sev, style=style))
        session.add(WorkspaceLayout(
            project_id=pid, name="Operación", is_default=True,
            layout={"name": "Operación", "panels": [
                {"id": "p-bdd", "type": "sysml-diagram", "config": {"title": "BDD — Sistema de Aulas"}},
                {"id": "p-stm", "type": "state-machine", "config": {"title": "STM — Ventilación Aula A"}},
                {"id": "p-plano", "type": "physical-2d", "config": {"title": "Plano — Piso 3"}},
                {"id": "p-tele", "type": "telemetry", "config": {"title": "CO₂ Aula A", "metric": "co2_ppm", "aula": "aula-A"}},
                {"id": "p-eventos", "type": "events", "config": {"title": "Eventos"}},
                {"id": "p-grafo", "type": "graph-explorer", "config": {"title": "Grafo"}},
            ]},
        ))
        await session.commit()

    logger.info("Proyecto demo sembrado (%s).", pid)
    return pid
