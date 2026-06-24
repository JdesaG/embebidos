#include "sensors.h"

#include <string.h>

#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"

/*
 * Pines fisicos usados en la ESP32 emisora.
 * GPIO34 y GPIO35 son solo entrada, por eso son adecuados para ADC.
 */
#define PIN_MIC     GPIO_NUM_27
#define PIN_BUZZER  GPIO_NUM_26
#define ADC_TMP36   ADC_CHANNEL_6 /* GPIO34 en ESP32 */
#define ADC_POT     ADC_CHANNEL_7 /* GPIO35 en ESP32 */

#define BPM_ALTO              100
#define TEMP_MIN_BEBE_X10     200
#define TEMP_MAX_BEBE_X10     220
#define FAST_READ_INTERVAL_US 100000
#define TEMP_READ_INTERVAL_US 2000000
#define SOUND_ALERT_US        3000000
#define SENSOR_SEND_US        1000000

static const char *TAG = "sensors";

typedef struct {
    adc_oneshot_unit_handle_t adc1;
    uint32_t seq;
    int16_t temp_c_x10;
    uint16_t bpm;
    uint8_t sound_detected;
    uint8_t alert_flags;
    uint16_t buzzer_interval_ms;
    uint8_t buzzer_on;
    int64_t last_fast_read_us;
    int64_t last_temp_read_us;
    int64_t sound_alert_until_us;
    int64_t last_buzzer_toggle_us;
    int64_t last_payload_us;
} sensors_state_t;

static sensors_state_t s_sensors;

static int clamp_int(int value, int min_value, int max_value)
{
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return value;
}

static int read_adc_raw(adc_channel_t channel)
{
    int raw = 0;
    esp_err_t ret = adc_oneshot_read(s_sensors.adc1, channel, &raw);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "No se pudo leer ADC canal %d: %s", channel, esp_err_to_name(ret));
        return 0;
    }
    return clamp_int(raw, 0, 4095);
}

static int16_t read_tmp36_x10(void)
{
    int raw = read_adc_raw(ADC_TMP36);

    /*
     * TMP36:
     *   voltaje = raw * 3.3 / 4095
     *   temperatura C = (voltaje - 0.5) * 100
     * Guardamos C x10 para transmitir enteros y evitar floats en el payload.
     */
    float voltage = ((float)raw * 3.3f) / 4095.0f;
    float temp_x10 = (voltage - 0.5f) * 1000.0f;
    return (int16_t)temp_x10;
}

static uint16_t read_bpm_simulado(void)
{
    int raw = read_adc_raw(ADC_POT);
    int bpm = 40 + ((raw * (180 - 40)) / 4095);
    return (uint16_t)clamp_int(bpm, 40, 180);
}

static void update_alert_flags(void)
{
    uint8_t flags = 0;

    if (esp_timer_get_time() < s_sensors.sound_alert_until_us) {
        flags |= SENSOR_ALERT_SOUND;
    }
    if (s_sensors.bpm >= BPM_ALTO) {
        flags |= SENSOR_ALERT_BPM_HIGH;
    }
    if (s_sensors.temp_c_x10 > TEMP_MAX_BEBE_X10) {
        flags |= SENSOR_ALERT_TEMP_HIGH;
    }
    if (s_sensors.temp_c_x10 < TEMP_MIN_BEBE_X10) {
        flags |= SENSOR_ALERT_TEMP_LOW;
    }

    s_sensors.alert_flags = flags;
}

static void update_buzzer_priority(void)
{
    bool sound_alert = (s_sensors.alert_flags & SENSOR_ALERT_SOUND) != 0;
    bool bpm_high = (s_sensors.alert_flags & SENSOR_ALERT_BPM_HIGH) != 0;
    bool temp_high = (s_sensors.alert_flags & SENSOR_ALERT_TEMP_HIGH) != 0;
    bool temp_low = (s_sensors.alert_flags & SENSOR_ALERT_TEMP_LOW) != 0;

    if (sound_alert && bpm_high) {
        s_sensors.buzzer_interval_ms = 80;
    } else if (bpm_high) {
        s_sensors.buzzer_interval_ms = 150;
    } else if (sound_alert) {
        s_sensors.buzzer_interval_ms = 400;
    } else if (temp_high) {
        s_sensors.buzzer_interval_ms = 1000;
    } else if (temp_low) {
        s_sensors.buzzer_interval_ms = 600;
    } else {
        s_sensors.buzzer_interval_ms = 0;
    }
}

static void update_buzzer_output(int64_t now_us)
{
    if (s_sensors.buzzer_interval_ms == 0) {
        if (s_sensors.buzzer_on) {
            gpio_set_level(PIN_BUZZER, 0);
            s_sensors.buzzer_on = 0;
        }
        return;
    }

    int64_t interval_us = (int64_t)s_sensors.buzzer_interval_ms * 1000;
    if (now_us - s_sensors.last_buzzer_toggle_us >= interval_us) {
        s_sensors.buzzer_on = !s_sensors.buzzer_on;
        gpio_set_level(PIN_BUZZER, s_sensors.buzzer_on ? 1 : 0);
        s_sensors.last_buzzer_toggle_us = now_us;
    }
}

void sensors_init(void)
{
    memset(&s_sensors, 0, sizeof(s_sensors));
    s_sensors.temp_c_x10 = 0;

    gpio_config_t mic_cfg = {
        .pin_bit_mask = 1ULL << PIN_MIC,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&mic_cfg));

    gpio_config_t buzzer_cfg = {
        .pin_bit_mask = 1ULL << PIN_BUZZER,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&buzzer_cfg));
    ESP_ERROR_CHECK(gpio_set_level(PIN_BUZZER, 0));

    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id = ADC_UNIT_1,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&unit_cfg, &s_sensors.adc1));

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_12,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_sensors.adc1, ADC_TMP36, &chan_cfg));
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_sensors.adc1, ADC_POT, &chan_cfg));

    s_sensors.temp_c_x10 = read_tmp36_x10();
    s_sensors.bpm = read_bpm_simulado();
    s_sensors.last_fast_read_us = esp_timer_get_time();
    s_sensors.last_temp_read_us = s_sensors.last_fast_read_us;
    s_sensors.last_buzzer_toggle_us = s_sensors.last_fast_read_us;
    s_sensors.last_payload_us = s_sensors.last_fast_read_us;
    update_alert_flags();
    update_buzzer_priority();

    ESP_LOGI(TAG, "Sensores listos: mic GPIO%d, buzzer GPIO%d, TMP36 GPIO34, BPM GPIO35",
             PIN_MIC, PIN_BUZZER);
}

void sensors_update(void)
{
    int64_t now_us = esp_timer_get_time();

    if (now_us - s_sensors.last_fast_read_us >= FAST_READ_INTERVAL_US) {
        s_sensors.bpm = read_bpm_simulado();
        s_sensors.sound_detected = gpio_get_level(PIN_MIC) == 0;
        if (s_sensors.sound_detected) {
            s_sensors.sound_alert_until_us = now_us + SOUND_ALERT_US;
        }

        if (now_us - s_sensors.last_temp_read_us >= TEMP_READ_INTERVAL_US) {
            s_sensors.temp_c_x10 = read_tmp36_x10();
            s_sensors.last_temp_read_us = now_us;
        }

        update_alert_flags();
        update_buzzer_priority();
        s_sensors.last_fast_read_us = now_us;
    }

    update_buzzer_output(now_us);
}

bool sensors_get_payload_if_due(sensor_payload_t *payload)
{
    int64_t now_us = esp_timer_get_time();
    if (!payload || now_us - s_sensors.last_payload_us < SENSOR_SEND_US) {
        return false;
    }

    s_sensors.last_payload_us = now_us;
    s_sensors.seq++;

    payload->magic = SENSOR_PAYLOAD_MAGIC;
    payload->version = SENSOR_PAYLOAD_VERSION;
    payload->size = sizeof(*payload);
    payload->seq = s_sensors.seq;
    payload->temp_c_x10 = s_sensors.temp_c_x10;
    payload->bpm = s_sensors.bpm;
    payload->sound_detected = s_sensors.sound_detected;
    payload->alert_flags = s_sensors.alert_flags;
    payload->buzzer_interval_ms = s_sensors.buzzer_interval_ms;
    payload->buzzer_on = s_sensors.buzzer_on;
    payload->reserved = 0;
    payload->uptime_ms = (uint32_t)(now_us / 1000);
    return true;
}
