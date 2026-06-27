#!/usr/bin/env python3
"""
Export full Jellyfin media catalog to JSON.

Current defaults are intentionally hardcoded for quick local usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_SERVER_URL = os.getenv("JELLYFIN_SERVER_URL", "")
DEFAULT_API_TOKEN = os.getenv("JELLYFIN_API_KEY", "")
DEFAULT_USERNAME = os.getenv("JELLYFIN_USER_NAME", "")


def api_get_json(server_url: str, token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = ""
    if params:
        query = "?" + urlencode(params)
    url = f"{server_url.rstrip('/')}{path}{query}"

    req = Request(
        url,
        headers={
            "X-Emby-Token": token,
            "Accept": "application/json",
            "User-Agent": "tv-experiments-catalog-export/1.0",
        },
    )

    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_user(users: list[dict[str, Any]], username: str | None) -> dict[str, Any]:
    if not users:
        raise RuntimeError("No users returned by Jellyfin API")

    if username:
        for user in users:
            if user.get("Name", "").lower() == username.lower():
                return user

    return users[0]


def get_items_by_type(server_url: str, token: str, user_id: str, item_type: str) -> list[dict[str, Any]]:
    payload = api_get_json(
        server_url,
        token,
        f"/Users/{user_id}/Items",
        params={
            "Recursive": "true",
            "IncludeItemTypes": item_type,
            "Fields": "Name,Type,ProductionYear,IndexNumber,Overview,PremiereDate,RunTimeTicks,ParentId",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "Limit": 10000,
        },
    )
    return payload.get("Items", [])


def get_children(server_url: str, token: str, user_id: str, parent_id: str, item_type: str) -> list[dict[str, Any]]:
    payload = api_get_json(
        server_url,
        token,
        f"/Users/{user_id}/Items",
        params={
            "ParentId": parent_id,
            "Recursive": "false",
            "IncludeItemTypes": item_type,
            "Fields": "Name,Type,ProductionYear,IndexNumber,Overview,PremiereDate,RunTimeTicks,ParentId",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "Limit": 10000,
        },
    )
    return payload.get("Items", [])


def normalize_movie(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("Id"),
        "name": item.get("Name"),
        "production_year": item.get("ProductionYear"),
        "overview": item.get("Overview"),
        "premiere_date": item.get("PremiereDate"),
        "runtime_ticks": item.get("RunTimeTicks"),
    }


def normalize_episode(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("Id"),
        "name": item.get("Name"),
        "episode_number": item.get("IndexNumber"),
        "overview": item.get("Overview"),
        "premiere_date": item.get("PremiereDate"),
        "runtime_ticks": item.get("RunTimeTicks"),
    }


def build_catalog(server_url: str, token: str, username: str | None) -> dict[str, Any]:
    users = api_get_json(server_url, token, "/Users")
    user = pick_user(users, username)
    user_id = user.get("Id")
    if not user_id:
        raise RuntimeError("Could not resolve user id")

    movies_raw = get_items_by_type(server_url, token, user_id, "Movie")
    series_raw = get_items_by_type(server_url, token, user_id, "Series")

    movies = [normalize_movie(item) for item in movies_raw]
    series: list[dict[str, Any]] = []
    episodes_count = 0

    for s in series_raw:
        series_id = s.get("Id")
        if not series_id:
            continue

        seasons_raw = get_children(server_url, token, user_id, series_id, "Season")
        seasons_out: list[dict[str, Any]] = []

        for season in seasons_raw:
            season_id = season.get("Id")
            if not season_id:
                continue

            episodes_raw = get_children(server_url, token, user_id, season_id, "Episode")
            episodes = [normalize_episode(ep) for ep in episodes_raw]
            episodes_count += len(episodes)

            seasons_out.append(
                {
                    "id": season_id,
                    "name": season.get("Name"),
                    "season_number": season.get("IndexNumber"),
                    "episodes": episodes,
                }
            )

        series.append(
            {
                "id": series_id,
                "name": s.get("Name"),
                "production_year": s.get("ProductionYear"),
                "overview": s.get("Overview"),
                "seasons": seasons_out,
            }
        )

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "server_url": server_url,
            "user": {"id": user_id, "name": user.get("Name")},
        },
        "counts": {
            "movies": len(movies),
            "series": len(series),
            "episodes": episodes_count,
        },
        "movies": movies,
        "series": series,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Jellyfin catalog as JSON")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="Jellyfin server URL")
    parser.add_argument("--api-token", default=DEFAULT_API_TOKEN, help="Jellyfin API token")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Preferred Jellyfin username")
    parser.add_argument("--output", default="", help="Optional output JSON file path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
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
        payload = build_catalog(args.server_url, args.api_token, args.username)
    except HTTPError as exc:
        print(f"ERROR: HTTP {exc.code} while calling Jellyfin API", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"ERROR: Cannot reach Jellyfin server: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
