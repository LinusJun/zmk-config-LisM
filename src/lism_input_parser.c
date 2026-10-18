#include "lism_input_parser.h"
#include <limits.h>
#include <string.h>

#define LOCAL_MAX 32
#define DEPTH_MAX 8
enum { MODE_NONE, MODE_KEYBOARD, MODE_MOUSE, MODE_CONSUMER };
struct globals { uint32_t page, size, count; int32_t min, max; uint8_t id; };
struct locals { uint32_t usage[LOCAL_MAX], min, max; uint8_t count; bool have_min, have_max; };
static uint32_t number(const uint8_t *p, unsigned n) {
    uint32_t v=0; for(unsigned i=0;i<n;i++) v|=(uint32_t)p[i]<<(8*i); return v;
}
static int32_t sign_value(uint32_t v,unsigned bits) {
    if(bits && (v & (UINT32_C(1)<<(bits-1)))) return (int32_t)((int64_t)v-(INT64_C(1)<<bits));
    return (int32_t)v;
}
static bool local_valid(const struct locals *l) {
    return l->have_min==l->have_max && (!l->have_min ||
        (!l->count && l->min<=l->max && (l->min>>16)==(l->max>>16)));
}
static uint32_t first_usage(const struct locals *l) { return l->have_min?l->min:l->count?l->usage[0]:0; }
static int report_for(struct lism_input_descriptor *d,uint8_t id) {
    for(unsigned i=0;i<d->report_count;i++) if(d->reports[i].id==id) return (int)i;
    if(d->report_count==LISM_INPUT_MAX_REPORTS) return -1;
    unsigned i=d->report_count++; d->reports[i].id=id; return (int)i;
}
static bool relevant(unsigned mode,uint32_t usage,bool relative) {
    unsigned page=usage>>16, code=usage&65535;
    if(mode==MODE_KEYBOARD) return page==7 && code<=255;
    if(mode==MODE_CONSUMER) return page==12 && code>0;
    if(mode==MODE_MOUSE) return (page==9 && code>=1 && code<=8) ||
        (relative && ((page==1 && (code==0x30 || code==0x31 || code==0x38)) || (page==12 && code==0x238)));
    return false;
}
static enum lism_input_error data_item(struct lism_input_descriptor *d,const struct globals *g,
    const struct locals *l,unsigned tag,uint32_t flags,unsigned mode) {
    if(!g->size || !g->count) return LISM_INPUT_ERR_MALFORMED;
    if(g->size>32 || g->count>512) return LISM_INPUT_ERR_LIMIT;
    int ri=report_for(d,g->id); if(ri<0) return LISM_INPUT_ERR_LIMIT;
    struct lism_input_report *report=&d->reports[ri];
    uint16_t *cursor=tag==8?&report->input_bits:tag==9?&report->output_bits:&report->feature_bits;
    uint32_t bits=g->size*g->count;
    if(bits>512U-*cursor) return LISM_INPUT_ERR_LIMIT;
    uint16_t offset=*cursor; *cursor+=(uint16_t)bits;
    if(tag!=8 || (flags&1) || !mode) return LISM_INPUT_OK;
    /* Media keys are also commonly declared inside the Keyboard application
     * rather than a separate Consumer Control application. */
    if(mode==MODE_KEYBOARD && (first_usage(l)>>16)==12) mode=MODE_CONSUMER;
    /* Buffered bytes / wrapping / nonlinear encodings are not scalar input. */
    if(flags & (0x100U|0x10U|0x20U)) return LISM_INPUT_OK;
    bool variable=(flags&2)!=0, relative=(flags&4)!=0, useful=false;
    if(l->have_min) {
        uint32_t end=l->max-l->min;
        if(end>65535) return LISM_INPUT_ERR_LIMIT;
        for(uint32_t i=0;i<=end;i++) if(relevant(mode,l->min+i,relative)) { useful=true; break; }
    } else for(unsigned i=0;i<l->count;i++) if(relevant(mode,l->usage[i],relative)) useful=true;
    if(!useful || (mode==MODE_MOUSE && !variable)) return LISM_INPUT_OK;
    if(g->min>g->max) return LISM_INPUT_ERR_RANGE;
    int64_t low=g->min<0?-(INT64_C(1)<<(g->size-1)):0;
    int64_t high=g->min<0?(INT64_C(1)<<(g->size-1))-1:
        (g->size==32?INT32_MAX:(INT64_C(1)<<g->size)-1);
    if(g->min<low || g->max>high) return LISM_INPUT_ERR_RANGE;
    if(d->run_count==LISM_INPUT_MAX_RUNS || l->count>LISM_INPUT_MAX_USAGES-d->usage_count) return LISM_INPUT_ERR_LIMIT;
    struct lism_input_run *r=&d->runs[d->run_count++];
    *r=(struct lism_input_run){.logical_min=g->min,.logical_max=g->max,
        .usage_min=l->min,.usage_max=l->max,.bit_offset=offset,.count=(uint16_t)g->count,
        .usage_offset=d->usage_count,.bit_size=(uint8_t)g->size,.report_index=(uint8_t)ri,
        .usage_count=l->count,.mode=(uint8_t)mode,.variable=variable,.relative=relative,
        .ranged=l->have_min,.null_state=(flags&0x40)!=0};
    for(unsigned i=0;i<l->count;i++) d->usages[d->usage_count++]=l->usage[i];
    uint8_t update=mode==MODE_KEYBOARD?LISM_INPUT_UPDATED_KEYBOARD:mode==MODE_MOUSE?LISM_INPUT_UPDATED_MOUSE:LISM_INPUT_UPDATED_CONSUMER;
    report->updates|=update;
    d->capabilities|=mode==MODE_KEYBOARD?LISM_INPUT_CAP_KEYBOARD:
        mode==MODE_MOUSE?LISM_INPUT_CAP_MOUSE:LISM_INPUT_CAP_CONSUMER;
    return LISM_INPUT_OK;
}
enum lism_input_error lism_input_parse(const uint8_t *map,size_t length,struct lism_input_descriptor *d) {
    if(!d) return LISM_INPUT_ERR_ARGUMENT;
    memset(d,0,sizeof(*d));
    if(!map || !length) return LISM_INPUT_ERR_ARGUMENT;
    if(length>LISM_INPUT_MAX_MAP_BYTES) return LISM_INPUT_ERR_LIMIT;
    struct globals g={0}, stack[4]; struct locals l={0};
    uint8_t modes[DEPTH_MAX]; unsigned depth=0,sp=0;
    enum lism_input_error err=LISM_INPUT_OK;
    for(size_t at=0;at<length && !err;) {
        uint8_t p=map[at++]; unsigned n=p&3; if(n==3)n=4;
        if(p==0xfe) { err=LISM_INPUT_ERR_UNSUPPORTED; break; }
        if(n>length-at) { err=LISM_INPUT_ERR_TRUNCATED; break; }
        uint32_t v=number(map+at,n);at+=n;unsigned type=(p>>2)&3,tag=p>>4;
        if(type==1) switch(tag) {
        case 0: if(!n || v>65535)err=LISM_INPUT_ERR_MALFORMED;else g.page=v;break;
        case 1: if(!n)err=LISM_INPUT_ERR_MALFORMED;else g.min=sign_value(v,n*8);break;
        case 2: if(!n || (g.min>=0 && v>INT32_MAX))err=LISM_INPUT_ERR_RANGE;else g.max=g.min<0?sign_value(v,n*8):(int32_t)v;break;
        case 3:case 4:case 5:case 6:break;
        case 7:g.size=v;break;
        case 8:if(!n || !v || v>255)err=LISM_INPUT_ERR_REPORT_ID;else {g.id=(uint8_t)v;d->has_report_ids=true;}break;
        case 9:g.count=v;break;
        case 10:if(n || sp==4)err=LISM_INPUT_ERR_LIMIT;else stack[sp++]=g;break;
        case 11:if(n || !sp)err=LISM_INPUT_ERR_MALFORMED;else g=stack[--sp];break;
        default:err=LISM_INPUT_ERR_UNSUPPORTED;
        } else if(type==2) {
            /* Designator and string metadata do not alter report layout or
             * usage mapping. Common vendor/composite maps include them. */
            if(tag==3 || tag==4 || tag==5 || tag==7 || tag==8 || tag==9) {
                if(!n)err=LISM_INPUT_ERR_MALFORMED;
                continue;
            }
            if(tag>2 || !n) {err=LISM_INPUT_ERR_UNSUPPORTED;continue;}
            uint32_t u=n==4?v:(g.page<<16)|v;
            if(tag==0) {if(l.count==LOCAL_MAX)err=LISM_INPUT_ERR_LIMIT;else l.usage[l.count++]=u;}
            else if(tag==1) {l.min=u;l.have_min=true;}else {l.max=u;l.have_max=true;}
        } else if(type==0) {
            if(!local_valid(&l)) {err=LISM_INPUT_ERR_MALFORMED;break;}
            if(tag==10) {
                if(n!=1 || (v>6 && v<0x80) || depth==DEPTH_MAX)err=LISM_INPUT_ERR_MALFORMED;
                else {
                    unsigned mode=depth?modes[depth-1]:MODE_NONE;uint32_t u=first_usage(&l);
                    if(v==1) mode=u==0x10006?MODE_KEYBOARD:u==0x10002?MODE_MOUSE:(u>>16)==12?MODE_CONSUMER:MODE_NONE;
                    if(v==1 && u==0x000d0005)d->capabilities|=LISM_INPUT_CAP_TOUCHPAD;
                    modes[depth++]=(uint8_t)mode;
                    if(!mode)d->capabilities|=LISM_INPUT_CAP_OTHER;
                }
            } else if(tag==12) {if(n || !depth)err=LISM_INPUT_ERR_MALFORMED;else depth--;}
            else if(tag==8 || tag==9 || tag==11) {
                if(!n || !depth)err=LISM_INPUT_ERR_MALFORMED;else err=data_item(d,&g,&l,tag,v,modes[depth-1]);
            } else err=LISM_INPUT_ERR_UNSUPPORTED;
            memset(&l,0,sizeof(l));
        } else err=LISM_INPUT_ERR_UNSUPPORTED;
    }
    if(!err && (depth || sp || l.count || l.have_min || l.have_max))err=LISM_INPUT_ERR_MALFORMED;
    if(!err && !d->report_count)err=LISM_INPUT_ERR_UNSUPPORTED;
    if(!err && d->has_report_ids)for(unsigned i=0;i<d->report_count;i++)if(!d->reports[i].id)err=LISM_INPUT_ERR_REPORT_ID;
    if(err)memset(d,0,sizeof(*d));
    return err;
}
static uint32_t bits(const uint8_t *p,unsigned offset,unsigned size) {
    uint32_t v=0;for(unsigned i=0;i<size;i++)if(p[(offset+i)/8]&(1U<<((offset+i)%8)))v|=UINT32_C(1)<<i;return v;
}
static uint32_t usage_at(const struct lism_input_descriptor *d,const struct lism_input_run *r,uint32_t i) {
    if(r->ranged)return i<=r->usage_max-r->usage_min?r->usage_min+i:0;
    /* HID variable fields repeat the final listed usage if count is larger. */
    if(r->variable && r->usage_count && i>=r->usage_count)i=r->usage_count-1;
    return i<r->usage_count?d->usages[r->usage_offset+i]:0;
}
static bool consumer_add(uint16_t *list,uint8_t *count,uint16_t usage) {
    for(unsigned i=0;i<*count;i++)if(list[i]==usage)return true;
    if(*count==LISM_INPUT_MAX_CONSUMERS)return false;
    list[(*count)++]=usage;return true;
}
static void key_add(struct lism_input_held *h,unsigned code) {
    if(code>=0xe0 && code<=0xe7)h->modifiers|=1U<<(code-0xe0);
    else if(code>=1 && code<=3)h->keyboard_rollover=true;
    else if(code>3 && code<256)h->keys[code/8]|=1U<<(code%8);
}
enum lism_input_error lism_input_decode(const struct lism_input_descriptor *d,struct lism_input_state *s,
    uint8_t id,const uint8_t *payload,size_t length,struct lism_input_event *event) {
    if(!d || !s || !payload || !event)return LISM_INPUT_ERR_ARGUMENT;
    if(d->report_count>LISM_INPUT_MAX_REPORTS || d->run_count>LISM_INPUT_MAX_RUNS || d->usage_count>LISM_INPUT_MAX_USAGES)return LISM_INPUT_ERR_MALFORMED;
    int ri=-1;for(unsigned i=0;i<d->report_count;i++)if(d->reports[i].id==id)ri=(int)i;
    if(ri<0)return LISM_INPUT_ERR_REPORT_ID;
    if(length!=(d->reports[ri].input_bits+7U)/8 || length>LISM_INPUT_MAX_REPORT_BYTES)return LISM_INPUT_ERR_LENGTH;
    if(!d->reports[ri].updates)return LISM_INPUT_ERR_UNSUPPORTED;
    struct lism_input_held held={0};struct lism_input_event e={.updated=d->reports[ri].updates};
    for(unsigned f=0;f<d->run_count;f++) {
        const struct lism_input_run *r=&d->runs[f];if(r->report_index!=ri)continue;
        if(!r->bit_size || r->bit_size>32 || !r->count || r->count>512 ||
            r->bit_offset+(uint32_t)r->bit_size*r->count>length*8 ||
            r->usage_offset+r->usage_count>d->usage_count ||
            (r->ranged && (r->usage_min>r->usage_max || (r->usage_min>>16)!=(r->usage_max>>16))))return LISM_INPUT_ERR_MALFORMED;
        for(unsigned i=0;i<r->count;i++) {
            uint32_t raw=bits(payload,r->bit_offset+i*r->bit_size,r->bit_size);
            int64_t value=r->logical_min<0?sign_value(raw,r->bit_size):(int64_t)raw;
            if(value<r->logical_min || value>r->logical_max) {
                if(r->null_state)continue;
                return LISM_INPUT_ERR_RANGE;
            }
            uint32_t u=usage_at(d,r,r->variable?i:(uint32_t)(value-r->logical_min));
            unsigned page=u>>16,code=u&65535;
            if(r->variable && value==0)continue;
            if(r->mode==MODE_KEYBOARD && page==7)key_add(&held,code);
            else if(r->mode==MODE_CONSUMER && page==12 && code &&
                !consumer_add(held.consumer,&held.consumer_count,(uint16_t)code))return LISM_INPUT_ERR_LIMIT;
            else if(r->mode==MODE_MOUSE) {
                if(page==9 && code>=1 && code<=8)held.buttons|=1U<<(code-1);
                else if(r->relative) {
                    int32_t *axis=page==1&&code==0x30?&e.dx:page==1&&code==0x31?&e.dy:
                        page==1&&code==0x38?&e.wheel:page==12&&code==0x238?&e.pan:NULL;
                    if(axis) {int64_t sum=(int64_t)*axis+value;if(sum<INT32_MIN || sum>INT32_MAX)return LISM_INPUT_ERR_RANGE;*axis=(int32_t)sum;}
                }
            }
        }
    }
    if(held.keyboard_rollover) {
        /* Keep this report's last good keys until a valid report replaces it.
         * Mark output rollover rather than inventing releases or error keys. */
        memcpy(held.keys,s->reports[ri].keys,sizeof(held.keys));
    }
    for(unsigned n=0;n<d->report_count;n++) {
        const struct lism_input_held *h=n==(unsigned)ri?&held:&s->reports[n];
        for(unsigned j=0;j<32;j++)e.keys[j]|=h->keys[j];
        e.modifiers|=h->modifiers;e.buttons|=h->buttons;e.keyboard_rollover|=h->keyboard_rollover;
        if(h->consumer_count>LISM_INPUT_MAX_CONSUMERS)return LISM_INPUT_ERR_MALFORMED;
        for(unsigned j=0;j<h->consumer_count;j++) {
            if(!consumer_add(e.consumer,&e.consumer_count,h->consumer[j]))return LISM_INPUT_ERR_LIMIT;
        }
    }
    s->reports[ri]=held;*event=e;return LISM_INPUT_OK;
}
const char *lism_input_error_string(enum lism_input_error e) {
    static const char *const names[]={"ok","argument","truncated","malformed","limit","unsupported","report ID","length","range"};
    return (unsigned)e<sizeof(names)/sizeof(names[0])?names[e]:"unknown";
}
