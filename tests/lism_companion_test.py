"""Run the production encrypted companion enrollment service, including A/B isolation."""
from pathlib import Path
import os
import subprocess
import tempfile
from lism_standalone_test import COMMON, ROOT
EXTRA=r'''
#define BT_SECURITY_L2 2
#define BT_ADDR_LE_RANDOM 1
static const bt_addr_le_t any_addr={0};
#define BT_ADDR_LE_ANY (&any_addr)
#define BT_GATT_ERR(n) (-(n))
#define BT_ATT_ERR_AUTHORIZATION 8
#define BT_ATT_ERR_UNLIKELY 14
#define BT_ATT_ERR_VALUE_NOT_ALLOWED 19
#define BT_ATT_ERR_INVALID_OFFSET 7
#define BT_GATT_SERVICE_DEFINE(...)
#define BT_UUID_INIT_128(...) {0}
struct bt_uuid {int unused;};
struct bt_uuid_128 {struct bt_uuid uuid;};
struct bt_gatt_attr {void *user_data;};
static bt_addr_le_t addresses[6],remote;
static unsigned slot,security=2;static bool dispatch=true,save_failure;
static const bt_addr_le_t *bt_conn_get_dst(struct bt_conn *conn){(void)conn;return &remote;}
static int bt_conn_get_security(struct bt_conn *conn){(void)conn;return security;}
static int bt_addr_le_cmp(const bt_addr_le_t *a,const bt_addr_le_t *b){return memcmp(a,b,sizeof(*a));}
static int bt_gatt_attr_read(struct bt_conn *c,const struct bt_gatt_attr *a,void *out,uint16_t len,uint16_t off,const void *data,size_t size){
 (void)c;(void)a;if(off>size)return -7;size_t n=size-off;if(n>len)n=len;memcpy(out,(const uint8_t *)data+off,n);return n;}
static int server_save(const char *name,const void *data,size_t len){(void)name;(void)data;(void)len;saves++;return save_failure?-EIO:0;}
#define settings_save_one server_save
'''
TEST=r'''
#include "peer_server.c"
int lism_split_sets_find_addr(const bt_addr_le_t *a){
 for(unsigned i=0;i<6;i++)if(!bt_addr_le_cmp(a,&addresses[i]))return i;return -1;}
uint32_t lism_split_sets_generation(uint8_t s){(void)s;return 1;}
bool lism_split_sets_dispatch(uint8_t s,uint32_t gen){(void)s;return dispatch && gen==1;}
int lism_split_sets_read_addr(uint8_t s,bt_addr_le_t *out){if(s>=6)return -EINVAL;*out=addresses[s];return 0;}
static void as(unsigned s){slot=s;remote=addresses[s];}
static int write(unsigned s,uint8_t *wire){as(s);return write_peer(&connection,NULL,wire,8,0,0);}
static int read(unsigned s,uint8_t *wire){as(s);return read_peer(&connection,NULL,wire,8,0);}
int main(void){
 for(unsigned i=0;i<6;i++)addresses[i]=(bt_addr_le_t){.type=1,.a={1,2,3,4,5,i+10}};
 bt_addr_le_t direct_a={.type=1,.a={8,1,2,3,4,5}},direct_b={.type=1,.a={9,1,2,3,4,5}};
 uint8_t a[8]={1},b[8]={1},out[8];memcpy(a+1,&direct_a,7);memcpy(b+1,&direct_b,7);
 security=1;assert(write(0,a)==-8 && read(1,out)==-8);security=2;
 dispatch=false;assert(write(0,a)==-8);dispatch=true;
 assert(write(1,a)==-8 && write(2,a)==-8 && read(2,out)==-8);
 as(0);assert(write_peer(&connection,NULL,a,7,0,0)==-19);
 assert(write_peer(&connection,NULL,a,8,1,0)==-19);
 a[0]=2;assert(write(0,a)==-19);a[0]=1;
 assert(write(0,a)==8 && saves==1);assert(write(0,a)==8 && saves==1);
 assert(read(1,out)==8 && !memcmp(out,a,8));
 assert(read(0,out)==8 && !memcmp(out+1,&addresses[1],7));
 assert(read(5,out)==-14);assert(write(4,b)==8 && saves==2);
 assert(read(5,out)==8 && !memcmp(out,b,8));assert(read(1,out)==8 && !memcmp(out,a,8));
 /* Replacing a registered left never inherits its old ID1 companion. */
 addresses[0].a[0]++;
 assert(read(1,out)==-14);assert(read(5,out)==8 && !memcmp(out,b,8));
 save_failure=true;assert(write(0,a)==-14 && read(1,out)==-14);
 save_failure=false;assert(write(0,a)==8 && read(1,out)==8);
 memset(a+1,0,7);assert(write(0,a)==-19);
 a[1]=255;assert(write(0,a)==-19);
 puts("PASS actual companion service: encrypted/registered admission, role-specific writes, A/B isolation, owner replacement invalidation, bounds/version checks, transactional persistence");
}
'''

def main():
    with tempfile.TemporaryDirectory() as d:
        d=Path(d);(d/'common.h').write_text(COMMON+EXTRA)
        for name in ['zephyr/bluetooth/conn.h','zephyr/bluetooth/addr.h','zephyr/bluetooth/gatt.h',
            'zephyr/bluetooth/uuid.h','zephyr/settings/settings.h','zephyr/kernel.h',
            'zmk/event_manager.h','zmk/events/position_state_changed.h',
            'zmk/split/transport/peripheral.h','zephyr/input/input.h']:
            p=d/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('#include "common.h"\n')
        (d/'test.c').write_text(TEST)
        subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
            '-Wno-unused-variable','-Wno-misleading-indentation','-Wno-sign-compare',
            '-fsanitize=address,undefined','-fno-omit-frame-pointer','-g','-I'+str(d),
            '-I'+str(ROOT/'src/standalone'),str(d/'test.c'),'-o',str(d/'test')],check=True)
        subprocess.run([str(d/'test')],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
if __name__=='__main__':main()
