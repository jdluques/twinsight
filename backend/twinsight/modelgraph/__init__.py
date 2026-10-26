"""Capa de acceso al grafo canónico del modelo (Diseño §3.1).

Expone la interfaz abstracta :class:`ModelGraph` y una factoría
:func:`get_model_graph` que devuelve la implementación según
``get_settings().graph_backend`` (por ahora solo ``"neo4j"``). La instancia es
un singleton de proceso para reutilizar el driver y su pool de conexiones.
"""

from __future__ import annotations

from ..config import get_settings
from .base import ModelGraph
from .neo4j_graph import Neo4jModelGraph

__all__ = ["ModelGraph", "Neo4jModelGraph", "get_model_graph", "reset_model_graph"]

_instance: ModelGraph | None = None


def get_model_graph() -> ModelGraph:
    """Devuelve el singleton de ``ModelGraph`` del backend configurado."""
    global _instance
    if _instance is None:
        backend = get_settings().graph_backend
        if backend == "neo4j":
            _instance = Neo4jModelGraph()
        else:
            raise ValueError(f"Backend de grafo no soportado: {backend!r}")
    return _instance


def reset_model_graph() -> None:
    """Olvida el singleton (útil en tests y al reconfigurar)."""
    global _instance
    _instance = None
