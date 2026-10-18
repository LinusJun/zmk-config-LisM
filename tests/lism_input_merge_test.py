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
    return 0;
}
''')
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer", "-g", "-I", str(out), "-I", str(root / "src"),
                    str(root / "src/lism_input_merge.c"), str(out / "main.c"),
                    "-o", str(out / "merge")], check=True)
    subprocess.run([str(out / "merge")], check=True)
    script = f'''
include("{root}/cmake/lism_input_output.cmake")
lism_input_patch_endpoints("{app}/src/endpoints.c" "{out}/endpoints.c" "{root}/src/lism_input_merge.h")
lism_input_patch_usb("{app}/src/usb_hid.c" "{out}/usb_hid.c" "{root}/src/lism_input_merge.h")
lism_input_patch_hog("{app}/src/hog.c" "{out}/hog.c" "{root}/src/lism_input_merge.h")
'''
    (out / "patch.cmake").write_text(script)
    subprocess.run(["cmake", "-P", str(out / "patch.cmake")], check=True)
    endpoints = (out / "endpoints.c").read_text()
    usb = (out / "usb_hid.c").read_text()
    hog = (out / "hog.c").read_text()
    assert endpoints.index("lism_input_merge_endpoint_reset();") < endpoints.index("zmk_hid_keyboard_clear();")
    transition = endpoints.split("static void update_current_endpoint(void) {", 1)[1].split(
        "\nstatic int endpoint_listener", 1)[0]
    assert transition.index("lism_input_endpoint_transition_begin();") < transition.index("zmk_endpoint_clear_reports();")
    assert transition.index("lism_input_endpoint_transition_begin();") < transition.index("get_selected_instance();")
    assert transition.index("current_instance = new_instance;") < transition.index("raise_zmk_endpoint_changed(")
    assert transition.index("raise_zmk_endpoint_changed(") < transition.index("lism_input_endpoint_transition_end();")
    for kind in ("keyboard", "consumer"):
        assert f"lism_input_hog_send_{kind}(&snapshot.body, epoch)" in endpoints
        assert f"lism_input_merge_{kind}(&copy)" in hog
        assert f"struct lism_hog_{kind}_packet" in hog
        assert f"int lism_input_hog_send_{kind}(" in hog
    assert "static uint8_t transfer[128] __aligned(4)" in usb
    assert "memcpy(transfer, report, len)" in usb
    assert usb.index("k_sem_take(&hid_sem") < usb.index("memcpy(transfer, report, len)")
    assert "hid_int_ep_write(hid_dev, transfer, len, NULL)" in usb
    assert "control_keyboard" in usb and "control_consumer" in usb
    assert "lism_input_merge_boot(&copy->boot)" in usb
    assert "struct bt_conn *conn = zmk_ble_active_profile_conn();" not in hog
    assert "struct lism_hog_mouse_packet" in hog
    assert "lism_input_hog_send_mouse(&snapshot.body, epoch)" in endpoints
    assert "bt_conn_lookup_addr_le(BT_ID_DEFAULT, &packet.destination)" in hog
    assert "if (epoch != lism_input_merge_generation()) { return -ESTALE; }" in hog
    # Execute the actual patched USB send function with a DMA-like driver that
    # retains the pointer after return. Caller snapshots may immediately expire.
    usb_send = usb.split("static int zmk_usb_hid_send_report(", 1)[1]
    usb_send = "static int zmk_usb_hid_send_report(" + usb_send.split(
        "\nint zmk_usb_hid_send_keyboard_report", 1)[0]
    mouse_send = "int zmk_usb_hid_send_mouse_report" + usb.split(
        "int zmk_usb_hid_send_mouse_report", 1)[1].split(
            "\n#endif // IS_ENABLED(CONFIG_ZMK_POINTING)", 1)[0]
    (out / "usb-lifetime.c").write_text(r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <stddef.h>
#define __aligned(n) __attribute__((aligned(n)))
#define K_MSEC(x) x
#define IS_ENABLED(x) x
#define CONFIG_ZMK_USB_BOOT 0
enum {USB_DC_SUSPEND, USB_DC_ERROR, USB_DC_RESET, USB_DC_DISCONNECTED, USB_DC_UNKNOWN, READY};
static int hid_sem, status=READY, sem_result, writes, given;
static const void *hid_dev;
static const uint8_t *dma;
static uint32_t current_epoch=1;
struct zmk_hid_mouse_report { uint8_t id; struct { uint16_t buttons; int16_t x,y; } body; };
static struct zmk_hid_mouse_report local_mouse;
static int mutate_mouse_during_wait;
static struct zmk_hid_mouse_report *zmk_hid_get_mouse_report(void) { return &local_mouse; }
static int zmk_usb_get_status(void) { return status; }
static int usb_wakeup_request(void) { return 0; }
static int k_sem_take(int *s,int timeout) {
    (void)s;(void)timeout;
    if(mutate_mouse_during_wait) local_mouse.body.x=999;
    return sem_result;
}
static void k_sem_give(int *s) { (void)s; given++; }
static uint32_t lism_input_merge_generation(void) { return current_epoch; }
static int hid_int_ep_write(const void *dev,const uint8_t *p,size_t len,void *unused) {
    (void)dev;(void)len;(void)unused;dma=p;writes++;return 0;
}
''' + usb_send + mouse_send + r'''
int main(void) {
    uint8_t snapshot[]={1,2,3,4};
    assert(zmk_usb_hid_send_report(snapshot,sizeof(snapshot),1)==0);
    assert(writes==1 && dma!=snapshot);
    memset(snapshot,99,sizeof(snapshot)); assert(dma[1]==2);
    sem_result=-EAGAIN;
    assert(zmk_usb_hid_send_report(snapshot,sizeof(snapshot),1)==-EAGAIN);
    assert(writes==1 && dma[1]==2); /* DMA storage not overwritten without ownership */
    sem_result=0; current_epoch=2;
    assert(zmk_usb_hid_send_report(snapshot,sizeof(snapshot),1)==-ESTALE);
    assert(writes==1 && given==1 && dma[1]==2);
    assert(zmk_usb_hid_send_report(snapshot,129,2)==-EMSGSIZE);
    status=USB_DC_DISCONNECTED;
    assert(zmk_usb_hid_send_report(snapshot,sizeof(snapshot),2)==-ENODEV);
    status=READY; local_mouse.id=3; local_mouse.body.x=7; mutate_mouse_during_wait=1;
    assert(zmk_usb_hid_send_mouse_report()==0);
    struct zmk_hid_mouse_report sent;
    memcpy(&sent,dma,sizeof(sent));
    assert(local_mouse.body.x==999 && sent.body.x==7); /* caller-owned before sem wait */
    return 0;
}
''')
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-o", str(out / "usb-lifetime"), str(out / "usb-lifetime.c")], check=True)
    subprocess.run([str(out / "usb-lifetime")], check=True)
    # Execute the real generated HOG queue functions. Old all-up goes ONLY to
    # its captured A address; stale ordinary input cannot follow it into B/C.
    hog_keyboard = "struct lism_hog_keyboard_packet" + hog.split(
        "struct lism_hog_keyboard_packet", 1)[1].split(
            "struct lism_hog_consumer_packet", 1)[0]
    routing_prelude = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#define K_NO_WAIT 0
#define BT_ID_DEFAULT 0
#define BT_SECURITY_L2 2
#define ZMK_TRANSPORT_BLE 1
#define CONFIG_ZMK_BLE_KEYBOARD_REPORT_QUEUE_SIZE 8
#define LOG_DBG(...)
typedef struct { uint8_t value; } bt_addr_le_t;
static const bt_addr_le_t any={255};
#define BT_ADDR_LE_ANY (&any)
static bt_addr_le_t addresses[]={{0},{1},{2}};
static int bt_addr_le_cmp(const bt_addr_le_t*a,const bt_addr_le_t*b){return a->value!=b->value;}
struct zmk_endpoint_instance { int transport; struct { int profile_index; } ble; };
static int selected;
static struct zmk_endpoint_instance zmk_endpoint_get_selected(void){
 return (struct zmk_endpoint_instance){.transport=1,.ble={selected}};
}
static bt_addr_le_t *zmk_ble_profile_address(int i){return &addresses[i];}
struct zmk_hid_keyboard_report_body {uint8_t modifiers,reserved,keys[6];};
struct queue {size_t size;unsigned count,read,write;uint8_t rows[8][128];};
#define K_MSGQ_DEFINE(name,size_,n,align) static struct queue name={.size=size_}
static int k_msgq_put(struct queue*q,const void*p,int wait){
 (void)wait;if(q->count==8)return -ENOMSG;
 memcpy(q->rows[q->write++%8],p,q->size);q->count++;return 0;
}
static int k_msgq_get(struct queue*q,void*p,int wait){
 (void)wait;if(!q->count)return -ENOMSG;
 memcpy(p,q->rows[q->read++%8],q->size);q->count--;return 0;
}
struct k_work {int dummy;};
#define K_WORK_DEFINE(name,fn) static struct k_work name
static int hog_work_q;
static void k_work_submit_to_queue(int*q,struct k_work*w){(void)q;(void)w;}
static uint32_t epoch=1;
static uint32_t lism_input_merge_generation(void){return epoch;}
struct bt_conn {int address;};
static struct bt_conn conns[]={{0},{1},{2}};
static struct bt_conn *bt_conn_lookup_addr_le(int id,const bt_addr_le_t*a){(void)id;return &conns[a->value];}
static void bt_conn_unref(struct bt_conn*c){(void)c;}
static void bt_conn_set_security(struct bt_conn*c,int l){(void)c;(void)l;}
static struct {int attrs[16];} hog_svc;
struct bt_gatt_notify_params {void*attr;const void*data;size_t len;};
static int notified, destinations[8], usages[8];
static int bt_gatt_notify_cb(struct bt_conn*c,struct bt_gatt_notify_params*p){
 const struct zmk_hid_keyboard_report_body*b=p->data;
 destinations[notified]=c->address;usages[notified++]=b->keys[0];return 0;
}
'''
    routing_main = r'''
int main(void){
 struct zmk_hid_keyboard_report_body body={0};
 assert(!lism_input_hog_send_keyboard(&body,1)); /* A release queued */
 body.keys[0]=4;assert(!lism_input_hog_send_keyboard(&body,1)); /* stale A input */
 epoch=2;selected=1;
 memset(&body,0,sizeof(body));assert(!lism_input_hog_send_keyboard(&body,2)); /* B release */
 epoch=3;selected=2;
 body.keys[0]=5;assert(!lism_input_hog_send_keyboard(&body,3)); /* new C input */
 send_keyboard_report_callback(0);
 assert(notified==3);
 assert(destinations[0]==0 && usages[0]==0);
 assert(destinations[1]==1 && usages[1]==0);
 assert(destinations[2]==2 && usages[2]==5);
 assert(lism_input_hog_send_keyboard(&body,1)==-ESTALE);
 return 0;
}
'''
    (out / "hog-routing.c").write_text(routing_prelude + hog_keyboard + routing_main)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-o", str(out / "hog-routing"), str(out / "hog-routing.c")], check=True)
    subprocess.run([str(out / "hog-routing")], check=True)
    hog_mouse = "struct lism_hog_mouse_packet" + hog.split(
        "struct lism_hog_mouse_packet", 1)[1].split(
            "#endif // IS_ENABLED(CONFIG_ZMK_POINTING)", 1)[0]
    mouse_routing = (routing_prelude.replace("keyboard", "mouse").replace("KEYBOARD", "MOUSE")
                     .replace("uint8_t modifiers,reserved,keys[6];", "uint8_t buttons;int16_t dx,dy,pan,wheel;")
                     .replace("b->keys[0]", "b->dx") + hog_mouse + routing_main.replace("keyboard", "mouse")
                     .replace("body.keys[0]", "body.dx"))
    (out / "mouse-routing.c").write_text(mouse_routing)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-o", str(out / "mouse-routing"), str(out / "mouse-routing.c")], check=True)
    subprocess.run([str(out / "mouse-routing")], check=True)
    # Execute the real endpoint transition: no old-host clear/new-host commit
    # or event callback can escape the thread-context transition mutex boundary.
    (out / "endpoint-lock.c").write_text(r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define LOG_INF(...)
#define ZMK_ENDPOINT_STR_LEN 16
struct zmk_endpoint_instance {int index;};
struct zmk_endpoint_changed {struct zmk_endpoint_instance endpoint;};
static struct zmk_endpoint_instance current_instance={0},next={1};
static int locked,begins,ends,clears,events;
static struct zmk_endpoint_instance get_selected_instance(void){assert(locked);return next;}
static bool zmk_endpoint_instance_eq(struct zmk_endpoint_instance a,struct zmk_endpoint_instance b){return a.index==b.index;}
static void lism_input_endpoint_transition_begin(void){assert(!locked);locked=1;begins++;}
static void lism_input_endpoint_transition_end(void){assert(locked);locked=0;ends++;}
static void zmk_endpoint_clear_reports(void){assert(locked&&current_instance.index==0);clears++;}
static void zmk_endpoint_instance_to_str(struct zmk_endpoint_instance i,char*s,size_t n){(void)i;(void)s;(void)n;assert(locked);}
static void raise_zmk_endpoint_changed(struct zmk_endpoint_changed e){assert(locked&&e.endpoint.index==1&&current_instance.index==1);events++;}
static void update_current_endpoint(void) {
''' + transition + r'''
int main(void){
 update_current_endpoint();assert(!locked&&begins==1&&ends==1&&clears==1&&events==1);
 update_current_endpoint();assert(begins==2&&ends==2&&!locked); /* selection itself is serialized */
 return 0;
}
''')
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                    "-o", str(out / "endpoint-lock"), str(out / "endpoint-lock.c")], check=True)
    subprocess.run([str(out / "endpoint-lock")], check=True)
    # An upstream hook mismatch must never silently produce a partly patched firmware.
    (out / "bad.c").write_text((app / "src/endpoints.c").read_text().replace("void zmk_endpoint_clear_reports(void)", "void changed(void)"))
    (out / "bad.cmake").write_text(f'include("{root}/cmake/lism_input_output.cmake")\nlism_input_patch_endpoints("{out}/bad.c" "{out}/bad-out.c" "unused")\n')
    assert subprocess.run(["cmake", "-P", str(out / "bad.cmake")], capture_output=True).returncode != 0
print("PASS: source-owned keyboard/consumer union, boot rollover, stale epochs, pinned USB/BLE snapshots and fail-closed hooks")
