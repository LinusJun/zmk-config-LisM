#pragma once
#include <stdbool.h>
#include <stdint.h>
struct _lv_obj_t;
void lism_screen_create(struct _lv_obj_t *screen);
void lism_screen_request_next(void);
bool lism_screen_take_toggle(void);
bool lism_screen_active(void);
void lism_screen_commit(bool active);
void lism_screen_nav(unsigned position);
bool lism_esb_coex_pressure(void);
void lism_coex_grant(void);
void lism_coex_log(void);

uint32_t lism_screen_generation(void);
