# Modelo especializado de pausa manual — 2026-08-12

## Artefacto

`manual_hold_specialized_20260812.joblib`

Modelo entrenado exclusivamente para el protocolo actual:

1. Una persona respira normalmente durante 60 segundos.
2. La alarma marca el inicio de una pausa voluntaria.
3. La persona pulsa el botón justo antes de volver a respirar.

No combina datos de junio ni las sesiones antiguas con intervalos automáticos.

## Datos de entrenamiento

- 13 sesiones capturadas.
- 11 sesiones con al menos una ventana continua de 20 segundos.
- 97 ventanas técnicamente válidas.
- 43 ventanas de respiración.
- 54 ventanas de pausa.
- Ventana: 20 segundos.
- Paso: 5 segundos.
- CSI: longitud 256, remuestreado a 50 Hz.

## Modelo

- Algoritmo: bosque aleatorio.
- Árboles: 500.
- Profundidad máxima: 4.
- Mínimo por hoja: 2.
- Balance de clases activado.
- Umbral de pausa: 0,40.

## Validación dejando una sesión completa fuera

- Exactitud: 76,29 %.
- Exactitud balanceada: 75,15 %.
- Sensibilidad de pausa: 85,19 %.
- Precisión de alertas: 75,41 %.
- AUC: 68,82 %.
- Matriz de confusión: `[[28, 15], [8, 46]]`.

El algoritmo y el umbral fueron seleccionados comparando configuraciones sobre
estas mismas sesiones. Por ello, estas métricas son de selección del modelo y
no sustituyen una prueba final completamente nueva.

## Alcance

El modelo está especializado para la persona, colocación, hardware, red y
protocolo usados en estas tomas. No se considera generalizable a otra persona o
habitación y no es un dispositivo médico.

## Prueba final pendiente

Realizar al menos cinco sesiones nuevas sin reentrenar. Conservarlas aparte y
evaluarlas una sola vez. No incorporarlas al entrenamiento antes de medir el
resultado final.
