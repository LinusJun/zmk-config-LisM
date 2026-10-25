"""Actual production icons, four signal groups and bounded predicate."""
from pathlib import Path
import hashlib,json,re,subprocess,tempfile
P=Path(__file__).resolve().parents[2];V=P/'verification';S=P/'source'
staged=S/'src/kb_route/icons.c';helper=S/'src/kb_route/icon_input.h'
code=re.sub(r'^#include.*$','',staged.read_text(),flags=re.M)
pre=r'''
#include <lvgl.h>
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <kb_route/view.h>
#include <kb_route/signal.h>
#include <kb_route/icon_input.h>
#define CONFIG_LISM_BLE_INPUT_BRIDGE 1
#define LISM_INPUT_PEERS 2
enum {LISM_INPUT_DEVICE_UNKNOWN,LISM_INPUT_DEVICE_MOUSE,LISM_INPUT_DEVICE_KEYBOARD,LISM_INPUT_DEVICE_TOUCHPAD,LISM_INPUT_DEVICE_GAMEPAD};
struct lism_input_peer_status {bool connected,ready;uint8_t type;char name[24];};
struct lism_input_status {bool enabled;struct lism_input_peer_status peers[2];};
static struct lism_input_status input_fixture;
static unsigned route=KB_ROUTE_DONGLE;
void kb_route_snapshot(struct kb_route_view *v){*v=(struct kb_route_view){.mode=route};}
void lism_input_host_snapshot(struct lism_input_status *v){*v=input_fixture;}
bool kb_esb_signal(unsigned i,int8_t *v){*v=i?-65:-50;return true;}
bool lism_input_host_slot_signal(unsigned slot,int8_t *v){*v=slot?-72:-85;return input_fixture.enabled && input_fixture.peers[slot].connected && input_fixture.peers[slot].ready;}
'''
main=r'''
static uint8_t panel[320*240*2];static unsigned width,height;
static void flush(lv_display_t*d,const lv_area_t*a,uint8_t*map){
 for(int y=a->y1;y<=a->y2;y++)for(int x=a->x1;x<=a->x2;x++){
 unsigned from=((y-a->y1)*(a->x2-a->x1+1)+x-a->x1)*2,to=(y*width+x)*2;
#if LV_COLOR_16_SWAP
 panel[to]=map[from+1];panel[to+1]=map[from];
#else
 panel[to]=map[from];panel[to+1]=map[from+1];
#endif
 }lv_display_flush_ready(d);
}
static void predicate_cases(void){
 const char *yes[]={"JP19","jp19","My JP19 mouse","CIRCLEPAD","Circlepad BLE","vendor-circlepad","[Jp19]","CirclePad_1"};
 const char *no[]={"XJP19","JP190","JP19mouse","CirclepadPro","mycirclepad","circle pad","JP 19","notCirclepad",""};
 for(unsigned i=0;i<sizeof(yes)/sizeof(*yes);i++){struct kb_icon_input_state s=kb_icon_input_classify(1,1,1,1,yes[i],strlen(yes[i])+1);assert(s.joystick&&!s.pad);}
 for(unsigned i=0;i<sizeof(no)/sizeof(*no);i++){struct kb_icon_input_state s=kb_icon_input_classify(1,1,1,1,no[i],strlen(no[i])+1);assert(!s.joystick&&s.pad);}
 for(unsigned bits=0;bits<16;bits++){struct kb_icon_input_state s=kb_icon_input_classify(bits&1,bits&2,bits&4,bits&8,"JP19",5);assert(s.joystick==(bits==15)&&!s.pad);}
 assert(!kb_icon_alias("JP19mouse",4,"jp19"));assert(!kb_icon_alias(NULL,24,"jp19"));
 char delimiter_no_nul[5]={'J','P','1','9','-'};assert(!kb_icon_alias(delimiter_no_nul,sizeof(delimiter_no_nul),"jp19"));
 char not_terminated[4]={'J','P','1','9'};assert(!kb_icon_alias(not_terminated,sizeof(not_terminated),"jp19"));
 puts("PASS pure predicate token/case/eligibility/false aliases/truncated name bounds");
}
static void peer(const char *name,unsigned type,bool ready){input_fixture=(struct lism_input_status){.enabled=true,.peers={{.connected=true,.ready=ready,.type=type}}};snprintf(input_fixture.peers[0].name,24,"%s",name);refresh(NULL);}
static void signal_bounds(void){
 lv_area_t a;lv_obj_get_coords(signal_icons,&a);
#ifdef CONFIG_LISM_XIAO147_TOUCH
 assert(a.x1==4&&a.y1==90&&a.x2==127&&a.y2==115);
#else
 assert(a.x1==10&&a.y1==140&&a.x2==133&&a.y2==165);
#endif
 const unsigned expected_bars[4]={4,3,1,2};
 const uint32_t expected_color[4]={0xb9b9a7,0xb9b9a7,0xb9b9a7,0x569fa7};
 const char *names[4]={"L","R","J","BLE"};
 const uint8_t semantic_bars[4]={4,3,2,1};assert(!memcmp(bars,semantic_bars,sizeof(bars)));
 for(unsigned i=0;i<4;i++){
  int miny=999,maxy=-1,count=0;
  for(int y=a.y1;y<=a.y2;y++)for(int x=a.x1+3+i*30;x<a.x1+3+(i+1)*30;x++){
   unsigned n=(y*width+x)*2;if(panel[n]||panel[n+1]){if(y<miny)miny=y;if(y>maxy)maxy=y;count++;}
  }
  assert(count>0 && miny>=a.y1+2 && maxy<a.y2-2);
  /* Sample all four opaque bar bottoms, independent of source draw indexes. */
  for(unsigned n=0;n<4;n++){
   unsigned x=a.x1+3+i*30+10+n*3,y=a.y1+19,offset=(y*width+x)*2;
   uint16_t actual=panel[offset]|panel[offset+1]<<8;
   uint16_t expected=lv_color_to_u16(lv_color_hex(n<expected_bars[i]?expected_color[i]:0x353640));
   assert(actual==expected);
  }
  /* J is font ink; the last column contains the teal Bluetooth rune. */
  unsigned teal=0,ink=0;
  for(int y=a.y1+4;y<=a.y1+16;y++)for(int x=a.x1+3+i*30;x<a.x1+12+i*30;x++){
   unsigned offset=(y*width+x)*2;uint16_t c=panel[offset]|panel[offset+1]<<8;
   ink+=c!=0;teal+=c==lv_color_to_u16(lv_color_hex(0x569fa7));
  }
  assert(ink>0);assert(i==3?teal>0:teal==0);
  printf("SIGNAL column=%u name=%s bars=%u ink_y=%d..%d bottom_margin=%d pixels=%d rune_teal=%u\n",i,names[i],expected_bars[i],miny,maxy,a.y2-maxy,count,teal);
 }
}
static void visible_bounds(void){
 lv_area_t a;lv_obj_get_coords(icons,&a);
#ifdef CONFIG_LISM_XIAO147_TOUCH
 assert(a.x1==200&&a.y1==92&&a.x2==315&&a.y2==113);
#else
 assert(a.x1==148&&a.y1==142&&a.x2==263&&a.y2==163);
#endif
 /* Segment actual nonblack ink columns; no fixed cells can hide unequal gaps. */
 int edges[4][2],nicons=0;
 for(int x=a.x1;x<=a.x2;){
  bool ink=false;for(int y=a.y1;y<=a.y2;y++){unsigned n=(y*width+x)*2;ink|=panel[n]||panel[n+1];}
  if(!ink){x++;continue;}
  assert(nicons<4);edges[nicons][0]=x;
  do {x++;ink=false;if(x<=a.x2)for(int y=a.y1;y<=a.y2;y++){unsigned n=(y*width+x)*2;ink|=panel[n]||panel[n+1];}} while(ink);
  edges[nicons++][1]=x-1;
 }
 assert(nicons==4);const int expected_width[4]={21,24,24,16};
 for(unsigned i=0;i<4;i++){
  int minx=edges[i][0],maxx=edges[i][1],miny=999,maxy=-1,count=0;
  for(int y=a.y1;y<=a.y2;y++)for(int x=minx;x<=maxx;x++){
   unsigned n=(y*width+x)*2;if(panel[n]||panel[n+1]){if(y<miny)miny=y;if(y>maxy)maxy=y;count++;}
  }
  printf("VISIBLE icon=%u bounds=%d,%d..%d,%d width=%d height=%d pixels=%d\n",i,minx,miny,maxx,maxy,maxx-minx+1,maxy-miny+1,count);
  assert(count>0&&miny==a.y1+2&&maxy==a.y1+19&&maxy-miny+1==18&&maxx-minx+1==expected_width[i]);
  if(i){int gap=minx-edges[i-1][1]-1;printf("INK_GAP pair=%u,%u gap=%d\n",i-1,i,gap);fflush(stdout);assert(gap==7);}
 }
 assert(edges[0][0]==a.x1+3&&edges[3][1]<=a.x2);

}
int main(int argc,char **argv){assert(argc==2);predicate_cases();lv_init();
#ifdef CONFIG_LISM_XIAO147_TOUCH
 width=320;height=172;
#else
 width=280;height=240;
#endif
 lv_display_t *d=lv_display_create(width,height);static uint8_t buffer[320*12*2];lv_display_set_color_format(d,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(d,buffer,NULL,sizeof(buffer),LV_DISPLAY_RENDER_MODE_PARTIAL);lv_display_set_flush_cb(d,flush);
 lv_obj_t *screen=lv_obj_create(NULL);lv_obj_set_style_bg_color(screen,lv_color_black(),0);lv_obj_set_style_bg_opa(screen,255,0);lv_screen_load(screen);kb_route_icons_create(screen);
 peer("JP19",LISM_INPUT_DEVICE_UNKNOWN,true);assert(!pad_ready&&!joystick_ready);
 peer("JP19",LISM_INPUT_DEVICE_MOUSE,false);assert(!pad_ready&&!joystick_ready);
 peer("JP19",LISM_INPUT_DEVICE_MOUSE,true);assert(!pad_ready&&joystick_ready);
 peer("normal mouse",LISM_INPUT_DEVICE_MOUSE,true);assert(pad_ready&&!joystick_ready);
 peer("Circlepad",LISM_INPUT_DEVICE_TOUCHPAD,true);assert(!pad_ready&&joystick_ready);
 input_fixture.enabled=false;refresh(NULL);assert(!pad_ready&&!joystick_ready);
 input_fixture.enabled=true;input_fixture.peers[0].connected=false;refresh(NULL);assert(!pad_ready&&!joystick_ready);
 input_fixture.peers[0].connected=true;input_fixture.peers[1]=(struct lism_input_peer_status){.connected=true,.ready=true,.type=LISM_INPUT_DEVICE_MOUSE};strcpy(input_fixture.peers[1].name,"mouse");refresh(NULL);assert(pad_ready&&joystick_ready);
 route=KB_ROUTE_BLE;refresh(NULL);lv_obj_update_layout(screen);lv_refr_now(d);visible_bounds();signal_bounds();
 FILE*f=fopen(argv[1],"wb");assert(f);fprintf(f,"P6\n%u %u\n255\n",width,height);
 for(unsigned i=0;i<width*height;i++){uint16_t c=panel[2*i]|panel[2*i+1]<<8;unsigned char rgb[3]={((c>>11)&31)*255/31,((c>>5)&63)*255/63,(c&31)*255/31};assert(fwrite(rgb,1,3,f)==3);}fclose(f);
 puts("PASS actual draw/refresh: four visible 18px symbols/common baseline and three equal 7px ink gaps; ordinary/identified/unknown/not-ready cases");
}
'''
assert (P/'wpm-native/build-1/liblvgl.a').exists()
logs=[];checks=[]
with tempfile.TemporaryDirectory(prefix='icons-staged-') as directory:
 t=Path(directory);(t/'main.c').write_text(pre+code+main)
 for board in ['a','b']:
  exe=t/board;image=V/f'signal-order-{board}.ppm';B=P/('wpm-native/build-0' if board=='b' else 'wpm-native/build-1');assert (B/'liblvgl.a').exists()
  cmd=['cc','-std=gnu11','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE','-DLV_COLOR_16_SWAP='+('0' if board=='b' else '1'),'-I'+str(V),'-I'+str(S/'src'),'-I/deps/modules/lib/gui/lvgl','-I'+str(P/'wpm-native')]
  if board=='b':cmd+=['-DCONFIG_LISM_XIAO147_TOUCH=1']
  cmd += [str(t/'main.c'),str(B/'liblvgl.a'),'-lm','-o',str(exe)]
  r=subprocess.run(cmd,capture_output=True,text=True);logs.append(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
  r=subprocess.run([str(exe),str(image)],capture_output=True,text=True);logs.append(r.stdout+r.stderr);print(board,r.stdout+r.stderr,end='');assert r.returncode==0,r.stdout+r.stderr
  checks.append({'board':board,'exit_code':r.returncode,'image':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'display':{'width':320 if board=='b' else 280,'height':172 if board=='b' else 240,'swap':0 if board=='b' else 1}})
(V/'signal-order-native.log').write_text(''.join(logs))
(V/'signal-order-result.json').write_text(json.dumps({'passed':True,'staged_only':False,'signal_order':['L','R','J','BLE'],'semantic_bars':[4,3,2,1],'rendered_column_bars':[4,3,1,2],'actual_ink_gaps':[7,7,7],'visible_heights':[18,18,18,18],'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [staged,helper,Path(__file__)]},'checks':checks,'boundary':'Actual production draw/refresh and pure predicate; input/view/signal snapshots mocked. Native LVGL reused uninstrumented library; extracted production executable is ASan+UBSan. Display classification only; no parser/scanner/input changes or JP19 hardware/name validation.','lucide_reference':'verification/icon-options/A-lucide-joystick.svg; https://lucide.dev/icons/joystick'},indent=2)+'\n')
