# Bundle portable `.dtproj`

Un `.dtproj` es un archivo ZIP con la **definición completa** de un proyecto de gemelo
digital, apto para compartirse y versionarse. Separa deliberadamente dos cosas:

- La **definición** de un gemelo es portable (viaja en el bundle).
- La **ejecución** (telemetría histórica, eventos, estados en vivo) pertenece a la
  instancia que lo corre y **nunca** viaja en el bundle.

El bundle describe *qué es* el gemelo, no *qué le ha pasado*. Al importarlo en otra
instancia, el gemelo "revive" al conectar la fuente de datos destino.

## Estructura

```
mi-gemelo.dtproj (zip)
├── manifest.yaml          # versión del formato, nombre, descripción, fecha de export,
│                          # inventario de fuentes con hashes, "sin datos de ejecución"
├── sources/
│   ├── modelo.gaphor      # fuentes de modelo (tal cual se importaron)
│   ├── piso3.svg          # representaciones 2D (SVG original o convertido de DXF)
│   └── aulas-foto.png     # imágenes con anclas
├── mappings.yaml          # mapeos (ver mapeo-y-convenciones.md)
├── rules.yaml             # reglas operativas simples
├── presentation.yaml      # presentación de alarmas (severidad → color/sonido/notificación)
├── layouts/
│   └── operacion.json     # layouts del workspace guardados
└── datasources.yaml       # fuentes MQTT (host/puerto/topic base; SIN credenciales)
```

## Qué NO contiene

- Telemetría histórica, eventos ni estados en vivo.
- Credenciales de ningún tipo (las fuentes MQTT se importan *desconectadas* y la UI
  pide las credenciales al importar).

El manifiesto declara explícitamente "sin datos de ejecución" y la UI lo comunica al
exportar.

## Exportar

El bundle se arma desde las fuentes de verdad: los archivos originales de cada
importación vigente, los mapeos generados desde el grafo, y las reglas, presentación,
layouts y fuentes del almacén relacional (excluyendo credenciales y datos de
ejecución). El `manifest.yaml` registra la versión del formato y los hashes SHA-256 de
cada fuente para verificar integridad.

## Importar

Al importar, la instancia destino:

1. Valida el manifiesto (versión del formato y hashes).
2. Pasa cada archivo de `sources/` por su traductor: el grafo se **reconstruye**, no se
   copia.
3. Restaura mapeos, reglas, presentación y layouts.
4. Crea las fuentes MQTT desconectadas y solicita las credenciales.

Todo extremo que no pueda resolverse queda marcado como **pendiente** y se lista en el
reporte de importación; nada se descarta en silencio.
