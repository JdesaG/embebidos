# Integración eléctrica y lista de aceptación

## Decisión de alimentación

Toda la unidad se alimenta con una power bank de 5 V / 2,4 A (12 W máximos).
Las luces de 12 V con fuente de 110 V no forman parte del producto final. El
GS1212S reportado también aparece como dispositivo de 12 V y debe sustituirse
por un buzzer activo de 5 V. Así se evita un elevador de voltaje, sus pérdidas y
una segunda tensión de potencia.

## Componentes finales

| Función | Componente | Alimentación | Señal ESP32 emisora |
|---|---|---:|---:|
| Temperatura | TMP36 | 3,3 V | GPIO34 / ADC1_CH6 |
| Sonido | Módulo digital de 3 pines | 3,3 V si lo admite | GPIO27 |
| Alarma | Buzzer activo de 5 V | 5 V por transistor/MOSFET | GPIO26 |
| Iluminación | 12–16 WS2812B | 5 V | GPIO25 |
| Reconfiguración | Pulsador a GND en gateway | 3,3 V lógico | GPIO32 |

El modelo exacto del sensor de sonido sigue pendiente. Antes de soldarlo hay que
confirmar su pinout y el voltaje de su salida digital. Si trabaja a 5 V o entrega
5 V en `DO`, no debe conectarse directo al ESP32: usar divisor o adaptador de
nivel a 3,3 V.

## PCB que debe entregar hardware

```text
Power bank 5 V
  ├── interruptor + protección de entrada
  ├── VIN/5V de las ESP32
  ├── 5V de WS2812B + 470–1000 µF cerca de la tira
  ├── 5V del buzzer a través de transistor/MOSFET
  └── regulador 3,3 V de la ESP → TMP36 y sensor compatible

Todas las tierras unidas en GND común.
```

- WS2812B: resistencia serie de aproximadamente 330 Ω en DATA, colocada cerca
  del primer LED, y adaptador 74AHCT125/74HCT125 de 3,3 V a 5 V.
- Buzzer: nunca desde GPIO26 directamente. Usar transistor NPN o MOSFET lógico,
  resistencia de compuerta/base y pull-down. Añadir diodo de rueda libre si la
  carga resulta inductiva y el fabricante lo recomienda.
- TMP36: alimentar a 3,3 V y desacoplar con 100 nF cerca del sensor. Verificar el
  orden físico de pines contra la hoja de datos del encapsulado comprado.
- Conectores rotulados y puntos de prueba para 5V, 3V3 y GND.
- No usar GPIO35: el potenciómetro/BPM simulado fue retirado del alcance.

Con 16 WS2812B el máximo teórico sin límite puede acercarse a 0,96 A solo para
los LEDs. El firmware limita el brillo global a 76/255 (aprox. 30 %), pero la PCB
y los cables deben diseñarse para los picos y no depender únicamente del límite
de software.

## Prueba obligatoria antes de conectar una ESP

1. Retirar ESP, sensores, buzzer y LEDs.
2. Medir que no exista corto entre 5V–GND ni 3V3–GND.
3. Inspeccionar puentes, polaridades y orientación de componentes.
4. Alimentar la PCB sola con una fuente limitada en corriente.
5. Medir 5 V y 3,3 V en cada conector y registrar resultados.
6. Agregar un módulo a la vez y repetir la medición de consumo/tensión.
7. Conectar las ESP solamente después de superar los pasos anteriores.

Hardware debe entregar: esquema actualizado, pinout, fotos de ambas caras, lista
de materiales, mediciones de tensión/consumo y resultado de continuidad.

## Criterios de aceptación del sistema

- Configura dos redes sin recompilar y recupera el portal si ambas fallan.
- La emisora se empareja sin conocer SSID/contraseña.
- El servidor se descubre aunque cambie la IP del computador.
- CSI, temperatura y sonido llegan a la web.
- Luz, brillo, color, buzzer y modo energético reciben confirmación de la ESP.
- No hay reinicios por caída de tensión con luz y buzzer activos.
- Una sesión de duración conocida permite medir autonomía real de la power bank.
