# CSI Web Dashboard

Dashboard local para ver los `CSI_DATA` del receptor ESP32 en tiempo real.

## Ejecutar

Cerrar primero cualquier `idf.py monitor` que esté usando el puerto del receptor.

```sh
/Users/jandonyggarofalo/.espressif/tools/python/v5.5.4/venv/bin/python3 \
  csi_web/server.py \
  --serial-port /dev/cu.usbserial-0001 \
  --baud 921600 \
  --http-port 8080
```

Abrir:

```text
http://127.0.0.1:8080
```

## Qué muestra

- Mapa 2D de referencia del cuarto con TX, RX y objetos.
- Amplitud CSI por subportadora del último paquete.
- Heatmap de amplitud CSI en el tiempo.
- RSSI y SNR aproximado.
- Actividad del enlace respecto a una línea base.

El botón `Calibrar` toma los últimos paquetes como baseline. Úsalo cuando el cuarto esté quieto.

## Recolección desde la web

La web y la recolección usan la misma lectura serial. No abras `idf.py monitor` sobre el puerto del receptor al mismo tiempo.

Flujo recomendado:

1. Ejecuta `server.py` y abre `http://127.0.0.1:8080`.
2. Verifica que `Paquetes` suba y que las gráficas cambien.
3. Ajusta `Duración`, `Posición persona`, `Entorno` y `Notas` si hace falta.
4. Presiona el botón del estado que vas a medir.
5. La sesión se detiene sola cuando termina el tiempo definido.

También puedes usar `Detener` para cortar una sesión manualmente.

Cada sesión guarda:

- CSV crudo enriquecido en `data/raw/`.
- Metadatos de la sesión en `data/metadata/`.

Estados disponibles por defecto:

- `Cuarto vacío`: sin nadie entre TX/RX.
- `Respiración`: persona quieta respirando normal.
- `Apnea`: ciclos de respiración normal y retención de aire.
- `Caminando`: movimiento entre TX/RX.
- `Ambiente activo`: aire/equipos/personas moviéndose.
