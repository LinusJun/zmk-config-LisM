/* SPDX-License-Identifier: MIT */
#include "transition.h"
#include <errno.h>
#include <string.h>

static bool valid(struct lism_mode m) {
    return m.output >= LISM_OUTPUT_USB && m.output <= LISM_OUTPUT_ESB &&
           (m.output == LISM_OUTPUT_BLE ? m.profile < 5 : m.profile == 0);
}
enum lism_link_mode lism_mode_link(struct lism_mode m) {
    (void)m;
    return LISM_LINK_ESB;
}
void lism_switch_init(struct lism_switch *s, struct lism_mode current, uint32_t epoch) {
    memset(s, 0, sizeof(*s));
    s->current = s->target = current;
    s->epoch = epoch;
}
int lism_switch_begin(struct lism_switch *s, struct lism_mode target,
                      uint8_t halves, uint64_t now) {
    if (!valid(target) || (halves & ~0x33u) ||
        ((halves & 0x03u) && (halves & 0x30u))) return -EINVAL;
    if (s->phase != LISM_SWITCH_IDLE) return -EBUSY;
    if (target.output == s->current.output && target.profile == s->current.profile)
        return -EALREADY;
    /* Refuse wrap: session replacement must be explicit, never accept an old epoch. */
    if (s->epoch == UINT32_MAX) return -EOVERFLOW;
    s->epoch++;
    s->target = target;
    s->halves = halves;
    s->required = halves;
    /* A new receiver cannot answer PREPARE before its ESB link is running.
     * Only an OLD ESB receiver participates in the old-link release barrier. */
    if (s->current.output == LISM_OUTPUT_ESB)
        s->required |= LISM_SWITCH_RECEIVER;
    s->ready = s->synced = 0;
    s->deadline = now + 5000;
    s->input_frozen = true;
    s->phase = s->required ? LISM_SWITCH_PREPARE : LISM_SWITCH_DRAIN;
    return s->required ? LISM_ACTION_PREPARE : LISM_ACTION_DRAIN;
}
static bool peer_required(const struct lism_switch *s, uint8_t peer) {
    return peer < 7 && (s->required & (1u << peer));
}
enum lism_switch_action lism_switch_ready(struct lism_switch *s, uint8_t peer,
                                         uint32_t epoch) {
    if (epoch != s->epoch || s->phase != LISM_SWITCH_PREPARE ||
        !peer_required(s, peer)) return LISM_ACTION_NONE;
    s->ready |= 1u << peer;
    if (s->ready != s->required) return LISM_ACTION_NONE;
    s->phase = LISM_SWITCH_DRAIN;
    return LISM_ACTION_DRAIN;
}
enum lism_switch_action lism_switch_drained(struct lism_switch *s, uint32_t epoch) {
    if (epoch != s->epoch || s->phase != LISM_SWITCH_DRAIN) return LISM_ACTION_NONE;
    /* Backend must have completed old-host key/button releases and TX flush.
     * Only now may it stop the old radio and enable the new one. */
    s->phase = LISM_SWITCH_ACTIVATE;
    return LISM_ACTION_ACTIVATE;
}
static enum lism_switch_action recover(struct lism_switch *s) {
    s->phase = LISM_SWITCH_RECOVER;
    return LISM_ACTION_RECOVER;
}
enum lism_switch_action lism_switch_activated(struct lism_switch *s,
                                             uint32_t epoch, bool success) {
    if (epoch != s->epoch || s->phase != LISM_SWITCH_ACTIVATE) return LISM_ACTION_NONE;
    if (!success) return recover(s);
    s->phase = LISM_SWITCH_SYNC;
    /* Build the NEW-link barrier independently of old-link reachability. */
    s->required = s->halves;
    if (s->target.output == LISM_OUTPUT_ESB) s->required |= LISM_SWITCH_RECEIVER;
    if (s->required) return LISM_ACTION_SYNC;
    s->current = s->target;
    s->phase = LISM_SWITCH_IDLE;
    s->input_frozen = false;
    return LISM_ACTION_RESUME;
}
enum lism_switch_action lism_switch_synced(struct lism_switch *s, uint8_t peer,
                                          uint32_t epoch) {
    if (epoch != s->epoch || s->phase != LISM_SWITCH_SYNC ||
        !peer_required(s, peer)) return LISM_ACTION_NONE;
    s->synced |= 1u << peer;
    if (s->synced != s->required) return LISM_ACTION_NONE;
    s->current = s->target;
    s->phase = LISM_SWITCH_IDLE;
    s->input_frozen = false;
    return LISM_ACTION_RESUME;
}
enum lism_switch_action lism_switch_tick(struct lism_switch *s, uint64_t now) {
    if (s->phase != LISM_SWITCH_IDLE && s->phase != LISM_SWITCH_RECOVER &&
        now >= s->deadline) return recover(s);
    return LISM_ACTION_NONE;
}
enum lism_switch_action lism_switch_recovered(struct lism_switch *s,
                                             uint32_t epoch, bool success) {
    if (epoch != s->epoch || s->phase != LISM_SWITCH_RECOVER || !success)
        return LISM_ACTION_NONE;
    /* Recovery means peers have rendezvoused on the old mode AND restored a
     * fresh state snapshot. A mere radio-start success is insufficient. */
    s->target = s->current;
    s->phase = LISM_SWITCH_IDLE;
    s->input_frozen = false;
    return LISM_ACTION_RESUME;
}
bool lism_switch_accept_input(const struct lism_switch *s, uint32_t epoch) {
    return s->phase == LISM_SWITCH_IDLE && !s->input_frozen && epoch == s->epoch;
}
