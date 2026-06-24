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
#include <unistd.h>

#include "nvs_flash.h"

#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"

#include "sensors.h"

/*
 * Este archivo es el firmware de la ESP32 emisora.
 *
 * Lenguaje: C, no C++.
 * Rol en el proyecto:
 *   1. Configura la radio WiFi.
 *   2. Usa ESP-NOW para transmitir paquetes a una frecuencia fija.
 *   3. Esos paquetes viajan por el aire y son recibidos por csi_recv.
 *   4. El receptor, no este emisor, extrae el CSI de los paquetes recibidos.
 *
 * Si cambias este archivo, flashealo solo en la placa emisora.
 */

/* Canal WiFi usado por emisor y receptor. Ambas placas deben usar el mismo
 * canal. Si este valor difiere de csi_recv, el receptor no vera paquetes y
 * no se producira CSI.
 */
#define CONFIG_LESS_INTERFERENCE_CHANNEL   11

/* Configuracion de radio WiFi dependiente del chip objetivo.
 * HT40 usa un canal mas ancho que HT20 y normalmente produce mas subportadoras
 * CSI. Si se cambia a HT20, el vector CSI puede ser mas corto y el parser o
 * modelo en Python puede ver una forma de senal distinta.
 */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#define CONFIG_WIFI_BAND_MODE   WIFI_BAND_MODE_2G_ONLY
#define CONFIG_WIFI_2G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_5G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_2G_PROTOCOL             WIFI_PROTOCOL_11N
#define CONFIG_WIFI_5G_PROTOCOL             WIFI_PROTOCOL_11N
#else
#define CONFIG_WIFI_BANDWIDTH           WIFI_BW_HT40
#endif

/* Modo fisico y tasa de ESP-NOW.
 * Deben coincidir con el receptor. Cambiarlos modifica como se envian los
 * paquetes por WiFi y puede afectar la estabilidad del CSI, la perdida de
 * paquetes y la frecuencia de muestreo.
 */
#define CONFIG_ESP_NOW_PHYMODE           WIFI_PHY_MODE_HT40
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

/* Configura el hardware WiFi antes de iniciar ESP-NOW.
 * No se conecta a un router; pone la ESP32 en modo estacion para que pueda
 * usar la radio WiFi y enviar tramas ESP-NOW.
 */
static void wifi_init()
{
    /* Crea el bucle de eventos por defecto usado internamente por WiFi en ESP-IDF. */
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* Inicializa la interfaz de red y la memoria del driver WiFi. */
    ESP_ERROR_CHECK(esp_netif_init());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    /* El modo estacion es suficiente para ESP-NOW. No se requiere conexion a router. */
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));

    /* Guarda la configuracion WiFi en RAM, no en flash. Asi los cambios de prueba son temporales. */
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

    /* Fuerza la radio al canal elegido. En HT40, el canal secundario se coloca
     * debajo. Emisor y receptor deben usar la misma configuracion de canal.
     */
#if CONFIG_IDF_TARGET_ESP32C5
    if ((CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY && CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW_HT20)
            || (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_5G_ONLY && CONFIG_WIFI_5G_BANDWIDTHS == WIFI_BW_HT20)) {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#elif (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0)) || CONFIG_IDF_TARGET_ESP32C61
    if (CONFIG_WIFI_BAND_MODE == WIFI_BAND_MODE_2G_ONLY && CONFIG_WIFI_2G_BANDWIDTHS == WIFI_BW_HT20) {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#else
    if (CONFIG_WIFI_BANDWIDTH == WIFI_BW_HT20) {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_NONE));
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_LESS_INTERFERENCE_CHANNEL, WIFI_SECOND_CHAN_BELOW));
    }
#endif

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

    /**
     * @brief Inicializa WiFi
     */
    wifi_init();

    /**
     * @brief Inicializa ESP-NOW
     *        Documentacion del protocolo ESP-NOW:
     *        https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html
     */
    esp_now_peer_info_t peer = {
        .channel   = CONFIG_LESS_INTERFERENCE_CHANNEL,
        .ifidx     = WIFI_IF_STA,
        .encrypt   = false,
        /* MAC broadcast: envia paquetes para que el receptor los escuche sin conocer
         * su direccion MAC exacta.
         */
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };
    wifi_esp_now_init(peer);
    sensors_init();

    ESP_LOGI(TAG, "================ CSI SEND ================");
    ESP_LOGI(TAG, "wifi_channel: %d, send_frequency: %d, mac: " MACSTR,
             CONFIG_LESS_INTERFERENCE_CHANNEL, CONFIG_SEND_FREQUENCY, MAC2STR(CONFIG_CSI_SEND_MAC));
    ESP_LOGI(TAG, "Sensores activos: SENSOR_DATA se enviara al receptor cada 1 s");

    /* Bucle infinito: envia continuamente un contador.
     * El receptor usa la llegada de estos paquetes para generar CSI.
     */
    for (uint32_t count = 0; ; ++count) {
        sensors_update();

        /* Envia el contador actual como payload. Es pequeno; lo importante no es el
         * contenido sino la trama WiFi en si.
         */
        esp_err_t ret = esp_now_send(peer.peer_addr, (const uint8_t *)&count, sizeof(count));
        if (ret != ESP_OK) {
            /* Si aparece ESP_ERR_ESPNOW_NO_MEM, la tasa de envio o el canal pueden estar
             * muy congestionados. Baja CONFIG_SEND_FREQUENCY o cambia de canal.
             */
            ESP_LOGW(TAG, "free_heap: %ld <%s> ESP-NOW send error", esp_get_free_heap_size(), esp_err_to_name(ret));
        }

        sensor_payload_t sensor_payload;
        if (sensors_get_payload_if_due(&sensor_payload)) {
            esp_err_t sensor_ret = esp_now_send(peer.peer_addr, (const uint8_t *)&sensor_payload, sizeof(sensor_payload));
            if (sensor_ret != ESP_OK) {
                ESP_LOGW(TAG, "free_heap: %ld <%s> ESP-NOW sensor send error",
                         esp_get_free_heap_size(), esp_err_to_name(sensor_ret));
            }
        }

        /* Duerme para que el bucle envie CONFIG_SEND_FREQUENCY paquetes por segundo. */
        usleep(1000 * 1000 / CONFIG_SEND_FREQUENCY);
    }
}
