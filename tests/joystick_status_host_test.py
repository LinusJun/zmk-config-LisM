"""Compile the actual GPIO status source with deterministic USB/battery/GPIO mocks."""
from pathlib import Path
import subprocess,tempfile,re
root=Path(__file__).resolve().parents[1]
source=(root/'src/joystick_status.c').read_text()
source=re.sub(r'^#include .*$', '', source, flags=re.M)
prelude=r"""
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#define CONFIG_ZMK_LOG_LEVEL 0
#define CONFIG_CHARGE_INDICATOR 0
#define CONFIG_RGBLED_WIDGET 0
#define IS_ENABLED(x) (x)
#define LOG_MODULE_REGISTER(...)
#define BUILD_ASSERT(x,msg) _Static_assert(x,msg)
#define ARG_UNUSED(x) ((void)(x))
#define BIT(n) (1u<<(n))
#define DT_NODELABEL(x) 0
#define DEVICE_DT_GET(x) (&mock_port)
#define K_MSEC(x) (x)
#define K_NO_WAIT 0
#define GPIO_INPUT 1
#define GPIO_OUTPUT_HIGH 2
#define GPIO_PULL_UP 4
#define GPIO_INT_EDGE_BOTH 8
#define SYS_INIT(...)
#define ZMK_EV_EVENT_BUBBLE 0
#define ZMK_LISTENER(...)
#define ZMK_SUBSCRIPTION(...)
struct device { int dummy; };
struct gpio_callback { int dummy; };
struct k_work { int dummy; };
struct k_work_delayable { int dummy; };
struct zmk_battery_state_changed { uint8_t state_of_charge; };
typedef struct { int type; struct zmk_battery_state_changed battery; } zmk_event_t;
static struct device mock_port;
static bool usb_power;
static int charge_stat, last_delay, flags[32], outputs[32];
static int64_t uptime;
#define K_WORK_DELAYABLE_DEFINE(name, fn) struct k_work_delayable name
static bool device_is_ready(const struct device *d) { (void)d; return true; }
static int gpio_pin_configure(const struct device *d, int pin, int f) {(void)d;flags[pin]=f;return 0;}
static int gpio_pin_set_raw(const struct device *d,int pin,int value){(void)d;outputs[pin]=value;return 0;}
static int gpio_pin_get_raw(const struct device *d,int pin){(void)d;(void)pin;return charge_stat;}
static int gpio_add_callback(const struct device *d,struct gpio_callback *c){(void)d;(void)c;return 0;}
static void gpio_init_callback(struct gpio_callback *cb,void (*fn)(const struct device *,struct gpio_callback *,uint32_t),uint32_t pins){(void)cb;(void)fn;(void)pins;}
static int gpio_pin_interrupt_configure(const struct device *d,int pin,int f){(void)d;(void)pin;(void)f;return 0;}
static void k_work_reschedule(struct k_work_delayable *w,int delay){(void)w;last_delay=delay;}
static void k_work_schedule(struct k_work_delayable *w,int delay){(void)w;last_delay=delay;}
static int64_t k_uptime_get(void){return uptime;}
static bool zmk_usb_is_powered(void){return usb_power;}
static const struct zmk_battery_state_changed *as_zmk_battery_state_changed(const zmk_event_t *e){return e->type==1?&e->battery:NULL;}
"""
test=r"""
static void color(bool r,bool g,bool b){assert(outputs[26]==!r && outputs[30]==!g && outputs[6]==!b);}
int main(void){
 last_delay=-1;
 zmk_event_t early={.type=1,.battery={.state_of_charge=40}};
 status_listener(&early);status_update(NULL);charge_irq(&mock_port,&charge_callback,BIT(17));
 assert(last_delay==-1 && battery_known); /* no GPIO work before init */
 battery_known=false;
 assert(status_init()==0);
 assert(flags[13]==GPIO_INPUT); /* no active drive or pull on ISET */
 assert(flags[17]==(GPIO_INPUT|GPIO_PULL_UP));
 usb_power=false;charge_stat=1;status_update(NULL);color(false,false,true);assert(last_delay==250);
 uptime=250;status_update(NULL);color(false,false,false);assert(last_delay==60000);
 usb_power=true;charge_stat=0;status_update(NULL);color(true,false,false); /* unknown != full */
 zmk_event_t event={.type=1,.battery={.state_of_charge=40}};
 status_listener(&event);status_update(NULL);color(true,true,false);
 event.battery.state_of_charge=90;status_listener(&event);status_update(NULL);color(false,true,false);
 charge_stat=1;status_update(NULL);color(false,false,false); /* USB present != charging */
 usb_power=false;status_update(NULL);color(false,false,false);
 event.battery.state_of_charge=8;status_listener(&event);status_update(NULL);color(true,false,false);assert(last_delay==150);
 uptime+=150;status_update(NULL);color(false,false,false);
 charge_irq(&mock_port,&charge_callback,BIT(17));assert(last_delay==10); /* nonblocking debounce */
 usb_power=true;charge_stat=-5;status_update(NULL);color(false,false,false); /* failed read is not charging */
 puts("PASS: safe ISET GPIO input, distinct CHG/RGB ownership, unknown/low/mid/high charge colors, USB without charging, unplug, bounded low-battery blink, nonblocking IRQ, invalid status");
}
"""
with tempfile.TemporaryDirectory() as directory:
 folder=Path(directory);c=folder/'status.c';c.write_text(prelude+source+test)
 binary=folder/'test'
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
