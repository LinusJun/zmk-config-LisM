#include "lism_hid_parser.h"
#include <limits.h>
#include <string.h>

#define GLOBAL_DEPTH 4
#define COLLECTION_DEPTH 8
#define LOCAL_USAGES 16

struct hid_globals {
    uint32_t page, size, count;
    int32_t minimum;
    uint32_t maximum;
    uint8_t id;
};
struct hid_locals {
    uint32_t usages[LOCAL_USAGES], minimum, maximum;
    uint8_t count;
    bool have_minimum, have_maximum;
};
static uint32_t unsigned_item(const uint8_t *bytes, size_t n)
{
    uint32_t value = 0;
    for (size_t i = 0; i < n; ++i) { value |= (uint32_t)bytes[i] << (i * 8); }
    return value;
}
static int32_t signed_item(uint32_t value, unsigned bits)
{
    if (bits == 32) {
        return value & UINT32_C(0x80000000) ?
            (int32_t)((int64_t)value - INT64_C(0x100000000)) : (int32_t)value;
    }
    if (bits && (value & (UINT32_C(1) << (bits - 1)))) {
        return (int32_t)((int64_t)value - (INT64_C(1) << bits));
    }
    return (int32_t)value;
}
static void capability(struct lism_hid_descriptor *d, uint32_t usage)
{
    uint32_t page = usage >> 16, code = usage & 65535U;
    if (page >= 0xff00) { d->capabilities |= LISM_HID_CAP_VENDOR; }
    else if (page == 7 || (page == 1 && code == 6)) { d->capabilities |= LISM_HID_CAP_KEYBOARD; }
    else if (page == 0x0d) { d->capabilities |= LISM_HID_CAP_DIGITIZER; }
    else if (page == 0x0c) { d->capabilities |= LISM_HID_CAP_CONSUMER; }
    else if (page == 1 && (code == 1 || code == 2)) { d->capabilities |= LISM_HID_CAP_MOUSE; }
    else if (page != 1 && page != 9) { d->capabilities |= LISM_HID_CAP_OTHER; }
}
static enum lism_hid_error usage_at(const struct hid_locals *l, unsigned index,
                                   uint32_t *usage)
{
    if (l->have_minimum != l->have_maximum) { return LISM_HID_ERR_MALFORMED; }
    if (l->have_minimum) {
        if (l->count || l->minimum > l->maximum ||
            (l->minimum >> 16) != (l->maximum >> 16)) { return LISM_HID_ERR_MALFORMED; }
        if (index > l->maximum - l->minimum) { *usage = 0; }
        else { *usage = l->minimum + index; }
    } else { *usage = index < l->count ? l->usages[index] : 0; }
    return LISM_HID_OK;
}
static int report_index(struct lism_hid_descriptor *d, uint8_t id)
{
    for (unsigned i = 0; i < d->report_count; ++i) {
        if (d->reports[i].id == id) { return (int)i; }
    }
    if (d->report_count == LISM_HID_MAX_REPORTS) { return -1; }
    unsigned index = d->report_count++;
    d->reports[index].id = id;
    return (int)index;
}
static enum lism_hid_error main_data(struct lism_hid_descriptor *d,
    const struct hid_globals *g, const struct hid_locals *l,
    unsigned tag, uint32_t flags, bool mouse)
{
    if (!g->size || !g->count) { return LISM_HID_ERR_MALFORMED; }
    if (g->size > 32 || g->count > LISM_HID_MAX_REPORT_BYTES * 8U) {
        return LISM_HID_ERR_LIMIT;
    }
    int index = report_index(d, g->id);
    if (index < 0) { return LISM_HID_ERR_LIMIT; }
    struct lism_hid_report *r = &d->reports[index];
    uint16_t *cursor = tag == 8 ? &r->input_bits : tag == 9 ? &r->output_bits : &r->feature_bits;
    uint32_t bits = g->size * g->count;
    if (bits > LISM_HID_MAX_REPORT_BYTES * 8U - *cursor) { return LISM_HID_ERR_LIMIT; }
    unsigned start = *cursor;
    *cursor += (uint16_t)bits;
    /* Output and feature have independent bit cursors. Never mistake their
     * fields for an input, even when they share the same report ID. */
    if (tag != 8 || (flags & 1) || !mouse) { return LISM_HID_OK; }
    for (unsigned n = 0; n < g->count; ++n) {
        uint32_t usage;
        enum lism_hid_error error = usage_at(l, n, &usage);
        if (error) { return error; }
        capability(d, usage);
        uint32_t page = usage >> 16, code = usage & 65535U;
        unsigned kind = 0;
        if (page == 1 && code == 0x30) { kind = LISM_HID_X; }
        else if (page == 1 && code == 0x31) { kind = LISM_HID_Y; }
        else if (page == 1 && code == 0x38) { kind = LISM_HID_WHEEL; }
        else if (page == 0x0c && code == 0x238) { kind = LISM_HID_PAN; }
        else if (page == 9 && code >= 1 && code <= 8) { kind = LISM_HID_BUTTON_1 + code - 1; }
        else { d->capabilities |= LISM_HID_CAP_UNSUPPORTED; continue; }
        /* HID arrays are selectors, NOT one bit per listed button. Ignore
         * them explicitly; other well-described mouse variables may remain. */
        if (!(flags & 2)) { d->capabilities |= LISM_HID_CAP_UNSUPPORTED; continue; }
        bool button = kind >= LISM_HID_BUTTON_1;
        if (!button && !(flags & 4)) {
            d->capabilities |= LISM_HID_CAP_ABSOLUTE;
            return LISM_HID_ERR_ABSOLUTE;
        }
        if ((flags & ~7U) || (button && (flags & 4))) {
            d->capabilities |= LISM_HID_CAP_UNSUPPORTED;
            return LISM_HID_ERR_UNSUPPORTED;
        }
        int32_t maximum = signed_item(g->maximum, 32);
        if (button) {
            if (g->minimum != 0 || g->maximum != 1) { return LISM_HID_ERR_RANGE; }
            uint8_t mask = (uint8_t)(1U << (kind - LISM_HID_BUTTON_1));
            if (r->button_mask & mask) { return LISM_HID_ERR_MALFORMED; }
            r->button_mask |= mask;
        } else {
            /* Relative cursor axes must declare a signed range fitting their
             * field width. No invented signedness or absolute conversion. */
            int64_t low = -(INT64_C(1) << (g->size - 1));
            int64_t high = (INT64_C(1) << (g->size - 1)) - 1;
            if (g->minimum >= 0 || maximum < 0 || g->minimum < low || maximum > high) {
                return LISM_HID_ERR_RANGE;
            }
            uint8_t mask = (uint8_t)(1U << (kind - 1));
            if (r->axis_mask & mask) { return LISM_HID_ERR_MALFORMED; }
            r->axis_mask |= mask;
        }
        if (d->field_count == LISM_HID_MAX_FIELDS) { return LISM_HID_ERR_LIMIT; }
        struct lism_hid_field *f = &d->fields[d->field_count++];
        f->report_index = (uint8_t)index; f->kind = (uint8_t)kind;
        f->bit_size = (uint8_t)g->size; f->bit_offset = (uint16_t)(start + n * g->size);
        f->logical_min = g->minimum; f->logical_max = button ? 1 : maximum;
        r->mouse_fields++;
    }
    return LISM_HID_OK;
}
enum lism_hid_error lism_hid_parse(const uint8_t *map, size_t length,
                                  struct lism_hid_descriptor *descriptor)
{
    if (!descriptor) { return LISM_HID_ERR_ARGUMENT; }
    memset(descriptor, 0, sizeof(*descriptor));
    if (!map || !length) { return LISM_HID_ERR_ARGUMENT; }
    if (length > LISM_HID_MAX_MAP_BYTES) { return LISM_HID_ERR_LIMIT; }
    struct lism_hid_descriptor d = {0};
    struct hid_globals g = {0}, stack[GLOBAL_DEPTH];
    struct hid_locals l = {0};
    bool collection[COLLECTION_DEPTH], blocked_collection[COLLECTION_DEPTH];
    unsigned depth = 0, globals = 0;
    enum lism_hid_error error = LISM_HID_OK;
    for (size_t at = 0; at < length;) {
        uint8_t prefix = map[at++];
        if (prefix == 0xfe) { error = LISM_HID_ERR_UNSUPPORTED; break; }
        unsigned size = prefix & 3; if (size == 3) { size = 4; }
        unsigned type = (prefix >> 2) & 3, tag = prefix >> 4;
        if (size > length - at) { error = LISM_HID_ERR_TRUNCATED; break; }
        uint32_t value = unsigned_item(map + at, size); at += size;
        if (type == 1) {
            switch (tag) {
            case 0:
                if (!size || value > 65535) { error = LISM_HID_ERR_MALFORMED; break; }
                g.page = value; capability(&d, value << 16); break;
            case 1:
                if (!size) { error = LISM_HID_ERR_MALFORMED; break; }
                g.minimum = signed_item(value, size * 8); break;
            case 2:
                if (!size) { error = LISM_HID_ERR_MALFORMED; break; }
                g.maximum = g.minimum < 0 ? (uint32_t)signed_item(value, size * 8) : value; break;
            case 3: case 4: case 5: case 6: break; /* physical range / unit: not cursor position */
            case 7: g.size = value; break;
            case 8:
                if (!size || !value || value > 255) { error = LISM_HID_ERR_REPORT_ID; break; }
                g.id = (uint8_t)value; d.has_report_ids = true; break;
            case 9: g.count = value; break;
            case 10:
                if (size) { error = LISM_HID_ERR_MALFORMED; break; }
                if (globals == GLOBAL_DEPTH) { error = LISM_HID_ERR_LIMIT; break; }
                stack[globals++] = g; break;
            case 11:
                if (size || !globals) { error = LISM_HID_ERR_MALFORMED; break; }
                g = stack[--globals]; break;
            default: error = LISM_HID_ERR_UNSUPPORTED; break;
            }
        } else if (type == 2) {
            if (tag > 2 || !size) { error = LISM_HID_ERR_UNSUPPORTED; }
            else {
                uint32_t usage = size == 4 ? value : (g.page << 16) | value;
                capability(&d, usage);
                if (tag == 0) {
                    if (l.count == LOCAL_USAGES) { error = LISM_HID_ERR_LIMIT; }
                    else { l.usages[l.count++] = usage; }
                } else if (tag == 1) { l.minimum = usage; l.have_minimum = true; }
                else { l.maximum = usage; l.have_maximum = true; }
            }
        } else if (type == 0) {
            uint32_t usage;
            error = usage_at(&l, 0, &usage);
            if (error) { break; }
            if (tag == 10) {
                if (size != 1 || value > 6) { error = LISM_HID_ERR_MALFORMED; }
                else if (depth == COLLECTION_DEPTH) { error = LISM_HID_ERR_LIMIT; }
                else {
                    uint32_t page = usage >> 16, code = usage & 65535U;
                    bool mouse = depth && collection[depth - 1];
                    bool blocked = depth && blocked_collection[depth - 1];
                    bool mouse_usage = page == 1 && (code == 1 || code == 2);
                    if (value == 1) { mouse = mouse_usage; }
                    /* A nested Mouse label cannot escape an unknown/vendor/
                     * digitizer/keyboard collection's capability-only scope. */
                    if (usage && !mouse_usage) { blocked = true; }
                    collection[depth] = mouse && !blocked;
                    blocked_collection[depth++] = blocked;
                }
            } else if (tag == 12) {
                if (size || !depth) { error = LISM_HID_ERR_MALFORMED; }
                else { depth--; }
            } else if (tag == 8 || tag == 9 || tag == 11) {
                if (!size || !depth) { error = LISM_HID_ERR_MALFORMED; }
                else { error = main_data(&d, &g, &l, tag, value, collection[depth - 1]); }
            } else { error = LISM_HID_ERR_UNSUPPORTED; }
            memset(&l, 0, sizeof(l));
        } else { error = LISM_HID_ERR_UNSUPPORTED; }
        if (error) { break; }
    }
    if (!error && (depth || globals || l.count || l.have_minimum || l.have_maximum)) {
        error = LISM_HID_ERR_MALFORMED;
    }
    if (!error && !d.report_count) { error = LISM_HID_ERR_UNSUPPORTED; }
    if (!error && d.has_report_ids) {
        for (unsigned i = 0; i < d.report_count; ++i) {
            if (!d.reports[i].id) { error = LISM_HID_ERR_REPORT_ID; break; }
        }
    }
    if (!error) { *descriptor = d; }
    else {
        descriptor->capabilities = d.capabilities |
            (error == LISM_HID_ERR_UNSUPPORTED ? LISM_HID_CAP_UNSUPPORTED : 0);
    }
    return error;
}
static uint32_t bits_at(const uint8_t *payload, unsigned offset, unsigned size)
{
    uint32_t value = 0;
    for (unsigned i = 0; i < size; ++i) {
        if (payload[(offset + i) / 8] & (1U << ((offset + i) % 8))) { value |= UINT32_C(1) << i; }
    }
    return value;
}
enum lism_hid_error lism_hid_decode(const struct lism_hid_descriptor *d,
    struct lism_hid_state *state, uint8_t report_id, const uint8_t *payload,
    size_t length, struct lism_hid_event *event)
{
    if (!d || !state || !payload || !event) { return LISM_HID_ERR_ARGUMENT; }
    if (d->report_count > LISM_HID_MAX_REPORTS || d->field_count > LISM_HID_MAX_FIELDS) {
        return LISM_HID_ERR_MALFORMED;
    }
    int index = -1;
    for (unsigned i = 0; i < d->report_count; ++i) {
        if (d->reports[i].id == report_id) { index = (int)i; break; }
    }
    if (index < 0) { return LISM_HID_ERR_REPORT_ID; }
    const struct lism_hid_report *r = &d->reports[index];
    if (!r->mouse_fields || !r->input_bits) { return LISM_HID_ERR_UNSUPPORTED; }
    if (r->input_bits > LISM_HID_MAX_REPORT_BYTES * 8U || length != (r->input_bits + 7U) / 8U) {
        return LISM_HID_ERR_LENGTH;
    }
    struct lism_hid_event out = {0};
    uint8_t next_buttons = 0, previous = 0;
    for (unsigned i = 0; i < d->report_count; ++i) { previous |= state->report_buttons[i]; }
    for (unsigned i = 0; i < d->field_count; ++i) {
        const struct lism_hid_field *f = &d->fields[i];
        if (f->report_index != index) { continue; }
        if (!f->bit_size || f->bit_size > 32 || f->bit_offset + f->bit_size > r->input_bits) {
            return LISM_HID_ERR_MALFORMED;
        }
        uint32_t bits = bits_at(payload, f->bit_offset, f->bit_size);
        int32_t value = f->logical_min < 0 ? signed_item(bits, f->bit_size) :
            bits > INT32_MAX ? INT32_MIN : (int32_t)bits;
        if (value < f->logical_min || value > f->logical_max) { return LISM_HID_ERR_RANGE; }
        if (f->kind == LISM_HID_X) { out.dx = value; }
        else if (f->kind == LISM_HID_Y) { out.dy = value; }
        else if (f->kind == LISM_HID_WHEEL) { out.wheel = value; }
        else if (f->kind == LISM_HID_PAN) { out.pan = value; }
        else if (f->kind >= LISM_HID_BUTTON_1 && f->kind < LISM_HID_BUTTON_1 + 8) {
            if (value) { next_buttons |= (uint8_t)(1U << (f->kind - LISM_HID_BUTTON_1)); }
        } else { return LISM_HID_ERR_MALFORMED; }
    }
    out.updated_buttons = r->button_mask;
    for (unsigned i = 0; i < d->report_count; ++i) {
        out.buttons |= i == (unsigned)index ? next_buttons : state->report_buttons[i];
    }
    out.changed = previous ^ out.buttons;
    out.pressed = out.changed & out.buttons;
    out.released = out.changed & previous;
    state->report_buttons[index] = next_buttons;
    *event = out;
    return LISM_HID_OK;
}
const char *lism_hid_error_string(enum lism_hid_error error)
{
    static const char *const names[] = {"ok", "argument", "truncated", "malformed", "limit",
        "unsupported", "absolute", "report-id", "length", "range"};
    return (unsigned)error < sizeof(names) / sizeof(names[0]) ? names[error] : "unknown";
}
