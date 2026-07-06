from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "core" / "jellyfin_nl_to_item_id_e2e.py"
DOTENV_PATH = PROJECT_ROOT / ".env"
LIVE_OUTPUT_ENV = "JELLYFIN_TEST_LIVE_OUTPUT"


TEST_CASES: list[dict[str, Any]] = [
    {
        "name": "scrubs_first_night_hospital",
        "request": "Включи серию Клиники, где Келсо обещает поездку в Рино за лучший клинический случай, а Тёрк пытается выбить новый лазер.",
        "expected_media_type": "series",
        "expected_media_name": "Scrubs",
        "expected_season": 2,
        "expected_episode": 3,
        "expected_resolved_title": "My Case Study",
    },
    {
        "name": "scrubs_janitor_wedding",
        "request": "включи серию клиники где свадьба уборщика",
        "expected_media_type": "series",
        "expected_media_name": "Scrubs",
        "expected_episode_options": [
            {"season": 8, "episode": 14, "title": "My Soul on Fire (1)"},
            {"season": 8, "episode": 15, "title": "My Soul on Fire (2)"},
        ],
    },
    {
        "name": "gravity_falls_pool_merman",
        "request": "включи серию гравити фолз где русал застрял в бассеине",
        "expected_media_type": "series",
        "expected_media_name": "Gravity Falls",
        "expected_season": 1,
        "expected_episode": 15,
        "expected_resolved_title": "The Deep End",
    },
    {
        "name": "harry_potter_giant_snake_movie",
        "request": "включи фильм про мальчика вошшебника где он он сражается с большой змеей",
        "expected_media_type": "movie",
        "expected_media_name": "Harry Potter and the Chamber of Secrets",
        "expected_resolved_title": "Harry Potter and the Chamber of Secrets",
    },
    {
        "name": "the_office_michael_carpet",
        "request": "включи серию офиса где майклу насрали на ковер",
        "expected_media_type": "series",
        "expected_media_name": "The Office",
        "expected_season": 2,
        "expected_episode": 14,
        "expected_resolved_title": "The Carpet",
    },
    {
        "name": "the_office_explicit_s05e06",
        "request": "включи 6 серию 5 сезона офиса",
        "expected_media_type": "series",
        "expected_media_name": "The Office",
        "expected_season": 5,
        "expected_episode": 6,
        "expected_resolved_title": "Customer Survey",
    },
    {
        "name": "scrubs_any_episode_season5",
        "request": "включи какую нибудь серию из 5 сезона клиники",
        "expected_media_type": "series",
        "expected_media_name": "Scrubs",
        "expected_season": 5,
    },
]


def _load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _extract_json_object(text: str) -> dict[str, Any]:
    # Script usually prints only JSON, but this keeps the parser robust.
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AssertionError(f"Cannot find JSON object in output:\n{text}")
    return json.loads(stripped[start : end + 1])


class JellyfinNLToItemIdE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_env = os.environ.copy()
        cls.base_env.update(_load_dotenv(DOTENV_PATH))
        # Ensure child Python processes print UTF-8 in live mode.
        cls.base_env.setdefault("PYTHONIOENCODING", "utf-8")
        cls.base_env.setdefault("PYTHONUTF8", "1")

        required = [
            "OPENAI_API_KEY",
            "JELLYFIN_SERVER_URL",
            "JELLYFIN_USER_NAME",
            "JELLYFIN_API_KEY",
        ]
        missing = [name for name in required if not cls.base_env.get(name)]
        if missing:
            raise unittest.SkipTest(f"Missing required env vars for e2e tests: {', '.join(missing)}")

    def _run_case(self, request: str) -> dict[str, Any]:
        cmd = [
            sys.executable,
            str(SCRIPT_PATH),
            "--request",
            request,
            "--use-episode-metadata",
            "--pretty",
        ]
        live_output = os.getenv(LIVE_OUTPUT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

        if live_output:
            print(f"\n--- Running resolver for request: {request}")
            proc = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=self.base_env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=180,
                check=False,
            )
            if proc.stdout:
                print(proc.stdout)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
            if proc.returncode != 0:
                raise AssertionError(
                    "Resolver script failed.\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Exit code: {proc.returncode}"
                )
        else:
            proc = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=self.base_env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=180,
                check=False,
            )
        if proc.returncode != 0:
            raise AssertionError(
                "Resolver script failed.\n"
                f"Command: {' '.join(cmd)}\n"
                f"Exit code: {proc.returncode}\n"
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}"
            )

        payload = _extract_json_object(proc.stdout)
        return payload

    def test_queries_resolve_expected_media(self) -> None:
        for case in TEST_CASES:
            with self.subTest(case=case["name"]):
                print(f"\n=== CASE: {case['name']}")
                payload = self._run_case(case["request"])
                expected_status = case.get("expected_status", "ok")
                self.assertEqual(payload.get("status"), expected_status, payload)

                selected_media = payload.get("selected_media") or {}
                self.assertEqual(selected_media.get("type"), case["expected_media_type"], payload)
                self.assertEqual(selected_media.get("name"), case["expected_media_name"], payload)

                if case["expected_media_type"] == "series":
                    series_resolution = payload.get("series_resolution") or {}
                    season = series_resolution.get("season")
                    episode = series_resolution.get("episode")
                    self.assertIsInstance(season, int, payload)

                    expected_episode_null = bool(case.get("expected_episode_null", False))
                    if expected_episode_null:
                        self.assertIsNone(episode, payload)
                    else:
                        self.assertIsInstance(episode, int, payload)

                    expected_options = case.get("expected_episode_options")
                    if expected_options is not None:
                        resolved_item = payload.get("resolved_item") or {}
                        actual = {
                            "season": season,
                            "episode": episode,
                            "title": resolved_item.get("title"),
                        }
                        self.assertIn(actual, expected_options, payload)
                        continue

                    expected_season = case.get("expected_season")
                    expected_episode = case.get("expected_episode")
                    expected_title = case.get("expected_resolved_title")

                    if expected_season is not None:
                        self.assertEqual(season, expected_season, payload)
                    else:
                        min_season, max_season = case["expected_season_range"]
                        self.assertGreaterEqual(season, min_season, payload)
                        self.assertLessEqual(season, max_season, payload)

                    if expected_episode_null:
                        self.assertIsNone(episode, payload)
                    elif expected_episode is not None:
                        self.assertEqual(episode, expected_episode, payload)
                    else:
                        self.assertGreater(episode, 0, payload)

                    if expected_title is not None:
                        resolved_item = payload.get("resolved_item") or {}
                        self.assertEqual(resolved_item.get("title"), expected_title, payload)
                else:
                    self.assertIsNone(payload.get("series_resolution"), payload)
                    resolved_item = payload.get("resolved_item") or {}
                    self.assertEqual(resolved_item.get("title"), case["expected_resolved_title"], payload)


if __name__ == "__main__":
    unittest.main()
