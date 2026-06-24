# CSI_SEND

Firmware de la ESP32 emisora.

## Funcionamiento actual

- Envia paquetes ESP-NOW a `100 Hz` para que `csi_recv` pueda extraer CSI.
- Lee sensores conectados al emisor:
  - `GPIO34`: TMP36.
  - `GPIO35`: potenciómetro/BPM simulado.
  - `GPIO27`: micrófono digital.
- Controla buzzer/parlante en `GPIO26`.
- Envia un paquete `SENSOR_DATA` por ESP-NOW cada segundo.

La ESP emisora puede estar conectada a una bateria. No necesita estar conectada
a la Mac durante la demo; el receptor reenvia todo por serial hacia la web.
