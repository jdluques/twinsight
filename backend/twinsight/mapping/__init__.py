"""Mapeo tripartito de TwinSight: sugerencias, YAML y resolución.

Une Element (modelo) ↔ VisualObject (plano) ↔ Channel (telemetría). Ver
:mod:`twinsight.mapping.service` y :mod:`twinsight.mapping.yaml_format`.
"""

from __future__ import annotations

from .service import MappingService, Suggestion
from .yaml_format import MappingEntry, export_yaml, import_yaml

__all__ = [
    "MappingService",
    "Suggestion",
    "MappingEntry",
    "export_yaml",
    "import_yaml",
]
