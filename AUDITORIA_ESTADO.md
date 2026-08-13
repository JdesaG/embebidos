# Auditoría de estado — BabyCSI

Fecha de corte: 11 de agosto de 2026.

## Estado implementado

| Área | Resultado |
|---|---|
| Configuración | Solo el gateway se configura; portal local con red principal y respaldo |
| Cambio de IP | Descubrimiento automático del servidor por UDP; sin IP fija en firmware |
| Enlace entre ESP | Emparejamiento automático por ESP-NOW recorriendo canales 1–13 |
| CSI | Emisor genera tráfico; receptor captura y envía lotes UDP al servidor |
| Sensores | TMP36 GPIO34 y sonido digital GPIO27; potenciómetro retirado |
| Buzzer | Control local/remoto por GPIO26 preparado para buzzer activo de 5 V |
| Luces | WS2812B GPIO25, 16 LEDs por defecto, límite de brillo al 30 % |
| Energía | Modos 100 Hz, 50 Hz y 1 Hz; LED apagado en standby; CPU 80–160 MHz dinámica |
| Web | Control de luz, brillo, color y modo; muestra confirmación devuelta por la ESP |
| Documentación | Manual de usuario, integración PCB, recuperación y criterios de aceptación |

## Bloqueos antes de declarar el prototipo físico terminado

1. **PCB:** no conectar otra ESP hasta demostrar que no hay corto y entregar las
   mediciones descritas en `INTEGRACION_HARDWARE.md`.
2. **Buzzer:** sustituir el GS1212S de 12 V por un buzzer activo de 5 V y definir
   la referencia exacta antes de cerrar el esquema.
3. **Sensor de sonido:** falta modelo/foto legible para confirmar pinout, tensión
   y polaridad de la salida. El firmware supone salida digital activa en LOW.
4. **Iluminación:** comprar/probar WS2812B de 5 V, 12–16 LEDs, 74AHCT125,
   resistencia de datos y capacitor de reserva.
5. **Validación física:** flashear ambas placas, medir consumo real en los tres
   modos y ejecutar una prueba de varias horas con la power bank.

## Mejoras necesarias después de la integración

- Reentrenar y validar el modelo con varias personas, posiciones, habitaciones y
  días; el modelo actual no es evidencia suficiente para uso médico.
- Medir tasa de pérdida, frecuencia CSI efectiva y reconexión después de apagar
  router, servidor, gateway y emisor por separado.
- Probar el portal y la dirección `.local` en Android/iPhone y en cada red de la
  demostración; algunas redes de invitados bloquean broadcast entre equipos.
- Cambiar la clave fija del portal para cada unidad y autenticar/cifrar comandos
  UDP/ESP-NOW antes de cualquier uso fuera de un laboratorio confiable.
- Añadir OTA firmada, versionado visible del firmware y registro de reinicios si
  el proyecto pasa de prototipo académico a producto.

## Verificación de software realizada

- `csi_recv`: compila con ESP-IDF 5.5.4 para ESP32.
- `csi_send`: compila con ESP-IDF 5.5.4 y `led_strip` 3.0.3 para ESP32.
- Servidor Python y JavaScript: validación sintáctica correcta.
- Pruebas automáticas: decodificación de confirmación de actuador y ciclo local
  de descubrimiento/respuesta/comando UDP.

Estas verificaciones no reemplazan una prueba sobre las placas y la PCB reales.
