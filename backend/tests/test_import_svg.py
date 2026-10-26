"""Pruebas de los traductores de representación física 2D (SVG e imagen).

Genera el plano de demostración, lo importa con :class:`SvgTranslator` y
verifica el conteo de objetos visuales, las variantes por estado, la metadata
de mapeo ``dt:*``, el saneado del SVG y el reconocimiento de imágenes raster.
"""

from __future__ import annotations

import struct

import pytest

from twinsight.importers.image import ImageTranslator, anchor_to_node
from twinsight.importers.svg import SvgTranslator, sanitize_svg

# Reusa el generador del plano demo para no duplicar el contenido de prueba.
from scripts.gen_demo_svg import build_svg


@pytest.fixture()
def plano_svg() -> bytes:
    """Bytes del plano de demostración recién generado."""
    return build_svg().encode("utf-8")


@pytest.fixture()
def batch(plano_svg: bytes):
    """GraphBatch resultante de importar el plano con SvgTranslator."""
    return SvgTranslator().translate(plano_svg, source_id="piso.svg")


def _por_id(batch, origin_id: str):
    for nodo in batch.nodes:
        if nodo.origin_id == origin_id:
            return nodo
    return None


# --------------------------------------------------------------------------
# SvgTranslator
# --------------------------------------------------------------------------
def test_can_import_por_extension_y_cabecera():
    tr = SvgTranslator()
    assert tr.can_import("piso.svg", b"")
    assert tr.can_import("", b"<svg xmlns='...'>")
    assert tr.can_import("", b"<?xml version='1.0'?><svg>")
    assert not tr.can_import("foto.png", b"\x89PNG\r\n")


def test_conteo_de_objetos_visuales(batch):
    # 10 sensores (5 x 2 aulas) + rejilla-vent-aulaA + zona-aulaA + zona-aulaB.
    assert batch.report.visual_objects == len(batch.nodes)
    assert batch.report.visual_objects >= 13

    ids = {n.origin_id for n in batch.nodes}
    for tipo in ("co2", "temp", "ruido", "luz", "ocupacion"):
        assert f"sensor-{tipo}-aulaA" in ids
        assert f"sensor-{tipo}-aulaB" in ids
    assert "rejilla-vent-aulaA" in ids
    assert "zona-aulaA" in ids
    assert "zona-aulaB" in ids

    # Todos son VisualObject tipo shape.
    assert all(n.label == "VisualObject" for n in batch.nodes)
    assert all(n.properties["kind"] == "shape" for n in batch.nodes)


def test_decoracion_sin_id_no_es_objeto(batch):
    # El grupo de paredes/puertas (sin id) se reporta como no convertido.
    assert batch.report.unconverted_entities


def test_variantes_por_estado_normalizadas(batch):
    rejilla = _por_id(batch, "rejilla-vent-aulaA")
    assert rejilla is not None
    estados = [v["estado"] for v in rejilla.properties["variants"]]
    assert estados == ["nominal", "forzada", "alarma"]
    # Cada variante expone un selector (id o índice del subgrupo).
    for v in rejilla.properties["variants"]:
        assert v["selector"]


def test_sensor_tiene_dt_channel_y_dt_element(batch):
    co2 = _por_id(batch, "sensor-co2-aulaA")
    assert co2 is not None
    assert co2.properties["dt_channel"] == "gemelo/aula-a/co2:co2_ppm"
    assert co2.properties["dt_element"] == "SensorCO2_AulaA"
    assert co2.properties["representation_id"] == "piso.svg"


def test_normalizacion_de_estado():
    from twinsight.importers.svg.translator import normalizar_estado

    assert normalizar_estado("  Forzada ") == "forzada"
    assert normalizar_estado("ALARMA") == "alarma"
    assert normalizar_estado("en alarma") == "en-alarma"
    assert normalizar_estado("en-alarma") == "en-alarma"
    assert normalizar_estado("presión_alta") == "presion-alta"


# --------------------------------------------------------------------------
# sanitize_svg
# --------------------------------------------------------------------------
def test_sanitize_elimina_script_y_on_handlers():
    sucio = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<script>alert("xss")</script>'
        b'<rect onload="hack()" onclick=\'steal()\' x="0" y="0"/>'
        b'<a href="http://malo.example/x">link</a>'
        b'<foreignObject><div onclick="x">h</div></foreignObject>'
        b"</svg>"
    )
    limpio = sanitize_svg(sucio)
    assert "<script" not in limpio.lower()
    assert "onload" not in limpio.lower()
    assert "onclick" not in limpio.lower()
    assert "foreignobject" not in limpio.lower()
    assert "http://malo.example" not in limpio
    # El contenido legítimo sobrevive.
    assert "<rect" in limpio
    assert "<svg" in limpio.lower()


def test_sanitize_conserva_el_plano(plano_svg):
    limpio = sanitize_svg(plano_svg)
    assert "sensor-co2-aulaA" in limpio
    assert "<script" not in limpio.lower()


# --------------------------------------------------------------------------
# ImageTranslator
# --------------------------------------------------------------------------
def _png_minimo(ancho: int = 4, alto: int = 3) -> bytes:
    """Cabecera PNG mínima con un IHDR válido (no es una imagen completa)."""
    ihdr = struct.pack(">II", ancho, alto) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + ihdr


def test_image_reconoce_png_y_jpg_por_cabecera():
    tr = ImageTranslator()
    assert tr.can_import("plano.png", _png_minimo())
    assert tr.can_import("foto.jpg", b"\xff\xd8\xff\xe0")
    assert not tr.can_import("plano.svg", b"<svg>")


def test_image_translate_no_crea_objetos():
    tr = ImageTranslator()
    batch = tr.translate(_png_minimo(640, 480), source_id="plano.png")
    assert batch.kind == "image"
    assert batch.report.visual_objects == 0
    assert batch.nodes == []
    assert any("640x480" in w for w in batch.report.warnings)


def test_anchor_to_node():
    nodo = anchor_to_node("plano.png", "bomba-1", 120.5, 240.0)
    assert nodo.label == "VisualObject"
    assert nodo.origin_id == "plano.png#bomba-1"
    assert nodo.properties["kind"] == "anchor"
    assert nodo.properties["x"] == 120.5
    assert nodo.properties["y"] == 240.0
    assert nodo.properties["representation_id"] == "plano.png"
