#!/usr/bin/env python3
"""
Single-entry workflow for Jellyfin playback on TV.

This script calls two existing scripts in sequence:
1) core/jellyfin_nl_to_item_id_e2e.py - resolve request -> item_id
2) core/app-specific/jellyfin/jellyfin_resume_if_ready.py - start playback on TV
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent
CORE_ROOT = PROJECT_ROOT / "core"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(CORE_ROOT))


STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "episode",
    "series",
    "office",
    "включи",
    "серия",
    "серию",
    "где",
    "про",
    "в",
    "на",
    "и",
    "офисе",
}


def extract_keywords(*texts: str) -> list[str]:
    tokens: list[str] = []
    for text in texts:
        for token in re.findall(r"[a-zA-Zа-яА-Я0-9]+", text.lower()):
            if len(token) < 3 or token in STOPWORDS:
                continue
            tokens.append(token)
    # Preserve order and uniqueness.
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def infer_episode_item_id(
    *,
    server_url: str,
    jellyfin_token: str,
    username: str,
    series_name: str,
    series_id: str,
    user_request: str,
    normalized_search_query: str,
) -> tuple[str | None, str | None]:
    from jellyfin_nl_to_item_id_e2e import get_series_episode_candidates, resolve_series_episode_item, resolve_user

    user = resolve_user(server_url, jellyfin_token, username)
    user_id = user.get("Id")
    if not user_id:
        return None, None

    candidates = get_series_episode_candidates(server_url, jellyfin_token, user_id, series_id)
    if not candidates:
        return None, None

    keywords = extract_keywords(user_request, normalized_search_query)
    if not keywords:
        return None, None

    best_score = 0
    best_candidate: dict | None = None
    for candidate in candidates:
        haystack = f"{candidate.get('title', '')} {candidate.get('overview', '')}".lower()
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if not best_candidate or best_score == 0:
        return None, None

    season = int(best_candidate["season"])
    episode = int(best_candidate["episode"])
    resolved = resolve_series_episode_item(server_url, jellyfin_token, user_id, series_name, season, episode)
    return resolved.get("Id"), resolved.get("Name")


def run_step(command: list[str], name: str) -> subprocess.CompletedProcess[str]:
    print(f"\n=== {name} ===")
    print(" ".join(command))
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    return completed


def is_target_playback_confirmed(run_output: str, item_id: str) -> bool:
    if f"Attempting Sessions API playback for item: {item_id}" not in run_output:
        return False
    return "Sessions API playback confirmed:" in run_output


def fetch_sessions(server_url: str, jellyfin_token: str) -> list[dict]:
    req = Request(
        f"{server_url.rstrip('/')}/Sessions",
        headers={
            "X-Emby-Token": jellyfin_token,
            "Accept": "application/json",
            "User-Agent": "jellyfin-request-to-playback/1.0",
        },
    )
    with urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, list):
        return payload
    return []


def has_remote_controllable_tv_session(server_url: str, jellyfin_token: str, preferred_client: str) -> bool:
    try:
        sessions = fetch_sessions(server_url, jellyfin_token)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return False

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
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve natural-language request to Jellyfin item and immediately start playback on TV"
    )
    parser.add_argument("--request", required=True, help="Natural-language playback request")
    parser.add_argument("--ip", default=os.getenv("TV_IP", ""), help="TV IP address (or set TV_IP)")
    parser.add_argument(
        "--mac",
        default=os.getenv("TV_MAC", ""),
        help="TV MAC address for WoL (or set TV_MAC)",
    )
    parser.add_argument("--adb-port", type=int, default=5555, help="ADB TCP port")
    parser.add_argument(
        "--tv-max-wait",
        type=int,
        default=90,
        help="Max seconds to wait for TV ADB readiness in ensure-on step",
    )
    parser.add_argument(
        "--tv-post-wake-delay",
        type=int,
        default=10,
        help="Extra seconds to wait after wake in ensure-on step",
    )
    parser.add_argument("--server-url", default=os.getenv("JELLYFIN_SERVER_URL", ""), help="Jellyfin server URL")
    parser.add_argument(
        "--jellyfin-token",
        default=os.getenv("JELLYFIN_API_KEY", ""),
        help="Jellyfin API key",
    )
    parser.add_argument(
        "--username", default=os.getenv("JELLYFIN_USER_NAME", ""), help="Preferred Jellyfin username"
    )
    parser.add_argument(
        "--jellyfin-package",
        default="org.jellyfin.androidtv",
        help="Jellyfin Android TV package name",
    )
    parser.add_argument(
        "--without-episode-metadata",
        action="store_true",
        help="Disable episode metadata enrichment for series resolution",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print resolver JSON to stdout")
    parser.add_argument(
        "--playback-attempts",
        type=int,
        default=3,
        help="How many times to retry playback step if target confirmation is missing",
    )
    parser.add_argument(
        "--playback-retry-delay",
        type=float,
        default=1.0,
        help="Delay in seconds between playback retries",
    )
    parser.add_argument(
        "--skip-ensure-step",
        action="store_true",
        help="Skip STEP 0 (TV/app ensure) completely",
    )
    args = parser.parse_args()

    if not args.server_url:
        print("ERROR: Jellyfin server URL is required. Set JELLYFIN_SERVER_URL or pass --server-url.", file=sys.stderr)
        return 2
    if not args.jellyfin_token:
        print("ERROR: Jellyfin API key is required. Set JELLYFIN_API_KEY or pass --jellyfin-token.", file=sys.stderr)
        return 2
    if not args.username:
        print("ERROR: Jellyfin username is required. Set JELLYFIN_USER_NAME or pass --username.", file=sys.stderr)
        return 2
    if not args.ip:
        print("ERROR: TV IP is required. Set TV_IP or pass --ip.", file=sys.stderr)
        return 2

    resolver_script = CORE_ROOT / "jellyfin_nl_to_item_id_e2e.py"
    playback_script = CORE_ROOT / "app-specific" / "jellyfin" / "jellyfin_resume_if_ready.py"
    ensure_tv_script = CORE_ROOT / "tv_ensure_on_open_app.py"

    if not resolver_script.exists():
        print(f"ERROR: Resolver script not found: {resolver_script}", file=sys.stderr)
        return 2
    if not playback_script.exists():
        print(f"ERROR: Playback script not found: {playback_script}", file=sys.stderr)
        return 2
    if not ensure_tv_script.exists():
        print(f"ERROR: TV ensure script not found: {ensure_tv_script}", file=sys.stderr)
        return 2

    with tempfile.NamedTemporaryFile(prefix="jellyfin_resolve_", suffix=".json", delete=False) as tmp:
        output_path = Path(tmp.name)

    use_episode_metadata = not args.without_episode_metadata

    try:
        if args.skip_ensure_step:
            print("Skipping STEP 0 by --skip-ensure-step.")
        else:
            if has_remote_controllable_tv_session(
                server_url=args.server_url,
                jellyfin_token=args.jellyfin_token,
                preferred_client="Jellyfin Android TV",
            ):
                print("STEP 0 skipped: remote-controllable Jellyfin Android TV session is already available.")
            else:
                if not args.mac:
                    print("ERROR: TV MAC is required for STEP 0. Set TV_MAC or pass --mac.", file=sys.stderr)
                    return 2

                ensure_cmd = [
                    sys.executable,
                    str(ensure_tv_script),
                    "--ip",
                    args.ip,
                    "--mac",
                    args.mac,
                    "--app-package",
                    args.jellyfin_package,
                    "--adb-port",
                    str(args.adb_port),
                    "--max-wait",
                    str(args.tv_max_wait),
                    "--post-wake-delay",
                    str(args.tv_post_wake_delay),
                ]
                ensured = run_step(ensure_cmd, "STEP 0: Ensure TV is on and Jellyfin is opened")
                if ensured.returncode != 0:
                    print(f"ERROR: TV ensure step failed with code {ensured.returncode}", file=sys.stderr)
                    return ensured.returncode

        resolve_cmd = [
            sys.executable,
            str(resolver_script),
            "--request",
            args.request,
            "--server-url",
            args.server_url,
            "--jellyfin-token",
            args.jellyfin_token,
            "--username",
            args.username,
            "--output",
            str(output_path),
        ]
        if use_episode_metadata:
            resolve_cmd.append("--use-episode-metadata")
        if args.pretty:
            resolve_cmd.append("--pretty")

        resolved = run_step(resolve_cmd, "STEP 1: Resolve request to item_id")
        if resolved.returncode != 0:
            print(f"ERROR: Resolver step failed with code {resolved.returncode}", file=sys.stderr)
            return resolved.returncode

        if not output_path.exists():
            print("ERROR: Resolver did not produce output JSON file.", file=sys.stderr)
            return 3

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        status = payload.get("status")
        resolved_item = payload.get("resolved_item") or {}
        item_id = resolved_item.get("item_id")
        title = resolved_item.get("title")

        if status != "ok" and not item_id:
            print(f"ERROR: Resolver status is '{status}', expected 'ok'.", file=sys.stderr)
            print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
            return 4

        if not item_id:
            print("ERROR: Resolver output has no resolved_item.item_id", file=sys.stderr)
            return 5

        print("\nResolved item:")
        print(f"- item_id: {item_id}")
        if title:
            print(f"- title: {title}")

        play_cmd = [
            sys.executable,
            str(playback_script),
            "--ip",
            args.ip,
            "--adb-port",
            str(args.adb_port),
            "--jellyfin-package",
            args.jellyfin_package,
            "--server-url",
            args.server_url,
            "--jellyfin-token",
            args.jellyfin_token,
            "--item-id",
            item_id,
        ]

        attempts = max(1, args.playback_attempts)
        played: subprocess.CompletedProcess[str] | None = None
        for attempt in range(1, attempts + 1):
            played = run_step(play_cmd, f"STEP 2: Start playback on TV (attempt {attempt}/{attempts})")
            if played.returncode != 0:
                # Code 6 is a strict, transient "PlayNow not confirmed yet" failure from
                # jellyfin_resume_if_ready.py; retry before giving up.
                if played.returncode == 6 and attempt < attempts:
                    print(
                        "WARN: Playback script returned code 6 (not confirmed yet); retrying...",
                        file=sys.stderr,
                    )
                    time.sleep(max(0.0, args.playback_retry_delay))
                    continue

                print(f"ERROR: Playback step failed with code {played.returncode}", file=sys.stderr)
                return played.returncode

            output = (played.stdout or "") + "\n" + (played.stderr or "")
            if is_target_playback_confirmed(output, item_id):
                break

            if attempt < attempts:
                print(
                    "WARN: Playback step finished but target item was not confirmed by Sessions API; retrying...",
                    file=sys.stderr,
                )
                time.sleep(max(0.0, args.playback_retry_delay))

        if played is None:
            print("ERROR: Playback step did not execute.", file=sys.stderr)
            return 6

        output = (played.stdout or "") + "\n" + (played.stderr or "")
        if not is_target_playback_confirmed(output, item_id):
            print("ERROR: Playback command ran, but target item was not confirmed by Sessions API.", file=sys.stderr)
            return 7

        print("\nSUCCESS: Playback workflow completed.")
        return 0
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
