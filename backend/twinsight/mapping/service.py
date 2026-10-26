"""Servicio de mapeo tripartito de TwinSight (RF-040 … RF-048).

El *mapeo tripartito* une tres mundos que viven en el grafo canónico:

* **Element** — un bloque del modelo SysML (lo que existe en la realidad).
* **VisualObject** — su representación en un plano/diagrama (``#id`` en un SVG).
* **Channel** — el canal de telemetría (``topic:metric``) que lo alimenta.

Un mapeo necesita **al menos dos** de esos extremos (2/3) y como mucho los tres
(3/3). Este servicio:

* **sugiere** mapeos (nunca los aplica solo, RF-042) combinando la metadata
  embebida ``dt:*`` (Apéndice F) y la convención de nombres;
* **confirma** sugerencias creándolas en el grafo;
* **crea/borra/lista** mapeos delegando en :class:`ModelGraph`;
* **resuelve** contrapartes y expone el mapa de resolución;
* **detecta huérfanos** (mapeos con extremos que ya no existen, RF-047).

Toda la lógica de *scoring* y emparejamiento es pura (sin E/S), de modo que es
fácil de testear con un ``ModelGraph`` falso.

Limitación conocida: ``ModelGraph.create_mapping`` no acepta todavía un
parámetro de *origen* (auto/manual/yaml) ni permite escribir propiedades del
mapeo tras crearlo. Para no tocar ese contrato, el origen se guarda en un
registro local del servicio y se fusiona al listar. Si un mapeo aparece en el
grafo sin que este servicio lo haya creado, se asume origen ``"manual"``.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from ..modelgraph.base import ModelGraph

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Sugerencia
# --------------------------------------------------------------------------- #
@dataclass
class Suggestion:
    """Una propuesta de mapeo pendiente de confirmación (RF-041, RF-042).

    Nunca se aplica sola: el servicio la devuelve para que una persona la
    confirme. ``score`` va en ``[0, 1]`` y ``confidence`` lo resume en
    ``"alta"`` (metadata ``dt:*``) o ``"media"`` (convención de nombres).
    """

    element_id: str | None = None
    visual_object_id: str | None = None
    channel_id: str | None = None
    score: float = 0.0
    confidence: str = "media"  # "alta" | "media"
    evidence: str = ""

    def endpoints(self) -> int:
        """Cuántos extremos concretos aporta la sugerencia (0..3)."""
        return sum(
            1
            for value in (self.element_id, self.visual_object_id, self.channel_id)
            if value
        )

    def _key(self) -> frozenset[tuple[str, str]]:
        """Conjunto de extremos presentes, para deduplicar sugerencias."""
        pairs: list[tuple[str, str]] = []
        if self.element_id:
            pairs.append(("element", self.element_id))
        if self.visual_object_id:
            pairs.append(("visual", self.visual_object_id))
        if self.channel_id:
            pairs.append(("channel", self.channel_id))
        return frozenset(pairs)


# --------------------------------------------------------------------------- #
# Normalización y accesores tolerantes a la forma del dict
# --------------------------------------------------------------------------- #
def _norm(value: object) -> str:
    """Minúsculas, sin acentos ni espacios sobrantes."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().strip()


def _slug(value: object) -> str:
    """Normaliza y deja solo alfanuméricos (``Sensor-CO2`` ~ ``sensorco2``)."""
    return "".join(ch for ch in _norm(value) if ch.isalnum())


def _node_id(node: dict) -> str | None:
    return node.get("origin_id") or node.get("id") or node.get("mapping_id")


def _element_name(element: dict) -> str | None:
    props = element.get("properties", {}) or {}
    return (
        element.get("name")
        or props.get("name")
        or element.get("label")
        or _node_id(element)
    )


def _visual_id(visual: dict) -> str | None:
    return visual.get("origin_id") or visual.get("id")


def _visual_label(visual: dict) -> str | None:
    props = visual.get("properties", {}) or {}
    return visual.get("label") or props.get("label")


def _visual_core(visual: dict) -> str | None:
    """Parte distintiva del id visual: lo que sigue a ``#`` (``plano.svg#x`` → ``x``)."""
    raw = _visual_id(visual) or _visual_label(visual) or ""
    return raw.split("#")[-1] if "#" in raw else raw


def _channel_id(channel: dict) -> str | None:
    return channel.get("origin_id") or channel.get("id") or channel.get("channel_id")


def _channel_topic(channel: dict) -> str | None:
    return channel.get("topic")


def _channel_metric(channel: dict) -> str | None:
    return channel.get("metric")


def _channel_core(channel: dict) -> str | None:
    """Parte distintiva del canal: la métrica, o el último segmento del topic."""
    metric = _channel_metric(channel)
    if metric:
        return metric
    topic = _channel_topic(channel) or ""
    return topic.split("/")[-1]


# --------------------------------------------------------------------------- #
# Resolución de la metadata dt:* a extremos reales
# --------------------------------------------------------------------------- #
def _resolve_channel(value: object, channels: list[dict]) -> str | None:
    """Encuentra el canal al que apunta ``dt_channel`` (id, ``topic:metric`` o topic)."""
    for channel in channels:
        topic, metric = _channel_topic(channel), _channel_metric(channel)
        if value == _channel_id(channel):
            return _channel_id(channel)
        if topic and metric and value == f"{topic}:{metric}":
            return _channel_id(channel)
        if value == topic:
            return _channel_id(channel)
    target = _slug(value)
    if target:
        for channel in channels:
            if target == _slug(_channel_core(channel)):
                return _channel_id(channel)
    return None


def _resolve_visual(value: object, visuals: list[dict]) -> str | None:
    """Encuentra el VisualObject al que apunta ``dt_visual`` (id o nombre)."""
    for visual in visuals:
        if value == _visual_id(visual):
            return _visual_id(visual)
    target = _slug(value)
    if target:
        for visual in visuals:
            candidates = {
                _slug(_visual_id(visual)),
                _slug(_visual_core(visual)),
                _slug(_visual_label(visual)),
            }
            if target in candidates:
                return _visual_id(visual)
    return None


def _resolve_element(value: object, elements: list[dict]) -> str | None:
    """Encuentra el Element al que apunta ``dt_element`` (id o nombre)."""
    for element in elements:
        if value == _node_id(element):
            return _node_id(element)
    target = _slug(value)
    if target:
        for element in elements:
            if target == _slug(_element_name(element)):
                return _node_id(element)
    return None


# --------------------------------------------------------------------------- #
# Convención de nombres (plantillas opcionales del proyecto)
# --------------------------------------------------------------------------- #
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_]+\}")


def _template_regex(template: str) -> re.Pattern[str]:
    """Convierte ``gemelo/{grupo}/{name}:{metric}`` en una regex con grupos."""
    pieces = re.split(r"(\{[a-zA-Z_]+\})", template)
    pattern = ""
    for piece in pieces:
        if _PLACEHOLDER.fullmatch(piece):
            pattern += f"(?P<{piece[1:-1]}>[^/:#]+)"
        else:
            pattern += re.escape(piece)
    return re.compile("^" + pattern + "$")


def _extract_name(template: str, value: object) -> str | None:
    """Extrae el grupo ``name`` de ``value`` según ``template`` (o ``None``)."""
    if value is None:
        return None
    match = _template_regex(template).match(str(value))
    if not match:
        return None
    return match.groupdict().get("name")


def _elem_slug(element: dict, convention: dict | None) -> str:
    if convention and convention.get("model"):
        name = _extract_name(convention["model"], _element_name(element))
        if name:
            return _slug(name)
    return _slug(_element_name(element))


def _visual_slug(visual: dict, convention: dict | None) -> str:
    if convention and convention.get("visual"):
        name = _extract_name(convention["visual"], _visual_id(visual))
        if name:
            return _slug(name)
    return _slug(_visual_core(visual))


def _channel_slug(channel: dict, convention: dict | None) -> str:
    if convention and convention.get("channel"):
        topic, metric = _channel_topic(channel), _channel_metric(channel)
        value = f"{topic}:{metric}" if metric else (topic or _channel_id(channel))
        name = _extract_name(convention["channel"], value)
        if name:
            return _slug(name)
    return _slug(_channel_core(channel))


# --------------------------------------------------------------------------- #
# Servicio
# --------------------------------------------------------------------------- #
@dataclass
class MappingService:
    """Orquesta sugerencias, confirmación y CRUD de mapeos sobre un grafo."""

    graph: ModelGraph
    # Registro local de orígenes por proyecto: {project_id: {mapping_id: origen}}.
    _origins: dict[str, dict[str, str]] = field(default_factory=dict)

    # --- Sugerencias (lógica pura, RF-041) ----------------------------------
    def suggest(
        self,
        project_id: str,
        elements: list[dict],
        visual_objects: list[dict],
        channels: list[dict],
        conventions: list[dict] | None = None,
        *,
        name_score: float = 0.6,
    ) -> list[Suggestion]:
        """Propone mapeos combinando metadata ``dt:*`` y convención de nombres.

        Las sugerencias **nunca** se aplican aquí (RF-042): se devuelven para
        que alguien las confirme con :meth:`confirm`.
        """
        suggestions: list[Suggestion] = []
        seen: set[frozenset[tuple[str, str]]] = set()

        def add(suggestion: Suggestion) -> None:
            key = suggestion._key()
            if not key or key in seen:
                return
            seen.add(key)
            suggestions.append(suggestion)

        # (1) Metadata dt:* embebida en la fuente → score 1.0, confianza alta.
        ev_meta = "metadata dt:* en la fuente"
        for element in elements:
            eid = _node_id(element)
            props = element.get("properties", {}) or {}
            if props.get("dt_channel"):
                cid = _resolve_channel(props["dt_channel"], channels)
                if cid:
                    add(Suggestion(element_id=eid, channel_id=cid, score=1.0,
                                   confidence="alta", evidence=ev_meta))
            if props.get("dt_visual"):
                vid = _resolve_visual(props["dt_visual"], visual_objects)
                if vid:
                    add(Suggestion(element_id=eid, visual_object_id=vid, score=1.0,
                                   confidence="alta", evidence=ev_meta))

        for visual in visual_objects:
            vid = _visual_id(visual)
            props = visual.get("properties", {}) or {}
            if props.get("dt_element"):
                eid = _resolve_element(props["dt_element"], elements)
                if eid:
                    add(Suggestion(element_id=eid, visual_object_id=vid, score=1.0,
                                   confidence="alta", evidence=ev_meta))
            if props.get("dt_channel"):
                cid = _resolve_channel(props["dt_channel"], channels)
                if cid:
                    add(Suggestion(visual_object_id=vid, channel_id=cid, score=1.0,
                                   confidence="alta", evidence=ev_meta))

        # (2) Convención de nombres → score configurable (<1.0), confianza media.
        # Sin convenciones usamos la heurística de nombre normalizado (robusta).
        ev_name = "coincidencia de nombre"
        for convention in (conventions or [None]):
            for element in elements:
                eid = _node_id(element)
                target = _elem_slug(element, convention)
                if not target:
                    continue
                for visual in visual_objects:
                    if _visual_slug(visual, convention) == target:
                        add(Suggestion(element_id=eid,
                                       visual_object_id=_visual_id(visual),
                                       score=name_score, confidence="media",
                                       evidence=ev_name))
                for channel in channels:
                    if _channel_slug(channel, convention) == target:
                        add(Suggestion(element_id=eid,
                                       channel_id=_channel_id(channel),
                                       score=name_score, confidence="media",
                                       evidence=ev_name))

        return suggestions

    # --- Confirmación (RF-042) ----------------------------------------------
    async def confirm(
        self, project_id: str, suggestions: list[Suggestion], *, origin: str = "auto"
    ) -> list[str]:
        """Crea los mapeos de las sugerencias con ≥2 extremos (las demás se avisan)."""
        created: list[str] = []
        for suggestion in suggestions:
            if suggestion.endpoints() < 2:
                logger.warning(
                    "Sugerencia ignorada: necesita ≥2 extremos (%r)", suggestion
                )
                continue
            mapping_id = await self.create(
                project_id,
                element_id=suggestion.element_id,
                visual_object_id=suggestion.visual_object_id,
                channel_id=suggestion.channel_id,
                origin=origin,
            )
            created.append(mapping_id)
        return created

    # --- CRUD delegando en el grafo -----------------------------------------
    async def create(
        self,
        project_id: str,
        *,
        element_id: str | None = None,
        visual_object_id: str | None = None,
        channel_id: str | None = None,
        origin: str = "manual",
    ) -> str:
        """Crea un mapeo (≥2 extremos) y recuerda su origen (auto/manual/yaml)."""
        present = [x for x in (element_id, visual_object_id, channel_id) if x]
        if len(present) < 2:
            raise ValueError("un mapeo requiere al menos 2 extremos")
        mapping_id = await self.graph.create_mapping(
            project_id,
            element_id=element_id,
            visual_object_id=visual_object_id,
            channel_id=channel_id,
        )
        self._origins.setdefault(project_id, {})[mapping_id] = origin
        return mapping_id

    async def delete(self, project_id: str, mapping_id: str) -> None:
        """Borra un mapeo y olvida su origen."""
        await self.graph.delete_mapping(project_id, mapping_id)
        self._origins.get(project_id, {}).pop(mapping_id, None)

    async def list(self, project_id: str) -> list[dict]:
        """Lista los mapeos anotados con completitud (2/3|3/3) y origen."""
        mappings = await self.graph.list_mappings(project_id)
        origins = self._origins.get(project_id, {})
        result: list[dict] = []
        for mapping in mappings:
            mapping_id = _node_id(mapping)
            present = sum(
                1
                for key in ("element_id", "visual_object_id", "channel_id")
                if mapping.get(key)
            )
            origin = origins.get(mapping_id) or mapping.get("origin") or "manual"
            result.append(
                {
                    **mapping,
                    "id": mapping_id,
                    "completeness": f"{present}/3",
                    "origin": origin,
                }
            )
        return result

    async def resolve(
        self,
        project_id: str,
        *,
        element_id: str | None = None,
        visual_object_id: str | None = None,
        channel_id: str | None = None,
    ) -> dict:
        """Contrapartes mapeadas de un extremo (RF-046); delega en el grafo."""
        return await self.graph.resolve(
            project_id,
            element_id=element_id,
            visual_object_id=visual_object_id,
            channel_id=channel_id,
        )

    async def resolution_map(self, project_id: str) -> dict:
        """Mapa de resolución completo para precargar en el cliente (RF-087)."""
        return await self.graph.resolution_map(project_id)

    # --- Huérfanos (RF-047) --------------------------------------------------
    async def detect_orphans(
        self,
        project_id: str,
        *,
        valid_element_ids: set[str] | None = None,
        valid_visual_ids: set[str] | None = None,
        valid_channel_ids: set[str] | None = None,
    ) -> list[dict]:
        """Mapeos cuyos extremos ya no existen en el grafo (RF-047).

        Si no se pasan los conjuntos de ids válidos, los Elements se obtienen de
        ``graph.get_elements``. Para VisualObjects y Channels no hay un listado
        global en el contrato de :class:`ModelGraph`, así que el API los aporta
        (igual que pasa ``channels`` a :meth:`suggest`); si no se aportan, esos
        extremos se consideran existentes.
        """
        mappings = await self.graph.list_mappings(project_id)
        if valid_element_ids is None:
            elements = await self.graph.get_elements(project_id)
            valid_element_ids = {_node_id(e) for e in elements}

        orphans: list[dict] = []
        for mapping in mappings:
            missing: list[tuple[str, str]] = []
            eid = mapping.get("element_id")
            vid = mapping.get("visual_object_id")
            cid = mapping.get("channel_id")
            if eid and eid not in valid_element_ids:
                missing.append(("element", eid))
            if vid and valid_visual_ids is not None and vid not in valid_visual_ids:
                missing.append(("visual", vid))
            if cid and valid_channel_ids is not None and cid not in valid_channel_ids:
                missing.append(("channel", cid))
            if missing:
                orphans.append({"mapping_id": _node_id(mapping), "missing": missing})
        return orphans
