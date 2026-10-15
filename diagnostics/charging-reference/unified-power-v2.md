# Unified power telemetry v2

This is a read-only display feature, not a charger controller or safety certification.
The same image supports USB and battery power on each of the five target devices.

## Display contract

- VBUS present with no usable battery evidence: dongle status bar is full and reads
  `USB 100%`. This is a USB source indicator, NOT a measured cell percentage and NOT
  proof of battery absence. Unknown cold USB starts intentionally use this display.
- Battery power: fresh voltage-derived battery percentage, no lightning.
- USB with qualified dynamic battery evidence: voltage-derived percentage; lightning
  only while four fresh 250 ms CHG-low samples and fresh voltage evidence coexist.
- Lightning means **inferred battery connection plus asserted charger status**, not
  a measurement of charging current. A high/error CHG reading removes it promptly;
  high CHG alone does not prove full charge. BQ25101 CHG describes the first charge
  cycle and may not assert on later recharge cycles.
- Peripheral circles and device-page battery icons use the same state. A supported
  peer reporting unknown/expired telemetry does not fall back to misleading BAS
  voltage percentages. Legacy peers retain BAS only and never acquire a fake bolt.
- Charging icons do not control charging. Reaching displayed 100% does not turn off
  a pin, change ISET/TS, or stop the hardware charger.

## Evidence and limitations

Official expansion schematic page 4: J1 BAT+ / VBAT_EXT -> SW1 pin 3; SW1 common
pin 2 -> VBAT / XIAO pin 32. SW1 pin 1 is unconnected. OFF isolates the external
cell in both directions, but USB still powers the board. SW1 has no GPIO feedback.

Sources:
- https://wiki.seeedstudio.com/function_1.47_inch_touch_display_nrf52840/#battery-status
- https://files.seeedstudio.com/wiki/Display_Gadgets/resources/schematic/XIAO%201.47%27%27%20IPS%20Display%20(nRF52840)%20Schematic.pdf
- https://www.ti.com/lit/ds/symlink/bq25101.pdf (8.3.2)

The implementation is an independent bounded state machine informed by the Seeed
voltage-transition method, not a port of its Arduino GPIO/ADC routines.
No new GPIO drive, charger load experiment, current setting or flash declaration.

USB uses the existing serialized ZMK battery work item at 1 Hz. No second ADC
reader is introduced. Four stable samples establish a session reference (not an
absence proof). A fresh downward step >=150 mV followed by four samples within
30 mV for >=3 s qualifies an **inference**, never BATTERY_CONFIRMED. A recent
battery-powered observation (<=5 s) may seed a USB handoff candidate, but new
settled USB samples within 80 mV are required for the same inference. Cached
percentages never cross the VBUS epoch. No candidate is saved to flash.

ADC errors, out-of-observation-range values, stale data (>3.5 s USB), large jumps
(>=100 mV per sample) or power transitions remove usable evidence. Noisy values
are not held as a charging display. A gap in STAT sampling restarts debounce.
The 2.8–4.3 V observation range is NOT a safe charging-voltage specification.

Ambiguities cannot be eliminated in software: a USB-only BAT node and a cell can
have identical voltage/CHG; a stable 150 mV load-induced drop can imitate battery
insertion; disconnecting a nearly full cell may have too small a voltage change.
Static USB+battery boot, small ON transition and low-voltage recovery alone may
remain USB/unknown. Recent battery->USB handoff is still inferred, not direct
contact detection. Do not promise correct lightning for every switch/plug order.
No additional user sampling is required by this implementation, but offline
regressions and compilation are not real-device validation of these thresholds.

## Protocol and safety

Four-byte encrypted private GATT report: version=2, flags, percent, reserved=0.
Flags: USB=1, STAT_VALID=2, STAT_ACTIVE=4, LEVEL_VALID=8,
BATTERY_CONFIRMED(non-USB only)=16, BATTERY_INFERRED(USB only)=32, OBSERVED=64.
Old versions fail closed; update dongle and peripherals together for this feature.
Disconnected, timed-out, malformed or wrong-role peers cannot retain a bolt.

Cell suitability, polarity and allowed charging current still require the actual
230 mAh cell specification; this change does not establish charging safety or
add cell-temperature measurement. Hardware termination/protection is unchanged.
