#!/usr/bin/env python3
"""Generate lightweight league events from hidden personalities.

This system intentionally starts as news-only. It creates public league-news
items that make the save feel alive, but it does not yet mutate injuries,
suspensions, contracts, morale, or ratings.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import league_news
import league_calendar


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.schema import ensure_schema as ensure_draft_schema


PLAYER_TRAITS = {
    "off_field_issue": {
        "rate": 0.006,
        "category": "Discipline",
        "priority": "high",
        "source": "Sim League Wire",
        "tags": ["personality", "availability", "rumor"],
        "major": True,
        "templates": [
            {
                "key": "availability-review",
                "title": "{name} draws internal availability review",
                "body": (
                    "In the save universe, {team} staff is reviewing an off-field availability flag "
                    "around {name}. No roster status change has been made, but clubs around the "
                    "league will monitor whether it develops into a discipline issue."
                ),
            },
            {
                "key": "conduct-monitor",
                "title": "{team} monitoring {name} situation",
                "body": (
                    "{team} staffers are quietly treating {name}'s week as one to monitor after "
                    "an internal conduct note surfaced in the save. The first pass is only a "
                    "public rumor; any future gameplay effect would need a separate roll."
                ),
            },
        ],
    },
    "locker_room_distraction": {
        "rate": 0.017,
        "category": "Rumors",
        "priority": "normal",
        "source": "Locker Room Wire",
        "tags": ["personality", "rumor", "culture"],
        "major": False,
        "templates": [
            {
                "key": "role-friction",
                "title": "Role friction follows {name}",
                "body": (
                    "People around {team} believe {name} is worth watching if snaps or touches "
                    "tighten. The concern is not talent; it is whether the week stays clean if "
                    "the role changes."
                ),
            },
            {
                "key": "staff-temperature",
                "title": "{team} staff keeps tabs on {name}'s temperature",
                "body": (
                    "{name} has enough presence to swing a room, for better or worse. The club "
                    "is trying to keep communication direct before a minor frustration becomes "
                    "a bigger story."
                ),
            },
        ],
    },
    "greedy": {
        "rate": 0.014,
        "category": "Rumors",
        "priority": "normal",
        "source": "Contract Wire",
        "tags": ["personality", "contract", "rumor"],
        "major": False,
        "templates": [
            {
                "key": "guarantees",
                "title": "{name}'s camp expected to push guarantees",
                "body": (
                    "League sources expect {name}'s representation to prioritize guaranteed "
                    "money whenever the next negotiation arrives. {team} can manage it, but "
                    "the ask may be less team-friendly than usual."
                ),
            },
            {
                "key": "market-watch",
                "title": "Market watchers circle {name}",
                "body": (
                    "{name} is being mentioned by contract watchers as a player who could chase "
                    "the strongest offer rather than the cleanest fit."
                ),
            },
        ],
    },
    "natural_leader": {
        "rate": 0.010,
        "category": "Team Culture",
        "priority": "normal",
        "source": "Team Wire",
        "tags": ["personality", "leadership"],
        "major": False,
        "templates": [
            {
                "key": "sets-tone",
                "title": "{name} setting the tone for {team}",
                "body": (
                    "{team} coaches have noticed {name} taking extra ownership in position "
                    "meetings. Teammates are responding to the steady voice."
                ),
            },
            {
                "key": "player-led-work",
                "title": "{name} leads player-run work",
                "body": (
                    "{name} organized extra player-run work this week, a small note that can "
                    "matter over a long season."
                ),
            },
        ],
    },
    "lunch_pail": {
        "rate": 0.008,
        "category": "Roster",
        "priority": "normal",
        "source": "Practice Wire",
        "tags": ["personality", "work-ethic"],
        "major": False,
        "templates": [
            {
                "key": "practice-standout",
                "title": "{name} earns staff praise for weekly work",
                "body": (
                    "{team} staffers noted {name}'s practice habits again this week. It is not "
                    "flashy, but that kind of week-to-week floor can keep a player in plans."
                ),
            }
        ],
    },
    "film_junkie": {
        "rate": 0.008,
        "category": "Roster",
        "priority": "normal",
        "source": "Practice Wire",
        "tags": ["personality", "preparation"],
        "major": False,
        "templates": [
            {
                "key": "film-note",
                "title": "{name}'s preparation stands out",
                "body": (
                    "{team} coaches believe {name}'s film work is translating into cleaner "
                    "practice reps. It is a small edge, but a useful one."
                ),
            }
        ],
    },
    "mentor": {
        "rate": 0.007,
        "category": "Team Culture",
        "priority": "normal",
        "source": "Team Wire",
        "tags": ["personality", "development"],
        "major": False,
        "templates": [
            {
                "key": "young-room",
                "title": "{name} helping steady young players in {team}",
                "body": (
                    "{team} coaches credit {name} with helping younger players get through the "
                    "week's install. That sort of hidden value does not always show up in a box score."
                ),
            }
        ],
    },
    "jokester": {
        "rate": 0.005,
        "category": "Team Culture",
        "priority": "low",
        "source": "Team Wire",
        "tags": ["personality", "culture"],
        "major": False,
        "templates": [
            {
                "key": "keeps-light",
                "title": "{name} keeps the mood loose in {team}",
                "body": (
                    "{name} gave {team} a lighter week around the facility. Coaches usually do "
                    "not mind that when the work still gets done."
                ),
            }
        ],
    },
    "streaky_confidence": {
        "rate": 0.009,
        "category": "Roster",
        "priority": "normal",
        "source": "Practice Wire",
        "tags": ["personality", "volatility"],
        "major": False,
        "templates": [
            {
                "key": "volatile-week",
                "title": "{name}'s week draws mixed reviews",
                "body": (
                    "The latest word around {name} is that the practice reps flashed enough to stay interesting, "
                    "but the consistency still has to stabilize."
                ),
            }
        ],
    },
    "big_stage": {
        "rate": 0.007,
        "category": "Roster",
        "priority": "normal",
        "source": "League Wire",
        "tags": ["personality", "spotlight"],
        "major": False,
        "templates": [
            {
                "key": "spotlight",
                "title": "{name} embraces bigger spotlight",
                "body": (
                    "{name} has been mentioned as a player who looks comfortable when the week "
                    "gets louder. {team} will gladly take that edge."
                ),
            }
        ],
    },
    "media_savvy": {
        "rate": 0.006,
        "category": "Rumors",
        "priority": "low",
        "source": "Media Wire",
        "tags": ["personality", "media"],
        "major": False,
        "templates": [
            {
                "key": "media-polish",
                "title": "{name} handles media week smoothly",
                "body": (
                    "{name} gave {team} a clean media week, steering attention toward the club's "
                    "message instead of adding noise."
                ),
            }
        ],
    },
    "chip_on_shoulder": {
        "rate": 0.006,
        "category": "Roster",
        "priority": "normal",
        "source": "Practice Wire",
        "tags": ["personality", "motivation"],
        "major": False,
        "templates": [
            {
                "key": "edge",
                "title": "{name} practices with an edge",
                "body": (
                    "{team} staffers believe {name} is using outside doubt as fuel. It is early, "
                    "but the energy was noticeable."
                ),
            }
        ],
    },
}


PROSPECT_TRAITS = {
    "off_field_issue": {
        "rate": 0.025,
        "category": "Prospects",
        "priority": "high",
        "source": "Draft Wire",
        "tags": ["draft", "personality", "background", "rumor"],
        "major": True,
        "templates": [
            {
                "key": "background-check",
                "title": "Teams dig deeper on {name}'s background",
                "body": (
                    "A few scouting departments are doing extra background work on {name}, a "
                    "{position} from {college}. The concern is still in the rumor stage, but it "
                    "could add variance to the grade if more teams hear the same thing."
                ),
            }
        ],
    },
    "locker_room_distraction": {
        "rate": 0.020,
        "category": "Prospects",
        "priority": "normal",
        "source": "Draft Wire",
        "tags": ["draft", "personality", "rumor"],
        "major": False,
        "templates": [
            {
                "key": "fit-questions",
                "title": "Fit questions trail {name}",
                "body": (
                    "Scouts remain split on {name}'s personality fit. The tape is part of the "
                    "evaluation, but interviews and school calls may matter more than usual."
                ),
            }
        ],
    },
    "greedy": {
        "rate": 0.018,
        "category": "Prospects",
        "priority": "low",
        "source": "Draft Wire",
        "tags": ["draft", "personality", "market"],
        "major": False,
        "templates": [
            {
                "key": "brand-focus",
                "title": "{name}'s camp seen as brand-conscious",
                "body": (
                    "Teams expect {name} to care heavily about role, market, and long-term earning "
                    "path. That does not hurt the grade by itself, but evaluators will want clear buy-in."
                ),
            }
        ],
    },
    "natural_leader": {
        "rate": 0.016,
        "category": "Prospects",
        "priority": "normal",
        "source": "Draft Wire",
        "tags": ["draft", "leadership"],
        "major": False,
        "templates": [
            {
                "key": "captain-buzz",
                "title": "{name} gaining captain buzz",
                "body": (
                    "School contacts describe {name} as one of the steadier voices in the building. "
                    "That leadership note could help break ties on draft weekend."
                ),
            }
        ],
    },
    "lunch_pail": {
        "rate": 0.017,
        "category": "Prospects",
        "priority": "normal",
        "source": "Draft Wire",
        "tags": ["draft", "work-ethic"],
        "major": False,
        "templates": [
            {
                "key": "riser-work",
                "title": "{name} draws work-ethic praise",
                "body": (
                    "Area scouts keep bringing up {name}'s weekly habits at {college}. The public "
                    "board may not move yet, but team boards could warm up."
                ),
            }
        ],
    },
    "film_junkie": {
        "rate": 0.015,
        "category": "Prospects",
        "priority": "normal",
        "source": "Draft Wire",
        "tags": ["draft", "preparation"],
        "major": False,
        "templates": [
            {
                "key": "whiteboard",
                "title": "{name} expected to test well on boards",
                "body": (
                    "Teams that value mental processing may circle {name}. Early school calls "
                    "suggest the {position} can handle a detailed football conversation."
                ),
            }
        ],
    },
    "streaky_confidence": {
        "rate": 0.018,
        "category": "Prospects",
        "priority": "normal",
        "source": "Draft Wire",
        "tags": ["draft", "volatility", "rumor"],
        "major": False,
        "templates": [
            {
                "key": "volatile-board",
                "title": "{name}'s grade shows early volatility",
                "body": (
                    "{name} has enough flashes to move up boards, but scouts are still sorting "
                    "out the low-end outcomes. Expect a wider range than the public rank implies."
                ),
            }
        ],
    },
    "big_stage": {
        "rate": 0.014,
        "category": "Prospects",
        "priority": "normal",
        "source": "Draft Wire",
        "tags": ["draft", "spotlight"],
        "major": False,
        "templates": [
            {
                "key": "big-game",
                "title": "{name} circles a spotlight game",
                "body": (
                    "Scouts are watching how {name} handles the bigger moments on the schedule. "
                    "A strong showing could make the public board catch up to team interest."
                ),
            }
        ],
    },
}


PROSPECT_EVENT_RULES = {
    "big_game": {
        "rate": 0.014,
        "category": "Prospects",
        "priority": "normal",
        "source": "College Wire",
        "tags": ["draft", "performance", "riser"],
        "grade_delta": 1,
        "templates": [
            {
                "key": "spotlight-game",
                "title": "{name} forces another look after big game",
                "body": (
                    "{name}, a {position} from {college}, put together the kind of game that gets "
                    "cross-checkers involved. The current public grade may need a small nudge if "
                    "the follow-up tape backs it up."
                ),
            },
            {
                "key": "trait-game",
                "title": "{name}'s traits pop in conference matchup",
                "body": (
                    "A strong week from {name} gave scouts more evidence that the athletic profile "
                    "translates on Saturdays. This is not a finished evaluation, but it is useful fuel."
                ),
            },
        ],
    },
    "bad_game": {
        "rate": 0.007,
        "category": "Prospects",
        "priority": "normal",
        "source": "College Wire",
        "tags": ["draft", "performance", "faller"],
        "grade_delta": -1,
        "templates": [
            {
                "key": "rough-tape",
                "title": "Rough tape adds questions for {name}",
                "body": (
                    "{name} had a week that will send scouts back to the cutups. The tools are still "
                    "there, but the consistency grade may get a little more cautious."
                ),
            },
        ],
    },
    "minor_injury": {
        "rate": 0.010,
        "category": "Prospects",
        "priority": "normal",
        "source": "Medical Wire",
        "tags": ["draft", "medical", "injury"],
        "medical": "minor",
        "templates": [
            {
                "key": "limited-week",
                "title": "{name} expected to be limited after injury",
                "body": (
                    "{name} picked up an injury that is not expected to end the process, but teams "
                    "will track the medicals. If the timeline stretches, combine participation could "
                    "become a partial-workout situation."
                ),
            },
        ],
    },
    "combine_miss_injury": {
        "rate": 0.005,
        "category": "Prospects",
        "priority": "high",
        "source": "Medical Wire",
        "tags": ["draft", "medical", "injury", "combine"],
        "medical": "combine_out",
        "templates": [
            {
                "key": "combine-out",
                "title": "{name} unlikely to work out at combine",
                "body": (
                    "Medical feedback suggests {name} may not be ready in time for the scouting "
                    "combine. That would explain a non-participation tag and push more weight onto "
                    "medical checks, interviews, and any later pro-day work."
                ),
            },
        ],
    },
    "off_board_discovery": {
        "rate": 0.016,
        "category": "Prospects",
        "priority": "normal",
        "source": "Regional Scout Wire",
        "tags": ["draft", "discovery", "small-school", "rumor"],
        "discover": True,
        "templates": [
            {
                "key": "regional-discovery",
                "title": "Regional scouts surface {name}",
                "body": (
                    "{name}, a {position} from {college}, is starting to move from area-scout notes "
                    "into broader league conversations. The public board may still be sleeping on him."
                ),
            },
        ],
    },
    "toolsy_riser": {
        "rate": 0.011,
        "category": "Prospects",
        "priority": "normal",
        "source": "Draft Wire",
        "tags": ["draft", "traits", "riser"],
        "grade_delta": 1,
        "templates": [
            {
                "key": "traits-riser",
                "title": "{name}'s tools are getting louder",
                "body": (
                    "The more scouts watch {name}, the more the physical tools stand out. The "
                    "evaluation still depends on role fit, but the upside case is gaining oxygen."
                ),
            },
        ],
    },
}


@dataclass(frozen=True)
class GenerationResult:
    game_id: str
    season: int
    week: int
    event_date: str
    status: str
    planned_count: int
    inserted_count: int
    events: list[dict[str, Any]]
    message: str
    phase_code: str | None = None
    cadence: str = "auto"


@dataclass(frozen=True)
class EventCadence:
    code: str
    label: str
    phase_code: str | None
    rate_multiplier: float
    default_max_events: int
    default_min_events: int


EVENT_CATEGORY_CAPS_BY_CADENCE: dict[str, dict[str, int]] = {
    "regular_season": {"Prospects": 1, "Roster": 1, "Team Culture": 1, "Rumors": 1, "Discipline": 1},
    "postseason": {"Prospects": 1, "Roster": 1, "Team Culture": 1, "Rumors": 1, "Discipline": 1},
    "preseason": {"Prospects": 1, "Roster": 1, "Team Culture": 1, "Rumors": 1, "Discipline": 1},
    "market_draft": {"Prospects": 2, "Roster": 1, "Team Culture": 1, "Rumors": 1, "Discipline": 1},
    "offseason": {"Prospects": 1, "Roster": 1, "Team Culture": 1, "Rumors": 1, "Discipline": 1},
}


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    ensure_draft_schema(con)
    league_news.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS league_event_generation_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            run_key TEXT NOT NULL,
            seed TEXT,
            planned_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, season, week, run_key)
        );

        CREATE INDEX IF NOT EXISTS idx_league_event_generation_runs_game
            ON league_event_generation_runs(game_id, season, week);
        """
    )


def phase_code_for_date(con: sqlite3.Connection, event_date: str) -> str | None:
    try:
        phase = league_calendar.phase_for_date(con, event_date)
    except (sqlite3.OperationalError, ValueError):
        return None
    if not phase:
        return None
    return str(phase["phase_code"] or "") or None


def event_cadence(con: sqlite3.Connection, *, event_date: str, week: int) -> EventCadence:
    phase_code = phase_code_for_date(con, event_date)
    phase = (phase_code or "").upper()
    try:
        month = date.fromisoformat(event_date).month
    except ValueError:
        month = 9 if week >= 1 else 7

    if "REGULAR_SEASON" in phase or (1 <= week <= 18 and (month >= 9 or month == 1)):
        return EventCadence(
            code="regular_season",
            label="Regular season light",
            phase_code=phase_code,
            rate_multiplier=0.42,
            default_max_events=1,
            default_min_events=0,
        )
    if "POSTSEASON" in phase:
        return EventCadence(
            code="postseason",
            label="Postseason controlled",
            phase_code=phase_code,
            rate_multiplier=0.55,
            default_max_events=1,
            default_min_events=0,
        )
    if any(token in phase for token in ("CAMP", "PRESEASON", "CUTDOWN")) or month in {7, 8}:
        return EventCadence(
            code="preseason",
            label="Preseason buzz",
            phase_code=phase_code,
            rate_multiplier=0.90,
            default_max_events=2,
            default_min_events=0,
        )
    if month in {3, 4}:
        return EventCadence(
            code="market_draft",
            label="Market and draft buzz",
            phase_code=phase_code,
            rate_multiplier=1.05,
            default_max_events=3,
            default_min_events=1,
        )
    return EventCadence(
        code="offseason",
        label="Offseason buzz",
        phase_code=phase_code,
        rate_multiplier=0.80,
        default_max_events=2,
        default_min_events=0,
    )


def latest_personality_season(con: sqlite3.Connection, game_id: str, season: int) -> int | None:
    if not table_exists(con, "player_personalities"):
        return None
    row = con.execute(
        """
        SELECT season
        FROM player_personalities
        WHERE game_id = ?
          AND season <= ?
        GROUP BY season
        ORDER BY season DESC
        LIMIT 1
        """,
        (game_id, season),
    ).fetchone()
    if row:
        return int(row["season"])
    row = con.execute(
        """
        SELECT season
        FROM player_personalities
        GROUP BY season
        ORDER BY season DESC
        LIMIT 1
        """
    ).fetchone()
    return int(row["season"]) if row else None


def format_height(height_in: int | None) -> str:
    if not height_in:
        return "-"
    feet = int(height_in) // 12
    inches = int(height_in) % 12
    return f"{feet}'{inches}\""


def row_int(row: sqlite3.Row, key: str, fallback: int = 0) -> int:
    value = row[key] if key in row.keys() else None
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))


def build_context(row: sqlite3.Row, *, source_type: str) -> dict[str, Any]:
    keys = row.keys()
    name = str(row["full_name"] or "Unknown Player")
    team_name = str((row["team_name"] if "team_name" in keys else None) or (row["team"] if "team" in keys else None) or "club")
    college = str((row["college"] if "college" in keys else None) or "college")
    position = str(row["position"] or "player")
    return {
        "name": name,
        "team": team_name,
        "team_abbr": str((row["team"] if "team" in keys else None) or ""),
        "position": position,
        "college": college,
        "height": format_height(row_int(row, "height_in", 0)),
        "weight": row_int(row, "weight_lbs", 0),
        "source_type": source_type,
    }


def combine_start_date(con: sqlite3.Connection, draft_year: int) -> str | None:
    if not table_exists(con, "league_calendar_events"):
        return None
    row = con.execute(
        """
        SELECT event_start_date
        FROM league_calendar_events
        WHERE event_code = 'SCOUTING_COMBINE'
          AND league_year = ?
        ORDER BY event_start_date
        LIMIT 1
        """,
        (draft_year,),
    ).fetchone()
    return str(row["event_start_date"]) if row else None


def append_note(existing: str | None, note: str) -> str:
    existing = str(existing or "").strip()
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} {note}"


def candidate_from_rule(
    *,
    row: sqlite3.Row,
    rule: dict[str, Any],
    template: dict[str, str],
    context: dict[str, Any],
    game_id: str,
    season: int,
    week: int,
    run_key: str,
    source_type: str,
    source_id: int,
    trait_key: str,
    score: float,
) -> dict[str, Any]:
    is_star = row_int(row, "overall", 0) >= 85 or row_int(row, "scout_grade", 0) >= 76
    is_major = bool(rule.get("major")) and (is_star or trait_key == "off_field_issue")
    return {
        "category": str(rule["category"]),
        "priority": "high" if is_major else str(rule.get("priority") or "normal"),
        "source": str(rule.get("source") or "League Wire"),
        "title": template["title"].format(**context),
        "body": template["body"].format(**context),
        "team_id": row_int(row, "team_id", 0) or None,
        "player_id": source_id if source_type == "player" else None,
        "prospect_id": source_id if source_type == "prospect" else None,
        "related_table": "players" if source_type == "player" else "draft_prospects",
        "related_id": source_id,
        "tags": list(rule.get("tags") or []),
        "is_major": is_major,
        "fingerprint": league_news.fingerprint_for(
            "weekly-event",
            game_id,
            season,
            week,
            run_key,
            source_type,
            source_id,
            trait_key,
            template["key"],
        ),
        "roll_score": score,
        "debug_trait": trait_key,
    }


def player_personality_candidates(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    run_key: str,
    rng: random.Random,
    rate_multiplier: float = 1.0,
) -> list[dict[str, Any]]:
    if not table_exists(con, "player_personalities") or not table_exists(con, "players"):
        return []
    personality_season = latest_personality_season(con, game_id, season)
    if personality_season is None:
        return []
    trait_keys = tuple(PLAYER_TRAITS)
    placeholders = ",".join("?" for _ in trait_keys)
    rows = con.execute(
        f"""
        SELECT
            pp.player_id,
            pp.trait_key,
            pp.intensity,
            p.first_name || ' ' || p.last_name AS full_name,
            p.position,
            p.team_id,
            p.age,
            p.height_in,
            p.weight_lbs,
            p.overall,
            p.status,
            t.abbreviation AS team,
            TRIM(COALESCE(t.city, '') || ' ' || COALESCE(t.nickname, '')) AS team_name
        FROM player_personalities pp
        JOIN players p ON p.player_id = pp.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE pp.game_id = ?
          AND pp.season = ?
          AND pp.trait_key IN ({placeholders})
          AND COALESCE(p.status, 'Active') NOT IN ('Retired')
          AND p.team_id IS NOT NULL
        """,
        (game_id, personality_season, *trait_keys),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        trait_key = str(row["trait_key"])
        rule = PLAYER_TRAITS[trait_key]
        intensity = row_int(row, "intensity", 50)
        rate = float(rule["rate"]) * (0.65 + intensity / 100.0 * 0.70) * rate_multiplier
        overall = row_int(row, "overall", 65)
        if overall >= 90:
            rate *= 1.45
        elif overall >= 82:
            rate *= 1.20
        if str(row["position"] or "") == "QB":
            rate *= 1.12
        roll = rng.random()
        if roll > rate:
            continue
        template = rng.choice(rule["templates"])
        score = rate - roll + rng.random() * 0.01
        candidates.append(
            candidate_from_rule(
                row=row,
                rule=rule,
                template=template,
                context=build_context(row, source_type="player"),
                game_id=game_id,
                season=season,
                week=week,
                run_key=run_key,
                source_type="player",
                source_id=row_int(row, "player_id"),
                trait_key=trait_key,
                score=score,
            )
        )
    return candidates


def prospect_personality_candidates(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    run_key: str,
    rng: random.Random,
    rate_multiplier: float = 1.0,
) -> list[dict[str, Any]]:
    if not table_exists(con, "draft_prospect_personalities") or not table_exists(con, "draft_prospects"):
        return []
    trait_keys = tuple(PROSPECT_TRAITS)
    placeholders = ",".join("?" for _ in trait_keys)
    rows = con.execute(
        f"""
        SELECT
            dpp.prospect_id,
            dpp.trait_key,
            dpp.intensity,
            dp.first_name || ' ' || dp.last_name AS full_name,
            dp.position,
            dp.college,
            dp.college_tier,
            dp.age,
            dp.height_in,
            dp.weight_lbs,
            dp.scout_grade,
            dp.public_board_rank,
            dp.public_board_status,
            dp.risk_level,
            dc.draft_year,
            NULL AS team_id,
            NULL AS team,
            NULL AS team_name
        FROM draft_prospect_personalities dpp
        JOIN draft_prospects dp ON dp.prospect_id = dpp.prospect_id
        LEFT JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        WHERE dpp.trait_key IN ({placeholders})
          AND COALESCE(dc.draft_year, ?) >= ?
          AND dp.selected_pick_id IS NULL
        """,
        (*trait_keys, season + 1, season + 1),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        trait_key = str(row["trait_key"])
        rule = PROSPECT_TRAITS[trait_key]
        intensity = row_int(row, "intensity", 50)
        rate = float(rule["rate"]) * (0.70 + intensity / 100.0 * 0.65) * rate_multiplier
        grade = row_int(row, "scout_grade", 60)
        public_rank = row_int(row, "public_board_rank", 999)
        if grade >= 76 or public_rank <= 64:
            rate *= 1.30
        if str(row["public_board_status"] or "") == "off_public_board":
            rate *= 1.15
        roll = rng.random()
        if roll > rate:
            continue
        template = rng.choice(rule["templates"])
        score = rate - roll + rng.random() * 0.01
        candidates.append(
            candidate_from_rule(
                row=row,
                rule=rule,
                template=template,
                context=build_context(row, source_type="prospect"),
                game_id=game_id,
                season=season,
                week=week,
                run_key=run_key,
                source_type="prospect",
                source_id=row_int(row, "prospect_id"),
                trait_key=trait_key,
                score=score,
            )
        )
    return candidates


def prospect_event_candidate_from_rule(
    *,
    row: sqlite3.Row,
    rule: dict[str, Any],
    template: dict[str, str],
    context: dict[str, Any],
    game_id: str,
    season: int,
    week: int,
    run_key: str,
    event_type: str,
    score: float,
) -> dict[str, Any]:
    prospect_id = row_int(row, "prospect_id")
    is_major = event_type == "combine_miss_injury" or row_int(row, "scout_grade", 0) >= 76
    item = {
        "category": str(rule["category"]),
        "priority": "high" if is_major else str(rule.get("priority") or "normal"),
        "source": str(rule.get("source") or "Draft Wire"),
        "title": template["title"].format(**context),
        "body": template["body"].format(**context),
        "team_id": None,
        "player_id": None,
        "prospect_id": prospect_id,
        "related_table": "draft_prospects",
        "related_id": prospect_id,
        "tags": list(rule.get("tags") or []),
        "is_major": is_major,
        "fingerprint": league_news.fingerprint_for(
            "weekly-event",
            game_id,
            season,
            week,
            run_key,
            "prospect-event",
            prospect_id,
            event_type,
            template["key"],
        ),
        "roll_score": score,
        "debug_trait": event_type,
        "event_type": event_type,
        "effects": {
            "grade_delta": int(rule.get("grade_delta") or 0),
            "medical": rule.get("medical"),
            "discover": bool(rule.get("discover")),
        },
    }
    return item


def prospect_event_candidates(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    run_key: str,
    event_date: str,
    cadence: EventCadence,
    rng: random.Random,
) -> list[dict[str, Any]]:
    if not table_exists(con, "draft_prospects"):
        return []
    draft_year = season + 1
    combine_date = combine_start_date(con, draft_year)
    before_combine = bool(combine_date and event_date <= combine_date)
    try:
        month = date.fromisoformat(event_date).month
    except ValueError:
        month = 9
    college_season = month in {8, 9, 10, 11, 12}
    market_window = cadence.code in {"market_draft", "postseason"} or month in {1, 2, 3, 4}
    rows = con.execute(
        """
        WITH rating_pivot AS (
            SELECT
                prospect_id,
                MAX(CASE WHEN rating_key = 'speed' THEN rating_value END) AS r_speed,
                MAX(CASE WHEN rating_key = 'agility' THEN rating_value END) AS r_agility,
                MAX(CASE WHEN rating_key = 'strength' THEN rating_value END) AS r_strength,
                MAX(CASE WHEN rating_key = 'durability' THEN rating_value END) AS r_durability,
                MAX(CASE WHEN rating_key = 'consistency' THEN rating_value END) AS r_consistency,
                MAX(CASE WHEN rating_key = 'composure' THEN rating_value END) AS r_composure,
                MAX(CASE WHEN rating_key = 'play_recognition' THEN rating_value END) AS r_play_recognition
            FROM draft_prospect_ratings
            GROUP BY prospect_id
        ),
        trait_pivot AS (
            SELECT
                prospect_id,
                MAX(CASE WHEN trait_key = 'big_stage' THEN intensity END) AS t_big_stage,
                MAX(CASE WHEN trait_key = 'chip_on_shoulder' THEN intensity END) AS t_chip,
                MAX(CASE WHEN trait_key = 'film_junkie' THEN intensity END) AS t_film,
                MAX(CASE WHEN trait_key = 'lunch_pail' THEN intensity END) AS t_lunch,
                MAX(CASE WHEN trait_key = 'streaky_confidence' THEN intensity END) AS t_streaky,
                MAX(CASE WHEN trait_key = 'off_field_issue' THEN intensity END) AS t_off_field
            FROM draft_prospect_personalities
            GROUP BY prospect_id
        )
        SELECT
            dp.prospect_id,
            dp.first_name || ' ' || dp.last_name AS full_name,
            dp.position,
            dp.position_group,
            dp.college,
            dp.college_tier,
            dp.age,
            dp.height_in,
            dp.weight_lbs,
            dp.true_grade,
            dp.ceiling_grade,
            dp.scout_grade,
            dp.scout_ceiling,
            dp.scout_confidence,
            dp.scouting_variance,
            dp.public_board_rank,
            dp.true_rank,
            dp.public_board_status,
            dp.discovery_status,
            dp.discovery_notes,
            dp.risk_level,
            dp.scout_risk,
            dp.injury_prone,
            COALESCE(rp.r_speed, dp.speed, 50) AS r_speed,
            COALESCE(rp.r_agility, dp.agility, 50) AS r_agility,
            COALESCE(rp.r_strength, dp.strength, 50) AS r_strength,
            COALESCE(rp.r_durability, 65) AS r_durability,
            COALESCE(rp.r_consistency, 50) AS r_consistency,
            COALESCE(rp.r_composure, 50) AS r_composure,
            COALESCE(rp.r_play_recognition, 50) AS r_play_recognition,
            COALESCE(tp.t_big_stage, 0) AS t_big_stage,
            COALESCE(tp.t_chip, 0) AS t_chip,
            COALESCE(tp.t_film, 0) AS t_film,
            COALESCE(tp.t_lunch, 0) AS t_lunch,
            COALESCE(tp.t_streaky, 0) AS t_streaky,
            COALESCE(tp.t_off_field, 0) AS t_off_field,
            dpc.combine_status,
            dpc.is_injured AS combine_injured,
            dpc.is_top_skip AS combine_top_skip,
            dpd.pro_day_status,
            NULL AS team_id,
            NULL AS team,
            NULL AS team_name
        FROM draft_prospects dp
        LEFT JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        LEFT JOIN rating_pivot rp ON rp.prospect_id = dp.prospect_id
        LEFT JOIN trait_pivot tp ON tp.prospect_id = dp.prospect_id
        LEFT JOIN draft_prospect_combine_results dpc ON dpc.prospect_id = dp.prospect_id
        LEFT JOIN draft_prospect_pro_day_results dpd ON dpd.prospect_id = dp.prospect_id
        WHERE COALESCE(dc.draft_year, ?) = ?
          AND dp.selected_pick_id IS NULL
          AND COALESCE(dp.status, 'Available') = 'Available'
        """,
        (draft_year, draft_year),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        context = build_context(row, source_type="prospect")
        scout_grade = row_int(row, "scout_grade", 60)
        true_grade = row_int(row, "true_grade", scout_grade)
        ceiling = row_int(row, "ceiling_grade", true_grade)
        variance = abs(row_int(row, "scouting_variance", 0))
        athletic = max(row_int(row, "r_speed", 50), row_int(row, "r_agility", 50), row_int(row, "r_strength", 50))
        durability = row_int(row, "r_durability", max(0, 100 - row_int(row, "injury_prone", 50)))
        consistency = row_int(row, "r_consistency", 50)
        big_stage = row_int(row, "t_big_stage", 0)
        chip = row_int(row, "t_chip", 0)
        film = row_int(row, "t_film", 0)
        lunch = row_int(row, "t_lunch", 0)
        streaky = row_int(row, "t_streaky", 0)
        off_board = str(row["public_board_status"] or "") == "off_public_board"
        already_discovered = str(row["discovery_status"] or "") == "discovered"
        grade_gap = true_grade - scout_grade

        event_rates = {
            "big_game": PROSPECT_EVENT_RULES["big_game"]["rate"]
            * (1.0 + max(0, grade_gap) / 24.0 + big_stage / 180.0 + athletic / 250.0)
            * (1.45 if college_season else 0.55),
            "bad_game": PROSPECT_EVENT_RULES["bad_game"]["rate"]
            * (1.0 + max(0, 58 - consistency) / 40.0 + streaky / 150.0)
            * (1.30 if college_season else 0.45),
            "minor_injury": PROSPECT_EVENT_RULES["minor_injury"]["rate"]
            * (1.0 + max(0, 68 - durability) / 42.0 + max(0, row_int(row, "injury_prone", 50) - 55) / 55.0),
            "combine_miss_injury": PROSPECT_EVENT_RULES["combine_miss_injury"]["rate"]
            * (1.0 + max(0, 65 - durability) / 35.0 + (0.35 if str(row["risk_level"] or "") == "High" else 0.0))
            * (1.50 if before_combine and market_window else 0.15),
            "off_board_discovery": PROSPECT_EVENT_RULES["off_board_discovery"]["rate"]
            * (1.0 + variance / 120.0 + max(0, grade_gap) / 20.0 + chip / 180.0)
            * (1.80 if off_board and not already_discovered else 0.20),
            "toolsy_riser": PROSPECT_EVENT_RULES["toolsy_riser"]["rate"]
            * (1.0 + max(0, ceiling - scout_grade) / 24.0 + athletic / 220.0 + (film + lunch) / 260.0),
        }
        for event_type, base_rate in event_rates.items():
            if event_type == "combine_miss_injury" and (not before_combine or row_int(row, "combine_injured", 0)):
                continue
            if event_type == "off_board_discovery" and (not off_board or already_discovered):
                continue
            if event_type in {"big_game", "bad_game"} and not college_season:
                continue
            rate = float(base_rate) * cadence.rate_multiplier
            roll = rng.random()
            if roll > rate:
                continue
            rule = PROSPECT_EVENT_RULES[event_type]
            template = rng.choice(rule["templates"])
            score = rate - roll + rng.random() * 0.01
            candidates.append(
                prospect_event_candidate_from_rule(
                    row=row,
                    rule=rule,
                    template=template,
                    context=context,
                    game_id=game_id,
                    season=season,
                    week=week,
                    run_key=run_key,
                    event_type=event_type,
                    score=score,
                )
            )
    return candidates


def fallback_candidates(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    run_key: str,
    rng: random.Random,
    needed: int,
) -> list[dict[str, Any]]:
    if needed <= 0 or not table_exists(con, "draft_prospects"):
        return []
    rows = con.execute(
        """
        SELECT
            dp.prospect_id,
            dp.first_name || ' ' || dp.last_name AS full_name,
            dp.position,
            dp.college,
            dp.height_in,
            dp.weight_lbs,
            dp.scout_grade,
            dp.public_board_rank,
            dp.public_board_status,
            NULL AS team_id,
            NULL AS team,
            NULL AS team_name
        FROM draft_prospects dp
        LEFT JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        WHERE COALESCE(dc.draft_year, ?) >= ?
          AND dp.selected_pick_id IS NULL
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (season + 1, season + 1, max(needed * 4, 12)),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        off_board = str(row["public_board_status"] or "") == "off_public_board"
        if off_board:
            title = "Regional scouts add {name} to deeper watch list"
            body = (
                "{name}, a {position} from {college}, is starting to get a few extra area-scout "
                "mentions. The name is still outside the public board, but the class has room "
                "for late discoveries."
            )
            tags = ["draft", "prospects", "discovery", "rumor"]
        else:
            title = "Scouts split on {name}'s early range"
            body = (
                "{name} remains a player with a wider team-board range than the public rank "
                "suggests. More cross-checking should narrow the grade as the year moves along."
            )
            tags = ["draft", "prospects", "rumor"]
        context = build_context(row, source_type="prospect")
        candidates.append(
            {
                "category": "Prospects",
                "priority": "normal",
                "source": "Draft Wire",
                "title": title.format(**context),
                "body": body.format(**context),
                "team_id": None,
                "player_id": None,
                "prospect_id": row_int(row, "prospect_id"),
                "related_table": "draft_prospects",
                "related_id": row_int(row, "prospect_id"),
                "tags": tags,
                "is_major": False,
                "fingerprint": league_news.fingerprint_for(
                    "weekly-event",
                    game_id,
                    season,
                    week,
                    run_key,
                    "fallback-prospect",
                    row_int(row, "prospect_id"),
                ),
                "roll_score": rng.random() * 0.001,
                "debug_trait": None,
            }
        )
    return candidates


def select_events(
    candidates: list[dict[str, Any]],
    *,
    max_events: int,
    cadence_code: str = "offseason",
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_sources: set[tuple[str | None, int | None]] = set()
    trait_counts: dict[str, int] = {}
    team_counts: dict[int, int] = {}
    category_counts: dict[str, int] = {}
    category_caps = EVENT_CATEGORY_CAPS_BY_CADENCE.get(cadence_code, EVENT_CATEGORY_CAPS_BY_CADENCE["offseason"])
    ordered = sorted(candidates, key=lambda event: float(event.get("roll_score") or 0), reverse=True)

    def try_add(item: dict[str, Any], *, diversity_pass: bool) -> bool:
        source_key = (str(item.get("related_table") or ""), item.get("related_id"))
        if source_key in seen_sources:
            return False
        trait_key = str(item.get("debug_trait") or "general")
        if trait_counts.get(trait_key, 0) >= 1:
            return False
        is_major = bool(item.get("is_major"))
        category = str(item.get("category") or "League")
        if diversity_pass and category_counts.get(category, 0) >= 1:
            return False
        if not is_major and category_counts.get(category, 0) >= category_caps.get(category, 1):
            return False
        team_id = item.get("team_id")
        if team_id is not None:
            team_key = int(team_id)
            if not is_major and team_counts.get(team_key, 0) >= 1:
                return False
        seen_sources.add(source_key)
        trait_counts[trait_key] = trait_counts.get(trait_key, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
        if team_id is not None:
            team_counts[int(team_id)] = team_counts.get(int(team_id), 0) + 1
        selected.append(item)
        return True

    for diversity_pass in (True, False):
        for item in ordered:
            if len(selected) >= max_events:
                break
            if not diversity_pass and item in selected:
                continue
            try_add(item, diversity_pass=diversity_pass)
        if len(selected) >= max_events:
            break
    return selected


def run_already_processed(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    run_key: str,
) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM league_event_generation_runs
        WHERE game_id = ?
          AND season = ?
          AND week = ?
          AND run_key = ?
        """,
        (game_id, season, week, run_key),
    ).fetchone()


def apply_prospect_event_effects(
    con: sqlite3.Connection,
    item: dict[str, Any],
    *,
    event_date: str,
) -> int:
    if item.get("related_table") != "draft_prospects" or not item.get("prospect_id"):
        return 0
    prospect_id = int(item["prospect_id"])
    effects = item.get("effects") or {}
    before = con.total_changes
    grade_delta = int(effects.get("grade_delta") or 0)
    if grade_delta:
        con.execute(
            """
            UPDATE draft_prospects
            SET scout_grade = CASE
                    WHEN scout_grade IS NULL THEN NULL
                    ELSE MAX(35, MIN(99, scout_grade + ?))
                END,
                scouting_summary = TRIM(
                    COALESCE(scouting_summary, '') ||
                    CASE WHEN COALESCE(scouting_summary, '') = '' THEN '' ELSE ' ' END ||
                    ?
                ),
                updated_at = datetime('now')
            WHERE prospect_id = ?
            """,
            (
                grade_delta,
                item["title"],
                prospect_id,
            ),
        )
    if effects.get("discover"):
        con.execute(
            """
            UPDATE draft_prospects
            SET discovery_status = 'discovered',
                discovery_notes = ?,
                updated_at = datetime('now')
            WHERE prospect_id = ?
              AND COALESCE(public_board_status, '') = 'off_public_board'
            """,
            (append_note(None, f"{event_date}: {item['title']}"), prospect_id),
        )
    medical = effects.get("medical")
    if medical == "minor":
        con.execute(
            """
            INSERT INTO draft_prospect_combine_results (
                prospect_id, combine_status, participation_note, is_injured, source, updated_at
            )
            VALUES (?, 'Partial participant', ?, 1, 'event_generator', datetime('now'))
            ON CONFLICT(prospect_id) DO UPDATE SET
                combine_status = CASE
                    WHEN draft_prospect_combine_results.combine_status = 'Full participant'
                    THEN 'Partial participant'
                    ELSE draft_prospect_combine_results.combine_status
                END,
                participation_note = ?,
                is_injured = 1,
                source = 'event_generator',
                updated_at = datetime('now')
            """,
            (prospect_id, item["body"], item["body"]),
        )
        con.execute(
            """
            UPDATE draft_prospects
            SET scout_risk = CASE
                    WHEN COALESCE(scout_risk, risk_level, 'Medium') = 'Low' THEN 'Medium'
                    ELSE COALESCE(scout_risk, risk_level, 'Medium')
                END,
                risk_level = CASE
                    WHEN risk_level = 'Low' THEN 'Medium'
                    ELSE risk_level
                END,
                updated_at = datetime('now')
            WHERE prospect_id = ?
            """,
            (prospect_id,),
        )
    elif medical == "combine_out":
        con.execute(
            """
            INSERT INTO draft_prospect_combine_results (
                prospect_id, combine_status, participation_note, drills_completed,
                drills_skipped, is_injured, source, updated_at
            )
            VALUES (?, 'Injured - did not participate', ?, 0, 'All workout drills', 1, 'event_generator', datetime('now'))
            ON CONFLICT(prospect_id) DO UPDATE SET
                combine_status = 'Injured - did not participate',
                participation_note = ?,
                drills_completed = 0,
                drills_skipped = 'All workout drills',
                is_injured = 1,
                source = 'event_generator',
                updated_at = datetime('now')
            """,
            (prospect_id, item["body"], item["body"]),
        )
        con.execute(
            """
            INSERT INTO draft_prospect_pro_day_results (
                prospect_id, pro_day_status, participation_note, medical_recheck, source, updated_at
            )
            VALUES (?, 'Medical recheck only', ?, 1, 'event_generator', datetime('now'))
            ON CONFLICT(prospect_id) DO UPDATE SET
                pro_day_status = CASE
                    WHEN draft_prospect_pro_day_results.pro_day_status = 'No pro day data'
                    THEN 'Medical recheck only'
                    ELSE draft_prospect_pro_day_results.pro_day_status
                END,
                participation_note = ?,
                medical_recheck = 1,
                source = 'event_generator',
                updated_at = datetime('now')
            """,
            (prospect_id, item["body"], item["body"]),
        )
        con.execute(
            """
            UPDATE draft_prospects
            SET scout_risk = 'High',
                risk_level = 'High',
                updated_at = datetime('now')
            WHERE prospect_id = ?
            """,
            (prospect_id,),
        )
    if effects:
        con.execute(
            """
            INSERT INTO draft_prospect_scouting_notes (
                prospect_id, note_date, source, grade, note
            )
            SELECT prospect_id, ?, 'event_generator', scout_grade, ?
            FROM draft_prospects
            WHERE prospect_id = ?
            """,
            (event_date, f"{item['title']}: {item['body']}", prospect_id),
        )
    return con.total_changes - before


def generate_weekly_events(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    season: int | None = None,
    week: int,
    event_date: str | None = None,
    seed: str | None = None,
    max_events: int | None = None,
    min_events: int | None = None,
    force: bool = False,
    apply: bool = False,
    run_key: str = "weekly",
) -> GenerationResult:
    ensure_schema(con)
    target_game_id = league_news.active_game_id(con, game_id)
    target_season = int(season or league_news.current_season(con))
    target_date = event_date or league_news.current_date(con)
    cadence = event_cadence(con, event_date=target_date, week=week)
    max_events = cadence.default_max_events if max_events is None else int(max_events)
    min_events = cadence.default_min_events if min_events is None else int(min_events)
    max_events = max(0, max_events)
    min_events = max(0, min(min_events, max_events))

    existing = run_already_processed(
        con,
        game_id=target_game_id,
        season=target_season,
        week=week,
        run_key=run_key,
    )
    if existing and not force:
        return GenerationResult(
            game_id=target_game_id,
            season=target_season,
            week=week,
            event_date=target_date,
            status="already_processed",
            planned_count=int(existing["planned_count"] or 0),
            inserted_count=0,
            events=[],
            message=f"{target_season} Week {week} event generation already processed.",
            phase_code=cadence.phase_code,
            cadence=cadence.label,
        )
    if existing and force:
        con.execute("DELETE FROM league_event_generation_runs WHERE run_id = ?", (int(existing["run_id"]),))

    seed_text = seed or f"{target_game_id}:{target_season}:week-{week}:{target_date}:{run_key}"
    rng = random.Random(seed_text)
    candidates: list[dict[str, Any]] = []
    candidates.extend(
        player_personality_candidates(
            con,
            game_id=target_game_id,
            season=target_season,
            week=week,
            run_key=run_key,
            rng=rng,
            rate_multiplier=cadence.rate_multiplier,
        )
    )
    candidates.extend(
        prospect_personality_candidates(
            con,
            game_id=target_game_id,
            season=target_season,
            week=week,
            run_key=run_key,
            rng=rng,
            rate_multiplier=cadence.rate_multiplier,
        )
    )
    candidates.extend(
        prospect_event_candidates(
            con,
            game_id=target_game_id,
            season=target_season,
            week=week,
            run_key=run_key,
            event_date=target_date,
            cadence=cadence,
            rng=rng,
        )
    )
    if len(candidates) < min_events:
        candidates.extend(
            fallback_candidates(
                con,
                game_id=target_game_id,
                season=target_season,
                week=week,
                run_key=run_key,
                rng=rng,
                needed=min_events - len(candidates),
            )
        )
    selected = select_events(candidates, max_events=max_events, cadence_code=cadence.code)
    inserted = 0
    if apply:
        for item in selected:
            before_item = con.total_changes
            league_news.add_news_item(
                con,
                game_id=target_game_id,
                news_date=target_date,
                category=str(item["category"]),
                priority=str(item["priority"]),
                source=str(item["source"]),
                title=str(item["title"]),
                body=str(item["body"]),
                team_id=item.get("team_id"),
                player_id=item.get("player_id"),
                prospect_id=item.get("prospect_id"),
                related_table=item.get("related_table"),
                related_id=item.get("related_id"),
                tags=list(item.get("tags") or []),
                is_major=bool(item.get("is_major")),
                fingerprint=str(item["fingerprint"]),
            )
            item_inserted = con.total_changes > before_item
            if item_inserted:
                inserted += 1
                apply_prospect_event_effects(con, item, event_date=target_date)
        con.execute(
            """
            INSERT INTO league_event_generation_runs (
                game_id, season, week, event_date, run_key, seed, planned_count, inserted_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (target_game_id, target_season, week, target_date, run_key, seed_text, len(selected), inserted),
        )

    for item in selected:
        item.pop("roll_score", None)
    status = "processed" if apply else "dry_run"
    message = (
        f"{target_season} Week {week} event generation "
        f"{'saved' if apply else 'planned'} {len(selected)} item(s)."
    )
    return GenerationResult(
        game_id=target_game_id,
        season=target_season,
        week=week,
        event_date=target_date,
        status=status,
        planned_count=len(selected),
        inserted_count=inserted,
        events=selected,
        message=message,
        phase_code=cadence.phase_code,
        cadence=cadence.label,
    )


def print_result(result: GenerationResult) -> None:
    print(f"League event generation: {result.message}")
    print(f"  Game: {result.game_id}")
    print(f"  Date: {result.event_date}")
    print(f"  Cadence: {result.cadence}{f' ({result.phase_code})' if result.phase_code else ''}")
    if result.status == "already_processed":
        print("  Use --force to roll this week again.")
        return
    if not result.events:
        print("  No events rolled.")
        return
    for item in result.events:
        major = " [MAJOR]" if item.get("is_major") else ""
        trait = f" ({item['debug_trait']})" if item.get("debug_trait") else ""
        print(f"- {item['category']}{major}{trait}: {item['title']}")
        print(f"  {item['body']}")


def action_weekly(args: argparse.Namespace) -> None:
    with connect(Path(args.db)) as con:
        result = generate_weekly_events(
            con,
            game_id=args.game_id,
            season=args.season,
            week=args.week,
            event_date=args.date,
            seed=args.seed,
            max_events=args.max_events,
            min_events=args.min_events,
            force=args.force,
            apply=args.apply,
            run_key=args.run_key,
        )
        if args.apply:
            con.commit()
        else:
            con.rollback()
    if not args.apply:
        print("Dry run only. Add --apply to save league news items.")
    print_result(result)


def action_setup(args: argparse.Namespace) -> None:
    with connect(Path(args.db)) as con:
        ensure_schema(con)
        con.commit()
    print("Event generator schema is ready.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Roll public league events from hidden personalities.")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to nfl_gm.db or active save database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create event generation tables.")
    setup_parser.set_defaults(func=action_setup)

    weekly_parser = subparsers.add_parser("weekly", help="Roll weekly league events.")
    weekly_parser.add_argument("--game-id")
    weekly_parser.add_argument("--season", type=int)
    weekly_parser.add_argument("--week", type=int, required=True)
    weekly_parser.add_argument("--date")
    weekly_parser.add_argument("--seed")
    weekly_parser.add_argument("--max-events", type=int)
    weekly_parser.add_argument("--min-events", type=int)
    weekly_parser.add_argument("--run-key", default="weekly")
    weekly_parser.add_argument("--force", action="store_true")
    weekly_parser.add_argument("--apply", action="store_true")
    weekly_parser.set_defaults(func=action_weekly)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
