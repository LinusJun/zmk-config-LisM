"""Synthetic descriptor/registry fixtures only; never accesses live hardware."""
import hashlib
import importlib.util
import json
import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cheerdots_probe", ROOT / "tools/cheerdots_hid_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

# Ordinary three-button relative mouse, no report ID.
MOUSE = bytes.fromhex("05010902a1010901a10005091901290315002501950375018102"
                      "9501750581030501093009311581257f750895028106c0c0")
# Synthetic Digitizer Touch Pad/Finger with Contact Identifier and Contact Count.
TOUCH = bytes.fromhex("050d0905a10185010922a1020951750895018102c00954750895018102c0")
# Keyboard and vendor-defined top-level collections, with all three report types.
COMPOSITE = bytes.fromhex("05010906a10185017508950109008102c0"
                          "0600ff0901a10185027508950109018102090291020903b102c0")


def target(raw=MOUSE, **extra):
    return dict(Product="CheerDots2", Manufacturer="CheerDots", Transport="Bluetooth",
                IOClass="IOHIDDevice", VendorID=123, ProductID=456,
                PrimaryUsagePage=1, PrimaryUsage=2, ReportDescriptor=raw, **extra)


class DescriptorTests(unittest.TestCase):
    def test_mouse(self):
        summary = probe.descriptor_summary(MOUSE)
        self.assertTrue(summary["valid_declaration_summary"])
        self.assertEqual(summary["declared_top_level_applications"],
                         [{"usage_page": 1, "usage": 2, "kind": "mouse"}])
        self.assertEqual(summary["reports"], [{"report_id": 0, "type": "Input", "bits": 24}])
        self.assertFalse(summary["multitouch_confirmed"])
        self.assertIn("does not prove", summary["note"])

    def test_touch_declaration_is_not_confirmation(self):
        summary = probe.descriptor_summary(TOUCH)
        self.assertTrue(summary["valid_declaration_summary"])
        self.assertEqual(summary["declared_top_level_applications"][0]["kind"], "digitizer_touchpad")
        self.assertEqual(summary["digitizer_usage_ids"], [5, 0x22, 0x51, 0x54])
        self.assertFalse(summary["multitouch_confirmed"])
        self.assertEqual(summary["runtime_forwarding_compatibility"], "unknown")

    def test_vendor_and_keyboard(self):
        summary = probe.descriptor_summary(COMPOSITE)
        self.assertTrue(summary["valid_declaration_summary"])
        self.assertEqual(summary["vendor_usage_pages"], [0xFF00])
        self.assertEqual(summary["declared_top_level_applications"][0]["kind"], "keyboard")
        self.assertEqual({r["type"] for r in summary["reports"]}, {"Input", "Output", "Feature"})
        consumer = bytes.fromhex("050c0901a1017508950109018102c0")
        self.assertEqual(probe.descriptor_summary(consumer)["declared_top_level_applications"][0]["kind"],
                         "consumer")

    def test_fail_closed_malformed(self):
        malformed = [b"", b"\x05", b"\xfe\x00\x00", b"\x0c", b"\xc0", b"\xb4", b"\x14" + MOUSE,
                     MOUSE[:-1], MOUSE + b"\xa4", TOUCH.replace(b"\x85\x01", b"\x85\x00"),
                     MOUSE + TOUCH, b"\xa4" * 17, b"\x05\x01\x09\x02\xa1\x01" * 33,
                     b"\x05\x01\x09\x02\xa1\x01\x75\x00\x95\x01\x81\x02\xc0",
                     b"\x05\x01\x09\x02\xa1\x01\x77\xff\xff\xff\xff\x95\x01\x81\x02\xc0",
                     b"\x00" * (probe.MAX_DESCRIPTOR + 1)]
        for raw in malformed:
            with self.subTest(raw=raw[:20]):
                summary = probe.descriptor_summary(raw)
                self.assertFalse(summary["valid_declaration_summary"])
                self.assertNotIn("reports", summary)
                self.assertFalse(summary["multitouch_confirmed"])

    def test_push_pop(self):
        raw = MOUSE[:-2] + bytes.fromhex("a4050d0951750895018102b4") + MOUSE[-2:]
        self.assertTrue(probe.descriptor_summary(raw)["valid_declaration_summary"])


class RegistryTests(unittest.TestCase):
    def test_target_subnodes_privacy_and_virtual_provenance(self):
        unrelated = {"Product": "Private Other Mouse", "SerialNumber": "SECRET", "ReportDescriptor": MOUSE}
        virtual = {"Product": "Mouse", "IOClass": "IOHIDUserDevice", "Transport": "Virtual",
                   "ReportDescriptor": COMPOSITE, "SerialNumber": "TARGET-SECRET"}
        root = target(IORegistryEntryChildren=[{"Product": "Touch Pad", "ReportDescriptor": TOUCH}, virtual])
        result = probe.collect([unrelated, {"Product": "Unrelated Parent", "IORegistryEntryChildren": [root]}])
        self.assertEqual(len(result["devices"]), 3)
        text = json.dumps(result)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("Unrelated", text)
        self.assertNotIn("Private", text)
        self.assertIn("virtual_indicator", result["devices"][2]["source_assessment"])
        self.assertEqual(result["devices"][0]["descriptor"]["raw_hex"], MOUSE.hex())
        self.assertEqual(result["devices"][0]["descriptor"]["sha256"], hashlib.sha256(MOUSE).hexdigest())
        self.assertEqual(result["devices"][1]["target_ancestor_metadata"][0]["metadata"]["Product"], "CheerDots2")

    def test_multiple_roots_missing_descriptor_and_no_false_match(self):
        first = target()
        first.pop("ReportDescriptor")
        result = probe.collect([first, target(TOUCH)])
        self.assertFalse(result["devices"][0]["descriptor"]["available"])
        for product in ("Mouse", "NotCheerDots2", "Cheerful mouse"):
            with self.assertRaises(probe.ProbeError):
                probe.collect([{"Product": product}])

    def test_malformed_trees_and_data(self):
        for snapshot in ({}, [None], [{"IORegistryEntryChildren": {}}],
                         [target(b"x" * (probe.MAX_DESCRIPTOR + 1))]):
            with self.assertRaises(probe.ProbeError):
                probe.collect(snapshot)
        result = probe.collect([target("not binary")])
        self.assertFalse(result["devices"][0]["descriptor"]["available"])
        result = probe.collect([target(b"\x05")])
        self.assertFalse(result["devices"][0]["descriptor"]["summary"]["valid_declaration_summary"])
        self.assertEqual(result["devices"][0]["descriptor"]["raw_hex"], "05")
        nested = target()
        for _ in range(66):
            nested = {"IORegistryEntryChildren": [nested]}
        with self.assertRaises(probe.ProbeError):
            probe.collect([nested])
        with self.assertRaises(probe.ProbeError):
            probe.collect([{} for _ in range(10001)])

    def test_offline_cli_exclusive_create_and_no_hardware(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(probe.subprocess, "run") as run:
            snapshot, output = Path(directory) / "sample.plist", Path(directory) / "result.json"
            snapshot.write_bytes(plistlib.dumps([target(), {"Product": "Private Other Device"}]))
            self.assertEqual(probe.main(["--snapshot", str(snapshot), "--output", str(output)]), 0)
            original = output.read_bytes()
            self.assertEqual(json.loads(original)["source"], "offline_snapshot")
            self.assertEqual(probe.main(["--snapshot", str(snapshot), "--output", str(output)]), 2)
            self.assertEqual(output.read_bytes(), original)
            snapshot.write_bytes(plistlib.dumps([{"Product": "No Match"}]))
            self.assertEqual(probe.main(["--snapshot", str(snapshot)]), 2)
            snapshot.write_bytes(b"invalid plist")
            self.assertEqual(probe.main(["--snapshot", str(snapshot)]), 2)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
