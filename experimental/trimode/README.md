# LisM runtime three-mode transport experiment

Base: `LinusJun/zmk-config-LisM`, `keymap-editor`,
`140d66bc5db8d2508af70f40aa3ecbe2656f0d25`.

This directory is a **host-tested coordinator prototype**, not an integrated
radio transport, not a UF2 firmware release. It is deliberately absent from
the default CMake and firmware matrix. Existing firmware behavior is unchanged.

## Requested mapping (2026-10-18)

| Host output | Halves to screen central | Screen central to host |
| --- | --- | --- |
| USB | Nordic ESB | USB HID |
| BLE 1–5 | BLE split | BLE HID |
| 2.4G | Nordic ESB | ESB to a separate XIAO nRF52840 USB HID receiver |

The selected all-ESB third mode avoids BLE/ESB coexistence for LisM itself.
Keeping the existing external BLE HID inputs would still require coexistence.
The upstream badjeff split-ESB module's ZMK 0.4 documentation reports BLE build
limitations. A local minimal compile/link probe now succeeds with the pinned
Zephyr 4.1 base, SoftDevice Controller, MPSL and Nordic ESB enabled together.
It uses `CONFIG_NRF_SECURITY=n` to retain Zephyr's built-in Mbed TLS PSA backend
instead of trying to build NCS security against incompatible/missing NCS crypto
modules. Bluetooth SMP remains enabled. This is NOT a security-disable switch.
The probe configures eight BLE connection slots and now compiles BLE advertising
plus ESB TX under short MPSL timeslot requests. It has NOT run on hardware, so
successful compilation proves neither actual radio coexistence nor latency,
power, pairing or successful packet delivery. It is a bench test, not a keyboard
image. Its fixed test address is unrelated to existing LisM pairings.
`coex_probe/verification.json` records the tested dependency revisions.

The existing BLE HID input bridge also shares the BLE host and scanner:
disabling all Bluetooth in USB mode would disconnect those inputs. Their
behavior must be integrated before enabling a firmware target.

## Full-feature baseline controller port check

The original dongle features also compile and link with SoftDevice Controller.
The repository's `ci_firmware.check_build('dongle', build)` configuration and
compiled-symbol checks pass. This is still the ORIGINAL BLE data path, not the
requested ESB product. Flash usage is 671360 bytes, RAM usage 233120 bytes; the
remaining RAM budget must be respected by the new transport.
`full_baseline_verification.json` records the local configuration overrides.
Those overrides are NOT integrated into the production build generator yet.
No baseline-probe UF2 is presented as a finished three-mode firmware.

## Implemented here

Serialized asynchronous transition policy: PREPARE, old endpoint release/TX
drain, radio activation, complete peer state snapshot barrier, resume. Timeouts
enter recovery without claiming the target mode succeeded. Stale transaction
acknowledgements and input are rejected. Mode changes do not alter any bonds or
registration records. New ESB receivers join the post-activation barrier;
they are not required to acknowledge a link that has not yet started.
Physical LisM A uses bits 0/1; B uses 4/5. A+B mixing is
rejected, preserving the existing mutually exclusive set policy. Receiver uses
bit 6; it is not a BLE profile or split slot.

## Still required before flashing

- Shared-radio backend with actual stop/start completion and bounded BLE/ESB
  timeslots, plus compatible Nordic SDK integration at the pinned ZMK revision.
- Control packets, receiver commissioning, device identity/session epochs,
  duplicate suppression, and authenticated peer mapping to existing slots.
- Distributed rendezvous/recovery when a peer misses the final switch message;
  a successful radio start alone must never satisfy `recovered()`.
- Old endpoint key/button release acknowledgement, full key/button snapshots,
  pointer-delta expiry and existing input generation/fence integration.
- ESB receiver USB keyboard, consumer, mouse reports; host silence watchdog,
  resynchronization, USB backpressure, LED feedback and replay protection.
- Screen mode control/status integration and actual four-device builds.
- Hardware validation with both LisM sets and the new receiver. Host policy tests
  cannot prove RF reliability, power usage or seamless handover.

Policy test:

```sh
gcc -std=c11 -Wall -Wextra -Werror -pedantic \
  -fsanitize=address,undefined experimental/trimode/transition.c \
  experimental/trimode/transition_test.c -o /tmp/lism-transition-test
ASAN_OPTIONS=detect_leaks=0 /tmp/lism-transition-test
```

LeakSanitizer is disabled because the execution host restricts `/proc` access;
AddressSanitizer and UndefinedBehaviorSanitizer remain enabled.
