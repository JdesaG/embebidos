# CSI_SEND

Firmware de la ESP32 emisora que queda junto a los sensores y actuadores.

## Funcionamiento actual

- No guarda SSID ni contraseña. Busca al gateway `csi_recv` recorriendo los
  canales de 2.4 GHz y se empareja automáticamente por ESP-NOW.
- Envía paquetes para obtener CSI a 100 Hz en modo `monitoring`, 50 Hz en `eco`
  y 1 Hz en `standby`.
- Lee el TMP36 por ADC en GPIO34 y el módulo de sonido digital en GPIO27.
- Controla un buzzer activo de 5 V en GPIO26 mediante la etapa de potencia de
  la PCB. El GS1212S de 12 V debe sustituirse.
- Controla hasta 16 LEDs WS2812B en GPIO25, con brillo limitado a 30 %.
- Recibe desde la web los comandos de luz, buzzer y modo energético y devuelve
  una confirmación de estado.

No existe ya un BPM simulado ni se requiere potenciómetro. El campo BPM se
mantiene en cero solamente para conservar compatibilidad con los datos viejos.

## Compilar y flashear

```sh
idf.py build
idf.py -p PUERTO flash monitor
```

Los pines y cantidades de LED pueden cambiarse desde el menú `CSI Sender
hardware` de `idf.py menuconfig`.
