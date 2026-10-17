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
assert "hid_mouse_draw_snapshot(&status);" in extract("static void draw_hid_mouse(")
assert "hid_rows[LISM_HID_VISIBLE_CANDIDATES]" in ui
assert "Unnamed HID" not in ui
assert "BLE devices; HID" in ui
source = (extract("static void hid_mouse_draw_snapshot(") +
          extract("static void hid_mouse_press(") + extract("static void hid_mouse_tap("))
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
#define HID_CANDIDATE_PAGES ((LISM_HID_CANDIDATES + LISM_HID_VISIBLE_CANDIDATES - 1) / LISM_HID_VISIBLE_CANDIDATES)
static uint8_t hid_page,hid_drawn_page,hid_press_page;
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
static void visible(void){hid_mouse_draw_snapshot(&current);}
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
 /* Twelve stable slots, only three visible rows, four bounded pages. */
 memset(&current,0,sizeof(current));current.pairing=true;current.generation=100;
 portrait=false;visible();assert(hid_page==0);
 candidate(0,"One","00:00:00:00:00:01",1);
 candidate(3,"Four","00:00:00:00:00:04",4);
 candidate(8,"Nine","00:00:00:00:00:09",9);
 candidate(11,"Twelve","00:00:00:00:00:12",12);
 visible();press();hid_mouse_tap(245,155);assert(hid_page==1&&!hid_tap_active);
 /* Page change cancels the old gesture even before a redraw. */
 hid_mouse_tap(20,80);assert(selected==-1);
 visible();press();hid_mouse_tap(20,80);assert(selected==3&&selected_token==4);selected=-1;
 /* Empty slot arriving between press/release on a later page is rejected. */
 visible();press();candidate(4,"Five","00:00:00:00:00:05",5);
 hid_mouse_tap(20,102);assert(selected==-1);
 visible();press();hid_mouse_tap(245,155);assert(hid_page==2);
 visible();press();hid_mouse_tap(20,125);assert(selected==8&&selected_token==9);selected=-1;
 visible();press();hid_mouse_tap(245,155);assert(hid_page==3);
 visible();press();hid_mouse_tap(20,125);assert(selected==11&&selected_token==12);selected=-1;
 /* A new scan resets the page and invalidates any active touch. */
 visible();press();current.generation++;visible();
 assert(hid_page==0&&!hid_tap_active);hid_mouse_tap(20,80);assert(selected==-1);
 for(int p=1;p<=4;p++){visible();press();hid_mouse_tap(245,155);assert(hid_page==p%4);}
 /* Both orientations page identically; footer and row gaps do not select. */
 portrait=true;visible();press();hid_mouse_tap(20,300);assert(hid_page==1);
 visible();press();hid_mouse_tap(20,120);assert(selected==3);selected=-1;
 visible();press();hid_mouse_tap(20,160);assert(selected==-1);
 visible();press();hid_mouse_tap(20,204);assert(selected==-1);
 visible();press();hid_mouse_tap(20,249);assert(selected==-1);
 visible();press();hid_mouse_tap(20,287);assert(hid_page==1&&selected==-1);
 visible();press();hid_mouse_tap(20,316);assert(hid_page==1&&selected==-1);
 current.pairing=false;visible();press();hid_mouse_tap(20,300);assert(hid_page==1);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="lism-hid-ui-") as directory:
    c, binary = Path(directory) / "test.c", Path(directory) / "test"
    c.write_text(prelude + header + mocks + source + checks)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("PASS: actual pairing UI identity, 12 slots / 3 rows / 4 pages, page+scan gesture invalidation, empty-row race, portrait gaps and forget confirmation")
