#pragma once
#include <stdbool.h>

/* Calls below must run in ZMK's display work queue. */
void xiao147_rotation_install(void);
void xiao147_rotation_set(bool portrait);
bool xiao147_rotation_is_portrait(void);
