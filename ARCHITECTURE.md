# Arquitectura de TwinSight

Este documento condensa la arquitectura de la plataforma. Para la especificación
completa (requisitos y diseño detallado) existe la documentación académica del
proyecto en `paper/docs/`; aquí se recoge lo necesario para entender y extender el
código.

## 1. El problema

En un gemelo digital la información vive hoy fragmentada en tres mundos que no se
hablan entre sí:

1. El **modelo de ingeniería** del sistema (diagramas SysML: estructura,
   comportamiento, máquinas de estado, requisitos), producido en herramientas de
   modelado como **Gaphor**.
2. La **representación física** (planos, figuras, modelos 3D), producida en
   herramientas CAD/vectoriales (AutoCAD, Inkscape, Revit…).
3. La **telemetría en tiempo real** de los sensores IoT, que suele quedar en
   dashboards desconectados del modelo.

El operador termina saltando entre herramientas sin un vínculo explícito entre "el
bloque SysML del ventilador", "el dibujo del ventilador en el plano" y "la serie de
CO₂ que dispara su encendido".

## 2. El enfoque: un hub de integración por traductores

TwinSight **no edita** modelos ni representaciones: los importa, los relaciona y los
presenta unificados. Cada fuente entra por un **traductor** que la convierte al
**grafo canónico del modelo**. Soportar un formato nuevo (p. ej. XMI) es escribir un
traductor más; el resto de la plataforma no cambia.

Sobre ese grafo canónico se apoyan los tres pilares funcionales:

- **Mapeo tripartito** — el vínculo navegable entre un elemento SysML, su objeto en
  la representación física y su canal de datos.
- **Comportamiento model-driven** — las máquinas de estado del modelo, con guardas
  sobre los datos (`co2_ppm > 1000`), se interpretan en vivo. El modelo es la fuente
  de verdad del comportamiento.
- **Análisis de impacto** — consultas multi-nivel sobre el grafo: dado un elemento
  (p. ej. un sensor caído), qué guardas, transiciones, estados, componentes y vistas
  quedan afectados.

```
Gaphor (.gaphor) ──traductor──►┐
Inkscape/AutoCAD (SVG/DXF) ────►│  Grafo del modelo (Neo4j)
Imagen + anclas ───────────────►│  + Telemetría (SQLite) + Bus en vivo
Revit/glTF/OBJ [fase 2] ───────►│
                                │
Broker MQTT externo ──suscripción──► Servidor TwinSight ──HTTP/WS──► Navegadores
(sensores/simulador publican)         (ejecuta el gemelo 24/7)        (workspace)
```

## 3. Dual-store: cada dato en su sitio

La división de almacenamiento es deliberada:

| Almacén | Qué guarda | Por qué |
|---|---|---|
| **Neo4j (grafo)** | Estructura, relaciones SysML, máquinas de estado, modos de falla, objetos visuales, mapeos y **estado discreto en vivo** (estado activo, severidad, canal *stale*). | Las consultas de impacto son caminos multi-nivel; en Cypher son un `MATCH` legible. |
| **SQLite (relacional)** | Telemetría (serie temporal), eventos, configuración, reglas, layouts, registro de importaciones. Portable a PostgreSQL por configuración. | Series y eventos no son un grafo; además el esquema de telemetría es interfaz pública estable (Grafana en lectura). |
| **Bus en memoria + WebSocket** | Lo que está pasando *ahora mismo*: lecturas, cambios de estado, eventos de reglas, estado de conexión. | Tiempo real sin colas externas (asyncio puro). |

**Regla invariable: la telemetría cruda nunca se escribe en el grafo.** El grafo solo
guarda el *estado discreto* resultante (en alarma / nominal / sin datos), no los
valores.

## 4. Componentes del backend

Monolito modular (un proceso FastAPI); los componentes se comunican por interfaces y
por el bus interno. El volumen objetivo (decenas de canales, cientos de lecturas/s)
no justifica microservicios.

| Componente | Responsabilidad |
|---|---|
| `api/` | Routers FastAPI por recurso. Solo orquesta: valida entrada, llama servicios, serializa salida. |
| `importers/` | Traductores de importación: `gaphor`, `svg`, `dxf` (vía ezdxf → SVG), `image` (anclas); fase 2 `ifc`, `gltf`. Implementan la interfaz `Translator` y producen un lote intermedio que persiste `modelgraph`. |
| `modelgraph/` | **Única capa que habla Cypher.** Encapsula el driver oficial `neo4j` (Bolt): upsert idempotente por id de origen, consultas de vecindario, resolución de mapeos y actualización del estado discreto. |
| `ingest/` | Cliente MQTT (aiomqtt): suscripción por fuente, validación del payload, normalización a lecturas (una por métrica), persistencia relacional y publicación en el bus. Reconexión con retroceso exponencial. |
| `bus/` | Bus de eventos interno (pub/sub sobre asyncio): tipos `reading`, `state_change`, `rule_event`, `source_status`. Desacopla productores de consumidores. |
| `behavior/` | Intérprete del comportamiento: máquinas de estado del modelo (parser de guardas con `lark`, estado activo por máquina) y reglas operativas simples (debounce/histéresis). Emite `state_change` / `rule_event`. |
| `impact/` | Análisis de impacto: consultas Cypher multi-nivel acotadas, con caché por elemento; calcula los conjuntos de resaltado que acompañan a cada evento. |
| `ws/` | Broadcaster WebSocket: conexiones por proyecto, suscripción por canal, fan-out de los eventos del bus con el impacto ya resuelto. |
| `mapping/` | Auto-mapeo (metadata `dt:*` y patrones de convención) y el formato `mappings.yaml` (export/import). |
| `access/` | Autorización por niveles: claves de instancia hasheadas, sesión por cookie firmada, enlaces viewer, rotación de claves. |
| `bundle/` | Exportación/importación del bundle portable `.dtproj`. |
| `persistence/` | Modelos SQLAlchemy 2, repositorios y migraciones sobre el almacén relacional. |

## 5. El frontend: un workspace de paneles

**El workspace es la aplicación.** Tras el launcher de proyectos, el usuario vive en
una pantalla a pantalla completa con una grilla de paneles componibles (docking real
con dockview): diagrama SysML, máquina de estados, representación física 2D, gráfica
de telemetría, bandeja de eventos, explorador de grafo y dashboard Grafana embebido.
La configuración (fuentes, mapeos, comportamiento, acceso) se abre como *overlays*
sobre el workspace, no como páginas separadas.

Su rasgo distintivo es el **resaltado cruzado**: al pasar el cursor sobre un elemento
en cualquier panel, sus contrapartes se resaltan de inmediato (≤ 100 ms) en todos los
demás paneles. Para lograrlo, el mapa de resolución de mapeos se **precarga** en el
cliente y el hover se resuelve sin tocar la red; los eventos del servidor llegan con
el impacto ya resuelto.

## 6. Modelo cliente-servidor: el gemelo es un servicio

**El gemelo digital es un servicio 24/7, no un programa que se abre y se cierra.** La
ingesta, la evaluación de guardas y la actualización del grafo deben ejecutarse de
forma continua, aunque nadie esté mirando: existe *un* sistema físico real y su estado
(qué máquina está en alarma, qué eventos ocurrieron de madrugada, qué alertas se
reconocieron) debe computarse **una sola vez** contra el stream real. Si cada cliente
ingiriera por su cuenta, habría históricos con huecos, alertas duplicadas y varias
"verdades" del mismo sistema.

Por eso la arquitectura es **un servidor por sistema físico + N clientes**, el patrón
del referente del proyecto: el **G2 Server** ejecuta la base de conocimiento junto al
sistema y los clientes **Telewindows** son ventanas remotas hacia él. En TwinSight, el
navegador es ese cliente. "Web vs app" es solo una decisión de empaquetado del cliente,
no de arquitectura: la exposición (localhost / red local / VPN / proxy TLS) es una
propiedad del servidor e idéntica para cualquier cliente.

## 7. Flujo de un dato en tiempo real

```
  sensor/simulador
        │ publica JSON
        ▼
  Broker MQTT (externo)
        │ suscripción
        ▼
   ingest/ ──valida──► persiste lectura (SQLite) ──► publica "reading" en bus/
                                                           │
                        ┌──────────────────────────────────┼───────────────────┐
                        ▼                                    ▼                   ▼
                      ws/ (telemetría             behavior/ (guardas STM      (otros
                      a suscritos)                 + reglas simples)          consumidores)
                                                        │ dispara
                                                        ▼
                                          persiste Event + actualiza estado
                                          discreto en modelgraph/ (Neo4j)
                                                        │
                                                        ▼
                                             impact/ resuelve afectados
                                                        │
                                                        ▼
                                   ws/ difunde state_change / rule_event
                                   + impact_highlight ──► cada panel resalta lo suyo
```

## 8. Decisiones tecnológicas

| Elección | Alternativas | Por qué |
|---|---|---|
| **FastAPI + Uvicorn** | Flask, Django | Async nativo (MQTT + WS + API en un event loop), Pydantic integrado, OpenAPI automático. |
| **Neo4j Community** | Grafo-sobre-SQL, ArangoDB, Memgraph | El modelo + mapeos + modos de falla forman un grafo; el impacto es un camino multi-nivel trivial en Cypher. Usado como servicio externo vía Bolt (sin enlazar código → compatible con MIT). |
| **SQLAlchemy 2 + Alembic** | sqlite3 crudo | Portabilidad SQLite↔PostgreSQL por configuración y migraciones versionadas. |
| **aiomqtt** | paho-mqtt (hilos) | Integración limpia con el event loop asyncio. |
| **lark** | parser manual, pyparsing | La gramática de guardas se transcribe literal; AST de datos inspeccionable, sin `eval` (seguridad). |
| **ezdxf** (DXF→SVG) | parser propio, ODA | Librería Python madura y MIT; reporta entidades no convertibles. |
| **React + TypeScript + Vite** | — | SPA compilada a estáticos servidos por el backend (un solo origen, sin CORS). |
| **dockview** | react-grid-layout, Golden Layout | Docking real (pestañas, split, drag, múltiples instancias) con serialización JSON de layouts. |
| **cytoscape.js** | react-force-graph, sigma.js | Estilos declarativos por tipo de nodo, layouts deterministas, grafos medianos sin WebGL obligatorio. |
| **uPlot** | Recharts, Chart.js | Streaming de decenas de miles de puntos a 60 fps en ~45 kB. |
| **SVG propio (React)** para diagramas | react-flow, GoJS | El `.gaphor` ya trae la geometría: se necesita dibujo fiel de solo lectura, no un editor. |

**Grafana** se descartó como frontend (sus paneles son islas que no soportan el
resaltado cruzado ≤ 100 ms) pero se **integra como panel embebido** sobre el esquema
público de telemetría.
