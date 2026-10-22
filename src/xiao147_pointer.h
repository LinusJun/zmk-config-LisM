#pragma once
#include <zephyr/device.h>
#include <zephyr/kernel.h>
int xiao147_touch_endpoint(void);
int xiao147_touch_key(const struct device*,uint16_t,int32_t,bool,k_timeout_t);
int xiao147_touch_rel(const struct device*,uint16_t,int32_t,bool,k_timeout_t);
