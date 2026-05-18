#!/usr/bin/env python3
"""Create contract-year detail and Top 51 cap accounting views.

This converts the existing flat contract rows into season-by-season cap rows.
The first pass is derived from the data already in contracts: AAV becomes the
default annual cap hit, with signing bonus separated into prorated bonus and the
remaining amount treated as base salary. More precise Spotrac/OTC year details
can later overwrite individual rows without changing the cap views.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
CURRENT_SEASON = 2026
TOP_51_COUNT = 51
_SCHEMA_ENSURED_CONNECTIONS: set[int] = set()


@dataclass(frozen=True)
class Contract:
    contract_id: int
    player_id: int
    team_id: int
    start_year: int
    end_year: int
    total_value: int
    total_years: int
    aav: int
    signing_bonus: int
    roster_bonus: int
    workout_bonus: int
    is_guaranteed: int
    option_year: int
    option_exercised: int
    contract_type: str
    is_active: int


def int_or_zero(value: object) -> int:
    if value is None:
        return 0
    return int(value)


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    marker = id(con)
    if marker in _SCHEMA_ENSURED_CONNECTIONS:
        return
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS game_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        INSERT INTO game_settings (setting_key, setting_value)
        VALUES
            ('current_season', '2026'),
            ('current_contract_year', '2026'),
            ('cap_accounting_mode', 'TOP_51_ALWAYS'),
            ('salary_cap_rule', 'TOP_51_ALWAYS'),
            ('top_51_count', '51'),
            ('regular_season_statuses_enabled', '0')
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = CASE
                WHEN excluded.setting_key IN ('cap_accounting_mode', 'salary_cap_rule', 'top_51_count')
                    THEN excluded.setting_value
                ELSE game_settings.setting_value
            END,
            updated_at = datetime('now');

        CREATE TABLE IF NOT EXISTS roster_status_types (
            status_code TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            counts_against_top51 INTEGER NOT NULL DEFAULT 1,
            counts_against_regular_cap INTEGER NOT NULL DEFAULT 1,
            counts_against_roster_limit INTEGER NOT NULL DEFAULT 1,
            counts_against_practice_squad_limit INTEGER NOT NULL DEFAULT 0,
            description TEXT
        );

        INSERT INTO roster_status_types (
            status_code, display_name, counts_against_top51,
            counts_against_regular_cap, counts_against_roster_limit,
            counts_against_practice_squad_limit, description
        )
        VALUES
            ('Active', 'Active Roster / Preseason Roster', 1, 1, 1, 0, 'Default player status at game start.'),
            ('Free Agent', 'Free Agent', 0, 0, 0, 0, 'Unassigned player pool.'),
            ('Practice Squad', 'Practice Squad', 0, 1, 0, 1, 'Available for later regular-season roster rules.'),
            ('Questionable', 'Questionable / Active Roster', 1, 1, 1, 0, 'Player is on the active roster but has questionable game availability.'),
            ('Doubtful', 'Doubtful / Active Roster', 1, 1, 1, 0, 'Player is on the active roster but is unlikely to play this week.'),
            ('Out', 'Out / Active Roster', 1, 1, 1, 0, 'Player is unavailable for the week but still occupies an active roster spot.'),
            ('IR', 'Injured Reserve', 0, 1, 0, 0, 'Available after injuries are enabled.'),
            ('PUP', 'Physically Unable To Perform', 0, 1, 0, 0, 'Available after preseason/regular-season statuses are enabled.'),
            ('NFI', 'Non-Football Injury', 0, 1, 0, 0, 'Available after preseason/regular-season statuses are enabled.'),
            ('Suspended', 'Suspended', 0, 1, 0, 0, 'Available after discipline/status systems are enabled.'),
            ('Retired', 'Retired', 0, 0, 0, 0, 'Inactive retired player.'),
            ('Exempt', 'Exempt', 0, 1, 0, 0, 'Commissioner exempt or other special status.')
        ON CONFLICT(status_code) DO UPDATE SET
            display_name = excluded.display_name,
            counts_against_top51 = excluded.counts_against_top51,
            counts_against_regular_cap = excluded.counts_against_regular_cap,
            counts_against_roster_limit = excluded.counts_against_roster_limit,
            counts_against_practice_squad_limit = excluded.counts_against_practice_squad_limit,
            description = excluded.description;

        CREATE TABLE IF NOT EXISTS player_roster_status_history (
            status_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            old_status TEXT,
            new_status TEXT NOT NULL,
            effective_date TEXT,
            season INTEGER,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS contract_years (
            contract_year_id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER NOT NULL REFERENCES contracts(contract_id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            contract_year_number INTEGER NOT NULL,
            base_salary INTEGER NOT NULL DEFAULT 0,
            signing_bonus_proration INTEGER NOT NULL DEFAULT 0,
            roster_bonus INTEGER NOT NULL DEFAULT 0,
            workout_bonus INTEGER NOT NULL DEFAULT 0,
            option_bonus_proration INTEGER NOT NULL DEFAULT 0,
            other_bonus INTEGER NOT NULL DEFAULT 0,
            guaranteed_salary INTEGER NOT NULL DEFAULT 0,
            cap_hit INTEGER NOT NULL DEFAULT 0,
            cash_due INTEGER NOT NULL DEFAULT 0,
            dead_cap_if_cut_pre_june1 INTEGER NOT NULL DEFAULT 0,
            dead_cap_if_cut_post_june1_current INTEGER NOT NULL DEFAULT 0,
            dead_cap_if_cut_post_june1_next INTEGER NOT NULL DEFAULT 0,
            is_option_year INTEGER NOT NULL DEFAULT 0,
            option_exercised INTEGER NOT NULL DEFAULT 0,
            is_void_year INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'derived_from_contracts_aav',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(contract_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_contract_years_team_season
            ON contract_years(team_id, season, is_active, cap_hit);

        CREATE INDEX IF NOT EXISTS idx_contract_years_player_season
            ON contract_years(player_id, season);

        CREATE TABLE IF NOT EXISTS team_cap_charges (
            cap_charge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            charge_type TEXT NOT NULL,
            description TEXT,
            amount INTEGER NOT NULL DEFAULT 0,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_team_cap_charges_team_season
            ON team_cap_charges(team_id, season);

        CREATE TABLE IF NOT EXISTS salary_cap_rollovers (
            rollover_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            from_season INTEGER NOT NULL,
            to_season INTEGER NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            elected INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'cap_rollover',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(team_id, from_season, to_season)
        );

        CREATE INDEX IF NOT EXISTS idx_salary_cap_rollovers_to_season
            ON salary_cap_rollovers(to_season, team_id);

        CREATE TABLE IF NOT EXISTS contract_restructures (
            restructure_id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER NOT NULL REFERENCES contracts(contract_id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            restructure_season INTEGER NOT NULL,
            converted_salary INTEGER NOT NULL,
            proration_years INTEGER NOT NULL,
            current_year_proration INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(contract_id, restructure_season)
        );

        CREATE INDEX IF NOT EXISTS idx_contract_restructures_contract
            ON contract_restructures(contract_id, restructure_season, is_active);

        DROP VIEW IF EXISTS contract_years_view;
        CREATE VIEW contract_years_view AS
        SELECT
            cy.contract_year_id,
            cy.contract_id,
            cy.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.status,
            cy.team_id,
            t.abbreviation AS team,
            cy.season,
            cy.contract_year_number,
            cy.base_salary,
            cy.signing_bonus_proration,
            cy.roster_bonus,
            cy.workout_bonus,
            cy.option_bonus_proration,
            cy.other_bonus,
            cy.guaranteed_salary,
            cy.cap_hit,
            cy.cash_due,
            cy.dead_cap_if_cut_pre_june1,
            cy.dead_cap_if_cut_post_june1_current,
            cy.dead_cap_if_cut_post_june1_next,
            cy.is_option_year,
            cy.option_exercised,
            cy.is_void_year,
            cy.is_active,
            c.start_year,
            c.end_year,
            c.total_value,
            c.aav,
            c.contract_type,
            cy.source,
            cy.notes,
            cy.updated_at
        FROM contract_years cy
        JOIN contracts c ON c.contract_id = cy.contract_id
        JOIN players p ON p.player_id = cy.player_id
        JOIN teams t ON t.team_id = cy.team_id;

        DROP VIEW IF EXISTS current_contract_years_view;
        CREATE VIEW current_contract_years_view AS
        SELECT cyv.*
        FROM contract_years_view cyv
        WHERE cyv.season = (
            SELECT COALESCE(
                (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_contract_year'),
                (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
            )
        )
          AND cyv.is_active = 1;

        DROP VIEW IF EXISTS team_top51_cap_detail_view;
        CREATE VIEW team_top51_cap_detail_view AS
        SELECT
            cyv.*,
            ROW_NUMBER() OVER (
                PARTITION BY cyv.team_id, cyv.season
                ORDER BY cyv.cap_hit DESC, cyv.player_name ASC, cyv.player_id ASC
            ) AS top51_rank,
            CASE
                WHEN ROW_NUMBER() OVER (
                    PARTITION BY cyv.team_id, cyv.season
                    ORDER BY cyv.cap_hit DESC, cyv.player_name ASC, cyv.player_id ASC
                ) <= (
                    SELECT CAST(setting_value AS INTEGER)
                    FROM game_settings
                    WHERE setting_key = 'top_51_count'
                )
                THEN 1 ELSE 0
            END AS counts_in_top51
        FROM current_contract_years_view cyv
        JOIN players p ON p.player_id = cyv.player_id
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE p.team_id = cyv.team_id
          AND COALESCE(rst.counts_against_top51, 1) = 1;

        DROP VIEW IF EXISTS team_top51_cap_view;
        CREATE VIEW team_top51_cap_view AS
        WITH detail AS (
            SELECT *
            FROM team_top51_cap_detail_view
        ),
        other_charges AS (
            SELECT
                team_id,
                season,
                COALESCE(SUM(amount), 0) AS other_cap_charges
            FROM team_cap_charges
            WHERE season = (
                SELECT COALESCE(
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_contract_year'),
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
                )
            )
            GROUP BY team_id, season
        )
        SELECT
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            (
                SELECT COALESCE(
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_contract_year'),
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
                )
            ) AS season,
            (SELECT setting_value FROM game_settings WHERE setting_key = 'cap_accounting_mode') AS cap_accounting_mode,
            t.salary_cap AS base_salary_cap,
            COALESCE(MAX(r.amount), 0) AS rollover_amount,
            t.salary_cap + COALESCE(MAX(r.amount), 0) AS salary_cap,
            COUNT(d.contract_year_id) AS active_contracts,
            COALESCE(SUM(CASE WHEN d.counts_in_top51 = 1 THEN d.cap_hit ELSE 0 END), 0) AS top51_cap_hit,
            COALESCE(SUM(CASE WHEN d.counts_in_top51 = 0 THEN d.cap_hit ELSE 0 END), 0) AS excluded_contract_cap_hit,
            COALESCE(MAX(CASE WHEN d.top51_rank = 51 THEN d.cap_hit END), 0) AS top51_cutoff_cap_hit,
            COALESCE(MAX(o.other_cap_charges), 0) AS other_cap_charges,
            COALESCE(SUM(CASE WHEN d.counts_in_top51 = 1 THEN d.cap_hit ELSE 0 END), 0)
                + COALESCE(MAX(o.other_cap_charges), 0) AS total_committed,
            t.salary_cap + COALESCE(MAX(r.amount), 0)
                - (
                    COALESCE(SUM(CASE WHEN d.counts_in_top51 = 1 THEN d.cap_hit ELSE 0 END), 0)
                    + COALESCE(MAX(o.other_cap_charges), 0)
                  ) AS cap_space,
            SUM(CASE WHEN d.counts_in_top51 = 1 THEN 1 ELSE 0 END) AS contracts_counted,
            SUM(CASE WHEN d.counts_in_top51 = 0 THEN 1 ELSE 0 END) AS contracts_excluded
        FROM teams t
        LEFT JOIN detail d ON d.team_id = t.team_id
        LEFT JOIN other_charges o ON o.team_id = t.team_id
        LEFT JOIN salary_cap_rollovers r
          ON r.team_id = t.team_id
         AND r.elected = 1
         AND r.to_season = (
                SELECT COALESCE(
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_contract_year'),
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
                )
            )
        GROUP BY t.team_id;

        DROP VIEW IF EXISTS team_cap_view;
        CREATE VIEW team_cap_view AS
        SELECT *
        FROM team_top51_cap_view;
        """
    )
    _SCHEMA_ENSURED_CONNECTIONS.add(marker)


def get_contracts(con: sqlite3.Connection) -> list[Contract]:
    rows = con.execute(
        """
        SELECT
            contract_id,
            player_id,
            team_id,
            COALESCE(start_year, ?) AS start_year,
            COALESCE(end_year, ?) AS end_year,
            COALESCE(total_value, 0) AS total_value,
            COALESCE(total_years, 0) AS total_years,
            COALESCE(aav, 0) AS aav,
            COALESCE(signing_bonus, 0) AS signing_bonus,
            COALESCE(roster_bonus, 0) AS roster_bonus,
            COALESCE(workout_bonus, 0) AS workout_bonus,
            COALESCE(is_guaranteed, 0) AS is_guaranteed,
            COALESCE(option_year, 0) AS option_year,
            COALESCE(option_exercised, 0) AS option_exercised,
            COALESCE(contract_type, 'Standard') AS contract_type,
            COALESCE(is_active, 1) AS is_active
        FROM contracts
        WHERE is_active = 1
        """,
        (CURRENT_SEASON, CURRENT_SEASON),
    ).fetchall()
    return [row_to_contract(row) for row in rows]


def get_contract(con: sqlite3.Connection, contract_id: int) -> Contract | None:
    row = con.execute(
        """
        SELECT
            contract_id,
            player_id,
            team_id,
            COALESCE(start_year, ?) AS start_year,
            COALESCE(end_year, ?) AS end_year,
            COALESCE(total_value, 0) AS total_value,
            COALESCE(total_years, 0) AS total_years,
            COALESCE(aav, 0) AS aav,
            COALESCE(signing_bonus, 0) AS signing_bonus,
            COALESCE(roster_bonus, 0) AS roster_bonus,
            COALESCE(workout_bonus, 0) AS workout_bonus,
            COALESCE(is_guaranteed, 0) AS is_guaranteed,
            COALESCE(option_year, 0) AS option_year,
            COALESCE(option_exercised, 0) AS option_exercised,
            COALESCE(contract_type, 'Standard') AS contract_type,
            COALESCE(is_active, 1) AS is_active
        FROM contracts
        WHERE contract_id = ?
          AND is_active = 1
        """,
        (CURRENT_SEASON, CURRENT_SEASON, contract_id),
    ).fetchone()
    return row_to_contract(row) if row else None


def row_to_contract(row: sqlite3.Row) -> Contract:
    return Contract(
        contract_id=int_or_zero(row["contract_id"]),
        player_id=int_or_zero(row["player_id"]),
        team_id=int_or_zero(row["team_id"]),
        start_year=int_or_zero(row["start_year"]),
        end_year=int_or_zero(row["end_year"]),
        total_value=int_or_zero(row["total_value"]),
        total_years=int_or_zero(row["total_years"]),
        aav=int_or_zero(row["aav"]),
        signing_bonus=int_or_zero(row["signing_bonus"]),
        roster_bonus=int_or_zero(row["roster_bonus"]),
        workout_bonus=int_or_zero(row["workout_bonus"]),
        is_guaranteed=int_or_zero(row["is_guaranteed"]),
        option_year=int_or_zero(row["option_year"]),
        option_exercised=int_or_zero(row["option_exercised"]),
        contract_type=str(row["contract_type"] or "Standard"),
        is_active=int_or_zero(row["is_active"]),
    )


def contract_seasons(contract: Contract) -> list[int]:
    start = contract.start_year or CURRENT_SEASON
    end = contract.end_year or max(start, CURRENT_SEASON)
    if end < start:
        end = start
    return list(range(start, end + 1))


def distribute_evenly(total: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base = total // count
    remainder = total - (base * count)
    values = [base for _ in range(count)]
    for index in range(remainder):
        values[index] += 1
    return values


def insert_contract_year_rows(con: sqlite3.Connection, contract: Contract) -> int:
    inserted = 0

    seasons = contract_seasons(contract)
    season_count = len(seasons)
    prorated_count = min(5, season_count) if contract.signing_bonus else 0
    proration_by_index = distribute_evenly(contract.signing_bonus, prorated_count)

    year_rows: list[dict[str, int]] = []
    for index, season in enumerate(seasons):
        signing_proration = proration_by_index[index] if index < prorated_count else 0
        roster_bonus = contract.roster_bonus
        workout_bonus = contract.workout_bonus
        other_bonus = 0
        target_cap_hit = contract.aav or (
            contract.total_value // max(1, contract.total_years or season_count)
        )
        base_salary = max(
            0,
            target_cap_hit - signing_proration - roster_bonus - workout_bonus - other_bonus,
        )
        cap_hit = base_salary + signing_proration + roster_bonus + workout_bonus + other_bonus
        guaranteed_salary = base_salary if contract.is_guaranteed else 0
        cash_due = base_salary + roster_bonus + workout_bonus
        if index == 0:
            cash_due += contract.signing_bonus

        year_rows.append(
            {
                "season": season,
                "contract_year_number": index + 1,
                "base_salary": base_salary,
                "signing_bonus_proration": signing_proration,
                "roster_bonus": roster_bonus,
                "workout_bonus": workout_bonus,
                "option_bonus_proration": 0,
                "other_bonus": other_bonus,
                "guaranteed_salary": guaranteed_salary,
                "cap_hit": cap_hit,
                "cash_due": cash_due,
                "is_option_year": 1 if contract.option_year and contract.option_exercised and index == season_count - 1 else 0,
                "option_exercised": contract.option_exercised,
                "is_void_year": 0,
            }
        )

    if table_exists(con, "contract_restructures"):
        restructures = con.execute(
            """
            SELECT *
            FROM contract_restructures
            WHERE contract_id = ?
              AND is_active = 1
            ORDER BY restructure_season, restructure_id
            """,
            (contract.contract_id,),
        ).fetchall()
        season_index = {season: index for index, season in enumerate(seasons)}
        for restructure in restructures:
            start_index = season_index.get(int(restructure["restructure_season"]))
            if start_index is None:
                continue
            remaining_years = len(year_rows) - start_index
            proration_years = max(
                1,
                min(int(restructure["proration_years"] or 1), remaining_years, 5),
            )
            converted_salary = min(
                int(restructure["converted_salary"] or 0),
                int(year_rows[start_index]["base_salary"] or 0),
            )
            if converted_salary <= 0:
                continue
            allocations = distribute_evenly(converted_salary, proration_years)
            year_rows[start_index]["base_salary"] = max(
                0,
                year_rows[start_index]["base_salary"] - converted_salary,
            )
            for offset, proration in enumerate(allocations):
                row = year_rows[start_index + offset]
                row["signing_bonus_proration"] += proration
            for row in year_rows[start_index : start_index + proration_years]:
                row["cap_hit"] = (
                    row["base_salary"]
                    + row["signing_bonus_proration"]
                    + row["roster_bonus"]
                    + row["workout_bonus"]
                    + row["other_bonus"]
                )

    for index, row in enumerate(year_rows):
        remaining_rows = year_rows[index:]
        future_rows = year_rows[index + 1 :]
        remaining_proration = sum(
            item["signing_bonus_proration"] + item["option_bonus_proration"]
            for item in remaining_rows
        )
        future_proration = sum(
            item["signing_bonus_proration"] + item["option_bonus_proration"]
            for item in future_rows
        )
        remaining_guarantees = sum(item["guaranteed_salary"] for item in remaining_rows)
        future_guarantees = sum(item["guaranteed_salary"] for item in future_rows)
        current_guarantees = row["guaranteed_salary"]
        current_proration = row["signing_bonus_proration"] + row["option_bonus_proration"]

        pre_june1_dead = remaining_proration + remaining_guarantees
        post_june1_current = current_proration + current_guarantees
        post_june1_next = future_proration + future_guarantees

        con.execute(
            """
            INSERT INTO contract_years (
                contract_id, player_id, team_id, season, contract_year_number,
                base_salary, signing_bonus_proration, roster_bonus, workout_bonus,
                option_bonus_proration, other_bonus, guaranteed_salary, cap_hit,
                cash_due, dead_cap_if_cut_pre_june1,
                dead_cap_if_cut_post_june1_current,
                dead_cap_if_cut_post_june1_next, is_option_year,
                option_exercised, is_void_year, is_active, source, notes,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                contract.contract_id,
                contract.player_id,
                contract.team_id,
                row["season"],
                row["contract_year_number"],
                row["base_salary"],
                row["signing_bonus_proration"],
                row["roster_bonus"],
                row["workout_bonus"],
                row["option_bonus_proration"],
                row["other_bonus"],
                row["guaranteed_salary"],
                row["cap_hit"],
                row["cash_due"],
                pre_june1_dead,
                post_june1_current,
                post_june1_next,
                row["is_option_year"],
                row["option_exercised"],
                row["is_void_year"],
                contract.is_active,
                "derived_from_contracts_aav",
                "",
            ),
        )
        inserted += 1

    return inserted


def rebuild_contract_year(con: sqlite3.Connection, contract_id: int) -> int:
    con.execute("DELETE FROM contract_years WHERE contract_id = ?", (contract_id,))
    contract = get_contract(con, contract_id)
    if not contract:
        return 0
    return insert_contract_year_rows(con, contract)


def rebuild_contract_years(con: sqlite3.Connection) -> int:
    con.execute("DELETE FROM contract_years")
    inserted = 0
    for contract in get_contracts(con):
        inserted += insert_contract_year_rows(con, contract)
    return inserted


def sync_team_cap_space(con: sqlite3.Connection) -> None:
    con.execute(
        """
        UPDATE teams
        SET cap_space = COALESCE((
            SELECT cap_space
            FROM team_top51_cap_view v
            WHERE v.team_id = teams.team_id
        ), salary_cap)
        """
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build contract_years and Top 51 cap accounting views."
    )
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--no-rebuild", action="store_true", help="Only create schema/views; do not rebuild contract_years.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        ensure_schema(con)
        inserted = 0
        if not args.no_rebuild:
            inserted = rebuild_contract_years(con)
            sync_team_cap_space(con)
        con.commit()

        current_rows = con.execute(
            """
            SELECT COUNT(*)
            FROM contract_years
            WHERE season = (
                SELECT COALESCE(
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_contract_year'),
                    (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
                )
            )
            """
        ).fetchone()[0]
        teams = con.execute("SELECT COUNT(*) FROM team_top51_cap_view").fetchone()[0]

    print(f"Contract-year rows rebuilt: {inserted}")
    print(f"Current-season contract-year rows: {current_rows}")
    print(f"Top 51 cap teams available: {teams}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
