"""Run real HCI TX adaptation and real BLE init-failure/profile-save bodies.
Transport, controller execution, settings and kernel work queue are mocked.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,re,subprocess,sys,tempfile
P=Path(__file__).resolve().parents[2]
a=argparse.ArgumentParser();a.add_argument('--workspace',type=Path,required=True);W=a.parse_args().workspace
def function(t,name):
    m=re.search(r'(?m)^(?:static )?[^\n;]+\b'+name+r'\([^;]*?\)\s*\{',t);assert m,name
    i=t.index('{',m.start())+1;depth=1
    while depth:depth+=(t[i]=='{')-(t[i]=='}');i+=1
    return t[m.start():i]
hci_path=next((W/'build-dongle').rglob('lism_hci_zephyr41.c'))
ble_path=next((W/'build-dongle').rglob('lism_ble_zephyr41.c'))
hci=hci_path.read_text();ble=ble_path.read_text()
old=(W/'nrf/subsys/bluetooth/controller/hci_driver.c').read_text()
original_ble=(W/'zmk/app/src/ble.c').read_text()
sys.path.insert(0,str(P/'source/tools'))
from prepare_hci_zephyr41 import prepare_hci,prepare_ble
from prepare_input_integration import patch_identity
with tempfile.TemporaryDirectory() as d:
    x=Path(d)/'hci.c';y=Path(d)/'ble.c'
    prepare_hci(W/'nrf/subsys/bluetooth/controller/hci_driver.c',x)
    prepare_ble(W/'zmk/app/src/ble.c',y)
    patch_identity(y)
    assert x.read_text()==hci and y.read_text()==ble
# All controller RX and command execution/locking functions remain unchanged.
for name in ['cmd_handle','acl_handle','event_packet_process','data_packet_process','process_hci_msg','hci_driver_receive_process']:
    assert function(hci,name)==function(old,name),name
assert function(ble,'zmk_ble_init').index('k_work_init_delayable')<function(ble,'zmk_ble_init').index('bt_enable(NULL)')
for name in ['zmk_ble_prof_select','ble_save_profile','ble_save_profile_work']:
    assert function(ble,name)==function(original_ble,name),name
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define BIT(x) (1U<<(x))
#define CONFIG_BT_CONN 1
#define CONFIG_SETTINGS 1
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_SETTINGS_SAVE_DEBOUNCE 60000
#define K_MSEC(x) (x)
#define ZMK_BLE_PROFILE_COUNT 5
#define LOG_DBG(...) do{}while(0)
#define LOG_INF(...) do{}while(0)
#define LOG_ERR(...) do{}while(0)
'''
buf_header=(W/'zephyr/include/zephyr/bluetooth/buf.h').read_text()
enum=buf_header[buf_header.index('enum bt_buf_type {'):buf_header.index('};',buf_header.index('enum bt_buf_type {'))+2]
hci_header=(W/'zephyr/include/zephyr/bluetooth/hci_types.h').read_text()
defines='\n'.join(re.findall(r'^#define BT_HCI_H4_(?:CMD|ACL|ISO)\s+[^\n]+',hci_header,re.M))
stubs=r'''
struct device{int unused;};
struct net_buf{uint8_t*data;size_t len;enum bt_buf_type type;int refs;};
static enum bt_buf_type bt_buf_get_type(struct net_buf*b){return b->type;}
static uint8_t net_buf_pull_u8(struct net_buf*b){assert(b->len);b->len--;return *b->data++;}
static void net_buf_unref(struct net_buf*b){b->refs--;}
static unsigned route,handled;
static int handler_rc;
static uint8_t seen[80];static size_t seen_len;
static int capture(struct net_buf*b,unsigned r){handled++;route=r;seen_len=b->len;memcpy(seen,b->data,b->len);return handler_rc;}
static int cmd_handle(struct net_buf*b){return capture(b,1);}
static int acl_handle(struct net_buf*b){return capture(b,2);}
struct k_work{void(*handler)(struct k_work*);};
struct k_work_delayable{struct k_work work;};
static struct k_work_delayable ble_save_work;
static uint8_t active_profile;
static int enable_rc,saves,registrations,advertised,ready_checks;
static bool ready;
static struct k_work_delayable*scheduled;
static int requested_delay;
static int profiles_handler;
static void settings_save_one(const char*key,const void*data,size_t len){assert(!strcmp(key,"ble/active_profile") && len==1);saves++;}
static void k_work_init_delayable(struct k_work_delayable*w,void(*cb)(struct k_work*)){w->work.handler=cb;}
static int k_work_reschedule(struct k_work_delayable*w,int ms){assert(w->work.handler);scheduled=w;requested_delay=ms;return 1;}
static int bt_enable(void*p){assert(!p);return enable_rc;}
static void settings_register(void*p){assert(p==&profiles_handler);registrations++;}
static bool bt_is_ready(void){ready_checks++;return ready;}
static void raise_profile_changed_event(void){}
static int update_advertising(void);
'''
# Execute the real early ready guard; underlying advertising is a counted stub.
adv=function(ble,'lism_update_advertising').replace('lism_update_advertising','update_advertising',1)
guard=adv[:adv.index('    int err = 0;')]+ '    advertised++;return 0;\n}'
main=r'''
static void check(unsigned type,uint8_t*data,size_t len,unsigned wanted) {
 struct net_buf b={.data=data,.len=len,.type=type,.refs=1};unsigned before=handled;
 assert(!hci_driver_send(NULL,&b));assert(handled==before+1 && route==wanted);
 assert(b.data==data && b.len==len && seen_len==len && !memcmp(seen,data,len) && b.refs==0);
}
int main(void){
 uint16_t ops[]={0x0c03,0x1001,0x1002,0x1003,0x1009,0x2001,0x2002,0x2003,0x2005,0x2006,0x2008,0x2009,0x200a,0x2018,0x2019};
 for(unsigned i=0;i<sizeof(ops)/sizeof(*ops);i++) {
  uint8_t data[9]={ops[i]&255,ops[i]>>8,6,1,2,3,4,5,0xc0};check(BT_BUF_CMD,data,sizeof(data),1);
 }
 // First payload byte is never interpreted as H4, even if it is 1,2,5.
 for(unsigned i=0;i<256;i++) {
  uint8_t cmd[]={i,0x20,0};uint8_t acl[]={i,0x20,2,0,0xaa,0xbb};
  check(BT_BUF_CMD,cmd,sizeof(cmd),1);check(BT_BUF_ACL_OUT,acl,sizeof(acl),2);
 }
 uint8_t random_addr[]={0x05,0x20,6,1,2,3,4,5,0xc0};
 struct net_buf old_buf={random_addr,sizeof(random_addr),BT_BUF_CMD,1};
 assert(old_hci_driver_send(NULL,&old_buf)==-EINVAL && old_buf.data==random_addr+1);
 uint8_t reset[]={3,0x0c,0};old_buf=(struct net_buf){reset,sizeof(reset),BT_BUF_CMD,1};
 assert(old_hci_driver_send(NULL,&old_buf)==-EINVAL);
 struct net_buf bad={reset,0,BT_BUF_CMD,1};assert(hci_driver_send(NULL,&bad)==-EINVAL && bad.refs==1);
 bad=(struct net_buf){reset,sizeof(reset),BT_BUF_EVT,1};assert(hci_driver_send(NULL,&bad)==-EINVAL && bad.data==reset && bad.len==3 && bad.refs==1);
 handler_rc=-EIO;bad=(struct net_buf){reset,sizeof(reset),BT_BUF_CMD,1};assert(hci_driver_send(NULL,&bad)==-EIO && bad.refs==1 && bad.data==reset);
 // Real init failure -> profile key -> valid delayed work handler -> save.
 enable_rc=-EIO;assert(zmk_ble_init()==-EIO && !registrations);
 assert(!zmk_ble_prof_select(4) && active_profile==4 && scheduled==&ble_save_work && requested_delay==60000);
 assert(ready_checks==1 && advertised==0);
 scheduled->work.handler(&scheduled->work);assert(saves==1);
 assert(zmk_ble_prof_select(5)==-ERANGE && active_profile==4);
 enable_rc=0;assert(!zmk_ble_init() && registrations==1);
 ready=false;assert(update_advertising()==-EAGAIN);
 ready=true;assert(!update_advertising() && advertised==1);
 puts("PASS: real HCI TX metadata ABI preserves 15 opcode examples and all 256 CMD/ACL prefixes; old driver rejects 0x2005/Reset; real BLE init-failure -> select -> 60000ms save schedule uses valid handler; not-ready guard");
}
'''
code=pre+enum+'\n'+defines+'\n'+stubs+'\n'+function(old,'hci_driver_send').replace('hci_driver_send','old_hci_driver_send',1)+'\n'+function(hci,'hci_driver_send')+'\n'+function(ble,'ble_save_profile_work')+'\n'+function(ble,'ble_save_profile')+'\n'+guard+'\n'+function(ble,'zmk_ble_prof_select')+'\n'+function(ble,'zmk_ble_init')+'\n'+main
with tempfile.TemporaryDirectory() as d:
    c=Path(d)/'test.c';exe=Path(d)/'test';c.write_text(code)
    subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-fno-omit-frame-pointer','-Werror=implicit-function-declaration',str(c),'-o',str(exe)],check=True)
    result=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
report={'passed':True,'generated_hci_sha256':hashlib.sha256(hci_path.read_bytes()).hexdigest(),
        'generated_ble_sha256':hashlib.sha256(ble_path.read_bytes()).hexdigest(),'TX_prefixes_per_type':256,'example_opcodes':15,
        'real_metadata_enum_used':True,'old_Reset_and_Set_Random_Address_negative_control':True,
        'controller_RX_unchanged':True,'real_failed_init_profile_delayed_save_path_executed':True,
        'stdout':result.stdout,'scope':'Controller execution/locks/RF, actual kernel timing, full advertising and RX delivery mocked or checked unchanged; does not prove hardware pairing or freeze root cause'}
(W/'hci-zephyr41-test.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
