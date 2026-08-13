#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"

/* Inicializa Wi-Fi, prueba las dos redes guardadas y abre el portal si fallan. */
void wifi_manager_start(void);

/* Espera a que el gateway tenga IP. El portal sigue funcionando mientras espera. */
bool wifi_manager_wait_connected(TickType_t timeout);

/* Canal real de la red domestica. ESP-NOW debe usar este mismo canal. */
uint8_t wifi_manager_channel(void);

/* Nombre del AP de configuracion mostrado en el manual. */
const char *wifi_manager_provisioning_ssid(void);

