#!/usr/bin/env python3
"""
End-to-end PoC: convert a natural-language request into a Jellyfin item ID.

Pipeline:
1. Normalize action request into a search query via OpenAI.
2. Select the most relevant media object from Jellyfin catalog via OpenAI.
3. If selected media is a series, resolve season/episode via OpenAI.
4. Resolve final Jellyfin item ID via Jellyfin API.

Preferred secret source:
- OPENAI_API_KEY environment variable
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_search_query_prompt import build_prompt as build_search_query_prompt
from build_series_episode_question_prompt import (
    build_llm_prompt as build_series_episode_prompt,
    build_search_question,
)
from jellyfin_build_resolution_prompt import build_prompt as build_media_selection_prompt
from jellyfin_build_resolution_prompt import fetch_media_list
from jellyfin_export_catalog import (
    DEFAULT_API_TOKEN,
    DEFAULT_SERVER_URL,
    DEFAULT_USERNAME,
    api_get_json,
    get_children,
    get_items_by_type,
    pick_user,
)

DEFAULT_OPENAI_MODEL_NORMALIZE = "gpt-4.1-mini"
DEFAULT_OPENAI_MODEL_SELECT = "gpt-4.1-mini"
DEFAULT_OPENAI_MODEL_EPISODE = "gpt-5.4-mini"
DEFAULT_TEMPERATURE_NORMALIZE = 0.0
DEFAULT_TEMPERATURE_SELECT = 0.0
DEFAULT_TEMPERATURE_EPISODE = 0.4
DEFAULT_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_SERVER_ID = "675d8887c5664e87bcb8dc8ad9dd32f4"


class PipelineError(RuntimeError):
    pass


def post_openai_json(api_key: str, model: str, prompt: str, temperature: float) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    req = Request(
        DEFAULT_OPENAI_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    choices = payload.get("choices") or []
    if not choices:
        raise PipelineError("OpenAI returned no choices")

    message = choices[0].get("message", {})
    content = message.get("content")
    if not content:
        raise PipelineError("OpenAI returned empty content")

    return content.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Failed to parse model JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PipelineError("Model response is not a JSON object")
    return parsed


def resolve_user(server_url: str, token: str, username: str | None) -> dict[str, Any]:
    users = api_get_json(server_url, token, "/Users")
    return pick_user(users, username)


def resolve_series_episode_item(
    server_url: str,
    token: str,
    user_id: str,
    series_name: str,
    season_number: int,
    episode_number: int,
) -> dict[str, Any]:
    series_items = get_items_by_type(server_url, token, user_id, "Series")
    series = next((item for item in series_items if item.get("Name") == series_name), None)
    if not series:
        raise PipelineError(f"Series not found in Jellyfin: {series_name}")

    seasons = get_children(server_url, token, user_id, series["Id"], "Season")
    season = next((item for item in seasons if item.get("IndexNumber") == season_number), None)
    if not season:
        raise PipelineError(f"Season not found: {series_name} S{season_number}")

    episodes = get_children(server_url, token, user_id, season["Id"], "Episode")
    episode = next((item for item in episodes if item.get("IndexNumber") == episode_number), None)
    if not episode:
        raise PipelineError(f"Episode not found: {series_name} S{season_number}E{episode_number}")

    return episode


def resolve_movie_item(server_url: str, token: str, user_id: str, movie_name: str) -> dict[str, Any]:
    movies = get_items_by_type(server_url, token, user_id, "Movie")
    movie = next((item for item in movies if item.get("Name") == movie_name), None)
    if not movie:
        raise PipelineError(f"Movie not found in Jellyfin: {movie_name}")
    return movie


def get_item_by_id(server_url: str, token: str, user_id: str, item_id: str) -> dict[str, Any]:
    item = api_get_json(server_url, token, f"/Users/{user_id}/Items/{item_id}")
    if not isinstance(item, dict) or not item.get("Id"):
        raise PipelineError(f"Item not found by id: {item_id}")
    return item


def is_collection_like(item: dict[str, Any]) -> bool:
    item_type = str(item.get("Type") or "").lower()
    name = str(item.get("Name") or "").lower()
    collection_type = str(item.get("CollectionType") or "").lower()
    if item_type in {"boxset", "collectionfolder"}:
        return True
    if collection_type in {"movies", "tvshows", "music"}:
        return True
    return name.endswith(" collection")


def get_collection_children_media(server_url: str, token: str, user_id: str, parent_id: str) -> list[dict[str, Any]]:
    payload = api_get_json(
        server_url,
        token,
        f"/Users/{user_id}/Items",
        params={
            "ParentId": parent_id,
            "Recursive": "false",
            "Fields": "Name,Type,CollectionType,IndexNumber,ParentId",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "Limit": 10000,
        },
    )
    items = payload.get("Items", [])
    out: list[dict[str, Any]] = []
    for item in items:
        item_id = item.get("Id")
        name = item.get("Name")
        item_type = str(item.get("Type") or "")
        if not item_id or not name:
            continue

        normalized_type = "unknown"
        lower_type = item_type.lower()
        if lower_type == "movie":
            normalized_type = "movie"
        elif lower_type == "series":
            normalized_type = "series"
        elif lower_type in {"boxset", "collectionfolder"}:
            normalized_type = "collection"

        out.append({"id": item_id, "name": name, "type": normalized_type})
    return out


def get_series_episode_candidates(
    server_url: str,
    token: str,
    user_id: str,
    series_id: str,
    allowed_seasons: set[int] | None = None,
) -> list[dict[str, Any]]:
    seasons = get_children(server_url, token, user_id, series_id, "Season")
    candidates: list[dict[str, Any]] = []
    for season in seasons:
        season_id = season.get("Id")
        season_number = season.get("IndexNumber")
        if not season_id or season_number is None:
            continue
        if allowed_seasons is not None and int(season_number) not in allowed_seasons:
            continue
        episodes = get_children(server_url, token, user_id, season_id, "Episode")
        for episode in episodes:
            episode_number = episode.get("IndexNumber")
            if episode_number is None:
                continue
            candidates.append(
                {
                    "season": season_number,
                    "episode": episode_number,
                    "title": episode.get("Name") or "",
                    "overview": episode.get("Overview") or "",
                }
            )

    candidates.sort(key=lambda x: (int(x["season"]), int(x["episode"])))
    return candidates


def select_media_via_llm(
    search_query: str,
    media_items: list[dict[str, Any]],
    openai_api_key: str,
    openai_model_select: str,
    temperature_select: float,
    dump_prompts: bool,
    prompt_label: str,
) -> tuple[int | None, dict[str, Any]]:
    step_prompt = build_media_selection_prompt(search_query, media_items)
    if dump_prompts:
        print(f"=== LLM PROMPT: {prompt_label} ===")
        print(step_prompt)
    step_raw = post_openai_json(openai_api_key, openai_model_select, step_prompt, temperature_select)
    step_obj = parse_json_object(step_raw)
    media_index = to_int_or_none(step_obj.get("media_index"), "media_index")
    return media_index, step_obj


def build_details_url(server_url: str, item_id: str, server_id: str = DEFAULT_SERVER_ID) -> str:
    return f"{server_url}/web/#/details?id={item_id}&serverId={server_id}"


def build_step_c1_constraints_prompt(user_request: str, series_name: str) -> str:
    return (
        "You process a verbal user request for an already selected TV series.\n"
        "Your task is to categorize, extract, and formalize the request into exactly three categories:\n"
        "1) seasons\n"
        "2) episodes\n"
        "3) remainder (all other useful non-structural constraints)\n"
        "Return STRICT JSON only with keys: seasons, episodes, remainder.\n"
        "Rules:\n"
        "1) seasons and episodes must be arrays of integers, sorted ascending, unique.\n"
        "2) Use [] when value is not explicitly or reasonably implied.\n"
        "3) remainder must be string|null.\n"
        "4) remainder contains all non-structural constraints and context: plot/theme, mood, character cues, qualifiers like 'funny', and any other useful text.\n"
        "5) Use remainder=null only when request is purely structural (season/episode only, including random/any selectors).\n"
        "6) Handle Russian/English ordinals and number words.\n"
        "7) Use the special value -1 to mean 'any/random' when the user explicitly asks for a random or unspecified episode or season.\n"
        "   Use [-1] in episodes when the user wants any episode from a specified season (e.g. 'any episode', 'random episode', 'some episode', 'любую серию', 'какую нибудь серию').\n"
        "   Use [-1] in seasons when the user wants a specific episode from any season (e.g. 'random season').\n"
        "   Use [] (empty) when the user does not mention season/episode at all.\n"
        "Valid examples:\n"
        "{\"seasons\":[4],\"episodes\":[3],\"remainder\":null}\n"
        "{\"seasons\":[4],\"episodes\":[-1],\"remainder\":null}\n"
        "{\"seasons\":[-1],\"episodes\":[5],\"remainder\":null}\n"
        "{\"seasons\":[4],\"episodes\":[],\"remainder\":\"christmas episode\"}\n"
        "{\"seasons\":[1,2],\"episodes\":[],\"remainder\":\"funny episode\"}\n"
        "{\"seasons\":[],\"episodes\":[],\"remainder\":\"where janitor wedding happens\"}\n\n"
        f"Selected series: {series_name}\n"
        f"User request: {user_request}\n"
    )


def normalize_int_list(value: Any, field_name: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PipelineError(f"Invalid {field_name}: expected array")
    out: list[int] = []
    for item in value:
        parsed = to_int_or_none(item, field_name)
        if parsed is None:
            continue
        if parsed <= 0 and parsed != -1:  # -1 is the "any/random" sentinel
            continue
        out.append(parsed)
    return sorted(set(out))


def compute_overview_truncation_stats(
    episode_candidates: list[dict[str, Any]],
    overview_max_chars: int,
) -> dict[str, Any]:
    total = len(episode_candidates)
    if total == 0:
        return {
            "overview_max_chars": overview_max_chars,
            "episodes_total": 0,
            "episodes_with_overview": 0,
            "episodes_truncated": 0,
            "total_chars_removed": 0,
            "avg_chars_removed_per_truncated": 0.0,
            "max_chars_removed_single_episode": 0,
        }

    with_overview = 0
    truncated = 0
    total_removed = 0
    max_removed = 0

    for candidate in episode_candidates:
        overview = str(candidate.get("overview") or "")
        compact = " ".join(overview.split())
        if not compact:
            continue
        with_overview += 1
        if len(compact) <= overview_max_chars:
            continue

        prefix = compact[: overview_max_chars - 1].rstrip()
        removed = len(compact) - len(prefix)
        truncated += 1
        total_removed += removed
        if removed > max_removed:
            max_removed = removed

    avg_removed = (total_removed / truncated) if truncated else 0.0
    return {
        "overview_max_chars": overview_max_chars,
        "episodes_total": total,
        "episodes_with_overview": with_overview,
        "episodes_truncated": truncated,
        "total_chars_removed": total_removed,
        "avg_chars_removed_per_truncated": round(avg_removed, 2),
        "max_chars_removed_single_episode": max_removed,
    }


def compute_dynamic_overview_max_chars(
    season_filter: list[int],
    fallback_overview_max_chars: int,
    single_season_max_chars: int = 400,
    per_extra_season_penalty: int = 20,
    min_overview_max_chars: int = 240,
) -> int:
    if not season_filter:
        return fallback_overview_max_chars

    season_count = len(set(season_filter))
    dynamic_value = single_season_max_chars - per_extra_season_penalty * (season_count - 1)
    return max(min_overview_max_chars, dynamic_value)


def to_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise PipelineError(f"Invalid boolean value: {value!r}")


def to_int_or_none(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise PipelineError(f"Invalid integer value for {field_name}: {value!r}")


def run_pipeline(
    user_request: str,
    openai_api_key: str,
    openai_model_normalize: str,
    openai_model_select: str,
    openai_model_episode: str,
    temperature_normalize: float,
    temperature_select: float,
    temperature_episode: float,
    server_url: str,
    jellyfin_token: str,
    username: str | None,
    use_episode_metadata: bool,
    episode_overview_max_chars: int,
    dump_prompts: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "error",
        "input_request": user_request,
        "models": {
            "normalize": openai_model_normalize,
            "select_media": openai_model_select,
            "resolve_episode": openai_model_episode,
        },
        "temperatures": {
            "normalize": temperature_normalize,
            "select_media": temperature_select,
            "resolve_episode": temperature_episode,
        },
        "normalized_search_query": None,
        "selected_media": None,
        "series_resolution": None,
        "resolved_item": None,
        "raw_llm": {},
    }

    user = resolve_user(server_url, jellyfin_token, username)
    user_id = user.get("Id")
    if not user_id:
        raise PipelineError("Could not resolve Jellyfin user id")

    user_obj, media_items = fetch_media_list(server_url, jellyfin_token, username)

    # Step A
    step_a_prompt = build_search_query_prompt(user_request)
    if dump_prompts:
        print("=== LLM PROMPT: NORMALIZE ===")
        print(step_a_prompt)
    step_a_raw = post_openai_json(openai_api_key, openai_model_normalize, step_a_prompt, temperature_normalize)
    result["raw_llm"]["normalize"] = parse_json_object(step_a_raw)
    search_query = result["raw_llm"]["normalize"].get("search_query")
    if not isinstance(search_query, str) or not search_query.strip():
        raise PipelineError("Step A did not return a valid search_query")
    search_query = search_query.strip()
    result["normalized_search_query"] = search_query

    # Step B
    media_index, step_b_obj = select_media_via_llm(
        search_query=search_query,
        media_items=media_items,
        openai_api_key=openai_api_key,
        openai_model_select=openai_model_select,
        temperature_select=temperature_select,
        dump_prompts=dump_prompts,
        prompt_label="SELECT_MEDIA",
    )
    result["raw_llm"]["select_media"] = step_b_obj
    if media_index is None:
        result["status"] = "needs_clarification"
        return result
    if media_index < 1 or media_index > len(media_items):
        raise PipelineError(f"media_index out of range: {media_index}")

    selected = media_items[media_index - 1]
    is_serial = selected["type"] == "series"
    result["selected_media"] = {
        "media_index": media_index,
        "name": selected["name"],
        "jellyfin_id": selected["id"],
        "is_serial": is_serial,
        "is_serial_source": "item_type",
        "type": selected["type"],
    }

    # If model selected a collection-like object, iteratively drill down and re-run selection on children.
    selection_path: list[dict[str, Any]] = []
    visited_collection_ids: set[str] = set()
    while True:
        selected_item = get_item_by_id(server_url, jellyfin_token, user_id, selected["id"])
        if not is_collection_like(selected_item):
            break

        collection_id = selected_item["Id"]
        if collection_id in visited_collection_ids:
            raise PipelineError(f"Collection traversal loop detected for item id: {collection_id}")
        visited_collection_ids.add(collection_id)

        children = get_collection_children_media(server_url, jellyfin_token, user_id, collection_id)
        if not children:
            raise PipelineError(f"Selected collection is empty: {selected_item.get('Name')}")

        nested_index, nested_raw = select_media_via_llm(
            search_query=search_query,
            media_items=children,
            openai_api_key=openai_api_key,
            openai_model_select=openai_model_select,
            temperature_select=temperature_select,
            dump_prompts=dump_prompts,
            prompt_label="SELECT_MEDIA_IN_COLLECTION",
        )

        selection_path.append(
            {
                "collection": {"name": selected_item.get("Name"), "jellyfin_id": collection_id},
                "candidate_count": len(children),
                "raw_llm": nested_raw,
            }
        )

        if nested_index is None:
            result["status"] = "needs_clarification"
            result["raw_llm"]["select_media_in_collection"] = selection_path
            return result

        if nested_index < 1 or nested_index > len(children):
            raise PipelineError(f"collection media_index out of range: {nested_index}")

        selected = children[nested_index - 1]
        media_index = nested_index
        is_serial = selected["type"] == "series"
        result["selected_media"] = {
            "media_index": media_index,
            "name": selected["name"],
            "jellyfin_id": selected["id"],
            "is_serial": is_serial,
            "is_serial_source": "item_type",
            "type": selected["type"],
        }

    if selection_path:
        result["raw_llm"]["select_media_in_collection"] = selection_path

    # Non-serial case: resolve directly by selected Jellyfin ID.
    if not is_serial:
        movie = get_item_by_id(server_url, jellyfin_token, user_id, selected["id"])
        result["resolved_item"] = {
            "item_id": movie.get("Id"),
            "title": movie.get("Name"),
            "details_url": build_details_url(server_url, movie.get("Id")),
        }
        result["status"] = "ok"
        return result

    # Step C1: extract structural constraints from normalized search query.
    step_c1_prompt = build_step_c1_constraints_prompt(search_query, selected["name"])
    if dump_prompts:
        print("=== LLM PROMPT: EXTRACT_EPISODE_CONSTRAINTS ===")
        print(step_c1_prompt)
    step_c1_raw = post_openai_json(openai_api_key, openai_model_normalize, step_c1_prompt, 0.0)
    step_c1_obj = parse_json_object(step_c1_raw)
    result["raw_llm"]["extract_episode_constraints"] = step_c1_obj

    extracted_seasons = normalize_int_list(step_c1_obj.get("seasons"), "seasons")
    extracted_episodes = normalize_int_list(step_c1_obj.get("episodes"), "episodes")
    remainder_raw = step_c1_obj.get("remainder")
    if remainder_raw is None:
        remainder: str | None = None
    elif isinstance(remainder_raw, str):
        remainder = remainder_raw.strip() or None
    else:
        raise PipelineError("Invalid remainder: expected string|null")

    result["episode_constraints"] = {
        "seasons": extracted_seasons,
        "episodes": extracted_episodes,
        "remainder": remainder,
    }

    # Handle random/any sentinel (-1): pick a random episode from matching candidates.
    has_random_episode = -1 in extracted_episodes
    has_random_season = -1 in extracted_seasons
    if has_random_episode or has_random_season:
        real_seasons = [s for s in extracted_seasons if s != -1]
        allowed_seasons = set(real_seasons) if real_seasons and not has_random_season else None
        random_candidates = get_series_episode_candidates(
            server_url, jellyfin_token, user_id, selected["id"],
            allowed_seasons=allowed_seasons,
        )
        if has_random_season and not has_random_episode:
            real_episodes = set(e for e in extracted_episodes if e != -1)
            random_candidates = [c for c in random_candidates if c["episode"] in real_episodes]
        if not random_candidates:
            result["status"] = "needs_clarification"
            return result
        chosen = random.choice(random_candidates)
        season_num = int(chosen["season"])
        episode_num = int(chosen["episode"])
        result["series_resolution"] = {"season": season_num, "episode": episode_num, "source": "random_selection"}
        item = resolve_series_episode_item(server_url, jellyfin_token, user_id, selected["name"], season_num, episode_num)
        result["resolved_item"] = {
            "item_id": item.get("Id"),
            "title": item.get("Name"),
            "details_url": build_details_url(server_url, item.get("Id")),
        }
        result["status"] = "ok"
        return result

    # If both season and episode are unambiguous, resolve directly.
    if len(extracted_seasons) == 1 and len(extracted_episodes) == 1:
        try:
            item = resolve_series_episode_item(
                server_url,
                jellyfin_token,
                user_id,
                selected["name"],
                extracted_seasons[0],
                extracted_episodes[0],
            )
            result["series_resolution"] = {
                "season": extracted_seasons[0],
                "episode": extracted_episodes[0],
                "source": "direct_constraints",
            }
            result["resolved_item"] = {
                "item_id": item.get("Id"),
                "title": item.get("Name"),
                "details_url": build_details_url(server_url, item.get("Id")),
            }
            result["status"] = "ok"
            return result
        except PipelineError:
            result["direct_constraints_fallback"] = True

    # Step C2
    search_question = build_search_question(selected["name"], search_query)
    effective_overview_max_chars = compute_dynamic_overview_max_chars(
        extracted_seasons,
        episode_overview_max_chars,
    )
    episode_candidates: list[dict[str, Any]] | None = None
    should_include_episode_metadata = use_episode_metadata or bool(extracted_seasons)
    if should_include_episode_metadata:
        allowed_seasons = set(extracted_seasons) if extracted_seasons else None
        episode_candidates = get_series_episode_candidates(
            server_url,
            jellyfin_token,
            user_id,
            selected["id"],
            allowed_seasons=allowed_seasons,
        )
        result["episode_metadata"] = {
            "enabled": True,
            "candidates_count": len(episode_candidates),
            "season_filter": extracted_seasons,
            "overview_max_chars_configured": episode_overview_max_chars,
            "overview_max_chars_effective": effective_overview_max_chars,
            "overview_truncation": compute_overview_truncation_stats(
                episode_candidates,
                effective_overview_max_chars,
            ),
        }
    elif not extracted_seasons:
        result["episode_resolution_note"] = (
            "No season constraints; using broad episode resolution which may degrade quality "
            "for long series (lost-in-the-middle)."
        )

    step_c_prompt = build_series_episode_prompt(
        search_question,
        episode_candidates=episode_candidates,
        overview_max_chars=effective_overview_max_chars,
    )
    if dump_prompts:
        print("=== LLM PROMPT: RESOLVE_EPISODE ===")
        print(step_c_prompt)
    step_c_raw = post_openai_json(openai_api_key, openai_model_episode, step_c_prompt, temperature_episode)
    step_c_obj = parse_json_object(step_c_raw)
    result["raw_llm"]["resolve_episode"] = step_c_obj

    season = to_int_or_none(step_c_obj.get("season"), "season")
    episode = to_int_or_none(step_c_obj.get("episode"), "episode")
    result["series_resolution"] = {"season": season, "episode": episode}
    if season is None or episode is None:
        result["status"] = "needs_clarification"
        return result

    item = resolve_series_episode_item(server_url, jellyfin_token, user_id, selected["name"], season, episode)
    result["resolved_item"] = {
        "item_id": item.get("Id"),
        "title": item.get("Name"),
        "details_url": build_details_url(server_url, item.get("Id")),
    }
    result["status"] = "ok"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end resolve natural-language request to Jellyfin item ID")
    parser.add_argument("--request", required=True, help="Original user request")
    parser.add_argument(
        "--openai-model-normalize",
        default=DEFAULT_OPENAI_MODEL_NORMALIZE,
        help="OpenAI model for action request normalization",
    )
    parser.add_argument(
        "--openai-model-select",
        default=DEFAULT_OPENAI_MODEL_SELECT,
        help="OpenAI model for media object selection",
    )
    parser.add_argument(
        "--openai-model-episode",
        default=DEFAULT_OPENAI_MODEL_EPISODE,
        help="OpenAI model for season/episode resolution",
    )
    parser.add_argument(
        "--temperature-normalize",
        type=float,
        default=DEFAULT_TEMPERATURE_NORMALIZE,
        help="Sampling temperature for normalization step",
    )
    parser.add_argument(
        "--temperature-select",
        type=float,
        default=DEFAULT_TEMPERATURE_SELECT,
        help="Sampling temperature for media selection step",
    )
    parser.add_argument(
        "--temperature-episode",
        type=float,
        default=DEFAULT_TEMPERATURE_EPISODE,
        help="Sampling temperature for season/episode resolution step",
    )
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="Jellyfin server URL")
    parser.add_argument("--jellyfin-token", default=DEFAULT_API_TOKEN, help="Jellyfin API token")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Preferred Jellyfin username")
    parser.add_argument(
        "--use-episode-metadata",
        action="store_true",
        help="Include Jellyfin episode titles and overviews in season/episode prompt",
    )
    parser.add_argument(
        "--episode-overview-max-chars",
        type=int,
        default=350,
        help="Max chars per episode overview included in prompt",
    )
    parser.add_argument("--output", default="", help="Optional JSON output file path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--dump-prompts", action="store_true", help="Print exact LLM prompts to console")
    args = parser.parse_args()

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        print("ERROR: OPENAI_API_KEY environment variable is required.", file=sys.stderr)
        return 2
    if not args.server_url:
        print("ERROR: Jellyfin server URL is required. Set JELLYFIN_SERVER_URL or pass --server-url.", file=sys.stderr)
        return 2
    if not args.jellyfin_token:
        print("ERROR: Jellyfin API key is required. Set JELLYFIN_API_KEY or pass --jellyfin-token.", file=sys.stderr)
        return 2
    if not args.username:
        print("ERROR: Jellyfin username is required. Set JELLYFIN_USER_NAME or pass --username.", file=sys.stderr)
        return 2

    try:
        payload = run_pipeline(
            user_request=args.request,
            openai_api_key=openai_api_key,
            openai_model_normalize=args.openai_model_normalize,
            openai_model_select=args.openai_model_select,
            openai_model_episode=args.openai_model_episode,
            temperature_normalize=args.temperature_normalize,
            temperature_select=args.temperature_select,
            temperature_episode=args.temperature_episode,
            server_url=args.server_url,
            jellyfin_token=args.jellyfin_token,
            username=args.username,
            use_episode_metadata=args.use_episode_metadata,
            episode_overview_max_chars=args.episode_overview_max_chars,
            dump_prompts=args.dump_prompts,
        )
    except HTTPError as exc:
        payload = {"status": "error", "error": f"HTTP {exc.code}", "details": str(exc)}
    except URLError as exc:
        payload = {"status": "error", "error": "network_error", "details": str(exc)}
    except PipelineError as exc:
        payload = {"status": "error", "error": "pipeline_error", "details": str(exc)}
    except Exception as exc:
        payload = {"status": "error", "error": "unexpected_error", "details": str(exc)}

    text = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
    print(text)
    return 0 if payload.get("status") in {"ok", "needs_clarification"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
