# One LisM pair: ESB input with five-profile BLE/USB host

Experimental continuation of the user-confirmed working BLE baseline
`b116964cbc5669bc9acca233c5d0974d1be9cd32`.
The preserved fallback is `baseline/lism-ble-working-20261020`.
Do not move that baseline or overwrite its original UF2 files.

Only one receiver, left digital four-way stick and right PAW3222 keyboard half
are supported. No second keyboard set, extra peripherals, touch UI, standalone
half-to-host BLE, registry or multiple receivers are added.
The original 46-position keymap (200 ms mod-tap, 125 ms prior idle), hardware
definitions and OPERATOR layout remain the baseline versions.

## Radios and profiles

Both halves have BLE disabled and use ESB directly from boot. No Bluetooth
discovery, pairing or GATT commissioning is required between halves and receiver.
The receiver retains USB and host BLE HID, one active BLE connection and five
computer profiles numbered 1–5. Its seven original bond slots are retained for
NVS compatibility, including two legacy half bonds. A generated compatibility
header fixes the actual host-profile count at five without enabling BLE split.
The effective profile array and two-source battery array are checked in the ELF.
The settings partition, existing profile indices and key namespaces are unchanged;
normal firmware does not clear stored settings.

ESB API/envelopes start from
[badjeff/zmk-feature-split-esb](https://github.com/badjeff/zmk-feature-split-esb/tree/314c7cbaf4a74e1add1d6ffc8249de3e29965b8c),
pinned to `314c7cbaf4a74e1add1d6ffc8249de3e29965b8c`.
Upstream currently documents only USB-only operation on Zephyr 4.1. This branch
adds an experimental compatibility adapter for BLE-host coexistence; it is not
an assertion of upstream support or hardware validation.

The receiver uses SDC/MPSL 4 ms requested windows; only MPSL owns RADIO/TIMER0/RTC0
vectors. High-priority callbacks perform hardware operations and atomic/SPSC
copies only. Kernel queues, callbacks, validation and logs execute in a cooperative
thread. Halves own their ESB radio continuously. All three use one address set,
pipes 0/1, 2 Mbps, +8 dBm, 2437 MHz; hopping is disabled for the first hardware
comparison. Addresses are not cryptographic pairing secrets.

## Compatibility and recovery

- Raw driver sources are hash-checked and copied into isolated generated modules.
  Shared ZMK, Nordic SDK and Prospector checkouts are never patched in place.
- TX records retain their own source/pipe and retry payload. Partial frames,
  invalid sources/types/lengths, CRC failures and RX backpressure are checked.
- Wire boot IDs and sequence numbers suppress duplicates. Commands are targeted
  at the current half boot and acknowledged after valid receive-queue insertion.
  Delayed bootloader/reset commands cannot execute on a later half boot.
- Every half sends a 200 ms held-state snapshot/heartbeat. It restores all 46
  positions, right mouse buttons and cached battery percentage after receiver
  restart, and heals dropped key/button releases. Each source has its own 1500 ms
  lease; one half cannot keep the other alive. Physical long holds are maintained
  by heartbeat. Disconnect cleanup preserves the last battery display value.
- Existing battery circles consume the original status and battery events,
  now supplied by actual ESB link evidence. RGB startup and charge colors remain;
  peripheral connectivity comes from ESB ACKs, not a nonexistent BLE connection.

This first-test ESB wire format uses CRC, not encryption, authentication or
cryptographic anti-replay. Boot/sequence checks are duplicate suppression only.
Host BLE keeps the original secure-connections-only policy. RF delivery is not
guaranteed by software tests: a hardware ACK can precede application processing;
overflow or interference can still lose fast taps, relative movement or scroll.
Snapshots repair held state, not lost relative motion. Range, latency, sleep/wake,
interference, simultaneous typing/pointing and BLE coexistence need real testing.

## Build and first test

Use the custom workflow or `tools/build_upstream_esb.py --workspace /workspace
--target left|right|dongle|reset` after initializing west with `config/west.yml`.
The generic baseline build entry points deliberately fail on this experimental
branch, so they cannot silently produce old BLE-split binaries.

Flash all three corresponding `LisM_ESBv2_*` images and restart them. Test USB
output first, then select the original BLE output/profile and test the same
keyboard and pointer inputs. No reset is required for ESB discovery. The separate
settings-reset image erases host pairings/settings and is optional, not a cure
for radio defects. Reflash all three archived original BLE UF2 files to revert.

Verify before delivery: four successful builds, actual configuration/ELF vector
and array checks, UF2 family/address checks, baseline keymap/scroll regressions,
and the production C framing/link/radio host tests under ASan/UBSan.
These tests fake hardware, timing and callbacks; they do not prove RF operation.
