#!/usr/bin/env python3
"""Seed 2027-2029 NFL draft-pick inventory.

All 32 teams receive original picks in rounds 1-7 for 2027, 2028, and 2029.
Known traded future picks are then overlaid, with condition notes stored when
the final pick movement depends on draft order or trade conditions.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from setup_transactions_cap_ledger import ensure_schema as ensure_transaction_schema
from setup_transactions_cap_ledger import insert_transaction


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
YEARS = (2027, 2028, 2029)
ROUNDS = range(1, 8)
SOURCE = "future_draft_pick_seed_2026_04_30"


@dataclass(frozen=True)
class PickTrade:
    year: int
    round: int
    from_team: str
    to_team: str
    note: str
    condition: str = ""
    source_url: str = ""


TRADED_PICKS: list[PickTrade] = [
    # 2027 first round
    PickTrade(2027, 1, "GB", "DAL", "Micah Parsons trade.", "Conditional pool: Dallas later sent the earlier of its original 2027 first or the acquired Green Bay 2027 first to the Jets in the Quinnen Williams trade."),
    PickTrade(2027, 1, "IND", "NYJ", "Sauce Gardner trade."),
    PickTrade(2027, 1, "DAL", "NYJ", "Quinnen Williams trade.", "Placeholder assignment: Jets receive the earlier of Dallas original 2027 first or Green Bay-acquired 2027 first. Revisit after draft order is known."),

    # 2027 third/fourth/fifth round
    PickTrade(2027, 3, "LAR", "KC", "Trent McDuffie trade."),
    PickTrade(2027, 3, "PHI", "MIN", "Jonathan Greenard trade."),
    PickTrade(2027, 4, "MIN", "CAR", "Adam Thielen trade."),
    PickTrade(2027, 4, "DAL", "GB", "Rashan Gary trade."),
    PickTrade(2027, 4, "NYG", "CLE", "2026 draft trade."),
    PickTrade(2027, 4, "SEA", "CLE", "2026 draft trade."),
    PickTrade(2027, 5, "HOU", "CLE", "2025 draft trade."),
    PickTrade(2027, 5, "DAL", "PIT", "George Pickens trade."),
    PickTrade(2027, 5, "PIT", "MIA", "Jalen Ramsey trade."),
    PickTrade(2027, 5, "CAR", "MIN", "Adam Thielen trade."),
    PickTrade(2027, 5, "CHI", "NE", "Garrett Bradbury trade."),

    # 2027 sixth round
    PickTrade(2027, 6, "PIT", "DAL", "George Pickens trade."),
    PickTrade(2027, 6, "SF", "KC", "Skyy Moore trade."),
    PickTrade(2027, 6, "NYJ", "MIN", "Harrison Phillips trade."),
    PickTrade(2027, 6, "GB", "PHI", "Darian Kinnard trade."),
    PickTrade(2027, 6, "NO", "NE", "Ja'Lynn Polk trade."),
    PickTrade(2027, 6, "CLE", "HOU", "Cam Robinson trade."),
    PickTrade(2027, 6, "PHI", "NYJ", "John Metchie III / Michael Carter II trade.", "Conditional pick."),
    PickTrade(2027, 6, "LAC", "NO", "Trevor Penning trade."),
    PickTrade(2027, 6, "KC", "NYJ", "Justin Fields trade.", "Conditional pick."),
    PickTrade(2027, 6, "PHI", "GB", "Dontayvion Wicks trade."),
    PickTrade(2027, 6, "BAL", "SF", "2026 draft trade."),
    PickTrade(2027, 6, "MIN", "NE", "2026 draft trade."),

    # 2027 seventh round
    PickTrade(2027, 7, "LAR", "BAL", "Tre'Davious White trade."),
    PickTrade(2027, 7, "BAL", "LAC", "Odafe Oweh trade; pick originally acquired from Rams when applicable."),
    PickTrade(2027, 7, "MIA", "PIT", "Jalen Ramsey / Minkah Fitzpatrick trade."),
    PickTrade(2027, 7, "NO", "DEN", "Devaughn Vele trade."),
    PickTrade(2027, 7, "KC", "SF", "Skyy Moore trade."),
    PickTrade(2027, 7, "MIN", "NYJ", "Harrison Phillips trade."),
    PickTrade(2027, 7, "PHI", "MIN", "Sam Howell trade."),
    PickTrade(2027, 7, "LAC", "HOU", "Austin Deculus trade.", "Conditional pick."),
    PickTrade(2027, 7, "ATL", "SEA", "Michael Jerrell trade.", "Conditional pick."),
    PickTrade(2027, 7, "NYG", "MIA", "Darren Waller trade."),
    PickTrade(2027, 7, "HOU", "CLE", "Cam Robinson trade."),
    PickTrade(2027, 7, "NYJ", "PHI", "John Metchie III / Michael Carter II trade.", "Conditional pick."),
    PickTrade(2027, 7, "BAL", "PHI", "Jaire Alexander trade."),
    PickTrade(2027, 7, "HOU", "DET", "David Montgomery trade."),
    PickTrade(2027, 7, "PHI", "CAR", "Andy Dalton trade."),
    PickTrade(2027, 7, "DAL", "PHI", "2026 draft trade."),
    PickTrade(2027, 7, "LV", "BUF", "2026 draft trade."),

    # 2028 known future picks
    PickTrade(2028, 6, "NO", "DAL", "Asim Richards trade."),
    PickTrade(2028, 7, "DAL", "NO", "Asim Richards trade."),
    PickTrade(2028, 7, "CLE", "LAR", "K.T. Leveston trade."),
    PickTrade(2028, 7, "NE", "NO", "Ja'Lynn Polk trade."),
    PickTrade(2028, 7, "NYJ", "LAC", "Ja'Sir Taylor trade.", "Conditional pick."),
]


def ensure_pick_schema(con: sqlite3.Connection) -> None:
    ensure_transaction_schema(con)
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS draft_pick_conditions (
            condition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pick_id INTEGER NOT NULL REFERENCES draft_picks(pick_id) ON DELETE CASCADE,
            condition_type TEXT NOT NULL,
            condition_text TEXT NOT NULL,
            resolution_status TEXT NOT NULL DEFAULT 'Pending',
            resolution_note TEXT,
            source TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS draft_pick_seed_sources (
            source_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            notes TEXT,
            checked_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_name, source_url)
        );

        INSERT INTO draft_pick_seed_sources (source_name, source_url, notes)
        VALUES
            ('Wikipedia 2027 NFL draft traded picks', 'https://en.wikipedia.org/wiki/2027_NFL_draft', 'Used for post-2026-draft traded-pick inventory and cited trade references.'),
            ('NFL.com 2026 draft trade tracker', 'https://www.nfl.com/news/2026-nfl-draft-trade-tracker-full-details-on-every-draft-related-move', 'Used to cross-check 2026 draft-day future-pick trades.'),
            ('NFLTradeRumors traded draft picks', 'https://nfltraderumors.co/traded-nfl-draft-picks/', 'Used to cross-check 2027-2028 future-pick inventory.'),
            ('Pro Sports Transactions future draft trades', 'https://prosportstransactions.com/football/DraftTrades/Future/', 'Used as a future-pick trade reference.')
        ON CONFLICT(source_name, source_url) DO UPDATE SET
            notes = excluded.notes,
            checked_at = datetime('now');

        DROP VIEW IF EXISTS draft_pick_inventory_view;
        CREATE VIEW draft_pick_inventory_view AS
        SELECT
            dp.pick_id,
            dp.draft_year,
            dp.round,
            dp.pick_number,
            dp.pick_in_round,
            original.abbreviation AS original_team,
            current.abbreviation AS current_team,
            dp.is_traded,
            dp.trade_note,
            dp.is_comp_pick,
            dp.is_used,
            CASE WHEN c.condition_id IS NOT NULL THEN 1 ELSE 0 END AS has_condition,
            c.condition_text,
            c.resolution_status,
            c.resolution_note
        FROM draft_picks dp
        LEFT JOIN teams original ON original.team_id = dp.original_team_id
        LEFT JOIN teams current ON current.team_id = dp.current_team_id
        LEFT JOIN draft_pick_conditions c ON c.pick_id = dp.pick_id;
        """
    )


def team_ids(con: sqlite3.Connection) -> dict[str, int]:
    return {
        row[0]: int(row[1])
        for row in con.execute("SELECT abbreviation, team_id FROM teams")
    }


def reset_future_picks(con: sqlite3.Connection) -> None:
    con.execute(
        """
        DELETE FROM transaction_log
        WHERE source = ?
          AND transaction_type = 'Draft Pick Move'
        """,
        (SOURCE,),
    )
    pick_ids = [
        row[0]
        for row in con.execute(
            "SELECT pick_id FROM draft_picks WHERE draft_year BETWEEN 2027 AND 2029"
        )
    ]
    if pick_ids:
        con.executemany(
            "DELETE FROM draft_pick_conditions WHERE pick_id = ?",
            [(pick_id,) for pick_id in pick_ids],
        )
        con.executemany(
            "DELETE FROM draft_picks WHERE pick_id = ?",
            [(pick_id,) for pick_id in pick_ids],
        )


def seed_base_picks(con: sqlite3.Connection, teams: dict[str, int]) -> int:
    inserted = 0
    for year in YEARS:
        for round_number in ROUNDS:
            for abbreviation in sorted(teams):
                team_id = teams[abbreviation]
                con.execute(
                    """
                    INSERT INTO draft_picks (
                        original_team_id, current_team_id, draft_year, round,
                        pick_number, pick_in_round, is_traded, trade_note,
                        is_comp_pick, is_used
                    )
                    VALUES (?, ?, ?, ?, NULL, NULL, 0, NULL, 0, 0)
                    """,
                    (team_id, team_id, year, round_number),
                )
                inserted += 1
    return inserted


def candidate_pick_rows(
    con: sqlite3.Connection,
    *,
    year: int,
    round_number: int,
    from_team_id: int,
) -> list[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT *
        FROM draft_picks
        WHERE draft_year = ?
          AND round = ?
          AND current_team_id = ?
          AND is_used = 0
        ORDER BY
            CASE WHEN original_team_id = ? THEN 0 ELSE 1 END,
            pick_id
        """,
        (year, round_number, from_team_id, from_team_id),
    ).fetchall()
    con.row_factory = None
    return rows


def apply_trade(
    con: sqlite3.Connection,
    teams: dict[str, int],
    trade: PickTrade,
    *,
    season: int,
    transaction_date: str,
) -> tuple[int | None, str]:
    from_team_id = teams[trade.from_team]
    to_team_id = teams[trade.to_team]
    rows = candidate_pick_rows(
        con,
        year=trade.year,
        round_number=trade.round,
        from_team_id=from_team_id,
    )
    if not rows:
        return None, f"No {trade.year} R{trade.round} pick currently owned by {trade.from_team}"

    pick = rows[0]
    pick_id = int(pick["pick_id"])
    original_team_id = int(pick["original_team_id"])
    original_team_abbr = next(
        abbreviation for abbreviation, team_id in teams.items() if team_id == original_team_id
    )
    existing_note = pick["trade_note"] or ""
    appended_note = (
        f"{trade.from_team} -> {trade.to_team}: {trade.note}"
        + (f" Condition: {trade.condition}" if trade.condition else "")
    )
    trade_note = appended_note if not existing_note else existing_note + " | " + appended_note

    con.execute(
        """
        UPDATE draft_picks
        SET current_team_id = ?,
            is_traded = 1,
            trade_note = ?
        WHERE pick_id = ?
        """,
        (to_team_id, trade_note, pick_id),
    )

    if trade.condition:
        con.execute(
            """
            INSERT INTO draft_pick_conditions (
                pick_id, condition_type, condition_text,
                resolution_status, source, updated_at
            )
            VALUES (?, 'Trade Condition', ?, 'Pending', ?, datetime('now'))
            """,
            (pick_id, trade.condition, SOURCE),
        )

    transaction_id, inserted = insert_transaction(
        con,
        transaction_date=transaction_date,
        season=season,
        phase="Preseason",
        transaction_type="Draft Pick Move",
        team_id=to_team_id,
        secondary_team_id=from_team_id,
        from_team_id=from_team_id,
        to_team_id=to_team_id,
        description=f"{trade.to_team} owns {original_team_abbr}'s {trade.year} round {trade.round} pick. {trade.note}",
        source=SOURCE,
        external_ref=f"pick:{trade.year}:{trade.round}:{original_team_abbr}:{trade.to_team}",
    )
    if inserted:
        con.execute(
            """
            INSERT INTO transaction_assets (
                transaction_id, asset_type, pick_id, from_team_id, to_team_id,
                season, asset_description
            )
            VALUES (?, 'DraftPick', ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                pick_id,
                from_team_id,
                to_team_id,
                trade.year,
                f"{original_team_abbr} {trade.year} round {trade.round} pick.",
            ),
        )

    return pick_id, f"{original_team_abbr} {trade.year} R{trade.round}: {trade.from_team} -> {trade.to_team}"


def seed_future_picks(con: sqlite3.Connection, *, transaction_date: str) -> dict[str, object]:
    ensure_pick_schema(con)
    teams = team_ids(con)
    reset_future_picks(con)
    base_count = seed_base_picks(con, teams)
    season = int(
        con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'"
        ).fetchone()[0]
    )

    applied: list[str] = []
    warnings: list[str] = []
    for trade in TRADED_PICKS:
        pick_id, message = apply_trade(
            con,
            teams,
            trade,
            season=season,
            transaction_date=transaction_date,
        )
        if pick_id is None:
            warnings.append(message)
        else:
            applied.append(message)

    return {
        "base_count": base_count,
        "trades_applied": len(applied),
        "warnings": warnings,
        "applied": applied,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed 2027-2029 future draft picks.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--transaction-date", default=None, help="Transaction date for pick-move log rows.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        transaction_date = args.transaction_date or con.execute("SELECT date('now')").fetchone()[0]
        result = seed_future_picks(con, transaction_date=transaction_date)
        con.commit()

        by_year = con.execute(
            """
            SELECT draft_year, count(*), sum(is_traded)
            FROM draft_picks
            WHERE draft_year BETWEEN 2027 AND 2029
            GROUP BY draft_year
            ORDER BY draft_year
            """
        ).fetchall()
        conditions = con.execute("SELECT COUNT(*) FROM draft_pick_conditions").fetchone()[0]

    print(f"Base future picks inserted: {result['base_count']}")
    print(f"Trades applied: {result['trades_applied']}")
    print(f"Pending pick conditions: {conditions}")
    print("By year:")
    for year, total, traded in by_year:
        print(f"  {year}: {total} picks, {traded or 0} traded")
    if result["warnings"]:
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
