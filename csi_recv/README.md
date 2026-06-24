# CSI_RECV

Firmware de la ESP32 receptora.

## Funcionamiento actual

- Escucha los paquetes ESP-NOW enviados por `csi_send`.
- Extrae CSI desde el driver WiFi y lo imprime por USB serial como `CSI_DATA`.
- Reconoce paquetes de sensores con firma `SENS` y los imprime como
  `SENSOR_DATA`.

Durante la demo esta es la unica ESP32 que debe estar conectada a la Mac. La web
local lee su puerto serial y muestra tanto el CSI/modelo respiratorio como los
sensores del emisor.
