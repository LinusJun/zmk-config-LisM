# CirclePad sprint and power trial, 2026-10-07

## Flashing
Flash the matching new dongle firmware together with the selected peripheral.
Use normal WASD peripheral firmware for wireless daily testing. ADC logging adds
USB serial diagnostics but still sends game keys through the dongle.
No settings reset is needed merely to change these bindings.

## Game input
W / WA / WD holds Left Shift beyond the configured 50% ADC radius. Shift releases
below 45%, on neutral, non-forward direction, disabled scanning or invalid ADC.
Direction keys remain held continuously. This is not a macro.
Space position 46 is reserved by the shared protocol; sprint is position 47.
CirclePad keeps accepted orientation, 300/240 radial threshold and 2-degree sector
hysteresis.
The 1400-count full radius in the CirclePad overlay is an independently editable TRIAL
estimate, not measured mechanical travel. Current 50% is 700 ADC counts, not proven
exact physical half travel. Confirm straight W and forward diagonals using a
key-state tester before gaming. Center must release all game keys. Power on with
the stick naturally centered for initial center calibration.

## Wireless idle
After five minutes of continuous valid neutral, scanning slows from 10 to 50 ms;
motion restores normal scanning. BLE remains connected. System OFF is disabled.
Held keys, calibration and invalid ADC prevent idle scanning. First movement
may add roughly 40 ms scan delay. No measured battery-life gain is claimed.

## Standard XIAO nRF52840 charging and RGB
Verified against Seeed original BQ25101 schematic. P0.13 stays input without pull
for about 50 mA application charge configuration. Firmware does not control it
before initialization or in the bootloader. BQ25101 handles CC/CV and termination.
Fixed TS resistor does not measure cell temperature; no new cell temperature or
overdischarge protection is added. Retain battery protection and charging ratings.
Dedicated hardware CHG LED remains charger-owned and distinct from RGB.
RGB: brief blue at boot; CHG active with USB power shows battery estimate red
(<20% or unknown), yellow (20..79%), green (>=80%, NOT charge complete). Normally
off unplugged; brief red warning at <=10% battery events. Battery measurement
keeps the board's original open-drain GPIO behavior.
