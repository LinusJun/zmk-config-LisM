from pathlib import Path
import subprocess,re,argparse
parser=argparse.ArgumentParser();parser.add_argument('--build',type=Path,required=True);args=parser.parse_args()
p=args.build/'modules/zmk-config-LisM/legacy_central.c'
s=p.read_text();part=s[s.index('struct lism_set_ad_info {'):s.index('static int start_scanning(void) {',s.index('struct lism_set_ad_info {'))]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <limits.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define MIN(a,b) ((a)<(b)?(a):(b))
#define LOG_DBG(...) ((void)0)
#define BT_ADDR_LE_STR_LEN 30
#define BT_DATA_NAME_COMPLETE 9
#define BT_DATA_NAME_SHORTENED 8
#define BT_DATA_UUID128_SOME 6
#define BT_DATA_UUID128_ALL 7
#define BT_DATA_SVC_DATA128 0x21
#define BT_GAP_ADV_TYPE_ADV_IND 0
#define BT_GAP_ADV_TYPE_ADV_DIRECT_IND 1
#define BT_GAP_ADV_TYPE_ADV_NONCONN_IND 3
#define BT_GAP_ADV_TYPE_SCAN_RSP 4
#define LISM_SPLIT_ROLE_VERSION 1
#define OWNER_RX 0
static int lism_owner_diag[1];
static void atomic_inc(int*p){++*p;}
typedef struct {uint8_t val[7];} bt_addr_le_t;
struct uuid {uint8_t val[16];};
static const struct uuid split_service_uuid={{1}},lism_role_uuid={{2}};
static int64_t now=100;
static int64_t k_uptime_get(void){return now;}
static int bt_addr_le_cmp(const bt_addr_le_t*a,const bt_addr_le_t*b){return memcmp(a,b,sizeof(*a));}
static void bt_addr_le_to_str(const bt_addr_le_t*a,char*out,size_t n){(void)a;snprintf(out,n,"test");}
struct net_buf_simple {uint8_t *data;size_t len;};
struct bt_data {uint8_t type;uint8_t data_len;const uint8_t *data;};
static void bt_data_parse(struct net_buf_simple *b,bool(*cb)(struct bt_data*,void*),void*user){
 while(b->len){unsigned n=*b->data++;b->len--;if(!n||n>b->len)break;
 struct bt_data d={b->data[0],n-1,b->data+1};b->data+=n;b->len-=n;if(!cb(&d,user))break;}
}
static bt_addr_le_t roles[2],bound[2];static bool have_role[2],have_bound[2];
static unsigned attempts,created[2];
static void lism_split_sets_observe(const bt_addr_le_t*a,const char*n,int8_t r){(void)a;(void)n;(void)r;}
static void lism_legacy_role_observe(const bt_addr_le_t*a,int r){if(r>=0&&r<2){roles[r]=*a;have_role[r]=true;}}
static bool lism_split_known_addr(const bt_addr_le_t*a){for(unsigned i=0;i<2;i++)if(have_bound[i]&&!bt_addr_le_cmp(a,&bound[i]))return true;return false;}
/* Connection owner/allocator are mocked here; actual allocator/cleanup have
 * their own ASan/UBSan test. This test executes the real generated AD parser,
 * packet evidence join and device_found callback, not a copied flow. */
static bool split_central_eir_found(const bt_addr_le_t*a){
 attempts++;for(unsigned i=0;i<2;i++)if(have_role[i]&&!bt_addr_le_cmp(a,&roles[i])){
 if(!have_bound[i]){bound[i]=*a;have_bound[i]=true;created[i]++;}return false;}return false;
}
static bool split_central_eir_parse(struct bt_data*d,void*user){
 if((d->type==BT_DATA_UUID128_SOME||d->type==BT_DATA_UUID128_ALL)&&d->data_len==16&&!memcmp(d->data,split_service_uuid.val,16))return split_central_eir_found(user);return true;
}
'''
test=r'''
static void feed(bt_addr_le_t*a,uint8_t type,bool split,int role){uint8_t wire[64];unsigned n=0;
 if(split){wire[n++]=17;wire[n++]=BT_DATA_UUID128_ALL;memcpy(wire+n,split_service_uuid.val,16);n+=16;}
 if(role!=-1){wire[n++]=19;wire[n++]=BT_DATA_SVC_DATA128;memcpy(wire+n,lism_role_uuid.val,16);n+=16;wire[n++]=1;wire[n++]=role;}
 struct net_buf_simple b={wire,n};split_central_device_found(a,-40,type,&b);
}
int main(void){bt_addr_le_t right={{10}},left={{20}},unknown={{30}},other={{40}};
 /* A new right sees exactly one ADV then one response: no duplicate ADV. */
 feed(&right,BT_GAP_ADV_TYPE_ADV_IND,true,-1);assert(!created[1]);
 feed(&right,BT_GAP_ADV_TYPE_SCAN_RSP,false,1);assert(created[1]==1&&!created[0]);
 feed(&left,BT_GAP_ADV_TYPE_ADV_IND,true,-1);feed(&left,BT_GAP_ADV_TYPE_SCAN_RSP,false,0);assert(created[0]==1);
 unsigned n=attempts;feed(&left,BT_GAP_ADV_TYPE_SCAN_RSP,false,0);assert(attempts==n); /* consumed evidence */
 feed(&unknown,BT_GAP_ADV_TYPE_SCAN_RSP,false,0);assert(attempts==n); /* role-only, not a split AD */
 feed(&unknown,BT_GAP_ADV_TYPE_ADV_NONCONN_IND,true,-1);feed(&unknown,BT_GAP_ADV_TYPE_SCAN_RSP,false,0);assert(attempts==n);
 feed(&unknown,BT_GAP_ADV_TYPE_ADV_IND,true,-1);n=attempts;feed(&other,BT_GAP_ADV_TYPE_SCAN_RSP,false,0);assert(attempts==n);
 now+=5001;feed(&unknown,BT_GAP_ADV_TYPE_SCAN_RSP,false,0);assert(attempts==n);
 feed(&unknown,BT_GAP_ADV_TYPE_ADV_IND,true,-1);n=attempts;feed(&unknown,BT_GAP_ADV_TYPE_SCAN_RSP,false,2);assert(attempts==n); /* invalid role */
 memset(have_bound,0,sizeof(have_bound));memset(have_role,0,sizeof(have_role));memset(created,0,sizeof(created));memset(legacy_split_ads,0,sizeof(legacy_split_ads));
 feed(&left,BT_GAP_ADV_TYPE_ADV_IND,true,-1);feed(&left,BT_GAP_ADV_TYPE_SCAN_RSP,false,0);
 feed(&right,BT_GAP_ADV_TYPE_ADV_IND,true,-1);feed(&right,BT_GAP_ADV_TYPE_SCAN_RSP,false,1);
 assert(created[0]==1&&created[1]==1); /* either power-on order */
 puts("PASS actual generated scanner: one ADV -> one SCAN_RSP without duplicate ADV, both role orders; duplicate/role-only/nonconnectable/wrong-address/expired/invalid-role rejection");
}
'''
path=args.build/'scan_actual.c';path.write_text(pre+part+test);exe=str(args.build/'scan_actual');subprocess.run(['cc','-std=c11','-fsanitize=address,undefined','-g',str(path),'-o',exe],check=True);subprocess.run([exe],check=True)
# Regression must fail against the exact previously shipped callback.
old=s[s.index('struct lism_set_ad_info {'):s.index('static int start_scanning(void) {',s.index('struct lism_set_ad_info {'))]
# Restore only the missing callback edge: cache still learns the role, but a
# response cannot initiate connection. The main assertion must then fail.
old=old.replace('if (lism_set_observe_ad(addr, rssi, type, ad)) { split_central_eir_found(addr); return; }','lism_set_observe_ad(addr, rssi, type, ad);')
path.write_text(pre+old+test);exe=str(args.build/'scan_previous_failure');subprocess.run(['cc','-std=c11',str(path),'-o',exe],check=True)
r=subprocess.run([exe],capture_output=True,text=True);assert r.returncode!=0
print('PASS regression negative control: previous missing SCAN_RSP edge fails first-connect assertion')
