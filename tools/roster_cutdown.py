#!/usr/bin/env python3
"""Automatic roster cutdown helper for playable saves.

This is deliberately a pragmatic football-ops fallback, not the final AI GM
brain. It makes every team regular-season compliant by choosing a balanced
53-man roster, moving developmental/fringe players to the practice squad, and
releasing the rest. Later, the selection step can be swapped for local LLM
advice while keeping the same command and transaction plumbing.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import roster_rules  # noqa: E402
from setup_contract_years import rebuild_contract_years, sync_team_cap_space  # noqa: E402
from setup_transactions_cap_ledger import insert_transaction, snapshot_cap_ledger  # noqa: E402


SOURCE = "roster_cutdown"
PHASE = "Regular Season"
ACTIVE_STATUS = "Active"
PRACTICE_SQUAD_STATUS = "Practice Squad"
FREE_AGENT_STATUS = "Free Agent"


POSITION_GROUPS: dict[str, tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB", "FB"),
    "WR": ("WR",),
    "TE": ("TE",),
    "OL": ("OT", "OG", "C"),
    "EDGE": ("EDGE", "DE"),
    "IDL": ("IDL", "DT", "NT"),
    "LB": ("ILB", "OLB", "LB"),
    "CB": ("CB", "NB"),
    "S": ("FS", "SS", "S"),
    "K": ("K",),
    "P": ("P",),
    "LS": ("LS",),
}


DEFAULT_ACTIVE_TARGETS: dict[str, int] = {
    "QB": 2,
    "RB": 4,
    "WR": 6,
    "TE": 3,
    "OL": 9,
    "EDGE": 5,
    "IDL": 5,
    "LB": 5,
    "CB": 6,
    "S": 5,
    "K": 1,
    "P": 1,
    "LS": 1,
}


POSITION_TO_GROUP = {
    position: group
    for group, positions in POSITION_GROUPS.items()
    for position in positions
}

SPECIALIST_POSITIONS = {"K", "P", "LS"}


@dataclass(frozen=True)
class PlayerCandidate:
    player_id: int
    name: str
    position: str
    group: str
    age: int
    is_rookie: int
    overall: float
    potential: float
    role_score: float
    depth_rank: int | None
    keep_score: float
    ps_score: float


@dataclass
class TeamCutdownResult:
    team: str
    before_active: int
    before_ps: int
    after_active: int
    after_ps: int
    signed_specialists: int
    moved_to_ps: int
    released: int
    validation_errors: int
    validation_warnings: int


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (name,),
    ).fetchone()
    return row is not None


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'"
    ).fetchone()
    return int(row["setting_value"]) if row else 2026


def current_date(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    return row["setting_value"] if row else datetime.now().date().isoformat()


def backup_database(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.stem}.pre_roster_cutdown_{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup)
    return backup


def ensure_cutdown_schema(con: sqlite3.Connection) -> None:
    roster_rules.ensure_schema(con)
    roster_rules.seed_rules(con)
    con.execute(
        """
        INSERT INTO transaction_types (transaction_type, category, description)
        VALUES
            ('Practice Squad Signing', 'Roster', 'Player signed to a practice squad.'),
            ('Release', 'Roster', 'Player released from a roster.'),
            ('Signing', 'Roster', 'Free agent or draft pick signed to a contract.'),
            ('Roster Status Change', 'Status', 'Player status changed.')
        ON CONFLICT(transaction_type) DO UPDATE SET
            category = excluded.category,
            description = excluded.description
        """
    )


def count_row(con: sqlite3.Connection, team_id: int) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM team_roster_counts_view WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No roster count row for team_id={team_id}.")
    return row


def team_rows(con: sqlite3.Connection, team: str | None) -> list[sqlite3.Row]:
    if team:
        row = con.execute(
            "SELECT * FROM teams WHERE abbreviation = ?",
            (team.upper(),),
        ).fetchone()
        if not row:
            raise ValueError(f"Team not found: {team}")
        return [row]
    return con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()


def best_role_score(con: sqlite3.Connection, player_id: int, season: int) -> float | None:
    if not table_exists(con, "player_role_scores"):
        return None
    row = con.execute(
        """
        SELECT MAX(role_score) AS role_score
        FROM player_role_scores
        WHERE player_id = ?
          AND season = ?
          AND scheme_key = 'default'
        """,
        (player_id, season),
    ).fetchone()
    return float(row["role_score"]) if row and row["role_score"] is not None else None


def rating_average(con: sqlite3.Connection, player_id: int, season: int) -> float | None:
    row = con.execute(
        """
        SELECT AVG(rating_value) AS rating_average
        FROM player_ratings
        WHERE player_id = ?
          AND season = ?
        """,
        (player_id, season),
    ).fetchone()
    return float(row["rating_average"]) if row and row["rating_average"] is not None else None


def depth_rank(con: sqlite3.Connection, team_id: int, player_id: int) -> int | None:
    if not table_exists(con, "depth_charts"):
        return None
    row = con.execute(
        """
        SELECT MIN(depth_rank) AS depth_rank
        FROM depth_charts
        WHERE team_id = ?
          AND player_id = ?
        """,
        (team_id, player_id),
    ).fetchone()
    return int(row["depth_rank"]) if row and row["depth_rank"] is not None else None


def active_candidates(con: sqlite3.Connection, team_id: int, season: int) -> list[PlayerCandidate]:
    rows = con.execute(
        """
        SELECT p.*
        FROM players p
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE p.team_id = ?
          AND COALESCE(
                rst.counts_against_roster_limit,
                CASE WHEN COALESCE(p.status, 'Active') NOT IN ('Retired', 'Free Agent') THEN 1 ELSE 0 END
              ) = 1
        ORDER BY p.position, p.last_name, p.first_name
        """,
        (team_id,),
    ).fetchall()

    candidates: list[PlayerCandidate] = []
    for row in rows:
        position = (row["position"] or "").upper()
        group = POSITION_TO_GROUP.get(position, "OTHER")
        overall = float(row["overall"] or 55)
        potential = float(row["potential"] or overall)
        role = best_role_score(con, int(row["player_id"]), season)
        avg_rating = rating_average(con, int(row["player_id"]), season)
        if position in SPECIALIST_POSITIONS:
            base = overall
        else:
            fallback = (overall * 0.7) + (potential * 0.2) + ((avg_rating or overall) * 0.1)
            base = max(float(role) if role is not None else 0.0, fallback)
        rank = depth_rank(con, team_id, int(row["player_id"]))
        depth_bonus = {1: 22.0, 2: 12.0, 3: 6.0}.get(rank or 0, 0.0)
        youth_bonus = 0.0
        age = int(row["age"] or 26)
        is_rookie = int(row["is_rookie"] or 0)
        if is_rookie:
            youth_bonus += 3.0
        if age <= 24:
            youth_bonus += 2.0
        elif age >= 33 and position not in SPECIALIST_POSITIONS:
            youth_bonus -= 2.0
        upside_bonus = max(0.0, potential - overall) * 0.18
        keep_score = base + depth_bonus + youth_bonus + upside_bonus
        ps_score = (potential * 0.48) + (base * 0.32) + youth_bonus + (8.0 if is_rookie else 0.0)
        if age > 28 and not is_rookie:
            ps_score -= min(10.0, float(age - 28) * 1.5)
        candidates.append(
            PlayerCandidate(
                player_id=int(row["player_id"]),
                name=f"{row['first_name']} {row['last_name']}",
                position=position,
                group=group,
                age=age,
                is_rookie=is_rookie,
                overall=overall,
                potential=potential,
                role_score=base,
                depth_rank=rank,
                keep_score=keep_score,
                ps_score=ps_score,
            )
        )
    return candidates


def choose_active_roster(candidates: list[PlayerCandidate], active_limit: int) -> set[int]:
    selected: set[int] = set()
    by_group: dict[str, list[PlayerCandidate]] = {group: [] for group in DEFAULT_ACTIVE_TARGETS}
    for candidate in candidates:
        if candidate.group in by_group:
            by_group[candidate.group].append(candidate)

    for group, target in DEFAULT_ACTIVE_TARGETS.items():
        group_candidates = sorted(
            by_group[group],
            key=lambda item: (item.keep_score, item.potential, item.overall, -item.age),
            reverse=True,
        )
        for candidate in group_candidates[:target]:
            selected.add(candidate.player_id)

    remaining = sorted(
        [candidate for candidate in candidates if candidate.player_id not in selected],
        key=lambda item: (item.keep_score, item.potential, item.overall, -item.age),
        reverse=True,
    )
    for candidate in remaining:
        if len(selected) >= active_limit:
            break
        selected.add(candidate.player_id)

    if len(selected) > active_limit:
        removable = sorted(
            [candidate for candidate in candidates if candidate.player_id in selected],
            key=lambda item: (
                item.position in SPECIALIST_POSITIONS,
                item.depth_rank == 1,
                item.keep_score,
            ),
        )
        for candidate in removable:
            if len(selected) <= active_limit:
                break
            if candidate.position in SPECIALIST_POSITIONS or candidate.depth_rank == 1:
                continue
            selected.remove(candidate.player_id)

    return selected


def choose_practice_squad(
    candidates: list[PlayerCandidate],
    active_ids: set[int],
    practice_squad_limit: int,
) -> set[int]:
    available = [
        candidate
        for candidate in candidates
        if candidate.player_id not in active_ids
        and candidate.position not in SPECIALIST_POSITIONS
    ]
    ranked = sorted(
        available,
        key=lambda item: (item.ps_score, item.potential, item.keep_score, -item.age),
        reverse=True,
    )
    return {candidate.player_id for candidate in ranked[:practice_squad_limit]}


def delete_depth_rows(con: sqlite3.Connection, player_id: int) -> None:
    if table_exists(con, "depth_charts"):
        con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player_id,))


def status_history(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    old_status: str,
    new_status: str,
    season: int,
    reason: str,
) -> None:
    con.execute(
        """
        INSERT INTO player_roster_status_history (
            player_id, old_status, new_status, effective_date, season, reason
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (player["player_id"], old_status, new_status, current_date(con), season, reason),
    )


def log_roster_transaction(
    con: sqlite3.Connection,
    *,
    transaction_type: str,
    player: sqlite3.Row,
    team_id: int | None,
    from_team_id: int | None,
    to_team_id: int | None,
    old_status: str,
    new_status: str,
    season: int,
    description: str,
    contract_id: int | None = None,
) -> int:
    transaction_id, _inserted = insert_transaction(
        con,
        transaction_date=current_date(con),
        season=season,
        phase=PHASE,
        transaction_type=transaction_type,
        team_id=team_id,
        player_id=int(player["player_id"]),
        contract_id=contract_id,
        from_team_id=from_team_id,
        to_team_id=to_team_id,
        old_status=old_status,
        new_status=new_status,
        description=description,
        source=SOURCE,
        external_ref=f"{transaction_type}:{player['player_id']}:{current_date(con)}:{new_status}",
    )
    return transaction_id


def active_contract_id(con: sqlite3.Connection, player_id: int) -> int | None:
    row = con.execute(
        """
        SELECT contract_id
        FROM contracts
        WHERE player_id = ?
          AND COALESCE(is_active, 1) = 1
        ORDER BY contract_id DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    return int(row["contract_id"]) if row else None


def move_to_practice_squad(con: sqlite3.Connection, player_id: int, season: int, notes: str) -> None:
    player = con.execute("SELECT * FROM players WHERE player_id = ?", (player_id,)).fetchone()
    if not player:
        return
    old_status = player["status"] or ACTIVE_STATUS
    team_id = int(player["team_id"])
    con.execute(
        "UPDATE players SET status = ? WHERE player_id = ?",
        (PRACTICE_SQUAD_STATUS, player_id),
    )
    delete_depth_rows(con, player_id)
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=PRACTICE_SQUAD_STATUS,
        season=season,
        reason=notes,
    )
    roster_rules.record_practice_squad_move(
        con,
        player_id=player_id,
        team_id=team_id,
        season=season,
        move_type="Sign",
        from_status=old_status,
        to_status=PRACTICE_SQUAD_STATUS,
        notes=notes,
    )
    log_roster_transaction(
        con,
        transaction_type="Practice Squad Signing",
        player=player,
        team_id=team_id,
        from_team_id=team_id,
        to_team_id=team_id,
        old_status=old_status,
        new_status=PRACTICE_SQUAD_STATUS,
        season=season,
        description=f"Auto-cutdown moved {player['first_name']} {player['last_name']} to the practice squad.",
        contract_id=active_contract_id(con, player_id),
    )


def release_player(con: sqlite3.Connection, player_id: int, season: int, notes: str) -> None:
    player = con.execute("SELECT * FROM players WHERE player_id = ?", (player_id,)).fetchone()
    if not player:
        return
    old_status = player["status"] or ACTIVE_STATUS
    from_team_id = int(player["team_id"]) if player["team_id"] is not None else None
    contract_id = active_contract_id(con, player_id)
    if contract_id:
        con.execute("UPDATE contracts SET is_active = 0 WHERE contract_id = ?", (contract_id,))
        if table_exists(con, "contract_years"):
            con.execute("UPDATE contract_years SET is_active = 0 WHERE contract_id = ?", (contract_id,))
    con.execute(
        "UPDATE players SET team_id = NULL, status = ? WHERE player_id = ?",
        (FREE_AGENT_STATUS, player_id),
    )
    delete_depth_rows(con, player_id)
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=FREE_AGENT_STATUS,
        season=season,
        reason=notes,
    )
    log_roster_transaction(
        con,
        transaction_type="Release",
        player=player,
        team_id=from_team_id,
        from_team_id=from_team_id,
        to_team_id=None,
        old_status=old_status,
        new_status=FREE_AGENT_STATUS,
        season=season,
        description=f"Auto-cutdown released {player['first_name']} {player['last_name']} to free agency.",
        contract_id=contract_id,
    )


def sign_missing_specialist(
    con: sqlite3.Connection,
    *,
    team: sqlite3.Row,
    position: str,
    season: int,
) -> bool:
    has_position = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND status = 'Active'
          AND position = ?
        """,
        (team["team_id"], position),
    ).fetchone()["count"]
    if int(has_position or 0) > 0:
        return False

    player = con.execute(
        """
        SELECT *
        FROM players
        WHERE team_id IS NULL
          AND status = 'Free Agent'
          AND position = ?
        ORDER BY COALESCE(overall, 50) DESC, COALESCE(potential, overall, 50) DESC, age ASC
        LIMIT 1
        """,
        (position,),
    ).fetchone()
    if not player:
        return False

    old_status = player["status"] or FREE_AGENT_STATUS
    minimum_aav = max(915_000, min(1_500_000, int((int(player["overall"] or 60) * 18_000) // 10_000 * 10_000)))
    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year,
            total_value, total_years, aav, signing_bonus, roster_bonus,
            workout_bonus, is_guaranteed, dead_cap_current, dead_cap_next,
            contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, 0, 0, 0, 0, 0, 'Minimum', 1)
        """,
        (
            player["player_id"],
            team["team_id"],
            current_date(con),
            season,
            season,
            minimum_aav,
            minimum_aav,
        ),
    )
    contract_id = int(cur.lastrowid)
    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (team["team_id"], player["player_id"]),
    )
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=ACTIVE_STATUS,
        season=season,
        reason=f"Auto-cutdown signed missing {position}.",
    )
    log_roster_transaction(
        con,
        transaction_type="Signing",
        player=player,
        team_id=int(team["team_id"]),
        from_team_id=None,
        to_team_id=int(team["team_id"]),
        old_status=old_status,
        new_status=ACTIVE_STATUS,
        season=season,
        description=f"{team['abbreviation']} signed {player['first_name']} {player['last_name']} as missing {position} depth before cutdown.",
        contract_id=contract_id,
    )
    return True


def normalize_unknown_roster_statuses(
    con: sqlite3.Connection,
    *,
    team: sqlite3.Row,
    season: int,
) -> int:
    rows = con.execute(
        """
        SELECT p.*
        FROM players p
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE p.team_id = ?
          AND rst.status_code IS NULL
          AND COALESCE(p.status, 'Active') NOT IN ('Free Agent', 'Retired', 'Waived')
        ORDER BY p.last_name, p.first_name
        """,
        (team["team_id"],),
    ).fetchall()
    changed = 0
    for player in rows:
        old_status = player["status"] or ACTIVE_STATUS
        con.execute(
            "UPDATE players SET status = 'Active' WHERE player_id = ?",
            (player["player_id"],),
        )
        status_history(
            con,
            player=player,
            old_status=old_status,
            new_status=ACTIVE_STATUS,
            season=season,
            reason="Auto-cutdown normalized unsupported pre-injury status to Active.",
        )
        log_roster_transaction(
            con,
            transaction_type="Roster Status Change",
            player=player,
            team_id=int(team["team_id"]),
            from_team_id=int(team["team_id"]),
            to_team_id=int(team["team_id"]),
            old_status=old_status,
            new_status=ACTIVE_STATUS,
            season=season,
            description=(
                f"Normalized {player['first_name']} {player['last_name']} "
                f"from {old_status} to Active before regular-season cutdown."
            ),
        )
        changed += 1
    return changed


def resolve_roster_alerts(con: sqlite3.Connection, game_id: str | None, team_id: int | None = None) -> None:
    if not game_id or not table_exists(con, "game_alerts"):
        return
    params: list[object] = [game_id]
    extra = ""
    if team_id is not None:
        extra = " AND team_id = ?"
        params.append(team_id)
    con.execute(
        f"""
        UPDATE game_alerts
        SET status = 'Resolved',
            resolved_at = datetime('now')
        WHERE game_id = ?
          AND alert_type = 'ROSTER_COMPLIANCE'
          AND status = 'Open'
          {extra}
        """,
        params,
    )


def validate_after(
    con: sqlite3.Connection,
    *,
    team: sqlite3.Row,
    rule_set: sqlite3.Row,
    save_validation: bool,
) -> tuple[int, int, int, int]:
    summary, issues = roster_rules.validate_team(con, team, rule_set, include_info=False)
    if save_validation:
        roster_rules.save_validation_run(con, rule_set, summary, issues)
    return (
        int(summary["active_count"]),
        int(summary["practice_squad_count"]),
        int(summary["error_count"]),
        int(summary["warning_count"]),
    )


def cutdown_team(
    con: sqlite3.Connection,
    *,
    team: sqlite3.Row,
    season: int,
    rule_set: sqlite3.Row,
    active_limit: int,
    practice_squad_limit: int,
    save_validation: bool,
    game_id: str | None,
) -> TeamCutdownResult:
    normalize_unknown_roster_statuses(con, team=team, season=season)
    before = count_row(con, int(team["team_id"]))
    signed_specialists = 0
    for position in ("K", "P", "LS"):
        if sign_missing_specialist(con, team=team, position=position, season=season):
            signed_specialists += 1

    candidates = active_candidates(con, int(team["team_id"]), season)
    active_ids = choose_active_roster(candidates, active_limit)
    ps_ids = choose_practice_squad(candidates, active_ids, practice_squad_limit)

    notes = f"Automatic regular-season cutdown for {season}."
    moved_to_ps = 0
    released = 0
    for candidate in sorted(candidates, key=lambda item: item.keep_score):
        if candidate.player_id in active_ids:
            continue
        if candidate.player_id in ps_ids:
            move_to_practice_squad(con, candidate.player_id, season, notes)
            moved_to_ps += 1
        else:
            release_player(con, candidate.player_id, season, notes)
            released += 1

    resolve_roster_alerts(con, game_id, int(team["team_id"]))
    after = count_row(con, int(team["team_id"]))
    return TeamCutdownResult(
        team=team["abbreviation"],
        before_active=int(before["active_roster_count"] or 0),
        before_ps=int(before["practice_squad_count"] or 0),
        after_active=int(after["active_roster_count"] or 0),
        after_ps=int(after["practice_squad_count"] or 0),
        signed_specialists=signed_specialists,
        moved_to_ps=moved_to_ps,
        released=released,
        validation_errors=0,
        validation_warnings=0,
    )


def print_results(results: list[TeamCutdownResult], *, applied: bool, backup: Path | None) -> None:
    mode = "APPLIED" if applied else "DRY RUN"
    print(f"Roster cutdown {mode}")
    if backup:
        print(f"Backup: {backup}")
    print("")
    print(f"{'TEAM':<4} {'ACTIVE':>11} {'PS':>7} {'SIGN':>5} {'TO_PS':>6} {'REL':>5} {'ERR':>4} {'WARN':>5}")
    for result in results:
        print(
            f"{result.team:<4} "
            f"{result.before_active:>3}->{result.after_active:<3} "
            f"{result.before_ps:>2}->{result.after_ps:<2} "
            f"{result.signed_specialists:>5} "
            f"{result.moved_to_ps:>6} "
            f"{result.released:>5} "
            f"{result.validation_errors:>4} "
            f"{result.validation_warnings:>5}"
        )
    print("")
    print(
        f"Totals: signed specialists {sum(r.signed_specialists for r in results)}, "
        f"practice squad {sum(r.moved_to_ps for r in results)}, "
        f"released {sum(r.released for r in results)}, "
        f"errors {sum(r.validation_errors for r in results)}, "
        f"warnings {sum(r.validation_warnings for r in results)}."
    )


def action_run(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    backup = backup_database(db_path) if args.apply and not args.no_backup else None
    con = connect(db_path)
    try:
        ensure_cutdown_schema(con)
        season = args.season if args.season is not None else current_season(con)
        rule_set = roster_rules.get_rule_set(con, season, PHASE)
        active_limit = args.active_limit or int(rule_set["active_roster_limit"])
        practice_squad_limit = (
            args.practice_squad_limit
            if args.practice_squad_limit is not None
            else int(rule_set["practice_squad_limit"])
        )
        teams = team_rows(con, args.team)
        results = [
            cutdown_team(
                con,
                team=team,
                season=season,
                rule_set=rule_set,
                active_limit=active_limit,
                practice_squad_limit=practice_squad_limit,
                save_validation=not args.no_validation_save,
                game_id=args.game_id,
            )
            for team in teams
        ]
        rebuild_contract_years(con)
        sync_team_cap_space(con)
        snapshot_cap_ledger(
            con,
            label="after_roster_cutdown",
            phase=PHASE,
            source=SOURCE,
            replace=True,
        )
        by_team = {result.team: result for result in results}
        for team in teams:
            after_active, after_ps, errors, warnings = validate_after(
                con,
                team=team,
                rule_set=rule_set,
                save_validation=not args.no_validation_save,
            )
            result = by_team[team["abbreviation"]]
            result.after_active = after_active
            result.after_ps = after_ps
            result.validation_errors = errors
            result.validation_warnings = warnings
        if args.apply:
            con.commit()
        else:
            con.rollback()
        print_results(results, applied=args.apply, backup=backup)
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run automatic 53-man roster cutdowns.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Create 53-man active rosters and practice squads.")
    run.add_argument("--team", help="Limit to one team abbreviation. Defaults to all teams.")
    run.add_argument("--season", type=int)
    run.add_argument("--active-limit", type=int)
    run.add_argument("--practice-squad-limit", type=int)
    run.add_argument("--game-id", help="Resolve roster-compliance alerts for this save id.")
    run.add_argument("--apply", action="store_true", help="Persist cutdown changes. Without this, rolls back.")
    run.add_argument("--no-backup", action="store_true", help="Do not create a pre-cutdown DB backup.")
    run.add_argument("--no-validation-save", action="store_true", help="Do not persist roster validation runs after cutdown.")
    run.set_defaults(func=action_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
