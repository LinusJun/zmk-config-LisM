"""Execute generated asynchronous USB sender with a persistently busy HID IN."""
from pathlib import Path
import sys,subprocess,tempfile
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
W=P.parent/'workspace';usb=next((W/'build-dongle').rglob('lism_inputs_usb.c')).read_text()
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <assert.h>
#include <errno.h>
#include <string.h>
#define __aligned(n) __attribute__((aligned(n)))
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
enum {USB_DC_SUSPEND,USB_DC_ERROR,USB_DC_RESET,USB_DC_DISCONNECTED,USB_DC_UNKNOWN,READY};
static int status=READY,hid_sem,hid_dev,waited,gives,writes,write_error;
static bool bridge,busy;
static const uint8_t *dma;static uint32_t epoch=1;
static bool lism_input_usb_nonblocking(void){return bridge;}
static uint32_t lism_input_merge_generation(void){return epoch;}
static int zmk_usb_get_status(void){return status;}
static int usb_wakeup_request(void){return -EAGAIN;}
static int k_sem_take(int*s,int timeout){(void)s;waited+=timeout;return busy?-EAGAIN:0;}
static void k_sem_give(int*s){(void)s;gives++;}
static int hid_int_ep_write(int d,const uint8_t*p,size_t n,void*x){(void)d;(void)n;(void)x;dma=p;writes++;return write_error;}
'''
main=r'''
int main(void){
 uint8_t report[8]={42};bridge=true;busy=true;
 for(int i=0;i<1000;i++)assert(zmk_usb_hid_send_report(report,8,epoch)==-EAGAIN);
 assert(waited==0 && writes==0 && gives==0);
 bridge=false;assert(zmk_usb_hid_send_report(report,8,epoch)==-EAGAIN);assert(waited==30);
 busy=false;bridge=true;assert(!zmk_usb_hid_send_report(report,8,epoch));assert(dma!=report);
 report[0]=99;assert(dma[0]==42); /* caller stack never escapes to USB DMA */
 assert(zmk_usb_hid_send_report(report,8,0)==-ESTALE && gives==1);
 write_error=-EIO;assert(zmk_usb_hid_send_report(report,8,epoch)==-EIO && gives==2);
 assert(zmk_usb_hid_send_report(report,129,epoch)==-EMSGSIZE);
 return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
 f=Path(tmp)/'test.c';f.write_text(pre+function(usb,'zmk_usb_hid_send_report')+main);b=Path(tmp)/'test'
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(f),'-o',str(b)],check=True);subprocess.run([str(b)],check=True)
print('PASS: 1000 busy USB sends wait zero in bridge context; normal policy/DMA lifetime/stale epochs preserved')
