"""Saneado de SVG para servirlo con seguridad al frontend.

El endpoint ``GET /representations/{id}`` devuelve el SVG importado para que el
navegador lo renderice inline. Un SVG puede transportar vectores de XSS
(``<script>``, manejadores ``on*``, ``<foreignObject>`` con HTML, referencias a
hosts externos), por lo que antes de servirlo se eliminan esos elementos.

Se opera con expresiones regulares sobre el texto para preservar el formato y
los namespaces originales (``inkscape:*``), que un reserializado XML alteraría.
Esto es defensa de servido, complementaria a la validación en la importación.
"""

from __future__ import annotations

import re

# <script>...</script> (con o sin contenido) y <script .../> autocerrado.
_SCRIPT = re.compile(
    r"<script\b[^>]*>.*?</script\s*>|<script\b[^>]*/\s*>",
    re.IGNORECASE | re.DOTALL,
)

# <foreignObject>...</foreignObject> y variante autocerrada (incrusta HTML).
_FOREIGN = re.compile(
    r"<foreignObject\b[^>]*>.*?</foreignObject\s*>|<foreignObject\b[^>]*/\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Atributos de evento on* (onload, onclick, onmouseover, ...).
_ON_ATTR = re.compile(
    r"""\s+on[a-zA-Z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""",
    re.IGNORECASE,
)

# Referencias externas: href / xlink:href a http(s): o protocolo-relativo //.
_HREF_EXTERNO = re.compile(
    r"""\s+(?:xlink:)?href\s*=\s*("(?:https?:)?//[^"]*"|'(?:https?:)?//[^']*')""",
    re.IGNORECASE,
)

# javascript: en cualquier href.
_HREF_JS = re.compile(
    r"""\s+(?:xlink:)?href\s*=\s*("\s*javascript:[^"]*"|'\s*javascript:[^']*')""",
    re.IGNORECASE,
)


def sanitize_svg(data: bytes) -> str:
    """Devuelve el SVG saneado como ``str``, listo para servir inline.

    Elimina ``<script>``, ``<foreignObject>``, atributos ``on*`` y referencias
    a recursos externos (``href`` http(s) o ``//``) y ``javascript:``.
    """
    texto = data.decode("utf-8", errors="replace")
    texto = _SCRIPT.sub("", texto)
    texto = _FOREIGN.sub("", texto)
    texto = _ON_ATTR.sub("", texto)
    texto = _HREF_EXTERNO.sub("", texto)
    texto = _HREF_JS.sub("", texto)
    return texto
