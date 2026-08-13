# CSI Web Dashboard

Dashboard local para ver los `CSI_DATA` del receptor ESP32 en tiempo real.

Arquitectura actual:

- `csi_send`: ESP32 emisora con bateria. Genera paquetes para CSI, lee sensores
  y activa el buzzer local.
- `csi_recv`: ESP32 receptora. Recibe paquetes del emisor y envia datagramas
  binarios UDP a la Mac.
- `csi_web/server.py`: corre en la Mac, recibe UDP y muestra CSI,
  modelo respiratorio, sensores y notificaciones.

## Ejecutar

Instalar las dependencias ML:

```sh
python3 -m pip install -r csi_web/requirements-ml.txt
```

Si usas el Python de ESP-IDF:

```sh
/Users/jandonyggarofalo/.espressif/python_env/idf5.5_py3.14_env/bin/python \
  -m pip install -r csi_web/requirements-ml.txt
```

```sh
python3 \
  csi_web/server.py \
  --model-path "models/apnea_model_raw_june_2026.joblib" \
  --http-port 8080 \
  --udp-port 5000
```

El servidor escucha la dashboard HTTP en `8080`, los datos en UDP `5000` y el
descubrimiento del gateway en UDP `5001`. La IP de la Mac se descubre
automáticamente y no se compila dentro de la ESP.

Abrir:

```text
http://127.0.0.1:8080
```

Desde un teléfono en la misma red se puede abrir la dirección `.local` que
imprime el servidor al arrancar, por ejemplo
`http://nombre-de-la-mac.local:8080/usuario`.

## Modelo respiratorio predeterminado

El modelo predeterminado es `models/apnea_model_raw_june_2026.joblib`. Fue
entrenado con las sesiones respiratorias etiquetadas de `Downloads/raw`, usando
las mismas ocho características y ventanas de 20 s que calcula el dashboard.

Para regenerarlo cuando se incorporen nuevas sesiones de julio:

```sh
/Users/jandonyggarofalo/.espressif/tools/python/v5.5.4/venv/bin/python3 \
  csi_ml/train_apnea_model.py --base-dir . \
  --raw-dir /Users/jandonyggarofalo/Downloads/raw --month 202606 \
  --output models/apnea_model_raw_june_2026.joblib \
  --report data/reports/apnea_model_raw_june_2026.json
```

El resultado de la evaluación por sesiones completas queda en
`data/reports/apnea_model_raw_june_2026.json`. Es un detector experimental de
retención simulada, no un dispositivo de diagnóstico médico.

## Alertas por Telegram

El servidor puede mandar un mensaje cada segundo mientras el modelo reporte
`apnea`. No guardes el token del bot dentro del repositorio.

1. Crea o regenera el token con `@BotFather`.
2. Abre el chat con el bot y envia `/start`.
3. Obtén el `chat_id`:

```sh
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates"
```

4. Edita `csi_web/.env.local` y pega tus valores:

```sh
TELEGRAM_BOT_TOKEN=pega_aqui_el_token_nuevo
TELEGRAM_CHAT_ID=pega_aqui_el_chat_id
TELEGRAM_ALERT_INTERVAL_S=1
```

5. Ejecuta el servidor normalmente. El archivo `.env.local` se carga solo. Cuando el modelo detecte apnea, enviará:

```text
🚨 ⚠️ ALERTA DE AHOGO: posible apnea detectada.
```

## Qué muestra

- Mapa 2D de referencia del cuarto con TX, RX y objetos.
- Resultado del modelo respiratorio en vivo: respirando / posible apnea, RPM,
  probabilidad, confianza y calidad de señal.
- Sensores del emisor: temperatura TMP36, sonido/llanto y buzzer.
- Control de LEDs WS2812B y modos energético `monitoring`, `eco` y `standby`.
- Estado de alertas Telegram y cantidad de mensajes enviados.
- Amplitud CSI por subportadora del último paquete.
- Heatmap de amplitud CSI en el tiempo.
- RSSI y SNR aproximado.
- Actividad del enlace respecto a una línea base.

El botón `Calibrar` toma los últimos paquetes como baseline. Úsalo cuando el cuarto esté quieto.

## Sensores y actuadores

Los sensores estan conectados al emisor:

- `GPIO34`: TMP36 por ADC.
- `GPIO27`: micrófono digital, activo cuando la lectura es `LOW`.
- `GPIO26`: señal para la etapa de potencia de un buzzer activo de 5 V.
- `GPIO25`: datos para los LEDs WS2812B.

El emisor manda un paquete binario `sensor_payload_t` por ESP-NOW cada segundo.
El receptor lo empaqueta en datagramas UDP binarios enviados a la Mac. El servidor
los transforma internamente a las mismas estructuras `CSI_DATA` y `SENSOR_DATA`
que usan el dashboard, el modelo y la recolección.

```text
SENSOR_DATA,seq,temp_c_x10,bpm,sound_detected,alert_sound,alert_bpm_high,alert_temp_high,alert_temp_low,buzzer_interval_ms,buzzer_on,uptime_ms
```

La temperatura viaja como `temp_c_x10`, por ejemplo `215` significa `21.5 C`.
El campo BPM permanece en cero por compatibilidad y no representa una medición.
Este flujo permite que solo el receptor este conectado a la Mac mientras el
emisor trabaja con bateria.

## Logs de errores

La web crea automaticamente esta carpeta al arrancar:

```text
/Users/jandonyggarofalo/Documents/Desa/UNI/Semestre7/embebidos/esp-csi/examples/get-started/csi_send/csi_web/logs
```

Archivos principales:

- `app.log`: log rotativo para lectura humana.
- `errors.jsonl`: eventos estructurados, un JSON por linea, con categorias
  `startup`, `network`, `parse`, `collection`, `ml`, `telegram` y `http`.

Puedes consultar el estado desde:

```text
http://127.0.0.1:8080/api/logs/status
```

O desde terminal:

```sh
tail -f csi_web/logs/app.log
tail -f csi_web/logs/errors.jsonl
```

## Recolección desde la web

La web y la recolección usan la misma ingesta UDP. El puerto USB de la ESP solo
se necesita para flashear y revisar logs; ya no transporta los datos CSI.

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
