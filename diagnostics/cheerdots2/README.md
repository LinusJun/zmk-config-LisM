# CheerDots2 descriptor-only probe

This directory contains no device evidence yet. The fixtures in the regression
test are synthetic, not reports captured from a CheerDots2.

After normally connecting the device through macOS Bluetooth settings, run:

```sh
python3 tools/cheerdots_hid_probe.py --output /tmp/cheerdots-descriptors-new.json
```

The probe reads `/usr/sbin/ioreg -r -c IOHIDDevice -l -a`. It never pairs a device,
opens a HID connection, reads live input, writes Feature reports, flashes firmware,
or contacts the network. Output includes only entries named CheerDots in Product
or Manufacturer and their registry descendants. Serial numbers and unrelated
device metadata are not exported. Existing output files are never overwritten.
If no named entry is visible, it exits nonzero; it does not guess which generic
virtual mouse is the target. Do not share a complete unfiltered ioreg snapshot.

An offline plist can be analyzed with `--snapshot /path/to/snapshot.plist` on
any supported Python platform. No hardware access takes place in this mode.

Each descriptor is preserved as raw hex plus SHA-256. The bounded short-item
summary identifies declared top-level collections, report IDs/types/bit lengths,
Digitizer usages, and vendor pages. Unsupported, incomplete, oversized, or
malformed descriptors produce an invalid summary, not forwarding instructions.
Missing descriptors mean unknown capability. Registry transport/class metadata
is labeled as declared or virtual-indicator evidence, never proof that a virtual
driver output is the physical Bluetooth device's report map.

This is not a complete HID parser or a bridge implementation. Digitizer/Finger
declarations do not confirm multiple simultaneously reported touch points; their
absence does not exclude vendor-defined gestures. No real multitouch support is
confirmed by this tool. OS HID descriptors may also differ from a BLE GATT HID
Report Map. Before claiming device-specific or multitouch compatibility, correlate the actual BLE
Report Map, Report References, protocol/security requirements, and independently
verified device report samples with explicit permission.

The separate generic mouse-only experiment is documented in
[HID_BRIDGE_TEST.md](HID_BRIDGE_TEST.md). It parses the live standard BLE Report
Map at runtime; it does not infer a CheerDots vendor format from this probe.

References:

- [Official CheerDots2 product](https://cheerdots.com/products/cheerdots2-air-mouse-voice-recorder)
- [Linux HID report descriptor introduction](https://docs.kernel.org/hid/hidintro.html)

Offline regression:

```sh
python3 tests/cheerdots_hid_probe_test.py
```
