# Resultados de las pausas manuales — 2026-08-12

Se procesaron únicamente las 13 sesiones indicadas. Los CSV originales no se
modificaron.

## Adquisición

- Las 13 sesiones terminaron mediante `participant_ended_hold`.
- Las 13 tienen `valid_for_training: true`.
- Duración real de las pausas: 39,202 a 95,230 segundos.
- Todas las muestras CSI válidas tienen longitud 256.
- Todos los metadatos reportan cero líneas corruptas.

La continuidad no fue uniforme. Las sesiones `131923` y `134940` contienen
cortes tan frecuentes que no producen ninguna ventana continua de 20 segundos.
Las sesiones `131332`, `131729` y `133128` son parcialmente aprovechables. Las
otras ocho tienen continuidad buena o aceptable.

## Filtros aplicados

1. CSI parseable, longitud par y consistente dentro de cada sesión.
2. Exclusión de los primeros 10 segundos de estabilización.
3. Ventanas de 20 segundos y paso de 5 segundos, iguales a la inferencia actual.
4. Etiqueta uniforme en el 100 % de la ventana.
5. Guarda de 2 segundos alrededor del cambio respiración/pausa.
6. Cobertura mínima del 80 % a una frecuencia objetivo de 50 Hz.
7. Rechazo de cualquier ventana con un corte entre muestras mayor de 100 ms.
8. Corrección robusta de picos por subportadora mediante mediana/MAD a 6 MAD.
9. Rechazo si más del 2 % de amplitudes requieren corrección.
10. Validación de subportadoras activas, PCA y características respiratorias.

## Dataset técnico resultante

- Ventanas candidatas: 238.
- Ventanas aceptadas: 97.
- Respiración: 43.
- Pausa: 54.
- Sesiones representadas: 11 de 13.

Rechazos:

- 62 por cortes mayores de 100 ms.
- 40 por mezclar respiración y pausa.
- 29 por la guarda de transición.
- 10 por cobertura insuficiente.

La evaluación principal conserva todas las ventanas técnicamente válidas y sus
etiquetas manuales. No elimina una pausa solo porque su señal se parezca a la
respiración.

## Evaluación dejando sesiones completas fuera

Clasificador logístico con características relativas al baseline respiratorio
de cada sesión:

- Exactitud: 68,04 %.
- Exactitud balanceada: 69,64 %.
- AUC: 75,28 %.
- Sensibilidad de pausa: 55,56 %.
- Precisión de alertas de pausa: 81,08 %.
- Matriz de confusión: `[[36, 7], [24, 30]]`.

Interpretación:

- 36 ventanas respirando fueron reconocidas correctamente.
- 7 ventanas respirando generaron falsas alertas.
- 30 pausas fueron detectadas.
- 24 pausas no fueron detectadas.

Las ventanas de 8 y 12 segundos dieron resultados inferiores. La ventana de 20
segundos es la mejor de las tres evaluadas y coincide con el servidor actual.

## Comparación con el modelo instalado

El modelo `apnea_model_raw_june_2026.joblib` clasificó las 97 ventanas nuevas
como respiración:

- Pausas detectadas: 0 de 54.
- Sensibilidad de pausa: 0 %.
- Exactitud balanceada: 50 %.

Por lo tanto, el modelo instalado no es compatible con estas tomas y no debe
considerarse validado. La nueva data sí es útil para entrenar un candidato
nuevo, pero una validación balanceada de 69,64 % todavía no es suficiente para
reemplazarlo como detector confiable.

## Archivos para el siguiente entrenamiento

- `X_quality_features.npy`: 97 filas por 8 características.
- `y_quality_labels.npy`: 43 respiración y 54 pausa.
- `quality_groups.npy`: sesión de origen para evitar fuga entre entrenamiento y prueba.
- `window_manifest.csv`: todas las decisiones y motivos de rechazo.
- `filter_report.json`: parámetros y conteos del filtrado.
- `evaluation_report.json`: validación por sesiones completas.
