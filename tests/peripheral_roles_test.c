#include <assert.h>
#include <stdio.h>

#include "../src/xiao147_peripheral_roles.h"
#include "../src/xiao147_battery_colors.h"

int main(void)
{
    bool connected[XIAO147_PERIPHERAL_COUNT] = {true, true, true, false};
    const uint8_t battery[] = {92, 62, 32, 18};
    const int8_t rssi[XIAO147_RSSI_COUNT] = {-40, -55, -65, -75, -85};
    assert(XIAO147_RSSI_COUNT == 5);
    assert(xiao147_active_joystick_slot(connected) == 2);
    connected[3] = true;
    assert(xiao147_active_joystick_slot(connected) == 2); /* Stable priority. */
    connected[2] = false;
    uint8_t slot = xiao147_active_joystick_slot(connected);
    assert(slot == 3 && battery[slot] == 18 && rssi[slot + 1] == -85);
    assert(xiao147_battery_color(battery[slot]) == 0xb77979);
    connected[3] = false;
    slot = xiao147_active_joystick_slot(connected);
    assert(!connected[slot]); /* Callers must render unknown, not stale data. */
    connected[2] = true;
    slot = xiao147_active_joystick_slot(connected);
    assert(slot == 2 && battery[slot] == 32 && rssi[slot + 1] == -75);
    assert(xiao147_battery_color(battery[slot]) == 0xc08040);
    assert(xiao147_battery_color(0) == 0xb77979);
    assert(xiao147_battery_color(20) == 0xb77979);
    assert(xiao147_battery_color(21) == 0xc08040);
    assert(xiao147_battery_color(49) == 0xc08040);
    assert(xiao147_battery_color(50) == 0xb4a264);
    assert(xiao147_battery_color(74) == 0xb4a264);
    assert(xiao147_battery_color(75) == 0x54806c);
    assert(xiao147_battery_color(100) == 0x54806c);
    puts("PASS: stable joystick selection, disconnect/fallback data alignment, battery boundaries");
    return 0;
}
