/*
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nvs_flash.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "esp_csi_gain_ctrl.h"

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "lwip/inet.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"

#include "sensor_payload.h"

/*
 * Receptor CSI:
 *   csi_send --ESP-NOW--> csi_recv --UDP--> Mac/server.py
 *
 * La salida de datos ya no se imprime por serial. El puerto USB se conserva
 * únicamente para logs, flasheo y depuración.
 *
 * Configure SSID, password, host y puerto UDP con `idf.py menuconfig` en la sección
 * "CSI Receiver Network". No guardar la contraseña en el repositorio.
 */

#define CSI_WIRE_MAGIC 0x48495343u /* bytes: "CSIH" en little-endian */
#define CSI_WIRE_VERSION 1u
#define CSI_WIRE_FRAME_CSI 1u
#define CSI_WIRE_FRAME_SENSOR 2u
#define CSI_WIRE_FRAME_FLAGS 0u
#define CSI_DATA_MAX_LEN 512
#define SENSOR_PAYLOAD_OFFSET 15
#define CSI_UDP_MAX_DATAGRAM_SIZE 1200

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || \
    (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#define CONFIG_WIFI_BAND_MODE WIFI_BAND_MODE_2G_ONLY
#define CONFIG_WIFI_2G_BANDWIDTHS WIFI_BW_HT20
#define CONFIG_WIFI_5G_BANDWIDTHS WIFI_BW_HT20
#define CONFIG_WIFI_2G_PROTOCOL WIFI_PROTOCOL_11N
#define CONFIG_WIFI_5G_PROTOCOL WIFI_PROTOCOL_11N
#else
#define CONFIG_WIFI_BANDWIDTH WIFI_BW_HT20
#endif

#define CONFIG_ESP_NOW_PHYMODE WIFI_PHY_MODE_HT20
#define CONFIG_ESP_NOW_RATE WIFI_PHY_RATE_MCS0_LGI
#define CONFIG_FORCE_GAIN 0

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF 0
#endif

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 || \
    CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || \
    CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL 1
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

/* Debe coincidir solamente con la MAC configurada en csi_send. */
static const uint8_t CONFIG_CSI_SEND_MAC[] = {
    0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};

static const char *TAG = "csi_recv";
static EventGroupHandle_t s_wifi_event_group;
static QueueHandle_t s_transport_queue;
static const EventBits_t WIFI_GOT_IP_BIT = BIT0;
static uint8_t s_wifi_channel;

typedef struct {
    uint8_t type;
    uint32_t seq;
    uint32_t timestamp;
    int8_t rssi;
    int8_t noise_floor;
    uint8_t rate;
    uint8_t channel;
    uint16_t sig_len;
    uint16_t csi_len;
    uint8_t first_word_invalid;
    uint8_t format;
    uint8_t mac[6];
    int16_t csi[CSI_DATA_MAX_LEN];
    sensor_payload_t sensor;
} transport_frame_t;

/* Wire format is packed and little-endian, matching the ESP32 and Python host. */
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t version;
    uint8_t frame_count;
    uint16_t reserved;
} wire_batch_header_t;

typedef struct __attribute__((packed)) {
    uint8_t type;
    uint8_t flags;
    uint16_t frame_len;
    uint32_t seq;
} wire_frame_header_t;

typedef struct __attribute__((packed)) {
    uint32_t timestamp;
    int8_t rssi;
    int8_t noise_floor;
    uint8_t rate;
    uint8_t channel;
    uint16_t sig_len;
    uint16_t csi_len;
    uint8_t first_word_invalid;
    uint8_t format;
    uint8_t mac[6];
} wire_csi_body_t;

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    (void)arg;
    (void)event_data;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT &&
               event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_event_group, WIFI_GOT_IP_BIT);
        ESP_LOGW(TAG, "WiFi desconectado; reintentando");
        esp_wifi_connect();
    }
}

static void ip_event_handler(void *arg, esp_event_base_t event_base,
                             int32_t event_id, void *event_data)
{
    (void)arg;
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "IP obtenida: " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_GOT_IP_BIT);
    }
}

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *sta_netif = esp_netif_create_default_wifi_sta();
    if (sta_netif == NULL) {
        ESP_LOGE(TAG, "Failed to create default Wi-Fi STA interface");
        abort();
    }

    s_wifi_event_group = xEventGroupCreate();
    if (!s_wifi_event_group) {
        ESP_LOGE(TAG, "No se pudo crear el grupo de eventos WiFi");
        abort();
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               ip_event_handler, NULL));

    wifi_config_t wifi_config = {0};
    strncpy((char *)wifi_config.sta.ssid, CONFIG_CSI_WIFI_SSID,
            sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, CONFIG_CSI_WIFI_PASSWORD,
            sizeof(wifi_config.sta.password) - 1);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_OPEN;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

#if CONFIG_IDF_TARGET_ESP32C5
    ESP_ERROR_CHECK(esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE));
    wifi_protocols_t protocols = {
        .ghz_2g = CONFIG_WIFI_2G_PROTOCOL,
        .ghz_5g = CONFIG_WIFI_5G_PROTOCOL};
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {
        .ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS,
        .ghz_5g = CONFIG_WIFI_5G_BANDWIDTHS};
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#elif (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || \
    CONFIG_IDF_TARGET_ESP32C61
    ESP_ERROR_CHECK(esp_wifi_set_band_mode(CONFIG_WIFI_BAND_MODE));
    wifi_protocols_t protocols = {.ghz_2g = CONFIG_WIFI_2G_PROTOCOL};
    ESP_ERROR_CHECK(esp_wifi_set_protocols(ESP_IF_WIFI_STA, &protocols));
    wifi_bandwidths_t bandwidth = {.ghz_2g = CONFIG_WIFI_2G_BANDWIDTHS};
    ESP_ERROR_CHECK(esp_wifi_set_bandwidths(ESP_IF_WIFI_STA, &bandwidth));
#else
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(ESP_IF_WIFI_STA,
                                           CONFIG_WIFI_BANDWIDTH));
#endif

    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static bool wifi_wait_for_ip(void)
{
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group, WIFI_GOT_IP_BIT,
                                           pdFALSE, pdFALSE, pdMS_TO_TICKS(30000));
    if ((bits & WIFI_GOT_IP_BIT) == 0) {
        ESP_LOGE(TAG, "No se obtuvo IP en 30 segundos");
        return false;
    }

    uint8_t primary = 0;
    wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
    ESP_ERROR_CHECK(esp_wifi_get_channel(&primary, &secondary));
    s_wifi_channel = primary;
    ESP_LOGI(TAG, "Canal WiFi actual: %u; ESP-NOW usara este canal",
             s_wifi_channel);
    return true;
}

static void wifi_esp_now_init(esp_now_peer_info_t peer)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    esp_now_rate_config_t rate_config = {
        .phymode = CONFIG_ESP_NOW_PHYMODE,
        .rate = CONFIG_ESP_NOW_RATE,
        .ersu = false,
        .dcm = false};
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));
}

static bool try_queue_sensor_frame(const wifi_csi_info_t *info)
{
    if (!info || !info->payload ||
        info->payload_len < SENSOR_PAYLOAD_OFFSET + sizeof(uint32_t)) {
        return false;
    }

    uint32_t magic = 0;
    memcpy(&magic, info->payload + SENSOR_PAYLOAD_OFFSET, sizeof(magic));
    if (magic != SENSOR_PAYLOAD_MAGIC) {
        return false;
    }

    if (info->payload_len < SENSOR_PAYLOAD_OFFSET + sizeof(sensor_payload_t)) {
        ESP_LOGW(TAG, "SENSOR_DATA truncado: payload_len=%u", info->payload_len);
        return true;
    }

    transport_frame_t frame = {0};
    memcpy(&frame.sensor, info->payload + SENSOR_PAYLOAD_OFFSET,
           sizeof(frame.sensor));
    if (frame.sensor.version != SENSOR_PAYLOAD_VERSION ||
        frame.sensor.size != sizeof(sensor_payload_t)) {
        ESP_LOGW(TAG, "SENSOR_DATA invalido: version=%u size=%u",
                 frame.sensor.version, frame.sensor.size);
        return true;
    }
    frame.type = CSI_WIRE_FRAME_SENSOR;
    frame.seq = frame.sensor.seq;
    if (xQueueSend(s_transport_queue, &frame, 0) != pdTRUE) {
        ESP_LOGW(TAG, "Cola de transporte llena; SENSOR_DATA descartado");
    }
    return true;
}

static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    (void)ctx;
    if (!info || !info->buf || info->len == 0) {
        return;
    }
    if (memcmp(info->mac, CONFIG_CSI_SEND_MAC, 6) != 0) {
        return;
    }
    if (try_queue_sensor_frame(info)) {
        return;
    }

    if (info->len > CSI_DATA_MAX_LEN) {
        ESP_LOGW(TAG, "CSI demasiado grande: len=%u", info->len);
        return;
    }

    transport_frame_t frame = {0};
    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;
    frame.type = CSI_WIRE_FRAME_CSI;
    frame.timestamp = rx_ctrl->timestamp;
    frame.rssi = rx_ctrl->rssi;
    frame.noise_floor = rx_ctrl->noise_floor;
    frame.rate = rx_ctrl->rate;
    frame.channel = rx_ctrl->channel;
    frame.sig_len = rx_ctrl->sig_len;
    frame.csi_len = info->len;
    frame.first_word_invalid = info->first_word_invalid ? 1 : 0;
    frame.format = 0;
    memcpy(frame.mac, info->mac, sizeof(frame.mac));

    uint32_t rx_id = info->rx_seq;
    if (info->payload && info->payload_len >= SENSOR_PAYLOAD_OFFSET + sizeof(rx_id)) {
        memcpy(&rx_id, info->payload + SENSOR_PAYLOAD_OFFSET, sizeof(rx_id));
    }
    frame.seq = rx_id;

    float compensate_gain = 1.0f;
#if CONFIG_GAIN_CONTROL
    uint8_t agc_gain = 0;
    int8_t fft_gain = 0;
    static uint8_t agc_gain_baseline = 0;
    static int8_t fft_gain_baseline = 0;
    static int gain_count = 0;
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (gain_count < 100) {
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    } else if (gain_count == 100) {
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline,
                                               &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
#endif
    }
    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain,
                                            fft_gain);
    gain_count++;
#endif

#if (CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61) && CSI_FORCE_LLTF
    if (info->len < 2 || ((info->len - 2) / 2) > CSI_DATA_MAX_LEN) {
        return;
    }
    frame.csi_len = (info->len - 2) / 2;
    for (uint16_t i = 0; i < frame.csi_len; ++i) {
        uint16_t offset = (uint16_t)(i * 2);
        int16_t value = (int16_t)(((((uint16_t)info->buf[offset + 1]) << 8) |
                                   (uint8_t)info->buf[offset]) << 4) >> 4;
        frame.csi[i] = (int16_t)(compensate_gain * value);
    }
#else
    for (uint16_t i = 0; i < frame.csi_len; ++i) {
        frame.csi[i] = (int16_t)(compensate_gain * info->buf[i]);
    }
#endif

    if (xQueueSend(s_transport_queue, &frame, 0) != pdTRUE) {
        static uint32_t dropped = 0;
        dropped++;
        if ((dropped % 100) == 0) {
            ESP_LOGW(TAG, "Cola de transporte llena; CSI descartado (%lu acumulados)",
                     (unsigned long)dropped);
        }
    }
}

static void wifi_csi_init(void)
{
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    wifi_csi_config_t csi_config = {
        .enable = true,
        .acquire_csi_legacy = false,
        .acquire_csi_force_lltf = CSI_FORCE_LLTF,
        .acquire_csi_ht20 = true,
        .acquire_csi_ht40 = true,
        .acquire_csi_vht = false,
        .acquire_csi_su = false,
        .acquire_csi_mu = false,
        .acquire_csi_dcm = false,
        .acquire_csi_beamformed = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg = 0,
        .dump_ack_en = false,
        .reserved = false};
#elif CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable = true,
        .acquire_csi_legacy = false,
        .acquire_csi_ht20 = true,
        .acquire_csi_ht40 = true,
        .acquire_csi_su = true,
        .acquire_csi_mu = true,
        .acquire_csi_dcm = true,
        .acquire_csi_beamformed = true,
        .acquire_csi_he_stbc = 2,
        .val_scale_cfg = false,
        .dump_ack_en = false,
        .reserved = false};
#else
    wifi_csi_config_t csi_config = {
        .lltf_en = true,
        .htltf_en = true,
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,
        .channel_filter_en = true,
        .manu_scale = false,
        .shift = false};
#endif

    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static size_t append_wire_frame(uint8_t *body, size_t offset,
                                const transport_frame_t *frame)
{
    wire_frame_header_t header = {
        .type = frame->type,
        .flags = CSI_WIRE_FRAME_FLAGS,
        .frame_len = 0,
        .seq = frame->seq};
    size_t body_len = 0;
    size_t body_start = offset + sizeof(header);

    if (frame->type == CSI_WIRE_FRAME_CSI) {
        wire_csi_body_t csi_body = {
            .timestamp = frame->timestamp,
            .rssi = frame->rssi,
            .noise_floor = frame->noise_floor,
            .rate = frame->rate,
            .channel = frame->channel,
            .sig_len = frame->sig_len,
            .csi_len = frame->csi_len,
            .first_word_invalid = frame->first_word_invalid,
            .format = frame->format};
        memcpy(csi_body.mac, frame->mac, sizeof(csi_body.mac));
        memcpy(body + body_start, &csi_body, sizeof(csi_body));
        memcpy(body + body_start + sizeof(csi_body), frame->csi,
               frame->csi_len * sizeof(frame->csi[0]));
        body_len = sizeof(csi_body) + frame->csi_len * sizeof(frame->csi[0]);
    } else if (frame->type == CSI_WIRE_FRAME_SENSOR) {
        memcpy(body + body_start, &frame->sensor, sizeof(frame->sensor));
        body_len = sizeof(frame->sensor);
    }

    header.frame_len = sizeof(header) + body_len;
    memcpy(body + offset, &header, sizeof(header));
    return offset + sizeof(header) + body_len;
}

static bool resolve_udp_destination(struct sockaddr_in *destination)
{
    struct addrinfo hints = {0};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    struct addrinfo *result = NULL;
    int err = getaddrinfo(CONFIG_CSI_UDP_HOST, NULL, &hints, &result);
    if (err != 0 || result == NULL) {
        ESP_LOGW(TAG, "No se pudo resolver destino UDP %s: %d",
                 CONFIG_CSI_UDP_HOST, err);
        return false;
    }

    memset(destination, 0, sizeof(*destination));
    memcpy(destination, result->ai_addr, sizeof(*destination));
    destination->sin_port = htons(CONFIG_CSI_UDP_PORT);
    freeaddrinfo(result);
    ESP_LOGI(TAG, "Destino UDP: %s:%d", CONFIG_CSI_UDP_HOST,
             CONFIG_CSI_UDP_PORT);
    return true;
}

static void udp_sender_task(void *arg)
{
    (void)arg;
    const size_t max_body = sizeof(wire_batch_header_t) +
                            sizeof(wire_frame_header_t) +
                            sizeof(wire_csi_body_t) +
                            CSI_DATA_MAX_LEN * sizeof(int16_t);
    uint8_t *body = malloc(max_body);
    transport_frame_t *frame = malloc(sizeof(transport_frame_t));
    if (!body || !frame) {
        ESP_LOGE(TAG, "No hay memoria para el emisor UDP");
        free(body);
        free(frame);
        vTaskDelete(NULL);
        return;
    }

    int sock = -1;
    struct sockaddr_in destination = {0};

    while (true) {
        if ((xEventGroupGetBits(s_wifi_event_group) & WIFI_GOT_IP_BIT) == 0) {
            if (sock >= 0) {
                close(sock);
                sock = -1;
            }
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        if (sock < 0) {
            if (!resolve_udp_destination(&destination)) {
                vTaskDelay(pdMS_TO_TICKS(1000));
                continue;
            }
            sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
            if (sock < 0) {
                ESP_LOGW(TAG, "No se pudo crear socket UDP");
                vTaskDelay(pdMS_TO_TICKS(1000));
                continue;
            }
        }

        if (xQueueReceive(s_transport_queue, frame, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        wire_batch_header_t batch = {
            .magic = CSI_WIRE_MAGIC,
            .version = CSI_WIRE_VERSION,
            .frame_count = 1,
            .reserved = 0};
        memcpy(body, &batch, sizeof(batch));
        size_t body_len = sizeof(batch);
        body_len = append_wire_frame(body, body_len, frame);

        int sent = sendto(sock, body, body_len, 0,
                          (struct sockaddr *)&destination,
                          sizeof(destination));
        if (sent < 0) {
            ESP_LOGW(TAG, "Envio UDP fallo; recreando socket");
            close(sock);
            sock = -1;
        }
    }
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init();
    while (!wifi_wait_for_ip()) {
        ESP_LOGW(TAG, "Esperando una conexion WiFi valida para CSI/ESP-NOW");
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    esp_now_peer_info_t peer = {
        /* 0: usa el canal actual de la interfaz STA asociada al router. */
        .channel = 0,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff}};
    wifi_esp_now_init(peer);

    s_transport_queue = xQueueCreate(CONFIG_CSI_UDP_QUEUE_LENGTH,
                                     sizeof(transport_frame_t));
    if (!s_transport_queue) {
        ESP_LOGE(TAG, "No se pudo crear la cola de transporte");
        abort();
    }
    xTaskCreate(udp_sender_task, "udp_sender", 8192, NULL, 5, NULL);

    wifi_csi_init();
    ESP_LOGI(TAG, "CSI activo; enviando datagramas UDP a %s:%d",
             CONFIG_CSI_UDP_HOST, CONFIG_CSI_UDP_PORT);
}
