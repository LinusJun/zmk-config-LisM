"""Real xyz -> peripheral framing/CRC -> filter -> central/split -> real zxy.
Hardware, input scheduling and USB HID are not emulated by this test.
"""
from pathlib import Path
import argparse,ast,re,subprocess,tempfile,json,hashlib,sys
P=Path(__file__).resolve().parents[2]
a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True);W=a.parse_args().workspace
ROOT=P/'source';D=W/'module-dongle/src/split/esb'
sys.path.insert(0,str(W/'zephyr/scripts/dts/python-devicetree/src'))
from devicetree import dtlib
right=dtlib.DT(str(W/'build-right_set1/zephyr/zephyr.dts'))
central=dtlib.DT(str(W/'build-dongle/zephyr/zephyr.dts'))
for tree,path in [(right,'/zip_xyz'),(central,'/zip_zxy')]:
    n=tree.get_node(path)
    assert n.props['type'].to_num()==2 and n.props['z-code'].to_num()==2
    assert n.props['xy-codes'].to_nums()==[0,1]
assert 'decompress' in central.get_node('/zip_zxy').props
rn=right.get_node('/split_inputs/trackball_split_input_right@1')
assert rn.props['reg'].to_num()==1
assert int.from_bytes(rn.props['input-processors'].value[-4:],'big')==right.get_node('/zip_xyz').props['phandle'].to_num()
assert int.from_bytes(central.get_node('/trackball_split_listener_right').props['input-processors'].value[:4],'big')==central.get_node('/zip_zxy').props['phandle'].to_num()
def stripped(path):return re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',Path(path).read_text(),flags=re.M)
def function(path,name):
    source=Path(path).read_text()
    m=re.search(r'\b'+name+r'\([^;]*?\)\s*\{',source);assert m,name
    start=source.rfind('\n',0,m.start())+1
    if not source[start:m.start()].strip():start=source.rfind('\n',0,start-1)+1
    pos=source.index('{',m.start());depth=1;i=pos+1
    while depth:
        if source[i]=='{':depth+=1
        elif source[i]=='}':depth-=1
        i+=1
    return source[start:i]
tree=ast.parse((ROOT/'tests/upstream_esb_host_test.py').read_text())
pre=next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pre' for t in n.targets))
xyz=W/'zmk-input-processor-xyz/src/pointing/input_processor_xyz.c'
xyz_text=xyz.read_text();xyz_body=xyz_text[xyz_text.index('struct zip_xyz_config'):xyz_text.index('static struct zmk_input_processor_driver_api')]
extra=r'''
#include <sys/types.h>
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define LOG_WRN(...)
#define LOG_DBG(...)
#define CONFIG_ZMK_INPUT_SPLIT 1
#define CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING 1
#define CONFIG_ZMK_INPUT_SPLIT_MAX_TRACKED_KEYS 5
#define CONFIG_ZMK_INPUT_PROCESSOR_XYZ_CODES_MAX_LEN 2
#define CONFIG_ZMK_SPLIT_ESB_RETRY_INPUT_EVENT 0
#define CONFIG_ZMK_SPLIT_ESB_RETRY_KEY_POSITION 3
#define CONFIG_ZMK_SPLIT_ESB_RETRY_SENSOR_EVENT 2
#define CONFIG_ZMK_SPLIT_ESB_RETRY_BATTERY_EVENT 1
#define ZMK_INPUT_PROC_CONTINUE 0
#define ZMK_INPUT_PROC_STOP 1
#define K_NO_WAIT 0
struct device {const void *config;void *data;};
struct zmk_input_processor_state {int unused;};
static struct device proxy;
static int input_report(const struct device*,uint8_t,uint16_t,int32_t,bool,int);
'''
input_header=(W/'zephyr/include/zephyr/input/input.h').read_text()
event_struct=input_header[input_header.index('struct input_event {'):input_header.index('};',input_header.index('struct input_event {'))+2]
setup=r'''
static struct zip_xyz_config xy_config={.type=INPUT_EV_REL,.as_code=INPUT_REL_Z,.codes_len=2,.codes={INPUT_REL_X,INPUT_REL_Y}};
static struct zip_xyz_data xy_data;
static struct device compressor={.config=&xy_config,.data=&xy_data};
static struct device decompressor={.config=&xy_config};
static struct input_event observed[8];static unsigned observed_n;
static int input_report(const struct device *dev,uint8_t type,uint16_t code,int32_t value,bool sync,int timeout) {
    struct input_event e={.dev=dev,.type=type,.code=code,.value=value,.sync=sync};
    if(code==INPUT_REL_Z) {
        assert(zip_xyz_handle_event_decompress(&decompressor,&e,0,0,NULL)==ZMK_INPUT_PROC_STOP);
    } else {assert(observed_n<8);observed[observed_n++]=e;}
    return 0;
}
struct zmk_split_transport_central {int unused;};
const struct zmk_split_transport_central esb_central={0};
static const struct zmk_split_transport_central *active_transport=&esb_central;
struct zmk_position_state_changed {uint8_t source;uint32_t position;bool state;int64_t timestamp;};
struct zmk_sensor_event {uint8_t sensor_index,channel_data_size;int64_t timestamp;struct zmk_sensor_channel_data channel_data[1];};
struct zmk_peripheral_battery_state_changed {uint8_t source,state_of_charge;};
struct zmk_split_central_status_changed {uint8_t slot;bool connected;};
static uint8_t peripheral_battery_levels[2];
static int raise_zmk_position_state_changed(struct zmk_position_state_changed e){return 0;}
static int raise_zmk_sensor_event(struct zmk_sensor_event e){return 0;}
static int raise_zmk_peripheral_battery_state_changed(struct zmk_peripheral_battery_state_changed e){return 0;}
static int raise_zmk_split_central_status_changed(struct zmk_split_central_status_changed e){return 0;}
bool lism_radio_ready(void){return true;}
void lism_notify_status(void){}
struct zis_entry {uint8_t reg;const struct device *dev;};
static const struct zis_entry proxy_inputs[]={{.reg=1,.dev=&proxy}};
static uint16_t proxy_active_keys[1][5];
static uint8_t sent_bytes[64];static unsigned sent_len,sent_count;
int zmk_split_esb_send(app_esb_data_t *p){
    assert(p->pipe==1 && p->len<=64);memcpy(sent_bytes,p->data,p->len);sent_len=p->len;sent_count++;return 0;
}
'''
producer=r'''
static const uint8_t peripheral_id=1;
RING_BUF_DECLARE(tx_buf,2048);
static struct zmk_split_esb_state state={.tx_pipe=1,.tx_buf=&tx_buf};
static void begin_tx(void){zmk_split_esb_tx(&state);}
'''
main=r'''
static void run_pair(int16_t x,int16_t y) {
    observed_n=0;unsigned previous=sent_count;
    struct input_event e={.dev=&proxy,.type=INPUT_EV_REL,.code=INPUT_REL_X,.value=x,.sync=0};
    assert(zip_xyz_handle_event_compress(&compressor,&e,0,0,NULL)==ZMK_INPUT_PROC_STOP);
    e=(struct input_event){.dev=&proxy,.type=INPUT_EV_REL,.code=INPUT_REL_Y,.value=y,.sync=1};
    assert(zip_xyz_handle_event_compress(&compressor,&e,0,0,NULL)==ZMK_INPUT_PROC_CONTINUE);
    assert(e.code==INPUT_REL_Z && e.sync==1);
    struct zmk_split_transport_peripheral_event event={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT,
        .data.input_event={.reg=1,.type=e.type,.code=e.code,.value=e.value,.sync=e.sync}};
    assert(!split_peripheral_esb_report_event_impl(&event));assert(sent_count==previous+1);
    RING_BUF_DECLARE(rx,128);ring_buf_put(&rx,sent_bytes,sent_len);
    struct esb_event_envelope env;
    assert(!zmk_split_esb_get_item(&rx,(void*)&env,sizeof(env)));
    assert(!lism_valid_event_v31(1,&env)); /* original bug is a failing control */
    assert(lism_wire_valid(1,sent_bytes,sent_len));assert(lism_valid_event(1,&env));
    assert(!lism_valid_event(0,&env));
    struct esb_event_envelope bad=env;bad.payload.event.data.input_event.reg=0;assert(!lism_valid_event(1,&bad));
    bad=env;bad.payload.event.data.input_event.code=0xffff;assert(!lism_valid_event(1,&bad));
    uint8_t saved=sent_bytes[sent_len-1];sent_bytes[sent_len-1]^=1;
    ring_buf_put(&rx,sent_bytes,sent_len);assert(zmk_split_esb_get_item(&rx,(void*)&bad,sizeof(bad))==-EINVAL);
    sent_bytes[sent_len-1]=saved;
    assert(!lism_handle_event(1,&env.payload.event));
    assert(observed_n==2);
    assert(observed[0].code==INPUT_REL_Y && (uint32_t)observed[0].value==(uint16_t)y && !observed[0].sync);
    assert(observed[1].code==INPUT_REL_X && (uint32_t)observed[1].value==(uint16_t)x && observed[1].sync);
    assert(!xy_data.rmds[0] && !xy_data.rmds[1]);
}
int main(void){
    const int16_t pairs[][2]={{1,2},{-1,5},{300,-500},{-32768,32767},{32767,-32768},{0,0},{10,0},{0,-10}};
    for(unsigned i=0;i<ARRAY_SIZE(pairs);i++)run_pair(pairs[i][0],pairs[i][1]);
    uint32_t r=1234567;
    for(unsigned i=0;i<1000;i++){r=r*1664525+1013904223;int16_t x=r;r=r*1664525+1013904223;run_pair(x,(int16_t)r);}
    puts("PASS: 1008 real xyz -> production peripheral framing/CRC -> new filter -> real ZMK central/input-split -> real zxy pairs; 16-bit patterns/sync preserved, decoder emits unsigned16 in int32 (no sign extension here); old filter rejects each; wrong pipe/reg/code and CRC rejected. Downstream sign handling, input queue and USB HID not modeled.");
}
'''
old_filter=function(P/'reference_v31_link.c','lism_valid_event').replace('lism_valid_event(','lism_valid_event_v31(',1)
parts=[pre,extra,event_struct,stripped(W/'zephyr/include/zephyr/sys/ring_buffer.h'),stripped(W/'zephyr/lib/utils/ring_buffer.c'),stripped(W/'zephyr/lib/crc/crc32_sw.c'),stripped(W/'zmk/app/include/zmk/split/transport/types.h'),stripped(D/'app_esb.h'),stripped(D/'common.h'),stripped(ROOT/'src/esb_upstream/link.h'),xyz_body,setup,
       function(W/'zmk/app/src/pointing/input_split.c','replace_active_key'),function(W/'zmk/app/src/pointing/input_split.c','zmk_input_split_report_peripheral_event'),
       function(W/'zmk/app/src/split/central.c','zmk_split_transport_central_peripheral_event_handler'),
       stripped(ROOT/'src/esb_upstream/common.c'),stripped(ROOT/'src/esb_upstream/link.c'),producer,
       function(W/'module-right_set1/src/split/esb/peripheral.c','get_payload_data_size'),function(W/'module-right_set1/src/split/esb/peripheral.c','get_retry_count'),
       function(W/'module-right_set1/src/split/esb/peripheral.c','split_peripheral_esb_report_event_impl'),old_filter,main]
with tempfile.TemporaryDirectory() as directory:
    d=Path(directory);(d/'test.c').write_text('\n'.join(parts))
    subprocess.run(['cc','-std=c11','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(d/'test.c'),'-o',str(d/'test')],check=True)
    result=subprocess.run([str(d/'test')],check=True,capture_output=True,text=True);print(result.stdout,end='')
(W/'trackball-path-test.json').write_text(json.dumps({'passed':True,'pairs':1008,'xyz_source_sha256':hashlib.sha256(xyz.read_bytes()).hexdigest(),'actual_production_modules_used':True,'hardware_or_USB_HID_tested':False,'output':result.stdout},indent=2)+'\n')
