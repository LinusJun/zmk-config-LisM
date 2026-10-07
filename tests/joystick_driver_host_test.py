"""Exercise the real driver work function with mocked Zephyr ADC/GPIO/work APIs."""
from pathlib import Path
import re
import json
import math
from collections import Counter
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'src/kscan_resistive_joystick.c').read_text()
overlay = (root / 'boards/shields/lism/lism_circlepad.overlay').read_text()
assert re.search(r'\bpreserve-calibrated-center\s*;', overlay)
assert '.preserve_calibrated_center = DT_PROP_OR(JOYSTICK_NODE, preserve_calibrated_center, false)' in source
assert 'DT_NODE_HAS_PROP(JOYSTICK_NODE, sector_hysteresis_degrees)' in source
assert re.search(r'sector-hysteresis-degrees\s*=\s*<2>', overlay)
assert not re.search(r'\bstrict-sector-boundaries\s*;', overlay)
types = source[source.index('enum joystick_column'):source.index('static int read_axis')]
work = source[source.index('static void report'):source.index('static int joystick_configure')]
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define BIT(n) (1u << (n))
#define CONTAINER_OF(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define K_MSEC(n) (n)
#define JOY_LOG_INTERVAL_MS 500
#define LOG_DBG(...) ((void)0)
#define LOG_INF(...) ((void)0)
#define LOG_WRN(...) (++warnings)
struct device { const void *config; void *data; };
struct k_work { int unused; };
struct k_work_delayable { struct k_work work; };
struct gpio_dt_spec { const void *port; };
typedef void (*kscan_callback_t)(const struct device *, uint32_t, uint32_t, bool);
static int16_t raw_x, raw_y;
static bool read_error;
static int64_t uptime;
static unsigned callback_mask, callback_count, warnings;
static int read_axis(const struct device *adc, uint8_t channel, int16_t *value) {
    (void)adc;
    if (read_error) return -5;
    *value = channel == 2 ? raw_x : raw_y;
    return 0;
}
static int gpio_pin_get_dt(const struct gpio_dt_spec *gpio) { (void)gpio; return 0; }
static int64_t k_uptime_get(void) { return uptime; }
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *work) {
    return CONTAINER_OF(work, struct k_work_delayable, work);
}
static void k_work_schedule(struct k_work_delayable *work, int delay) {
    (void)work; (void)delay;
}
'''
test = r'''
static struct joystick_config cfg = {
    .x_channel = 2, .y_channel = 3, .poll_ms = 10,
    .activation = 300, .release = 240, .calibration_samples = 64,
    .center_min = 100, .center_max = 3400, .calibration_max_spread = 100,
    .sample_min = 40, .sample_max = 4000,
    .swap_axes = true, .invert_x = true, .invert_y = true,
    .eight_sector = true, .configurable_sector_boundaries = true,
    .sector_hysteresis_degrees = 2,
    .preserve_calibrated_center = true,
};
static struct joystick_data data;
static struct device dev = { .config = &cfg, .data = &data };
static void callback(const struct device *device, uint32_t row, uint32_t col, bool down) {
    (void)device;
    assert(row == 0 && col < JOY_COLUMN_COUNT);
    callback_count++;
    if (down) callback_mask |= BIT(col); else callback_mask &= ~BIT(col);
}
static void tick(int x, int y, bool fail) {
    raw_x = x; raw_y = y; read_error = fail; uptime += 10;
    joystick_work(&data.work.work);
}
static void initial_calibration(bool preserve) {
    memset(&data, 0, sizeof(data)); data.dev = &dev; data.enabled = true;
    data.callback = callback; cfg.preserve_calibrated_center = preserve;
    callback_mask = callback_count = warnings = 0; uptime = 0;
    for (int i = 0; i < 64; ++i) tick(1803, 1955, false);
    assert(data.calibrated && data.x_center == 1803 && data.y_center == 1955);
    assert(callback_mask == 0);
}
int fault_tests(void) {
    /* Both real error branches must release callback state, retain the center,
     * and avoid accepting 80 stable held samples as a new neutral point. */
    for (int error_kind = 0; error_kind < 2; ++error_kind) {
        initial_calibration(true);
        tick(2859, 943, false); assert(callback_mask == 9);
        unsigned before = callback_count;
        tick(0, 0, error_kind == 1);
        assert(callback_mask == 0 && callback_count >= before + 2);
        assert(data.calibrated && data.x_center == 1803 && data.y_center == 1955);
        for (int i = 0; i < 80; ++i) tick(2859, 943, false);
        assert(data.calibrated && data.x_center == 1803 && data.y_center == 1955);
        assert(callback_mask == 9);
        tick(1948, 1819, false); assert(callback_mask == 0);
        assert(data.x_center == 1803 && data.y_center == 1955);
    }
    /* Errors before first successful calibration still reset its partial sum. */
    memset(&data, 0, sizeof(data)); data.dev = &dev; data.enabled = true;
    data.callback = callback; cfg.preserve_calibrated_center = true;
    callback_mask = 0;
    for (int i = 0; i < 20; ++i) tick(2859, 943, false);
    tick(0, 0, true); assert(!data.calibrated && data.samples == 0);
    for (int i = 0; i < 64; ++i) tick(1803, 1955, false);
    assert(data.calibrated && data.x_center == 1803 && data.y_center == 1955);
    /* Property off keeps the existing re-calibration behavior. */
    initial_calibration(false); tick(2859, 943, false); tick(0, 0, false);
    assert(!data.calibrated && callback_mask == 0);
    for (int i = 0; i < 64; ++i) tick(2859, 943, false);
    assert(data.calibrated && data.x_center == 2859 && data.y_center == 943);
    initial_calibration(false); tick(0, 0, true); assert(!data.calibrated);
    /* Persistent invalid samples release all keys without log flooding. */
    initial_calibration(true); tick(2859, 943, false); warnings = 0;
    for (int i = 0; i < 100; ++i) tick(0, 0, false);
    assert(data.calibrated && callback_mask == 0 && warnings <= 3);
    puts("PASS: actual driver rail/read failure callbacks, center retained across 80 held samples, observed neutral releases, initial calibration recovery, default compatibility, warning rate limit");
    return 0;
}
'''

# All samples in chronological order, including unlabeled transitions/gaps.
# Compare the real work function to an independent atan2 history model.
# Sparse replay is not proof of behavior between the logged samples.
rows = json.loads((root / 'tests/circlepad_recorded_trace.json').read_text())['samples']
directions = [8, 10, 2, 6, 4, 5, 1, 9]
label_masks = dict(up=1, down=2, left=4, right=8, up_left=5,
                   down_left=6, down_right=10, up_right=9, neutral_start=0, neutral_end=0)
previous = 0
records = []
counts = Counter()
for row in rows:
    dx, dy = -(row['y'] - row['center_y']), -(row['x'] - row['center_x'])
    radius = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(dy, dx)) % 360
    if radius <= (240 if previous else 300):
        expected = 0
    elif previous in directions and abs((angle - directions.index(previous) * 45 + 180) % 360 - 180) <= 24.5:
        expected = previous
    else:
        expected = directions[int((angle + 22.5) // 45) % 8]
    previous = expected
    if row['label']:
        intended = label_masks[row['label']] if radius > 300 else 0
        assert expected == intended, (row, expected, intended)
        counts[row['label'], expected] += 1
    records.append('{%d,%d,%d}' % (row['x'], row['y'], expected))
assert len(rows) == 231 and sum(counts.values()) == 119
assert counts['up_left', 5] == 9
replay = r"""
static const struct { int x, y, expected; } replay[] = {
""" + ',\n'.join(records) + r"""
};
int main(void) {
    fault_tests();
    memset(&data, 0, sizeof(data)); data.dev = &dev; data.enabled = true;
    data.callback = callback; callback_mask = 0;
    cfg.preserve_calibrated_center = true;
    cfg.configurable_sector_boundaries = true; cfg.sector_hysteresis_degrees = 2;
    for (int i = 0; i < 64; ++i) tick(1950, 1814, false);
    assert(data.calibrated);
    for (unsigned i = 0; i < sizeof(replay) / sizeof(replay[0]); ++i) {
        tick(replay[i].x, replay[i].y, false);
        assert(callback_mask == (unsigned)replay[i].expected);
        assert(data.x_center == 1950 && data.y_center == 1814);
    }
    tick(1950, 1814, false); assert(callback_mask == 0);
    /* No angular history survives neutral. No gain normalization occurs. */
    tick(2250, 2814, false); assert(callback_mask == 4);
    tick(2400, 2814, false); assert(callback_mask == 4);
    tick(1950, 1814, false); assert(callback_mask == 0);
    tick(2400, 2814, false); assert(callback_mask == 5);
    puts("PASS: actual driver sparse chronological replay 231 samples / 119 labelled observations; 9 recorded active left-up samples WA; no new hardware validation");
    return 0;
}
"""
test += replay

with tempfile.TemporaryDirectory(prefix='circlepad-driver-test-') as directory:
    folder = Path(directory)
    c = folder / 'driver.c'
    c.write_text(prelude + '\n#include "' + str(root / 'src/joystick_sector.h') + '"\n' + types + work + test)
    binary = folder / 'test'
    subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
