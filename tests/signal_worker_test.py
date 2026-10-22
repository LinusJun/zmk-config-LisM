"""Run the actual low-priority RSSI worker/cache against a fake HCI controller."""
from pathlib import Path
import sys,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
s=(P/'src/lism_input_host.c').read_text();cache=s[s.index('static struct {struct bt_conn *conn;uint32_t epoch,disabled;'):s.index('static void rssi_worker')]
pre=r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <setjmp.h>
#include <errno.h>
#define ARG_UNUSED(x) (void)(x)
#define K_SECONDS(x) ((x)*1000)
#define LISM_INPUT_PEERS 2
#define BT_HCI_OP_READ_RSSI 0x1405
#define sys_cpu_to_le16(x) (x)
#define sys_le16_to_cpu(x) (x)
typedef int atomic_t;static int atomic_get(atomic_t*p){return *p;}
struct k_spinlock{int n;};typedef int k_spinlock_key_t;
static int k_spin_lock(struct k_spinlock*p){(void)p;return 0;}
static void k_spin_unlock(struct k_spinlock*p,int k){(void)p;(void)k;}
struct bt_conn{unsigned n;};static struct bt_conn a={1},b={2};
static struct k_spinlock identity_lock;static atomic_t disable_generation;
static struct{struct bt_conn*conn;uint32_t epoch;}identities[2];
static struct{atomic_t input_ready;}sessions[2];
static bool enabled,disable_during_query;static int8_t reply_rssi=-63;
static int queries,refs,unrefs;static int64_t now;static int64_t k_uptime_get(void){return now;}
static bool lism_input_host_enabled(void){return enabled;}
static struct bt_conn*bt_conn_ref(struct bt_conn*c){refs++;return c;}
static void bt_conn_unref(struct bt_conn*c){assert(c);unrefs++;}
static int bt_hci_get_conn_handle(struct bt_conn*c,uint16_t*h){assert(c==&a||c==&b);*h=c->n;return 0;}
struct net_buf{unsigned len;uint8_t*data;};static uint8_t txbytes[8],rxbytes[8];static struct net_buf tx={0,txbytes},rx={0,rxbytes};
struct bt_hci_cp_read_rssi{uint16_t handle;}__attribute__((packed));
struct bt_hci_rp_read_rssi{uint8_t status;uint16_t handle;int8_t rssi;}__attribute__((packed));
static struct net_buf*bt_hci_cmd_create(unsigned op,size_t n){assert(enabled&&op==BT_HCI_OP_READ_RSSI&&n==2);tx.len=0;return &tx;}
static void*net_buf_add(struct net_buf*buf,size_t n){buf->len+=n;return buf->data;}
static int bt_hci_cmd_send_sync(unsigned op,struct net_buf*cmd,struct net_buf**out){
 assert(enabled&&op==BT_HCI_OP_READ_RSSI&&cmd==&tx);queries++;
 struct bt_hci_cp_read_rssi*cp=(void*)cmd->data;struct bt_hci_rp_read_rssi rp={0,cp->handle,reply_rssi};
 __builtin_memcpy(rxbytes,&rp,sizeof(rp));rx.len=sizeof(rp);*out=&rx;
 if(disable_during_query){enabled=false;disable_generation++;}return 0;
}
static void net_buf_unref(struct net_buf*p){assert(p==&rx);}
static jmp_buf stop;static unsigned sleeps,budget;
static void k_sleep(int ms){assert(ms==3000);now+=ms;if(sleeps++>=budget)longjmp(stop,1);}
'''
main=r'''
static void run(unsigned n){sleeps=0;budget=n;if(!setjmp(stop))rssi_worker(NULL,NULL,NULL);assert(refs==unrefs);}
int main(void){
 identities[0].conn=&a;identities[0].epoch=7;sessions[0].input_ready=1;
 int8_t dbm;run(20);assert(!queries&&!lism_input_host_signal(&dbm));
 enabled=true;run(2);assert(queries==1&&lism_input_host_signal(&dbm)&&dbm==-63);
 now+=8001;assert(!lism_input_host_signal(&dbm));
 identities[0].epoch++;assert(!lism_input_host_signal(&dbm));
 disable_during_query=true;run(2);assert(!enabled&&!lism_input_host_signal(&dbm));
 int before=queries;run(10);assert(queries==before);
 enabled=true;disable_during_query=false;reply_rssi=127;run(2);assert(!lism_input_host_signal(&dbm));
 reply_rssi=-71;run(2);assert(lism_input_host_signal(&dbm)&&dbm==-71);
 sessions[0].input_ready=0;assert(!lism_input_host_signal(&dbm));
 puts("PASS actual RSSI worker/cache: zero HCI while OFF, one peer per 3s, cache expiry, connection epoch, OFF during query, invalid RSSI, ready-only cache");
}
'''
with tempfile.TemporaryDirectory() as t:
 f=Path(t)/'signal.c';o=Path(t)/'signal';f.write_text(pre+cache+function(s,'rssi_worker')+function(s,'lism_input_host_signal')+main)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(f),'-o',str(o)],check=True);subprocess.run([str(o)],check=True)
