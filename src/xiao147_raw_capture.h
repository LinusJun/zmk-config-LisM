/* Bounded diagnostic sampling of the AXS5106L factory RAW/DIFF interface.
 * This is not a multitouch decoder and never produces input/HID events.
 * Independently implemented from the register protocol in the public
 * AXS5106_Stm32_Driver_V1.3_20260120 source; no vendor binary is included.
 */
#ifndef XIAO147_RAW_CAPTURE_H
#define XIAO147_RAW_CAPTURE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define XIAO147_RAW_MAX_CHANNELS 13U
#define XIAO147_RAW_SESSION_MS 60000U
#define XIAO147_RAW_CLEANUP_MS 2000U
#define XIAO147_RAW_FAST_FRAME_MS 20U

enum xiao147_raw_mode {
    XIAO147_RAW_MODE_ALTERNATE = 0,
    XIAO147_RAW_MODE_FAST_DIFF = 1,
    /* Automatic owner: fresh-sample watchdog instead of a diagnostic lease. */
    XIAO147_RAW_MODE_AUTO_DIFF = 2,
};

enum xiao147_raw_type {
    XIAO147_RAW_DATA = 1,
    XIAO147_RAW_DIFF = 2,
};

enum xiao147_raw_event {
    XIAO147_RAW_STARTED,
    XIAO147_RAW_VERSION,
    XIAO147_RAW_CHANNELS,
    XIAO147_RAW_ERROR,
    XIAO147_RAW_RESTORING,
    XIAO147_RAW_RESTORED,
    XIAO147_RAW_FAULT,
};

enum xiao147_raw_error {
    XIAO147_RAW_OK = 0,
    XIAO147_RAW_IO_ERROR = -1,
    XIAO147_RAW_ID_MISMATCH = -2,
    XIAO147_RAW_VERSION_MISMATCH = -3,
    XIAO147_RAW_BAD_CHANNELS = -4,
    XIAO147_RAW_ACK_TIMEOUT = -5,
    XIAO147_RAW_SCAN_TIMEOUT = -6,
    XIAO147_RAW_BAD_NORMAL_REPORT = -7,
    XIAO147_RAW_CANCELLED = -8,
    XIAO147_RAW_SESSION_TIMEOUT = -9,
    XIAO147_RAW_CLEANUP_TIMEOUT = -10,
};

/* read/write must return zero only after a complete transfer. Callbacks are
 * synchronous but must have bounded I2C timeouts. Neither may reset the chip.
 * All functions and callbacks run on the single touch-poll owner, not from
 * interrupts or competing threads. sample's array is valid only in callback.
 */
struct xiao147_raw_ops {
    int (*read)(void *context, uint8_t reg, uint8_t *buffer, size_t length);
    int (*write)(void *context, uint8_t reg, const uint8_t *payload, size_t length);
    void (*event)(void *context, enum xiao147_raw_event event, int detail);
    void (*sample)(void *context, enum xiao147_raw_type type, uint8_t channels,
                   const int32_t *values, uint32_t elapsed_ms);
};

/* Exposed for static allocation and diagnostics, not caller mutation. */
enum xiao147_raw_phase {
    XIAO147_RAW_IDLE,
    XIAO147_RAW_CHECK_ID,
    XIAO147_RAW_CHECK_VERSION,
    XIAO147_RAW_ENTER_WRITE,
    XIAO147_RAW_ENTER_ACK,
    XIAO147_RAW_READ_CHANNELS,
    XIAO147_RAW_SELECT_TYPE,
    XIAO147_RAW_SCAN_START,
    XIAO147_RAW_SCAN_POLL,
    XIAO147_RAW_READ_SAMPLE,
    XIAO147_RAW_PAIR_WAIT,
    XIAO147_RAW_RESTORE_WRITE,
    XIAO147_RAW_RESTORE_ACK,
    XIAO147_RAW_RESTORE_ID,
    XIAO147_RAW_RESTORE_REPORT,
    XIAO147_RAW_STICKY_FAULT,
};

struct xiao147_raw_capture {
    struct xiao147_raw_ops ops;
    void *context;
    enum xiao147_raw_phase phase;
    enum xiao147_raw_mode mode;
    enum xiao147_raw_type type;
    enum xiao147_raw_error last_error;
    int io_error;
    uint16_t expected_version;
    uint16_t firmware_version;
    uint32_t started_ms;
    uint32_t phase_ms;
    uint32_t pair_ms;
    uint32_t cleanup_ms;
    uint32_t last_io_ms;
    /* Observed tick/acquisition timestamps, not an assumed sample rate.
     * Counters include attempted transfers/scans, even when they fail.
     * Sample statistics advance only after a successful, fresh read.
     */
    uint32_t frame_started_ms;
    uint32_t io_calls;
    uint32_t scan_requests;
    uint32_t samples_received;
    uint32_t last_sample_ms;
    uint32_t last_interval_ms;
    uint32_t min_interval_ms;
    uint32_t max_interval_ms;
    uint8_t channels;
    uint8_t channel_layout[3];
    uint8_t restore_attempts;
    bool page_write_attempted;
    bool sample_valid;
    int32_t values[XIAO147_RAW_MAX_CHANNELS];
};

void xiao147_raw_capture_init(struct xiao147_raw_capture *capture,
                             const struct xiao147_raw_ops *ops, void *context);
/* expected_version is an explicitly approved firmware version, obtained by
 * read-only inspection. A mismatch is rejected before ANY factory writes.
 * start does not perform I2C. False means already active or missing callbacks.
 */
bool xiao147_raw_capture_start(struct xiao147_raw_capture *capture, uint32_t now_ms,
                              uint16_t expected_version);
/* FAST_DIFF uses exactly the same verified factory interface, selects DIFF
 * once, and triggers a fresh scan for every frame. A 20ms minimum between
 * scan starts is a ceiling of 50Hz, NOT a claim about achieved hardware rate.
 * Caller must independently verify no contacts before starting. This engine
 * does not generate HID or perform calibration/controller-firmware writes.
 */
bool xiao147_raw_capture_start_mode(struct xiao147_raw_capture *capture, uint32_t now_ms,
                                   uint16_t expected_version, enum xiao147_raw_mode mode);
void xiao147_raw_capture_stop(struct xiao147_raw_capture *capture, uint32_t now_ms);
/* At most one I2C callback per tick. Unsigned elapsed time handles uptime wrap.
 * Alternate mode remains deliberately slow. FAST_DIFF may be polled every
 * 4ms; status reads still wait >=5ms and scan starts are spaced >=20ms.
 */
void xiao147_raw_capture_tick(struct xiao147_raw_capture *capture, uint32_t now_ms);
/* active remains true throughout restoration, including a sticky failure.
 * Caller MUST suppress ordinary 0x63 polling and ALL mouse-generating paths
 * while active. After successful restoration, swallow touch until real lift.
 */
bool xiao147_raw_capture_active(const struct xiao147_raw_capture *capture);
bool xiao147_raw_capture_faulted(const struct xiao147_raw_capture *capture);

#endif
