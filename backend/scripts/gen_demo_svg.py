#!/usr/bin/env python
"""Genera el plano 2D de demostración ``examples/aulas-pi3/piso.svg``.

Dibuja DOS aulas (Aula A y Aula B) lado a lado con paredes, puertas y, por
aula, cinco marcadores de sensores (co2, temp, ruido, luz, ocupacion)
etiquetados con la metadata de mapeo del Apéndice F de la ERS
(``data-dt-element`` / ``data-dt-channel``, e ``id`` de la forma como
identidad).

Además incluye:

* ``rejilla-vent-aulaA``: objeto con TRES variantes por estado mediante capas
  hijas ``inkscape:label="estado:nominal|forzada|alarma"`` (RF-028).
* ``zona-aulaA`` y ``zona-aulaB``: rectángulos de zona para resaltar el aula.

El SVG es autocontenido, válido y legible. Los ``id`` de los sensores están
coordinados con los que el importador ``.gaphor`` escribe en ``dt:visual``
(p.ej. ``sensor-co2-aulaA``) para que el mapeo automático case sin fricción.

Uso::

    uv run python scripts/gen_demo_svg.py
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Catálogo de sensores de la demo. El orden fija la posición en el aula.
# metric = magnitud del contrato MQTT (Apéndice A); element = nombre del
# bloque SysML esperado; code = texto corto del marcador.
# ---------------------------------------------------------------------------
SENSORES = [
    {"tipo": "co2", "metric": "co2_ppm", "element": "SensorCO2", "code": "CO2"},
    {"tipo": "temp", "metric": "temp_c", "element": "SensorTemp", "code": "T"},
    {"tipo": "ruido", "metric": "spl_db", "element": "SensorRuido", "code": "dB"},
    {"tipo": "luz", "metric": "lux", "element": "SensorLuz", "code": "LUX"},
    {"tipo": "ocupacion", "metric": "count", "element": "SensorOcupacion", "code": "OCU"},
]

# Geometría de cada aula: (clave, letra, x0 de la pared interior).
AULAS = [
    {"clave": "aulaA", "letra": "A", "x0": 60},
    {"clave": "aulaB", "letra": "B", "x0": 440},
]

ANCHO_AULA = 300
Y0 = 90
ALTO_AULA = 320


def _marcador_sensor(aula: dict, idx: int, sensor: dict) -> str:
    """Construye el ``<g>`` de un marcador de sensor con su metadata dt:*."""
    letra = aula["letra"]
    clave = aula["clave"]
    gid = f"sensor-{sensor['tipo']}-{clave}"
    element = f"{sensor['element']}_Aula{letra}"
    channel = f"gemelo/aula-{letra.lower()}/{sensor['tipo']}:{sensor['metric']}"

    # Distribución en rejilla 3x2 dentro del aula.
    col = idx % 3
    fila = idx // 3
    cx = aula["x0"] + 55 + col * 95
    cy = Y0 + 70 + fila * 150

    return (
        f'    <g id="{gid}"\n'
        f'       data-dt-element="{element}"\n'
        f'       data-dt-channel="{channel}">\n'
        f'      <circle cx="{cx}" cy="{cy}" r="20" fill="#eef4ff" '
        f'stroke="#2b6cb0" stroke-width="2"/>\n'
        f'      <text x="{cx}" y="{cy + 4}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="12" font-weight="bold" '
        f'fill="#2b6cb0">{sensor["code"]}</text>\n'
        f'      <text x="{cx}" y="{cy + 34}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="9" fill="#444">'
        f'{sensor["tipo"]}</text>\n'
        f"    </g>\n"
    )


def _rejilla_vent(aula: dict) -> str:
    """Objeto con variantes por estado (capas ``estado:*``) — RF-028.

    Tres capas hijas con ``inkscape:label`` que la plataforma alterna según el
    estado activo del elemento mapeado: nominal (gris), forzada (azul),
    alarma (rojo + icono de advertencia).
    """
    letra = aula["letra"]
    clave = aula["clave"]
    # La rejilla se dibuja en la esquina inferior derecha del aula.
    bx = aula["x0"] + ANCHO_AULA - 70
    by = Y0 + ALTO_AULA - 60
    w = 44

    def aspas(color: str) -> str:
        cx = bx + w / 2
        cy = by + w / 2
        return (
            f'        <rect x="{bx}" y="{by}" width="{w}" height="{w}" '
            f'rx="4" fill="none" stroke="{color}" stroke-width="2"/>\n'
            f'        <line x1="{bx}" y1="{by}" x2="{bx + w}" y2="{by + w}" '
            f'stroke="{color}" stroke-width="2"/>\n'
            f'        <line x1="{bx + w}" y1="{by}" x2="{bx}" y2="{by + w}" '
            f'stroke="{color}" stroke-width="2"/>\n'
            f'        <line x1="{cx}" y1="{by}" x2="{cx}" y2="{by + w}" '
            f'stroke="{color}" stroke-width="2"/>\n'
            f'        <line x1="{bx}" y1="{cy}" x2="{bx + w}" y2="{cy}" '
            f'stroke="{color}" stroke-width="2"/>\n'
        )

    return (
        f'    <g id="rejilla-vent-{clave}"\n'
        f'       data-dt-element="RejillaVent_Aula{letra}">\n'
        f'      <g inkscape:label="estado:nominal">\n'
        f"{aspas('#9aa0a6')}"
        f"      </g>\n"
        f'      <g inkscape:label="estado:forzada">\n'
        f"{aspas('#1a73e8')}"
        f"      </g>\n"
        f'      <g inkscape:label="estado:alarma">\n'
        f"{aspas('#d93025')}"
        f'        <text x="{bx + w / 2}" y="{by - 6}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="16" fill="#d93025">&#9888;</text>\n'
        f"      </g>\n"
        f"    </g>\n"
    )


def _aula(aula: dict) -> str:
    """Zona resaltable + paredes + puerta + sensores + rejilla de un aula."""
    clave = aula["clave"]
    letra = aula["letra"]
    x0 = aula["x0"]

    partes: list[str] = []

    # Zona resaltable del aula (VisualObject tipo shape).
    partes.append(
        f'    <g id="zona-{clave}">\n'
        f'      <rect x="{x0}" y="{Y0}" width="{ANCHO_AULA}" '
        f'height="{ALTO_AULA}" rx="6" fill="#f7fafc" stroke="#cbd5e0" '
        f'stroke-width="1" stroke-dasharray="6 4"/>\n'
        f'      <text x="{x0 + ANCHO_AULA / 2}" y="{Y0 + 22}" '
        f'text-anchor="middle" font-family="sans-serif" font-size="16" '
        f'font-weight="bold" fill="#2d3748">Aula {letra}</text>\n'
        f"    </g>\n"
    )

    # Sensores.
    for idx, sensor in enumerate(SENSORES):
        partes.append(_marcador_sensor(aula, idx, sensor))

    # Rejilla con variantes por estado (solo Aula A, según el enunciado).
    if clave == "aulaA":
        partes.append(_rejilla_vent(aula))

    return "".join(partes)


def build_svg() -> str:
    """Devuelve el documento SVG completo del plano de demostración."""
    cuerpo: list[str] = []

    # Decoración arquitectónica SIN id: el importador debe ignorarla
    # (la reporta como entidad no convertida).
    cuerpo.append(
        "    <g>\n"
        '      <rect x="40" y="70" width="720" height="360" fill="none" '
        'stroke="#2d3748" stroke-width="4"/>\n'
        '      <line x1="400" y1="70" x2="400" y2="430" '
        'stroke="#2d3748" stroke-width="4"/>\n'
        # Puertas (huecos marcados con trazo claro).
        '      <line x1="60" y1="430" x2="110" y2="430" '
        'stroke="#ffffff" stroke-width="6"/>\n'
        '      <line x1="440" y1="430" x2="490" y2="430" '
        'stroke="#ffffff" stroke-width="6"/>\n'
        "    </g>\n"
    )

    for aula in AULAS:
        cuerpo.append(_aula(aula))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg"\n'
        '     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"\n'
        '     viewBox="0 0 800 500" width="800" height="500">\n'
        "  <title>Plano de demostración — Aulas PI3</title>\n"
        '  <rect x="0" y="0" width="800" height="500" fill="#ffffff"/>\n'
        '  <text x="400" y="40" text-anchor="middle" font-family="sans-serif" '
        'font-size="20" font-weight="bold" fill="#1a202c">'
        "TwinSight — Piso de aulas (demo PI3)</text>\n"
        + "".join(cuerpo)
        + "</svg>\n"
    )


def main() -> Path:
    """Escribe el plano en ``examples/aulas-pi3/piso.svg`` y devuelve la ruta."""
    # backend/scripts/gen_demo_svg.py -> code/examples/aulas-pi3/piso.svg
    code_dir = Path(__file__).resolve().parents[2]
    destino = code_dir / "examples" / "aulas-pi3" / "piso.svg"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(build_svg(), encoding="utf-8")
    print(f"Plano escrito en: {destino}")
    return destino


if __name__ == "__main__":
    main()
