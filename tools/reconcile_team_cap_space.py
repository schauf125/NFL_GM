#!/usr/bin/env python3
"""Reconcile displayed team cap space to an external 2026 cap-space snapshot.

The project still has many contracts represented as simple AAV-derived cap
hits. That is good enough for roster building, but it can make team-level cap
space wildly wrong. This tool preserves the player contract rows and adds one
team-level cap adjustment per club so the cap view reflects a real external
team-cap target.

Kyler Murray note: the 2026 OTC data already treats Murray as a $1.3M Vikings
cap charge, with Arizona carrying a large dead-money/retained-cost bucket. This
tool keeps that model intact.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from setup_contract_years import ensure_schema as ensure_contract_schema
from setup_contract_years import sync_team_cap_space


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SEASON = 2026
SOURCE = "overthecap_2026_cap_space_2026_04_30"
SOURCE_NAME = "Over The Cap 2026 Salary Cap Space"
SOURCE_URL = "https://overthecap.com/salary-cap-space"
SOURCE_BASE_SALARY_CAP = 301_200_000


@dataclass(frozen=True)
class TeamCapSnapshot:
    abbreviation: str
    team_name: str
    cap_space: int
    effective_cap_space: int
    active_players: int
    active_cap_spending: int
    dead_money: int


OTC_2026_CAP_SNAPSHOT = [
    TeamCapSnapshot("TEN", "Tennessee Titans", 63_118_380, 51_246_264, 86, 254_435_832, 25_184_566),
    TeamCapSnapshot("WAS", "Washington Commanders", 49_506_244, 42_762_089, 81, 257_452_446, 20_692_736),
    TeamCapSnapshot("LAC", "Los Angeles Chargers", 45_814_740, 41_215_657, 75, 253_552_368, 5_548_177),
    TeamCapSnapshot("SF", "San Francisco 49ers", 44_907_747, 41_509_028, 84, 259_001_503, 36_281_074),
    TeamCapSnapshot("ARI", "Arizona Cardinals", 40_835_523, 29_311_130, 84, 212_972_289, 73_320_820),
    TeamCapSnapshot("NYJ", "New York Jets", 39_551_357, 23_100_943, 81, 172_086_805, 111_246_438),
    TeamCapSnapshot("NE", "New England Patriots", 35_708_626, 31_718_475, 81, 277_801_963, 38_489_465),
    TeamCapSnapshot("SEA", "Seattle Seahawks", 32_792_183, 29_391_005, 83, 280_798_156, 483_723),
    TeamCapSnapshot("BAL", "Baltimore Ravens", 27_674_010, 21_583_366, 70, 267_874_217, 18_198_715),
    TeamCapSnapshot("IND", "Indianapolis Colts", 26_627_200, 24_680_075, 87, 266_565_586, 9_593_644),
    TeamCapSnapshot("LAR", "Los Angeles Rams", 25_944_238, 21_094_084, 69, 278_563_864, 8_835_821),
    TeamCapSnapshot("PIT", "Pittsburgh Steelers", 25_582_705, 19_927_107, 83, 282_976_329, 12_221_838),
    TeamCapSnapshot("GB", "Green Bay Packers", 24_614_250, 22_831_227, 80, 246_035_542, 43_274_939),
    TeamCapSnapshot("LV", "Las Vegas Raiders", 22_722_186, 9_813_094, 76, 239_488_393, 52_012_266),
    TeamCapSnapshot("DET", "Detroit Lions", 22_582_868, 17_578_856, 76, 273_001_240, 26_468_791),
    TeamCapSnapshot("CLE", "Cleveland Browns", 21_284_197, 10_305_513, 78, 225_287_867, 91_602_938),
    TeamCapSnapshot("ATL", "Atlanta Falcons", 19_117_655, 17_348_275, 77, 241_655_333, 43_860_329),
    TeamCapSnapshot("DEN", "Denver Broncos", 18_782_088, 17_475_871, 80, 281_075_024, 3_385_588),
    TeamCapSnapshot("NYG", "New York Giants", 18_124_565, 3_224_888, 84, 255_157_012, 26_555_399),
    TeamCapSnapshot("MIN", "Minnesota Vikings", 16_071_234, 10_737_466, 68, 252_833_456, 45_046_407),
    TeamCapSnapshot("PHI", "Philadelphia Eagles", 15_350_899, 10_940_037, 83, 248_675_391, 51_617_968),
    TeamCapSnapshot("NO", "New Orleans Saints", 13_897_399, 6_644_320, 80, 190_787_439, 112_108_154),
    TeamCapSnapshot("DAL", "Dallas Cowboys", 13_106_278, 4_595_847, 76, 276_429_736, 41_550_057),
    TeamCapSnapshot("HOU", "Houston Texans", 12_934_353, 7_326_066, 77, 230_464_097, 66_366_709),
    TeamCapSnapshot("TB", "Tampa Bay Buccaneers", 12_815_460, 7_193_958, 76, 290_392_123, 13_329_591),
    TeamCapSnapshot("BUF", "Buffalo Bills", 11_866_991, 8_562_659, 78, 242_570_671, 46_164_050),
    TeamCapSnapshot("JAX", "Jacksonville Jaguars", 11_078_713, 8_676_759, 72, 243_963_003, 54_692_874),
    TeamCapSnapshot("CHI", "Chicago Bears", 10_794_549, 6_140_231, 75, 278_245_466, 19_895_493),
    TeamCapSnapshot("CIN", "Cincinnati Bengals", 7_497_125, 5_105_699, 78, 299_065_843, 10_416_745),
    TeamCapSnapshot("KC", "Kansas City Chiefs", 5_947_066, -5_254_475, 73, 285_410_506, 9_773_977),
    TeamCapSnapshot("CAR", "Carolina Panthers", 1_902_996, -2_994_470, 82, 287_542_297, 22_140_178),
    TeamCapSnapshot("MIA", "Miami Dolphins", 1_755_394, -8_078_773, 86, 126_939_592, 179_204_257),
]


def format_money(value: int) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.0f}K"
    return f"{sign}${value}"


def ensure_schema(con: sqlite3.Connection) -> None:
    ensure_contract_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS external_team_cap_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT,
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            abbreviation TEXT NOT NULL,
            team_name TEXT NOT NULL,
            source_base_salary_cap INTEGER NOT NULL,
            source_cap_space INTEGER NOT NULL,
            source_effective_cap_space INTEGER NOT NULL,
            source_active_players INTEGER NOT NULL,
            source_active_cap_spending INTEGER NOT NULL,
            source_dead_money INTEGER NOT NULL,
            game_salary_cap INTEGER NOT NULL,
            game_top51_player_cap INTEGER NOT NULL,
            game_existing_other_charges INTEGER NOT NULL,
            reconciliation_amount INTEGER NOT NULL,
            notes TEXT,
            imported_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, season, team_id)
        );

        CREATE INDEX IF NOT EXISTS idx_external_team_cap_snapshots_team
            ON external_team_cap_snapshots(team_id, season, source);
        """
    )


def get_team(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation,),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found for abbreviation: {abbreviation}")
    return row


def existing_other_charges(con: sqlite3.Connection, team_id: int, season: int) -> int:
    row = con.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS amount
        FROM team_cap_charges
        WHERE team_id = ?
          AND season = ?
          AND COALESCE(source, '') <> ?
        """,
        (team_id, season, SOURCE),
    ).fetchone()
    return int(row["amount"] or 0)


def top51_player_cap(con: sqlite3.Connection, team_id: int, season: int) -> int:
    row = con.execute(
        """
        SELECT COALESCE(top51_cap_hit, 0) AS top51_cap_hit
        FROM team_top51_cap_view
        WHERE team_id = ? AND season = ?
        """,
        (team_id, season),
    ).fetchone()
    return int(row["top51_cap_hit"] if row else 0)


def upsert_snapshot(
    con: sqlite3.Connection,
    team: sqlite3.Row,
    row: TeamCapSnapshot,
    *,
    game_top51_cap: int,
    game_other_charges: int,
    reconciliation_amount: int,
) -> None:
    notes = (
        "Team-level reconciliation from OTC cap-space snapshot. This preserves "
        "existing player contracts while correcting displayed team cap space. "
        "Arizona's row includes the external dead-money/retained-cost bucket "
        "that keeps Kyler Murray at a $1.3M Vikings cap charge."
        if row.abbreviation == "ARI"
        else "Team-level reconciliation from OTC cap-space snapshot. This preserves existing player contracts while correcting displayed team cap space."
    )
    con.execute(
        """
        INSERT INTO external_team_cap_snapshots (
            source, source_name, source_url, season, team_id, abbreviation,
            team_name, source_base_salary_cap, source_cap_space,
            source_effective_cap_space, source_active_players,
            source_active_cap_spending, source_dead_money, game_salary_cap,
            game_top51_player_cap, game_existing_other_charges,
            reconciliation_amount, notes, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(source, season, team_id) DO UPDATE SET
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            abbreviation = excluded.abbreviation,
            team_name = excluded.team_name,
            source_base_salary_cap = excluded.source_base_salary_cap,
            source_cap_space = excluded.source_cap_space,
            source_effective_cap_space = excluded.source_effective_cap_space,
            source_active_players = excluded.source_active_players,
            source_active_cap_spending = excluded.source_active_cap_spending,
            source_dead_money = excluded.source_dead_money,
            game_salary_cap = excluded.game_salary_cap,
            game_top51_player_cap = excluded.game_top51_player_cap,
            game_existing_other_charges = excluded.game_existing_other_charges,
            reconciliation_amount = excluded.reconciliation_amount,
            notes = excluded.notes,
            imported_at = datetime('now')
        """,
        (
            SOURCE,
            SOURCE_NAME,
            SOURCE_URL,
            SEASON,
            int(team["team_id"]),
            row.abbreviation,
            row.team_name,
            SOURCE_BASE_SALARY_CAP,
            row.cap_space,
            row.effective_cap_space,
            row.active_players,
            row.active_cap_spending,
            row.dead_money,
            int(team["salary_cap"] or 0),
            game_top51_cap,
            game_other_charges,
            reconciliation_amount,
            notes,
        ),
    )


def apply_reconciliation(con: sqlite3.Connection, *, dry_run: bool = False) -> list[dict[str, int | str]]:
    ensure_schema(con)
    con.execute(
        "DELETE FROM team_cap_charges WHERE source = ? AND season = ?",
        (SOURCE, SEASON),
    )

    results: list[dict[str, int | str]] = []
    for row in OTC_2026_CAP_SNAPSHOT:
        team = get_team(con, row.abbreviation)
        game_cap = int(team["salary_cap"] or 0)
        player_top51 = top51_player_cap(con, int(team["team_id"]), SEASON)
        other_charges = existing_other_charges(con, int(team["team_id"]), SEASON)
        target_total_committed = game_cap - row.cap_space
        reconciliation_amount = target_total_committed - player_top51 - other_charges

        upsert_snapshot(
            con,
            team,
            row,
            game_top51_cap=player_top51,
            game_other_charges=other_charges,
            reconciliation_amount=reconciliation_amount,
        )
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(team["team_id"]),
                SEASON,
                "External Cap Reconciliation",
                (
                    f"Normalize 2026 displayed cap space to {SOURCE_NAME}. "
                    f"OTC cap space: {format_money(row.cap_space)}; "
                    f"OTC active spending/dead money: {format_money(row.active_cap_spending)} / {format_money(row.dead_money)}."
                ),
                reconciliation_amount,
                SOURCE,
            ),
        )
        results.append(
            {
                "team": row.abbreviation,
                "source_cap_space": row.cap_space,
                "game_top51_cap": player_top51,
                "reconciliation_amount": reconciliation_amount,
                "target_total_committed": target_total_committed,
            }
        )

    sync_team_cap_space(con)
    if dry_run:
        con.rollback()
    else:
        con.commit()
    return results


def print_results(results: list[dict[str, int | str]]) -> None:
    print("Team  OTC Space     Player Top51   Reconcile     Target Committed")
    print("----  ------------  ------------  ------------  ----------------")
    for row in sorted(results, key=lambda item: str(item["team"])):
        print(
            f"{row['team']:<4}  "
            f"{format_money(int(row['source_cap_space'])):>12}  "
            f"{format_money(int(row['game_top51_cap'])):>12}  "
            f"{format_money(int(row['reconciliation_amount'])):>12}  "
            f"{format_money(int(row['target_total_committed'])):>16}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile team cap space to OTC 2026 targets.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--dry-run", action="store_true", help="Calculate and roll back instead of saving.")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        results = apply_reconciliation(con, dry_run=args.dry_run)
    finally:
        con.close()

    print_results(results)
    print(f"Reconciled teams: {len(results)}")
    if args.dry_run:
        print("DRY RUN: rolled back.")
    else:
        print(f"Saved source: {SOURCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
