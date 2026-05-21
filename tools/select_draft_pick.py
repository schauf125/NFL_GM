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
from engine.qb_behavior import ensure_player_qb_behavior_schema
from engine.rb_behavior import ensure_player_rb_behavior_schema
from engine.receiver_behavior import ensure_player_receiver_behavior_schema
from engine.ol_behavior import ensure_player_ol_behavior_schema
from engine.edge_behavior import ensure_player_edge_behavior_schema
from engine.idl_behavior import ensure_player_idl_behavior_schema
from engine.lb_behavior import ensure_player_lb_behavior_schema
from engine.secondary_behavior import ensure_player_secondary_behavior_schema
from engine.specialist_behavior import ensure_player_specialist_behavior_schema
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
import player_headshot_backfill
import draft_portrait_assets


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
    ensure_player_qb_behavior_schema(con)
    ensure_player_rb_behavior_schema(con)
    ensure_player_receiver_behavior_schema(con)
    ensure_player_ol_behavior_schema(con)
    ensure_player_edge_behavior_schema(con)
    ensure_player_idl_behavior_schema(con)
    ensure_player_lb_behavior_schema(con)
    ensure_player_secondary_behavior_schema(con)
    ensure_player_specialist_behavior_schema(con)
    draft_portrait_assets.ensure_schema(con)
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


def copy_qb_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_qb_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_qb_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_qb_behavior_profiles (
            player_id, season, label, rhythm, pocket_discipline, pocket_drift,
            checkdown_willingness, deep_aggression, pressure_escape,
            broken_play_creation, scramble_trigger, sack_risk,
            throwaway_discipline, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            rhythm = excluded.rhythm,
            pocket_discipline = excluded.pocket_discipline,
            pocket_drift = excluded.pocket_drift,
            checkdown_willingness = excluded.checkdown_willingness,
            deep_aggression = excluded.deep_aggression,
            pressure_escape = excluded.pressure_escape,
            broken_play_creation = excluded.broken_play_creation,
            scramble_trigger = excluded.scramble_trigger,
            sack_risk = excluded.sack_risk,
            throwaway_discipline = excluded.throwaway_discipline,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["rhythm"]),
            int(row["pocket_discipline"]),
            int(row["pocket_drift"]),
            int(row["checkdown_willingness"]),
            int(row["deep_aggression"]),
            int(row["pressure_escape"]),
            int(row["broken_play_creation"]),
            int(row["scramble_trigger"]),
            int(row["sack_risk"]),
            int(row["throwaway_discipline"]),
            row["notes"],
        ),
    )
    return 1


def copy_rb_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_rb_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_rb_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_rb_behavior_profiles (
            player_id, season, label, early_down_gravity, patience,
            one_cut_decisiveness, bounce_tendency, home_run_hunting,
            contact_appetite, space_creation, pass_game_usage,
            short_yardage_trust, ball_security_mindset, source, notes,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            early_down_gravity = excluded.early_down_gravity,
            patience = excluded.patience,
            one_cut_decisiveness = excluded.one_cut_decisiveness,
            bounce_tendency = excluded.bounce_tendency,
            home_run_hunting = excluded.home_run_hunting,
            contact_appetite = excluded.contact_appetite,
            space_creation = excluded.space_creation,
            pass_game_usage = excluded.pass_game_usage,
            short_yardage_trust = excluded.short_yardage_trust,
            ball_security_mindset = excluded.ball_security_mindset,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["early_down_gravity"]),
            int(row["patience"]),
            int(row["one_cut_decisiveness"]),
            int(row["bounce_tendency"]),
            int(row["home_run_hunting"]),
            int(row["contact_appetite"]),
            int(row["space_creation"]),
            int(row["pass_game_usage"]),
            int(row["short_yardage_trust"]),
            int(row["ball_security_mindset"]),
            row["notes"],
        ),
    )
    return 1


def copy_receiver_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_receiver_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_receiver_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_receiver_behavior_profiles (
            player_id, season, label, target_gravity, release_urgency,
            route_pacing, vertical_intent, middle_comfort, contested_alpha,
            sideline_awareness, yac_intent, scramble_drill, catch_security,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            target_gravity = excluded.target_gravity,
            release_urgency = excluded.release_urgency,
            route_pacing = excluded.route_pacing,
            vertical_intent = excluded.vertical_intent,
            middle_comfort = excluded.middle_comfort,
            contested_alpha = excluded.contested_alpha,
            sideline_awareness = excluded.sideline_awareness,
            yac_intent = excluded.yac_intent,
            scramble_drill = excluded.scramble_drill,
            catch_security = excluded.catch_security,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["target_gravity"]),
            int(row["release_urgency"]),
            int(row["route_pacing"]),
            int(row["vertical_intent"]),
            int(row["middle_comfort"]),
            int(row["contested_alpha"]),
            int(row["sideline_awareness"]),
            int(row["yac_intent"]),
            int(row["scramble_drill"]),
            int(row["catch_security"]),
            row["notes"],
        ),
    )
    return 1


def copy_ol_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_ol_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_ol_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_ol_behavior_profiles (
            player_id, season, label, pass_set_patience, mirror_vs_speed,
            anchor_vs_power, hand_timing, stunt_awareness, drive_finish,
            reach_range, combo_timing, second_level_climb, penalty_control,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            pass_set_patience = excluded.pass_set_patience,
            mirror_vs_speed = excluded.mirror_vs_speed,
            anchor_vs_power = excluded.anchor_vs_power,
            hand_timing = excluded.hand_timing,
            stunt_awareness = excluded.stunt_awareness,
            drive_finish = excluded.drive_finish,
            reach_range = excluded.reach_range,
            combo_timing = excluded.combo_timing,
            second_level_climb = excluded.second_level_climb,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["pass_set_patience"]),
            int(row["mirror_vs_speed"]),
            int(row["anchor_vs_power"]),
            int(row["hand_timing"]),
            int(row["stunt_awareness"]),
            int(row["drive_finish"]),
            int(row["reach_range"]),
            int(row["combo_timing"]),
            int(row["second_level_climb"]),
            int(row["penalty_control"]),
            row["notes"],
        ),
    )
    return 1


def copy_edge_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_edge_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_edge_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_edge_behavior_profiles (
            player_id, season, label, getoff_timing, speed_arc, power_collapse,
            counter_plan, stunt_timing, contain_discipline, run_squeeze,
            backside_pursuit, finish_skill, rush_discipline, source, notes,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            getoff_timing = excluded.getoff_timing,
            speed_arc = excluded.speed_arc,
            power_collapse = excluded.power_collapse,
            counter_plan = excluded.counter_plan,
            stunt_timing = excluded.stunt_timing,
            contain_discipline = excluded.contain_discipline,
            run_squeeze = excluded.run_squeeze,
            backside_pursuit = excluded.backside_pursuit,
            finish_skill = excluded.finish_skill,
            rush_discipline = excluded.rush_discipline,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["getoff_timing"]),
            int(row["speed_arc"]),
            int(row["power_collapse"]),
            int(row["counter_plan"]),
            int(row["stunt_timing"]),
            int(row["contain_discipline"]),
            int(row["run_squeeze"]),
            int(row["backside_pursuit"]),
            int(row["finish_skill"]),
            int(row["rush_discipline"]),
            row["notes"],
        ),
    )
    return 1


def copy_idl_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_idl_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_idl_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_idl_behavior_profiles (
            player_id, season, label, getoff_timing, penetration_burst,
            power_collapse, double_team_anchor, gap_control,
            block_shed_timing, stunt_timing, rush_counter_plan,
            finish_skill, rush_discipline, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            getoff_timing = excluded.getoff_timing,
            penetration_burst = excluded.penetration_burst,
            power_collapse = excluded.power_collapse,
            double_team_anchor = excluded.double_team_anchor,
            gap_control = excluded.gap_control,
            block_shed_timing = excluded.block_shed_timing,
            stunt_timing = excluded.stunt_timing,
            rush_counter_plan = excluded.rush_counter_plan,
            finish_skill = excluded.finish_skill,
            rush_discipline = excluded.rush_discipline,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["getoff_timing"]),
            int(row["penetration_burst"]),
            int(row["power_collapse"]),
            int(row["double_team_anchor"]),
            int(row["gap_control"]),
            int(row["block_shed_timing"]),
            int(row["stunt_timing"]),
            int(row["rush_counter_plan"]),
            int(row["finish_skill"]),
            int(row["rush_discipline"]),
            row["notes"],
        ),
    )
    return 1


def copy_lb_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_lb_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_lb_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_lb_behavior_profiles (
            player_id, season, label, trigger_quickness, gap_fit_discipline,
            scrape_range, traffic_navigation, zone_landmark_depth,
            man_match_carry, blitz_timing, tackle_finish, rally_support,
            penalty_control, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            trigger_quickness = excluded.trigger_quickness,
            gap_fit_discipline = excluded.gap_fit_discipline,
            scrape_range = excluded.scrape_range,
            traffic_navigation = excluded.traffic_navigation,
            zone_landmark_depth = excluded.zone_landmark_depth,
            man_match_carry = excluded.man_match_carry,
            blitz_timing = excluded.blitz_timing,
            tackle_finish = excluded.tackle_finish,
            rally_support = excluded.rally_support,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["trigger_quickness"]),
            int(row["gap_fit_discipline"]),
            int(row["scrape_range"]),
            int(row["traffic_navigation"]),
            int(row["zone_landmark_depth"]),
            int(row["man_match_carry"]),
            int(row["blitz_timing"]),
            int(row["tackle_finish"]),
            int(row["rally_support"]),
            int(row["penalty_control"]),
            row["notes"],
        ),
    )
    return 1


def copy_secondary_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_secondary_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_secondary_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_secondary_behavior_profiles (
            player_id, season, label, press_timing, man_mirror,
            zone_eye_discipline, break_trigger, deep_range,
            ball_play_timing, catch_point_compete, slot_traffic,
            run_support_fit, tackle_finish, penalty_control,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            press_timing = excluded.press_timing,
            man_mirror = excluded.man_mirror,
            zone_eye_discipline = excluded.zone_eye_discipline,
            break_trigger = excluded.break_trigger,
            deep_range = excluded.deep_range,
            ball_play_timing = excluded.ball_play_timing,
            catch_point_compete = excluded.catch_point_compete,
            slot_traffic = excluded.slot_traffic,
            run_support_fit = excluded.run_support_fit,
            tackle_finish = excluded.tackle_finish,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["press_timing"]),
            int(row["man_mirror"]),
            int(row["zone_eye_discipline"]),
            int(row["break_trigger"]),
            int(row["deep_range"]),
            int(row["ball_play_timing"]),
            int(row["catch_point_compete"]),
            int(row["slot_traffic"]),
            int(row["run_support_fit"]),
            int(row["tackle_finish"]),
            int(row["penalty_control"]),
            row["notes"],
        ),
    )
    return 1


def copy_specialist_behavior_profile(con: sqlite3.Connection, prospect_id: int, player_id: int, season: int) -> int:
    ensure_player_specialist_behavior_schema(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_prospect_specialist_behavior_profiles
        WHERE prospect_id = ?
        """,
        (prospect_id,),
    ).fetchone()
    if not row:
        return 0
    con.execute(
        """
        INSERT INTO player_specialist_behavior_profiles (
            player_id, season, label, kick_operation, kickoff_control,
            punt_hang_time, punt_placement, snap_accuracy,
            lane_release, gunner_speed, return_lane_vision,
            block_timing, coverage_tackle, penalty_control,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_selection', ?, datetime('now'))
        ON CONFLICT(player_id, season) DO UPDATE SET
            label = excluded.label,
            kick_operation = excluded.kick_operation,
            kickoff_control = excluded.kickoff_control,
            punt_hang_time = excluded.punt_hang_time,
            punt_placement = excluded.punt_placement,
            snap_accuracy = excluded.snap_accuracy,
            lane_release = excluded.lane_release,
            gunner_speed = excluded.gunner_speed,
            return_lane_vision = excluded.return_lane_vision,
            block_timing = excluded.block_timing,
            coverage_tackle = excluded.coverage_tackle,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            season,
            row["label"],
            int(row["kick_operation"]),
            int(row["kickoff_control"]),
            int(row["punt_hang_time"]),
            int(row["punt_placement"]),
            int(row["snap_accuracy"]),
            int(row["lane_release"]),
            int(row["gunner_speed"]),
            int(row["return_lane_vision"]),
            int(row["block_timing"]),
            int(row["coverage_tackle"]),
            int(row["penalty_control"]),
            row["notes"],
        ),
    )
    return 1


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
    attached_portrait = draft_portrait_assets.attach_prospect_portrait_to_player(
        con,
        prospect_id=int(prospect["prospect_id"]),
        player_id=player_id,
        team_abbr="FA",
        root=ROOT,
    )
    if not attached_portrait:
        player_headshot_backfill.ensure_fallback_headshot(con, player_id=player_id, root=ROOT)
    rating_rows = copy_ratings(con, int(prospect["prospect_id"]), player_id, draft_year)
    qb_behavior_rows = copy_qb_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    rb_behavior_rows = copy_rb_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    receiver_behavior_rows = copy_receiver_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    ol_behavior_rows = copy_ol_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    edge_behavior_rows = copy_edge_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    idl_behavior_rows = copy_idl_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    lb_behavior_rows = copy_lb_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    secondary_behavior_rows = copy_secondary_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
    specialist_behavior_rows = copy_specialist_behavior_profile(con, int(prospect["prospect_id"]), player_id, draft_year)
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
        "qb_behavior_profiles": qb_behavior_rows,
        "rb_behavior_profiles": rb_behavior_rows,
        "receiver_behavior_profiles": receiver_behavior_rows,
        "ol_behavior_profiles": ol_behavior_rows,
        "edge_behavior_profiles": edge_behavior_rows,
        "idl_behavior_profiles": idl_behavior_rows,
        "lb_behavior_profiles": lb_behavior_rows,
        "secondary_behavior_profiles": secondary_behavior_rows,
        "specialist_behavior_profiles": specialist_behavior_rows,
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
    attached_portrait = draft_portrait_assets.attach_prospect_portrait_to_player(
        con,
        prospect_id=int(prospect["prospect_id"]),
        player_id=player_id,
        team_abbr=team_abbr,
        root=ROOT,
    )
    if not attached_portrait:
        player_headshot_backfill.ensure_fallback_headshot(con, player_id=player_id, root=ROOT)
    rating_rows = copy_ratings(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    qb_behavior_rows = copy_qb_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    rb_behavior_rows = copy_rb_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    receiver_behavior_rows = copy_receiver_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    ol_behavior_rows = copy_ol_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    edge_behavior_rows = copy_edge_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    idl_behavior_rows = copy_idl_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    lb_behavior_rows = copy_lb_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    secondary_behavior_rows = copy_secondary_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
    specialist_behavior_rows = copy_specialist_behavior_profile(con, int(prospect["prospect_id"]), player_id, args.draft_year)
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
        "qb_behavior_profiles": qb_behavior_rows,
        "rb_behavior_profiles": rb_behavior_rows,
        "receiver_behavior_profiles": receiver_behavior_rows,
        "ol_behavior_profiles": ol_behavior_rows,
        "edge_behavior_profiles": edge_behavior_rows,
        "idl_behavior_profiles": idl_behavior_rows,
        "lb_behavior_profiles": lb_behavior_rows,
        "secondary_behavior_profiles": secondary_behavior_rows,
        "specialist_behavior_profiles": specialist_behavior_rows,
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
        f"{result.get('qb_behavior_profiles', 0)} QB behavior profiles, "
        f"{result.get('rb_behavior_profiles', 0)} RB behavior profiles, "
        f"{result.get('receiver_behavior_profiles', 0)} receiver behavior profiles, "
        f"{result.get('ol_behavior_profiles', 0)} OL behavior profiles, "
        f"{result.get('edge_behavior_profiles', 0)} Edge behavior profiles, "
        f"{result.get('idl_behavior_profiles', 0)} IDL behavior profiles, "
        f"{result.get('lb_behavior_profiles', 0)} LB behavior profiles, "
        f"{result.get('secondary_behavior_profiles', 0)} secondary behavior profiles, "
        f"{result.get('specialist_behavior_profiles', 0)} specialist behavior profiles, "
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
