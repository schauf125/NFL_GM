#!/usr/bin/env python3
"""Season storyline hooks with small progression footprints.

Storylines are not just flavor text. Each item can carry a small momentum,
confidence, or potential delta that the offseason progression engine can read
later. The goal is to make camp reports, position battles, streaks, and trade
rumors feel connected to actual football context without overpowering ratings.
"""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path
from typing import Any

import league_news
import scouting
import pro_player_fog


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"

STORY_SOURCE = "Season Storylines"
PASS_RUSH_POSITIONS = {"EDGE", "OLB", "DE"}
YOUNG_CB_POSITIONS = {"CB", "NB", "FS", "SS", "S"}
SKILL_POSITIONS = {"QB", "RB", "WR", "TE", "CB", "NB", "EDGE"}


def connect(db_path: Path) -> sqlite3.Connection:
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
    scouting.ensure_schema(con)
    league_news.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS season_storyline_events (
            storyline_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER,
            event_date TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE CASCADE,
            secondary_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            storyline_type TEXT NOT NULL,
            momentum_delta REAL NOT NULL DEFAULT 0,
            confidence_delta REAL NOT NULL DEFAULT 0,
            potential_delta REAL NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'Season Storylines',
            tags_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_season_storyline_events_unique
            ON season_storyline_events(
                game_id,
                season,
                IFNULL(week, -1),
                event_date,
                IFNULL(player_id, -1),
                storyline_type
            );

        CREATE INDEX IF NOT EXISTS idx_season_storyline_events_game_player
            ON season_storyline_events(game_id, season, player_id);

        CREATE INDEX IF NOT EXISTS idx_season_storyline_events_game_date
            ON season_storyline_events(game_id, event_date DESC, storyline_id DESC);
        """
    )


def active_user_team_id(con: sqlite3.Connection) -> int | None:
    if not table_exists(con, "active_game_save_view"):
        return None
    row = con.execute("SELECT user_team_id FROM active_game_save_view LIMIT 1").fetchone()
    return int(row["user_team_id"]) if row and row["user_team_id"] is not None else None


def team_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT team_id, abbreviation FROM teams ORDER BY abbreviation").fetchall()


def roster_rows(con: sqlite3.Connection, team_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            p.player_id,
            p.team_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            COALESCE(p.age, 26) AS age,
            COALESCE(p.years_exp, 0) AS years_exp,
            COALESCE(p.is_rookie, 0) AS is_rookie,
            COALESCE(p.overall, 50) AS overall,
            COALESCE(p.potential, COALESCE(p.overall, 50)) AS potential,
            COALESCE(p.dev_trait, 'Normal') AS dev_trait,
            COALESCE(p.status, 'Active') AS status
        FROM players p
        WHERE p.team_id = ?
          AND COALESCE(p.status, 'Active') NOT IN ('Free Agent', 'Retired')
        ORDER BY p.position, COALESCE(p.overall, 50) DESC, p.player_id
        """,
        (team_id,),
    ).fetchall()


def insert_storyline(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_date: str,
    storyline_type: str,
    title: str,
    body: str,
    week: int | None = None,
    team_id: int | None = None,
    player_id: int | None = None,
    secondary_player_id: int | None = None,
    momentum_delta: float = 0.0,
    confidence_delta: float = 0.0,
    potential_delta: float = 0.0,
    tags: list[str] | None = None,
) -> bool:
    ensure_schema(con)
    before = con.total_changes
    con.execute(
        """
        INSERT OR IGNORE INTO season_storyline_events (
            game_id, season, week, event_date, team_id, player_id, secondary_player_id,
            storyline_type, momentum_delta, confidence_delta, potential_delta,
            title, body, tags_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            season,
            week,
            event_date,
            team_id,
            player_id,
            secondary_player_id,
            storyline_type,
            round(momentum_delta, 3),
            round(confidence_delta, 3),
            round(potential_delta, 3),
            title,
            body,
            json.dumps(tags or [], separators=(",", ":")),
        ),
    )
    return con.total_changes > before


def emit_user_message(
    con: sqlite3.Connection,
    *,
    game_id: str,
    user_team_id: int | None,
    team_id: int | None,
    title: str,
    body: str,
    event_date: str,
    season: int,
    player_id: int | None,
    priority: str = "normal",
) -> int:
    if not user_team_id or team_id != user_team_id:
        return 0
    body = pro_player_fog.append_event_read_note(
        body,
        con,
        game_id=game_id,
        team_id=team_id,
        player_id=player_id,
        season=season,
    )
    scouting.add_inbox_message(
        con,
        game_id=game_id,
        title=title,
        body=body,
        category="Team Storylines",
        priority=priority,
        source="Coaching Staff",
        message_date=event_date,
        related_table="players" if player_id else None,
        related_id=player_id,
    )
    return 1


def player_label(player: sqlite3.Row) -> str:
    return f"{player['player_name']} ({player['position']})"


def story_choice(rng: random.Random, options: list[str]) -> str:
    return options[rng.randrange(len(options))] if options else ""


def process_camp_storylines(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_date: str,
    seed: str | int | None = None,
    emit_messages: bool = True,
) -> dict[str, int]:
    """Create practice reports and camp position-battle notes."""
    ensure_schema(con)
    rng = random.Random(str(seed or f"{game_id}:{season}:{event_date}:camp-storylines"))
    user_team_id = active_user_team_id(con)
    inserted = inbox = news = 0

    for team in team_rows(con):
        team_id = int(team["team_id"])
        roster = roster_rows(con, team_id)
        if not roster:
            continue
        candidates = [
            player for player in roster
            if (
                (int(player["is_rookie"] or 0) and player["position"] in SKILL_POSITIONS)
                or (int(player["years_exp"] or 0) <= 2 and int(player["potential"] or 0) - int(player["overall"] or 0) >= 5)
                or (int(player["age"] or 0) >= 30 and int(player["overall"] or 0) >= 70)
            )
        ]
        rng.shuffle(candidates)
        for player in candidates[: rng.randint(1, 2)]:
            age = int(player["age"] or 26)
            rookie = int(player["is_rookie"] or 0) == 1
            potential_gap = int(player["potential"] or 50) - int(player["overall"] or 50)
            if age >= 30 and rng.random() < 0.55:
                kind = "practice_decline_note"
                title = story_choice(
                    rng,
                    [
                        f"Camp Note: {player['player_name']} Under Watch",
                        f"Staff Monitoring {player['player_name']}",
                        f"{player['player_name']} Has a Quieter Camp",
                    ],
                )
                body = story_choice(
                    rng,
                    [
                        f"Coaches still trust {player_label(player)}, but the practice notes have been a little less clean than usual. The staff wants to see whether it is timing, workload, or a real physical dip.",
                        f"{player_label(player)} is not in danger of being written off, but the staff has noticed fewer easy wins in individual work. Preseason reps should give a better read.",
                        f"The veteran baseline is still respected, but {player_label(player)} has looked more ordinary than expected in camp. Coaches are watching how the body responds as the weeks stack up.",
                    ],
                )
                momentum = -rng.uniform(0.18, 0.42)
                confidence = -rng.uniform(0.06, 0.18)
                potential = -rng.uniform(0.00, 0.06)
            elif rookie and rng.random() < 0.42:
                kind = "practice_playbook_struggle"
                title = story_choice(
                    rng,
                    [
                        f"Camp Note: {player['player_name']} Still Processing",
                        f"{player['player_name']} Learning the Details",
                        f"Rookie Check-In: {player['player_name']}",
                    ],
                )
                body = story_choice(
                    rng,
                    [
                        f"{player_label(player)} has flashed enough talent to keep coaches interested, but the assignment details are not automatic yet. The staff is treating it as a normal rookie checkpoint.",
                        f"The tools show up for {player_label(player)}, then the playbook slows the next rep down. Coaches want the game to settle before expanding the role.",
                        f"{player_label(player)} has had the kind of uneven camp that makes the preseason important: traits on one snap, corrections on the next.",
                    ],
                )
                momentum = -rng.uniform(0.08, 0.32)
                confidence = -rng.uniform(0.06, 0.20)
                potential = -rng.uniform(0.00, 0.04)
            else:
                kind = "practice_flash"
                title = story_choice(
                    rng,
                    [
                        f"Camp Note: {player['player_name']} Flashing",
                        f"{player['player_name']} Keeps Showing Up",
                        f"Staff Takes Notice of {player['player_name']}",
                    ],
                )
                body = story_choice(
                    rng,
                    [
                        f"{player_label(player)} has stacked enough positive practice reps for coaches to keep looking for a role. The next test is whether it carries into live work.",
                        f"The staff has started circling {player_label(player)} after a few loud camp reps. It is not a finished story yet, but the player has earned more attention.",
                        f"{player_label(player)} is giving the staff something to think about. Coaches want to see if the flash can become a weekly assignment instead of a camp note.",
                    ],
                )
                momentum = rng.uniform(0.16, 0.42)
                confidence = rng.uniform(0.05, 0.18)
                potential = rng.uniform(0.00, 0.07) if potential_gap >= 5 else 0.0
            if insert_storyline(
                con,
                game_id=game_id,
                season=season,
                event_date=event_date,
                team_id=team_id,
                player_id=int(player["player_id"]),
                storyline_type=kind,
                momentum_delta=momentum,
                confidence_delta=confidence,
                potential_delta=potential,
                title=title,
                body=body,
                tags=["camp", "practice", str(player["position"])],
            ):
                inserted += 1
                pro_player_fog.apply_evaluation_event(
                    con,
                    game_id=game_id,
                    player_id=int(player["player_id"]),
                    team_id=team_id,
                    season=season,
                    event_date=event_date,
                    event_type=kind,
                    signal_strength=momentum + confidence + potential,
                    snap_count=0,
                    source="camp_storyline_pro_fog",
                    notes="Training camp report sharpened the staff evaluation read.",
                )
                if emit_messages:
                    inbox += emit_user_message(
                        con,
                        game_id=game_id,
                        user_team_id=user_team_id,
                        team_id=team_id,
                        title=title,
                        body=body,
                        event_date=event_date,
                        season=season,
                        player_id=int(player["player_id"]),
                    )

        inserted_battle, inbox_battle, news_battle = process_position_battles_for_team(
            con,
            game_id=game_id,
            season=season,
            event_date=event_date,
            team_id=team_id,
            roster=roster,
            rng=rng,
            user_team_id=user_team_id,
            emit_messages=emit_messages,
        )
        inserted += inserted_battle
        inbox += inbox_battle
        news += news_battle
    return {"inserted": inserted, "inbox": inbox, "news": news}


def process_position_battles_for_team(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_date: str,
    team_id: int,
    roster: list[sqlite3.Row],
    rng: random.Random,
    user_team_id: int | None,
    emit_messages: bool,
) -> tuple[int, int, int]:
    by_pos: dict[str, list[sqlite3.Row]] = {}
    for player in roster:
        pos = str(player["position"])
        if pos in {"K", "P", "LS"}:
            continue
        by_pos.setdefault(pos, []).append(player)
    battles = []
    for pos, players in by_pos.items():
        if len(players) < 2:
            continue
        ordered = sorted(players, key=lambda p: int(p["overall"] or 0), reverse=True)
        a, b = ordered[0], ordered[1]
        gap = abs(int(a["overall"] or 0) - int(b["overall"] or 0))
        if gap <= 4 or (int(b["potential"] or 0) - int(a["overall"] or 0) >= 2 and gap <= 7):
            battles.append((pos, a, b, gap))
    rng.shuffle(battles)
    inserted = inbox = news = 0
    for pos, leader, challenger, _gap in battles[:1]:
        challenger_score = int(challenger["potential"] or 0) - int(challenger["overall"] or 0) + rng.uniform(-2.0, 3.5)
        leader_score = int(leader["overall"] or 0) - int(challenger["overall"] or 0) + rng.uniform(-1.5, 2.5)
        winner, other = (challenger, leader) if challenger_score > leader_score else (leader, challenger)
        title = story_choice(
            rng,
            [
                f"{pos} Battle Tightens: {winner['player_name']}",
                f"Camp Competition Brewing at {pos}",
                f"{winner['player_name']} Forces a Longer Look",
            ],
        )
        body = story_choice(
            rng,
            [
                f"{player_label(winner)} has made the {pos} competition more interesting. {player_label(other)} is still firmly in the mix, but the staff has a real decision to monitor through preseason.",
                f"The staff expected the {pos} room to have an order by now, but {player_label(winner)} has kept the battle alive. {player_label(other)} still has the cleaner resume.",
                f"{player_label(winner)} has done enough in camp to keep stealing staff meeting time. The coaches are not ready to move off {player_label(other)}, but the gap is worth watching.",
            ],
        )
        if insert_storyline(
            con,
            game_id=game_id,
            season=season,
            event_date=event_date,
            team_id=team_id,
            player_id=int(winner["player_id"]),
            secondary_player_id=int(other["player_id"]),
            storyline_type="position_battle",
            momentum_delta=0.32,
            confidence_delta=0.14,
            potential_delta=0.03 if int(winner["years_exp"] or 0) <= 2 else 0.0,
            title=title,
            body=body,
            tags=["camp", "position_battle", pos],
        ):
            inserted += 1
            pro_player_fog.apply_evaluation_event(
                con,
                game_id=game_id,
                player_id=int(winner["player_id"]),
                team_id=team_id,
                season=season,
                event_date=event_date,
                event_type="position_battle",
                signal_strength=0.49,
                snap_count=0,
                source="position_battle_pro_fog",
                notes="Position battle reps gave the staff a clearer read.",
            )
            if emit_messages:
                inbox += emit_user_message(
                    con,
                    game_id=game_id,
                    user_team_id=user_team_id,
                    team_id=team_id,
                    title=title,
                    body=body,
                    event_date=event_date,
                    season=season,
                    player_id=int(winner["player_id"]),
                    priority="high" if team_id == user_team_id else "normal",
                )
            if rng.random() < 0.05:
                news_id = league_news.add_news_item(
                    con,
                    game_id=game_id,
                    news_date=event_date,
                    category="Training Camp",
                    priority="normal",
                    source="Camp Wire",
                    title=title,
                    body=body,
                    team_id=team_id,
                    player_id=int(winner["player_id"]),
                    tags=["camp", "position_battle", pos],
                    fingerprint=league_news.fingerprint_for("position-battle", game_id, season, team_id, pos, int(winner["player_id"])),
                )
                news += 1 if news_id is not None else 0
    return inserted, inbox, news


def weekly_player_stat_map(con: sqlite3.Connection, season: int, week: int) -> dict[int, dict[str, float]]:
    if not table_exists(con, "game_player_stats") or not table_exists(con, "game_sim_runs"):
        return {}
    rows = con.execute(
        """
        SELECT gps.player_id, gps.team_id, gps.stat_key, SUM(gps.stat_value) AS value
        FROM game_player_stats gps
        JOIN game_sim_runs gsr ON gsr.run_id = gps.run_id
        WHERE gsr.season = ?
          AND gsr.week = ?
          AND gsr.status = 'final'
        GROUP BY gps.player_id, gps.team_id, gps.stat_key
        """,
        (season, week),
    ).fetchall()
    stats: dict[int, dict[str, float]] = {}
    for row in rows:
        data = stats.setdefault(int(row["player_id"]), {"team_id": int(row["team_id"])})
        data[str(row["stat_key"])] = float(row["value"] or 0.0)
    return stats


def process_weekly_storylines(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    event_date: str,
    seed: str | int | None = None,
    emit_messages: bool = True,
) -> dict[str, int]:
    ensure_schema(con)
    rng = random.Random(str(seed or f"{game_id}:{season}:{week}:weekly-storylines"))
    user_team_id = active_user_team_id(con)
    stats_by_player = weekly_player_stat_map(con, season, week)
    if not stats_by_player:
        return {"inserted": 0, "inbox": 0, "news": 0, "trade_rumors": 0}

    players = {
        int(row["player_id"]): row
        for row in con.execute(
            """
            SELECT player_id, team_id, first_name || ' ' || last_name AS player_name,
                   position, COALESCE(age, 26) AS age, COALESCE(years_exp, 0) AS years_exp,
                   COALESCE(is_rookie, 0) AS is_rookie, COALESCE(overall, 50) AS overall,
                   COALESCE(potential, COALESCE(overall, 50)) AS potential
            FROM players
            WHERE team_id IS NOT NULL
            """
        ).fetchall()
    }
    candidates = []
    for player_id, stats in stats_by_player.items():
        player = players.get(player_id)
        if not player:
            continue
        score = weekly_confidence_score(player, stats)
        if abs(score) >= 0.42:
            candidates.append((abs(score), score, player, stats))
    candidates.sort(key=lambda item: (item[0], rng.random()), reverse=True)

    inserted = inbox = news = 0
    for _abs_score, score, player, stats in candidates[:10]:
        title, body, kind = weekly_story_text(player, stats, score)
        event_snaps = pro_player_fog.effective_snap_evidence(stats)
        if insert_storyline(
            con,
            game_id=game_id,
            season=season,
            week=week,
            event_date=event_date,
            team_id=int(player["team_id"]),
            player_id=int(player["player_id"]),
            storyline_type=kind,
            momentum_delta=score * 0.34,
            confidence_delta=score * 0.26,
            potential_delta=0.02 if score > 0.65 and int(player["years_exp"] or 0) <= 1 else 0.0,
            title=title,
            body=body,
            tags=["streak", str(player["position"])],
        ):
            inserted += 1
            pro_player_fog.apply_evaluation_event(
                con,
                game_id=game_id,
                player_id=int(player["player_id"]),
                team_id=int(player["team_id"]),
                season=season,
                event_date=event_date,
                event_type=kind,
                signal_strength=score,
                snap_count=event_snaps,
                source="weekly_storyline_pro_fog",
                notes="Game snaps attached to this report sharpened the staff evaluation read.",
            )
            if emit_messages:
                inbox += emit_user_message(
                    con,
                    game_id=game_id,
                    user_team_id=user_team_id,
                    team_id=int(player["team_id"]),
                    title=title,
                    body=body,
                    event_date=event_date,
                    season=season,
                    player_id=int(player["player_id"]),
                )
            if rng.random() < (0.16 if abs(score) >= 0.8 else 0.06):
                news_id = league_news.add_news_item(
                    con,
                    game_id=game_id,
                    news_date=event_date,
                    category="Performance",
                    priority="normal",
                    source="League Wire",
                    title=title,
                    body=body,
                    team_id=int(player["team_id"]),
                    player_id=int(player["player_id"]),
                    tags=["streak", str(player["position"])],
                    fingerprint=league_news.fingerprint_for("weekly-streak", game_id, season, week, int(player["player_id"]), kind),
                )
                news += 1 if news_id is not None else 0
    trade_rumors = process_trade_deadline_rumors(
        con,
        game_id=game_id,
        season=season,
        week=week,
        event_date=event_date,
        seed=f"{seed}:trade-rumors" if seed is not None else None,
    )
    return {"inserted": inserted, "inbox": inbox, "news": news, "trade_rumors": trade_rumors}


def weekly_confidence_score(player: sqlite3.Row, stats: dict[str, float]) -> float:
    pos = str(player["position"])
    age = int(player["age"] or 26)
    years = int(player["years_exp"] or 0)
    rookie = int(player["is_rookie"] or 0) == 1
    if pos == "QB":
        attempts = stats.get("pass_attempts", 0.0)
        if attempts < 16:
            return 0.0
        score = stats.get("pass_tds", 0.0) * 0.24 + stats.get("pass_yards", 0.0) / 260.0
        score -= stats.get("interceptions_thrown", 0.0) * 0.34
        score -= stats.get("sacks_taken", 0.0) * 0.04
        return max(-1.0, min(1.0, (score - 1.05) / 1.20))
    if pos == "K":
        attempts = stats.get("fg_attempts", 0.0)
        if attempts < 2:
            return 0.0
        made = stats.get("fg_made", 0.0)
        score = (made / max(1.0, attempts) - 0.78) * 2.0
        if stats.get("long_fg", 0.0) >= 52:
            score += 0.18
        return max(-1.0, min(1.0, score))
    if rookie:
        total = stats.get("total_snaps", stats.get("offensive_snaps", 0.0) + stats.get("defensive_snaps", 0.0))
        if total < 18:
            return 0.0
        production = (
            stats.get("rush_yards", 0.0) / 85.0
            + stats.get("receiving_yards", 0.0) / 80.0
            + stats.get("sacks", 0.0) * 0.35
            + stats.get("interceptions", 0.0) * 0.45
            + stats.get("pass_deflections", 0.0) * 0.12
        )
        return max(-0.75, min(0.9, production - 0.38))
    if age <= 24 and pos in YOUNG_CB_POSITIONS:
        snaps = stats.get("defensive_snaps", 0.0)
        if snaps < 28:
            return 0.0
        plays = stats.get("interceptions", 0.0) * 0.55 + stats.get("pass_deflections", 0.0) * 0.14
        tackles = stats.get("tackles", 0.0) * 0.03
        return max(-0.65, min(0.85, plays + tackles - 0.35))
    if years <= 2 and pos in PASS_RUSH_POSITIONS:
        snaps = stats.get("defensive_snaps", 0.0)
        if snaps < 20:
            return 0.0
        return max(-0.65, min(0.85, stats.get("sacks", 0.0) * 0.34 + stats.get("qb_hits", 0.0) * 0.08 - 0.28))
    return 0.0


def weekly_story_text(player: sqlite3.Row, stats: dict[str, float], score: float) -> tuple[str, str, str]:
    name = str(player["player_name"])
    pos = str(player["position"])
    rng = random.Random(f"{player['player_id']}:{pos}:{round(score, 3)}:{round(sum(stats.values()), 2)}")
    if score > 0:
        title = story_choice(
            rng,
            [
                f"{name} Builds Momentum",
                f"{name} Gives Staff a Strong Week",
                f"{name} Makes the Week Count",
            ],
        )
        body = story_choice(
            rng,
            [
                f"{name} ({pos}) put together the kind of week coaches can point to in meetings. The staff wants to see whether it becomes a trend or stays as one good Sunday.",
                f"The staff came away encouraged by {name}'s ({pos}) week. It was not enough to settle the long-term read, but it gives the player something to build on.",
                f"{name} ({pos}) gave the coaches a positive data point this week. Another similar performance would make the role conversation more interesting.",
            ],
        )
        return title, body, "hot_streak"
    title = story_choice(
        rng,
        [
            f"{name} Draws Staff Attention",
            f"{name} Has a Week to Clean Up",
            f"Coaches Want a Response from {name}",
        ],
    )
    body = story_choice(
        rng,
        [
            f"{name} ({pos}) had a shaky week, and the staff wants a cleaner response before letting one game change the evaluation.",
            f"The coaches are not overreacting to {name}'s ({pos}) week, but the mistakes were clear enough to make the next one matter.",
            f"{name} ({pos}) left the staff with corrections to make. The bigger question is whether the player responds quickly or lets it linger.",
        ],
    )
    return title, body, "cold_streak"


def process_trade_deadline_rumors(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    event_date: str,
    seed: str | int | None = None,
) -> int:
    if week not in {7, 8, 9}:
        return 0
    if not table_exists(con, "season_team_records"):
        return 0
    rng = random.Random(str(seed or f"{game_id}:{season}:{week}:trade-rumors"))
    records = con.execute(
        """
        SELECT str.team_id, str.wins, str.losses, str.ties,
               COALESCE(str.points_for, 0) - COALESCE(str.points_against, 0) AS point_diff,
               t.abbreviation
        FROM season_team_records str
        JOIN teams t ON t.team_id = str.team_id
        WHERE str.season = ?
        """,
        (season,),
    ).fetchall()
    sellers = []
    buyers = []
    for row in records:
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        ties = int(row["ties"] or 0)
        games = max(1, wins + losses + ties)
        pct = (wins + ties * 0.5) / games
        diff = float(row["point_diff"] or 0) / games
        if pct <= 0.36 or diff <= -7.0:
            sellers.append(row)
        elif pct >= 0.58 or diff >= 5.0:
            buyers.append(row)
    if not sellers or not buyers:
        return 0
    created = 0
    rng.shuffle(sellers)
    for seller in sellers[:5]:
        team_id = int(seller["team_id"])
        vets = con.execute(
            """
            SELECT p.player_id, p.first_name || ' ' || p.last_name AS player_name,
                   p.position, COALESCE(p.age, 26) AS age, COALESCE(p.overall, 50) AS overall
            FROM players p
            WHERE p.team_id = ?
              AND COALESCE(p.status, 'Active') = 'Active'
              AND COALESCE(p.age, 26) >= 29
              AND COALESCE(p.overall, 50) >= 72
              AND p.position NOT IN ('QB', 'K', 'P', 'LS')
            ORDER BY COALESCE(p.overall, 50) DESC, p.player_id
            LIMIT 8
            """,
            (team_id,),
        ).fetchall()
        if not vets:
            continue
        player = rng.choice(vets[: min(4, len(vets))])
        buyer = rng.choice(buyers)
        title = story_choice(
            rng,
            [
                f"Deadline Watch: {player['player_name']}",
                f"{seller['abbreviation']} Could Get Calls on {player['player_name']}",
                f"Trade Market Watching {player['player_name']}",
            ],
        )
        body = story_choice(
            rng,
            [
                f"With {seller['abbreviation']} drifting toward seller territory, rival teams believe veteran {player['player_name']} ({player['position']}) could draw calls. Contenders like {buyer['abbreviation']} are expected to monitor holes before the deadline.",
                f"Teams around the league are watching whether {seller['abbreviation']} gets realistic about selling. {player['player_name']} ({player['position']}) is one name that could interest a contender such as {buyer['abbreviation']}.",
                f"{seller['abbreviation']} is not openly shopping {player['player_name']}, but the club's record has made other front offices curious. {buyer['abbreviation']} is among the teams expected to keep tabs on the market.",
            ],
        )
        news_id = league_news.add_news_item(
            con,
            game_id=game_id,
            news_date=event_date,
            category="Trade Rumor",
            priority="normal",
            source="League Wire",
            title=title,
            body=body,
            team_id=team_id,
            player_id=int(player["player_id"]),
            tags=["trade_deadline", "rumor", str(player["position"])],
            fingerprint=league_news.fingerprint_for("trade-rumor", game_id, season, week, team_id, int(player["player_id"])),
        )
        if news_id is not None:
            created += 1
    return created


def load_progression_context(con: sqlite3.Connection, *, game_id: str, season: int) -> dict[int, dict[str, float]]:
    if not table_exists(con, "season_storyline_events"):
        return {}
    rows = con.execute(
        """
        SELECT
            player_id,
            SUM(COALESCE(momentum_delta, 0)) AS momentum,
            SUM(COALESCE(confidence_delta, 0)) AS confidence,
            SUM(COALESCE(potential_delta, 0)) AS potential,
            COUNT(*) AS story_count
        FROM season_storyline_events
        WHERE game_id = ?
          AND season = ?
          AND player_id IS NOT NULL
        GROUP BY player_id
        """,
        (game_id, season),
    ).fetchall()
    return {
        int(row["player_id"]): {
            "storyline_momentum": float(row["momentum"] or 0.0),
            "storyline_confidence": float(row["confidence"] or 0.0),
            "storyline_potential": float(row["potential"] or 0.0),
            "storyline_count": float(row["story_count"] or 0.0),
        }
        for row in rows
    }
