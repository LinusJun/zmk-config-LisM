"""Portable Report Map parser and notification decoder; synthetic HID, no BLE I/O."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = r'''
#include "lism_hid_parser.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>

/* Ordinary no-report-ID three-button mouse with constant padding. */
static const uint8_t mouse[]={
 0x05,0x01,0x09,0x02,0xa1,0x01,0x09,0x01,0xa1,0x00,
 0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,
 0x75,0x01,0x95,0x03,0x81,0x02,0x75,0x05,0x95,0x01,0x81,0x01,
 0x05,0x01,0x09,0x30,0x09,0x31,0x09,0x38,0x15,0x81,0x25,0x7f,
 0x75,0x08,0x95,0x03,0x81,0x06,0xc0,0xc0
};
/* A Mouse app may include standard Consumer AC Pan. All payloads are HOGP
 * notification bytes WITHOUT a report-ID prefix, supplied separately via API. */
static const uint8_t multi[]={
 0x05,0x01,0x09,0x02,0xa1,0x01,
 0x85,0x01,0x05,0x09,0x19,0x01,0x29,0x08,0x15,0x00,0x25,0x01,
 0x75,0x01,0x95,0x08,0x81,0x02,
 0x85,0x02,0x05,0x01,0x09,0x30,0x09,0x31,0x16,0x00,0xf8,0x26,0xff,0x07,
 0x75,0x0c,0x95,0x02,0x81,0x06,
 0x85,0x03,0x05,0x01,0x09,0x38,0x16,0x00,0x80,0x26,0xff,0x7f,
 0x75,0x10,0x95,0x01,0x81,0x06,
 0x05,0x0c,0x0a,0x38,0x02,0x81,0x06,
 0x85,0x04,0x05,0x09,0x09,0x01,0x15,0x00,0x25,0x01,
 0x75,0x01,0x95,0x01,0x81,0x02,0x75,0x07,0x81,0x01,0xc0
};
static struct lism_hid_descriptor d;
static struct lism_hid_state s;
static struct lism_hid_event e;
static void put_bits(uint8_t *bytes,unsigned offset,unsigned count,uint32_t value){
 for(unsigned i=0;i<count;i++)if(value&(UINT32_C(1)<<i))bytes[(offset+i)/8]|=1U<<((offset+i)%8);
}
static void parse_good(const uint8_t *bytes,size_t length){
 assert(lism_hid_parse(bytes,length,&d)==LISM_HID_OK);memset(&s,0,sizeof(s));
}
static void bad(const uint8_t *bytes,size_t length,enum lism_hid_error expected){
 memset(&d,0xa5,sizeof(d));enum lism_hid_error actual=lism_hid_parse(bytes,length,&d);
 if(actual!=expected)fprintf(stderr,"length=%zu actual=%s expected=%s\n",length,lism_hid_error_string(actual),lism_hid_error_string(expected));
 assert(actual==expected);
 assert(!d.field_count&&!d.report_count); /* rejected maps cannot become decodable */
}
static void decode_bad(unsigned id,const uint8_t *bytes,size_t length,enum lism_hid_error expected){
 struct lism_hid_state before=s;memset(&e,0xa5,sizeof(e));struct lism_hid_event untouched=e;
 assert(lism_hid_decode(&d,&s,id,bytes,length,&e)==expected);
 assert(!memcmp(&before,&s,sizeof(s))&&!memcmp(&untouched,&e,sizeof(e)));
}
static void standard(void){
 assert(sizeof(d)<3072);
 parse_good(mouse,sizeof(mouse));assert(d.field_count==6&&d.report_count==1&&!d.has_report_ids);
 assert(d.capabilities&LISM_HID_CAP_MOUSE);
 assert(d.reports[0].input_bits==32&&d.reports[0].button_mask==7);
 uint8_t p[]={3,0xf6,20,0xff};
 assert(lism_hid_decode(&d,&s,0,p,sizeof(p),&e)==0);
 assert(e.dx==-10&&e.dy==20&&e.wheel==-1&&!e.pan);
 assert(e.buttons==3&&e.changed==3&&e.pressed==3&&!e.released&&e.updated_buttons==7);
 assert(lism_hid_decode(&d,&s,0,p,sizeof(p),&e)==0);
 assert(e.buttons==3&&!e.changed&&!e.pressed&&!e.released); /* no-change is not release */
 p[0]=2;assert(lism_hid_decode(&d,&s,0,p,sizeof(p),&e)==0);
 assert(e.buttons==2&&e.changed==1&&e.released==1&&!e.pressed);
 decode_bad(1,p,sizeof(p),LISM_HID_ERR_REPORT_ID);
 decode_bad(0,p,3,LISM_HID_ERR_LENGTH);
 uint8_t prefix[]={0,2,1,2,3};decode_bad(0,prefix,sizeof(prefix),LISM_HID_ERR_LENGTH);
 p[1]=0x80;decode_bad(0,p,sizeof(p),LISM_HID_ERR_RANGE); /* descriptor says -127, not -128 */
 p[1]=0;p[0]=0;assert(lism_hid_decode(&d,&s,0,p,sizeof(p),&e)==0);
 assert(!e.buttons&&e.released==2);
}
static void reports_and_bits(void){
 parse_good(multi,sizeof(multi));assert(d.report_count==4&&d.field_count==13&&d.has_report_ids);
 uint8_t b[]={0x81};assert(lism_hid_decode(&d,&s,1,b,1,&e)==0);
 assert(e.buttons==0x81&&e.pressed==0x81&&e.updated_buttons==255);
 uint8_t xy[3]={0};put_bits(xy,0,12,(uint32_t)-1000&4095);put_bits(xy,12,12,2047);
 assert(lism_hid_decode(&d,&s,2,xy,sizeof(xy),&e)==0);
 assert(e.dx==-1000&&e.dy==2047&&e.buttons==0x81&&!e.changed&&!e.updated_buttons);
 memset(xy,0,sizeof(xy));put_bits(xy,0,12,2048);put_bits(xy,12,12,(uint32_t)-1&4095);
 assert(lism_hid_decode(&d,&s,2,xy,sizeof(xy),&e)==0);assert(e.dx==-2048&&e.dy==-1);
 uint8_t scroll[]={0x00,0x80,0xff,0x7f};
 assert(lism_hid_decode(&d,&s,3,scroll,sizeof(scroll),&e)==0);
 assert(e.wheel==-32768&&e.pan==32767&&e.buttons==0x81&&!e.changed);
 uint8_t duplicate[]={1};assert(lism_hid_decode(&d,&s,4,duplicate,1,&e)==0);
 assert(e.buttons==0x81&&!e.changed&&e.updated_buttons==1);
 b[0]=0;assert(lism_hid_decode(&d,&s,1,b,1,&e)==0);
 assert(e.buttons==1&&e.released==0x80); /* report 4 still owns button one */
 duplicate[0]=0;assert(lism_hid_decode(&d,&s,4,duplicate,1,&e)==0);
 assert(!e.buttons&&e.released==1);
 decode_bad(0,xy,3,LISM_HID_ERR_REPORT_ID);
}
static void offsets_and_globals(void){
 /* Output and feature offsets advance independently of input; Global PUSH /
  * POP restores report ID, count, size, usage page and signed logical range. */
 const uint8_t map[]={
  0x05,1,0x09,2,0xa1,1,0x85,7,
  0x75,8,0x95,2,0x91,1,0xb1,1, /* output16, feature16 */
  0x95,1,0x75,3,0x81,1, /* three input padding bits */
  0x15,0x81,0x25,0x7f,0x75,8,0x95,1,
  0xa4,0x85,8,0x05,0x0c,0x0a,0x38,2,0x81,6,0xb4,
  0x09,0x30,0x81,6,0xc0};
 parse_good(map,sizeof(map));assert(d.report_count==2&&d.field_count==2);
 assert(d.reports[0].id==7&&d.reports[0].input_bits==11);
 assert(d.reports[0].output_bits==16&&d.reports[0].feature_bits==16);
 assert(d.fields[1].bit_offset==3&&d.fields[1].kind==LISM_HID_X);
 uint8_t p[2]={0};put_bits(p,3,8,(uint32_t)-43&255);
 assert(lism_hid_decode(&d,&s,7,p,2,&e)==0&&e.dx==-43&&!e.pan);
 uint8_t pan[]={12};assert(lism_hid_decode(&d,&s,8,pan,1,&e)==0&&e.pan==12&&!e.dx);
 /* Four-byte local Usage encodes its own page and must not inherit Button. */
 const uint8_t explicit_page[]={0x05,1,0x09,2,0xa1,1,0x05,9,
  0x0b,0x30,0x00,0x01,0x00,0x15,0x81,0x25,0x7f,0x75,8,0x95,1,0x81,6,0xc0};
 parse_good(explicit_page,sizeof(explicit_page));uint8_t negative[]={0xfb};
 assert(lism_hid_decode(&d,&s,0,negative,1,&e)==0&&e.dx==-5);
 /* Signed 32-bit extraction has defined behavior, including INT32_MIN. */
 const uint8_t wide[]={0x05,1,0x09,2,0xa1,1,0x09,0x30,
  0x17,0,0,0,0x80,0x27,0xff,0xff,0xff,0x7f,0x75,32,0x95,1,0x81,6,0xc0};
 parse_good(wide,sizeof(wide));uint8_t min32[]={0,0,0,0x80};
 assert(lism_hid_decode(&d,&s,0,min32,4,&e)==0&&e.dx==INT32_MIN);
 min32[0]=min32[1]=min32[2]=0xff;min32[3]=0x7f;
 assert(lism_hid_decode(&d,&s,0,min32,4,&e)==0&&e.dx==INT32_MAX);
}
static void capability_only(void){
 const uint8_t vendor[]={0x06,0,0xff,0x09,1,0xa1,1,0x15,0,0x26,0xff,0,
  0x75,8,0x95,4,0x09,2,0x81,2,0xc0};
 parse_good(vendor,sizeof(vendor));assert(!d.field_count&&(d.capabilities&LISM_HID_CAP_VENDOR));
 uint8_t p[4]={1,2,3,4};decode_bad(0,p,4,LISM_HID_ERR_UNSUPPORTED);
 const uint8_t digitizer[]={0x05,0x0d,0x09,4,0xa1,1,0x05,1,0x09,0x30,
  0x15,0,0x26,0xff,0x7f,0x75,16,0x95,1,0x81,2,0xc0};
 parse_good(digitizer,sizeof(digitizer));assert(!d.field_count&&(d.capabilities&LISM_HID_CAP_DIGITIZER));
 const uint8_t keyboard[]={0x05,1,0x09,6,0xa1,1,0x05,7,0x19,0,0x29,0x65,
  0x15,0,0x25,0x65,0x75,8,0x95,6,0x81,0,0xc0};
 parse_good(keyboard,sizeof(keyboard));assert(!d.field_count&&(d.capabilities&LISM_HID_CAP_KEYBOARD));
 /* Array button selectors are not independent variable buttons. Padding is
  * consumed, while the following real signed X axis still works. */
 const uint8_t array[]={0x05,1,0x09,2,0xa1,1,0x05,9,0x19,1,0x29,8,
  0x15,0,0x25,8,0x75,8,0x95,1,0x81,0,
  0x05,1,0x09,0x30,0x15,0x81,0x25,0x7f,0x81,6,0xc0};
 parse_good(array,sizeof(array));assert(d.field_count==1&&!d.reports[0].button_mask);
 uint8_t a[]={2,7};assert(lism_hid_decode(&d,&s,0,a,2,&e)==0&&e.dx==7&&!e.buttons);
 assert(d.capabilities&LISM_HID_CAP_UNSUPPORTED);
 /* Vendor/digitizer nested collections do not gain mouse meaning by ancestry. */
 const uint8_t nested[]={0x05,1,0x09,2,0xa1,1,0x05,0x0d,0x09,4,0xa1,0,
  0x05,1,0x09,0x30,0x15,0x81,0x25,0x7f,0x75,8,0x95,1,0x81,6,0xc0,0xc0};
 parse_good(nested,sizeof(nested));assert(!d.field_count);
 const uint8_t escaped[]={0x05,0x0d,0x09,4,0xa1,1,
  0x05,1,0x09,2,0xa1,1,0x09,0x30,0x15,0x81,0x25,0x7f,
  0x75,8,0x95,1,0x81,6,0xc0,0xc0};
 parse_good(escaped,sizeof(escaped));assert(!d.field_count&&(d.capabilities&LISM_HID_CAP_DIGITIZER));
}
static void rejection(void){
 const uint8_t truncated[]={0x27,1,2};bad(truncated,sizeof(truncated),LISM_HID_ERR_TRUNCATED);
 const uint8_t longitem[]={0xfe,1,0,0};bad(longitem,sizeof(longitem),LISM_HID_ERR_UNSUPPORTED);
 const uint8_t underflow[]={0xb4};bad(underflow,sizeof(underflow),LISM_HID_ERR_MALFORMED);
 const uint8_t end[]={0xc0};bad(end,sizeof(end),LISM_HID_ERR_MALFORMED);
 const uint8_t push[]={0xa4,0xa4,0xa4,0xa4,0xa4};bad(push,sizeof(push),LISM_HID_ERR_LIMIT);
 const uint8_t unclosed[]={0x05,1,0x09,2,0xa1,1};bad(unclosed,sizeof(unclosed),LISM_HID_ERR_MALFORMED);
 const uint8_t absolute[]={0x05,1,0x09,2,0xa1,1,0x09,0x30,0x15,0x81,0x25,0x7f,
  0x75,8,0x95,1,0x81,2,0xc0};bad(absolute,sizeof(absolute),LISM_HID_ERR_ABSOLUTE);
 assert(d.capabilities&LISM_HID_CAP_ABSOLUTE);
 uint8_t too_wide[sizeof(absolute)];memcpy(too_wide,absolute,sizeof(absolute));
 too_wide[13]=33;bad(too_wide,sizeof(too_wide),LISM_HID_ERR_LIMIT);
 const uint8_t count[]={0x05,1,0x09,2,0xa1,1,0x75,32,0x96,0xff,0xff,0x81,1,0xc0};
 bad(count,sizeof(count),LISM_HID_ERR_LIMIT);
 const uint8_t zeroid[]={0x85,0};bad(zeroid,sizeof(zeroid),LISM_HID_ERR_REPORT_ID);
 const uint8_t mixed[]={0x05,1,0x09,2,0xa1,1,0x75,8,0x95,1,0x81,1,0x85,1,0x81,1,0xc0};
 bad(mixed,sizeof(mixed),LISM_HID_ERR_REPORT_ID);
 const uint8_t unsignedaxis[]={0x05,1,0x09,2,0xa1,1,0x09,0x30,0x15,0,0x26,0xff,0,
  0x75,8,0x95,1,0x81,6,0xc0};bad(unsignedaxis,sizeof(unsignedaxis),LISM_HID_ERR_RANGE);
 const uint8_t toobiglogical[]={0x05,1,0x09,2,0xa1,1,0x09,0x30,0x16,0,0x80,0x26,0xff,0x7f,
  0x75,8,0x95,1,0x81,6,0xc0};bad(toobiglogical,sizeof(toobiglogical),LISM_HID_ERR_RANGE);
 const uint8_t duplicate[]={0x05,1,0x09,2,0xa1,1,0x09,0x30,0x09,0x30,
  0x15,0x81,0x25,0x7f,0x75,8,0x95,2,0x81,6,0xc0};
 bad(duplicate,sizeof(duplicate),LISM_HID_ERR_MALFORMED);
 const uint8_t reversed[]={0x05,1,0x09,2,0xa1,1,0x05,9,0x19,8,0x29,1,
  0x15,0,0x25,1,0x75,1,0x95,8,0x81,2,0xc0};
 bad(reversed,sizeof(reversed),LISM_HID_ERR_MALFORMED);
 /* Every configured bound is exercised at its boundary and one past it. */
 uint8_t many[1024];size_t length=0;
 for(unsigned id=1;id<=9;id++){
  many[length++]=0x85;many[length++]=(uint8_t)id;
  memcpy(many+length,mouse,sizeof(mouse));length+=sizeof(mouse);
  if(id==8){parse_good(many,length);assert(d.field_count==LISM_HID_MAX_FIELDS);}
 }
 bad(many,length,LISM_HID_ERR_LIMIT);
 const uint8_t begin[]={0x05,1,0x09,2,0xa1,1};
 memcpy(many,begin,sizeof(begin));length=sizeof(begin);
 for(unsigned id=1;id<=17;id++){
  const uint8_t next[]={0x85,(uint8_t)id,0x75,8,0x95,1,0x81,1};
  memcpy(many+length,next,sizeof(next));length+=sizeof(next);
  if(id==16){many[length]=0xc0;parse_good(many,length+1);assert(d.report_count==16);}
 }
 many[length++]=0xc0;bad(many,length,LISM_HID_ERR_LIMIT);
 length=0;for(unsigned i=0;i<9;i++){many[length++]=0xa1;many[length++]=0;}
 bad(many,length,LISM_HID_ERR_LIMIT);
 memcpy(many,begin,sizeof(begin));length=sizeof(begin);
 for(unsigned i=0;i<17;i++){many[length++]=0x09;many[length++]=0x30;}
 bad(many,length,LISM_HID_ERR_LIMIT);
 const uint8_t maximum_offset[]={0x05,1,0x09,2,0xa1,1,0x75,8,0x95,63,0x81,1,
  0x09,0x30,0x15,0x81,0x25,0x7f,0x95,1,0x81,6,0xc0};
 parse_good(maximum_offset,sizeof(maximum_offset));assert(d.fields[0].bit_offset==504);
 uint8_t sixtyfour[64]={0};sixtyfour[63]=0xf6;
 assert(lism_hid_decode(&d,&s,0,sixtyfour,sizeof(sixtyfour),&e)==0&&e.dx==-10);
 uint8_t overflow[sizeof(maximum_offset)];memcpy(overflow,maximum_offset,sizeof(overflow));
 overflow[9]=64;bad(overflow,sizeof(overflow),LISM_HID_ERR_LIMIT);
 /* Truncate every prefix and mutate every byte. Parser/decoder never read
  * outside bounded input, even when a mutation yields another valid map. */
 for(size_t n=0;n<sizeof(multi);n++){
  enum lism_hid_error r=lism_hid_parse(multi,n,&d);
  assert(r!=LISM_HID_OK||(!d.field_count&&n==0));
 }
 for(size_t n=0;n<sizeof(multi);n++)for(unsigned bit=0;bit<8;bit++){
  uint8_t mutation[sizeof(multi)];memcpy(mutation,multi,sizeof(multi));mutation[n]^=1U<<bit;
  if(lism_hid_parse(mutation,sizeof(mutation),&d)==0){
   memset(&s,0,sizeof(s));uint8_t zeros[LISM_HID_MAX_REPORT_BYTES]={0};
   for(unsigned i=0;i<d.report_count;i++){
    (void)lism_hid_decode(&d,&s,d.reports[i].id,zeros,(d.reports[i].input_bits+7)/8,&e);
   }
  }
 }
}
int main(void){
 standard();reports_and_bits();offsets_and_globals();capability_only();rejection();
 assert(!strcmp(lism_hid_error_string(LISM_HID_ERR_ABSOLUTE),"absolute"));
 printf("PASS: bounded HID parser (%zu bytes), Mouse-only relative fields, 12/16/32-bit signed offsets, independent report IDs/buttons, output/feature cursors, capability isolation and malformed fail-closed\n",sizeof(d));
}
'''

with tempfile.TemporaryDirectory(prefix="lism-hid-parser-") as directory:
    c = Path(directory)/"test.c"
    c.write_text(source)
    exe = c.with_suffix("")
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-I"+str(root/"src"),
                    str(c), str(root/"src/lism_hid_parser.c"), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
