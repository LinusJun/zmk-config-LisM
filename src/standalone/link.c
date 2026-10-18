/* SPDX-License-Identifier: MIT */
#include "mode.h"
#include "../lism_split_role.h"
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/atomic.h>
#include <zmk/split/bluetooth/uuid.h>
#include <zmk/events/split_peripheral_status_changed.h>
#include <zmk/ble.h>
#include "service.h"
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(lism_companion,CONFIG_ZMK_LOG_LEVEL);
#define RIGHT IS_ENABLED(CONFIG_LISM_STANDALONE_RIGHT)
static bt_addr_le_t peer,dongle;
static K_MUTEX_DEFINE(client_lock);
static struct bt_conn *direct_conn;
static bool loaded,identity_ready,advertising;
static atomic_t left_direct,id0_connected,id1_connected;
static uint8_t advertising_id=255,snapshots;
static int64_t left_grace;
static zmk_split_transport_peripheral_status_changed_cb_t status_cb;
extern struct zmk_split_transport_peripheral bt_peripheral;
static void link_work_fn(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(link_work,link_work_fn);
static bt_addr_le_t saved_addr(bool companion) {
    k_mutex_lock(&client_lock,K_FOREVER);bt_addr_le_t addr=companion?peer:dongle;
    k_mutex_unlock(&client_lock);return addr;
}
bool lism_standalone_companion_ready(void) {
    bt_addr_le_t p=saved_addr(true),d=saved_addr(false);
    return bt_addr_le_cmp(&p,BT_ADDR_LE_ANY) && bt_addr_le_cmp(&d,BT_ADDR_LE_ANY);
}
bool lism_standalone_left_addr(const bt_addr_le_t *addr) {
    bt_addr_le_t known=saved_addr(true);
    return bt_addr_le_cmp(&known,BT_ADDR_LE_ANY) && !bt_addr_le_cmp(&known,addr);
}
#if !defined(CONFIG_LISM_STANDALONE_RIGHT)
bool lism_standalone_direct(void) {return atomic_get(&left_direct);}
bool lism_standalone_running(void) {return loaded;}
#endif
bool lism_standalone_host_conn(struct bt_conn *conn) {
    struct bt_conn_info info;bt_addr_le_t known=saved_addr(false);
#if defined(CONFIG_LISM_STANDALONE_RIGHT)
    if(!zmk_ble_active_profile_is_open() &&
       bt_addr_le_cmp(zmk_ble_active_profile_addr(),bt_conn_get_dst(conn)))return false;
    int profile=zmk_ble_profile_index(bt_conn_get_dst(conn));
    if(profile>=0 && profile!=zmk_ble_active_profile_index())return false;
#endif
    return RIGHT && lism_standalone_direct() && lism_standalone_running() &&
        !bt_conn_get_info(conn,&info) && info.role==BT_CONN_ROLE_PERIPHERAL && info.id==0 &&
        bt_addr_le_cmp(bt_conn_get_dst(conn),&known);
}
bool lism_standalone_control_conn(struct bt_conn *conn) {
    struct bt_conn_info info;bt_addr_le_t known=saved_addr(false);
#if defined(CONFIG_LISM_STANDALONE_RIGHT)
    if(zmk_ble_profile_index(bt_conn_get_dst(conn))>=0)return false;
#endif
    return !bt_conn_get_info(conn,&info) && info.role==BT_CONN_ROLE_PERIPHERAL && info.id==0 &&
        (!bt_addr_le_cmp(&known,BT_ADDR_LE_ANY) || !bt_addr_le_cmp(bt_conn_get_dst(conn),&known));
}
bool lism_standalone_pairing_allowed(struct bt_conn *conn) {
    struct bt_conn_info info;
    if(bt_conn_get_info(conn,&info))return false;
    if(info.role==BT_CONN_ROLE_CENTRAL)return RIGHT && lism_standalone_direct() &&
        lism_standalone_left_addr(bt_conn_get_dst(conn));
    if(!RIGHT && info.id==1)return lism_standalone_left_addr(bt_conn_get_dst(conn));
    if(info.id!=0)return false;
    if(RIGHT && lism_standalone_direct())return lism_standalone_host_conn(conn);
    return lism_standalone_control_conn(conn);
}
bool lism_standalone_split_conn(struct bt_conn *conn) {
    struct bt_conn_info info;
    if(bt_conn_get_info(conn,&info) || info.role!=BT_CONN_ROLE_PERIPHERAL ||
       bt_conn_get_security(conn)<BT_SECURITY_L2)return false;
    if(info.id==1)return !RIGHT && lism_standalone_left_addr(bt_conn_get_dst(conn));
    return info.id==0 && (!RIGHT || !lism_standalone_direct()) && lism_standalone_control_conn(conn);
}
static const struct bt_data ad[]={
    BT_DATA_BYTES(BT_DATA_FLAGS,BT_LE_AD_GENERAL|BT_LE_AD_NO_BREDR),
    BT_DATA_BYTES(BT_DATA_UUID128_ALL,ZMK_SPLIT_BT_SERVICE_UUID)};
static const struct bt_data companion_ad[]={
    BT_DATA_BYTES(BT_DATA_FLAGS,BT_LE_AD_GENERAL|BT_LE_AD_NO_BREDR),
    BT_DATA_BYTES(BT_DATA_UUID128_ALL,LISM_COMPANION_UUID)};
static const struct bt_data sd[]={BT_DATA_BYTES(BT_DATA_SVC_DATA128,
    LISM_SPLIT_ROLE_UUID,LISM_SPLIT_ROLE_VERSION,RIGHT?1:0)};
static void first_bond(const struct bt_bond_info *info,void *arg) {
    bt_addr_le_t *out=arg;
#if defined(CONFIG_LISM_STANDALONE_RIGHT)
    if(zmk_ble_profile_index(&info->addr)>=0)return;
#endif
    if(!bt_addr_le_cmp(out,BT_ADDR_LE_ANY))*out=info->addr;
}
void lism_standalone_link_wake(void) {k_work_reschedule(&link_work,K_NO_WAIT);}
static struct zmk_split_transport_status status(void) {
    return (struct zmk_split_transport_status){.available=loaded,.enabled=true,
        .connections=(lism_standalone_direct()?atomic_get(&id1_connected):atomic_get(&id0_connected))?
        ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED:ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_DISCONNECTED};
}
static int set_callback(zmk_split_transport_peripheral_status_changed_cb_t cb) {status_cb=cb;return 0;}
static int set_enabled(bool en) {ARG_UNUSED(en);lism_standalone_link_wake();return 0;}
static int report(const struct zmk_split_transport_peripheral_event *event) {
    /* Keep both snapshots current before handing over. The dongle explicitly
     * ignores the BLE position notifications in ESB firmware. */
    int rc=lism_standalone_esb_report(event);
    int bt=zmk_split_transport_peripheral_bt_report_event(event);
    return lism_standalone_direct()?bt:rc;
}
static const struct zmk_split_transport_peripheral_api api={
    .report_event=report,.set_enabled=set_enabled,.get_status=status,.set_status_callback=set_callback};
/* Same priority as ESB, lexically before it: this is the only data router. */
ZMK_SPLIT_TRANSPORT_PERIPHERAL_REGISTER(bt_peripheral,&api,0);
struct zmk_split_transport_peripheral *zmk_split_transport_peripheral_bt(void) {return &bt_peripheral;}
bool zmk_split_bt_peripheral_is_connected(void) {return status().connections==ZMK_SPLIT_TRANSPORT_CONNECTIONS_STATUS_ALL_CONNECTED;}
bool zmk_split_bt_peripheral_is_bonded(void) {return bt_addr_le_cmp(&dongle,BT_ADDR_LE_ANY);}
static void disconnect_id0(struct bt_conn *conn,void *arg) {
    ARG_UNUSED(arg);if(lism_standalone_control_conn(conn))bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);
}
static void connected(struct bt_conn *conn,uint8_t err) {
    struct bt_conn_info info;
    if(bt_conn_get_info(conn,&info) || info.role!=BT_CONN_ROLE_PERIPHERAL)return;
#if defined(CONFIG_LISM_STANDALONE_RIGHT)
    if(lism_standalone_direct()) {
        lism_standalone_host_adv_consumed();
        if(!err && !lism_standalone_host_conn(conn))bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        lism_standalone_link_wake();return;
    }
#endif
    if(info.id>1)return;
    advertising=false;advertising_id=255;
    if(err){lism_standalone_link_wake();return;}
    if(!lism_standalone_pairing_allowed(conn)) {
        bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);return;
    }
    if(info.id==0)atomic_set(&id0_connected,1);
    bt_conn_set_security(conn,BT_SECURITY_L2);
    lism_standalone_link_wake();
}
static void security_changed(struct bt_conn *conn,bt_security_t level,enum bt_security_err err) {
    struct bt_conn_info info;
    if(RIGHT || bt_conn_get_info(conn,&info) || info.role!=BT_CONN_ROLE_PERIPHERAL || info.id!=1)return;
    if(err || level<BT_SECURITY_L2 || !lism_standalone_left_addr(bt_conn_get_dst(conn))) {
        bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);return;
    }
    if(direct_conn && direct_conn!=conn) {bt_conn_disconnect(conn,BT_HCI_ERR_REMOTE_USER_TERM_CONN);return;}
    if(!direct_conn)direct_conn=bt_conn_ref(conn);
    atomic_set(&id1_connected,1);atomic_set(&left_direct,1);snapshots=5;left_grace=0;
    lism_standalone_esb_enable(false);
    bt_conn_foreach(BT_CONN_TYPE_LE,disconnect_id0,NULL);
    raise_zmk_split_peripheral_status_changed((struct zmk_split_peripheral_status_changed){.connected=true});
    lism_standalone_link_wake();
}
/* Persistent GATT params belong to one referenced ID0 connection until either
 * terminal callback or cancellation from that connection's disconnect. */
static struct bt_uuid_128 peer_uuid=BT_UUID_INIT_128(LISM_COMPANION_UUID);
static struct bt_gatt_discover_params discovery;
static struct bt_gatt_read_params reader;
static struct bt_gatt_write_params writer;
static struct bt_conn *client;
static uint8_t wire[8],read_wire[8],read_len;
static bool pending_peer;
static void finish(void) {if(client){bt_conn_unref(client);client=NULL;}}
static uint8_t read_cb(struct bt_conn *conn,uint8_t err,struct bt_gatt_read_params *params,
                      const void *data,uint16_t len) {
    ARG_UNUSED(params);
    k_mutex_lock(&client_lock,K_FOREVER);
    if(client!=conn){k_mutex_unlock(&client_lock);return BT_GATT_ITER_STOP;}
    if(!err && data && read_len+len<=sizeof(read_wire)) {
        memcpy(read_wire+read_len,data,len);read_len+=len;
        k_mutex_unlock(&client_lock);return BT_GATT_ITER_CONTINUE;
    }
    if(!err && !data && read_len==8 && read_wire[0]==1)pending_peer=true;
    finish();k_mutex_unlock(&client_lock);lism_standalone_link_wake();return BT_GATT_ITER_STOP;
}
static void start_read(struct bt_conn *conn,uint16_t handle) {
    read_len=0;
    reader=(struct bt_gatt_read_params){.func=read_cb,.handle_count=1,.single={.handle=handle}};
    if(bt_gatt_read(conn,&reader))finish();
}
static void write_cb(struct bt_conn *conn,uint8_t err,struct bt_gatt_write_params *params) {
    k_mutex_lock(&client_lock,K_FOREVER);
    if(client==conn){if(err)finish();else start_read(conn,params->handle);}
    k_mutex_unlock(&client_lock);
}
static uint8_t found(struct bt_conn *conn,const struct bt_gatt_attr *attr,struct bt_gatt_discover_params *params) {
    ARG_UNUSED(params);
    k_mutex_lock(&client_lock,K_FOREVER);
    if(client!=conn){k_mutex_unlock(&client_lock);return BT_GATT_ITER_STOP;}
    if(!attr)finish();
    else {
        uint16_t handle=((const struct bt_gatt_chrc*)attr->user_data)->value_handle;
        if(RIGHT)start_read(conn,handle);
        else {
            bt_addr_le_t ids[2];size_t count=2;bt_id_get(ids,&count);
            if(count!=2)finish();
            else {
                wire[0]=1;memcpy(wire+1,&ids[1],sizeof(ids[1]));
                writer=(struct bt_gatt_write_params){.func=write_cb,.handle=handle,.data=wire,.length=8};
                if(bt_gatt_write(conn,&writer))finish();
            }
        }
    }
    k_mutex_unlock(&client_lock);return BT_GATT_ITER_STOP;
}
void lism_standalone_peer_poll(struct bt_conn *control) {
    if(!loaded || !identity_ready || bt_conn_get_security(control)<BT_SECURITY_L2 ||
       !lism_standalone_control_conn(control))return;
    k_mutex_lock(&client_lock,K_FOREVER);
    if(!client && !pending_peer) {
        /* Only an encrypted, ESB-provisioning dongle can establish this owner. */
        const bt_addr_le_t *addr=bt_conn_get_dst(control);
        if(bt_addr_le_cmp(&dongle,addr) && !settings_save_one("lism_link/dongle",addr,sizeof(*addr)))dongle=*addr;
        client=bt_conn_ref(control);
        discovery=(struct bt_gatt_discover_params){.uuid=&peer_uuid.uuid,.func=found,
            .start_handle=BT_ATT_FIRST_ATTRIBUTE_HANDLE,.end_handle=BT_ATT_LAST_ATTRIBUTE_HANDLE,
            .type=BT_GATT_DISCOVER_CHARACTERISTIC};
        if(bt_gatt_discover(client,&discovery))finish();
    }
    k_mutex_unlock(&client_lock);
}
static void disconnected(struct bt_conn *conn,uint8_t reason) {
    ARG_UNUSED(reason);struct bt_conn_info info;
    k_mutex_lock(&client_lock,K_FOREVER);
    if(client==conn) {
        bt_gatt_cancel(conn,&discovery);bt_gatt_cancel(conn,&reader);bt_gatt_cancel(conn,&writer);finish();
    }
    k_mutex_unlock(&client_lock);
    if(bt_conn_get_info(conn,&info) || info.role!=BT_CONN_ROLE_PERIPHERAL)return;
    if(info.id==0 && lism_standalone_control_conn(conn))atomic_clear(&id0_connected);
    if(!RIGHT && info.id==1 && direct_conn==conn) {
        bt_conn_unref(direct_conn);direct_conn=NULL;
        atomic_clear(&id1_connected);left_grace=k_uptime_get()+3000;
        raise_zmk_split_peripheral_status_changed((struct zmk_split_peripheral_status_changed){.connected=false});
    }
    lism_standalone_link_wake();
}
static void recycled(void) {lism_standalone_link_wake();}
BT_CONN_CB_DEFINE(lism_link_callbacks)={.connected=connected,.disconnected=disconnected,
    .security_changed=security_changed,.recycled=recycled};
#if !defined(CONFIG_LISM_STANDALONE_RIGHT)
static enum bt_security_err pairing(struct bt_conn *conn,const struct bt_conn_pairing_feat *const feat) {
    ARG_UNUSED(feat);return lism_standalone_pairing_allowed(conn)?BT_SECURITY_ERR_SUCCESS:BT_SECURITY_ERR_PAIR_NOT_ALLOWED;
}
static struct bt_conn_auth_cb auth={.pairing_accept=pairing};
#endif
static void link_work_fn(struct k_work *work) {
    ARG_UNUSED(work);if(!loaded)return;
    if(!identity_ready) {
        bt_addr_le_t ids[2];size_t count=2;bt_id_get(ids,&count);
        if(!RIGHT && count<2) {int id=bt_id_create(NULL,NULL);if(id!=1)goto retry;}
        identity_ready=true;
        if(!bt_addr_le_cmp(&dongle,BT_ADDR_LE_ANY))bt_foreach_bond(0,first_bond,&dongle);
        lism_standalone_esb_enable(!lism_standalone_direct() && lism_standalone_running());
    }
    k_mutex_lock(&client_lock,K_FOREVER);
    if(pending_peer) {
        bt_addr_le_t next;memcpy(&next,read_wire+1,sizeof(next));
        if(next.type<=BT_ADDR_LE_RANDOM && bt_addr_le_cmp(&next,BT_ADDR_LE_ANY) && bt_addr_le_cmp(&peer,&next)) {
            if(!settings_save_one("lism_link/peer",&next,sizeof(next)))peer=next;
        }
        pending_peer=false;
    }
    k_mutex_unlock(&client_lock);
    if(!RIGHT && left_grace && k_uptime_get()>=left_grace) {
        left_grace=0;atomic_clear(&left_direct);lism_standalone_esb_enable(true);
    }
    if(!RIGHT && snapshots && lism_standalone_direct()) {
        extern int send_position_state(void);send_position_state();snapshots--;
    }
    if(RIGHT && (lism_standalone_direct() || !lism_standalone_running())) {
        advertising=false;advertising_id=255;
#if defined(CONFIG_LISM_STANDALONE_RIGHT)
        if(lism_standalone_running())lism_standalone_host_update();
#endif
        goto retry;
    }
    uint8_t id=0;
    bool companion=!RIGHT && bt_addr_le_cmp(&peer,BT_ADDR_LE_ANY);
    if(companion && !atomic_get(&id1_connected) &&
       (lism_standalone_direct() || atomic_get(&id0_connected) || (k_uptime_get()/1500)%3==2))id=1;
    bool connected_to=id==0?atomic_get(&id0_connected):atomic_get(&id1_connected);
    if(connected_to || (!RIGHT && atomic_get(&id1_connected))) {
        if(advertising)bt_le_adv_stop();
        advertising=false;advertising_id=255;goto retry;
    }
    if(advertising && advertising_id==id)goto retry;
    if(advertising)bt_le_adv_stop();
    advertising=false;
    struct bt_le_adv_param param=*BT_LE_ADV_CONN_FAST_2;param.id=id;
    param.options|=BT_LE_ADV_OPT_USE_IDENTITY;
    bt_addr_le_t target_addr=saved_addr(id==1);
    const bt_addr_le_t *target=&target_addr;
    /* Low duty directed adverts keep old bonds exclusive. Initial ID1 pairing
     * advertises its stable identity; the SMP admission check requires peer. */
    bool bonded=false;
    if(bt_addr_le_cmp(target,BT_ADDR_LE_ANY)) {
        struct bt_conn *c=bt_conn_lookup_addr_le(id,target);if(c)bt_conn_unref(c);
        bt_addr_le_t bond=bt_addr_le_any;bt_foreach_bond(id,first_bond,&bond);
        bonded=!bt_addr_le_cmp(&bond,target);
    }
    int rc;
    if(bonded) {
        param=*BT_LE_ADV_CONN_DIR_LOW_DUTY(target);param.id=id;
        param.options|=BT_LE_ADV_OPT_USE_IDENTITY;
        rc=bt_le_adv_start(&param,NULL,0,NULL,0);
    } else if(id==1)rc=bt_le_adv_start(&param,companion_ad,ARRAY_SIZE(companion_ad),NULL,0);
    else rc=bt_le_adv_start(&param,ad,ARRAY_SIZE(ad),sd,ARRAY_SIZE(sd));
    if(!rc){advertising=true;advertising_id=id;}
    if(status_cb)status_cb(&bt_peripheral,status());
retry:
    k_work_reschedule(&link_work,K_MSEC(300));
}
static int load(const char *name,size_t len,settings_read_cb read,void *arg) {
    bt_addr_le_t *addr=!strcmp(name,"peer")?&peer:!strcmp(name,"dongle")?&dongle:NULL;
    if(!addr)return -ENOENT;
    if(len!=sizeof(*addr))return -EINVAL;
    return read(arg,addr,len)==len?0:-EIO;
}
static int commit(void) {loaded=true;lism_standalone_link_wake();return 0;}
static struct settings_handler handler={.name="lism_link",.h_set=load,.h_commit=commit};
static int init(void) {
    int rc=bt_enable(NULL);if(rc && rc!=-EALREADY)return rc;
#if !defined(CONFIG_LISM_STANDALONE_RIGHT)
    bt_conn_auth_cb_register(&auth);
#endif
    return settings_register(&handler);
}
SYS_INIT(init,APPLICATION,CONFIG_ZMK_BLE_INIT_PRIORITY);
