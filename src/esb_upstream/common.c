/* SPDX-License-Identifier: MIT
 * Pinned badjeff envelope format, with one complete record per TX operation.
 * No SDK or kernel operations from zero-latency radio callbacks.
 */
#include "common.h"
#include "link.h"
#include <zephyr/sys/crc.h>
#include <string.h>
BUILD_ASSERT(sizeof(struct esb_command_envelope) + sizeof(struct esb_msg_postfix) +
             LISM_WIRE_HEADER <= CONFIG_ESB_MAX_PAYLOAD_LENGTH);
BUILD_ASSERT(sizeof(struct esb_event_envelope) + sizeof(struct esb_msg_postfix) +
             LISM_WIRE_HEADER <= CONFIG_ESB_MAX_PAYLOAD_LENGTH);
static K_MUTEX_DEFINE(tx_mutex);
void lism_tx_lock(void) { k_mutex_lock(&tx_mutex,K_FOREVER); }
void lism_tx_unlock(void) { k_mutex_unlock(&tx_mutex); }

void zmk_split_esb_tx(struct zmk_split_esb_state *state) {
    lism_tx_lock();
    while (ring_buf_size_get(state->tx_buf) >= sizeof(struct esb_msg_prefix)) {
        struct esb_msg_prefix prefix;
        ring_buf_peek(state->tx_buf,(void *)&prefix,sizeof(prefix));
        size_t frame=sizeof(prefix)+prefix.payload_size+sizeof(struct esb_msg_postfix);
        size_t total=frame+sizeof(struct esb_msg_meta);
        if (frame>CONFIG_ESB_MAX_PAYLOAD_LENGTH-LISM_WIRE_HEADER || memcmp(prefix.magic_prefix,"ZmKe",4)) {
            ring_buf_reset(state->tx_buf); break;
        }
        if (ring_buf_size_get(state->tx_buf)<total) break;
        uint8_t bytes[CONFIG_ESB_MAX_PAYLOAD_LENGTH+sizeof(struct esb_msg_meta)];
        ring_buf_peek(state->tx_buf,bytes,total);
        struct esb_msg_meta meta; memcpy(&meta,bytes+frame,sizeof(meta));
        /* Both upstream envelope kinds carry source as the first payload byte.
         * Routing belongs to each queued record, never the producer's latest pipe. */
        uint8_t pipe=bytes[sizeof(prefix)];
        if(pipe>=2) {ring_buf_reset(state->tx_buf);break;}
        app_esb_data_t packet={.pipe=pipe,.data=bytes,.len=frame,
            .msg_id=meta.msg_id,.max_retry=meta.max_retry};
        if (zmk_split_esb_send(&packet)) break; /* retain the entire record */
        ring_buf_get(state->tx_buf,bytes,total);
    }
    lism_tx_unlock();
}

void zmk_split_esb_cb(app_esb_event_t *event,struct zmk_split_esb_state *state) {
    if (event->evt_type!=APP_ESB_EVT_RX) { zmk_split_esb_tx(state); return; }
    if (event->pipe>=2 || !event->buf || !event->data_length ||
        event->data_length>CONFIG_ESB_MAX_PAYLOAD_LENGTH-LISM_WIRE_HEADER) return;
    struct ring_buf *rx=&state->rx_bufs[event->pipe];
    if (ring_buf_space_get(rx)<event->data_length) return;
    ring_buf_put(rx,event->buf,event->data_length);
    event->accepted=true;
    if (state->process_rx_callback) state->process_rx_callback(event->pipe);
}

int zmk_split_esb_get_item(struct ring_buf *rx,uint8_t *out,size_t capacity) {
    if (ring_buf_size_get(rx)<sizeof(struct esb_msg_prefix)) return -EAGAIN;
    struct esb_msg_prefix p;ring_buf_peek(rx,(void *)&p,sizeof(p));
    size_t frame=sizeof(p)+p.payload_size,total=frame+sizeof(struct esb_msg_postfix);
    if (memcmp(p.magic_prefix,"ZmKe",4) || p.payload_size<2 || frame>capacity ||
        total>CONFIG_ESB_MAX_PAYLOAD_LENGTH-LISM_WIRE_HEADER) { ring_buf_reset(rx);return -EINVAL; }
    if (ring_buf_size_get(rx)<total) return -EAGAIN;
    memset(out,0,capacity);
    ring_buf_get(rx,out,frame);
    struct esb_msg_postfix crc;ring_buf_get(rx,(void *)&crc,sizeof(crc));
    if (crc32_ieee(out,frame)!=crc.crc) return -EINVAL;
    return 0;
}
bool lism_wire_valid(uint8_t pipe,const uint8_t *frame,size_t length) {
    if(pipe>=2 || !frame || length<sizeof(struct esb_msg_prefix)+2+sizeof(struct esb_msg_postfix))return false;
    struct esb_msg_prefix prefix;memcpy(&prefix,frame,sizeof(prefix));
    if(prefix.payload_size+sizeof(prefix)+sizeof(struct esb_msg_postfix)!=length)return false;
#if IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)
    struct esb_event_envelope env={0};
    if(length-sizeof(struct esb_msg_postfix)>sizeof(env))return false;
    memcpy(&env,frame,length-sizeof(struct esb_msg_postfix));return lism_valid_event(pipe,&env);
#else
    struct esb_command_envelope env={0};
    if(pipe!=CONFIG_ZMK_SPLIT_ESB_PERIPHERAL_ID || length-sizeof(struct esb_msg_postfix)>sizeof(env))return false;
    memcpy(&env,frame,length-sizeof(struct esb_msg_postfix));
    if(env.payload.source!=pipe)return false;
    size_t size;const struct zmk_split_transport_central_command *cmd=&env.payload.cmd;
    switch(cmd->type) {
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_POLL_EVENTS:size=0;break;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_PHYSICAL_LAYOUT:size=sizeof(cmd->data.set_physical_layout);break;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_SET_HID_INDICATORS:size=sizeof(cmd->data.set_hid_indicators);break;
    case ZMK_SPLIT_TRANSPORT_CENTRAL_CMD_TYPE_INVOKE_BEHAVIOR:
        if(cmd->data.invoke_behavior.state>1 || !memchr(cmd->data.invoke_behavior.behavior_dev,0,16))return false;
        size=sizeof(cmd->data.invoke_behavior);break;
    default:return false;
    }
    return prefix.payload_size==1+sizeof(cmd->type)+size;
#endif
}
