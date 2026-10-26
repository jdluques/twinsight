# Protocolo MQTT de ingesta

TwinSight se conecta a un broker MQTT externo **como suscriptor** y normaliza cada
mensaje a lecturas de telemetría. Este documento describe el contrato de ingesta por
defecto.

## Topic

```
<topic_base>/<grupo>/<dispositivo>
```

El `topic_base` se configura por fuente de datos (por defecto `gemelo`). Ejemplo:

```
gemelo/aula-A/bme280
```

## Payload (JSON)

```json
{
  "aula": "aula-A",
  "sensor": "bme280",
  "ts": "2026-10-19T14:30:00Z",
  "metrics": {
    "temp_c": 24.6,
    "hum_pct": 51.2,
    "pres_hpa": 1013.2
  }
}
```

## Canales

Cada par **(topic, clave de métrica)** identifica un **canal** — la serie de datos
individual que se mapea y se grafica. El mensaje de ejemplo anterior produce tres
canales:

- `gemelo/aula-A/bme280 : temp_c`
- `gemelo/aula-A/bme280 : hum_pct`
- `gemelo/aula-A/bme280 : pres_hpa`

Los canales pueden definirse manualmente (topic + métrica + unidad + etiqueta) o
descubrirse automáticamente a partir de los topics y métricas observados.

## Reglas de validación

- `metrics` es **obligatorio y no vacío**.
- Los valores de `metrics` deben ser **numéricos**.
- `ts` debe estar en **ISO-8601**; si falta, se usa la hora de recepción.
- Los demás campos de nivel superior (`aula`, `sensor`, …) son informativos.

Los mensajes que no validan se **descartan** registrando el motivo, sin interrumpir la
ingesta del resto.

## Notas de conexión

- Una fuente de datos declara `host`, `puerto`, `topic_base` y credenciales
  opcionales. La contraseña se referencia por variable de entorno, nunca en claro.
- El estado de conexión de cada fuente (conectada/desconectada) se reporta en tiempo
  real; la reconexión usa retroceso exponencial.
- Un proyecto puede tener **varias fuentes** y un elemento puede mapearse a canales de
  fuentes distintas. La **redundancia** (la misma métrica desde dos fuentes con
  failover o fusión) está en el [roadmap](roadmap.md).
