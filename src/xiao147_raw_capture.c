#include "xiao147_raw_capture.h"

#include <string.h>

#define PAGE_SETTLE_MS 10U
#define PAGE_ACK_MS 250U
#define SCAN_SETTLE_MS 5U
#define SCAN_TIMEOUT_MS 100U
#define PAIR_INTERVAL_MS 100U
#define RESTORE_MAX_ATTEMPTS 3U

static void notify(struct xiao147_raw_capture *capture, enum xiao147_raw_event event,
                   int detail)
{
    if (capture->ops.event != NULL) {
        capture->ops.event(capture->context, event, detail);
    }
}

static bool elapsed(uint32_t now_ms, uint32_t then_ms, uint32_t duration_ms)
{
    return now_ms - then_ms >= duration_ms;
}

static int read_data(struct xiao147_raw_capture *capture, uint8_t reg,
                     uint8_t *buffer, size_t length)
{
    capture->io_calls++;
    return capture->ops.read(capture->context, reg, buffer, length);
}

static int write_data(struct xiao147_raw_capture *capture, uint8_t reg,
                      const uint8_t *payload, size_t length)
{
    capture->io_calls++;
    return capture->ops.write(capture->context, reg, payload, length);
}

static bool restoring(const struct xiao147_raw_capture *capture)
{
    return capture->phase >= XIAO147_RAW_RESTORE_WRITE &&
           capture->phase <= XIAO147_RAW_RESTORE_REPORT;
}

static bool identity_valid(const uint8_t identity[3])
{
    return identity[0] == 0x51 && identity[1] == 0x06 && identity[2] == 0x01;
}

static bool normal_report_valid(const uint8_t report[14])
{
    uint8_t count = report[1];
    if (count > 2) { return false; }
    for (uint8_t index = 0; index < count; index++) {
        size_t offset = 2U + 6U * index;
        uint16_t x = ((uint16_t)(report[offset] & 0x0f) << 8) | report[offset + 1];
        uint16_t y = ((uint16_t)(report[offset + 2] & 0x0f) << 8) | report[offset + 3];
        if ((report[offset] >> 6) > 2 || x >= 172 || y >= 320) { return false; }
    }
    /* Duplicate IDs cannot establish a sane two-contact normal frame. */
    return count != 2 || (report[4] >> 4) != (report[10] >> 4);
}

static void fault(struct xiao147_raw_capture *capture,
                  enum xiao147_raw_error reason)
{
    capture->last_error = reason;
    capture->sample_valid = false;
    capture->phase = XIAO147_RAW_STICKY_FAULT;
    notify(capture, XIAO147_RAW_FAULT, reason);
}

static void begin_restore(struct xiao147_raw_capture *capture, uint32_t now_ms,
                          enum xiao147_raw_error reason)
{
    if (restoring(capture) || capture->phase == XIAO147_RAW_STICKY_FAULT) { return; }
    capture->last_error = reason;
    capture->sample_valid = false;
    if (reason != XIAO147_RAW_CANCELLED && reason != XIAO147_RAW_SESSION_TIMEOUT) {
        notify(capture, XIAO147_RAW_ERROR, reason);
    }
    /* Before a page-write attempt, the verified normal interface has not
     * been altered. In particular, never write to an unverified identity. */
    if (!capture->page_write_attempted) {
        capture->phase = XIAO147_RAW_IDLE;
        return;
    }
    capture->cleanup_ms = now_ms;
    capture->phase_ms = now_ms;
    capture->last_io_ms = now_ms - PAGE_SETTLE_MS;
    capture->restore_attempts = 0;
    capture->phase = XIAO147_RAW_RESTORE_WRITE;
    notify(capture, XIAO147_RAW_RESTORING, reason);
}

static void retry_restore(struct xiao147_raw_capture *capture, uint32_t now_ms,
                          enum xiao147_raw_error reason)
{
    capture->last_error = reason;
    if (capture->restore_attempts >= RESTORE_MAX_ATTEMPTS) {
        fault(capture, reason);
        return;
    }
    capture->last_io_ms = now_ms;
    capture->phase = XIAO147_RAW_RESTORE_WRITE;
}

void xiao147_raw_capture_init(struct xiao147_raw_capture *capture,
                             const struct xiao147_raw_ops *ops, void *context)
{
    memset(capture, 0, sizeof(*capture));
    if (ops != NULL) { capture->ops = *ops; }
    capture->context = context;
    capture->phase = XIAO147_RAW_IDLE;
}

bool xiao147_raw_capture_start(struct xiao147_raw_capture *capture, uint32_t now_ms,
                              uint16_t expected_version)
{
    return xiao147_raw_capture_start_mode(capture, now_ms, expected_version,
                                         XIAO147_RAW_MODE_ALTERNATE);
}

bool xiao147_raw_capture_start_mode(struct xiao147_raw_capture *capture, uint32_t now_ms,
                                   uint16_t expected_version, enum xiao147_raw_mode mode)
{
    if (capture->phase != XIAO147_RAW_IDLE || capture->ops.read == NULL ||
        capture->ops.write == NULL ||
        (mode != XIAO147_RAW_MODE_ALTERNATE && mode != XIAO147_RAW_MODE_FAST_DIFF)) { return false; }
    capture->phase = XIAO147_RAW_CHECK_ID;
    capture->mode = mode;
    capture->type = mode == XIAO147_RAW_MODE_FAST_DIFF ? XIAO147_RAW_DIFF : XIAO147_RAW_DATA;
    capture->last_error = XIAO147_RAW_OK;
    capture->io_error = 0;
    capture->expected_version = expected_version;
    capture->firmware_version = 0;
    capture->started_ms = now_ms;
    capture->phase_ms = now_ms;
    capture->pair_ms = now_ms;
    capture->frame_started_ms = now_ms;
    capture->io_calls = 0;
    capture->scan_requests = 0;
    capture->samples_received = 0;
    capture->last_sample_ms = 0;
    capture->last_interval_ms = 0;
    capture->min_interval_ms = 0;
    capture->max_interval_ms = 0;
    capture->channels = 0;
    capture->restore_attempts = 0;
    capture->page_write_attempted = false;
    capture->sample_valid = false;
    memset(capture->channel_layout, 0, sizeof(capture->channel_layout));
    memset(capture->values, 0, sizeof(capture->values));
    notify(capture, XIAO147_RAW_STARTED, expected_version);
    return true;
}

void xiao147_raw_capture_stop(struct xiao147_raw_capture *capture, uint32_t now_ms)
{
    if (capture->phase == XIAO147_RAW_IDLE ||
        capture->phase == XIAO147_RAW_STICKY_FAULT || restoring(capture)) { return; }
    begin_restore(capture, now_ms, XIAO147_RAW_CANCELLED);
}

bool xiao147_raw_capture_active(const struct xiao147_raw_capture *capture)
{
    return capture->phase != XIAO147_RAW_IDLE;
}

bool xiao147_raw_capture_faulted(const struct xiao147_raw_capture *capture)
{
    return capture->phase == XIAO147_RAW_STICKY_FAULT;
}

void xiao147_raw_capture_tick(struct xiao147_raw_capture *capture, uint32_t now_ms)
{
    uint8_t buffer[2U * XIAO147_RAW_MAX_CHANNELS];
    int result;
    if (capture->phase == XIAO147_RAW_IDLE ||
        capture->phase == XIAO147_RAW_STICKY_FAULT) { return; }
    if (restoring(capture)) {
        if (elapsed(now_ms, capture->cleanup_ms, XIAO147_RAW_CLEANUP_MS)) {
            fault(capture, XIAO147_RAW_CLEANUP_TIMEOUT);
            return;
        }
    } else if (elapsed(now_ms, capture->started_ms, XIAO147_RAW_SESSION_MS)) {
        begin_restore(capture, now_ms, XIAO147_RAW_SESSION_TIMEOUT);
        return;
    }

    switch (capture->phase) {
    case XIAO147_RAW_CHECK_ID:
        result = read_data(capture, 0x08, buffer, 3);
        if (result != 0) { goto io_failed; }
        if (!identity_valid(buffer)) {
            begin_restore(capture, now_ms, XIAO147_RAW_ID_MISMATCH);
            return;
        }
        capture->phase = XIAO147_RAW_CHECK_VERSION;
        return;
    case XIAO147_RAW_CHECK_VERSION:
        result = read_data(capture, 0x05, buffer, 2);
        if (result != 0) { goto io_failed; }
        capture->firmware_version = ((uint16_t)buffer[0] << 8) | buffer[1];
        notify(capture, XIAO147_RAW_VERSION, capture->firmware_version);
        if (capture->firmware_version != capture->expected_version) {
            begin_restore(capture, now_ms, XIAO147_RAW_VERSION_MISMATCH);
            return;
        }
        capture->phase = XIAO147_RAW_ENTER_WRITE;
        return;
    case XIAO147_RAW_ENTER_WRITE:
        /* Even an unsuccessful write can have switched the device page. */
        capture->page_write_attempted = true;
        buffer[0] = 0x5a; buffer[1] = 0xa5; buffer[2] = 0x20;
        result = write_data(capture, 0xfe, buffer, 3);
        if (result != 0) { goto io_failed; }
        capture->phase_ms = now_ms;
        capture->last_io_ms = now_ms;
        capture->phase = XIAO147_RAW_ENTER_ACK;
        return;
    case XIAO147_RAW_ENTER_ACK:
        if (elapsed(now_ms, capture->phase_ms, PAGE_ACK_MS)) {
            begin_restore(capture, now_ms, XIAO147_RAW_ACK_TIMEOUT);
            return;
        }
        if (!elapsed(now_ms, capture->last_io_ms, PAGE_SETTLE_MS)) { return; }
        result = read_data(capture, 0xfe, buffer, 2);
        if (result != 0) { goto io_failed; }
        capture->last_io_ms = now_ms;
        if (buffer[0] == 0) { capture->phase = XIAO147_RAW_READ_CHANNELS; }
        return;
    case XIAO147_RAW_READ_CHANNELS: {
        result = read_data(capture, 0x0b, buffer, 3);
        if (result != 0) { goto io_failed; }
        memcpy(capture->channel_layout, buffer, 3);
        uint16_t channels = buffer[2] >= 0xf0 ? (uint16_t)buffer[0] + buffer[1] :
                                              (uint16_t)buffer[0] * buffer[1] + buffer[2];
        if (channels == 0 || channels > XIAO147_RAW_MAX_CHANNELS) {
            begin_restore(capture, now_ms, XIAO147_RAW_BAD_CHANNELS);
            return;
        }
        capture->channels = (uint8_t)channels;
        capture->type = capture->mode == XIAO147_RAW_MODE_FAST_DIFF ?
                            XIAO147_RAW_DIFF : XIAO147_RAW_DATA;
        capture->pair_ms = now_ms;
        capture->phase = XIAO147_RAW_SELECT_TYPE;
        notify(capture, XIAO147_RAW_CHANNELS, capture->channels);
        return;
    }
    case XIAO147_RAW_SELECT_TYPE:
        buffer[0] = (uint8_t)capture->type;
        result = write_data(capture, 0x00, buffer, 1);
        if (result != 0) { goto io_failed; }
        capture->phase = XIAO147_RAW_SCAN_START;
        return;
    case XIAO147_RAW_SCAN_START:
        if (capture->mode == XIAO147_RAW_MODE_FAST_DIFF && capture->scan_requests != 0 &&
            !elapsed(now_ms, capture->frame_started_ms, XIAO147_RAW_FAST_FRAME_MS)) { return; }
        capture->frame_started_ms = now_ms;
        capture->scan_requests++;
        capture->sample_valid = false;
        memset(capture->values, 0, sizeof(capture->values));
        buffer[0] = 0x01;
        result = write_data(capture, 0x03, buffer, 1);
        if (result != 0) { goto io_failed; }
        capture->phase_ms = now_ms;
        capture->last_io_ms = now_ms;
        capture->phase = XIAO147_RAW_SCAN_POLL;
        return;
    case XIAO147_RAW_SCAN_POLL:
        if (elapsed(now_ms, capture->phase_ms, SCAN_TIMEOUT_MS)) {
            begin_restore(capture, now_ms, XIAO147_RAW_SCAN_TIMEOUT);
            return;
        }
        if (!elapsed(now_ms, capture->last_io_ms, SCAN_SETTLE_MS)) { return; }
        result = read_data(capture, 0x03, buffer, 1);
        if (result != 0) { goto io_failed; }
        capture->last_io_ms = now_ms;
        if (buffer[0] == 0) { capture->phase = XIAO147_RAW_READ_SAMPLE; }
        return;
    case XIAO147_RAW_READ_SAMPLE:
        if (capture->mode == XIAO147_RAW_MODE_FAST_DIFF &&
            elapsed(now_ms, capture->phase_ms, SCAN_TIMEOUT_MS)) {
            begin_restore(capture, now_ms, XIAO147_RAW_SCAN_TIMEOUT);
            return;
        }
        result = read_data(capture, 0x02, buffer, (size_t)capture->channels * 2U);
        if (result != 0) { goto io_failed; }
        for (uint8_t channel = 0; channel < capture->channels; channel++) {
            uint16_t bits = ((uint16_t)buffer[2U * channel] << 8) | buffer[2U * channel + 1U];
            capture->values[channel] = capture->type == XIAO147_RAW_DIFF && bits >= 0x8000 ?
                                           (int32_t)bits - 65536 : (int32_t)bits;
        }
        if (capture->samples_received != 0) {
            uint32_t interval = now_ms - capture->last_sample_ms;
            capture->last_interval_ms = interval;
            if (capture->samples_received == 1 || interval < capture->min_interval_ms) {
                capture->min_interval_ms = interval;
            }
            if (interval > capture->max_interval_ms) { capture->max_interval_ms = interval; }
        }
        capture->last_sample_ms = now_ms;
        capture->samples_received++;
        capture->sample_valid = true;
        if (capture->ops.sample != NULL) {
            capture->ops.sample(capture->context, capture->type, capture->channels,
                                 capture->values, now_ms - capture->started_ms);
        }
        /* A sample consumer may request a stop; never overwrite its cleanup
         * phase with the next scan/selection after returning from callback. */
        if (capture->phase != XIAO147_RAW_READ_SAMPLE) { return; }
        if (capture->mode == XIAO147_RAW_MODE_FAST_DIFF) {
            capture->phase = XIAO147_RAW_SCAN_START;
        } else if (capture->type == XIAO147_RAW_DATA) {
            capture->type = XIAO147_RAW_DIFF;
            capture->phase = XIAO147_RAW_SELECT_TYPE;
        } else {
            capture->phase = XIAO147_RAW_PAIR_WAIT;
        }
        return;
    case XIAO147_RAW_PAIR_WAIT:
        if (elapsed(now_ms, capture->pair_ms, PAIR_INTERVAL_MS)) {
            capture->pair_ms = now_ms;
            capture->type = XIAO147_RAW_DATA;
            capture->phase = XIAO147_RAW_SELECT_TYPE;
        }
        return;
    case XIAO147_RAW_RESTORE_WRITE:
        if (!elapsed(now_ms, capture->last_io_ms, PAGE_SETTLE_MS)) { return; }
        capture->restore_attempts++;
        buffer[0] = 0x5a; buffer[1] = 0xa5; buffer[2] = 0x00;
        result = write_data(capture, 0xfe, buffer, 3);
        if (result != 0) {
            capture->io_error = result;
            retry_restore(capture, now_ms, XIAO147_RAW_IO_ERROR);
            return;
        }
        capture->phase_ms = now_ms;
        capture->last_io_ms = now_ms;
        capture->phase = XIAO147_RAW_RESTORE_ACK;
        return;
    case XIAO147_RAW_RESTORE_ACK:
        if (elapsed(now_ms, capture->phase_ms, PAGE_ACK_MS)) {
            retry_restore(capture, now_ms, XIAO147_RAW_ACK_TIMEOUT);
            return;
        }
        if (!elapsed(now_ms, capture->last_io_ms, PAGE_SETTLE_MS)) { return; }
        result = read_data(capture, 0xfe, buffer, 2);
        if (result != 0) {
            capture->io_error = result;
            retry_restore(capture, now_ms, XIAO147_RAW_IO_ERROR);
            return;
        }
        capture->last_io_ms = now_ms;
        if (buffer[0] == 0) { capture->phase = XIAO147_RAW_RESTORE_ID; }
        return;
    case XIAO147_RAW_RESTORE_ID:
        result = read_data(capture, 0x08, buffer, 3);
        if (result != 0) {
            capture->io_error = result;
            retry_restore(capture, now_ms, XIAO147_RAW_IO_ERROR);
            return;
        }
        if (!identity_valid(buffer)) {
            retry_restore(capture, now_ms, XIAO147_RAW_ID_MISMATCH);
            return;
        }
        capture->phase = XIAO147_RAW_RESTORE_REPORT;
        return;
    case XIAO147_RAW_RESTORE_REPORT:
        result = read_data(capture, 0x01, buffer, 14);
        if (result != 0) {
            capture->io_error = result;
            retry_restore(capture, now_ms, XIAO147_RAW_IO_ERROR);
            return;
        }
        if (!normal_report_valid(buffer)) {
            retry_restore(capture, now_ms, XIAO147_RAW_BAD_NORMAL_REPORT);
            return;
        }
        capture->phase = XIAO147_RAW_IDLE;
        notify(capture, XIAO147_RAW_RESTORED, capture->last_error);
        return;
    case XIAO147_RAW_IDLE:
    case XIAO147_RAW_STICKY_FAULT:
        return;
    }
    return;

io_failed:
    capture->io_error = result;
    begin_restore(capture, now_ms, XIAO147_RAW_IO_ERROR);
}
