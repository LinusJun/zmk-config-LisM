"""Run actual receive/IRQ code: rejected ACKs retain their old semantics."""
from pathlib import Path
import tempfile,subprocess
P=Path(__file__).resolve().parent
base=P/'left_radio_native_test.py';t=base.read_text();g={'__file__':str(base)}
exec(compile(t[:t.index('with tempfile.TemporaryDirectory()')],str(base),'exec'),g)
main=g['main'].replace('int main(int argc,char**argv)','int old_main(int argc,char**argv)')
main+=r'''
static void push(struct esb_payload p){
 fifo[0]=p;fn=1;fh=0;current_net=KB_NET_PRIVATE;
 event(&(struct esb_evt){ESB_EVENT_RX_RECEIVED});receive();
}
static void reseal(struct esb_payload*p){uint32_t c=crc32_ieee(p->data+20,p->length-24);memcpy(p->data+p->length-4,&c,4);}
static void deny(app_esb_event_t*e){e->accepted=false;}
int main(void){
 boot_id=123;now=100;callback=received_callback;
 struct esb_payload good=command(99,1,1,KB_LEFT_FREEZE,0),p;
 push(good);assert(command_seq[1]==1&&route_received==1&&coordinator_seen==100);
 p=good;put32(p.data+12,987);push(p);assert(rx_diag[1].target==1&&rx_diag[1].last_target==987);
 p=good;p.data[0]^=1;push(p);assert(rx_diag[1].header==1);
 p=good;p.data[30]^=1;push(p);assert(rx_diag[1].crc==1);
 p=good;p.data[20]='X';reseal(&p);push(p);assert(rx_diag[1].body==1);
 previous_boot[1]=88;push(command(88,2,1,KB_LEFT_FREEZE,0));assert(rx_diag[1].old_boot==1);
 now=200;push(good);assert(rx_diag[1].sequence==1&&route_duplicate==1);
 p=command(99,2,1,KB_LEFT_FREEZE,0);
 struct esb_command_envelope env={0};memcpy(&env,p.data+20,p.length-24);
 env.payload.cmd.data.invoke_behavior.state=0;memcpy(p.data+20,&env,p.length-24);reseal(&p);push(p);
 assert(rx_diag[1].policy==1&&command_seq[1]==1&&coordinator_seen==100);
 policy=(struct kb_left_policy){.authority=99,.epoch=1,.phase=KB_LEFT_APPLIED,.target=1,.station=KB_STATION_NONE,.prepared=true};
 p=command(99,3,1,KB_LEFT_COMMIT,1);memcpy(&env,p.data+20,p.length-24);
 strcpy(env.payload.cmd.data.invoke_behavior.behavior_dev,"test");memcpy(p.data+20,&env,p.length-24);reseal(&p);
 callback=deny;push(p);assert(rx_diag[1].dispatch==1&&command_seq[1]==1&&coordinator_seen==100);
 callback=received_callback;push(p);assert(command_seq[1]==3&&rx_diag[1].accepted==2&&coordinator_seen==100);
 /* Seven slots are usable. The eighth callback is counted as ring loss,
  * not misclassified as a bad target or policy rejection. */
 for(unsigned i=0;i<8;i++)fifo[i]=good;
 fn=8;fh=0;current_net=1;event(&(struct esb_evt){ESB_EVENT_RX_RECEIVED});
 assert(rx_ring_dropped==1);receive();
 struct left_rx_diag d=rx_diag[1];
 assert(d.frames==d.header+d.target+d.crc+d.body+d.old_boot+d.sequence+d.policy+d.dispatch+d.accepted);
 assert(d.frames==17&&d.accepted==2&&d.sequence==8&&command_seq[1]==3&&coordinator_seen==100);
 puts("PASS actual left ACK filters: exclusive outcomes, target/CRC/body/boot/sequence/policy distinction, unchanged authorization and dedup state, bounded ring-overflow counter");
}
'''
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'diag.c';o=Path(d)/'diag';f.write_text(g['code']+main)
 subprocess.run(['cc','-std=gnu11','-Werror=implicit-function-declaration','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True)
 subprocess.run([str(o)],check=True)
