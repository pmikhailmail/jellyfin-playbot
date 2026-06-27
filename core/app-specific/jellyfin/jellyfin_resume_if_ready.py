#!/usr/bin/env python3
"""
Jellyfin-specific script.

Behavior:
1) Checks that TV is reachable via ADB.
2) Does NOT wake TV if unavailable.
3) Ensures Jellyfin is in foreground (launches it if needed).
4) Sends resume/play key event (best effort).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

# Reuse shared ADB helper from the base power script.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tv_power_test import run_adb


def jellyfin_api_json(server_url: str, token: str, path: str, params: dict[str, str] | None = None) -> dict | list:
    query = ""
    if params:
        query = "?" + urlencode(params)
    url = f"{server_url.rstrip('/')}{path}{query}"
    req = Request(
        url,
        headers={
            "X-Emby-Token": token,
            "Accept": "application/json",
            "User-Agent": "jellyfin-resume-if-ready/1.0",
        },
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def jellyfin_api_post(server_url: str, token: str, path: str, params: dict[str, str] | None = None) -> int:
    query = ""
    if params:
        query = "?" + urlencode(params)
    url = f"{server_url.rstrip('/')}{path}{query}"
    req = Request(
        url,
        data=b"",
        headers={
            "X-Emby-Token": token,
            "Content-Type": "application/json",
            "User-Agent": "jellyfin-resume-if-ready/1.0",
        },
        method="POST",
    )
    with urlopen(req, timeout=30) as resp:
        return resp.status


def pick_best_remote_session(sessions: list[dict], preferred_client: str = "Jellyfin Android TV") -> dict | None:
    candidates: list[dict] = []
    for session in sessions:
        if session.get("Client") != preferred_client:
            continue
        if not session.get("SupportsRemoteControl"):
            continue
        capabilities = session.get("Capabilities") or {}
        if not capabilities.get("SupportsMediaControl"):
            continue
        playable = session.get("PlayableMediaTypes") or []
        if "Video" not in playable:
            continue
        candidates.append(session)

    if not candidates:
        return None

    # Pick freshest active session.
    candidates.sort(key=lambda s: s.get("LastActivityDate") or "", reverse=True)
    return candidates[0]


def play_item_via_sessions_api(server_url: str, token: str, item_id: str) -> bool:
    try:
        sessions_payload = jellyfin_api_json(server_url, token, "/Sessions")
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"WARN: Could not query Jellyfin sessions API: {exc}")
        return False

    if not isinstance(sessions_payload, list):
        print("WARN: Unexpected /Sessions payload format.")
        return False

    session = pick_best_remote_session(sessions_payload)
    if not session:
        print("WARN: No remote-controllable Jellyfin Android TV session found.")
        return False

    session_id = session.get("Id")
    if not session_id:
        print("WARN: Selected session has no Id.")
        return False

    print(f"Using Jellyfin remote session: {session_id}")
    try:
        status = jellyfin_api_post(
            server_url,
            token,
            f"/Sessions/{session_id}/Playing",
            params={
                "ItemIds": item_id,
                "PlayCommand": "PlayNow",
                "StartPositionTicks": "0",
            },
        )
        print(f"Sessions API PlayNow status: {status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"WARN: Failed to send PlayNow via Sessions API: {exc}")
        return False

    # Verify that target session now has the requested item in NowPlaying.
    # Jellyfin Android TV may apply PlayNow asynchronously; avoid false negatives.
    for _ in range(6):
        time.sleep(0.8)
        try:
            sessions_after = jellyfin_api_json(server_url, token, "/Sessions")
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"WARN: Could not verify playback state via Sessions API: {exc}")
            return False

        if not isinstance(sessions_after, list):
            continue

        current = next((s for s in sessions_after if s.get("Id") == session_id), None)
        if not current:
            continue

        now_item = (current.get("NowPlayingItem") or {}).get("Id")
        if now_item == item_id:
            title = (current.get("NowPlayingItem") or {}).get("Name")
            if title:
                print(f"Sessions API playback confirmed: {title}")
            return True

    print("WARN: Sessions API command accepted, but NowPlaying did not update to requested item.")
    return False


def adb_device_state(adb_path: str, target: str, timeout: int = 15) -> str | None:
    try:
        connect = run_adb(adb_path, ["connect", target], timeout=timeout)
        msg = (connect.stdout or connect.stderr).strip()
        if msg:
            print(msg)

        devices = run_adb(adb_path, ["devices"], timeout=timeout)
    except subprocess.TimeoutExpired:
        print("WARN: adb connect timed out.")
        return None

    for line in devices.stdout.splitlines():
        line = line.strip()
        if line.startswith(target):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
            return None
    return None


def is_jellyfin_foreground(adb_path: str, target: str, package_name: str, timeout: int = 20) -> bool:
    commands = [
        ["-s", target, "shell", "dumpsys", "window", "windows"],
        ["-s", target, "shell", "dumpsys", "activity", "activities"],
    ]

    for cmd in commands:
        try:
            probe = run_adb(adb_path, cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
        out = ((probe.stdout or "") + (probe.stderr or "")).lower()
        if package_name.lower() in out and (
            "mcurrentfocus" in out or "mresumedactivity" in out or "topresumedactivity" in out
        ):
            return True
    return False


def send_resume(adb_path: str, target: str, timeout: int = 15) -> bool:
    # KEYCODE_MEDIA_PLAY usually resumes from last paused position if media session exists.
    primary = run_adb(adb_path, ["-s", target, "shell", "input", "keyevent", "126"], timeout=timeout)
    if primary.returncode == 0:
        out = (primary.stdout or primary.stderr).strip()
        if out:
            print(out)
        return True

    # Fallback to toggle if PLAY is not accepted on this firmware.
    fallback = run_adb(adb_path, ["-s", target, "shell", "input", "keyevent", "85"], timeout=timeout)
    out = (fallback.stdout or fallback.stderr).strip()
    if out:
        print(out)
    return fallback.returncode == 0


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
    out = (launch.stdout or launch.stderr).strip()
    if out:
        print(out)
    return launch.returncode == 0


def resolve_view_intent(adb_path: str, target: str, uri: str, timeout: int = 20) -> bool:
    probe = run_adb(
        adb_path,
        [
            "-s",
            target,
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            uri,
        ],
        timeout=timeout,
    )
    out = ((probe.stdout or "") + (probe.stderr or "")).strip()
    return probe.returncode == 0 and out and "No activity found" not in out and "ResolverActivity" not in out


def am_start_view(
    adb_path: str,
    target: str,
    uri: str,
    package_name: str,
    timeout: int = 20,
) -> bool:
    start = run_adb(
        adb_path,
        [
            "-s",
            target,
            "shell",
            "am",
            "start",
            "-W",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            uri,
            package_name,
        ],
        timeout=timeout,
    )
    out = ((start.stdout or "") + (start.stderr or "")).strip()
    if out:
        print(out)

    # On some Android builds `am start` returns 0 even when it prints an error.
    lowered = out.lower()
    if "error:" in lowered or "unable to resolve intent" in lowered:
        return False
    return start.returncode == 0


def parse_item_id_from_url(item_url: str) -> str | None:
    parsed = urlparse(item_url)
    params = parse_qs(parsed.query)
    values = params.get("id")
    if values:
        return values[0]

    # Jellyfin web UI commonly stores route/query inside the URL fragment.
    # Example: /web/#/details?id=<itemId>&serverId=...
    fragment = parsed.fragment or ""
    if "?" in fragment:
        _, frag_query = fragment.split("?", 1)
    else:
        frag_query = fragment
    frag_params = parse_qs(frag_query)
    frag_values = frag_params.get("id")
    if frag_values:
        return frag_values[0]
    return None


def open_jellyfin_item(
    adb_path: str,
    target: str,
    package_name: str,
    server_url: str,
    item_id: str,
    timeout: int = 20,
) -> bool:
    # Try app deeplink first.
    deeplink = f"jellyfin://details?id={item_id}"
    if resolve_view_intent(adb_path, target, deeplink, timeout=timeout):
        if am_start_view(adb_path, target, deeplink, package_name, timeout=timeout):
            return True
    else:
        print("Jellyfin deep link scheme is not resolvable on this TV client.")

    # Fallback: open Jellyfin web details URL in the app package.
    web_url = f"{server_url.rstrip('/')}/web/#/details?id={item_id}"
    if resolve_view_intent(adb_path, target, web_url, timeout=timeout):
        if am_start_view(adb_path, target, web_url, package_name, timeout=timeout):
            return True
    else:
        print("Jellyfin web details URL is not resolvable for app package on this TV client.")

    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="If TV is ready, ensure Jellyfin is foreground, then send resume/play command"
    )
    parser.add_argument("--ip", required=True, help="TV IP address")
    parser.add_argument("--adb-port", type=int, default=5555, help="ADB TCP port")
    parser.add_argument(
        "--jellyfin-package",
        default="org.jellyfin.androidtv",
        help="Jellyfin Android TV package",
    )
    parser.add_argument("--item-id", help="Jellyfin item ID to open before resume/play")
    parser.add_argument("--item-url", help="Jellyfin web URL with details?id=... to extract item ID")
    parser.add_argument(
        "--server-url",
        default=os.getenv("JELLYFIN_SERVER_URL", ""),
        help="Jellyfin server base URL used for web URL fallback",
    )
    parser.add_argument(
        "--jellyfin-token",
        default=os.getenv("JELLYFIN_API_KEY", ""),
        help="Jellyfin API key for Sessions API playback control",
    )
    parser.add_argument("--timeout", type=int, default=15, help="ADB command timeout in seconds")
    parser.add_argument("--settle-delay", type=float, default=0.8, help="Small delay before sending resume")
    args = parser.parse_args()

    if not args.server_url:
        print("ERROR: Jellyfin server URL is required. Set JELLYFIN_SERVER_URL or pass --server-url.")
        return 2

    adb_path = shutil.which("adb")
    if not adb_path:
        print("ERROR: adb is not found in PATH.")
        return 2

    target = f"{args.ip}:{args.adb_port}"
    state = adb_device_state(adb_path, target, timeout=args.timeout)

    if state is None:
        print("TV is not reachable via ADB. Skipping by design (no wake).")
        return 1

    if state != "device":
        print(f"TV ADB state is '{state}', expected 'device'. Skipping.")
        return 3

    print("TV is reachable and authorized over ADB.")

    if not is_jellyfin_foreground(adb_path, target, args.jellyfin_package, timeout=args.timeout):
        print("Jellyfin is not in foreground. Launching app to bring it to focus...")
        if not launch_app(adb_path, target, args.jellyfin_package, timeout=args.timeout):
            print("Failed to launch Jellyfin app.")
            return 4

        time.sleep(max(args.settle_delay, 1.0))
        if not is_jellyfin_foreground(adb_path, target, args.jellyfin_package, timeout=args.timeout):
            print("Jellyfin launch command sent, but app is still not in foreground.")
            return 4

    print("Jellyfin is in foreground.")

    item_id = args.item_id
    if not item_id and args.item_url:
        item_id = parse_item_id_from_url(args.item_url)
        if item_id:
            print(f"Extracted item ID from URL: {item_id}")
        else:
            print("WARN: Could not extract item ID from --item-url.")

    if item_id:
        if not args.jellyfin_token:
            print("ERROR: Jellyfin token is required for item playback when fallback is disabled.")
            return 6

        print(f"Attempting Sessions API playback for item: {item_id}")
        started = play_item_via_sessions_api(args.server_url, args.jellyfin_token, item_id)
        if started:
            return 0

        print("ERROR: Sessions API playback failed and fallback is disabled.")
        return 6

    print("Sending resume command...")
    if args.settle_delay > 0:
        time.sleep(args.settle_delay)

    if send_resume(adb_path, target, timeout=args.timeout):
        print("Resume command sent.")
        return 0

    print("Failed to send resume command.")
    return 5


if __name__ == "__main__":
    sys.exit(main())
