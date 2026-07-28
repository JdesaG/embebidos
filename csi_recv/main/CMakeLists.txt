idf_component_register(SRC_DIRS "."
                       INCLUDE_DIRS "." "../../common"
                       REQUIRES esp_wifi esp_event esp_netif nvs_flash esp_csi_gain_ctrl lwip)
