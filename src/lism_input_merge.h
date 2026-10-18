#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <zmk/hid.h>

#define LISM_INPUT_MERGE_SLOTS 2
#define LISM_INPUT_MERGE_CONSUMERS 12

struct lism_input_merge_state {
    uint8_t keys[32];
    uint8_t modifiers;
    uint16_t consumer[LISM_INPUT_MERGE_CONSUMERS];
    uint8_t consumer_count;
    bool rollover;
};

uint32_t lism_input_merge_generation(void);
/* Stale notifications must carry their original generation, not fetch a fresh one. */
int lism_input_merge_set(unsigned int slot, uint32_t generation,
                         const struct lism_input_merge_state *state);
int lism_input_merge_clear(unsigned int slot, uint32_t generation);
/* Called before clearing/sending the OLD endpoint's release reports. */
void lism_input_merge_endpoint_reset(void);
/* Freeze input BEFORE generation can advance. Must not block. */
void lism_input_merge_endpoint_changing(void);
/* Host override: invalidate queued frames and adopt the new generation. Never block. */
void lism_input_merge_endpoint_changed(uint32_t generation);
/* Thread-context transition lock spans old endpoint release and new commit. */
void lism_input_endpoint_transition_begin(void);
void lism_input_endpoint_transition_end(void);

/* Caller owns the copy for the whole send; local ZMK state is never modified. */
void lism_input_merge_keyboard(struct zmk_hid_keyboard_report *copy);
void lism_input_merge_consumer(struct zmk_hid_consumer_report *copy);
/* Epoch travels with the caller-owned snapshot through the BLE queue. */
int lism_input_hog_send_keyboard(struct zmk_hid_keyboard_report_body *copy, uint32_t epoch);
int lism_input_hog_send_consumer(struct zmk_hid_consumer_report_body *copy, uint32_t epoch);
#if IS_ENABLED(CONFIG_ZMK_POINTING)
int lism_input_hog_send_mouse(struct zmk_hid_mouse_report_body *copy, uint32_t epoch);
#endif
#if IS_ENABLED(CONFIG_ZMK_USB_BOOT)
void lism_input_merge_boot(zmk_hid_boot_report_t *copy);
#endif
