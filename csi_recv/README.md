# CSI_RECV

Firmware de la ESP32 receptora.

## Funcionamiento actual

- Escucha los paquetes ESP-NOW enviados por `csi_send`.
- Extrae CSI desde el driver Wi-Fi y lo acumula en pequeños lotes binarios.
- Reconoce paquetes de sensores con firma `SENS` y los incluye en esos lotes.
- Se conecta como estación a una red Wi-Fi común y envía datagramas UDP a la Mac.

Durante la demo esta es la única ESP32 que debe estar conectada por USB a la Mac
para flashear o revisar logs. Los datos CSI y sensores viajan por Wi-Fi.

## Configuración

En `idf.py menuconfig`, menú `CSI Receiver Network`, configurar:

- `Wi-Fi SSID` y `Wi-Fi password`.
- `UDP destination host`: IP de la Mac, por ejemplo `192.168.1.100`.
- `UDP destination port`: `5000`, salvo que cambies el puerto del servidor.

La red debe ser de 2.4 GHz. Ambas ESP se conectan al mismo router y ESP-NOW usa
automáticamente el canal actual de esa red; ya no se fija el canal 11.
