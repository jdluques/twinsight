"""Catálogo de sensores y parseo del contrato MQTT (Apéndice A de la ERS).

Lógica pura, sin dependencias de MQTT ni de Gaphor — la parte más fácil de
testear. Adaptado del prototipo PI3 (``server/gemelo/models.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class MetricSpec:
    """Una magnitud medida por un sensor (una columna de datos)."""

    key: str
    label: str
    unit: str


@dataclass(frozen=True)
class SensorSpec:
    """Ficha técnica de un sensor (fila del Excel de instrumentación PI3)."""

    key: str
    model: str
    category: str
    interface: str
    purpose: str
    event: str
    metrics: tuple[MetricSpec, ...]

    def metric(self, key: str) -> MetricSpec | None:
        for m in self.metrics:
            if m.key == key:
                return m
        return None


SENSORS: dict[str, SensorSpec] = {
    "inmp441": SensorSpec(
        key="inmp441", model="INMP441", category="Ruido", interface="I2S",
        purpose="Detecta actividad acústica del aula (nivel de ruido).",
        event="Ruido elevado / actividad acústica detectada",
        metrics=(MetricSpec("spl_db", "Nivel de presión sonora", "dB"),),
    ),
    "mhz19b": SensorSpec(
        key="mhz19b", model="MH-Z19B", category="Calidad de aire / CO2",
        interface="UART/PWM",
        purpose="Mide la concentración de CO2 (NDIR) para evaluar ventilación.",
        event="CO2 elevado / ventilación insuficiente",
        metrics=(MetricSpec("co2_ppm", "CO2", "ppm"),),
    ),
    "bme280": SensorSpec(
        key="bme280", model="BME280", category="Termodinámica",
        interface="I2C/SPI",
        purpose="Mide temperatura, humedad relativa y presión atmosférica.",
        event="Temperatura, humedad o presión fuera de rango",
        metrics=(
            MetricSpec("temp_c", "Temperatura", "°C"),
            MetricSpec("hum_pct", "Humedad relativa", "%"),
            MetricSpec("pres_hpa", "Presión", "hPa"),
        ),
    ),
    "bh1750": SensorSpec(
        key="bh1750", model="BH1750 (GY-302)", category="Lumínico",
        interface="I2C",
        purpose="Mide iluminancia en lux (iluminación natural/artificial).",
        event="Iluminación insuficiente / excesiva / luz sin ocupación",
        metrics=(MetricSpec("lux", "Iluminancia", "lux"),),
    ),
    "mlx90640": SensorSpec(
        key="mlx90640", model="MLX90640 32x24",
        category="Ocupación / Mapeo térmico", interface="I2C",
        purpose="Matriz térmica de 768 puntos para estimar presencia/ocupación.",
        event="Presencia térmica / cambio de ocupación",
        metrics=(
            MetricSpec("min_c", "Temperatura mínima", "°C"),
            MetricSpec("max_c", "Temperatura máxima", "°C"),
            MetricSpec("mean_c", "Temperatura media", "°C"),
            MetricSpec("occupancy_est", "Ocupación estimada", "personas"),
        ),
    ),
}

# El ESP32 publica un "heartbeat" en un topic aparte; no es un sensor.
ESP32_KEY = "esp32"


@dataclass
class Reading:
    """Una medición proveniente de un sensor de un aula concreta."""

    aula: str
    sensor: str
    ts: datetime
    metrics: dict[str, float]

    @property
    def spec(self) -> SensorSpec | None:
        return SENSORS.get(self.sensor)


class PayloadError(ValueError):
    """El mensaje MQTT no cumple el contrato esperado (Apéndice A)."""


def _coerce_ts(value) -> datetime:
    """Acepta epoch (segundos o ms) o ISO-8601; devuelve datetime UTC."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        if value > 1e11:  # heurística: milisegundos
            value = value / 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PayloadError(f"ts inválido: {value!r}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise PayloadError(f"ts de tipo no soportado: {type(value).__name__}")


def parse_topic(topic: str, base_topic: str = "gemelo") -> tuple[str, str]:
    """Extrae ``(aula, sensor)`` de un topic ``gemelo/<aula>/<sensor>``."""
    parts = topic.strip("/").split("/")
    if len(parts) != 3 or parts[0] != base_topic:
        raise PayloadError(
            f"Topic inesperado {topic!r}; se esperaba '{base_topic}/<aula>/<sensor>'"
        )
    _, aula, sensor = parts
    if not aula or not sensor:
        raise PayloadError(f"Topic con aula/sensor vacío: {topic!r}")
    return aula, sensor


def parse_payload(topic: str, raw: bytes | str, base_topic: str = "gemelo") -> Reading:
    """Convierte un mensaje MQTT en un :class:`Reading` validado (Apéndice A)."""
    aula_t, sensor_t = parse_topic(topic, base_topic)

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"JSON inválido en {topic!r}: {exc}") from exc
    if not isinstance(body, dict):
        raise PayloadError("El cuerpo del mensaje debe ser un objeto JSON.")

    aula = str(body.get("aula", aula_t))
    sensor = str(body.get("sensor", sensor_t))

    raw_metrics = body.get("metrics")
    if not isinstance(raw_metrics, dict) or not raw_metrics:
        raise PayloadError("Falta el objeto 'metrics' con al menos un valor.")

    metrics: dict[str, float] = {}
    for name, value in raw_metrics.items():
        try:
            metrics[str(name)] = float(value)
        except (TypeError, ValueError) as exc:
            raise PayloadError(f"Métrica {name!r} no numérica: {value!r}") from exc

    return Reading(aula=aula, sensor=sensor, ts=_coerce_ts(body.get("ts")), metrics=metrics)
