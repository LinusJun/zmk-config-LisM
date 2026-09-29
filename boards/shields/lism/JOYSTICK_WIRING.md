# LisM standalone joystick peripheral wiring

The peripheral uses the D4/D5/D6 wiring validated with the standalone USB
WASD firmware on the user's Switch 5P stick.

| Joystick signal | XIAO nRF52840 pin | MCU function |
| --- | --- | --- |
| X axis | D4 / P0.04 | AIN2 |
| Y axis | D5 / P0.05 | AIN3 |
| Push switch | D6 / P1.11 | active-low GPIO |
| Supply | 3V3 | wiring tested with the user's current stick |
| Ground | GND | ground |

D4, D5, and D6 are three consecutive pins on one side of the XIAO. 3V3 and
GND are consecutive on the other side. A single five-pin row cannot provide
both two ADC channels and the required power pins.

Verify the FPC connector orientation and its actual pin order with the
joystick documentation or a continuity meter before applying power. Never
connect the joystick supply to the XIAO 5V pin.

## Automatic startup calibration

On every joystick power-up, leave the stick centered and untouched for about
one second. The driver collects 64 samples at 10 ms intervals and accepts a
stable center away from the ADC rails. If the stick is moving or disconnected,
it retries automatically; directions stay released until calibration passes.
WASD then works immediately, with no one-second press or manual unlock.
Push sends Space independently, including during calibration. Each direction
is held while deflected and released on return to neutral; host key repeat is
normal while a direction is held. Diagonals hold two direction keys.

The activation/release thresholds are 150/90 ADC counts, matching the passed
USB test. ADC read errors or rail readings release directions and restart
calibration. These guards do not detect every possible loose contact.

## Firmware files

- Dongle: `lism_dongle_prospector_operator.uf2`
- Joystick XIAO: `lism_joystick_peripheral_wasd.uf2`
- Left/right trackball images and settings-reset are also built in this package.

If the left/right halves are already running the three-peripheral package
from Actions #154, this update changes no matrix positions or pairing layout.
Flash the dongle and joystick first; retain existing bonds. Use settings-reset
only if a separate pairing recovery is actually needed.

## Tested 5P breakout wiring

For the photographed connector orientation (FPC contacts facing into the socket),
the breakout labels map as follows. This is not a universal FPC numbering rule.

| Breakout pad | Wire | Signal | XIAO |
| --- | --- | --- | --- |
| 1 | Yellow | X | D4 |
| 2 | White | GND | GND |
| 3 | Purple | Push | D6 |
| 4 | Blue | Y | D5 |
| 5 | Red | Supply | 3V3 for the tested stick |
| 6 | Unused | - | - |
