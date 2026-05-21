#!/usr/bin/env python3
"""Prune stale free-agent player rows that never became usable players."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"

SNAP_KEYS = ("offensive_snaps", "defensive_snaps", "special_teams_snaps", "total_snaps")
PRUNABLE_STATUSES = ("Free Agent", "Released", "Waived")


@dataclass(frozen=True)
class PruneCandidate:
    player_id: int
    name: str
    position: str
    age: int | None
    years_exp: int | None
    overall: int
    potential: int
    status: str
    last_contract_year: int | None
    unsigned_years: int | None
    sim_snaps: float
    historical_games: int
    drafted_refs: int


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def current_league_year(con: sqlite3.Connection) -> int:
    if table_exists(con, "game_saves"):
        row = con.execute(
            """
            SELECT current_league_year
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["current_league_year"]:
            return int(row["current_league_year"])
    if table_exists(con, "game_settings"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'current_league_year'"
        ).fetchone()
        if row and row["setting_value"]:
            return int(row["setting_value"])
    row = con.execute(
        """
        SELECT MAX(season) AS season
        FROM season_games
        """
    ).fetchone() if table_exists(con, "season_games") else None
    if row and row["season"]:
        return int(row["season"])
    return 2026


def _sum_expr(table: str, key_col: str, value_col: str, keys: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{key}'" for key in keys)
    return (
        f"SELECT player_id, SUM(CASE WHEN {key_col} IN ({quoted}) "
        f"THEN COALESCE({value_col}, 0) ELSE 0 END) AS snaps "
        f"FROM {table} GROUP BY player_id"
    )


def candidate_rows(
    con: sqlite3.Connection,
    *,
    league_year: int,
    min_unsigned_years: int,
    max_overall: int,
    include_historical: bool,
    include_drafted: bool,
) -> list[PruneCandidate]:
    game_snap_subquery = (
        _sum_expr("game_player_stats", "stat_key", "stat_value", SNAP_KEYS)
        if table_exists(con, "game_player_stats")
        else "SELECT NULL AS player_id, 0 AS snaps WHERE 0"
    )
    season_snap_subquery = (
        _sum_expr("season_player_stats", "stat_key", "stat_value", SNAP_KEYS)
        if table_exists(con, "season_player_stats")
        else "SELECT NULL AS player_id, 0 AS snaps WHERE 0"
    )
    historical_games_subquery = (
        """
        SELECT player_id, SUM(COALESCE(games, 0)) AS games
        FROM player_season_stats
        GROUP BY player_id
        """
        if table_exists(con, "player_season_stats")
        else "SELECT NULL AS player_id, 0 AS games WHERE 0"
    )
    drafted_refs_subquery = (
        """
        SELECT selected_player_id AS player_id, COUNT(*) AS refs
        FROM draft_picks
        WHERE selected_player_id IS NOT NULL
        GROUP BY selected_player_id
        """
        if table_exists(con, "draft_picks")
        else "SELECT NULL AS player_id, 0 AS refs WHERE 0"
    )
    active_contract_clause = (
        """
        NOT EXISTS (
            SELECT 1
            FROM contracts active_contract
            WHERE active_contract.player_id = p.player_id
              AND COALESCE(active_contract.is_active, 0) = 1
        )
        """
        if table_exists(con, "contracts")
        else "1 = 1"
    )
    last_contract_subquery = (
        """
        SELECT player_id, MAX(end_year) AS last_contract_year
        FROM contracts
        GROUP BY player_id
        """
        if table_exists(con, "contracts")
        else "SELECT NULL AS player_id, NULL AS last_contract_year WHERE 0"
    )
    status_placeholders = ", ".join("?" for _ in PRUNABLE_STATUSES)
    params: list[object] = [league_year, *PRUNABLE_STATUSES, max_overall]
    rows = con.execute(
        f"""
        WITH last_contract AS ({last_contract_subquery}),
             game_snaps AS ({game_snap_subquery}),
             season_snaps AS ({season_snap_subquery}),
             historical_games AS ({historical_games_subquery}),
             drafted_refs AS ({drafted_refs_subquery})
        SELECT
            p.player_id,
            TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')) AS name,
            p.position,
            p.age,
            p.years_exp,
            COALESCE(p.overall, 0) AS overall,
            COALESCE(p.potential, 0) AS potential,
            COALESCE(p.status, '') AS status,
            last_contract.last_contract_year,
            CASE
                WHEN last_contract.last_contract_year IS NOT NULL
                    THEN ? - last_contract.last_contract_year
                ELSE NULL
            END AS unsigned_years,
            COALESCE(game_snaps.snaps, 0) + COALESCE(season_snaps.snaps, 0) AS sim_snaps,
            COALESCE(historical_games.games, 0) AS historical_games,
            COALESCE(drafted_refs.refs, 0) AS drafted_refs
        FROM players p
        LEFT JOIN last_contract ON last_contract.player_id = p.player_id
        LEFT JOIN game_snaps ON game_snaps.player_id = p.player_id
        LEFT JOIN season_snaps ON season_snaps.player_id = p.player_id
        LEFT JOIN historical_games ON historical_games.player_id = p.player_id
        LEFT JOIN drafted_refs ON drafted_refs.player_id = p.player_id
        WHERE p.team_id IS NULL
          AND COALESCE(p.status, '') IN ({status_placeholders})
          AND COALESCE(p.overall, 0) < ?
          AND {active_contract_clause}
          AND COALESCE(game_snaps.snaps, 0) + COALESCE(season_snaps.snaps, 0) = 0
          AND (
              (last_contract.last_contract_year IS NOT NULL AND ? - last_contract.last_contract_year >= ?)
              OR (last_contract.last_contract_year IS NULL AND COALESCE(p.years_exp, 0) >= ?)
          )
        ORDER BY p.overall ASC, p.potential ASC, p.age DESC, name
        """,
        [*params, league_year, min_unsigned_years, min_unsigned_years],
    ).fetchall()
    candidates = [
        PruneCandidate(
            player_id=int(row["player_id"]),
            name=str(row["name"] or f"Player {row['player_id']}"),
            position=str(row["position"] or ""),
            age=int(row["age"]) if row["age"] is not None else None,
            years_exp=int(row["years_exp"]) if row["years_exp"] is not None else None,
            overall=int(row["overall"] or 0),
            potential=int(row["potential"] or 0),
            status=str(row["status"] or ""),
            last_contract_year=int(row["last_contract_year"]) if row["last_contract_year"] is not None else None,
            unsigned_years=int(row["unsigned_years"]) if row["unsigned_years"] is not None else None,
            sim_snaps=float(row["sim_snaps"] or 0),
            historical_games=int(row["historical_games"] or 0),
            drafted_refs=int(row["drafted_refs"] or 0),
        )
        for row in rows
    ]
    if not include_historical:
        candidates = [candidate for candidate in candidates if candidate.historical_games <= 0]
    if not include_drafted:
        candidates = [candidate for candidate in candidates if candidate.drafted_refs <= 0]
    return candidates


def prune_candidates(con: sqlite3.Connection, candidates: list[PruneCandidate]) -> int:
    if not candidates:
        return 0
    ids = [candidate.player_id for candidate in candidates]
    placeholders = ", ".join("?" for _ in ids)
    # These tables use SET NULL or NO ACTION where keeping a stale player link
    # is unnecessary for never-used free-agent rows.
    set_null_updates = (
        ("draft_prospects", "player_id"),
        ("draft_room_events", "player_id"),
        ("free_agency_events", "player_id"),
        ("game_injury_events", "opponent_player_id"),
        ("game_sim_plays", "defense_player_id"),
        ("game_sim_plays", "target_player_id"),
        ("game_sim_plays", "offense_player_id"),
        ("league_news_items", "player_id"),
        ("team_cap_charges", "player_id"),
        ("team_cap_ledger_lines", "player_id"),
        ("trade_proposal_assets", "player_id"),
        ("transaction_assets", "player_id"),
        ("transaction_log", "player_id"),
    )
    for table, column in set_null_updates:
        if table_exists(con, table):
            con.execute(f"UPDATE {table} SET {column} = NULL WHERE {column} IN ({placeholders})", ids)
    cur = con.execute(f"DELETE FROM players WHERE player_id IN ({placeholders})", ids)
    return int(cur.rowcount or 0)


def print_candidates(candidates: list[PruneCandidate], *, limit: int) -> None:
    print(f"Prune candidates: {len(candidates)}")
    for candidate in candidates[:limit]:
        unsigned = (
            f"{candidate.unsigned_years}y"
            if candidate.unsigned_years is not None
            else f"no contract, {candidate.years_exp or 0} exp"
        )
        print(
            f"{candidate.player_id:>5}  {candidate.name:<24} {candidate.position:<4} "
            f"OVR {candidate.overall:<2} POT {candidate.potential:<2} "
            f"age {candidate.age or '-':>2} exp {candidate.years_exp or 0:<2} "
            f"unsigned {unsigned:<16} status {candidate.status}"
        )
    if len(candidates) > limit:
        print(f"... {len(candidates) - limit} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune stale unsigned players who never played a snap.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--league-year", type=int)
    parser.add_argument("--min-unsigned-years", type=int, default=2)
    parser.add_argument("--max-overall", type=int, default=60)
    parser.add_argument("--include-historical", action="store_true")
    parser.add_argument("--include-drafted", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    con = connect(args.db)
    try:
        league_year = args.league_year or current_league_year(con)
        candidates = candidate_rows(
            con,
            league_year=league_year,
            min_unsigned_years=args.min_unsigned_years,
            max_overall=args.max_overall,
            include_historical=args.include_historical,
            include_drafted=args.include_drafted,
        )
        print(f"League year: {league_year}")
        print_candidates(candidates, limit=args.limit)
        if not args.apply:
            print("Dry run only. Add --apply to delete these player rows.")
            return 0
        deleted = prune_candidates(con, candidates)
        con.commit()
        print(f"Deleted player rows: {deleted}")
        return 0
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
