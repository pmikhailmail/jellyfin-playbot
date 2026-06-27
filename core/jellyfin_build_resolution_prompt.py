#!/usr/bin/env python3
"""
Build an LLM prompt for resolving a natural-language request to a Jellyfin media item.

The script fetches available movies/series from Jellyfin and formats them as:
N: Title

Then it creates a prompt that asks the model to return strict JSON:
- media_index
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jellyfin_export_catalog import (
    DEFAULT_API_TOKEN,
    DEFAULT_SERVER_URL,
    DEFAULT_USERNAME,
    api_get_json,
    get_items_by_type,
    pick_user,
)


def fetch_media_list(server_url: str, token: str, username: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    users = api_get_json(server_url, token, "/Users")
    user = pick_user(users, username)
    user_id = user.get("Id")
    if not user_id:
        raise RuntimeError("Could not resolve user id")

    movies = get_items_by_type(server_url, token, user_id, "Movie")
    series = get_items_by_type(server_url, token, user_id, "Series")

    merged = []
    for item in movies:
        merged.append({"type": "movie", "name": item.get("Name", ""), "id": item.get("Id")})
    for item in series:
        merged.append({"type": "series", "name": item.get("Name", ""), "id": item.get("Id")})

    merged.sort(key=lambda x: x["name"].lower())
    return user, merged


def build_prompt(user_query: str, media_items: list[dict[str, Any]]) -> str:
    lines = []
    for idx, item in enumerate(media_items, start=1):
        type_label = "SERIES" if item["type"] == "series" else "MOVIE"
        lines.append(f"{idx}: [{type_label}] {item['name']}")

    media_block = "\n".join(lines)

    return (
        "You are a media request classifier for a local Jellyfin library.\n"
        "You are given a list of available media items and a user request.\n\n"
        "Task:\n"
        "1) Determine which list item best matches the request.\n"
        "2) If the request is free-form (mood/genre/style/country/theme), infer intent and pick the single best match from the list.\n"
        "3) Use semantic matching, not only exact title matching. Consider cues like genre, tone, country, era, plot theme, audience intent, and common synonyms.\n"
        "4) Return media_index=null only when there is no reasonably suitable match in the available list.\n"
        "5) At this step, do NOT determine whether it is a series or a movie: that is resolved outside the LLM.\n"
        "6) At this step, do NOT determine a specific season or episode.\n"
        "7) Handle typos, Russian/English names, and transliteration (for example: клиника -> Scrubs).\n"
        "8) The user may use imperative phrasing (for example: play/start).\n"
        "   Ignore action verbs and extract only the target media item.\n\n"
        "Return STRICT JSON only, with no markdown and no explanations.\n"
        "Response format:\n"
        "{\n"
        "  \"media_index\": number|null\n"
        "}\n\n"
        "Valid response examples:\n"
        "{\"media_index\": 20}\n"
        "{\"media_index\": 2}\n"
        "{\"media_index\": null}\n\n"
        "Available media items:\n"
        f"{media_block}\n\n"
        "User request:\n"
        f"{user_query}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build prompt for media resolution via LLM")
    parser.add_argument("--query", required=True, help="User natural-language request")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="Jellyfin server URL")
    parser.add_argument("--api-token", default=DEFAULT_API_TOKEN, help="Jellyfin API token")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Preferred Jellyfin username")
    parser.add_argument("--output", default="", help="Optional output file for generated prompt")
    parser.add_argument(
        "--mapping-output",
        default="",
        help="Optional JSON file with media index mapping to Jellyfin IDs",
    )
    args = parser.parse_args()

    if not args.server_url:
        print("ERROR: Jellyfin server URL is required. Set JELLYFIN_SERVER_URL or pass --server-url.", file=sys.stderr)
        return 2
    if not args.api_token:
        print("ERROR: Jellyfin API key is required. Set JELLYFIN_API_KEY or pass --api-token.", file=sys.stderr)
        return 2
    if not args.username:
        print("ERROR: Jellyfin username is required. Set JELLYFIN_USER_NAME or pass --username.", file=sys.stderr)
        return 2

    try:
        user, media_items = fetch_media_list(args.server_url, args.api_token, args.username)
    except HTTPError as exc:
        print(f"ERROR: HTTP {exc.code} while calling Jellyfin API", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"ERROR: Cannot reach Jellyfin server: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    prompt = build_prompt(args.query, media_items)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(prompt)

    if args.mapping_output:
        payload = {
            "meta": {
                "user": {"id": user.get("Id"), "name": user.get("Name")},
                "server_url": args.server_url,
            },
            "items": [
                {
                    "index": idx,
                    "type": item["type"],
                    "name": item["name"],
                    "jellyfin_id": item["id"],
                }
                for idx, item in enumerate(media_items, start=1)
            ],
        }
        with open(args.mapping_output, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
