#pragma once

#include <stdint.h>

/*
 * Protocolo pequeno compartido por el servidor, el gateway CSI y el nodo de
 * sensores. Todos los campos son enteros y el paquete va packed para que el
 * formato sea estable entre ESP32 y Python.
 */

#define DEVICE_MESSAGE_MAGIC 0x44495343u /* "CSID" little-endian */
#define DEVICE_MESSAGE_VERSION 1u

enum {
    DEVICE_MSG_PAIR_HELLO = 1u,
    DEVICE_MSG_PAIR_SYNC = 2u,
    DEVICE_MSG_ACTUATOR_COMMAND = 3u,
    DEVICE_MSG_ACTUATOR_STATE = 4u,
    DEVICE_MSG_SERVER_DISCOVERY = 5u,
    DEVICE_MSG_SERVER_REPLY = 6u,
};

enum {
    DEVICE_VALUE_LIGHT_ON = 0,
    DEVICE_VALUE_BRIGHTNESS = 1,
    DEVICE_VALUE_RED = 2,
    DEVICE_VALUE_GREEN = 3,
    DEVICE_VALUE_BLUE = 4,
    DEVICE_VALUE_BUZZER_OVERRIDE = 5,
    DEVICE_VALUE_BUZZER_ON = 6,
    DEVICE_VALUE_ENERGY_MODE = 7,
};

enum {
    DEVICE_ENERGY_MONITORING = 0u,
    DEVICE_ENERGY_ECO = 1u,
    DEVICE_ENERGY_STANDBY = 2u,
};

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t version;
    uint8_t type;
    uint16_t size;
    uint32_t seq;
    uint8_t value[8];
    uint32_t uptime_ms;
} device_message_t;

static inline void device_message_init(device_message_t *message, uint8_t type,
                                       uint32_t seq)
{
    if (!message) {
        return;
    }
    *message = (device_message_t){
        .magic = DEVICE_MESSAGE_MAGIC,
        .version = DEVICE_MESSAGE_VERSION,
        .type = type,
        .size = sizeof(*message),
        .seq = seq,
    };
}

static inline int device_message_valid(const device_message_t *message)
{
    return message && message->magic == DEVICE_MESSAGE_MAGIC &&
           message->version == DEVICE_MESSAGE_VERSION &&
           message->size == sizeof(*message);
}
