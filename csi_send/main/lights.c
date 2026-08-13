#include "lights.h"

#include "esp_log.h"
#include "led_strip.h"

#define LIGHT_GPIO CONFIG_CSI_LIGHT_GPIO
#define LIGHT_LED_COUNT CONFIG_CSI_LIGHT_LED_COUNT
#define LIGHT_MAX_BRIGHTNESS CONFIG_CSI_LIGHT_MAX_BRIGHTNESS

static const char *TAG = "lights";
static led_strip_handle_t s_strip;

static uint8_t scale_channel(uint8_t channel, uint8_t brightness)
{
    uint16_t limited = brightness;
    if (limited > LIGHT_MAX_BRIGHTNESS) limited = LIGHT_MAX_BRIGHTNESS;
    return (uint8_t)(((uint16_t)channel * limited) / 255u);
}

void lights_init(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num = LIGHT_GPIO,
        .max_leds = LIGHT_LED_COUNT,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
        .flags.invert_out = false,
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 64,
        .flags.with_dma = false,
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config,
                                              &s_strip));
    ESP_ERROR_CHECK(led_strip_clear(s_strip));
    ESP_LOGI(TAG, "WS2812 listo: GPIO%d, %d LEDs, brillo maximo %d/255",
             LIGHT_GPIO, LIGHT_LED_COUNT, LIGHT_MAX_BRIGHTNESS);
}

void lights_set(bool on, uint8_t brightness, uint8_t red, uint8_t green,
                uint8_t blue)
{
    if (!s_strip) return;
    uint8_t r = on ? scale_channel(red, brightness) : 0;
    uint8_t g = on ? scale_channel(green, brightness) : 0;
    uint8_t b = on ? scale_channel(blue, brightness) : 0;
    for (uint32_t index = 0; index < LIGHT_LED_COUNT; ++index) {
        led_strip_set_pixel(s_strip, index, r, g, b);
    }
    led_strip_refresh(s_strip);
}

void lights_off(void)
{
    if (s_strip) led_strip_clear(s_strip);
}
