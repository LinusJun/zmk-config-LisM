"""Sanitised regression of the actual portable AXS5106L RAW capture C engine.

No device access. The mock enforces the full register-write allowlist and page
ownership. RAW capture is diagnostic evidence gathering, not a multitouch fix.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "xiao147_raw_capture.h"

static const uint16_t words[13] = {
    0x0000, 0x0001, 0x00ff, 0x0100, 0x1234, 0x7fff, 0x8000,
    0x8001, 0xff00, 0xffff, 0xabcd, 0x55aa, 0x1100,
};
static const int32_t signed_words[13] = {
    0, 1, 255, 256, 4660, 32767, -32768, -32767,
    -256, -1, -21555, 21930, 4352,
};

struct mock {
    struct xiao147_raw_capture capture;
    uint32_t now;
    unsigned calls_this_tick, calls, writes, page_entries, page_returns;
    unsigned normal_reports, raw_samples, diff_samples, scans;
    unsigned events[8];
    uint32_t sample_times[64];
    enum xiao147_raw_phase fail_phase;
    bool fail_once, fail_always, failed, uncertain_write;
    bool bad_identity, bad_restore_identity, bad_report;
    bool enter_busy, restore_busy, scan_busy, returned_report_ok;
    uint16_t version;
    uint8_t layout[3], normal_report[14], page, selected_type;
};

static bool fail(struct mock *mock)
{
    mock->calls++;
    mock->calls_this_tick++;
    assert(mock->calls_this_tick == 1); /* no I2C loops inside one tick */
    if (mock->capture.phase == mock->fail_phase &&
        (mock->fail_always || (mock->fail_once && !mock->failed))) {
        mock->failed = true;
        return true;
    }
    return false;
}

static int read_register(void *context, uint8_t reg, uint8_t *buffer, size_t length)
{
    struct mock *mock = context;
    assert(xiao147_raw_capture_active(&mock->capture));
    assert(buffer != NULL && length > 0 && length <= 26);
    if (fail(mock)) { return -5; }
    memset(buffer, 0, length);
    switch (reg) {
    case 0x08:
        assert(mock->page == 0 && length == 3);
        assert(mock->capture.phase == XIAO147_RAW_CHECK_ID ||
               mock->capture.phase == XIAO147_RAW_RESTORE_ID);
        buffer[0] = 0x51; buffer[1] = 0x06; buffer[2] = 0x01;
        if (mock->bad_identity || (mock->bad_restore_identity &&
            mock->capture.phase == XIAO147_RAW_RESTORE_ID)) { buffer[2] = 0x02; }
        break;
    case 0x05:
        /* Calibration register 05 must NEVER be read on factory page. */
        assert(mock->page == 0 && length == 2 &&
               mock->capture.phase == XIAO147_RAW_CHECK_VERSION);
        buffer[0] = mock->version >> 8; buffer[1] = mock->version & 0xff;
        break;
    case 0xfe:
        assert(length == 2);
        assert(mock->capture.phase == XIAO147_RAW_ENTER_ACK ||
               mock->capture.phase == XIAO147_RAW_RESTORE_ACK);
        if (mock->capture.phase == XIAO147_RAW_ENTER_ACK) {
            assert(mock->page == 0x20);
            buffer[0] = mock->enter_busy;
        } else {
            assert(mock->page == 0);
            buffer[0] = mock->restore_busy;
        }
        /* SDK ack uses first byte only; deliberately nonzero second byte. */
        buffer[1] = 0x7f;
        break;
    case 0x0b:
        assert(mock->page == 0x20 && length == 3);
        memcpy(buffer, mock->layout, 3);
        break;
    case 0x03:
        assert(mock->page == 0x20 && length == 1 &&
               mock->capture.phase == XIAO147_RAW_SCAN_POLL);
        buffer[0] = mock->scan_busy;
        break;
    case 0x02:
        assert(mock->page == 0x20 &&
               length == 2U * mock->capture.channels && length <= 26);
        for (size_t index = 0; index < length / 2; index++) {
            buffer[2 * index] = words[index] >> 8;
            buffer[2 * index + 1] = words[index] & 0xff;
        }
        break;
    case 0x01:
        assert(mock->page == 0 && length == 14 &&
               mock->capture.phase == XIAO147_RAW_RESTORE_REPORT);
        mock->normal_reports++;
        memcpy(buffer, mock->normal_report, 14);
        if (mock->bad_report) { buffer[1] = 0xff; }
        else { mock->returned_report_ok = true; }
        break;
    default:
        assert(!"register outside read allowlist");
    }
    return 0;
}

static int write_register(void *context, uint8_t reg, const uint8_t *payload,
                          size_t length)
{
    struct mock *mock = context;
    assert(xiao147_raw_capture_active(&mock->capture));
    assert(payload != NULL);
    bool should_fail = fail(mock);
    mock->writes++;
    switch (reg) {
    case 0xfe:
        assert(length == 3 && payload[0] == 0x5a && payload[1] == 0xa5);
        assert(payload[2] == 0 || payload[2] == 0x20);
        if (payload[2] == 0x20) {
            assert(mock->capture.phase == XIAO147_RAW_ENTER_WRITE &&
                   mock->capture.page_write_attempted && !mock->bad_identity &&
                   mock->capture.firmware_version == mock->capture.expected_version);
            mock->page_entries++;
        } else {
            assert(mock->capture.phase == XIAO147_RAW_RESTORE_WRITE &&
                   mock->capture.restore_attempts >= 1 &&
                   mock->capture.restore_attempts <= 3);
            mock->page_returns++;
        }
        /* Test that a NACK can still have switched the controller page. */
        if (!should_fail || mock->uncertain_write) { mock->page = payload[2]; }
        break;
    case 0x00:
        assert(mock->page == 0x20 && length == 1 &&
               (payload[0] == 1 || payload[0] == 2) &&
               mock->capture.phase == XIAO147_RAW_SELECT_TYPE);
        mock->selected_type = payload[0];
        break;
    case 0x03:
        assert(mock->page == 0x20 && length == 1 && payload[0] == 1 &&
               mock->capture.phase == XIAO147_RAW_SCAN_START);
        mock->scans++;
        break;
    default:
        /* No calibration 05, CB, open/short, sleep, INT, debug or MTP writes. */
        assert(!"register outside WRITE ALLOWLIST");
    }
    return should_fail ? -5 : 0;
}

static void event(void *context, enum xiao147_raw_event kind, int detail)
{
    struct mock *mock = context;
    assert(kind >= XIAO147_RAW_STARTED && kind <= XIAO147_RAW_FAULT);
    mock->events[kind]++;
    if (kind == XIAO147_RAW_RESTORED) {
        assert(!xiao147_raw_capture_active(&mock->capture) &&
               mock->returned_report_ok && mock->normal_reports > 0);
    }
    if (kind == XIAO147_RAW_FAULT) {
        assert(xiao147_raw_capture_active(&mock->capture) &&
               xiao147_raw_capture_faulted(&mock->capture));
    }
    if (kind == XIAO147_RAW_VERSION) { assert(detail == mock->version); }
}

static void sample(void *context, enum xiao147_raw_type type, uint8_t channels,
                   const int32_t *values, uint32_t elapsed_ms)
{
    struct mock *mock = context;
    assert(channels > 0 && channels <= 13 && mock->page == 0x20 &&
           xiao147_raw_capture_active(&mock->capture) && type == mock->selected_type);
    assert(elapsed_ms == mock->now - mock->capture.started_ms);
    unsigned index = mock->raw_samples + mock->diff_samples;
    if (index < 64) { mock->sample_times[index] = mock->now; }
    if (type == XIAO147_RAW_DATA) {
        assert(mock->raw_samples == mock->diff_samples);
        mock->raw_samples++;
        for (unsigned i = 0; i < channels; i++) { assert(values[i] == words[i]); }
    } else {
        assert(type == XIAO147_RAW_DIFF && mock->raw_samples == mock->diff_samples + 1);
        mock->diff_samples++;
        for (unsigned i = 0; i < channels; i++) { assert(values[i] == signed_words[i]); }
    }
}

static void init(struct mock *mock, uint32_t now)
{
    memset(mock, 0, sizeof(*mock));
    mock->now = now;
    mock->version = 0x0101;
    mock->layout[0] = 6; mock->layout[1] = 7; mock->layout[2] = 0xf0;
    mock->fail_phase = XIAO147_RAW_IDLE;
    struct xiao147_raw_ops ops = {read_register, write_register, event, sample};
    xiao147_raw_capture_init(&mock->capture, &ops, mock);
    assert(!xiao147_raw_capture_active(&mock->capture) &&
           !xiao147_raw_capture_faulted(&mock->capture));
}

static void step(struct mock *mock, uint32_t delta)
{
    mock->now += delta;
    mock->calls_this_tick = 0;
    bool had_page_write = mock->capture.page_write_attempted;
    xiao147_raw_capture_tick(&mock->capture, mock->now);
    assert(mock->calls_this_tick <= 1);
    /* Leaving active state after page modification requires valid restore. */
    if (had_page_write && !xiao147_raw_capture_active(&mock->capture)) {
        assert(mock->events[XIAO147_RAW_RESTORED] == 1 && mock->returned_report_ok);
    }
}

static void reach(struct mock *mock, enum xiao147_raw_phase phase)
{
    for (unsigned ticks = 0; ticks < 500 && mock->capture.phase != phase; ticks++) {
        assert(xiao147_raw_capture_active(&mock->capture) &&
               !xiao147_raw_capture_faulted(&mock->capture));
        step(mock, 5);
    }
    assert(mock->capture.phase == phase);
}

static void finish_restore(struct mock *mock)
{
    for (unsigned ticks = 0; ticks < 500 &&
         xiao147_raw_capture_active(&mock->capture) &&
         !xiao147_raw_capture_faulted(&mock->capture); ticks++) { step(mock, 5); }
    assert(!xiao147_raw_capture_active(&mock->capture) && mock->page == 0 &&
           mock->events[XIAO147_RAW_RESTORED] == 1 && mock->normal_reports > 0);
}

static void finish_fault(struct mock *mock)
{
    for (unsigned ticks = 0; ticks < 500 &&
         !xiao147_raw_capture_faulted(&mock->capture); ticks++) { step(mock, 5); }
    assert(xiao147_raw_capture_active(&mock->capture) &&
           xiao147_raw_capture_faulted(&mock->capture) &&
           mock->events[XIAO147_RAW_RESTORED] == 0 &&
           mock->events[XIAO147_RAW_FAULT] == 1);
    unsigned calls = mock->calls;
    xiao147_raw_capture_stop(&mock->capture, mock->now);
    assert(!xiao147_raw_capture_start(&mock->capture, mock->now, 0x0101));
    step(mock, 10);
    assert(mock->calls == calls && xiao147_raw_capture_active(&mock->capture));
}

static void start(struct mock *mock)
{
    unsigned calls = mock->calls;
    assert(xiao147_raw_capture_start(&mock->capture, mock->now, 0x0101));
    assert(mock->calls == calls && mock->events[XIAO147_RAW_STARTED] == 1);
    assert(!xiao147_raw_capture_start(&mock->capture, mock->now, 0x0101));
}

static void contact(uint8_t *report, unsigned index, uint8_t id, uint8_t event_code,
                    unsigned x, unsigned y)
{
    unsigned off = 2 + 6 * index;
    report[off] = (event_code << 6) | (x >> 8);
    report[off + 1] = x & 0xff;
    report[off + 2] = (id << 4) | (y >> 8);
    report[off + 3] = y & 0xff;
}

int main(void)
{
    struct mock mock;
    init(&mock, 0); start(&mock);
    reach(&mock, XIAO147_RAW_PAIR_WAIT);
    assert(mock.raw_samples == 1 && mock.diff_samples == 1 && mock.scans == 2);
    unsigned calls = mock.calls;
    step(&mock, 0); assert(mock.calls == calls && mock.capture.phase == XIAO147_RAW_PAIR_WAIT);
    while (mock.diff_samples < 3) { step(&mock, 5); }
    /* Full pairs never exceed 10 Hz; RAW/DIFF within a pair are sequential. */
    assert(mock.sample_times[2] - mock.sample_times[0] >= 100 &&
           mock.sample_times[4] - mock.sample_times[2] >= 100);
    xiao147_raw_capture_stop(&mock.capture, mock.now);
    assert(xiao147_raw_capture_active(&mock.capture));
    finish_restore(&mock);
    assert(mock.page_entries == 1 && mock.page_returns == 1 &&
           mock.capture.last_error == XIAO147_RAW_CANCELLED);

    /* Identity/version gate must cause ZERO writes, including on mismatch. */
    init(&mock, 0); mock.bad_identity = true; start(&mock); step(&mock, 5);
    assert(!xiao147_raw_capture_active(&mock.capture) && mock.writes == 0 &&
           mock.capture.last_error == XIAO147_RAW_ID_MISMATCH);
    init(&mock, 0); mock.version = 0x0123; start(&mock); step(&mock, 5); step(&mock, 5);
    assert(!xiao147_raw_capture_active(&mock.capture) && mock.writes == 0 &&
           mock.capture.firmware_version == 0x0123 &&
           mock.capture.last_error == XIAO147_RAW_VERSION_MISMATCH);

    /* Every cancellable phase: never unblock without confirmed normal state. */
    for (enum xiao147_raw_phase phase = XIAO147_RAW_CHECK_ID;
         phase <= XIAO147_RAW_PAIR_WAIT; phase++) {
        init(&mock, 0); start(&mock); reach(&mock, phase);
        bool modified = mock.capture.page_write_attempted;
        xiao147_raw_capture_stop(&mock.capture, mock.now);
        if (modified) { finish_restore(&mock); }
        else { assert(!xiao147_raw_capture_active(&mock.capture) && mock.writes == 0); }
    }

    /* Every preflight/capture I2C phase fails once; uncertain page write too. */
    const enum xiao147_raw_phase phases[] = {
        XIAO147_RAW_CHECK_ID, XIAO147_RAW_CHECK_VERSION, XIAO147_RAW_ENTER_WRITE,
        XIAO147_RAW_ENTER_ACK, XIAO147_RAW_READ_CHANNELS, XIAO147_RAW_SELECT_TYPE,
        XIAO147_RAW_SCAN_START, XIAO147_RAW_SCAN_POLL, XIAO147_RAW_READ_SAMPLE,
    };
    for (unsigned index = 0; index < sizeof(phases) / sizeof(phases[0]); index++) {
        init(&mock, 0); start(&mock); reach(&mock, phases[index]);
        mock.fail_phase = phases[index]; mock.fail_once = true; mock.uncertain_write = true;
        step(&mock, 10);
        assert(mock.failed && mock.capture.io_error == -5);
        if (phases[index] <= XIAO147_RAW_CHECK_VERSION) {
            assert(!xiao147_raw_capture_active(&mock.capture) && mock.writes == 0);
        } else { finish_restore(&mock); }
    }

    /* Invalid topology never reads data; uint16 arithmetic catches >255 totals. */
    const uint8_t bad_layout[][3] = {
        {0, 0, 0}, {7, 7, 0xf0}, {255, 255, 0xf0},
        {255, 255, 239}, {4, 4, 0}, {1, 1, 13},
    };
    for (unsigned index = 0; index < sizeof(bad_layout) / sizeof(bad_layout[0]); index++) {
        init(&mock, 0); memcpy(mock.layout, bad_layout[index], 3); start(&mock);
        reach(&mock, XIAO147_RAW_READ_CHANNELS); step(&mock, 5);
        assert(mock.capture.last_error == XIAO147_RAW_BAD_CHANNELS && !mock.scans);
        finish_restore(&mock); assert(mock.raw_samples == 0 && mock.diff_samples == 0);
    }
    const uint8_t good_layout[][3] = {{1, 1, 0}, {3, 4, 1}, {0, 13, 0xff}};
    for (unsigned index = 0; index < sizeof(good_layout) / sizeof(good_layout[0]); index++) {
        init(&mock, 0); memcpy(mock.layout, good_layout[index], 3); start(&mock);
        reach(&mock, XIAO147_RAW_PAIR_WAIT);
        assert(mock.capture.channels == (index == 0 ? 1 : 13));
        xiao147_raw_capture_stop(&mock.capture, mock.now); finish_restore(&mock);
    }

    /* Busy status is bounded. A scan is triggered ONCE, never retriggered. */
    init(&mock, 0); mock.enter_busy = true; start(&mock);
    reach(&mock, XIAO147_RAW_ENTER_ACK);
    for (unsigned ticks = 0; mock.capture.phase == XIAO147_RAW_ENTER_ACK; ticks++) {
        assert(ticks <= 51); step(&mock, 5);
    }
    assert(mock.capture.last_error == XIAO147_RAW_ACK_TIMEOUT && mock.page_entries == 1);
    finish_restore(&mock);
    init(&mock, 0); mock.scan_busy = true; start(&mock);
    reach(&mock, XIAO147_RAW_SCAN_POLL);
    for (unsigned ticks = 0; mock.capture.phase == XIAO147_RAW_SCAN_POLL; ticks++) {
        assert(ticks <= 21); step(&mock, 5);
    }
    assert(mock.capture.last_error == XIAO147_RAW_SCAN_TIMEOUT && mock.scans == 1);
    finish_restore(&mock);

    /* Settle timing is nonblocking; no read before 10 ms page / 5 ms scan. */
    init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_ENTER_ACK);
    calls = mock.calls; step(&mock, 9); assert(mock.calls == calls);
    step(&mock, 1); assert(mock.calls == calls + 1);
    reach(&mock, XIAO147_RAW_SCAN_POLL);
    calls = mock.calls; step(&mock, 4); assert(mock.calls == calls);
    step(&mock, 1); assert(mock.calls == calls + 1);
    xiao147_raw_capture_stop(&mock.capture, mock.now); finish_restore(&mock);

    /* Restoration errors retry exactly the normal-page sequence, max 3. */
    const enum xiao147_raw_phase restore_phases[] = {
        XIAO147_RAW_RESTORE_WRITE, XIAO147_RAW_RESTORE_ACK,
        XIAO147_RAW_RESTORE_ID, XIAO147_RAW_RESTORE_REPORT,
    };
    for (unsigned index = 0; index < sizeof(restore_phases) / sizeof(restore_phases[0]); index++) {
        init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
        xiao147_raw_capture_stop(&mock.capture, mock.now); reach(&mock, restore_phases[index]);
        enum xiao147_raw_phase stopped_phase = mock.capture.phase;
        uint32_t stopped_deadline = mock.capture.cleanup_ms;
        calls = mock.calls;
        xiao147_raw_capture_stop(&mock.capture, mock.now);
        assert(mock.capture.phase == stopped_phase && mock.capture.cleanup_ms == stopped_deadline &&
               mock.calls == calls && xiao147_raw_capture_active(&mock.capture));
        finish_restore(&mock);

        init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
        xiao147_raw_capture_stop(&mock.capture, mock.now); reach(&mock, restore_phases[index]);
        mock.fail_phase = restore_phases[index]; mock.fail_once = true;
        step(&mock, 10); assert(mock.failed && xiao147_raw_capture_active(&mock.capture));
        finish_restore(&mock); assert(mock.page_returns == 2);

        init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
        xiao147_raw_capture_stop(&mock.capture, mock.now);
        mock.fail_phase = restore_phases[index]; mock.fail_always = true;
        finish_fault(&mock); assert(mock.page_returns == 3);
    }
    init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    mock.restore_busy = true; xiao147_raw_capture_stop(&mock.capture, mock.now);
    finish_fault(&mock); assert(mock.page_returns == 3);
    init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    mock.bad_restore_identity = true; xiao147_raw_capture_stop(&mock.capture, mock.now);
    finish_fault(&mock); assert(mock.page_returns == 3 && !mock.normal_reports);
    init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    mock.bad_report = true; xiao147_raw_capture_stop(&mock.capture, mock.now);
    finish_fault(&mock); assert(mock.page_returns == 3 && mock.normal_reports == 3);

    /* Invalid report count, event, coordinates and duplicate IDs fail closed. */
    for (unsigned kind = 0; kind < 5; kind++) {
        init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
        mock.normal_report[1] = kind == 0 ? 3 : kind == 4 ? 2 : 1;
        contact(mock.normal_report, 0, 0, kind == 1 ? 3 : 0,
                kind == 2 ? 172 : 171, kind == 3 ? 320 : 319);
        contact(mock.normal_report, 1, 0, 0, 40, 70);
        xiao147_raw_capture_stop(&mock.capture, mock.now); finish_fault(&mock);
        assert(mock.capture.last_error == XIAO147_RAW_BAD_NORMAL_REPORT);
    }
    for (unsigned count = 0; count <= 2; count++) {
        init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
        mock.normal_report[1] = count;
        contact(mock.normal_report, 0, 0, 2, 171, 319);
        contact(mock.normal_report, 1, 1, 0, 40, 70);
        xiao147_raw_capture_stop(&mock.capture, mock.now); finish_restore(&mock);
    }

    /* Total capture timeout and cleanup have independent clocks. */
    init(&mock, 0); start(&mock); step(&mock, XIAO147_RAW_SESSION_MS);
    assert(!xiao147_raw_capture_active(&mock.capture) && mock.writes == 0 &&
           mock.capture.last_error == XIAO147_RAW_SESSION_TIMEOUT);
    init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    step(&mock, XIAO147_RAW_SESSION_MS - mock.now);
    assert(mock.capture.last_error == XIAO147_RAW_SESSION_TIMEOUT &&
           xiao147_raw_capture_active(&mock.capture));
    finish_restore(&mock);
    init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    xiao147_raw_capture_stop(&mock.capture, mock.now);
    calls = mock.calls; step(&mock, XIAO147_RAW_CLEANUP_MS);
    assert(mock.calls == calls && mock.capture.last_error == XIAO147_RAW_CLEANUP_TIMEOUT);
    finish_fault(&mock);

    /* uint32 uptime wrap must not cause immediate timeout or an endless scan. */
    init(&mock, UINT32_MAX - 50U); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    assert(mock.now < 100 && mock.raw_samples == 1 && mock.diff_samples == 1);
    xiao147_raw_capture_stop(&mock.capture, mock.now); finish_restore(&mock);
    init(&mock, UINT32_MAX - 50U); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    step(&mock, XIAO147_RAW_SESSION_MS - (mock.now - mock.capture.started_ms));
    assert(mock.capture.last_error == XIAO147_RAW_SESSION_TIMEOUT); finish_restore(&mock);

    /* Repeated stop never resets the cleanup deadline or re-enables input. */
    init(&mock, 0); start(&mock); reach(&mock, XIAO147_RAW_PAIR_WAIT);
    xiao147_raw_capture_stop(&mock.capture, mock.now);
    uint32_t cleanup_start = mock.capture.cleanup_ms;
    step(&mock, 500); xiao147_raw_capture_stop(&mock.capture, mock.now);
    assert(mock.capture.cleanup_ms == cleanup_start && xiao147_raw_capture_active(&mock.capture));
    step(&mock, 1500); finish_fault(&mock);

    /* Null optional callbacks are valid; missing I2C operations never start. */
    struct xiao147_raw_capture no_callbacks;
    xiao147_raw_capture_init(&no_callbacks, NULL, NULL);
    assert(!xiao147_raw_capture_start(&no_callbacks, 0, 0x0101));
    xiao147_raw_capture_tick(&no_callbacks, 0);
    xiao147_raw_capture_stop(&no_callbacks, 0);
    init(&mock, 0); mock.capture.ops.event = NULL; mock.capture.ops.sample = NULL;
    assert(xiao147_raw_capture_start(&mock.capture, mock.now, 0x0101));
    reach(&mock, XIAO147_RAW_PAIR_WAIT);
    xiao147_raw_capture_stop(&mock.capture, mock.now);
    for (unsigned ticks = 0; ticks < 50 && xiao147_raw_capture_active(&mock.capture); ticks++) {
        mock.calls_this_tick = 0; mock.now += 5;
        xiao147_raw_capture_tick(&mock.capture, mock.now);
    }
    assert(!xiao147_raw_capture_active(&mock.capture) && mock.returned_report_ok);

    puts("PASS: actual AXS RAW/DIFF C, strict write allowlist, identity/version gate, "
         "13-channel bounds, endian/sign decoding, one-I/O ticks, timing, every-phase "
         "cancel/error, bounded restoration, fail-closed input ownership and uptime wrap");
}
'''

with tempfile.TemporaryDirectory(prefix="xiao147-raw-capture-") as directory:
    folder = Path(directory)
    source = folder / "test.c"
    source.write_text(harness)
    binary = folder / "test"
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        "-I", str(root / "src"), str(root / "src/xiao147_raw_capture.c"),
        str(source), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
