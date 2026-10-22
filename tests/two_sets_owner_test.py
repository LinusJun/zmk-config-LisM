"""Compile production owner policy: two competing sets, immutable boot lease."""
from pathlib import Path
import subprocess,tempfile
P=Path(__file__).resolve().parents[1]
code=r'''
#include <assert.h>
#include <stdio.h>
#include "kb_route/owner_policy.h"
static bool prepare(struct kb_owner_policy*o,unsigned set,unsigned boot,int64_t t){return kb_owner_status(o,set,boot,100+set,7,KB_LEFT_FREEZE,0,2,t);}
static bool commit(struct kb_owner_policy*o,unsigned set,unsigned boot,int64_t t){return kb_owner_status(o,set,boot,100+set,7,KB_LEFT_COMMIT,0,0,t);}
int main(void){
 for(unsigned only=0;only<2;only++)for(unsigned set=0;set<2;set++){
  struct kb_owner_policy station[2]={0};int64_t now=10;
  for(unsigned n=0;n<100;n++)assert(kb_owner_probe_allowed(&station[only],set,10+set,now+n));
  assert(!station[only].bound && !kb_owner_expire(&station[only],1000000));
  assert(!commit(&station[only],set,10+set,now));
  assert(prepare(&station[only],set,10+set,now));assert(commit(&station[only],set,10+set,now+1));
  for(unsigned n=0;n<1000;n++){
   now+=1;assert(!kb_owner_probe_allowed(&station[only],set^1,20+set,now));
   assert(!prepare(&station[only],set^1,20+set,now));assert(!commit(&station[only],set^1,20+set,now));
   assert(commit(&station[only],set,10+set,now));
  }
  int64_t seen=station[only].seen;
  assert(kb_owner_probe_allowed(&station[only],set,10+set,now+10));assert(station[only].seen==seen);
  assert(!kb_owner_probe_allowed(&station[only],set,99,now+11));
  assert(!prepare(&station[only],set,99,now+12));
  assert(!kb_owner_status(&station[only],set,10+set,999,8,0,0,2,now+13));
  assert(kb_owner_expire(&station[only],seen+2500));
  for(unsigned s=0;s<2;s++)for(unsigned n=0;n<100;n++)assert(!prepare(&station[only],s,10+s,seen+2501+n));
  assert(station[only].retired); /* no same-boot hot migration */
  station[only]=(struct kb_owner_policy){0}; /* hardware reset: queues gone */
  assert(prepare(&station[only],set^1,20+set,now+3000));
 }
 for(unsigned winner=0;winner<2;winner++){
  struct kb_owner_policy a={0},b={0};
  assert(kb_owner_probe_allowed(&a,0,10,1)&&kb_owner_probe_allowed(&a,1,11,1));
  assert(prepare(&a,winner,10+winner,1));assert(!prepare(&a,winner^1,11-winner,1));
  assert(prepare(&b,winner^1,11-winner,2));assert(commit(&a,winner,10+winner,3));assert(commit(&b,winner^1,11-winner,3));
  assert(!kb_owner_status(&a,winner,10+winner,100+winner,6,0,0,2,4));
  assert(!kb_owner_status(&a,winner,10+winner,100+winner,8,1,0,0,4));
  assert(!kb_owner_status(&a,winner,10+winner,100+winner,7,0,0,2,4));
  assert(kb_owner_status(&a,winner,10+winner,100+winner,8,0,1,2,5));
  assert(kb_owner_status(&a,winner,10+winner,100+winner,8,1,1,1,6));
  assert(!kb_owner_probe_allowed(&a,winner^1,11-winner,7)&&a.retired);
 }
 struct kb_owner_policy preview={0};
 for(unsigned n=0;n<1000;n++){
  assert(kb_owner_probe_allowed(&preview,0,10,10));
  assert(kb_owner_probe_allowed(&preview,1,11,10));
  assert(!kb_owner_status(&preview,0,10,100,7,1,1,1,10));
  assert(!kb_owner_status(&preview,1,11,101,7,1,1,1,10));
  assert(!preview.bound&&!preview.retired);
 }
 assert(prepare(&preview,1,11,11));
 puts("PASS actual owner policy: either set/sole A or B; first claim exclusive; free other station; probes cannot claim/renew; tuple/epoch/phase checks; direct BLE display yields; retirement irreversible until reset");
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'owner.c';out=Path(t)/'owner';f.write_text(code)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(P/'src'),str(f),'-o',str(out)],check=True)
 subprocess.run([str(out)],check=True)
