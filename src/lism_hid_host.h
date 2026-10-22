#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <zephyr/bluetooth/addr.h>

#define LISM_HID_CANDIDATES 12
#define LISM_HID_VISIBLE_CANDIDATES 3
struct lism_hid_candidate {
    uint32_t token;
    char name[24];
    char address[18];
    int8_t rssi;
    bool valid;
};
struct lism_hid_status {
    uint32_t generation;
    uint32_t advertisements_seen, advertisements_dropped;
    bool pairing, connected, ready, saved, unsupported;
    uint8_t seconds_left;
    char message[48], name[24];
    struct lism_hid_candidate candidates[LISM_HID_CANDIDATES];
};
void lism_hid_host_snapshot(struct lism_hid_status *status);
void lism_hid_host_pair(void);
void lism_hid_host_cancel(void);
void lism_hid_host_select(uint8_t index, uint32_t token);
void lism_hid_host_forget(void);

/* Sole scanner belongs to the patched pinned ZMK split central. */
bool lism_hid_host_scan_needed(void);
bool lism_hid_host_scan_paused(void);
bool lism_hid_host_claim_ad(const bt_addr_le_t *addr);
void lism_hid_scan_resume(void);
int lism_hid_scan_pause(void);
int lism_hid_advertising_begin(void);
void lism_hid_advertising_end(void);
/* Read-only scanner-owner snapshot; invoked only by host coordinator logs. */
void lism_hid_scan_diagnostics(void);
