# New 3DS XL Circle Pad, four-wire LisM peripheral

This Circle Pad build replaces the JP19 stick peripheral in the already
validated three-peripheral LisM dongle setup. Flash
`lism_circlepad_peripheral_wasd.uf2` to the XIAO inside the Circle Pad
case. Use the matching `lism_dongle_prospector_operator.uf2` only when
updating the dongle as well; the left/right peripheral images and the
four-direction logical row is used; this branch has no joystick press key. Do not run the JP19 and Circle Pad
peripherals simultaneously against this three-slot dongle build.

## Electrical map and evidence

| Phone-Controller symbol/PCB pad | Signal | XIAO nRF52840 |
| --- | --- | --- |
| 1 | GND | GND |
| 2 | Y analog | D5 / P0.05 / AIN3 |
| 3 | 3.3 V supply | 3V3 |
| 4 | X analog | D4 / P0.04 / AIN2 |

The Phone-Controller project uses New 3DS XL sticks. Its KiCad
`Lib_controller:3DS_stick` symbol assigns pin 1=GND, 2=Y, 3=3V3,
4=X; the two PCB stick footprints carry those same nets. Its
Arduino firmware reads each X/Y output with `analogRead`. These
sources verify that project's **electrical numbering**; they do not
prove which exposed solder hole is pin 1 on your trimmed flex/gray tail.
Confirm the physical hole order by tracing the flex or probing continuity
before soldering. Do not infer left-to-right order from a photo, and
never connect the stick to XIAO 5V.

References:
- https://github.com/Inertie-Production/Phone-Controller
- https://github.com/Inertie-Production/Phone-Controller/blob/main/PCB/pcb_phone_controller/pcb_phone_controller.kicad_sch
- https://github.com/Inertie-Production/Phone-Controller/blob/main/PCB/pcb_phone_controller/pcb_phone_controller.kicad_pcb
- https://bitbuilt.net/forums/threads/wiring-up-3ds-sticks-to-your-controller.173/

The BitBuilt thread labels its image as a 3DS nub-to-GameCube-C-stick
diagram. It is useful background on resistive stick signals, but is
**not** the pin-number authority for this four-pin New 3DS XL Circle Pad.

## Behavior

Keep the pad untouched at power-up. The firmware samples a stable
neutral for about 0.64 seconds, then sends held WASD directions through
the dongle: up=W, down=S, left=A, right=D. No press-to-unlock step.
A 4-wire Circle Pad has no push-contact output. The layout contains only
W, S, A and D for this stick; there is no fifth `SPACE` position.
BSI-10 only switches power.
The Circle Pad thresholds (90 press, 55 release ADC counts) and
100..3400 neutral acceptance are first-fit values. The Phone-Controller
firmware uses 12-bit values 143..880 as initial stick endpoint defaults,
which motivated the lower neutral bound here; those are not measurements
of your own pad. ADC errors, rail readings, or unstable startup readings
release directions and trigger a fresh center calibration.

If directions are reversed after the first USB/BLE test, the
`joystick_kscan` node in `lism_circlepad.overlay` supports the boolean
properties `invert-x;`, `invert-y;`, and `swap-axes;`. Rebuild after
changing them; do not swap a power wire to fix an axis direction.

## Hardware check before connecting the XIAO

With USB and battery disconnected, identify the four pad contacts
and verify there is no short between supply and ground. First power
from XIAO 3V3 and measure both ADC outputs while centering and moving
the pad. Both should change continuously and remain within 0..3.3 V.
If your part has a different pin order, stop and re-map the wires before
testing firmware. The original 3DS console's operating rail does not
by itself establish the rating or exact pad order of a replacement
module; this branch follows the Phone-Controller 3.3 V circuit and
still needs a physical first fit.
