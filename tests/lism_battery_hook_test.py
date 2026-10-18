"""Execute the real CMake-generated ZMK worker with sample/publish faults."""
from pathlib import Path
import os
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
app=Path(os.environ.get('LISM_ZMK_APP','/private/tmp/lism-trackpad-build/zmk/app'))

def function(source, signature):
    start=source.index(signature)
    return source[start:source.index('\n}',start)+3]

prelude=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#define IS_ENABLED(x) (x)
#define CONFIG_ZMK_BATTERY_REPORTING_FETCH_MODE_STATE_OF_CHARGE (!MODE)
#define CONFIG_ZMK_BATTERY_REPORTING_FETCH_MODE_LITHIUM_VOLTAGE MODE
#define CONFIG_BT_BAS 1
#define LOG_DBG(...)
#define LOG_WRN(...)
#define LOG_ERR(...)
struct device {int unused;};static struct device dev;
static const struct device *battery=&dev;
struct k_work {int unused;};
struct sensor_value {int val1,val2;};
enum {SENSOR_CHAN_GAUGE_STATE_OF_CHARGE=1,SENSOR_CHAN_VOLTAGE=2};
struct zmk_battery_state_changed {int state_of_charge;};
static uint8_t last_state_of_charge;
static int fetch_error,channel_error,event_error,bas_error;
static unsigned fetches,reads,events,bas_calls,begins,notes;
static uint8_t soc=68,bas=20;
static int last_sample_result;
static int sensor_sample_fetch_chan(const struct device*d,int channel){
 assert(d==battery&&channel==(MODE?2:1));fetches++;return fetch_error;
}
static int sensor_channel_get(const struct device*d,int channel,struct sensor_value*v){
 assert(d==battery&&channel==(MODE?2:1));reads++;
 v->val1=MODE?3:soc;v->val2=MODE?950000:0;return channel_error;
}
static int raise_zmk_battery_state_changed(struct zmk_battery_state_changed e){
 assert(e.state_of_charge==last_state_of_charge);events++;return event_error;
}
static uint8_t bt_bas_get_battery_level(void){return bas;}
static int bt_bas_set_battery_level(uint8_t value){bas_calls++;if(!bas_error)bas=value;return bas_error;}
void lism_charge_begin_battery_sample(void){begins++;}
void lism_charge_note_battery_sample(int result){notes++;last_sample_result=result;}
'''
test=r'''
int main(void){
 struct k_work work={0};
 fetch_error=-ETIMEDOUT;zmk_battery_work(&work);assert(last_sample_result==-ETIMEDOUT&&reads==0&&events==0);
 fetch_error=0;channel_error=-EINVAL;zmk_battery_work(&work);assert(last_sample_result==-EINVAL&&!events);
 channel_error=0;event_error=-ENOTCONN;zmk_battery_work(&work);
 assert(last_sample_result==0&&events==1&&last_state_of_charge>0&&!bas_calls);
 event_error=0;bas_error=-ENOMEM;zmk_battery_work(&work);
 assert(last_sample_result==0&&bas_calls==1); /* delivery failure is not ADC failure */
 bas_error=0;zmk_battery_work(&work);assert(!last_sample_result&&bas==last_state_of_charge);
 zmk_battery_work(&work);assert(!last_sample_result&&begins==6&&notes==6&&fetches==6);
 puts("PASS: actual generated battery worker separates fresh ADC/SOC from event/BAS delivery faults");
}
'''
with tempfile.TemporaryDirectory(prefix='lism-battery-hook-') as tmp:
    tmp=Path(tmp);target=tmp/'generated.c';cmake=tmp/'generate.cmake'
    cmake.write_text(f'include("{root}/cmake/lism_battery_telemetry.cmake")\n'
                     f'lism_patch_battery_telemetry("{app}/src/battery.c" "{target}")\n')
    subprocess.run(['cmake','-P',str(cmake)],check=True)
    generated=target.read_text()
    actual=function(generated,'static uint8_t lithium_ion_mv_to_pct(')
    actual+=function(generated,'static int zmk_battery_update(')
    actual+=function(generated,'static void zmk_battery_work(')
    for mode in (0,1):
        path=tmp/f'test-{mode}.c';exe=path.with_suffix('');path.write_text(prelude+actual+test)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                        '-Wno-unused-parameter','-fsanitize=address,undefined',f'-DMODE={mode}',str(path),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    bad=tmp/'bad.c';bad.write_text((app/'src/battery.c').read_text()+'\n/* unexpected revision */\n')
    target=tmp/'must-not-exist.c'
    cmake.write_text(f'include("{root}/cmake/lism_battery_telemetry.cmake")\n'
                     f'lism_patch_battery_telemetry("{bad}" "{target}")\n')
    rejected=subprocess.run(['cmake','-P',str(cmake)],capture_output=True)
    assert rejected.returncode and not target.exists()
    print('PASS: changed upstream battery source fails closed before generating a hook')
