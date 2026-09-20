#include <stdio.h>
#include <string.h>
#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_log.h"

void app_main(void)
{
    // 1. Get the MAC Address (Identity)
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);

    // 2. Get the Firmware SHA-256 Hash (Integrity)
    // This looks at the currently running partition and hashes the binary
    uint8_t sha_256[32];
    esp_partition_get_sha256(esp_ota_get_running_partition(), sha_256);

    printf("\n======================================================\n");
    printf("        ESP32 ATTESTATION DATA (OBU IDENTITY)        \n");
    printf("======================================================\n");

    // Print MAC Address
    printf("NODE_MAC:  %02X:%02X:%02X:%02X:%02X:%02X\n", 
            mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    // Print Firmware Hash
    printf("NODE_HASH: ");
    for (int i = 0; i < 32; i++) {
        printf("%02x", sha_256[i]);
    }
    printf("\n");

    printf("======================================================\n");
    printf("Save BOTH values to your Fedora RSU Whitelist.\n");
    printf("If you change any code and re-flash, the HASH will change!\n");
    printf("======================================================\n");
}
