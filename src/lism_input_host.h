#pragma once
#include "lism_hid_host.h"

#define LISM_INPUT_PEERS 2
enum lism_input_device_type { LISM_INPUT_DEVICE_UNKNOWN, LISM_INPUT_DEVICE_MOUSE,
    LISM_INPUT_DEVICE_KEYBOARD, LISM_INPUT_DEVICE_TOUCHPAD, LISM_INPUT_DEVICE_GAMEPAD };
enum lism_input_auth_type { LISM_INPUT_AUTH_NONE, LISM_INPUT_AUTH_DISPLAY,
    LISM_INPUT_AUTH_CONFIRM, LISM_INPUT_AUTH_ENTRY };
struct lism_input_peer_status {
    bool saved, connected, ready;
    bool legacy_available, legacy_allowed, legacy_used;
    uint32_t legacy_token;
    uint8_t type;
    char name[24], message[48];
};
struct lism_input_status {
    uint32_t generation, auth_token, passkey;
    uint8_t selected, auth_slot, auth_type, seconds_left;
    bool searching, show_other;
    char message[48];
    struct lism_input_peer_status peers[LISM_INPUT_PEERS];
    struct lism_hid_candidate candidates[LISM_HID_CANDIDATES];
};
void lism_input_host_snapshot(struct lism_input_status *out);
void lism_input_host_search(uint8_t slot, bool show_other);
void lism_input_host_cancel(void);
void lism_input_host_select(uint8_t index, uint32_t token);
void lism_input_host_forget(uint8_t slot);
void lism_input_host_retry(uint8_t slot);
void lism_input_host_legacy(uint8_t slot, uint32_t token, bool allow);
void lism_input_host_auth(uint32_t token, bool accept, uint32_t passkey);
