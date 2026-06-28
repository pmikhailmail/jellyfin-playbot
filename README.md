# TV + Jellyfin NL Playback POC

Date: 2026-06-27

## What This Project Is

This repository is a proof of concept for voice/text-like playback requests in natural language.

Input example:
- "включи как это работает серию про туалетную бумагу"

Output behavior:
- Resolve request to a concrete Jellyfin `item_id`
- Start playback on Android TV via Jellyfin Sessions API
- Confirm that the target media is actually playing

## Main Idea

One command runs a full pipeline:
1. Ensure TV/Jellyfin app is ready (or skip if a controllable TV session already exists).
2. Resolve natural-language request to a concrete media item.
3. Trigger strict playback via Sessions API and verify target playback.

The single-entry script is:
- `jellyfin_request_to_playback.py`

## End-to-End Flow

### STEP 0: TV/App readiness
- Script: `tv_ensure_on_open_app.py`
- Wakes TV (WoL if needed), checks ADB, opens Jellyfin app.
- Auto-skipped if there is already a remote-controllable "Jellyfin Android TV" session.

### STEP 1: NL request -> item_id
- Script: `jellyfin_nl_to_item_id_e2e.py`
- Internally uses prompt builders:
  - `build_search_query_prompt.py`
  - `jellyfin_build_resolution_prompt.py`
  - `build_series_episode_question_prompt.py`
- For series, it can use episode metadata (`--use-episode-metadata`) for better season/episode resolution.
- Supports collection recursion (for boxsets/collections) until a concrete item is selected.

### STEP 2: Strict playback on TV
- Script: `app-specific/jellyfin/jellyfin_resume_if_ready.py`
- Uses Jellyfin Sessions API `PlayNow`.
- Verifies that `NowPlaying` switched to the requested item.
- Fallback playback path is disabled by design (strict mode).

## Repository Highlights

- `jellyfin_request_to_playback.py`: production-like orchestrator for this POC.
- `jellyfin_nl_to_item_id_e2e.py`: resolver pipeline (LLM + Jellyfin API).
- `app-specific/jellyfin/jellyfin_resume_if_ready.py`: strict TV playback executor.
- `jellyfin_two_script_playback.md`: documented two-script manual mode.
- `poc_nl_to_jellyfin_id.md`: detailed breakdown of resolver logic.

## Environment

This workspace has been used with:
- OS: Windows
- Python: 3.11
- Conda env: `.conda`
- TV ADB endpoint: `192.168.0.122:5555`

Required Jellyfin environment variables:
- `JELLYFIN_SERVER_URL`
- `JELLYFIN_USER_NAME`
- `JELLYFIN_API_KEY`

Required TV environment variable:
- `TV_IP`

Conditionally required TV environment variable (for STEP 0 wake/WoL):
- `TV_MAC`

PowerShell example:

```powershell
$env:JELLYFIN_SERVER_URL = "http://192.168.0.104:8899"
$env:JELLYFIN_USER_NAME = "smarttv"
$env:JELLYFIN_API_KEY = "<YOUR_JELLYFIN_API_KEY>"
$env:TV_IP = "192.168.0.122"
$env:TV_MAC = "2c:1b:3a:c3:d8:2d"
```

## Quick Start

Run from project root:

```powershell
conda run -p .conda python jellyfin_request_to_playback.py `
  --request "включи что-нибудь легкое на вечер"
```

## Useful Flags

`jellyfin_request_to_playback.py`:
- `--request`: natural-language request (required)
- `--ip`: TV IP address override (default: `TV_IP`)
- `--mac`: TV MAC override for WoL in STEP 0 (default: `TV_MAC`)
- `--server-url`: Jellyfin server URL override (default: `JELLYFIN_SERVER_URL`)
- `--jellyfin-token`: Jellyfin API key override (default: `JELLYFIN_API_KEY`)
- `--username`: Jellyfin username override (default: `JELLYFIN_USER_NAME`)
- `--without-episode-metadata`: disable metadata enrichment for series resolution
- `--playback-attempts`: retry count for strict playback confirmation (default: 3)
- `--playback-retry-delay`: delay between retries (default: 1.0)
- `--skip-ensure-step`: skip STEP 0 entirely

## Success Criteria

A successful run includes logs like:
- `Resolved item:`
- `Attempting Sessions API playback for item: ...`
- `Sessions API PlayNow status: 204`
- `Sessions API playback confirmed: ...`
- `SUCCESS: Playback workflow completed.`

## Operational Notes

- A `204` from `PlayNow` does not always mean immediate `NowPlaying` update.
- Transient strict-confirmation failures are retried by orchestrator logic.
- If TV session is already controllable, skipping STEP 0 helps preserve remote-control capability.
- `TV_MAC`/`--mac` is required when STEP 0 runs (wake/ensure flow).

## Troubleshooting

1. Resolver returns `needs_clarification`:
- Try a more specific request.
- Keep episode metadata enabled (default behavior).

2. Playback returns transient code 6 before success:
- Usually normal timing lag in `NowPlaying` update.
- Increase `--playback-attempts` or retry delay if needed.

3. No remote control support in Jellyfin session:
- Re-open app on TV or run without `--skip-ensure-step`.
- Check Jellyfin `/Sessions` for `SupportsRemoteControl=true` and media control capability.

## Security Note

Jellyfin credentials and endpoint are now sourced from environment variables. Do not commit real secrets to the repository history.
