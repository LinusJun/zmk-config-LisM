/* SPDX-License-Identifier: MIT */
#pragma once
#include <lvgl.h>
#include <strings.h>
LV_FONT_DECLARE(FG_Medium_21);
LV_FONT_DECLARE(DINishExpanded_Light_36);

/* Display aliases do not alter keymap names, indices or behavior. */
static inline void kb_layer_label_update(lv_obj_t *label,const char *name) {
    bool joystick=strcasecmp(name,"GAMEJOYSTICK")==0 || strcasecmp(name,"JOYSTICK")==0;
    lv_obj_set_style_text_font(label,joystick?&FG_Medium_21:&DINishExpanded_Light_36,LV_PART_MAIN);
    lv_label_set_text(label,joystick?"JOYSTICK":name);
    /* Keep the complete label inside the WPM panel's clipping rectangle. */
    lv_obj_align(label,LV_ALIGN_BOTTOM_RIGHT,0,0);
}
