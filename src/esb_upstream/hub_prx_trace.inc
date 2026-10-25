/* v5.0.4.7 TEST: bounded, read-only private ACK evidence. One IRQ producer,
 * one radio-thread consumer. No key data, allocation, logging or policy edits
 * in the IRQ. Local ACK completion is not proof of remote delivery. */
struct prx_trace_record {
    uint32_t at,grant,epoch,peer,rxseq,ack,txseq;
    uint16_t offset_cycles;
    uint8_t event,length,detail,valid;
};
static struct prx_trace_record prx_ring[32],prx_history[32],prx_snapshot[16];
static atomic_t prx_head,prx_tail,prx_dropped;
static atomic_t prx_payload,prx_empty,prx_done_payload,prx_done_empty,prx_cut,prx_crc,prx_full;
static uint32_t prx_grant_cycle;
static unsigned prx_history_count,prx_history_head,prx_print_count,prx_print_at;
static uint32_t prx_peer,prx_epoch,prx_ack,prx_progress_at,prx_seen_at;
static unsigned prx_report_level;
extern void lism_esb_prx_slot_end_trace(void);
void lism_esb_prx_trace(unsigned event,const uint8_t *in,unsigned in_len,
                       const uint8_t *out,unsigned out_len,unsigned detail) {
    if(grant_link)return;
    if(event==1){if(out_len)atomic_inc(&prx_payload);else atomic_inc(&prx_empty);}
    else if(event==2){if(out_len)atomic_inc(&prx_done_payload);else atomic_inc(&prx_done_empty);}
    else if(event==3){if(!(detail&1))return;atomic_inc(&prx_cut);}
    else if(event==4)atomic_inc(&prx_crc);
    else if(event==5)atomic_inc(&prx_full);
    unsigned h=atomic_get(&prx_head),next=(h+1)%ARRAY_SIZE(prx_ring);
    if(next==(unsigned)atomic_get(&prx_tail)){atomic_inc(&prx_dropped);return;}
    /* On the pinned nRF RTC driver, k_cycle_get_32 only reads the counter and
     * overflow anchor; it never takes timeout_lock. Uptime/conversions belong
     * exclusively to the consumer thread, outside the zero-latency IRQ. */
    uint32_t cycles=k_cycle_get_32();
    struct prx_trace_record r={.at=cycles,.grant=atomic_get(&diag_private_start),.epoch=left_route_epoch,
        .offset_cycles=MIN(cycles-prx_grant_cycle,UINT16_MAX),
        .event=event,.length=out_len,.detail=detail};
    if(in && in_len>=20 && in_len<=64 && !memcmp(in,"LsE3",4)){
        r.peer=get32(in+4);r.rxseq=get32(in+8);r.ack=get32(in+16);
        r.valid=get32(in+12)==links[0].boot;
    }
    /* In particular an empty ACK must never expose an old buffer's seq. */
    if(out && out_len>=20 && out_len<=64 && !memcmp(out,"LsE3",4))r.txseq=get32(out+8);
    prx_ring[h]=r;atomic_set(&prx_head,next);
}
static void prx_trace_poll(void) {
    uint32_t now=k_uptime_get_32();
    if(prx_peer!=links[0].last_boot[0] || prx_epoch!=left_route_epoch){
        prx_peer=links[0].last_boot[0];prx_epoch=left_route_epoch;
        prx_ack=prx_seen_at=0;prx_progress_at=now;prx_report_level=0;
        prx_history_count=prx_history_head=prx_print_count=prx_print_at=0;
    }
    unsigned t;
    while((t=atomic_get(&prx_tail))!=(unsigned)atomic_get(&prx_head)){
        struct prx_trace_record r=prx_ring[t];atomic_set(&prx_tail,(t+1)%ARRAY_SIZE(prx_ring));
        if(r.epoch!=prx_epoch)continue;
        r.at=now-k_cyc_to_ms_floor32(k_cycle_get_32()-r.at);
        prx_history[prx_history_head]=r;prx_history_head=(prx_history_head+1)%ARRAY_SIZE(prx_history);
        if(prx_history_count<ARRAY_SIZE(prx_history))prx_history_count++;
        if(r.event==1 && r.valid && r.peer==prx_peer){
            prx_seen_at=r.at;
            if(r.ack>prx_ack){prx_ack=r.ack;prx_progress_at=r.at;prx_report_level=0;}
        }
    }
    bool pending=links[0].have && links[0].tx.peer==prx_peer &&
        links[0].tx.generation==prx_epoch && left_route_phase==KB_LEFT_COMMIT;
    uint32_t age=now-prx_progress_at;
    unsigned level=age>=1300?2:age>=350?1:0;
    if(!prx_print_count && pending && prx_seen_at && now-prx_seen_at<100 && level>prx_report_level){
        prx_report_level=level;prx_print_count=MIN(prx_history_count,ARRAY_SIZE(prx_snapshot));prx_print_at=0;
        for(unsigned i=0;i<prx_print_count;i++)prx_snapshot[i]=prx_history[(prx_history_head+ARRAY_SIZE(prx_history)-prx_print_count+i)%ARRAY_SIZE(prx_history)];
        LOG_INF("Right PRX47 stall ms=%u peer=%u epoch=%u ack=%u pending=%u prepared=%u empty=%u done=%u done_empty=%u cut=%u bad_crc=%u full=%u drop=%u records=%u",
            age,prx_peer,prx_epoch,prx_ack,get32(links[0].tx.p.data+8),
            (unsigned)atomic_get(&prx_payload),(unsigned)atomic_get(&prx_empty),
            (unsigned)atomic_get(&prx_done_payload),(unsigned)atomic_get(&prx_done_empty),
            (unsigned)atomic_get(&prx_cut),(unsigned)atomic_get(&prx_crc),
            (unsigned)atomic_get(&prx_full),(unsigned)atomic_get(&prx_dropped),prx_print_count);
    }
    /* Freeze the snapshot before printing, at most two fixed records per turn. */
    for(unsigned n=0;n<2 && prx_print_count;n++){
        const struct prx_trace_record *r=&prx_snapshot[prx_print_at++];
        LOG_INF("Right PRX47 t=%u grant=%u epoch=%u us=%u ev=%u len=%u bits=%u peer=%u rx=%u ack=%u tx=%u valid=%u",
            r->at,r->grant,r->epoch,k_cyc_to_us_floor32(r->offset_cycles),r->event,r->length,r->detail,r->peer,r->rxseq,r->ack,r->txseq,r->valid);
        if(prx_print_at==prx_print_count)prx_print_count=prx_print_at=0;
    }
}
