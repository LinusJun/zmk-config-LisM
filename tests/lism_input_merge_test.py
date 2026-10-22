"""Compile the real merger under ASAN/UBSAN; verify all pinned output hooks."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
app = Path(os.environ.get("LISM_ZMK_APP", "/private/tmp/lism-trackpad-build/zmk/app"))
with tempfile.TemporaryDirectory(prefix="lism-input-merge-") as tmp:
    out = Path(tmp)
    (out / "zmk").mkdir()
    (out / "zephyr").mkdir()
    (out / "zmk/hid.h").write_text(r'''
#pragma once
#include <stdint.h>
#define IS_ENABLED(x) x
#define CONFIG_ZMK_USB_BOOT 1
#define CONFIG_ZMK_HID_REPORT_TYPE_HKRO 1
#define CONFIG_ZMK_HID_CONSUMER_REPORT_USAGES_BASIC 0
struct zmk_hid_keyboard_report_body { uint8_t modifiers, _reserved, keys[6]; };
struct zmk_hid_keyboard_report { uint8_t report_id; struct zmk_hid_keyboard_report_body body; };
typedef struct zmk_hid_keyboard_report_body zmk_hid_boot_report_t;
struct zmk_hid_mouse_report {struct {uint8_t buttons;} body;};
struct zmk_hid_consumer_report_body { uint16_t keys[6]; };
struct zmk_hid_consumer_report { uint8_t report_id; struct zmk_hid_consumer_report_body body; };
''')
    (out / "zephyr/kernel.h").write_text(r'''
#pragma once
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
struct k_spinlock { int unused; };
typedef int k_spinlock_key_t;
static inline int k_spin_lock(struct k_spinlock *p) { (void)p; return 0; }
static inline void k_spin_unlock(struct k_spinlock *p, int k) { (void)p; (void)k; }
''')
    (out / "main.c").write_text(r'''
#include <assert.h>
#include <errno.h>
#include <string.h>
#include "lism_input_merge.h"
static unsigned callbacks;
void lism_input_merge_endpoint_changed(uint32_t epoch) {
    assert(epoch == lism_input_merge_generation()); callbacks++;
}
static void put(struct lism_input_merge_state *s, unsigned u) { s->keys[u/8] |= 1u << (u%8); }
int main(void) {
    uint32_t epoch = lism_input_merge_generation();
    struct lism_input_merge_state a={0}, b={0};
    put(&a,4); a.modifiers=2; a.consumer[0]=0xe9; a.consumer_count=1;
    put(&b,4); put(&b,5); b.modifiers=4;
    b.consumer[0]=0xe9; b.consumer[1]=0xea; b.consumer_count=2;
    assert(!lism_input_merge_set(0,epoch,&a));
    assert(!lism_input_merge_set(1,epoch,&b));
    struct zmk_hid_keyboard_report local={.report_id=1,.body={.modifiers=1,.keys={4,6}}};
    struct zmk_hid_keyboard_report copy=local;
    lism_input_merge_keyboard(&copy);
    assert(copy.body.modifiers==7 && copy.body.keys[0]==4 && copy.body.keys[1]==5 && copy.body.keys[2]==6);
    assert(local.body.modifiers==1 && local.body.keys[1]==6); /* local ownership unchanged */
    assert(!lism_input_merge_clear(0,epoch));
    copy=local; lism_input_merge_keyboard(&copy);
    assert(copy.body.keys[0]==4 && copy.body.keys[1]==5 && copy.body.modifiers==5);
    assert(!lism_input_merge_clear(1,epoch));
    copy=local; lism_input_merge_keyboard(&copy);
    assert(!memcmp(&copy,&local,sizeof(copy))); /* peer release cannot release local A */
    for(unsigned u=4;u<11;u++) put(&a,u);
    assert(!lism_input_merge_set(0,epoch,&a));
    copy=local; lism_input_merge_keyboard(&copy);
    for(unsigned i=0;i<6;i++) assert(copy.body.keys[i]==1);
    memset(&a,0,sizeof(a)); put(&a,7); put(&a,0xe3);
    assert(!lism_input_merge_set(0,epoch,&a));
    copy=local; lism_input_merge_keyboard(&copy);
    assert(copy.body.keys[2]==7 && copy.body.modifiers==9); /* rollover recovers */
    a.rollover=true; assert(!lism_input_merge_set(0,epoch,&a));
    zmk_hid_boot_report_t boot={0}; lism_input_merge_boot(&boot);
    for(unsigned i=0;i<6;i++) assert(boot.keys[i]==1);
    a.rollover=false;
    for(unsigned i=0;i<12;i++) a.consumer[i]=0x100+i;
    a.consumer_count=12; assert(!lism_input_merge_set(0,epoch,&a));
    struct zmk_hid_consumer_report consumer={.report_id=2,.body={.keys={0xe9,0xe9,0xea}}};
    lism_input_merge_consumer(&consumer);
    assert(consumer.body.keys[0]==0xe9 && consumer.body.keys[1]==0xea);
    for(unsigned i=2;i<6;i++) assert(consumer.body.keys[i]==0x100+i-2);
    a.consumer_count=13; assert(lism_input_merge_set(0,epoch,&a)==-EINVAL);
    assert(lism_input_merge_set(2,epoch,&b)==-EINVAL);
    lism_input_merge_endpoint_reset(); assert(callbacks==1);
    assert(lism_input_merge_set(0,epoch,&b)==-ESTALE);
    copy=local; lism_input_merge_keyboard(&copy); assert(!memcmp(&copy,&local,sizeof(copy)));
    /* New first keydown is valid; no artificial all-up baseline swallows it. */
    assert(!lism_input_merge_set(1,lism_input_merge_generation(),&b));
    copy=local; lism_input_merge_keyboard(&copy); assert(copy.body.keys[1]==5);
    struct zmk_hid_mouse_report mouse={.body={.buttons=1}};
    b.buttons=3;assert(!lism_input_merge_set(1,lism_input_merge_generation(),&b));
    lism_input_merge_mouse(&mouse);assert(mouse.body.buttons==3);
    assert(!lism_input_merge_clear(1,lism_input_merge_generation()));
    mouse.body.buttons=1;lism_input_merge_mouse(&mouse);assert(mouse.body.buttons==1);
    a.consumer_count=0;a.buttons=4;b.buttons=4;
    assert(!lism_input_merge_set(0,lism_input_merge_generation(),&a));
    assert(!lism_input_merge_set(1,lism_input_merge_generation(),&b));
    assert(!lism_input_merge_clear(0,lism_input_merge_generation()));
    mouse.body.buttons=0;lism_input_merge_mouse(&mouse);assert(mouse.body.buttons==4);
    return 0;
}
''')
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer", "-g", "-I", str(out), "-I", str(root / "src"),
                    str(root / "src/lism_input_merge.c"), str(out / "main.c"),
                    "-o", str(out / "merge")], check=True)
    subprocess.run([str(out / "merge")], check=True)
print("PASS: real keyboard/consumer/mouse source-owned merge and epoch reset")
