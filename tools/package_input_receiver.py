"""Fail-closed packaging of the BLE input integration-test build; never flashes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import zipfile

BASE = "c247e1be9a473f1d5147ab5dbf29ec6c1aaa0cdb"
PERIPHERAL = "e6fcd4135b74ffe4d3a964915ecf33541e8e4323"
GIT = shutil.which("git") or "git"
PERIPHERAL_FILES = ("lism_left_4waystick.uf2", "lism_right_trackball.uf2",
                    "lism_jp19.uf2", "lism_circlepad.uf2",
                    "reset_危险备用_默认不要刷/settings_reset-xiao_ble-zmk.uf2")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_child(root, name):
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and ".." not in relative.parts,
            f"Unsafe relative path: {name}")
    path = root / relative
    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
            f"Not a regular contained file: {name}")
    return path


def validate_uf2(data):
    require(data and len(data) % 512 == 0, "UF2 must contain complete 512-byte blocks")
    count = len(data) // 512
    payload = bytearray()
    for i in range(count):
        block = data[i * 512:(i + 1) * 512]
        magic, magic2, flags, address, size, index, total, family = struct.unpack("<8I", block[:32])
        require((magic, magic2, flags, size, index, total, family) ==
                (0x0A324655, 0x9E5D5157, 0x2000, 256, i, count, 0xADA52840),
                f"Unexpected UF2 header/family/block sequence at block {i}")
        require(address == 0x27000 + i * 256 and address + size <= 0xEC000,
                f"Unexpected UF2 application address at block {i}")
        require(struct.unpack("<I", block[-4:])[0] == 0x0AB16F30,
                f"Invalid UF2 trailing magic at block {i}")
        payload.extend(block[32:32 + size])
    return bytes(payload), count


def check_config(text, scoped_legacy=False):
    values = dict(line.split("=", 1) for line in text.splitlines()
                  if line.startswith("CONFIG_") and "=" in line)
    expected = {"CONFIG_LISM_BLE_INPUT_BRIDGE": "y", "CONFIG_LISM_TWO_KEYBOARD_SETS": "y",
                "CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS": "6", "CONFIG_BT_MAX_PAIRED": "14",
                "CONFIG_LISM_BLE_HOST_PROFILES": "6", "CONFIG_LISM_XIAO147_AUTO_TOUCH": "y",
                "CONFIG_BT_SMP_SC_PAIR_ONLY": "y", "CONFIG_BT_SMP_ENFORCE_MITM": "y",
                "CONFIG_BT_SMP_MIN_ENC_KEY_SIZE": "16", "CONFIG_BT_SMP_LOG_LEVEL": "3",
                "CONFIG_BT_KEYS_LOG_LEVEL": "3"}
    for key, value in expected.items():
        if scoped_legacy and key == "CONFIG_BT_SMP_SC_PAIR_ONLY":
            continue
        require(values.get(key) == value, f"Build configuration mismatch: {key}")
    if scoped_legacy:
        require(values.get("CONFIG_BT_SMP_SC_PAIR_ONLY") != "y" and
                values.get("CONFIG_LISM_INPUT_LEGACY_COMPAT") == "y",
                "Per-device legacy policy must be compiled instead of the global-only gate")
        expected["CONFIG_BT_SMP_SC_PAIR_ONLY"] = "n"
        expected["CONFIG_LISM_INPUT_LEGACY_COMPAT"] = "y"
    else:
        require(values.get("CONFIG_LISM_INPUT_LEGACY_COMPAT") != "y",
                "Legacy-capable firmware must use the explicit compatibility package variant")
    for key in ("CONFIG_LISM_XIAO147_RAW_CAPTURE", "CONFIG_LISM_XIAO147_GESTURE_SHADOW",
                "CONFIG_LISM_XIAO147_RAW_GESTURE_HID_TEST", "CONFIG_BT_KEYS_OVERWRITE_OLDEST",
                "CONFIG_BT_SMP_ALLOW_UNAUTH_OVERWRITE", "CONFIG_BT_SMP_LOG_LEVEL_DBG",
                "CONFIG_BT_KEYS_LOG_LEVEL_DBG", "CONFIG_BT_LOG_SNIFFER_INFO"):
        require(values.get(key) != "y", f"Temporary diagnostic mode enabled: {key}")
    return expected


def source_files(root):
    output = subprocess.check_output([GIT, "-C", str(root), "ls-files", "-z", "--cached",
                                      "--others", "--exclude-standard"])
    names = sorted(set(name.decode() for name in output.split(b"\0") if name))
    allowed_dirs = {"src", "include", "config", "boards", "dts", "tests", "tools",
                    "cmake", "diagnostics", ".github", "zephyr", "scripts", "snippets"}
    suffixes = {".c", ".h", ".py", ".cmake", ".conf", ".overlay", ".keymap", ".dtsi",
                ".dts", ".yaml", ".yml", ".json", ".md", ".txt", ".defconfig", ".sh"}
    selected = []
    for name in names:
        path = PurePosixPath(name)
        if any(part in {".git", ".env", "__pycache__", ".aws", ".codex", ".agents"}
               or part.startswith(".env.") for part in path.parts):
            continue
        if len(path.parts) > 1:
            if path.parts[0] not in allowed_dirs:
                continue
        elif not (path.name in {"CMakeLists.txt", "west.yml", "west.yaml", "AGENTS.md",
                               "LICENSE", "Makefile", "XIAO_MENU_FONT_LICENSE.txt"}
                  or path.name.startswith(("Kconfig", "README", "build"))):
            continue
        if path.suffix == ".json" and path.parts[0] != "tests":
            continue
        if path.suffix == ".txt" and path.name not in {"CMakeLists.txt", "XIAO_MENU_FONT_LICENSE.txt"} and not path.name.startswith("README"):
            continue
        if path.suffix not in suffixes and path.name not in {"LICENSE", "Makefile"} and not path.name.startswith(("Kconfig", "CMakeLists", "defconfig")):
            continue
        source = root / path
        if source.is_file() and not source.is_symlink():
            selected.append((name, source.read_bytes()))
    require(any(name == "src/lism_input_host.c" for name, _ in selected), "Missing receiver source snapshot")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dongle", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--variant", choices=("input-receiver", "usr1-navigation", "security-diagnostic", "cheerpod-compat", "universal-pair"), default="input-receiver")
    parser.add_argument("--peripheral-build-root", type=Path)
    parser.add_argument("--matching-peripherals", type=Path,
                        help="Require all packaged non-dongle UF2 bytes to match this prior package")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    require(not args.out.exists(), "Output already exists; choose a fresh directory")
    head = subprocess.check_output([GIT, "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(head == BASE, "Base HEAD changed: explicitly review provenance before packaging")

    old = json.loads((args.previous / "manifest.json").read_text())
    require(old["dongle_commit"] == BASE and old["peripheral_and_reset_commit"] == PERIPHERAL,
            "Previous package provenance does not match expected base")
    old_records = {}
    for record in old["files"]:
        name = record["file"]
        require(name not in old_records, "Duplicate previous manifest path")
        data = safe_child(args.previous, name).read_bytes()
        require(len(data) == record["bytes"] and sha(data) == record["sha256"],
                f"Previous package hash mismatch: {name}")
        validate_uf2(data)
        old_records[name] = record
    require(len(old_records) == 6, "Expected exactly six previous UF2 records")
    for name in PERIPHERAL_FILES:
        require(old_records[name]["source_commit"] == PERIPHERAL, f"Wrong peripheral origin: {name}")

    dongle = args.dongle.read_bytes()
    require(dongle == (args.build / "zephyr/zmk.uf2").read_bytes(), "Dongle differs from selected build output")
    payload, _ = validate_uf2(dongle)
    for marker in (b"BLE Inputs", b"standard HID", b"AXS_AUTO_READY"):
        require(marker in payload, f"Required firmware marker missing: {marker!r}")
    navigation = b"USR1: next" in payload
    universal = args.variant == "universal-pair"
    compatibility = args.variant in ("cheerpod-compat", "universal-pair")
    require(args.variant != "usr1-navigation" or navigation,
            "USR1 navigation marker missing; refusing a stale dongle build")
    if args.variant == "security-diagnostic":
        for marker in (b"Input %u SMP features", b"Secure Connections required (4)",
                       b"Existing bond policy rejected (4)", b"USR1: next"):
            require(marker in payload, f"Security diagnostic marker missing: {marker!r}")
    if compatibility:
        for marker in (b"Legacy BLE pairing", b"Legacy allowed for this device", b"Secure Connections required (4)",
                       b"Legacy disabled; existing bond retained", b"USR1: next"):
            require(marker in payload, f"Per-device compatibility marker missing: {marker!r}")
    for marker in (b"AXS_RAW_READY", b"AXS_SHADOW_READY", b"AXS_GESTURE_READY",
                   b"AXS_RAW_START ", b"AXS_SHADOW_START ", b"AXS_GESTURE_START "):
        require(marker not in payload, f"Temporary sampling window present: {marker!r}")
    config = (args.build / "zephyr/.config").read_text()
    required_config = check_config(config, compatibility)
    if universal:
        require(b"uptime_s_mod65536=%u" in payload,
                "Charging diagnostic client missing; refusing a stale dongle build")
        require(b"lism_display/palette" in payload and b"Bright\x00" in payload,
                "Runtime palette switch missing; refusing a stale dongle build")
        for flag in ("CONFIG_LISM_INPUT_AUTO_PAIR=y", "CONFIG_LISM_AUTO_SPLIT_SETS=y"):
            require(flag in config, f"Automatic pairing configuration missing: {flag}")
        require(args.peripheral_build_root is not None, "New role-tagged peripherals required")
        battery_generated = (args.build / "modules/zmk-config-LisM/lism_zmk_battery.c").read_bytes()
        require(b"lism_charge_note_battery_sample(sample_result)" in battery_generated and
                b"*sample_result = 0;" in battery_generated,
                "Measurement/delivery separation missing from dongle battery owner")
    policy_evidence = {}
    if compatibility:
        generated = args.build / "modules/zmk-config-LisM"
        smp = (generated / "lism_input_zephyr_smp.c").read_text()
        ble = (generated / "lism_hid_zmk_ble.c").read_text()
        for marker in ("static bool lism_input_legacy_permitted", "lism_input_identity_allowed(conn, &req->addr)",
                       "lism_input_legacy_permitted(conn) &&", "return -EACCES;"):
            require(marker in smp, f"Generated SMP policy missing: {marker}")
        require(smp.count("lism_input_legacy_permitted(") >= 18, "Incomplete legacy runtime guard set")
        for marker in ("bool lism_input_protected_identity", "lism_profile_identity_lock",
                       "profiles[idx] = loaded_profile", "&loaded_peripheral"):
            require(marker in ble, f"Protected identity registry missing: {marker}")
        elf = (args.build / "zephyr/zmk.elf").read_bytes()
        for symbol in (b"lism_input_legacy_allowed", b"lism_input_identity_allowed", b"lism_input_protected_identity"):
            require(symbol in elf, f"Compiled policy symbol missing: {symbol!r}")
        kconfig = args.verification / "Kconfig"
        require(kconfig.is_file() and "select BT_SMP_SC_PAIR_ONLY if !LISM_INPUT_LEGACY_COMPAT" in kconfig.read_text(),
                "Missing generated pinned Kconfig evidence")
        cache = (args.build / "CMakeCache.txt").read_text()
        require(any(line.startswith("KCONFIG_ROOT:") and line.split("=", 1)[-1] == str(kconfig)
                    for line in cache.splitlines()), "Build did not use the recorded Kconfig root")
        policy_evidence = {"SMP.generated.c": smp.encode(), "BLE.generated.c": ble.encode(),
                           "Kconfig.generated": kconfig.read_bytes()}
        if universal:
            keys = (generated / "lism_input_zephyr_keys.c").read_bytes()
            require(b'lism_keys_store(keys)' in keys and
                    b'BT_SETTINGS_DEFINE(keys, "keys", lism_keys_set, keys_commit)' in keys,
                    "Canonical key storage adapter not compiled")
            require(b'lism_keys_set' in elf and b'lism_split_auto_observe' in elf,
                    "Automatic registration / safe key loader missing from ELF")
            policy_evidence["KEYS.generated.c"] = keys
            policy_evidence["KEYS.adapter.h"] = (root / "src/lism_input_key_storage.h").read_bytes()
            policy_evidence["BATTERY.generated.c"] = battery_generated
    report_data = (args.verification / "regression-results.json").read_bytes()
    report = json.loads(report_data)
    expected_tests = {p.name for p in (root / "tests").glob("*_test.py")}
    actual_tests = [record["test"] for record in report["results"]]
    test_count = len(expected_tests)
    require(test_count >= 40 and report["program_count"] == test_count and report["all_passed"] is True and
            len(actual_tests) == test_count and len(set(actual_tests)) == test_count and
            set(actual_tests) == expected_tests and all(r["returncode"] == 0 for r in report["results"]),
            "Expected every current regression program to pass")
    build_log = (args.verification / "build.log").read_bytes()
    require(b"Converted to uf2" in build_log and b"zmk.uf2" in build_log,
            "Build log lacks successful UF2 generation")
    sources = source_files(root)
    dirty_names = subprocess.check_output([GIT, "-C", str(root), "status", "--porcelain", "--untracked-files=all"], text=True)

    args.out.mkdir(parents=True, exist_ok=False)
    dongle_name = "lism_dongle_xiao147_" + args.variant.replace("-", "_") + "_TEST.uf2"
    files = [(dongle_name, dongle, BASE, False)]
    files += [(name, safe_child(args.previous, name).read_bytes(), PERIPHERAL,
               name.startswith("reset_")) for name in PERIPHERAL_FILES]
    if universal:
        for side,name in (("left","lism_left_4waystick.uf2"),("right","lism_right_trackball.uf2")):
            build=args.peripheral_build_root/side
            cfg=(build/"zephyr/.config").read_text()
            require("CONFIG_LISM_SPLIT_ROLE_TAG=y" in cfg and
                    f"CONFIG_SHIELD_LISM_{'L' if side=='left' else 'R'}=y" in cfg,
                    f"Wrong {side} peripheral configuration")
            data=(build/"zephyr/zmk.uf2").read_bytes();validate_uf2(data)
            generated_role=build/"modules/zmk-config-LisM/lism_role_peripheral.c"
            require(b"lism_role_scan_response" in generated_role.read_bytes(), "Role response not generated")
            require(b"lism_role_scan_response" in (build/"zephyr/zmk.elf").read_bytes(), "Role response not linked")
            require(b"read_charge_diagnostics" in (build/"zephyr/zmk.elf").read_bytes(),
                    "Encrypted charge diagnostics not linked in " + side)
            require(b"lism_charge_note_battery_sample(sample_result)" in
                    (build/"modules/zmk-config-LisM/lism_zmk_battery.c").read_bytes(),
                    "Measurement/delivery separation missing in " + side)
            files=[(n,data if n==name else d,BASE if n==name else c,r) for n,d,c,r in files]
    if args.matching_peripherals:
        reference = json.loads((args.matching_peripherals / "manifest.json").read_text())
        prior_hashes = {r["file"]: r["sha256"] for r in reference["files"]}
        for name, data, _, _ in files:
            if name == dongle_name:
                continue
            prior = safe_child(args.matching_peripherals, name).read_bytes()
            require(sha(prior) == prior_hashes[name] and data == prior,
                    "Unexpected peripheral/reset change: " + name)
    records = []
    for name, data, commit, reset in files:
        target = args.out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _, blocks = validate_uf2(data)
        records.append({"file": name, "bytes": len(data), "blocks": blocks, "sha256": sha(data),
                        "source_base_commit": commit, "local_modifications": commit == BASE,
                        "reset_clears_target_settings_and_bonds": reset})
    instructions = (root / "diagnostics/INPUT_RECEIVER.md").read_text()
    if args.variant == "security-diagnostic":
        instructions = (root / "diagnostics/SECURITY_COMPATIBILITY.md").read_text() + "\n\n" + instructions
    if compatibility:
        instructions = (root / "diagnostics/CHEERPOD_COMPATIBILITY.md").read_text() + "\n\n" + instructions
    if universal:
        instructions=(root/"diagnostics/UNIVERSAL_PAIRING.md").read_text()
    (args.out / "先读我.md").write_text(instructions.replace("lism_dongle_xiao147_input_receiver_TEST.uf2", dongle_name))
    evidence = args.out / "验证"
    evidence.mkdir()
    (evidence / "regression-results.json").write_bytes(report_data)
    (evidence / "build.log").write_bytes(build_log)
    (evidence / ".config").write_text(config)
    for name, data in policy_evidence.items():
        (evidence / name).write_bytes(data)
    if universal:
        for side in ("left","right"):
            for suffix in ("configure.log","build.log"):
                shutil.copyfile(args.verification/(side+"-"+suffix),evidence/(side+"-"+suffix))
            shutil.copyfile(args.peripheral_build_root/side/"zephyr/.config",evidence/(side+".config"))
            shutil.copyfile(args.peripheral_build_root/side/"modules/zmk-config-LisM/lism_zmk_battery.c",
                            evidence/(side+"-BATTERY.generated.c"))
    for name in actual_tests:
        source = args.verification / (Path(name).stem + ".log")
        require(source.is_file() and source.stat().st_size > 0, f"Missing test log: {source.name}")
        shutil.copyfile(source, evidence / source.name)
    if (args.verification / "configure.log").is_file():
        shutil.copyfile(args.verification / "configure.log", evidence / "configure.log")

    snapshot = args.out / "源码快照.zip"
    with zipfile.ZipFile(snapshot, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sources:
            archive.writestr(name, data)
    with zipfile.ZipFile(snapshot) as archive:
        require(len(archive.namelist()) == len(sources), "Source archive file count mismatch")
        for name, data in sources:
            require(sha(archive.read(name)) == sha(data), f"Source archive mismatch: {name}")

    source_record = {"base_commit": BASE, "working_tree_not_committed": True,
                     "git_status_at_packaging": dirty_names.splitlines(),
                     "source_files": [{"file": n, "sha256": sha(d), "bytes": len(d)} for n, d in sources]}
    (args.out / "source-record.json").write_text(json.dumps(source_record, ensure_ascii=False, indent=2))
    package_files = {str(p.relative_to(args.out)): sha(p.read_bytes())
                     for p in sorted(args.out.rglob("*")) if p.is_file()}
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(),
                "release_type": "new_feature_integration_test_not_hardware_validated",
                "base_commit": BASE, "peripheral_commit": None if universal else PERIPHERAL,
                "left_right_source_base_commit": BASE if universal else PERIPHERAL,
                "left_right_local_modifications": universal,
                "unchanged_auxiliary_peripheral_and_reset_commit": PERIPHERAL,
                "new_commit_created": False, "pushed_to_github": False,
                "device_flashed": False, "bonds_cleared": False,
                "files": records, "uf2_count": 6, "required_build_config": required_config,
                "generic_ble_input_slots": 2, "computer_profiles_capacity": 6,
                "default_visible_computer_profiles": 5, "shared_bond_capacity": 14,
                "bond_full_auto_eviction": False, "normal_update_needs_reset": False,
                "left_right_same_uf2_for_both_sets": True,
                "usr1_navigation": navigation,
                "security_diagnostics_only": args.variant == "security-diagnostic",
                "security_policy_unchanged": not compatibility,
                "explicit_per_identity_legacy_opt_in": compatibility and not universal,
                "legacy_opt_in_default_enabled": universal,
                "automatic_generic_negotiation": universal,
                "automatic_tagged_lism_registration": universal,
                "canonical_key_record_bytes": 52 if universal else None,
                "key_load_may_delete_records": False if universal else None,
                "non_authorized_roles_require_secure_connections": True,
                "security_policy_evidence_sha256": {name: sha(data) for name, data in policy_evidence.items()},
                "cheerpod_pairing_fixed": False,
                "usr1_double_click_interval_ms": 300 if navigation else None,
                "peripherals_reused_unchanged": bool(args.matching_peripherals) or not universal,
                "unchanged_peripherals_reference": str(args.matching_peripherals) if args.matching_peripherals else None,
                "automated_tests_passed": test_count, "native_multitouch_forwarding": False,
                "classic_bluetooth": False, "gamepad_input_forwarding": False,
                "hardware_validation_this_build": "not_performed",
                "charging_detection_hardware_confirmed": False,
                "known_unresolved_hardware": ["Intermittent USB battery detection on first-set left/right requires physical validation; the cause of previous individual events is not proven"],
                "charging_diagnostics_only_not_claimed_fixed": False,
                "charging_recovery_not_hardware_validated": True,
                "battery_measurement_independent_of_delivery": True,
                "battery_recovery": {"immutable_seed_deadline_ms": 7000, "fresh_votes": 4,
                    "minimum_span_ms": 3000, "baseline_delta_mv": 80,
                    "candidate_delta_mv": 30, "hard_jump_mv": 100,
                    "diagnostic_v2_bytes": 18, "normal_state_v2_bytes": 4},
                "layer_text_rgb": {"default_matches_selected_layer_bar": "e0e0e0", "bright_unchanged": "b0b0b0"},
                "palette_switch": {"default": "muted", "settings_key": "lism_display/palette",
                    "save_delay_ms": 2000, "bright_gain": "bounded30_percent",
                    "battery_green_muted": "54806c", "battery_green_bright": "6da68c",
                    "slot_digits_muted": {"selected": "000000", "other": "7b7d93"},
                    "slot_digits_bright": "ffffff", "wpm_rose_unchanged": True},
                "known_unverified_hardware": ["generic BLE keyboards", "CheerPod", "two LisM set switching"],
                "source_snapshot_sha256": sha(snapshot.read_bytes()), "package_files": package_files}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    archive_path = args.out / ("lism-xiao147-" + args.variant + "-integration-TEST.zip")
    entries = [(p.relative_to(args.out).as_posix(), p.read_bytes())
               for p in sorted(args.out.rglob("*")) if p.is_file()]
    with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    with zipfile.ZipFile(archive_path) as archive:
        require(sum(name.endswith(".uf2") for name in archive.namelist()) == 6, "Archive UF2 count mismatch")
        require(len(archive.namelist()) == len(entries), "Archive member count mismatch")
        for name, data in entries:
            require(sha(archive.read(name)) == sha(data), f"Archive SHA256 mismatch: {name}")
    print(json.dumps({"archive": str(archive_path), "sha256": sha(archive_path.read_bytes()),
                      "uf2_count": 6, "tests_passed": test_count, "hardware_tested": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
