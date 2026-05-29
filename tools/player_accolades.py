"""Generate and export player accolade history.

The table is intentionally simple: one row per player, season, and award.
Award generation is deterministic from the saved season stats and postseason
champion so player profiles can show OOTP-style badge history across years.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
SOURCE = "season_awards_v1"

POSITION_LABELS = {
    "QB": "Quarterback",
    "RB": "Running Back",
    "FB": "Fullback",
    "WR": "Wide Receiver",
    "TE": "Tight End",
    "OT": "Offensive Tackle",
    "OG": "Guard",
    "C": "Center",
    "IDL": "Interior Defensive Line",
    "EDGE": "Edge Defender",
    "LB": "Linebacker",
    "CB": "Cornerback",
    "S": "Safety",
    "K": "Kicker",
    "P": "Punter",
}

AWARD_META = {
    "MVP": {
        "name": "Most Valuable Player",
        "badge": "MVP",
        "tier": "mvp",
        "group": "major",
        "sort": 10,
    },
    "SUPER_BOWL_TITLE": {
        "name": "Super Bowl Champion",
        "badge": "SB",
        "tier": "champion",
        "group": "championship",
        "sort": 20,
    },
    "POSITION_OF_YEAR": {
        "name": "Position of the Year",
        "badge": "POY",
        "tier": "position",
        "group": "major",
        "sort": 30,
    },
    "ROOKIE_OF_YEAR": {
        "name": "Rookie of the Year",
        "badge": "ROTY",
        "tier": "rookie",
        "group": "major",
        "sort": 40,
    },
    "COMEBACK_PLAYER_OF_YEAR": {
        "name": "Comeback Player of the Year",
        "badge": "CPOTY",
        "tier": "comeback",
        "group": "major",
        "sort": 50,
    },
    "FIRST_TEAM_ALL_PRO": {
        "name": "1st Team All-Pro",
        "badge": "1AP",
        "tier": "allpro-first",
        "group": "all_pro",
        "sort": 60,
    },
    "SECOND_TEAM_ALL_PRO": {
        "name": "2nd Team All-Pro",
        "badge": "2AP",
        "tier": "allpro-second",
        "group": "all_pro",
        "sort": 70,
    },
    "PRO_BOWL": {
        "name": "Pro Bowl",
        "badge": "PB",
        "tier": "pro-bowl",
        "group": "pro_bowl",
        "sort": 90,
    },
}

ALL_PRO_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 3,
    "TE": 1,
    "OT": 2,
    "OG": 2,
    "C": 1,
    "IDL": 2,
    "EDGE": 2,
    "LB": 3,
    "CB": 3,
    "S": 2,
    "K": 1,
    "P": 1,
}

PRO_BOWL_SLOTS = {
    "QB": 6,
    "RB": 8,
    "WR": 12,
    "TE": 6,
    "OT": 8,
    "OG": 8,
    "C": 4,
    "IDL": 8,
    "EDGE": 8,
    "LB": 10,
    "CB": 10,
    "S": 8,
    "K": 4,
    "P": 4,
}

STAT_KEY_ALIASES = {
    "completions": "pass_completions",
    "passing_completions": "pass_completions",
    "passing_attempts": "pass_attempts",
    "passing_yards": "pass_yards",
    "passing_tds": "pass_tds",
    "passing_interceptions": "interceptions_thrown",
    "pass_interceptions": "interceptions_thrown",
    "sacks_suffered": "sacks_taken",
    "carries": "rush_attempts",
    "rushing_attempts": "rush_attempts",
    "rushing_yards": "rush_yards",
    "rushing_tds": "rush_tds",
    "rushing_fumbles_lost": "fumbles_lost",
    "receiving_fumbles_lost": "fumbles_lost",
    "def_tackles_solo": "solo_tackles",
    "def_tackle_assists": "assisted_tackles",
    "def_tackles_with_assist": "tackles",
    "def_tackles_for_loss": "tackles_for_loss",
    "def_fumbles_forced": "forced_fumbles",
    "def_sacks": "sacks",
    "def_qb_hits": "qb_hits",
    "def_interceptions": "interceptions",
    "def_pass_defended": "pass_deflections",
    "fg_att": "fg_attempts",
    "pat_made": "xp_made",
    "pat_att": "xp_attempts",
}


@dataclass
class Candidate:
    player_id: int
    name: str
    position: str
    team_id: int | None
    team_abbr: str
    age: int
    years_exp: int
    is_rookie: bool
    overall: int
    potential: int
    stats: dict[str, float] = field(default_factory=dict)
    prior_stats: dict[str, float] = field(default_factory=dict)
    team_wins: int = 0
    team_losses: int = 0
    team_ties: int = 0


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_accolades (
            accolade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            award_key TEXT NOT NULL,
            award_name TEXT NOT NULL,
            award_group TEXT NOT NULL,
            award_position TEXT,
            badge_label TEXT NOT NULL,
            badge_tier TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 100,
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_player_accolades_player
            ON player_accolades(player_id, sort_order, season DESC);

        CREATE INDEX IF NOT EXISTS idx_player_accolades_season
            ON player_accolades(season, award_key, award_position);
        """
    )


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def stat(stats: dict[str, float], *keys: str) -> float:
    return sum(as_float(stats.get(key)) for key in keys)


def stat_one(stats: dict[str, float], *keys: str) -> float:
    for key in keys:
        value = as_float(stats.get(key))
        if value:
            return value
    return 0.0


def normalize_stat_key(key: str) -> str:
    return STAT_KEY_ALIASES.get(key, key)


def normalize_award_position(position: str | None) -> str:
    key = str(position or "").upper()
    aliases = {
        "FS": "S",
        "SS": "S",
        "SAF": "S",
        "DB": "CB",
        "NB": "CB",
        "ILB": "LB",
        "OLB": "LB",
        "LT": "OT",
        "RT": "OT",
        "LG": "OG",
        "RG": "OG",
        "G": "OG",
        "DT": "IDL",
        "NT": "IDL",
        "DE": "EDGE",
    }
    return aliases.get(key, key)


def games(candidate: Candidate) -> float:
    return stat_one(candidate.stats, "games")


def win_pct(candidate: Candidate) -> float:
    total = candidate.team_wins + candidate.team_losses + candidate.team_ties
    if total <= 0:
        return 0.5
    return (candidate.team_wins + candidate.team_ties * 0.5) / total


def load_imported_season_stats(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    if not table_exists(con, "player_season_stats"):
        return {}
    rows = con.execute(
        """
        SELECT *
        FROM player_season_stats
        WHERE season = ?
        """,
        (season,),
    ).fetchall()
    mapped: dict[int, dict[str, float]] = defaultdict(dict)
    aliases = {
        "games": "games",
        "completions": "pass_completions",
        "passing_completions": "pass_completions",
        "passing_attempts": "pass_attempts",
        "passing_yards": "pass_yards",
        "passing_tds": "pass_tds",
        "passing_interceptions": "interceptions_thrown",
        "sacks_suffered": "sacks_taken",
        "carries": "rush_attempts",
        "rushing_yards": "rush_yards",
        "rushing_tds": "rush_tds",
        "rushing_fumbles": "fumbles",
        "receptions": "receptions",
        "targets": "targets",
        "receiving_yards": "receiving_yards",
        "receiving_tds": "receiving_tds",
        "def_tackles_solo": "solo_tackles",
        "def_tackle_assists": "assisted_tackles",
        "def_tackles_with_assist": "tackles",
        "def_tackles_for_loss": "tackles_for_loss",
        "def_fumbles_forced": "forced_fumbles",
        "def_sacks": "sacks",
        "def_qb_hits": "qb_hits",
        "def_interceptions": "interceptions",
        "def_pass_defended": "pass_deflections",
        "fg_made": "fg_made",
        "fg_att": "fg_attempts",
        "fg_long": "long_fg",
        "pat_made": "xp_made",
        "pat_att": "xp_attempts",
        "fantasy_points_ppr": "fantasy_points_ppr",
    }
    for row in rows:
        player_id = int(row["player_id"])
        for source_key, target_key in aliases.items():
            if source_key in row.keys():
                mapped[player_id][target_key] = mapped[player_id].get(target_key, 0.0) + as_float(row[source_key])
    return mapped


def load_stat_totals(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    stats = load_imported_season_stats(con, season)
    if table_exists(con, "season_player_stats"):
        rows = con.execute(
            """
            SELECT player_id, stat_key, SUM(stat_value) AS stat_value
            FROM season_player_stats
            WHERE season = ?
            GROUP BY player_id, stat_key
            """,
            (season,),
        ).fetchall()
        for row in rows:
            player_id = int(row["player_id"])
            stat_key = normalize_stat_key(str(row["stat_key"]))
            stats[player_id][stat_key] = stats[player_id].get(stat_key, 0.0) + as_float(row["stat_value"])
    if table_exists(con, "game_player_stats") and table_exists(con, "game_sim_runs"):
        rows = con.execute(
            """
            SELECT gps.player_id, COUNT(DISTINCT gps.run_id) AS games
            FROM game_player_stats gps
            JOIN game_sim_runs r ON r.run_id = gps.run_id
            WHERE r.season = ?
              AND r.status = 'final'
              AND COALESCE(r.counts_for_stats, 1) = 1
            GROUP BY gps.player_id
            """,
            (season,),
        ).fetchall()
        for row in rows:
            stats[int(row["player_id"])]["games"] = max(
                stats[int(row["player_id"])].get("games", 0.0),
                as_float(row["games"]),
            )
    return stats


def dominant_team_ids(con: sqlite3.Connection, season: int) -> dict[int, int]:
    if not table_exists(con, "season_player_stats"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, team_id,
               SUM(CASE WHEN stat_key = 'total_snaps' THEN stat_value ELSE 0 END) AS snaps,
               SUM(ABS(stat_value)) AS activity
        FROM season_player_stats
        WHERE season = ?
        GROUP BY player_id, team_id
        ORDER BY player_id, snaps DESC, activity DESC
        """,
        (season,),
    ).fetchall()
    teams: dict[int, int] = {}
    for row in rows:
        player_id = int(row["player_id"])
        if row["team_id"] is not None and player_id not in teams:
            teams[player_id] = int(row["team_id"])
    return teams


def team_records(con: sqlite3.Connection, season: int) -> dict[int, sqlite3.Row]:
    if not table_exists(con, "season_team_records"):
        return {}
    return {
        int(row["team_id"]): row
        for row in con.execute(
            """
            SELECT *
            FROM season_team_records
            WHERE season = ?
            """,
            (season,),
        ).fetchall()
    }


def load_candidates(con: sqlite3.Connection, season: int) -> list[Candidate]:
    stats = load_stat_totals(con, season)
    prior_stats = load_stat_totals(con, season - 1)
    dominant_teams = dominant_team_ids(con, season)
    records = team_records(con, season)
    team_abbrs = {
        int(row["team_id"]): str(row["abbreviation"] or "")
        for row in con.execute("SELECT team_id, abbreviation FROM teams").fetchall()
    }
    rows = con.execute(
        """
        SELECT p.player_id, p.first_name, p.last_name, p.position, p.team_id,
               p.age, p.years_exp, p.is_rookie, p.overall, p.potential,
               t.abbreviation
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE COALESCE(p.status, '') NOT IN ('Retired')
        """,
    ).fetchall()
    candidates: list[Candidate] = []
    for row in rows:
        player_id = int(row["player_id"])
        player_stats = stats.get(player_id, {})
        if not player_stats:
            continue
        team_id = dominant_teams.get(player_id)
        if team_id is None and row["team_id"] is not None:
            team_id = int(row["team_id"])
        record = records.get(team_id or -1)
        candidates.append(
            Candidate(
                player_id=player_id,
                name=f"{row['first_name']} {row['last_name']}".strip(),
                position=normalize_award_position(row["position"]),
                team_id=team_id,
                team_abbr=team_abbrs.get(team_id or -1, str(row["abbreviation"] or "")),
                age=int(row["age"] or 0),
                years_exp=int(row["years_exp"] or 0),
                is_rookie=bool(row["is_rookie"]) or int(row["years_exp"] or 0) == 0,
                overall=int(row["overall"] or 0),
                potential=int(row["potential"] or 0),
                stats=player_stats,
                prior_stats=prior_stats.get(player_id, {}),
                team_wins=int(record["wins"] or 0) if record else 0,
                team_losses=int(record["losses"] or 0) if record else 0,
                team_ties=int(record["ties"] or 0) if record else 0,
            )
        )
    return candidates


def ypc(candidate: Candidate) -> float:
    attempts = stat(candidate.stats, "rush_attempts")
    if attempts <= 0:
        return 0.0
    return stat(candidate.stats, "rush_yards") / attempts


def position_score(candidate: Candidate) -> float:
    stats = candidate.stats
    pos = candidate.position
    g = games(candidate)
    team_bonus = max(0.0, win_pct(candidate) - 0.5) * 24.0
    if pos == "QB":
        attempts = max(1.0, stat(stats, "pass_attempts"))
        efficiency = (stat(stats, "pass_completions") / attempts) * 24.0
        return (
            stat(stats, "pass_yards") * 0.013
            + stat(stats, "pass_tds") * 4.8
            - stat(stats, "interceptions_thrown") * 4.5
            - stat(stats, "sacks_taken") * 0.7
            + stat(stats, "rush_yards") * 0.025
            + stat(stats, "rush_tds") * 4.5
            + efficiency
            + team_bonus
        ) * min(1.1, max(0.35, g / 14.0))
    if pos in {"RB", "FB"}:
        return (
            stat(stats, "rush_yards") * 0.060
            + stat(stats, "rush_tds") * 6.2
            + stat(stats, "receiving_yards") * 0.040
            + stat(stats, "receiving_tds") * 5.5
            + stat(stats, "receptions") * 0.45
            - stat(stats, "fumbles_lost") * 4.0
            + max(0.0, ypc(candidate) - 4.0) * 7.0
        ) * min(1.08, max(0.4, g / 13.0))
    if pos in {"WR", "TE"}:
        tight_end_bonus = 1.12 if pos == "TE" else 1.0
        return (
            stat(stats, "receiving_yards") * 0.066
            + stat(stats, "receiving_tds") * 7.0
            + stat(stats, "receptions") * 0.55
            + stat(stats, "rush_yards") * 0.035
            + stat(stats, "rush_tds") * 4.5
            - stat(stats, "fumbles_lost") * 4.0
            + team_bonus * 0.25
        ) * tight_end_bonus * min(1.08, max(0.4, g / 13.0))
    if pos in {"OT", "OG", "C"}:
        snaps = stat(stats, "offensive_snaps", "total_snaps")
        position_bonus = {"C": 3.0, "OT": 5.0, "OG": 0.0}.get(pos, 0.0)
        return candidate.overall * 2.8 + snaps * 0.030 + team_bonus + position_bonus
    if pos in {"IDL", "EDGE", "LB"}:
        edge_bonus = 1.08 if pos == "EDGE" else 1.0
        return (
            stat(stats, "sacks") * 9.4
            + stat(stats, "tackles") * 0.52
            + stat(stats, "solo_tackles") * 0.35
            + stat(stats, "forced_fumbles") * 6.2
            + stat(stats, "interceptions") * 8.0
            + stat(stats, "pass_deflections") * 1.6
            + stat(stats, "defensive_snaps") * 0.008
            + team_bonus * 0.5
        ) * edge_bonus
    if pos in {"CB", "S"}:
        return (
            stat(stats, "interceptions") * 12.0
            + stat(stats, "pass_deflections") * 2.4
            + stat(stats, "tackles") * 0.45
            + stat(stats, "solo_tackles") * 0.25
            + stat(stats, "sacks") * 5.5
            + stat(stats, "forced_fumbles") * 6.0
            + stat(stats, "defensive_tds", "interception_return_tds", "fumble_return_tds") * 8.0
            + team_bonus * 0.45
        )
    if pos == "K":
        attempts = stat(stats, "fg_attempts")
        misses = max(0.0, attempts - stat(stats, "fg_made"))
        return stat(stats, "fg_made") * 4.0 - misses * 4.5 + stat(stats, "xp_made") * 0.45 + stat(stats, "long_fg") * 0.38
    if pos == "P":
        punts = max(1.0, stat(stats, "punts"))
        avg = stat(stats, "punt_yards") / punts
        return punts * 1.3 + avg * 3.2 + candidate.overall * 0.7
    return candidate.overall * 1.5 + stat(stats, "total_snaps") * 0.02


def mvp_score(candidate: Candidate) -> float:
    score = position_score(candidate)
    record_bonus = max(0.0, win_pct(candidate) - 0.5)
    if candidate.position == "QB":
        attempts = stat(candidate.stats, "pass_attempts")
        if games(candidate) >= 10 and attempts < 260:
            return score * 0.60
        efficiency_bonus = max(0.0, stat(candidate.stats, "pass_tds") - stat(candidate.stats, "interceptions_thrown") * 1.25) * 1.2
        volume_bonus = max(0.0, stat(candidate.stats, "pass_yards") - 3500.0) * 0.012
        return score * 1.58 + record_bonus * 72.0 + efficiency_bonus + volume_bonus
    if candidate.position in {"RB", "WR", "TE"}:
        scrimmage_yards = stat(candidate.stats, "rush_yards", "receiving_yards")
        touchdowns = stat(candidate.stats, "rush_tds", "receiving_tds")
        historic_bonus = 0.0
        if candidate.position in {"RB", "FB"}:
            historic_bonus += max(0.0, scrimmage_yards - 1900.0) * 0.030
            historic_bonus += max(0.0, touchdowns - 16.0) * 3.0
        elif candidate.position in {"WR", "TE"}:
            historic_bonus += max(0.0, stat(candidate.stats, "receiving_yards") - 1650.0) * 0.026
            historic_bonus += max(0.0, touchdowns - 14.0) * 3.0
        return score * 0.60 + record_bonus * 8.0 + min(42.0, historic_bonus)
    if candidate.position in {"EDGE", "IDL", "LB", "CB", "S"}:
        historic_bonus = max(0.0, stat(candidate.stats, "sacks") - 18.0) * 3.0
        historic_bonus += max(0.0, stat(candidate.stats, "interceptions") - 7.0) * 3.0
        return score * 0.46 + record_bonus * 6.0 + min(32.0, historic_bonus)
    return score * 0.35


def rookie_score(candidate: Candidate) -> float:
    score = position_score(candidate)
    if candidate.position in {"OT", "OG", "C"}:
        return score * 0.40
    if candidate.position in {"K", "P"}:
        return score * 0.30
    if candidate.position == "TE":
        return score * 1.04
    if candidate.position in {"EDGE", "CB", "S", "LB"}:
        return score * 1.03
    return score


def comeback_score(candidate: Candidate) -> float:
    if candidate.position in {"K", "P", "LS", "FB"}:
        return -1.0
    current = position_score(candidate)
    previous = prior_position_score(candidate)
    prior_games = stat_one(candidate.prior_stats, "games")
    current_games = games(candidate)
    if current_games < 8 or current < 55 or candidate.age < 25:
        return -1.0
    stats = candidate.stats
    pos = candidate.position
    if pos == "QB" and stat(stats, "pass_attempts") < 250:
        return -1.0
    if pos == "RB" and stat(stats, "rush_yards", "receiving_yards") < 850:
        return -1.0
    if pos in {"WR", "TE"} and stat(stats, "receiving_yards") < 650:
        return -1.0
    if pos in {"OT", "OG", "C"} and stat(stats, "offensive_snaps", "total_snaps") < 650:
        return -1.0
    if pos in {"IDL", "EDGE", "LB", "CB", "S"} and stat(stats, "defensive_snaps", "total_snaps") < 500:
        return -1.0
    low_prior_bonus = max(0.0, 10.0 - prior_games) * 6.0
    rebound = max(0.0, current - previous * 0.65)
    if low_prior_bonus <= 0 and rebound < 25:
        return -1.0
    veteran_bonus = min(16.0, max(0.0, candidate.age - 25) * 1.2)
    return rebound + low_prior_bonus + veteran_bonus


def prior_position_score(candidate: Candidate) -> float:
    old = Candidate(
        player_id=candidate.player_id,
        name=candidate.name,
        position=candidate.position,
        team_id=candidate.team_id,
        team_abbr=candidate.team_abbr,
        age=max(0, candidate.age - 1),
        years_exp=max(0, candidate.years_exp - 1),
        is_rookie=False,
        overall=candidate.overall,
        potential=candidate.potential,
        stats=candidate.prior_stats,
        team_wins=8,
        team_losses=9,
        team_ties=0,
    )
    return position_score(old)


def award_name(award_key: str, position: str | None = None) -> str:
    if award_key == "POSITION_OF_YEAR" and position:
        return f"{POSITION_LABELS.get(position, position)} of the Year"
    return AWARD_META[award_key]["name"]


def badge_label(award_key: str, position: str | None = None) -> str:
    if award_key == "POSITION_OF_YEAR" and position:
        return f"{position} OY"
    return AWARD_META[award_key]["badge"]


def accolade_notes(candidate: Candidate, award_key: str, score: float | None = None) -> str:
    parts = [candidate.team_abbr] if candidate.team_abbr else []
    if score is not None:
        parts.append(f"award score {score:.1f}")
    if award_key == "SUPER_BOWL_TITLE":
        parts.append("member of the championship roster")
    return "; ".join(parts)


def insert_accolade(
    con: sqlite3.Connection,
    *,
    candidate: Candidate,
    season: int,
    award_key: str,
    award_position: str | None = None,
    score: float | None = None,
) -> None:
    meta = AWARD_META[award_key]
    position = award_position or candidate.position or None
    fingerprint = f"{season}:{candidate.player_id}:{award_key}:{position or ''}"
    con.execute(
        """
        INSERT INTO player_accolades (
            player_id, season, team_id, award_key, award_name, award_group,
            award_position, badge_label, badge_tier, sort_order, source,
            notes, fingerprint, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(fingerprint) DO UPDATE SET
            team_id = excluded.team_id,
            award_name = excluded.award_name,
            award_group = excluded.award_group,
            award_position = excluded.award_position,
            badge_label = excluded.badge_label,
            badge_tier = excluded.badge_tier,
            sort_order = excluded.sort_order,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            candidate.player_id,
            season,
            candidate.team_id,
            award_key,
            award_name(award_key, award_position),
            meta["group"],
            award_position,
            badge_label(award_key, award_position),
            meta["tier"],
            int(meta["sort"]),
            SOURCE,
            accolade_notes(candidate, award_key, score),
            fingerprint,
        ),
    )


def champion_team_id(con: sqlite3.Connection, season: int) -> int | None:
    if table_exists(con, "season_completions"):
        row = con.execute(
            "SELECT champion_team_id FROM season_completions WHERE season = ?",
            (season,),
        ).fetchone()
        if row and row["champion_team_id"] is not None:
            return int(row["champion_team_id"])
    if table_exists(con, "playoff_games"):
        row = con.execute(
            """
            SELECT winner_team_id
            FROM playoff_games
            WHERE season = ?
              AND round_code = 'SB'
              AND winner_team_id IS NOT NULL
            ORDER BY game_number
            LIMIT 1
            """,
            (season,),
        ).fetchone()
        if row and row["winner_team_id"] is not None:
            return int(row["winner_team_id"])
    return None


def champion_roster(con: sqlite3.Connection, season: int, candidates_by_id: dict[int, Candidate]) -> list[Candidate]:
    team_id = champion_team_id(con, season)
    if team_id is None:
        return []
    team_row = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    team_abbr = str(team_row["abbreviation"] or "") if team_row else ""
    rows = con.execute(
        """
        SELECT player_id, first_name, last_name, position, team_id, age, years_exp,
               is_rookie, overall, potential
        FROM players
        WHERE team_id = ?
          AND COALESCE(status, '') NOT IN ('Retired', 'FA')
        """,
        (team_id,),
    ).fetchall()
    roster: list[Candidate] = []
    for row in rows:
        player_id = int(row["player_id"])
        existing = candidates_by_id.get(player_id)
        if existing:
            roster.append(existing)
            continue
        roster.append(
            Candidate(
                player_id=player_id,
                name=f"{row['first_name']} {row['last_name']}".strip(),
                position=normalize_award_position(row["position"]),
                team_id=team_id,
                team_abbr=team_abbr,
                age=int(row["age"] or 0),
                years_exp=int(row["years_exp"] or 0),
                is_rookie=bool(row["is_rookie"]) or int(row["years_exp"] or 0) == 0,
                overall=int(row["overall"] or 0),
                potential=int(row["potential"] or 0),
            )
        )
    return roster


def candidate_pool_by_position(candidates: list[Candidate], position: str) -> list[Candidate]:
    pool = [candidate for candidate in candidates if candidate.position == position and games(candidate) >= 6]
    return sorted(pool, key=position_score, reverse=True)


def generate_season_accolades(con: sqlite3.Connection, season: int, *, force: bool = False) -> dict[str, int]:
    ensure_schema(con)
    existing = con.execute(
        "SELECT COUNT(*) AS c FROM player_accolades WHERE season = ? AND source = ?",
        (season, SOURCE),
    ).fetchone()
    if existing and int(existing["c"] or 0) and not force:
        return {"inserted": 0, "skipped_existing": int(existing["c"] or 0)}
    con.execute("DELETE FROM player_accolades WHERE season = ? AND source = ?", (season, SOURCE))

    candidates = load_candidates(con, season)
    candidates_by_id = {candidate.player_id: candidate for candidate in candidates}
    inserted = 0

    if candidates:
        mvp = max(candidates, key=mvp_score)
        if mvp_score(mvp) > 70:
            insert_accolade(con, candidate=mvp, season=season, award_key="MVP", score=mvp_score(mvp))
            inserted += 1

    for position in ALL_PRO_SLOTS:
        pool = candidate_pool_by_position(candidates, position)
        if not pool:
            continue

        slots = ALL_PRO_SLOTS[position]
        first_team = pool[:slots]
        second_team = pool[slots: slots * 2]
        for candidate in first_team:
            insert_accolade(
                con,
                candidate=candidate,
                season=season,
                award_key="FIRST_TEAM_ALL_PRO",
                award_position=position,
                score=position_score(candidate),
            )
            inserted += 1
        for candidate in second_team:
            insert_accolade(
                con,
                candidate=candidate,
                season=season,
                award_key="SECOND_TEAM_ALL_PRO",
                award_position=position,
                score=position_score(candidate),
            )
            inserted += 1

        for candidate in pool[: PRO_BOWL_SLOTS[position]]:
            insert_accolade(
                con,
                candidate=candidate,
                season=season,
                award_key="PRO_BOWL",
                award_position=position,
                score=position_score(candidate),
            )
            inserted += 1

    rookies = [candidate for candidate in candidates if candidate.is_rookie and games(candidate) >= 6]
    if rookies:
        rookie = max(rookies, key=rookie_score)
        if rookie_score(rookie) >= 30:
            insert_accolade(
                con,
                candidate=rookie,
                season=season,
                award_key="ROOKIE_OF_YEAR",
                score=rookie_score(rookie),
            )
            inserted += 1

    comeback_pool = [candidate for candidate in candidates if comeback_score(candidate) > 45]
    if comeback_pool:
        comeback = max(comeback_pool, key=comeback_score)
        insert_accolade(
            con,
            candidate=comeback,
            season=season,
            award_key="COMEBACK_PLAYER_OF_YEAR",
            score=comeback_score(comeback),
        )
        inserted += 1

    for player in champion_roster(con, season, candidates_by_id):
        insert_accolade(con, candidate=player, season=season, award_key="SUPER_BOWL_TITLE")
        inserted += 1

    return {"inserted": inserted, "skipped_existing": 0}


def rows_for_players(con: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids or not table_exists(con, "player_accolades"):
        return {player_id: [] for player_id in player_ids}
    placeholders = ",".join("?" for _ in player_ids)
    rows = con.execute(
        f"""
        SELECT pa.*, t.abbreviation AS team
        FROM player_accolades pa
        LEFT JOIN teams t ON t.team_id = pa.team_id
        WHERE pa.player_id IN ({placeholders})
          AND NOT (pa.award_key = 'POSITION_OF_YEAR' AND COALESCE(pa.source, '') = ?)
        ORDER BY pa.player_id, pa.sort_order, pa.season DESC, pa.award_name
        """,
        [*player_ids, SOURCE],
    ).fetchall()
    by_player: dict[int, list[dict[str, Any]]] = {player_id: [] for player_id in player_ids}
    for row in rows:
        by_player[int(row["player_id"])].append(
            {
                "season": int(row["season"]),
                "team": row["team"],
                "awardKey": row["award_key"],
                "awardName": row["award_name"],
                "awardGroup": row["award_group"],
                "awardPosition": row["award_position"],
                "badgeLabel": row["badge_label"],
                "badgeTier": row["badge_tier"],
                "sortOrder": int(row["sort_order"] or 100),
                "notes": row["notes"],
            }
        )
    return by_player


def summary_for_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str | None], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["awardKey"]), row.get("awardPosition"))
        current = groups.get(key)
        if not current:
            groups[key] = {
                "awardKey": row["awardKey"],
                "awardName": row["awardName"],
                "awardPosition": row.get("awardPosition"),
                "badgeLabel": row["badgeLabel"],
                "badgeTier": row["badgeTier"],
                "sortOrder": row["sortOrder"],
                "count": 0,
                "latestSeason": row["season"],
            }
            current = groups[key]
        current["count"] += 1
        current["latestSeason"] = max(int(current["latestSeason"]), int(row["season"]))
    return sorted(groups.values(), key=lambda item: (int(item["sortOrder"]), -int(item["latestSeason"]), item["awardName"]))


def accolades_for_players(con: sqlite3.Connection, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = rows_for_players(con, player_ids)
    return {
        player_id: {
            "badges": summary_for_rows(player_rows),
            "history": player_rows,
            "count": len(player_rows),
        }
        for player_id, player_rows in rows.items()
    }


def action_generate(args: argparse.Namespace) -> None:
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        result = generate_season_accolades(con, args.season, force=args.force)
        if args.apply:
            con.commit()
        else:
            con.rollback()
        mode = "Saved" if args.apply else "Dry run"
        print(f"{mode}: {result['inserted']} generated accolade row(s), {result['skipped_existing']} existing row(s) skipped.")
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate player accolades from season results.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate accolades for a completed season.")
    generate.add_argument("--season", type=int, required=True)
    generate.add_argument("--apply", action="store_true")
    generate.add_argument("--force", action="store_true")
    generate.set_defaults(func=action_generate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
