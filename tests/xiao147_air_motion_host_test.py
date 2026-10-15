"""Actual air-mouse core: calibration gates, dt invariance, noise and boundaries."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
source = r'''
#include <assert.h>
#include <stdio.h>
#include <limits.h>
#include "xiao147_air_motion.h"
static const int16_t gravity[3]={0,0,16384};
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
 for(unsigned t=0;t<=2000;t+=interval){sum+=sample(&s,t,sign*1000,0,0).x;}
 return sum;
}
static struct xiao147_xy vector_movement(unsigned interval,int x,int y,int pose){
 struct xiao147_air_motion s={.bias_valid=true};struct xiao147_xy sum={0};
 int16_t raw[3]={x,y,0};
 for(unsigned t=0;t<=2000;t+=interval){
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
int main(void){
 struct xiao147_air_motion s={0};
 xiao147_air_begin(&s,0);
 for(unsigned t=0;t<10000;t+=20){struct xiao147_xy p=sample(&s,t,2000,1000,0);assert(!p.x&&!p.y);}
 assert(!s.bias_valid); /* moving past 3 seconds never becomes zero-bias output */
 for(unsigned t=10000;t<12500;t+=20)sample(&s,t,100,-100,50);
 assert(s.bias_valid); /* retries without toggling or serial intervention */
 s=(struct xiao147_air_motion){0};calibrate(&s,0);
 int16_t slow[3]={200,-100,50};int plus=0,minus=0;
 xiao147_air_clear(&s);
 for(unsigned t=2000;t<4000;t+=20)plus+=xiao147_air_sample(&s,t,0,slow).x;
 xiao147_air_clear(&s);slow[0]=0;
 for(unsigned t=4000;t<6000;t+=20)minus+=xiao147_air_sample(&s,t,0,slow).x;
 assert(plus>0&&plus==-minus); /* fractional micro-motion not rounded away */
 int n10=movement(10,1),n20=movement(20,1),n40=movement(40,1);
 assert(abs(n10-n20)<=2&&abs(n20-n40)<=3&&movement(20,-1)==-n20);
 xiao147_air_clear(&s);int16_t move[3]={1300,650,0};
 struct xiao147_xy p=xiao147_air_sample(&s,7000,0,move);assert(!p.x&&!p.y);
 p=xiao147_air_sample(&s,7000,0,move);assert(!p.x&&!p.y);
 xiao147_air_sample(&s,7020,0,move);
 p=xiao147_air_sample(&s,7040,0,move);assert(p.x>0&&p.y<0);
 p=xiao147_air_sample(&s,7500,0,move);assert(!p.x&&!p.y);
 assert(!s.remainder[0]&&!s.filtered[0]);
 for(int pose=0;pose<4;pose++){
  s=(struct xiao147_air_motion){.bias_valid=true};
  xiao147_air_sample(&s,0,pose,move);xiao147_air_sample(&s,20,pose,move);
  p=xiao147_air_sample(&s,40,pose,move);
  assert((p.x>0)==(pose==0||pose==1));assert((p.y>0)==(pose==1||pose==2));
 }
 s=(struct xiao147_air_motion){.bias_valid=true};
 for(unsigned t=0;t<1000;t+=20){p=sample(&s,t,32767,-32768,0);assert(abs(p.x)<=30&&abs(p.y)<=30);}
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
 assert(abs(diagonal.x+2*diagonal.y)<=3); /* common gain preserves direction */
 for(int orientation=1;orientation<4;orientation++){
  struct xiao147_xy rotated=vector_movement(20,3080,1580,orientation);
  assert(abs(rotated.x)==abs(orientation%2?diagonal.y:diagonal.x));
  assert(abs(rotated.y)==abs(orientation%2?diagonal.x:diagonal.y));
 }
 /* High-gain diagonals hitting the speed ceiling must not turn 2:1 into 1:1. */
 for(int sign=-1;sign<=1;sign+=2)for(int orientation=0;orientation<4;orientation++){
  struct xiao147_xy capped=vector_movement(20,sign*6080,sign*3080,orientation);
  int major=orientation%2?abs(capped.y):abs(capped.x);
  int minor=orientation%2?abs(capped.x):abs(capped.y);
  assert(major>2500&&major<=3000&&abs(major-2*minor)<=3);
  assert((capped.x>0)==((orientation==0||orientation==1)==(sign>0)));
  assert((capped.y>0)==((orientation==1||orientation==2)==(sign>0)));
 }
 struct xiao147_xy fast10=vector_movement(10,6080,0,0),fast40=vector_movement(40,6080,0,0);
 assert(abs(fast10.x-fast.x)<=30&&abs(fast40.x-fast.x)<=30);
 s=(struct xiao147_air_motion){.bias_valid=true};
 for(unsigned t=0;t<1000;t+=20)sample(&s,t,32767,32767,0);
 xiao147_air_clear(&s);p=sample(&s,1000,0,0,0);assert(!p.x&&!p.y);
 p=sample(&s,1020,0,0,0);assert(!p.x&&!p.y&&!s.remainder[0]&&!s.remainder[1]);
 printf("Air units over 2s: precision=%d medium=%d fast=%d (old hard ceiling 500)\n",
        precision_reference(20,500),mid.x,fast.x);
 puts("PASS: air mouse no-bias fail closed, automatic retry, fresh gravity, actual dt, all poses, subpixel symmetry, gap/reset and no overspeed backlog");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-air-") as d:
    c=Path(d)/"test.c";c.write_text(source);exe=c.with_suffix("")
    subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror","-fsanitize=address,undefined",
                    "-I"+str(root/"src"),str(c),"-o",str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
