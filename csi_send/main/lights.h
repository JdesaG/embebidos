#pragma once

#include <stdbool.h>
#include <stdint.h>

void lights_init(void);
void lights_set(bool on, uint8_t brightness, uint8_t red, uint8_t green,
                uint8_t blue);
void lights_off(void);

