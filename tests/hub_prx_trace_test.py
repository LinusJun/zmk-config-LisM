"""Execute the IRQ-to-thread diagnostic queue: no packet or policy mutation."""
from pathlib import Path
import subprocess,tempfile
P=Path(__file__).resolve().parent
base=P/'kb_radio_native_test.py';t=base.read_text();g={'__file__':str(base)}
exec(compile(t[:t.index('code=pre+')],str(base),'exec'),g)
s=g['radio'];function=g['function']
trace=(P.parent/'src/esb_upstream/hub_prx_trace.inc').read_text()
irq=function(trace,'lism_esb_prx_trace')
assert not any(x in irq for x in ['k_uptime_get','LOG_INF(','k_malloc','k_spin_lock','k_mutex_lock'])
# The actual pinned target's cycle read is lock-free, unlike uptime.
rtc=Path('/sdk/zephyr/drivers/timer/nrf_rtc_timer.c').read_text()
for name in ['sys_clock_cycle_get_32','z_nrf_rtc_timer_read','counter']:
 assert not any(x in function(rtc,name) for x in ['irq_lock(', 'k_spin_lock(', 'k_mutex_lock('])
pre=g['pre']+r'''
#define KB_LEFT_COMMIT 1
#define MIN(a,b) ((a)<(b)?(a):(b))
#undef LOG_INF
#define LOG_INF(...) do{logs++;}while(0)
static unsigned grant_link,logs;
static atomic_t diag_private_start;
static uint32_t k_uptime_get_32(void){return now;}
static uint32_t k_cycle_get_32(void){return now*1000;}
static uint32_t k_cyc_to_us_floor32(uint32_t x){return x;}
static uint32_t k_cyc_to_ms_floor32(uint32_t x){return x/1000;}
'''
main=r'''
static void packet(uint8_t*p,uint32_t peer,uint32_t seq,uint32_t authority,uint32_t ack){
 memcpy(p,"LsE3",4);put32(p+4,peer);put32(p+8,seq);put32(p+12,authority);put32(p+16,ack);
}
int main(void){
 uint8_t in[20],out[20],tiny[1]={0};now=100;links[0].boot=99;links[0].last_boot[0]=77;
 left_route_epoch=3;left_route_phase=KB_LEFT_COMMIT;
 links[0].have=true;links[0].tx.peer=77;links[0].tx.generation=3;put32(links[0].tx.p.data+8,10);
 packet(in,77,1,99,9);packet(out,99,10,77,3);prx_trace_poll();
 lism_esb_prx_trace(1,in,20,out,20,1);prx_trace_poll();assert(prx_ack==9&&prx_progress_at==100);
 lism_esb_prx_trace(1,tiny,1,tiny,0,1);prx_trace_poll();
 struct prx_trace_record r=prx_history[(prx_history_head+31)%32];assert(!r.valid&&!r.peer&&!r.txseq);
 now=460;lism_esb_prx_trace(1,in,20,out,20,1);prx_trace_poll();assert(prx_report_level==1&&prx_print_count==3&&prx_print_at==2);
 /* Snapshot is immutable even if IRQs continue while thread prints. */
 struct prx_trace_record snap=prx_snapshot[0];lism_esb_prx_trace(2,NULL,0,out,20,1);
 assert(!memcmp(&snap,&prx_snapshot[0],sizeof(snap)));prx_trace_poll();assert(!prx_print_count);
 unsigned before=logs;prx_trace_poll();assert(logs==before);
 now=1450;lism_esb_prx_trace(1,in,20,out,0,1);prx_trace_poll();assert(prx_report_level==2);
 r=prx_history[(prx_history_head+31)%32];assert(!r.txseq&&r.length==0);
 packet(in,77,2,99,10);now=1460;lism_esb_prx_trace(1,in,20,out,20,1);prx_trace_poll();assert(prx_ack==10&&prx_report_level==0);
 for(unsigned i=0;i<35;i++)lism_esb_prx_trace(2,NULL,0,out,20,1);
 assert(prx_dropped==4);prx_trace_poll();assert(prx_head==prx_tail);
 unsigned head=prx_head;grant_link=1;lism_esb_prx_trace(1,in,20,out,20,1);assert(prx_head==head);grant_link=0;
 lism_esb_prx_trace(3,NULL,0,out,20,0);assert(prx_head==head); // idle end is not a cut
 lism_esb_prx_trace(3,NULL,0,out,20,1);assert(prx_cut==1);prx_trace_poll();
 /* Queue an old-epoch high ACK, then change epoch before draining it. It must
  * neither enter new history nor advance the new epoch's progress timer. */
 packet(in,77,3,99,900);lism_esb_prx_trace(1,in,20,out,20,1);
 prx_print_count=3;prx_print_at=1;left_route_epoch=4;prx_trace_poll();
 assert(prx_ack==0&&prx_report_level==0&&!prx_history_count&&!prx_print_count);
 assert(links[0].tx.peer==77&&links[0].tx.generation==3&&get32(links[0].tx.p.data+8)==10);
 puts("PASS actual PRX trace: bounded SPSC overflow, frozen snapshot, two records/turn, short/empty ACK guards, current authority/peer, generation reset, no route or payload mutations; no RF/timing claim");
}
'''
code=pre+function(s,'get32')+function(s,'put32')+trace+main
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'trace.c';o=Path(d)/'trace';f.write_text(code)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
