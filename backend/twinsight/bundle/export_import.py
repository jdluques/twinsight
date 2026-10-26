"""Bundle portable ``.dtproj`` de TwinSight (Apéndice E).

Un ``.dtproj`` es un ZIP autocontenido con la *definición* de un proyecto: las
fuentes importadas tal cual, los mapeos, las reglas, la presentación, los
layouts del espacio de trabajo y las fuentes de datos (SIN credenciales). Sirve
para mover un proyecto entre instancias o versionarlo en git.

Lo que el bundle **nunca** lleva (RF-110, RF-112):

* telemetría ni lecturas (``TelemetryReading``),
* eventos ni estado en vivo (``Event``, severidades activas, estados activos),
* credenciales de las fuentes de datos (``username`` / ``password``).

Por eso ``manifest.contains_execution_data`` es siempre ``False``.

Estructura del ZIP::

    manifest.yaml        # {version, name, description, exported_at, sources[], contains_execution_data}
    sources/<archivo>    # raw_file de cada ImportRecord, byte a byte
    mappings.yaml        # formato de twinsight.mapping.yaml_format
    rules.yaml           # {rules: [{name, enabled, severity, definition}]}
    presentation.yaml    # {presentation: [{severity, style}]}
    layouts/<nombre>.json  # {name, is_default, layout}
    datasources.yaml     # {sources: [{kind, config}]}  (config sin credenciales)

Las dos funciones públicas, :func:`export_project` e :func:`import_project`,
admiten inyectar el ``sessionmaker`` relacional y el ``ModelGraph`` para poder
probarlas sin una instancia real (el unit test usa SQLite temporal y un grafo
falso; la integración usa Neo4j).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..mapping.yaml_format import export_yaml, import_yaml
from ..persistence.db import get_sessionmaker
from ..persistence.models import (
    DataSource,
    ImportRecord,
    PresentationConfig,
    Project,
    RuleDef,
    WorkspaceLayout,
)

BUNDLE_VERSION = 1

# Claves de credenciales que NUNCA viajan en el bundle (RF-112).
_CREDENTIAL_KEYS = frozenset({"username", "password"})


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _sha256(data: bytes) -> str:
    """Hash hexadecimal SHA-256 de unos bytes."""
    return hashlib.sha256(data).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    """Minúsculas, sin acentos ni espacios sobrantes (para casar por nombre)."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().strip()


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    """Nombre de archivo seguro (sin separadores de ruta ni caracteres raros)."""
    cleaned = _SAFE_NAME.sub("_", (name or "").strip()) or "sin-nombre"
    return cleaned


def _strip_credentials(config: dict | None) -> dict:
    """Devuelve una copia del config de una fuente de datos SIN credenciales."""
    config = dict(config or {})
    for key in _CREDENTIAL_KEYS:
        config.pop(key, None)
    return config


def _dump_yaml(document: Any) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


# --------------------------------------------------------------------------- #
# Mapeos: conversión grafo <-> entradas del YAML
# --------------------------------------------------------------------------- #
def _mapping_to_entry(mapping: dict) -> dict:
    """Convierte un mapeo de ``ModelGraph.list_mappings`` a una entrada YAML.

    Reparte los extremos por su etiqueta en las claves ``element_id`` /
    ``visual_object_id`` / ``channel_id`` que entiende ``export_yaml``.
    """
    entry: dict = {}
    for endpoint in mapping.get("endpoints", []):
        label = endpoint.get("label")
        nid = endpoint.get("id")
        if not nid:
            continue
        if label == "Element":
            entry["element_id"] = nid
        elif label == "VisualObject":
            entry["visual_object_id"] = nid
        elif label == "Channel":
            entry["channel_id"] = nid
    return entry


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
async def export_project(
    project_id: str,
    *,
    sessionmaker: async_sessionmaker | None = None,
    graph: Any | None = None,
) -> bytes:
    """Arma el ``.dtproj`` (ZIP en memoria) con la definición del proyecto.

    No incluye telemetría, eventos, estado en vivo ni credenciales.
    """
    sm = sessionmaker or get_sessionmaker()

    async with sm() as session:
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            raise ValueError(f"proyecto no encontrado: {project_id!r}")

        records = list(
            (
                await session.execute(
                    select(ImportRecord).where(ImportRecord.project_id == project_id)
                )
            ).scalars()
        )
        rules = list(
            (
                await session.execute(
                    select(RuleDef).where(RuleDef.project_id == project_id)
                )
            ).scalars()
        )
        presentation = list(
            (
                await session.execute(
                    select(PresentationConfig).where(
                        PresentationConfig.project_id == project_id
                    )
                )
            ).scalars()
        )
        layouts = list(
            (
                await session.execute(
                    select(WorkspaceLayout).where(
                        WorkspaceLayout.project_id == project_id
                    )
                )
            ).scalars()
        )
        datasources = list(
            (
                await session.execute(
                    select(DataSource).where(DataSource.project_id == project_id)
                )
            ).scalars()
        )

        project_name = project.name
        project_description = project.description or ""

    # --- Fuentes: cada raw_file bajo sources/, con nombre único en el ZIP ---
    manifest_sources: list[dict] = []
    stored_sources: list[tuple[str, bytes]] = []
    used_names: set[str] = set()
    for record in records:
        raw = bytes(record.raw_file or b"")
        base = _safe_filename(record.filename or f"{record.kind}.bin")
        stored_name = base
        counter = 2
        while stored_name in used_names:
            stored_name = f"{counter}_{base}"
            counter += 1
        used_names.add(stored_name)
        stored_sources.append((stored_name, raw))
        manifest_sources.append(
            {"filename": stored_name, "kind": record.kind, "sha256": _sha256(raw)}
        )

    # --- Mapeos vía el grafo ---
    graph_obj = graph if graph is not None else _lazy_graph()
    raw_mappings = await graph_obj.list_mappings(project_id)
    mapping_entries = [_mapping_to_entry(m) for m in raw_mappings]
    mapping_entries = [e for e in mapping_entries if e]
    mappings_yaml = export_yaml(project_name, mapping_entries)

    # --- Reglas / presentación / datasources ---
    rules_doc = {
        "version": BUNDLE_VERSION,
        "rules": [
            {
                "name": r.name,
                "enabled": bool(r.enabled),
                "severity": r.severity,
                "definition": r.definition or {},
            }
            for r in rules
        ],
    }
    presentation_doc = {
        "version": BUNDLE_VERSION,
        "presentation": [
            {"severity": p.severity, "style": p.style or {}} for p in presentation
        ],
    }
    datasources_doc = {
        "version": BUNDLE_VERSION,
        "sources": [
            {"kind": ds.kind, "config": _strip_credentials(ds.config)}
            for ds in datasources
        ],
    }

    manifest = {
        "version": BUNDLE_VERSION,
        "name": project_name,
        "description": project_description,
        "exported_at": _utcnow_iso(),
        "sources": manifest_sources,
        "contains_execution_data": False,
    }

    # --- Ensamblar el ZIP ---
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", _dump_yaml(manifest))
        for stored_name, raw in stored_sources:
            zf.writestr(f"sources/{stored_name}", raw)
        zf.writestr("mappings.yaml", mappings_yaml)
        zf.writestr("rules.yaml", _dump_yaml(rules_doc))
        zf.writestr("presentation.yaml", _dump_yaml(presentation_doc))
        zf.writestr("datasources.yaml", _dump_yaml(datasources_doc))
        layout_names: set[str] = set()
        for layout in layouts:
            payload = {
                "name": layout.name,
                "is_default": bool(layout.is_default),
                "layout": layout.layout or {},
            }
            base = _safe_filename(layout.name)
            fname = base
            counter = 2
            while fname in layout_names:
                fname = f"{counter}_{base}"
                counter += 1
            layout_names.add(fname)
            zf.writestr(
                f"layouts/{fname}.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
async def import_project(
    data: bytes,
    *,
    new_name: str | None = None,
    sessionmaker: async_sessionmaker | None = None,
    graph: Any | None = None,
) -> dict:
    """Importa un ``.dtproj`` como un proyecto NUEVO y devuelve un reporte.

    Pasos: valida el manifest y re-verifica los hashes de las fuentes (si no
    cuadran, el bundle fue manipulado); crea el proyecto con nombre único;
    por cada fuente recrea su ``ImportRecord`` y re-ejecuta su traductor contra
    el grafo; restaura reglas, presentación y layouts; recrea las fuentes de
    datos DESCONECTADAS (sin credenciales); y re-aplica los mapeos resolviendo
    los extremos por id o por nombre (los no resueltos se reportan).
    """
    sm = sessionmaker or get_sessionmaker()
    graph_obj = graph if graph is not None else _lazy_graph()

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        if "manifest.yaml" not in names:
            raise ValueError("bundle inválido: falta manifest.yaml")
        manifest = yaml.safe_load(zf.read("manifest.yaml")) or {}
        if not isinstance(manifest, dict):
            raise ValueError("manifest.yaml inválido")
        version = manifest.get("version")
        if version != BUNDLE_VERSION:
            raise ValueError(
                f"versión de bundle no soportada: {version!r} "
                f"(se espera {BUNDLE_VERSION})"
            )

        # --- Re-verificar hashes de las fuentes (anti-manipulación) ---
        manifest_sources = manifest.get("sources") or []
        source_blobs: list[tuple[str, str, bytes]] = []  # (filename, kind, raw)
        for src in manifest_sources:
            filename = src.get("filename")
            kind = src.get("kind")
            expected = src.get("sha256")
            arcname = f"sources/{filename}"
            if arcname not in names:
                raise ValueError(f"bundle manipulado: falta la fuente {filename!r}")
            raw = zf.read(arcname)
            if _sha256(raw) != expected:
                raise ValueError(
                    f"bundle manipulado: el hash de {filename!r} no coincide"
                )
            source_blobs.append((filename, kind, raw))

        mappings_text = (
            zf.read("mappings.yaml").decode("utf-8")
            if "mappings.yaml" in names
            else ""
        )
        rules_doc = (
            yaml.safe_load(zf.read("rules.yaml"))
            if "rules.yaml" in names
            else None
        )
        presentation_doc = (
            yaml.safe_load(zf.read("presentation.yaml"))
            if "presentation.yaml" in names
            else None
        )
        datasources_doc = (
            yaml.safe_load(zf.read("datasources.yaml"))
            if "datasources.yaml" in names
            else None
        )
        layout_payloads = [
            json.loads(zf.read(n))
            for n in sorted(names)
            if n.startswith("layouts/") and n.endswith(".json")
        ]

    base_name = (new_name or manifest.get("name") or "Proyecto importado").strip()

    # --- Crear el proyecto (nombre único) ---
    async with sm() as session:
        name = await _unique_project_name(session, base_name)
        project = Project(name=name, description=manifest.get("description") or "")
        session.add(project)
        await session.flush()
        project_id = project.id

        import_records: list[tuple[str, str, bytes]] = []  # (record_id, kind, raw)
        for filename, kind, raw in source_blobs:
            record = ImportRecord(
                project_id=project_id,
                kind=kind,
                filename=filename,
                file_hash=_sha256(raw),
                raw_file=raw,
                status="ok",
            )
            session.add(record)
            await session.flush()
            import_records.append((record.id, kind, raw))

        # Reglas.
        rules_restored = 0
        for rule in _coerce_list(rules_doc, "rules"):
            session.add(
                RuleDef(
                    project_id=project_id,
                    name=rule.get("name", ""),
                    enabled=bool(rule.get("enabled", True)),
                    severity=rule.get("severity", "warning"),
                    definition=rule.get("definition") or {},
                )
            )
            rules_restored += 1

        # Presentación.
        presentation_restored = 0
        for pres in _coerce_list(presentation_doc, "presentation"):
            session.add(
                PresentationConfig(
                    project_id=project_id,
                    severity=pres.get("severity", "normal"),
                    style=pres.get("style") or {},
                )
            )
            presentation_restored += 1

        # Layouts.
        layouts_restored = 0
        for payload in layout_payloads:
            session.add(
                WorkspaceLayout(
                    project_id=project_id,
                    name=payload.get("name", "layout"),
                    is_default=bool(payload.get("is_default", False)),
                    layout=payload.get("layout") or {},
                )
            )
            layouts_restored += 1

        # Fuentes de datos: DESCONECTADAS, sin credenciales.
        datasources_restored = 0
        for ds in _coerce_list(datasources_doc, "sources"):
            session.add(
                DataSource(
                    project_id=project_id,
                    kind=ds.get("kind", "mqtt"),
                    config=_strip_credentials(ds.get("config")),
                )
            )
            datasources_restored += 1

        await session.commit()

    # --- Re-ejecutar los traductores contra el grafo ---
    from ..importers.registry import detect_translator, get_translator

    source_report: list[dict] = []
    total_elements = 0
    for record_id, kind, raw in import_records:
        translator = None
        try:
            translator = get_translator(kind)
        except KeyError:
            translator = detect_translator(record_id, raw)
        if translator is None:
            source_report.append({"kind": kind, "status": "sin-traductor"})
            continue
        batch = translator.translate(raw, source_id=record_id)
        await graph_obj.apply_batch(project_id, batch)
        total_elements += int(getattr(batch.report, "elements", 0) or 0)
        source_report.append(
            {"kind": kind, "status": "ok", "elements": batch.report.elements}
        )

    # --- Re-aplicar los mapeos resolviendo extremos por id o nombre ---
    created_mappings = 0
    unresolved: list[dict] = []
    if mappings_text.strip():
        _conventions, entries = import_yaml(mappings_text)
        elements = await graph_obj.get_elements(project_id)
        element_ids = {e.get("id") for e in elements}
        element_by_name: dict[str, str] = {}
        for e in elements:
            key = _norm(e.get("name"))
            if key:
                element_by_name.setdefault(key, e.get("id"))

        for entry in entries:
            element_id = _resolve_element(entry.model, element_ids, element_by_name)
            visual_object_id = entry.visual  # el id visual es estable entre export/import
            channel_id = entry.channel
            try:
                await graph_obj.create_mapping(
                    project_id,
                    element_id=element_id,
                    visual_object_id=visual_object_id,
                    channel_id=channel_id,
                )
                created_mappings += 1
            except ValueError:
                unresolved.append(
                    {
                        "model": entry.model,
                        "visual": entry.visual,
                        "channel": entry.channel,
                        "reason": "menos de 2 extremos resolubles en el grafo",
                    }
                )

    report = {
        "elements": total_elements,
        "sources": source_report,
        "mappings_created": created_mappings,
        "unresolved_mappings": unresolved,
        "rules": rules_restored,
        "presentation": presentation_restored,
        "layouts": layouts_restored,
        "datasources": datasources_restored,
    }
    return {"project_id": project_id, "report": report}


# --------------------------------------------------------------------------- #
# Helpers de import
# --------------------------------------------------------------------------- #
def _lazy_graph():
    """Obtiene el singleton de ``ModelGraph`` (import perezoso para los tests)."""
    from ..modelgraph import get_model_graph

    return get_model_graph()


async def _unique_project_name(session, base: str) -> str:
    """Devuelve un nombre de proyecto único añadiendo ``(n)`` si hace falta."""
    name = base
    counter = 2
    while (
        await session.execute(select(Project).where(Project.name == name))
    ).scalar_one_or_none() is not None:
        name = f"{base} ({counter})"
        counter += 1
    return name


def _coerce_list(document: Any, key: str) -> list[dict]:
    """Normaliza un documento a una lista de dicts.

    Acepta tanto ``{<key>: [...]}`` como una lista directa en el nivel superior.
    """
    if document is None:
        return []
    if isinstance(document, list):
        items = document
    elif isinstance(document, dict):
        items = document.get(key) or []
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def _resolve_element(
    value: str | None, element_ids: set, element_by_name: dict[str, str]
) -> str | None:
    """Resuelve el extremo Element de un mapeo por id o, si no, por nombre."""
    if not value:
        return None
    if value in element_ids:
        return value
    return element_by_name.get(_norm(value), value)
