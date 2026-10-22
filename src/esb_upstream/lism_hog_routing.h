/* SPDX-License-Identifier: MIT */
#pragma once
#include <zmk/hog.h>

int zmk_hog_send_keyboard_report_to_profile(struct zmk_hid_keyboard_report_body *report,
                                            uint8_t profile);
int zmk_hog_send_consumer_report_to_profile(struct zmk_hid_consumer_report_body *report,
                                            uint8_t profile);
#if IS_ENABLED(CONFIG_ZMK_POINTING)
int zmk_hog_send_mouse_report_to_profile(struct zmk_hid_mouse_report_body *report,
                                         uint8_t profile);
#endif
