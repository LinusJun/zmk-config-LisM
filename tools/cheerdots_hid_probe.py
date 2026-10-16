#!/usr/bin/env python3
"""Read registry descriptors only; never open HID devices or read input events."""
import argparse
import hashlib
import json
import plistlib
import re
import subprocess
import sys
from pathlib import Path

MAX_SNAPSHOT = 16 * 1024 * 1024
MAX_DESCRIPTOR = 16384
METADATA = ("Product", "Manufacturer", "Transport", "IOClass", "VendorID",
            "ProductID", "PrimaryUsagePage", "PrimaryUsage")
TARGET = re.compile(r"\bcheer[\s_-]*dots(?:[\s_-]*2)?\b", re.I)


class ProbeError(ValueError):
    pass


def descriptor_summary(data):
    """Bounded declaration summary, NOT a runtime report decoder or compatibility test."""
    try:
        if not data or len(data) > MAX_DESCRIPTOR:
            raise ProbeError("empty or oversized descriptor")
        pos, page, report_id, size, count = 0, 0, 0, 0, 0
        globals_stack, collections, applications, reports = [], [], [], {}
        usages, usage_min, usage_max = [], None, None
        vendor_pages, digitizer_usages = set(), set()
        item_count = 0
        while pos < len(data):
            item_count += 1
            if item_count > 8192:
                raise ProbeError("item limit exceeded")
            prefix = data[pos]
            pos += 1
            if prefix == 0xFE:
                raise ProbeError("long items are unsupported")
            length = (0, 1, 2, 4)[prefix & 3]
            if pos + length > len(data):
                raise ProbeError("truncated short item")
            value = int.from_bytes(data[pos:pos + length], "little")
            pos += length
            kind, tag = (prefix >> 2) & 3, prefix >> 4
            if kind == 3:
                raise ProbeError("reserved item type")
            if kind == 1:
                if tag <= 9 and not length:
                    raise ProbeError("empty global value")
                if tag == 0:
                    if length not in (1, 2) or value > 0xFFFF:
                        raise ProbeError("invalid usage page")
                    page = value
                    if page >= 0xFF00:
                        vendor_pages.add(page)
                elif tag == 7:
                    size = value
                elif tag == 8:
                    if length != 1 or not 1 <= value <= 255:
                        raise ProbeError("invalid report ID (explicit zero is forbidden)")
                    report_id = value
                elif tag == 9:
                    count = value
                elif tag == 10:
                    if length or len(globals_stack) >= 16:
                        raise ProbeError("invalid or too deeply nested global Push")
                    globals_stack.append((page, report_id, size, count))
                elif tag == 11:
                    if length or not globals_stack:
                        raise ProbeError("unmatched global Pop")
                    page, report_id, size, count = globals_stack.pop()
                elif tag not in (1, 2, 3, 4, 5, 6):
                    raise ProbeError("unsupported global item")
            elif kind == 2:
                if tag in (0, 1, 2):
                    if not length:
                        raise ProbeError("empty usage")
                    usage = (value >> 16, value & 0xFFFF) if length == 4 else (page, value)
                    if usage[0] >= 0xFF00:
                        vendor_pages.add(usage[0])
                    if usage[0] == 0x0D:
                        digitizer_usages.add(usage[1])
                    if tag == 0:
                        usages.append(usage)
                        if len(usages) > 1024:
                            raise ProbeError("local usage limit exceeded")
                    elif tag == 1:
                        usage_min = usage
                    else:
                        usage_max = usage
                else:
                    raise ProbeError("unsupported local item")
            else:
                if tag == 10:
                    if length != 1 or value > 6 or len(collections) >= 32:
                        raise ProbeError("invalid or too deeply nested collection")
                    usage = usages[0] if usages else (page, 0)
                    if value == 1 and not collections:
                        applications.append({"usage_page": usage[0], "usage": usage[1]})
                    collections.append((value, usage))
                elif tag == 12:
                    if length or not collections:
                        raise ProbeError("unmatched End Collection")
                    collections.pop()
                elif tag in (8, 9, 11):
                    if not length or not collections or not 1 <= size <= 32 or not 1 <= count <= 1024:
                        raise ProbeError("invalid report field or missing collection")
                    if (usage_min is None) != (usage_max is None):
                        raise ProbeError("incomplete usage range")
                    if usage_min and (usage_min[0] != usage_max[0] or usage_min[1] > usage_max[1]):
                        raise ProbeError("invalid usage range")
                    key = (report_id, {8: "Input", 9: "Output", 11: "Feature"}[tag])
                    reports[key] = reports.get(key, 0) + size * count
                    if reports[key] > 8192:
                        raise ProbeError("report bit limit exceeded")
                else:
                    raise ProbeError("unsupported main item")
                usages, usage_min, usage_max = [], None, None
        if collections or globals_stack:
            raise ProbeError("unclosed collection or global Push")
        ids = {key[0] for key in reports}
        if 0 in ids and len(ids) > 1:
            raise ProbeError("mixed unnumbered and numbered reports")
        if not reports:
            raise ProbeError("no report fields")
        standard = {(1, 2): "mouse", (1, 6): "keyboard", (0x0C, 1): "consumer",
                    (0x0D, 5): "digitizer_touchpad"}
        return {"valid_declaration_summary": True,
                "declared_top_level_applications": [dict(a, kind=standard.get(
                    (a["usage_page"], a["usage"]), "other")) for a in applications],
                "reports": [{"report_id": rid, "type": typ, "bits": bits}
                            for (rid, typ), bits in sorted(reports.items())],
                "vendor_usage_pages": sorted(vendor_pages),
                "digitizer_usage_ids": sorted(digitizer_usages),
                "multitouch_confirmed": False,
                "runtime_forwarding_compatibility": "unknown",
                "note": "Declarations only. Vendor reports may carry gestures or touch points; "
                        "absence of Digitizer declarations does not prove absence of multitouch."}
    except ProbeError as exc:
        return {"valid_declaration_summary": False, "error": str(exc),
                "multitouch_confirmed": False, "runtime_forwarding_compatibility": "unknown"}


def metadata(node):
    return {k: node[k] for k in METADATA
            if k in node and isinstance(node[k], (str, int)) and not isinstance(node[k], bool)}


def provenance(meta):
    if "userdevice" in str(meta.get("IOClass", "")).lower() or meta.get("Transport") == "Virtual":
        return "virtual_indicator_not_physical_device_evidence"
    if str(meta.get("Transport", "")).lower() in ("bluetooth", "bluetooth low energy", "ble"):
        return "registry_declares_bluetooth_not_independent_physical_verification"
    return "unknown_physical_or_virtual"


def collect(snapshot):
    if not isinstance(snapshot, list):
        raise ProbeError("snapshot root must be an IORegistry plist array")
    result, visited = [], 0

    def walk(node, ancestors, target_parent=None, depth=0):
        nonlocal visited
        visited += 1
        if depth > 64 or visited > 10000 or not isinstance(node, dict):
            raise ProbeError("invalid registry tree or traversal limit exceeded")
        meta = metadata(node)
        direct = any(TARGET.search(str(meta.get(k, ""))) for k in ("Product", "Manufacturer"))
        context = meta if direct else target_parent
        if context is not None:
            record = {"metadata": meta, "match": "name" if direct else "target_descendant",
                      "source_assessment": provenance(meta),
                      "target_ancestor_metadata": ancestors}
            raw = node.get("ReportDescriptor")
            if raw is None:
                record["descriptor"] = {"available": False, "capabilities": "unknown"}
            elif not isinstance(raw, bytes):
                record["descriptor"] = {"available": False, "error": "ReportDescriptor is not plist data",
                                        "capabilities": "unknown"}
            elif len(raw) > MAX_DESCRIPTOR:
                raise ProbeError("target descriptor exceeds size limit")
            else:
                record["descriptor"] = {"available": True, "byte_length": len(raw),
                    "raw_hex": raw.hex(), "sha256": hashlib.sha256(raw).hexdigest(),
                    "summary": descriptor_summary(raw)}
            result.append(record)
        children = node.get("IORegistryEntryChildren", [])
        if not isinstance(children, list):
            raise ProbeError("invalid registry children")
        next_ancestors = ancestors + [dict(metadata=meta, source_assessment=provenance(meta))] if context is not None else []
        for child in children:
            walk(child, next_ancestors, context, depth + 1)

    for root in snapshot:
        walk(root, [])
    if not result:
        raise ProbeError("No CheerDots-named HID registry entry found. Connect the device normally "
                         "in Bluetooth settings first; a custom driver may expose only an unnamed virtual device.")
    return {"schema_version": 1, "collection_method": "IORegistry descriptor snapshot only; no HID input events",
            "selection": "CheerDots name in Product/Manufacturer and its registry descendants",
            "multitouch_confirmed": False, "devices": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Read an offline ioreg plist instead of live registry")
    parser.add_argument("--output", type=Path, help="Create JSON exclusively (never overwrite existing files)")
    args = parser.parse_args(argv)
    try:
        if args.snapshot:
            with args.snapshot.open("rb") as stream:
                raw = stream.read(MAX_SNAPSHOT + 1)
        else:
            if sys.platform != "darwin":
                raise ProbeError("Live probing requires macOS; use --snapshot for offline analysis")
            run = subprocess.run(["/usr/sbin/ioreg", "-r", "-c", "IOHIDDevice", "-l", "-a"],
                                 capture_output=True, check=True, timeout=20)
            raw = run.stdout
        if len(raw) > MAX_SNAPSHOT:
            raise ProbeError("snapshot exceeds size limit")
        result = collect(plistlib.loads(raw))
        result["source"] = "offline_snapshot" if args.snapshot else "macos_ioreg_live"
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
        else:
            print(text, end="")
        return 0
    except (ProbeError, OSError, ValueError, OverflowError, RecursionError, plistlib.InvalidFileException,
            subprocess.SubprocessError) as exc:
        print(f"CheerDots descriptor probe: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
