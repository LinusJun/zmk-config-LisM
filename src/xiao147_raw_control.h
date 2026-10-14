/* Exact, bounded serial commands for the opt-in RAW sensor diagnostic. */
#ifndef XIAO147_RAW_CONTROL_H
#define XIAO147_RAW_CONTROL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum xiao147_raw_command {
    XIAO147_RAW_COMMAND_NONE,
    XIAO147_RAW_COMMAND_INFO,
    XIAO147_RAW_COMMAND_START,
    XIAO147_RAW_COMMAND_STOP,
    XIAO147_RAW_COMMAND_INVALID,
};

struct xiao147_raw_control {
    char line[32];
    size_t length;
    bool discard;
    uint16_t expected_fw;
};

static inline int xiao147_raw_hex(uint8_t byte)
{
    if (byte >= '0' && byte <= '9') { return byte - '0'; }
    if (byte >= 'A' && byte <= 'F') { return byte - 'A' + 10; }
    if (byte >= 'a' && byte <= 'f') { return byte - 'a' + 10; }
    return -1;
}

static inline enum xiao147_raw_command
xiao147_raw_control_byte(struct xiao147_raw_control *control, uint8_t byte)
{
    if (byte != '\n') {
        if (control->discard) { return XIAO147_RAW_COMMAND_NONE; }
        if (byte == 0 || control->length >= sizeof(control->line) - 1) {
            control->discard = true;
        } else { control->line[control->length++] = (char)byte; }
        return XIAO147_RAW_COMMAND_NONE;
    }
    enum xiao147_raw_command command = XIAO147_RAW_COMMAND_INVALID;
    if (!control->discard) {
        size_t length = control->length;
        if (length && control->line[length - 1] == '\r') { length--; }
        control->line[length] = 0;
        if (!strcmp(control->line, "AXS_RAW_INFO")) { command = XIAO147_RAW_COMMAND_INFO; }
        else if (!strcmp(control->line, "AXS_RAW_STOP")) { command = XIAO147_RAW_COMMAND_STOP; }
        else if (length == 18 && !memcmp(control->line, "AXS_RAW_START ", 14)) {
            uint16_t version = 0;
            bool valid = true;
            for (size_t i = 14; i < 18; i++) {
                int digit = xiao147_raw_hex((uint8_t)control->line[i]);
                if (digit < 0) { valid = false; break; }
                version = (uint16_t)((version << 4) | (uint16_t)digit);
            }
            if (valid) { control->expected_fw = version; command = XIAO147_RAW_COMMAND_START; }
        } else if (length == 0) { command = XIAO147_RAW_COMMAND_NONE; }
    }
    control->length = 0;
    control->discard = false;
    return command;
}

#endif
