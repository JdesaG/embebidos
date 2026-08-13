#include "wifi_manager.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs.h"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_CONNECT_TIMEOUT_MS 12000
#define WIFI_CONFIG_BUTTON GPIO_NUM_32
#define WIFI_CONFIG_HOLD_MS 5000
#define WIFI_AP_PASSWORD "BabyCSI26"
#define WIFI_NAMESPACE "wifi_cfg"

typedef struct {
    char ssid[33];
    char password[65];
} saved_network_t;

static const char *TAG = "wifi_manager";
static EventGroupHandle_t s_events;
static httpd_handle_t s_httpd;
static bool s_allow_reconnect;
static uint8_t s_reconnect_attempts;
static uint8_t s_channel;
static char s_ap_ssid[33] = "BabyCSI";

static bool refresh_wifi_channel(void)
{
    uint8_t primary = 0;
    wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
    esp_err_t err = esp_wifi_get_channel(&primary, &secondary);
    if (err != ESP_OK || primary < 1 || primary > 13) {
        ESP_LOGE(TAG, "No se pudo leer un canal Wi-Fi valido: %s canal=%u",
                 esp_err_to_name(err), (unsigned)primary);
        return false;
    }
    s_channel = primary;
    return true;
}

static void erase_saved_networks(void)
{
    nvs_handle_t handle;
    if (nvs_open(WIFI_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
        nvs_erase_all(handle);
        nvs_commit(handle);
        nvs_close(handle);
        ESP_LOGW(TAG, "Redes guardadas eliminadas por el boton de configuracion");
    }
}

static void check_reset_button(void)
{
    gpio_config_t config = {
        .pin_bit_mask = 1ULL << WIFI_CONFIG_BUTTON,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&config));
    if (gpio_get_level(WIFI_CONFIG_BUTTON) != 0) {
        return;
    }

    ESP_LOGW(TAG, "Boton presionado; mantenlo 5 s para borrar Wi-Fi");
    vTaskDelay(pdMS_TO_TICKS(WIFI_CONFIG_HOLD_MS));
    if (gpio_get_level(WIFI_CONFIG_BUTTON) == 0) {
        erase_saved_networks();
    }
}

static bool read_string(nvs_handle_t handle, const char *key, char *out,
                        size_t out_size)
{
    size_t required = out_size;
    esp_err_t err = nvs_get_str(handle, key, out, &required);
    if (err != ESP_OK || out[0] == '\0') {
        out[0] = '\0';
        return false;
    }
    return true;
}

static size_t load_networks(saved_network_t networks[2])
{
    memset(networks, 0, sizeof(saved_network_t) * 2);
    nvs_handle_t handle;
    if (nvs_open(WIFI_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) {
        return 0;
    }

    size_t count = 0;
    for (size_t index = 0; index < 2; ++index) {
        char ssid_key[4];
        char pass_key[4];
        snprintf(ssid_key, sizeof(ssid_key), "s%u", (unsigned)index);
        snprintf(pass_key, sizeof(pass_key), "p%u", (unsigned)index);
        if (read_string(handle, ssid_key, networks[index].ssid,
                        sizeof(networks[index].ssid))) {
            read_string(handle, pass_key, networks[index].password,
                        sizeof(networks[index].password));
            count++;
        }
    }
    nvs_close(handle);
    return count;
}

static esp_err_t save_networks(const char *primary_ssid,
                               const char *primary_password,
                               const char *backup_ssid,
                               const char *backup_password)
{
    if (!primary_ssid || primary_ssid[0] == '\0' ||
        strlen(primary_ssid) > 32 || !primary_password ||
        strlen(primary_password) > 64 || !backup_ssid ||
        strlen(backup_ssid) > 32 || !backup_password ||
        strlen(backup_password) > 64) {
        return ESP_ERR_INVALID_ARG;
    }

    nvs_handle_t handle;
    ESP_RETURN_ON_ERROR(nvs_open(WIFI_NAMESPACE, NVS_READWRITE, &handle), TAG,
                        "No se pudo abrir NVS");
    esp_err_t err = nvs_set_str(handle, "s0", primary_ssid);
    if (err == ESP_OK) {
        err = nvs_set_str(handle, "p0", primary_password);
    }
    if (err == ESP_OK && backup_ssid[0] != '\0') {
        err = nvs_set_str(handle, "s1", backup_ssid);
    }
    if (err == ESP_OK && backup_ssid[0] != '\0') {
        err = nvs_set_str(handle, "p1", backup_password);
    }
    if (err == ESP_OK && backup_ssid[0] == '\0') {
        esp_err_t erase_ssid = nvs_erase_key(handle, "s1");
        if (erase_ssid != ESP_OK && erase_ssid != ESP_ERR_NVS_NOT_FOUND) {
            err = erase_ssid;
        }
        esp_err_t erase_pass = nvs_erase_key(handle, "p1");
        if (err == ESP_OK && erase_pass != ESP_OK &&
            erase_pass != ESP_ERR_NVS_NOT_FOUND) {
            err = erase_pass;
        }
    }
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    (void)arg;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = event_data;
        xEventGroupClearBits(s_events, WIFI_CONNECTED_BIT);
        ESP_LOGW(TAG, "Wi-Fi desconectado (motivo=%u)",
                 event ? (unsigned)event->reason : 0U);
        if (s_allow_reconnect) {
            s_reconnect_attempts++;
            if (s_reconnect_attempts >= 10) {
                ESP_LOGW(TAG, "Red perdida; reiniciando para probar el respaldo");
                esp_restart();
            } else {
                ESP_LOGW(TAG, "Wi-Fi desconectado; reintento %u/10",
                         s_reconnect_attempts);
                esp_wifi_connect();
            }
        }
    }
}

static void ip_event_handler(void *arg, esp_event_base_t event_base,
                             int32_t event_id, void *event_data)
{
    (void)arg;
    if (event_base != IP_EVENT || event_id != IP_EVENT_STA_GOT_IP) {
        return;
    }
    const ip_event_got_ip_t *event = event_data;
    s_reconnect_attempts = 0;
    ESP_LOGI(TAG, "Gateway conectado: IP=" IPSTR " mascara=" IPSTR
             " router=" IPSTR,
             IP2STR(&event->ip_info.ip), IP2STR(&event->ip_info.netmask),
             IP2STR(&event->ip_info.gw));
    if (refresh_wifi_channel()) {
        ESP_LOGI(TAG, "Canal Wi-Fi del gateway: %u", (unsigned)s_channel);
    }
    xEventGroupSetBits(s_events, WIFI_CONNECTED_BIT);
}

static bool try_network(const saved_network_t *network)
{
    wifi_config_t config = {0};
    strlcpy((char *)config.sta.ssid, network->ssid, sizeof(config.sta.ssid));
    strlcpy((char *)config.sta.password, network->password,
            sizeof(config.sta.password));
    config.sta.threshold.authmode = WIFI_AUTH_OPEN;

    xEventGroupClearBits(s_events, WIFI_CONNECTED_BIT);
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    ESP_LOGI(TAG, "Intentando red guardada: %s", network->ssid);
    esp_wifi_disconnect();
    ESP_ERROR_CHECK(esp_wifi_connect());
    EventBits_t bits = xEventGroupWaitBits(
        s_events, WIFI_CONNECTED_BIT, pdFALSE, pdFALSE,
        pdMS_TO_TICKS(WIFI_CONNECT_TIMEOUT_MS));
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void url_decode(char *destination, size_t destination_size,
                       const char *source)
{
    size_t out = 0;
    for (size_t in = 0; source[in] && out + 1 < destination_size; ++in) {
        if (source[in] == '+') {
            destination[out++] = ' ';
        } else if (source[in] == '%' && source[in + 1] && source[in + 2]) {
            int high = hex_value(source[in + 1]);
            int low = hex_value(source[in + 2]);
            if (high >= 0 && low >= 0) {
                destination[out++] = (char)((high << 4) | low);
                in += 2;
            }
        } else {
            destination[out++] = source[in];
        }
    }
    destination[out] = '\0';
}

static bool form_value(const char *body, const char *name, char *out,
                       size_t out_size)
{
    size_t name_len = strlen(name);
    const char *cursor = body;
    while (cursor && *cursor) {
        if ((cursor == body || cursor[-1] == '&') &&
            strncmp(cursor, name, name_len) == 0 && cursor[name_len] == '=') {
            cursor += name_len + 1;
            const char *end = strchr(cursor, '&');
            size_t length = end ? (size_t)(end - cursor) : strlen(cursor);
            char encoded[130];
            if (length >= sizeof(encoded)) length = sizeof(encoded) - 1;
            memcpy(encoded, cursor, length);
            encoded[length] = '\0';
            url_decode(out, out_size, encoded);
            return true;
        }
        cursor = strchr(cursor, '&');
        if (cursor) cursor++;
    }
    return false;
}

static const char PORTAL_HTML[] =
    "<!doctype html><html lang='es'><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>BabyCSI</title><style>body{font-family:system-ui;max-width:520px;"
    "margin:40px auto;padding:0 20px;background:#f7f8fb;color:#172033}"
    "form{background:white;padding:24px;border-radius:16px;box-shadow:0 8px 30px #0001}"
    "label{display:block;margin:14px 0 6px}input,select,button{width:100%;box-sizing:border-box;"
    "padding:12px;border-radius:9px;border:1px solid #bcc5d4}button{margin-top:20px;"
    "background:#2855d9;color:white;border:0;font-weight:700}</style>"
    "<h1>Configurar BabyCSI</h1><p>Guarda una red principal Wi-Fi de 2.4 GHz "
    "y, si quieres, una red de respaldo.</p><form method='post' action='/save'>"
    "<label>Red principal (SSID)</label>"
    "<input name='ssid0' maxlength='32' required><label>Contraseña principal</label>"
    "<input name='password0' type='password' maxlength='64'>"
    "<label>Red de respaldo (opcional)</label>"
    "<input name='ssid1' maxlength='32'><label>Contraseña de respaldo</label>"
    "<input name='password1' type='password' maxlength='64'>"
    "<button type='submit'>Guardar y conectar</button></form>"
    "<p>Si esta página no abre automáticamente, usa <b>192.168.4.1</b>.</p></html>";

static esp_err_t root_handler(httpd_req_t *request)
{
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    return httpd_resp_send(request, PORTAL_HTML, HTTPD_RESP_USE_STRLEN);
}

static void restart_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(1200));
    esp_restart();
}

static esp_err_t save_handler(httpd_req_t *request)
{
    if (request->content_len <= 0 || request->content_len > 600) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Formulario invalido");
        return ESP_FAIL;
    }
    char body[601];
    int received = httpd_req_recv(request, body, request->content_len);
    if (received <= 0) return ESP_FAIL;
    body[received] = '\0';

    char primary_ssid[33] = {0};
    char primary_password[65] = {0};
    char backup_ssid[33] = {0};
    char backup_password[65] = {0};
    form_value(body, "ssid0", primary_ssid, sizeof(primary_ssid));
    form_value(body, "password0", primary_password,
               sizeof(primary_password));
    form_value(body, "ssid1", backup_ssid, sizeof(backup_ssid));
    form_value(body, "password1", backup_password,
               sizeof(backup_password));

    if (save_networks(primary_ssid, primary_password, backup_ssid,
                      backup_password) != ESP_OK) {
        httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST,
                            "No se pudo guardar la red");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Red principal guardada: %s", primary_ssid);
    httpd_resp_set_type(request, "text/html; charset=utf-8");
    httpd_resp_sendstr(request,
        "<html><meta charset='utf-8'><h2>Red guardada</h2>"
        "<p>El gateway se reiniciará y buscará el servidor automáticamente.</p></html>");
    xTaskCreate(restart_task, "wifi_restart", 2048, NULL, 3, NULL);
    return ESP_OK;
}

static esp_err_t not_found_handler(httpd_req_t *request, httpd_err_code_t error)
{
    (void)error;
    httpd_resp_set_status(request, "302 Found");
    httpd_resp_set_hdr(request, "Location", "http://192.168.4.1/");
    return httpd_resp_send(request, NULL, 0);
}

static void start_portal(void)
{
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf(s_ap_ssid, sizeof(s_ap_ssid), "BabyCSI-%02X%02X", mac[4], mac[5]);

    esp_netif_create_default_wifi_ap();
    wifi_config_t ap_config = {0};
    strlcpy((char *)ap_config.ap.ssid, s_ap_ssid, sizeof(ap_config.ap.ssid));
    strlcpy((char *)ap_config.ap.password, WIFI_AP_PASSWORD,
            sizeof(ap_config.ap.password));
    ap_config.ap.ssid_len = strlen(s_ap_ssid);
    ap_config.ap.channel = 1;
    ap_config.ap.max_connection = 4;
    ap_config.ap.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_config));

    httpd_config_t http_config = HTTPD_DEFAULT_CONFIG();
    http_config.max_uri_handlers = 4;
    ESP_ERROR_CHECK(httpd_start(&s_httpd, &http_config));
    httpd_uri_t root = {.uri = "/", .method = HTTP_GET, .handler = root_handler};
    httpd_uri_t save = {.uri = "/save", .method = HTTP_POST, .handler = save_handler};
    ESP_ERROR_CHECK(httpd_register_uri_handler(s_httpd, &root));
    ESP_ERROR_CHECK(httpd_register_uri_handler(s_httpd, &save));
    httpd_register_err_handler(s_httpd, HTTPD_404_NOT_FOUND, not_found_handler);
    ESP_LOGW(TAG, "Sin red disponible. Conecta el teléfono a %s (clave %s) y abre 192.168.4.1",
             s_ap_ssid, WIFI_AP_PASSWORD);
}

void wifi_manager_start(void)
{
    s_events = xEventGroupCreate();
    if (!s_events) abort();
    check_reset_button();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_config));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               ip_event_handler, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());

    saved_network_t networks[2];
    size_t count = load_networks(networks);
    ESP_LOGI(TAG, "Redes guardadas encontradas: %u", (unsigned)count);
    s_allow_reconnect = false;
    for (size_t index = 0; index < 2; ++index) {
        if (networks[index].ssid[0] && try_network(&networks[index])) {
            s_allow_reconnect = true;
            ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
            ESP_LOGI(TAG, "Red %u/%u conectada; portal desactivado",
                     (unsigned)(index + 1), (unsigned)count);
            return;
        }
    }

    esp_wifi_disconnect();
    start_portal();
}

bool wifi_manager_wait_connected(TickType_t timeout)
{
    EventBits_t bits = xEventGroupWaitBits(s_events, WIFI_CONNECTED_BIT,
                                           pdFALSE, pdFALSE, timeout);
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

uint8_t wifi_manager_channel(void)
{
    /* La API requiere ambos punteros. Volver a consultar aquí también evita
     * conservar cero si el evento GOT_IP ocurrió antes de estabilizar la
     * información del canal. */
    refresh_wifi_channel();
    return s_channel;
}

const char *wifi_manager_provisioning_ssid(void)
{
    return s_ap_ssid;
}
