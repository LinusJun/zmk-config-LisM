#include <zephyr/bluetooth/bluetooth.h>
#include <esb.h>
#include <mpsl_timeslot.h>
extern void lism_probe_slots_start(void);
int main(void) {
    int rc = bt_enable(NULL);
    if (rc) return rc;
    /* This probe advertises BLE while requesting ESB windows. It does NOT
     * implement a split keyboard, commissioning or a USB HID receiver. */
    rc = bt_le_adv_start(BT_LE_ADV_CONN_FAST_1, NULL, 0, NULL, 0);
    if (!rc) lism_probe_slots_start();
    return rc;
}
