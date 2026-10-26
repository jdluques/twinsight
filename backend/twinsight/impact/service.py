"""Servicio de impacto: arma los mensajes ``impact_highlight`` (Diseño §4.2).

Es una capa delgada sobre :meth:`ModelGraph.impact`. A partir de un evento de
cambio de estado o de regla, resuelve el conjunto afectado en el grafo (por
``element_id`` o por el/los canal/es implicados) y lo formatea como el mensaje
``impact_highlight`` que el cliente usa para resaltar sin tener que consultar
nada más.

El grafo se puede inyectar (útil en tests); si no, se resuelve de forma perezosa
vía :func:`twinsight.modelgraph.get_model_graph`, de modo que un ``monkeypatch``
sobre esa factoría también funciona.
"""

from __future__ import annotations

from ..bus import RuleEvent, StateChangeEvent
from ..modelgraph.base import ModelGraph


def _resolve_graph(graph: ModelGraph | None) -> ModelGraph:
    """Devuelve el grafo inyectado o el singleton configurado."""
    if graph is not None:
        return graph
    # Import perezoso para respetar monkeypatch de get_model_graph en tests.
    from ..modelgraph import get_model_graph

    return get_model_graph()


def _ids(entries: list[dict] | None) -> list[str]:
    """Extrae los ``id`` de una lista de entradas del cierre de impacto."""
    if not entries:
        return []
    return [e["id"] for e in entries if e.get("id") is not None]


def _message(
    *,
    kind: str,
    ref: str | None,
    severity: str | None,
    impact: dict,
) -> dict:
    """Formatea un ``impact_highlight`` a partir del cierre de impacto."""
    return {
        "type": "impact_highlight",
        "cause": {"kind": kind, "ref": ref},
        "severity": severity,
        "elements": _ids(impact.get("elements")),
        "visual_objects": _ids(impact.get("visual_objects")),
        "channels": _ids(impact.get("channels")),
        "states": _ids(impact.get("states")),
    }


def _empty(kind: str, ref: str | None, severity: str | None) -> dict:
    """Mensaje sin impacto (todas las listas vacías)."""
    return {
        "type": "impact_highlight",
        "cause": {"kind": kind, "ref": ref},
        "severity": severity,
        "elements": [],
        "visual_objects": [],
        "channels": [],
        "states": [],
    }


async def highlight_for_state_change(
    evt: StateChangeEvent, *, graph: ModelGraph | None = None
) -> dict:
    """Arma el ``impact_highlight`` disparado por un cambio de estado.

    El impacto se calcula desde el ``element_id`` de la máquina que transicionó.
    La severidad del cambio de estado (si la hay) se propaga tal cual.
    """
    severity = getattr(evt, "severity", None)
    if not evt.element_id:
        return _empty("state_change", evt.element_id, severity)
    graph = _resolve_graph(graph)
    impact = await graph.impact(evt.project_id, element_id=evt.element_id)
    return _message(
        kind="state_change",
        ref=evt.element_id,
        severity=severity,
        impact=impact,
    )


async def highlight_for_rule_event(
    evt: RuleEvent, *, graph: ModelGraph | None = None
) -> dict:
    """Arma el ``impact_highlight`` disparado por un evento de regla.

    Una regla no referencia un elemento directamente sino uno o más canales
    (las claves de ``payload["values"]``). Se calcula el impacto de cada canal y
    se fusionan los conjuntos afectados, deduplicando por ``id``.
    """
    severity = evt.severity
    channels = _rule_channels(evt)
    if not channels:
        return _empty("rule_event", evt.rule_id, severity)

    graph = _resolve_graph(graph)
    merged: dict[str, dict] = {
        "elements": {},
        "visual_objects": {},
        "channels": {},
        "states": {},
    }
    for channel_id in channels:
        impact = await graph.impact(evt.project_id, channel_id=channel_id)
        for bucket in merged:
            for entry in impact.get(bucket) or []:
                if entry.get("id") is not None:
                    merged[bucket][entry["id"]] = entry

    return _message(
        kind="rule_event",
        ref=evt.rule_id,
        severity=severity,
        impact={k: list(v.values()) for k, v in merged.items()},
    )


def _rule_channels(evt: RuleEvent) -> list[str]:
    """Canales implicados por una regla, leídos de ``payload["values"]``."""
    payload = evt.payload or {}
    values = payload.get("values")
    if isinstance(values, dict):
        return [ch for ch in values if ch]
    # Alternativa por si el payload trae una lista explícita de canales.
    chans = payload.get("channels")
    if isinstance(chans, list):
        return [ch for ch in chans if ch]
    return []
