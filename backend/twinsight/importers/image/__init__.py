"""Traductor de representaciones físicas en imagen raster (PNG/JPG).

A diferencia del SVG, una imagen no contiene objetos semánticos: los objetos
visuales se añaden después como **anclas** (puntos con coordenadas) mediante la
API ``POST /representations/{id}/anchors``. Este módulo solo reconoce el
formato y expone :func:`anchor_to_node` para materializar un ancla como nodo.
"""

from __future__ import annotations

from twinsight.importers.image.translator import ImageTranslator, anchor_to_node

__all__ = ["ImageTranslator", "anchor_to_node"]
