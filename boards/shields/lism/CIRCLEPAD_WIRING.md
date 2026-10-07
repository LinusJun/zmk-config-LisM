# New 3DS XL Circle Pad, four-wire LisM peripheral

This Circle Pad build replaces the JP19 stick peripheral in the already
validated three-peripheral LisM dongle setup. Flash
`lism_circlepad_peripheral_wasd.uf2` to the XIAO inside the Circle Pad
case. Flash the matching `lism_dongle_prospector_operator.uf2` to the
dongle as well, because this version adds virtual Left Shift at position 47 and restores
JP19 Space at position 46. CirclePad itself still has no push input. Existing left and
right peripheral firmware can remain installed. Do not run the JP19 and Circle Pad
peripherals simultaneously against this three-slot dongle build.
If the Circle Pad uses a different XIAO from the paired JP19 unit,
the dongle and Circle Pad may need their saved split pairing cleared
before pairing the new peripheral.

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

## User-observed four-contact order

With the cap facing the viewer and the flex pointing downward, the
owner's top-view observation is **left to right: X, V+, Y, GND**.
Wire those signals to XIAO **D4/A4, 3V3, D5/A5, GND** respectively.
This corresponds to the Phone-Controller electrical pad numbers
**4, 3, 2, 1** in that viewing direction. When looking at solder vias
from the *back* of the flipped flex, the apparent left-to-right order
reverses to **GND, Y, V+, X**. The owner has not yet electrically
confirmed the tiny physical vias; continuity-check them before power.

A photo-based color wiring sheet is saved in the Circle Pad v0.5
workspace under `wiring/CirclePad_4P_XIAO_photo_wiring.png`.

## Behavior

Keep the pad untouched at power-up. The firmware samples a stable
neutral for about 0.64 seconds, then sends held WASD directions through
the dongle: up=W, down=S, left=A, right=D. No press-to-unlock step.
A 4-wire Circle Pad has no physical push-contact output. The shared layout
now reserves position 46 for JP19 Space and 47 for held sprint; CirclePad never
reports the Space position. BSI-10 only switches power.
Current CirclePad radial thresholds are 300 enter / 240 release ADC counts,
with 45-degree sectors and 2-degree boundary hysteresis. Startup calibration
uses 64 stable neutral samples; post-calibration ADC errors/rails release keys
while retaining the accepted center. These guards do not detect every loose contact.
Forward W/WA/WD can hold Left Shift beyond the independent trial sprint radius.
Read the current README_测试说明.txt and SPRINT_POWER_TEST.md before flashing.

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
