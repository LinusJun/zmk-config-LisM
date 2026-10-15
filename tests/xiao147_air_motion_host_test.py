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
 for(unsigned i=0;i<55;i++){struct xiao147_xy p=sample(s,start+i*20,100,-100,50);assert(!p.x&&!p.y);}
 assert(s->bias_valid&&!s->calibrating&&s->bias[0]==100&&s->bias[1]==-100);
}
static int movement(unsigned interval,int sign){
 struct xiao147_air_motion s={.bias_valid=true};int sum=0;
 for(unsigned t=0;t<=2000;t+=interval){sum+=sample(&s,t,sign*1000,0,0).x;}
 return sum;
}
int main(void){
 struct xiao147_air_motion s={0};
 xiao147_air_begin(&s,0);
 for(unsigned t=0;t<10000;t+=20){struct xiao147_xy p=sample(&s,t,2000,1000,0);assert(!p.x&&!p.y);}
 assert(!s.bias_valid); /* moving past 3 seconds never becomes zero-bias output */
 for(unsigned t=10000;t<11500;t+=20)sample(&s,t,100,-100,50);
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
 for(unsigned t=0;t<1000;t+=20){p=sample(&s,t,32767,-32768,0);assert(abs(p.x)<=5&&abs(p.y)<=5);}
 for(unsigned t=1000;t<2000;t+=20){p=sample(&s,t,0,0,0);assert(!p.x&&!p.y&&!s.remainder[0]);}
 /* Missing/flat-zero/stale acceleration must not establish a bias. */
 s=(struct xiao147_air_motion){0};xiao147_air_begin(&s,0);
 int16_t still[3]={100,0,0};
 for(unsigned t=0;t<5000;t+=20)xiao147_air_sample(&s,t,0,still);
 assert(!s.bias_valid);
 s=(struct xiao147_air_motion){0};calibrate(&s,UINT_MAX-500);
 puts("PASS: air mouse no-bias fail closed, automatic retry, fresh gravity, actual dt, all poses, subpixel symmetry, gap/reset and no overspeed backlog");
}
'''
with tempfile.TemporaryDirectory(prefix="xiao147-air-") as d:
    c=Path(d)/"test.c";c.write_text(source);exe=c.with_suffix("")
    subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror","-fsanitize=address,undefined",
                    "-I"+str(root/"src"),str(c),"-o",str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
