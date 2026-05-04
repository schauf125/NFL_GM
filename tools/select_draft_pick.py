#!/usr/bin/env python3
"""Select draft prospects and convert them into normal player rows.

The draft generator keeps prospects out of the real player universe. This tool
is the bridge that runs when a team actually makes a pick.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DATABASE_DIR = ROOT / "database"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DATABASE_DIR) not in sys.path:
    sys.path.insert(0, str(DATABASE_DIR))

from engine.draft.schema import ensure_schema as ensure_draft_schema
from migrate_legacy_sim_ratings import ensure_sim_rating_schema
from setup_contract_years import (
    ensure_schema as ensure_contract_schema,
    rebuild_contract_year,
    rebuild_contract_years,
    sync_team_cap_space,
)
from setup_transactions_cap_ledger import (
    current_season,
    ensure_schema as ensure_transaction_schema,
    insert_transaction,
    snapshot_cap_ledger,
)

import player_personalities
import player_development_modifiers
import scheme_fits


SOURCE = "draft_selection"
DEFAULT_PHASE = "Draft"


@dataclass(frozen=True)
class RookieContractEstimate:
    total_value: int
    years: int
    aav: int
    signing_bonus: int
    guaranteed: int
    option_year: int
    effective_pick_number: int
    effective_pick_in_round: int
    estimate_note: str


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_all_schema(con: sqlite3.Connection) -> None:
    ensure_sim_rating_schema(con)
    ensure_contract_schema(con)
    ensure_transaction_schema(con)
    ensure_draft_schema(con)
    player_personalities.ensure_schema(con)
    player_personalities.seed_trait_definitions(con)
    player_development_modifiers.seed_master_data(con)
    scheme_fits.seed_master_data(con)
    con.execute(
        """
        INSERT INTO transaction_types (transaction_type, category, description)
        VALUES ('Draft Selection', 'Draft', 'A draft prospect was selected and added to a team roster.')
        ON CONFLICT(transaction_type) DO UPDATE SET
            category = excluded.category,
            description = excluded.description
        """
    )


def current_setting(con: sqlite3.Connection, key: str) -> str | None:
    try:
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = ?",
            (key,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row["setting_value"]) if row else None


def upsert_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    try:
        con.execute(
            """
            INSERT INTO game_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = datetime('now')
            """,
            (key, value),
        )
    except sqlite3.OperationalError:
        return


def mark_depth_chart_stale(con: sqlite3.Connection, team_abbr: str | None) -> None:
    if not team_abbr:
        return
    upsert_setting(con, f"depth_chart_needs_update_{team_abbr.upper()}", "1")
    upsert_setting(con, "depth_chart_needs_update", "1")


def current_game_date(con: sqlite3.Connection, draft_year: int) -> str:
    return current_setting(con, "current_game_date") or f"{draft_year}-04-30"


def current_phase(con: sqlite3.Connection) -> str:
    return current_setting(con, "current_calendar_phase") or DEFAULT_PHASE


def active_game_id(con: sqlite3.Connection) -> str | None:
    setting = current_setting(con, "active_game_id")
    if setting:
        return setting
    try:
        row = con.execute(
            """
            SELECT game_id
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row["game_id"]) if row else None


def latest_development_seed(con: sqlite3.Connection, game_id: str) -> int | str:
    try:
        row = con.execute(
            """
            SELECT rng_seed
            FROM new_game_development_runs
            WHERE game_id = ?
            ORDER BY season DESC, run_id DESC
            LIMIT 1
            """,
            (game_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return game_id
    return int(row["rng_seed"]) if row and row["rng_seed"] is not None else game_id


def ensure_scheme_context_for_season(con: sqlite3.Connection, season: int) -> None:
    scheme_fits.seed_master_data(con)
    coach_rows = con.execute(
        "SELECT COUNT(*) FROM coach_scheme_fits WHERE season = ?",
        (season,),
    ).fetchone()[0]
    if int(coach_rows or 0) == 0:
        scheme_fits.seed_coach_scheme_fits(con, season)
    team_rows = con.execute(
        "SELECT COUNT(*) FROM team_scheme_identities WHERE season = ?",
        (season,),
    ).fetchone()[0]
    if int(team_rows or 0) == 0:
        scheme_fits.seed_team_identities(con, season)


def initialize_new_player_foundation(con: sqlite3.Connection, *, player_id: int, season: int) -> dict[str, int]:
    """Give drafted/UDFA rookies the same foundation rows as new-save players."""
    ensure_scheme_context_for_season(con, season)
    scheme_rows = scheme_fits.seed_player_scheme_fits(con, season, player_ids=[player_id])

    game_id = active_game_id(con)
    if not game_id:
        return {
            "development_modifiers": 0,
            "development_profiles": 0,
            "scheme_fits": scheme_rows,
        }

    seed = latest_development_seed(con, game_id)
    development = player_development_modifiers.seed_development_for_players(
        con,
        game_id=game_id,
        season=season,
        player_ids=[player_id],
        seed=f"{seed}:draft_player_foundation:{season}",
        source=SOURCE,
        notes="Supplemental rookie foundation seeded at draft conversion.",
    )
    return {
        "development_modifiers": development["modifiers"],
        "development_profiles": development["profiles"],
        "scheme_fits": scheme_rows,
    }


def team_by_abbr(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return row


def pick_by_args(con: sqlite3.Connection, args: argparse.Namespace) -> sqlite3.Row:
    if args.pick_id is not None:
        row = con.execute(
            """
            SELECT dp.*, current.abbreviation AS current_team, original.abbreviation AS original_team
            FROM draft_picks dp
            LEFT JOIN teams current ON current.team_id = dp.current_team_id
            LEFT JOIN teams original ON original.team_id = dp.original_team_id
            WHERE dp.pick_id = ?
            """,
            (args.pick_id,),
        ).fetchone()
    else:
        if not args.team or args.round is None:
            raise ValueError("Use --pick-id or provide --team and --round.")
        team = team_by_abbr(con, args.team)
        params: list[Any] = [args.draft_year, int(team["team_id"]), args.round]
        filters = [
            "dp.draft_year = ?",
            "dp.current_team_id = ?",
            "dp.round = ?",
            "COALESCE(dp.is_used, 0) = 0",
        ]
        if args.pick_in_round is not None:
            filters.append("dp.pick_in_round = ?")
            params.append(args.pick_in_round)
        row = con.execute(
            f"""
            SELECT dp.*, current.abbreviation AS current_team, original.abbreviation AS original_team
            FROM draft_picks dp
            LEFT JOIN teams current ON current.team_id = dp.current_team_id
            LEFT JOIN teams original ON original.team_id = dp.original_team_id
            WHERE {' AND '.join(filters)}
            ORDER BY
                CASE WHEN dp.pick_in_round IS NULL THEN 1 ELSE 0 END,
                dp.pick_in_round,
                dp.pick_id
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        raise ValueError("Draft pick not found for the supplied criteria.")
    if int(row["draft_year"]) != int(args.draft_year):
        raise ValueError(f"Pick belongs to {row['draft_year']}, not {args.draft_year}.")
    if int(row["is_used"] or 0):
        raise ValueError(f"Pick {row['pick_id']} has already been used.")
    if row["current_team_id"] is None:
        raise ValueError(f"Pick {row['pick_id']} has no current team owner.")
    return row


def prospect_by_args(con: sqlite3.Connection, args: argparse.Namespace) -> sqlite3.Row:
    params: list[Any]
    if args.prospect_id is not None:
        filters = ["dp.prospect_id = ?"]
        params = [args.prospect_id]
    elif args.prospect:
        filters = ["lower(dp.first_name || ' ' || dp.last_name) LIKE ?"]
        params = [f"%{args.prospect.lower()}%"]
    elif args.board_rank is not None:
        filters = ["COALESCE(dp.public_board_rank, dp.scouting_rank) = ?"]
        params = [args.board_rank]
    else:
        raise ValueError("Use --prospect-id, --prospect, or --board-rank.")

    filters.append("dc.draft_year = ?")
    params.append(args.draft_year)
    rows = con.execute(
        f"""
        SELECT
            dp.*,
            dc.draft_year,
            COALESCE(dp.public_board_rank, dp.scouting_rank) AS board_rank
        FROM draft_prospects dp
        JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        WHERE {' AND '.join(filters)}
        ORDER BY
            CASE WHEN COALESCE(dp.public_board_rank, dp.scouting_rank) IS NULL THEN 1 ELSE 0 END,
            COALESCE(dp.public_board_rank, dp.scouting_rank),
            dp.prospect_id
        """,
        params,
    ).fetchall()
    if not rows:
        raise ValueError("Draft prospect not found for the supplied criteria.")
    if len(rows) > 1:
        summary = ", ".join(
            f"{row['prospect_id']}:{row['first_name']} {row['last_name']} ({row['position']}, rank {row['board_rank']})"
            for row in rows[:8]
        )
        raise ValueError(f"Multiple prospects matched. Use --prospect-id. Matches: {summary}")
    row = rows[0]
    if row["status"] != "Available":
        raise ValueError(f"Prospect {row['prospect_id']} is not available; status={row['status']}.")
    if row["player_id"] is not None:
        raise ValueError(f"Prospect {row['prospect_id']} already has player_id={row['player_id']}.")
    return row


def money(value: int | None) -> str:
    if value is None:
        return "-"
    if value < 0:
        return "-" + money(abs(value))
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value}"


def round_money(value: float, nearest: int = 10_000) -> int:
    return int(round(value / nearest) * nearest)


def interpolate(start: int, end: int, pick_in_round: int) -> int:
    slot = max(1, min(32, pick_in_round))
    pct = (slot - 1) / 31
    return round_money(start + ((end - start) * pct))


def estimate_rookie_contract(pick: sqlite3.Row, *, overall_pick: int | None = None) -> RookieContractEstimate:
    round_number = int(pick["round"])
    stored_pick_number = pick["pick_number"]
    pick_number = overall_pick if overall_pick is not None else stored_pick_number
    pick_in_round = pick["pick_in_round"]
    estimated = False
    if pick_number is None:
        estimated = True
        if pick_in_round is None:
            pick_in_round = 16
        pick_number = ((round_number - 1) * 32) + int(pick_in_round)
    if pick_in_round is None:
        pick_in_round = ((int(pick_number) - 1) % 32) + 1

    ranges = {
        1: (45_000_000, 14_000_000),
        2: (10_500_000, 6_600_000),
        3: (6_600_000, 5_300_000),
        4: (5_100_000, 4_500_000),
        5: (4_500_000, 4_150_000),
        6: (4_150_000, 3_950_000),
        7: (3_950_000, 3_800_000),
    }
    bonus_pct_by_round = {
        1: (0.66, 0.42),
        2: (0.36, 0.26),
        3: (0.22, 0.16),
        4: (0.13, 0.09),
        5: (0.08, 0.055),
        6: (0.05, 0.035),
        7: (0.035, 0.02),
    }
    total_start, total_end = ranges.get(round_number, ranges[7])
    bonus_start, bonus_end = bonus_pct_by_round.get(round_number, bonus_pct_by_round[7])
    total_value = interpolate(total_start, total_end, int(pick_in_round))
    bonus_pct = bonus_start + ((bonus_end - bonus_start) * ((max(1, min(32, int(pick_in_round))) - 1) / 31))
    signing_bonus = round_money(total_value * bonus_pct)
    years = 4
    if overall_pick is not None and stored_pick_number != overall_pick:
        note = "Overall pick override used."
    elif estimated:
        note = "Pick number missing; rookie scale estimated from round/mid-round slot."
    else:
        note = "Exact pick number used."
    return RookieContractEstimate(
        total_value=total_value,
        years=years,
        aav=total_value // years,
        signing_bonus=signing_bonus,
        guaranteed=1,
        option_year=1 if round_number == 1 else 0,
        effective_pick_number=int(pick_number),
        effective_pick_in_round=int(pick_in_round),
        estimate_note=note,
    )


def legacy_values(prospect: sqlite3.Row) -> dict[str, Any]:
    return {
        "overall": prospect["true_grade"] if prospect["true_grade"] is not None else prospect["overall"],
        "potential": prospect["ceiling_grade"] if prospect["ceiling_grade"] is not None else prospect["potential"],
        "dev_trait": prospect["dev_trait"] or "Normal",
        "speed": prospect["speed"],
        "strength": prospect["strength"],
        "agility": prospect["agility"],
        "awareness": prospect["awareness"],
        "injury_prone": prospect["injury_prone"],
        "throw_power": prospect["throw_power"],
        "throw_acc": prospect["throw_acc"],
        "route_running": prospect["route_running"],
        "catching": prospect["catching"],
        "run_blocking": prospect["run_blocking"],
        "pass_blocking": prospect["pass_blocking"],
        "trucking": prospect["trucking"],
        "tackle": prospect["tackle"],
        "pass_rush": prospect["pass_rush"],
        "coverage": prospect["coverage"],
        "kick_power": prospect["kick_power"],
        "kick_acc": prospect["kick_acc"],
    }


def draft_accolade(pick: sqlite3.Row, contract: RookieContractEstimate, draft_year: int) -> str:
    if pick["pick_number"] is not None or contract.effective_pick_number:
        suffix = ordinal(contract.effective_pick_number)
        return f"{suffix} Pick {draft_year}"
    return f"Round {pick['round']} Pick {contract.effective_pick_in_round} {draft_year}"


def ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def insert_player(
    con: sqlite3.Connection,
    *,
    prospect: sqlite3.Row,
    team_id: int,
    draft_year: int,
    pick: sqlite3.Row,
    rookie_contract: RookieContractEstimate,
) -> int:
    legacy = legacy_values(prospect)
    accolade = draft_accolade(pick, rookie_contract, draft_year)
    cur = con.execute(
        """
        INSERT INTO players (
            first_name, last_name, position, team_id, age, years_exp,
            college, height_in, weight_lbs,
            overall, potential, dev_trait,
            speed, strength, agility, awareness, injury_prone,
            throw_power, throw_acc, route_running, catching,
            run_blocking, pass_blocking, trucking, tackle,
            pass_rush, coverage, kick_power, kick_acc,
            status, is_rookie, accolades
        )
        VALUES (
            ?, ?, ?, ?, ?, 0,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            'Active', 1, ?
        )
        """,
        (
            prospect["first_name"],
            prospect["last_name"],
            prospect["position"],
            team_id,
            prospect["age"],
            prospect["college"],
            prospect["height_in"],
            prospect["weight_lbs"],
            legacy["overall"],
            legacy["potential"],
            legacy["dev_trait"],
            legacy["speed"],
            legacy["strength"],
            legacy["agility"],
            legacy["awareness"],
            legacy["injury_prone"],
            legacy["throw_power"],
            legacy["throw_acc"],
            legacy["route_running"],
            legacy["catching"],
            legacy["run_blocking"],
            legacy["pass_blocking"],
            legacy["trucking"],
            legacy["tackle"],
            legacy["pass_rush"],
            legacy["coverage"],
            legacy["kick_power"],
            legacy["kick_acc"],
            json.dumps([accolade]),
        ),
    )
    return int(cur.lastrowid)


def insert_undrafted_free_agent_player(
    con: sqlite3.Connection,
    *,
    prospect: sqlite3.Row,
    draft_year: int,
) -> int:
    legacy = legacy_values(prospect)
    accolade = f"Undrafted Free Agent {draft_year}"
    cur = con.execute(
        """
        INSERT INTO players (
            first_name, last_name, position, team_id, age, years_exp,
            college, height_in, weight_lbs,
            overall, potential, dev_trait,
            speed, strength, agility, awareness, injury_prone,
            throw_power, throw_acc, route_running, catching,
            run_blocking, pass_blocking, trucking, tackle,
            pass_rush, coverage, kick_power, kick_acc,
            status, is_rookie, accolades
        )
        VALUES (
            ?, ?, ?, NULL, ?, 0,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            'Free Agent', 1, ?
        )
        """,
        (
            prospect["first_name"],
            prospect["last_name"],
            prospect["position"],
            prospect["age"],
            prospect["college"],
            prospect["height_in"],
            prospect["weight_lbs"],
            legacy["overall"],
            legacy["potential"],
            legacy["dev_trait"],
            legacy["speed"],
            legacy["strength"],
            legacy["agility"],
            legacy["awareness"],
            legacy["injury_prone"],
            legacy["throw_power"],
            legacy["throw_acc"],
            legacy["route_running"],
            legacy["catching"],
            legacy["run_blocking"],
            legacy["pass_blocking"],
            legacy["trucking"],
            legacy["tackle"],
            legacy["pass_rush"],
            legacy["coverage"],
            legacy["kick_power"],
            legacy["kick_acc"],
            json.dumps([accolade]),
        ),
    )
    return int(cur.lastrowid)


def copy_ratings(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    rows = con.execute(
        """
        SELECT rating_key, rating_value, confidence, notes
        FROM draft_prospect_ratings
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchall()
    if not rows:
        raise ValueError(f"Prospect {prospect_id} has no draft_prospect_ratings rows.")
    con.executemany(
        """
        INSERT INTO player_ratings (
            player_id, season, rating_key, rating_value, confidence, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season, rating_key) DO UPDATE SET
            rating_value = excluded.rating_value,
            confidence = excluded.confidence,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        [
            (
                player_id,
                season,
                row["rating_key"],
                int(row["rating_value"]),
                row["confidence"] or "high",
                SOURCE,
                row["notes"] or "Copied from generated draft prospect true ratings.",
            )
            for row in rows
        ],
    )
    return len(rows)


def convert_undrafted_prospect_to_free_agent(
    con: sqlite3.Connection,
    *,
    prospect: sqlite3.Row,
    draft_year: int,
) -> dict[str, Any]:
    if prospect["player_id"] is not None:
        return {
            "prospect_id": int(prospect["prospect_id"]),
            "player_id": int(prospect["player_id"]),
            "player_name": f"{prospect['first_name']} {prospect['last_name']}",
            "position": prospect["position"],
            "converted": False,
            "reason": "already_has_player",
        }

    player_id = insert_undrafted_free_agent_player(
        con,
        prospect=prospect,
        draft_year=draft_year,
    )
    rating_rows = copy_ratings(con, int(prospect["prospect_id"]), player_id, draft_year)
    role_assignment_rows, role_score_rows = copy_roles(con, int(prospect["prospect_id"]), player_id, draft_year)
    insert_primary_flex(con, prospect, player_id)
    insert_career_shell(con, player_id, "FA")
    personality_rows = copy_draft_personalities(
        con,
        prospect_id=int(prospect["prospect_id"]),
        player_id=player_id,
        season=draft_year,
    )
    foundation = initialize_new_player_foundation(con, player_id=player_id, season=draft_year)
    con.execute(
        """
        UPDATE draft_prospects
        SET status = 'Archived',
            player_id = ?,
            selected_pick_id = NULL,
            selected_team_id = NULL,
            updated_at = datetime('now')
        WHERE prospect_id = ?
        """,
        (player_id, int(prospect["prospect_id"])),
    )

    try:
        import roster_actions

        roster_actions.upsert_basic_free_agent_profile(con, player_id, ensure_ratings=False)
    except Exception as exc:  # pragma: no cover - optional market table can be absent in early DBs.
        profile_status = f"free agent profile skipped: {exc}"
    else:
        profile_status = "free agent profile ready"

    return {
        "prospect_id": int(prospect["prospect_id"]),
        "player_id": player_id,
        "player_name": f"{prospect['first_name']} {prospect['last_name']}",
        "position": prospect["position"],
        "college": prospect["college"],
        "overall": legacy_values(prospect)["overall"],
        "potential": legacy_values(prospect)["potential"],
        "true_rank": prospect["true_rank"] if "true_rank" in prospect.keys() else None,
        "converted": True,
        "ratings": rating_rows,
        "role_assignments": role_assignment_rows,
        "role_scores": role_score_rows,
        "personalities": personality_rows,
        "development_modifiers": foundation["development_modifiers"],
        "development_profiles": foundation["development_profiles"],
        "scheme_fits": foundation["scheme_fits"],
        "profile_status": profile_status,
    }


def convert_undrafted_available_prospects(
    con: sqlite3.Connection,
    draft_year: int,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    ensure_all_schema(con)
    sql = """
        SELECT dp.*
        FROM draft_prospects dp
        JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        WHERE dc.draft_year = ?
          AND dp.status = 'Available'
          AND dp.selected_pick_id IS NULL
          AND dp.player_id IS NULL
        ORDER BY
          CASE WHEN dp.true_rank IS NULL THEN 9999 ELSE dp.true_rank END,
          CASE WHEN dp.public_board_rank IS NULL THEN 9999 ELSE dp.public_board_rank END,
          dp.prospect_id
    """
    params: list[Any] = [draft_year]
    if limit is not None:
        sql += "\nLIMIT ?"
        params.append(int(limit))
    prospects = con.execute(sql, params).fetchall()
    return [
        convert_undrafted_prospect_to_free_agent(con, prospect=prospect, draft_year=draft_year)
        for prospect in prospects
    ]


def copy_roles(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> tuple[int, int]:
    assignments = con.execute(
        """
        SELECT role_key, priority, notes
        FROM draft_prospect_role_assignments
        WHERE prospect_id = ?
        ORDER BY priority
        """,
        (prospect_id,),
    ).fetchall()
    scores = con.execute(
        """
        SELECT role_key, scheme_key, role_score
        FROM draft_prospect_role_scores
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchall()
    con.executemany(
        """
        INSERT INTO player_role_assignments (
            player_id, season, role_key, priority, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season, role_key) DO UPDATE SET
            priority = excluded.priority,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        [
            (
                player_id,
                season,
                row["role_key"],
                int(row["priority"]),
                SOURCE,
                row["notes"] or "Copied from generated draft prospect role assignment.",
            )
            for row in assignments
        ],
    )
    con.executemany(
        """
        INSERT INTO player_role_scores (
            player_id, season, role_key, scheme_key, role_score, source
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season, role_key, scheme_key) DO UPDATE SET
            role_score = excluded.role_score,
            source = excluded.source,
            calculated_at = datetime('now')
        """,
        [
            (
                player_id,
                season,
                row["role_key"],
                row["scheme_key"] or "default",
                float(row["role_score"]),
                SOURCE,
            )
            for row in scores
        ],
    )
    return len(assignments), len(scores)


def insert_primary_flex(con: sqlite3.Connection, prospect: sqlite3.Row, player_id: int) -> None:
    true_grade = int(prospect["true_grade"] or prospect["overall"] or 50)
    ceiling = int(prospect["ceiling_grade"] or prospect["potential"] or true_grade)
    experience = max(5, min(10, round(true_grade / 10) + 1))
    potential = max(experience, max(6, min(10, round(ceiling / 10) + 1)))
    con.execute(
        """
        INSERT INTO player_position_flex (
            player_id, position, experience, potential, is_primary, source, notes
        )
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(player_id, position) DO UPDATE SET
            experience = excluded.experience,
            potential = excluded.potential,
            is_primary = 1,
            source = excluded.source,
            notes = excluded.notes
        """,
        (
            player_id,
            prospect["position"],
            experience,
            potential,
            SOURCE,
            f"Primary draft position. Archetype: {prospect['archetype'] or 'Unknown'}.",
        ),
    )


def insert_career_shell(con: sqlite3.Connection, player_id: int, team_abbr: str) -> None:
    table = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'player_career_stats'
        """
    ).fetchone()
    if not table:
        return
    con.execute(
        """
        INSERT OR IGNORE INTO player_career_stats (
            player_id, first_season, last_season, teams_played_for
        )
        VALUES (?, NULL, NULL, ?)
        """,
        (player_id, json.dumps([team_abbr])),
    )


def insert_rookie_contract(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int,
    signed_date: str,
    draft_year: int,
    contract: RookieContractEstimate,
) -> int:
    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year,
            total_value, total_years, aav, signing_bonus,
            roster_bonus, workout_bonus, is_guaranteed, dead_cap_current,
            dead_cap_next, no_trade_clause, option_year, option_exercised,
            franchise_tag, contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, 0, ?, 0, NULL, 'RookieScale', 1)
        """,
        (
            player_id,
            team_id,
            signed_date,
            draft_year,
            draft_year + contract.years - 1,
            contract.total_value,
            contract.years,
            contract.aav,
            contract.signing_bonus,
            contract.guaranteed,
            contract.signing_bonus,
            contract.option_year,
        ),
    )
    return int(cur.lastrowid)


def copy_draft_personalities(
    con: sqlite3.Connection,
    *,
    prospect_id: int,
    player_id: int,
    season: int,
) -> int:
    game_id = active_game_id(con)
    if not game_id:
        return 0
    rows = con.execute(
        """
        SELECT trait_key, intensity, assignment_type, hidden, notes
        FROM draft_prospect_personalities
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchall()
    if not rows:
        return 0
    con.executemany(
        """
        INSERT INTO player_personalities (
            game_id, season, player_id, trait_key, intensity,
            assignment_type, hidden, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, season, player_id, trait_key) DO UPDATE SET
            intensity = excluded.intensity,
            assignment_type = excluded.assignment_type,
            hidden = excluded.hidden,
            source = excluded.source,
            notes = excluded.notes
        """,
        [
            (
                game_id,
                season,
                player_id,
                row["trait_key"],
                int(row["intensity"]),
                f"draft_{row['assignment_type'] or 'generated'}",
                int(row["hidden"] if row["hidden"] is not None else 1),
                SOURCE,
                row["notes"] or "Copied from hidden draft prospect personality.",
            )
            for row in rows
        ],
    )
    return len(rows)


def mark_pick_and_prospect(
    con: sqlite3.Connection,
    *,
    pick: sqlite3.Row,
    prospect: sqlite3.Row,
    player_id: int,
    team_id: int,
    contract: RookieContractEstimate,
    overall_pick_override: int | None,
) -> None:
    pick_number = overall_pick_override if overall_pick_override is not None else pick["pick_number"]
    pick_in_round = pick["pick_in_round"]
    if pick_number is not None:
        pick_in_round = ((int(pick_number) - 1) % 32) + 1
    con.execute(
        """
        UPDATE draft_picks
        SET is_used = 1,
            selected_player_id = ?,
            pick_number = COALESCE(?, pick_number),
            pick_in_round = COALESCE(?, pick_in_round)
        WHERE pick_id = ?
        """,
        (
            player_id,
            pick_number,
            pick_in_round,
            pick["pick_id"],
        ),
    )
    con.execute(
        """
        UPDATE draft_prospects
        SET status = 'Drafted',
            player_id = ?,
            selected_pick_id = ?,
            selected_team_id = ?,
            updated_at = datetime('now')
        WHERE prospect_id = ?
        """,
        (player_id, pick["pick_id"], team_id, prospect["prospect_id"]),
    )


def log_selection(
    con: sqlite3.Connection,
    *,
    pick: sqlite3.Row,
    prospect: sqlite3.Row,
    player_id: int,
    contract_id: int,
    team_id: int,
    draft_year: int,
    signed_date: str,
    contract: RookieContractEstimate,
) -> int:
    player_name = f"{prospect['first_name']} {prospect['last_name']}"
    description = (
        f"{pick['current_team']} selected {player_name} ({prospect['position']}, {prospect['college']}) "
        f"in round {pick['round']} with pick {contract.effective_pick_number}. "
        f"Rookie contract: {contract.years} years, {money(contract.total_value)} total, "
        f"{money(contract.signing_bonus)} signing bonus. {contract.estimate_note}"
    )
    transaction_id, _inserted = insert_transaction(
        con,
        transaction_date=signed_date,
        season=draft_year,
        phase=current_phase(con),
        transaction_type="Draft Selection",
        team_id=team_id,
        player_id=player_id,
        contract_id=contract_id,
        to_team_id=team_id,
        old_status="Draft Prospect",
        new_status="Active",
        cap_delta_current=first_year_cap_hit(con, contract_id, draft_year),
        cash_delta=first_year_cash_due(con, contract_id, draft_year),
        description=description,
        source=SOURCE,
        external_ref=f"draft_pick:{pick['pick_id']}:prospect:{prospect['prospect_id']}",
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id, pick_id,
            to_team_id, amount, season, asset_description
        )
        VALUES (?, 'DraftPickUsed', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            player_id,
            contract_id,
            pick["pick_id"],
            team_id,
            first_year_cap_hit(con, contract_id, draft_year),
            draft_year,
            description,
        ),
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id,
            to_team_id, amount, season, asset_description
        )
        VALUES (?, 'PlayerContract', ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            player_id,
            contract_id,
            team_id,
            first_year_cap_hit(con, contract_id, draft_year),
            draft_year,
            f"{player_name} rookie contract created by draft selection.",
        ),
    )
    return transaction_id


def first_year_cap_hit(con: sqlite3.Connection, contract_id: int, draft_year: int) -> int:
    row = con.execute(
        "SELECT cap_hit FROM contract_years WHERE contract_id = ? AND season = ?",
        (contract_id, draft_year),
    ).fetchone()
    return int(row["cap_hit"] or 0) if row else 0


def first_year_cash_due(con: sqlite3.Connection, contract_id: int, draft_year: int) -> int:
    row = con.execute(
        "SELECT cash_due FROM contract_years WHERE contract_id = ? AND season = ?",
        (contract_id, draft_year),
    ).fetchone()
    return int(row["cash_due"] or 0) if row else 0


def maybe_snapshot(con: sqlite3.Connection, transaction_id: int, enabled: bool, draft_year: int) -> str:
    if not enabled:
        return "skipped by --no-cap-snapshot"
    season = current_season(con)
    if season != draft_year:
        return f"skipped because current game season is {season}, not draft year {draft_year}"
    snapshot_cap_ledger(
        con,
        label=f"after_transaction_{transaction_id}_draft_selection",
        phase=current_phase(con),
        source=SOURCE,
        replace=True,
    )
    return f"written for season {season}"


def select_prospect(con: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "schema_ready", False):
        ensure_all_schema(con)
    pick = pick_by_args(con, args)
    prospect = prospect_by_args(con, args)
    team_id = int(pick["current_team_id"])
    team_abbr = str(pick["current_team"])
    contract = estimate_rookie_contract(pick, overall_pick=args.overall_pick)
    signed_date = args.signed_date or current_game_date(con, args.draft_year)

    player_id = insert_player(
        con,
        prospect=prospect,
        team_id=team_id,
        draft_year=args.draft_year,
        pick=pick,
        rookie_contract=contract,
    )
    rating_rows = copy_ratings(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    role_assignment_rows, role_score_rows = copy_roles(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    insert_primary_flex(con, prospect, player_id)
    insert_career_shell(con, player_id, team_abbr)
    contract_id = insert_rookie_contract(
        con,
        player_id=player_id,
        team_id=team_id,
        signed_date=signed_date,
        draft_year=args.draft_year,
        contract=contract,
    )
    rebuild_contract_year(con, contract_id)
    sync_team_cap_space(con)
    personality_rows = copy_draft_personalities(
        con,
        prospect_id=int(prospect["prospect_id"]),
        player_id=player_id,
        season=args.draft_year,
    )
    foundation = initialize_new_player_foundation(con, player_id=player_id, season=args.draft_year)
    mark_pick_and_prospect(
        con,
        pick=pick,
        prospect=prospect,
        player_id=player_id,
        team_id=team_id,
        contract=contract,
        overall_pick_override=args.overall_pick,
    )
    mark_depth_chart_stale(con, team_abbr)
    transaction_id = log_selection(
        con,
        pick=pick,
        prospect=prospect,
        player_id=player_id,
        contract_id=contract_id,
        team_id=team_id,
        draft_year=args.draft_year,
        signed_date=signed_date,
        contract=contract,
    )
    cap_snapshot_status = maybe_snapshot(con, transaction_id, not args.no_cap_snapshot, args.draft_year)
    return {
        "player_id": player_id,
        "contract_id": contract_id,
        "transaction_id": transaction_id,
        "team": team_abbr,
        "pick_id": int(pick["pick_id"]),
        "round": int(pick["round"]),
        "effective_pick_number": contract.effective_pick_number,
        "prospect_id": int(prospect["prospect_id"]),
        "player_name": f"{prospect['first_name']} {prospect['last_name']}",
        "position": prospect["position"],
        "college": prospect["college"],
        "ratings": rating_rows,
        "role_assignments": role_assignment_rows,
        "role_scores": role_score_rows,
        "personalities": personality_rows,
        "development_modifiers": foundation["development_modifiers"],
        "development_profiles": foundation["development_profiles"],
        "scheme_fits": foundation["scheme_fits"],
        "contract_total": contract.total_value,
        "contract_aav": contract.aav,
        "contract_bonus": contract.signing_bonus,
        "contract_note": contract.estimate_note,
        "cap_snapshot_status": cap_snapshot_status,
    }


def print_selection(result: dict[str, Any], *, dry_run: bool) -> None:
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(
        f"{result['team']} selected {result['player_name']} "
        f"({result['position']}, {result['college']})"
    )
    print(
        f"Pick: round {result['round']}, effective overall {result['effective_pick_number']} "
        f"(pick_id {result['pick_id']}, prospect_id {result['prospect_id']}, player_id {result['player_id']})"
    )
    print(
        f"Rookie contract: 4 years, {money(result['contract_total'])} total, "
        f"{money(result['contract_aav'])} AAV, {money(result['contract_bonus'])} signing bonus"
    )
    print(f"Contract note: {result['contract_note']}")
    print(
        f"Copied: {result['ratings']} ratings, {result['role_assignments']} role assignments, "
        f"{result['role_scores']} role scores, {result['personalities']} hidden personality traits, "
        f"{result.get('development_modifiers', 0)} development modifiers, "
        f"{result.get('scheme_fits', 0)} scheme fits"
    )
    print(f"Contract id: {result['contract_id']}; transaction id: {result['transaction_id']}")
    print(f"Cap ledger snapshot: {result['cap_snapshot_status']}")
    if dry_run:
        print("Dry run only. Rolled back changes. Add --apply to commit.")


def action_select(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        con.execute("BEGIN")
        try:
            result = select_prospect(con, args)
            if args.apply:
                con.commit()
            else:
                con.rollback()
            print_selection(result, dry_run=not args.apply)
        except Exception:
            con.rollback()
            raise


def action_board(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_draft_schema(con)
        params: list[Any] = [args.draft_year]
        filters = ["draft_year = ?"]
        if args.position:
            filters.append("position = ?")
            params.append(args.position.upper())
        if args.available_only:
            filters.append("status = 'Available'")
        rows = con.execute(
            f"""
            SELECT *
            FROM draft_board_view
            WHERE {' AND '.join(filters)}
            ORDER BY
                CASE WHEN scouting_rank IS NULL THEN 1 ELSE 0 END,
                scouting_rank,
                prospect_id
            LIMIT ?
            """,
            (*params, args.limit),
        ).fetchall()
    for row in rows:
        print(
            f"{row['scouting_rank'] or '-':>3} "
            f"{row['prospect_id']:>4} "
            f"{row['first_name']} {row['last_name']:<18} "
            f"{row['position']:<4} {row['college'] or '-':<20} "
            f"scout {row['scout_grade'] or '-':>2}/{row['scout_ceiling'] or '-':>2} "
            f"risk {row['scout_risk'] or '-':<6} {row['status']}"
        )


def action_picks(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        params: list[Any] = [args.draft_year]
        filters = ["dp.draft_year = ?"]
        if args.team:
            team = team_by_abbr(con, args.team)
            filters.append("dp.current_team_id = ?")
            params.append(int(team["team_id"]))
        if args.unused_only:
            filters.append("COALESCE(dp.is_used, 0) = 0")
        rows = con.execute(
            f"""
            SELECT
                dp.pick_id, dp.round, dp.pick_number, dp.pick_in_round,
                dp.is_used, original.abbreviation AS original_team,
                current.abbreviation AS current_team, dp.trade_note,
                p.first_name || ' ' || p.last_name AS selected_player
            FROM draft_picks dp
            LEFT JOIN teams original ON original.team_id = dp.original_team_id
            LEFT JOIN teams current ON current.team_id = dp.current_team_id
            LEFT JOIN players p ON p.player_id = dp.selected_player_id
            WHERE {' AND '.join(filters)}
            ORDER BY dp.round,
                CASE WHEN dp.pick_in_round IS NULL THEN 1 ELSE 0 END,
                dp.pick_in_round,
                dp.pick_id
            LIMIT ?
            """,
            (*params, args.limit),
        ).fetchall()
    for row in rows:
        used = "USED" if row["is_used"] else "OPEN"
        pick_text = row["pick_number"] if row["pick_number"] is not None else "-"
        in_round = row["pick_in_round"] if row["pick_in_round"] is not None else "-"
        print(
            f"{row['pick_id']:>4} R{row['round']} #{pick_text:<3} in-rd {in_round:<2} "
            f"{row['current_team'] or '-':<3} from {row['original_team'] or '-':<3} {used:<4} "
            f"{row['selected_player'] or ''}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    board = subparsers.add_parser("board", help="Show the public draft board.")
    board.add_argument("--draft-year", type=int, required=True)
    board.add_argument("--position")
    board.add_argument("--limit", type=int, default=40)
    board.add_argument("--available-only", action="store_true")
    board.set_defaults(func=action_board)

    picks = subparsers.add_parser("picks", help="Show draft picks.")
    picks.add_argument("--draft-year", type=int, required=True)
    picks.add_argument("--team")
    picks.add_argument("--unused-only", action="store_true")
    picks.add_argument("--limit", type=int, default=80)
    picks.set_defaults(func=action_picks)

    select = subparsers.add_parser("select", help="Use a pick on a prospect.")
    select.add_argument("--draft-year", type=int, required=True)
    pick_target = select.add_mutually_exclusive_group(required=False)
    pick_target.add_argument("--pick-id", type=int)
    pick_target.add_argument("--team")
    select.add_argument("--round", type=int)
    select.add_argument("--pick-in-round", type=int)
    prospect_target = select.add_mutually_exclusive_group(required=True)
    prospect_target.add_argument("--prospect-id", type=int)
    prospect_target.add_argument("--prospect")
    prospect_target.add_argument("--board-rank", type=int)
    select.add_argument("--overall-pick", type=int, help="Override/set overall pick number when draft order is known.")
    select.add_argument("--signed-date")
    select.add_argument("--no-cap-snapshot", action="store_true")
    select.add_argument("--apply", action="store_true", help="Commit the selection. Omit for dry run.")
    select.set_defaults(func=action_select)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
