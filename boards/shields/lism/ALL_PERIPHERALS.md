# All LisM peripherals in one build

This branch starts at the successful XIAO 1.47-inch screen build `8bfa820`.
The JP19 overlay/config comes from `codex/third-peripheral-joystick-wasd`
at `b2487f3490943a32c33cbd6ffb3da33efe214e46` (successful run
[37645040595](https://github.com/LinusJun/zmk-config-LisM/actions/runs/37645040595)).
The Circle Pad overlay/config and recorded trace come from
`codex/circlepad-dongle-fourwire` at `df9e1eb56f583dae091c6b368ba523a5520da654`
(successful run [37645072706](https://github.com/LinusJun/zmk-config-LisM/actions/runs/37645072706)).
Their common ADC driver, sector code and charging/LED policy are identical.

One Actions run exports seven normal UF2 files:

- Left trackball keyboard.
- Right trackball keyboard.
- JP19 independent peripheral, including its push switch and existing sprint behavior.
- Four-wire Circle Pad independent peripheral, including its existing sprint behavior.
- Original Prospector OPERATOR dongle.
- New XIAO Plus 1.47-inch touch-display dongle with the current menus and colors.
- Settings reset for XIAO BLE.

The separate USB serial ADC-diagnostic builds are excluded from the matrix.

## Pairing and switching sticks

Both dongles reserve four peripheral pairings and five host profiles. On first
setup, pair the left keyboard, then the right, then the two joysticks in either
order. Existing settings can remain when they already follow this slot order.
If previously established roles differ, rebuild the pairing order deliberately
rather than expecting a firmware update to rearrange saved bonds.

Use one joystick at a time. Ordinary WASD handling in the pinned ZMK version
does not count identical keys by their source, so simultaneous manipulation of
both sticks is outside this combined version's use case. The two sticks have
separate virtual positions (JP19 42..47, Circle Pad 48..53); all original 42
keyboard positions and bindings remain unchanged.

The new screen keeps three peripheral rings/cards. Its third slot is labelled
`JOYSTICK`/`JOY` and follows whichever joystick is connected. If both are online,
the first-paired joystick is preferred consistently for its battery and RSSI.
The two joysticks need not have a fixed JP19/Circle Pad pairing order.

The existing pin mappings, axis transforms, dead zones, calibration, sprint
thresholds and idle polling values are preserved from their tested branches.
See [JP19 wiring](JOYSTICK_WIRING.md) and [Circle Pad wiring](CIRCLEPAD_WIRING.md).
The combined CI keeps both source branches' driver tests, recorded-trace replay,
sector tests and charging/LED tests, plus the mapping and slot-selection checks.
