# Roadmap

La primera versión (0.1.0) es un *vertical slice* funcional del MVP. Lo que sigue está
diferido de forma deliberada: no es parte del alcance actual pero el diseño está
preparado para incorporarlo sin reescribir el núcleo.

## Distribución

- **Instalador nativo "TwinSight Setup"** — instalador gráfico autosuficiente
  (backend + Neo4j empaquetados) para Windows, macOS y Linux, que deja el servidor como
  servicio con autoarranque y añade un **ícono de bandeja** (Abrir TwinSight,
  iniciar/detener, estado, "Exponer en la red" con regla de firewall y URL mDNS). El
  objetivo: instalar y usar sin ver jamás una terminal. Hoy la vía es `docker compose`.

## Representación 3D (fase 2)

- Importación de modelos **IFC** (BIM, vía IfcOpenShell → glTF) y **glTF/GLB/OBJ**
  (OBJ vía trimesh), preservando la jerarquía de nodos como objetos mapeables.
- **Panel visor 3D** con el mismo contrato de interacción que el 2D: hover → resaltado
  cruzado + mini-popup, clic → vista de detalle, severidades y valores en vivo.
- Cubre las rutas de exportación de Blender, SketchUp Free y Sweet Home 3D.

## Importadores adicionales

- **DXF** (AutoCAD 2D) → SVG en el backend vía ezdxf.
- **IFC** → glTF (ver fase 2).
- Nuevos formatos de modelo (p. ej. XMI/Cameo) como traductores adicionales al grafo
  canónico.

## Cliente y offline

- **PWA offline**: visor instalable con ícono propio, ventana sin barra del navegador y
  modo pantalla completa; modo visor offline de bundles `.dtproj`.
- Envoltorio de ventana nativa (tipo Tauri) — solo empaquetado, no cambia la
  arquitectura.

## Acceso y seguridad

- **RBAC con cuentas individuales**: usuarios creados por el Editor en la UI, con
  layouts personales en servidor y auditoría de reconocimientos (quién reconoció cada
  alerta). El middleware de autorización no cambia.
- **Asistente "Exponer en internet"** sobre un túnel (cloudflared/ngrok), señalando la
  dependencia de terceros, además de las rutas ya documentadas (VPN, proxy TLS).

## Datos

- **Redundancia de fuentes de datos**: la misma métrica publicada por dos fuentes con
  *failover* o fusión, como un canal virtual agregador sobre varios canales físicos.
- Otros protocolos de ingesta (OPC-UA, Kafka, HTTP) publicando al bus de eventos
  interno.
- Exportación de histórico (CSV/Parquet) y política de retención avanzada.
