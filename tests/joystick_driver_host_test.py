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
clean_overlay = re.sub(r'/\*.*?\*/', '', overlay, flags=re.S)
assert re.search(r'\bswap-axes\s*;', clean_overlay)
assert not re.search(r'\binvert-[xy]\s*;', clean_overlay)
assert re.search(r'\bpreserve-calibrated-center\s*;', overlay)
assert '.preserve_calibrated_center = DT_PROP_OR(JOYSTICK_NODE, preserve_calibrated_center, false)' in source
assert 'DT_NODE_HAS_PROP(JOYSTICK_NODE, sector_hysteresis_degrees)' in source
assert re.search(r'sector-hysteresis-degrees\s*=\s*<2>', overlay)
assert not re.search(r'\bstrict-sector-boundaries\s*;', overlay)
types = source[source.index('enum joystick_column'):source.index('static int read_axis')]
work = source[source.index('static void report'):source.index('static int joystick_configure')]
work += source[source.index('static int joystick_disable'):source.index('static int joystick_init')]
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
static int button_value, scheduled_delay;
static int read_axis(const struct device *adc, uint8_t channel, int16_t *value) {
    (void)adc;
    if (read_error) return -5;
    *value = channel == 2 ? raw_x : raw_y;
    return 0;
}
static int gpio_pin_get_dt(const struct gpio_dt_spec *gpio) { (void)gpio; return button_value; }
static int64_t k_uptime_get(void) { return uptime; }
static struct k_work_delayable *k_work_delayable_from_work(struct k_work *work) {
    return CONTAINER_OF(work, struct k_work_delayable, work);
}
static void k_work_schedule(struct k_work_delayable *work, int delay) {
    (void)work; scheduled_delay = delay;
}
static void k_work_cancel_delayable(struct k_work_delayable *work) { (void)work; }
'''
test = r'''
static struct joystick_config cfg = {
    .x_channel = 2, .y_channel = 3, .poll_ms = 10,
    .activation = 300, .release = 240, .calibration_samples = 64,
    .center_min = 100, .center_max = 3400, .calibration_max_spread = 100,
    .sample_min = 40, .sample_max = 4000,
    .swap_axes = true, .invert_x = false, .invert_y = false,
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
    assert(!((callback_mask & BIT(JOY_SPRINT)) && (callback_mask & JOY_MASK_DOWN)));
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
        tick(2859, 943, false); assert(callback_mask == 6);
        unsigned before = callback_count;
        tick(0, 0, error_kind == 1);
        assert(callback_mask == 0 && callback_count >= before + 2);
        assert(data.calibrated && data.x_center == 1803 && data.y_center == 1955);
        for (int i = 0; i < 80; ++i) tick(2859, 943, false);
        assert(data.calibrated && data.x_center == 1803 && data.y_center == 1955);
        assert(callback_mask == 6);
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
# Fixture labels describe the previous grip; expected masks rotate 180deg.
label_masks = dict(up=2, down=1, left=8, right=4, up_left=10,
                   down_left=9, down_right=5, up_right=6, neutral_start=0, neutral_end=0)
previous = 0
records = []
counts = Counter()
for row in rows:
    dx, dy = row['y'] - row['center_y'], row['x'] - row['center_x']
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
assert counts['up_left', 10] == 9
test += r'''
static void processed_tick(int x, int y, bool fail) {
    if (cfg.invert_x) x = -x;
    if (cfg.invert_y) y = -y;
    if (cfg.swap_axes) { int t = x; x = y; y = t; }
    tick(data.x_center + x, data.y_center + y, fail);
}
int sprint_idle_tests(void) {
    const unsigned shift = BIT(JOY_SPRINT), space = BIT(JOY_PRESS);
    for (int kind = 0; kind < 2; ++kind) {
        cfg.eight_sector = kind == 0;
        cfg.swap_axes = true; cfg.invert_x = kind == 1;
        cfg.activation = kind == 0 ? 300 : 150;
        cfg.release = kind == 0 ? 240 : 90;
        cfg.idle_timeout_ms = 300000; cfg.idle_poll_ms = 50;
        cfg.sprint_full_radius = 1400;
        cfg.sprint_enter_percent = 50; cfg.sprint_exit_percent = 45;
        cfg.press.port = kind == 1 ? (const void *)1 : NULL;
        button_value = 0;
        initial_calibration(true);
        processed_tick(0, -500, false); assert(callback_mask == JOY_MASK_UP);
        processed_tick(0, -700, false); assert(callback_mask == JOY_MASK_UP);
        processed_tick(0, -701, false); assert(callback_mask == (JOY_MASK_UP | shift));
        unsigned held_callbacks = callback_count;
        for (int i=0; i<80; ++i) processed_tick(0, -1000, false);
        assert(callback_count == held_callbacks); /* held, no typing macro */
        processed_tick(0, -680, false); assert(callback_mask & shift);
        processed_tick(0, -631, false); assert(callback_mask & shift);
        processed_tick(0, -630, false); assert(callback_mask == JOY_MASK_UP);
        /* Diagonal uses radial travel: neither axis alone exceeds 700. */
        processed_tick(-400, -400, false); assert(callback_mask == (JOY_MASK_UP | JOY_MASK_LEFT));
        processed_tick(-500, -500, false); assert(callback_mask == (JOY_MASK_UP | JOY_MASK_LEFT | shift));
        processed_tick(500, -500, false); assert(callback_mask == (JOY_MASK_UP | JOY_MASK_RIGHT | shift));
        processed_tick(1000, 0, false); assert(callback_mask == JOY_MASK_RIGHT);
        processed_tick(0, 1000, false); assert(callback_mask == JOY_MASK_DOWN);
        processed_tick(-1000, 1000, false); assert(callback_mask == (JOY_MASK_DOWN | JOY_MASK_LEFT));
        processed_tick(-1000, 0, false); assert(callback_mask == JOY_MASK_LEFT);
        processed_tick(0, -1000, false); assert(callback_mask & shift);
        processed_tick(0, 0, true); assert(callback_mask == 0 && data.calibrated);
        processed_tick(0, -1000, false); assert(callback_mask & shift);
        tick(0,0,false); assert(callback_mask == 0 && data.calibrated);
        processed_tick(0, -1000, false); processed_tick(0, 0, false);
        assert(callback_mask == 0);
        if (kind == 1) {
            button_value = 1;
            processed_tick(0,-1000,false); assert(callback_mask == (JOY_MASK_UP | shift | space));
            processed_tick(0,0,true); assert(callback_mask == space); /* independent push */
            processed_tick(0,0,false); button_value=0;
            processed_tick(0,0,false); assert(callback_mask == 0);
        }
        /* Six-minute sustained game movement never goes into slow sampling. */
        processed_tick(0,-1000,false);
        uptime += 360000; processed_tick(0,-1000,false); assert(scheduled_delay==10);
        processed_tick(0,0,false); uptime += 300001;
        processed_tick(0,0,false); assert(scheduled_delay==50);
        processed_tick(0,-1000,false); assert(scheduled_delay==10 && (callback_mask & shift));
        /* Invalid ADC is not continuous neutral; require another five minutes. */
        processed_tick(0,0,false); uptime += 300001;
        processed_tick(0,0,false); assert(scheduled_delay==50);
        tick(0,0,true); assert(scheduled_delay==10 && callback_mask==0);
        processed_tick(0,0,false); assert(scheduled_delay==10);
        processed_tick(0,-1000,false);
        joystick_disable(&dev); assert(callback_mask==0 && !data.enabled);
    }
    /* Exact requested JP19 mapping from OLD raw-axis directions. */
    cfg.eight_sector=false; cfg.swap_axes=true; cfg.invert_x=true;
    cfg.sprint_full_radius=0; cfg.press.port=NULL; button_value=0;
    initial_calibration(true);
    tick(1803, 955, false); assert(callback_mask==JOY_MASK_RIGHT); /* old W -> D */
    processed_tick(0,0,false);
    tick(803, 1955, false); assert(callback_mask==JOY_MASK_UP);    /* old A -> W */
    processed_tick(0,0,false);
    tick(1803, 2955, false); assert(callback_mask==JOY_MASK_LEFT); /* old S -> A */
    processed_tick(0,0,false);
    tick(2803, 1955, false); assert(callback_mask==JOY_MASK_DOWN); /* old D -> S */
    cfg.eight_sector=true; cfg.invert_x=false; cfg.activation=300; cfg.release=240;
    cfg.idle_timeout_ms=0; cfg.idle_poll_ms=0;
    puts("PASS: Circle/JP19 sprint threshold/hysteresis, simultaneous WA/WD, no repeat macro, independent Space, nonforward/error/rail/disable release, six-minute held input, neutral slow scan/wake/error reset, exact JP19 rotation");
    return 0;
}
'''

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
    tick(2250, 2814, false); assert(callback_mask == 8);
    tick(2400, 2814, false); assert(callback_mask == 8);
    tick(1950, 1814, false); assert(callback_mask == 0);
    tick(2400, 2814, false); assert(callback_mask == 10);
    puts("PASS: actual driver sparse chronological replay 231 samples / 119 labelled observations; 9 recorded prior-grip left-up samples now SD; grip mapping rotated 180deg; no new hardware validation");
    return 0;
}
"""
test += replay.replace('    fault_tests();', '    fault_tests();\n    sprint_idle_tests();')

with tempfile.TemporaryDirectory(prefix='circlepad-driver-test-') as directory:
    folder = Path(directory)
    c = folder / 'driver.c'
    c.write_text(prelude + '\n#include "' + str(root / 'src/joystick_sector.h') + '"\n' + types + work + test)
    binary = folder / 'test'
    subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
