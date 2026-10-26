"""Interfaz de la capa de acceso al grafo canónico (RF-106, Diseño §3.1).

``ModelGraph`` es el **único** punto que habla con el motor de grafos. El resto
de la plataforma depende de esta interfaz, nunca del driver concreto, de modo
que el backend de grafo (Neo4j por defecto) sea sustituible.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..importers.base import GraphBatch


class ModelGraph(ABC):
    """Operaciones sobre el grafo del modelo de un proyecto."""

    # --- Ciclo de vida ---
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def ping(self) -> bool:
        """True si el grafo responde (para /health)."""

    # --- Importación (dual-store, Diseño §3.3) ---
    @abstractmethod
    async def apply_batch(self, project_id: str, batch: GraphBatch) -> list[str]:
        """Aplica un lote idempotente por ``origin_id`` en una transacción.

        Elimina los nodos de esa ``source_id`` ausentes del lote y devuelve la
        lista de mapeos huérfanos (extremos que dejaron de existir).
        """

    @abstractmethod
    async def delete_project(self, project_id: str) -> None:
        """``MATCH (n {project_id}) DETACH DELETE n`` (RF-003)."""

    # --- Exploración del modelo ---
    @abstractmethod
    async def get_elements(
        self, project_id: str, kind: str | None = None, q: str | None = None
    ) -> list[dict]: ...

    @abstractmethod
    async def get_element(self, project_id: str, origin_id: str) -> dict | None:
        """Detalle con relaciones, mapeos y canales (RF-014)."""

    @abstractmethod
    async def get_diagrams(self, project_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_diagram(self, project_id: str, diagram_id: str) -> dict | None:
        """Diagrama con ítems y geometría para el render (RF-011, RF-012)."""

    @abstractmethod
    async def get_state_machines(self, project_id: str) -> list[dict]: ...

    @abstractmethod
    async def get_state_machine(self, project_id: str, machine_id: str) -> dict | None:
        """Estados, transiciones (con guard_status), estado activo (RF-050)."""

    # --- Canales (espejo del relacional) ---
    @abstractmethod
    async def upsert_channel(
        self, project_id: str, channel_id: str, topic: str, metric: str
    ) -> None: ...

    # --- Estado discreto en vivo (RF-102; la telemetría NUNCA entra al grafo) ---
    @abstractmethod
    async def set_active_state(self, project_id: str, machine_id: str, state_id: str) -> None: ...

    @abstractmethod
    async def set_element_severity(self, project_id: str, element_id: str, severity: str) -> None: ...

    @abstractmethod
    async def set_channel_stale(self, project_id: str, channel_id: str, stale: bool) -> None: ...

    # --- Mapeo tripartito (RF-040, RF-046, RF-048) ---
    @abstractmethod
    async def create_mapping(
        self,
        project_id: str,
        *,
        element_id: str | None = None,
        visual_object_id: str | None = None,
        channel_id: str | None = None,
    ) -> str:
        """Crea el mapeo como relaciones ``MAPPED_TO``; ≥2 extremos. Devuelve id."""

    @abstractmethod
    async def delete_mapping(self, project_id: str, mapping_id: str) -> None: ...

    @abstractmethod
    async def list_mappings(self, project_id: str) -> list[dict]:
        """Mapeos con origen y completitud (2/3 o 3/3) (RF-044)."""

    @abstractmethod
    async def resolution_map(self, project_id: str) -> dict:
        """Mapa de resolución completo para precargar en el cliente (RF-087)."""

    @abstractmethod
    async def resolve(
        self,
        project_id: str,
        *,
        element_id: str | None = None,
        visual_object_id: str | None = None,
        channel_id: str | None = None,
    ) -> dict:
        """Contrapartes mapeadas de un extremo (RF-046)."""

    # --- Dependencias de guardas (base del impacto) ---
    @abstractmethod
    async def set_guard_dependencies(
        self, project_id: str, transition_id: str, channel_ids: list[str]
    ) -> None:
        """(Re)crea aristas ``GUARD_DEPENDS_ON`` transición→canal."""

    @abstractmethod
    async def set_guard_status(self, project_id: str, transition_id: str, status: str) -> None:
        """``ok`` | ``inert`` (RF-052)."""

    # --- Grafo e impacto (RF-103, RF-104, RF-105) ---
    @abstractmethod
    async def neighborhood(self, project_id: str, node_id: str, depth: int = 1) -> dict: ...

    @abstractmethod
    async def impact(
        self, project_id: str, *, element_id: str | None = None,
        channel_id: str | None = None, max_depth: int = 4,
    ) -> dict:
        """Cierre de impacto con rutas de dependencia (RF-103)."""

    @abstractmethod
    async def failure_modes(self, project_id: str, mode: str | None = None) -> list[dict]: ...
