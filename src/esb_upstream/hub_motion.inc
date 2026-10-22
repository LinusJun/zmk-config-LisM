/* Only unsent consecutive packed-XY reports may merge. A reliable event
 * flushes this tail first. Selected/retried frames are immutable. */
static struct hub_tx latest_motion;
static bool motion_pending;
static uint32_t motion_merged,motion_expired;
static bool motion_event(const struct hub_tx *tx,struct esb_event_envelope *env) {
    if(tx->p.pipe!=1)return false;
    memset(env,0,sizeof(*env));
    size_t n=tx->p.length-LISM_WIRE_HEADER-sizeof(struct esb_msg_postfix);
    if(n>sizeof(*env))return false;
    memcpy(env,tx->p.data+LISM_WIRE_HEADER,n);
    return env->payload.event.type==ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_INPUT_EVENT &&
        env->payload.event.data.input_event.reg==1 && env->payload.event.data.input_event.type==INPUT_EV_REL &&
        env->payload.event.data.input_event.code==INPUT_REL_Z && env->payload.event.data.input_event.sync==1;
}
static bool motion_old(const struct hub_tx *tx) {
    struct esb_event_envelope e;
    return tx->generation!=(uint32_t)atomic_get(&uplink_generation) || (motion_event(tx,&e) && k_uptime_get()-tx->since>40);
}
static bool merge_motion(struct hub_tx *a,const struct hub_tx *b) {
    struct esb_event_envelope x,y;
    if(a->generation!=b->generation || !motion_event(a,&x) || !motion_event(b,&y))return false;
    uint32_t av=x.payload.event.data.input_event.value,bv=y.payload.event.data.input_event.value;
    int32_t hi=(int16_t)(av>>16)+(int32_t)(int16_t)(bv>>16);
    int32_t lo=(int16_t)av+(int32_t)(int16_t)bv;
    if(hi<INT16_MIN || hi>INT16_MAX || lo<INT16_MIN || lo>INT16_MAX)return false;
    x.payload.event.data.input_event.value=(uint32_t)(uint16_t)hi<<16 | (uint16_t)lo;
    size_t n=a->p.length-LISM_WIRE_HEADER-sizeof(struct esb_msg_postfix);
    memcpy(a->p.data+LISM_WIRE_HEADER,&x,n);
    struct esb_msg_postfix crc={.crc=crc32_ieee(a->p.data+LISM_WIRE_HEADER,n)};
    memcpy(a->p.data+LISM_WIRE_HEADER+n,&crc,sizeof(crc));motion_merged++;return true;
}
static int flush_motion(void) {
    if(!motion_pending)return 0;
    if(motion_old(&latest_motion)){motion_pending=false;motion_expired++;return 0;}
    int rc=k_msgq_put(&dongle_tx,&latest_motion,K_NO_WAIT);
    if(!rc)motion_pending=false;
    return rc;
}
