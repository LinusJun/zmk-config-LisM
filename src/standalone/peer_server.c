/* SPDX-License-Identifier: MIT */
#include "mode.h"
#include "../lism_split_sets.h"
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/settings/settings.h>
#include <zephyr/kernel.h>
/* The identity is bound to its registered left owner, not just an A/B index.
 * Replacing a role can never silently inherit the previous set's companion. */
struct companion { bt_addr_le_t owner, direct; };
static struct companion peers[2];
static K_MUTEX_DEFINE(lock);
static struct bt_uuid_128 uuid=BT_UUID_INIT_128(LISM_COMPANION_UUID);
static int source_for(struct bt_conn *conn) {
    int slot=lism_split_sets_find_addr(bt_conn_get_dst(conn));
    if(slot!=0 && slot!=1 && slot!=4 && slot!=5)return -1;
    if(bt_conn_get_security(conn)<BT_SECURITY_L2 ||
       !lism_split_sets_dispatch(slot,lism_split_sets_generation(slot)))return -1;
    return slot;
}
static ssize_t read_peer(struct bt_conn *conn,const struct bt_gatt_attr *attr,
                        void *buf,uint16_t len,uint16_t offset) {
    int slot=source_for(conn);uint8_t wire[8]={1};bt_addr_le_t addr,owner;
    if(slot<0)return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);
    unsigned set=slot>=4, left=set?4:0;
    k_mutex_lock(&lock,K_FOREVER);
    bool ok=lism_split_sets_read_addr(left,&owner)==0;
    if(slot&1) {
        ok=ok && !bt_addr_le_cmp(&owner,&peers[set].owner) &&
            bt_addr_le_cmp(&peers[set].direct,BT_ADDR_LE_ANY);
        addr=peers[set].direct;
    } else ok=ok && lism_split_sets_read_addr(left+1,&addr)==0;
    k_mutex_unlock(&lock);
    if(!ok)return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    memcpy(wire+1,&addr,sizeof(addr));
    return bt_gatt_attr_read(conn,attr,buf,len,offset,wire,sizeof(wire));
}
static ssize_t write_peer(struct bt_conn *conn,const struct bt_gatt_attr *attr,
            const void *buf,uint16_t len,uint16_t offset,uint8_t flags) {
    ARG_UNUSED(attr);ARG_UNUSED(flags);
    int slot=source_for(conn);const uint8_t *wire=buf;
    if(slot!=0 && slot!=4)return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);
    if(offset || len!=8 || wire[0]!=1)return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    struct companion next={.owner=*bt_conn_get_dst(conn)};
    memcpy(&next.direct,wire+1,sizeof(next.direct));
    if(!bt_addr_le_cmp(&next.direct,BT_ADDR_LE_ANY) || next.direct.type>BT_ADDR_LE_RANDOM)
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    unsigned set=slot>=4;int rc=0;
    k_mutex_lock(&lock,K_FOREVER);
    if(memcmp(&peers[set],&next,sizeof(next))) {
        rc=settings_save_one(set?"lism_companion/b":"lism_companion/a",&next,sizeof(next));
        if(!rc)peers[set]=next;
    }
    k_mutex_unlock(&lock);
    return rc?BT_GATT_ERR(BT_ATT_ERR_UNLIKELY):len;
}
BT_GATT_SERVICE_DEFINE(lism_companion_service,
    BT_GATT_PRIMARY_SERVICE(&uuid),
    BT_GATT_CHARACTERISTIC(&uuid.uuid,BT_GATT_CHRC_READ|BT_GATT_CHRC_WRITE,
        BT_GATT_PERM_READ_ENCRYPT|BT_GATT_PERM_WRITE_ENCRYPT,read_peer,write_peer,NULL));
static int load(const char *name,size_t len,settings_read_cb read,void *arg) {
    int i=!strcmp(name,"a")?0:!strcmp(name,"b")?1:-1;
    if(i<0)return -ENOENT;
    if(len!=sizeof(peers[i]))return -EINVAL;
    return read(arg,&peers[i],len)==len?0:-EIO;
}
SETTINGS_STATIC_HANDLER_DEFINE(lism_companion,"lism_companion",NULL,load,NULL,NULL);
