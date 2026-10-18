/* SPDX-License-Identifier: MIT */
#include "protocol.h"
#include <errno.h>
#include <string.h>
#include <tinycrypt/aes.h>
#include <tinycrypt/ccm_mode.h>
#include <tinycrypt/constants.h>

bool lism_esb_keyboard_slot(uint8_t slot) {
    return slot == 0 || slot == 1 || slot == 4 || slot == 5;
}
uint32_t lism_esb_u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
           (uint32_t)p[3] << 24;
}
void lism_esb_put_u32(uint8_t *p, uint32_t v) {
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i));
}
int lism_esb_config_encode(const struct lism_esb_session *s, uint8_t *out) {
    if (!lism_esb_keyboard_slot(s->slot) || !s->id || s->channel > 100) return -EINVAL;
    out[0] = LISM_ESB_VERSION; out[1] = s->slot;
    lism_esb_put_u32(out + 2, s->id);
    memcpy(out + 6, s->key, 16); memcpy(out + 22, s->base0, 4);
    memcpy(out + 26, s->base1, 4); out[30] = s->channel;
    return LISM_ESB_CONFIG_SIZE;
}
int lism_esb_config_decode(struct lism_esb_session *s, const uint8_t *data, size_t len) {
    if (len != LISM_ESB_CONFIG_SIZE || data[0] != LISM_ESB_VERSION ||
        !lism_esb_keyboard_slot(data[1]) || !lism_esb_u32(data + 2) || data[30] > 100)
        return -EINVAL;
    s->slot = data[1]; s->id = lism_esb_u32(data + 2);
    memcpy(s->key, data + 6, 16); memcpy(s->base0, data + 22, 4);
    memcpy(s->base1, data + 26, 4); s->channel = data[30];
    return 0;
}
static void nonce_for(const uint8_t header[LISM_ESB_HEADER], uint8_t nonce[13]) {
    memcpy(nonce, header, LISM_ESB_HEADER);
    nonce[12] = 0x4c;
}
int lism_esb_seal(const struct lism_esb_session *s, const struct lism_esb_message *m,
                  uint8_t out[LISM_ESB_PACKET]) {
    if (!s->id || !lism_esb_keyboard_slot(s->slot) || !m->sequence ||
        m->length > LISM_ESB_BODY || m->kind < LISM_ESB_EVENT ||
        m->kind > LISM_ESB_HEARTBEAT) return -EINVAL;
    out[0] = LISM_ESB_VERSION; out[1] = m->kind; out[2] = s->slot; out[3] = m->length;
    lism_esb_put_u32(out + 4, s->id); lism_esb_put_u32(out + 8, m->sequence);
    uint8_t nonce[13]; nonce_for(out, nonce);
    struct tc_aes_key_sched_struct key;
    struct tc_ccm_mode_struct ccm;
    int rc = tc_aes128_set_encrypt_key(&key, s->key) &&
             tc_ccm_config(&ccm, &key, nonce, sizeof(nonce), LISM_ESB_TAG) &&
             tc_ccm_generation_encryption(out + LISM_ESB_HEADER,
                 m->length + LISM_ESB_TAG, out, LISM_ESB_HEADER, m->body, m->length, &ccm);
    memset(&key, 0, sizeof(key));
    return rc == TC_CRYPTO_SUCCESS ? LISM_ESB_HEADER + m->length + LISM_ESB_TAG : -EIO;
}
int lism_esb_open(const struct lism_esb_session *s, const uint8_t *data, size_t len,
                  struct lism_esb_message *m) {
    if (len < LISM_ESB_HEADER + LISM_ESB_TAG || len > LISM_ESB_PACKET ||
        data[0] != LISM_ESB_VERSION || data[1] < LISM_ESB_EVENT ||
        data[1] > LISM_ESB_HEARTBEAT || data[2] != s->slot ||
        data[3] > LISM_ESB_BODY || len != (size_t)LISM_ESB_HEADER + data[3] + LISM_ESB_TAG ||
        !s->id || lism_esb_u32(data + 4) != s->id || !lism_esb_u32(data + 8)) return -EINVAL;
    uint8_t nonce[13]; nonce_for(data, nonce);
    struct tc_aes_key_sched_struct key;
    struct tc_ccm_mode_struct ccm;
    int rc = tc_aes128_set_encrypt_key(&key, s->key) &&
             tc_ccm_config(&ccm, &key, nonce, sizeof(nonce), LISM_ESB_TAG) &&
             tc_ccm_decryption_verification(m->body, sizeof(m->body), data, LISM_ESB_HEADER,
                 data + LISM_ESB_HEADER, len - LISM_ESB_HEADER, &ccm);
    memset(&key, 0, sizeof(key));
    if (rc != TC_CRYPTO_SUCCESS) { memset(m, 0, sizeof(*m)); return -EACCES; }
    m->kind = data[1]; m->length = data[3]; m->sequence = lism_esb_u32(data + 8);
    return 0;
}
