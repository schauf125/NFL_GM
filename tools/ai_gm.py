#!/usr/bin/env python3
"""Local LLM AI GM orchestrator for NFL GM Sim.

This module is deliberately conservative. The LLM can propose structured
front-office decisions, but this tool validates the JSON and records it as
advisory output. It does not directly mutate rosters, contracts, cap tables,
or draft inventory.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import roster_rules
import trade_engine as te


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026
DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 2000
DEFAULT_TIMEOUT_SEC = 120

ADVISORY_DECISION_TYPES = {
    "camp_cutdown_recommendation": {
        "label": "Camp cutdown recommendations",
        "target_rule_phase": "Regular Season",
        "allowed_actions": {
            "recommend_release",
            "recommend_waive",
            "recommend_trade",
            "recommend_practice_squad",
            "hold_on_roster",
        },
    },
    "practice_squad_priorities": {
        "label": "Practice squad priorities",
        "target_rule_phase": "Regular Season",
        "allowed_actions": {
            "practice_squad_priority",
            "protect_from_claims",
            "low_priority_practice_squad",
        },
    },
    "trade_block_update": {
        "label": "Trade block generation",
        "target_rule_phase": None,
        "allowed_actions": {
            "add_to_trade_block",
            "hold_from_trade_block",
            "explore_trade_market",
        },
    },
    "extension_interest": {
        "label": "Extension interest",
        "target_rule_phase": None,
        "allowed_actions": {
            "explore_extension",
            "defer_extension",
            "monitor_extension_market",
        },
    },
    "free_agent_shortlist": {
        "label": "Free-agent shortlist",
        "target_rule_phase": None,
        "allowed_actions": {
            "shortlist_free_agent",
            "monitor_free_agent",
            "avoid_free_agent",
        },
    },
    "depth_chart_review": {
        "label": "Depth chart review",
        "target_rule_phase": None,
        "allowed_actions": {
            "promote_on_depth_chart",
            "demote_on_depth_chart",
            "change_role",
            "hold_depth_chart",
        },
    },
    "draft_strategy_update": {
        "label": "Draft strategy based on needs and contracts",
        "target_rule_phase": None,
        "allowed_actions": {
            "prioritize_draft_position",
            "deprioritize_draft_position",
            "target_contract_successor",
            "preserve_pick_value",
            "consider_trade_down",
        },
    },
    "trade_proposal": {
        "label": "Propose a trade targeting a specific need or surplus",
        "target_rule_phase": None,
        "allowed_actions": {
            "propose_trade",
            "shop_player",
            "request_draft_pick",
            "request_player_swap",
            "hold_no_trade",
        },
    },
    "trade_response": {
        "label": "Evaluate and respond to an incoming trade proposal",
        "target_rule_phase": None,
        "allowed_actions": {
            "accept_trade",
            "counter_trade",
            "reject_trade",
            "request_more_value",
            "conditionally_accept",
        },
    },
}

TRADE_DECISION_TYPES = {"trade_proposal", "trade_response"}
TRADE_CHART_ASSIGNMENT_SEED = 42

GM_SOURCE_NAME = "Wikipedia current NFL general managers list"
GM_SOURCE_URL = "https://en.wikipedia.org/wiki/General_manager_(American_football)#List_of_current_NFL_general_managers"
GM_SOURCE_RETRIEVED_AT = "2026-04-30"

REAL_LIFE_GM_SEEDS = {
    "ARI": ("Monti Ossenfort", 2023),
    "ATL": ("Ian Cunningham", 2026),
    "BAL": ("Eric DeCosta", 2019),
    "BUF": ("Brandon Beane", 2017),
    "CAR": ("Dan Morgan", 2024),
    "CHI": ("Ryan Poles", 2022),
    "CIN": ("Duke Tobin", 1999),
    "CLE": ("Andrew Berry", 2020),
    "DAL": ("Jerry Jones", 1989),
    "DEN": ("George Paton", 2021),
    "DET": ("Brad Holmes", 2021),
    "GB": ("Brian Gutekunst", 2018),
    "HOU": ("Nick Caserio", 2021),
    "IND": ("Chris Ballard", 2017),
    "JAX": ("James Gladstone", 2025),
    "KC": ("Brett Veach", 2017),
    "LAC": ("Joe Hortiz", 2024),
    "LAR": ("Les Snead", 2012),
    "LV": ("John Spytek", 2025),
    "MIA": ("Jon-Eric Sullivan", 2026),
    "MIN": ("Rob Brzezinski", 2026),
    "NE": ("Eliot Wolf", 2024),
    "NO": ("Mickey Loomis", 2002),
    "NYG": ("Joe Schoen", 2022),
    "NYJ": ("Darren Mougey", 2025),
    "PHI": ("Howie Roseman", 2010),
    "PIT": ("Omar Khan", 2022),
    "SEA": ("John Schneider", 2010),
    "SF": ("John Lynch", 2017),
    "TB": ("Jason Licht", 2014),
    "TEN": ("Mike Borgonzi", 2025),
    "WAS": ("Adam Peters", 2024),
}

UNSAFE_ACTION_KEYS = {
    "sql",
    "query",
    "raw_sql",
    "database_statement",
    "python",
    "script",
    "shell",
    "command",
}

UNSAFE_TEXT_MARKERS = (
    "select *",
    "insert into",
    "delete from",
    "drop table",
    "alter table",
    "pragma ",
    "sqlite_master",
    "subprocess",
    "powershell",
    "cmd.exe",
    "exec(",
    "eval(",
)

MUTATING_ACTION_TYPES = {
    "execute_sql",
    "run_sql",
    "sign_player",
    "release_player",
    "trade_player",
    "edit_contract",
    "update_database",
    "delete_player",
    "apply_move",
}


@dataclass(frozen=True)
class LlmConfig:
    game_id: str
    provider: str
    endpoint: str
    model: str
    temperature: float
    max_tokens: int
    request_timeout_sec: int
    enabled: int


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def money(value: int | None) -> str:
    if value is None:
        return "-"
    if value < 0:
        return "-" + money(abs(value))
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def backup_sqlite(source: Path, label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = source.with_name(f"{source.stem}.pre_{label}_{timestamp}{source.suffix}")
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destination


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(con: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(row["name"] == column_name for row in con.execute(f'PRAGMA table_info("{table_name}")'))


def ensure_column(con: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    if not table_exists(con, table_name):
        return
    if not column_exists(con, table_name, column_name):
        con.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {column_sql}')


def ensure_schema(con: sqlite3.Connection) -> None:
    roster_rules.ensure_schema(con)
    roster_rules.seed_rules(con)
    for column_name, column_sql in [
        ("real_life_gm_name", "real_life_gm_name TEXT"),
        ("gm_title", "gm_title TEXT"),
        ("gm_tenure_start_year", "gm_tenure_start_year INTEGER"),
        ("gm_source_name", "gm_source_name TEXT"),
        ("gm_source_url", "gm_source_url TEXT"),
        ("gm_source_retrieved_at", "gm_source_retrieved_at TEXT"),
        ("team_build_state", "team_build_state TEXT"),
        ("team_tendency_summary", "team_tendency_summary TEXT"),
        ("depth_chart_policy", "depth_chart_policy TEXT"),
        ("release_policy", "release_policy TEXT"),
        ("youth_vs_veteran_policy", "youth_vs_veteran_policy TEXT"),
        ("future_build_policy", "future_build_policy TEXT"),
        ("draft_policy", "draft_policy TEXT"),
        ("contract_policy", "contract_policy TEXT"),
        ("free_agency_policy", "free_agency_policy TEXT"),
        ("trade_policy", "trade_policy TEXT"),
        ("staff_alignment_policy", "staff_alignment_policy TEXT"),
        ("risk_profile", "risk_profile TEXT"),
        ("job_security", "job_security TEXT"),
        ("owner_pressure", "owner_pressure TEXT"),
        ("coach_alignment", "coach_alignment TEXT"),
        ("current_mandate", "current_mandate TEXT"),
        ("negotiation_style", "negotiation_style TEXT"),
        ("signature_biases_json", "signature_biases_json TEXT"),
        ("scheme_fit_policy", "scheme_fit_policy TEXT"),
        ("draft_pick_policy", "draft_pick_policy TEXT"),
        ("free_agent_cap_policy", "free_agent_cap_policy TEXT"),
        ("position_investment_policy", "position_investment_policy TEXT"),
        ("untouchables_policy", "untouchables_policy TEXT"),
        ("acquisition_checklist_json", "acquisition_checklist_json TEXT"),
        ("trade_value_chart", "trade_value_chart TEXT"),
        ("chart_deviation_factor", "chart_deviation_factor REAL DEFAULT 0.15"),
        ("prompt_directives_json", "prompt_directives_json TEXT"),
        ("source_note", "source_note TEXT"),
    ]:
        ensure_column(con, "ai_gm_profiles", column_name, column_sql)
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS ai_gm_profiles (
            team_id INTEGER PRIMARY KEY REFERENCES teams(team_id) ON DELETE CASCADE,
            gm_name TEXT NOT NULL,
            real_life_gm_name TEXT,
            gm_title TEXT,
            gm_tenure_start_year INTEGER,
            gm_source_name TEXT,
            gm_source_url TEXT,
            gm_source_retrieved_at TEXT,
            personality TEXT NOT NULL,
            roster_philosophy TEXT NOT NULL,
            cap_tolerance TEXT NOT NULL,
            draft_tendency TEXT NOT NULL,
            trade_aggression TEXT NOT NULL,
            patience_with_young_players TEXT NOT NULL,
            team_build_state TEXT,
            team_tendency_summary TEXT,
            depth_chart_policy TEXT,
            release_policy TEXT,
            youth_vs_veteran_policy TEXT,
            future_build_policy TEXT,
            draft_policy TEXT,
            contract_policy TEXT,
            free_agency_policy TEXT,
            trade_policy TEXT,
            staff_alignment_policy TEXT,
            risk_profile TEXT,
            job_security TEXT,
            owner_pressure TEXT,
            coach_alignment TEXT,
            current_mandate TEXT,
            negotiation_style TEXT,
            signature_biases_json TEXT,
            scheme_fit_policy TEXT,
            draft_pick_policy TEXT,
            free_agent_cap_policy TEXT,
            position_investment_policy TEXT,
            untouchables_policy TEXT,
            acquisition_checklist_json TEXT,
            trade_value_chart TEXT,
            chart_deviation_factor REAL DEFAULT 0.15,
            prompt_directives_json TEXT,
            source_note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ai_gm_objectives (
            objective_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            objective_type TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 5,
            description TEXT NOT NULL,
            deadline_date TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'ai_gm_seed',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(priority BETWEEN 1 AND 10),
            CHECK(status IN ('active', 'completed', 'paused', 'archived')),
            UNIQUE(team_id, season, objective_type, description)
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_objectives_team_season
            ON ai_gm_objectives(team_id, season, status, priority);

        CREATE TABLE IF NOT EXISTS ai_gm_memory (
            memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            memory_date TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 5,
            source_decision_log_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(importance BETWEEN 1 AND 10)
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_memory_team_date
            ON ai_gm_memory(team_id, memory_date DESC, importance DESC);

        CREATE TABLE IF NOT EXISTS ai_gm_decision_queue (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            decision_date TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            context_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            priority INTEGER NOT NULL DEFAULT 5,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(priority BETWEEN 1 AND 10),
            CHECK(status IN ('queued', 'running', 'completed', 'invalid', 'failed', 'cancelled'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_decision_queue_game_status
            ON ai_gm_decision_queue(game_id, status, decision_date);

        CREATE TABLE IF NOT EXISTS ai_gm_decision_log (
            decision_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER REFERENCES ai_gm_decision_queue(decision_id) ON DELETE SET NULL,
            game_id TEXT NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            decision_date TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            provider TEXT,
            endpoint TEXT,
            model TEXT,
            prompt_json TEXT NOT NULL,
            response_json TEXT,
            validation_result TEXT NOT NULL,
            action_taken TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(status IN ('valid', 'invalid', 'failed'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_decision_log_game_team
            ON ai_gm_decision_log(game_id, team_id, decision_date DESC);

        CREATE TABLE IF NOT EXISTS ai_gm_llm_config (
            game_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'ollama',
            endpoint TEXT NOT NULL,
            model TEXT NOT NULL,
            temperature REAL NOT NULL DEFAULT 0.7,
            max_tokens INTEGER NOT NULL DEFAULT 2000,
            request_timeout_sec INTEGER NOT NULL DEFAULT 120,
            enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(enabled IN (0, 1))
        );

        DROP VIEW IF EXISTS ai_gm_profiles_view;
        CREATE VIEW ai_gm_profiles_view AS
        SELECT
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            t.conference,
            t.division,
            p.gm_name,
            p.real_life_gm_name,
            p.gm_title,
            p.gm_tenure_start_year,
            p.gm_source_name,
            p.gm_source_url,
            p.gm_source_retrieved_at,
            p.personality,
            p.roster_philosophy,
            p.cap_tolerance,
            p.draft_tendency,
            p.trade_aggression,
            p.patience_with_young_players,
            p.team_build_state,
            p.team_tendency_summary,
            p.depth_chart_policy,
            p.release_policy,
            p.youth_vs_veteran_policy,
            p.future_build_policy,
            p.draft_policy,
            p.contract_policy,
            p.free_agency_policy,
            p.trade_policy,
            p.staff_alignment_policy,
            p.risk_profile,
            p.job_security,
            p.owner_pressure,
            p.coach_alignment,
            p.current_mandate,
            p.negotiation_style,
            p.signature_biases_json,
            p.scheme_fit_policy,
            p.draft_pick_policy,
            p.free_agent_cap_policy,
            p.position_investment_policy,
            p.untouchables_policy,
            p.acquisition_checklist_json,
            p.trade_value_chart,
            p.chart_deviation_factor,
            p.prompt_directives_json,
            p.source_note,
            p.updated_at
        FROM ai_gm_profiles p
        JOIN teams t ON t.team_id = p.team_id;

        DROP VIEW IF EXISTS ai_gm_decision_log_view;
        CREATE VIEW ai_gm_decision_log_view AS
        SELECT
            l.decision_log_id,
            l.queue_id,
            l.game_id,
            l.decision_date,
            l.decision_type,
            l.status,
            l.provider,
            l.model,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            l.action_taken,
            l.error_message,
            l.created_at
        FROM ai_gm_decision_log l
        JOIN teams t ON t.team_id = l.team_id;
        """
    )


def ensure_trade_support(
    con: sqlite3.Connection,
    *,
    chart_seed: int = TRADE_CHART_ASSIGNMENT_SEED,
) -> dict[str, int]:
    """Ensure the deterministic trade substrate exists for AI GM trade decisions."""
    te.ensure_schema(con)
    chart_points = te.seed_charts(con)
    chart_assignments = te.assign_charts_to_gms(con, seed=chart_seed)
    return {"chart_points": chart_points, "chart_assignments": chart_assignments}


def default_endpoint(provider: str) -> str:
    normalized = provider.lower()
    if normalized == "ollama":
        return "http://localhost:11434/api/chat"
    if normalized in {"lm_studio", "llama_cpp", "openai_compatible"}:
        return "http://localhost:1234/v1/chat/completions"
    return "http://localhost:11434/api/chat"


def gm_title_for_team(abbreviation: str) -> str:
    special_titles = {
        "CIN": "Director of Player Personnel / de facto GM",
        "DAL": "Owner, President, and General Manager",
        "MIN": "Executive Vice President of Football Operations / de facto GM",
        "NE": "Executive Vice President of Player Personnel / de facto GM",
    }
    return special_titles.get(abbreviation, "General Manager")


def team_build_state_for_profile(
    *,
    top_roster_avg: float,
    avg_age: float,
    cap_space: int,
    roster_count: int,
) -> str:
    if cap_space < 8_000_000 and top_roster_avg >= 78:
        return "cap-constrained contender/retool"
    if top_roster_avg >= 82:
        return "win-now core"
    if avg_age < 26.6 and cap_space > 25_000_000:
        return "ascending young build"
    if roster_count > 85:
        return "camp-evaluation roster"
    if cap_space > 35_000_000:
        return "flexible opportunistic build"
    return "balanced competitive build"


def default_profile_for_team(con: sqlite3.Connection, team: sqlite3.Row) -> dict[str, str]:
    cap = con.execute(
        "SELECT cap_space FROM team_cap_view WHERE team_id = ?",
        (team["team_id"],),
    ).fetchone()
    cap_space = int(cap["cap_space"] or 0) if cap else 0
    if cap_space < 8_000_000:
        cap_tolerance = "tight cap operator; avoids long future commitments unless the player is a core piece"
    elif cap_space > 35_000_000:
        cap_tolerance = "flexible cap manager; willing to spend for clear upgrades while preserving rollover"
    else:
        cap_tolerance = "balanced cap manager; prefers value contracts and manageable guarantees"

    count_row = con.execute(
        "SELECT COUNT(*) AS players, AVG(age) AS avg_age FROM players WHERE team_id = ?",
        (team["team_id"],),
    ).fetchone()
    roster_count = int(count_row["players"] or 0)
    avg_age = float(count_row["avg_age"] or 27.0)
    top_row = con.execute(
        """
        SELECT AVG(overall) AS top_roster_avg
        FROM (
            SELECT overall
            FROM players
            WHERE team_id = ?
              AND status <> 'Free Agent'
            ORDER BY overall DESC
            LIMIT 12
        )
        """,
        (team["team_id"],),
    ).fetchone()
    top_roster_avg = float(top_row["top_roster_avg"] or 72.0)
    if avg_age < 26.5:
        patience = "high patience with young players; development reps are part of the plan"
    elif avg_age > 28.5:
        patience = "low-to-medium patience; veterans must justify roster spots quickly"
    else:
        patience = "balanced patience; young players get time when the depth chart can absorb it"

    if roster_count > 85:
        trade_aggression = "moderately aggressive; uses camp depth to hunt late picks or swaps"
    elif cap_space > 35_000_000:
        trade_aggression = "opportunistic; willing to buy talent if the price stays disciplined"
    else:
        trade_aggression = "selective; avoids splash moves without a clear roster fit"

    strengths = con.execute(
        """
        SELECT position, COUNT(*) AS players, ROUND(AVG(overall), 1) AS avg_overall
        FROM players
        WHERE team_id = ?
          AND position NOT IN ('K', 'P', 'LS')
        GROUP BY position
        HAVING COUNT(*) >= 2
        ORDER BY avg_overall DESC, players DESC
        LIMIT 3
        """,
        (team["team_id"],),
    ).fetchall()
    if not strengths:
        strengths = con.execute(
            """
            SELECT position, COUNT(*) AS players, ROUND(AVG(overall), 1) AS avg_overall
            FROM players
            WHERE team_id = ?
              AND position NOT IN ('K', 'P', 'LS')
            GROUP BY position
            ORDER BY avg_overall DESC, players DESC
            LIMIT 3
            """,
            (team["team_id"],),
        ).fetchall()
    strength_text = ", ".join(row["position"] for row in strengths) or "the roster core"
    weak_rows = con.execute(
        """
        SELECT position, COUNT(*) AS players, ROUND(AVG(overall), 1) AS avg_overall
        FROM players
        WHERE team_id = ?
          AND position NOT IN ('K', 'P', 'LS')
        GROUP BY position
        HAVING COUNT(*) >= 2
        ORDER BY avg_overall ASC, players ASC
        LIMIT 3
        """,
        (team["team_id"],),
    ).fetchall()
    need_text = ", ".join(row["position"] for row in weak_rows) or "premium depth"
    gm_seed = REAL_LIFE_GM_SEEDS.get(team["abbreviation"])
    real_life_gm_name = gm_seed[0] if gm_seed else None
    gm_tenure_start_year = gm_seed[1] if gm_seed else None
    gm_name = team["gm_name"] or real_life_gm_name or f"{team['abbreviation']} AI GM"
    gm_title = gm_title_for_team(team["abbreviation"])
    team_build_state = team_build_state_for_profile(
        top_roster_avg=top_roster_avg,
        avg_age=avg_age,
        cap_space=cap_space,
        roster_count=roster_count,
    )
    team_tendency_summary = (
        f"{team_build_state}; roster count {roster_count}, average roster age {avg_age:.1f}, "
        f"top-core average overall {top_roster_avg:.1f}, cap room {money(cap_space)}."
    )
    depth_chart_policy = (
        "Set depth charts by role fit first, then current ability, then development value. "
        "A younger player should pass an older veteran when he is within 3 overall points, "
        "has stronger potential/dev trait, or fills a needed special-teams/game-day role. "
        "Do not bury a high-role-score player behind a veteran who is only marginally safer."
    )
    release_policy = (
        "Release or waive players from surplus position groups when they have low overall, "
        "low role fit, limited potential, and little dead-cap consequence. Preserve starters, "
        "rookies with meaningful upside, cheap multi-position depth, and specialists unless a "
        "clear replacement is already available."
    )
    youth_vs_veteran_policy = (
        "Prefer youth over age for the bottom third of the roster, practice squad, and backup "
        "roles. Prefer veterans when they protect a young quarterback, stabilize a thin room, "
        "or are materially better in the current season. Age becomes a negative when contract "
        "cost, injury risk, and blocked development all point the same direction."
    )
    future_build_policy = (
        "Manage one and two years ahead: identify expensive or expiring starters, draft or sign "
        "replacements before the need becomes urgent, and keep enough future cap room to extend "
        "homegrown core players. Do not trade future picks unless the move clearly changes the "
        "team's competitive window."
    )
    draft_policy = (
        f"Draft from the intersection of board value, current needs around {need_text}, and "
        "contract cliffs on the roster. Premium positions and players with early role paths get "
        "priority; non-premium positions need either clear starter upside or immediate roster utility."
    )
    contract_policy = (
        "Extend young core players before the market moves when role score, production, and age "
        "align. Avoid backloading contracts for aging non-core players. Use short veteran deals "
        "to patch weak rooms without blocking rookies."
    )
    free_agency_policy = (
        "Use free agency for targeted floor-raising, not broad identity building. Favor players "
        "who solve a thin depth chart, protect a premium young player, or reduce draft pressure. "
        "Walk away when asking price exceeds role and future cap value."
    )
    trade_policy = (
        "Shop surplus veterans and blocked players for picks or younger depth. Buy players only "
        "when the cap fit, contract control, and depth-chart role are obvious. Never trade a "
        "future premium pick to solve a replacement-level problem."
    )
    staff_alignment_policy = (
        "Depth-chart and acquisition decisions should fit the head coach and coordinator strengths. "
        "When roster value is close, prefer the player who best supports the current scheme and game-day usage."
    )
    risk_profile = (
        "Aggressive on cheap upside and market inefficiencies; conservative with future cap, "
        "premium picks, and aging-player guarantees."
    )
    if team_build_state in {"win-now core", "cap-constrained contender/retool"}:
        job_security = "stable but results-driven; ownership expects playoff-level roster decisions"
        owner_pressure = "win now without creating a future cap spiral"
        current_mandate = (
            "Turn picks and cap room into immediate starter or high-leverage depth only when the move "
            "fits the scheme and protects the next two cap years."
        )
    elif team_build_state in {"ascending young build", "flexible opportunistic build"}:
        job_security = "stable development runway; ownership expects visible talent accumulation"
        owner_pressure = "build a sustainable core and avoid impatient veteran spending"
        current_mandate = (
            "Use premium draft picks on long-term starters, add free agents who stabilize weak rooms, "
            "and keep future extensions affordable."
        )
    else:
        job_security = "moderate pressure; ownership wants a coherent plan more than isolated splash moves"
        owner_pressure = "stay competitive while clarifying the next roster core"
        current_mandate = (
            "Protect premium assets, fill scheme-critical holes, and avoid medium-cost veterans who do "
            "not change the team's trajectory."
        )

    coach_alignment = (
        f"Treat head coach {team['coach_name'] or 'the head coach'} and coordinator preferences as the "
        "scheme filter for acquisitions; close calls should favor players who match likely game-day roles."
    )
    if cap_space < 8_000_000:
        negotiation_style = "patient cap-constrained negotiator; demands discounts, short guarantees, and clear roster value"
    elif cap_space > 35_000_000:
        negotiation_style = "opportunistic negotiator; willing to strike early for clean scheme fits but still protects rollover"
    else:
        negotiation_style = "balanced value negotiator; uses the market patiently and avoids bidding wars for replaceable roles"

    premium_positions = ["QB", "OT", "EDGE", "CB", "WR", "IDL"]
    signature_biases = [
        "treat scheme fit as a requirement, not a tiebreaker",
        "protect future cap space for extensions before spending on free agents",
        "use premium picks on premium positions or obvious contract-cliff successors",
    ]
    if cap_space < 12_000_000:
        signature_biases.append("prefer draft solutions and minimum/short-term free agents because cap room is tight")
    if avg_age < 26.5:
        signature_biases.append("avoid veterans who block young players unless they solve a scheme-critical weakness")
    if top_roster_avg >= 82:
        signature_biases.append("pay a small pick or cap premium for players who upgrade a playoff role immediately")

    scheme_fit_policy = (
        f"Draft and free-agent targets must address a weak room around {need_text}, improve a defined role, "
        "or protect a premium player. Prefer role-score and position-room evidence over generic overall rating."
    )
    draft_pick_policy = (
        "Treat round 1-2 picks as core-player bets. Use them for premium positions, clear scheme starters, "
        "or successors to expensive/expiring starters. Trade down when the board does not match need or value; "
        "trade up only for quarterback, premium-position starter, or a rare player who solves both scheme and contract pressure."
    )
    free_agent_cap_policy = (
        "Before shortlisting a free agent, preserve an in-season cap buffer, compare asking AAV to projected role, "
        "and ask whether the signing blocks a cheap young player. Multi-year offers require starter-level role fit, "
        "clean age curve, and no obvious draft alternative."
    )
    position_investment_policy = (
        f"Premium investment priority: {', '.join(premium_positions)}. Non-premium positions can be targeted when "
        "the room is below replacement level, the price is modest, or the player has immediate special-teams/game-day utility."
    )
    untouchables_policy = (
        "Do not shop elite young core players, plus premium-position starters with strong role fit, unless the offer materially "
        "changes the franchise timeline. Aging expensive veterans are movable when a cheaper successor is already credible."
    )
    acquisition_checklist = [
        "Does this target solve a scheme need or contract cliff visible in the context?",
        "Is the pick cost or AAV proportional to the player's expected role?",
        "Does the move preserve an in-season cap buffer and future extension room?",
        "Does the move block a young player with similar role value?",
        "If using a premium pick, is the player a premium-position starter or a direct successor to an expensive starter?",
        "If signing a free agent, is there a cheaper draft/development alternative with acceptable downside?",
    ]
    prompt_directives = [
        "Always explain whether the decision is about present wins, future roster value, cap health, or development.",
        "When recommending a cut, mention replacement plan, dead-cap awareness, and practice-squad risk.",
        "When recommending a depth-chart move, compare role fit, current rating, youth, and special-teams value.",
        "When recommending draft priorities, consider current need, expiring contracts, age curves, and positional scarcity.",
        "When recommending draft-pick usage, explain why the round value matches the scheme need and contract timeline.",
        "When recommending free agents, compare asking AAV, likely role, cap buffer, and whether a draft/development option is better.",
        "When recommending trades, state minimum return and why the team can absorb the player leaving.",
        "Use the assigned trade value chart as a baseline, then explain any deliberate overpay or discount.",
        "When negotiating, separate value-chart math from team-context reasons such as need, cap, age, and contract control.",
        "Avoid generic advice; tie every action to a named player, position room, or contract pressure.",
    ]
    source_note = (
        "Real-life GM identity seeded from a current public GM list; tendency/policy fields are "
        "simulation attributes generated from roster age, cap space, role needs, and Codex-authored "
        "front-office rules. Review and tune per team as desired."
    )

    return {
        "gm_name": gm_name,
        "real_life_gm_name": real_life_gm_name or gm_name,
        "gm_title": gm_title,
        "gm_tenure_start_year": gm_tenure_start_year,
        "gm_source_name": GM_SOURCE_NAME if real_life_gm_name else None,
        "gm_source_url": GM_SOURCE_URL if real_life_gm_name else None,
        "gm_source_retrieved_at": GM_SOURCE_RETRIEVED_AT if real_life_gm_name else None,
        "personality": (
            f"{gm_name} is a pragmatic, team-specific operator for "
            f"{team['city']} {team['nickname']}; calm in public, direct in roster meetings, "
            "and protective of the club's medium-term flexibility."
        ),
        "roster_philosophy": (
            f"Build around current strengths at {strength_text}, keep the bottom of the roster "
            "younger and cheaper, and avoid blocking players with starter-level upside."
        ),
        "cap_tolerance": cap_tolerance,
        "draft_tendency": (
            f"Prioritizes premium-position depth and known needs around {need_text}; "
            "leans toward players with clear roles over pure traits."
        ),
        "trade_aggression": trade_aggression,
        "patience_with_young_players": patience,
        "team_build_state": team_build_state,
        "team_tendency_summary": team_tendency_summary,
        "depth_chart_policy": depth_chart_policy,
        "release_policy": release_policy,
        "youth_vs_veteran_policy": youth_vs_veteran_policy,
        "future_build_policy": future_build_policy,
        "draft_policy": draft_policy,
        "contract_policy": contract_policy,
        "free_agency_policy": free_agency_policy,
        "trade_policy": trade_policy,
        "staff_alignment_policy": staff_alignment_policy,
        "risk_profile": risk_profile,
        "job_security": job_security,
        "owner_pressure": owner_pressure,
        "coach_alignment": coach_alignment,
        "current_mandate": current_mandate,
        "negotiation_style": negotiation_style,
        "signature_biases_json": json.dumps(signature_biases, sort_keys=True),
        "scheme_fit_policy": scheme_fit_policy,
        "draft_pick_policy": draft_pick_policy,
        "free_agent_cap_policy": free_agent_cap_policy,
        "position_investment_policy": position_investment_policy,
        "untouchables_policy": untouchables_policy,
        "acquisition_checklist_json": json.dumps(acquisition_checklist, sort_keys=True),
        "prompt_directives_json": json.dumps(prompt_directives, sort_keys=True),
        "source_note": source_note,
    }


def seed_profiles(
    con: sqlite3.Connection,
    season: int = DEFAULT_SEASON,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    ensure_schema(con)
    inserted_profiles = 0
    updated_profiles = 0
    inserted_objectives = 0
    for team in con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall():
        profile = default_profile_for_team(con, team)
        before = con.total_changes
        if overwrite:
            con.execute(
                """
                INSERT INTO ai_gm_profiles (
                    team_id, gm_name, real_life_gm_name, gm_title, gm_tenure_start_year,
                    gm_source_name, gm_source_url, gm_source_retrieved_at,
                    personality, roster_philosophy, cap_tolerance,
                    draft_tendency, trade_aggression, patience_with_young_players,
                    team_build_state, team_tendency_summary, depth_chart_policy,
                    release_policy, youth_vs_veteran_policy, future_build_policy,
                    draft_policy, contract_policy, free_agency_policy, trade_policy,
                    staff_alignment_policy, risk_profile, job_security, owner_pressure,
                    coach_alignment, current_mandate, negotiation_style, signature_biases_json,
                    scheme_fit_policy, draft_pick_policy, free_agent_cap_policy,
                    position_investment_policy, untouchables_policy, acquisition_checklist_json,
                    prompt_directives_json,
                    source_note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(team_id) DO UPDATE SET
                    gm_name = excluded.gm_name,
                    real_life_gm_name = excluded.real_life_gm_name,
                    gm_title = excluded.gm_title,
                    gm_tenure_start_year = excluded.gm_tenure_start_year,
                    gm_source_name = excluded.gm_source_name,
                    gm_source_url = excluded.gm_source_url,
                    gm_source_retrieved_at = excluded.gm_source_retrieved_at,
                    personality = excluded.personality,
                    roster_philosophy = excluded.roster_philosophy,
                    cap_tolerance = excluded.cap_tolerance,
                    draft_tendency = excluded.draft_tendency,
                    trade_aggression = excluded.trade_aggression,
                    patience_with_young_players = excluded.patience_with_young_players,
                    team_build_state = excluded.team_build_state,
                    team_tendency_summary = excluded.team_tendency_summary,
                    depth_chart_policy = excluded.depth_chart_policy,
                    release_policy = excluded.release_policy,
                    youth_vs_veteran_policy = excluded.youth_vs_veteran_policy,
                    future_build_policy = excluded.future_build_policy,
                    draft_policy = excluded.draft_policy,
                    contract_policy = excluded.contract_policy,
                    free_agency_policy = excluded.free_agency_policy,
                    trade_policy = excluded.trade_policy,
                    staff_alignment_policy = excluded.staff_alignment_policy,
                    risk_profile = excluded.risk_profile,
                    job_security = excluded.job_security,
                    owner_pressure = excluded.owner_pressure,
                    coach_alignment = excluded.coach_alignment,
                    current_mandate = excluded.current_mandate,
                    negotiation_style = excluded.negotiation_style,
                    signature_biases_json = excluded.signature_biases_json,
                    scheme_fit_policy = excluded.scheme_fit_policy,
                    draft_pick_policy = excluded.draft_pick_policy,
                    free_agent_cap_policy = excluded.free_agent_cap_policy,
                    position_investment_policy = excluded.position_investment_policy,
                    untouchables_policy = excluded.untouchables_policy,
                    acquisition_checklist_json = excluded.acquisition_checklist_json,
                    prompt_directives_json = excluded.prompt_directives_json,
                    source_note = excluded.source_note,
                    updated_at = datetime('now')
                """,
                (
                    int(team["team_id"]),
                    profile["gm_name"],
                    profile["real_life_gm_name"],
                    profile["gm_title"],
                    profile["gm_tenure_start_year"],
                    profile["gm_source_name"],
                    profile["gm_source_url"],
                    profile["gm_source_retrieved_at"],
                    profile["personality"],
                    profile["roster_philosophy"],
                    profile["cap_tolerance"],
                    profile["draft_tendency"],
                    profile["trade_aggression"],
                    profile["patience_with_young_players"],
                    profile["team_build_state"],
                    profile["team_tendency_summary"],
                    profile["depth_chart_policy"],
                    profile["release_policy"],
                    profile["youth_vs_veteran_policy"],
                    profile["future_build_policy"],
                    profile["draft_policy"],
                    profile["contract_policy"],
                    profile["free_agency_policy"],
                    profile["trade_policy"],
                    profile["staff_alignment_policy"],
                    profile["risk_profile"],
                    profile["job_security"],
                    profile["owner_pressure"],
                    profile["coach_alignment"],
                    profile["current_mandate"],
                    profile["negotiation_style"],
                    profile["signature_biases_json"],
                    profile["scheme_fit_policy"],
                    profile["draft_pick_policy"],
                    profile["free_agent_cap_policy"],
                    profile["position_investment_policy"],
                    profile["untouchables_policy"],
                    profile["acquisition_checklist_json"],
                    profile["prompt_directives_json"],
                    profile["source_note"],
                ),
            )
        else:
            con.execute(
                """
                INSERT INTO ai_gm_profiles (
                    team_id, gm_name, real_life_gm_name, gm_title, gm_tenure_start_year,
                    gm_source_name, gm_source_url, gm_source_retrieved_at,
                    personality, roster_philosophy, cap_tolerance,
                    draft_tendency, trade_aggression, patience_with_young_players,
                    team_build_state, team_tendency_summary, depth_chart_policy,
                    release_policy, youth_vs_veteran_policy, future_build_policy,
                    draft_policy, contract_policy, free_agency_policy, trade_policy,
                    staff_alignment_policy, risk_profile, job_security, owner_pressure,
                    coach_alignment, current_mandate, negotiation_style, signature_biases_json,
                    scheme_fit_policy, draft_pick_policy, free_agent_cap_policy,
                    position_investment_policy, untouchables_policy, acquisition_checklist_json,
                    prompt_directives_json,
                    source_note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(team_id) DO NOTHING
                """,
                (
                    int(team["team_id"]),
                    profile["gm_name"],
                    profile["real_life_gm_name"],
                    profile["gm_title"],
                    profile["gm_tenure_start_year"],
                    profile["gm_source_name"],
                    profile["gm_source_url"],
                    profile["gm_source_retrieved_at"],
                    profile["personality"],
                    profile["roster_philosophy"],
                    profile["cap_tolerance"],
                    profile["draft_tendency"],
                    profile["trade_aggression"],
                    profile["patience_with_young_players"],
                    profile["team_build_state"],
                    profile["team_tendency_summary"],
                    profile["depth_chart_policy"],
                    profile["release_policy"],
                    profile["youth_vs_veteran_policy"],
                    profile["future_build_policy"],
                    profile["draft_policy"],
                    profile["contract_policy"],
                    profile["free_agency_policy"],
                    profile["trade_policy"],
                    profile["staff_alignment_policy"],
                    profile["risk_profile"],
                    profile["job_security"],
                    profile["owner_pressure"],
                    profile["coach_alignment"],
                    profile["current_mandate"],
                    profile["negotiation_style"],
                    profile["signature_biases_json"],
                    profile["scheme_fit_policy"],
                    profile["draft_pick_policy"],
                    profile["free_agent_cap_policy"],
                    profile["position_investment_policy"],
                    profile["untouchables_policy"],
                    profile["acquisition_checklist_json"],
                    profile["prompt_directives_json"],
                    profile["source_note"],
                ),
            )
        inserted_profiles += con.total_changes - before
        before = con.total_changes
        con.execute(
            """
            UPDATE ai_gm_profiles
            SET
                job_security = COALESCE(job_security, ?),
                owner_pressure = COALESCE(owner_pressure, ?),
                coach_alignment = COALESCE(coach_alignment, ?),
                current_mandate = COALESCE(current_mandate, ?),
                negotiation_style = COALESCE(negotiation_style, ?),
                signature_biases_json = COALESCE(signature_biases_json, ?),
                scheme_fit_policy = COALESCE(scheme_fit_policy, ?),
                draft_pick_policy = COALESCE(draft_pick_policy, ?),
                free_agent_cap_policy = COALESCE(free_agent_cap_policy, ?),
                position_investment_policy = COALESCE(position_investment_policy, ?),
                untouchables_policy = COALESCE(untouchables_policy, ?),
                acquisition_checklist_json = COALESCE(acquisition_checklist_json, ?),
                updated_at = CASE
                    WHEN job_security IS NULL
                      OR owner_pressure IS NULL
                      OR coach_alignment IS NULL
                      OR current_mandate IS NULL
                      OR negotiation_style IS NULL
                      OR signature_biases_json IS NULL
                      OR scheme_fit_policy IS NULL
                      OR draft_pick_policy IS NULL
                      OR free_agent_cap_policy IS NULL
                      OR position_investment_policy IS NULL
                      OR untouchables_policy IS NULL
                      OR acquisition_checklist_json IS NULL
                    THEN datetime('now')
                    ELSE updated_at
                END
            WHERE team_id = ?
              AND (
                  job_security IS NULL
                  OR owner_pressure IS NULL
                  OR coach_alignment IS NULL
                  OR current_mandate IS NULL
                  OR negotiation_style IS NULL
                  OR signature_biases_json IS NULL
                  OR scheme_fit_policy IS NULL
                  OR draft_pick_policy IS NULL
                  OR free_agent_cap_policy IS NULL
                  OR position_investment_policy IS NULL
                  OR untouchables_policy IS NULL
                  OR acquisition_checklist_json IS NULL
              )
            """,
            (
                profile["job_security"],
                profile["owner_pressure"],
                profile["coach_alignment"],
                profile["current_mandate"],
                profile["negotiation_style"],
                profile["signature_biases_json"],
                profile["scheme_fit_policy"],
                profile["draft_pick_policy"],
                profile["free_agent_cap_policy"],
                profile["position_investment_policy"],
                profile["untouchables_policy"],
                profile["acquisition_checklist_json"],
                int(team["team_id"]),
            ),
        )
        updated_profiles += con.total_changes - before
        objectives = [
            (
                "camp_roster_plan",
                2,
                "Build a cutdown board that reaches future roster limits without exposing core young players.",
                f"{season}-09-01",
            ),
            (
                "cap_health",
                4,
                "Preserve enough cap flexibility to handle in-season injuries and opportunistic upgrades.",
                f"{season}-11-03",
            ),
            (
                "development_pipeline",
                5,
                "Protect players with credible year-two or year-three upside when roster decisions are close.",
                f"{season + 1}-01-15",
            ),
        ]
        for objective_type, priority, description, deadline in objectives:
            before = con.total_changes
            con.execute(
                """
                INSERT INTO ai_gm_objectives (
                    team_id, season, objective_type, priority, description,
                    deadline_date, status, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', 'ai_gm_seed', datetime('now'), datetime('now'))
                ON CONFLICT(team_id, season, objective_type, description) DO NOTHING
                """,
                (int(team["team_id"]), season, objective_type, priority, description, deadline),
            )
            inserted_objectives += con.total_changes - before
    return {"profiles": inserted_profiles, "profiles_updated": updated_profiles, "objectives": inserted_objectives}


def resolve_game_state(con: sqlite3.Connection, game_id: str | None = None) -> dict[str, Any]:
    ensure_schema(con)
    row = None
    if game_id:
        row = con.execute(
            "SELECT * FROM game_saves WHERE game_id = ?",
            (game_id,),
        ).fetchone()
    else:
        active = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'",
        ).fetchone()
        if active:
            row = con.execute(
                "SELECT * FROM game_saves WHERE game_id = ? AND status = 'active'",
                (active["setting_value"],),
            ).fetchone()
        if row is None:
            row = con.execute(
                """
                SELECT *
                FROM game_saves
                WHERE status = 'active'
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()

    if row:
        current_date = row["current_date"]
        season = int(row["current_league_year"])
        resolved_game_id = row["game_id"]
        phase_code = row["current_phase_code"]
    else:
        current_date = setting(con, "current_game_date", f"{DEFAULT_SEASON}-06-01")
        season = int(setting(con, "current_season", setting(con, "current_league_year", str(DEFAULT_SEASON))))
        resolved_game_id = game_id or "master"
        phase_code = setting(con, "current_calendar_phase", "OFFSEASON_OPEN")

    phase = con.execute(
        """
        SELECT *
        FROM league_phase_windows
        WHERE date(?) BETWEEN date(start_date) AND date(end_date)
        ORDER BY league_year
        LIMIT 1
        """,
        (current_date,),
    ).fetchone()
    return {
        "game_id": resolved_game_id,
        "current_date": current_date,
        "season": season,
        "phase_code": phase["phase_code"] if phase else phase_code,
        "phase_name": phase["phase_name"] if phase else phase_code,
        "roster_limits_enforced": int(phase["roster_limits_enforced"] or 0) if phase else 0,
        "roster_rule_phase": phase["roster_rule_phase"] if phase else None,
        "transactions_open": int(phase["transactions_open"] or 0) if phase else 1,
        "salary_cap_mode": phase["salary_cap_mode"] if phase else "TOP_51_ALWAYS",
    }


def setting(con: sqlite3.Connection, key: str, default: str) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = ?",
        (key,),
    ).fetchone()
    return row["setting_value"] if row else default


def get_team(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return row


def target_rule_phase(game_state: dict[str, Any], decision_type: str) -> str:
    decision_meta = ADVISORY_DECISION_TYPES[decision_type]
    if decision_meta["target_rule_phase"]:
        return str(decision_meta["target_rule_phase"])
    if game_state.get("roster_rule_phase"):
        return str(game_state["roster_rule_phase"])
    return "Preseason"


def rows_as_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def team_position_summary(con: sqlite3.Connection, team_id: int) -> list[dict[str, Any]]:
    return rows_as_dicts(
        con.execute(
            """
            SELECT position, COUNT(*) AS players, ROUND(AVG(overall), 1) AS avg_overall,
                   MAX(overall) AS best_overall
            FROM players
            WHERE team_id = ?
            GROUP BY position
            ORDER BY position
            """,
            (team_id,),
        ).fetchall()
    )


def scheme_need_summary(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    team_id: int,
    season: int,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not table_exists(con, "player_role_scores"):
        return []
    if table_exists(con, "current_player_scheme_fit_view"):
        return rows_as_dicts(
            con.execute(
                """
                WITH position_room AS (
                    SELECT
                        p.position,
                        COUNT(*) AS players,
                        ROUND(AVG(p.overall), 1) AS avg_overall,
                        MAX(p.overall) AS best_overall
                    FROM players p
                    WHERE p.team_id = ?
                      AND p.position NOT IN ('K', 'P', 'LS')
                    GROUP BY p.position
                ),
                current_fit AS (
                    SELECT
                        position,
                        current_scheme,
                        current_scheme_key,
                        ROUND(AVG(current_fit), 1) AS avg_role_score,
                        ROUND(MAX(current_fit), 1) AS best_role_score,
                        ROUND(AVG(growth_fit), 1) AS avg_growth_fit
                    FROM current_player_scheme_fit_view
                    WHERE team = ?
                      AND season = ?
                    GROUP BY position, current_scheme, current_scheme_key
                )
                SELECT
                    pr.position,
                    pr.players,
                    pr.avg_overall,
                    pr.best_overall,
                    cf.avg_role_score,
                    cf.best_role_score,
                    cf.avg_growth_fit,
                    cf.current_scheme,
                    cf.current_scheme_key,
                    CASE
                        WHEN pr.position IN ('QB', 'OT', 'EDGE', 'CB', 'WR', 'IDL') THEN 'premium'
                        WHEN pr.position IN ('OG', 'C', 'TE', 'FS', 'SS', 'ILB', 'DT', 'DE') THEN 'starter_or_depth'
                        ELSE 'low_cost_role'
                    END AS investment_tier,
                    CASE
                        WHEN pr.players <= 2 THEN 'thin_room'
                        WHEN COALESCE(cf.avg_role_score, pr.avg_overall) < 68 THEN 'scheme_fit_gap'
                        WHEN pr.best_overall < 74 THEN 'starter_ceiling_gap'
                        ELSE 'monitor'
                    END AS need_driver
                FROM position_room pr
                LEFT JOIN current_fit cf ON cf.position = pr.position
                ORDER BY
                    CASE
                        WHEN pr.players <= 2 THEN 0
                        WHEN COALESCE(cf.avg_role_score, pr.avg_overall) < 68 THEN 1
                        WHEN pr.best_overall < 74 THEN 2
                        ELSE 3
                    END,
                    COALESCE(cf.avg_role_score, pr.avg_overall) ASC,
                    pr.players ASC,
                    pr.position
                LIMIT ?
                """,
                (team_id, team_abbr, season, limit),
            ).fetchall()
        )
    return rows_as_dicts(
        con.execute(
            """
            WITH position_room AS (
                SELECT
                    position,
                    COUNT(*) AS players,
                    ROUND(AVG(overall), 1) AS avg_overall,
                    MAX(overall) AS best_overall
                FROM players
                WHERE team_id = ?
                  AND position NOT IN ('K', 'P', 'LS')
                GROUP BY position
            ),
            role_fit AS (
                SELECT
                    position,
                    ROUND(AVG(role_score), 1) AS avg_role_score,
                    ROUND(MAX(role_score), 1) AS best_role_score
                FROM player_role_scores_view
                WHERE team = ?
                  AND season = ?
                  AND scheme_key = 'default'
                GROUP BY position
            )
            SELECT
                pr.position,
                pr.players,
                pr.avg_overall,
                pr.best_overall,
                rf.avg_role_score,
                rf.best_role_score,
                CASE
                    WHEN pr.position IN ('QB', 'OT', 'EDGE', 'CB', 'WR', 'IDL') THEN 'premium'
                    WHEN pr.position IN ('OG', 'C', 'TE', 'FS', 'SS', 'ILB', 'DT', 'DE') THEN 'starter_or_depth'
                    ELSE 'low_cost_role'
                END AS investment_tier,
                CASE
                    WHEN pr.players <= 2 THEN 'thin_room'
                    WHEN COALESCE(rf.avg_role_score, pr.avg_overall) < 68 THEN 'scheme_fit_gap'
                    WHEN pr.best_overall < 74 THEN 'starter_ceiling_gap'
                    ELSE 'monitor'
                END AS need_driver
            FROM position_room pr
            LEFT JOIN role_fit rf ON rf.position = pr.position
            ORDER BY
                CASE
                    WHEN pr.players <= 2 THEN 0
                    WHEN COALESCE(rf.avg_role_score, pr.avg_overall) < 68 THEN 1
                    WHEN pr.best_overall < 74 THEN 2
                    ELSE 3
                END,
                COALESCE(rf.avg_role_score, pr.avg_overall) ASC,
                pr.players ASC,
                pr.position
            LIMIT ?
            """,
            (team_id, team_abbr, season, limit),
        ).fetchall()
    )


def acquisition_decision_context(
    *,
    cap: dict[str, Any] | None,
    draft_inventory_rows: list[dict[str, Any]],
    expiring_rows: list[dict[str, Any]],
    scheme_needs: list[dict[str, Any]],
    season: int,
) -> dict[str, Any]:
    cap = cap or {}
    cap_space = int(cap.get("cap_space") or 0)
    salary_cap = int(cap.get("salary_cap") or 0)
    in_season_buffer = max(7_500_000, int(salary_cap * 0.025)) if salary_cap else 7_500_000
    practical_free_agent_budget = max(0, cap_space - in_season_buffer)
    if cap_space < in_season_buffer:
        cap_band = "critical"
    elif cap_space < 15_000_000:
        cap_band = "tight"
    elif cap_space < 35_000_000:
        cap_band = "workable"
    else:
        cap_band = "flexible"

    next_draft_year = season + 1
    premium_pick_count = sum(
        int(row.get("picks") or 0)
        for row in draft_inventory_rows
        if int(row.get("draft_year") or 0) == next_draft_year and int(row.get("round") or 99) <= 3
    )
    day_three_count = sum(
        int(row.get("picks") or 0)
        for row in draft_inventory_rows
        if int(row.get("draft_year") or 0) == next_draft_year and int(row.get("round") or 0) >= 4
    )
    low_cost_role_positions = {"FB", "P", "K", "LS"}
    high_priority_needs = [
        row for row in scheme_needs
        if row.get("need_driver") != "monitor" and row.get("position") not in low_cost_role_positions
    ][:5]
    if not high_priority_needs:
        high_priority_needs = [
            row for row in scheme_needs
            if row.get("position") not in low_cost_role_positions
        ][:3]
    low_cost_role_needs = [
        row for row in scheme_needs
        if row.get("need_driver") != "monitor" and row.get("position") in low_cost_role_positions
    ][:4]
    contract_cliffs = [
        row for row in expiring_rows
        if int(row.get("overall") or 0) >= 75 or int(row.get("aav") or 0) >= 5_000_000
    ][:8]

    return {
        "cap_constraints": {
            "cap_band": cap_band,
            "cap_space": cap_space,
            "recommended_in_season_buffer": in_season_buffer,
            "practical_free_agent_budget": practical_free_agent_budget,
            "guidance": (
                "Do not spend below the recommended buffer unless the player is a clear starter "
                "at a scheme-critical need or a cheap short-term injury/roster fix."
            ),
        },
        "draft_pick_logic": {
            "next_draft_year": next_draft_year,
            "premium_picks_rounds_1_to_3": premium_pick_count,
            "day_three_picks_rounds_4_to_7": day_three_count,
            "guidance": [
                "Rounds 1-2 should solve premium positions, top scheme needs, or contract cliffs.",
                "Round 3 can target high-probability starters, premium depth, or immediate role players.",
                "Day-three picks should add cheap depth, special-teams value, and developmental scheme fits.",
                "Trade down when the board and scheme need do not match pick value.",
            ],
        },
        "free_agent_logic": {
            "target_positions": [row.get("position") for row in high_priority_needs],
            "low_cost_role_positions": [row.get("position") for row in low_cost_role_needs],
            "guidance": [
                "Shortlist free agents only when asking AAV fits practical budget or the player is a true starter.",
                "Prefer one-year or low-guarantee deals for age-28-plus non-core players.",
                "Avoid paying market price at non-premium positions when a draft/development option is plausible.",
                "Fill low-cost role needs like FB, specialists, and fringe depth with day-three picks, minimum deals, or waivers.",
                "A free agent should reduce draft pressure, protect a young core player, or fill a scheme role immediately.",
            ],
        },
        "scheme_needs": high_priority_needs,
        "low_cost_role_needs": low_cost_role_needs,
        "contract_cliffs": contract_cliffs,
        "decision_questions": [
            "What scheme role is being solved?",
            "What is the cap cost now and next year?",
            "Is a draft pick better value than this free agent?",
            "Does the acquisition block a young player with similar role value?",
            "Is this a premium-position investment or a cheap role patch?",
        ],
    }


def top_players(
    con: sqlite3.Connection,
    team_id: int,
    season: int,
    *,
    limit: int,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    direction = "ASC" if ascending else "DESC"
    return rows_as_dicts(
        con.execute(
            f"""
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.age,
                p.years_exp,
                p.status,
                p.overall,
                p.potential,
                p.dev_trait,
                cy.cap_hit,
                cy.dead_cap_if_cut_pre_june1,
                c.end_year
            FROM players p
            LEFT JOIN contracts c
              ON c.player_id = p.player_id
             AND c.is_active = 1
            LEFT JOIN contract_years cy
              ON cy.contract_id = c.contract_id
             AND cy.season = ?
            WHERE p.team_id = ?
            ORDER BY p.overall {direction}, p.potential {direction}, player_name
            LIMIT ?
            """,
            (season, team_id, limit),
        ).fetchall()
    )


def expiring_contracts(con: sqlite3.Connection, team_id: int, season: int, limit: int) -> list[dict[str, Any]]:
    return rows_as_dicts(
        con.execute(
            """
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.age,
                p.overall,
                p.potential,
                c.end_year,
                c.aav,
                cy.cap_hit
            FROM contracts c
            JOIN players p ON p.player_id = c.player_id
            LEFT JOIN contract_years cy
              ON cy.contract_id = c.contract_id
             AND cy.season = ?
            WHERE c.team_id = ?
              AND c.is_active = 1
              AND c.end_year <= ?
            ORDER BY p.overall DESC, c.aav DESC, player_name
            LIMIT ?
            """,
            (season, team_id, season + 1, limit),
        ).fetchall()
    )


def draft_inventory(con: sqlite3.Connection, team_id: int, season: int) -> list[dict[str, Any]]:
    return rows_as_dicts(
        con.execute(
            """
            SELECT
                dp.draft_year,
                dp.round,
                COUNT(*) AS picks,
                GROUP_CONCAT(orig.abbreviation, ', ') AS original_teams
            FROM draft_picks dp
            LEFT JOIN teams orig ON orig.team_id = dp.original_team_id
            WHERE dp.current_team_id = ?
              AND dp.draft_year BETWEEN ? AND ?
              AND COALESCE(dp.is_used, 0) = 0
            GROUP BY dp.draft_year, dp.round
            ORDER BY dp.draft_year, dp.round
            """,
            (team_id, season + 1, season + 3),
        ).fetchall()
    )


def recent_transactions(con: sqlite3.Connection, team_id: int, limit: int) -> list[dict[str, Any]]:
    return rows_as_dicts(
        con.execute(
            """
            SELECT
                tl.transaction_date,
                tl.transaction_type,
                COALESCE(p.first_name || ' ' || p.last_name, '') AS player_name,
                tl.description
            FROM transaction_log tl
            LEFT JOIN players p ON p.player_id = tl.player_id
            WHERE tl.team_id = ?
               OR tl.from_team_id = ?
               OR tl.to_team_id = ?
               OR tl.secondary_team_id = ?
            ORDER BY tl.transaction_date DESC, tl.transaction_id DESC
            LIMIT ?
            """,
            (team_id, team_id, team_id, team_id, limit),
        ).fetchall()
    )


def role_scores(con: sqlite3.Connection, team_abbr: str, season: int, limit: int) -> list[dict[str, Any]]:
    if not table_exists(con, "player_role_scores"):
        return []
    return rows_as_dicts(
        con.execute(
            """
            SELECT player_id, player_name, position, role_name, ROUND(role_score, 1) AS role_score
            FROM player_role_scores_view
            WHERE team = ?
              AND season = ?
            ORDER BY role_score DESC, player_name
            LIMIT ?
            """,
            (team_abbr, season, limit),
        ).fetchall()
    )


def free_agent_options(
    con: sqlite3.Connection,
    limit: int,
    *,
    cap_budget: int | None = None,
    target_positions: list[str] | None = None,
) -> list[dict[str, Any]]:
    query_limit = max(limit, min(limit * 4, 80))
    options = rows_as_dicts(
        con.execute(
            """
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.age,
                p.overall,
                p.potential,
                fap.position_group,
                fap.market_tier,
                fap.asking_aav,
                fap.minimum_aav,
                fap.preferred_years,
                fap.motivation
            FROM free_agent_profiles fap
            JOIN players p ON p.player_id = fap.player_id
            WHERE p.status = 'Free Agent'
            ORDER BY p.overall DESC, fap.asking_aav DESC, player_name
            LIMIT ?
            """,
            (query_limit,),
        ).fetchall()
    )
    target_position_set = {position for position in (target_positions or []) if position}
    for option in options:
        asking_aav = int(option.get("asking_aav") or 0)
        minimum_aav = int(option.get("minimum_aav") or 0)
        if cap_budget is None:
            cap_fit = "unknown"
        elif asking_aav <= cap_budget:
            cap_fit = "fits_asking_aav"
        elif minimum_aav <= cap_budget:
            cap_fit = "fits_only_if_negotiated_down"
        else:
            cap_fit = "over_budget"
        option["cap_fit"] = cap_fit
        option["need_fit"] = option.get("position") in target_position_set or option.get("position_group") in target_position_set
        option["cap_budget_delta_vs_asking"] = None if cap_budget is None else cap_budget - asking_aav
        if option["need_fit"] and cap_fit == "fits_asking_aav":
            option["ai_gm_posture"] = "shortlist_candidate"
        elif option["need_fit"] and cap_fit == "fits_only_if_negotiated_down":
            option["ai_gm_posture"] = "monitor_if_price_drops"
        elif option["need_fit"]:
            option["ai_gm_posture"] = "avoid_at_current_price"
        elif cap_fit == "fits_asking_aav":
            option["ai_gm_posture"] = "only_if_depth_need_emerges"
        else:
            option["ai_gm_posture"] = "avoid_or_monitor_only"
    cap_fit_rank = {
        "fits_asking_aav": 0,
        "fits_only_if_negotiated_down": 1,
        "over_budget": 2,
        "unknown": 3,
    }
    options.sort(
        key=lambda option: (
            not bool(option.get("need_fit")),
            cap_fit_rank.get(str(option.get("cap_fit")), 9),
            -int(option.get("overall") or 0),
            int(option.get("asking_aav") or 0),
        )
    )
    return options[:limit]


def validation_snapshot(
    con: sqlite3.Connection,
    team: sqlite3.Row,
    season: int,
    rule_phase: str,
) -> dict[str, Any]:
    try:
        rule_set = roster_rules.get_rule_set(con, season, rule_phase)
        summary, issues = roster_rules.validate_team(con, team, rule_set, include_info=True)
        return {
            "rule_phase": rule_phase,
            "summary": summary,
            "issues": issues[:12],
        }
    except Exception as exc:
        return {
            "rule_phase": rule_phase,
            "error": str(exc),
        }


def build_team_context(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    decision_type: str,
    game_id: str | None = None,
    max_players: int = 18,
    max_free_agents: int = 18,
) -> dict[str, Any]:
    ensure_schema(con)
    if decision_type not in ADVISORY_DECISION_TYPES:
        raise ValueError(f"Unknown decision type: {decision_type}")
    counts = seed_profiles(con)
    if decision_type in TRADE_DECISION_TYPES:
        ensure_trade_support(con)
    if counts["profiles"] or counts["objectives"]:
        con.commit()

    team = get_team(con, team_abbr)
    game_state = resolve_game_state(con, game_id)
    season = int(game_state["season"])
    rule_phase = target_rule_phase(game_state, decision_type)
    cap = row_dict(
        con.execute(
            "SELECT * FROM team_cap_view WHERE team_id = ?",
            (team["team_id"],),
        ).fetchone()
    )
    roster_counts = row_dict(
        con.execute(
            "SELECT * FROM team_roster_counts_view WHERE team_id = ?",
            (team["team_id"],),
        ).fetchone()
    )
    profile = row_dict(
        con.execute(
            "SELECT * FROM ai_gm_profiles_view WHERE team_id = ?",
            (team["team_id"],),
        ).fetchone()
    )
    profile_directives: list[str] = []
    if profile and profile.get("prompt_directives_json"):
        try:
            parsed_directives = json.loads(profile["prompt_directives_json"])
            if isinstance(parsed_directives, list):
                profile_directives = [str(item) for item in parsed_directives]
        except json.JSONDecodeError:
            profile_directives = []
    signature_biases = parse_json_list(profile.get("signature_biases_json") if profile else None)
    acquisition_checklist = parse_json_list(profile.get("acquisition_checklist_json") if profile else None)
    objectives = rows_as_dicts(
        con.execute(
            """
            SELECT objective_type, priority, description, deadline_date, status
            FROM ai_gm_objectives
            WHERE team_id = ?
              AND season = ?
              AND status = 'active'
            ORDER BY priority, deadline_date
            """,
            (team["team_id"], season),
        ).fetchall()
    )
    memory = rows_as_dicts(
        con.execute(
            """
            SELECT memory_date, memory_type, summary, importance
            FROM ai_gm_memory
            WHERE team_id = ?
            ORDER BY memory_date DESC, importance DESC
            LIMIT 8
            """,
            (team["team_id"],),
        ).fetchall()
    )

    position_summary = team_position_summary(con, int(team["team_id"]))
    expiring = expiring_contracts(con, int(team["team_id"]), season, 12)
    draft_picks = draft_inventory(con, int(team["team_id"]), season)
    scheme_needs = scheme_need_summary(
        con,
        team_abbr=team["abbreviation"],
        team_id=int(team["team_id"]),
        season=season,
    )
    acquisition_context = acquisition_decision_context(
        cap=cap,
        draft_inventory_rows=draft_picks,
        expiring_rows=expiring,
        scheme_needs=scheme_needs,
        season=season,
    )

    context = {
        "game": game_state,
        "team": {
            "team_id": int(team["team_id"]),
            "abbreviation": team["abbreviation"],
            "name": f"{team['city']} {team['nickname']}",
            "conference": team["conference"],
            "division": team["division"],
            "head_coach": team["coach_name"],
            "gm_name": team["gm_name"],
            "prestige": team["prestige"],
        },
        "ai_gm_profile": profile,
        "ai_gm_operating_directives": profile_directives,
        "ai_gm_signature_biases": signature_biases,
        "ai_gm_acquisition_checklist": acquisition_checklist,
        "active_objectives": objectives,
        "recent_memory": memory,
        "decision_request": {
            "decision_type": decision_type,
            "label": ADVISORY_DECISION_TYPES[decision_type]["label"],
            "allowed_action_types": sorted(ADVISORY_DECISION_TYPES[decision_type]["allowed_actions"]),
            "max_actions": 8,
            "advisory_only": True,
            "must_reference_existing_player_ids": True,
        },
        "cap": cap,
        "roster_counts": roster_counts,
        "position_summary": position_summary,
        "scheme_need_summary": scheme_needs,
        "acquisition_decision_context": acquisition_context,
        "roster_validation": validation_snapshot(con, team, season, rule_phase),
        "top_players": top_players(con, int(team["team_id"]), season, limit=max_players),
        "bottom_roster_players": top_players(
            con,
            int(team["team_id"]),
            season,
            limit=max_players,
            ascending=True,
        ),
        "expiring_contracts": expiring,
        "draft_inventory": draft_picks,
        "top_role_scores": role_scores(con, team["abbreviation"], season, 12),
        "recent_transactions": recent_transactions(con, int(team["team_id"]), 10),
    }
    if decision_type == "free_agent_shortlist":
        context["free_agent_options"] = free_agent_options(
            con,
            max_free_agents,
            cap_budget=acquisition_context["cap_constraints"]["practical_free_agent_budget"],
            target_positions=acquisition_context["free_agent_logic"]["target_positions"],
        )
    if decision_type in TRADE_DECISION_TYPES:
        context["trade_context"] = _build_trade_context(con, int(team["team_id"]), season, decision_type)
    return context


def _build_trade_context(
    con: sqlite3.Connection,
    team_id: int,
    season: int,
    decision_type: str,
) -> dict[str, Any]:
    """Build supplementary trade context for trade_proposal / trade_response decisions."""
    ensure_trade_support(con)
    chart = te.gm_chart(con, team_id)
    deviation = te.gm_deviation(con, team_id)
    chart_detail = row_dict(con.execute(
        """
        SELECT chart_name, display_name, description, source_name, source_url
        FROM trade_value_charts
        WHERE chart_name = ?
        """,
        (chart,),
    ).fetchone())
    round_value_reference = []
    for round_num in range(1, 8):
        midpoint_pick = (round_num - 1) * 32 + 16
        round_value_reference.append({
            "round": round_num,
            "midpoint_pick": midpoint_pick,
            "value": te.pick_value(con, chart, midpoint_pick),
        })

    # Trade-block candidates: surplus / aging / expensive players
    trade_block = rows_as_dicts(con.execute(
        """
        SELECT p.player_id,
               p.first_name || ' ' || p.last_name AS name,
               p.position, p.age, p.overall, p.potential,
               c.aav, c.end_year
        FROM players p
        LEFT JOIN contracts c ON c.player_id = p.player_id AND c.is_active = 1
        WHERE p.team_id = ?
          AND p.overall < 82
          AND (p.age >= 28 OR p.overall < 72)
        ORDER BY p.age DESC, c.aav DESC
        LIMIT 12
        """,
        (team_id,),
    ).fetchall())

    # Current draft picks available for trade
    tradeable_picks = rows_as_dicts(con.execute(
        """
        SELECT dp.pick_id, dp.draft_year, dp.round, dp.pick_number
        FROM draft_picks dp
        WHERE dp.current_team_id = ?
          AND COALESCE(dp.is_used, 0) = 0
          AND dp.draft_year BETWEEN ? AND ? + 2
        ORDER BY dp.draft_year, dp.round, dp.pick_number
        """,
        (team_id, season + 1, season),
    ).fetchall())

    # Assign chart values to trade-block players
    for player in trade_block:
        pid = player.get("player_id")
        if pid:
            player["trade_value"] = te.player_trade_value(con, int(pid), season, chart)

    # Assign chart values to picks
    for pick in tradeable_picks:
        pn = pick.get("pick_number")
        if pn:
            pick["chart_value"] = te.pick_value(con, chart, int(pn))
        else:
            pick["chart_value"] = te.pick_value_for_round(
                con, chart, int(pick.get("draft_year", season + 1)),
                int(pick.get("round", 1)), team_id,
            )

    ctx: dict[str, Any] = {
        "assigned_chart": chart,
        "assigned_chart_detail": chart_detail,
        "chart_deviation_factor": deviation,
        "round_value_reference": round_value_reference,
        "valuation_guidance": [
            "Use the assigned chart as this GM's default negotiation anchor.",
            "Deviation factor is the normal flexibility band, not an automatic limit.",
            "A GM may knowingly overpay for a premium player, quarterback support, or a trade-up target, but must explain why.",
            "A GM should ask for a discount when taking age, cap risk, or an awkward contract from another team.",
        ],
        "trade_block_candidates": trade_block,
        "tradeable_draft_picks": tradeable_picks,
        "weak_positions": list(te.team_weak_positions(con, team_id, season)),
    }

    outgoing = rows_as_dicts(con.execute(
        """
        SELECT tp.proposal_id, tp.proposal_date, tp.status, tp.proposer_note,
               recv.abbreviation AS receiving_team,
               tp.proposing_value, tp.receiving_value
        FROM trade_proposals tp
        JOIN teams recv ON recv.team_id = tp.receiving_team_id
        WHERE tp.proposing_team_id = ?
          AND tp.status IN ('proposed', 'countered')
        ORDER BY tp.proposal_date DESC, tp.proposal_id DESC
        LIMIT 5
        """,
        (team_id,),
    ).fetchall())
    if outgoing:
        ctx["active_outgoing_trade_proposals"] = outgoing

    # For trade_response, include pending incoming proposals
    if decision_type == "trade_response":
        pending = rows_as_dicts(con.execute(
            """
            SELECT tp.proposal_id, tp.proposal_date, tp.proposer_note,
                   prop.abbreviation AS proposing_team,
                   tp.proposing_value, tp.receiving_value
            FROM trade_proposals tp
            JOIN teams prop ON prop.team_id = tp.proposing_team_id
            WHERE tp.receiving_team_id = ?
              AND tp.status IN ('proposed', 'countered')
            ORDER BY tp.proposal_date DESC
            LIMIT 5
            """,
            (team_id,),
        ).fetchall())
        for p in pending:
            pid = p["proposal_id"]
            assets = rows_as_dicts(con.execute(
                """
                SELECT side, asset_type, player_id, pick_id,
                       draft_year, round, pick_number, description, chart_value
                FROM trade_proposal_assets
                WHERE proposal_id = ?
                ORDER BY side, asset_id
                """,
                (pid,),
            ).fetchall())
            p["assets"] = assets
            evaluation = te.evaluate_trade_for_team(
                con, team_id=team_id, proposal_id=pid, season=season,
            )
            p["chart_evaluation"] = evaluation
        ctx["pending_trade_proposals"] = pending

    return ctx


def response_schema_for_context(context: dict[str, Any]) -> dict[str, Any]:
    decision_type = context["decision_request"]["decision_type"]
    base = {
        "team": context["team"]["abbreviation"],
        "decision_type": decision_type,
        "summary": "One or two concise sentences.",
        "actions": [
            {
                "action_type": "one allowed action_type",
                "player_id": "existing integer player_id when action involves a player",
                "reason": "football and roster-management rationale",
                "minimum_return": "only for trade-block actions when relevant",
                "position_group": "for draft/depth-chart strategy actions when relevant",
                "contract_driver": "expiring or expensive player/room creating the need when relevant",
                "priority": "integer 1-10 when useful",
            }
        ],
        "confidence": 0.0,
    }
    if decision_type == "trade_proposal":
        base["actions"] = [
            {
                "action_type": "propose_trade, shop_player, request_draft_pick, request_player_swap, or hold_no_trade",
                "target_team": "team abbreviation for a proposed trade partner when known",
                "player_id": "player your team would shop or include, when relevant",
                "target_player_id": "player requested from the other team, when relevant",
                "requested_pick_id": "draft pick id requested, when relevant",
                "requested_pick_round": "future pick round 1-7 when no pick id is known",
                "value_chart_note": "how the assigned chart and deviation affected the ask",
                "reason": "why the trade fits roster, cap, contract, or team-window logic",
                "minimum_return": "minimum acceptable return if shopping a player",
            }
        ]
    elif decision_type == "free_agent_shortlist":
        base["actions"] = [
            {
                "action_type": "shortlist_free_agent, monitor_free_agent, or avoid_free_agent",
                "player_id": "free-agent player_id from free_agent_options",
                "expected_role": "starter, rotational, bridge, depth, or special teams",
                "scheme_fit_note": "specific role/position need the player solves",
                "cap_fit_note": "asking AAV/minimum AAV versus practical free-agent budget",
                "max_aav": "optional maximum annual value the GM would tolerate",
                "fallback_plan": "draft/development/minimum-veteran fallback if price is too high",
                "reason": "why this is logical given scheme need and cap constraints",
            }
        ]
    elif decision_type == "draft_strategy_update":
        base["actions"] = [
            {
                "action_type": "prioritize_draft_position, deprioritize_draft_position, target_contract_successor, preserve_pick_value, or consider_trade_down",
                "position_group": "position or room being evaluated",
                "round_band": "round 1, round 2, day two, or day three",
                "scheme_need": "specific scheme/role gap from acquisition_decision_context",
                "contract_driver": "expiring/expensive player or cap cliff creating the need",
                "pick_value_note": "why the pick value should be spent, preserved, or traded down",
                "free_agent_alternative": "whether free agency is a better or worse answer",
                "reason": "draft-pick logic tied to scheme need and cap considerations",
            }
        ]
    elif decision_type == "trade_response":
        base["actions"] = [
            {
                "action_type": "accept_trade, counter_trade, reject_trade, request_more_value, or conditionally_accept",
                "proposal_id": "incoming proposal id from trade_context.pending_trade_proposals",
                "target_player_id": "player requested in a counter, when relevant",
                "counter_pick_round": "additional or changed pick round 1-7, when relevant",
                "conditions": "plain-English condition for conditionally_accept, when relevant",
                "value_chart_note": "why the chart math is acceptable or not",
                "reason": "football and roster-management rationale",
            }
        ]
    return base


def build_prompt(context: dict[str, Any]) -> list[dict[str, str]]:
    schema = response_schema_for_context(context)
    decision_type = context["decision_request"]["decision_type"]
    trade_instruction = ""
    if decision_type in TRADE_DECISION_TYPES:
        trade_instruction = (
            " For trade decisions, use trade_context.assigned_chart as the GM's "
            "negotiation baseline and trade_context.chart_deviation_factor as normal "
            "flexibility. The chart is a guide, not a prison: you may overpay or "
            "discount when roster window, cap, age, contract control, or a specific "
            "trade-up/down target justifies it. Include proposal_id for trade responses."
        )
    system = (
        "You are a CPU-controlled NFL general manager inside NFL GM Sim. "
        "You propose front-office decisions, but you do not control the database. "
        "Follow the team's real-life GM identity, team tendency profile, and operating directives "
        "from the context packet when evaluating depth chart, releases, youth versus veterans, "
        "future roster construction, draft needs, contract timing, free agency, and trades. "
        "For draft and free-agent decisions, anchor the recommendation in acquisition_decision_context: "
        "scheme_need_summary, cap_constraints, draft_pick_logic, free_agent_logic, and contract_cliffs. "
        "Do not recommend spending a premium pick or meaningful AAV unless the role, scheme fit, and cap logic all line up."
        + trade_instruction
        + " Return strict JSON only. Do not include markdown. Do not write SQL, code, "
        "shell commands, or instructions to bypass validation. Use only player_id values "
        "that appear in the context packet unless using target_player_id for a trade partner's player. "
        "Keep actions advisory."
    )
    user = {
        "task": "Review the team context and produce one AI GM decision JSON object.",
        "response_schema": schema,
        "context": context,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json_dumps(user)},
    ]


def load_config(con: sqlite3.Connection, game_id: str) -> LlmConfig:
    ensure_schema(con)
    row = con.execute(
        "SELECT * FROM ai_gm_llm_config WHERE game_id = ?",
        (game_id,),
    ).fetchone()
    if row:
        return LlmConfig(
            game_id=row["game_id"],
            provider=row["provider"],
            endpoint=row["endpoint"],
            model=row["model"],
            temperature=float(row["temperature"]),
            max_tokens=int(row["max_tokens"]),
            request_timeout_sec=int(row["request_timeout_sec"]),
            enabled=int(row["enabled"]),
        )
    return LlmConfig(
        game_id=game_id,
        provider=DEFAULT_PROVIDER,
        endpoint=default_endpoint(DEFAULT_PROVIDER),
        model=DEFAULT_MODEL,
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=DEFAULT_MAX_TOKENS,
        request_timeout_sec=DEFAULT_TIMEOUT_SEC,
        enabled=0,
    )


def upsert_config(
    con: sqlite3.Connection,
    *,
    game_id: str,
    provider: str,
    endpoint: str,
    model: str,
    temperature: float,
    max_tokens: int,
    request_timeout_sec: int,
    enabled: int,
) -> None:
    con.execute(
        """
        INSERT INTO ai_gm_llm_config (
            game_id, provider, endpoint, model, temperature, max_tokens,
            request_timeout_sec, enabled, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(game_id) DO UPDATE SET
            provider = excluded.provider,
            endpoint = excluded.endpoint,
            model = excluded.model,
            temperature = excluded.temperature,
            max_tokens = excluded.max_tokens,
            request_timeout_sec = excluded.request_timeout_sec,
            enabled = excluded.enabled,
            updated_at = datetime('now')
        """,
        (
            game_id,
            provider,
            endpoint,
            model,
            temperature,
            max_tokens,
            request_timeout_sec,
            enabled,
        ),
    )


def http_json_post(url: str, payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LLM endpoint {url}: {exc.reason}") from exc
    return json.loads(body)


def call_llm(config: LlmConfig, messages: list[dict[str, str]]) -> tuple[dict[str, Any], str]:
    provider = config.provider.lower()
    if provider == "ollama":
        payload = {
            "model": config.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": config.temperature,
                "num_predict": config.max_tokens,
            },
        }
    elif provider in {"openai_compatible", "lm_studio", "llama_cpp"}:
        payload = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "response_format": {"type": "json_object"},
        }
    else:
        raise ValueError(f"Unsupported AI GM provider: {config.provider}")

    response = http_json_post(config.endpoint, payload, config.request_timeout_sec)
    if provider == "ollama":
        text = (
            response.get("message", {}).get("content")
            or response.get("response")
            or ""
        )
    else:
        choices = response.get("choices") or []
        if choices and "message" in choices[0]:
            text = choices[0]["message"].get("content") or ""
        elif choices:
            text = choices[0].get("text") or ""
        else:
            text = ""
    if not text.strip():
        raise RuntimeError("LLM response did not include message content.")
    return response, text


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first >= 0 and last > first:
            return json.loads(cleaned[first : last + 1])
        raise


def find_unsafe_fragments(value: Any, path: str = "payload") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in UNSAFE_ACTION_KEYS:
                findings.append(f"{path}.{key_text} uses unsafe key {key_text!r}.")
            findings.extend(find_unsafe_fragments(child, f"{path}.{key_text}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_unsafe_fragments(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        lower = value.lower()
        for marker in UNSAFE_TEXT_MARKERS:
            if marker in lower:
                findings.append(f"{path} contains unsafe text marker {marker.strip()!r}.")
                break
    return findings


def player_for_validation(con: sqlite3.Connection, player_id: int) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.team_id,
            p.status,
            t.abbreviation AS team
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE p.player_id = ?
        """,
        (player_id,),
    ).fetchone()
    return dict(row) if row else None


def team_for_validation(con: sqlite3.Connection, abbreviation: str | None) -> dict[str, Any] | None:
    if not abbreviation:
        return None
    row = con.execute(
        "SELECT team_id, abbreviation, city, nickname FROM teams WHERE abbreviation = ?",
        (str(abbreviation).upper(),),
    ).fetchone()
    return dict(row) if row else None


def proposal_for_validation(
    con: sqlite3.Connection,
    proposal_id: int,
    receiving_team_id: int,
) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT proposal_id, proposing_team_id, receiving_team_id, status
        FROM trade_proposals
        WHERE proposal_id = ?
          AND receiving_team_id = ?
          AND status IN ('proposed', 'countered')
        """,
        (proposal_id, receiving_team_id),
    ).fetchone()
    return dict(row) if row else None


def draft_pick_for_validation(con: sqlite3.Connection, pick_id: int) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT pick_id, current_team_id, draft_year, round, pick_number, is_used
        FROM draft_picks
        WHERE pick_id = ?
        """,
        (pick_id,),
    ).fetchone()
    return dict(row) if row else None


def validate_round_value(value: Any) -> bool:
    try:
        round_num = int(value)
    except (TypeError, ValueError):
        return False
    return 1 <= round_num <= 7


def validate_decision_payload(
    con: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    team: sqlite3.Row,
    decision_type: str,
    max_actions: int = 8,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    accepted_actions: list[dict[str, Any]] = []
    allowed_actions = ADVISORY_DECISION_TYPES[decision_type]["allowed_actions"]

    if not isinstance(payload, dict):
        return {"valid": False, "errors": ["Response root must be a JSON object."], "warnings": [], "accepted_actions": []}
    unsafe_payload_findings = find_unsafe_fragments(payload)
    if unsafe_payload_findings:
        errors.extend(unsafe_payload_findings)
    if payload.get("team") != team["abbreviation"]:
        errors.append(f"team must be {team['abbreviation']}.")
    if payload.get("decision_type") != decision_type:
        errors.append(f"decision_type must be {decision_type}.")
    if not isinstance(payload.get("summary"), str) or not payload.get("summary", "").strip():
        errors.append("summary must be a non-empty string.")
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        errors.append("confidence must be a number between 0 and 1.")

    actions = payload.get("actions")
    if not isinstance(actions, list):
        errors.append("actions must be a list.")
        actions = []
    if len(actions) > max_actions:
        errors.append(f"actions may include at most {max_actions} items.")

    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            errors.append(f"actions[{index}] must be an object.")
            continue
        unsafe_keys = sorted(UNSAFE_ACTION_KEYS.intersection(action.keys()))
        if unsafe_keys:
            errors.append(f"actions[{index}] contains unsafe key(s): {', '.join(unsafe_keys)}.")
        action_type = action.get("action_type")
        if action_type in MUTATING_ACTION_TYPES:
            errors.append(f"actions[{index}] action_type {action_type!r} is a mutating action and is not allowed.")
        if action_type not in allowed_actions:
            errors.append(f"actions[{index}] action_type {action_type!r} is not allowed for {decision_type}.")

        player_id = action.get("player_id")
        player = None
        target_team_key = action.get("target_team") or action.get("target_team_abbreviation") or action.get("receiving_team")
        target_team = team_for_validation(con, target_team_key)
        if target_team_key and target_team is None:
            errors.append(f"actions[{index}] target team {target_team_key!r} does not exist.")
        if target_team and int(target_team["team_id"]) == int(team["team_id"]):
            errors.append(f"actions[{index}] target team cannot be {team['abbreviation']}.")
        if player_id is None:
            # trade_proposal and trade_response may reference picks/teams instead
            if decision_type in {
                "camp_cutdown_recommendation",
                "practice_squad_priorities",
                "trade_block_update",
                "extension_interest",
                "free_agent_shortlist",
                "depth_chart_review",
            }:
                errors.append(f"actions[{index}] must include player_id.")
            elif decision_type == "trade_proposal" and action.get("action_type") in (
                "shop_player", "request_player_swap",
            ):
                errors.append(f"actions[{index}] must include player_id for {action.get('action_type')}.")
            elif decision_type == "trade_response" and action.get("action_type") in (
                "counter_trade", "conditionally_accept",
            ) and not action.get("target_player_id") and not action.get("counter_pick_round"):
                warnings.append(f"actions[{index}] counter/conditional trade should specify assets.")
        else:
            try:
                player = player_for_validation(con, int(player_id))
            except (TypeError, ValueError):
                errors.append(f"actions[{index}] player_id must be an integer.")
            if player is None:
                errors.append(f"actions[{index}] player_id {player_id!r} does not exist.")
            elif decision_type == "free_agent_shortlist":
                if player["status"] != "Free Agent":
                    errors.append(
                        f"actions[{index}] player_id {player_id} is not a free agent "
                        f"(status={player['status']})."
                    )
            elif player["team_id"] != team["team_id"]:
                errors.append(
                    f"actions[{index}] player_id {player_id} belongs to "
                    f"{player['team'] or 'no team'}, not {team['abbreviation']}."
                )


        if decision_type == "trade_proposal":
            if action_type in {"propose_trade", "request_draft_pick", "request_player_swap"} and target_team is None:
                errors.append(f"actions[{index}] must include a valid target_team for {action_type}.")
            target_player_id = action.get("target_player_id") or action.get("requested_player_id")
            if target_player_id is not None:
                try:
                    target_player = player_for_validation(con, int(target_player_id))
                except (TypeError, ValueError):
                    target_player = None
                    errors.append(f"actions[{index}] target_player_id must be an integer.")
                if target_player is None:
                    errors.append(f"actions[{index}] target_player_id {target_player_id!r} does not exist.")
                elif target_player["team_id"] == team["team_id"]:
                    errors.append(f"actions[{index}] target_player_id {target_player_id} already belongs to {team['abbreviation']}.")
                elif target_team and target_player["team_id"] != target_team["team_id"]:
                    errors.append(
                        f"actions[{index}] target_player_id {target_player_id} does not belong to "
                        f"{target_team['abbreviation']}."
                    )
            requested_pick_id = action.get("requested_pick_id") or action.get("pick_id")
            if requested_pick_id is not None:
                try:
                    requested_pick = draft_pick_for_validation(con, int(requested_pick_id))
                except (TypeError, ValueError):
                    requested_pick = None
                    errors.append(f"actions[{index}] requested_pick_id must be an integer.")
                if requested_pick is None:
                    errors.append(f"actions[{index}] requested_pick_id {requested_pick_id!r} does not exist.")
                elif requested_pick.get("is_used"):
                    errors.append(f"actions[{index}] requested_pick_id {requested_pick_id} has already been used.")
                elif target_team and requested_pick["current_team_id"] != target_team["team_id"]:
                    errors.append(
                        f"actions[{index}] requested_pick_id {requested_pick_id} is not owned by "
                        f"{target_team['abbreviation']}."
                    )
            requested_pick_round = action.get("requested_pick_round")
            if requested_pick_round is not None and not validate_round_value(requested_pick_round):
                errors.append(f"actions[{index}] requested_pick_round must be an integer from 1 to 7.")
        elif decision_type == "trade_response":
            if action_type in {
                "accept_trade",
                "counter_trade",
                "reject_trade",
                "request_more_value",
                "conditionally_accept",
            }:
                proposal_id = action.get("proposal_id")
                if proposal_id is None:
                    errors.append(f"actions[{index}] must include proposal_id for {action_type}.")
                else:
                    try:
                        proposal = proposal_for_validation(con, int(proposal_id), int(team["team_id"]))
                    except (TypeError, ValueError):
                        proposal = None
                        errors.append(f"actions[{index}] proposal_id must be an integer.")
                    if proposal is None:
                        errors.append(
                            f"actions[{index}] proposal_id {proposal_id!r} is not a pending incoming proposal "
                            f"for {team['abbreviation']}."
                        )
            counter_pick_round = action.get("counter_pick_round")
            if counter_pick_round is not None and not validate_round_value(counter_pick_round):
                errors.append(f"actions[{index}] counter_pick_round must be an integer from 1 to 7.")
            if action_type == "conditionally_accept" and not action.get("conditions"):
                warnings.append(f"actions[{index}] conditionally_accept should include conditions.")
            target_player_id = action.get("target_player_id") or action.get("requested_player_id")
            if target_player_id is not None:
                try:
                    target_player = player_for_validation(con, int(target_player_id))
                except (TypeError, ValueError):
                    target_player = None
                    errors.append(f"actions[{index}] target_player_id must be an integer.")
                if target_player is None:
                    errors.append(f"actions[{index}] target_player_id {target_player_id!r} does not exist.")
                elif target_player["team_id"] == team["team_id"]:
                    warnings.append(
                        f"actions[{index}] target_player_id {target_player_id} is already on {team['abbreviation']}."
                    )

        if not isinstance(action.get("reason"), str) or not action.get("reason", "").strip():
            warnings.append(f"actions[{index}] is missing a useful reason.")
        accepted = dict(action)
        if player:
            accepted["validated_player"] = {
                "player_id": player["player_id"],
                "player_name": player["player_name"],
                "position": player["position"],
                "team": player["team"],
                "status": player["status"],
            }
        accepted_actions.append(accepted)

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "accepted_actions": accepted_actions if not errors else [],
    }


def insert_queue(
    con: sqlite3.Connection,
    context: dict[str, Any],
    *,
    priority: int,
) -> int:
    cur = con.execute(
        """
        INSERT INTO ai_gm_decision_queue (
            game_id, team_id, decision_date, decision_type, context_json,
            status, priority, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'running', ?, datetime('now'), datetime('now'))
        """,
        (
            context["game"]["game_id"],
            int(context["team"]["team_id"]),
            context["game"]["current_date"],
            context["decision_request"]["decision_type"],
            json_dumps(context),
            priority,
        ),
    )
    return int(cur.lastrowid)


def update_queue(con: sqlite3.Connection, queue_id: int, status: str) -> None:
    con.execute(
        """
        UPDATE ai_gm_decision_queue
        SET status = ?, updated_at = datetime('now')
        WHERE decision_id = ?
        """,
        (status, queue_id),
    )


def log_decision(
    con: sqlite3.Connection,
    *,
    queue_id: int | None,
    context: dict[str, Any],
    config: LlmConfig | None,
    prompt: list[dict[str, str]],
    response_payload: dict[str, Any] | None,
    raw_response_text: str | None,
    validation: dict[str, Any],
    status: str,
    action_taken: str,
    error_message: str | None = None,
) -> int:
    response_json = None
    if response_payload is not None or raw_response_text is not None:
        response_json = json_dumps(
            {
                "payload": response_payload,
                "raw_text": raw_response_text,
            }
        )
    cur = con.execute(
        """
        INSERT INTO ai_gm_decision_log (
            queue_id, game_id, team_id, decision_date, decision_type,
            provider, endpoint, model, prompt_json, response_json,
            validation_result, action_taken, status, error_message, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            queue_id,
            context["game"]["game_id"],
            int(context["team"]["team_id"]),
            context["game"]["current_date"],
            context["decision_request"]["decision_type"],
            config.provider if config else None,
            config.endpoint if config else None,
            config.model if config else None,
            json_dumps({"messages": prompt}),
            response_json,
            json_dumps(validation),
            action_taken,
            status,
            error_message,
        ),
    )
    return int(cur.lastrowid)


def run_ai_decision(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    decision_type: str,
    game_id: str | None,
    priority: int,
    print_prompt: bool = False,
) -> dict[str, Any]:
    context = build_team_context(
        con,
        team_abbr=team_abbr,
        decision_type=decision_type,
        game_id=game_id,
    )
    prompt = build_prompt(context)
    if print_prompt:
        print(json_dumps({"messages": prompt}))

    config = load_config(con, context["game"]["game_id"])
    if not config.enabled:
        raise ValueError(
            f"AI GM LLM is disabled for game_id={context['game']['game_id']}. "
            "Run ai-gm config --enable first."
        )
    queue_id = insert_queue(con, context, priority=priority)
    raw_llm_response: dict[str, Any] | None = None
    raw_text: str | None = None
    parsed: dict[str, Any] | None = None
    try:
        raw_llm_response, raw_text = call_llm(config, prompt)
        parsed = parse_json_response(raw_text)
        team = get_team(con, team_abbr)
        validation = validate_decision_payload(
            con,
            parsed,
            team=team,
            decision_type=decision_type,
            max_actions=int(context["decision_request"]["max_actions"]),
        )
        status = "valid" if validation["valid"] else "invalid"
        action_taken = "ADVISORY_ONLY: response logged; no roster, contract, cap, or draft tables were changed."
        log_id = log_decision(
            con,
            queue_id=queue_id,
            context=context,
            config=config,
            prompt=prompt,
            response_payload=parsed,
            raw_response_text=raw_text,
            validation=validation,
            status=status,
            action_taken=action_taken,
        )
        update_queue(con, queue_id, "completed" if validation["valid"] else "invalid")
        con.commit()
        return {
            "log_id": log_id,
            "queue_id": queue_id,
            "status": status,
            "decision": parsed,
            "validation": validation,
            "action_taken": action_taken,
        }
    except Exception as exc:
        validation = {
            "valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "accepted_actions": [],
        }
        log_id = log_decision(
            con,
            queue_id=queue_id,
            context=context,
            config=config,
            prompt=prompt,
            response_payload=parsed or raw_llm_response,
            raw_response_text=raw_text,
            validation=validation,
            status="failed",
            action_taken="NONE: LLM request failed or returned invalid JSON before validation.",
            error_message=str(exc),
        )
        update_queue(con, queue_id, "failed")
        con.commit()
        raise RuntimeError(f"AI GM decision failed; log_id={log_id}: {exc}") from exc


def print_decision_result(result: dict[str, Any]) -> None:
    decision = result["decision"]
    validation = result["validation"]
    print(f"AI GM decision log: {result['log_id']} ({result['status']})")
    print(f"Summary: {decision.get('summary')}")
    print(f"Confidence: {decision.get('confidence')}")
    if validation["errors"]:
        print("Validation errors:")
        for error in validation["errors"]:
            print(f"  - {error}")
    if validation["warnings"]:
        print("Validation warnings:")
        for warning in validation["warnings"]:
            print(f"  - {warning}")
    actions = validation["accepted_actions"] or decision.get("actions") or []
    if actions:
        print("Actions:")
        for action in actions:
            player = action.get("validated_player") or {}
            detail_parts = []
            if action.get("player_id") is not None:
                detail_parts.append(f"player_id={action.get('player_id')}")
            if player:
                detail_parts.append(f"{player.get('player_name')} ({player.get('position')})")
            for key in ("proposal_id", "target_team", "target_team_abbreviation", "requested_pick_id", "requested_pick_round"):
                if action.get(key) is not None:
                    detail_parts.append(f"{key}={action.get(key)}")
            details = f" [{'; '.join(detail_parts)}]" if detail_parts else ""
            print(f"  - {action.get('action_type')}{details}: {action.get('reason', '')}")
    print(result["action_taken"])


def action_setup(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    if args.backup and args.db.exists():
        backup_path = backup_sqlite(args.db, "ai_gm")
        print(f"Backup created: {backup_path}")
    ensure_schema(con)
    counts = (
        seed_profiles(con, season=args.season, overwrite=args.overwrite_profiles)
        if args.seed_profiles
        else {"profiles": 0, "profiles_updated": 0, "objectives": 0}
    )
    trade_counts = (
        ensure_trade_support(con, chart_seed=args.trade_chart_seed)
        if args.seed_trade_charts
        else {"chart_points": 0, "chart_assignments": 0}
    )
    con.commit()
    print("AI GM schema is ready.")
    if args.seed_profiles:
        print(f"Profiles inserted: {counts['profiles']}")
        print(f"Profiles acquisition context updated: {counts.get('profiles_updated', 0)}")
        print(f"Objectives inserted: {counts['objectives']}")
    if args.seed_trade_charts:
        print(f"Trade chart points upserted: {trade_counts['chart_points']}")
        print(f"GM trade charts assigned: {trade_counts['chart_assignments']}")


def action_seed_profiles(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    counts = seed_profiles(con, season=args.season, overwrite=args.overwrite)
    trade_counts = (
        ensure_trade_support(con, chart_seed=args.trade_chart_seed)
        if args.seed_trade_charts
        else {"chart_points": 0, "chart_assignments": 0}
    )
    con.commit()
    print(f"Profiles inserted: {counts['profiles']}")
    print(f"Profiles acquisition context updated: {counts.get('profiles_updated', 0)}")
    print(f"Objectives inserted: {counts['objectives']}")
    if args.seed_trade_charts:
        print(f"Trade chart points upserted: {trade_counts['chart_points']}")
        print(f"GM trade charts assigned: {trade_counts['chart_assignments']}")


def action_profiles(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_profiles(con, season=args.season)
    con.commit()
    if args.team:
        rows = con.execute(
            "SELECT * FROM ai_gm_profiles_view WHERE abbreviation = ?",
            (args.team.upper(),),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM ai_gm_profiles_view ORDER BY abbreviation",
        ).fetchall()
    for row in rows:
        print(f"{row['abbreviation']} {row['city']} {row['nickname']} - {row['gm_name']}")
        if row["real_life_gm_name"]:
            since = f" since {row['gm_tenure_start_year']}" if row["gm_tenure_start_year"] else ""
            print(f"  Real GM: {row['real_life_gm_name']} ({row['gm_title'] or 'GM'}{since})")
        print(f"  Personality: {row['personality']}")
        print(f"  Build state: {row['team_build_state']}")
        print(f"  Tendencies: {row['team_tendency_summary']}")
        print(f"  Philosophy: {row['roster_philosophy']}")
        print(f"  Depth chart: {row['depth_chart_policy']}")
        print(f"  Release: {row['release_policy']}")
        print(f"  Youth/Veteran: {row['youth_vs_veteran_policy']}")
        print(f"  Future: {row['future_build_policy']}")
        print(f"  Cap: {row['cap_tolerance']}")
        print(f"  Draft: {row['draft_policy'] or row['draft_tendency']}")
        print(f"  Free agency: {row['free_agency_policy']}")
        if "current_mandate" in row.keys() and row["current_mandate"]:
            print(f"  Mandate: {row['current_mandate']}")
        if "scheme_fit_policy" in row.keys() and row["scheme_fit_policy"]:
            print(f"  Scheme fit: {row['scheme_fit_policy']}")
        if "draft_pick_policy" in row.keys() and row["draft_pick_policy"]:
            print(f"  Pick discipline: {row['draft_pick_policy']}")
        if "free_agent_cap_policy" in row.keys() and row["free_agent_cap_policy"]:
            print(f"  FA cap discipline: {row['free_agent_cap_policy']}")
        if "negotiation_style" in row.keys() and row["negotiation_style"]:
            print(f"  Negotiation: {row['negotiation_style']}")
        print(f"  Trades: {row['trade_policy'] or row['trade_aggression']}")
        if "trade_value_chart" in row.keys():
            deviation = row["chart_deviation_factor"] if row["chart_deviation_factor"] is not None else 0.15
            print(f"  Trade chart: {row['trade_value_chart'] or 'unassigned'} (deviation {deviation})")
        print(f"  Youth: {row['patience_with_young_players']}")
        if row["source_note"]:
            print(f"  Source note: {row['source_note']}")


def action_config(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game_state = resolve_game_state(con, args.game_id)
    current = load_config(con, game_state["game_id"])
    provider = args.provider or current.provider
    if args.endpoint:
        endpoint = args.endpoint
    elif args.provider and args.provider != current.provider:
        endpoint = default_endpoint(provider)
    else:
        endpoint = current.endpoint or default_endpoint(provider)
    model = args.model or current.model
    temperature = args.temperature if args.temperature is not None else current.temperature
    max_tokens = args.max_tokens if args.max_tokens is not None else current.max_tokens
    timeout = args.timeout if args.timeout is not None else current.request_timeout_sec
    enabled = current.enabled
    if args.enable:
        enabled = 1
    if args.disable:
        enabled = 0
    upsert_config(
        con,
        game_id=game_state["game_id"],
        provider=provider,
        endpoint=endpoint,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout_sec=timeout,
        enabled=enabled,
    )
    con.commit()
    print(f"AI GM config saved for game_id={game_state['game_id']}")
    print(f"  enabled: {bool(enabled)}")
    print(f"  provider: {provider}")
    print(f"  endpoint: {endpoint}")
    print(f"  model: {model}")
    print(f"  temperature: {temperature}")
    print(f"  max_tokens: {max_tokens}")


def action_show_config(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    config = load_config(con, game_state["game_id"])
    print(f"AI GM config for game_id={config.game_id}")
    print(f"  enabled: {bool(config.enabled)}")
    print(f"  provider: {config.provider}")
    print(f"  endpoint: {config.endpoint}")
    print(f"  model: {config.model}")
    print(f"  temperature: {config.temperature}")
    print(f"  max_tokens: {config.max_tokens}")
    print(f"  timeout: {config.request_timeout_sec}s")


def action_context(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    context = build_team_context(
        con,
        team_abbr=args.team,
        decision_type=args.decision_type,
        game_id=args.game_id,
        max_players=args.max_players,
        max_free_agents=args.max_free_agents,
    )
    con.commit()
    print(json_dumps(context))


def action_run(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    result = run_ai_decision(
        con,
        team_abbr=args.team,
        decision_type=args.decision_type,
        game_id=args.game_id,
        priority=args.priority,
        print_prompt=args.print_prompt,
    )
    if args.json:
        print(json_dumps(result))
    else:
        print_decision_result(result)


def action_logs(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    params: list[Any] = []
    where = []
    if args.game_id:
        where.append("game_id = ?")
        params.append(args.game_id)
    if args.team:
        where.append("team = ?")
        params.append(args.team.upper())
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = con.execute(
        f"""
        SELECT *
        FROM ai_gm_decision_log_view
        {clause}
        ORDER BY created_at DESC, decision_log_id DESC
        LIMIT ?
        """,
        (*params, args.limit),
    ).fetchall()
    for row in rows:
        print(
            f"{row['decision_log_id']} {row['created_at']} "
            f"{row['game_id']} {row['team']} {row['decision_type']} {row['status']}"
        )
        print(f"  {row['action_taken']}")
        if row["error_message"]:
            print(f"  Error: {row['error_message']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local LLM AI GM tools.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create AI GM tables and seed default profiles.")
    setup_parser.add_argument("--no-backup", dest="backup", action="store_false", help="Skip DB backup before setup.")
    setup_parser.set_defaults(backup=True)
    setup_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    setup_parser.add_argument("--no-seed-profiles", dest="seed_profiles", action="store_false")
    setup_parser.add_argument("--overwrite-profiles", action="store_true", help="Refresh generated team profiles.")
    setup_parser.add_argument("--no-trade-charts", dest="seed_trade_charts", action="store_false", help="Skip trade chart seeding/assignment.")
    setup_parser.add_argument("--trade-chart-seed", type=int, default=TRADE_CHART_ASSIGNMENT_SEED)
    setup_parser.set_defaults(seed_profiles=True, seed_trade_charts=True)

    seed_parser = subparsers.add_parser("seed-profiles", help="Seed missing team AI GM profiles/objectives.")
    seed_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    seed_parser.add_argument("--overwrite", action="store_true", help="Refresh generated team profiles.")
    seed_parser.add_argument("--no-trade-charts", dest="seed_trade_charts", action="store_false", help="Skip trade chart seeding/assignment.")
    seed_parser.add_argument("--trade-chart-seed", type=int, default=TRADE_CHART_ASSIGNMENT_SEED)
    seed_parser.set_defaults(seed_trade_charts=True)

    profiles_parser = subparsers.add_parser("profiles", help="Show AI GM profiles.")
    profiles_parser.add_argument("--team")
    profiles_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)

    config_parser = subparsers.add_parser("config", help="Set local LLM config for a save/game.")
    config_parser.add_argument("--game-id")
    config_parser.add_argument("--provider", choices=["ollama", "openai_compatible", "lm_studio", "llama_cpp"])
    config_parser.add_argument("--endpoint")
    config_parser.add_argument("--model")
    config_parser.add_argument("--temperature", type=float)
    config_parser.add_argument("--max-tokens", type=int)
    config_parser.add_argument("--timeout", type=int)
    config_parser.add_argument("--enable", action="store_true")
    config_parser.add_argument("--disable", action="store_true")

    show_config_parser = subparsers.add_parser("show-config", help="Show local LLM config.")
    show_config_parser.add_argument("--game-id")

    context_parser = subparsers.add_parser("context", help="Print the team context packet for an AI GM decision.")
    context_parser.add_argument("--game-id")
    context_parser.add_argument("--team", required=True)
    context_parser.add_argument("--decision-type", required=True, choices=sorted(ADVISORY_DECISION_TYPES))
    context_parser.add_argument("--max-players", type=int, default=18)
    context_parser.add_argument("--max-free-agents", type=int, default=18)

    run_parser = subparsers.add_parser("run", help="Call the configured local LLM and log an advisory decision.")
    run_parser.add_argument("--game-id")
    run_parser.add_argument("--team", required=True)
    run_parser.add_argument("--decision-type", required=True, choices=sorted(ADVISORY_DECISION_TYPES))
    run_parser.add_argument("--priority", type=int, default=5)
    run_parser.add_argument("--print-prompt", action="store_true")
    run_parser.add_argument("--json", action="store_true")

    logs_parser = subparsers.add_parser("logs", help="Show AI GM decision logs.")
    logs_parser.add_argument("--game-id")
    logs_parser.add_argument("--team")
    logs_parser.add_argument("--limit", type=int, default=20)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    con = connect(args.db)
    try:
        if args.command == "setup":
            action_setup(con, args)
        elif args.command == "seed-profiles":
            action_seed_profiles(con, args)
        elif args.command == "profiles":
            action_profiles(con, args)
        elif args.command == "config":
            if args.enable and args.disable:
                raise ValueError("Use only one of --enable or --disable.")
            action_config(con, args)
        elif args.command == "show-config":
            action_show_config(con, args)
        elif args.command == "context":
            action_context(con, args)
        elif args.command == "run":
            action_run(con, args)
        elif args.command == "logs":
            action_logs(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
