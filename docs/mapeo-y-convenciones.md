# Mapeo tripartito y convenciones

El **mapeo tripartito** es el vínculo navegable entre los tres mundos que TwinSight
unifica: un elemento del modelo SysML, su objeto en la representación física y su canal
de datos. Un mapeo es válido con al menos **dos** de los tres extremos.

El mapeo se define en tres niveles complementarios.

## Nivel 1 — Metadata embebida (`dt:*`)

La máxima confianza. Se escribe en las propias fuentes y el importador la convierte en
sugerencias pre-marcadas para confirmación en lote (nunca se aplica en silencio).

En **Gaphor**, en un comentario (Comment) adjunto al elemento o en su nota, una línea
por declaración:

```
dt:channel=gemelo/aula-A/co2:co2_ppm
dt:visual=piso3.svg#sensor-co2-aulaA
```

En **SVG**, el `id` de la forma (convención por nombre) o atributos explícitos:

```xml
<g id="sensor-co2-aulaA"
   data-dt-element="SensorCO2_AulaA"
   data-dt-channel="gemelo/aula-A/co2:co2_ppm"> … </g>
```

## Nivel 2 — Convención de nombres

Patrones configurables por proyecto que describen cómo se escriben los identificadores
en cada mundo. El importador normaliza los identificadores (minúsculas, sin acentos,
separadores unificados) y casa por coincidencia; las sugerencias se presentan con su
evidencia y un *score* para confirmación.

## Nivel 3 — Editor manual

El editor visual muestra las tres colecciones en paralelo (elementos, objetos visuales
—seleccionables también sobre la vista— y canales) y crea el vínculo por selección.
Siempre está disponible, independientemente de las convenciones.

## Formato `mappings.yaml`

Formato abierto, versionable junto a los archivos de origen, exportable e importable
por la plataforma y generable por herramientas externas.

```yaml
version: 1
project: gemelo-aulas-pi3

conventions:                 # patrones de auto-sugerencia (nivel 2)
  - model: "{name}"          # bloque SysML por nombre
    visual: "#{name}"        # id de forma SVG
    channel: "gemelo/{grupo}/{name}:{metric}"

mappings:
  - model: SensorCO2_AulaA               # nombre o gaphor_id del elemento
    visual: piso3.svg#sensor-co2-aulaA   # archivo#id de forma, o imagen#ancla
    channel: gemelo/aula-A/co2:co2_ppm   # topic:métrica
    note: "Sensor MH-Z19B en pared norte"

  - model: Ventilacion_AulaA             # mapeo 2/3 (sin canal)
    visual: piso3.svg#rejilla-vent-A
```

Reglas: cada entrada requiere al menos dos extremos; los extremos se resuelven contra
el grafo al importar y los no resueltos se reportan como pendientes/huérfanos (nunca se
descartan en silencio). Incluir la sección `conventions` hace que versionar el archivo
capture también la configuración del auto-mapeo.

## Variantes gráficas por estado (`estado:<nombre>`)

Un objeto visual puede cambiar de apariencia según el estado activo del elemento
mapeado, usando **capas con convención de nombre** en la propia fuente. En SVG se usa
`inkscape:label` (o, en su defecto, el `id`); en DXF, capas con el mismo prefijo.

```xml
<g id="rejilla-vent-A">
  <g inkscape:label="estado:nominal"> … aspas grises … </g>
  <g inkscape:label="estado:forzada"> … aspas girando azul … </g>
  <g inkscape:label="estado:alarma">  … aspas rojas + icono ⚠ … </g>
</g>
```

La plataforma muestra la capa cuyo nombre coincide con el estado activo (insensible a
mayúsculas y acentos) y oculta las demás; los nodos fuera de toda capa `estado:*` son
la base siempre visible. Los nombres de estado deben coincidir con los estados de la
máquina SysML del elemento.

### Precedencia visual

Cuando varios mecanismos quieren decidir cómo se ve un elemento, gana el más
específico:

```
capa estado:<nombre>  >  regla de estilo configurada  >  paleta de severidades
```

Si no existe una capa para el estado actual, se degrada al siguiente nivel sin error.

---

Todas estas convenciones son **opcionales**: todo lo que habilitan también puede
hacerse manualmente en la plataforma.
