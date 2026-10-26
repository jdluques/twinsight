# Guardas de máquinas de estado

El comportamiento del gemelo se define **en el modelo**: las transiciones de las
máquinas de estado de Gaphor llevan una **guarda**, una condición booleana sobre los
datos en vivo que TwinSight interpreta para disparar la transición. El modelo es la
fuente de verdad del comportamiento.

Las guardas se escriben con una **gramática restringida**: no ejecutan código
arbitrario (se parsean a un árbol de datos, nunca con `eval`).

## Gramática

```
guarda      := condicion ( ("and" | "or") condicion )*
condicion   := referencia operador valor
             | referencia "between" valor "and" valor
             | "stale(" referencia "," segundos ")"
operador    := ">" | ">=" | "<" | "<=" | "==" | "!="
referencia  := nombre_value_property | nombre_canal
```

## Ejemplos

```
co2_ppm > 1000
co2_ppm > 1000 and personas >= 1
temp_c between 18 and 26
stale(co2_ppm, 30)
```

- `co2_ppm > 1000` — dispara cuando el CO₂ supera 1000 ppm.
- `co2_ppm > 1000 and personas >= 1` — combina dos condiciones (solo si hay gente).
- `temp_c between 18 and 26` — rango cerrado.
- `stale(co2_ppm, 30)` — detecta **ausencia de datos**: se cumple si el canal no
  recibe lecturas durante 30 segundos. Se verifica con un tick periódico, no con la
  llegada de datos.

## Resolución de referencias

Una `referencia` se resuelve en este orden:

1. Como **value property** del bloque dueño de la máquina de estados; de ahí se sigue
   su canal mapeado.
2. Directamente como **canal** del proyecto.

El mapeo (ver [mapeo-y-convenciones.md](mapeo-y-convenciones.md)) es lo que conecta el
nombre que aparece en la guarda con la serie de datos real.

## Guardas no resolubles

Si una guarda no parsea o referencia un canal/propiedad sin mapear, se reporta como
**advertencia de interpretación** al importar, sin bloquear el resto de la máquina. La
transición afectada queda **inerte**: jamás se evalúa y la UI la muestra atenuada con
su advertencia. Corregir el modelo o completar el mapeo y reimportar la reactiva.

## Evaluación en vivo

Al llegar una lectura, solo se re-evalúan las transiciones salientes del estado activo
cuyas guardas referencian el canal recibido. Al cumplirse una guarda, se actualiza el
estado activo (en memoria y en el grafo), se registra el evento de transición y se
notifica a todas las vistas, que resaltan el impacto simultáneamente.
