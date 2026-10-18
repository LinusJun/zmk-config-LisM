/* Display-only preference. Never reads, migrates or deletes Bluetooth bonds. */
#include <errno.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/logging/log.h>
#include <zmk/workqueue.h>
#include "xiao147_palette.h"

LOG_MODULE_REGISTER(xiao147_palette, LOG_LEVEL_INF);
static atomic_t bright = ATOMIC_INIT(0);
K_MUTEX_DEFINE(palette_lock);
static bool loaded, dirty, saved_valid;
static uint8_t saved;
static uint32_t generation;
static unsigned failures;
static void save_palette(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(palette_save_work, save_palette);

bool xiao147_palette_bright(void) { return atomic_get(&bright) != 0; }

static void schedule_save(unsigned delay_ms) {
    k_work_reschedule_for_queue(zmk_workqueue_lowprio_work_q(), &palette_save_work,
                                K_MSEC(delay_ms));
}

void xiao147_palette_set_bright(bool enabled) {
    k_mutex_lock(&palette_lock, K_FOREVER);
    bool changed = xiao147_palette_bright() != enabled;
    if (changed) {
        atomic_set(&bright, enabled);
        generation++;
        dirty = true;
        failures = 0;
    }
    bool ready = loaded;
    k_mutex_unlock(&palette_lock);
    if (changed && ready) { schedule_save(2000); }
}

static void save_palette(struct k_work *work) {
    ARG_UNUSED(work);
    k_mutex_lock(&palette_lock, K_FOREVER);
    uint8_t value = xiao147_palette_bright();
    uint32_t version = generation;
    if (!loaded || !dirty || (saved_valid && saved == value)) {
        if (saved_valid && saved == value) { dirty = false; }
        k_mutex_unlock(&palette_lock);
        return;
    }
    k_mutex_unlock(&palette_lock);
    int rc = settings_save_one("lism_display/palette", &value, sizeof(value));
    k_mutex_lock(&palette_lock, K_FOREVER);
    if (!rc) {
        saved = value;
        saved_valid = true;
        if (version == generation) { dirty = false; }
    } else if (version == generation) { failures++; }
    bool again = dirty && (version != generation || failures < 3);
    k_mutex_unlock(&palette_lock);
    if (rc) { LOG_WRN("Palette not saved (%d); current screen choice retained", rc); }
    if (again) { schedule_save(rc ? 5000 : 2000); }
}

static int palette_settings_set(const char *name, size_t len, settings_read_cb read,
                                 void *context) {
    if (strcmp(name, "palette")) { return -ENOENT; }
    uint8_t value;
    if (len != sizeof(value) || read(context, &value, sizeof(value)) != sizeof(value) ||
        value > 1) { return -EINVAL; }
    k_mutex_lock(&palette_lock, K_FOREVER);
    saved = value;
    saved_valid = true;
    /* A late settings load must not overwrite an explicit user choice. */
    if (!generation) { atomic_set(&bright, value); }
    k_mutex_unlock(&palette_lock);
    return 0;
}

static int palette_settings_commit(void) {
    k_mutex_lock(&palette_lock, K_FOREVER);
    loaded = true;
    bool pending = dirty;
    k_mutex_unlock(&palette_lock);
    /* Normal loading never writes. UI's display-queue redraw reads the choice. */
    if (pending) { schedule_save(2000); }
    return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(lism_display, "lism_display", NULL,
                              palette_settings_set, palette_settings_commit, NULL);
