#!/usr/bin/env python3
"""
Build a prompt that converts an action-style media command
into a clean search query.

Example input:
  "включи серию клиники, где свадьба уборщика"

Expected LLM output format:
  {"search_query":"клиника свадьба уборщика"}
"""

from __future__ import annotations

import argparse
import json


def build_prompt(user_request: str) -> str:
    return (
        "You are a query normalizer for Jellyfin media search.\n"
        "You are given the original user command in conversational form.\n\n"
        "Task:\n"
        "1) Remove action verbs and command filler words (for example: play, start, put on, please).\n"
        "2) Keep only search intent: media title + important plot hints.\n"
        "3) Fix obvious typos and normalize wording for search.\n"
        "4) The output search_query MUST be in English.\n"
        "5) For movie/series names, do not do a literal translation. Use the canonical English title"
        " that the title is commonly known by (for example: клиника -> Scrubs, офис -> The Office,"
        " гарри поттер и кубок огня -> Harry Potter and the Goblet of Fire,"
        " как это работает -> How It's Made).\n"
        "6) If the exact canonical title is uncertain, use the most likely widely used English variant"
        " without inventing new facts.\n"
        "7) Do not add facts that are not present in the user request.\n\n"
        "Return STRICT JSON only (no markdown, no explanations):\n"
        "{\"search_query\":\"...\"}\n\n"
        "Examples:\n"
        "Input: включи серию клиники, где свадьба уборщика\n"
        "Output: {\"search_query\":\"Scrubs janitor wedding episode\"}\n\n"
        "Input: запусти гравити фолз где русалдо попал в бассейн\n"
        "Output: {\"search_query\":\"Gravity Falls Mermando pool episode\"}\n\n"
        "Input: включи серию как это работает про туалетную бумагу\n"
        "Output: {\"search_query\":\"How It's Made toilet paper episode\"}\n\n"
        "Original user command:\n"
        f"{user_request}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build prompt to normalize action request into search query")
    parser.add_argument("--request", required=True, help="Original user action-style request")
    parser.add_argument("--output", default="", help="Optional file path to save generated prompt")
    parser.add_argument("--json", action="store_true", help="Print prompt wrapped in JSON payload")
    args = parser.parse_args()

    prompt = build_prompt(args.request)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(prompt)

    if args.json:
        print(json.dumps({"prompt": prompt}, ensure_ascii=False))
    else:
        print(prompt)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
