/* SPDX-License-Identifier: MIT */
/* Included once, by the A-only copied ST7789 driver. */
#include <lism_lcd_diag.h>
#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/logging/log.h>
#include <string.h>
LOG_MODULE_REGISTER(lism_lcd_diag, LOG_LEVEL_INF);
struct lism_lcd_control {
    uint32_t seq, ms;
    int rc;
    uint16_t len;
    uint8_t cmd, data0;
};
struct lism_lcd_pixel {
    uint32_t write, ms;
    int rc;
    uint8_t bytes[2], valid;
};
struct lism_lcd_state {
    uint32_t commands, command_errors, writes, write_errors, controls;
    uint32_t ui_samples, bg;
    uint16_t x, y, w, h;
    uint8_t first[2], last[2], sample_valid;
    int write_rc;
    int16_t x1, y1, x2, y2;
    char text[16];
    struct lism_lcd_control ring[8];
    struct lism_lcd_pixel pixel[2];
    uint32_t output_samples, output_ms, output_bg;
    uint16_t output_state;
    int16_t output_box[4];
    int8_t output_owner;
    uint8_t output_opa, output_visible, output_local, output_profile;
};
static struct k_spinlock lism_lcd_lock;
static struct lism_lcd_state lism_lcd_state;
void lism_lcd_trace_command(uint8_t cmd, const uint8_t *data, size_t len, int rc)
{
    /* CASET/RASET/RAMWR belong to every flush; retain only control commands. */
    bool control = cmd != 0x2a && cmd != 0x2b && cmd != 0x2c;
    struct lism_lcd_control item = {.ms=k_uptime_get_32(), .rc=rc,
        .len=(uint16_t)(len > UINT16_MAX ? UINT16_MAX : len), .cmd=cmd,
        .data0=(data && len) ? data[0] : 0};
    k_spinlock_key_t key = k_spin_lock(&lism_lcd_lock);
    lism_lcd_state.commands++;
    if(rc < 0) lism_lcd_state.command_errors++;
    if(control) {
        item.seq = ++lism_lcd_state.controls;
        lism_lcd_state.ring[(item.seq-1)%8] = item;
    }
    k_spin_unlock(&lism_lcd_lock,key);
}
void lism_lcd_trace_write(uint16_t x, uint16_t y,
                          const struct display_buffer_descriptor *desc, const void *buf, int rc)
{
    uint8_t first[2]={0}, last[2]={0};
    struct lism_lcd_pixel pixel[2]={0};
    bool sample_valid=false;
    /* Copy bytes while the synchronous caller still owns buf. Never retain it. */
    if(buf && desc->width && desc->height && desc->pitch >= desc->width) {
        size_t end=((size_t)(desc->height-1)*desc->pitch+desc->width)*2;
        if(end >= 2 && end <= desc->buf_size) {
            memcpy(first,buf,2);
            memcpy(last,(const uint8_t *)buf+end-2,2);
            sample_valid=true;
            /* A home: BLE background and same-color keyboard top border.
             * Attempted RGB565 bytes, not panel readback. Each point keeps
             * its own sequence/time because partial writes can differ.
             */
            if(IS_ENABLED(CONFIG_ST7789V_RGB565)) {
                static const uint16_t points[2][2]={{214,176},{218,145}};
                for(unsigned i=0;i<2;i++) {
                    uint32_t px=points[i][0],py=points[i][1];
                    if(px>=x && px<(uint32_t)x+desc->width &&
                       py>=y && py<(uint32_t)y+desc->height) {
                        size_t offset=((size_t)(py-y)*desc->pitch+px-x)*2;
                        memcpy(pixel[i].bytes,(const uint8_t *)buf+offset,2);
                        pixel[i].valid=1;
                    }
                }
            }
        }
    }
    k_spinlock_key_t key=k_spin_lock(&lism_lcd_lock);
    lism_lcd_state.writes++;
    if(rc < 0) lism_lcd_state.write_errors++;
    lism_lcd_state.x=x; lism_lcd_state.y=y;
    lism_lcd_state.w=desc->width; lism_lcd_state.h=desc->height;
    lism_lcd_state.write_rc=rc;
    lism_lcd_state.sample_valid=sample_valid;
    memcpy(lism_lcd_state.first,first,2); memcpy(lism_lcd_state.last,last,2);
    for(unsigned i=0;i<2;i++) if(pixel[i].valid) {
        pixel[i].write=lism_lcd_state.writes;
        pixel[i].ms=k_uptime_get_32();pixel[i].rc=rc;
        lism_lcd_state.pixel[i]=pixel[i];
    }
    k_spin_unlock(&lism_lcd_lock,key);
}
void lism_lcd_trace_output_ui(uint32_t bg, uint8_t opa, uint16_t state,
    uint8_t visible, int8_t owner, uint8_t local_transport, uint8_t profile,
    int16_t x1, int16_t y1, int16_t x2, int16_t y2)
{
    k_spinlock_key_t key=k_spin_lock(&lism_lcd_lock);
    lism_lcd_state.output_samples++;lism_lcd_state.output_ms=k_uptime_get_32();
    lism_lcd_state.output_bg=bg & 0xffffff;lism_lcd_state.output_opa=opa;
    lism_lcd_state.output_state=state;lism_lcd_state.output_visible=visible;
    lism_lcd_state.output_owner=owner;lism_lcd_state.output_local=local_transport;
    lism_lcd_state.output_profile=profile;
    lism_lcd_state.output_box[0]=x1;lism_lcd_state.output_box[1]=y1;
    lism_lcd_state.output_box[2]=x2;lism_lcd_state.output_box[3]=y2;
    k_spin_unlock(&lism_lcd_lock,key);
}
void lism_lcd_trace_ui(uint32_t bg, const char *text, int16_t x1, int16_t y1,
                       int16_t x2, int16_t y2)
{
    char copy[16]={0};
    if(text) { strncpy(copy,text,sizeof(copy)-1); }
    k_spinlock_key_t key=k_spin_lock(&lism_lcd_lock);
    lism_lcd_state.ui_samples++; lism_lcd_state.bg=bg & 0xffffff;
    lism_lcd_state.x1=x1; lism_lcd_state.y1=y1;
    lism_lcd_state.x2=x2; lism_lcd_state.y2=y2;
    memcpy(lism_lcd_state.text,copy,sizeof(copy));
    k_spin_unlock(&lism_lcd_lock,key);
}
static void lism_lcd_log_work(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(lism_lcd_work,lism_lcd_log_work);
static void lism_lcd_log_work(struct k_work *work)
{
    ARG_UNUSED(work);
    struct lism_lcd_state s;
    k_spinlock_key_t key=k_spin_lock(&lism_lcd_lock);
    s=lism_lcd_state;
    k_spin_unlock(&lism_lcd_lock,key);
    static uint32_t logged_controls, dropped_controls;
    uint32_t oldest=s.controls>8?s.controls-7:1;
    if(logged_controls+1<oldest) {
        dropped_controls+=oldest-logged_controls-1;
        logged_controls=oldest-1;
    }
    LOG_INF("LCD11 cmd=%u err=%u write=%u err=%u xywh=%u,%u,%u,%u rc=%d valid=%u bytes=%02x%02x/%02x%02x ui=%u bg=%06x text=%s box=%d,%d,%d,%d ctrl_drop=%u",
        s.commands,s.command_errors,s.writes,s.write_errors,s.x,s.y,s.w,s.h,s.write_rc,
        s.sample_valid,s.first[0],s.first[1],s.last[0],s.last[1],s.ui_samples,s.bg,s.text,s.x1,s.y1,s.x2,s.y2,dropped_controls);
    LOG_INF("LCD15 output=%u ms=%u rgb=%06x opa=%u state=%u visible=%u owner=%d local=%u profile=%u box=%d,%d,%d,%d",
        s.output_samples,s.output_ms,s.output_bg,s.output_opa,s.output_state,
        s.output_visible,s.output_owner,s.output_local,s.output_profile,
        s.output_box[0],s.output_box[1],s.output_box[2],s.output_box[3]);
    for(unsigned i=0;i<2;i++) {
        const struct lism_lcd_pixel *p=&s.pixel[i];
        LOG_INF("LCD15 point=%u xy=%u,%u valid=%u bytes=%02x%02x attempt_write=%u ms=%u rc=%d",
            i,i?218:214,i?145:176,p->valid,p->bytes[0],p->bytes[1],p->write,p->ms,p->rc);
    }
    /* Drain in order, at most two per five seconds; report ring overwrites. */
    for(unsigned n=0;n<2 && logged_controls<s.controls;n++) {
        uint32_t seq=++logged_controls;
        struct lism_lcd_control c=s.ring[(seq-1)%8];
        LOG_INF("LCD11 ctrl=%u ms=%u cmd=%02x len=%u data0=%02x rc=%d",c.seq,c.ms,c.cmd,c.len,c.data0,c.rc);
    }
    k_work_reschedule(&lism_lcd_work,K_SECONDS(5));
}
static int lism_lcd_diag_init(void)
{
    k_work_reschedule(&lism_lcd_work,K_SECONDS(5));
    return 0;
}
SYS_INIT(lism_lcd_diag_init,APPLICATION,90);
