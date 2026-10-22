"""Actual left loop/receive: hardware ACK without fresh authorization."""
from pathlib import Path
import tempfile,subprocess
P=Path(__file__).resolve().parent
base=P/'left_radio_native_test.py';t=base.read_text();g={'__file__':str(base)}
exec(compile(t[:t.index('with tempfile.TemporaryDirectory()')],str(base),'exec'),g)
main=g['main'].replace('int main(int argc,char**argv)','int old_main(int argc,char**argv)')
prototype=r'''
static struct esb_payload command(uint32_t,uint32_t,uint32_t,unsigned,unsigned);
static unsigned gap_fast,last_probe,grant_seq=10,renew_data;static bool frozen_seen;
'''
main=prototype+main
main=main.replace('now+=ms;if(steps++>=budget)',r'''
 now+=ms;
 if(now%200==0){struct lism_snapshot snap={.battery=80};struct zmk_split_transport_peripheral_event e={.type=LISM_SNAPSHOT_TYPE};memcpy(&e.data,&snap,sizeof(snap));kb_left_track(&e);}
 if(now>3200 && now<4200 && policy.phase==KB_LEFT_FREEZE)frozen_seen=true;
 if(steps++>=budget)''')
start=main.index('if((int)env.payload.event.type==KB_LEFT_CONTROL){')
end=main.index('\n assert((int)env.payload.event.type==KB_LEFT_DATA);',start)
main=main[:start]+r'''
 if((int)env.payload.event.type==KB_LEFT_CONTROL){
  /* Hardware ACK always succeeds. Lose new command payloads for several
   * authorization lease periods, with repeated old ACK payloads mixed in. */
  if(now>1900 && now<4000 && last_probe){unsigned gap=now-last_probe;if(gap>gap_fast)gap_fast=gap;}
  last_probe=now;
  if(now<1500 || now>=4200){
   fifo[0]=command(99,++grant_seq,1,policy.needs_freeze||!policy.prepared?KB_LEFT_FREEZE:KB_LEFT_COMMIT,0);
   fn=1;fh=0;event(&(struct esb_evt){ESB_EVENT_RX_RECEIVED});
  }else if(steps%3==0){
   fifo[0]=command(99,grant_seq,1,KB_LEFT_COMMIT,0);fn=1;fh=0;event(&(struct esb_evt){ESB_EVENT_RX_RECEIVED});
  }
  event(&(struct esb_evt){ESB_EVENT_TX_SUCCESS});return;
 }
 assert(policy.phase==KB_LEFT_APPLIED);
 assert(current_net==kb_left_network(policy.target,policy.station));
 renew_data++;event(&(struct esb_evt){ESB_EVENT_TX_SUCCESS});return;
''' + main[end:]
main+=r'''
int main(void){
 callback=received_callback;enabled=1;policy=(struct kb_left_policy){.authority=99,.epoch=1,.phase=KB_LEFT_APPLIED,.target=0,.station=0,.receiver_boot=777,.prepared=true};route_generation=1;
 budget=6500;now=1;coordinator_seen=1;
 if(!setjmp(stop))radio_thread(NULL,NULL,NULL);
 printf("renewal gap_fast=%u expiry=%u fresh=%u stale=%u data=%u phase=%u\n",gap_fast,lease_expired,route_received,route_duplicate,renew_data,policy.phase);
 assert(gap_fast<=12);assert(frozen_seen && lease_expired>0 && route_duplicate>0 && route_received>0 && renew_data>0);
 assert(policy.phase==KB_LEFT_APPLIED && policy.prepared && !policy.needs_freeze && now-coordinator_seen<200);
 puts("PASS fresh ACK payload renews; empty/duplicate ACKs do not; lease freezes and PREPARE/COMMIT recovers; data yields continue");
}
'''
code=g['code']+main
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'renew.c';o=Path(d)/'renew'
 for old in (False,True):
  candidate=code
  if old:
   candidate=candidate.replace('coordinator_seen && now-coordinator_seen<200','control_seen && now-control_seen<200')
   assert candidate!=code
  f.write_text(candidate)
  subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True)
  r=subprocess.run([str(o)],capture_output=True,text=True)
  if old:assert r.returncode!=0 and 'gap_fast<=12' in r.stderr,r.stdout+r.stderr;print('PASS negative control: hardware-ACK decision reproduces slow probing under lost route payloads')
  else:assert r.returncode==0,r.stdout+r.stderr;print(r.stdout)
