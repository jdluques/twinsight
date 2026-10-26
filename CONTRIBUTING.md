# Contribuir a TwinSight

Gracias por tu interés en contribuir. Esta guía cubre cómo levantar el entorno,
correr los tests, la estructura del repositorio y las convenciones del proyecto.

## Entorno de desarrollo

Necesitas [uv](https://docs.astral.sh/uv/) para el backend (Python ≥ 3.12) y
[Node.js](https://nodejs.org/) (≥ 18) para el frontend. Neo4j y un broker MQTT se
levantan localmente con Docker.

### Backend

```bash
cd backend
uv sync --extra dev        # instala dependencias (incl. las de desarrollo)
uv run uvicorn twinsight.app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Servicios de apoyo (Neo4j y Mosquitto)

```bash
docker run --rm -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/twinsight neo4j:5-community

docker run --rm -p 1883:1883 eclipse-mosquitto:2
```

Para probar sin hardware, publica telemetría con el simulador incluido:

```bash
cd simulator
python simulate.py --host localhost --port 1883 --interval 2 --scenario co2
```

Ver [`docs/instalacion.md`](docs/instalacion.md) para el detalle de cada modo de
ejecución (demo, despliegue de referencia y desarrollo).

## Tests

```bash
cd backend && uv run pytest        # backend
cd frontend && npm test            # frontend
```

Los tests de los traductores, el intérprete de guardas, el motor de reglas, el
servicio de impacto y la API son prioritarios (cobertura objetivo ≥ 80 % en esos
módulos).

## Estructura del repositorio

```
code/
├── backend/
│   ├── twinsight/
│   │   ├── api/          # routers FastAPI por recurso
│   │   ├── importers/    # traductores: gaphor, svg, dxf, image (fase 2: ifc, gltf)
│   │   ├── modelgraph/   # única capa que habla Cypher (Neo4j)
│   │   ├── ingest/       # cliente MQTT, validación, normalización
│   │   ├── bus/          # bus de eventos interno (asyncio)
│   │   ├── behavior/     # intérprete de STM + reglas simples
│   │   ├── impact/       # análisis de impacto (consultas Cypher acotadas)
│   │   ├── ws/           # broadcaster WebSocket
│   │   ├── mapping/      # auto-mapeo y formato mappings.yaml
│   │   ├── access/       # autorización por niveles, claves, sesiones
│   │   ├── bundle/       # export/import del bundle .dtproj
│   │   ├── persistence/  # modelos SQLAlchemy, repositorios, migraciones
│   │   ├── app/          # ensamblado de la aplicación FastAPI
│   │   └── config.py     # configuración (variables TWINSIGHT_*)
│   └── tests/
├── frontend/             # SPA React (workspace de paneles)
├── simulator/            # simulador de sensores (CLI Python)
├── examples/             # proyecto de ejemplo (demo de dos salones)
├── docs/                 # documentación de repo (instalación, MQTT, mapeo…)
├── docker-compose.yml        # despliegue de referencia (broker externo)
├── docker-compose.demo.yml   # entorno de demo (broker + simulador incluidos)
└── ARCHITECTURE.md
```

## Convención de commits

El proyecto usa [Conventional Commits](https://www.conventionalcommits.org/):

```
<tipo>(<scope opcional>): <descripción en imperativo>
```

Tipos admitidos:

| Tipo | Uso |
|---|---|
| `feat` | nueva funcionalidad |
| `fix` | corrección de errores |
| `docs` | solo documentación |
| `test` | añadir o ajustar tests |
| `chore` | tareas de mantenimiento (dependencias, configuración) |
| `build` | sistema de build o empaquetado |
| `refactor` | cambio de código que no añade función ni corrige error |

El *scope* es opcional e indica el componente afectado. Ejemplos:

```
feat(behavior): soportar el operador stale en las guardas
fix(ingest): descartar payloads MQTT sin campo metrics
docs(mapeo): documentar las capas estado:<nombre>
test(impact): cubrir el cierre de impacto acotado a 4 saltos
refactor(modelgraph): extraer el upsert idempotente a un helper
```

## Estilo

- **Comentarios y mensajes en español.**
- **Type hints** en todo el código Python; el frontend es TypeScript.
- Los **tests viven junto al código** que prueban.
- Las guardas y reglas nunca ejecutan código arbitrario: guardas con gramática
  restringida (`lark`), reglas declarativas validadas por esquema.
- Las credenciales (MQTT, Neo4j, claves de acceso) van por variables de entorno,
  nunca en el repositorio.
