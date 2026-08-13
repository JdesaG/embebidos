/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */
/* Ejemplo inicial

   Este codigo de ejemplo esta en dominio publico o bajo licencia CC0.

   Salvo que la ley lo exija o se acuerde por escrito, este software se
   distribuye "TAL CUAL", sin garantias de ningun tipo.
*/
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "nvs_flash.h"

#include "esp_mac.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_pm.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "device_protocol.h"
#include "lights.h"
#include "sensors.h"

/*
 * Este archivo es el firmware de la ESP32 emisora.
 *
 * Lenguaje: C, no C++.
 * Rol en el proyecto:
 *   1. Configura la radio WiFi.
 *   2. Encuentra automáticamente el canal del gateway, sin guardar Wi-Fi.
 *   3. Usa ESP-NOW para transmitir paquetes con frecuencia según el modo.
 *   4. Esos paquetes viajan por el aire y son recibidos por csi_recv.
 *   5. El receptor, no este emisor, extrae el CSI de los paquetes recibidos.
 *
 * Si cambias este archivo, flashealo solo en la placa emisora.
 */

/* Configuracion de radio WiFi dependiente del chip objetivo.
 * HT40 usa un canal mas ancho que HT20 y normalmente produce mas subportadoras
 * CSI. Si se cambia a HT20, el vector CSI puede ser mas corto y el parser o
 * modelo en Python puede ver una forma de senal distinta.
 */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#define CONFIG_WIFI_BAND_MODE   WIFI_BAND_MODE_2G_ONLY
#define CONFIG_WIFI_2G_BANDWIDTHS           WIFI_BW_HT20
#define CONFIG_WIFI_5G_BANDWIDTHS           WIFI_BW_HT20
#define CONFIG_WIFI_2G_PROTOCOL             WIFI_PROTOCOL_11N
#define CONFIG_WIFI_5G_PROTOCOL             WIFI_PROTOCOL_11N
#else
#define CONFIG_WIFI_BANDWIDTH           WIFI_BW_HT20
#endif

/* Modo fisico y tasa de ESP-NOW.
 * Deben coincidir con el receptor. Cambiarlos modifica como se envian los
 * paquetes por WiFi y puede afectar la estabilidad del CSI, la perdida de
 * paquetes y la frecuencia de muestreo.
 */
#define CONFIG_ESP_NOW_PHYMODE           WIFI_PHY_MODE_HT20
#define CONFIG_ESP_NOW_RATE             WIFI_PHY_RATE_MCS0_LGI
/* Paquetes enviados por segundo. El receptor solo puede producir CSI cuando
 * llegan paquetes. Aumentarlo da mas muestras, pero puede congestionar los
 * buffers de ESP-NOW. Reducirlo da menos muestras CSI y puede empeorar la
 * deteccion de respiracion.
 */
#define CONFIG_SEND_FREQUENCY               100

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

/* Direccion MAC fija usada por el emisor.
 * El receptor filtra paquetes usando exactamente esta MAC. Si la cambias aqui,
 * cambia tambien CONFIG_CSI_SEND_MAC en csi_recv/main/app_main.c.
 */
static const uint8_t CONFIG_CSI_SEND_MAC[] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};

/* Etiqueta de log que aparece en el monitor de ESP-IDF. */
static const char *TAG = "csi_send";

/* La emisora no entra al router: recorre los canales hasta recibir la respuesta
 * del gateway. Así solamente csi_recv necesita configuración Wi-Fi. */
static EventGroupHandle_t s_pair_events;
static QueueHandle_t s_command_queue;
static const EventBits_t GATEWAY_FOUND_BIT = BIT0;
static uint8_t s_gateway_channel;
static uint8_t s_energy_mode = DEVICE_ENERGY_MONITORING;
static uint8_t s_broadcast_peer[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static uint32_t s_message_seq;
static volatile uint32_t s_pair_sync_count;
static volatile uint32_t s_command_rx_count;

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    if (esp_netif_create_default_wifi_sta() == NULL) {
        ESP_LOGE(TAG, "No se pudo crear la interfaz STA");
        abort();
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

#if CONFIG_IDF_TARGET_ESP32C5
    /* Los chips C5/C6/C61 usan APIs nuevas multibanda. */
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
        .ghz_5g = CONFIG_WIFI_5G_PROTOCOL
    };
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
        .ghz_5g = CONFIG_WIFI_5G_BANDWIDTHS
    };
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#elif (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || CONFIG_IDF_TARGET_ESP32C61
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE);
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
    };
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
    };
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#else
    /* Ruta para ESP32/ESP32-S3/C3 clasicos: configura el ancho de banda con la API anterior. */
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, CONFIG_WIFI_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_start());

#endif

    /* Desactiva el ahorro de energia WiFi. Si se activa, el tiempo entre paquetes
     * puede ser menos estable y el muestreo CSI puede volverse irregular.
     */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    /* Aplica la MAC fija del emisor para que el receptor pueda identificar esta placa. */
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CONFIG_CSI_SEND_MAC));
}

/* Inicializa ESP-NOW y registra el peer que recibira paquetes.
 * La direccion peer es broadcast en app_main, por lo que cualquier receptor
 * en el mismo canal puede escuchar los paquetes.
 */
static void wifi_esp_now_init(esp_now_peer_info_t peer)
{
    /* Inicia la pila del protocolo ESP-NOW. */
    ESP_ERROR_CHECK(esp_now_init());

    /* Clave maestra primaria. ESP-NOW la requiere durante la configuracion aunque
     * el cifrado del peer este desactivado en este ejemplo.
     */
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));

    /* Agrega el peer/broadcast para que esp_now_send tenga permiso de enviar. */
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    /* Configura el modo fisico WiFi y la tasa usada por las tramas ESP-NOW. */
    esp_now_rate_config_t rate_config = {
        .phymode = CONFIG_ESP_NOW_PHYMODE,
        .rate = CONFIG_ESP_NOW_RATE,
        .ersu = false,
        .dcm = false
    };
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));
}

static void esp_now_receive_cb(const esp_now_recv_info_t *receive_info,
                               const uint8_t *data, int data_len)
{
    (void)receive_info;
    if (!data || data_len != sizeof(device_message_t)) return;
    const device_message_t *message = (const device_message_t *)data;
    if (!device_message_valid(message)) return;

    if (message->type == DEVICE_MSG_PAIR_SYNC && message->value[0] >= 1 &&
        message->value[0] <= 13) {
        s_pair_sync_count++;
        s_gateway_channel = message->value[0];
        xEventGroupSetBits(s_pair_events, GATEWAY_FOUND_BIT);
    } else if (message->type == DEVICE_MSG_ACTUATOR_COMMAND) {
        s_command_rx_count++;
        xQueueOverwrite(s_command_queue, message);
    }
}

static void pair_with_gateway(void)
{
    ESP_LOGI(TAG, "Buscando gateway en canales 1-13");
    while ((xEventGroupGetBits(s_pair_events) & GATEWAY_FOUND_BIT) == 0) {
        for (uint8_t channel = 1; channel <= 13; ++channel) {
            ESP_ERROR_CHECK(esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE));
            device_message_t hello;
            device_message_init(&hello, DEVICE_MSG_PAIR_HELLO, ++s_message_seq);
            hello.value[0] = channel;
            hello.uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);
            esp_now_send(s_broadcast_peer, (const uint8_t *)&hello, sizeof(hello));
            EventBits_t bits = xEventGroupWaitBits(
                s_pair_events, GATEWAY_FOUND_BIT, pdFALSE, pdFALSE,
                pdMS_TO_TICKS(250));
            if (bits & GATEWAY_FOUND_BIT) {
                ESP_ERROR_CHECK(esp_wifi_set_channel(s_gateway_channel,
                                                     WIFI_SECOND_CHAN_NONE));
                ESP_LOGI(TAG, "Gateway encontrado en canal %u", s_gateway_channel);
                return;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

static void apply_actuator_command(const device_message_t *command)
{
    bool light_on = command->value[DEVICE_VALUE_LIGHT_ON] != 0;
    uint8_t brightness = command->value[DEVICE_VALUE_BRIGHTNESS];
    uint8_t red = command->value[DEVICE_VALUE_RED];
    uint8_t green = command->value[DEVICE_VALUE_GREEN];
    uint8_t blue = command->value[DEVICE_VALUE_BLUE];
    bool buzzer_override = command->value[DEVICE_VALUE_BUZZER_OVERRIDE] != 0;
    bool buzzer_on = command->value[DEVICE_VALUE_BUZZER_ON] != 0;
    uint8_t energy_mode = command->value[DEVICE_VALUE_ENERGY_MODE];
    if (energy_mode > DEVICE_ENERGY_STANDBY) {
        energy_mode = DEVICE_ENERGY_MONITORING;
    }
    s_energy_mode = energy_mode;
    sensors_set_buzzer_override(buzzer_override, buzzer_on);
    if (s_energy_mode == DEVICE_ENERGY_STANDBY) {
        lights_off();
    } else {
        lights_set(light_on, brightness, red, green, blue);
    }

    device_message_t state = *command;
    state.type = DEVICE_MSG_ACTUATOR_STATE;
    state.value[DEVICE_VALUE_LIGHT_ON] =
        (light_on && s_energy_mode != DEVICE_ENERGY_STANDBY) ? 1 : 0;
    state.value[DEVICE_VALUE_BUZZER_ON] = sensors_buzzer_is_on() ? 1 : 0;
    state.uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);
    esp_now_send(s_broadcast_peer, (const uint8_t *)&state, sizeof(state));
}

static TickType_t send_period_ticks(void)
{
    uint32_t frequency = CONFIG_SEND_FREQUENCY;
    if (s_energy_mode == DEVICE_ENERGY_ECO) frequency = 50;
    if (s_energy_mode == DEVICE_ENERGY_STANDBY) frequency = 1;
    TickType_t ticks = pdMS_TO_TICKS(1000 / frequency);
    return ticks ? ticks : 1;
}

static void configure_power_management(void)
{
#if CONFIG_PM_ENABLE
    esp_pm_config_t config = {
        .max_freq_mhz = 160,
        .min_freq_mhz = 80,
        /* El monitoreo CSI requiere temporizacion estable; standby baja la tasa
         * de paquetes, pero no entra a deep sleep para evitar que la power bank
         * se apague por carga insuficiente. */
        .light_sleep_enable = false,
    };
    ESP_ERROR_CHECK(esp_pm_configure(&config));
    ESP_LOGI(TAG, "DFS activo: CPU dinamica 80-160 MHz");
#else
    ESP_LOGW(TAG, "Power management no esta habilitado en sdkconfig");
#endif
}

/* Punto de entrada de ESP-IDF. Es equivalente a main() en un programa C normal. */
void app_main()
{
    /**
     * @brief Inicializa NVS
     */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* Si NVS tiene un estado antiguo incompatible, se borra e inicializa otra vez. */
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    configure_power_management();

    /**
     * @brief Inicializa WiFi
     */
    wifi_init();
    s_pair_events = xEventGroupCreate();
    s_command_queue = xQueueCreate(1, sizeof(device_message_t));
    if (!s_pair_events || !s_command_queue) abort();

    /**
     * @brief Inicializa ESP-NOW
     *        Documentacion del protocolo ESP-NOW:
     *        https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html
     */
    esp_now_peer_info_t peer = {
        .channel   = 0,
        .ifidx     = WIFI_IF_STA,
        .encrypt   = false,
        /* MAC broadcast: envia paquetes para que el receptor los escuche sin conocer
         * su direccion MAC exacta.
         */
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };
    wifi_esp_now_init(peer);
    ESP_ERROR_CHECK(esp_now_register_recv_cb(esp_now_receive_cb));
    pair_with_gateway();
    sensors_init();
    lights_init();

    ESP_LOGI(TAG, "================ CSI SEND ================");
    ESP_LOGI(TAG, "send_frequency: %d, mac: " MACSTR,
             CONFIG_SEND_FREQUENCY, MAC2STR(CONFIG_CSI_SEND_MAC));
    ESP_LOGI(TAG, "Sensores activos: SENSOR_DATA se enviara al receptor cada 1 s");

    /* Bucle infinito: envia continuamente un contador.
     * El receptor usa la llegada de estos paquetes para generar CSI.
     */
    TickType_t last_wake = xTaskGetTickCount();
    int64_t last_diagnostic_us = esp_timer_get_time();
    uint32_t sent_ok = 0;
    uint32_t send_errors = 0;
    uint32_t sensors_sent = 0;
    for (uint32_t count = 0; ; ++count) {
        device_message_t command;
        if (xQueueReceive(s_command_queue, &command, 0) == pdTRUE) {
            apply_actuator_command(&command);
        }
        sensors_update();

        /* Envia el contador actual como payload. Es pequeno; lo importante no es el
         * contenido sino la trama WiFi en si.
         */
        esp_err_t ret = esp_now_send(peer.peer_addr, (const uint8_t *)&count, sizeof(count));
        if (ret != ESP_OK) {
            send_errors++;
            /* Si aparece ESP_ERR_ESPNOW_NO_MEM, la tasa de envio o el canal pueden estar
             * muy congestionados. Baja CONFIG_SEND_FREQUENCY o cambia de canal.
             */
            ESP_LOGW(TAG, "free_heap: %ld <%s> ESP-NOW send error", esp_get_free_heap_size(), esp_err_to_name(ret));
        } else {
            sent_ok++;
        }

        sensor_payload_t sensor_payload;
        if (sensors_get_payload_if_due(&sensor_payload)) {
            esp_err_t sensor_ret = esp_now_send(peer.peer_addr, (const uint8_t *)&sensor_payload, sizeof(sensor_payload));
            if (sensor_ret != ESP_OK) {
                send_errors++;
                ESP_LOGW(TAG, "free_heap: %ld <%s> ESP-NOW sensor send error",
                         esp_get_free_heap_size(), esp_err_to_name(sensor_ret));
            } else {
                sensors_sent++;
            }
        }

        int64_t now_us = esp_timer_get_time();
        if (now_us - last_diagnostic_us >= 10000000) {
            ESP_LOGI(TAG,
                     "DIAG gateway=OK canal=%u pair_sync=%lu ESP-NOW_tx=%lu "
                     "sensor_tx=%lu comandos_rx=%lu errores=%lu modo=%u heap=%lu",
                     (unsigned)s_gateway_channel,
                     (unsigned long)s_pair_sync_count,
                     (unsigned long)sent_ok,
                     (unsigned long)sensors_sent,
                     (unsigned long)s_command_rx_count,
                     (unsigned long)send_errors, (unsigned)s_energy_mode,
                     (unsigned long)esp_get_free_heap_size());
            last_diagnostic_us = now_us;
        }

        /* Bloquea la tarea en lugar de ocupar CPU. ECO y standby reducen la tasa. */
        vTaskDelayUntil(&last_wake, send_period_ticks());
    }
}
