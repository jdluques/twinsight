"""Simulador de sensores para TwinSight.

Publica telemetría sintética por MQTT conforme al contrato (Apéndice A de la
ERS) para operar y demostrar la plataforma sin hardware. Adaptado del
prototipo PI3.

Escenarios:
  - ``nominal``   : valores plausibles que "respiran" dentro de rango.
  - ``co2``       : el CO2 de aula-A sube progresivamente por encima de 1000 y
                    luego 1600 ppm, para disparar la máquina de estados de
                    ventilación (Nominal → Forzada → Alarma) y volver a bajar.
  - ``stale``     : deja de publicar el CO2 de aula-A (prueba del operador
                    ``stale`` de las guardas/reglas).

Uso:
  python simulate.py --host localhost --port 1883 --interval 2 --scenario co2
"""

from __future__ import annotations

import argparse
import json
import math
import time

import paho.mqtt.client as mqtt

AULAS = ["aula-A", "aula-B"]
SENSORS = ["inmp441", "mhz19b", "bme280", "bh1750", "mlx90640"]


def _co2_scenario_value(tick: int) -> float:
    """CO2 de aula-A: sube > 1000 (~tick 15), > 1600 (~tick 30), baja (~tick 45)."""
    if tick < 15:
        return 600 + tick * 25        # 600 → ~975
    if tick < 30:
        return 1000 + (tick - 15) * 50  # 1000 → ~1750
    if tick < 45:
        return 1750 - (tick - 30) * 80  # baja de nuevo
    return 650


def _sample(sensor: str, tick: int, aula_offset: float, scenario: str, aula: str) -> dict[str, float]:
    wave = math.sin(tick / 5.0)
    if sensor == "inmp441":
        return {"spl_db": round(45 + 15 * abs(wave) + aula_offset, 1)}
    if sensor == "mhz19b":
        if scenario in ("co2", "stale") and aula == "aula-A":
            if scenario == "stale" and tick > 10:
                return {}  # deja de publicar: canal stale
            return {"co2_ppm": round(_co2_scenario_value(tick))}
        return {"co2_ppm": round(600 + 250 * (wave + 1) + 20 * aula_offset)}
    if sensor == "bme280":
        return {
            "temp_c": round(22 + 3 * wave + aula_offset, 2),
            "hum_pct": round(50 + 10 * wave, 1),
            "pres_hpa": round(1012 + 2 * wave, 1),
        }
    if sensor == "bh1750":
        return {"lux": round(max(0.0, 300 + 250 * wave + 30 * aula_offset), 1)}
    if sensor == "mlx90640":
        occ = max(0, int(round(3 + 2 * wave + aula_offset)))
        return {
            "min_c": round(21 + wave, 2),
            "max_c": round(31 + 3 * abs(wave) + occ * 0.5, 2),
            "mean_c": round(25 + wave, 2),
            "occupancy_est": occ,
        }
    return {}


def _make_client(client_id: str):
    version = getattr(mqtt, "CallbackAPIVersion", None)
    if version is not None:  # paho-mqtt >= 2.0
        return mqtt.Client(callback_api_version=version.VERSION2, client_id=client_id)
    return mqtt.Client(client_id=client_id)


def run(host: str, port: int, base_topic: str, interval: float, count: int, scenario: str) -> int:
    client = _make_client("twinsight-simulador")
    for attempt in range(30):
        try:
            client.connect(host, port, keepalive=30)
            break
        except OSError:
            print(f"[sim] broker {host}:{port} no disponible, reintento {attempt + 1}...")
            time.sleep(2)
    else:
        print("[sim] no se pudo conectar al broker")
        return 1
    client.loop_start()
    print(f"[sim] escenario={scenario}; {len(AULAS)} aulas x {len(SENSORS)} sensores cada {interval}s")
    tick = 0
    try:
        while count == 0 or tick < count:
            for offset, aula in enumerate(AULAS):
                for sensor in SENSORS:
                    metrics = _sample(sensor, tick, offset, scenario, aula)
                    if not metrics:
                        continue
                    topic = f"{base_topic}/{aula}/{sensor}"
                    client.publish(topic, json.dumps({
                        "aula": aula, "sensor": sensor, "ts": time.time(), "metrics": metrics,
                    }), qos=1)
            tick += 1
            if count == 0 or tick < count:
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[sim] detenido.")
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Simulador de sensores TwinSight")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--base-topic", default="gemelo")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--count", type=int, default=0, help="0 = infinito")
    p.add_argument("--scenario", default="co2", choices=["nominal", "co2", "stale"])
    args = p.parse_args()
    return run(args.host, args.port, args.base_topic, args.interval, args.count, args.scenario)


if __name__ == "__main__":
    raise SystemExit(main())
