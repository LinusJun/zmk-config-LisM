# LisM standalone joystick peripheral wiring

The firmware intentionally keeps the same pins as the previously successful
`codex/left-joystick-validation` build.

| Joystick signal | XIAO nRF52840 pin | MCU function |
| --- | --- | --- |
| X axis | D4 / P0.04 | AIN2 |
| Y axis | D5 / P0.05 | AIN3 |
| Push switch | D6 / P1.11 | active-low GPIO |
| Supply | 3V3 | 3.3 V only |
| Ground | GND | ground |

D4, D5, and D6 are three consecutive pins on one side of the XIAO. 3V3 and
GND are consecutive on the other side. A single five-pin row cannot provide
both two ADC channels and the required power pins.

Verify the FPC connector orientation and its actual pin order with the
joystick documentation or a continuity meter before applying power. Never
connect the joystick supply to the XIAO 5V pin.
