#pragma once

#include <stdint.h>

/*
 * Paquete binario que csi_send envia por ESP-NOW y csi_recv transforma al
 * formato SENSOR_DATA dentro de lotes HTTP. Mantenerlo pequeno evita
 * interferir con el trafico CSI de 100 Hz.
 */

#define SENSOR_PAYLOAD_MAGIC   0x534e4553u /* "SENS" en little-endian */
#define SENSOR_PAYLOAD_VERSION 1u

enum {
    SENSOR_ALERT_SOUND     = 1u << 0,
    SENSOR_ALERT_BPM_HIGH  = 1u << 1,
    SENSOR_ALERT_TEMP_HIGH = 1u << 2,
    SENSOR_ALERT_TEMP_LOW  = 1u << 3,
};

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    uint32_t seq;
    int16_t temp_c_x10;
    uint16_t bpm;
    uint8_t sound_detected;
    uint8_t alert_flags;
    uint16_t buzzer_interval_ms;
    uint8_t buzzer_on;
    uint8_t reserved;
    uint32_t uptime_ms;
} sensor_payload_t;
