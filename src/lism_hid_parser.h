#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Independent of Zephyr/Bluetooth. HOGP supplies report_id via Report Reference;
 * payload never includes an implicit report-ID byte. Bounds are hard limits. */
#define LISM_HID_MAX_MAP_BYTES 1024
#define LISM_HID_MAX_REPORTS 16
#define LISM_HID_MAX_FIELDS 48
#define LISM_HID_MAX_REPORT_BYTES 64

enum lism_hid_error {
    LISM_HID_OK = 0,
    LISM_HID_ERR_ARGUMENT,
    LISM_HID_ERR_TRUNCATED,
    LISM_HID_ERR_MALFORMED,
    LISM_HID_ERR_LIMIT,
    LISM_HID_ERR_UNSUPPORTED,
    LISM_HID_ERR_ABSOLUTE,
    LISM_HID_ERR_REPORT_ID,
    LISM_HID_ERR_LENGTH,
    LISM_HID_ERR_RANGE,
};
enum lism_hid_capability {
    LISM_HID_CAP_MOUSE = 1U << 0,
    LISM_HID_CAP_KEYBOARD = 1U << 1,
    LISM_HID_CAP_CONSUMER = 1U << 2,
    LISM_HID_CAP_VENDOR = 1U << 3,
    LISM_HID_CAP_DIGITIZER = 1U << 4,
    LISM_HID_CAP_OTHER = 1U << 5,
    LISM_HID_CAP_ABSOLUTE = 1U << 6,
    LISM_HID_CAP_UNSUPPORTED = 1U << 7,
};
enum lism_hid_field_kind {
    LISM_HID_X = 1, LISM_HID_Y, LISM_HID_WHEEL, LISM_HID_PAN,
    LISM_HID_BUTTON_1 = 16,
};
struct lism_hid_field {
    int32_t logical_min, logical_max;
    uint16_t bit_offset;
    uint8_t bit_size, kind, report_index;
};
struct lism_hid_report {
    uint16_t input_bits, output_bits, feature_bits;
    uint8_t id, button_mask, mouse_fields, axis_mask;
};
struct lism_hid_descriptor {
    struct lism_hid_field fields[LISM_HID_MAX_FIELDS];
    struct lism_hid_report reports[LISM_HID_MAX_REPORTS];
    uint32_t capabilities;
    uint8_t report_count, field_count;
    bool has_report_ids;
};
struct lism_hid_state {
    uint8_t report_buttons[LISM_HID_MAX_REPORTS];
};
struct lism_hid_event {
    int32_t dx, dy, wheel, pan;
    /* All report button states are OR-combined. Axis-only notifications leave
     * button ownership unchanged. changed/pressed/released are transition masks. */
    uint8_t buttons, changed, pressed, released, updated_buttons;
};

/* On error descriptor is cleared (capabilities may retain diagnostic flags).
 * No dynamically allocated memory or guessed field offsets. Unknown valid HID
 * capabilities may parse successfully with zero accepted mouse fields. */
enum lism_hid_error lism_hid_parse(const uint8_t *map, size_t length,
                                  struct lism_hid_descriptor *descriptor);
/* State and event remain unchanged on any error. Exact input payload length is
 * required. A syntactically valid report without accepted mouse fields returns
 * UNSUPPORTED, as do feature/output-only reports. */
enum lism_hid_error lism_hid_decode(const struct lism_hid_descriptor *descriptor,
    struct lism_hid_state *state, uint8_t report_id, const uint8_t *payload,
    size_t length, struct lism_hid_event *event);
const char *lism_hid_error_string(enum lism_hid_error error);
