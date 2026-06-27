#!/usr/bin/env python3
"""
Ensure TV is on, then open any Android TV app package.

This script reuses WoL/ADB helpers from tv_power_test.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import shutil
import socket
import sys
import time

# Ensure local imports work when script is executed via wrapper tools.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tv_power_test import run_adb, send_wol


def can_reach_adb(ip: str, adb_port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((ip, adb_port)) == 0


def adb_is_device(adb_path: str, target: str, timeout: int = 15) -> bool:
    try:
        connect = run_adb(adb_path, ["connect", target], timeout=timeout)
        connect_msg = (connect.stdout or connect.stderr).strip()
        if connect_msg:
            print(connect_msg)

        devices = run_adb(adb_path, ["devices"], timeout=timeout)
    except subprocess.TimeoutExpired:
        print("WARN: adb connect timed out; TV is not ready yet.")
        return False

    for line in devices.stdout.splitlines():
        if line.strip().startswith(target) and "device" in line and "offline" not in line:
            return True
    return False


def wait_for_adb(adb_path: str, ip: str, adb_port: int, max_wait: int, step_seconds: int = 2) -> bool:
    target = f"{ip}:{adb_port}"
    deadline = time.time() + max_wait

    while time.time() < deadline:
        if can_reach_adb(ip, adb_port) and adb_is_device(adb_path, target):
            return True
        time.sleep(step_seconds)
    return False


def is_app_installed(adb_path: str, target: str, package_name: str, timeout: int = 20) -> bool:
    try:
        probe = run_adb(
            adb_path,
            ["-s", target, "shell", "pm", "path", package_name],
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False

    output = (probe.stdout or "") + (probe.stderr or "")
    return probe.returncode == 0 and "package:" in output


def suggest_installed_packages(adb_path: str, target: str, hint: str, timeout: int = 30) -> list[str]:
    try:
        listing = run_adb(
            adb_path,
            ["-s", target, "shell", "pm", "list", "packages"],
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return []
    if listing.returncode != 0:
        return []

    hint_lower = hint.lower()
    result: list[str] = []
    for line in listing.stdout.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        pkg = line.replace("package:", "", 1)
        if hint_lower in pkg.lower():
            result.append(pkg)
    return result[:10]


def launch_app(adb_path: str, target: str, package_name: str, timeout: int = 15) -> bool:
    launch = run_adb(
        adb_path,
        [
            "-s",
            target,
            "shell",
            "monkey",
            "-p",
            package_name,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        timeout=timeout,
    )

    output = (launch.stdout or launch.stderr).strip()
    if output:
        print(output)

    return launch.returncode == 0


def is_app_in_foreground(adb_path: str, target: str, package_name: str, timeout: int = 20) -> bool:
    focused = get_foreground_package(adb_path, target, timeout=timeout)
    return bool(focused and focused.lower() == package_name.lower())


def extract_package_from_line(line: str) -> str | None:
    # Extract package from patterns like com.app/.MainActivity or com.app/com.app.MainActivity.
    match = re.search(r"([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)\/[a-zA-Z0-9_.$]+", line)
    if match:
        return match.group(1)
    return None


def get_foreground_package(adb_path: str, target: str, timeout: int = 20) -> str | None:
    commands = [
        ["-s", target, "shell", "dumpsys", "window", "windows"],
        ["-s", target, "shell", "dumpsys", "activity", "activities"],
    ]

    # Prefer strict focus/resume lines; avoid scanning the whole dump for package substrings.
    preferred_markers = (
        "mCurrentFocus",
        "mFocusedApp",
        "topResumedActivity",
        "mResumedActivity",
    )

    for cmd in commands:
        try:
            probe = run_adb(adb_path, cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            continue

        text = (probe.stdout or "") + (probe.stderr or "")
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not any(marker in line for marker in preferred_markers):
                continue
            package = extract_package_from_line(line)
            if package:
                return package

    return None


def get_wakefulness(adb_path: str, target: str, timeout: int = 15) -> str | None:
    try:
        probe = run_adb(
            adb_path,
            ["-s", target, "shell", "dumpsys", "power"],
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None

    output = ((probe.stdout or "") + (probe.stderr or "")).splitlines()
    for line in output:
        line = line.strip()
        if line.startswith("mWakefulness="):
            return line.split("=", 1)[1].strip()
    return None


def wake_display_and_home_via_adb(adb_path: str, target: str, timeout: int = 15, attempts: int = 2) -> bool:
    for attempt in range(1, attempts + 1):
        print(f"Wake attempt {attempt}/{attempts}: sending WAKEUP (224) and HOME (3)...")

        try:
            wake = run_adb(adb_path, ["-s", target, "shell", "input", "keyevent", "224"], timeout=timeout)
        except subprocess.TimeoutExpired:
            print("WARN: WAKEUP command timed out.")
            continue

        if wake.returncode != 0:
            print("WARN: KEYCODE_WAKEUP failed, trying POWER toggle (26).")
            try:
                wake = run_adb(adb_path, ["-s", target, "shell", "input", "keyevent", "26"], timeout=timeout)
            except subprocess.TimeoutExpired:
                print("WARN: POWER fallback command timed out.")
                continue

        try:
            home = run_adb(adb_path, ["-s", target, "shell", "input", "keyevent", "3"], timeout=timeout)
        except subprocess.TimeoutExpired:
            print("WARN: HOME command timed out.")
            continue

        wakefulness = get_wakefulness(adb_path, target, timeout=timeout)
        if wakefulness:
            print(f"Current wakefulness: {wakefulness}")

        if wake.returncode == 0 and home.returncode == 0 and wakefulness != "Asleep":
            return True

        time.sleep(1)

    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure TV is on and open a TV app")
    parser.add_argument("--ip", required=True, help="TV IP address")
    parser.add_argument("--mac", required=True, help="TV MAC address (for WoL)")
    parser.add_argument("--app-package", required=True, help="Android app package name to launch")
    parser.add_argument("--adb-port", type=int, default=5555, help="ADB TCP port")
    parser.add_argument(
        "--max-wait",
        type=int,
        default=90,
        help="Max seconds to wait for ADB to become ready after wake",
    )
    parser.add_argument(
        "--post-wake-delay",
        type=int,
        default=10,
        help="Extra seconds to wait after wake before launching app",
    )
    args = parser.parse_args()

    adb_path = shutil.which("adb")
    if not adb_path:
        print("ERROR: adb is not found in PATH.")
        print("Install Android platform-tools and ensure adb is available.")
        return 2

    target = f"{args.ip}:{args.adb_port}"

    if adb_is_device(adb_path, target):
        print("TV already on and ADB-ready. Skipping wake step.")
    else:
        print("TV is not ADB-ready. Sending Wake-on-LAN...")
        try:
            send_wol(args.mac)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 2

        if not wait_for_adb(adb_path, args.ip, args.adb_port, args.max_wait):
            print("Failed: TV did not become ADB-ready in time.")
            return 1
        print("TV is awake and ADB-ready.")
        if args.post_wake_delay > 0:
            print(f"Waiting {args.post_wake_delay}s for TV UI to settle...")
            time.sleep(args.post_wake_delay)

    print("Attempting to wake display and open HOME...")
    if wake_display_and_home_via_adb(adb_path, target):
        print("Display wake and HOME command sent.")
    else:
        print("WARN: Could not confirm display wake/HOME; continuing with app checks.")

    if not is_app_installed(adb_path, target, args.app_package):
        print(f"App package is not installed: {args.app_package}")
        hint = args.app_package.split(".")[-1]
        suggestions = suggest_installed_packages(adb_path, target, hint)
        if suggestions:
            print("Similar installed packages:")
            for pkg in suggestions:
                print(f"- {pkg}")
        else:
            print("No similar installed packages found.")
        return 3

    focused_before = get_foreground_package(adb_path, target, timeout=20)
    if focused_before:
        print(f"Detected foreground package before launch: {focused_before}")
    else:
        print("Detected foreground package before launch: <unknown>")

    in_foreground = is_app_in_foreground(adb_path, target, args.app_package)
    if in_foreground:
        print(f"{args.app_package} appears to be running; requesting foreground focus...")
    else:
        print(f"{args.app_package} is not in foreground; launching app...")

    if launch_app(adb_path, target, args.app_package):
        print("Launch command sent (foreground requested).")
    else:
        print("Failed to send app launch command.")
        return 1

    time.sleep(1)
    focused_after = get_foreground_package(adb_path, target, timeout=20)
    if focused_after:
        print(f"Detected foreground package after launch: {focused_after}")
    else:
        print("Detected foreground package after launch: <unknown>")

    if is_app_in_foreground(adb_path, target, args.app_package):
        print("App is now in foreground.")
        return 0

    print("WARN: Launch command sent but app is still not detected in foreground.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
