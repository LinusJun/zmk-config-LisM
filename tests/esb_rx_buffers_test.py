"""Run old/new generated central RX paths with real Zephyr rings under ASAN.
Compare live queue capacity, accepted/rejected bursts, dispatch order and hostile
pipe boundaries. Modeled scheduling is not a physical latency measurement.
"""
from pathlib import Path
import ast,re,subprocess,tempfile,json,hashlib,sys
P=Path(__file__).resolve().parents[1];W=P.parent/'workspace';B=Path('/rxbaseline');sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function

def stripped(path):return re.sub(r'^\s*#\s*(?:include|pragma once).*$', '',Path(path).read_text(),flags=re.M)
values={}
for node in ast.parse((P/'tests/upstream_esb_host_test.py').read_text()).body:
 if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
  for target in node.targets:
   if isinstance(target,ast.Name):values[target.id]=node.value.value
cfg=dict(line.split('=',1) for line in (P/'artifacts/dongle.config').read_text().splitlines() if line.startswith('CONFIG_') and '=' in line)
pre=values['pre']+r'''
#define CONFIG_ESB_PIPE_COUNT 8
#define CONFIG_ZMK_SPLIT_ESB_RF_CH_HOP 0
#define KB_ROUTE_EVENT 126
#define KB_MIRROR_EVENT 125
#define LOG_WRN(...) ((void)0)
#define LOG_ERR(...) ((void)0)
#define K_WORK_DEFINE(n,f) struct k_work n
struct kb_route_status{uint8_t version,mode,profile;uint32_t epoch,authority;uint8_t phase,target;} __packed;
struct kb_mirror{uint32_t epoch;uint8_t version,wpm,layer,mods,indicators,profile,host_connected,left_battery,left_online;} __packed;
static struct k_work notify_status_work,process_rx_work;
static unsigned submits,dispatched;static uint64_t trace=1469598103934665603ULL;
static int k_work_submit(struct k_work*w){assert(w==&notify_status_work||w==&process_rx_work);submits++;return 0;}
'''+f"\n#define CONFIG_ZMK_SPLIT_ESB_EVENT_BUFFER_ITEMS {cfg['CONFIG_ZMK_SPLIT_ESB_EVENT_BUFFER_ITEMS']}\n#define CONFIG_ZMK_SPLIT_ESB_CMD_BUFFER_ITEMS {cfg['CONFIG_ZMK_SPLIT_ESB_CMD_BUFFER_ITEMS']}\n"
post=r'''
static void process_rx_cb(uint8_t pipe);
void zmk_split_esb_tx(struct zmk_split_esb_state*s){(void)s;assert(0); /* RX-only fixture must not enter TX path. */}
static int lism_handle_event_stub(uint8_t source,const struct zmk_split_transport_peripheral_event*e){assert(source<2);dispatched++;trace^=source;trace*=1099511628211ULL;trace^=e->data.key_position_event.position;trace*=1099511628211ULL;return 0;}
int lism_handle_event(uint8_t source,const struct zmk_split_transport_peripheral_event*e){return lism_handle_event_stub(source,e);}
int zmk_split_esb_init(app_esb_mode_t mode,app_esb_callback_t cb){assert(mode==APP_ESB_MODE_PRX&&cb);return 0;}
'''
main=r'''
static size_t frame(uint8_t*out,uint8_t source,uint8_t position){
 struct esb_event_envelope e={.prefix={.magic_prefix="ZmKe",.payload_size=1+sizeof(e.payload.event.type)+sizeof(e.payload.event.data.key_position_event)},.payload={.source=source,.event={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=position,.pressed=1}}}};
 size_t n=sizeof(e.prefix)+e.prefix.payload_size;memcpy(out,&e,n);struct esb_msg_postfix crc={.crc=crc32_ieee(out,n)};memcpy(out+n,&crc,sizeof(crc));return n+sizeof(crc);
}
int main(void){
 assert(!zmk_split_esb_central_init());assert(RX_RING_BUF_SIZE==RX_BUFFER_SIZE*CONFIG_ZMK_SPLIT_ESB_EVENT_BUFFER_ITEMS);
 for(unsigned pipe=0;pipe<2;pipe++)assert(ring_buf_capacity_get(&rx_bufs[pipe])==RX_RING_BUF_SIZE);
 uint8_t bytes[64],saved[2][RX_RING_BUF_SIZE];size_t n=frame(bytes,0,0);
 unsigned accepted[2]={0},rejected[2]={0};
 for(unsigned i=0;i<200;i++)for(unsigned pipe=0;pipe<2;pipe++){
  n=frame(bytes,pipe,i%46);app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=pipe,.buf=bytes,.data_length=n};unsigned before=submits;
  zmk_split_esb_on_prx_esb_callback(&e);if(e.accepted){accepted[pipe]++;assert(submits==before+1);}else{rejected[pipe]++;assert(submits==before);}
 }
 assert(accepted[0]==RX_RING_BUF_SIZE/n&&accepted[1]==accepted[0]&&rejected[0]&&rejected[1]);
 memcpy(saved,rx_bufs_data,sizeof(saved));unsigned before=submits;
 for(unsigned pipe=2;pipe<256;pipe++){
  app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=pipe,.buf=bytes,.data_length=n};zmk_split_esb_on_prx_esb_callback(&e);assert(!e.accepted&&submits==before);
 }
 assert(!memcmp(saved,rx_bufs_data,sizeof(saved))); /* no out-of-range indexing or mutation */
 process_rx_work_cb(NULL);assert(dispatched==accepted[0]+accepted[1]);
 assert(!ring_buf_size_get(&rx_bufs[0])&&!ring_buf_size_get(&rx_bufs[1]));
 /* Reuse after saturation and wrap, alternating both sources. */
 for(unsigned i=0;i<1000;i++){
  unsigned pipe=i%2;n=frame(bytes,pipe,i%46);app_esb_event_t e={.evt_type=APP_ESB_EVT_RX,.pipe=pipe,.buf=bytes,.data_length=n};zmk_split_esb_on_prx_esb_callback(&e);assert(e.accepted);process_rx_work_cb(NULL);
 }
 before=dispatched;n=frame(bytes,1,3);bytes[n-1]^=1;app_esb_event_t bad={.evt_type=APP_ESB_EVT_RX,.pipe=1,.buf=bytes,.data_length=n};zmk_split_esb_on_prx_esb_callback(&bad);assert(bad.accepted);process_rx_work_cb(NULL);assert(dispatched==before);
 /* Length and null guards still reject before indexing. */
 app_esb_event_t empty={.evt_type=APP_ESB_EVT_RX,.pipe=1,.buf=NULL,.data_length=n};before=submits;zmk_split_esb_on_prx_esb_callback(&empty);assert(!empty.accepted&&submits==before);
 empty.buf=bytes;empty.data_length=0;zmk_split_esb_on_prx_esb_callback(&empty);assert(!empty.accepted&&submits==before);
 empty.data_length=CONFIG_ESB_MAX_PAYLOAD_LENGTH-LISM_WIRE_HEADER+1;zmk_split_esb_on_prx_esb_callback(&empty);assert(!empty.accepted&&submits==before);
 printf("capacity=%u accepted=%u,%u rejected=%u,%u dispatched=%u trace=%llu submits=%u\n",(unsigned)RX_RING_BUF_SIZE,accepted[0],accepted[1],rejected[0],rejected[1],dispatched,(unsigned long long)trace,submits);
 return 0;
}
'''
traces={}
with tempfile.TemporaryDirectory() as tmp:
 for label,root in [('v47',B),('v471',P.parent)]:
  d=root/'workspace/module-dongle/src/split/esb';central=(d/'central.c').read_text()
  arrays=central[central.index('#if IS_ENABLED(CONFIG_ZMK_SPLIT_ESB_MSG_POSTFIX_CRC)'):central.index('static void begin_tx')]
  funcs='\n'.join(function(central,name) for name in ['zmk_split_esb_on_prx_esb_callback','zmk_split_esb_central_init','process_rx_work_cb','process_rx_cb'])
  parts=[pre,stripped(W/'zephyr/include/zephyr/sys/ring_buffer.h'),stripped(W/'zephyr/lib/utils/ring_buffer.c'),stripped(W/'zephyr/lib/crc/crc32_sw.c'),stripped(W/'include-dongle/zmk/split/transport/types.h'),stripped(d/'app_esb.h'),stripped(d/'common.h'),stripped(P/'src/esb_upstream/link.h'),arrays,post,function((P/'src/esb_upstream/link.c').read_text(),'lism_valid_event'),function((P/'src/esb_upstream/common.c').read_text(),'zmk_split_esb_cb'),function((P/'src/esb_upstream/common.c').read_text(),'zmk_split_esb_get_item'),funcs,main]
  f=Path(tmp)/(label+'.c');exe=Path(tmp)/label;f.write_text('\n'.join(parts));subprocess.run(['cc','-std=gnu11','-fsanitize=address,undefined','-Werror=implicit-function-declaration',str(f),'-o',str(exe)],check=True)
  traces[label]=subprocess.check_output([str(exe)],text=True).strip()
assert traces['v47']==traces['v471'],traces
report={'passed':True,'traces':traces,'scope':'Actual old/new generated central declarations/init/worker and production common RX callback + parser + validator, real pinned Zephyr rings/CRC; ASAN+UBSAN. No physical radio latency measurement.'}
(P.parent/'verification/rx-buffers-native.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS unchanged live capacity, saturation/backpressure, notifications and event order; pipes 2..255 rejected before indexing; 1000 reuse/wrap operations; CRC/null guards; actual old/new trace identical')
print(traces['v471'])
