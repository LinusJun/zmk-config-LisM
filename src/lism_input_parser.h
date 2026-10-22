#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* HOGP Report Reference supplies the ID; payload excludes the ID byte. */
#define LISM_INPUT_MAX_MAP_BYTES 2048
#define LISM_INPUT_MAX_REPORTS 16
#define LISM_INPUT_MAX_REPORT_BYTES 64
#define LISM_INPUT_MAX_RUNS 64
#define LISM_INPUT_MAX_USAGES 128
#define LISM_INPUT_MAX_CONSUMERS 12
enum lism_input_error {
    LISM_INPUT_OK, LISM_INPUT_ERR_ARGUMENT, LISM_INPUT_ERR_TRUNCATED,
    LISM_INPUT_ERR_MALFORMED, LISM_INPUT_ERR_LIMIT, LISM_INPUT_ERR_UNSUPPORTED,
    LISM_INPUT_ERR_REPORT_ID, LISM_INPUT_ERR_LENGTH, LISM_INPUT_ERR_RANGE,
};
enum lism_input_capability {
    LISM_INPUT_CAP_MOUSE=1, LISM_INPUT_CAP_KEYBOARD=2,
    LISM_INPUT_CAP_CONSUMER=4, LISM_INPUT_CAP_OTHER=8,
    /* Classification only: Digitizer Touch Pad application. Does not imply
     * accepted contact reports or native multitouch forwarding support. */
    LISM_INPUT_CAP_TOUCHPAD=16,
};
enum lism_input_update {
    LISM_INPUT_UPDATED_KEYBOARD=1, LISM_INPUT_UPDATED_MOUSE=2,
    LISM_INPUT_UPDATED_CONSUMER=4,
};
struct lism_input_run {
    int32_t logical_min, logical_max;
    uint32_t usage_min, usage_max;
    uint16_t bit_offset, count, usage_offset;
    uint8_t bit_size, report_index, usage_count, mode;
    bool variable, relative, ranged, null_state;
};
struct lism_input_report { uint16_t input_bits, output_bits, feature_bits; uint8_t id, updates; };
struct lism_input_descriptor {
    struct lism_input_run runs[LISM_INPUT_MAX_RUNS];
    struct lism_input_report reports[LISM_INPUT_MAX_REPORTS];
    uint32_t usages[LISM_INPUT_MAX_USAGES], capabilities;
    uint16_t usage_count;
    uint8_t report_count, run_count;
    bool has_report_ids;
};
struct lism_input_held {
    uint8_t keys[32], modifiers, buttons, consumer_count;
    uint16_t consumer[LISM_INPUT_MAX_CONSUMERS];
    bool keyboard_rollover;
};
struct lism_input_state { struct lism_input_held reports[LISM_INPUT_MAX_REPORTS]; };
struct lism_input_event {
    uint8_t keys[32], modifiers, consumer_count, buttons, updated;
    uint16_t consumer[LISM_INPUT_MAX_CONSUMERS];
    bool keyboard_rollover;
    int32_t dx, dy, wheel, pan;
};
/* Parse errors clear descriptor. Valid unsupported collections are ignored,
 * but their bit positions are retained. No dynamic allocation. */
enum lism_input_error lism_input_parse(const uint8_t *map, size_t length,
                                     struct lism_input_descriptor *descriptor);
/* Exact payload size required. Errors leave state AND event unchanged.
 * The event unions held state from all Report IDs; deltas are current-report
 * only. Rollover/error keyboard arrays never become actual key usages. */
enum lism_input_error lism_input_decode(const struct lism_input_descriptor *descriptor,
    struct lism_input_state *state, uint8_t report_id, const uint8_t *payload,
    size_t length, struct lism_input_event *event);
const char *lism_input_error_string(enum lism_input_error error);
