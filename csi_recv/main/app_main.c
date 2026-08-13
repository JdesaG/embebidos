/*
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nvs_flash.h"

#include "esp_log.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_csi_gain_ctrl.h"

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "lwip/inet.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"

#include "device_protocol.h"
#include "sensor_payload.h"
#include "wifi_manager.h"

/*
 * Receptor CSI:
 *   csi_send --ESP-NOW--> csi_recv --UDP--> Mac/server.py
 *
 * La salida de datos ya no se imprime por serial. El puerto USB se conserva
 * únicamente para logs, flasheo y depuración.
 *
 * El gateway configura hasta dos redes desde su portal local y descubre el
 * servidor automáticamente por UDP; no hay IP ni contraseña fija en firmware.
 */

#define CSI_WIRE_MAGIC 0x48495343u /* bytes: "CSIH" en little-endian */
#define CSI_WIRE_VERSION 1u
#define CSI_WIRE_FRAME_CSI 1u
#define CSI_WIRE_FRAME_SENSOR 2u
#define CSI_WIRE_FRAME_ACTUATOR 3u
#define CSI_WIRE_FRAME_FLAGS 0u
#define CSI_DATA_MAX_LEN 512
#define SENSOR_PAYLOAD_OFFSET 15
#define CSI_UDP_MAX_DATAGRAM_SIZE 1200
#define CSI_DISCOVERY_PORT 5001
#define CSI_COMMAND_PORT 5002
#define SERVER_FOUND_BIT BIT0

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
static EventGroupHandle_t s_network_events;
static QueueHandle_t s_transport_queue;
static SemaphoreHandle_t s_server_mutex;
static uint8_t s_wifi_channel;
static struct sockaddr_in s_server_destination;
static uint8_t s_broadcast_peer[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static volatile uint32_t s_pair_hello_count;
static volatile uint32_t s_csi_rx_count;
static volatile uint32_t s_sensor_rx_count;
static volatile uint32_t s_actuator_rx_count;
static volatile uint32_t s_queue_drop_count;
static volatile uint32_t s_udp_sent_count;
static volatile uint32_t s_udp_error_count;
static volatile uint32_t s_discovery_sent_count;
static volatile uint32_t s_discovery_reply_count;

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
    device_message_t device;
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

static void esp_now_receive_cb(const esp_now_recv_info_t *receive_info,
                               const uint8_t *data, int data_len)
{
    (void)receive_info;
    if (!data || data_len != sizeof(device_message_t)) return;
    const device_message_t *message = (const device_message_t *)data;
    if (!device_message_valid(message) ||
        message->type != DEVICE_MSG_PAIR_HELLO) return;

    s_pair_hello_count++;
    if (s_wifi_channel < 1 || s_wifi_channel > 13) {
        ESP_LOGE(TAG, "No se responde al emisor: canal Wi-Fi invalido=%u",
                 (unsigned)s_wifi_channel);
        return;
    }
    device_message_t reply;
    device_message_init(&reply, DEVICE_MSG_PAIR_SYNC, message->seq);
    reply.value[0] = s_wifi_channel;
    reply.uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);
    esp_now_send(s_broadcast_peer, (const uint8_t *)&reply, sizeof(reply));
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
        s_queue_drop_count++;
        ESP_LOGW(TAG, "Cola de transporte llena; SENSOR_DATA descartado");
    } else {
        s_sensor_rx_count++;
    }
    return true;
}

static bool try_queue_device_frame(const wifi_csi_info_t *info)
{
    if (!info || !info->payload ||
        info->payload_len < SENSOR_PAYLOAD_OFFSET + sizeof(device_message_t)) {
        return false;
    }
    device_message_t message;
    memcpy(&message, info->payload + SENSOR_PAYLOAD_OFFSET, sizeof(message));
    if (!device_message_valid(&message)) return false;

    /* Los mensajes de emparejamiento no forman parte del dataset CSI. */
    if (message.type == DEVICE_MSG_PAIR_HELLO ||
        message.type == DEVICE_MSG_PAIR_SYNC ||
        message.type == DEVICE_MSG_ACTUATOR_COMMAND) {
        return true;
    }
    if (message.type != DEVICE_MSG_ACTUATOR_STATE) return true;

    transport_frame_t frame = {0};
    frame.type = CSI_WIRE_FRAME_ACTUATOR;
    frame.seq = message.seq;
    frame.device = message;
    if (xQueueSend(s_transport_queue, &frame, 0) != pdTRUE) {
        s_queue_drop_count++;
        ESP_LOGW(TAG, "Cola llena; estado de actuadores descartado");
    } else {
        s_actuator_rx_count++;
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
    if (try_queue_device_frame(info)) {
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
        s_queue_drop_count++;
        if ((s_queue_drop_count % 100) == 0) {
            ESP_LOGW(TAG, "Cola de transporte llena; CSI descartado (%lu acumulados)",
                     (unsigned long)s_queue_drop_count);
        }
    } else {
        s_csi_rx_count++;
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
    } else if (frame->type == CSI_WIRE_FRAME_ACTUATOR) {
        memcpy(body + body_start, &frame->device, sizeof(frame->device));
        body_len = sizeof(frame->device);
    }

    header.frame_len = sizeof(header) + body_len;
    memcpy(body + offset, &header, sizeof(header));
    return offset + sizeof(header) + body_len;
}

static bool copy_server_destination(struct sockaddr_in *destination)
{
    if ((xEventGroupGetBits(s_network_events) & SERVER_FOUND_BIT) == 0) {
        return false;
    }
    if (xSemaphoreTake(s_server_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return false;
    }
    *destination = s_server_destination;
    xSemaphoreGive(s_server_mutex);
    return destination->sin_addr.s_addr != 0;
}

static bool command_from_current_server(const struct sockaddr_in *source)
{
    if (!source ||
        (xEventGroupGetBits(s_network_events) & SERVER_FOUND_BIT) == 0) {
        return false;
    }
    if (xSemaphoreTake(s_server_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return false;
    }
    bool matches = source->sin_addr.s_addr ==
                   s_server_destination.sin_addr.s_addr;
    xSemaphoreGive(s_server_mutex);
    return matches;
}

static void discovery_and_command_task(void *arg)
{
    (void)arg;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) {
        ESP_LOGE(TAG, "No se pudo crear socket de descubrimiento");
        vTaskDelete(NULL);
        return;
    }
    int enabled = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &enabled, sizeof(enabled));
    struct timeval timeout = {.tv_sec = 1, .tv_usec = 0};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    struct sockaddr_in local = {
        .sin_family = AF_INET,
        .sin_port = htons(CSI_COMMAND_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(sock, (struct sockaddr *)&local, sizeof(local)) < 0) {
        ESP_LOGE(TAG, "No se pudo abrir puerto de comandos %d", CSI_COMMAND_PORT);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    struct sockaddr_in broadcast = {
        .sin_family = AF_INET,
        .sin_port = htons(CSI_DISCOVERY_PORT),
        .sin_addr.s_addr = htonl(INADDR_BROADCAST),
    };
    int64_t last_discovery_us = 0;
    uint32_t discovery_seq = 0;

    while (true) {
        int64_t now_us = esp_timer_get_time();
        if (now_us - last_discovery_us >= 5000000) {
            device_message_t discovery;
            device_message_init(&discovery, DEVICE_MSG_SERVER_DISCOVERY,
                                ++discovery_seq);
            discovery.value[0] = CSI_COMMAND_PORT & 0xff;
            discovery.value[1] = (CSI_COMMAND_PORT >> 8) & 0xff;
            discovery.uptime_ms = (uint32_t)(now_us / 1000);
            int sent = sendto(sock, &discovery, sizeof(discovery), 0,
                              (struct sockaddr *)&broadcast,
                              sizeof(broadcast));
            if (sent == sizeof(discovery)) {
                s_discovery_sent_count++;
                if (s_discovery_sent_count == 1 ||
                    (s_discovery_sent_count % 6) == 0) {
                    ESP_LOGI(TAG,
                             "Buscando servidor: broadcast UDP 255.255.255.255:%d intento=%lu",
                             CSI_DISCOVERY_PORT,
                             (unsigned long)s_discovery_sent_count);
                }
            } else {
                s_udp_error_count++;
                ESP_LOGW(TAG, "Fallo al enviar descubrimiento UDP");
            }
            last_discovery_us = now_us;
        }

        device_message_t message;
        struct sockaddr_in source = {0};
        socklen_t source_len = sizeof(source);
        int received = recvfrom(sock, &message, sizeof(message), 0,
                                (struct sockaddr *)&source, &source_len);
        if (received != sizeof(message) || !device_message_valid(&message)) {
            continue;
        }
        if (message.type == DEVICE_MSG_SERVER_REPLY) {
            uint16_t port = (uint16_t)message.value[0] |
                            ((uint16_t)message.value[1] << 8);
            source.sin_port = htons(port ? port : CONFIG_CSI_UDP_PORT);
            if (xSemaphoreTake(s_server_mutex, pdMS_TO_TICKS(100)) == pdTRUE) {
                s_server_destination = source;
                xSemaphoreGive(s_server_mutex);
                xEventGroupSetBits(s_network_events, SERVER_FOUND_BIT);
                s_discovery_reply_count++;
                ESP_LOGI(TAG, "Servidor encontrado: %s:%u",
                         inet_ntoa(source.sin_addr),
                         (unsigned)ntohs(source.sin_port));
            }
        } else if (message.type == DEVICE_MSG_ACTUATOR_COMMAND &&
                   command_from_current_server(&source)) {
            esp_err_t err = esp_now_send(s_broadcast_peer,
                                         (const uint8_t *)&message,
                                         sizeof(message));
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "No se pudo reenviar comando: %s",
                         esp_err_to_name(err));
            }
        }
    }
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
        if (sock < 0) {
            if (!copy_server_destination(&destination)) {
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
        /* La IP del computador puede cambiar al cambiar de red. Refrescar el
         * destino en cada envío evita conservar una dirección ya obsoleta. */
        if (!copy_server_destination(&destination)) {
            close(sock);
            sock = -1;
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
            s_udp_error_count++;
            ESP_LOGW(TAG, "Envio UDP fallo; recreando socket");
            close(sock);
            sock = -1;
            xEventGroupClearBits(s_network_events, SERVER_FOUND_BIT);
        } else {
            s_udp_sent_count++;
        }
    }
}

static void serial_diagnostics_task(void *arg)
{
    (void)arg;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));
        bool server_found = s_network_events &&
            ((xEventGroupGetBits(s_network_events) & SERVER_FOUND_BIT) != 0);
        UBaseType_t queued = s_transport_queue ?
            uxQueueMessagesWaiting(s_transport_queue) : 0;
        ESP_LOGI(TAG,
                 "DIAG WiFi=OK canal=%u servidor=%s discovery_tx=%lu reply=%lu "
                 "pair_hello=%lu CSI_rx=%lu sensor_rx=%lu actuator_rx=%lu "
                 "UDP_tx=%lu errores=%lu cola=%u descartados=%lu",
                 (unsigned)s_wifi_channel, server_found ? "OK" : "BUSCANDO",
                 (unsigned long)s_discovery_sent_count,
                 (unsigned long)s_discovery_reply_count,
                 (unsigned long)s_pair_hello_count,
                 (unsigned long)s_csi_rx_count,
                 (unsigned long)s_sensor_rx_count,
                 (unsigned long)s_actuator_rx_count,
                 (unsigned long)s_udp_sent_count,
                 (unsigned long)s_udp_error_count, (unsigned)queued,
                 (unsigned long)s_queue_drop_count);
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

    wifi_manager_start();
    while (!wifi_manager_wait_connected(pdMS_TO_TICKS(1000))) {
        ESP_LOGI(TAG, "Esperando configuracion Wi-Fi desde el portal");
    }
    do {
        s_wifi_channel = wifi_manager_channel();
        if (s_wifi_channel < 1 || s_wifi_channel > 13) {
            ESP_LOGW(TAG, "Esperando que el driver reporte el canal Wi-Fi");
            vTaskDelay(pdMS_TO_TICKS(500));
        }
    } while (s_wifi_channel < 1 || s_wifi_channel > 13);
    ESP_LOGI(TAG, "ESP-NOW y CSI usaran el canal real %u", s_wifi_channel);

    esp_now_peer_info_t peer = {
        .channel = 0,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff}};
    wifi_esp_now_init(peer);
    ESP_ERROR_CHECK(esp_now_register_recv_cb(esp_now_receive_cb));

    s_transport_queue = xQueueCreate(CONFIG_CSI_UDP_QUEUE_LENGTH,
                                     sizeof(transport_frame_t));
    if (!s_transport_queue) {
        ESP_LOGE(TAG, "No se pudo crear la cola de transporte");
        abort();
    }
    s_network_events = xEventGroupCreate();
    s_server_mutex = xSemaphoreCreateMutex();
    if (!s_network_events || !s_server_mutex) {
        ESP_LOGE(TAG, "No se pudo crear sincronizacion de red");
        abort();
    }
    xTaskCreate(discovery_and_command_task, "discovery_cmd", 6144, NULL, 5, NULL);
    xTaskCreate(udp_sender_task, "udp_sender", 8192, NULL, 5, NULL);
    xTaskCreate(serial_diagnostics_task, "serial_diag", 4096, NULL, 3, NULL);

    wifi_csi_init();
    ESP_LOGI(TAG, "CSI activo; buscando servidor por UDP broadcast en %d",
             CSI_DISCOVERY_PORT);
}
