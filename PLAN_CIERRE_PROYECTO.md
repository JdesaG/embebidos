# Estado y plan de cierre - Proyecto BabyCSI

## Propósito del documento

Este documento alinea al equipo sobre lo que ya funciona, los bloqueos reales y
el orden de trabajo necesario para obtener un prototipo demostrable, seguro y
coherente con lo reportado. El alcance se concentra en terminar la integración;
las mejoras que no sean necesarias para la demostración quedan separadas.

## 1. Resultado que debe entregar el proyecto

BabyCSI utiliza dos ESP32:

- `csi_send`: nodo ubicado junto a los sensores y actuadores. Lee temperatura y
  sonido, controla buzzer y luces, y genera tráfico para CSI.
- `csi_recv`: gateway que recibe ESP-NOW, extrae CSI, se conecta al Wi-Fi y
  reenvía datos al servidor local.
- Computadora: ejecuta el dashboard, recibe CSI, carga el modelo y permite el
  control desde el teléfono.
- Teléfono: configura únicamente el gateway y abre el panel de usuario.

La alimentación final debe salir de una power bank de 5 V / 2.4 A. No se debe
depender de una fuente de 110 V ni de las luces actuales de 12 V.

## 2. Trabajo terminado

- Firmware del emisor y del gateway compila con ESP-IDF 5.5.4.
- Solo el gateway solicita red y contraseña mediante el portal
  `BabyCSI-XXXX` en `192.168.4.1`.
- Se pueden guardar una red principal y una de respaldo.
- La emisora encuentra automáticamente el canal del gateway por ESP-NOW.
- El gateway descubre el servidor aunque cambie la IP de la computadora.
- TMP36 integrado en GPIO34.
- Sensor de sonido digital previsto en GPIO27, activo en nivel LOW.
- El potenciómetro y el BPM simulado fueron retirados del alcance.
- Control de WS2812B previsto en GPIO25, con 16 LEDs y brillo limitado al 30 %.
- Control de buzzer previsto en GPIO26 mediante etapa de potencia.
- Modos energéticos implementados: monitoring, eco y standby.
- Panel web con luz, color, brillo, modo energético y confirmación de estado.
- Servidor, protocolo UDP y confirmación de actuadores probados localmente.
- Afiche de usuario y manual independiente del servidor preparados.

## 3. Stoppers actuales

### A. Seguridad de la PCB

La placa ha presentado síntomas compatibles con corto o distribución incorrecta
de alimentación. No se debe conectar otra ESP hasta medir continuidad entre
5V-GND y 3V3-GND, alimentar la PCB sola con corriente limitada y comprobar cada
riel.

**Salida requerida:** esquema actualizado, pinout, fotos de ambas caras y tabla
de tensiones/consumo antes de montar las ESP.

### B. Buzzer incompatible con la alimentación

El GS1212S reportado debe tratarse como componente de 12 V hasta confirmar su
hoja de datos. Mantenerlo obligaría a añadir un elevador de tensión y rompe la
arquitectura de 5 V.

**Decisión recomendada:** sustituirlo por un buzzer activo de 5 V, controlado por
transistor o MOSFET. No conectarlo directamente al GPIO26.

### C. Sensor de sonido sin identificación

Solo se conoce que tiene tres pines. Falta confirmar modelo, orden VCC/GND/DO,
tensión de trabajo y polaridad de salida.

**Salida requerida:** foto legible o modelo exacto y medición de `DO`. Si la
salida es de 5 V, colocar divisor o adaptador a 3.3 V.

### D. Iluminación pendiente

Las luces de 12 V no forman parte del producto final. Faltan WS2812B de 5 V y su
etapa correcta: 74AHCT125/74HCT125, resistencia de datos de aproximadamente
330 ohm y capacitor de 470-1000 uF.

### E. Integración física sin validar

El software compila y las comunicaciones se probaron localmente, pero todavía
falta flashear las versiones finales y comprobar el flujo completo sobre las
dos ESP, la PCB y la power bank reales.

### F. Modelo experimental

El modelo actual fue entrenado con datos limitados. Sirve para una demostración
académica controlada, pero no permite afirmar desempeño médico ni generalización
a otras personas o habitaciones.

## 4. Orden de cierre recomendado

### Fase 1 - Alinear hardware y congelar componentes

1. Confirmar ESP32, TMP36 y pinout del sensor de sonido.
2. Sustituir el buzzer por uno activo de 5 V.
3. Conseguir WS2812B y componentes de protección/adaptación.
4. Congelar el pinout: TMP36 GPIO34, sonido GPIO27, buzzer GPIO26, LEDs GPIO25 y
   botón de configuración GPIO32 en el gateway.

### Fase 2 - Recuperar y probar la PCB

1. Trabajar con módulos retirados.
2. Medir 5V-GND y 3V3-GND.
3. Alimentar con límite de corriente.
4. Registrar 5 V y 3.3 V en cada conector.
5. Agregar un módulo a la vez: TMP36, sonido, buzzer, LEDs y finalmente ESP.

### Fase 3 - Cargar software y levantar servidor

1. Flashear `csi_recv` y `csi_send` con los binarios finales.
2. Iniciar `server.py` siguiendo el manual del responsable.
3. Configurar el gateway desde `BabyCSI-XXXX`.
4. Confirmar descubrimiento del servidor y emparejamiento de la emisora.

### Fase 4 - Prueba funcional completa

1. Verificar que aumenten los paquetes CSI.
2. Comparar TMP36 con una referencia de temperatura.
3. Probar sonido y confirmar su polaridad.
4. Encender y apagar buzzer desde alerta y desde control remoto.
5. Probar luz, color, brillo y los tres modos energéticos.
6. Cambiar la computadora a otra red y comprobar que no sea necesario recompilar.
7. Reiniciar cada elemento por separado y verificar recuperación.

### Fase 5 - Evidencia y entrega

- Fotografías del montaje y mediciones eléctricas.
- Video corto de configuración Wi-Fi, recepción CSI y control de luces.
- Captura del dashboard con sensores y confirmación de actuadores.
- Afiche junto al prototipo y manual del servidor disponible.
- Carpeta final con código, binarios, esquema y lista de materiales.

## 5. Trabajo paralelo y responsables

### Jorge / hardware

- Diagnóstico de corto y corrección de PCB.
- Confirmación del sensor de sonido.
- Cambio a buzzer activo de 5 V.
- Montaje de WS2812B, etapa de nivel y protecciones.
- Entrega de esquema, pinout y mediciones.

### Jandony / software e integración

- Mantener los firmwares y servidor en su versión compilada.
- Flashear las placas cuando hardware libere la PCB.
- Configurar Wi-Fi, servidor y controles web.
- Ejecutar las pruebas funcionales y guardar evidencia.

### Equipo

- Conseguir componentes faltantes.
- Preparar carcasa o montaje seguro.
- Registrar pruebas, actualizar presentación y organizar la demostración.

Las tareas de compra, revisión de PCB, preparación del servidor y organización
de evidencia pueden realizarse en paralelo. La conexión de las ESP depende de
que hardware libere primero la alimentación.

## 6. Decisiones de contingencia

- Si no se consigue el GS1212S compatible: usar buzzer activo de 5 V equivalente.
- Si el sensor de sonido entrega 5 V: añadir divisor o level shifter; no arriesgar
  el GPIO.
- Si no se consiguen 16 WS2812B: usar un segmento menor y ajustar
  `CSI_LIGHT_LED_COUNT` sin cambiar el protocolo.
- Si la red bloquea broadcast: utilizar un router propio de 2.4 GHz para la
  demostración.
- Si el modelo no carga: mostrar CSI y sensores, corregir el entorno Python y no
  afirmar una inferencia que no se ejecutó.
- Si la PCB no supera continuidad y alimentación: demostrar con protoboard
  ordenada y documentada; no conectar una ESP a una placa insegura.

## 7. Criterios de aceptación

- [ ] PCB sin corto y con mediciones registradas.
- [ ] Sistema completo alimentado por 5 V desde la power bank.
- [ ] Gateway configurable sin recompilar.
- [ ] Emisora emparejada automáticamente.
- [ ] Servidor descubierto aunque cambie su IP.
- [ ] CSI, temperatura y sonido visibles en la web.
- [ ] Buzzer activo de 5 V funcionando mediante etapa de potencia.
- [ ] Luces controlables desde el teléfono con confirmación.
- [ ] Modos monitoring, eco y standby comprobados.
- [ ] Reinicio y recuperación comprobados.
- [ ] Evidencia, código, esquema y manuales reunidos.

El prototipo se considera cerrado cuando se cumplen estos criterios, no solo
cuando cada módulo funciona de manera aislada.
