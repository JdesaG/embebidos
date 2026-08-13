#pragma once

#include <stdbool.h>

#include "sensor_payload.h"

/* Inicializa GPIO, ADC y estado interno de sensores/actuadores. */
void sensors_init(void);

/* Actualiza lecturas y buzzer. Llamar frecuentemente desde el bucle principal. */
void sensors_update(void);

/*
 * Devuelve true cuando ya toca transmitir una lectura al receptor.
 * La frecuencia esta limitada internamente para no saturar ESP-NOW ni serial.
 */
bool sensors_get_payload_if_due(sensor_payload_t *payload);

/* Control remoto del buzzer activo de 5 V. Desactivado conserva las alertas. */
void sensors_set_buzzer_override(bool enabled, bool on);
bool sensors_buzzer_is_on(void);
