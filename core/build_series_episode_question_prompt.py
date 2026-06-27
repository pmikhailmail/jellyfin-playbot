#!/usr/bin/env python3
"""
Build a follow-up prompt that asks for season/episode of a specific series.

Input example:
- series name: Scrubs
- user request: the janitor's wedding

Output target:
- a query like: "which season and episode of Scrubs has the janitor's wedding"
- optional JSON-oriented prompt for an LLM
"""

from __future__ import annotations

import argparse
import json


def _sanitize_text(text: str, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def build_search_question(series_name: str, user_request: str) -> str:
    series = series_name.strip()
    request = user_request.strip()
    return f"which season and episode of {series} matches: {request}".strip()


def build_llm_prompt(
    search_question: str,
    episode_candidates: list[dict[str, object]] | None = None,
    overview_max_chars: int = 220,
) -> str:
    metadata_block = ""
    if episode_candidates:
        lines: list[str] = []
        for idx, candidate in enumerate(episode_candidates, start=1):
            season = candidate.get("season")
            episode = candidate.get("episode")
            title = str(candidate.get("title") or "")
            overview = str(candidate.get("overview") or "")
            header = f"{idx}. S{season}E{episode} - {title}" if season and episode else f"{idx}. {title}"
            if overview:
                lines.append(f"{header} | { _sanitize_text(overview, overview_max_chars) }")
            else:
                lines.append(header)

        episode_list = "\n".join(lines)
        metadata_block = (
            "\n"
            "Below is a list of real episodes for the already selected series from Jellyfin.\n"
            "Use this list as the source of truth and return season/episode only from this list.\n"
            "For vague or free-form requests, choose the best semantic match instead of defaulting to null too early.\n"
            "Return null only when there is no reasonable candidate in the provided list.\n"
            "Episode list:\n"
            f"{episode_list}\n"
        )

    return (
        "You are selecting season and episode from a text request for an already selected series.\n"
        "Return the answer strictly as JSON in a fixed format.\n"
        "No markdown, no extra text, no comments.\n"
        "Only two keys are allowed: season and episode.\n"
        "Both fields must be integer or null.\n"
        "If the request is broad, mood-based, or otherwise free-form, infer the best semantic episode match from the list.\n"
        "Use null only when no candidate in the list is a reasonable match.\n"
        "Do not add any other keys (for example confidence, candidates, reason).\n"
        "The only valid response format is:\n"
        "{\"season\": <integer|null>, \"episode\": <integer|null>}\n\n"
        f"{metadata_block}"
        "Request:\n"
        f"{search_question}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build season/episode question prompt")
    parser.add_argument("--series-name", required=True, help="Chosen series name")
    parser.add_argument("--user-request", required=True, help="Original or normalized user request")
    parser.add_argument(
        "--mode",
        choices=["question", "prompt"],
        default="prompt",
        help="question: print only transformed question, prompt: print full LLM prompt",
    )
    parser.add_argument("--output", default="", help="Optional file path to save result")
    parser.add_argument("--json", action="store_true", help="Return deterministic JSON payload")
    args = parser.parse_args()

    question = build_search_question(args.series_name, args.user_request)
    result = question if args.mode == "question" else build_llm_prompt(question)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)

    if args.json:
        if args.mode == "question":
            payload = {"search_question": question}
        else:
            payload = {
                "prompt": result,
                "expected_response_format": {
                    "season": "integer|null",
                    "episode": "integer|null",
                },
            }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
