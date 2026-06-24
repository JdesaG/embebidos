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
#include <stdbool.h>

#include "nvs_flash.h"

#include "esp_mac.h"
#include "rom/ets_sys.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_csi_gain_ctrl.h"

#include "sensor_payload.h"

/*
 * Este archivo es el firmware de la ESP32 receptora.
 *
 * Lenguaje: C, no C++.
 * Rol en el proyecto:
 *   1. Configura la radio WiFi en el mismo canal que csi_send.
 *   2. Recibe paquetes ESP-NOW enviados por csi_send.
 *   3. Pide al driver WiFi que extraiga CSI de cada paquete recibido.
 *   4. Imprime cada trama CSI como texto por el puerto serial USB.
 *
 * La Mac lee esas lineas CSI_DATA desde /dev/cu.usbserial-0001.
 * Si cambias este archivo, flashealo solo en la placa receptora.
 */

/* Canal WiFi usado para escuchar al emisor. Debe coincidir con csi_send. */
#define CONFIG_LESS_INTERFERENCE_CHANNEL   11

/* Configuracion de radio dependiente del chip objetivo. HT40 normalmente da
 * mas subportadoras CSI que HT20. Si se cambia, el parser/modelo en Python
 * puede recibir otra longitud CSI y requerir recalibracion o reentrenamiento.
 */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61 || (CONFIG_IDF_TARGET_ESP32C6 && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 4, 0))
#define CONFIG_WIFI_BAND_MODE               WIFI_BAND_MODE_2G_ONLY
#define CONFIG_WIFI_2G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_5G_BANDWIDTHS           WIFI_BW_HT40
#define CONFIG_WIFI_2G_PROTOCOL             WIFI_PROTOCOL_11N
#define CONFIG_WIFI_5G_PROTOCOL             WIFI_PROTOCOL_11N
#else
#define CONFIG_WIFI_BANDWIDTH           WIFI_BW_HT40
#endif

/* Modo fisico y tasa de ESP-NOW. Debe coincidir con csi_send. */
#define CONFIG_ESP_NOW_PHYMODE           WIFI_PHY_MODE_HT40
#define CONFIG_ESP_NOW_RATE             WIFI_PHY_RATE_MCS0_LGI

/* 0 significa que el codigo compensa la ganancia por software, pero no fuerza
 * una ganancia fija por hardware. Ponerlo en 1 puede estabilizar la ganancia,
 * pero solo despues de usar los primeros 100 paquetes como linea base.
 */
#define CONFIG_FORCE_GAIN                   0

/* Algunos chips nuevos pueden forzar el formato CSI LLTF. Dejalo en 0 salvo
 * que tambien ajustes el parser/modelo al formato CSI resultante.
 */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF                      0
#endif

/* Activa la compensacion de control de ganancia en chips compatibles.
 * Apagarlo puede hacer que la amplitud salte cuando el receptor WiFi cambia
 * su ganancia interna.
 */
#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL                 1
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

/* MAC del emisor que aceptamos. Si no coincide con csi_send, todos los
 * paquetes seran ignorados por wifi_csi_rx_cb y no se imprimira CSI_DATA.
 */
static const uint8_t CONFIG_CSI_SEND_MAC[] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};

/* Etiqueta de log para los mensajes del monitor ESP-IDF. */
static const char *TAG = "csi_recv";

/* Configura el hardware WiFi en modo estacion.
 * El receptor no necesita router. Solo necesita la radio WiFi activa en el
 * mismo canal que el emisor.
 */
static void wifi_init()
{
    /* Crea el bucle de eventos usado por el driver WiFi de ESP-IDF. */
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* Inicializa la interfaz de red y la memoria del driver WiFi. */
    ESP_ERROR_CHECK(esp_netif_init());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    /* El modo estacion es suficiente para recibir ESP-NOW y capturar CSI. */
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));

    /* Mantiene la configuracion WiFi en RAM para que cambios de prueba no persistan en flash. */
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

#if CONFIG_IDF_TARGET_ESP32C5
    /* C5/C6/C61 usan APIs WiFi nuevas multibanda. */
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
    /* Ruta para ESP32/ESP32-S3/C3 clasicos. */
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(ESP_IF_WIFI_STA, CONFIG_WIFI_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_start());
#endif

    /* Desactiva ahorro de energia para mantener estable el tiempo de paquetes y la captura CSI. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    /* Fija el receptor al mismo canal/ancho de banda que el emisor. */
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

    /* Este ejemplo configura la MAC de estacion del receptor con el mismo valor
     * fijo usado por el ejemplo del emisor. Se mantiene desde el ejemplo de
     * Espressif. El filtro inferior aun revisa la MAC origen antes de imprimir CSI.
     */
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, CONFIG_CSI_SEND_MAC));
}

/* Inicializa el lado receptor de ESP-NOW y configura peer/tasa. */
static void wifi_esp_now_init(esp_now_peer_info_t peer)
{
    /* Inicia la pila del protocolo ESP-NOW. */
    ESP_ERROR_CHECK(esp_now_init());

    /* Clave maestra primaria requerida por la configuracion ESP-NOW. */
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));

    /* Hace coincidir modo fisico/tasa del emisor para que los paquetes lleguen de forma consistente. */
    esp_now_rate_config_t rate_config = {
        .phymode = CONFIG_ESP_NOW_PHYMODE,
        .rate = CONFIG_ESP_NOW_RATE,//  WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false
    };
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(peer.peer_addr, &rate_config));

}

/* Lee el payload ESP-NOW dentro del paquete CSI.
 * En este ejemplo de Espressif, los datos reales de ESP-NOW empiezan en
 * info->payload + 15. El contador CSI ocupa 4 bytes; el paquete de sensores
 * empieza con la firma SENSOR_PAYLOAD_MAGIC para poder distinguirlo.
 */
static bool try_print_sensor_payload(const wifi_csi_info_t *info)
{
    if (!info || !info->payload) {
        return false;
    }

    uint32_t magic = 0;
    memcpy(&magic, info->payload + 15, sizeof(magic));
    if (magic != SENSOR_PAYLOAD_MAGIC) {
        return false;
    }

    sensor_payload_t sensor;
    memcpy(&sensor, info->payload + 15, sizeof(sensor));
    if (sensor.version != SENSOR_PAYLOAD_VERSION || sensor.size != sizeof(sensor)) {
        ESP_LOGW(TAG, "Paquete SENSOR_DATA invalido: version=%u size=%u",
                 sensor.version, sensor.size);
        return true;
    }

    static bool s_sensor_header_printed = false;
    if (!s_sensor_header_printed) {
        ets_printf("type,seq,temp_c_x10,bpm,sound_detected,alert_sound,alert_bpm_high,alert_temp_high,alert_temp_low,buzzer_interval_ms,buzzer_on,uptime_ms\n");
        s_sensor_header_printed = true;
    }

    ets_printf("SENSOR_DATA,%lu,%d,%u,%u,%u,%u,%u,%u,%u,%u,%lu\n",
               (unsigned long)sensor.seq,
               sensor.temp_c_x10,
               sensor.bpm,
               sensor.sound_detected,
               (sensor.alert_flags & SENSOR_ALERT_SOUND) ? 1 : 0,
               (sensor.alert_flags & SENSOR_ALERT_BPM_HIGH) ? 1 : 0,
               (sensor.alert_flags & SENSOR_ALERT_TEMP_HIGH) ? 1 : 0,
               (sensor.alert_flags & SENSOR_ALERT_TEMP_LOW) ? 1 : 0,
               sensor.buzzer_interval_ms,
               sensor.buzzer_on,
               (unsigned long)sensor.uptime_ms);

    return true;
}

/* Callback de recepcion CSI.
 * ESP-IDF llama automaticamente a esta funcion cuando el driver WiFi recibe
 * un paquete con CSI disponible. Es la funcion mas importante del receptor.
 */
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    /* Validacion defensiva: si el driver no entrega buffer de datos, se ignora el evento. */
    if (!info || !info->buf) {
        ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
        return;
    }

    /* Acepta solo paquetes que vienen de la MAC esperada del emisor. Si cambias
     * CONFIG_CSI_SEND_MAC en algun proyecto, actualiza ambos lados.
     */
    if (memcmp(info->mac, CONFIG_CSI_SEND_MAC, 6)) {
        return;
    }

    /* Si el emisor mando sensores, se imprimen como SENSOR_DATA y no como CSI_DATA
     * para no contaminar la ventana del modelo respiratorio.
     */
    if (try_print_sensor_payload(info)) {
        return;
    }

    /* rx_ctrl contiene metadatos del paquete WiFi recibido: RSSI, tasa,
     * piso de ruido, canal, timestamp, longitud de senal, etc.
     */
    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

    /* Cuenta los paquetes CSI impresos. Sirve para imprimir la cabecera CSV una
     * sola vez y para aprender las primeras 100 muestras de ganancia.
     */
    static int s_count = 0;

    /* Multiplicador de compensacion de ganancia por software. Empieza en 1.0 y
     * puede cambiar despues de leer la ganancia AGC/FFT del receptor.
     */
    float compensate_gain = 1.0f;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;
#if !CONFIG_GAIN_CONTROL && !(CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61)
    (void)agc_gain;
    (void)fft_gain;
#endif
#if CONFIG_GAIN_CONTROL
    /* Variables de linea base de ganancia. Los primeros 100 paquetes se usan para
     * aprender una linea base; luego los paquetes posteriores se compensan con ella.
     */
    static uint8_t agc_gain_baseline = 0;
    static int8_t fft_gain_baseline = 0;

    /* Lee valores de ganancia del receptor desde los metadatos del paquete. */
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (s_count < 100) {
        /* Recoge valores de ganancia base durante los primeros paquetes. */
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    } else if (s_count == 100) {
        /* Calcula la linea base cuando ya existen suficientes muestras. */
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
        /* Opcional: fuerza la ganancia de hardware a la linea base. Puede estabilizar
         * los datos, pero tambien reduce adaptabilidad si cambia el entorno.
         */
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
        ESP_LOGD(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
#endif
    }

    /* Calcula el factor de compensacion por software usando las ganancias actuales. */
    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
    ESP_LOGI(TAG, "compensate_gain %f, agc_gain %d, fft_gain %d", compensate_gain, agc_gain, fft_gain);
#endif

    /* El payload del emisor es un contador uint32_t. En este layout de paquete
     * ESP-NOW, el ejemplo lo lee desde payload + 15 y lo imprime como rx_id.
     */
    uint32_t rx_id = 0;
    memcpy(&rx_id, info->payload + 15, sizeof(rx_id));
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
    if (!s_count) {
        /* Imprime la cabecera CSV una sola vez. El parser en Mac/Python espera filas
         * CSI_DATA que sigan esta estructura.
         */
        ESP_LOGI(TAG, "================ CSI RECV ================");
        ets_printf("type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,channel,local_timestamp,sig_len,rx_format,len,first_word,data\n");
    }

    /* Imprime metadatos para chips nuevos. ets_printf escribe al serial UART/USB;
     * asi es como la computadora recibe los datos.
     */
    ets_printf("CSI_DATA,%d," MACSTR ",%d,%d,%d,%d,%d,%d,%d,%d,%d",
               rx_id, MAC2STR(info->mac), rx_ctrl->rssi, rx_ctrl->rate,
               rx_ctrl->noise_floor, fft_gain, agc_gain,  rx_ctrl->channel,
               rx_ctrl->timestamp, rx_ctrl->sig_len, rx_ctrl->cur_bb_format);
#else
    if (!s_count) {
        /* Cabecera para metadatos estilo ESP32 clasico. */
        ESP_LOGI(TAG, "================ CSI RECV ================");
        ets_printf("type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data\n");
    }

    /* Imprime metadatos para chips ESP32 clasicos. */
    ets_printf("CSI_DATA,%d," MACSTR ",%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d",
               rx_id, MAC2STR(info->mac), rx_ctrl->rssi, rx_ctrl->rate, rx_ctrl->sig_mode,
               rx_ctrl->mcs, rx_ctrl->cwb, rx_ctrl->smoothing, rx_ctrl->not_sounding,
               rx_ctrl->aggregation, rx_ctrl->stbc, rx_ctrl->fec_coding, rx_ctrl->sgi,
               rx_ctrl->noise_floor, rx_ctrl->ampdu_cnt, rx_ctrl->channel, rx_ctrl->secondary_channel,
               rx_ctrl->timestamp, rx_ctrl->ant, rx_ctrl->sig_len, rx_ctrl->sig_mode);

#endif
#if (CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61) && CSI_FORCE_LLTF
    /* Ruta LLTF especial: los valores CSI estan empaquetados como enteros con signo
     * de 12 bits en dos bytes. Si se activa, ajusta tambien parser/modelo.
     */
    int16_t csi = ((int16_t)(((((uint16_t)info->buf[1]) << 8) | info->buf[0]) << 4) >> 4);
    ets_printf(",%d,%d,\"[%d", (info->len - 2) / 2, info->first_word_invalid, (int16_t)(compensate_gain * csi));
    for (int i = 2; i < (info->len - 2); i += 2) {
        csi = ((int16_t)(((((uint16_t)info->buf[i + 1]) << 8) | info->buf[i]) << 4) >> 4);
        ets_printf(",%d", (int16_t)(compensate_gain * csi));
    }
#else
    /* Ruta normal: imprime cada byte/valor CSI crudo desde info->buf. El codigo
     * Python interpreta este arreglo como valores alternados imaginario y real.
     */
    ets_printf(",%d,%d,\"[%d", info->len, info->first_word_invalid, (int16_t)(compensate_gain * info->buf[0]));
    for (int i = 1; i < info->len; i++) {
        ets_printf(",%d", (int16_t)(compensate_gain * info->buf[i]));
    }
#endif
    /* Cierra el arreglo CSV entre comillas y termina la linea serial. */
    ets_printf("]\"\n");

    /* Avanza el contador de paquetes. */
    s_count++;
}

/* Activa la captura CSI en el driver WiFi. */
static void wifi_csi_init()
{
    /* El modo promiscuo permite que el driver WiFi inspeccione tramas entrantes y
     * exponga CSI aunque no estemos conectados a un router.
     */
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    /**< Configuracion por defecto */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    /* Campos CSI seleccionados para ESP32-C5/C61. Apagar campos puede reducir el
     * tamano de salida; activar mas campos puede cambiar el tamano del vector.
     */
    wifi_csi_config_t csi_config = {
        .enable                   = true,
        .acquire_csi_legacy       = false,
        .acquire_csi_force_lltf   = CSI_FORCE_LLTF,
        .acquire_csi_ht20         = true,
        .acquire_csi_ht40         = true,
        .acquire_csi_vht          = false,
        .acquire_csi_su           = false,
        .acquire_csi_mu           = false,
        .acquire_csi_dcm          = false,
        .acquire_csi_beamformed   = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg            = 0,
        .dump_ack_en              = false,
        .reserved                 = false
    };
#elif CONFIG_IDF_TARGET_ESP32C6
    /* Campos CSI seleccionados para ESP32-C6. */
    wifi_csi_config_t csi_config = {
        .enable                 = true,
        .acquire_csi_legacy     = false,
        .acquire_csi_ht20       = true,
        .acquire_csi_ht40       = true,
        .acquire_csi_su         = true,
        .acquire_csi_mu         = true,
        .acquire_csi_dcm        = true,
        .acquire_csi_beamformed = true,
        .acquire_csi_he_stbc    = 2,
        .val_scale_cfg          = false,
        .dump_ack_en            = false,
        .reserved               = false
    };
#else
    /* Campos CSI para chips ESP32 clasicos.
     * LLTF/HT-LTF/STBC-HT-LTF son campos de entrenamiento usados por WiFi OFDM.
     */
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = true,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = false,
    };
#endif
    /* Envia la configuracion CSI al driver WiFi. */
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));

    /* Registra el callback que sera llamado por cada paquete CSI. */
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));

    /* Finalmente activa la captura CSI. Sin esta linea, no se imprime CSI_DATA. */
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

/* Punto de entrada de ESP-IDF. Es equivalente a main() en un programa C normal. */
void app_main()
{
    /**
     * @brief Inicializa NVS
     */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* Si NVS contiene datos incompatibles, se borra e inicializa otra vez. */
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
        /* Peer broadcast para aceptar paquetes del emisor sin configurar el receptor
         * con una MAC especifica del peer.
         */
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };

    /* Inicia ESP-NOW antes de activar CSI. */
    wifi_esp_now_init(peer);

    /* Inicia la captura CSI. Despues de esto, wifi_csi_rx_cb imprime lineas CSI_DATA. */
    wifi_csi_init();
}
