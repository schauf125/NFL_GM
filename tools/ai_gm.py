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

import ai_gm_contract_planner as contract_planner
import ai_gm_cutdown_planner as cutdown_planner
import ai_gm_draft_planner as draft_planner
import ai_gm_free_agent_planner as free_agent_planner
import ai_gm_operating_models as gm_operating_models
import ai_gm_offseason_driver as offseason_driver
import ai_gm_ops_controller as ops_controller
import ai_gm_team_evaluator as team_eval
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
AUTONOMY_MODES = {
    "advisory_only",
    "auto_apply_low_risk",
    "review_required_major_moves",
    "full_cpu_control",
}
AUTONOMY_MODE_RANK = {
    "advisory_only": 0,
    "auto_apply_low_risk": 1,
    "review_required_major_moves": 2,
    "full_cpu_control": 3,
}
OPERATION_RISK_TIERS = {
    "review_cutdown_plan": "low",
    "practice_squad_priorities": "low",
    "depth_chart_review": "low",
    "free_agent_shortlist": "medium",
    "free_agent_plan": "medium",
    "extension_review": "medium",
    "contract_plan": "medium",
    "draft_strategy": "medium",
    "trade_block_review": "high",
    "trade_proposal": "high",
    "trade_response": "high",
}
AUTO_APPLY_OPERATION_TYPES = {"review_cutdown_plan", "contract_plan", "free_agent_shortlist", "free_agent_plan"}
REVIEW_LIFECYCLE_STATUSES = {
    "pending_review",
    "approved",
    "rejected",
    "expired",
    "stale",
    "applied",
    "blocked",
}

GM_SOURCE_NAME = "Wikipedia current NFL general managers list"
GM_SOURCE_URL = "https://en.wikipedia.org/wiki/General_manager_(American_football)#List_of_current_NFL_general_managers"
GM_SOURCE_RETRIEVED_AT = "2026-05-05"

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


@dataclass(frozen=True)
class AutonomySettings:
    game_id: str
    team_id: int | None
    mode: str
    queue_llm_advisory: int
    auto_apply_low_risk: int
    review_medium_risk: int
    review_high_risk: int
    include_user_team: int
    max_operations_per_day: int
    max_auto_apply_per_day: int


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
    for column_name, column_sql in [
        ("applied_at", "applied_at TEXT"),
        ("apply_result_json", "apply_result_json TEXT"),
        ("apply_error", "apply_error TEXT"),
    ]:
        ensure_column(con, "ai_gm_review_items", column_name, column_sql)
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

        CREATE TABLE IF NOT EXISTS ai_gm_autonomy_settings (
            setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE CASCADE,
            mode TEXT NOT NULL DEFAULT 'advisory_only',
            queue_llm_advisory INTEGER NOT NULL DEFAULT 1,
            auto_apply_low_risk INTEGER NOT NULL DEFAULT 0,
            review_medium_risk INTEGER NOT NULL DEFAULT 1,
            review_high_risk INTEGER NOT NULL DEFAULT 1,
            include_user_team INTEGER NOT NULL DEFAULT 0,
            max_operations_per_day INTEGER NOT NULL DEFAULT 20,
            max_auto_apply_per_day INTEGER NOT NULL DEFAULT 4,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(mode IN ('advisory_only', 'auto_apply_low_risk', 'review_required_major_moves', 'full_cpu_control')),
            CHECK(queue_llm_advisory IN (0, 1)),
            CHECK(auto_apply_low_risk IN (0, 1)),
            CHECK(review_medium_risk IN (0, 1)),
            CHECK(review_high_risk IN (0, 1)),
            CHECK(include_user_team IN (0, 1)),
            UNIQUE(game_id, team_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_gm_autonomy_default
            ON ai_gm_autonomy_settings(game_id)
            WHERE team_id IS NULL;

        CREATE TABLE IF NOT EXISTS ai_gm_daily_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            run_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            phase_code TEXT,
            scope_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            all_teams INTEGER NOT NULL DEFAULT 0,
            autonomy_mode TEXT NOT NULL,
            persist_mode INTEGER NOT NULL DEFAULT 0,
            apply_mode INTEGER NOT NULL DEFAULT 0,
            operations_scanned INTEGER NOT NULL DEFAULT 0,
            operations_planned INTEGER NOT NULL DEFAULT 0,
            operations_enqueued INTEGER NOT NULL DEFAULT 0,
            operations_applied INTEGER NOT NULL DEFAULT 0,
            operations_blocked INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ai_gm_review_items (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            run_id INTEGER REFERENCES ai_gm_daily_runs(run_id) ON DELETE SET NULL,
            review_date TEXT NOT NULL,
            season INTEGER NOT NULL,
            phase_code TEXT,
            item_type TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            artifact_id INTEGER,
            operation_key TEXT,
            operation_type TEXT,
            decision_type TEXT,
            risk_tier TEXT NOT NULL DEFAULT 'medium',
            priority INTEGER NOT NULL DEFAULT 5,
            title TEXT NOT NULL,
            summary TEXT,
            lifecycle_status TEXT NOT NULL DEFAULT 'pending_review',
            review_note TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            applied_at TEXT,
            apply_result_json TEXT,
            apply_error TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(priority BETWEEN 1 AND 10),
            CHECK(risk_tier IN ('low', 'medium', 'high')),
            CHECK(lifecycle_status IN ('pending_review', 'approved', 'rejected', 'expired', 'stale', 'applied', 'blocked'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_gm_review_items_artifact
            ON ai_gm_review_items(game_id, artifact_type, artifact_id)
            WHERE artifact_id IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_ai_gm_review_items_inbox
            ON ai_gm_review_items(game_id, lifecycle_status, priority DESC, created_at DESC);

        DROP VIEW IF EXISTS ai_gm_autonomy_settings_view;
        CREATE VIEW ai_gm_autonomy_settings_view AS
        SELECT
            s.setting_id,
            s.game_id,
            s.team_id,
            t.abbreviation AS team,
            s.mode,
            s.queue_llm_advisory,
            s.auto_apply_low_risk,
            s.review_medium_risk,
            s.review_high_risk,
            s.include_user_team,
            s.max_operations_per_day,
            s.max_auto_apply_per_day,
            s.updated_at
        FROM ai_gm_autonomy_settings s
        LEFT JOIN teams t ON t.team_id = s.team_id;

        DROP VIEW IF EXISTS ai_gm_daily_runs_view;
        CREATE VIEW ai_gm_daily_runs_view AS
        SELECT
            r.run_id,
            r.game_id,
            r.run_date,
            r.season,
            r.phase_code,
            r.scope_team_id,
            t.abbreviation AS scope_team,
            r.all_teams,
            r.autonomy_mode,
            r.persist_mode,
            r.apply_mode,
            r.operations_scanned,
            r.operations_planned,
            r.operations_enqueued,
            r.operations_applied,
            r.operations_blocked,
            r.created_at
        FROM ai_gm_daily_runs r
        LEFT JOIN teams t ON t.team_id = r.scope_team_id;

        DROP VIEW IF EXISTS ai_gm_review_items_view;
        CREATE VIEW ai_gm_review_items_view AS
        SELECT
            r.review_id,
            r.game_id,
            r.team_id,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            r.run_id,
            r.review_date,
            r.season,
            r.phase_code,
            r.item_type,
            r.artifact_type,
            r.artifact_id,
            r.operation_key,
            r.operation_type,
            r.decision_type,
            r.risk_tier,
            r.priority,
            r.title,
            r.summary,
            r.lifecycle_status,
            r.review_note,
            r.reviewed_at,
            r.reviewed_by,
            r.applied_at,
            r.apply_result_json,
            r.apply_error,
            r.detail_json,
            r.created_at,
            r.updated_at
        FROM ai_gm_review_items r
        JOIN teams t ON t.team_id = r.team_id;

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
    team_eval.ensure_schema(con)
    cutdown_planner.ensure_schema(con)
    contract_planner.ensure_schema(con)
    draft_planner.ensure_schema(con)
    free_agent_planner.ensure_schema(con)


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

    profile = {
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
    return gm_operating_models.apply_operating_model(
        profile,
        team_abbr=team["abbreviation"],
        team_name=f"{team['city']} {team['nickname']}",
    )


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
    team_evaluation = team_eval.evaluate_team(
        con,
        team_abbr=team["abbreviation"],
        season=season,
        game_id=game_state["game_id"],
        evaluation_date=game_state["current_date"],
        persist=False,
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
        "team_evaluation": team_evaluation,
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
        "Use team_evaluation as the deterministic football-ops baseline for team phase, "
        "roster needs, surplus rooms, contract pressure, cut candidates, practice squad priorities, "
        "extension candidates, trade-block candidates, and risk flags. "
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


def normalize_autonomy_mode(mode: str | None) -> str:
    value = (mode or "advisory_only").strip().lower().replace("-", "_")
    if value not in AUTONOMY_MODES:
        raise ValueError(f"Unknown AI GM autonomy mode: {mode}")
    return value


def autonomy_settings_from_row(row: sqlite3.Row, *, game_id: str, team_id: int | None) -> AutonomySettings:
    return AutonomySettings(
        game_id=str(row["game_id"] if "game_id" in row.keys() else game_id),
        team_id=row["team_id"] if "team_id" in row.keys() else team_id,
        mode=normalize_autonomy_mode(str(row["mode"] or "advisory_only")),
        queue_llm_advisory=int(row["queue_llm_advisory"] or 0),
        auto_apply_low_risk=int(row["auto_apply_low_risk"] or 0),
        review_medium_risk=int(row["review_medium_risk"] or 0),
        review_high_risk=int(row["review_high_risk"] or 0),
        include_user_team=int(row["include_user_team"] or 0),
        max_operations_per_day=max(1, int(row["max_operations_per_day"] or 20)),
        max_auto_apply_per_day=max(0, int(row["max_auto_apply_per_day"] or 4)),
    )


def default_autonomy_settings(game_id: str, team_id: int | None = None) -> AutonomySettings:
    return AutonomySettings(
        game_id=game_id,
        team_id=team_id,
        mode="auto_apply_low_risk",
        queue_llm_advisory=0,
        auto_apply_low_risk=1,
        review_medium_risk=1,
        review_high_risk=1,
        include_user_team=0,
        max_operations_per_day=20,
        max_auto_apply_per_day=4,
    )


def load_autonomy_settings(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int | None = None,
) -> AutonomySettings:
    ensure_schema(con)
    lookups: list[tuple[str, int | None]] = []
    if team_id is not None:
        lookups.append((game_id, team_id))
    lookups.append((game_id, None))
    if game_id != "master":
        if team_id is not None:
            lookups.append(("master", team_id))
        lookups.append(("master", None))

    for lookup_game_id, lookup_team_id in lookups:
        if lookup_team_id is None:
            row = con.execute(
                """
                SELECT *
                FROM ai_gm_autonomy_settings
                WHERE game_id = ? AND team_id IS NULL
                ORDER BY setting_id DESC
                LIMIT 1
                """,
                (lookup_game_id,),
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT *
                FROM ai_gm_autonomy_settings
                WHERE game_id = ? AND team_id = ?
                ORDER BY setting_id DESC
                LIMIT 1
                """,
                (lookup_game_id, lookup_team_id),
            ).fetchone()
        if row:
            return autonomy_settings_from_row(row, game_id=game_id, team_id=team_id)
    return default_autonomy_settings(game_id, team_id)


def upsert_autonomy_settings(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int | None,
    mode: str,
    queue_llm_advisory: int,
    auto_apply_low_risk: int,
    review_medium_risk: int,
    review_high_risk: int,
    include_user_team: int,
    max_operations_per_day: int,
    max_auto_apply_per_day: int,
) -> None:
    mode = normalize_autonomy_mode(mode)
    values = (
        mode,
        int(bool(queue_llm_advisory)),
        int(bool(auto_apply_low_risk)),
        int(bool(review_medium_risk)),
        int(bool(review_high_risk)),
        int(bool(include_user_team)),
        max(1, int(max_operations_per_day)),
        max(0, int(max_auto_apply_per_day)),
    )
    if team_id is None:
        existing = con.execute(
            "SELECT setting_id FROM ai_gm_autonomy_settings WHERE game_id = ? AND team_id IS NULL",
            (game_id,),
        ).fetchone()
        if existing:
            con.execute(
                """
                UPDATE ai_gm_autonomy_settings
                SET mode = ?,
                    queue_llm_advisory = ?,
                    auto_apply_low_risk = ?,
                    review_medium_risk = ?,
                    review_high_risk = ?,
                    include_user_team = ?,
                    max_operations_per_day = ?,
                    max_auto_apply_per_day = ?,
                    updated_at = datetime('now')
                WHERE setting_id = ?
                """,
                (*values, int(existing["setting_id"])),
            )
            return
        con.execute(
            """
            INSERT INTO ai_gm_autonomy_settings (
                game_id, team_id, mode, queue_llm_advisory, auto_apply_low_risk,
                review_medium_risk, review_high_risk, include_user_team,
                max_operations_per_day, max_auto_apply_per_day, created_at, updated_at
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (game_id, *values),
        )
        return
    con.execute(
        """
        INSERT INTO ai_gm_autonomy_settings (
            game_id, team_id, mode, queue_llm_advisory, auto_apply_low_risk,
            review_medium_risk, review_high_risk, include_user_team,
            max_operations_per_day, max_auto_apply_per_day, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(game_id, team_id) DO UPDATE SET
            mode = excluded.mode,
            queue_llm_advisory = excluded.queue_llm_advisory,
            auto_apply_low_risk = excluded.auto_apply_low_risk,
            review_medium_risk = excluded.review_medium_risk,
            review_high_risk = excluded.review_high_risk,
            include_user_team = excluded.include_user_team,
            max_operations_per_day = excluded.max_operations_per_day,
            max_auto_apply_per_day = excluded.max_auto_apply_per_day,
            updated_at = datetime('now')
        """,
        (game_id, team_id, *values),
    )


def autonomy_settings_to_dict(settings: AutonomySettings) -> dict[str, Any]:
    return {
        "game_id": settings.game_id,
        "team_id": settings.team_id,
        "mode": settings.mode,
        "queue_llm_advisory": bool(settings.queue_llm_advisory),
        "auto_apply_low_risk": bool(settings.auto_apply_low_risk),
        "review_medium_risk": bool(settings.review_medium_risk),
        "review_high_risk": bool(settings.review_high_risk),
        "include_user_team": bool(settings.include_user_team),
        "max_operations_per_day": settings.max_operations_per_day,
        "max_auto_apply_per_day": settings.max_auto_apply_per_day,
    }


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


def queued_operation_exists(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int,
    decision_date: str,
    decision_type: str,
    operation_key: str,
) -> int | None:
    row = con.execute(
        """
        SELECT decision_id
        FROM ai_gm_decision_queue
        WHERE game_id = ?
          AND team_id = ?
          AND decision_date = ?
          AND decision_type = ?
          AND status IN ('queued', 'running')
          AND context_json LIKE ?
        ORDER BY decision_id DESC
        LIMIT 1
        """,
        (
            game_id,
            team_id,
            decision_date,
            decision_type,
            f'%"operation_key": "{operation_key}"%',
        ),
    ).fetchone()
    return int(row["decision_id"]) if row else None


def enqueue_operation_context(
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
        VALUES (?, ?, ?, ?, ?, 'queued', ?, datetime('now'), datetime('now'))
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


def queue_rows(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    team_abbr: str | None = None,
    status: str | None = "queued",
    limit: int = 20,
    include_context: bool = False,
) -> list[dict[str, Any]]:
    ensure_schema(con)
    params: list[Any] = []
    where: list[str] = []
    if game_id:
        where.append("q.game_id = ?")
        params.append(game_id)
    if team_abbr:
        where.append("t.abbreviation = ?")
        params.append(team_abbr.upper())
    if status and status != "all":
        where.append("q.status = ?")
        params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = con.execute(
        f"""
        SELECT
            q.decision_id,
            q.game_id,
            q.decision_date,
            q.decision_type,
            q.status,
            q.priority,
            q.context_json,
            q.created_at,
            q.updated_at,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name
        FROM ai_gm_decision_queue q
        JOIN teams t ON t.team_id = q.team_id
        {clause}
        ORDER BY
            CASE q.status WHEN 'queued' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,
            q.priority DESC,
            q.decision_date ASC,
            q.decision_id ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = {key: row[key] for key in row.keys() if key != "context_json"}
        context = None
        try:
            context = json.loads(row["context_json"])
            operation = context.get("ai_gm_operation") or {}
            request = context.get("decision_request") or {}
            item["operation_type"] = operation.get("operation_type")
            item["ops_phase"] = operation.get("ops_phase")
            item["operation_key"] = operation.get("operation_key") or request.get("operation_key")
            item["summary"] = operation.get("summary") or request.get("prompt")
            item["drivers"] = operation.get("drivers") or []
        except (TypeError, json.JSONDecodeError) as exc:
            item["context_error"] = str(exc)
        if include_context:
            item["context"] = context
        items.append(item)
    return items


def print_queue_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No AI GM queue rows found.")
        return
    print(f"{'ID':>4} {'TEAM':<4} {'DATE':<10} {'PRI':>3} {'STATUS':<10} {'TYPE':<26} {'OPERATION':<24}")
    for row in rows:
        print(
            f"{row['decision_id']:>4} {row['team']:<4} {row['decision_date']:<10} "
            f"{row['priority']:>3} {row['status']:<10} {row['decision_type']:<26} "
            f"{str(row.get('operation_type') or '-')[:24]:<24}"
        )
        if row.get("summary"):
            print(f"      {row['summary']}")
        if row.get("context_error"):
            print(f"      Context error: {row['context_error']}")


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


def process_queue_row(
    con: sqlite3.Connection,
    row: dict[str, Any],
    *,
    print_prompt: bool = False,
) -> dict[str, Any]:
    queue_id = int(row["decision_id"])
    context = row.get("context")
    if not isinstance(context, dict):
        update_queue(con, queue_id, "failed")
        con.commit()
        return {
            "queue_id": queue_id,
            "status": "failed",
            "error": row.get("context_error") or "Queue row does not contain a valid context packet.",
        }

    prompt = build_prompt(context)
    if print_prompt:
        print(json_dumps({"queue_id": queue_id, "messages": prompt}))

    config = load_config(con, context["game"]["game_id"])
    if not config.enabled:
        return {
            "queue_id": queue_id,
            "team": row["team"],
            "decision_type": row["decision_type"],
            "status": "skipped_disabled",
            "error": (
                f"AI GM LLM is disabled for game_id={context['game']['game_id']}. "
                "Run ai-gm config --enable first."
            ),
        }

    update_queue(con, queue_id, "running")
    con.commit()
    raw_llm_response: dict[str, Any] | None = None
    raw_text: str | None = None
    parsed: dict[str, Any] | None = None
    try:
        raw_llm_response, raw_text = call_llm(config, prompt)
        parsed = parse_json_response(raw_text)
        team = get_team(con, context["team"]["abbreviation"])
        validation = validate_decision_payload(
            con,
            parsed,
            team=team,
            decision_type=context["decision_request"]["decision_type"],
            max_actions=int(context["decision_request"]["max_actions"]),
        )
        status = "valid" if validation["valid"] else "invalid"
        action_taken = "ADVISORY_ONLY: queued response logged; no roster, contract, cap, or draft tables were changed."
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
            "team": row["team"],
            "decision_type": row["decision_type"],
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
            action_taken="NONE: queued LLM request failed or returned invalid JSON before validation.",
            error_message=str(exc),
        )
        update_queue(con, queue_id, "failed")
        con.commit()
        return {
            "log_id": log_id,
            "queue_id": queue_id,
            "team": row["team"],
            "decision_type": row["decision_type"],
            "status": "failed",
            "validation": validation,
            "error": str(exc),
        }


def process_queue_rows(
    con: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    print_prompt: bool = False,
) -> dict[str, Any]:
    results = [process_queue_row(con, row, print_prompt=print_prompt) for row in rows]
    counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {"processed": len(results), "counts": counts, "results": results}


def print_queue_process_result(result: dict[str, Any]) -> None:
    print(f"AI GM queue processing: {result['processed']} row(s)")
    if result["counts"]:
        print("Counts: " + ", ".join(f"{key}={value}" for key, value in sorted(result["counts"].items())))
    for item in result["results"]:
        prefix = f"  - queue {item['queue_id']}: {item['status']}"
        if item.get("team") and item.get("decision_type"):
            prefix += f" {item['team']} {item['decision_type']}"
        print(prefix)
        if item.get("log_id"):
            print(f"      log_id={item['log_id']}")
        if item.get("error"):
            print(f"      {item['error']}")
        validation = item.get("validation") or {}
        if validation.get("errors"):
            for error in validation["errors"][:3]:
                print(f"      error: {error}")
        if validation.get("warnings"):
            for warning in validation["warnings"][:3]:
                print(f"      warning: {warning}")


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
    print("Team evaluator schema is ready.")


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


def action_evaluate(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    season = args.season or int(game_state["season"])
    game_id = game_state["game_id"]
    if not args.team and not args.all:
        raise ValueError("Provide --team TEAM or --all.")
    if args.all:
        evaluations = team_eval.evaluate_league(
            con,
            season=season,
            game_id=game_id,
            persist=args.persist,
        )
        if args.json:
            print(json_dumps(evaluations))
        else:
            for evaluation in evaluations:
                team_eval.print_evaluation(evaluation, detail_limit=args.detail_limit)
        if args.persist:
            con.commit()
        return

    evaluation = team_eval.evaluate_team(
        con,
        team_abbr=args.team,
        season=season,
        game_id=game_id,
        evaluation_date=game_state["current_date"],
        persist=args.persist,
    )
    if args.json:
        print(json_dumps(evaluation))
    else:
        team_eval.print_evaluation(evaluation, detail_limit=args.detail_limit)
    if args.persist:
        con.commit()


def action_cutdown_plan(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    season = args.season or int(game_state["season"])
    game_id = game_state["game_id"]
    if not args.team and not args.all:
        raise ValueError("Provide --team TEAM or --all.")
    if args.all:
        plans = cutdown_planner.build_league_cutdown_plans(
            con,
            season=season,
            game_id=game_id,
            active_limit=args.active_limit,
            practice_squad_limit=args.practice_squad_limit,
            persist=args.persist,
        )
        if args.json:
            print(json_dumps(plans))
        else:
            for plan in plans:
                cutdown_planner.print_cutdown_plan(plan, detail_limit=args.detail_limit)
        if args.persist:
            con.commit()
        return

    plan = cutdown_planner.build_cutdown_plan(
        con,
        team_abbr=args.team,
        season=season,
        game_id=game_id,
        plan_date=game_state["current_date"],
        active_limit=args.active_limit,
        practice_squad_limit=args.practice_squad_limit,
        persist=args.persist,
    )
    if args.json:
        print(json_dumps(plan))
    else:
        cutdown_planner.print_cutdown_plan(plan, detail_limit=args.detail_limit)
    if args.persist:
        con.commit()


def action_cutdown_plans(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    rows = cutdown_planner.list_cutdown_plans(
        con,
        team_abbr=args.team,
        game_id=game_state["game_id"],
        limit=args.limit,
    )
    if args.json:
        print(json_dumps(rows))
    else:
        cutdown_planner.print_plan_rows(rows)


def action_apply_cutdown_plan(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    backup_path = None
    if args.apply and not args.no_backup:
        backup_path = backup_sqlite(args.db, f"ai_gm_cutdown_plan_{args.plan_id}")
    result = cutdown_planner.apply_cutdown_plan(
        con,
        plan_id=args.plan_id,
        allow_warning=args.allow_warning,
        allow_stale=args.allow_stale,
        save_validation=not args.no_validation_save,
    )
    if args.apply and result.get("applied"):
        con.commit()
    else:
        con.rollback()
    if args.json:
        print(json_dumps(result))
    else:
        cutdown_planner.print_apply_result(result, applied=args.apply, backup=backup_path)


def action_contract_plan(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    season = args.season or int(game_state["season"])
    game_id = game_state["game_id"]
    if not args.team and not args.all:
        raise ValueError("Provide --team TEAM or --all.")
    if args.all:
        plans = contract_planner.build_league_contract_plans(
            con,
            season=season,
            game_id=game_id,
            persist=args.persist,
        )
        if args.json:
            print(json_dumps(plans))
        else:
            for plan in plans:
                contract_planner.print_contract_plan(plan, detail_limit=args.detail_limit)
        if args.persist:
            con.commit()
        return

    plan = contract_planner.build_contract_plan(
        con,
        team_abbr=args.team,
        season=season,
        game_id=game_id,
        plan_date=game_state["current_date"],
        persist=args.persist,
    )
    if args.json:
        print(json_dumps(plan))
    else:
        contract_planner.print_contract_plan(plan, detail_limit=args.detail_limit)
    if args.persist:
        con.commit()


def action_contract_plans(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    rows = contract_planner.list_contract_plans(
        con,
        team_abbr=args.team,
        game_id=game_state["game_id"],
        limit=args.limit,
    )
    if args.json:
        print(json_dumps(rows))
    else:
        contract_planner.print_plan_rows(rows)


def action_apply_contract_plan(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    backup_path = None
    if args.apply and not args.no_backup:
        backup_path = backup_sqlite(args.db, f"ai_gm_contract_plan_{args.plan_id}")
    result = contract_planner.apply_contract_plan(
        con,
        plan_id=args.plan_id,
        allow_stale=args.allow_stale,
        max_extensions=args.max_extensions,
        max_total_aav=args.max_total_aav,
    )
    if args.apply and result.get("applied"):
        con.commit()
    else:
        con.rollback()
    if args.json:
        print(json_dumps(result))
    else:
        contract_planner.print_apply_result(result, applied=args.apply, backup=backup_path)


def action_free_agent_plan(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    season = args.season or int(game_state["season"])
    league_year = args.league_year or season
    game_id = game_state["game_id"]
    if not args.team and not args.all:
        raise ValueError("Provide --team TEAM or --all.")
    if args.all:
        plans = free_agent_planner.build_league_free_agent_plans(
            con,
            league_year=league_year,
            season=season,
            game_id=game_id,
            persist=args.persist,
            refresh_market=args.refresh_market,
            market_limit=args.market_limit,
        )
        if args.json:
            print(json_dumps(plans))
        else:
            for plan in plans:
                free_agent_planner.print_free_agent_plan(plan, detail_limit=args.detail_limit)
        if args.persist:
            con.commit()
        return

    plan = free_agent_planner.build_free_agent_plan(
        con,
        team_abbr=args.team,
        league_year=league_year,
        season=season,
        game_id=game_id,
        persist=args.persist,
        refresh_market=args.refresh_market,
        market_limit=args.market_limit,
    )
    if args.json:
        print(json_dumps(plan))
    else:
        free_agent_planner.print_free_agent_plan(plan, detail_limit=args.detail_limit)
    if args.persist:
        con.commit()


def action_draft_plan(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    season = args.season or int(game_state["season"])
    draft_year = args.draft_year or season + 1
    game_id = game_state["game_id"]
    if not args.team and not args.all:
        raise ValueError("Provide --team TEAM or --all.")
    if args.all:
        plans = draft_planner.build_league_draft_plans(
            con,
            draft_year=draft_year,
            season=season,
            game_id=game_id,
            board_limit=args.board_limit,
            persist=args.persist,
        )
        if args.json:
            print(json_dumps(plans))
        else:
            for plan in plans:
                draft_planner.print_draft_plan(plan, detail_limit=args.detail_limit)
                print()
        if args.persist:
            con.commit()
        return

    plan = draft_planner.build_draft_plan(
        con,
        team_abbr=args.team,
        draft_year=draft_year,
        season=season,
        game_id=game_id,
        plan_date=game_state["current_date"],
        board_limit=args.board_limit,
        persist=args.persist,
    )
    if args.json:
        print(json_dumps(plan))
    else:
        draft_planner.print_draft_plan(plan, detail_limit=args.detail_limit)
    if args.persist:
        con.commit()


def action_draft_plans(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    rows = draft_planner.list_draft_plans(
        con,
        team_abbr=args.team,
        game_id=game_state["game_id"],
        draft_year=args.draft_year,
        limit=args.limit,
    )
    if args.json:
        print(json_dumps(rows))
    else:
        draft_planner.print_plan_rows(rows)


def action_free_agent_plans(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    rows = free_agent_planner.list_free_agent_plans(
        con,
        team_abbr=args.team,
        game_id=game_state["game_id"],
        limit=args.limit,
    )
    if args.json:
        print(json_dumps(rows))
    else:
        free_agent_planner.print_plan_rows(rows)


def action_apply_free_agent_plan(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    backup_path = None
    if args.apply and not args.no_backup:
        backup_path = backup_sqlite(args.db, f"ai_gm_free_agent_plan_{args.plan_id}")
    result = free_agent_planner.apply_free_agent_plan(
        con,
        plan_id=args.plan_id,
        allow_stale=args.allow_stale,
        max_offers=args.max_offers,
        max_total_aav=args.max_total_aav,
    )
    if args.apply and result.get("applied"):
        con.commit()
    else:
        con.rollback()
    if args.json:
        print(json_dumps(result))
    else:
        free_agent_planner.print_apply_result(result, applied=args.apply, backup=backup_path)


def action_offseason_run(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    season = args.season or int(game_state["season"])
    league_year = args.league_year or season
    backup_path = None
    if args.apply and not args.no_backup:
        backup_path = backup_sqlite(args.db, f"ai_gm_offseason_{args.phase}")
    result = offseason_driver.run_offseason(
        con,
        phase=args.phase,
        game_id=game_state["game_id"],
        season=season,
        league_year=league_year,
        current_date=game_state["current_date"],
        team_abbr=args.team,
        all_teams=args.all,
        include_user_team=args.include_user_team,
        max_teams=args.max_teams,
        apply_mode=args.apply,
        allow_stale=args.allow_stale,
        max_extensions_per_team=args.max_extensions_per_team,
        max_extension_aav=args.max_extension_aav,
        max_offers_per_team=args.max_offers_per_team,
        max_fa_aav=args.max_fa_aav,
        refresh_market=args.refresh_market,
        market_limit=args.market_limit,
    )
    if args.apply and result.get("committable"):
        con.commit()
    else:
        con.rollback()
    if args.json:
        print(json_dumps(result))
    else:
        offseason_driver.print_offseason_result(
            result,
            applied=args.apply,
            backup=backup_path,
            detail_limit=args.detail_limit,
        )


def ops_scan_result(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    team_abbr: str | None = None,
    all_teams: bool = False,
    phase: str = "auto",
    include_low: bool = False,
    limit: int = 40,
    current_date: str | None = None,
    enqueue: bool = False,
    dedupe: bool = True,
    max_players: int = 18,
    max_free_agents: int = 18,
) -> dict[str, Any]:
    ensure_schema(con)
    if not team_abbr and not all_teams:
        raise ValueError("Provide --team TEAM or --all.")
    result = ops_controller.build_operations(
        con,
        game_id=game_id,
        team_abbr=team_abbr,
        all_teams=all_teams,
        phase=phase,
        include_low=include_low,
        limit=limit,
        current_date=current_date,
    )
    queued = 0
    skipped = 0
    if enqueue:
        for operation in result["operations"]:
            existing_id = None
            if dedupe:
                existing_id = queued_operation_exists(
                    con,
                    game_id=str(operation["game_id"]),
                    team_id=int(operation["team_id"]),
                    decision_date=str(operation["decision_date"]),
                    decision_type=str(operation["decision_type"]),
                    operation_key=str(operation["operation_key"]),
                )
            if existing_id:
                operation["queue_status"] = "already_queued"
                operation["queue_id"] = existing_id
                skipped += 1
                continue
            context = build_team_context(
                con,
                team_abbr=str(operation["team"]),
                decision_type=str(operation["decision_type"]),
                game_id=str(operation["game_id"]),
                max_players=max_players,
                max_free_agents=max_free_agents,
            )
            context["game"]["current_date"] = operation["decision_date"]
            context["game"]["phase_code"] = operation["calendar_phase"]
            context["decision_request"]["operation_key"] = operation["operation_key"]
            context["decision_request"]["ops_phase"] = operation["ops_phase"]
            context["decision_request"]["source"] = "ai_gm_ops_controller"
            context["ai_gm_operation"] = operation
            queue_id = enqueue_operation_context(
                con,
                context,
                priority=int(operation["priority"]),
            )
            operation["queue_status"] = "queued"
            operation["queue_id"] = queue_id
            queued += 1
        result["counts"]["queued"] = queued
        result["counts"]["queue_skipped"] = skipped
    return result


def risk_tier_for_operation(operation: dict[str, Any]) -> str:
    op_type = str(operation.get("operation_type") or "")
    return OPERATION_RISK_TIERS.get(op_type, "medium")


def user_team_id_for_game(con: sqlite3.Connection, game_id: str) -> int | None:
    row = con.execute(
        "SELECT user_team_id FROM game_saves WHERE game_id = ?",
        (game_id,),
    ).fetchone()
    if row and row["user_team_id"] is not None:
        return int(row["user_team_id"])
    return None


def insert_ai_gm_memory(
    con: sqlite3.Connection,
    *,
    team_id: int,
    memory_date: str,
    memory_type: str,
    summary: str,
    importance: int,
) -> int:
    cur = con.execute(
        """
        INSERT INTO ai_gm_memory (
            team_id, memory_date, memory_type, summary, importance, created_at
        )
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (team_id, memory_date, memory_type, summary, max(1, min(10, importance))),
    )
    return int(cur.lastrowid)


def with_autonomy_mode(settings: AutonomySettings, mode: str | None) -> AutonomySettings:
    if not mode:
        return settings
    mode = normalize_autonomy_mode(mode)
    review_medium_risk = 0 if mode == "full_cpu_control" else settings.review_medium_risk
    return AutonomySettings(
        game_id=settings.game_id,
        team_id=settings.team_id,
        mode=mode,
        queue_llm_advisory=settings.queue_llm_advisory,
        auto_apply_low_risk=1 if AUTONOMY_MODE_RANK[mode] >= 1 else settings.auto_apply_low_risk,
        review_medium_risk=review_medium_risk,
        review_high_risk=settings.review_high_risk,
        include_user_team=settings.include_user_team,
        max_operations_per_day=settings.max_operations_per_day,
        max_auto_apply_per_day=settings.max_auto_apply_per_day,
    )


def can_auto_apply_operation(
    operation: dict[str, Any],
    settings: AutonomySettings,
    *,
    apply_mode: bool,
    applied_so_far: int,
) -> tuple[bool, str]:
    if not apply_mode:
        return False, "apply flag not supplied"
    op_type = str(operation.get("operation_type") or "")
    risk = risk_tier_for_operation(operation)
    if risk == "high":
        return False, "high risk requires review"
    if risk == "medium" and settings.mode != "full_cpu_control":
        return False, "medium risk requires full CPU control"
    if op_type not in AUTO_APPLY_OPERATION_TYPES:
        return False, f"{op_type} does not have an auto-apply bridge yet"
    if risk == "low" and not settings.auto_apply_low_risk and AUTONOMY_MODE_RANK[settings.mode] < 1:
        return False, "auto-apply low-risk is disabled"
    if applied_so_far >= settings.max_auto_apply_per_day:
        return False, "daily auto-apply limit reached"
    return True, "allowed"


def plan_operation(
    con: sqlite3.Connection,
    operation: dict[str, Any],
    *,
    season: int,
    persist: bool,
    current_date: str,
) -> dict[str, Any]:
    team = str(operation["team"])
    op_type = str(operation["operation_type"])
    game_id = str(operation["game_id"])
    if op_type == "review_cutdown_plan":
        plan = cutdown_planner.build_cutdown_plan(
            con,
            team_abbr=team,
            season=season,
            game_id=game_id,
            plan_date=current_date,
            persist=persist,
        )
        return {
            "status": "planned",
            "plan_type": "cutdown",
            "plan_id": plan.get("plan_id"),
            "validation_status": (plan.get("validation") or {}).get("status"),
            "summary": plan.get("summary"),
        }
    if op_type in {"contract_plan"}:
        plan = contract_planner.build_contract_plan(
            con,
            team_abbr=team,
            season=season,
            game_id=game_id,
            plan_date=current_date,
            persist=persist,
        )
        return {
            "status": "planned",
            "plan_type": "contract",
            "plan_id": plan.get("plan_id"),
            "summary": plan.get("summary"),
            "counts": plan.get("counts"),
        }
    if op_type in {"free_agent_shortlist", "free_agent_plan"}:
        plan = free_agent_planner.build_free_agent_plan(
            con,
            team_abbr=team,
            league_year=season,
            season=season,
            game_id=game_id,
            plan_date=current_date,
            persist=persist,
        )
        return {
            "status": "planned",
            "plan_type": "free_agent",
            "plan_id": plan.get("plan_id"),
            "summary": plan.get("summary"),
            "counts": plan.get("counts"),
        }
    if op_type == "draft_strategy":
        plan = draft_planner.build_draft_plan(
            con,
            team_abbr=team,
            draft_year=season + 1,
            season=season,
            game_id=game_id,
            plan_date=current_date,
            persist=persist,
        )
        return {
            "status": "planned",
            "plan_type": "draft",
            "plan_id": plan.get("plan_id"),
            "summary": plan.get("summary"),
            "counts": plan.get("counts"),
        }
    return {"status": "not_plannable", "plan_type": None, "summary": "No deterministic planner for this operation type."}


def enqueue_or_preview_operation(
    con: sqlite3.Connection,
    operation: dict[str, Any],
    *,
    persist: bool,
    max_players: int,
    max_free_agents: int,
) -> dict[str, Any]:
    if not persist:
        return {"status": "would_enqueue", "queue_id": None}
    existing_id = queued_operation_exists(
        con,
        game_id=str(operation["game_id"]),
        team_id=int(operation["team_id"]),
        decision_date=str(operation["decision_date"]),
        decision_type=str(operation["decision_type"]),
        operation_key=str(operation["operation_key"]),
    )
    if existing_id:
        return {"status": "already_queued", "queue_id": existing_id}
    context = build_team_context(
        con,
        team_abbr=str(operation["team"]),
        decision_type=str(operation["decision_type"]),
        game_id=str(operation["game_id"]),
        max_players=max_players,
        max_free_agents=max_free_agents,
    )
    context["game"]["current_date"] = operation["decision_date"]
    context["game"]["phase_code"] = operation["calendar_phase"]
    context["decision_request"]["operation_key"] = operation["operation_key"]
    context["decision_request"]["ops_phase"] = operation["ops_phase"]
    context["decision_request"]["source"] = "ai_gm_daily_run"
    context["ai_gm_operation"] = operation
    queue_id = enqueue_operation_context(con, context, priority=int(operation["priority"]))
    return {"status": "queued", "queue_id": queue_id}


def execute_autonomy_operation(
    con: sqlite3.Connection,
    operation: dict[str, Any],
    *,
    settings: AutonomySettings,
    season: int,
    current_date: str,
    persist: bool,
    apply_mode: bool,
    applied_so_far: int,
    max_players: int,
    max_free_agents: int,
) -> dict[str, Any]:
    risk = risk_tier_for_operation(operation)
    op_type = str(operation["operation_type"])
    result: dict[str, Any] = {
        "operation_key": operation["operation_key"],
        "team": operation["team"],
        "team_id": int(operation["team_id"]),
        "operation_type": op_type,
        "decision_type": operation["decision_type"],
        "priority": int(operation["priority"]),
        "risk_tier": risk,
        "autonomy_mode": settings.mode,
        "summary": operation.get("summary"),
        "drivers": operation.get("drivers") or [],
        "status": "review_required",
        "plan": None,
        "queue": None,
        "apply": None,
    }

    if risk == "high" and settings.review_high_risk:
        result["review_reason"] = "high-risk operation requires review"
    elif risk == "medium" and settings.review_medium_risk and settings.mode != "full_cpu_control":
        result["review_reason"] = "medium-risk operation requires review"
    elif settings.mode == "advisory_only":
        result["review_reason"] = "advisory-only mode"

    plan = plan_operation(con, operation, season=season, persist=persist, current_date=current_date)
    if plan["status"] == "planned":
        result["plan"] = plan
        result["status"] = "planned"
    elif settings.queue_llm_advisory:
        queue = enqueue_or_preview_operation(
            con,
            operation,
            persist=persist,
            max_players=max_players,
            max_free_agents=max_free_agents,
        )
        result["queue"] = queue
        result["status"] = queue["status"]
    else:
        result["status"] = "blocked"
        result["review_reason"] = "no deterministic planner and LLM queueing disabled"

    allowed, reason = can_auto_apply_operation(
        operation,
        settings,
        apply_mode=apply_mode,
        applied_so_far=applied_so_far,
    )
    result["auto_apply_allowed"] = allowed
    result["auto_apply_reason"] = reason
    if allowed:
        result.pop("review_reason", None)
    if allowed and result.get("plan") and result["plan"].get("plan_id"):
        plan_id = int(result["plan"]["plan_id"])
        plan_type = str(result["plan"].get("plan_type") or "")
        if plan_type == "cutdown":
            apply_result = cutdown_planner.apply_cutdown_plan(
                con,
                plan_id=plan_id,
                allow_warning=False,
                allow_stale=False,
                save_validation=True,
            )
        elif plan_type == "contract":
            apply_result = contract_planner.apply_contract_plan(
                con,
                plan_id=plan_id,
                allow_stale=False,
                max_extensions=2,
                max_total_aav=None,
            )
        elif plan_type == "free_agent":
            apply_result = free_agent_planner.apply_free_agent_plan(
                con,
                plan_id=plan_id,
                allow_stale=False,
                max_offers=2,
                max_total_aav=None,
            )
        else:
            apply_result = {
                "applied": False,
                "blocked_reason": f"No auto-apply bridge for plan type {plan_type}.",
            }
        result["apply"] = apply_result
        result["status"] = "applied" if apply_result.get("applied") else "apply_blocked"
    return result


def record_autonomy_memory(
    con: sqlite3.Connection,
    *,
    operation_result: dict[str, Any],
    current_date: str,
    persist: bool,
) -> int | None:
    if not persist:
        return None
    plan = operation_result.get("plan") or {}
    queue = operation_result.get("queue") or {}
    detail = None
    if plan.get("plan_id"):
        detail = f"{plan.get('plan_type')} plan {plan.get('plan_id')}"
    elif queue.get("queue_id"):
        detail = f"queue {queue.get('queue_id')}"
    summary = (
        f"{operation_result['operation_type']} finished as {operation_result['status']} "
        f"({operation_result['risk_tier']} risk"
        f"{', ' + detail if detail else ''}). {operation_result.get('summary') or ''}"
    ).strip()
    importance = 8 if operation_result["risk_tier"] == "high" else 6 if operation_result["risk_tier"] == "medium" else 4
    return insert_ai_gm_memory(
        con,
        team_id=int(operation_result["team_id"]),
        memory_date=current_date,
        memory_type="autonomy_daily_operation",
        summary=summary,
        importance=importance,
    )


def review_lifecycle_for_operation_result(operation_result: dict[str, Any]) -> str:
    status = str(operation_result.get("status") or "")
    if status == "applied":
        return "applied"
    if status in {"blocked", "apply_blocked"}:
        return "blocked"
    return "pending_review"


def review_artifact_for_operation_result(operation_result: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    plan = operation_result.get("plan") or {}
    if plan.get("plan_id"):
        plan_type = str(plan.get("plan_type") or "plan")
        return f"{plan_type}_plan", int(plan["plan_id"]), f"{plan_type}_plan"
    queue = operation_result.get("queue") or {}
    if queue.get("queue_id"):
        return "decision_queue", int(queue["queue_id"]), "queued_decision"
    return None, None, None


def upsert_review_item_for_operation(
    con: sqlite3.Connection,
    *,
    operation_result: dict[str, Any],
    run_id: int,
    game_state: dict[str, Any],
) -> int | None:
    artifact_type, artifact_id, item_type = review_artifact_for_operation_result(operation_result)
    if not artifact_type or artifact_id is None or not item_type:
        return None
    lifecycle_status = review_lifecycle_for_operation_result(operation_result)
    title = f"{operation_result.get('team') or ''} {str(operation_result.get('operation_type') or item_type).replace('_', ' ')}".strip()
    summary = operation_result.get("summary") or operation_result.get("review_reason") or ""
    existing = con.execute(
        """
        SELECT review_id, lifecycle_status
        FROM ai_gm_review_items
        WHERE game_id = ?
          AND artifact_type = ?
          AND artifact_id = ?
        """,
        (game_state["game_id"], artifact_type, artifact_id),
    ).fetchone()
    if existing:
        existing_status = str(existing["lifecycle_status"] or "pending_review")
        next_status = existing_status if existing_status in {"approved", "rejected", "applied"} else lifecycle_status
        con.execute(
            """
            UPDATE ai_gm_review_items
            SET run_id = ?,
                review_date = ?,
                season = ?,
                phase_code = ?,
                operation_key = ?,
                operation_type = ?,
                decision_type = ?,
                risk_tier = ?,
                priority = ?,
                title = ?,
                summary = ?,
                lifecycle_status = ?,
                detail_json = ?,
                updated_at = datetime('now')
            WHERE review_id = ?
            """,
            (
                run_id,
                game_state["current_date"],
                int(game_state["season"]),
                game_state["phase_code"],
                operation_result.get("operation_key"),
                operation_result.get("operation_type"),
                operation_result.get("decision_type"),
                operation_result.get("risk_tier") or "medium",
                int(operation_result.get("priority") or 5),
                title,
                summary,
                next_status,
                json_dumps(operation_result),
                int(existing["review_id"]),
            ),
        )
        return int(existing["review_id"])
    cur = con.execute(
        """
        INSERT INTO ai_gm_review_items (
            game_id, team_id, run_id, review_date, season, phase_code,
            item_type, artifact_type, artifact_id, operation_key,
            operation_type, decision_type, risk_tier, priority,
            title, summary, lifecycle_status, detail_json,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            game_state["game_id"],
            int(operation_result["team_id"]),
            run_id,
            game_state["current_date"],
            int(game_state["season"]),
            game_state["phase_code"],
            item_type,
            artifact_type,
            artifact_id,
            operation_result.get("operation_key"),
            operation_result.get("operation_type"),
            operation_result.get("decision_type"),
            operation_result.get("risk_tier") or "medium",
            int(operation_result.get("priority") or 5),
            title,
            summary,
            lifecycle_status,
            json_dumps(operation_result),
        ),
    )
    return int(cur.lastrowid)


def create_review_items_for_daily_run(
    con: sqlite3.Connection,
    *,
    run_result: dict[str, Any],
    run_id: int,
) -> list[dict[str, Any]]:
    game_state = run_result["game"]
    review_items: list[dict[str, Any]] = []
    for operation_result in run_result.get("operations") or []:
        review_id = upsert_review_item_for_operation(
            con,
            operation_result=operation_result,
            run_id=run_id,
            game_state=game_state,
        )
        if review_id:
            operation_result["review_id"] = review_id
            review_items.append(
                {
                    "review_id": review_id,
                    "team": operation_result.get("team"),
                    "operation_type": operation_result.get("operation_type"),
                    "risk_tier": operation_result.get("risk_tier"),
                    "lifecycle_status": review_lifecycle_for_operation_result(operation_result),
                }
            )
    return review_items


def open_review_item_for_operation(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int,
    operation_type: str,
) -> sqlite3.Row | None:
    if not table_exists(con, "ai_gm_review_items"):
        return None
    return con.execute(
        """
        SELECT review_id, lifecycle_status, artifact_type, artifact_id
        FROM ai_gm_review_items
        WHERE game_id = ?
          AND team_id = ?
          AND operation_type = ?
          AND lifecycle_status IN ('pending_review', 'approved', 'blocked')
        ORDER BY updated_at DESC, review_id DESC
        LIMIT 1
        """,
        (game_id, team_id, operation_type),
    ).fetchone()


def review_item_commands(row: sqlite3.Row | dict[str, Any]) -> list[str]:
    item = dict(row)
    artifact_type = item.get("artifact_type")
    artifact_id = item.get("artifact_id")
    review_id = item.get("review_id")
    commands = [
        f"python tools\\play.py ai-gm review-show --review-id {review_id}",
        f"python tools\\play.py ai-gm review-update --review-id {review_id} --status approved",
        f"python tools\\play.py ai-gm review-update --review-id {review_id} --status rejected --note \"reason\"",
        f"python tools\\play.py ai-gm review-apply --review-id {review_id}",
        f"python tools\\play.py ai-gm review-apply --review-id {review_id} --apply",
    ]
    if artifact_type == "cutdown_plan" and artifact_id:
        commands.append(f"python tools\\play.py ai-gm apply-cutdown-plan --plan-id {artifact_id}")
        commands.append(f"python tools\\play.py ai-gm apply-cutdown-plan --plan-id {artifact_id} --allow-warning --apply")
    elif artifact_type == "contract_plan" and artifact_id:
        commands.append(f"python tools\\play.py ai-gm apply-contract-plan --plan-id {artifact_id}")
        commands.append(f"python tools\\play.py ai-gm apply-contract-plan --plan-id {artifact_id} --apply")
    elif artifact_type == "free_agent_plan" and artifact_id:
        commands.append(f"python tools\\play.py ai-gm apply-free-agent-plan --plan-id {artifact_id}")
        commands.append(f"python tools\\play.py ai-gm apply-free-agent-plan --plan-id {artifact_id} --apply")
    elif artifact_type == "decision_queue":
        commands.append(f"python tools\\play.py ai-gm process-queue --team {item.get('team') or 'TEAM'} --limit 1")
    return commands


def review_inbox_rows(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_abbr: str | None,
    lifecycle_status: str,
    risk_tier: str | None,
    item_type: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    ensure_schema(con)
    params: list[Any] = [game_id]
    where = ["game_id = ?"]
    if team_abbr:
        where.append("team = ?")
        params.append(team_abbr.upper())
    if lifecycle_status != "all":
        where.append("lifecycle_status = ?")
        params.append(lifecycle_status)
    if risk_tier:
        where.append("risk_tier = ?")
        params.append(risk_tier)
    if item_type:
        where.append("item_type = ?")
        params.append(item_type)
    params.append(max(1, limit))
    return con.execute(
        f"""
        SELECT *
        FROM ai_gm_review_items_view
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE lifecycle_status
                WHEN 'pending_review' THEN 0
                WHEN 'blocked' THEN 1
                WHEN 'approved' THEN 2
                ELSE 3
            END,
            CASE risk_tier WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
            priority DESC,
            created_at DESC,
            review_id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def review_history_rows(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_abbr: str | None,
    lifecycle_status: str,
    risk_tier: str | None,
    item_type: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    ensure_schema(con)
    params: list[Any] = [game_id]
    where = ["game_id = ?"]
    if team_abbr:
        where.append("team = ?")
        params.append(team_abbr.upper())
    if lifecycle_status != "all":
        where.append("lifecycle_status = ?")
        params.append(lifecycle_status)
    if risk_tier:
        where.append("risk_tier = ?")
        params.append(risk_tier)
    if item_type:
        where.append("item_type = ?")
        params.append(item_type)
    params.append(max(1, limit))
    return con.execute(
        f"""
        SELECT *
        FROM ai_gm_review_items_view
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(applied_at, reviewed_at, updated_at, created_at) DESC,
                 review_id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def load_review_item(con: sqlite3.Connection, review_id: int) -> sqlite3.Row:
    ensure_schema(con)
    row = con.execute(
        """
        SELECT
            r.*,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name
        FROM ai_gm_review_items r
        JOIN teams t ON t.team_id = r.team_id
        WHERE r.review_id = ?
        """,
        (review_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"AI GM review item not found: {review_id}")
    return row


def update_review_item_status(
    con: sqlite3.Connection,
    *,
    review_id: int,
    lifecycle_status: str,
    note: str | None,
    reviewed_by: str,
) -> sqlite3.Row:
    if lifecycle_status not in REVIEW_LIFECYCLE_STATUSES:
        raise ValueError(f"Invalid lifecycle status: {lifecycle_status}")
    row = load_review_item(con, review_id)
    if lifecycle_status == "rejected" and row["artifact_type"] == "decision_queue" and row["artifact_id"]:
        con.execute(
            """
            UPDATE ai_gm_decision_queue
            SET status = 'cancelled', updated_at = datetime('now')
            WHERE decision_id = ?
              AND status IN ('queued', 'running')
            """,
            (int(row["artifact_id"]),),
        )
    if lifecycle_status == "pending_review":
        con.execute(
            """
            UPDATE ai_gm_review_items
            SET lifecycle_status = ?,
                review_note = ?,
                reviewed_at = NULL,
                reviewed_by = NULL,
                updated_at = datetime('now')
            WHERE review_id = ?
            """,
            (lifecycle_status, note, review_id),
        )
    else:
        con.execute(
            """
            UPDATE ai_gm_review_items
            SET lifecycle_status = ?,
                review_note = ?,
                reviewed_at = datetime('now'),
                reviewed_by = ?,
                updated_at = datetime('now')
            WHERE review_id = ?
            """,
            (lifecycle_status, note, reviewed_by, review_id),
        )
    return load_review_item(con, review_id)


def action_dev_seed_review(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game_state = resolve_game_state(con, args.game_id)
    team = get_team(con, args.team)
    if args.clear_existing:
        con.execute(
            """
            DELETE FROM ai_gm_review_items
            WHERE game_id = ?
              AND team_id = ?
              AND item_type = 'dev_review_seed'
              AND lifecycle_status IN ('pending_review', 'approved', 'blocked', 'rejected')
            """,
            (game_state["game_id"], int(team["team_id"])),
        )
    detail = {
        "source": "dev_seed_review",
        "non_mutating": True,
        "plan": {
            "validation_status": "dev_only",
            "recommended_action": "no_op",
            "affected_records": [],
        },
        "validation": {
            "status": "warning",
            "warnings": [
                "Development-only review item.",
                "This item is intended to test the Game Center review workflow.",
            ],
            "errors": [],
        },
        "blockers": [],
        "warnings": [
            "No roster, cap, draft, contract, or free-agent state should be changed by this seed item.",
        ],
        "queue": {
            "status": "not_queued",
            "decision_type": "dev_review_workflow_test",
        },
    }
    cur = con.execute(
        """
        INSERT INTO ai_gm_review_items (
            game_id, team_id, run_id, review_date, season, phase_code,
            item_type, artifact_type, artifact_id, operation_key,
            operation_type, decision_type, risk_tier, priority,
            title, summary, lifecycle_status, detail_json,
            created_at, updated_at
        )
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, datetime('now'), datetime('now'))
        """,
        (
            game_state["game_id"],
            int(team["team_id"]),
            game_state["current_date"],
            int(game_state["season"]),
            game_state["phase_code"],
            "dev_review_seed",
            "dev_review_seed",
            f"dev_review_seed:{team['abbreviation']}:{game_state['current_date']}",
            "dev_review_seed",
            "dev_review_workflow_test",
            "low",
            1,
            f"{team['abbreviation']} dev review workflow test",
            "Development-only item for testing in-app AI GM review detail and action buttons.",
            json_dumps(detail),
        ),
    )
    con.commit()
    payload = {
        "review_id": int(cur.lastrowid),
        "game_id": game_state["game_id"],
        "team": team["abbreviation"],
        "status": "pending_review",
        "title": f"{team['abbreviation']} dev review workflow test",
    }
    if getattr(args, "json", False):
        print(json_dumps(payload))
    else:
        print(f"Seeded dev AI GM review item #{payload['review_id']} for {payload['team']}.")


def action_dev_clear_reviews(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game_state = resolve_game_state(con, args.game_id)
    params: list[Any] = [game_state["game_id"]]
    where = [
        "game_id = ?",
        "item_type = 'dev_review_seed'",
        "artifact_type = 'dev_review_seed'",
    ]
    team_abbr = None
    if args.team:
        team = get_team(con, args.team)
        team_abbr = team["abbreviation"]
        where.append("team_id = ?")
        params.append(int(team["team_id"]))
    cur = con.execute(
        f"DELETE FROM ai_gm_review_items WHERE {' AND '.join(where)}",
        params,
    )
    con.commit()
    payload = {
        "deleted": int(cur.rowcount or 0),
        "game_id": game_state["game_id"],
        "team": team_abbr,
    }
    if getattr(args, "json", False):
        print(json_dumps(payload))
    else:
        scope = f" for {team_abbr}" if team_abbr else ""
        print(f"Cleared {payload['deleted']} dev AI GM review item(s){scope}.")


def update_review_apply_outcome(
    con: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    lifecycle_status: str,
    result: dict[str, Any] | None,
    error: str | None,
    reviewed_by: str,
    note: str | None,
) -> sqlite3.Row:
    detail = json.loads(row["detail_json"] or "{}")
    detail["last_apply_attempt"] = {
        "status": lifecycle_status,
        "applied": bool(result.get("applied")) if result else False,
        "error": error,
        "attempted_at": now_iso(),
    }
    con.execute(
        """
        UPDATE ai_gm_review_items
        SET lifecycle_status = ?,
            review_note = COALESCE(?, review_note),
            reviewed_at = COALESCE(reviewed_at, datetime('now')),
            reviewed_by = COALESCE(reviewed_by, ?),
            applied_at = CASE WHEN ? = 'applied' THEN datetime('now') ELSE applied_at END,
            apply_result_json = ?,
            apply_error = ?,
            detail_json = ?,
            updated_at = datetime('now')
        WHERE review_id = ?
        """,
        (
            lifecycle_status,
            note,
            reviewed_by,
            lifecycle_status,
            json_dumps(result) if result is not None else None,
            error,
            json_dumps(detail),
            int(row["review_id"]),
        ),
    )
    return load_review_item(con, int(row["review_id"]))


def latest_valid_decision_log_for_queue(con: sqlite3.Connection, queue_id: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM ai_gm_decision_log
        WHERE queue_id = ?
          AND status = 'valid'
        ORDER BY created_at DESC, decision_log_id DESC
        LIMIT 1
        """,
        (queue_id,),
    ).fetchone()


def queue_row_for_review(con: sqlite3.Connection, queue_id: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT q.*, t.abbreviation AS team
        FROM ai_gm_decision_queue q
        JOIN teams t ON t.team_id = q.team_id
        WHERE q.decision_id = ?
        """,
        (queue_id,),
    ).fetchone()


def accepted_actions_from_decision_log(log_row: sqlite3.Row) -> list[dict[str, Any]]:
    try:
        validation = json.loads(log_row["validation_result"] or "{}")
    except json.JSONDecodeError:
        return []
    actions = validation.get("accepted_actions")
    return [dict(action) for action in actions if isinstance(action, dict)] if isinstance(actions, list) else []


def trade_review_note(action: dict[str, Any], fallback: str) -> str:
    reason = str(action.get("reason") or "").strip()
    value_note = str(action.get("value_chart_note") or "").strip()
    conditions = str(action.get("conditions") or "").strip()
    parts = [fallback]
    if reason:
        parts.append(reason)
    if value_note:
        parts.append(value_note)
    if conditions:
        parts.append(f"Conditions: {conditions}")
    return " ".join(parts).strip()


def log_trade_review_action(
    con: sqlite3.Connection,
    *,
    proposal_id: int,
    game_id: str,
    team_id: int,
    action: str,
    message: str,
) -> None:
    try:
        te._log_negotiation(  # type: ignore[attr-defined]
            con,
            proposal_id,
            game_id,
            team_id,
            action,
            message,
            te.gm_chart(con, team_id),
        )
    except Exception:
        # Negotiation logging should not block the validated status change.
        return


def apply_trade_response_actions(
    con: sqlite3.Connection,
    *,
    review_row: sqlite3.Row,
    queue_row: sqlite3.Row,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    changed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    team_id = int(queue_row["team_id"])
    game_id = str(queue_row["game_id"] or review_row["game_id"])

    for action in actions:
        action_type = str(action.get("action_type") or "")
        if action_type not in {"accept_trade", "reject_trade", "counter_trade", "request_more_value", "conditionally_accept"}:
            continue
        try:
            proposal_id = int(action.get("proposal_id"))
        except (TypeError, ValueError):
            blocked.append({"action_type": action_type, "blocked_reason": "Missing or invalid proposal_id."})
            continue
        proposal = proposal_for_validation(con, proposal_id, team_id)
        if not proposal:
            blocked.append(
                {
                    "action_type": action_type,
                    "proposal_id": proposal_id,
                    "blocked_reason": "Proposal is no longer a pending incoming trade for this team.",
                }
            )
            continue
        if action_type == "accept_trade":
            note = trade_review_note(action, "Accepted by reviewed AI GM decision.")
            te.update_proposal_status(
                con,
                proposal_id,
                "accepted",
                responder_note=note,
                evaluated_accept=1,
                evaluated_reason=note,
            )
            log_trade_review_action(
                con,
                proposal_id=proposal_id,
                game_id=game_id,
                team_id=team_id,
                action="review_accept",
                message=note,
            )
            changed.append({"action_type": action_type, "proposal_id": proposal_id, "status": "accepted"})
        elif action_type == "reject_trade":
            note = trade_review_note(action, "Rejected by reviewed AI GM decision.")
            te.update_proposal_status(
                con,
                proposal_id,
                "rejected",
                responder_note=note,
                evaluated_accept=0,
                evaluated_reason=note,
            )
            log_trade_review_action(
                con,
                proposal_id=proposal_id,
                game_id=game_id,
                team_id=team_id,
                action="review_reject",
                message=note,
            )
            changed.append({"action_type": action_type, "proposal_id": proposal_id, "status": "rejected"})
        else:
            note = trade_review_note(action, "Counter requested by reviewed AI GM decision.")
            counter_round = action.get("counter_pick_round")
            target_player = action.get("target_player_id") or action.get("requested_player_id")
            extras = []
            if counter_round:
                extras.append(f"counter_pick_round={counter_round}")
            if target_player:
                extras.append(f"target_player_id={target_player}")
            if extras:
                note = f"{note} ({', '.join(extras)})"
            te.update_proposal_status(
                con,
                proposal_id,
                "countered",
                responder_note=note,
                evaluated_accept=0,
                evaluated_reason=note,
            )
            log_trade_review_action(
                con,
                proposal_id=proposal_id,
                game_id=game_id,
                team_id=team_id,
                action="review_counter",
                message=note,
            )
            changed.append({"action_type": action_type, "proposal_id": proposal_id, "status": "countered"})

    return {
        "review_id": int(review_row["review_id"]),
        "artifact_type": "decision_queue",
        "queue_id": int(queue_row["decision_id"]),
        "decision_type": "trade_response",
        "team": review_row["team"],
        "applied": bool(changed),
        "trade_actions": changed,
        "blocked_actions": blocked,
        "blocked_reason": None if changed else "No executable trade-response actions were available.",
    }


def player_asset(con: sqlite3.Connection, player_id: int) -> dict[str, Any]:
    player = con.execute(
        "SELECT first_name, last_name, position FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if not player:
        raise ValueError(f"Player {player_id} not found.")
    return {
        "asset_type": "PlayerContract",
        "player_id": player_id,
        "description": f"{player['first_name']} {player['last_name']} ({player['position']})",
    }


def draft_pick_asset(con: sqlite3.Connection, *, pick_id: int | None, draft_year: int, round_num: int) -> dict[str, Any]:
    if pick_id is not None:
        pick = con.execute("SELECT * FROM draft_picks WHERE pick_id = ?", (pick_id,)).fetchone()
        if not pick:
            raise ValueError(f"Draft pick {pick_id} not found.")
        return {
            "asset_type": "DraftPick",
            "pick_id": pick_id,
            "draft_year": int(pick["draft_year"]),
            "round": int(pick["round"]),
            "pick_number": int(pick["pick_number"]) if pick["pick_number"] else None,
            "description": f"{pick['draft_year']} R{pick['round']} pick",
        }
    return {
        "asset_type": "DraftPick",
        "pick_id": None,
        "draft_year": draft_year,
        "round": round_num,
        "pick_number": None,
        "description": f"{draft_year} R{round_num} pick",
    }


def apply_trade_proposal_actions(
    con: sqlite3.Connection,
    *,
    review_row: sqlite3.Row,
    queue_row: sqlite3.Row,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    proposals: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    proposing_team_id = int(queue_row["team_id"])
    game_id = str(queue_row["game_id"] or review_row["game_id"])
    season = int(review_row["season"] or te.current_season(con))

    for action in actions:
        action_type = str(action.get("action_type") or "")
        if action_type == "hold_no_trade":
            continue
        if action_type not in {"propose_trade", "shop_player", "request_draft_pick", "request_player_swap"}:
            continue
        target_key = action.get("target_team") or action.get("target_team_abbreviation") or action.get("receiving_team")
        if not target_key:
            blocked.append({"action_type": action_type, "blocked_reason": "Missing target_team."})
            continue
        try:
            receiving_team = get_team(con, str(target_key))
            receiving_team_id = int(receiving_team["team_id"])
            player_id = int(action.get("player_id"))
            proposing_assets = [player_asset(con, player_id)]
            requested_player_id = action.get("target_player_id") or action.get("requested_player_id")
            requested_pick_id = action.get("requested_pick_id") or action.get("pick_id")
            requested_pick_round = action.get("requested_pick_round")
            receiving_assets: list[dict[str, Any]] = []
            if requested_player_id is not None:
                receiving_assets.append(player_asset(con, int(requested_player_id)))
            if requested_pick_id is not None:
                receiving_assets.append(
                    draft_pick_asset(con, pick_id=int(requested_pick_id), draft_year=season + 1, round_num=1)
                )
            elif requested_pick_round is not None:
                receiving_assets.append(
                    draft_pick_asset(
                        con,
                        pick_id=None,
                        draft_year=season + 1,
                        round_num=int(requested_pick_round),
                    )
                )
            if not receiving_assets:
                blocked.append(
                    {
                        "action_type": action_type,
                        "blocked_reason": "Trade proposal needs requested_pick_id, requested_pick_round, or target_player_id.",
                    }
                )
                continue
            note = trade_review_note(action, "Created from reviewed AI GM trade proposal.")
            proposal_id = te.create_proposal(
                con,
                game_id=game_id,
                proposing_team_id=proposing_team_id,
                receiving_team_id=receiving_team_id,
                proposing_assets=proposing_assets,
                receiving_assets=receiving_assets,
                proposer_note=note,
            )
            proposals.append(
                {
                    "action_type": action_type,
                    "proposal_id": proposal_id,
                    "target_team": receiving_team["abbreviation"],
                    "offered_player_id": player_id,
                    "requested_assets": len(receiving_assets),
                    "status": "proposed",
                }
            )
        except Exception as exc:
            blocked.append({"action_type": action_type, "blocked_reason": str(exc)})

    return {
        "review_id": int(review_row["review_id"]),
        "artifact_type": "decision_queue",
        "queue_id": int(queue_row["decision_id"]),
        "decision_type": "trade_proposal",
        "team": review_row["team"],
        "applied": bool(proposals),
        "trade_proposals": proposals,
        "blocked_actions": blocked,
        "blocked_reason": None if proposals else "No executable trade-proposal actions were available.",
    }


def apply_decision_queue_review(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    queue_id = int(row["artifact_id"])
    queue = queue_row_for_review(con, queue_id)
    if not queue:
        return {
            "review_id": int(row["review_id"]),
            "artifact_type": "decision_queue",
            "queue_id": queue_id,
            "team": row["team"],
            "applied": False,
            "blocked_reason": "Queued AI GM decision no longer exists.",
        }
    if str(queue["status"]) != "completed":
        return {
            "review_id": int(row["review_id"]),
            "artifact_type": "decision_queue",
            "queue_id": queue_id,
            "team": row["team"],
            "applied": False,
            "blocked_reason": f"Queued AI GM decision must be completed with a valid LLM result first; current status is {queue['status']}.",
        }
    log_row = latest_valid_decision_log_for_queue(con, queue_id)
    if not log_row:
        return {
            "review_id": int(row["review_id"]),
            "artifact_type": "decision_queue",
            "queue_id": queue_id,
            "team": row["team"],
            "applied": False,
            "blocked_reason": "No valid AI GM decision log exists for this queue row.",
        }
    actions = accepted_actions_from_decision_log(log_row)
    if not actions:
        return {
            "review_id": int(row["review_id"]),
            "artifact_type": "decision_queue",
            "queue_id": queue_id,
            "team": row["team"],
            "applied": False,
            "blocked_reason": "The latest valid AI GM decision has no accepted actions.",
        }
    decision_type = str(queue["decision_type"] or "")
    if decision_type == "trade_response":
        return apply_trade_response_actions(con, review_row=row, queue_row=queue, actions=actions)
    if decision_type == "trade_proposal":
        return apply_trade_proposal_actions(con, review_row=row, queue_row=queue, actions=actions)
    return {
        "review_id": int(row["review_id"]),
        "artifact_type": "decision_queue",
        "queue_id": queue_id,
        "decision_type": decision_type,
        "team": row["team"],
        "applied": False,
        "blocked_reason": f"Queued AI GM decision type {decision_type} does not have an apply bridge yet.",
    }


def apply_review_item_bridge(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    allow_warning: bool,
    allow_stale: bool,
    max_extensions: int,
    max_offers: int,
    max_total_aav: int | None,
    save_validation: bool,
) -> dict[str, Any]:
    artifact_type = str(row["artifact_type"] or "")
    artifact_id = row["artifact_id"]
    if artifact_id is None:
        return {
            "review_id": int(row["review_id"]),
            "artifact_type": artifact_type,
            "applied": False,
            "blocked_reason": "Review item has no linked artifact.",
        }
    plan_id = int(artifact_id)
    if artifact_type == "cutdown_plan":
        return cutdown_planner.apply_cutdown_plan(
            con,
            plan_id=plan_id,
            allow_warning=allow_warning,
            allow_stale=allow_stale,
            save_validation=save_validation,
        )
    if artifact_type == "contract_plan":
        return contract_planner.apply_contract_plan(
            con,
            plan_id=plan_id,
            allow_stale=allow_stale,
            max_extensions=max_extensions,
            max_total_aav=max_total_aav,
        )
    if artifact_type == "free_agent_plan":
        return free_agent_planner.apply_free_agent_plan(
            con,
            plan_id=plan_id,
            allow_stale=allow_stale,
            max_offers=max_offers,
            max_total_aav=max_total_aav,
        )
    if artifact_type == "draft_plan":
        return {
            "review_id": int(row["review_id"]),
            "artifact_type": artifact_type,
            "plan_id": plan_id,
            "team": row["team"],
            "applied": False,
            "blocked_reason": "Draft plans are consumed by the CPU draft room; there is no direct apply bridge.",
        }
    if artifact_type == "decision_queue":
        return apply_decision_queue_review(con, row)
    return {
        "review_id": int(row["review_id"]),
        "artifact_type": artifact_type,
        "artifact_id": plan_id,
        "applied": False,
        "blocked_reason": f"No apply bridge for artifact type {artifact_type}.",
    }


def apply_review_item(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    commit_apply: bool,
    allow_unapproved: bool,
    allow_warning: bool,
    allow_stale: bool,
    max_extensions: int,
    max_offers: int,
    max_total_aav: int | None,
    save_validation: bool,
    reviewed_by: str,
    note: str | None,
) -> dict[str, Any]:
    if row["lifecycle_status"] != "approved" and not allow_unapproved:
        result = {
            "review_id": int(row["review_id"]),
            "team": row["team"],
            "applied": False,
            "blocked_reason": f"Review item must be approved before apply; current status is {row['lifecycle_status']}.",
        }
        if commit_apply:
            update_review_apply_outcome(
                con,
                row=row,
                lifecycle_status="blocked",
                result=result,
                error=result["blocked_reason"],
                reviewed_by=reviewed_by,
                note=note,
            )
        return result

    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        result = apply_review_item_bridge(
            con,
            row,
            allow_warning=allow_warning,
            allow_stale=allow_stale,
            max_extensions=max(1, max_extensions),
            max_offers=max(1, max_offers),
            max_total_aav=max_total_aav,
            save_validation=save_validation,
        )
    except Exception as exc:
        error = str(exc)
        con.rollback()
        result = {
            "review_id": int(row["review_id"]),
            "team": row["team"],
            "artifact_type": row["artifact_type"],
            "artifact_id": row["artifact_id"],
            "applied": False,
            "error": error,
        }

    result = dict(result or {})
    result["review_id"] = int(row["review_id"])
    result["artifact_type"] = row["artifact_type"]
    result["artifact_id"] = row["artifact_id"]
    result["dry_run"] = not commit_apply
    if commit_apply:
        lifecycle_status = "applied" if result.get("applied") else "blocked"
        update_review_apply_outcome(
            con,
            row=row,
            lifecycle_status=lifecycle_status,
            result=result,
            error=error or result.get("blocked_reason"),
            reviewed_by=reviewed_by,
            note=note,
        )
    return result


def print_review_apply_results(results: list[dict[str, Any]], *, applied: bool, backup: Path | None) -> None:
    mode = "APPLY" if applied else "DRY RUN"
    print(f"AI GM review apply {mode}: {len(results)} item(s)")
    if backup:
        print(f"Backup: {backup}")
    for result in results:
        status = "applied" if result.get("applied") else "blocked"
        if result.get("dry_run") and result.get("applied"):
            status = "would_apply"
        reason = result.get("blocked_reason") or result.get("error") or ""
        print(
            f"  - review #{result.get('review_id')} {status}: "
            f"{result.get('artifact_type')}#{result.get('artifact_id')}"
            f"{' - ' + reason if reason else ''}"
        )


def run_daily_autonomy(
    con: sqlite3.Connection,
    *,
    game_id: str | None,
    team_abbr: str | None,
    all_teams: bool,
    phase: str,
    include_low: bool,
    limit: int | None,
    persist: bool,
    apply_mode: bool,
    include_user_team: bool,
    mode_override: str | None,
    max_players: int,
    max_free_agents: int,
    current_date: str | None = None,
) -> dict[str, Any]:
    ensure_schema(con)
    game_state = ops_controller.apply_date_override(con, resolve_game_state(con, game_id), current_date)
    default_settings = with_autonomy_mode(
        load_autonomy_settings(con, game_id=game_state["game_id"], team_id=None),
        mode_override,
    )
    resolved_limit = limit or default_settings.max_operations_per_day
    ops_result = ops_scan_result(
        con,
        game_id=game_state["game_id"],
        team_abbr=team_abbr,
        all_teams=all_teams,
        phase=phase,
        include_low=include_low,
        limit=resolved_limit,
        current_date=game_state["current_date"],
        enqueue=False,
        max_players=max_players,
        max_free_agents=max_free_agents,
    )
    user_team_id = user_team_id_for_game(con, game_state["game_id"])
    applied_so_far = 0
    operation_results: list[dict[str, Any]] = []
    for operation in ops_result["operations"]:
        team_id = int(operation["team_id"])
        settings = with_autonomy_mode(
            load_autonomy_settings(con, game_id=game_state["game_id"], team_id=team_id),
            mode_override,
        )
        if include_user_team:
            settings = AutonomySettings(
                game_id=settings.game_id,
                team_id=settings.team_id,
                mode=settings.mode,
                queue_llm_advisory=settings.queue_llm_advisory,
                auto_apply_low_risk=settings.auto_apply_low_risk,
                review_medium_risk=settings.review_medium_risk,
                review_high_risk=settings.review_high_risk,
                include_user_team=1,
                max_operations_per_day=settings.max_operations_per_day,
                max_auto_apply_per_day=settings.max_auto_apply_per_day,
            )
        if user_team_id is not None and team_id == user_team_id and not settings.include_user_team:
            operation_results.append(
                {
                    "operation_key": operation["operation_key"],
                    "team": operation["team"],
                    "team_id": team_id,
                    "operation_type": operation["operation_type"],
                    "decision_type": operation["decision_type"],
                    "priority": operation["priority"],
                    "risk_tier": risk_tier_for_operation(operation),
                    "autonomy_mode": settings.mode,
                    "summary": operation.get("summary"),
                    "status": "skipped_user_team",
                    "review_reason": "active user team excluded from CPU autonomy",
                }
            )
            continue
        existing_review = open_review_item_for_operation(
            con,
            game_id=game_state["game_id"],
            team_id=team_id,
            operation_type=str(operation["operation_type"]),
        ) if persist else None
        existing_review_blocks = existing_review and not (
            settings.mode == "full_cpu_control" and risk_tier_for_operation(operation) != "high"
        )
        if existing_review_blocks:
            operation_results.append(
                {
                    "operation_key": operation["operation_key"],
                    "team": operation["team"],
                    "team_id": team_id,
                    "operation_type": operation["operation_type"],
                    "decision_type": operation["decision_type"],
                    "priority": operation["priority"],
                    "risk_tier": risk_tier_for_operation(operation),
                    "autonomy_mode": settings.mode,
                    "summary": operation.get("summary"),
                    "status": "skipped_existing_review",
                    "review_id": int(existing_review["review_id"]),
                    "review_reason": f"open review #{existing_review['review_id']} is already {existing_review['lifecycle_status']}",
                }
            )
            continue
        operation_result = execute_autonomy_operation(
            con,
            operation,
            settings=settings,
            season=int(game_state["season"]),
            current_date=str(game_state["current_date"]),
            persist=persist,
            apply_mode=apply_mode,
            applied_so_far=applied_so_far,
            max_players=max_players,
            max_free_agents=max_free_agents,
        )
        if operation_result["status"] == "applied":
            applied_so_far += 1
        memory_id = record_autonomy_memory(
            con,
            operation_result=operation_result,
            current_date=str(game_state["current_date"]),
            persist=persist,
        )
        if memory_id:
            operation_result["memory_id"] = memory_id
        operation_results.append(operation_result)

    counts: dict[str, int] = {
        "operations_scanned": len(ops_result["operations"]),
        "planned": 0,
        "enqueued": 0,
        "applied": 0,
        "blocked": 0,
        "skipped": 0,
    }
    for result in operation_results:
        status = str(result.get("status") or "")
        if result.get("plan"):
            counts["planned"] += 1
        if status in {"queued", "already_queued", "would_enqueue"}:
            counts["enqueued"] += 1
        if status == "applied":
            counts["applied"] += 1
        if status in {"blocked", "apply_blocked"}:
            counts["blocked"] += 1
        if status in {"skipped_user_team", "skipped_existing_review"}:
            counts["skipped"] += 1

    result = {
        "game": game_state,
        "autonomy": autonomy_settings_to_dict(default_settings),
        "persist": persist,
        "apply": apply_mode,
        "include_user_team": include_user_team,
        "ops_scan": {
            "requested_phase": ops_result["requested_phase"],
            "resolved_phase": ops_result["resolved_phase"],
            "counts": ops_result["counts"],
        },
        "counts": counts,
        "operations": operation_results,
    }
    if persist:
        cur = con.execute(
            """
            INSERT INTO ai_gm_daily_runs (
                game_id, run_date, season, phase_code, scope_team_id, all_teams,
                autonomy_mode, persist_mode, apply_mode, operations_scanned,
                operations_planned, operations_enqueued, operations_applied,
                operations_blocked, result_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                game_state["game_id"],
                game_state["current_date"],
                int(game_state["season"]),
                game_state["phase_code"],
                get_team(con, team_abbr)["team_id"] if team_abbr else None,
                int(bool(all_teams)),
                default_settings.mode,
                int(bool(persist)),
                int(bool(apply_mode)),
                counts["operations_scanned"],
                counts["planned"],
                counts["enqueued"],
                counts["applied"],
                counts["blocked"],
                json_dumps(result),
            ),
        )
        result["run_id"] = int(cur.lastrowid)
        result["review_items"] = create_review_items_for_daily_run(
            con,
            run_result=result,
            run_id=int(cur.lastrowid),
        )
        con.execute(
            "UPDATE ai_gm_daily_runs SET result_json = ? WHERE run_id = ?",
            (json_dumps(result), int(cur.lastrowid)),
        )
    return result


def print_daily_autonomy_result(result: dict[str, Any]) -> None:
    game = result["game"]
    counts = result["counts"]
    mode = result["autonomy"]["mode"]
    persistence = "persist" if result["persist"] else "dry-run"
    apply_mode = "apply" if result["apply"] else "no-apply"
    print(
        f"AI GM daily run {persistence}/{apply_mode}: {game['game_id']} "
        f"{game['current_date']} {game['phase_code']} mode={mode}"
    )
    if result.get("run_id"):
        print(f"Run id: {result['run_id']}")
    print(
        f"Operations: scanned {counts['operations_scanned']}, planned {counts['planned']}, "
        f"enqueued {counts['enqueued']}, applied {counts['applied']}, "
        f"blocked {counts['blocked']}, skipped {counts['skipped']}"
    )
    for item in result["operations"][:20]:
        label = f"{item['team']} {item['operation_type']} [{item['risk_tier']}]"
        print(f"  - {item['status']:<17} {label}: {item.get('summary') or ''}")
        if item.get("plan"):
            plan = item["plan"]
            detail = f"{plan.get('plan_type')} plan"
            if plan.get("plan_id"):
                detail += f" {plan['plan_id']}"
            if plan.get("validation_status"):
                detail += f" ({plan['validation_status']})"
            print(f"      {detail}")
        if item.get("queue"):
            print(f"      queue: {item['queue']['status']} {item['queue'].get('queue_id') or ''}".rstrip())
        if item.get("auto_apply_reason") and item["status"] != "applied":
            print(f"      auto-apply: {item['auto_apply_reason']}")
        if item.get("review_reason"):
            print(f"      review: {item['review_reason']}")
        if item.get("review_id"):
            print(f"      inbox: review #{item['review_id']}")


def action_ops(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    result = ops_scan_result(
        con,
        game_id=args.game_id,
        team_abbr=args.team,
        all_teams=args.all,
        phase=args.phase,
        include_low=args.include_low,
        limit=args.limit,
        current_date=None,
        enqueue=args.enqueue,
        dedupe=args.dedupe,
        max_players=args.max_players,
        max_free_agents=args.max_free_agents,
    )
    if args.enqueue:
        con.commit()
    if args.json:
        print(json_dumps(result))
    else:
        ops_controller.print_operations(result, detail_limit=args.detail_limit)
        if args.enqueue:
            queued = int(result["counts"].get("queued", 0))
            skipped = int(result["counts"].get("queue_skipped", 0))
            print(f"Queued {queued}; skipped {skipped} existing queued/running operations.")


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


def action_autonomy_config(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game_state = resolve_game_state(con, args.game_id)
    team_id = get_team(con, args.team)["team_id"] if args.team else None
    current = load_autonomy_settings(con, game_id=game_state["game_id"], team_id=team_id)
    mode = normalize_autonomy_mode(args.mode or current.mode)
    queue_llm = current.queue_llm_advisory if args.queue_llm is None else int(args.queue_llm)
    auto_apply = current.auto_apply_low_risk if args.auto_apply_low_risk is None else int(args.auto_apply_low_risk)
    review_medium = current.review_medium_risk if args.review_medium_risk is None else int(args.review_medium_risk)
    review_high = current.review_high_risk if args.review_high_risk is None else int(args.review_high_risk)
    include_user = current.include_user_team if args.include_user_team is None else int(args.include_user_team)
    max_ops = args.max_operations_per_day if args.max_operations_per_day is not None else current.max_operations_per_day
    max_apply = args.max_auto_apply_per_day if args.max_auto_apply_per_day is not None else current.max_auto_apply_per_day
    upsert_autonomy_settings(
        con,
        game_id=game_state["game_id"],
        team_id=team_id,
        mode=mode,
        queue_llm_advisory=queue_llm,
        auto_apply_low_risk=auto_apply,
        review_medium_risk=review_medium,
        review_high_risk=review_high,
        include_user_team=include_user,
        max_operations_per_day=max_ops,
        max_auto_apply_per_day=max_apply,
    )
    con.commit()
    saved = load_autonomy_settings(con, game_id=game_state["game_id"], team_id=team_id)
    label = args.team.upper() if args.team else "default"
    print(f"AI GM autonomy saved for game_id={game_state['game_id']} scope={label}")
    for key, value in autonomy_settings_to_dict(saved).items():
        print(f"  {key}: {value}")


def action_autonomy_show(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game_state = resolve_game_state(con, args.game_id)
    params: list[Any] = [game_state["game_id"]]
    where = ["game_id = ?"]
    if args.team:
        where.append("team = ?")
        params.append(args.team.upper())
    clause = " AND ".join(where)
    rows = con.execute(
        f"""
        SELECT *
        FROM ai_gm_autonomy_settings_view
        WHERE {clause}
        ORDER BY team IS NOT NULL, team
        """,
        params,
    ).fetchall()
    if not rows and not args.team:
        settings = default_autonomy_settings(game_state["game_id"])
        print(f"AI GM autonomy default for game_id={game_state['game_id']} (not saved yet)")
        for key, value in autonomy_settings_to_dict(settings).items():
            print(f"  {key}: {value}")
        return
    if not rows and args.team:
        team = get_team(con, args.team)
        settings = load_autonomy_settings(con, game_id=game_state["game_id"], team_id=int(team["team_id"]))
        print(f"AI GM autonomy inherited for {team['abbreviation']} game_id={game_state['game_id']}")
        for key, value in autonomy_settings_to_dict(settings).items():
            print(f"  {key}: {value}")
        return
    for row in rows:
        label = row["team"] or "default"
        print(f"{label} game_id={row['game_id']} mode={row['mode']}")
        print(
            f"  queue_llm={bool(row['queue_llm_advisory'])} "
            f"auto_low={bool(row['auto_apply_low_risk'])} "
            f"review_medium={bool(row['review_medium_risk'])} "
            f"review_high={bool(row['review_high_risk'])} "
            f"include_user={bool(row['include_user_team'])}"
        )
        print(
            f"  max_ops={row['max_operations_per_day']} "
            f"max_auto_apply={row['max_auto_apply_per_day']}"
        )


def action_daily_run(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    backup_path = None
    persist = bool(args.persist or args.apply)
    if args.apply and not args.no_backup:
        backup_path = backup_sqlite(args.db, "ai_gm_daily_run")
    result = run_daily_autonomy(
        con,
        game_id=args.game_id,
        team_abbr=args.team,
        all_teams=args.all,
        phase=args.phase,
        include_low=args.include_low,
        limit=args.limit,
        persist=persist,
        apply_mode=args.apply,
        include_user_team=args.include_user_team,
        mode_override=args.mode,
        max_players=args.max_players,
        max_free_agents=args.max_free_agents,
        current_date=args.current_date,
    )
    if persist:
        con.commit()
    else:
        con.rollback()
    if args.json:
        print(json_dumps(result))
    else:
        print_daily_autonomy_result(result)
        if backup_path:
            print(f"Backup created: {backup_path}")
        if not persist:
            print("Dry run only. No plans, queue rows, memory, or roster changes were saved.")


def print_review_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("No AI GM review items found.")
        return
    print("ID   Team Risk   Status          Type             Artifact  Summary")
    for row in rows:
        artifact = f"{row['artifact_type']}#{row['artifact_id']}" if row["artifact_id"] is not None else row["artifact_type"]
        summary = str(row["summary"] or row["title"] or "")
        if len(summary) > 76:
            summary = summary[:73].rstrip() + "..."
        print(
            f"{int(row['review_id']):<4} {row['team']:<4} {row['risk_tier']:<6} "
            f"{row['lifecycle_status']:<15} {row['item_type']:<16} {artifact:<9} {summary}"
        )


def review_activity_time(row: sqlite3.Row) -> str:
    return str(row["applied_at"] or row["reviewed_at"] or row["updated_at"] or row["created_at"] or "")


def review_activity_summary(row: sqlite3.Row) -> str:
    if row["apply_error"]:
        return str(row["apply_error"])
    result: dict[str, Any] = {}
    if row["apply_result_json"]:
        try:
            parsed = json.loads(str(row["apply_result_json"]))
            result = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            result = {}
    if result.get("blocked_reason"):
        return str(result["blocked_reason"])
    if result.get("error"):
        return str(result["error"])
    if result.get("applied"):
        if row["artifact_type"] == "decision_queue":
            proposal_count = len(result.get("trade_proposals") or [])
            action_count = len(result.get("trade_actions") or [])
            if proposal_count:
                return f"Created {proposal_count} trade proposal(s)"
            if action_count:
                actions = result.get("trade_actions") or []
                status = actions[0].get("status") if actions and isinstance(actions[0], dict) else None
                return f"Trade response {status}" if status else f"Applied {action_count} trade response(s)"
            return "Applied queued decision"
        if row["artifact_type"] == "cutdown_plan":
            return "Applied roster cutdown"
        if row["artifact_type"] == "contract_plan":
            count = len(result.get("extensions") or result.get("signed_extensions") or [])
            return f"Applied {count} extension(s)" if count else "Applied contract plan"
        if row["artifact_type"] == "free_agent_plan":
            count = len(result.get("offers") or result.get("submitted_offers") or [])
            return f"Submitted {count} offer(s)" if count else "Applied free-agent plan"
        return "Applied"
    status = str(row["lifecycle_status"] or "")
    if status == "approved":
        return f"Approved by {row['reviewed_by'] or 'user'}"
    if status == "rejected":
        return str(row["review_note"] or "Rejected")
    if status == "blocked":
        return str(row["apply_error"] or "Blocked")
    if status == "pending_review":
        return "Awaiting review"
    return status.replace("_", " ").title() if status else "-"


def print_review_history_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("No AI GM review history found.")
        return
    print("ID   Team Status          Type             Updated             Outcome")
    for row in rows:
        outcome = review_activity_summary(row)
        if len(outcome) > 86:
            outcome = outcome[:83].rstrip() + "..."
        print(
            f"{int(row['review_id']):<4} {row['team']:<4} {row['lifecycle_status']:<15} "
            f"{row['item_type']:<16} {review_activity_time(row):<19} {outcome}"
        )


def action_review_inbox(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    status = args.status or "pending_review"
    if status != "all" and status not in REVIEW_LIFECYCLE_STATUSES:
        raise ValueError(f"Invalid lifecycle status: {status}")
    rows = review_inbox_rows(
        con,
        game_id=game_state["game_id"],
        team_abbr=args.team,
        lifecycle_status=status,
        risk_tier=args.risk,
        item_type=args.type,
        limit=args.limit,
    )
    if args.json:
        print(json_dumps([dict(row) for row in rows]))
    else:
        print_review_rows(rows)


def action_review_history(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    status = args.status or "all"
    if status != "all" and status not in REVIEW_LIFECYCLE_STATUSES:
        raise ValueError(f"Invalid lifecycle status: {status}")
    rows = review_history_rows(
        con,
        game_id=game_state["game_id"],
        team_abbr=args.team,
        lifecycle_status=status,
        risk_tier=args.risk,
        item_type=args.type,
        limit=args.limit,
    )
    if args.json:
        payload = [dict(row) | {"activity_time": review_activity_time(row), "activity_summary": review_activity_summary(row)} for row in rows]
        print(json_dumps(payload))
    else:
        print_review_history_rows(rows)


def action_review_show(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    row = load_review_item(con, int(args.review_id))
    detail = json.loads(row["detail_json"] or "{}")
    if args.json:
        payload = dict(row)
        payload["detail"] = detail
        payload["commands"] = review_item_commands(row)
        print(json_dumps(payload))
        return
    print(f"AI GM review #{row['review_id']} {row['team']} {row['lifecycle_status']}")
    print(f"  Title: {row['title']}")
    print(f"  Risk: {row['risk_tier']} | Priority: {row['priority']} | Date: {row['review_date']}")
    print(f"  Type: {row['item_type']} | Artifact: {row['artifact_type']}#{row['artifact_id']}")
    if row["operation_type"]:
        print(f"  Operation: {row['operation_type']} | Decision: {row['decision_type'] or '-'}")
    if row["summary"]:
        print(f"  Summary: {row['summary']}")
    if row["review_note"]:
        print(f"  Review note: {row['review_note']}")
    if row["apply_error"]:
        print(f"  Apply error: {row['apply_error']}")
    if row["applied_at"]:
        print(f"  Applied at: {row['applied_at']}")
    plan = detail.get("plan") or {}
    queue = detail.get("queue") or {}
    if plan:
        print(f"  Plan: {plan.get('plan_type') or '-'} #{plan.get('plan_id') or '-'} {plan.get('validation_status') or ''}".rstrip())
    if queue:
        print(f"  Queue: {queue.get('status') or '-'} #{queue.get('queue_id') or '-'}")
    print("  Suggested commands:")
    for command in review_item_commands(row):
        print(f"    {command}")


def action_review_update(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    row = update_review_item_status(
        con,
        review_id=int(args.review_id),
        lifecycle_status=args.status,
        note=args.note,
        reviewed_by=args.reviewed_by,
    )
    con.commit()
    if args.json:
        print(json_dumps(dict(row)))
        return
    print(f"AI GM review #{row['review_id']} updated to {row['lifecycle_status']}.")
    if row["artifact_type"] == "decision_queue" and row["lifecycle_status"] == "rejected":
        print(f"Linked queue item {row['artifact_id']} was cancelled if it was still queued/running.")


def action_review_apply(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    if bool(args.review_id) == bool(args.all_approved):
        raise ValueError("Provide exactly one of --review-id or --all-approved.")
    game_state = resolve_game_state(con, args.game_id)
    backup_path = None
    if args.apply and not args.no_backup:
        label = f"ai_gm_review_{args.review_id}" if args.review_id else "ai_gm_review_all"
        backup_path = backup_sqlite(args.db, label)
    if args.review_id:
        rows = [load_review_item(con, int(args.review_id))]
    else:
        rows = review_inbox_rows(
            con,
            game_id=game_state["game_id"],
            team_abbr=args.team,
            lifecycle_status="approved",
            risk_tier=args.risk,
            item_type=args.type,
            limit=args.limit,
        )
    results = [
        apply_review_item(
            con,
            row,
            commit_apply=args.apply,
            allow_unapproved=args.allow_unapproved,
            allow_warning=args.allow_warning,
            allow_stale=args.allow_stale,
            max_extensions=args.max_extensions,
            max_offers=args.max_offers,
            max_total_aav=args.max_total_aav,
            save_validation=not args.no_validation_save,
            reviewed_by=args.reviewed_by,
            note=args.note,
        )
        for row in rows
    ]
    if args.apply:
        con.commit()
    else:
        con.rollback()
    if args.json:
        print(json_dumps({"applied": bool(args.apply), "backup": str(backup_path) if backup_path else None, "results": results}))
    else:
        print_review_apply_results(results, applied=bool(args.apply), backup=backup_path)
        if not args.apply:
            print("Dry run only. No review item status or game-state changes were saved.")


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


def action_queue(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_state = resolve_game_state(con, args.game_id)
    rows = queue_rows(
        con,
        game_id=game_state["game_id"],
        team_abbr=args.team,
        status=args.status,
        limit=args.limit,
        include_context=False,
    )
    if args.json:
        print(json_dumps(rows))
    else:
        print_queue_rows(rows)


def action_process_queue(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    if not args.team and not args.all:
        raise ValueError("Provide --team TEAM or --all.")
    game_state = resolve_game_state(con, args.game_id)
    rows = queue_rows(
        con,
        game_id=game_state["game_id"],
        team_abbr=None if args.all else args.team,
        status="queued",
        limit=args.limit,
        include_context=True,
    )
    result = process_queue_rows(con, rows, print_prompt=args.print_prompt)
    if args.json:
        print(json_dumps(result))
    else:
        print_queue_process_result(result)


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

    evaluate_parser = subparsers.add_parser("evaluate", help="Run the deterministic AI GM team evaluator.")
    evaluate_parser.add_argument("--game-id")
    evaluate_parser.add_argument("--team")
    evaluate_parser.add_argument("--all", action="store_true", help="Evaluate every team.")
    evaluate_parser.add_argument("--season", type=int)
    evaluate_parser.add_argument("--persist", action="store_true", help="Store the evaluation snapshot.")
    evaluate_parser.add_argument("--json", action="store_true")
    evaluate_parser.add_argument("--detail-limit", type=int, default=5)

    cutdown_parser = subparsers.add_parser("cutdown-plan", help="Build an advisory AI GM 53/16 cutdown plan.")
    cutdown_parser.add_argument("--game-id")
    cutdown_parser.add_argument("--team")
    cutdown_parser.add_argument("--all", action="store_true", help="Plan for every team.")
    cutdown_parser.add_argument("--season", type=int)
    cutdown_parser.add_argument("--active-limit", type=int)
    cutdown_parser.add_argument("--practice-squad-limit", type=int)
    cutdown_parser.add_argument("--persist", action="store_true", help="Store the advisory plan snapshot.")
    cutdown_parser.add_argument("--json", action="store_true")
    cutdown_parser.add_argument("--detail-limit", type=int, default=8)

    cutdown_plans_parser = subparsers.add_parser("cutdown-plans", help="List persisted advisory AI GM cutdown plans.")
    cutdown_plans_parser.add_argument("--game-id")
    cutdown_plans_parser.add_argument("--team")
    cutdown_plans_parser.add_argument("--limit", type=int, default=20)
    cutdown_plans_parser.add_argument("--json", action="store_true")

    apply_cutdown_parser = subparsers.add_parser(
        "apply-cutdown-plan",
        help="Review/apply one persisted AI GM cutdown plan. Dry-run unless --apply is supplied.",
    )
    apply_cutdown_parser.add_argument("--plan-id", type=int, required=True)
    apply_cutdown_parser.add_argument("--allow-warning", action="store_true")
    apply_cutdown_parser.add_argument("--allow-stale", action="store_true")
    apply_cutdown_parser.add_argument("--apply", action="store_true", help="Commit roster changes.")
    apply_cutdown_parser.add_argument("--no-backup", action="store_true", help="Skip DB backup before committed apply.")
    apply_cutdown_parser.add_argument("--no-validation-save", action="store_true")
    apply_cutdown_parser.add_argument("--json", action="store_true")

    contract_parser = subparsers.add_parser("contract-plan", help="Build an advisory AI GM expiring-contract plan.")
    contract_parser.add_argument("--game-id")
    contract_parser.add_argument("--team")
    contract_parser.add_argument("--all", action="store_true", help="Plan for every team.")
    contract_parser.add_argument("--season", type=int)
    contract_parser.add_argument("--persist", action="store_true", help="Store the advisory contract plan snapshot.")
    contract_parser.add_argument("--json", action="store_true")
    contract_parser.add_argument("--detail-limit", type=int, default=8)

    contract_plans_parser = subparsers.add_parser("contract-plans", help="List persisted advisory AI GM contract plans.")
    contract_plans_parser.add_argument("--game-id")
    contract_plans_parser.add_argument("--team")
    contract_plans_parser.add_argument("--limit", type=int, default=20)
    contract_plans_parser.add_argument("--json", action="store_true")

    apply_contract_parser = subparsers.add_parser(
        "apply-contract-plan",
        help="Review/apply one persisted AI GM contract plan. Dry-run unless --apply is supplied.",
    )
    apply_contract_parser.add_argument("--plan-id", type=int, required=True)
    apply_contract_parser.add_argument("--allow-stale", action="store_true")
    apply_contract_parser.add_argument("--max-extensions", type=int, default=4)
    apply_contract_parser.add_argument("--max-total-aav", type=int)
    apply_contract_parser.add_argument("--apply", action="store_true", help="Commit extension contracts.")
    apply_contract_parser.add_argument("--no-backup", action="store_true", help="Skip DB backup before committed apply.")
    apply_contract_parser.add_argument("--json", action="store_true")

    fa_plan_parser = subparsers.add_parser("free-agent-plan", help="Build an advisory AI GM free-agent target plan.")
    fa_plan_parser.add_argument("--game-id")
    fa_plan_parser.add_argument("--team")
    fa_plan_parser.add_argument("--all", action="store_true", help="Plan for every team.")
    fa_plan_parser.add_argument("--league-year", type=int)
    fa_plan_parser.add_argument("--season", type=int)
    fa_plan_parser.add_argument("--market-limit", type=int, default=120)
    fa_plan_parser.add_argument("--refresh-market", action="store_true", help="Refresh the free-agent market table before planning.")
    fa_plan_parser.add_argument("--persist", action="store_true", help="Store the advisory free-agent plan snapshot.")
    fa_plan_parser.add_argument("--json", action="store_true")
    fa_plan_parser.add_argument("--detail-limit", type=int, default=8)

    draft_plan_parser = subparsers.add_parser("draft-plan", help="Build an advisory AI GM draft plan.")
    draft_plan_parser.add_argument("--game-id")
    draft_plan_parser.add_argument("--team")
    draft_plan_parser.add_argument("--all", action="store_true", help="Plan for every team.")
    draft_plan_parser.add_argument("--draft-year", type=int)
    draft_plan_parser.add_argument("--season", type=int)
    draft_plan_parser.add_argument("--board-limit", type=int, default=draft_planner.DEFAULT_BOARD_LIMIT)
    draft_plan_parser.add_argument("--persist", action="store_true", help="Store the advisory draft plan snapshot.")
    draft_plan_parser.add_argument("--json", action="store_true")
    draft_plan_parser.add_argument("--detail-limit", type=int, default=10)

    draft_plans_parser = subparsers.add_parser("draft-plans", help="List persisted advisory AI GM draft plans.")
    draft_plans_parser.add_argument("--game-id")
    draft_plans_parser.add_argument("--team")
    draft_plans_parser.add_argument("--draft-year", type=int)
    draft_plans_parser.add_argument("--limit", type=int, default=20)
    draft_plans_parser.add_argument("--json", action="store_true")

    fa_plans_parser = subparsers.add_parser("free-agent-plans", help="List persisted advisory AI GM free-agent plans.")
    fa_plans_parser.add_argument("--game-id")
    fa_plans_parser.add_argument("--team")
    fa_plans_parser.add_argument("--limit", type=int, default=20)
    fa_plans_parser.add_argument("--json", action="store_true")

    apply_fa_parser = subparsers.add_parser(
        "apply-free-agent-plan",
        help="Review/apply one persisted AI GM free-agent plan. Dry-run unless --apply is supplied.",
    )
    apply_fa_parser.add_argument("--plan-id", type=int, required=True)
    apply_fa_parser.add_argument("--allow-stale", action="store_true")
    apply_fa_parser.add_argument("--max-offers", type=int, default=4)
    apply_fa_parser.add_argument("--max-total-aav", type=int)
    apply_fa_parser.add_argument("--apply", action="store_true", help="Commit pending free-agent offers.")
    apply_fa_parser.add_argument("--no-backup", action="store_true", help="Skip DB backup before committed apply.")
    apply_fa_parser.add_argument("--json", action="store_true")

    offseason_parser = subparsers.add_parser(
        "offseason-run",
        help="Run CPU AI GM offseason phases. Dry-run unless --apply is supplied.",
    )
    offseason_parser.add_argument("--game-id")
    offseason_parser.add_argument("--team")
    offseason_parser.add_argument("--all", action="store_true", help="Run for every CPU team.")
    offseason_parser.add_argument(
        "--phase",
        choices=sorted(offseason_driver.PHASES),
        default="pre-free-agency",
        help="Offseason phase to run.",
    )
    offseason_parser.add_argument("--season", type=int)
    offseason_parser.add_argument("--league-year", type=int)
    offseason_parser.add_argument("--max-teams", type=int, default=32)
    offseason_parser.add_argument(
        "--include-user-team",
        action="store_true",
        help="Include the active save user team when --all is used.",
    )
    offseason_parser.add_argument("--allow-stale", action="store_true")
    offseason_parser.add_argument(
        "--max-extensions-per-team",
        type=int,
        default=offseason_driver.DEFAULT_MAX_EXTENSIONS_PER_TEAM,
    )
    offseason_parser.add_argument("--max-extension-aav", type=int)
    offseason_parser.add_argument(
        "--max-offers-per-team",
        type=int,
        default=offseason_driver.DEFAULT_MAX_OFFERS_PER_TEAM,
    )
    offseason_parser.add_argument("--max-fa-aav", type=int)
    offseason_parser.add_argument("--market-limit", type=int, default=offseason_driver.DEFAULT_MARKET_LIMIT)
    offseason_parser.add_argument("--refresh-market", action="store_true")
    offseason_parser.add_argument("--apply", action="store_true", help="Commit persisted plans and successful operations.")
    offseason_parser.add_argument("--no-backup", action="store_true", help="Skip DB backup before committed apply.")
    offseason_parser.add_argument("--json", action="store_true")
    offseason_parser.add_argument("--detail-limit", type=int, default=32)

    ops_parser = subparsers.add_parser(
        "ops",
        help="Scan phase-aware AI GM operations and optionally queue advisory tasks.",
    )
    ops_parser.add_argument("--game-id")
    ops_parser.add_argument("--team")
    ops_parser.add_argument("--all", action="store_true", help="Scan every team.")
    ops_parser.add_argument("--phase", choices=sorted(ops_controller.OPS_PHASES), default="auto")
    ops_parser.add_argument("--include-low", action="store_true", help="Include low-priority housekeeping operations.")
    ops_parser.add_argument("--limit", type=int, default=40)
    ops_parser.add_argument("--detail-limit", type=int, default=20)
    ops_parser.add_argument("--enqueue", action="store_true", help="Persist operations to ai_gm_decision_queue.")
    ops_parser.add_argument("--dedupe", action=argparse.BooleanOptionalAction, default=True)
    ops_parser.add_argument("--max-players", type=int, default=18)
    ops_parser.add_argument("--max-free-agents", type=int, default=18)
    ops_parser.add_argument("--json", action="store_true")

    queue_parser = subparsers.add_parser("queue", help="List AI GM decision queue rows.")
    queue_parser.add_argument("--game-id")
    queue_parser.add_argument("--team")
    queue_parser.add_argument(
        "--status",
        choices=["queued", "running", "completed", "invalid", "failed", "cancelled", "all"],
        default="queued",
    )
    queue_parser.add_argument("--limit", type=int, default=20)
    queue_parser.add_argument("--json", action="store_true")

    process_queue_parser = subparsers.add_parser(
        "process-queue",
        help="Process queued AI GM advisory decisions in priority order.",
    )
    process_queue_parser.add_argument("--game-id")
    process_queue_parser.add_argument("--team")
    process_queue_parser.add_argument("--all", action="store_true", help="Process all teams.")
    process_queue_parser.add_argument("--limit", type=int, default=5)
    process_queue_parser.add_argument("--print-prompt", action="store_true")
    process_queue_parser.add_argument("--json", action="store_true")

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

    autonomy_config_parser = subparsers.add_parser("autonomy-config", help="Set AI GM autonomy policy for a game or team.")
    autonomy_config_parser.add_argument("--game-id")
    autonomy_config_parser.add_argument("--team", help="Optional team override. Omit for game default.")
    autonomy_config_parser.add_argument("--mode", choices=sorted(AUTONOMY_MODES))
    autonomy_config_parser.add_argument("--queue-llm", action=argparse.BooleanOptionalAction, default=None)
    autonomy_config_parser.add_argument("--auto-apply-low-risk", action=argparse.BooleanOptionalAction, default=None)
    autonomy_config_parser.add_argument("--review-medium-risk", action=argparse.BooleanOptionalAction, default=None)
    autonomy_config_parser.add_argument("--review-high-risk", action=argparse.BooleanOptionalAction, default=None)
    autonomy_config_parser.add_argument("--include-user-team", action=argparse.BooleanOptionalAction, default=None)
    autonomy_config_parser.add_argument("--max-operations-per-day", type=int)
    autonomy_config_parser.add_argument("--max-auto-apply-per-day", type=int)

    autonomy_show_parser = subparsers.add_parser("autonomy-show", help="Show AI GM autonomy policy.")
    autonomy_show_parser.add_argument("--game-id")
    autonomy_show_parser.add_argument("--team")

    daily_parser = subparsers.add_parser("daily-run", help="Run the central AI GM autonomy loop. Dry-run unless --persist or --apply is supplied.")
    daily_parser.add_argument("--game-id")
    daily_parser.add_argument("--team")
    daily_parser.add_argument("--all", action="store_true", help="Run for all teams.")
    daily_parser.add_argument("--phase", choices=sorted(ops_controller.OPS_PHASES), default="auto")
    daily_parser.add_argument("--mode", choices=sorted(AUTONOMY_MODES), help="Temporary mode override for this run.")
    daily_parser.add_argument("--include-low", action="store_true")
    daily_parser.add_argument("--include-user-team", action="store_true")
    daily_parser.add_argument("--limit", type=int)
    daily_parser.add_argument("--current-date", help="Override the game date for scan context.")
    daily_parser.add_argument("--max-players", type=int, default=18)
    daily_parser.add_argument("--max-free-agents", type=int, default=18)
    daily_parser.add_argument("--persist", action="store_true", help="Save plans, queue rows, daily-run log, and AI GM memories.")
    daily_parser.add_argument("--apply", action="store_true", help="Auto-apply allowed low-risk operations after validation.")
    daily_parser.add_argument("--no-backup", action="store_true", help="Skip DB backup before committed apply.")
    daily_parser.add_argument("--json", action="store_true")

    review_inbox_parser = subparsers.add_parser("review-inbox", help="List AI GM decisions that need user/commissioner review.")
    review_inbox_parser.add_argument("--game-id")
    review_inbox_parser.add_argument("--team")
    review_inbox_parser.add_argument("--status", default="pending_review", help="Lifecycle status, or 'all'.")
    review_inbox_parser.add_argument("--risk", choices=["low", "medium", "high"])
    review_inbox_parser.add_argument("--type", help="Filter by item_type such as cutdown_plan, contract_plan, free_agent_plan, draft_plan, queued_decision.")
    review_inbox_parser.add_argument("--limit", type=int, default=20)
    review_inbox_parser.add_argument("--json", action="store_true")

    review_history_parser = subparsers.add_parser("review-history", help="Show recent AI GM review lifecycle and apply history.")
    review_history_parser.add_argument("--game-id")
    review_history_parser.add_argument("--team")
    review_history_parser.add_argument("--status", default="all", help="Lifecycle status, or 'all'.")
    review_history_parser.add_argument("--risk", choices=["low", "medium", "high"])
    review_history_parser.add_argument("--type", help="Filter by item_type such as cutdown_plan, contract_plan, free_agent_plan, draft_plan, queued_decision.")
    review_history_parser.add_argument("--limit", type=int, default=20)
    review_history_parser.add_argument("--json", action="store_true")

    review_show_parser = subparsers.add_parser("review-show", help="Inspect one AI GM review item and its suggested follow-up commands.")
    review_show_parser.add_argument("--review-id", type=int, required=True)
    review_show_parser.add_argument("--json", action="store_true")

    review_update_parser = subparsers.add_parser("review-update", help="Update the lifecycle status of one AI GM review item.")
    review_update_parser.add_argument("--review-id", type=int, required=True)
    review_update_parser.add_argument("--status", required=True, choices=sorted(REVIEW_LIFECYCLE_STATUSES))
    review_update_parser.add_argument("--note")
    review_update_parser.add_argument("--reviewed-by", default="user")
    review_update_parser.add_argument("--json", action="store_true")

    review_apply_parser = subparsers.add_parser("review-apply", help="Dry-run or apply approved AI GM review items.")
    review_apply_target = review_apply_parser.add_mutually_exclusive_group(required=True)
    review_apply_target.add_argument("--review-id", type=int)
    review_apply_target.add_argument("--all-approved", action="store_true", help="Apply approved review items matching the filters.")
    review_apply_parser.add_argument("--game-id")
    review_apply_parser.add_argument("--team")
    review_apply_parser.add_argument("--risk", choices=["low", "medium", "high"])
    review_apply_parser.add_argument("--type", help="Filter --all-approved by item_type.")
    review_apply_parser.add_argument("--limit", type=int, default=20)
    review_apply_parser.add_argument("--apply", action="store_true", help="Commit validated game-state changes. Omit for dry run.")
    review_apply_parser.add_argument("--allow-unapproved", action="store_true", help="Allow applying one item that is not approved yet.")
    review_apply_parser.add_argument("--allow-warning", action="store_true", help="Allow warning cutdown plans.")
    review_apply_parser.add_argument("--allow-stale", action="store_true", help="Allow stale saved plans.")
    review_apply_parser.add_argument("--max-extensions", type=int, default=4)
    review_apply_parser.add_argument("--max-offers", type=int, default=4)
    review_apply_parser.add_argument("--max-total-aav", type=int)
    review_apply_parser.add_argument("--no-validation-save", action="store_true")
    review_apply_parser.add_argument("--no-backup", action="store_true")
    review_apply_parser.add_argument("--reviewed-by", default="user")
    review_apply_parser.add_argument("--note")
    review_apply_parser.add_argument("--json", action="store_true")

    dev_seed_review_parser = subparsers.add_parser("dev-seed-review", help="Create a safe development-only AI GM review item for UI workflow testing.")
    dev_seed_review_parser.add_argument("--game-id")
    dev_seed_review_parser.add_argument("--team", required=True)
    dev_seed_review_parser.add_argument("--clear-existing", action="store_true", help="Remove prior dev review seed items for this game/team first.")
    dev_seed_review_parser.add_argument("--json", action="store_true")

    dev_clear_reviews_parser = subparsers.add_parser("dev-clear-reviews", help="Delete development-only AI GM review seed items.")
    dev_clear_reviews_parser.add_argument("--game-id")
    dev_clear_reviews_parser.add_argument("--team")
    dev_clear_reviews_parser.add_argument("--json", action="store_true")

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
        elif args.command == "evaluate":
            action_evaluate(con, args)
        elif args.command == "cutdown-plan":
            action_cutdown_plan(con, args)
        elif args.command == "cutdown-plans":
            action_cutdown_plans(con, args)
        elif args.command == "apply-cutdown-plan":
            action_apply_cutdown_plan(con, args)
        elif args.command == "contract-plan":
            action_contract_plan(con, args)
        elif args.command == "contract-plans":
            action_contract_plans(con, args)
        elif args.command == "apply-contract-plan":
            action_apply_contract_plan(con, args)
        elif args.command == "free-agent-plan":
            action_free_agent_plan(con, args)
        elif args.command == "draft-plan":
            action_draft_plan(con, args)
        elif args.command == "draft-plans":
            action_draft_plans(con, args)
        elif args.command == "free-agent-plans":
            action_free_agent_plans(con, args)
        elif args.command == "apply-free-agent-plan":
            action_apply_free_agent_plan(con, args)
        elif args.command == "offseason-run":
            action_offseason_run(con, args)
        elif args.command == "ops":
            action_ops(con, args)
        elif args.command == "queue":
            action_queue(con, args)
        elif args.command == "process-queue":
            action_process_queue(con, args)
        elif args.command == "config":
            if args.enable and args.disable:
                raise ValueError("Use only one of --enable or --disable.")
            action_config(con, args)
        elif args.command == "show-config":
            action_show_config(con, args)
        elif args.command == "autonomy-config":
            action_autonomy_config(con, args)
        elif args.command == "autonomy-show":
            action_autonomy_show(con, args)
        elif args.command == "daily-run":
            if not args.team and not args.all:
                raise ValueError("Provide --team TEAM or --all.")
            action_daily_run(con, args)
        elif args.command == "review-inbox":
            action_review_inbox(con, args)
        elif args.command == "review-history":
            action_review_history(con, args)
        elif args.command == "review-show":
            action_review_show(con, args)
        elif args.command == "review-update":
            action_review_update(con, args)
        elif args.command == "review-apply":
            action_review_apply(con, args)
        elif args.command == "dev-seed-review":
            action_dev_seed_review(con, args)
        elif args.command == "dev-clear-reviews":
            action_dev_clear_reviews(con, args)
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
