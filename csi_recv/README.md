# CSI_RECV

Firmware de la ESP32 gateway/receptora. Esta es la única ESP que el usuario
configura.

## Funcionamiento actual

- Si no puede entrar a una red guardada, crea el punto de acceso
  `BabyCSI-XXXX` con clave `BabyCSI26`.
- Sirve el portal en `http://192.168.4.1` para guardar una red principal y una
  red de respaldo, ambas de 2.4 GHz.
- Responde al emparejamiento del emisor para que ambas ESP usen el canal real
  del router.
- Extrae CSI y recibe sensores/estados por ESP-NOW.
- Descubre automáticamente `server.py` por broadcast UDP; la IP del computador
  ya no se compila ni se escribe en la ESP.
- Reenvía al emisor los comandos de luz, buzzer y ahorro de energía de la web.

Para borrar las redes, mantener presionado durante cinco segundos el botón de
configuración conectado entre GPIO32 y GND mientras se enciende la placa.

La consola USB trabaja a 115200 baudios y se usa solo para diagnóstico. Cada
10 segundos imprime una línea `DIAG` con el estado del servidor y contadores de
emparejamiento, CSI, sensores y datagramas UDP. Los datos CSI continúan viajando
por ESP-NOW y Wi-Fi; no se transportan por el cable USB.

## Compilar y flashear

```sh
idf.py build
idf.py -p PUERTO flash monitor
```

Para abrir únicamente el diagnóstico serial de una placa ya flasheada:

```sh
idf.py -p PUERTO monitor
```

El único ajuste de red compilado es el puerto UDP de ingesta, por defecto 5000.
