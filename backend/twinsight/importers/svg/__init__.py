"""Traductor de representaciones físicas 2D en SVG (RF-027, Apéndice F).

Convierte un plano SVG en un :class:`~twinsight.importers.base.GraphBatch` de
objetos visuales (``VisualObject``), detectando metadata de mapeo (``dt:*``) y
variantes gráficas por estado (capas ``estado:*``).
"""

from __future__ import annotations

from twinsight.importers.svg.sanitize import sanitize_svg
from twinsight.importers.svg.translator import SvgTranslator

__all__ = ["SvgTranslator", "sanitize_svg"]
