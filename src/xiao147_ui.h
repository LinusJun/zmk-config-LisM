#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "xiao147_orientation.h"
#define XIAO147_TOUCH_CANCEL UINT8_MAX
enum xiao147_page {XIAO147_PAGE_STATUS,XIAO147_PAGE_DEVICES,XIAO147_PAGE_COUNT};
struct _lv_obj_t;
void xiao147_ui_start(struct _lv_obj_t *screen);
enum xiao147_page xiao147_ui_page(void);
void xiao147_ui_next_page(void);
void xiao147_ui_previous_page(void);
void xiao147_ui_touch(uint8_t fingers,uint16_t x,uint16_t y,enum xiao147_orientation orientation);
bool xiao147_ui_imu_enabled(void);
bool xiao147_ui_is_sleeping(void);
uint32_t xiao147_ui_pointer_generation(void);
bool xiao147_ui_wake_on_touch(void);
bool xiao147_ui_wake_on_motion(void);
int xiao147_pointer_endpoint(void);
