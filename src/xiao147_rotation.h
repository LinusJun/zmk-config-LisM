#pragma once
#include <stdbool.h>
#include "xiao147_orientation.h"

/* Calls below must run in ZMK's display work queue. */
void xiao147_rotation_install(void);
void xiao147_rotation_set(enum xiao147_orientation orientation);
enum xiao147_orientation xiao147_rotation_get(void);
bool xiao147_rotation_is_portrait(void);
