"""``ImageTranslator`` — representaciones físicas en imagen raster (PNG/JPG).

No se analizan píxeles: la importación de una imagen produce un
:class:`GraphBatch` sin objetos visuales. Los objetos se crean luego como
anclas (puntos ``(x, y)`` sobre la imagen) vía la API, materializadas con
:func:`anchor_to_node`.
"""

from __future__ import annotations

import struct

from twinsight.importers.base import GraphBatch, GraphNode, ImportReport

# Firmas de cabecera de los formatos soportados.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _dimensiones_png(data: bytes) -> tuple[int, int] | None:
    """Ancho y alto de un PNG leyendo el chunk IHDR (sin dependencias)."""
    if not data.startswith(_PNG_MAGIC) or len(data) < 24:
        return None
    # IHDR empieza en el offset 16: width (4 bytes) + height (4 bytes), big-endian.
    try:
        ancho, alto = struct.unpack(">II", data[16:24])
    except struct.error:
        return None
    return ancho, alto


class ImageTranslator:
    """Traductor de imágenes raster (PNG/JPG) — Diseño §6.1."""

    kind = "image"

    def can_import(self, filename: str, head: bytes) -> bool:
        """Reconoce por cabecera PNG (``\\x89PNG``) o JPEG (``\\xff\\xd8``)."""
        if head.startswith(_PNG_MAGIC) or head.startswith(b"\x89PNG"):
            return True
        return head.startswith(b"\xff\xd8")

    def translate(self, data: bytes, *, source_id: str) -> GraphBatch:
        """Importa la imagen: GraphBatch vacío de objetos (solo metadata)."""
        report = ImportReport(visual_objects=0)

        dims = _dimensiones_png(data)
        if dims is not None:
            ancho, alto = dims
            report.warnings.append(
                f"Imagen PNG de {ancho}x{alto} px; las anclas se añaden por API."
            )
        else:
            report.warnings.append(
                "Imagen importada; las anclas se añaden por API."
            )

        return GraphBatch(source_id=source_id, kind=self.kind, report=report)


def anchor_to_node(
    representation_id: str, name: str, x: float, y: float
) -> GraphNode:
    """Materializa un ancla ``(x, y)`` como ``VisualObject`` tipo ``"anchor"``.

    La usa la API al crear un ancla (``POST /representations/{id}/anchors``).
    El ``origin_id`` combina la representación y el nombre del ancla para que la
    identidad sea estable e idempotente por ``origin_id``.
    """
    origin_id = f"{representation_id}#{name}"
    return GraphNode(
        label="VisualObject",
        origin_id=origin_id,
        properties={
            "representation_id": representation_id,
            "kind": "anchor",
            "severity": "normal",
            "variants": [],
            "label": name,
            "x": float(x),
            "y": float(y),
        },
    )
