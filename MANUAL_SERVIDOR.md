# Manual del responsable del servidor BabyCSI

Este procedimiento lo ejecuta Jandony antes de entregar el control al usuario.
La computadora actúa como servidor local del dashboard y del modelo. Debe
permanecer encendida, conectada a la misma red y con `server.py` ejecutándose.

## 1. Preparación única

Abrir Terminal y entrar en la raíz del proyecto:

```sh
cd /Users/jandonyggarofalo/Documents/Desa/UNI/Semestre7/embebidos/esp-csi/examples/get-started
```

Instalar las dependencias del modelo:

```sh
python3 -m pip install -r csi_send/csi_web/requirements-ml.txt
```

Comprobar que existen estos archivos:

```sh
test -f csi_send/csi_web/server.py && echo "Servidor OK"
test -f csi_send/models/apnea_model_raw_june_2026.joblib && echo "Modelo OK"
```

Las alertas de Telegram son opcionales. Si se utilizan, los valores deben estar
en `csi_send/csi_web/.env.local`; no deben copiarse al manual ni publicarse.

## 2. Antes de cada uso

1. Conectar la Mac a la red Wi-Fi de 2.4 GHz que utilizará el gateway.
2. Desactivar temporalmente VPN, compartir Internet o una red de invitados que
   aísle los dispositivos.
3. En macOS, permitir conexiones entrantes para Python en **Configuración del
   Sistema > Red > Firewall > Opciones**.
4. Confirmar que están disponibles:
   - TCP 8080: dashboard.
   - UDP 5000: datos CSI y sensores.
   - UDP 5001: descubrimiento del servidor.

## 3. Iniciar el servidor

Desde la raíz del proyecto:

```sh
python3 csi_send/csi_web/server.py
```

No es obligatorio escribir `--model-path`: el servidor carga por defecto
`csi_send/models/apnea_model_raw_june_2026.joblib`.

El arranque correcto imprime mensajes semejantes a:

```text
CSI dashboard: http://0.0.0.0:8080
Panel para el teléfono: http://Jandonys-MacBook-Air.local:8080/usuario
CSI ingest: UDP 0.0.0.0:5000
Gateway discovery: UDP 0.0.0.0:5001
```

Mantener abierta esta Terminal. Cerrar la ventana o presionar `Ctrl+C` detiene
el servidor.

## 4. Comprobar el servidor antes de encender el equipo

En la Mac, abrir:

```text
http://127.0.0.1:8080
```

Luego probar el panel del usuario:

```text
http://Jandonys-MacBook-Air.local:8080/usuario
```

También puede comprobarse la API desde otra Terminal:

```sh
curl http://127.0.0.1:8080/api/status
```

Antes de encender las ESP es normal que `gateway` sea `null` y que no existan
paquetes CSI.

## 5. Orden correcto de encendido

1. Servidor ejecutándose y panel local abierto.
2. Encender la ESP gateway `csi_recv`.
3. Esperar que la Terminal registre **Gateway CSI descubierto**.
4. Encender la ESP emisora `csi_send`.
5. Confirmar que el contador de paquetes aumenta y que aparecen temperatura y
   sonido.
6. Desde `/usuario`, enviar un cambio de luz o modo energético.
7. Entregar el teléfono al usuario únicamente cuando aparezca **Confirmado** en
   el estado del dispositivo.

## 6. Lista de verificación para entregar al usuario

- [ ] Mac conectada a la red correcta y sin VPN.
- [ ] `server.py` continúa abierto sin errores.
- [ ] Modelo cargado.
- [ ] Gateway descubierto.
- [ ] Paquetes CSI aumentando.
- [ ] Temperatura y sonido visibles.
- [ ] Teléfono en la misma red.
- [ ] `/usuario` abre desde el teléfono.
- [ ] Comando de luces confirmado por la ESP emisora.
- [ ] Power bank cargada y PCB revisada.

## 7. Solución rápida de problemas

### El puerto ya está ocupado

Existe otra copia del servidor. Revisar:

```sh
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -nP -iUDP:5000
lsof -nP -iUDP:5001
```

Volver a la Terminal anterior y detenerla con `Ctrl+C`; evitar `kill -9` salvo
que el proceso no responda.

### El gateway no aparece

- Confirmar que Mac y gateway están en la misma red de 2.4 GHz.
- Evitar redes `Guest/Invitados` con aislamiento de clientes.
- Revisar firewall y UDP 5001.
- Reiniciar primero el gateway y después la emisora.

### La web abre, pero no llegan datos

- Revisar si la Terminal muestra **Gateway CSI descubierto**.
- Confirmar que la emisora encontró un canal.
- Revisar alimentación de ambas ESP y tierra común.
- Abrir `csi_send/csi_web/logs/app.log` y `errors.jsonl`.

### El modelo no carga

Ejecutar:

```sh
ls -lh csi_send/models/apnea_model_raw_june_2026.joblib
python3 -m pip install -r csi_send/csi_web/requirements-ml.txt
```

El modelo es experimental y no debe presentarse como dispositivo médico.

## 8. Cierre correcto

1. Recuperar el teléfono o cerrar el panel del usuario.
2. Apagar ESP emisora y gateway.
3. Volver a la Terminal del servidor y presionar `Ctrl+C` una vez.
4. Confirmar el mensaje `Stopping CSI dashboard`.
5. Verificar que cualquier sesión de recolección haya guardado sus archivos.
