#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <zephyr/bluetooth/addr.h>
struct bt_conn;

/* Diagnostic hooks on the pinned SMP receive path are read-only. Never observe
 * keys, random values, confirm values or passkeys. The separate legacy policy
 * query below is the only opt-in decision consumed by the SMP adapter. */
enum lism_input_smp_block {
    LISM_INPUT_SMP_KEY_SIZE = 1,
    LISM_INPUT_SMP_STORED_BOND,
    LISM_INPUT_SMP_AUTH_LEVEL,
    LISM_INPUT_SMP_SC_REQUIRED,
};
void lism_input_smp_received(struct bt_conn *conn, uint8_t opcode,
                             const void *data, size_t length);
void lism_input_smp_blocked(struct bt_conn *conn, uint8_t block);
void lism_input_smp_failed(struct bt_conn *conn, uint8_t opcode, uint8_t reason);
/* Explicit per-identity user grant, bound to one live central-role connection
 * and session epoch. Unknown, split, computer and stale connections fail closed. */
bool lism_input_legacy_allowed(struct bt_conn *conn);
/* Only the exact live, selected central input may save a legacy central LTK. */
bool lism_input_legacy_key_allowed(uint8_t id, const bt_addr_le_t *identity);
bool lism_input_computer_identity(const bt_addr_le_t *identity);
bool lism_input_non_split_identity(const bt_addr_le_t *identity);
/* Before SMP touches an identity's existing key record: generic sessions may
 * never resolve to split/computer/another generic identity. Other roles retain
 * their upstream behavior. */
bool lism_input_identity_allowed(struct bt_conn *conn, const bt_addr_le_t *identity);
