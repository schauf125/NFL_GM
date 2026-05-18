#!/usr/bin/env python3
"""Create transaction logging and team cap ledger infrastructure.

The transaction log records roster/contract moves. The cap ledger snapshots
the current Top 51 cap view into persistent summary and line-item tables, so
future save files can show how a team's cap changed over time.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_PHASE = "Preseason"
DEFAULT_SOURCE = "initial_database_snapshot"
_SCHEMA_ENSURED_CONNECTIONS: set[int] = set()


def ensure_schema(con: sqlite3.Connection) -> None:
    marker = id(con)
    if marker in _SCHEMA_ENSURED_CONNECTIONS:
        return
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS transaction_types (
            transaction_type TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            description TEXT
        );

        INSERT INTO transaction_types (transaction_type, category, description)
        VALUES
            ('Initial Roster Snapshot', 'Baseline', 'Baseline team roster state when transaction tracking was enabled.'),
            ('Initial Contract Load', 'Baseline', 'Existing active contract imported into the transaction log.'),
            ('Initial Free Agent Pool Load', 'Baseline', 'Free-agent pool player imported into the transaction log.'),
            ('Signing', 'Roster', 'Free agent or draft pick signed to a contract.'),
            ('Release', 'Roster', 'Player released from a roster.'),
            ('Contract Expired', 'Contract', 'Expired contract moved a player into the free-agent pool.'),
            ('Waiver Claim', 'Roster', 'Player claimed through waivers.'),
            ('Trade', 'Roster', 'Trade involving players, picks, or cap assets.'),
            ('Extension', 'Contract', 'Player signed a contract extension.'),
            ('Restructure', 'Contract', 'Contract restructured for cap/cash purposes.'),
            ('Option Decision', 'Contract', 'Team or player option exercised or declined.'),
            ('Franchise Tag', 'Contract', 'Franchise or transition tag applied.'),
            ('Rights Tender', 'Contract', 'Restricted or exclusive-rights free-agent tender applied.'),
            ('Fifth-Year Option', 'Contract', 'First-round rookie fifth-year option exercised or declined.'),
            ('Offer Sheet', 'Contract', 'Restricted or transition-tag offer sheet submitted, matched, or declined.'),
            ('Cap Rollover', 'Cap', 'Unused cap space carried into the next league year.'),
            ('Roster Status Change', 'Status', 'Player moved to Active, IR, PUP, Practice Squad, etc.'),
            ('Retirement', 'Status', 'Player retired.'),
            ('Cap Adjustment', 'Cap', 'Manual cap charge, credit, dead money, or league adjustment.'),
            ('Draft Pick Move', 'Draft', 'Draft pick traded, awarded, forfeited, or used.')
        ON CONFLICT(transaction_type) DO UPDATE SET
            category = excluded.category,
            description = excluded.description;

        CREATE TABLE IF NOT EXISTS transaction_log (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            phase TEXT NOT NULL DEFAULT 'Preseason',
            week INTEGER,
            transaction_type TEXT NOT NULL REFERENCES transaction_types(transaction_type),
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            secondary_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            contract_id INTEGER REFERENCES contracts(contract_id) ON DELETE SET NULL,
            from_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            to_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            old_status TEXT,
            new_status TEXT,
            cap_delta_current INTEGER NOT NULL DEFAULT 0,
            cap_delta_next INTEGER NOT NULL DEFAULT 0,
            cash_delta INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            source TEXT,
            external_ref TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, external_ref)
        );

        CREATE INDEX IF NOT EXISTS idx_transaction_log_team
            ON transaction_log(team_id, season, transaction_date);

        CREATE INDEX IF NOT EXISTS idx_transaction_log_player
            ON transaction_log(player_id, season, transaction_date);

        CREATE INDEX IF NOT EXISTS idx_transaction_log_type
            ON transaction_log(transaction_type, season);

        CREATE TABLE IF NOT EXISTS transaction_assets (
            transaction_asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL REFERENCES transaction_log(transaction_id) ON DELETE CASCADE,
            asset_type TEXT NOT NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            contract_id INTEGER REFERENCES contracts(contract_id) ON DELETE SET NULL,
            pick_id INTEGER REFERENCES draft_picks(pick_id) ON DELETE SET NULL,
            from_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            to_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            season INTEGER,
            asset_description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_transaction_assets_transaction
            ON transaction_assets(transaction_id);

        CREATE INDEX IF NOT EXISTS idx_transaction_assets_player
            ON transaction_assets(player_id);

        CREATE TABLE IF NOT EXISTS team_cap_ledger_snapshots (
            cap_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            phase TEXT NOT NULL DEFAULT 'Preseason',
            snapshot_label TEXT NOT NULL,
            snapshot_time TEXT NOT NULL DEFAULT (datetime('now')),
            cap_accounting_mode TEXT NOT NULL,
            salary_cap INTEGER NOT NULL,
            active_contracts INTEGER NOT NULL DEFAULT 0,
            contracts_counted INTEGER NOT NULL DEFAULT 0,
            contracts_excluded INTEGER NOT NULL DEFAULT 0,
            top51_player_cap INTEGER NOT NULL DEFAULT 0,
            excluded_contract_cap_hit INTEGER NOT NULL DEFAULT 0,
            top51_cutoff_cap_hit INTEGER NOT NULL DEFAULT 0,
            other_cap_charges INTEGER NOT NULL DEFAULT 0,
            dead_cap_charges INTEGER NOT NULL DEFAULT 0,
            draft_pool_reserve INTEGER NOT NULL DEFAULT 0,
            total_committed INTEGER NOT NULL DEFAULT 0,
            cap_space INTEGER NOT NULL DEFAULT 0,
            source TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(team_id, season, phase, snapshot_label, source)
        );

        CREATE INDEX IF NOT EXISTS idx_team_cap_ledger_snapshots_team_season
            ON team_cap_ledger_snapshots(team_id, season, snapshot_time);

        CREATE TABLE IF NOT EXISTS team_cap_ledger_lines (
            cap_ledger_line_id INTEGER PRIMARY KEY AUTOINCREMENT,
            cap_snapshot_id INTEGER NOT NULL REFERENCES team_cap_ledger_snapshots(cap_snapshot_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            line_type TEXT NOT NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            contract_id INTEGER REFERENCES contracts(contract_id) ON DELETE SET NULL,
            contract_year_id INTEGER REFERENCES contract_years(contract_year_id) ON DELETE SET NULL,
            cap_charge_id INTEGER REFERENCES team_cap_charges(cap_charge_id) ON DELETE SET NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            cap_counted_amount INTEGER NOT NULL DEFAULT 0,
            top51_rank INTEGER,
            counts_in_top51 INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_team_cap_ledger_lines_snapshot
            ON team_cap_ledger_lines(cap_snapshot_id);

        CREATE INDEX IF NOT EXISTS idx_team_cap_ledger_lines_team_season
            ON team_cap_ledger_lines(team_id, season);

        DROP VIEW IF EXISTS transaction_log_view;
        CREATE VIEW transaction_log_view AS
        SELECT
            tl.transaction_id,
            tl.transaction_date,
            tl.season,
            tl.phase,
            tl.week,
            tl.transaction_type,
            tt.category AS transaction_category,
            tl.team_id,
            team.abbreviation AS team,
            tl.secondary_team_id,
            secondary_team.abbreviation AS secondary_team,
            tl.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position AS player_position,
            tl.contract_id,
            tl.from_team_id,
            from_team.abbreviation AS from_team,
            tl.to_team_id,
            to_team.abbreviation AS to_team,
            tl.old_status,
            tl.new_status,
            tl.cap_delta_current,
            tl.cap_delta_next,
            tl.cash_delta,
            tl.description,
            tl.source,
            tl.external_ref,
            tl.created_at
        FROM transaction_log tl
        LEFT JOIN transaction_types tt ON tt.transaction_type = tl.transaction_type
        LEFT JOIN teams team ON team.team_id = tl.team_id
        LEFT JOIN teams secondary_team ON secondary_team.team_id = tl.secondary_team_id
        LEFT JOIN teams from_team ON from_team.team_id = tl.from_team_id
        LEFT JOIN teams to_team ON to_team.team_id = tl.to_team_id
        LEFT JOIN players p ON p.player_id = tl.player_id;

        DROP VIEW IF EXISTS transaction_assets_view;
        CREATE VIEW transaction_assets_view AS
        SELECT
            ta.transaction_asset_id,
            ta.transaction_id,
            tl.transaction_date,
            tl.transaction_type,
            ta.asset_type,
            ta.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position AS player_position,
            ta.contract_id,
            ta.pick_id,
            ta.from_team_id,
            from_team.abbreviation AS from_team,
            ta.to_team_id,
            to_team.abbreviation AS to_team,
            ta.amount,
            ta.season,
            ta.asset_description,
            ta.created_at
        FROM transaction_assets ta
        JOIN transaction_log tl ON tl.transaction_id = ta.transaction_id
        LEFT JOIN players p ON p.player_id = ta.player_id
        LEFT JOIN teams from_team ON from_team.team_id = ta.from_team_id
        LEFT JOIN teams to_team ON to_team.team_id = ta.to_team_id;

        DROP VIEW IF EXISTS player_transaction_history_view;
        CREATE VIEW player_transaction_history_view AS
        SELECT *
        FROM transaction_log_view
        WHERE player_id IS NOT NULL;

        DROP VIEW IF EXISTS team_transaction_history_view;
        CREATE VIEW team_transaction_history_view AS
        SELECT *
        FROM transaction_log_view
        WHERE team_id IS NOT NULL
           OR from_team_id IS NOT NULL
           OR to_team_id IS NOT NULL
           OR secondary_team_id IS NOT NULL;

        DROP VIEW IF EXISTS team_cap_ledger_latest_view;
        CREATE VIEW team_cap_ledger_latest_view AS
        WITH latest AS (
            SELECT team_id, season, MAX(cap_snapshot_id) AS cap_snapshot_id
            FROM team_cap_ledger_snapshots
            GROUP BY team_id, season
        )
        SELECT s.*
        FROM team_cap_ledger_snapshots s
        JOIN latest l ON l.cap_snapshot_id = s.cap_snapshot_id;

        DROP VIEW IF EXISTS team_cap_ledger_lines_view;
        CREATE VIEW team_cap_ledger_lines_view AS
        SELECT
            line.cap_ledger_line_id,
            line.cap_snapshot_id,
            snap.snapshot_label,
            snap.snapshot_time,
            line.team_id,
            t.abbreviation AS team,
            line.season,
            line.line_type,
            line.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position AS player_position,
            line.contract_id,
            line.contract_year_id,
            line.cap_charge_id,
            line.amount,
            line.cap_counted_amount,
            line.top51_rank,
            line.counts_in_top51,
            line.description,
            line.source,
            line.created_at
        FROM team_cap_ledger_lines line
        JOIN team_cap_ledger_snapshots snap ON snap.cap_snapshot_id = line.cap_snapshot_id
        JOIN teams t ON t.team_id = line.team_id
        LEFT JOIN players p ON p.player_id = line.player_id;

        DROP VIEW IF EXISTS current_team_cap_ledger_lines_view;
        CREATE VIEW current_team_cap_ledger_lines_view AS
        SELECT line_view.*
        FROM team_cap_ledger_lines_view line_view
        JOIN team_cap_ledger_latest_view latest
          ON latest.cap_snapshot_id = line_view.cap_snapshot_id;
        """
    )
    _SCHEMA_ENSURED_CONNECTIONS.add(marker)


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


def insert_transaction(
    con: sqlite3.Connection,
    *,
    transaction_date: str,
    season: int,
    phase: str,
    transaction_type: str,
    team_id: int | None = None,
    secondary_team_id: int | None = None,
    player_id: int | None = None,
    contract_id: int | None = None,
    from_team_id: int | None = None,
    to_team_id: int | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
    cap_delta_current: int = 0,
    cap_delta_next: int = 0,
    cash_delta: int = 0,
    description: str | None = None,
    source: str = DEFAULT_SOURCE,
    external_ref: str | None = None,
) -> tuple[int, bool]:
    if external_ref:
        row = con.execute(
            """
            SELECT transaction_id
            FROM transaction_log
            WHERE source = ? AND external_ref = ?
            """,
            (source, external_ref),
        ).fetchone()
        if row:
            return int(row[0]), False

    cur = con.execute(
        """
        INSERT INTO transaction_log (
            transaction_date, season, phase, transaction_type, team_id,
            secondary_team_id, player_id, contract_id, from_team_id, to_team_id,
            old_status, new_status, cap_delta_current, cap_delta_next, cash_delta,
            description, source, external_ref
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_date,
            season,
            phase,
            transaction_type,
            team_id,
            secondary_team_id,
            player_id,
            contract_id,
            from_team_id,
            to_team_id,
            old_status,
            new_status,
            cap_delta_current,
            cap_delta_next,
            cash_delta,
            description,
            source,
            external_ref,
        ),
    )
    return int(cur.lastrowid), True


def seed_initial_transactions(con: sqlite3.Connection, transaction_date: str, phase: str) -> dict[str, int]:
    season = current_season(con)
    counts = {"team_snapshots": 0, "contracts": 0, "free_agents": 0, "assets": 0}

    for row in con.execute(
        """
        SELECT
            t.team_id,
            t.abbreviation,
            COUNT(p.player_id) AS roster_count
        FROM teams t
        LEFT JOIN players p ON p.team_id = t.team_id
        GROUP BY t.team_id
        ORDER BY t.abbreviation
        """
    ):
        team_id, abbreviation, roster_count = row
        _, inserted = insert_transaction(
            con,
            transaction_date=transaction_date,
            season=season,
            phase=phase,
            transaction_type="Initial Roster Snapshot",
            team_id=int(team_id),
            description=f"Initial preseason roster snapshot for {abbreviation}: {roster_count} players.",
            external_ref=f"team_roster:{team_id}:season:{season}",
        )
        if inserted:
            counts["team_snapshots"] += 1

    for row in con.execute(
        """
        SELECT
            c.contract_id,
            c.player_id,
            c.team_id,
            t.abbreviation,
            p.first_name || ' ' || p.last_name AS player_name,
            c.end_year,
            COALESCE(cy_current.cap_hit, c.aav, 0) AS current_cap,
            COALESCE(cy_next.cap_hit, 0) AS next_cap,
            COALESCE(cy_current.cash_due, c.aav, 0) AS cash_due
        FROM contracts c
        JOIN players p ON p.player_id = c.player_id
        JOIN teams t ON t.team_id = c.team_id
        LEFT JOIN contract_years cy_current
          ON cy_current.contract_id = c.contract_id
         AND cy_current.season = ?
        LEFT JOIN contract_years cy_next
          ON cy_next.contract_id = c.contract_id
         AND cy_next.season = ? + 1
        WHERE c.is_active = 1
        ORDER BY t.abbreviation, player_name
        """,
        (season, season),
    ):
        (
            contract_id,
            player_id,
            team_id,
            abbreviation,
            player_name,
            end_year,
            current_cap,
            next_cap,
            cash_due,
        ) = row
        transaction_id, inserted = insert_transaction(
            con,
            transaction_date=transaction_date,
            season=season,
            phase=phase,
            transaction_type="Initial Contract Load",
            team_id=int(team_id),
            player_id=int(player_id),
            contract_id=int(contract_id),
            to_team_id=int(team_id),
            new_status="Active",
            cap_delta_current=int(current_cap or 0),
            cap_delta_next=int(next_cap or 0),
            cash_delta=int(cash_due or 0),
            description=f"Initial DB load: {player_name} under active contract with {abbreviation} through {end_year}.",
            external_ref=f"contract:{contract_id}:initial",
        )
        if inserted:
            counts["contracts"] += 1
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
                    int(player_id),
                    int(contract_id),
                    int(team_id),
                    int(current_cap or 0),
                    season,
                    f"{player_name} active contract baseline.",
                ),
            )
            counts["assets"] += 1

    for row in con.execute(
        """
        SELECT player_id, first_name || ' ' || last_name AS player_name, position
        FROM players
        WHERE team_id IS NULL AND status = 'Free Agent'
        ORDER BY player_name
        """
    ):
        player_id, player_name, position = row
        transaction_id, inserted = insert_transaction(
            con,
            transaction_date=transaction_date,
            season=season,
            phase=phase,
            transaction_type="Initial Free Agent Pool Load",
            player_id=int(player_id),
            old_status=None,
            new_status="Free Agent",
            description=f"Initial free-agent pool load: {player_name} ({position}).",
            external_ref=f"free_agent:{player_id}:initial",
        )
        if inserted:
            counts["free_agents"] += 1
            con.execute(
                """
                INSERT INTO transaction_assets (
                    transaction_id, asset_type, player_id, season, asset_description
                )
                VALUES (?, 'FreeAgentPlayer', ?, ?, ?)
                """,
                (
                    transaction_id,
                    int(player_id),
                    season,
                    f"{player_name} available in free-agent pool.",
                ),
            )
            counts["assets"] += 1

    return counts


def snapshot_cap_ledger(
    con: sqlite3.Connection,
    *,
    label: str,
    phase: str,
    source: str,
    replace: bool,
) -> dict[str, int]:
    season = current_season(con)
    if replace:
        snapshot_ids = [
            row[0]
            for row in con.execute(
                """
                SELECT cap_snapshot_id
                FROM team_cap_ledger_snapshots
                WHERE season = ? AND phase = ? AND snapshot_label = ? AND source = ?
                """,
                (season, phase, label, source),
            )
        ]
        if snapshot_ids:
            con.executemany(
                "DELETE FROM team_cap_ledger_snapshots WHERE cap_snapshot_id = ?",
                [(snapshot_id,) for snapshot_id in snapshot_ids],
            )

    counts = {"snapshots": 0, "lines": 0}
    team_rows = con.execute(
        """
        SELECT
            team_id,
            season,
            cap_accounting_mode,
            salary_cap,
            active_contracts,
            contracts_counted,
            contracts_excluded,
            top51_cap_hit,
            excluded_contract_cap_hit,
            top51_cutoff_cap_hit,
            other_cap_charges,
            total_committed,
            cap_space
        FROM team_cap_view
        ORDER BY abbreviation
        """
    ).fetchall()

    for row in team_rows:
        (
            team_id,
            row_season,
            accounting_mode,
            salary_cap,
            active_contracts,
            contracts_counted,
            contracts_excluded,
            top51_cap_hit,
            excluded_contract_cap_hit,
            top51_cutoff_cap_hit,
            other_cap_charges,
            total_committed,
            cap_space,
        ) = row
        cur = con.execute(
            """
            INSERT INTO team_cap_ledger_snapshots (
                team_id, season, phase, snapshot_label, cap_accounting_mode,
                salary_cap, active_contracts, contracts_counted,
                contracts_excluded, top51_player_cap,
                excluded_contract_cap_hit, top51_cutoff_cap_hit,
                other_cap_charges, dead_cap_charges, draft_pool_reserve,
                total_committed, cap_space, source, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
            ON CONFLICT(team_id, season, phase, snapshot_label, source)
            DO UPDATE SET
                snapshot_time = datetime('now'),
                cap_accounting_mode = excluded.cap_accounting_mode,
                salary_cap = excluded.salary_cap,
                active_contracts = excluded.active_contracts,
                contracts_counted = excluded.contracts_counted,
                contracts_excluded = excluded.contracts_excluded,
                top51_player_cap = excluded.top51_player_cap,
                excluded_contract_cap_hit = excluded.excluded_contract_cap_hit,
                top51_cutoff_cap_hit = excluded.top51_cutoff_cap_hit,
                other_cap_charges = excluded.other_cap_charges,
                total_committed = excluded.total_committed,
                cap_space = excluded.cap_space,
                notes = excluded.notes
            """,
            (
                int(team_id),
                int(row_season),
                phase,
                label,
                accounting_mode,
                int(salary_cap),
                int(active_contracts or 0),
                int(contracts_counted or 0),
                int(contracts_excluded or 0),
                int(top51_cap_hit or 0),
                int(excluded_contract_cap_hit or 0),
                int(top51_cutoff_cap_hit or 0),
                int(other_cap_charges or 0),
                int(total_committed or 0),
                int(cap_space or 0),
                source,
                "Top 51 cap snapshot from team_cap_view.",
            ),
        )
        snapshot_id = cur.lastrowid
        if not snapshot_id:
            snapshot_id = con.execute(
                """
                SELECT cap_snapshot_id
                FROM team_cap_ledger_snapshots
                WHERE team_id = ? AND season = ? AND phase = ?
                  AND snapshot_label = ? AND source = ?
                """,
                (int(team_id), int(row_season), phase, label, source),
            ).fetchone()[0]

        con.execute(
            "DELETE FROM team_cap_ledger_lines WHERE cap_snapshot_id = ?",
            (int(snapshot_id),),
        )
        counts["snapshots"] += 1

        detail_rows = con.execute(
            """
            SELECT
                contract_year_id,
                contract_id,
                player_id,
                team_id,
                season,
                cap_hit,
                top51_rank,
                counts_in_top51,
                player_name
            FROM team_top51_cap_detail_view
            WHERE team_id = ? AND season = ?
            ORDER BY top51_rank
            """,
            (int(team_id), int(row_season)),
        ).fetchall()
        for detail in detail_rows:
            (
                contract_year_id,
                contract_id,
                player_id,
                detail_team_id,
                detail_season,
                cap_hit,
                top51_rank,
                counts_in_top51,
                player_name,
            ) = detail
            counted = int(counts_in_top51 or 0)
            line_type = "PLAYER_TOP51" if counted else "PLAYER_EXCLUDED"
            con.execute(
                """
                INSERT INTO team_cap_ledger_lines (
                    cap_snapshot_id, team_id, season, line_type, player_id,
                    contract_id, contract_year_id, amount, cap_counted_amount,
                    top51_rank, counts_in_top51, description, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(snapshot_id),
                    int(detail_team_id),
                    int(detail_season),
                    line_type,
                    int(player_id),
                    int(contract_id),
                    int(contract_year_id),
                    int(cap_hit or 0),
                    int(cap_hit or 0) if counted else 0,
                    int(top51_rank),
                    counted,
                    f"{player_name} current-season cap charge.",
                    source,
                ),
            )
            counts["lines"] += 1

        charge_rows = con.execute(
            """
            SELECT cap_charge_id, team_id, season, charge_type, description, amount, player_id, source
            FROM team_cap_charges
            WHERE team_id = ? AND season = ?
            ORDER BY cap_charge_id
            """,
            (int(team_id), int(row_season)),
        ).fetchall()
        for charge in charge_rows:
            cap_charge_id, charge_team_id, charge_season, charge_type, description, amount, player_id, charge_source = charge
            con.execute(
                """
                INSERT INTO team_cap_ledger_lines (
                    cap_snapshot_id, team_id, season, line_type, player_id,
                    cap_charge_id, amount, cap_counted_amount, counts_in_top51,
                    description, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    int(snapshot_id),
                    int(charge_team_id),
                    int(charge_season),
                    charge_type,
                    int(player_id) if player_id is not None else None,
                    int(cap_charge_id),
                    int(amount or 0),
                    int(amount or 0),
                    description,
                    charge_source or source,
                ),
            )
            counts["lines"] += 1

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set up transaction logging and team cap ledger snapshots."
    )
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--phase", default=DEFAULT_PHASE, help="Phase label for baseline rows. Default: Preseason")
    parser.add_argument("--label", default="initial_preseason_top51", help="Cap ledger snapshot label.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Source tag for idempotent baseline rows.")
    parser.add_argument("--transaction-date", default=None, help="Transaction date. Default: SQLite current date.")
    parser.add_argument("--no-seed-transactions", action="store_true", help="Only create schema and cap ledger snapshot.")
    parser.add_argument("--no-ledger-snapshot", action="store_true", help="Only create schema and seed transactions.")
    parser.add_argument("--no-replace-ledger", action="store_true", help="Do not replace existing snapshot with same label/source.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        ensure_schema(con)
        if args.transaction_date:
            transaction_date = args.transaction_date
        else:
            transaction_date = con.execute("SELECT date('now')").fetchone()[0]

        transaction_counts = {"team_snapshots": 0, "contracts": 0, "free_agents": 0, "assets": 0}
        if not args.no_seed_transactions:
            transaction_counts = seed_initial_transactions(con, transaction_date, args.phase)

        ledger_counts = {"snapshots": 0, "lines": 0}
        if not args.no_ledger_snapshot:
            ledger_counts = snapshot_cap_ledger(
                con,
                label=args.label,
                phase=args.phase,
                source=args.source,
                replace=not args.no_replace_ledger,
            )

        con.commit()

        total_transactions = con.execute("SELECT COUNT(*) FROM transaction_log").fetchone()[0]
        total_snapshots = con.execute("SELECT COUNT(*) FROM team_cap_ledger_snapshots").fetchone()[0]
        total_lines = con.execute("SELECT COUNT(*) FROM team_cap_ledger_lines").fetchone()[0]

    print("Transaction seed counts:")
    for key, value in transaction_counts.items():
        print(f"  {key}: {value}")
    print("Cap ledger snapshot counts:")
    for key, value in ledger_counts.items():
        print(f"  {key}: {value}")
    print(f"Total transactions: {total_transactions}")
    print(f"Total cap snapshots: {total_snapshots}")
    print(f"Total cap ledger lines: {total_lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
