"""Registro de traductores de importación.

Punto único para seleccionar el :class:`Translator` adecuado por ``kind`` o por
detección de contenido. Añadir un formato = registrar un traductor aquí
(RF-018, RF-027).
"""

from __future__ import annotations

from .base import Translator
from .gaphor import GaphorTranslator
from .image.translator import ImageTranslator
from .svg.translator import SvgTranslator

# Instancias (los traductores son sin estado).
_TRANSLATORS: dict[str, Translator] = {
    "gaphor": GaphorTranslator(),
    "svg": SvgTranslator(),
    "image": ImageTranslator(),
}


def get_translator(kind: str) -> Translator:
    """Devuelve el traductor por ``kind`` o lanza KeyError."""
    return _TRANSLATORS[kind]


def detect_translator(filename: str, data: bytes) -> Translator | None:
    """Elige el traductor que reconoce el archivo (por extensión/cabecera)."""
    head = data[:512]
    # Prioridad por extensión explícita.
    lower = filename.lower()
    if lower.endswith(".gaphor"):
        return _TRANSLATORS["gaphor"]
    if lower.endswith(".svg"):
        return _TRANSLATORS["svg"]
    if lower.endswith((".png", ".jpg", ".jpeg")):
        return _TRANSLATORS["image"]
    # Si no, por detección de contenido.
    for translator in _TRANSLATORS.values():
        try:
            if translator.can_import(filename, head):
                return translator
        except Exception:  # noqa: BLE001 - un traductor no debe romper la detección
            continue
    return None


def kind_for_file(filename: str, data: bytes) -> str | None:
    t = detect_translator(filename, data)
    return t.kind if t else None
