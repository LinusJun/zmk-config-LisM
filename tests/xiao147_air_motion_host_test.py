"""Actual air-mouse core: calibration gates, dt invariance, noise and boundaries."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
source = r'''
#include <assert.h>
#include <stdio.h>
#include <limits.h>
#include <math.h>
#include "xiao147_air_motion.h"
/* Explicit native support acceleration for a flat, screen-front-up grip. */
static const int16_t gravity[3]={0,0,-16384};
static const int right[4][3]={{0,-1,0},{-1,0,0},{0,1,0},{1,0,0}};
static struct xiao147_xy sample(struct xiao147_air_motion *s,uint32_t t,int x,int y,int z){
 int16_t g[3]={x,y,z};xiao147_air_gravity(s,t,gravity);return xiao147_air_sample(s,t,0,g);
}
static void calibrate(struct xiao147_air_motion *s,uint32_t start){
 xiao147_air_begin(s,start);
 for(unsigned i=0;i<110;i++){struct xiao147_xy p=sample(s,start+i*20,100,-100,50);assert(!p.x&&!p.y);}
 assert(s->bias_valid&&!s->calibrating&&s->bias[0]==100&&s->bias[1]==-100);
}
static int movement(unsigned interval,int sign){
 struct xiao147_air_motion s={.bias_valid=true};int sum=0;
 for(unsigned t=0;t<=2000;t+=interval){sum+=sample(&s,t,0,0,sign*1000).x;}
 return sum;
}
static struct xiao147_xy vector_movement(unsigned interval,int x,int y,int pose){
 struct xiao147_air_motion s={.bias_valid=true};struct xiao147_xy sum={0};
 /* Encode logical rates in an independently defined physical flat basis. */
 int16_t raw[3]={-right[pose][0]*y,-right[pose][1]*y,x};
 for(unsigned t=0;t<=2000;t+=interval){
  xiao147_air_gravity(&s,t,gravity);
  struct xiao147_xy p=xiao147_air_sample(&s,t,pose,raw);sum.x+=p.x;sum.y+=p.y;
 }
 return sum;
}
/* Independent old low-speed filter/integrator; no new gain helper here. */
static int precision_reference(unsigned interval,int raw){
 int filtered=0,remainder=0,sum=0;
 for(unsigned t=interval;t<=2000;t+=interval){
  int corrected=raw;if(abs(corrected)<=80){filtered=remainder=0;continue;}
  corrected+=corrected>0?-80:80;
  filtered+=(int)(((int64_t)corrected*256-filtered)*interval/(30+interval));
  int64_t integrated=remainder+(int64_t)filtered*interval;
  sum+=integrated/(6500*256);remainder=integrated%(6500*256);
 }
 return sum;
}
static void geometry(void){
 /* Independent analytic tables, not calculated from the implementation's
  * vector helper. Columns are native gyro X,Y,Z; rows logical X,Y response.
  * Test yaw, pitch and ignored roll in both grip styles for every screen pose. */
 static const int16_t top[4][3]={{-16384,0,0},{0,16384,0},{16384,0,0},{0,-16384,0}};
 static const int flat_x[4][3]={{0,0,1},{0,0,1},{0,0,1},{0,0,1}};
 static const int flat_y[4][3]={{0,1,0},{1,0,0},{0,-1,0},{-1,0,0}};
 static const int upright_x[4][3]={{1,0,0},{0,-1,0},{-1,0,0},{0,1,0}};
 static const int upright_y[4][3]={{0,1,0},{1,0,0},{0,-1,0},{-1,0,0}};
 for(int grip=0;grip<2;grip++)for(int pose=0;pose<4;pose++){
  for(int axis=0;axis<3;axis++)for(int sign=-1;sign<=1;sign+=2){
   struct xiao147_air_motion s={.bias_valid=true};struct xiao147_xy v;
   int16_t raw[3]={0};raw[axis]=sign*1200;
   xiao147_air_gravity(&s,0,grip?top[pose]:gravity);
   assert(xiao147_air_project(&s,0,pose,raw,&v));
   assert(v.x==sign*1200*(grip?upright_x:flat_x)[pose][axis]);
   assert(v.y==sign*1200*(grip?upright_y:flat_y)[pose][axis]);
  }
 }
 /* Native Z is useful yaw flat, not the ignored upright forward-axis roll. */
 struct xiao147_air_motion s={.bias_valid=true};int sum=0;
 for(unsigned t=0;t<=1000;t+=20)sum+=sample(&s,t,0,0,1800).x;
 assert(sum>0);
 /* Physically coherent trajectories, not a staircase of arbitrary gravity
  * values: d(UP_body)/dt = -omega_body CROSS UP_body. A +RIGHT rotation
  * carries support acceleration from screen-front to screen-top. Reverse
  * rotation traverses the same path backwards. 1800 gyro counts at the
  * configured 8.75 mdps/count is 15.75 deg/s; each sample advances 20 ms.
  * Optional yaw about world UP leaves that gravity trajectory unchanged. */
 for(int pose=0;pose<4;pose++)for(int direction=-1;direction<=1;direction+=2)
 for(int with_yaw=0;with_yaw<2;with_yaw++){
  s=(struct xiao147_air_motion){.bias_valid=true};int vertical=0,horizontal=0;
  for(unsigned now=0;now<=5700;now+=20){
   double angle=1800.0*.00875*(now/1000.0)*3.141592653589793/180.0;
   if(direction<0)angle=3.141592653589793/2-angle;
   int16_t a[3]={lround(top[pose][0]*sin(angle)),
                 lround(top[pose][1]*sin(angle)),lround(-16384*cos(angle))};
   int16_t raw[3];
   for(int axis=0;axis<3;axis++)raw[axis]=direction*1800*right[pose][axis]
       -(with_yaw?lround(1000.0*a[axis]/16384.0):0);
   struct xiao147_xy rate;
   xiao147_air_gravity(&s,now,a);
   assert(xiao147_air_project(&s,now,pose,raw,&rate));
   assert(abs(rate.y+direction*1800)<=1);
   assert(with_yaw?abs(rate.x-1000)<=3:rate.x==0);
   struct xiao147_xy p=xiao147_air_sample(&s,now,pose,raw);
   if(!with_yaw)assert(!p.x);
   else assert(p.x>=0);
   horizontal+=p.x;vertical+=p.y;
  }
  assert((vertical<0)==(direction>0)&&abs(vertical)>500);
  assert(with_yaw?horizontal>0:horizontal==0);
 }
}
static void projection_guards(void){
 struct xiao147_air_motion s={.bias_valid=true};struct xiao147_xy v;
 int16_t raw[3]={0,1200,1200};uint32_t now=0;
 xiao147_air_gravity(&s,now,gravity);assert(xiao147_air_project(&s,now,0,raw,&v));
 /* Touch/HID clear preserves trusted UP and independent logical rate. */
 xiao147_air_clear(&s);assert(s.up_valid&&s.up[2]==-16384);
 assert(xiao147_air_project(&s,now,0,raw,&v)&&v.x==1200&&v.y==1200);
 /* Invalid norms do not refresh the trusted orientation's lease. */
 int16_t invalid[3]={0};
 for(now=20;now<=100;now+=20){
  xiao147_air_gravity(&s,now,invalid);assert(xiao147_air_project(&s,now,0,raw,&v));
 }
 xiao147_air_gravity(&s,101,invalid);
 assert(!xiao147_air_project(&s,101,0,raw,&v)&&!v.x&&!v.y&&!s.have_time);
 xiao147_air_gravity(&s,120,gravity);assert(xiao147_air_project(&s,120,0,raw,&v));
 s.have_gravity=false;assert(!xiao147_air_project(&s,120,0,raw,&v)); /* I2C error */
 xiao147_air_gravity(&s,140,gravity);assert(xiao147_air_project(&s,140,0,raw,&v));
 assert(xiao147_air_project(&s,240,0,raw,&v));
 assert(!xiao147_air_project(&s,241,0,raw,&v));
 s.bias_valid=false;xiao147_air_gravity(&s,260,gravity);
 assert(!xiao147_air_project(&s,260,0,raw,&v));
 s.bias_valid=true;s.calibrating=true;assert(!xiao147_air_project(&s,260,0,raw,&v));
 /* A hemisphere discontinuity must not normalize back to the old UP forever.
  * Invalidate this frame, then re-anchor on a subsequent fresh valid sample. */
 s=(struct xiao147_air_motion){.bias_valid=true};
 int16_t opposite[3]={0,0,16384};
 xiao147_air_gravity(&s,0,gravity);xiao147_air_gravity(&s,20,opposite);
 assert(!xiao147_air_project(&s,20,0,raw,&v)&&!s.up_valid&&!s.have_time);
 xiao147_air_gravity(&s,40,opposite);
 assert(xiao147_air_project(&s,40,0,raw,&v)&&v.x==-1200&&v.y==1200);
 struct xiao147_xy p=xiao147_air_sample(&s,40,0,raw);assert(!p.x&&!p.y);
 /* RIGHT parallel to UP is singular. Exit below .35; remain silent in the
  * .35..50 hysteresis band until .50 recovery. Never substitute another axis. */
 s=(struct xiao147_air_motion){.bias_valid=true};now=0;
 const int16_t dirs[5][3]={{0,0,-16384},{0,-15016,-6554},{0,-15629,-4915},
                         {0,-15016,-6554},{0,-13107,-9830}};
 for(unsigned phase=0;phase<5;phase++){
  for(unsigned frame=0;frame<80;frame++){
   now+=20;xiao147_air_gravity(&s,now,dirs[phase]);
   bool ok=xiao147_air_project(&s,now,0,raw,&v);
   if(phase==3)assert(!ok);
  }
  assert(s.project_ready==(phase<2||phase==4));
  if(phase==2||phase==3)assert(!s.have_time&&!s.filtered[0]&&!s.remainder[0]);
 }
 p=xiao147_air_sample(&s,now,0,raw);assert(!p.x&&!p.y);
 xiao147_air_begin(&s,now);assert(!s.up_valid&&!s.project_ready);
 /* Extreme signed input, full-width sqrt and time wrap remain defined. */
 s=(struct xiao147_air_motion){.bias_valid=true,.bias={-32768,32767,-32768}};
 int16_t huge[3]={32767,-32768,32767};xiao147_air_gravity(&s,UINT_MAX-10,gravity);
 assert(xiao147_air_project(&s,9,0,huge,&v)&&v.x==65535&&v.y==-65535);
 for(uint64_t n=0;n<100000;n+=17){
  uint64_t r=xiao147_air_sqrt(n);assert(r*r<=n&&(r+1)*(r+1)>n);
 }
 assert(xiao147_air_sqrt(UINT64_MAX)==UINT32_MAX);
}
int main(void){
 geometry();projection_guards();
 struct xiao147_air_motion s={0};
 xiao147_air_begin(&s,0);
 for(unsigned t=0;t<10000;t+=20){struct xiao147_xy p=sample(&s,t,2000,1000,0);assert(!p.x&&!p.y);}
 assert(!s.bias_valid); /* moving past 3 seconds never becomes zero-bias output */
 for(unsigned t=10000;t<12500;t+=20)sample(&s,t,100,-100,50);
 assert(s.bias_valid); /* retries without toggling or serial intervention */
 s=(struct xiao147_air_motion){0};calibrate(&s,0);
 int plus=0,minus=0;
 xiao147_air_clear(&s);
 for(unsigned t=2200;t<4200;t+=20)plus+=sample(&s,t,100,-100,150).x;
 xiao147_air_clear(&s);
 for(unsigned t=4200;t<6200;t+=20)minus+=sample(&s,t,100,-100,-50).x;
 assert(plus>0&&plus==-minus); /* fractional micro-motion not rounded away */
 int n10=movement(10,1),n20=movement(20,1),n40=movement(40,1);
 assert(abs(n10-n20)<=2&&abs(n20-n40)<=3&&movement(20,-1)==-n20);
 xiao147_air_clear(&s);
 struct xiao147_xy p=sample(&s,7000,100,550,1350);assert(!p.x&&!p.y);
 p=sample(&s,7000,100,550,1350);assert(!p.x&&!p.y);
 sample(&s,7020,100,550,1350);
 p=sample(&s,7040,100,550,1350);assert(p.x>0&&p.y>0);
 p=sample(&s,7500,100,550,1350);assert(!p.x&&!p.y);
 assert(!s.remainder[0]&&!s.filtered[0]);
 s=(struct xiao147_air_motion){.bias_valid=true};
 for(unsigned t=0;t<1000;t+=20){p=sample(&s,t,0,-32768,32767);assert(abs(p.x)<=30&&abs(p.y)<=30);}
 for(unsigned t=1000;t<2000;t+=20){p=sample(&s,t,0,0,0);assert(!p.x&&!p.y&&!s.remainder[0]);}
 /* Missing/flat-zero/stale acceleration must not establish a bias. */
 s=(struct xiao147_air_motion){0};xiao147_air_begin(&s,0);
 int16_t still[3]={100,0,0};
 for(unsigned t=0;t<5000;t+=20)xiao147_air_sample(&s,t,0,still);
 assert(!s.bias_valid);
 /* Synthetic stationary noise bounded by the observed device log: its
  * -358..-524 Y bias must calibrate, never be forced to a zero bias. */
 s=(struct xiao147_air_motion){0};xiao147_air_begin(&s,0);
 const int measured_y[10]={-523,-457,-524,-427,-358,-505,-440,-457,-510,-483};
 for(unsigned i=0;i<120;i++)sample(&s,i*20,180,measured_y[i%10],-24);
 assert(s.bias_valid&&!s.calibrating&&s.bias[1]<-400&&s.bias[1]>-510);
 /* A changing/large rotation, duplicated clock or missing gravity must
  * not be accepted merely because the calibration deadline elapsed. */
 s=(struct xiao147_air_motion){0};xiao147_air_begin(&s,0);
 for(unsigned t=0;t<5000;t+=20)sample(&s,t,(t/20)%2?700:-700,0,0);
 assert(!s.bias_valid);
 for(unsigned i=0;i<200;i++)sample(&s,5000,180,-480,-24);
 assert(!s.bias_valid);
 s=(struct xiao147_air_motion){0};calibrate(&s,UINT_MAX-500);
 /* Low-speed precision is bit-identical to the old curve, including signed
  * fractions, deadband and multiple actual polling intervals. */
 for(unsigned dt=10;dt<=40;dt*=2)for(int v=-580;v<=580;v+=20){
  struct xiao147_xy sum=vector_movement(dt,v,0,0);
  assert(sum.x==precision_reference(dt,v)&&sum.y==0);
 }
 int previous=256;
 for(int v=0;v<=8000*256;v+=17){
  int gain=xiao147_air_gain_q8(v);assert(gain>=previous&&gain>=256&&gain<=1024);previous=gain;
 }
 assert(xiao147_air_gain_q8(500*256)==256&&xiao147_air_gain_q8(6000*256)==1024);
 struct xiao147_xy mid=vector_movement(20,3080,0,0),fast=vector_movement(20,6080,0,0);
 assert(mid.x>2*precision_reference(20,3080));
 assert(fast.x>5*500&&fast.x<=3000); /* old hard cap <=500 units per 2 seconds */
 int prior=0;
 for(int v=100;v<=32700;v+=100){
  struct xiao147_xy plus=vector_movement(20,v,0,0),minus=vector_movement(20,-v,0,0);
  assert(plus.x>=prior&&plus.x==-minus.x);prior=plus.x;
 }
 struct xiao147_xy diagonal=vector_movement(20,3080,1580,0);
 assert(abs(diagonal.x-2*diagonal.y)<=3); /* common gain preserves direction */
 for(int orientation=1;orientation<4;orientation++){
  struct xiao147_xy rotated=vector_movement(20,3080,1580,orientation);
  assert(rotated.x==diagonal.x&&rotated.y==diagonal.y);
 }
 /* High-gain diagonals hitting the speed ceiling must not turn 2:1 into 1:1. */
 for(int sign=-1;sign<=1;sign+=2)for(int orientation=0;orientation<4;orientation++){
  struct xiao147_xy capped=vector_movement(20,sign*6080,sign*3080,orientation);
  int major=abs(capped.x),minor=abs(capped.y);
  assert(major>2500&&major<=3000&&abs(major-2*minor)<=3);
  assert((capped.x>0)==(sign>0));
  assert((capped.y>0)==(sign>0));
 }
 struct xiao147_xy fast10=vector_movement(10,6080,0,0),fast40=vector_movement(40,6080,0,0);
 assert(abs(fast10.x-fast.x)<=30&&abs(fast40.x-fast.x)<=30);
 s=(struct xiao147_air_motion){.bias_valid=true};
 for(unsigned t=0;t<1000;t+=20)sample(&s,t,0,32767,32767);
 xiao147_air_clear(&s);p=sample(&s,1000,0,0,0);assert(!p.x&&!p.y);
 p=sample(&s,1020,0,0,0);assert(!p.x&&!p.y&&!s.remainder[0]&&!s.remainder[1]);
 printf("Air units over 2s: precision=%d medium=%d fast=%d (old hard ceiling 500)\n",
        precision_reference(20,500),mid.x,fast.x);
 puts("PASS: analytic flat/upright yaw/pitch/roll in four poses, continuous grip trajectory, no pitch-to-yaw leak, singularity hysteresis, antipodal recovery, fresh gravity, native calibration, actual dt/wrap, signed precision, unchanged gain and no backlog");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-air-") as d:
    c=Path(d)/"test.c";c.write_text(source);exe=c.with_suffix("")
    subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror","-fsanitize=address,undefined",
                    "-I"+str(root/"src"),str(c),"-lm","-o",str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
