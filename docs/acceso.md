# Acceso y niveles

El modelo de acceso de esta versión es **sin cuentas individuales**: tres niveles por
**clave de instancia**. La capacidad de modificar depende del **nivel de acceso**,
nunca de la ubicación del cliente — un editor remoto modifica; un viewer en la misma
máquina solo mira.

## Niveles

| Nivel | Puede |
|---|---|
| **Editor** | Toda operación: importar, mapear, reglas, configuración, acceso. |
| **Operador** | Visualización completa + reconocer alertas + gestionar sus propios layouts. |
| **Viewer** | Solo lectura. |

Resumen de autorización en la API:

| Grupo de endpoints | Viewer | Operador | Editor |
|---|---|---|---|
| Lecturas (elementos, diagramas, representaciones, mapeos, impacto, máquinas de estado, eventos, telemetría) + WebSocket | sí | sí | sí |
| Reconocer alertas · layouts propios | — | sí | sí |
| Mutaciones de definición (importar/exportar, fuentes, canales, mapeos, reglas, presentación, enlaces viewer, proyectos) | — | — | sí |

La UI refleja el nivel activo: los controles de edición se ocultan o deshabilitan para
Operador y Viewer.

## Claves de instancia

- Las claves de **editor** y **operador** se generan automáticamente (en el instalador
  o, en despliegues compose, por variable de entorno).
- Se almacenan **hasheadas** (argon2id, con bcrypt como fallback) en el almacén
  relacional; **nunca se exponen por la API**.
- La pantalla de acceso pide la clave, la verifica contra el hash y establece una
  **sesión por cookie firmada** (`HttpOnly`, `SameSite=Lax`) que lleva el nivel y la
  versión de clave. No hay estado de sesión en el servidor: la validez se deriva de la
  firma y de la versión.

## Enlaces viewer

El Editor puede generar un **enlace de visualización compartible** (acceso Viewer) para
un proyecto. El token se almacena como hash y es **revocable** por el Editor; el enlace
se intercambia por una cookie de nivel Viewer.

## Rotación de claves

El Editor puede **rotar** una clave de instancia desde la configuración. La rotación
genera una clave nueva (mostrada una sola vez) e incrementa la versión de clave; todas
las cookies emitidas con la versión anterior dejan de validar de inmediato — es decir,
**la rotación invalida todas las sesiones activas de ese nivel** sin mantener una lista
de sesiones.

## Roadmap

Las **cuentas de usuario individuales** (creadas por el Editor en la UI, con layouts
propios en servidor y auditoría de quién reconoció cada alerta) están en el
[roadmap](roadmap.md). El middleware de autorización no cambia: una cuenta se resuelve a
un nivel igual que una clave de instancia.
