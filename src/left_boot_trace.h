/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
extern int lism_left_clock_request_state;
void lism_left_trace_configured(void);
void lism_left_trace_boot_log(void);
void lism_left_trace_expiry(uint32_t boot,uint32_t epoch,unsigned net,
                           uint32_t control_age,uint32_t ack_age);
