/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define LISM_ESB_VERSION 1
#define LISM_ESB_HEADER 12
#define LISM_ESB_TAG 8
#define LISM_ESB_BODY 48
#define LISM_ESB_PACKET (LISM_ESB_HEADER + LISM_ESB_BODY + LISM_ESB_TAG)
#define LISM_ESB_CONFIG_SIZE 31
#define LISM_ESB_UUID BT_UUID_128_ENCODE(0x7813af34, 0x37c2, 0x4631, 0xa7b1, 0x135597521a01)

enum lism_esb_kind { LISM_ESB_EVENT = 1, LISM_ESB_ACK, LISM_ESB_SNAPSHOT,
                     LISM_ESB_HEARTBEAT };
struct lism_esb_session {
    uint8_t slot;
    uint32_t id;
    uint8_t key[16], base0[4], base1[4], channel;
};
struct lism_esb_message {
    uint8_t kind, length;
    uint32_t sequence;
    uint8_t body[LISM_ESB_BODY];
};
bool lism_esb_keyboard_slot(uint8_t slot);
uint32_t lism_esb_u32(const uint8_t *p);
void lism_esb_put_u32(uint8_t *p, uint32_t value);
int lism_esb_config_encode(const struct lism_esb_session *s, uint8_t *out);
int lism_esb_config_decode(struct lism_esb_session *s, const uint8_t *data, size_t len);
/* The header is authenticated. Nonces separate directions and message kinds.
 * A session gets a fresh random AES key on EVERY encrypted BLE connection.
 * A sequence is never reused for different bytes, and may not wrap. */
int lism_esb_seal(const struct lism_esb_session *s, const struct lism_esb_message *m,
                  uint8_t out[LISM_ESB_PACKET]);
int lism_esb_open(const struct lism_esb_session *s, const uint8_t *data, size_t len,
                  struct lism_esb_message *m);
