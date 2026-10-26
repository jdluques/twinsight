# Changelog

Todas las novedades relevantes de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el
proyecto se adhiere al [Versionado Semántico](https://semver.org/lang/es/).

## [No publicado]

## [0.1.0] - 2026-10-21

Primera versión: un *vertical slice* funcional del MVP.

### Added

- **Importación SysML (`.gaphor` v4)**: bloques, propiedades, requisitos, paquetes,
  relaciones y máquinas de estado con el texto de sus guardas; diagramas con su
  geometría de presentación. Importación idempotente por `gaphor_id`.
- **Representación 2D**: importación de SVG (formas seleccionables y mapeables) e
  imagen con anclas, servida de forma sanitizada al workspace.
- **Grafo canónico del modelo (Neo4j)** con elementos, relaciones, estados, modos de
  falla, objetos visuales, canales y mapeos; **análisis de impacto** multi-nivel con
  rutas de dependencia.
- **Comportamiento model-driven**: interpretación en vivo de las máquinas de estado
  del modelo, con parser de guardas (gramática restringida) y reglas operativas
  simples con debounce e histéresis.
- **Mapeo tripartito** en tres niveles: metadata embebida `dt:*`, patrones de
  convención de nombres y editor manual; export/import de `mappings.yaml`.
- **Ingesta MQTT** (broker externo) con validación de payloads y reconexión; **estado
  discreto en vivo** reflejado en el grafo (la telemetría cruda nunca entra al grafo).
- **Workspace de paneles componibles** con resaltado cruzado entre paneles, panel
  Grafana embebido y layouts compartidos/personales con configuración por panel.
- **Bundle portable `.dtproj`**: exportación e importación de la definición completa
  de un proyecto (sin datos de ejecución ni credenciales).
- **Acceso por niveles** editor/operador/viewer mediante claves de instancia
  hasheadas, sesión por cookie firmada y enlaces viewer revocables.
- **Demo de dos salones** (`docker-compose.demo.yml`): broker Mosquitto de juguete,
  Neo4j, backend y simulador que publica telemetría de dos aulas.

[No publicado]: https://github.com/jdluques/twinsight/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jdluques/twinsight/releases/tag/v0.1.0
