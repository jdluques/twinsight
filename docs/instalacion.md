# Instalación y ejecución

TwinSight se ejecuta como un **servicio**: el servidor corre el gemelo de forma
continua y los navegadores son los clientes. Hay tres formas de levantarlo según el
objetivo.

> El **broker MQTT es infraestructura externa**: el despliegue de referencia **no** lo
> incluye (la plataforma solo se suscribe a él). El único entorno que trae un broker
> es el de demo, explícitamente etiquetado como tal.

## 1. Demo de dos salones

La forma más rápida de ver la plataforma completa funcionando, sin hardware ni broker
propios. Requiere **Docker** y **Docker Compose**.

```bash
docker compose -f docker-compose.demo.yml up --build
```

Levanta un broker Mosquitto de juguete, Neo4j, el backend y un simulador que publica
telemetría de dos aulas. El proyecto *Aulas PI3* se siembra al arrancar.

- Abre **http://localhost:8000**.
- Clave de editor: `editor-demo` (configurable con `TWINSIGHT_EDITOR_KEY`).

Observa cómo, al subir el CO₂ simulado, la máquina de estados *Ventilación* pasa a
*Forzada* en vivo y el impacto se resalta en todos los paneles a la vez.

## 2. Despliegue de referencia

Para un servidor real conectado a tu propio broker MQTT. Requiere **Docker** y
**Docker Compose**.

```bash
docker compose up --build
```

Levanta Neo4j y el backend (con el frontend ya compilado dentro). Configura el broker
externo por variables de entorno (p. ej. en un archivo `.env`):

```bash
TWINSIGHT_MQTT_HOST=192.168.1.10
TWINSIGHT_MQTT_PORT=1883
TWINSIGHT_NEO4J_PASSWORD=<una-contraseña-fuerte>
TWINSIGHT_EDITOR_KEY=<clave-de-editor>
```

El servidor queda escuchando en el puerto **8000**. Las credenciales (Neo4j, MQTT,
claves de acceso) se pasan siempre por entorno, nunca en el repositorio.

## 3. Modo desarrollo

Para trabajar sobre el código, con recarga en caliente. Necesitas
[uv](https://docs.astral.sh/uv/) (Python ≥ 3.12) y [Node.js](https://nodejs.org/).

Levanta Neo4j y Mosquitto locales con Docker:

```bash
docker run --rm -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/twinsight neo4j:5-community

docker run --rm -p 1883:1883 eclipse-mosquitto:2
```

Backend:

```bash
cd backend
uv sync --extra dev
uv run uvicorn twinsight.app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Simulador de sensores (opcional, para generar telemetría):

```bash
cd simulator
python simulate.py --host localhost --port 1883 --interval 2 --scenario co2
```

Escenarios disponibles: `nominal`, `co2` (sube el CO₂ en un aula) y `stale` (deja de
publicar un canal para probar la detección de datos ausentes).

## Variables de entorno

| Variable | Descripción | Por defecto |
|---|---|---|
| `TWINSIGHT_NEO4J_URI` | URI Bolt de Neo4j | `bolt://localhost:7687` |
| `TWINSIGHT_NEO4J_USER` | usuario de Neo4j | `neo4j` |
| `TWINSIGHT_NEO4J_PASSWORD` | contraseña de Neo4j | — |
| `TWINSIGHT_DATABASE_URL` | URL del almacén relacional (SQLite/PostgreSQL) | SQLite local |
| `TWINSIGHT_MQTT_HOST` | host del broker MQTT externo | `localhost` |
| `TWINSIGHT_MQTT_PORT` | puerto del broker | `1883` |
| `TWINSIGHT_EDITOR_KEY` | clave de nivel editor | generada |
| `TWINSIGHT_SEED_DEMO` | siembra el proyecto demo al arrancar | `0` |

## Roadmap: instalador nativo

El método de instalación pensado para usuarios sin conocimientos técnicos es
**"TwinSight Setup"**: un instalador gráfico autosuficiente (backend + Neo4j
empaquetados) que deja el servidor como servicio con autoarranque y añade un ícono de
bandeja con "Abrir TwinSight", iniciar/detener y "Exponer en la red". Está en el
[roadmap](roadmap.md); hoy la vía recomendada es `docker compose`.
