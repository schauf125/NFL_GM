#!/usr/bin/env python3
"""Roster action commands for the NFL GM Sim database.

Supported actions:
- sign-fa
- release
- change-status
- trade-player
- cap
- find-player

Mutating commands support --dry-run. A dry run performs the action inside a
database transaction, prints the projected result, then rolls it back.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from setup_contract_years import ensure_schema as ensure_contract_schema
from setup_contract_years import rebuild_contract_years, sync_team_cap_space
from setup_transactions_cap_ledger import ensure_schema as ensure_transaction_schema
from setup_transactions_cap_ledger import insert_transaction, snapshot_cap_ledger
import roster_rules


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DATABASE_DIR = ROOT / "database"
if str(DATABASE_DIR) not in sys.path:
    sys.path.insert(0, str(DATABASE_DIR))

from migrate_legacy_sim_ratings import ensure_player_normalized_ratings, ensure_sim_rating_schema  # noqa: E402

SOURCE = "roster_actions"
PHASE = "Preseason"


def normalize_name(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.lower()))


def parse_money(value: str | int | float) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace("$", "").replace(",", "").upper()
    multiplier = 1
    if text.endswith("M"):
        multiplier = 1_000_000
        text = text[:-1]
    elif text.endswith("K"):
        multiplier = 1_000
        text = text[:-1]
    return int(float(text) * multiplier)


def format_money(amount: int | None) -> str:
    if amount is None:
        return "-"
    if amount < 0:
        return "-" + format_money(abs(amount))
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        """
        SELECT setting_value
        FROM game_settings
        WHERE setting_key IN ('current_contract_year', 'current_season')
        ORDER BY CASE setting_key WHEN 'current_contract_year' THEN 0 ELSE 1 END
        LIMIT 1
        """
    ).fetchone()
    return int(row[0]) if row else 2026


def today(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return con.execute("SELECT date('now')").fetchone()[0]


def ensure_all_schema(con: sqlite3.Connection) -> None:
    ensure_contract_schema(con)
    ensure_transaction_schema(con)
    ensure_sim_rating_schema(con)


def get_team(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return row


def find_player(
    con: sqlite3.Connection,
    name: str,
    *,
    team_id: int | None = None,
    require_free_agent: bool = False,
    require_rostered: bool = False,
) -> sqlite3.Row:
    key = normalize_name(name)
    rows = [
        row
        for row in con.execute(
            """
            SELECT p.*, t.abbreviation AS team
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            """
        ).fetchall()
        if normalize_name(f"{row['first_name']} {row['last_name']}") == key
    ]
    if team_id is not None:
        rows = [row for row in rows if row["team_id"] == team_id]
    if require_free_agent:
        rows = [
            row
            for row in rows
            if row["team_id"] is None and row["status"] == "Free Agent"
        ]
    if require_rostered:
        rows = [row for row in rows if row["team_id"] is not None]
    if not rows:
        raise ValueError(f"Player not found for criteria: {name}")
    if len(rows) > 1:
        summary = ", ".join(
            f"{row['player_id']}:{row['first_name']} {row['last_name']} ({row['team'] or row['status']})"
            for row in rows[:10]
        )
        raise ValueError(f"Multiple players matched {name}: {summary}")
    return rows[0]


def active_contract(con: sqlite3.Connection, player_id: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM contracts
        WHERE player_id = ? AND is_active = 1
        ORDER BY contract_id DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()


def current_contract_year(
    con: sqlite3.Connection, contract_id: int, season: int
) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM contract_years
        WHERE contract_id = ? AND season = ?
        """,
        (contract_id, season),
    ).fetchone()


def cap_row(con: sqlite3.Connection, team_id: int) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM team_cap_view WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No cap row for team_id={team_id}")
    return row


def print_cap_row(row: sqlite3.Row) -> None:
    print(
        f"{row['abbreviation']} {row['season']} {row['cap_accounting_mode']}: "
        f"committed {format_money(row['total_committed'])}, "
        f"space {format_money(row['cap_space'])}, "
        f"counted {row['contracts_counted']}, excluded {row['contracts_excluded']}"
    )


def rebuild_financials(con: sqlite3.Connection) -> None:
    rebuild_contract_years(con)
    sync_team_cap_space(con)


def sync_cap_only(con: sqlite3.Connection) -> None:
    sync_team_cap_space(con)


def ensure_cap_ok(con: sqlite3.Connection, team_id: int, allow_over_cap: bool) -> None:
    row = cap_row(con, team_id)
    if row["cap_space"] < 0 and not allow_over_cap:
        raise ValueError(
            f"{row['abbreviation']} would be over the Top 51 cap by {format_money(abs(row['cap_space']))}. "
            "Use --allow-over-cap if you want to permit it."
        )


def make_snapshot(con: sqlite3.Connection, label: str) -> None:
    snapshot_cap_ledger(
        con,
        label=label,
        phase=PHASE,
        source=SOURCE,
        replace=True,
    )


def player_market_score(con: sqlite3.Connection, player_id: int, season: int) -> int:
    row = con.execute(
        """
        SELECT MAX(role_score) AS score
        FROM player_role_scores
        WHERE player_id = ?
          AND season = ?
          AND scheme_key = 'default'
        """,
        (player_id, season),
    ).fetchone()
    if row and row["score"] is not None:
        return int(round(row["score"]))

    row = con.execute(
        """
        SELECT MAX(role_score) AS score
        FROM player_role_scores
        WHERE player_id = ?
          AND scheme_key = 'default'
          AND season = (
              SELECT MAX(season)
              FROM player_role_scores
              WHERE player_id = ?
                AND scheme_key = 'default'
                AND season <= ?
          )
        """,
        (player_id, player_id, season),
    ).fetchone()
    if row and row["score"] is not None:
        return int(round(row["score"]))

    row = con.execute(
        """
        SELECT ROUND(AVG(rating_value)) AS score
        FROM player_ratings
        WHERE player_id = ?
          AND season = ?
        """,
        (player_id, season),
    ).fetchone()
    if row and row["score"] is not None:
        return int(row["score"])

    row = con.execute(
        """
        SELECT ROUND(AVG(rating_value)) AS score
        FROM player_ratings
        WHERE player_id = ?
          AND season = (
              SELECT MAX(season)
              FROM player_ratings
              WHERE player_id = ?
                AND season <= ?
          )
        """,
        (player_id, player_id, season),
    ).fetchone()
    if row and row["score"] is not None:
        return int(row["score"])

    row = con.execute("SELECT overall FROM players WHERE player_id = ?", (player_id,)).fetchone()
    return int(row["overall"] or 60) if row else 60


def upsert_basic_free_agent_profile(
    con: sqlite3.Connection,
    player_id: int,
    *,
    ensure_ratings: bool = True,
) -> None:
    season = current_season(con)
    if ensure_ratings:
        ensure_player_normalized_ratings(
            con,
            player_id,
            source="released_player_market",
            schema_ready=True,
        )
    player = con.execute(
        "SELECT * FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if not player:
        return

    table_exists = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='free_agent_profiles'"
    ).fetchone()[0]
    if not table_exists:
        return

    overall = player_market_score(con, player_id, season)
    position = player["position"]
    group = {
        "QB": "QB",
        "RB": "RB",
        "FB": "RB",
        "WR": "WR",
        "TE": "TE",
        "OT": "OT",
        "OG": "IOL",
        "C": "IOL",
        "EDGE": "EDGE",
        "IDL": "IDL",
        "DT": "IDL",
        "NT": "IDL",
        "ILB": "LB",
        "OLB": "LB",
        "LB": "LB",
        "CB": "CB",
        "NB": "CB",
        "FS": "S",
        "SS": "S",
        "S": "S",
        "K": "ST",
        "P": "ST",
        "LS": "ST",
    }.get(position, position)

    if overall >= 78:
        tier = "Premium"
    elif overall >= 72:
        tier = "Starter"
    elif overall >= 66:
        tier = "Rotation"
    elif overall >= 60:
        tier = "Depth"
    else:
        tier = "Camp"

    base = {
        "QB": 5_000_000,
        "RB": 2_000_000,
        "WR": 3_500_000,
        "TE": 3_000_000,
        "OT": 5_000_000,
        "IOL": 3_500_000,
        "EDGE": 5_000_000,
        "IDL": 4_000_000,
        "LB": 3_000_000,
        "CB": 4_000_000,
        "S": 3_000_000,
        "ST": 1_500_000,
    }.get(group, 2_000_000)
    asking = max(915_000, int(round(base * max(0.6, 1 + ((overall - 64) * 0.1)) / 100_000) * 100_000))
    minimum = max(840_000, int(round(asking * 0.6 / 100_000) * 100_000))

    con.execute(
        """
        INSERT INTO free_agent_profiles (
            player_id, position_group, previous_team, market_tier,
            asking_aav, minimum_aav, preferred_years, guarantee_pct,
            contract_priority, contender_priority, role_priority,
            hometown_priority, patience, preferred_teams, hometown_teams,
            motivation, signing_notes, source, source_url, updated_at
        )
        VALUES (?, ?, NULL, ?, ?, ?, 1, 10, 10, 10, 12, 5, 8, NULL, NULL,
                'released_player_market', 'Generated after release by roster_actions.',
                ?, NULL, datetime('now'))
        ON CONFLICT(player_id) DO UPDATE SET
            position_group = excluded.position_group,
            market_tier = excluded.market_tier,
            asking_aav = excluded.asking_aav,
            minimum_aav = excluded.minimum_aav,
            motivation = excluded.motivation,
            signing_notes = excluded.signing_notes,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (player_id, group, tier, asking, minimum, SOURCE),
    )


def action_sign_fa(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    team = get_team(con, args.team)
    player = find_player(con, args.player, require_free_agent=True)
    season = current_season(con)
    before = cap_row(con, team["team_id"])
    aav = parse_money(args.aav)
    bonus = parse_money(args.bonus or 0)
    years = int(args.years)

    profile = con.execute(
        "SELECT minimum_aav, asking_aav FROM free_agent_profiles WHERE player_id = ?",
        (player["player_id"],),
    ).fetchone()
    if profile and aav < int(profile["minimum_aav"] or 0) and not args.force_market:
        raise ValueError(
            f"{player['first_name']} {player['last_name']} has a minimum AAV of "
            f"{format_money(profile['minimum_aav'])}. Use --force-market to override."
        )

    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year, total_value,
            total_years, aav, signing_bonus, roster_bonus, workout_bonus,
            is_guaranteed, dead_cap_current, dead_cap_next, contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 0, ?, 1)
        """,
        (
            player["player_id"],
            team["team_id"],
            today(con),
            season,
            season + years - 1,
            aav * years,
            years,
            aav,
            bonus,
            1 if args.guaranteed else 0,
            args.contract_type,
        ),
    )
    contract_id = int(cur.lastrowid)
    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (team["team_id"], player["player_id"]),
    )
    ensure_player_normalized_ratings(
        con,
        player["player_id"],
        source="free_agent_signing",
        schema_ready=True,
    )
    rebuild_financials(con)
    after = cap_row(con, team["team_id"])
    ensure_cap_ok(con, team["team_id"], args.allow_over_cap)
    cy = current_contract_year(con, contract_id, season)
    cap_delta = int(after["total_committed"] or 0) - int(before["total_committed"] or 0)

    transaction_id, _ = insert_transaction(
        con,
        transaction_date=today(con),
        season=season,
        phase=PHASE,
        transaction_type="Signing",
        team_id=team["team_id"],
        player_id=player["player_id"],
        contract_id=contract_id,
        to_team_id=team["team_id"],
        old_status="Free Agent",
        new_status="Active",
        cap_delta_current=cap_delta,
        cap_delta_next=0,
        cash_delta=int(cy["cash_due"] if cy else aav + bonus),
        description=(
            f"{team['abbreviation']} signed {player['first_name']} {player['last_name']} "
            f"for {years} year(s), {format_money(aav)} AAV, {format_money(bonus)} signing bonus."
        ),
        source=SOURCE,
        external_ref=f"sign:{player['player_id']}:{contract_id}",
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id, to_team_id,
            amount, season, asset_description
        )
        VALUES (?, 'PlayerContract', ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            player["player_id"],
            contract_id,
            team["team_id"],
            int(cy["cap_hit"] if cy else aav),
            season,
            "Free-agent signing contract.",
        ),
    )
    make_snapshot(con, f"after_transaction_{transaction_id}_signing")
    print(
        f"Signed {player['first_name']} {player['last_name']} to {team['abbreviation']}. "
        f"Top 51 delta: {format_money(cap_delta)}. New space: {format_money(after['cap_space'])}."
    )


def action_release(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    team = get_team(con, args.team) if args.team else None
    player = find_player(
        con,
        args.player,
        team_id=team["team_id"] if team else None,
        require_rostered=True,
    )
    from_team_id = player["team_id"]
    before = cap_row(con, from_team_id)
    season = current_season(con)
    if roster_rules.waiver_required_for_player(con, player, season=season, waiver_date=today(con)):
        roster_rules.ensure_schema(con)
        waiver_id = roster_rules.place_player_on_waivers(
            con,
            player=player,
            season=season,
            waiver_date=today(con),
            reason="Released and subject to waivers.",
            source=SOURCE,
        )
        rebuild_financials(con)
        after = cap_row(con, from_team_id)
        print(
            f"Waived {player['first_name']} {player['last_name']} "
            f"(entry {waiver_id}). Current space: {format_money(after['cap_space'])}."
        )
        return
    contract = active_contract(con, player["player_id"])
    dead_cap = 0
    contract_id = None
    if contract:
        contract_id = int(contract["contract_id"])
        cy = current_contract_year(con, contract_id, season)
        if cy:
            dead_cap = int(
                cy["dead_cap_if_cut_post_june1_current"]
                if args.post_june1
                else cy["dead_cap_if_cut_pre_june1"]
            )
        con.execute(
            "UPDATE contracts SET is_active = 0 WHERE contract_id = ?",
            (contract_id,),
        )
    if dead_cap:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                from_team_id,
                season,
                f"Dead cap from releasing {player['first_name']} {player['last_name']}.",
                dead_cap,
                player["player_id"],
                SOURCE,
            ),
        )

    con.execute(
        "UPDATE players SET team_id = NULL, status = 'Free Agent' WHERE player_id = ?",
        (player["player_id"],),
    )
    con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player["player_id"],))
    ensure_player_normalized_ratings(
        con,
        player["player_id"],
        source="released_player_market",
        schema_ready=True,
    )
    upsert_basic_free_agent_profile(con, player["player_id"])
    rebuild_financials(con)
    after = cap_row(con, from_team_id)
    cap_delta = int(after["total_committed"] or 0) - int(before["total_committed"] or 0)

    transaction_id, _ = insert_transaction(
        con,
        transaction_date=today(con),
        season=season,
        phase=PHASE,
        transaction_type="Release",
        team_id=from_team_id,
        player_id=player["player_id"],
        contract_id=contract_id,
        from_team_id=from_team_id,
        old_status=player["status"],
        new_status="Free Agent",
        cap_delta_current=cap_delta,
        description=(
            f"Released {player['first_name']} {player['last_name']}. "
            f"Dead cap: {format_money(dead_cap)}."
        ),
        source=SOURCE,
        external_ref=f"release:{player['player_id']}:{contract_id or 'no_contract'}:{today(con)}",
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id, from_team_id,
            amount, season, asset_description
        )
        VALUES (?, 'ReleasedPlayer', ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            player["player_id"],
            contract_id,
            from_team_id,
            dead_cap,
            season,
            "Player released to free agency.",
        ),
    )
    make_snapshot(con, f"after_transaction_{transaction_id}_release")
    print(
        f"Released {player['first_name']} {player['last_name']}. "
        f"Top 51 delta: {format_money(cap_delta)}. New space: {format_money(after['cap_space'])}."
    )


def action_change_status(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    player = find_player(con, args.player)
    season = current_season(con)
    status_exists = con.execute(
        "SELECT COUNT(*) FROM roster_status_types WHERE status_code = ?",
        (args.status,),
    ).fetchone()[0]
    if not status_exists:
        raise ValueError(f"Unknown status: {args.status}")

    before = cap_row(con, player["team_id"]) if player["team_id"] else None
    old_status = player["status"]
    con.execute(
        "UPDATE players SET status = ? WHERE player_id = ?",
        (args.status, player["player_id"]),
    )
    con.execute(
        """
        INSERT INTO player_roster_status_history (
            player_id, old_status, new_status, effective_date, season, reason
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (player["player_id"], old_status, args.status, today(con), season, args.reason),
    )
    sync_cap_only(con)
    after = cap_row(con, player["team_id"]) if player["team_id"] else None
    cap_delta = 0
    if before and after:
        cap_delta = int(after["total_committed"] or 0) - int(before["total_committed"] or 0)

    transaction_id, _ = insert_transaction(
        con,
        transaction_date=today(con),
        season=season,
        phase=PHASE,
        transaction_type="Roster Status Change",
        team_id=player["team_id"],
        player_id=player["player_id"],
        old_status=old_status,
        new_status=args.status,
        cap_delta_current=cap_delta,
        description=args.reason or f"Status changed from {old_status} to {args.status}.",
        source=SOURCE,
        external_ref=f"status:{player['player_id']}:{old_status}:{args.status}:{today(con)}",
    )
    make_snapshot(con, f"after_transaction_{transaction_id}_status")
    print(
        f"Changed {player['first_name']} {player['last_name']} from {old_status} to {args.status}. "
        f"Cap delta: {format_money(cap_delta)}."
    )


def remaining_bonus_proration(con: sqlite3.Connection, contract_id: int, season: int) -> int:
    return int(
        con.execute(
            """
            SELECT COALESCE(SUM(signing_bonus_proration + option_bonus_proration), 0)
            FROM contract_years
            WHERE contract_id = ? AND season >= ?
            """,
            (contract_id, season),
        ).fetchone()[0]
        or 0
    )


def action_trade_player(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    to_team = get_team(con, args.to)
    from_team = get_team(con, args.from_team) if args.from_team else None
    player = find_player(
        con,
        args.player,
        team_id=from_team["team_id"] if from_team else None,
        require_rostered=True,
    )
    if player["team_id"] == to_team["team_id"]:
        raise ValueError("Player is already on that team.")
    from_team_id = player["team_id"]
    from_team_abbr = con.execute(
        "SELECT abbreviation FROM teams WHERE team_id = ?",
        (from_team_id,),
    ).fetchone()[0]
    season = current_season(con)
    before_from = cap_row(con, from_team_id)
    before_to = cap_row(con, to_team["team_id"])
    contract = active_contract(con, player["player_id"])
    contract_id = int(contract["contract_id"]) if contract else None
    old_team_dead_cap = 0

    if contract_id:
        old_team_dead_cap = remaining_bonus_proration(con, contract_id, season)
        if old_team_dead_cap:
            con.execute(
                """
                INSERT INTO team_cap_charges (
                    team_id, season, charge_type, description, amount, player_id, source
                )
                VALUES (?, ?, 'Trade Dead Cap', ?, ?, ?, ?)
                """,
                (
                    from_team_id,
                    season,
                    f"Remaining bonus proration from trading {player['first_name']} {player['last_name']} to {to_team['abbreviation']}.",
                    old_team_dead_cap,
                    player["player_id"],
                    SOURCE,
                ),
            )
        con.execute(
            "UPDATE contracts SET team_id = ? WHERE contract_id = ?",
            (to_team["team_id"], contract_id),
        )
        con.execute(
            """
            UPDATE contract_years
            SET team_id = ?,
                cap_hit = base_salary + roster_bonus + workout_bonus + other_bonus,
                signing_bonus_proration = 0,
                option_bonus_proration = 0,
                dead_cap_if_cut_pre_june1 = guaranteed_salary,
                dead_cap_if_cut_post_june1_current = guaranteed_salary,
                dead_cap_if_cut_post_june1_next = 0,
                source = 'trade_adjusted_remaining_salary',
                notes = 'Trade adjusted by roster_actions; old team keeps remaining bonus as cap charge.',
                updated_at = datetime('now')
            WHERE contract_id = ? AND season >= ?
            """,
            (to_team["team_id"], contract_id, season),
        )

    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (to_team["team_id"], player["player_id"]),
    )
    con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player["player_id"],))
    sync_cap_only(con)
    after_from = cap_row(con, from_team_id)
    after_to = cap_row(con, to_team["team_id"])
    if after_to["cap_space"] < 0 and not args.allow_over_cap:
        raise ValueError(
            f"{to_team['abbreviation']} would be over the Top 51 cap by "
            f"{format_money(abs(after_to['cap_space']))}. Use --allow-over-cap to permit it."
        )

    cap_delta_from = int(after_from["total_committed"] or 0) - int(before_from["total_committed"] or 0)
    cap_delta_to = int(after_to["total_committed"] or 0) - int(before_to["total_committed"] or 0)
    transaction_id, _ = insert_transaction(
        con,
        transaction_date=today(con),
        season=season,
        phase=PHASE,
        transaction_type="Trade",
        team_id=to_team["team_id"],
        secondary_team_id=from_team_id,
        player_id=player["player_id"],
        contract_id=contract_id,
        from_team_id=from_team_id,
        to_team_id=to_team["team_id"],
        cap_delta_current=cap_delta_to,
        description=(
            f"{to_team['abbreviation']} acquired {player['first_name']} {player['last_name']} "
            f"from {from_team_abbr}. {from_team_abbr} dead cap: {format_money(old_team_dead_cap)}."
        ),
        source=SOURCE,
        external_ref=f"trade_player:{player['player_id']}:{from_team_id}:{to_team['team_id']}:{today(con)}",
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id, from_team_id,
            to_team_id, amount, season, asset_description
        )
        VALUES (?, 'TradedPlayer', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            player["player_id"],
            contract_id,
            from_team_id,
            to_team["team_id"],
            cap_delta_to,
            season,
            "Player traded; draft-pick asset support can be attached later.",
        ),
    )
    make_snapshot(con, f"after_transaction_{transaction_id}_trade")
    print(
        f"Traded {player['first_name']} {player['last_name']} from {from_team_abbr} to {to_team['abbreviation']}. "
        f"{from_team_abbr} cap delta {format_money(cap_delta_from)}, "
        f"{to_team['abbreviation']} cap delta {format_money(cap_delta_to)}."
    )


def action_cap(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    if args.team:
        print_cap_row(cap_row(con, get_team(con, args.team)["team_id"]))
        return
    for row in con.execute("SELECT * FROM team_cap_view ORDER BY cap_space ASC"):
        print_cap_row(row)


def action_find_player(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    key = normalize_name(args.player)
    season = current_season(con)
    rows = [
        row
        for row in con.execute(
            """
            WITH best_role AS (
                SELECT player_id, role_name, role_score
                FROM (
                    SELECT
                        prs.player_id,
                        rsd.display_name AS role_name,
                        prs.role_score,
                        ROW_NUMBER() OVER (
                            PARTITION BY prs.player_id
                            ORDER BY prs.role_score DESC, prs.role_key
                        ) AS rn
                    FROM player_role_scores prs
                    LEFT JOIN role_score_definitions rsd
                        ON rsd.role_key = prs.role_key
                    WHERE prs.season = ?
                      AND prs.scheme_key = 'default'
                )
                WHERE rn = 1
            ),
            rating_pivot AS (
                SELECT
                    player_id,
                    MAX(CASE WHEN rating_key = 'kick_power' THEN rating_value END) AS kick_power,
                    MAX(CASE WHEN rating_key = 'kick_accuracy' THEN rating_value END) AS kick_accuracy,
                    MAX(CASE WHEN rating_key = 'composure' THEN rating_value END) AS composure,
                    AVG(CASE WHEN rating_key IN (
                        'play_recognition', 'processing_speed', 'discipline',
                        'composure', 'consistency', 'speed', 'acceleration',
                        'agility', 'strength', 'stamina'
                    ) THEN rating_value END) AS general_rating
                FROM player_ratings
                WHERE season = ?
                GROUP BY player_id
            )
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.status,
                t.abbreviation AS team,
                p.potential,
                COALESCE(
                    ROUND(br.role_score),
                    CASE
                        WHEN p.position = 'K'
                            THEN ROUND(((COALESCE(rp.kick_power, 50) * 9.0) + (COALESCE(rp.kick_accuracy, 50) * 12.0) + (COALESCE(rp.composure, 50) * 4.0)) / 25.0)
                        WHEN p.position = 'P'
                            THEN ROUND(((COALESCE(rp.kick_power, 50) * 12.0) + (COALESCE(rp.kick_accuracy, 50) * 8.0) + (COALESCE(rp.composure, 50) * 3.0)) / 23.0)
                        ELSE ROUND(COALESCE(rp.general_rating, 50))
                    END
                ) AS sim_rating,
                COALESCE(
                    br.role_name,
                    CASE
                        WHEN p.position IN ('K', 'P') THEN 'Specialist'
                        WHEN p.position = 'LS' THEN 'Long Snapper'
                        ELSE 'General'
                    END
                ) AS sim_role
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            LEFT JOIN best_role br ON br.player_id = p.player_id
            LEFT JOIN rating_pivot rp ON rp.player_id = p.player_id
            ORDER BY player_name
            """,
            (season, season),
        )
        if key in normalize_name(row["player_name"])
    ]
    for row in rows:
        print(
            f"{row['player_id']}: {row['player_name']} {row['position']} "
            f"{row['team'] or row['status']} SIM {int(row['sim_rating'] or 50)} "
            f"POT {row['potential']} ROLE {row['sim_role']}"
        )


def run_mutation(con: sqlite3.Connection, func, args: argparse.Namespace) -> None:
    try:
        con.execute("BEGIN")
        func(con, args)
        if args.dry_run:
            con.rollback()
            print("DRY RUN: rolled back.")
        else:
            con.commit()
    except Exception:
        con.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Roster and transaction actions.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cap_parser = subparsers.add_parser("cap", help="Show Top 51 cap summary.")
    cap_parser.add_argument("--team", help="Team abbreviation. Omit for all teams.")

    find_parser = subparsers.add_parser("find-player", help="Find players by name substring.")
    find_parser.add_argument("player")

    sign_parser = subparsers.add_parser("sign-fa", help="Sign a free agent.")
    sign_parser.add_argument("--player", required=True)
    sign_parser.add_argument("--team", required=True)
    sign_parser.add_argument("--years", required=True, type=int)
    sign_parser.add_argument("--aav", required=True)
    sign_parser.add_argument("--bonus", default="0")
    sign_parser.add_argument("--contract-type", default="Standard")
    sign_parser.add_argument("--guaranteed", action="store_true")
    sign_parser.add_argument("--force-market", action="store_true")
    sign_parser.add_argument("--allow-over-cap", action="store_true")
    sign_parser.add_argument("--dry-run", action="store_true")

    release_parser = subparsers.add_parser("release", help="Release a rostered player.")
    release_parser.add_argument("--player", required=True)
    release_parser.add_argument("--team", help="Optional team abbreviation to disambiguate.")
    release_parser.add_argument("--post-june1", action="store_true")
    release_parser.add_argument("--dry-run", action="store_true")

    status_parser = subparsers.add_parser("change-status", help="Change a player's roster status.")
    status_parser.add_argument("--player", required=True)
    status_parser.add_argument("--status", required=True)
    status_parser.add_argument("--reason")
    status_parser.add_argument("--dry-run", action="store_true")

    trade_parser = subparsers.add_parser("trade-player", help="Trade a player to another team.")
    trade_parser.add_argument("--player", required=True)
    trade_parser.add_argument("--to", required=True)
    trade_parser.add_argument("--from-team", dest="from_team")
    trade_parser.add_argument("--allow-over-cap", action="store_true")
    trade_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        ensure_all_schema(con)
        if args.command == "cap":
            action_cap(con, args)
        elif args.command == "find-player":
            action_find_player(con, args)
        elif args.command == "sign-fa":
            run_mutation(con, action_sign_fa, args)
        elif args.command == "release":
            run_mutation(con, action_release, args)
        elif args.command == "change-status":
            run_mutation(con, action_change_status, args)
        elif args.command == "trade-player":
            run_mutation(con, action_trade_player, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
