"""Compile actual pairing press/release UI code against immutable drawn snapshots."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
ui = (root / "src/xiao147_ui.c").read_text()
header = (root / "src/lism_hid_host.h").read_text().replace("#pragma once", "")
header = "\n".join(line for line in header.splitlines() if "zephyr/bluetooth/addr.h" not in line)


def extract(signature):
    start = ui.index(signature)
    return ui[start:ui.index("\n}\n", start) + 3]


assert "hid_mouse_press();" in extract("static void touch_handler(")
assert "hid_drawn_status = status;" in extract("static void draw_hid_mouse(")
source = extract("static void hid_mouse_press(") + extract("static void hid_mouse_tap(")
prelude = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
typedef struct {int unused;} bt_addr_le_t;
'''
mocks = r'''
#define SCREEN_W 320
#define SCREEN_H 172
static bool portrait,hid_drawn_valid,hid_tap_active;
static uint32_t hid_forget_deadline,now=100;
static struct lism_hid_status hid_drawn_status,hid_press_status,current;
static int selected=-1,paired,cancelled,forgotten;
static uint32_t selected_token;
static bool xiao147_rotation_is_portrait(void){return portrait;}
static uint32_t k_uptime_get_32(void){return now;}
static void redraw(void *unused){(void)unused;}
void lism_hid_host_snapshot(struct lism_hid_status *out){*out=current;}
void lism_hid_host_select(uint8_t index,uint32_t token){selected=index;selected_token=token;}
void lism_hid_host_pair(void){paired++;}
void lism_hid_host_cancel(void){cancelled++;}
void lism_hid_host_forget(void){forgotten++;}
'''
checks = r'''
static void visible(void){hid_drawn_status=current;hid_drawn_valid=true;}
static void press(void){hid_mouse_press();assert(hid_tap_active==hid_drawn_valid);}
static void candidate(int index,const char *name,const char *address,uint32_t token){
 struct lism_hid_candidate *c=&current.candidates[index];c->valid=true;c->token=token;
 strcpy(c->name,name);strcpy(c->address,address);
}
int main(void){
 current.pairing=true;current.generation=10;visible();press();
 /* An empty row becoming populated after press must never connect. */
 candidate(0,"Mouse A","00:00:00:00:00:01",1);hid_mouse_tap(20,80);assert(selected==-1);
 /* Arrival which wasn't actually drawn also cannot become a press target. */
 press();hid_mouse_tap(20,80);assert(selected==-1);
 visible();press();hid_mouse_tap(20,80);assert(selected==0&&selected_token==1);selected=-1;
 /* Same row and name cannot mask another device or another scan generation. */
 visible();press();current.generation++;hid_mouse_tap(20,80);assert(selected==-1);
 visible();press();current.candidates[0].token++;hid_mouse_tap(20,80);assert(selected==-1);
 visible();press();strcpy(current.candidates[0].address,"00:00:00:00:00:02");
 hid_mouse_tap(20,80);assert(selected==-1);
 visible();press();strcpy(current.candidates[0].name,"Mouse B");hid_mouse_tap(20,80);assert(selected==-1);
 visible();press();current.pairing=false;hid_mouse_tap(20,80);assert(selected==-1);
 current.pairing=true;visible();press();current.candidates[0].valid=false;
 hid_mouse_tap(20,80);assert(selected==-1);
 candidate(1,"Mouse C","00:00:00:00:00:03",3);portrait=true;visible();press();
 hid_mouse_tap(20,165);assert(selected==1&&selected_token==3);selected=-1;
 visible();press();hid_mouse_tap(20,162);assert(selected==-1); /* 4px gap */
 hid_drawn_valid=false;press();assert(!hid_tap_active);hid_mouse_tap(20,165);assert(selected==-1);
 /* A button must not change from Start to Forget during a touch. */
 memset(&current,0,sizeof(current));portrait=false;visible();press();
 current.saved=true;hid_mouse_tap(20,55);assert(!forgotten&&!hid_forget_deadline);
 visible();press();hid_mouse_tap(20,55);assert(!forgotten&&hid_forget_deadline==5100);
 visible();press();hid_mouse_tap(20,55);assert(forgotten==1&&!hid_forget_deadline);
 visible();press();hid_mouse_tap(20,55);now=6000;visible();press();hid_mouse_tap(20,55);
 assert(forgotten==1&&hid_forget_deadline==11000);
 current.saved=false;visible();press();hid_mouse_tap(20,55);assert(paired==1);
 current.pairing=true;visible();press();hid_mouse_tap(20,55);assert(cancelled==1);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="lism-hid-ui-") as directory:
    c, binary = Path(directory) / "test.c", Path(directory) / "test"
    c.write_text(prelude + header + mocks + source + checks)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("PASS: actual pairing UI drawn/press identity, empty-row race, token/generation, portrait gaps and forget confirmation")
