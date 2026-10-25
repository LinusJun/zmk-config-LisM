/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdbool.h>
#include <stddef.h>
/* ASCII token matching, bounded by the actual peer name capacity. */
static inline unsigned char kb_icon_ascii_lower(unsigned char c) {
    return c >= 'A' && c <= 'Z' ? c + ('a'-'A') : c;
}
static inline bool kb_icon_name_word(unsigned char c) {
    return (c>='a'&&c<='z') || (c>='A'&&c<='Z') || (c>='0'&&c<='9');
}
static inline bool kb_icon_alias(const char *name, size_t capacity, const char *alias) {
    if(!name) return false;
    size_t length=0;
    while(length<capacity && name[length]) length++;
    if(length==capacity) return false; /* Reject unterminated peer metadata. */
    for(size_t i=0;i<capacity && name[i];i++) {
        if(i && kb_icon_name_word((unsigned char)name[i-1])) continue;
        size_t j=0;
        while(alias[j] && i+j<capacity && name[i+j] &&
              kb_icon_ascii_lower((unsigned char)name[i+j])==(unsigned char)alias[j]) j++;
        if(!alias[j] && i+j<capacity && !kb_icon_name_word((unsigned char)name[i+j])) return true;
    }
    return false;
}
struct kb_icon_input_state { bool pad, joystick; };
/* Display classification only; never authorizes or changes HID input parsing. */
static inline struct kb_icon_input_state kb_icon_input_classify(bool enabled,
        bool connected, bool ready, bool pointer_type, const char *name, size_t capacity) {
    struct kb_icon_input_state out={0};
    if(enabled && connected && ready && pointer_type) {
        out.joystick=kb_icon_alias(name,capacity,"jp19") || kb_icon_alias(name,capacity,"circlepad");
        out.pad=!out.joystick;
    }
    return out;
}
