"""Execute the production left routing policy, including resets and stale phases."""
from pathlib import Path
import subprocess,tempfile
P=Path(__file__).resolve().parents[1]
code=r'''
#include <assert.h>
#include <stdio.h>
#include "kb_route/left_protocol.h"
int main(void){
 struct kb_left_policy p={0};
 assert(p.phase!=KB_LEFT_APPLIED);
 assert(!kb_left_policy_command(&p,11,1,KB_LEFT_COMMIT,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(kb_left_policy_command(&p,11,1,KB_LEFT_FREEZE,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),false));
 assert(!p.prepared && p.phase==KB_LEFT_FREEZE);
 assert(!kb_left_policy_command(&p,11,1,KB_LEFT_COMMIT,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),false));
 kb_left_observe(&p,true);assert(p.prepared);
 assert(kb_left_policy_command(&p,11,1,KB_LEFT_COMMIT,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),false));
 assert(p.phase==KB_LEFT_COMMIT);kb_left_observe(&p,false);assert(p.phase==KB_LEFT_COMMIT);
 kb_left_observe(&p,true);assert(p.phase==KB_LEFT_APPLIED);
 /* Duplicate prepare/commit is idempotent; a stale phase cannot close the route. */
 assert(kb_left_policy_command(&p,11,1,KB_LEFT_FREEZE,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));assert(p.phase==KB_LEFT_APPLIED);
 assert(kb_left_policy_command(&p,11,1,KB_LEFT_COMMIT,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(!kb_left_policy_command(&p,11,1,KB_LEFT_COMMIT,1,(1==0?0:KB_STATION_NONE),(1==0?101:0),true));
 assert(kb_left_policy_command(&p,11,2,KB_LEFT_FREEZE,1,(1==0?0:KB_STATION_NONE),(1==0?101:0),true));assert(p.phase==KB_LEFT_FREEZE);
 assert(!kb_left_policy_command(&p,11,1,KB_LEFT_FREEZE,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(kb_left_policy_command(&p,11,2,KB_LEFT_COMMIT,1,(1==0?0:KB_STATION_NONE),(1==0?101:0),true));assert(p.phase==KB_LEFT_APPLIED);
 /* A rebooted right requires a new prepare; prior right cannot reclaim control. */
 assert(!kb_left_policy_command(&p,22,1,KB_LEFT_COMMIT,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(kb_left_policy_command(&p,22,1,KB_LEFT_FREEZE,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(!kb_left_policy_command(&p,11,3,KB_LEFT_FREEZE,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(kb_left_policy_command(&p,22,1,KB_LEFT_COMMIT,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 for(unsigned n=2;n<1000;n++){
  unsigned target=n&1;assert(kb_left_policy_command(&p,22,n,0,target,(target==0?0:KB_STATION_NONE),(target==0?101:0),false));
  for(unsigned missed=0;missed<7;missed++)assert(kb_left_policy_command(&p,22,n,0,target,(target==0?0:KB_STATION_NONE),(target==0?101:0),false));
  kb_left_observe(&p,true);
  assert(kb_left_policy_command(&p,22,n,1,target,(target==0?0:KB_STATION_NONE),(target==0?101:0),false));assert(p.phase!=KB_LEFT_APPLIED);
  kb_left_observe(&p,true);assert(p.phase==KB_LEFT_APPLIED);
 }
 /* Old timeout/overflow cannot promote a newly prepared route. */
 assert(kb_left_policy_command(&p,22,1001,0,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(!kb_left_fault(&p,false,true) && p.phase==KB_LEFT_FREEZE);
 assert(!kb_left_fault(&p,true,true) && p.phase==KB_LEFT_FREEZE);
 assert(kb_left_policy_command(&p,22,1001,1,0,(0==0?0:KB_STATION_NONE),(0==0?101:0),true));
 assert(kb_left_fault(&p,true,false) && p.phase==KB_LEFT_COMMIT);
 kb_left_observe(&p,true);assert(p.phase==KB_LEFT_APPLIED);
 /* Lease expiry is a latch, physical all-up cannot restore old permission. */
 p.phase=KB_LEFT_FREEZE;p.prepared=false;p.needs_freeze=true;
 kb_left_observe(&p,true);assert(!p.prepared);
 assert(!kb_left_policy_command(&p,22,1001,KB_LEFT_COMMIT,0,0,101,true));
 assert(kb_left_policy_command(&p,22,1001,KB_LEFT_FREEZE,0,0,101,true));
 assert(kb_left_policy_command(&p,22,1001,KB_LEFT_COMMIT,0,0,101,true));
 assert(!kb_left_policy_command(&p,22,1001,KB_LEFT_COMMIT,0,1,102,true));
 assert(kb_left_policy_command(&p,22,1002,KB_LEFT_FREEZE,0,1,102,true));
 assert(!kb_left_policy_command(&p,22,1001,KB_LEFT_COMMIT,0,0,101,true));
 assert(kb_left_policy_command(&p,22,1002,KB_LEFT_COMMIT,0,1,102,true));
 struct kb_left_packet k={.authority=22,.epoch=999,.battery=100,.station=0,.receiver_boot=101};
 for(unsigned i=0;i<46;i++){kb_left_pack_keys(&k,UINT64_C(1)<<i);assert(kb_left_keys(&k)==(UINT64_C(1)<<i));assert(kb_left_packet_valid(&k));}
 k.keys[5]|=0x40;assert(!kb_left_packet_valid(&k));k.keys[5]=0;k.battery=101;assert(!kb_left_packet_valid(&k));
 assert(sizeof(struct kb_left_packet)==21 && sizeof(struct kb_left_control)==24);
 puts("PASS production left policy: cold boot, real-release quarantine, prepare/commit ACK loss, idempotency, stale epochs, right reboot, 46-key wire bounds");
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'left.c';o=Path(t)/'left';f.write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(P/'src'),str(f),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
