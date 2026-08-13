# Filtrado respiratorio — 2026-08-12

Los 11 CSV originales permanecen intactos. Este directorio contiene ventanas de
8 segundos, con paso de 2 segundos y remuestreo a 50 Hz.

## Filtros aplicados

1. CSI válido, parseable, de longitud par y consistente dentro de la sesión.
2. Exclusión de los primeros 10 segundos de estabilización.
3. Etiqueta uniforme en el 100 % de la ventana.
4. Guarda de 2 segundos alrededor de cada transición programada.
5. Cobertura mínima equivalente al 80 % de 50 muestras por segundo.
6. Rechazo de ventanas con un intervalo entre muestras mayor de 100 ms.
7. Limitación robusta de picos por subportadora mediante mediana y MAD, a 6 MAD.
8. Rechazo si más del 2 % de las amplitudes necesitan corrección.
9. Validación de subportadoras activas, PCA y extracción de características respiratorias.
10. Filtro conservador de etiqueta: una retención queda `ambigua` si su actividad
    respiratoria alcanza el percentil 25 del baseline respiratorio de esa sesión.
    Nunca se convierte automáticamente una retención ambigua en respiración.

## Resultado del filtrado

- Ventanas candidatas: 1.236
- Ventanas que superan calidad técnica: 741
- Respiración confiable: 558
- Retención de alta confianza: 44
- Retención ambigua excluida: 139
- Dataset final: 602 ventanas

## Evaluación por sesiones completas

Con características relativas al baseline:

- Exactitud: 80,40 %
- Exactitud balanceada: 81,05 %
- Sensibilidad de retención: 81,82 %
- Precisión de alertas de retención: 24,66 %
- Matriz de confusión: `[[448, 110], [8, 36]]`

La exactitud balanceada aumenta porque solo se conservan retenciones de alta
confianza, pero 110 ventanas de respiración todavía producen falsas alertas. El
dataset puede complementar un entrenamiento nuevo; no es suficiente por sí solo
para reemplazar el modelo en producción.

## Archivos

- `window_manifest.csv`: todas las ventanas, decisiones y motivos de rechazo.
- `X_features.npy`: características de las 602 ventanas finales.
- `y_labels.npy`: etiquetas binarias finales.
- `groups.npy`: sesión de origen de cada ventana.
- `filter_report.json`: parámetros y conteos del filtrado.
- `evaluation_report.json`: validación dejando sesiones completas fuera.

## Limitación de ground truth

No existe un registro externo de los instantes en que la persona volvió a
respirar durante una retención. Por ello, las 139 ventanas sospechosas se
excluyen y no se reetiquetan. El próximo protocolo debe registrar manualmente
el inicio y el fin reales de cada pausa.
