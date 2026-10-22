"""Execute actual ball callback and pinned XYZ compressor across closing gates."""
from pathlib import Path
import runpy,tempfile,subprocess
P=Path(__file__).resolve().parents[1];b=runpy.run_path(str(P/'tests/kb_route_native_test.py'));pre=b['pre'];function=b['function'];hub=b['hub']
pre+=r'''
#define ZMK_INPUT_PROC_CONTINUE 0
#define ZMK_INPUT_PROC_STOP 1
#define CONFIG_ZMK_INPUT_PROCESSOR_XYZ_CODES_MAX_LEN 2
struct input_event {void*dev;uint8_t type;uint16_t code;int32_t value;bool sync;};
struct device {void *data;const void*config;};struct zmk_input_processor_state {int x;};
struct zip_xyz_config {uint8_t type;uint16_t as_code;size_t codes_len;uint16_t codes[2];};struct zip_xyz_data {int16_t rmds[2];};
static struct zip_xyz_data xy[2];static struct zip_xyz_config cfg={.type=INPUT_EV_REL,.as_code=INPUT_REL_Z,.codes_len=2,.codes={INPUT_REL_X,INPUT_REL_Y}};
static struct device devices[2]={{&xy[0],&cfg},{&xy[1],&cfg}};
#define zip_xyz 0
#define zip_zxy 1
#define DT_NODELABEL(n) n
#define DEVICE_DT_GET(n) (&devices[n])
struct proc {const struct device *dev;uint32_t param1,param2;};static struct proc processors[]={{&devices[0],0,0}};
static struct k_spinlock state_lock;static struct lism_snapshot physical[2];static bool initialized=true,switching;static uint8_t current_mode=KB_ROUTE_BLE;
static unsigned relayed,proxied;static bool inject_close;static int32_t proxy_value;
static int kb_route_relay(unsigned source,const struct zmk_split_transport_peripheral_event*e){assert(source==1);(void)e;relayed++;return 0;}
static int zmk_input_split_report_peripheral_event(uint8_t r,uint8_t t,uint16_t c,int32_t v,bool sync){assert(r==1);(void)t;(void)c;(void)sync;proxy_value=v;proxied++;if(inject_close)switching=true;return 0;}
'''
xyz=Path('/deps/zmk-input-processor-xyz/src/pointing/input_processor_xyz.c').read_text();ball=(P/'src/kb_route/trackball.c').read_text()
code=pre+function(xyz,'zip_xyz_handle_event_compress')+r'''
static int zmk_input_processor_handle_event(const struct device*d,struct input_event*e,uint32_t p1,uint32_t p2,void*s){return zip_xyz_handle_event_compress(d,e,p1,p2,s);}
'''+ '\n'.join(function(hub,n) for n in ['physical_event','kb_route_accept_pointer','kb_route_input','all_released'])+function(ball,'ball')+function(ball,'kb_route_pointer_reset')+r'''
int main(void){
 struct input_event e={.type=INPUT_EV_KEY,.code=INPUT_BTN_LEFT,.value=1,.sync=true};inject_close=true;ball(&e,NULL);assert(switching&&physical[1].buttons==1);e.value=0;ball(&e,NULL);assert(!physical[1].buttons&&all_released()); /* release survives closed entry gate */
 switching=false;inject_close=false;struct input_event x={.type=INPUT_EV_REL,.code=INPUT_REL_X,.value=1234,.sync=false};ball(&x,NULL);assert(xy[0].rmds[0]==1234);switching=true;kb_route_pointer_reset();assert(!xy[0].rmds[0]&&!xy[0].rmds[1]);
 unsigned before=proxied;struct input_event y={.type=INPUT_EV_REL,.code=INPUT_REL_Y,.value=-9,.sync=true};ball(&y,NULL);assert(proxied==before&&!xy[0].rmds[1]);switching=false;ball(&y,NULL);assert(proxy_value==0x0000fff7); /* no old X delta */
 current_mode=KB_ROUTE_DONGLE;e.value=1;ball(&e,NULL);assert(physical[1].buttons&&relayed);switching=true;e.value=0;ball(&e,NULL);assert(all_released());
 puts("PASS actual pointer callback: closing-gate button release, partial XYZ reset, no old relative delta, dongle/standalone button tracking");return 0;}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'ball.c';out=Path(t)/'ball';f.write_text(code);subprocess.run(['cc','-std=gnu11','-fsanitize=address,undefined',str(f),'-o',str(out)],check=True);subprocess.run([str(out)],check=True)
