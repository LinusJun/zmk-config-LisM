# Original Prospector: single-set ESB test

This branch starts at `codex/original-dongle-left-4waystick`, commit
`86bf1e95803c07381bde194825dce18c7ecf8dbb`. Its original 46-position matrix,
left digital four-way stick, right PAW3222, Prospector OPERATOR screen,
normal RGB indicators and charge LED policy are retained.

Requested keymap differences: mod-tap is 200 ms with 125 ms prior idle;
Base stick is Up/Right/Down/Left, Nav stick continuously scrolls
Down/Left/Up/Right. Other original key/layer/combo bindings remain unchanged.
No JP19/CirclePad placeholder positions are added.

Keyboard and pointer input use authenticated AES-CCM ESB. Encrypted split BLE
connections are still required for commissioning, session keys and lifecycle.
There is only one left/right pair and one receiver. There is no standalone
host BLE on either half, touch UI, extra display page, multi-set registry,
second receiver, generic BLE input bridge or charge-status telemetry.
Normal battery percentages and original single-page status remain.

## Build

Use **Build original Prospector ESB test** in GitHub Actions, or initialize a
west workspace using `config/west.yml`, then run:

```
python3 tools/build_esb.py --workspace /path/to/workspace --target left
python3 tools/build_esb.py --workspace /path/to/workspace --target right
python3 tools/build_esb.py --workspace /path/to/workspace --target dongle
python3 tools/build_esb.py --workspace /path/to/workspace --target reset
```

Use the three `LisM_Original_*_ESB.uf2` files under `artifacts/`. The old generic
Make/build.yaml entry points are retained for history and are not the ESB build
entry point. No build script flashes hardware. `settings_reset_XIAO_nRF52840.uf2` is a separate image: flashing and running it clears that board's pairings and saved settings. Normal images explicitly reject reset-on-start.

## Integration details and checks

- Original module/GPIO definitions stay at the base revision. All moving
  dependencies are pinned; Nordic SDK/nrfxlib are added for ESB/SoftDevice.
- The original charge LED's GPIO callback is deferred to work context, keeping
  its visual policy while avoiding a sleeping call inside its ISR.
- An active scan's first connectable split advertisement and its role scan
  response are joined by address within five seconds. Duplicate filtering may
  deliver each only once; the response must trigger connection immediately.
  Role-only, nonconnectable, wrong-address, expired or malformed reports cannot
  supply that evidence. Known addresses retain their slots; new roles never
  replace them. New addresses remain volatile until encrypted discovery ready.
- Generation fencing and queued keyboard/mouse releases are preserved. A stale
  ready callback cannot provision a later connection.
- Only MPSL owns RADIO/TIMER0/RTC0 vectors. UF2 target family, contiguous
  application addresses, actual configuration and feature symbols are checked.
- `tests/esb_scan_test.py` executes the generated scanner, including the exact
  one-ADV/one-SCAN_RSP first-pair case and an old-logic negative control.
- `tests/esb_single_set_test.py` compiles the actual generated allocator and
  policy with ASan/UBSan. It checks role ordering, rollback, stale generations,
  existing bindings and held-key cleanup. Protocol/peripheral tests execute
  actual C with fake queues and BLE callbacks, not RF hardware.

These are candidate test images. Compilation and simulated callbacks do not
establish real RF range, hardware reconnect, display behavior or bootloader
handoff. An earlier reduced build failed first-pair enrollment in hardware;
that scanner omission is explicitly covered here. The separately reported
UF2 automatic reboot symptom has not yet been established as the same cause.
Do not repeatedly clear settings to work around firmware defects.
