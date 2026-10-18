#pragma once
#include <zephyr/bluetooth/addr.h>
#include <stdbool.h>
#include <stdint.h>
void lism_split_auto_observe(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
                            bool split, int role);
void lism_split_auto_ready(uint8_t slot);
bool lism_split_auto_pending(void);
void lism_split_auto_cancel(void);
/* Atomically claim a vacant role without rewriting any stored address. */
int lism_split_sets_auto_claim(uint8_t role);
void lism_split_sets_auto_release(void);
bool lism_split_sets_auto_hold(void);
bool lism_split_sets_auto_begin_release(uint8_t slot);
void lism_split_sets_auto_end_release(uint8_t slot);
int lism_split_sets_stage_addr(uint8_t slot,const bt_addr_le_t *addr);
int lism_split_sets_commit_addr(uint8_t slot,const bt_addr_le_t *addr);
void lism_split_sets_auto_status(const char *text);
