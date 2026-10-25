"""Execute real radio_thread retry boundary priorities with fake ESB/clock.

Reuse only fixture construction from existing tests, without executing either
legacy test main. The actual thread is extracted fresh from left_radio.inc.
Native scheduling/ESB results are injected; this does not model RF or IRQ timing.
"""
from pathlib import Path
import subprocess
import tempfile

P = Path(__file__).resolve().parents[1]
# Load ABI/mocks only; deliberately do not run the base tests or their mains.
base_path = P / 'tests/kb_route_native_test.py'
base = {'__file__': str(base_path)}
exec(compile(base_path.read_text().split("route_pre=r'''", 1)[0], str(base_path), 'exec'), base)
fixture_path = P / 'tests/left_radio_native_test.py'
fixture = {'__file__': str(fixture_path), 'base': base}
prefix = fixture_path.read_text().split("main=r'''", 1)[0]
prefix = prefix.replace("b=runpy.run_path(str(P/'tests/kb_route_native_test.py'))", 'b=base')
assert 'runpy.run_path(' not in prefix
exec(compile(prefix, str(fixture_path), 'exec'), fixture)
code = fixture['code']
old = '''static unsigned sdk_writes,sdk_starts;
static struct esb_payload sent;static bool active_tx;static int esb_write_payload(const struct esb_payload*p){sdk_writes++;sent=*p;return 0;}
static int esb_start_tx(void){assert(rf_channel==rf_expected);sdk_starts++;active_tx=true;return 0;}static void esb_flush_tx(void){active_tx=false;}'''
new = r'''
static unsigned sdk_writes,sdk_starts,flushes,disables,configures;
static struct esb_payload sent;static bool active_tx,queued;
static unsigned scenario,control_starts,data_starts,expired_starts,retry_injected;
static int64_t first_data_at=-1;
static uint32_t first_data_seq;static unsigned writes_at_start;static bool late_active;static unsigned late_disables,late_configures;
static unsigned sent_type(void);static bool sent_keys(void);
static int esb_write_payload(const struct esb_payload*p){assert(!active_tx);sdk_writes++;sent=*p;queued=true;return 0;}
static int esb_start_tx(void){
 assert(rf_channel==rf_expected);sdk_starts++;assert(queued);bool retained=writes_at_start==sdk_writes;writes_at_start=sdk_writes;
 if((scenario==7||scenario==8)&&!late_active&&sent_type()==KB_LEFT_DATA&&retained&&first_data_at>=0&&now-first_data_at>=490&&now-first_data_at<499){
  late_active=true;late_disables=disables;late_configures=configures;active_tx=true;
  if(scenario==8){retry_injected++;return -EBUSY;}
 }
 if((scenario==3||scenario==4)&&sdk_starts==2){
  retry_injected++;if(scenario==4){active_tx=true;return -EBUSY;}
  queued=false;return -ENODATA;
 }
 active_tx=true;
 if(sent_type()==KB_LEFT_CONTROL)control_starts++;
 else {assert(sent_type()==KB_LEFT_DATA);data_starts++;
  uint32_t seq=sent.data[8]|((uint32_t)sent.data[9]<<8)|((uint32_t)sent.data[10]<<16)|((uint32_t)sent.data[11]<<24);
  if(first_data_at<0){first_data_at=now;first_data_seq=seq;}
  if(now-first_data_at>=500&&seq==first_data_seq&&sent_keys())expired_starts++;
 }
 return 0;
}
static void esb_flush_tx(void){assert(!active_tx);flushes++;queued=false;}
'''
assert old in code
code = code.replace(old, new)
# These definitions precede SDK TX mocks, so use forward declarations and keep
# bookkeeping definitions at that earlier location.
code = code.replace('static int esb_init(struct esb_config*c){assert(!c->retransmit_count);return 0;}',
                    'static unsigned flushes,disables,configures;static bool active_tx,queued;\nstatic int esb_init(struct esb_config*c){assert(!c->retransmit_count);configures++;queued=false;return 0;}')
code = code.replace('static void esb_disable(void){rf_channel=UINT32_MAX;}',
                    'static void esb_disable(void){rf_channel=UINT32_MAX;disables++;active_tx=queued=false;}')
main = r'''
static unsigned sent_type(void){struct esb_event_envelope e={0};memcpy(&e,sent.data+20,sent.length-24);return e.payload.event.type;}
static bool sent_keys(void){struct esb_event_envelope e={0};memcpy(&e,sent.data+20,sent.length-24);struct kb_left_packet p;memcpy(&p,&e.payload.event.data,sizeof(p));return kb_left_keys(&p)!=0;}
static void enqueue_press(void){
 struct zmk_split_transport_peripheral_event e={.type=ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT,.data.key_position_event={.position=4,.pressed=1}};
 kb_left_track(&e);
}
static void fail_tx(void){active_tx=false;event(&(struct esb_evt){ESB_EVENT_TX_FAILED});}
static void ack_tx(void){active_tx=queued=false;event(&(struct esb_evt){ESB_EVENT_TX_SUCCESS});}
static void k_sleep(int ms){
 now+=ms;steps++;
 if(scenario==1||scenario==7||scenario==8){
  if(late_active){
   if(now-first_data_at<499){now=first_data_at+499;return;}
   if(now-first_data_at==500)return;
   assert(now-first_data_at>500&&disables>=late_disables+1&&configures-late_configures==disables-late_disables&&warnings==1&&!expired_starts);
   if(scenario==8)assert(retry_injected==1&&retry_start_fail==1);
   longjmp(stop,1);
  }
  if(now>=505){assert(scenario==1&&first_data_at>=0&&data_starts>20&&warnings==1&&!expired_starts);assert(policy.phase!=KB_LEFT_APPLIED);longjmp(stop,1);}
  if(active_tx){if(sent_type()==KB_LEFT_CONTROL)ack_tx();else fail_tx();}
 }else if(scenario==2){
  if(steps==1){assert(sdk_starts==1);enabled=0;fail_tx();}
  if(steps>=20){assert(sdk_starts==1&&!active_tx&&!queued&&flushes==1);longjmp(stop,1);}
 }else if(scenario==3){
  if(steps==1)fail_tx();
  if(steps==2){assert(retry_injected==1&&retry_start_fail==1&&sdk_starts==2&&sdk_writes==1&&flushes==1&&!queued&&!active_tx);longjmp(stop,1);}
 }else if(scenario==4){
  if(steps==1)fail_tx();
  if(steps==2){assert(retry_injected==1&&active_tx&&flushes==0&&sdk_writes==1);}
  if(steps==4){assert(!active_tx&&!queued&&disables==1&&configures==2&&flushes==0&&sdk_starts==2);longjmp(stop,1);}
 }else if(scenario==5){
  if(steps<=2)fail_tx();
  if(steps==3){assert(control_starts==2&&sdk_writes==1&&retained_retry==1&&flushes==1&&!active_tx&&!queued);longjmp(stop,1);}
 }else if(scenario==6){
  if(steps==1){enqueue_press();fail_tx();}
  if(steps==2){assert(control_starts==1&&data_starts==1&&flushes==1&&retained_retry==0);longjmp(stop,1);}
 }else if(scenario==9){
  if(steps==1){enabled=0;fail_tx();}
  if(steps==2){assert(sdk_starts==1);enabled=1;}
  if(steps==3){assert(sdk_starts==2&&sdk_writes==1&&retained_retry==1);ack_tx();}
  if(sdk_starts==3){assert(sdk_writes==2&&control_acks==1&&retained_retry==1&&retry_start_fail==0);longjmp(stop,1);}
 }
 assert(steps<600);
}
int main(int argc,char **argv){
 assert(argc==2);scenario=atoi(argv[1]);assert(scenario>=1&&scenario<=9);
 callback=received_callback;enabled=1;now=1;
 policy=(struct kb_left_policy){.authority=99,.epoch=1,.phase=KB_LEFT_APPLIED,.target=0,.station=0,.receiver_boot=777,.prepared=true};route_generation=1;coordinator_seen=1;
 if(scenario==1||scenario==7||scenario==8)enqueue_press();
 if(!setjmp(stop))radio_thread(NULL,NULL,NULL);
 printf("CHANNEL actual configured data37=%u private76=%u; every retained/busy/expiry start matches base0 role\n",rf_sets[0],rf_sets[1]);
 printf("PASS actual radio_thread boundary case %u: starts=%u writes=%u flush=%u disable=%u configure=%u retained=%u start_fail=%u\n",scenario,sdk_starts,sdk_writes,flushes,disables,configures,retained_retry,retry_start_fail);
}
'''
with tempfile.TemporaryDirectory() as directory:
    c = Path(directory) / 'boundaries.c'
    binary = Path(directory) / 'boundaries'
    c.write_text(code + main)
    subprocess.run(['cc', '-std=gnu11', '-Werror=implicit-function-declaration',
                    '-fsanitize=address,undefined', '-I'+str(P/'src'), str(c), '-o', str(binary)], check=True)
    for scenario in range(1, 10):
        subprocess.run([str(binary), str(scenario)], check=True)
print('PASS boundaries: expired original press is not restarted; disabled blocks retry; ENODATA cancels idle FIFO; EBUSY remains tracked until disable/configure deadline; two-start budget; queued data yields control; active retained/busy retry crossing data expiry disables safely; disabled resume and success reset retry identity. Native injected events/clock, no RF proof.')
