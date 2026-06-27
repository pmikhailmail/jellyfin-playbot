#!/usr/bin/env python3
"""
Minimal TV power test for TCL Google TV.

What it does:
- on  -> sends Wake-on-LAN magic packet
- off -> sends Android TV key event via ADB

No Jellyfin logic here by design.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time


def normalize_mac(mac: str) -> bytes:
    cleaned = mac.replace(":", "").replace("-", "").strip()
    if len(cleaned) != 12:
        raise ValueError("MAC must have 12 hex digits")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError("MAC contains non-hex characters") from exc


def send_wol(mac: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> None:
    mac_bytes = normalize_mac(mac)
    packet = b"\xff" * 6 + mac_bytes * 16

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.sendto(packet, (broadcast_ip, port))
    finally:
        sock.close()


def run_adb(adb_path: str, args: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [adb_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def adb_connect_with_retries(
    adb_path: str,
    target: str,
    attempts: int = 3,
    timeout: int = 15,
    retry_delay: int = 2,
) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            conn = run_adb(adb_path, ["connect", target], timeout=timeout)
            conn_out = conn.stdout.strip() or conn.stderr.strip()
            if conn_out:
                print(f"ADB connect attempt {attempt}/{attempts}: {conn_out}")
        except subprocess.TimeoutExpired:
            print(f"WARN: ADB connect attempt {attempt}/{attempts} timed out after {timeout}s.")
            if attempt < attempts:
                time.sleep(retry_delay)
            continue

        # Verify the transport is really usable, not just a stale connect message.
        try:
            state = run_adb(adb_path, ["-s", target, "get-state"], timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"WARN: ADB get-state attempt {attempt}/{attempts} timed out after {timeout}s.")
            if attempt < attempts:
                time.sleep(retry_delay)
            continue

        if state.returncode == 0 and state.stdout.strip() == "device":
            return True

        if attempt < attempts:
            time.sleep(retry_delay)

    return False


def power_off_via_adb(ip: str, adb_port: int, timeout: int = 15) -> bool:
    adb_path = shutil.which("adb")
    if not adb_path:
        print("ERROR: adb is not found in PATH.")
        print("Install Android platform-tools and ensure adb is available.")
        return False

    target = f"{ip}:{adb_port}"
    if not adb_connect_with_retries(adb_path, target, attempts=3, timeout=timeout):
        print("ERROR: Could not establish ADB connection after retries.")
        return False

    # Prefer explicit sleep instead of power toggle to avoid accidental turn-on.
    sleep_cmd = run_adb(
        adb_path,
        ["-s", target, "shell", "input", "keyevent", "223"],
        timeout=timeout,
    )

    if sleep_cmd.returncode != 0:
        print("WARN: KEYCODE_SLEEP failed, trying POWER toggle (26).")
        toggle_cmd = run_adb(
            adb_path,
            ["-s", target, "shell", "input", "keyevent", "26"],
            timeout=timeout,
        )
        ok = toggle_cmd.returncode == 0
        if not ok:
            print(toggle_cmd.stdout.strip() or toggle_cmd.stderr.strip())
    else:
        ok = True

    run_adb(adb_path, ["disconnect", target], timeout=timeout)
    return ok


def wake_display_and_open_home_via_adb(ip: str, adb_port: int, timeout: int = 15) -> bool:
    adb_path = shutil.which("adb")
    if not adb_path:
        print("ERROR: adb is not found in PATH.")
        print("Install Android platform-tools and ensure adb is available.")
        return False

    target = f"{ip}:{adb_port}"
    if not adb_connect_with_retries(adb_path, target, attempts=3, timeout=timeout):
        print("ERROR: Could not establish ADB connection after retries.")
        return False

    wake_cmd = run_adb(
        adb_path,
        ["-s", target, "shell", "input", "keyevent", "224"],
        timeout=timeout,
    )
    if wake_cmd.returncode != 0:
        print("WARN: KEYCODE_WAKEUP failed, trying POWER toggle (26).")
        wake_cmd = run_adb(
            adb_path,
            ["-s", target, "shell", "input", "keyevent", "26"],
            timeout=timeout,
        )

    home_cmd = run_adb(
        adb_path,
        ["-s", target, "shell", "input", "keyevent", "3"],
        timeout=timeout,
    )

    ok = wake_cmd.returncode == 0 and home_cmd.returncode == 0
    if not ok:
        print(home_cmd.stdout.strip() or home_cmd.stderr.strip() or wake_cmd.stdout.strip() or wake_cmd.stderr.strip())

    run_adb(adb_path, ["disconnect", target], timeout=timeout)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="TV power on/off test script")
    parser.add_argument("action", choices=["on", "off"], help="Power action")
    parser.add_argument("--ip", required=True, help="TV IP address")
    parser.add_argument("--mac", required=True, help="TV MAC address (for WoL)")
    parser.add_argument("--adb-port", type=int, default=5555, help="ADB TCP port")
    parser.add_argument(
        "--wait",
        type=int,
        default=0,
        help="Optional wait seconds after WoL before ADB wake/home",
    )
    args = parser.parse_args()

    if args.action == "on":
        try:
            send_wol(args.mac)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 2
        print("Wake-on-LAN packet sent.")
        if args.wait > 0:
            time.sleep(args.wait)
        ok = wake_display_and_open_home_via_adb(args.ip, args.adb_port)
        if ok:
            print("Display wake and HOME command sent.")
            return 0

        print("Failed to wake display/open HOME via ADB after WoL.")
        return 1

    ok = power_off_via_adb(args.ip, args.adb_port)
    if ok:
        print("Power-off command sent.")
        return 0

    print("Failed to send power-off command.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
