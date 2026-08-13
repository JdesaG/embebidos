#include "sensors.h"

#include <string.h>

#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"

/*
 * Pines fisicos usados en la ESP32 emisora.
 * GPIO34 y GPIO35 son solo entrada, por eso son adecuados para ADC.
 */
#define PIN_MIC GPIO_NUM_27
#define PIN_BUZZER GPIO_NUM_26
#define ADC_TMP36 ADC_CHANNEL_6 /* GPIO34 en ESP32 */

#define TEMP_MIN_BEBE_X10 100
#define TEMP_MAX_BEBE_X10 300
#define FAST_READ_INTERVAL_US 100000
#define TEMP_READ_INTERVAL_US 2000000
#define SOUND_ALERT_US 3000000
#define SENSOR_SEND_US 1000000
#define ADC_FALLBACK_VREF_MV 3300
#define TMP36_OFFSET_MV 500
#define TMP36_TEMP_OFFSET_X10 0

static const char *TAG = "sensors";

typedef struct {
  adc_oneshot_unit_handle_t adc1;
  adc_cali_handle_t adc1_cali;
  bool adc1_cali_ready;
  uint32_t seq;
  int16_t temp_c_x10;
  uint16_t bpm;
  uint8_t sound_detected;
  uint8_t alert_flags;
  uint16_t buzzer_interval_ms;
  uint8_t buzzer_on;
  uint8_t buzzer_override;
  uint8_t buzzer_command_on;
  int64_t last_fast_read_us;
  int64_t last_temp_read_us;
  int64_t sound_alert_until_us;
  int64_t last_buzzer_toggle_us;
  int64_t last_payload_us;
} sensors_state_t;

static sensors_state_t s_sensors;

static int clamp_int(int value, int min_value, int max_value) {
  if (value < min_value) {
    return min_value;
  }
  if (value > max_value) {
    return max_value;
  }
  return value;
}

static int read_adc_raw(adc_channel_t channel) {
  int raw = 0;
  esp_err_t ret = adc_oneshot_read(s_sensors.adc1, channel, &raw);
  if (ret != ESP_OK) {
    ESP_LOGW(TAG, "No se pudo leer ADC canal %d: %s", channel,
             esp_err_to_name(ret));
    return 0;
  }
  return clamp_int(raw, 0, 4095);
}

static int read_adc_mv(adc_channel_t channel) {
  int raw = read_adc_raw(channel);
  int voltage_mv = 0;

  if (s_sensors.adc1_cali_ready &&
      adc_cali_raw_to_voltage(s_sensors.adc1_cali, raw, &voltage_mv) ==
          ESP_OK) {
    return voltage_mv;
  }

  /* Respaldo si la placa no soporta calibracion ADC: aproxima 0-4095 a 0-3.3 V.
   */
  return (raw * ADC_FALLBACK_VREF_MV) / 4095;
}

static int16_t read_tmp36_x10(void) {
  int voltage_mv = read_adc_mv(ADC_TMP36);

  /*
   * TMP36:
   *   500 mV equivalen a 0 C.
   *   Cada 10 mV equivalen a 1 C.
   *   temp_c_x10 = (mV - 500), porque 1 mV equivale a 0.1 C.
   * Guardamos C x10 para transmitir enteros y evitar floats en el payload.
   */
  return (int16_t)(voltage_mv - TMP36_OFFSET_MV + TMP36_TEMP_OFFSET_X10);
}

static void update_alert_flags(void) {
  uint8_t flags = 0;

  if (esp_timer_get_time() < s_sensors.sound_alert_until_us) {
    flags |= SENSOR_ALERT_SOUND;
  }
  if (s_sensors.temp_c_x10 > TEMP_MAX_BEBE_X10) {
    flags |= SENSOR_ALERT_TEMP_HIGH;
  }
  if (s_sensors.temp_c_x10 < TEMP_MIN_BEBE_X10) {
    flags |= SENSOR_ALERT_TEMP_LOW;
  }

  s_sensors.alert_flags = flags;
}

static void update_buzzer_priority(void) {
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

static void update_buzzer_output(int64_t now_us) {
  if (s_sensors.buzzer_override) {
    uint8_t requested = s_sensors.buzzer_command_on ? 1 : 0;
    if (s_sensors.buzzer_on != requested) {
      s_sensors.buzzer_on = requested;
      gpio_set_level(PIN_BUZZER, requested);
    }
    return;
  }
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

static void init_adc_calibration(void) {
#if ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
  adc_cali_line_fitting_config_t cali_config = {
      .unit_id = ADC_UNIT_1,
      .atten = ADC_ATTEN_DB_12,
      .bitwidth = ADC_BITWIDTH_12,
  };
  esp_err_t ret =
      adc_cali_create_scheme_line_fitting(&cali_config, &s_sensors.adc1_cali);
  s_sensors.adc1_cali_ready = (ret == ESP_OK);
  if (s_sensors.adc1_cali_ready) {
    ESP_LOGI(TAG, "Calibracion ADC activada para sensores analogicos");
  } else {
    ESP_LOGW(
        TAG,
        "ADC sin calibracion disponible: %s. Se usara conversion aproximada.",
        esp_err_to_name(ret));
  }
#else
  s_sensors.adc1_cali_ready = false;
  ESP_LOGW(TAG, "ADC calibration line fitting no soportado; se usara "
                "conversion aproximada.");
#endif
}

void sensors_init(void) {
  memset(&s_sensors, 0, sizeof(s_sensors));
  s_sensors.temp_c_x10 = 0;

  gpio_config_t mic_cfg = {
      .pin_bit_mask = 1ULL << PIN_MIC,
      .mode = GPIO_MODE_INPUT,
      .pull_up_en = GPIO_PULLUP_ENABLE,
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
  ESP_ERROR_CHECK(
      adc_oneshot_config_channel(s_sensors.adc1, ADC_TMP36, &chan_cfg));
  init_adc_calibration();

  s_sensors.temp_c_x10 = read_tmp36_x10();
  s_sensors.bpm = 0; /* No hay sensor BPM instalado; se conserva el campo. */
  s_sensors.last_fast_read_us = esp_timer_get_time();
  s_sensors.last_temp_read_us = s_sensors.last_fast_read_us;
  s_sensors.last_buzzer_toggle_us = s_sensors.last_fast_read_us;
  s_sensors.last_payload_us = s_sensors.last_fast_read_us;
  update_alert_flags();
  update_buzzer_priority();

  ESP_LOGI(
      TAG,
      "Sensores listos: sonido GPIO%d, buzzer activo 5 V GPIO%d, TMP36 GPIO34",
      PIN_MIC, PIN_BUZZER);
}

void sensors_update(void) {
  int64_t now_us = esp_timer_get_time();

  if (now_us - s_sensors.last_fast_read_us >= FAST_READ_INTERVAL_US) {
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

bool sensors_get_payload_if_due(sensor_payload_t *payload) {
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

void sensors_set_buzzer_override(bool enabled, bool on) {
  s_sensors.buzzer_override = enabled ? 1 : 0;
  s_sensors.buzzer_command_on = on ? 1 : 0;
  update_buzzer_output(esp_timer_get_time());
}

bool sensors_buzzer_is_on(void) {
  return s_sensors.buzzer_on != 0;
}
