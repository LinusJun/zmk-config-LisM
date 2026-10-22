"""Actual AUTO owner/driver, fake bus only; no live device or serial commands."""
from pathlib import Path
import ast
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
tree=ast.parse((root/'tests/xiao147_raw_integration_host_test.py').read_text())
prelude=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id=='prelude' for t in n.targets))
prelude=prelude.replace('assert(delay==16)','assert(delay==16||delay==4||delay==20)')
prelude=prelude.replace('assert(delay==1000)','assert(delay==1000||delay==20)')
prelude=prelude.replace('static uint32_t now;', '''
#define CONFIG_LISM_XIAO147_AUTO_TOUCH 1
#define CONFIG_I2C_NRFX_TRANSFER_TIMEOUT 100
#include "xiao147_auto_touch.h"

static bool screen_sleeping,imu_enabled,factory_page,block_restore,wrong_id;
static unsigned read_error,release_error,press_error,rel_error;
static int endpoint;
static int xiao147_pointer_endpoint(void){return endpoint;}
static uint16_t firmware=0x0100;
static unsigned enters,restores,left_clicks,right_clicks,rel_count;
static int rel_x,rel_y,wheel,pan,button_down;
static int32_t profile_values[12];
static bool xiao147_ui_is_sleeping(void){return screen_sleeping;}
static bool xiao147_ui_imu_enabled(void){return imu_enabled;}
static bool xiao147_ui_auto_rotate_enabled(void){return false;}
static unsigned motion_wakes;
static bool xiao147_ui_wake_on_motion(void){
 bool woke=screen_sleeping;if(woke){screen_sleeping=false;motion_wakes++;}return woke;
}
static void xiao147_ui_set_auto_orientation(enum xiao147_orientation p){(void)p;}
static uint32_t now;''')
a=prelude.index('static int input_report_key(')
b=prelude.index('static enum xiao147_page xiao147_ui_page',a)
prelude=prelude[:a]+r'''
static int input_report_key(const struct device *dev,int code,int value,bool sync,int wait){
 (void)dev;assert(sync&&wait==0);
 if(value&&press_error){press_error--;return -EIO;}
 if(!value&&release_error){release_error--;return -EIO;}
 if(value){assert(!button_down);button_down=code;if(code==256)left_clicks++;else right_clicks++;}
 else{assert(button_down==code);button_down=0;}
 hid_events++;key_events++;last_key=code;return 0;
}
static int input_report_rel(const struct device *dev,int code,int value,bool sync,int wait){
 (void)dev;assert(sync&&wait==0&&!button_down);
 if(rel_error){rel_error--;return -EIO;}
 if(code==INPUT_REL_X)rel_x+=value;else if(code==INPUT_REL_Y)rel_y+=value;
 else if(code==INPUT_REL_WHEEL)wheel+=value;else if(code==INPUT_REL_HWHEEL)pan+=value;
 rel_count++;hid_events++;return 0;
}
'''+prelude[b:]
driver=re.sub(r'^#include .*$', '',(root/'src/axs5106l_touchpad.c').read_text(),flags=re.M)
# Driver output API is modeled here; owned-source queue is tested separately.
driver=driver.replace('#define input_report_key xiao147_touch_key','').replace('#define input_report_rel xiao147_touch_rel','').replace('xiao147_touch_endpoint()', 'xiao147_pointer_endpoint()')
imu=''
hardware=r'''
static struct axs_touch_data state;
static const struct axs_touch_config config={0};
static struct device touch_device={&state,&config};
static bool input_must_be_blocked(void){return factory_page;}
static int i2c_write_dt(const struct i2c_dt_spec *bus,const void *bytes,size_t len){
 assert(bus==&config.bus);const uint8_t *b=bytes;
 if(len==1){selected_register=b[0];io_phase=1;register_selects++;return 0;}
 assert(xiao147_raw_capture_active(&state.automatic.capture));
 io_phase=0;
 if(len==4&&b[0]==0xfe){
  assert(b[1]==0x5a&&b[2]==0xa5&&(b[3]==0x20||b[3]==0));
  if(b[3]){assert(!factory_page);factory_page=true;enters++;}
  else{restores++;if(block_restore)return -EIO;factory_page=false;}
 }else{
  assert(factory_page&&len==2&&((b[0]==0&&b[1]==2)||(b[0]==3&&b[1]==1)));
 }
 return 0;
}
static int i2c_read_dt(const struct i2c_dt_spec *bus,void *bytes,size_t len){
 assert(bus==&config.bus&&io_phase==2);io_phase=0;uint8_t *out=bytes;
 if(read_error){read_error--;return -EIO;}
 switch(selected_register){
 case 8:assert(len==3);out[0]=0x51;out[1]=6;out[2]=wrong_id?2:1;break;
 case 5:assert(len==2&&!factory_page);out[0]=firmware>>8;out[1]=firmware;break;
 case 0xfe:assert(len==2);out[0]=0;out[1]=factory_page?0x20:0;break;
 case 0x0b:assert(len==3&&factory_page);out[0]=6;out[1]=2;out[2]=0;break;
 case 3:assert(len==1&&factory_page);out[0]=0;break;
 case 2:assert(len==24&&factory_page);for(int i=0;i<12;i++){out[2*i]=profile_values[i]>>8;out[2*i+1]=profile_values[i];}break;
 case 1:
  assert(!factory_page&&(len==14||len==32));ordinary_reads++;
  if(xiao147_raw_capture_active(&state.automatic.capture))normal_validation_reads++;
  memcpy(out,sensor,len);break;
 default:assert(false);
 }
 return 0;
}
static void poll(void){now+=4;axs_poll(&state.poll_work.work);}
static void wait_ms(unsigned ms){for(unsigned i=0;i<ms;i+=4)poll();}
static void values(const int32_t *v,unsigned ms){memcpy(profile_values,v,sizeof(profile_values));wait_ms(ms);}
static const int32_t quiet[12]={0};
static const int32_t one[12]={0,0,100,100,150,150,50,50,0,0,0,0};
static const int32_t two[12]={0,0,200,200,25,25,25,25,180,180,30,30};
static void fixture(void){
 memset(&state,0,sizeof(state));memset(sensor,0,sizeof(sensor));memset(profile_values,0,sizeof(profile_values));
 now=2000;page=0;pose=0;screen_sleeping=imu_enabled=factory_page=block_restore=wrong_id=false;
 endpoint=0;
 read_error=release_error=press_error=rel_error=0;firmware=0x0100;
 enters=restores=left_clicks=right_clicks=rel_count=0;rel_x=rel_y=wheel=pan=button_down=0;
 hid_events=ordinary_reads=menu_samples=menu_cancels=normal_validation_reads=0;
 atomic_set(&capture_gate,0);atomic_set(&normal_button_gate,0);
 atomic_set(&air_gate,1);atomic_set(&air_frame_at,0);
 atomic_set(&air_wake_idle,0);atomic_set(&air_wake_at,0);
 assert(axs_touch_init(&touch_device)==0);
}
static void ready(void){wait_ms(600);assert(factory_page&&state.automatic.baseline.armed&&!state.automatic.blocked);}
static void tap(const int32_t *v){values(v,80);values(quiet,100);}
static void frame(const int32_t *v,unsigned ms){
 now+=ms;state.automatic.now=now;
 state.automatic.capture.ops.sample(&state.automatic,XIAO147_RAW_DIFF,12,v,0);
}
static void restored(void){wait_ms(300);assert(!factory_page&&!xiao147_touch_capture_active()&&normal_validation_reads);}
int main(void){
 fixture();sensor[1]=1;wait_ms(1000);assert(!enters&&!hid_events);
 sensor[1]=0;ready();
 wait_ms(180000);assert(enters==1&&restores==0&&state.automatic.capture.samples_received>5000);
 tap(one);assert(left_clicks==1&&!right_clicks&&!button_down);
 tap(two);assert(right_clicks==1&&left_clicks==1&&!button_down);
 /* Adding a second finger cannot turn a long hold or pointer movement into a tap. */
 values(one,400);values(two,80);values(quiet,100);assert(right_clicks==1);
 values(one,80);
 int32_t shifted_one[12]={0,0,50,50,150,150,100,100,0,0,0,0};
 values(shifted_one,60);assert(state.automatic.one_moved);
 values(two,80);values(quiet,100);assert(right_clicks==1&&left_clicks==1);
 /* TWO->ONE may never become pointer/left. */
 values(two,80);int moves=rel_count;values(one,200);values(quiet,100);
 assert(left_clicks==1&&rel_count==(unsigned)moves);
 /* Unknown excursion cancels the whole single gesture, including release. */
 values(one,60);int32_t unknown[12];for(int i=0;i<12;i++)unknown[i]=100;
 values(unknown,20);values(one,60);values(quiet,100);assert(left_clicks==1);
 /* Native menu uses actual absolute coordinates, never raw-profile ratios. */
 page=1;restored();sensor[1]=1;sensor[3]=40;sensor[5]=70;wait_ms(60);assert(menu_samples);
 sensor[1]=0;wait_ms(60);page=0;ready();
 unsigned clicks,rc;
 /* Slightly slower taps remain clicks; true holds and movement do not. */
 clicks=left_clicks;rc=right_clicks;
 values(one,260);values(quiet,100);assert(left_clicks==clicks+1);
 values(two,260);values(quiet,100);assert(right_clicks==rc+1);
 values(two,400);values(quiet,100);assert(right_clicks==rc+1);
 unsigned retained_left=left_clicks;
 screen_sleeping=true;restored();screen_sleeping=false;ready();
 endpoint=-1;restored();endpoint=1;ready();
 values(one,60);pose=1;values(one,60);values(quiet,100);assert(left_clicks==retained_left);
 /* Failed release still runs cleanup, retries once/tick, and cannot drag. */
 release_error=100;tap(two);assert(button_down==257&&state.pending_button==257);
 wait_ms(500);assert(!button_down&&!state.pending_button&&!factory_page);
 wait_ms(5500);ready();
 /* Transport watchdog exits, retries with cooldown; sticky restore fails shut. */
 read_error=1;wait_ms(100);assert(!factory_page&&state.automatic.failures==1);
 wait_ms(1000);assert(!factory_page);wait_ms(5000);ready();
 block_restore=true;page=1;wait_ms(2500);
 assert(xiao147_raw_capture_faulted(&state.automatic.capture)&&xiao147_touch_capture_active());
 unsigned keys=key_events;wait_ms(2000);assert(key_events==keys);
 fixture();firmware=0x0101;wait_ms(2000);assert(!enters&&state.automatic.disabled);
 fixture();wrong_id=true;wait_ms(2000);assert(!enters&&state.automatic.disabled);
 fixture();ready();now+=500;poll();wait_ms(200);assert(!factory_page); /* stale frame lease */
 fixture();now=UINT32_MAX-300;ready();wait_ms(1000);tap(one);assert(left_clicks==1);
 /* Third confirmed single frame includes displacement from frame two. */
 fixture();ready();
 const int32_t shift1[12]={0,0,80,80,150,150,70,70,0,0,0,0};
 const int32_t shift2[12]={0,0,60,60,150,150,90,90,0,0,0,0};
 const int32_t *onset[3]={one,shift1,shift2};
 struct xiao147_raw_profile first,last;
 assert(xiao147_raw_profile_evaluate(one,12,state.automatic.baseline.values,&first));
 assert(xiao147_raw_profile_evaluate(shift2,12,state.automatic.baseline.values,&last));
 for(unsigned i=0;i<3;i++){
  now+=20;state.automatic.now=now;
  state.automatic.capture.ops.sample(&state.automatic,XIAO147_RAW_DIFF,12,onset[i],0);
  if(i<2)assert(!rel_count);
 }
 assert(rel_x==(int)last.native_long_y-first.native_long_y);
 assert(rel_y==(int)last.native_short_x-first.native_short_x);
 assert(rel_count>0&&!left_clicks&&!right_clicks);
 /* A qualified stationary single may fade briefly before two genuine NONEs.
  * Weak/ambiguous data itself is never a click, motion, release or air-idle. */
 const int32_t weak_tail[12]={0,0,35,35,40,40,25,25,0,0,0,0};
 const int32_t weaker[12]={0,0,25,25,30,30,20,20,0,0,0,0};
 for(int orientation=0;orientation<4;orientation++){
  fixture();pose=orientation;ready();
  frame(one,20);frame(one,20);frame(weak_tail,20);
  assert(state.automatic.one_weak_tail&&!left_clicks&&!rel_count);
  assert(!xiao147_auto_air_idle(&state.automatic,now));
  frame(weaker,20);frame(quiet,20);assert(!left_clicks);
  frame(quiet,20);assert(left_clicks==1&&!right_clicks&&!rel_count&&!button_down);
  frame(quiet,20);assert(left_clicks==1);
  frame(one,20);frame(one,20);frame(quiet,20);frame(quiet,20);
  assert(left_clicks==2); /* no seconds-long inter-tap lockout */
 }
 /* Never rescue weak-only, one unconfirmed frame, moving or long gestures. */
 fixture();ready();frame(weak_tail,20);frame(weaker,20);frame(quiet,20);frame(quiet,20);
 assert(!left_clicks);
 frame(one,20);frame(weak_tail,20);frame(quiet,20);frame(quiet,20);assert(!left_clicks);
 frame(one,20);frame(shifted_one,20);frame(weak_tail,20);frame(quiet,20);frame(quiet,20);
 assert(!left_clicks);
 for(unsigned i=0;i<16;i++)frame(one,20);
 frame(weak_tail,20);frame(quiet,20);frame(quiet,20);assert(!left_clicks);
 /* Reappearance, growth, a new electrode, a second lobe, unknown shapes,
  * NONE bounce and timeout are not releases or restartable tap clocks. */
 const int32_t moved_weak[12]={25,0,35,35,40,40,25,25,0,0,0,0};
 const int32_t *reject[5]={one,two,unknown,weak_tail,moved_weak};
 for(unsigned i=0;i<5;i++){
  fixture();ready();frame(one,20);frame(one,20);frame(weak_tail,20);frame(weaker,20);
  frame(reject[i],10);frame(quiet,20);frame(quiet,20);assert(!left_clicks&&!right_clicks);
 }
 fixture();ready();frame(one,20);frame(one,20);frame(weak_tail,20);frame(quiet,20);
 frame(weaker,10);frame(quiet,10);frame(quiet,10);assert(!left_clicks);
 fixture();ready();frame(one,20);frame(one,20);frame(weak_tail,20);frame(quiet,40);
 frame(quiet,21);assert(!left_clicks); /* 61ms even when second NONE would qualify */
 fixture();ready();frame(one,20);frame(one,20);frame(weak_tail,20);frame(quiet,0);
 frame(quiet,20);assert(!left_clicks); /* duplicate sample clock */
 fixture();ready();frame(one,20);frame(one,20);frame(weak_tail,20);frame(quiet,101);
 frame(quiet,20);assert(!left_clicks);
 /* Existing TWO ownership and UI/endpoint transitions remain fail-closed. */
 fixture();ready();frame(two,20);frame(two,20);frame(weak_tail,20);
 frame(quiet,20);frame(quiet,20);assert(!left_clicks);
 for(unsigned transition=0;transition<3;transition++){
  fixture();ready();frame(one,20);frame(one,20);frame(weak_tail,20);
  if(transition==0)page=1;else if(transition==1)endpoint=-1;else screen_sleeping=true;
  values(quiet,100);assert(!left_clicks&&!right_clicks);
 }
 puts("PASS: actual automatic driver no UART/60s gate, held startup, single/double taps, native menus, sleep/page/endpoint transitions, failed release cleanup, bounded retry/fault and wrap");
}
'''
with tempfile.TemporaryDirectory(prefix='xiao147-auto-') as directory:
    c=Path(directory)/'test.c';c.write_text(prelude+driver+imu+hardware)
    exe=c.with_suffix('')
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-DRAW_MODE=0',
                    '-fsanitize=address,undefined','-I'+str(root/'src'),str(c),
                    *[str(root/'src'/f) for f in ('xiao147_auto_touch.c','xiao147_raw_capture.c','xiao147_raw_gestures.c')],
                    '-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
