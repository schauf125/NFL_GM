#!/usr/bin/env python3
"""Own-team contract negotiation helpers.

This is intentionally simple for the first playable offseason. It finds players
whose contracts expire after the current season, estimates a practical ask, and
can add a next-year extension while preserving the current contract row.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import setup_contract_years  # noqa: E402
from tools import setup_transactions_cap_ledger  # noqa: E402
from tools import roster_actions  # noqa: E402


SOURCE = "contract_negotiations"
PHASE = "Offseason"
_RIGHTS_SCHEMA_ENSURED_CONNECTIONS: set[int] = set()

POSITION_GROUP = {
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
}

BASE_AAV = {
    "QB": 12_000_000,
    "RB": 2_500_000,
    "WR": 7_500_000,
    "TE": 4_000_000,
    "OT": 7_000_000,
    "IOL": 4_500_000,
    "EDGE": 8_000_000,
    "IDL": 5_500_000,
    "LB": 4_000_000,
    "CB": 6_000_000,
    "S": 4_000_000,
    "ST": 1_200_000,
}

MAX_AAV = {
    "QB": 58_000_000,
    "RB": 13_000_000,
    "WR": 31_000_000,
    "TE": 18_000_000,
    "OT": 29_000_000,
    "IOL": 22_000_000,
    "EDGE": 34_000_000,
    "IDL": 28_000_000,
    "LB": 19_000_000,
    "CB": 26_000_000,
    "S": 20_000_000,
    "ST": 3_500_000,
}

FRANCHISE_TAG_AAV = {
    "QB": 43_000_000,
    "RB": 13_000_000,
    "WR": 24_000_000,
    "TE": 14_500_000,
    "OT": 23_500_000,
    "IOL": 21_000_000,
    "EDGE": 26_000_000,
    "IDL": 24_000_000,
    "LB": 22_000_000,
    "CB": 21_000_000,
    "S": 18_000_000,
    "ST": 6_000_000,
}

TRANSITION_TAG_AAV = {
    "QB": 37_500_000,
    "RB": 11_000_000,
    "WR": 21_000_000,
    "TE": 12_500_000,
    "OT": 20_500_000,
    "IOL": 18_000_000,
    "EDGE": 22_500_000,
    "IDL": 20_500_000,
    "LB": 18_500_000,
    "CB": 18_000_000,
    "S": 15_000_000,
    "ST": 5_000_000,
}

RFA_TENDER_AAV = {
    # Simmed 2026 tender levels. These are centralized game constants so yearly
    # tender tuning stays in one place when the league year data is updated.
    "first_round": 7_300_000,
    "second_round": 5_400_000,
    "original_round": 3_300_000,
    "rofr": 3_200_000,
}

ERFA_TENDER_AAV = {
    "QB": 1_300_000,
    "RB": 1_100_000,
    "WR": 1_100_000,
    "TE": 1_100_000,
    "OT": 1_200_000,
    "IOL": 1_150_000,
    "EDGE": 1_200_000,
    "IDL": 1_150_000,
    "LB": 1_100_000,
    "CB": 1_100_000,
    "S": 1_100_000,
    "ST": 1_000_000,
}

FIFTH_YEAR_OPTION_AAV = {
    "QB": 27_500_000,
    "RB": 8_500_000,
    "WR": 15_500_000,
    "TE": 10_500_000,
    "OT": 18_000_000,
    "IOL": 15_000_000,
    "EDGE": 19_500_000,
    "IDL": 17_500_000,
    "LB": 14_500_000,
    "CB": 16_000_000,
    "S": 13_000_000,
    "ST": 4_000_000,
}

MAX_SINGLE_YEAR_ROLLOVER = 85_000_000

MIN_AAV = {
    "QB": 1_500_000,
    "RB": 1_000_000,
    "WR": 1_100_000,
    "TE": 1_100_000,
    "OT": 1_200_000,
    "IOL": 1_100_000,
    "EDGE": 1_200_000,
    "IDL": 1_100_000,
    "LB": 1_100_000,
    "CB": 1_100_000,
    "S": 1_100_000,
    "ST": 1_000_000,
}

MIN_RESTRUCTURE_BASE_FLOOR = 1_200_000
MIN_RESTRUCTURE_SAVINGS = 1_000_000


@dataclass(frozen=True)
class OfferEstimate:
    tier: str
    priority: str
    recommendation: str
    suggested_years: int
    asking_aav: int
    minimum_aav: int
    guarantee_pct: int


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


def ensure_contract_rights_schema(con: sqlite3.Connection) -> None:
    marker = id(con)
    if marker in _RIGHTS_SCHEMA_ENSURED_CONNECTIONS:
        return
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS contract_rights_tenders (
            tender_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            rights_type TEXT NOT NULL,
            tender_type TEXT NOT NULL,
            compensation TEXT,
            aav INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'applied',
            applied_contract_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(league_year, team_id, player_id)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contract_rights_tenders_year_team
            ON contract_rights_tenders(league_year, team_id, status)
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fifth_year_option_decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            rookie_contract_id INTEGER NOT NULL REFERENCES contracts(contract_id) ON DELETE CASCADE,
            option_contract_id INTEGER REFERENCES contracts(contract_id) ON DELETE SET NULL,
            decision TEXT NOT NULL,
            option_salary INTEGER NOT NULL DEFAULT 0,
            decision_date TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(league_year, team_id, player_id)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fifth_year_option_decisions_year_team
            ON fifth_year_option_decisions(league_year, team_id, decision)
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS free_agency_offer_sheets (
            offer_sheet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            original_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            offering_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            tender_type TEXT NOT NULL,
            compensation TEXT,
            compensation_round INTEGER,
            years INTEGER NOT NULL,
            aav INTEGER NOT NULL,
            signing_bonus INTEGER NOT NULL DEFAULT 0,
            guarantee_pct INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending_match',
            submitted_date TEXT NOT NULL,
            decision_date TEXT,
            matched INTEGER,
            resulting_contract_id INTEGER REFERENCES contracts(contract_id) ON DELETE SET NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_free_agency_offer_sheets_year_status
            ON free_agency_offer_sheets(league_year, status, original_team_id)
        """
    )
    _RIGHTS_SCHEMA_ENSURED_CONNECTIONS.add(marker)


def current_game_date(con: sqlite3.Connection) -> str:
    active_date = None
    if table_exists(con, "active_game_save_view"):
        row = con.execute('SELECT "current_date" FROM active_game_save_view LIMIT 1').fetchone()
        if row and row["current_date"]:
            active_date = str(row["current_date"])
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    setting_date = str(row["setting_value"]) if row else None
    if active_date and setting_date:
        return max(active_date, setting_date)
    return active_date or setting_date or "2027-02-01"


def current_phase(con: sqlite3.Connection) -> str:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT phase_name FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["phase_name"]:
            return str(row["phase_name"])
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_calendar_phase'"
    ).fetchone()
    return str(row["setting_value"]) if row else PHASE


def team_row(con: sqlite3.Connection, team: str | int) -> sqlite3.Row:
    if isinstance(team, int) or str(team).isdigit():
        row = con.execute("SELECT * FROM teams WHERE team_id = ?", (int(team),)).fetchone()
    else:
        row = con.execute("SELECT * FROM teams WHERE abbreviation = ?", (str(team).upper(),)).fetchone()
    if not row:
        raise ValueError(f"Unknown team: {team}")
    return row


def money(value: int | float | None) -> str:
    if value is None:
        return "-"
    amount = int(round(value))
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount:,}"


def parse_money(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text[-1].lower() == "m":
        multiplier = 1_000_000
        text = text[:-1]
    elif text[-1].lower() == "k":
        multiplier = 1_000
        text = text[:-1]
    return int(float(text) * multiplier)


def rounded_money(value: float) -> int:
    return int(round(value / 100_000) * 100_000)


def position_group(position: str | None) -> str:
    return POSITION_GROUP.get(str(position or "").upper(), str(position or "OTHER").upper())


def player_score(row: sqlite3.Row) -> float:
    if row["best_role_score"] is not None:
        return float(row["best_role_score"])
    if row["avg_rating"] is not None:
        return float(row["avg_rating"])
    if row["overall"] is not None:
        return float(row["overall"])
    return 60.0


def market_tier(group: str, score: float) -> str:
    if group == "QB":
        if score >= 82:
            return "Franchise"
        if score >= 76:
            return "Starter"
        if score >= 68:
            return "Bridge"
        return "Backup"
    if group == "ST":
        if score >= 75:
            return "High-End Specialist"
        return "Specialist"
    if score >= 84:
        return "Core"
    if score >= 78:
        return "Starter"
    if score >= 72:
        return "Regular"
    if score >= 66:
        return "Depth"
    return "Camp"


def age_factor(group: str, age: int | None, score: float) -> float:
    if age is None:
        return 1.0
    if group == "RB":
        if age >= 31:
            return 0.55
        if age >= 29:
            return 0.72
    if group in {"QB", "OT", "IOL", "ST"}:
        if age >= 36:
            return 0.72
        if age >= 33:
            return 0.84
        if age >= 31:
            return 0.92
    else:
        if age >= 33:
            return 0.68
        if age >= 30:
            return 0.86
    if age <= 25 and score >= 72 and group != "ST":
        return 1.12
    return 1.0


def score_factor(group: str, score: float) -> float:
    if group == "QB":
        if score >= 82:
            return 2.10 + ((score - 82) * 0.10)
        if score >= 76:
            return 1.35 + ((score - 76) * 0.12)
        if score >= 68:
            return 0.45 + ((score - 68) * 0.13)
        return 0.20 + max(0, score - 58) * 0.025
    if score >= 84:
        return 2.05 + ((score - 84) * 0.12)
    if score >= 78:
        return 1.45 + ((score - 78) * 0.10)
    if score >= 72:
        return 0.85 + ((score - 72) * 0.10)
    if score >= 66:
        return 0.42 + ((score - 66) * 0.07)
    return 0.25 + max(0, score - 58) * 0.025


def suggested_years(group: str, age: int | None, score: float) -> int:
    if group == "ST":
        return 1 if age and age >= 34 else 2
    if group == "RB":
        if age and age >= 29:
            return 1
        return 3 if score >= 78 else 2
    if age and age >= 33:
        return 1
    if age and age >= 30:
        return 2 if score >= 76 else 1
    if score >= 82:
        return 4
    if score >= 76:
        return 3
    if score >= 70:
        return 2
    return 1


def estimate_offer(row: sqlite3.Row) -> OfferEstimate:
    group = position_group(row["position"])
    score = player_score(row)
    age = int(row["age"]) if row["age"] is not None else None
    current_aav = int(row["aav"] or 0)

    if group == "ST":
        raw = BASE_AAV[group] * (0.8 + max(0, score - 60) * 0.045)
    else:
        raw = BASE_AAV.get(group, 2_000_000) * score_factor(group, score)
    raw *= age_factor(group, age, score)

    if current_aav > 0 and score >= 72:
        raw = max(raw, current_aav * (1.10 if age and age >= 30 else 1.20))
    elif current_aav > 0 and score >= 66:
        raw = max(raw, current_aav * 1.05)

    ask = rounded_money(raw)
    ask = max(MIN_AAV.get(group, 1_000_000), min(MAX_AAV.get(group, 18_000_000), ask))

    minimum = rounded_money(max(MIN_AAV.get(group, 1_000_000), ask * 0.78))
    tier = market_tier(group, score)
    years = suggested_years(group, age, score)
    guarantee_pct = 45 if score >= 82 else 35 if score >= 76 else 20 if score >= 70 else 10

    if score >= 80 and not (age and age >= 32 and group not in {"QB", "OT", "IOL"}):
        priority = "Priority"
        recommendation = "Try to retain before free agency."
    elif score >= 73:
        priority = "Negotiable"
        recommendation = "Keep if the price stays near the estimate."
    elif group == "ST":
        priority = "Low-cost"
        recommendation = "Fine to keep on short specialist terms."
    else:
        priority = "Optional"
        recommendation = "Let the market test him unless depth is thin."

    return OfferEstimate(
        tier=tier,
        priority=priority,
        recommendation=recommendation,
        suggested_years=years,
        asking_aav=ask,
        minimum_aav=minimum,
        guarantee_pct=guarantee_pct,
    )


def expiring_players(con: sqlite3.Connection, team: str | int, season: int) -> list[dict[str, Any]]:
    team = team_row(con, team)
    rows = con.execute(
        """
        SELECT
            c.contract_id,
            c.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.first_name,
            p.last_name,
            p.position,
            p.age,
            p.years_exp,
            p.status,
            p.overall,
            c.start_year,
            c.end_year,
            c.total_years,
            c.total_value,
            c.aav,
            c.contract_type,
            cy.cap_hit,
            cy.cash_due,
            (
                SELECT MAX(role_score)
                FROM player_role_scores prs
                WHERE prs.player_id = p.player_id
                  AND prs.scheme_key = 'default'
                  AND prs.season = ?
            ) AS best_role_score,
            (
                SELECT AVG(rating_value)
                FROM player_ratings pr
                WHERE pr.player_id = p.player_id
                  AND pr.season = ?
            ) AS avg_rating
        FROM contracts c
        JOIN players p ON p.player_id = c.player_id
        LEFT JOIN contract_years cy ON cy.contract_id = c.contract_id AND cy.season = ?
        WHERE c.team_id = ?
          AND p.team_id = c.team_id
          AND c.is_active = 1
          AND COALESCE(c.end_year, ?) <= ?
          AND NOT EXISTS (
              SELECT 1
              FROM contracts future
              WHERE future.player_id = c.player_id
                AND future.team_id = c.team_id
                AND future.is_active = 1
                AND COALESCE(future.start_year, ?) > ?
          )
        ORDER BY
            CASE p.status WHEN 'Active' THEN 0 WHEN 'Practice Squad' THEN 1 ELSE 2 END,
            COALESCE(best_role_score, avg_rating, p.overall, 60) DESC,
            c.aav DESC,
            player_name
        """,
        (season, season, season, int(team["team_id"]), season, season, season, season),
    ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        estimate = estimate_offer(row)
        score = player_score(row)
        item = dict(row)
        group = position_group(row["position"])
        current_aav = int(row["aav"] or 0)
        rights_type = rights_type_for_years_exp(row["years_exp"])
        tender_options: list[dict[str, Any]] = []
        if rights_type == "RFA":
            tender_options = rfa_tender_options(group, current_aav)
        elif rights_type == "ERFA":
            tender_options = [
                {
                    "type": "erfa",
                    "label": "ERFA Tender",
                    "aav": tag_tender_aav(group, current_aav, "erfa"),
                    "compensation": tender_compensation("erfa"),
                }
            ]
        item.update(
            {
                "team_id": int(team["team_id"]),
                "team": team["abbreviation"],
                "position_group": group,
                "rights_type": rights_type,
                "market_score": round(score, 1),
                "market_tier": estimate.tier,
                "priority": estimate.priority,
                "recommendation": estimate.recommendation,
                "suggested_years": estimate.suggested_years,
                "asking_aav": estimate.asking_aav,
                "minimum_aav": estimate.minimum_aav,
                "guarantee_pct": estimate.guarantee_pct,
                "franchise_tag_aav": tag_tender_aav(group, current_aav, "franchise"),
                "transition_tag_aav": tag_tender_aav(group, current_aav, "transition"),
                "rfa_tender_options": tender_options,
                "erfa_tender_aav": tag_tender_aav(group, current_aav, "erfa") if rights_type == "ERFA" else None,
                "extension_start_year": season + 1,
                "extension_end_year": season + estimate.suggested_years,
            }
        )
        result.append(item)
    return result


def cap_summary(con: sqlite3.Connection, team: str | int) -> dict[str, Any] | None:
    team = team_row(con, team)
    if not table_exists(con, "team_cap_view"):
        return None
    row = con.execute(
        "SELECT * FROM team_cap_view WHERE team_id = ?",
        (team["team_id"],),
    ).fetchone()
    return dict(row) if row else None


def projected_cap_summary(con: sqlite3.Connection, team: str | int, season: int) -> dict[str, Any] | None:
    team = team_row(con, team)
    if not table_exists(con, "contract_years"):
        setup_contract_years.ensure_schema(con)
    top51_row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'top_51_count'"
    ).fetchone()
    top51_count = int(top51_row["setting_value"]) if top51_row else 51
    row = con.execute(
        """
        WITH ranked AS (
            SELECT
                cy.contract_year_id,
                cy.cap_hit,
                ROW_NUMBER() OVER (
                    PARTITION BY cy.team_id, cy.season
                    ORDER BY cy.cap_hit DESC, p.first_name || ' ' || p.last_name, cy.player_id
                ) AS top51_rank
            FROM contract_years cy
            JOIN players p ON p.player_id = cy.player_id
            LEFT JOIN roster_status_types rst ON rst.status_code = p.status
            WHERE cy.team_id = ?
              AND cy.season = ?
              AND cy.is_active = 1
              AND p.team_id = cy.team_id
              AND COALESCE(rst.counts_against_top51, 1) = 1
        ),
        other_charges AS (
            SELECT COALESCE(SUM(amount), 0) AS amount
            FROM team_cap_charges
            WHERE team_id = ? AND season = ?
        )
        SELECT
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            ? AS season,
            (SELECT setting_value FROM game_settings WHERE setting_key = 'cap_accounting_mode') AS cap_accounting_mode,
            t.salary_cap,
            COUNT(r.contract_year_id) AS active_contracts,
            COALESCE(SUM(CASE WHEN r.top51_rank <= ? THEN r.cap_hit ELSE 0 END), 0) AS top51_cap_hit,
            COALESCE(MAX(CASE WHEN r.top51_rank = ? THEN r.cap_hit END), 0) AS top51_cutoff_cap_hit,
            (SELECT amount FROM other_charges) AS other_cap_charges,
            COALESCE(SUM(CASE WHEN r.top51_rank <= ? THEN r.cap_hit ELSE 0 END), 0)
                + (SELECT amount FROM other_charges) AS total_committed,
            t.salary_cap
                - (
                    COALESCE(SUM(CASE WHEN r.top51_rank <= ? THEN r.cap_hit ELSE 0 END), 0)
                    + (SELECT amount FROM other_charges)
                  ) AS cap_space,
            SUM(CASE WHEN r.top51_rank <= ? THEN 1 ELSE 0 END) AS contracts_counted
        FROM teams t
        LEFT JOIN ranked r ON 1 = 1
        WHERE t.team_id = ?
        GROUP BY t.team_id
        """,
        (
            int(team["team_id"]),
            season,
            int(team["team_id"]),
            season,
            season,
            top51_count,
            top51_count,
            top51_count,
            top51_count,
            top51_count,
            int(team["team_id"]),
        ),
    ).fetchone()
    return dict(row) if row else None


def cap_casualty_candidates(
    con: sqlite3.Connection,
    team: str | int,
    season: int,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    team = team_row(con, team)
    if not table_exists(con, "contract_years"):
        setup_contract_years.ensure_schema(con)
    top51_row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'top_51_count'"
    ).fetchone()
    top51_count = int(top51_row["setting_value"]) if top51_row else 51
    cap = projected_cap_summary(con, int(team["team_id"]), season) or {}
    cutoff = int(cap.get("top51_cutoff_cap_hit") or 0)
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT
                cy.*,
                c.start_year,
                c.end_year,
                c.aav,
                c.contract_type,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.age,
                p.years_exp,
                p.status,
                p.overall,
                ROW_NUMBER() OVER (
                    PARTITION BY cy.team_id, cy.season
                    ORDER BY cy.cap_hit DESC, p.first_name || ' ' || p.last_name, cy.player_id
                ) AS top51_rank,
                (
                    SELECT MAX(role_score)
                    FROM player_role_scores prs
                    WHERE prs.player_id = p.player_id
                      AND prs.scheme_key = 'default'
                      AND prs.season = ?
                ) AS best_role_score,
                (
                    SELECT AVG(rating_value)
                    FROM player_ratings pr
                    WHERE pr.player_id = p.player_id
                      AND pr.season = ?
                ) AS avg_rating
            FROM contract_years cy
            JOIN contracts c ON c.contract_id = cy.contract_id
            JOIN players p ON p.player_id = cy.player_id
            LEFT JOIN roster_status_types rst ON rst.status_code = p.status
            WHERE cy.team_id = ?
              AND cy.season = ?
              AND cy.is_active = 1
              AND c.is_active = 1
              AND COALESCE(c.start_year, ?) <= ?
              AND COALESCE(c.end_year, ?) >= ?
              AND p.team_id = cy.team_id
              AND COALESCE(rst.counts_against_top51, 1) = 1
        )
        SELECT *
        FROM ranked
        WHERE COALESCE(end_year, ?) >= ?
        ORDER BY
            (cap_hit - dead_cap_if_cut_pre_june1) DESC,
            cap_hit DESC,
            player_name
        LIMIT ?
        """,
        (
            season - 1,
            season - 1,
            int(team["team_id"]),
            season,
            season,
            season,
            season,
            season,
            season,
            season,
            limit * 3,
        ),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        gross_pre = int(row["cap_hit"] or 0) - int(row["dead_cap_if_cut_pre_june1"] or 0)
        gross_post = int(row["cap_hit"] or 0) - int(row["dead_cap_if_cut_post_june1_current"] or 0)
        replacement = cutoff if int(row["top51_rank"] or 999) <= top51_count else 0
        net_pre = gross_pre - replacement
        net_post = gross_post - replacement
        if gross_pre <= 0 and gross_post <= 0:
            continue
        score = player_score(row)
        item = dict(row)
        item.update(
            {
                "team_id": int(team["team_id"]),
                "team": team["abbreviation"],
                "season": season,
                "market_score": round(score, 1),
                "market_tier": market_tier(position_group(row["position"]), score),
                "position_group": position_group(row["position"]),
                "gross_savings_pre_june1": gross_pre,
                "gross_savings_post_june1": gross_post,
                "top51_replacement_estimate": replacement,
                "net_savings_pre_june1": net_pre,
                "net_savings_post_june1": net_post,
            }
        )
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            int(item.get("net_savings_pre_june1") or 0),
            int(item.get("gross_savings_pre_june1") or 0),
            int(item.get("cap_hit") or 0),
        ),
        reverse=True,
    )
    return candidates[:limit]


def restructure_candidates(
    con: sqlite3.Connection,
    team: str | int,
    season: int,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    team = team_row(con, team)
    if not table_exists(con, "contract_years") or not table_exists(con, "contract_restructures"):
        setup_contract_years.ensure_schema(con)
    rows = con.execute(
        """
        SELECT
            cy.*,
            c.start_year,
            c.end_year,
            c.aav,
            c.contract_type,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            p.status,
            p.overall,
            (
                SELECT MAX(role_score)
                FROM player_role_scores prs
                WHERE prs.player_id = p.player_id
                  AND prs.scheme_key = 'default'
                  AND prs.season = ?
            ) AS best_role_score,
            (
                SELECT AVG(rating_value)
                FROM player_ratings pr
                WHERE pr.player_id = p.player_id
                  AND pr.season = ?
            ) AS avg_rating
        FROM contract_years cy
        JOIN contracts c ON c.contract_id = cy.contract_id
        JOIN players p ON p.player_id = cy.player_id
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE cy.team_id = ?
          AND cy.season = ?
          AND cy.is_active = 1
          AND c.is_active = 1
          AND p.team_id = cy.team_id
          AND COALESCE(rst.counts_against_top51, 1) = 1
          AND COALESCE(c.end_year, ?) > ?
          AND cy.base_salary > ?
          AND NOT EXISTS (
              SELECT 1
              FROM contract_restructures rr
              WHERE rr.contract_id = cy.contract_id
                AND rr.restructure_season = ?
                AND rr.is_active = 1
          )
        ORDER BY cy.base_salary DESC, cy.cap_hit DESC, player_name
        LIMIT ?
        """,
        (
            season - 1,
            season - 1,
            int(team["team_id"]),
            season,
            season,
            season,
            MIN_RESTRUCTURE_BASE_FLOOR + MIN_RESTRUCTURE_SAVINGS,
            season,
            limit * 3,
        ),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        remaining_years = max(1, int(row["end_year"] or season) - season + 1)
        proration_years = min(5, remaining_years)
        max_convert = max(0, int(row["base_salary"] or 0) - MIN_RESTRUCTURE_BASE_FLOOR)
        suggested_convert = min(max_convert, rounded_money(int(row["base_salary"] or 0) * 0.60))
        if suggested_convert <= 0:
            continue
        current_proration = setup_contract_years.distribute_evenly(
            suggested_convert,
            proration_years,
        )[0]
        current_savings = suggested_convert - current_proration
        if current_savings < MIN_RESTRUCTURE_SAVINGS:
            continue
        score = player_score(row)
        item = dict(row)
        item.update(
            {
                "team_id": int(team["team_id"]),
                "team": team["abbreviation"],
                "season": season,
                "market_score": round(score, 1),
                "market_tier": market_tier(position_group(row["position"]), score),
                "position_group": position_group(row["position"]),
                "remaining_contract_years": remaining_years,
                "proration_years": proration_years,
                "suggested_convert": suggested_convert,
                "current_year_proration": current_proration,
                "estimated_current_savings": current_savings,
                "future_cap_added": suggested_convert - current_proration,
            }
        )
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            int(item.get("estimated_current_savings") or 0),
            int(item.get("base_salary") or 0),
            int(item.get("cap_hit") or 0),
        ),
        reverse=True,
    )
    return candidates[:limit]


def active_user_team(con: sqlite3.Connection) -> str | None:
    if not table_exists(con, "active_game_save_view"):
        return None
    row = con.execute("SELECT user_team FROM active_game_save_view LIMIT 1").fetchone()
    return str(row["user_team"]) if row and row["user_team"] else None


def set_current_contract_year(con: sqlite3.Connection, contract_year: int) -> None:
    con.execute(
        """
        INSERT INTO game_settings (setting_key, setting_value, updated_at)
        VALUES ('current_contract_year', ?, datetime('now'))
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = excluded.setting_value,
            updated_at = datetime('now')
        """,
        (str(contract_year),),
    )


def active_game_id(con: sqlite3.Connection) -> str | None:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT game_id FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["game_id"]:
            return str(row["game_id"])
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
    ).fetchone()
    return str(row["setting_value"]) if row and row["setting_value"] else None


def existing_future_contract(con: sqlite3.Connection, player_id: int, season: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM contracts
        WHERE player_id = ?
          AND is_active = 1
          AND COALESCE(start_year, ?) > ?
        ORDER BY start_year
        LIMIT 1
        """,
        (player_id, season, season),
    ).fetchone()


def tag_label(tag_type: str) -> str:
    value = str(tag_type or "").strip().lower().replace("_", "-")
    if value in {"transition", "transition-tag"}:
        return "Transition Tag"
    if value in {"exclusive", "exclusive-franchise", "exclusive-franchise-tag"}:
        return "Exclusive Franchise Tag"
    if value in {"franchise", "non-exclusive", "non-exclusive-franchise", "franchise-tag"}:
        return "Franchise Tag"
    if value in {"rfa-first", "rfa-first-round", "first-round", "first-round-tender"}:
        return "RFA First-Round Tender"
    if value in {"rfa-second", "rfa-second-round", "second-round", "second-round-tender"}:
        return "RFA Second-Round Tender"
    if value in {"rfa-original", "rfa-original-round", "original-round", "original-round-tender"}:
        return "RFA Original-Round Tender"
    if value in {"rfa-rofr", "rofr", "right-of-first-refusal", "right-of-first-refusal-tender"}:
        return "RFA Right-of-First-Refusal Tender"
    if value in {"erfa", "erfa-tender", "exclusive-rights", "exclusive-rights-tender"}:
        return "ERFA Tender"
    raise ValueError("Tender type must be franchise, exclusive, transition, rfa_first, rfa_second, rfa_original, rfa_rofr, or erfa.")


def tag_tender_aav(group: str, current_aav: int, tag_type: str) -> int:
    label = tag_label(tag_type)
    if label == "RFA First-Round Tender":
        return rounded_money(max(RFA_TENDER_AAV["first_round"], int(current_aav * 1.10)))
    if label == "RFA Second-Round Tender":
        return rounded_money(max(RFA_TENDER_AAV["second_round"], int(current_aav * 1.10)))
    if label == "RFA Original-Round Tender":
        return rounded_money(max(RFA_TENDER_AAV["original_round"], int(current_aav * 1.10)))
    if label == "RFA Right-of-First-Refusal Tender":
        return rounded_money(max(RFA_TENDER_AAV["rofr"], int(current_aav * 1.05)))
    if label == "ERFA Tender":
        return rounded_money(max(ERFA_TENDER_AAV.get(group, 1_000_000), int(current_aav * 1.03)))
    table = TRANSITION_TAG_AAV if label == "Transition Tag" else FRANCHISE_TAG_AAV
    tender = table.get(group, table.get("ST", 5_000_000))
    if label == "Exclusive Franchise Tag":
        tender = int(tender * 1.08)
    # NFL tender rule: the tag is the greater of the positional tender or 120% of
    # the player's prior-year salary. This uses AAV as the practical sim salary.
    return rounded_money(max(tender, int(current_aav * 1.20)))


def rights_type_for_years_exp(years_exp: Any) -> str:
    try:
        years = int(years_exp or 0)
    except (TypeError, ValueError):
        years = 0
    if years < 3:
        return "ERFA"
    if years == 3:
        return "RFA"
    return "UFA"


def tender_key(tag_type: str) -> str:
    label = tag_label(tag_type)
    return {
        "Franchise Tag": "franchise",
        "Exclusive Franchise Tag": "exclusive",
        "Transition Tag": "transition",
        "RFA First-Round Tender": "rfa_first",
        "RFA Second-Round Tender": "rfa_second",
        "RFA Original-Round Tender": "rfa_original",
        "RFA Right-of-First-Refusal Tender": "rfa_rofr",
        "ERFA Tender": "erfa",
    }[label]


def tender_compensation(tag_type: str) -> str:
    label = tag_label(tag_type)
    return {
        "Franchise Tag": "Two first-round picks if offer sheet is not matched",
        "Exclusive Franchise Tag": "No outside negotiation",
        "Transition Tag": "Right to match; no draft-pick compensation",
        "RFA First-Round Tender": "First-round pick compensation",
        "RFA Second-Round Tender": "Second-round pick compensation",
        "RFA Original-Round Tender": "Original-round pick compensation",
        "RFA Right-of-First-Refusal Tender": "Right to match; no draft-pick compensation",
        "ERFA Tender": "Exclusive rights retained",
    }[label]


def tender_contract_type(tag_type: str) -> str:
    return {
        "franchise": "FranchiseTag",
        "exclusive": "ExclusiveFranchiseTag",
        "transition": "TransitionTag",
        "rfa_first": "RFAFirstRoundTender",
        "rfa_second": "RFASecondRoundTender",
        "rfa_original": "RFAOriginalRoundTender",
        "rfa_rofr": "RFAROFRTender",
        "erfa": "ERFATender",
    }[tender_key(tag_type)]


def rfa_tender_options(group: str, current_aav: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "rfa_first",
            "label": "1st Round Tender",
            "aav": tag_tender_aav(group, current_aav, "rfa_first"),
            "compensation": tender_compensation("rfa_first"),
        },
        {
            "type": "rfa_second",
            "label": "2nd Round Tender",
            "aav": tag_tender_aav(group, current_aav, "rfa_second"),
            "compensation": tender_compensation("rfa_second"),
        },
        {
            "type": "rfa_original",
            "label": "Original Round Tender",
            "aav": tag_tender_aav(group, current_aav, "rfa_original"),
            "compensation": tender_compensation("rfa_original"),
        },
        {
            "type": "rfa_rofr",
            "label": "ROFR Tender",
            "aav": tag_tender_aav(group, current_aav, "rfa_rofr"),
            "compensation": tender_compensation("rfa_rofr"),
        },
    ]


def existing_team_tag(con: sqlite3.Connection, team_id: int, contract_year: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT c.*, p.first_name || ' ' || p.last_name AS player_name
        FROM contracts c
        JOIN players p ON p.player_id = c.player_id
        WHERE c.team_id = ?
          AND c.start_year = ?
          AND c.is_active = 1
          AND c.contract_type IN ('FranchiseTag', 'ExclusiveFranchiseTag', 'TransitionTag')
        ORDER BY c.contract_id
        LIMIT 1
        """,
        (team_id, contract_year),
    ).fetchone()


def tag_eligible_players(con: sqlite3.Connection, team: str | int, season: int) -> list[dict[str, Any]]:
    players = expiring_players(con, team, season)
    for item in players:
        group = str(item.get("position_group") or position_group(item.get("position")))
        current_aav = int(item.get("aav") or 0)
        franchise = tag_tender_aav(group, current_aav, "franchise")
        transition = tag_tender_aav(group, current_aav, "transition")
        score = float(item.get("market_score") or 60)
        item["franchise_tag_aav"] = franchise
        item["transition_tag_aav"] = transition
        item["tag_eligible"] = True
        item["tag_recommendation"] = (
            "Franchise tag candidate"
            if score >= 84 or (group in {"QB", "OT", "EDGE", "WR", "CB"} and score >= 80)
            else "Transition tag candidate"
            if score >= 76 and group not in {"RB", "ST"}
            else "Tag only if negotiations stall"
        )
    return players


def fifth_year_option_salary(group: str, current_aav: int, score: float) -> int:
    base = FIFTH_YEAR_OPTION_AAV.get(group, FIFTH_YEAR_OPTION_AAV["ST"])
    if score >= 90:
        base = max(base, TRANSITION_TAG_AAV.get(group, base))
    elif score >= 84:
        base = int(base * 1.12)
    elif score >= 76:
        base = int(base * 1.00)
    else:
        base = int(base * 0.78)
    return rounded_money(max(base, int(current_aav * 1.20)))


def fifth_year_option_candidates(con: sqlite3.Connection, team: str | int | None, league_year: int) -> list[dict[str, Any]]:
    ensure_contract_rights_schema(con)
    team_sql = ""
    params: list[Any] = [league_year - 1, league_year - 1, league_year - 3, league_year - 3, league_year]
    if team is not None:
        selected = team_row(con, team)
        team_sql = " AND c.team_id = ?"
        params.append(int(selected["team_id"]))
    rows = con.execute(
        f"""
        SELECT
            c.contract_id,
            c.player_id,
            c.team_id,
            t.abbreviation AS team,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            p.overall,
            c.start_year,
            c.end_year,
            c.aav,
            c.total_value,
            c.option_year,
            c.option_exercised,
            (
                SELECT MAX(role_score)
                FROM player_role_scores prs
                WHERE prs.player_id = p.player_id
                  AND prs.scheme_key = 'default'
                  AND prs.season = ?
            ) AS best_role_score,
            (
                SELECT AVG(rating_value)
                FROM player_ratings pr
                WHERE pr.player_id = p.player_id
                  AND pr.season = ?
            ) AS avg_rating
        FROM contracts c
        JOIN players p ON p.player_id = c.player_id
        JOIN teams t ON t.team_id = c.team_id
        WHERE c.is_active = 1
          AND c.contract_type = 'RookieScale'
          AND COALESCE(c.option_year, 0) = 1
          AND COALESCE(c.option_exercised, 0) = 0
          AND COALESCE(c.start_year, ?) = ?
          AND p.team_id = c.team_id
          AND NOT EXISTS (
              SELECT 1 FROM fifth_year_option_decisions d
              WHERE d.league_year = ?
                AND d.player_id = c.player_id
                AND d.team_id = c.team_id
          )
          {team_sql}
        ORDER BY t.abbreviation, COALESCE(best_role_score, avg_rating, p.overall, 60) DESC, player_name
        """,
        params,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        score = player_score(row)
        group = position_group(row["position"])
        salary = fifth_year_option_salary(group, int(row["aav"] or 0), score)
        recommendation = "Exercise" if score >= 76 or group == "QB" and score >= 70 else "Decline"
        result.append(
            {
                **dict(row),
                "position_group": group,
                "market_score": round(score, 1),
                "option_salary": salary,
                "option_season": league_year + 1,
                "recommendation": recommendation,
            }
        )
    return result


def exercise_fifth_year_option(
    con: sqlite3.Connection,
    *,
    team: str | int,
    league_year: int,
    player_id: int,
    apply: bool,
    force: bool = False,
    quiet: bool = False,
    rebuild_all_contracts: bool = False,
    sync_cap: bool = True,
    write_cap_snapshot: bool = True,
) -> int | None:
    team_row_value = team_row(con, team)
    candidates = fifth_year_option_candidates(con, int(team_row_value["team_id"]), league_year)
    target = next((row for row in candidates if int(row["player_id"]) == int(player_id)), None)
    if not target:
        raise ValueError("Player is not an eligible fifth-year option candidate for this league year.")
    option_season = int(target["option_season"])
    salary = int(target["option_salary"])
    projected = projected_cap_summary(con, int(team_row_value["team_id"]), option_season) or {}
    cap_space = int(projected.get("cap_space") or 0)
    if cap_space < salary and not force:
        raise ValueError(
            f"{team_row_value['abbreviation']} lacks projected {option_season} cap room for the fifth-year option "
            f"({money(salary)}). Use --force to override."
        )
    if not apply:
        print(
            "Dry run only. Add --apply to exercise "
            f"{target['player_name']}'s fifth-year option for {option_season} at {money(salary)}."
        )
        return None

    setup_contract_years.ensure_schema(con)
    ensure_contract_rights_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    signed_date = current_game_date(con)
    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year,
            total_value, total_years, aav, signing_bonus,
            roster_bonus, workout_bonus, is_guaranteed,
            dead_cap_current, dead_cap_next, no_trade_clause,
            option_year, option_exercised, franchise_tag, contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, 0, 0, 1, 0, 0, 0, 1, 1, NULL, 'FifthYearOption', 1)
        """,
        (
            player_id,
            int(team_row_value["team_id"]),
            signed_date,
            option_season,
            option_season,
            salary,
            salary,
        ),
    )
    option_contract_id = int(cur.lastrowid)
    con.execute(
        "UPDATE contracts SET option_exercised = 1 WHERE contract_id = ?",
        (int(target["contract_id"]),),
    )
    con.execute(
        """
        INSERT INTO fifth_year_option_decisions (
            league_year, team_id, player_id, rookie_contract_id, option_contract_id,
            decision, option_salary, decision_date, reason
        )
        VALUES (?, ?, ?, ?, ?, 'exercised', ?, ?, ?)
        ON CONFLICT(league_year, team_id, player_id) DO UPDATE SET
            option_contract_id = excluded.option_contract_id,
            decision = 'exercised',
            option_salary = excluded.option_salary,
            decision_date = excluded.decision_date,
            reason = excluded.reason
        """,
        (
            league_year,
            int(team_row_value["team_id"]),
            player_id,
            int(target["contract_id"]),
            option_contract_id,
            salary,
            signed_date,
            f"Projected role score {target['market_score']}; {target['recommendation'].lower()} recommendation.",
        ),
    )
    if rebuild_all_contracts:
        setup_contract_years.rebuild_contract_years(con)
    else:
        setup_contract_years.rebuild_contract_year(con, option_contract_id)
        setup_contract_years.rebuild_contract_year(con, int(target["contract_id"]))
    if sync_cap:
        setup_contract_years.sync_team_cap_space(con)
    transaction_id, _ = setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=signed_date,
        season=league_year,
        phase=current_phase(con),
        transaction_type="Fifth-Year Option",
        team_id=int(team_row_value["team_id"]),
        player_id=player_id,
        contract_id=option_contract_id,
        to_team_id=int(team_row_value["team_id"]),
        cap_delta_next=salary,
        cash_delta=salary,
        description=f"{team_row_value['abbreviation']} exercised {target['player_name']}'s fifth-year option for {option_season} at {money(salary)}.",
        source=SOURCE,
        external_ref=f"fifth_year_option:{league_year}:{team_row_value['team_id']}:{player_id}",
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
            option_contract_id,
            int(team_row_value["team_id"]),
            salary,
            option_season,
            "Fully guaranteed fifth-year option.",
        ),
    )
    if write_cap_snapshot:
        setup_transactions_cap_ledger.snapshot_cap_ledger(
            con,
            label=f"after_transaction_{transaction_id}_fifth_year_option",
            phase=current_phase(con),
            source=SOURCE,
            replace=True,
        )
    if not quiet:
        print(f"{team_row_value['abbreviation']} exercised {target['player_name']}'s fifth-year option: {money(salary)} for {option_season}.")
    return option_contract_id


def decline_fifth_year_option(
    con: sqlite3.Connection,
    *,
    team: str | int,
    league_year: int,
    player_id: int,
    apply: bool,
    quiet: bool = False,
) -> None:
    team_row_value = team_row(con, team)
    candidates = fifth_year_option_candidates(con, int(team_row_value["team_id"]), league_year)
    target = next((row for row in candidates if int(row["player_id"]) == int(player_id)), None)
    if not target:
        raise ValueError("Player is not an eligible fifth-year option candidate for this league year.")
    if not apply:
        print(f"Dry run only. Add --apply to decline {target['player_name']}'s fifth-year option.")
        return
    ensure_contract_rights_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    decision_date = current_game_date(con)
    con.execute(
        """
        INSERT INTO fifth_year_option_decisions (
            league_year, team_id, player_id, rookie_contract_id,
            decision, option_salary, decision_date, reason
        )
        VALUES (?, ?, ?, ?, 'declined', ?, ?, ?)
        ON CONFLICT(league_year, team_id, player_id) DO UPDATE SET
            decision = 'declined',
            option_salary = excluded.option_salary,
            decision_date = excluded.decision_date,
            reason = excluded.reason
        """,
        (
            league_year,
            int(team_row_value["team_id"]),
            player_id,
            int(target["contract_id"]),
            int(target["option_salary"]),
            decision_date,
            f"Projected role score {target['market_score']}; {target['recommendation'].lower()} recommendation.",
        ),
    )
    setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=decision_date,
        season=league_year,
        phase=current_phase(con),
        transaction_type="Fifth-Year Option",
        team_id=int(team_row_value["team_id"]),
        player_id=player_id,
        contract_id=int(target["contract_id"]),
        description=f"{team_row_value['abbreviation']} declined {target['player_name']}'s fifth-year option.",
        source=SOURCE,
        external_ref=f"fifth_year_option_declined:{league_year}:{team_row_value['team_id']}:{player_id}",
    )
    if not quiet:
        print(f"{team_row_value['abbreviation']} declined {target['player_name']}'s fifth-year option.")


def compute_team_cap_space_for_season(con: sqlite3.Connection, team_id: int, season: int) -> dict[str, int]:
    setup_contract_years.ensure_schema(con)
    top51_row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'top_51_count'"
    ).fetchone()
    top51_count = int(top51_row["setting_value"]) if top51_row else 51
    cap_row = con.execute("SELECT COALESCE(salary_cap, 0) AS salary_cap FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    base_cap = int(cap_row["salary_cap"] if cap_row else 0)
    rollover_row = con.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS amount
        FROM salary_cap_rollovers
        WHERE team_id = ?
          AND to_season = ?
          AND elected = 1
        """,
        (team_id, season),
    ).fetchone()
    rollover = int(rollover_row["amount"] if rollover_row else 0)
    cap_hits = [
        int(row["cap_hit"] or 0)
        for row in con.execute(
            """
            SELECT cy.cap_hit
            FROM contract_years cy
            JOIN players p ON p.player_id = cy.player_id
            LEFT JOIN roster_status_types rst ON rst.status_code = p.status
            WHERE cy.team_id = ?
              AND cy.season = ?
              AND cy.is_active = 1
              AND p.team_id = cy.team_id
              AND COALESCE(rst.counts_against_top51, 1) = 1
            ORDER BY cy.cap_hit DESC
            """,
            (team_id, season),
        ).fetchall()
    ]
    top51 = sum(cap_hits[:top51_count])
    charges_row = con.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS amount
        FROM team_cap_charges
        WHERE team_id = ?
          AND season = ?
        """,
        (team_id, season),
    ).fetchone()
    other = int(charges_row["amount"] if charges_row else 0)
    salary_cap = base_cap + rollover
    committed = top51 + other
    return {
        "base_salary_cap": base_cap,
        "rollover_amount": rollover,
        "salary_cap": salary_cap,
        "top51_cap_hit": top51,
        "other_cap_charges": other,
        "total_committed": committed,
        "cap_space": salary_cap - committed,
    }


def process_cap_rollover(
    con: sqlite3.Connection,
    *,
    from_season: int,
    to_season: int | None = None,
    apply: bool,
    max_rollover: int = MAX_SINGLE_YEAR_ROLLOVER,
    quiet: bool = False,
) -> dict[str, Any]:
    to_season = int(to_season or from_season + 1)
    setup_contract_years.ensure_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    teams = con.execute("SELECT team_id, abbreviation FROM teams ORDER BY team_id").fetchall()
    rows: list[dict[str, Any]] = []
    for team in teams:
        summary = compute_team_cap_space_for_season(con, int(team["team_id"]), from_season)
        raw_space = int(summary["cap_space"])
        rollover = min(max(0, raw_space), int(max_rollover))
        item = {
            "team_id": int(team["team_id"]),
            "team": str(team["abbreviation"]),
            "from_season": from_season,
            "to_season": to_season,
            "cap_space": raw_space,
            "rollover_amount": rollover,
        }
        rows.append(item)
        if apply:
            con.execute(
                """
                INSERT INTO salary_cap_rollovers (
                    team_id, from_season, to_season, amount, elected, source, notes
                )
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(team_id, from_season, to_season) DO UPDATE SET
                    amount = excluded.amount,
                    elected = 1,
                    source = excluded.source,
                    notes = excluded.notes,
                    created_at = datetime('now')
                """,
                (
                    int(team["team_id"]),
                    from_season,
                    to_season,
                    rollover,
                    SOURCE,
                    f"Unused {from_season} cap carried into {to_season}. Single-year sim cap: {money(max_rollover)}.",
                ),
            )
            if rollover:
                setup_transactions_cap_ledger.insert_transaction(
                    con,
                    transaction_date=current_game_date(con),
                    season=to_season,
                    phase=current_phase(con),
                    transaction_type="Cap Rollover",
                    team_id=int(team["team_id"]),
                    cap_delta_current=rollover,
                    description=f"{team['abbreviation']} rolled {money(rollover)} of unused {from_season} cap into {to_season}.",
                    source=SOURCE,
                    external_ref=f"cap_rollover:{from_season}:{to_season}:{team['team_id']}",
                )
    if apply:
        setup_contract_years.sync_team_cap_space(con)
    if not quiet:
        mode = "processed" if apply else "dry run"
        print(f"Cap rollover {mode}: {from_season} -> {to_season}.")
        for row in rows[:32]:
            print(f"  {row['team']}: {money(row['rollover_amount'])} from {money(row['cap_space'])} unused space")
    return {"from_season": from_season, "to_season": to_season, "teams": rows, "applied": apply}


def compensation_round_for_tender(tender_type: str) -> int | None:
    key = tender_key(tender_type)
    if key == "rfa_first":
        return 1
    if key == "rfa_second":
        return 2
    if key == "rfa_original":
        return None
    return None


def active_tender_for_player(con: sqlite3.Connection, player_id: int, league_year: int) -> sqlite3.Row | None:
    ensure_contract_rights_schema(con)
    return con.execute(
        """
        SELECT rt.*, t.abbreviation AS original_team
        FROM contract_rights_tenders rt
        JOIN teams t ON t.team_id = rt.team_id
        WHERE rt.player_id = ?
          AND rt.league_year = ?
          AND rt.status = 'applied'
        ORDER BY rt.tender_id DESC
        LIMIT 1
        """,
        (player_id, league_year),
    ).fetchone()


def submit_offer_sheet(
    con: sqlite3.Connection,
    *,
    league_year: int,
    offering_team: str | int,
    player_id: int,
    years: int,
    aav: int,
    signing_bonus: int,
    guarantee_pct: int,
    apply: bool,
    quiet: bool = False,
) -> int | None:
    offering = team_row(con, offering_team)
    tender = active_tender_for_player(con, player_id, league_year)
    if not tender:
        raise ValueError("Player does not have an active RFA or transition tender for this league year.")
    tender_type = str(tender["tender_type"])
    if tender_type == "erfa":
        raise ValueError("ERFA players cannot negotiate with other teams.")
    if int(tender["team_id"]) == int(offering["team_id"]):
        raise ValueError("Offering team already controls this player's rights.")
    if years < 1 or years > 6:
        raise ValueError("Offer sheet years must be between 1 and 6.")
    if aav <= 0:
        raise ValueError("Offer sheet AAV must be positive.")
    if not apply:
        print(
            "Dry run only. Add --apply to submit offer sheet "
            f"for player {player_id}: {years} yr, {money(aav)} AAV from {offering['abbreviation']}."
        )
        return None
    ensure_contract_rights_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    submitted_date = current_game_date(con)
    comp_round = compensation_round_for_tender(tender_type)
    cur = con.execute(
        """
        INSERT INTO free_agency_offer_sheets (
            league_year, player_id, original_team_id, offering_team_id,
            tender_type, compensation, compensation_round, years, aav,
            signing_bonus, guarantee_pct, status, submitted_date, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_match', ?, ?)
        """,
        (
            league_year,
            player_id,
            int(tender["team_id"]),
            int(offering["team_id"]),
            tender_type,
            tender["compensation"],
            comp_round,
            years,
            aav,
            signing_bonus,
            guarantee_pct,
            submitted_date,
            "Original team has simulated five-day match window.",
        ),
    )
    offer_sheet_id = int(cur.lastrowid)
    setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=submitted_date,
        season=league_year,
        phase=current_phase(con),
        transaction_type="Offer Sheet",
        team_id=int(offering["team_id"]),
        secondary_team_id=int(tender["team_id"]),
        player_id=player_id,
        description=f"{offering['abbreviation']} submitted an offer sheet for player {player_id}: {years} yr, {money(aav)} AAV.",
        source=SOURCE,
        external_ref=f"offer_sheet_submitted:{league_year}:{offer_sheet_id}",
    )
    if not quiet:
        print(f"Offer sheet submitted: id={offer_sheet_id}, {offering['abbreviation']} player={player_id}, {years} yr at {money(aav)} AAV.")
    return offer_sheet_id


def create_contract_from_offer_sheet(
    con: sqlite3.Connection,
    *,
    team_id: int,
    player_id: int,
    league_year: int,
    years: int,
    aav: int,
    signing_bonus: int,
    guarantee_pct: int,
) -> int:
    total_value = int(aav) * int(years)
    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year,
            total_value, total_years, aav, signing_bonus,
            roster_bonus, workout_bonus, is_guaranteed,
            dead_cap_current, dead_cap_next, no_trade_clause,
            option_year, option_exercised, franchise_tag, contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 0, 0, 0, 0, NULL, 'OfferSheet', 1)
        """,
        (
            player_id,
            team_id,
            current_game_date(con),
            league_year,
            league_year + int(years) - 1,
            total_value,
            years,
            aav,
            signing_bonus,
            1 if int(guarantee_pct or 0) >= 50 else 0,
        ),
    )
    contract_id = int(cur.lastrowid)
    setup_contract_years.rebuild_contract_year(con, contract_id)
    return contract_id


def award_offer_sheet_compensation(
    con: sqlite3.Connection,
    *,
    from_team_id: int,
    to_team_id: int,
    league_year: int,
    round_number: int | None,
) -> int | None:
    if not round_number:
        return None
    pick = con.execute(
        """
        SELECT pick_id
        FROM draft_picks
        WHERE draft_year = ?
          AND round = ?
          AND original_team_id = ?
          AND current_team_id = ?
          AND COALESCE(is_used, 0) = 0
        ORDER BY COALESCE(pick_number, pick_id), pick_id
        LIMIT 1
        """,
        (league_year, round_number, from_team_id, from_team_id),
    ).fetchone()
    if not pick:
        return None
    con.execute(
        """
        UPDATE draft_picks
        SET current_team_id = ?,
            is_traded = 1,
            trade_note = ?
        WHERE pick_id = ?
        """,
        (
            to_team_id,
            f"Offer sheet compensation, {league_year} round {round_number}.",
            int(pick["pick_id"]),
        ),
    )
    return int(pick["pick_id"])


def resolve_offer_sheet(
    con: sqlite3.Connection,
    *,
    offer_sheet_id: int,
    match: bool,
    apply: bool,
    quiet: bool = False,
) -> int | None:
    ensure_contract_rights_schema(con)
    offer = con.execute(
        """
        SELECT os.*, p.team_id AS current_team_id,
               p.first_name || ' ' || p.last_name AS player_name,
               orig.abbreviation AS original_team,
               offer.abbreviation AS offering_team
        FROM free_agency_offer_sheets os
        JOIN players p ON p.player_id = os.player_id
        JOIN teams orig ON orig.team_id = os.original_team_id
        JOIN teams offer ON offer.team_id = os.offering_team_id
        WHERE os.offer_sheet_id = ?
        """,
        (offer_sheet_id,),
    ).fetchone()
    if not offer:
        raise ValueError("Unknown offer sheet.")
    if str(offer["status"]) != "pending_match":
        raise ValueError("Offer sheet has already been resolved.")
    if not apply:
        action = "match" if match else "decline"
        print(f"Dry run only. Add --apply to {action} offer sheet {offer_sheet_id}.")
        return None

    league_year = int(offer["league_year"])
    player_id = int(offer["player_id"])
    original_team_id = int(offer["original_team_id"])
    offering_team_id = int(offer["offering_team_id"])
    contract_team_id = original_team_id if match else offering_team_id
    con.execute(
        """
        UPDATE contracts
        SET is_active = 0
        WHERE player_id = ?
          AND team_id = ?
          AND is_active = 1
          AND contract_type IN ('RFAFirstRoundTender', 'RFASecondRoundTender', 'RFAOriginalRoundTender', 'RFAROFRTender', 'TransitionTag')
          AND start_year = ?
        """,
        (player_id, original_team_id, league_year),
    )
    contract_id = create_contract_from_offer_sheet(
        con,
        team_id=contract_team_id,
        player_id=player_id,
        league_year=league_year,
        years=int(offer["years"]),
        aav=int(offer["aav"]),
        signing_bonus=int(offer["signing_bonus"] or 0),
        guarantee_pct=int(offer["guarantee_pct"] or 0),
    )
    comp_pick_id = None
    if not match:
        con.execute(
            "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
            (offering_team_id, player_id),
        )
        comp_pick_id = award_offer_sheet_compensation(
            con,
            from_team_id=offering_team_id,
            to_team_id=original_team_id,
            league_year=league_year,
            round_number=int(offer["compensation_round"]) if offer["compensation_round"] is not None else None,
        )
    else:
        con.execute(
            "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
            (original_team_id, player_id),
        )
    con.execute(
        """
        UPDATE free_agency_offer_sheets
        SET status = ?,
            decision_date = ?,
            matched = ?,
            resulting_contract_id = ?,
            updated_at = datetime('now')
        WHERE offer_sheet_id = ?
        """,
        ("matched" if match else "declined", current_game_date(con), 1 if match else 0, contract_id, offer_sheet_id),
    )
    con.execute(
        """
        UPDATE contract_rights_tenders
        SET status = ?
        WHERE player_id = ?
          AND league_year = ?
          AND team_id = ?
        """,
        ("matched_offer_sheet" if match else "lost_offer_sheet", player_id, league_year, original_team_id),
    )
    setup_contract_years.sync_team_cap_space(con)
    setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=current_game_date(con),
        season=league_year,
        phase=current_phase(con),
        transaction_type="Offer Sheet",
        team_id=contract_team_id,
        secondary_team_id=offering_team_id if match else original_team_id,
        player_id=player_id,
        contract_id=contract_id,
        from_team_id=original_team_id if not match else None,
        to_team_id=contract_team_id,
        cash_delta=int(offer["aav"]) * int(offer["years"]),
        description=(
            f"{offer['original_team']} matched {offer['offering_team']}'s offer sheet for {offer['player_name']}."
            if match
            else f"{offer['original_team']} declined to match {offer['offering_team']}'s offer sheet for {offer['player_name']}."
        ),
        source=SOURCE,
        external_ref=f"offer_sheet_resolved:{offer_sheet_id}",
    )
    if not quiet:
        result = "matched" if match else "declined"
        comp = f"; compensation pick {comp_pick_id}" if comp_pick_id else ""
        print(f"Offer sheet {offer_sheet_id} {result}; contract_id={contract_id}{comp}.")
    return contract_id


def apply_tag(
    con: sqlite3.Connection,
    *,
    team: str | int,
    season: int,
    player_id: int,
    tag_type: str,
    apply: bool,
    force: bool = False,
    target_player: dict[str, Any] | sqlite3.Row | None = None,
    skip_cap_check: bool = False,
    quiet: bool = False,
    rebuild_all_contracts: bool = False,
    sync_cap: bool = True,
    write_cap_snapshot: bool = True,
) -> int | None:
    team = team_row(con, team)
    contract_year = season + 1
    label = tag_label(tag_type)
    tender_type = tender_key(tag_type)
    contract_type = tender_contract_type(tag_type)
    is_tag = tender_type in {"franchise", "exclusive", "transition"}
    if is_tag:
        existing = existing_team_tag(con, int(team["team_id"]), contract_year)
        if existing:
            raise ValueError(
                f"{team['abbreviation']} already used a tag on {existing['player_name']} for {contract_year}."
            )
    target = target_player
    if target is not None and int(target["player_id"]) != int(player_id):
        raise ValueError("Preloaded tender target does not match player_id.")
    if target is None:
        target = next(
            (row for row in tag_eligible_players(con, int(team["team_id"]), season) if int(row["player_id"]) == int(player_id)),
            None,
        )
    if not target:
        raise ValueError("Player is not an eligible expiring contract for this team/season.")
    future = existing_future_contract(con, player_id, season)
    if future:
        raise ValueError("Player already has a future active contract.")

    group = str(target["position_group"])
    score = float(target["market_score"] or 60)
    rights_type = str(target.get("rights_type") or rights_type_for_years_exp(target.get("years_exp")))
    if tender_type.startswith("rfa_") and rights_type != "RFA" and not force:
        raise ValueError("RFA tenders are only available for expiring players with three accrued seasons. Use --force to override.")
    if tender_type == "erfa" and rights_type != "ERFA" and not force:
        raise ValueError("ERFA tenders are only available for expiring players with fewer than three accrued seasons. Use --force to override.")
    if is_tag and rights_type != "UFA" and not force:
        raise ValueError("Franchise and transition tags are reserved for UFA-level expiring players. Use --force to override.")
    tag_aav = tag_tender_aav(group, int(target["aav"] or 0), tag_type)
    if not skip_cap_check:
        projected = projected_cap_summary(con, int(team["team_id"]), contract_year) or {}
        cap_space = int(projected.get("cap_space") or 0)
        if cap_space < tag_aav and not force:
            raise ValueError(
                f"{team['abbreviation']} lacks projected {contract_year} cap room for a {label} tender "
                f"({money(tag_aav)}). Use --force to override."
            )
    if label == "Franchise Tag" and group == "RB" and score < 86 and not force:
        raise ValueError("RB franchise tags are restricted to elite backs. Use --force to override.")
    if label == "Transition Tag" and score < 74 and not force:
        raise ValueError("Transition tags should be reserved for credible starters. Use --force to override.")
    if tender_type.startswith("rfa_") and score < 58 and not force:
        raise ValueError("RFA tenders should be reserved for players with a realistic roster path. Use --force to override.")

    if not apply:
        print(
            "Dry run only. Add --apply to place the "
            f"{label} on {target['player_name']} for {contract_year} at {money(tag_aav)}."
        )
        return None

    setup_contract_years.ensure_schema(con)
    ensure_contract_rights_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    signed_date = current_game_date(con)
    before = {} if skip_cap_check else (projected_cap_summary(con, int(team["team_id"]), contract_year) or {})
    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year,
            total_value, total_years, aav, signing_bonus,
            roster_bonus, workout_bonus, is_guaranteed,
            dead_cap_current, dead_cap_next, franchise_tag, contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, 0, 0, 1, 0, 0, ?, ?, 1)
        """,
        (
            player_id,
            int(team["team_id"]),
            signed_date,
            contract_year,
            contract_year,
            tag_aav,
            tag_aav,
            "Transition"
            if contract_type == "TransitionTag"
            else "Exclusive"
            if contract_type == "ExclusiveFranchiseTag"
            else tender_type
            if tender_type in {"rfa_first", "rfa_second", "rfa_original", "rfa_rofr", "erfa"}
            else "Non-Exclusive",
            contract_type,
        ),
    )
    contract_id = int(cur.lastrowid)
    if rebuild_all_contracts:
        setup_contract_years.rebuild_contract_years(con)
    else:
        setup_contract_years.rebuild_contract_year(con, contract_id)
    if sync_cap:
        setup_contract_years.sync_team_cap_space(con)
    after = {} if skip_cap_check else (projected_cap_summary(con, int(team["team_id"]), contract_year) or {})
    future_delta = tag_aav if skip_cap_check else int((after.get("total_committed") or 0) - (before.get("total_committed") or 0))
    transaction_id, _ = setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=signed_date,
        season=contract_year,
        phase=current_phase(con),
        transaction_type="Rights Tender" if not is_tag else "Franchise Tag",
        team_id=int(team["team_id"]),
        player_id=player_id,
        contract_id=contract_id,
        to_team_id=int(team["team_id"]),
        old_status=target["status"],
        new_status=target["status"],
        cap_delta_current=0,
        cap_delta_next=future_delta,
        cash_delta=tag_aav,
        description=f"{team['abbreviation']} placed the {label} on {target['player_name']} at {money(tag_aav)}.",
        source=SOURCE,
        external_ref=f"tag:{contract_year}:{team['team_id']}:{player_id}:{contract_type}",
    )
    con.execute(
        """
        INSERT INTO contract_rights_tenders (
            league_year, team_id, player_id, rights_type, tender_type,
            compensation, aav, status, applied_contract_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'applied', ?)
        ON CONFLICT(league_year, team_id, player_id) DO UPDATE SET
            rights_type = excluded.rights_type,
            tender_type = excluded.tender_type,
            compensation = excluded.compensation,
            aav = excluded.aav,
            status = 'applied',
            applied_contract_id = excluded.applied_contract_id,
            created_at = datetime('now')
        """,
        (
            contract_year,
            int(team["team_id"]),
            player_id,
            rights_type,
            tender_type,
            tender_compensation(tag_type),
            tag_aav,
            contract_id,
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
            int(team["team_id"]),
            tag_aav,
            contract_year,
            f"{label} one-year tender.",
        ),
    )
    if write_cap_snapshot:
        setup_transactions_cap_ledger.snapshot_cap_ledger(
            con,
            label=f"after_transaction_{transaction_id}_tag",
            phase=current_phase(con),
            source=SOURCE,
            replace=True,
        )
    if not quiet:
        verb = "tagged" if is_tag else "tendered"
        print(f"{team['abbreviation']} {verb} {target['player_name']}: {label}, {money(tag_aav)} for {contract_year}.")
    return contract_id


def extend_player(
    con: sqlite3.Connection,
    *,
    team: str | int,
    season: int,
    player_id: int,
    years: int | None,
    aav: int | None,
    signing_bonus: int,
    apply: bool,
    force: bool,
    quiet: bool = False,
    rebuild_all_contracts: bool = True,
    sync_cap: bool = True,
    write_cap_snapshot: bool = True,
) -> int | None:
    team = team_row(con, team)
    players = expiring_players(con, int(team["team_id"]), season)
    target = next((row for row in players if int(row["player_id"]) == int(player_id)), None)
    if not target:
        future = existing_future_contract(con, player_id, season)
        if future:
            raise ValueError("Player already has a future active contract.")
        raise ValueError("Player is not an eligible expiring contract for this team/season.")

    chosen_years = int(years or target["suggested_years"])
    chosen_aav = int(aav or target["asking_aav"])
    if chosen_years < 1 or chosen_years > 6:
        raise ValueError("Extension years must be between 1 and 6.")
    if chosen_aav < int(target["minimum_aav"]) and not force:
        raise ValueError(
            f"Offer is below estimated minimum ({money(target['minimum_aav'])}). Use --force to override."
        )

    start_year = season + 1
    end_year = start_year + chosen_years - 1
    total_value = chosen_aav * chosen_years
    guaranteed = 1 if int(target["guarantee_pct"] or 0) >= 35 else 0

    if not apply:
        print(
            "Dry run only. Add --apply to extend "
            f"{target['player_name']} for {chosen_years} year(s), {money(chosen_aav)} AAV."
        )
        return None

    setup_contract_years.ensure_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    before = projected_cap_summary(con, int(team["team_id"]), start_year) or {}
    signed_date = current_game_date(con)
    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year,
            total_value, total_years, aav, signing_bonus,
            roster_bonus, workout_bonus, is_guaranteed,
            dead_cap_current, dead_cap_next, contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 0, 'Extension', 1)
        """,
        (
            player_id,
            int(team["team_id"]),
            signed_date,
            start_year,
            end_year,
            total_value,
            chosen_years,
            chosen_aav,
            signing_bonus,
            guaranteed,
        ),
    )
    contract_id = int(cur.lastrowid)
    if rebuild_all_contracts:
        setup_contract_years.rebuild_contract_years(con)
    else:
        setup_contract_years.rebuild_contract_year(con, contract_id)
    if sync_cap:
        setup_contract_years.sync_team_cap_space(con)
    after = projected_cap_summary(con, int(team["team_id"]), start_year) or {}
    future_delta = int((after.get("total_committed") or 0) - (before.get("total_committed") or 0))

    transaction_id, _ = setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=signed_date,
        season=season,
        phase=current_phase(con),
        transaction_type="Extension",
        team_id=int(team["team_id"]),
        player_id=player_id,
        contract_id=contract_id,
        to_team_id=int(team["team_id"]),
        old_status=target["status"],
        new_status=target["status"],
        cap_delta_current=0,
        cap_delta_next=future_delta,
        cash_delta=total_value,
        description=(
            f"{team['abbreviation']} extended {target['player_name']} "
            f"for {chosen_years} year(s), {money(chosen_aav)} AAV."
        ),
        source=SOURCE,
        external_ref=f"extension:{player_id}:{contract_id}",
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
            int(team["team_id"]),
            chosen_aav,
            start_year,
            "Own-team contract extension.",
        ),
    )
    if write_cap_snapshot:
        setup_transactions_cap_ledger.snapshot_cap_ledger(
            con,
            label=f"after_transaction_{transaction_id}_extension",
            phase=current_phase(con),
            source=SOURCE,
            replace=True,
        )
    if not quiet:
        print(
            f"Extended {target['player_name']} with {team['abbreviation']}: "
            f"{chosen_years} year(s), {money(chosen_aav)} AAV, starts {start_year}. "
            f"Projected {start_year} Top 51 space: {money(after.get('cap_space'))}."
        )
    return contract_id


def release_player(
    con: sqlite3.Connection,
    *,
    team: str | int,
    season: int,
    player_id: int,
    post_june1: bool,
    apply: bool,
    force: bool,
    rebuild_all_contracts: bool = True,
    sync_cap: bool = True,
    write_cap_snapshot: bool = True,
    quiet: bool = False,
) -> None:
    team = team_row(con, team)
    contract_year = season + 1
    candidates = [] if force else cap_casualty_candidates(con, int(team["team_id"]), contract_year, limit=200)
    target = next((row for row in candidates if int(row["player_id"]) == int(player_id)), None)
    if not target and not force:
        raise ValueError("Player is not a projected cap-casualty candidate. Use --force to override.")
    if not target:
        row = con.execute(
            """
            SELECT
                cy.*,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.age,
                p.status,
                p.overall,
                c.end_year,
                c.aav,
                NULL AS best_role_score,
                NULL AS avg_rating
            FROM contract_years cy
            JOIN contracts c ON c.contract_id = cy.contract_id
            JOIN players p ON p.player_id = cy.player_id
            WHERE cy.player_id = ?
              AND cy.team_id = ?
              AND cy.season = ?
              AND cy.is_active = 1
              AND c.is_active = 1
              AND p.team_id = cy.team_id
            ORDER BY cy.cap_hit DESC
            LIMIT 1
            """,
            (player_id, int(team["team_id"]), contract_year),
        ).fetchone()
        if not row:
            raise ValueError("No active projected contract year found for that player.")
        target = dict(row)
        target["team"] = team["abbreviation"]

    dead_current = int(
        target["dead_cap_if_cut_post_june1_current"]
        if post_june1
        else target["dead_cap_if_cut_pre_june1"]
    )
    dead_next = int(target["dead_cap_if_cut_post_june1_next"] or 0) if post_june1 else 0
    before = (projected_cap_summary(con, int(team["team_id"]), contract_year) or {}) if sync_cap else {}
    if not apply:
        savings = int(target["cap_hit"] or 0) - dead_current
        print(
            "Dry run only. Add --apply to release "
            f"{target['player_name']} for projected {contract_year} cap savings around {money(savings)}."
        )
        return

    setup_transactions_cap_ledger.ensure_schema(con)
    signed_date = current_game_date(con)
    con.execute(
        """
        UPDATE contracts
        SET is_active = 0
        WHERE player_id = ? AND team_id = ? AND is_active = 1
        """,
        (player_id, int(team["team_id"])),
    )
    if dead_current:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                int(team["team_id"]),
                contract_year,
                f"Dead cap from releasing {target['player_name']}.",
                dead_current,
                player_id,
                SOURCE,
            ),
        )
    if dead_next:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                int(team["team_id"]),
                contract_year + 1,
                f"Post-June 1 dead cap from releasing {target['player_name']}.",
                dead_next,
                player_id,
                SOURCE,
            ),
        )
    con.execute(
        "UPDATE players SET team_id = NULL, status = 'Free Agent' WHERE player_id = ?",
        (player_id,),
    )
    if table_exists(con, "depth_charts"):
        con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player_id,))
    roster_actions.upsert_basic_free_agent_profile(con, player_id)
    if rebuild_all_contracts:
        setup_contract_years.rebuild_contract_years(con)
    elif table_exists(con, "contract_years"):
        con.execute(
            "UPDATE contract_years SET is_active = 0 WHERE player_id = ? AND team_id = ?",
            (player_id, int(team["team_id"])),
        )
    if sync_cap:
        setup_contract_years.sync_team_cap_space(con)
        after = projected_cap_summary(con, int(team["team_id"]), contract_year) or {}
        cap_delta = int((after.get("total_committed") or 0) - (before.get("total_committed") or 0))
    else:
        after = {}
        cap_delta = dead_current - int(target["cap_hit"] or 0)
    transaction_id, _ = setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=signed_date,
        season=contract_year,
        phase=current_phase(con),
        transaction_type="Release",
        team_id=int(team["team_id"]),
        player_id=player_id,
        contract_id=int(target["contract_id"]),
        from_team_id=int(team["team_id"]),
        old_status=target.get("status"),
        new_status="Free Agent",
        cap_delta_current=cap_delta,
        cash_delta=0,
        description=(
            f"{team['abbreviation']} released {target['player_name']} as a projected "
            f"{contract_year} cap casualty. Dead cap: {money(dead_current)}."
        ),
        source=SOURCE,
        external_ref=f"cap_casualty_release:{contract_year}:{player_id}:{target['contract_id']}",
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id,
            from_team_id, amount, season, asset_description
        )
        VALUES (?, 'ReleasedPlayer', ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            player_id,
            int(target["contract_id"]),
            int(team["team_id"]),
            dead_current,
            contract_year,
            "Projected offseason cap-casualty release.",
        ),
    )
    if write_cap_snapshot:
        setup_transactions_cap_ledger.snapshot_cap_ledger(
            con,
            label=f"after_transaction_{transaction_id}_cap_casualty_release",
            phase=current_phase(con),
            source=SOURCE,
            replace=True,
        )
    if not quiet:
        print(
            f"Released {target['player_name']} from {team['abbreviation']}. "
            f"Projected {contract_year} Top 51 delta: {money(cap_delta)}. "
            f"Projected space: {money(after.get('cap_space'))}."
        )


def recalc_contract_dead_cap(con: sqlite3.Connection, contract_id: int) -> None:
    rows = con.execute(
        """
        SELECT *
        FROM contract_years
        WHERE contract_id = ?
        ORDER BY season
        """,
        (contract_id,),
    ).fetchall()
    for index, row in enumerate(rows):
        remaining_rows = rows[index:]
        future_rows = rows[index + 1 :]
        remaining_proration = sum(
            int(item["signing_bonus_proration"] or 0) + int(item["option_bonus_proration"] or 0)
            for item in remaining_rows
        )
        future_proration = sum(
            int(item["signing_bonus_proration"] or 0) + int(item["option_bonus_proration"] or 0)
            for item in future_rows
        )
        remaining_guarantees = sum(int(item["guaranteed_salary"] or 0) for item in remaining_rows)
        future_guarantees = sum(int(item["guaranteed_salary"] or 0) for item in future_rows)
        current_guarantees = int(row["guaranteed_salary"] or 0)
        current_proration = int(row["signing_bonus_proration"] or 0) + int(row["option_bonus_proration"] or 0)
        con.execute(
            """
            UPDATE contract_years
            SET dead_cap_if_cut_pre_june1 = ?,
                dead_cap_if_cut_post_june1_current = ?,
                dead_cap_if_cut_post_june1_next = ?,
                updated_at = datetime('now')
            WHERE contract_year_id = ?
            """,
            (
                remaining_proration + remaining_guarantees,
                current_proration + current_guarantees,
                future_proration + future_guarantees,
                int(row["contract_year_id"]),
            ),
        )


def apply_restructure_to_contract_years(
    con: sqlite3.Connection,
    *,
    contract_id: int,
    restructure_season: int,
    converted_salary: int,
    proration_years: int,
) -> None:
    rows = con.execute(
        """
        SELECT *
        FROM contract_years
        WHERE contract_id = ?
          AND season >= ?
          AND is_active = 1
        ORDER BY season
        LIMIT ?
        """,
        (contract_id, restructure_season, proration_years),
    ).fetchall()
    if not rows:
        raise ValueError("No contract-year rows available to apply restructure.")
    allocations = setup_contract_years.distribute_evenly(converted_salary, len(rows))
    first = rows[0]
    if int(first["base_salary"] or 0) < converted_salary:
        raise ValueError("Restructure amount is larger than available projected base salary.")
    con.execute(
        """
        UPDATE contract_years
        SET base_salary = base_salary - ?,
            signing_bonus_proration = signing_bonus_proration + ?,
            guaranteed_salary = MAX(0, guaranteed_salary - ?),
            cap_hit = cap_hit - ? + ?,
            source = 'manual_restructure',
            notes = COALESCE(notes || ' ', '') || ?,
            updated_at = datetime('now')
        WHERE contract_year_id = ?
        """,
        (
            converted_salary,
            allocations[0],
            converted_salary,
            converted_salary,
            allocations[0],
            f"Restructure applied in {restructure_season}.",
            int(first["contract_year_id"]),
        ),
    )
    for row, proration in zip(rows[1:], allocations[1:]):
        con.execute(
            """
            UPDATE contract_years
            SET signing_bonus_proration = signing_bonus_proration + ?,
                cap_hit = cap_hit + ?,
                source = 'manual_restructure',
                notes = COALESCE(notes || ' ', '') || ?,
                updated_at = datetime('now')
            WHERE contract_year_id = ?
            """,
            (
                proration,
                proration,
                f"Restructure proration from {restructure_season}.",
                int(row["contract_year_id"]),
            ),
        )
    recalc_contract_dead_cap(con, contract_id)


def restructure_player(
    con: sqlite3.Connection,
    *,
    team: str | int,
    season: int,
    player_id: int,
    amount: int | None,
    apply: bool,
    force: bool,
) -> None:
    team = team_row(con, team)
    contract_year = season + 1
    candidates = [] if force else restructure_candidates(con, int(team["team_id"]), contract_year, limit=200)
    target = next((row for row in candidates if int(row["player_id"]) == int(player_id)), None)
    if not target and not force:
        raise ValueError("Player is not a projected restructure candidate. Use --force to override.")
    if not target:
        row = con.execute(
            """
            SELECT
                cy.*,
                c.start_year,
                c.end_year,
                c.aav,
                c.contract_type,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.age,
                p.status,
                p.overall,
                NULL AS best_role_score,
                NULL AS avg_rating
            FROM contract_years cy
            JOIN contracts c ON c.contract_id = cy.contract_id
            JOIN players p ON p.player_id = cy.player_id
            WHERE cy.player_id = ?
              AND cy.team_id = ?
              AND cy.season = ?
              AND cy.is_active = 1
              AND c.is_active = 1
              AND p.team_id = cy.team_id
            ORDER BY cy.base_salary DESC
            LIMIT 1
            """,
            (player_id, int(team["team_id"]), contract_year),
        ).fetchone()
        if not row:
            raise ValueError("No active projected contract year found for that player.")
        remaining_years = max(1, int(row["end_year"] or contract_year) - contract_year + 1)
        proration_years = min(5, remaining_years)
        suggested_convert = max(
            0,
            min(
                int(row["base_salary"] or 0) - MIN_RESTRUCTURE_BASE_FLOOR,
                rounded_money(int(row["base_salary"] or 0) * 0.60),
            ),
        )
        current_proration = setup_contract_years.distribute_evenly(
            suggested_convert,
            proration_years,
        )[0] if suggested_convert else 0
        target = dict(row)
        target.update(
            {
                "team": team["abbreviation"],
                "proration_years": proration_years,
                "suggested_convert": suggested_convert,
                "current_year_proration": current_proration,
                "estimated_current_savings": suggested_convert - current_proration,
            }
        )

    existing = con.execute(
        """
        SELECT restructure_id
        FROM contract_restructures
        WHERE contract_id = ?
          AND restructure_season = ?
          AND is_active = 1
        """,
        (int(target["contract_id"]), contract_year),
    ).fetchone()
    if existing:
        raise ValueError("That contract has already been restructured for this contract year.")

    proration_years = max(1, min(int(target["proration_years"] or 1), 5))
    max_convert = max(0, int(target["base_salary"] or 0) - MIN_RESTRUCTURE_BASE_FLOOR)
    converted = min(amount or int(target["suggested_convert"] or 0), max_convert)
    converted = min(rounded_money(converted), max_convert)
    if converted <= 0:
        raise ValueError("No restructure amount is available for that player.")
    current_proration = setup_contract_years.distribute_evenly(converted, proration_years)[0]
    estimated_savings = converted - current_proration
    before = projected_cap_summary(con, int(team["team_id"]), contract_year) or {}
    before_next = projected_cap_summary(con, int(team["team_id"]), contract_year + 1) or {}
    if not apply:
        print(
            "Dry run only. Add --apply to restructure "
            f"{target['player_name']}: convert {money(converted)} into bonus over "
            f"{proration_years} year(s), projected {contract_year} savings about "
            f"{money(estimated_savings)}."
        )
        return

    setup_contract_years.ensure_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    signed_date = current_game_date(con)
    con.execute(
        """
        INSERT INTO contract_restructures (
            contract_id, player_id, team_id, restructure_season, converted_salary,
            proration_years, current_year_proration, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(target["contract_id"]),
            player_id,
            int(team["team_id"]),
            contract_year,
            converted,
            proration_years,
            current_proration,
            SOURCE,
            "Converted base salary to prorated signing bonus for cap relief.",
        ),
    )
    apply_restructure_to_contract_years(
        con,
        contract_id=int(target["contract_id"]),
        restructure_season=contract_year,
        converted_salary=converted,
        proration_years=proration_years,
    )
    setup_contract_years.sync_team_cap_space(con)
    after = projected_cap_summary(con, int(team["team_id"]), contract_year) or {}
    after_next = projected_cap_summary(con, int(team["team_id"]), contract_year + 1) or {}
    cap_delta_current = int((after.get("total_committed") or 0) - (before.get("total_committed") or 0))
    cap_delta_next = int((after_next.get("total_committed") or 0) - (before_next.get("total_committed") or 0))
    transaction_id, _ = setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=signed_date,
        season=contract_year,
        phase=current_phase(con),
        transaction_type="Restructure",
        team_id=int(team["team_id"]),
        player_id=player_id,
        contract_id=int(target["contract_id"]),
        from_team_id=int(team["team_id"]),
        to_team_id=int(team["team_id"]),
        old_status=target.get("status"),
        new_status=target.get("status"),
        cap_delta_current=cap_delta_current,
        cap_delta_next=cap_delta_next,
        cash_delta=0,
        description=(
            f"{team['abbreviation']} restructured {target['player_name']}, converting "
            f"{money(converted)} of {contract_year} salary into prorated bonus."
        ),
        source=SOURCE,
        external_ref=f"restructure:{contract_year}:{player_id}:{target['contract_id']}",
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id,
            from_team_id, to_team_id, amount, season, asset_description
        )
        VALUES (?, 'RestructuredContract', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            player_id,
            int(target["contract_id"]),
            int(team["team_id"]),
            int(team["team_id"]),
            converted,
            contract_year,
            f"Converted salary to bonus over {proration_years} year(s).",
        ),
    )
    setup_transactions_cap_ledger.snapshot_cap_ledger(
        con,
        label=f"after_transaction_{transaction_id}_restructure",
        phase=current_phase(con),
        source=SOURCE,
        replace=True,
    )
    print(
        f"Restructured {target['player_name']} with {team['abbreviation']}: "
        f"converted {money(converted)} over {proration_years} year(s). "
        f"Projected {contract_year} Top 51 delta: {money(cap_delta_current)}. "
        f"Projected space: {money(after.get('cap_space'))}."
    )


def expiring_contract_rows(
    con: sqlite3.Connection,
    *,
    expiring_season: int,
    team: str | int | None = None,
) -> list[sqlite3.Row]:
    team_filter = ""
    params: list[Any] = [expiring_season, expiring_season, expiring_season]
    if team is not None:
        selected_team = team_row(con, team)
        team_filter = " AND latest.team_id = ?"
        params.append(int(selected_team["team_id"]))
    rows = con.execute(
        f"""
        WITH latest AS (
            SELECT
                c.*,
                p.first_name || ' ' || p.last_name AS player_name,
                p.status AS player_status,
                p.position,
                t.abbreviation AS team,
                ROW_NUMBER() OVER (
                    PARTITION BY c.player_id
                    ORDER BY COALESCE(c.end_year, ?) DESC, c.contract_id DESC
                ) AS rn
            FROM contracts c
            JOIN players p ON p.player_id = c.player_id
            JOIN teams t ON t.team_id = c.team_id
            WHERE c.is_active = 1
              AND p.team_id = c.team_id
        )
        SELECT *
        FROM latest
        WHERE rn = 1
          AND COALESCE(end_year, ?) <= ?
          {team_filter}
          AND NOT EXISTS (
              SELECT 1
              FROM contracts future
              WHERE future.player_id = latest.player_id
                AND future.team_id = latest.team_id
                AND future.is_active = 1
                AND COALESCE(future.start_year, ?) > ?
          )
        ORDER BY team, player_name
        """,
        (*params, expiring_season, expiring_season),
    ).fetchall()
    return list(rows)


def process_expired_contracts(
    con: sqlite3.Connection,
    *,
    expiring_season: int,
    contract_league_year: int | None = None,
    transaction_date: str | None = None,
    team: str | int | None = None,
    write_cap_snapshot: bool = True,
) -> dict[str, Any]:
    contract_year = int(contract_league_year or expiring_season + 1)
    transaction_date = transaction_date or current_game_date(con)
    setup_contract_years.ensure_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    set_current_contract_year(con, contract_year)

    rows = expiring_contract_rows(con, expiring_season=expiring_season, team=team)
    processed = 0
    by_team: dict[str, int] = {}
    expired_contract_ids: list[int] = []
    for row in rows:
        player_id = int(row["player_id"])
        team_id = int(row["team_id"])
        contract_id = int(row["contract_id"])
        player_name = str(row["player_name"])
        team_abbr = str(row["team"])
        old_status = str(row["player_status"] or "Active")

        con.execute(
            """
            UPDATE contracts
            SET is_active = 0
            WHERE player_id = ?
              AND team_id = ?
              AND is_active = 1
              AND COALESCE(end_year, ?) <= ?
            """,
            (player_id, team_id, expiring_season, expiring_season),
        )
        con.execute(
            "UPDATE players SET team_id = NULL, status = 'Free Agent' WHERE player_id = ?",
            (player_id,),
        )
        if table_exists(con, "depth_charts"):
            con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player_id,))
        if table_exists(con, "player_roster_status_history"):
            con.execute(
                """
                INSERT INTO player_roster_status_history (
                    player_id, old_status, new_status, effective_date, season, reason
                )
                VALUES (?, ?, 'Free Agent', ?, ?, ?)
                """,
                (
                    player_id,
                    old_status,
                    transaction_date,
                    contract_year,
                    f"{team_abbr} contract expired after {expiring_season}.",
                ),
            )

        roster_actions.upsert_basic_free_agent_profile(con, player_id, ensure_ratings=False)
        if table_exists(con, "free_agent_profiles"):
            con.execute(
                """
                UPDATE free_agent_profiles
                SET previous_team = ?,
                    motivation = COALESCE(motivation, 'contract_expired'),
                    signing_notes = ?,
                    updated_at = datetime('now')
                WHERE player_id = ?
                """,
                (
                    team_abbr,
                    f"Contract expired with {team_abbr} after {expiring_season}.",
                    player_id,
                ),
            )

        transaction_id, _ = setup_transactions_cap_ledger.insert_transaction(
            con,
            transaction_date=transaction_date,
            season=contract_year,
            phase=current_phase(con),
            transaction_type="Contract Expired",
            team_id=team_id,
            player_id=player_id,
            contract_id=contract_id,
            from_team_id=team_id,
            old_status=old_status,
            new_status="Free Agent",
            description=f"{player_name}'s contract with {team_abbr} expired after {expiring_season}.",
            source=SOURCE,
            external_ref=f"contract_expired:{expiring_season}:{player_id}:{contract_id}",
        )
        con.execute(
            """
            INSERT INTO transaction_assets (
                transaction_id, asset_type, player_id, contract_id,
                from_team_id, season, asset_description
            )
            VALUES (?, 'ExpiredContract', ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                player_id,
                contract_id,
                team_id,
                contract_year,
                "Expired contract moved player to free agency.",
            ),
        )
        processed += 1
        expired_contract_ids.append(contract_id)
        by_team[team_abbr] = by_team.get(team_abbr, 0) + 1

    if expired_contract_ids:
        con.executemany(
            "UPDATE contract_years SET is_active = 0 WHERE contract_id = ?",
            [(contract_id,) for contract_id in expired_contract_ids],
        )
    setup_contract_years.sync_team_cap_space(con)
    if write_cap_snapshot:
        setup_transactions_cap_ledger.snapshot_cap_ledger(
            con,
            label=f"after_contract_expiration_{expiring_season}",
            phase=current_phase(con),
            source=SOURCE,
            replace=True,
        )

    game_id = active_game_id(con)
    if game_id and table_exists(con, "game_flow_log"):
        con.execute(
            """
            INSERT INTO game_flow_log (
                game_id, game_date, log_type, event_code, title, details
            )
            VALUES (?, ?, 'CONTRACT_EXPIRATION', 'CONTRACTS_EXPIRED', ?, ?)
            """,
            (
                game_id,
                transaction_date,
                "Expired contracts processed",
                f"{processed} player(s) moved to free agency for contract year {contract_year}.",
            ),
        )

    return {
        "expiringSeason": expiring_season,
        "contractLeagueYear": contract_year,
        "processed": processed,
        "teams": by_team,
    }


def print_list(con: sqlite3.Connection, team: str | int, season: int) -> None:
    team = team_row(con, team)
    rows = expiring_players(con, int(team["team_id"]), season)
    cap = projected_cap_summary(con, int(team["team_id"]), season + 1) or {}
    print(f"{team['abbreviation']} expiring contracts after {season}")
    print(f"Projected {season + 1} Top 51 cap space: {money(cap.get('cap_space'))}")
    print(f"{len(rows)} player(s) need a decision before free agency.\n")
    for row in rows:
        print(
            f"{row['player_id']:>5}  {row['player_name']:<24} {row['position']:<4} "
            f"{row['market_tier']:<12} {row['priority']:<11} "
            f"Ask {money(row['asking_aav']):>8} x {row['suggested_years']}  "
            f"Current {money(row['aav']):>8}  {row['recommendation']}"
        )


def print_expiration_summary(result: dict[str, Any]) -> None:
    print(
        f"Expired contracts after {result['expiringSeason']} "
        f"for contract year {result['contractLeagueYear']}: "
        f"{result['processed']} player(s)."
    )
    for team, count in sorted(result.get("teams", {}).items()):
        print(f"  {team}: {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Own-team contract negotiation tools.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List expiring contracts for one team.")
    list_parser.add_argument("--team", help="Team abbreviation. Defaults to active save user team.")
    list_parser.add_argument("--season", type=int, required=True)
    list_parser.set_defaults(func=action_list)

    extend_parser = subparsers.add_parser("extend", help="Extend one own-team expiring player.")
    extend_parser.add_argument("--team", help="Team abbreviation. Defaults to active save user team.")
    extend_parser.add_argument("--season", type=int, required=True)
    extend_parser.add_argument("--player-id", type=int, required=True)
    extend_parser.add_argument("--years", type=int)
    extend_parser.add_argument("--aav")
    extend_parser.add_argument("--bonus", default="0")
    extend_parser.add_argument("--force", action="store_true")
    extend_parser.add_argument(
        "--fast",
        action="store_true",
        help="Only rebuild this contract's derived rows and skip the cap-ledger snapshot.",
    )
    extend_parser.add_argument(
        "--no-full-rebuild",
        action="store_true",
        help="Only rebuild this contract's derived contract_years rows.",
    )
    extend_parser.add_argument(
        "--no-cap-snapshot",
        action="store_true",
        help="Skip writing a full cap-ledger snapshot after the extension.",
    )
    extend_parser.add_argument("--apply", action="store_true")
    extend_parser.set_defaults(func=action_extend)

    tag_parser = subparsers.add_parser("tag", help="Use a franchise/transition tag or RFA/ERFA tender on one own-team expiring player.")
    tag_parser.add_argument("--team", help="Team abbreviation. Defaults to active save user team.")
    tag_parser.add_argument("--season", type=int, required=True, help="Season after which the current contract expires.")
    tag_parser.add_argument("--player-id", type=int, required=True)
    tag_parser.add_argument(
        "--tag-type",
        choices=["franchise", "exclusive", "transition", "rfa_first", "rfa_second", "rfa_original", "rfa_rofr", "erfa"],
        default="franchise",
    )
    tag_parser.add_argument("--force", action="store_true")
    tag_parser.add_argument("--fast", action="store_true", help="Only rebuild this contract and skip the cap-ledger snapshot.")
    tag_parser.add_argument("--no-full-rebuild", action="store_true", help="Only rebuild this contract's derived rows.")
    tag_parser.add_argument("--no-cap-snapshot", action="store_true")
    tag_parser.add_argument("--apply", action="store_true")
    tag_parser.set_defaults(func=action_tag)

    option_list = subparsers.add_parser("option-list", help="List fifth-year option candidates.")
    option_list.add_argument("--team", help="Optional team abbreviation. Defaults to all teams.")
    option_list.add_argument("--league-year", type=int, required=True, help="League year when the option decision is due.")
    option_list.set_defaults(func=action_option_list)

    option_exercise = subparsers.add_parser("option-exercise", help="Exercise a first-round rookie fifth-year option.")
    option_exercise.add_argument("--team", help="Team abbreviation. Defaults to active save user team.")
    option_exercise.add_argument("--league-year", type=int, required=True)
    option_exercise.add_argument("--player-id", type=int, required=True)
    option_exercise.add_argument("--force", action="store_true")
    option_exercise.add_argument("--fast", action="store_true")
    option_exercise.add_argument("--no-full-rebuild", action="store_true")
    option_exercise.add_argument("--no-cap-snapshot", action="store_true")
    option_exercise.add_argument("--apply", action="store_true")
    option_exercise.set_defaults(func=action_option_exercise)

    option_decline = subparsers.add_parser("option-decline", help="Decline a first-round rookie fifth-year option.")
    option_decline.add_argument("--team", help="Team abbreviation. Defaults to active save user team.")
    option_decline.add_argument("--league-year", type=int, required=True)
    option_decline.add_argument("--player-id", type=int, required=True)
    option_decline.add_argument("--apply", action="store_true")
    option_decline.set_defaults(func=action_option_decline)

    rollover_parser = subparsers.add_parser("cap-rollover", help="Carry unused cap room into the next league year.")
    rollover_parser.add_argument("--from-season", type=int, required=True)
    rollover_parser.add_argument("--to-season", type=int)
    rollover_parser.add_argument("--max-rollover", default=str(MAX_SINGLE_YEAR_ROLLOVER))
    rollover_parser.add_argument("--apply", action="store_true")
    rollover_parser.set_defaults(func=action_rollover)

    offer_sheet = subparsers.add_parser("offer-sheet", help="Submit an RFA/transition offer sheet.")
    offer_sheet.add_argument("--league-year", type=int, required=True)
    offer_sheet.add_argument("--offering-team", required=True)
    offer_sheet.add_argument("--player-id", type=int, required=True)
    offer_sheet.add_argument("--years", type=int, required=True)
    offer_sheet.add_argument("--aav", required=True)
    offer_sheet.add_argument("--bonus", default="0")
    offer_sheet.add_argument("--guarantee-pct", type=int, default=0)
    offer_sheet.add_argument("--apply", action="store_true")
    offer_sheet.set_defaults(func=action_offer_sheet_submit)

    resolve_sheet = subparsers.add_parser("resolve-offer-sheet", help="Match or decline a pending offer sheet.")
    resolve_sheet.add_argument("--offer-sheet-id", type=int, required=True)
    decision = resolve_sheet.add_mutually_exclusive_group(required=True)
    decision.add_argument("--match", action="store_true")
    decision.add_argument("--decline", action="store_true")
    resolve_sheet.add_argument("--apply", action="store_true")
    resolve_sheet.set_defaults(func=action_offer_sheet_resolve)

    expire_parser = subparsers.add_parser("expire", help="Move unextended expired contracts into free agency.")
    expire_parser.add_argument("--team", help="Optional team abbreviation. Defaults to all teams.")
    expire_parser.add_argument("--expiring-season", type=int, required=True)
    expire_parser.add_argument("--league-year", type=int, help="New contract league year. Defaults to expiring season + 1.")
    expire_parser.add_argument("--date", help="Transaction date. Defaults to current game date.")
    expire_parser.add_argument("--apply", action="store_true")
    expire_parser.set_defaults(func=action_expire)

    release_parser = subparsers.add_parser("release", help="Release a projected cap-casualty candidate.")
    release_parser.add_argument("--team", help="Team abbreviation. Defaults to active save user team.")
    release_parser.add_argument("--season", type=int, required=True)
    release_parser.add_argument("--player-id", type=int, required=True)
    release_parser.add_argument("--post-june1", action="store_true")
    release_parser.add_argument("--force", action="store_true")
    release_parser.add_argument("--apply", action="store_true")
    release_parser.set_defaults(func=action_release)

    restructure_parser = subparsers.add_parser("restructure", help="Restructure a contract to move cap to future years.")
    restructure_parser.add_argument("--team", help="Team abbreviation. Defaults to active save user team.")
    restructure_parser.add_argument("--season", type=int, required=True)
    restructure_parser.add_argument("--player-id", type=int, required=True)
    restructure_parser.add_argument("--amount", help="Salary amount to convert. Defaults to suggested conversion.")
    restructure_parser.add_argument("--force", action="store_true")
    restructure_parser.add_argument("--apply", action="store_true")
    restructure_parser.set_defaults(func=action_restructure)
    return parser


def team_arg(con: sqlite3.Connection, value: str | None) -> str:
    team = value or active_user_team(con)
    if not team:
        raise ValueError("Provide --team or use an active save with a user team.")
    return team


def action_list(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    print_list(con, team_arg(con, args.team), args.season)


def action_extend(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    extend_player(
        con,
        team=team_arg(con, args.team),
        season=args.season,
        player_id=args.player_id,
        years=args.years,
        aav=parse_money(args.aav),
        signing_bonus=parse_money(args.bonus) or 0,
        apply=args.apply,
        force=args.force,
        rebuild_all_contracts=not (args.fast or args.no_full_rebuild),
        write_cap_snapshot=not (args.fast or args.no_cap_snapshot),
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_tag(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    apply_tag(
        con,
        team=team_arg(con, args.team),
        season=args.season,
        player_id=args.player_id,
        tag_type=args.tag_type,
        apply=args.apply,
        force=args.force,
        rebuild_all_contracts=not (args.fast or args.no_full_rebuild),
        write_cap_snapshot=not (args.fast or args.no_cap_snapshot),
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_option_list(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    rows = fifth_year_option_candidates(con, args.team, args.league_year)
    print(f"Fifth-year option candidates for {args.league_year}: {len(rows)}")
    for row in rows:
        print(
            f"{row['team']:>3} {row['player_id']:>5} {row['player_name']:<24} "
            f"{row['position']:<4} score {row['market_score']:>4} "
            f"option {money(row['option_salary']):>8} {row['recommendation']}"
        )


def action_option_exercise(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    exercise_fifth_year_option(
        con,
        team=team_arg(con, args.team),
        league_year=args.league_year,
        player_id=args.player_id,
        apply=args.apply,
        force=args.force,
        rebuild_all_contracts=not (args.fast or args.no_full_rebuild),
        write_cap_snapshot=not (args.fast or args.no_cap_snapshot),
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_option_decline(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    decline_fifth_year_option(
        con,
        team=team_arg(con, args.team),
        league_year=args.league_year,
        player_id=args.player_id,
        apply=args.apply,
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_rollover(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    process_cap_rollover(
        con,
        from_season=args.from_season,
        to_season=args.to_season,
        apply=args.apply,
        max_rollover=parse_money(args.max_rollover) or MAX_SINGLE_YEAR_ROLLOVER,
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_offer_sheet_submit(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    submit_offer_sheet(
        con,
        league_year=args.league_year,
        offering_team=args.offering_team,
        player_id=args.player_id,
        years=args.years,
        aav=parse_money(args.aav) or 0,
        signing_bonus=parse_money(args.bonus) or 0,
        guarantee_pct=args.guarantee_pct,
        apply=args.apply,
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_offer_sheet_resolve(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    resolve_offer_sheet(
        con,
        offer_sheet_id=args.offer_sheet_id,
        match=args.match,
        apply=args.apply,
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_release(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    release_player(
        con,
        team=team_arg(con, args.team),
        season=args.season,
        player_id=args.player_id,
        post_june1=args.post_june1,
        apply=args.apply,
        force=args.force,
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_restructure(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    restructure_player(
        con,
        team=team_arg(con, args.team),
        season=args.season,
        player_id=args.player_id,
        amount=parse_money(args.amount),
        apply=args.apply,
        force=args.force,
    )
    if args.apply:
        con.commit()
    else:
        con.rollback()


def action_expire(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    result = process_expired_contracts(
        con,
        expiring_season=args.expiring_season,
        contract_league_year=args.league_year,
        transaction_date=args.date,
        team=args.team,
    )
    print_expiration_summary(result)
    if args.apply:
        con.commit()
    else:
        con.rollback()
        print("Dry run only. Add --apply to move players to free agency.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = connect(db_path)
    try:
        args.func(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
