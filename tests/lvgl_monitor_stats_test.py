"""Real pinned Zephyr LVGL stats functions with modeled heap counters."""
from pathlib import Path
import os,sys,tempfile,subprocess
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from prepare_hog_routing import function
Z=Path(os.environ.get('LISM_ZEPHYR_BASE','/sdk/zephyr'))/'modules/lvgl'
stats=function((Z/'lvgl_mem.c').read_text(),'lvgl_heap_stats')
monitor=function((Z/'lvgl.c').read_text(),'lv_mem_monitor_core')
assert "if display_central:options+=['-DCONFIG_SYS_HEAP_RUNTIME_STATS=y'" in (P/'tools/build_upstream_esb.py').read_text()
pre=r'''
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
#define CONFIG_LV_Z_MEM_POOL_SYS_HEAP 1
#define ARG_UNUSED(x) ((void)(x))
#define LOG_WRN_ONCE(...) ((void)0)
struct sys_memory_stats{size_t allocated_bytes,free_bytes,max_allocated_bytes;};
typedef struct{unsigned used_pct;size_t max_used;}lv_mem_monitor_t;
typedef int k_spinlock_key_t;static int lvgl_heap_lock,lvgl_heap;
static int k_spin_lock(int*p){(void)p;return 1;}
static void k_spin_unlock(int*p,int k){(void)p;assert(k==1);}
static void sys_heap_runtime_stats_get(int*p,struct sys_memory_stats*s){(void)p;*s=(struct sys_memory_stats){18000,12000,20000};}
'''
main=r'''
int main(void){
 struct sys_memory_stats value;memset(&value,0xa5,sizeof(value));lvgl_heap_stats(&value);
#if CONFIG_SYS_HEAP_RUNTIME_STATS
 lv_mem_monitor_t m;lv_mem_monitor_core(&m);assert(m.used_pct==60 && m.max_used==20000);
 assert(value.allocated_bytes==18000 && value.free_bytes==12000);
 puts("PASS enabled stats: real monitor receives initialized heap counters");
#else
 unsigned char expected[sizeof(value)];memset(expected,0xa5,sizeof(expected));assert(!memcmp(&value,expected,sizeof(value)));
 /* Do not execute the original undefined monitor arithmetic. Prove its
  * supplier leaves every byte unchanged when runtime stats are disabled. */
 puts("PASS original failure control: stats supplier leaves output untouched when disabled");
#endif
}
'''
with tempfile.TemporaryDirectory() as d:
 f=Path(d)/'stats.c';o=Path(d)/'stats';f.write_text(pre+stats+monitor+main)
 for enabled in (0,1):
  subprocess.run(['cc','-std=c11','-fsanitize=address,undefined',*(['-DCONFIG_SYS_HEAP_RUNTIME_STATS=1'] if enabled else []),str(f),'-o',str(o)],check=True)
  subprocess.run([str(o)],check=True)
