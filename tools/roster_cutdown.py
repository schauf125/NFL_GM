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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import roster_rules  # noqa: E402
import cpu_depth_chart  # noqa: E402
import pro_player_fog  # noqa: E402
from engine import depth_packages  # noqa: E402
from setup_contract_years import rebuild_contract_years, sync_team_cap_space  # noqa: E402
from setup_transactions_cap_ledger import insert_transaction, snapshot_cap_ledger  # noqa: E402

try:
    import jersey_numbers  # noqa: E402
except ImportError:  # pragma: no cover - supports package-style imports.
    from tools import jersey_numbers  # noqa: E402


SOURCE = "roster_cutdown"
INJURY_REPLACEMENT_SOURCE = "cpu_injury_replacement"
PRACTICE_SQUAD_SANITY_SOURCE = "cpu_practice_squad_sanity"
PRACTICE_SQUAD_POACH_SOURCE = "cpu_practice_squad_poach"
PHASE = "Regular Season"
ACTIVE_STATUS = "Active"
PRACTICE_SQUAD_STATUS = "Practice Squad"
FREE_AGENT_STATUS = "Free Agent"
ACTIVE_ROSTER_STATUSES = {"Active", "Questionable", "Doubtful", "Out"}
INJURY_REPLACEMENT_STATUSES = {"IR"}


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

MIN_ACTIVE_BY_POSITION: dict[str, int] = {
    "QB": 2,
    "RB": 3,
    "WR": 5,
    "TE": 2,
    "K": 1,
    "P": 1,
    "LS": 1,
}

MIN_ACTIVE_BY_GROUP: dict[str, int] = {
    "OL": 8,
    "LB": 4,
    "CB": 5,
    "S": 3,
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
    years_exp: int
    is_rookie: int
    is_international_pathway: int
    overall: float
    potential: float
    contract_aav: int
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
    signed_to_ps: int
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
    if row and row["setting_value"]:
        return str(row["setting_value"])
    if table_exists(con, "active_game_save_view"):
        row = con.execute('SELECT "current_date" FROM active_game_save_view LIMIT 1').fetchone()
        if row and row["current_date"]:
            return str(row["current_date"])
    if table_exists(con, "game_saves"):
        row = con.execute('SELECT "current_date" FROM game_saves ORDER BY updated_at DESC LIMIT 1').fetchone()
        if row and row["current_date"]:
            return str(row["current_date"])
    return f"{current_season(con)}-06-01"


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
            ('Practice Squad Poaching', 'Roster', 'Player signed from another team practice squad to the active roster.'),
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


def active_depth_slots_for_team(con: sqlite3.Connection, team_id: int, season: int) -> set[str] | None:
    if not table_exists(con, "team_scheme_identities_view"):
        return None
    row = con.execute(
        "SELECT * FROM team_scheme_identities_view WHERE team_id = ? AND season = ?",
        (team_id, season),
    ).fetchone()
    if not row:
        return None
    info = depth_packages.team_package_profile_from_db(
        con,
        team_id,
        season,
        row,
        team_abbr=str(row["team"] or ""),
    )
    active_slots = set(
        depth_packages.active_depth_slots(
            list(info.get("offensePackages") or ["11", "12"]),
            list(info.get("defensePackages") or ["nickel"]),
            include_special=True,
        )
    )
    active_slots.update(depth_packages.legacy_fallback_slots(active_slots))
    return active_slots


def depth_rank(con: sqlite3.Connection, team_id: int, player_id: int, season: int | None = None) -> int | None:
    if not table_exists(con, "depth_charts"):
        return None
    active_slots = active_depth_slots_for_team(con, team_id, season) if season is not None else None
    slot_filter = ""
    params: list[object] = [team_id, player_id]
    if active_slots:
        slot_filter = f" AND position IN ({','.join('?' for _ in active_slots)})"
        params.extend(sorted(active_slots))
    row = con.execute(
        f"""
        SELECT MIN(depth_rank) AS depth_rank
        FROM depth_charts
        WHERE team_id = ?
          AND player_id = ?
          {slot_filter}
        """,
        params,
    ).fetchone()
    return int(row["depth_rank"]) if row and row["depth_rank"] is not None else None


def active_contract_aav(con: sqlite3.Connection, player_id: int) -> int:
    row = con.execute(
        """
        SELECT COALESCE(aav, 0) AS aav
        FROM contracts
        WHERE player_id = ?
          AND COALESCE(is_active, 1) = 1
        ORDER BY contract_id DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    return int(row["aav"] or 0) if row else 0


def player_candidate(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    season: int,
    team_id: int | None,
) -> PlayerCandidate:
    position = (row["position"] or "").upper()
    group = POSITION_TO_GROUP.get(position, "OTHER")
    overall = float(row["overall"] or 55)
    potential = float(row["potential"] or overall)
    if team_id is not None:
        overall, potential, _read = pro_player_fog.perceived_overall_potential(
            con,
            game_id=pro_player_fog.active_game_id(con),
            season=season,
            evaluator_team_id=team_id,
            player_id=int(row["player_id"]),
            true_overall=overall,
            true_potential=potential,
            create_missing=True,
        )
    contract_aav_value = active_contract_aav(con, int(row["player_id"]))
    role = best_role_score(con, int(row["player_id"]), season)
    avg_rating = rating_average(con, int(row["player_id"]), season)
    if position in SPECIALIST_POSITIONS:
        base = overall
    else:
        fallback = (overall * 0.7) + (potential * 0.2) + ((avg_rating or overall) * 0.1)
        base = max(float(role) if role is not None else 0.0, fallback)
    rank = depth_rank(con, team_id, int(row["player_id"]), season) if team_id is not None else None
    depth_bonus = {1: 22.0, 2: 12.0, 3: 6.0}.get(rank or 0, 0.0)
    youth_bonus = 0.0
    age = int(row["age"] or 26)
    years_exp = int(row["years_exp"] or 0)
    is_rookie = int(row["is_rookie"] or 0)
    try:
        is_international_pathway = int(row["is_international_pathway"] or 0)
    except (IndexError, KeyError):
        is_international_pathway = 0
    if is_rookie:
        youth_bonus += 3.0
    if age <= 24:
        youth_bonus += 2.0
    elif age >= 33 and position not in SPECIALIST_POSITIONS:
        youth_bonus -= 2.0
    upside_bonus = max(0.0, potential - overall) * 0.18
    keep_score = base + depth_bonus + youth_bonus + upside_bonus
    ps_score = (potential * 0.48) + (base * 0.32) + youth_bonus + (8.0 if is_rookie else 0.0)
    if years_exp >= 3 and age <= 31 and 60 <= overall <= 69 and position not in SPECIALIST_POSITIONS:
        ps_score += 5.0
    if age > 28 and not is_rookie:
        ps_score -= min(10.0, float(age - 28) * 1.5)
    return PlayerCandidate(
        player_id=int(row["player_id"]),
        name=f"{row['first_name']} {row['last_name']}",
        position=position,
        group=group,
        age=age,
        years_exp=years_exp,
        is_rookie=is_rookie,
        is_international_pathway=is_international_pathway,
        overall=overall,
        potential=potential,
        contract_aav=contract_aav_value,
        role_score=base,
        depth_rank=rank,
        keep_score=keep_score,
        ps_score=ps_score,
    )


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
    return [player_candidate(con, row, season=season, team_id=team_id) for row in rows]


def choose_active_roster(candidates: list[PlayerCandidate], active_limit: int) -> set[int]:
    selected: set[int] = {
        candidate.player_id
        for candidate in candidates
        if is_cutdown_release_protected(candidate)
    }
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
                is_cutdown_release_protected(item),
                item.keep_score,
            ),
        )
        for candidate in removable:
            if len(selected) <= active_limit:
                break
            if candidate.position in SPECIALIST_POSITIONS or candidate.depth_rank == 1:
                continue
            if is_cutdown_release_protected(candidate):
                continue
            selected.remove(candidate.player_id)

    return selected


def is_cutdown_release_protected(candidate: PlayerCandidate) -> bool:
    """Protect starter-caliber or high-upside players from automatic cutdown releases."""
    if candidate.is_international_pathway:
        return False
    if candidate.age <= 24 and candidate.potential >= 76 and candidate.overall >= 60:
        return True
    if candidate.age <= 25 and candidate.potential >= 80:
        return True
    if candidate.years_exp <= 2 and candidate.potential >= 78 and candidate.overall >= 62:
        return True
    if candidate.contract_aav >= 4_000_000 and candidate.overall >= 62:
        return True
    if candidate.position in SPECIALIST_POSITIONS:
        return candidate.overall >= 72
    if candidate.overall >= 72 and candidate.age <= 31:
        return True
    if candidate.position == "QB" and candidate.overall >= 68 and candidate.age <= 32:
        return True
    if candidate.overall >= 68 and candidate.potential >= 80 and candidate.age <= 26:
        return True
    if candidate.depth_rank is not None and candidate.depth_rank <= 2 and candidate.overall >= 68:
        return True
    return False


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
        and is_practice_squad_stash_candidate(candidate)
    ]
    ranked = sorted(
        available,
        key=lambda item: (item.ps_score, item.potential, item.keep_score, -item.age),
        reverse=True,
    )
    selected: set[int] = set()
    developmental = 0
    veterans = 0
    ipp = 0
    dev_limit = min(practice_squad_limit, roster_rules.PRACTICE_SQUAD_DEVELOPMENTAL_LIMIT)
    vet_limit = roster_rules.PRACTICE_SQUAD_VETERAN_EXCEPTION_LIMIT
    for candidate in ranked:
        bucket, _reason = roster_rules.practice_squad_bucket(
            {
                "years_exp": candidate.years_exp,
                "is_rookie": candidate.is_rookie,
                "is_international_pathway": candidate.is_international_pathway,
            }
        )
        if bucket == "international_exemption" and ipp < 1 and len(selected) >= practice_squad_limit:
            selected.add(candidate.player_id)
            ipp += 1
            continue
        if len(selected) >= practice_squad_limit:
            break
        if bucket == "veteran_exception":
            if veterans >= vet_limit:
                continue
            veterans += 1
        else:
            if developmental >= dev_limit:
                continue
            developmental += 1
        selected.add(candidate.player_id)
    return selected


def is_practice_squad_stash_candidate(candidate: PlayerCandidate) -> bool:
    """Keep established active-roster caliber players out of cutdown stashes."""
    if candidate.position in SPECIALIST_POSITIONS:
        return False
    if candidate.is_international_pathway:
        return True
    if candidate.contract_aav >= 2_500_000 and candidate.years_exp >= 3:
        return False
    if is_cutdown_release_protected(candidate):
        return False
    if candidate.overall >= 70:
        return False
    if candidate.position == "QB" and candidate.overall >= 67:
        return False
    if candidate.years_exp >= 3 and candidate.overall >= 68:
        return False
    if candidate.age >= 30 and candidate.overall >= 65:
        return False
    if candidate.depth_rank is not None and candidate.depth_rank <= 3 and candidate.overall >= 65:
        return False
    if candidate.potential >= 78 and candidate.overall >= 67 and not candidate.is_rookie:
        return False
    return True


def is_practice_squad_swap_demote_candidate(candidate: PlayerCandidate, promoted: PlayerCandidate) -> bool:
    if candidate.position in SPECIALIST_POSITIONS or candidate.is_international_pathway:
        return False
    if candidate.depth_rank == 1:
        return False
    if is_cutdown_release_protected(candidate):
        return False
    if candidate.overall >= 70:
        return False
    if candidate.potential >= 84 and candidate.age <= 25:
        return False
    if candidate.overall > promoted.overall - 2 and candidate.potential >= promoted.potential:
        return False
    return True


def free_agent_practice_squad_candidates(
    con: sqlite3.Connection,
    *,
    season: int,
    exclude_ids: set[int],
    limit: int,
) -> list[PlayerCandidate]:
    if limit <= 0:
        return []
    rows = con.execute(
        """
        SELECT p.*
        FROM players p
        WHERE COALESCE(p.status, 'Free Agent') = 'Free Agent'
          AND p.team_id IS NULL
          AND COALESCE(p.status, 'Free Agent') != 'Retired'
        ORDER BY p.position, p.last_name, p.first_name
        """
    ).fetchall()
    candidates: list[PlayerCandidate] = []
    for row in rows:
        if int(row["player_id"]) in exclude_ids or (row["position"] or "").upper() in SPECIALIST_POSITIONS:
            continue
        candidate = player_candidate(con, row, season=season, team_id=None)
        if not is_practice_squad_stash_candidate(candidate):
            continue
        if candidate.overall >= 68:
            continue
        if candidate.years_exp >= 3 and candidate.age <= 31 and 60 <= candidate.overall <= 67:
            candidates.append(candidate)
            continue
        if candidate.age <= 26 and candidate.potential >= candidate.overall + 4:
            candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda item: (item.ps_score, item.potential, item.keep_score, -item.age),
        reverse=True,
    )[:limit]


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
    source: str = SOURCE,
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
        source=source,
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


def move_to_practice_squad(con: sqlite3.Connection, player_id: int, season: int, notes: str) -> bool:
    player = con.execute("SELECT * FROM players WHERE player_id = ?", (player_id,)).fetchone()
    if not player:
        return False
    old_status = player["status"] or ACTIVE_STATUS
    team_id = int(player["team_id"])
    team = con.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    rule_set = roster_rules.get_rule_set(con, season, PHASE)
    eligibility = roster_rules.practice_squad_eligibility(con, player, team, rule_set, season=season)
    if not eligibility["eligible"]:
        return False
    con.execute(
        "UPDATE players SET status = ? WHERE player_id = ?",
        (PRACTICE_SQUAD_STATUS, player_id),
    )
    delete_depth_rows(con, player_id)
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=team_id,
        reason="Player moved from active roster to practice squad.",
    )
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
    return True


def sign_free_agent_to_practice_squad(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int,
    season: int,
    notes: str,
) -> bool:
    player = con.execute("SELECT * FROM players WHERE player_id = ?", (player_id,)).fetchone()
    if not player:
        return False
    team = con.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    rule_set = roster_rules.get_rule_set(con, season, PHASE)
    eligibility = roster_rules.practice_squad_eligibility(con, player, team, rule_set, season=season)
    if not eligibility["eligible"]:
        return False
    old_status = player["status"] or FREE_AGENT_STATUS
    from_team_id = int(player["team_id"]) if player["team_id"] is not None else None
    con.execute(
        "UPDATE players SET team_id = ?, status = ? WHERE player_id = ?",
        (team_id, PRACTICE_SQUAD_STATUS, player_id),
    )
    jersey_numbers.assign_player_number(
        con,
        int(player_id),
        team_id=int(team_id),
        source="practice_squad_signing",
    )
    delete_depth_rows(con, player_id)
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=team_id,
        reason="Practice-squad signing changed roster composition.",
    )
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
        from_team_id=from_team_id,
        to_team_id=team_id,
        old_status=old_status,
        new_status=PRACTICE_SQUAD_STATUS,
        season=season,
        description=f"Auto-cutdown signed {player['first_name']} {player['last_name']} to the practice squad.",
        contract_id=active_contract_id(con, player_id),
    )
    return True


def fill_practice_squad_from_free_agents(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    practice_squad_limit: int,
    notes: str,
    exclude_ids: set[int],
) -> int:
    current = count_row(con, team_id)
    open_slots = practice_squad_limit - int(current["practice_squad_count"] or 0)
    if open_slots <= 0:
        return 0
    signed = 0
    for candidate in free_agent_practice_squad_candidates(
        con,
        season=season,
        exclude_ids=exclude_ids,
        limit=open_slots,
    ):
        if not sign_free_agent_to_practice_squad(
            con,
            player_id=candidate.player_id,
            team_id=team_id,
            season=season,
            notes=notes,
        ):
            continue
        exclude_ids.add(candidate.player_id)
        signed += 1
    return signed


def release_player(
    con: sqlite3.Connection,
    player_id: int,
    season: int,
    notes: str,
    *,
    source: str = SOURCE,
) -> None:
    player = con.execute("SELECT * FROM players WHERE player_id = ?", (player_id,)).fetchone()
    if not player:
        return
    old_status = player["status"] or ACTIVE_STATUS
    from_team_id = int(player["team_id"]) if player["team_id"] is not None else None
    contract_id = active_contract_id(con, player_id)
    if (
        from_team_id is not None
        and old_status != PRACTICE_SQUAD_STATUS
        and roster_rules.waiver_required_for_player(con, player, season=season, waiver_date=current_date(con))
    ):
        roster_rules.place_player_on_waivers(
            con,
            player=player,
            season=season,
            waiver_date=current_date(con),
            reason=notes,
            source=source,
        )
        cpu_depth_chart.mark_depth_chart_stale(
            con,
            team_id=from_team_id,
            reason="Player waiver changed roster composition.",
        )
        return
    if contract_id:
        con.execute("UPDATE contracts SET is_active = 0 WHERE contract_id = ?", (contract_id,))
        if table_exists(con, "contract_years"):
            con.execute("UPDATE contract_years SET is_active = 0 WHERE contract_id = ?", (contract_id,))
    con.execute(
        "UPDATE players SET team_id = NULL, status = ? WHERE player_id = ?",
        (FREE_AGENT_STATUS, player_id),
    )
    delete_depth_rows(con, player_id)
    if from_team_id is not None:
        cpu_depth_chart.mark_depth_chart_stale(
            con,
            team_id=from_team_id,
            reason="Player release changed roster composition.",
        )
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=FREE_AGENT_STATUS,
        season=season,
        reason=notes,
    )
    if source == PRACTICE_SQUAD_SANITY_SOURCE:
        if old_status == PRACTICE_SQUAD_STATUS:
            description = (
                f"{player['first_name']} {player['last_name']} was released from the practice squad "
                "after roster sanity review."
            )
        else:
            description = (
                f"{player['first_name']} {player['last_name']} was released from the active roster "
                "after roster sanity review."
            )
    else:
        description = f"Auto-cutdown released {player['first_name']} {player['last_name']} to free agency."
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
        description=description,
        contract_id=contract_id,
        source=source,
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
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
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
    jersey_numbers.assign_player_number(
        con,
        int(player["player_id"]),
        team_id=int(team["team_id"]),
        source="missing_specialist_signing",
    )
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=int(team["team_id"]),
        reason=f"Missing {position} specialist signing changed roster composition.",
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
        jersey_numbers.assign_player_number(
            con,
            int(player["player_id"]),
            team_id=int(team["team_id"]),
            source="status_normalized_active",
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


def active_roster_count(con: sqlite3.Connection, team_id: int) -> int:
    return int(count_row(con, team_id)["active_roster_count"] or 0)


def active_roster_status_clause(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"{prefix}status IN ('Active', 'Questionable', 'Doubtful', 'Out')"


def status_group_counts(con: sqlite3.Connection, team_id: int, statuses: set[str]) -> dict[str, int]:
    if not statuses:
        return {}
    placeholders = ",".join("?" for _ in statuses)
    rows = con.execute(
        f"""
        SELECT position, COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND status IN ({placeholders})
        GROUP BY position
        """,
        (team_id, *sorted(statuses)),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        group = POSITION_TO_GROUP.get(str(row["position"] or "").upper(), "OTHER")
        counts[group] = counts.get(group, 0) + int(row["count"] or 0)
    return counts


def replacement_group_order(con: sqlite3.Connection, team_id: int) -> list[str]:
    active_counts = status_group_counts(con, team_id, ACTIVE_ROSTER_STATUSES)
    injury_counts = status_group_counts(con, team_id, INJURY_REPLACEMENT_STATUSES)
    groups = list(DEFAULT_ACTIVE_TARGETS)
    scored: list[tuple[float, str]] = []
    for group in groups:
        target = DEFAULT_ACTIVE_TARGETS[group]
        active_deficit = max(0, target - active_counts.get(group, 0))
        injury_need = injury_counts.get(group, 0)
        score = (active_deficit * 100.0) + (injury_need * 35.0)
        if group in {"K", "P", "LS"} and active_deficit:
            score += 200.0
        scored.append((score, group))
    ordered = [group for score, group in sorted(scored, reverse=True) if score > 0]
    ordered.extend(group for group in groups if group not in ordered)
    return ordered


def best_practice_squad_promotion(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    groups: list[str],
) -> sqlite3.Row | None:
    rows = con.execute(
        """
        SELECT *
        FROM players
        WHERE team_id = ?
          AND status = 'Practice Squad'
        """,
        (team_id,),
    ).fetchall()
    by_group: dict[str, list[PlayerCandidate]] = {group: [] for group in groups}
    row_by_id = {int(row["player_id"]): row for row in rows}
    for row in rows:
        candidate = player_candidate(con, row, season=season, team_id=team_id)
        by_group.setdefault(candidate.group, []).append(candidate)
    for group in groups:
        ranked = sorted(
            by_group.get(group, []),
            key=lambda item: (item.keep_score, item.overall, item.potential, -item.age),
            reverse=True,
        )
        if ranked:
            return row_by_id[ranked[0].player_id]
    return None


def minimum_contract_aav(player: sqlite3.Row) -> int:
    overall = int(player["overall"] or 60)
    age = int(player["age"] or 26)
    floor = 1_050_000 if age >= 27 else 915_000
    return max(floor, min(2_500_000, int((overall * 18_000) // 10_000 * 10_000)))


def best_free_agent_replacement(
    con: sqlite3.Connection,
    *,
    season: int,
    groups: list[str],
) -> sqlite3.Row | None:
    rows = con.execute(
        """
        SELECT *
        FROM players
        WHERE team_id IS NULL
          AND status = 'Free Agent'
          AND COALESCE(status, 'Free Agent') != 'Retired'
        """
    ).fetchall()
    by_group: dict[str, list[PlayerCandidate]] = {group: [] for group in groups}
    row_by_id = {int(row["player_id"]): row for row in rows}
    for row in rows:
        candidate = player_candidate(con, row, season=season, team_id=None)
        by_group.setdefault(candidate.group, []).append(candidate)
    for group in groups:
        ranked = sorted(
            by_group.get(group, []),
            key=lambda item: (item.overall, item.keep_score, item.potential, -item.age),
            reverse=True,
        )
        if ranked:
            return row_by_id[ranked[0].player_id]
    return None


def has_protected_young_depth(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    group: str,
) -> bool:
    rows = con.execute(
        """
        SELECT *
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out', 'Practice Squad')
        """,
        (team_id,),
    ).fetchall()
    for row in rows:
        candidate = player_candidate(con, row, season=season, team_id=team_id)
        if candidate.group != group:
            continue
        if candidate.age <= 24 and candidate.potential >= 78 and candidate.overall >= 58:
            return True
        if candidate.years_exp <= 2 and candidate.potential >= 80 and candidate.overall >= 60:
            return True
        if candidate.depth_rank is not None and candidate.depth_rank <= 3 and candidate.age <= 25 and candidate.potential >= 76:
            return True
    return False


def practice_squad_poach_score(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    target_team_id: int,
    season: int,
    group_need: float,
) -> float:
    candidate = player_candidate(con, player, season=season, team_id=target_team_id)
    target_current = active_group_replacement_level(con, target_team_id, candidate.group, season)
    improvement = candidate.overall - target_current
    youth_value = max(0.0, min(8.0, candidate.potential - candidate.overall)) * 0.8
    return (improvement * 7.0) + group_need + youth_value + (2.0 if candidate.age <= 24 else 0.0)


def active_group_replacement_level(con: sqlite3.Connection, team_id: int, group: str, season: int) -> float:
    positions = POSITION_GROUPS.get(group, ())
    if not positions:
        return 50.0
    placeholders = ",".join("?" for _ in positions)
    rows = con.execute(
        f"""
        SELECT p.*
        FROM players p
        WHERE p.team_id = ?
          AND p.status IN ('Active', 'Questionable', 'Doubtful', 'Out')
          AND p.position IN ({placeholders})
        """,
        (team_id, *positions),
    ).fetchall()
    if not rows:
        return 45.0
    ranked = sorted(
        (player_candidate(con, row, season=season, team_id=team_id) for row in rows),
        key=lambda item: (item.overall, item.keep_score),
    )
    return float(ranked[0].overall)


def external_practice_squad_candidates(
    con: sqlite3.Connection,
    *,
    target_team_id: int,
    season: int,
    groups: list[str],
    max_per_group: int = 8,
) -> list[sqlite3.Row]:
    rows: list[tuple[float, sqlite3.Row]] = []
    target_counts = {group: active_group_count(con, target_team_id, group) for group in groups}
    for group in groups:
        positions = POSITION_GROUPS.get(group, ())
        if not positions:
            continue
        placeholders = ",".join("?" for _ in positions)
        group_need = max(0, DEFAULT_ACTIVE_TARGETS.get(group, 0) - target_counts.get(group, 0)) * 18.0
        group_rows = con.execute(
            f"""
            SELECT p.*, t.abbreviation AS source_team
            FROM players p
            JOIN teams t ON t.team_id = p.team_id
            WHERE p.team_id IS NOT NULL
              AND p.team_id != ?
              AND p.status = 'Practice Squad'
              AND p.position IN ({placeholders})
              AND (
                    COALESCE(p.overall, 50) >= 60
                 OR COALESCE(p.potential, COALESCE(p.overall, 50)) >= 74
              )
            ORDER BY COALESCE(p.overall, 50) DESC, COALESCE(p.potential, p.overall, 50) DESC, p.age ASC
            LIMIT ?
            """,
            (target_team_id, *positions, max_per_group),
        ).fetchall()
        for row in group_rows:
            candidate = player_candidate(con, row, season=season, team_id=target_team_id)
            if candidate.position in SPECIALIST_POSITIONS:
                continue
            if candidate.overall < 58 and candidate.potential < 72:
                continue
            score = practice_squad_poach_score(
                con,
                player=row,
                target_team_id=target_team_id,
                season=season,
                group_need=group_need,
            )
            rows.append((score, row))
    return [row for score, row in sorted(rows, key=lambda item: item[0], reverse=True)]


def sign_practice_squad_poach(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    team: sqlite3.Row,
    season: int,
    reason: str,
) -> bool:
    old_team_id = int(player["team_id"]) if player["team_id"] is not None else None
    if old_team_id is None or old_team_id == int(team["team_id"]):
        return False
    old_status = player["status"] or PRACTICE_SQUAD_STATUS
    contract_id = roster_rules.transfer_active_contract(con, int(player["player_id"]), int(team["team_id"]))
    if contract_id is None:
        aav = minimum_contract_aav(player)
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
                aav,
                aav,
            ),
        )
        contract_id = int(cur.lastrowid)
    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (team["team_id"], player["player_id"]),
    )
    jersey_numbers.assign_player_number(
        con,
        int(player["player_id"]),
        team_id=int(team["team_id"]),
        source="practice_squad_poach",
    )
    delete_depth_rows(con, int(player["player_id"]))
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=old_team_id,
        reason="Practice-squad player was poached by another team.",
    )
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=int(team["team_id"]),
        reason="Practice-squad poach changed roster composition.",
    )
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=ACTIVE_STATUS,
        season=season,
        reason=reason,
    )
    roster_rules.record_practice_squad_move(
        con,
        player_id=int(player["player_id"]),
        team_id=int(team["team_id"]),
        season=season,
        move_type="Poach",
        from_status=old_status,
        to_status=ACTIVE_STATUS,
        notes=reason,
    )
    log_roster_transaction(
        con,
        transaction_type="Practice Squad Poaching",
        player=player,
        team_id=int(team["team_id"]),
        from_team_id=old_team_id,
        to_team_id=int(team["team_id"]),
        old_status=old_status,
        new_status=ACTIVE_STATUS,
        season=season,
        description=(
            f"{team['abbreviation']} signed {player['first_name']} {player['last_name']} "
            "from another team's practice squad to the active roster."
        ),
        contract_id=contract_id,
        source=PRACTICE_SQUAD_POACH_SOURCE,
    )
    return True


def best_external_practice_squad_replacement(
    con: sqlite3.Connection,
    *,
    target_team_id: int,
    season: int,
    groups: list[str],
) -> sqlite3.Row | None:
    for row in external_practice_squad_candidates(
        con,
        target_team_id=target_team_id,
        season=season,
        groups=groups,
    ):
        candidate = player_candidate(con, row, season=season, team_id=int(row["team_id"]))
        if active_group_count(con, target_team_id, candidate.group) >= DEFAULT_ACTIVE_TARGETS.get(candidate.group, 99):
            if has_protected_young_depth(con, team_id=target_team_id, season=season, group=candidate.group):
                continue
        return row
    return None


def best_practice_squad_position_promotion(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    position: str,
) -> sqlite3.Row | None:
    rows = con.execute(
        """
        SELECT *
        FROM players
        WHERE team_id = ?
          AND status = 'Practice Squad'
          AND position = ?
        """,
        (team_id, position),
    ).fetchall()
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda row: (
            player_candidate(con, row, season=season, team_id=team_id).keep_score,
            int(row["overall"] or 0),
            int(row["potential"] or row["overall"] or 0),
            -int(row["age"] or 26),
        ),
        reverse=True,
    )
    return ranked[0]


def best_free_agent_position_replacement(
    con: sqlite3.Connection,
    *,
    position: str,
) -> sqlite3.Row | None:
    return con.execute(
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


def active_group_count(con: sqlite3.Connection, team_id: int, group: str) -> int:
    positions = POSITION_GROUPS.get(group, ())
    if not positions:
        return 0
    placeholders = ",".join("?" for _ in positions)
    row = con.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
          AND position IN ({placeholders})
        """,
        (team_id, *positions),
    ).fetchone()
    return int(row["count"] or 0)


def best_practice_squad_group_promotion(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    group: str,
) -> sqlite3.Row | None:
    positions = POSITION_GROUPS.get(group, ())
    if not positions:
        return None
    placeholders = ",".join("?" for _ in positions)
    rows = con.execute(
        f"""
        SELECT *
        FROM players
        WHERE team_id = ?
          AND status = 'Practice Squad'
          AND position IN ({placeholders})
        """,
        (team_id, *positions),
    ).fetchall()
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda row: (
            player_candidate(con, row, season=season, team_id=team_id).keep_score,
            int(row["overall"] or 0),
            int(row["potential"] or row["overall"] or 0),
            -int(row["age"] or 26),
        ),
        reverse=True,
    )
    return ranked[0]


def best_free_agent_group_replacement(con: sqlite3.Connection, *, group: str) -> sqlite3.Row | None:
    positions = POSITION_GROUPS.get(group, ())
    if not positions:
        return None
    placeholders = ",".join("?" for _ in positions)
    return con.execute(
        f"""
        SELECT *
        FROM players
        WHERE team_id IS NULL
          AND status = 'Free Agent'
          AND position IN ({placeholders})
        ORDER BY COALESCE(overall, 50) DESC, COALESCE(potential, overall, 50) DESC, age ASC
        LIMIT 1
        """,
        positions,
    ).fetchone()


def promote_practice_squad_replacement(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    team: sqlite3.Row,
    season: int,
    reason: str,
    source: str = INJURY_REPLACEMENT_SOURCE,
) -> None:
    old_status = player["status"] or PRACTICE_SQUAD_STATUS
    con.execute("UPDATE players SET status = 'Active' WHERE player_id = ?", (player["player_id"],))
    jersey_numbers.assign_player_number(
        con,
        int(player["player_id"]),
        team_id=int(team["team_id"]),
        source="practice_squad_promotion",
    )
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=int(team["team_id"]),
        reason="Practice-squad promotion changed active roster composition.",
    )
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=ACTIVE_STATUS,
        season=season,
        reason=reason,
    )
    roster_rules.record_practice_squad_move(
        con,
        player_id=int(player["player_id"]),
        team_id=int(team["team_id"]),
        season=season,
        move_type="Promote",
        from_status=old_status,
        to_status=ACTIVE_STATUS,
        notes=reason,
    )
    if source == PRACTICE_SQUAD_SANITY_SOURCE:
        description = (
            f"{team['abbreviation']} promoted {player['first_name']} {player['last_name']} "
            "from the practice squad after roster sanity review."
        )
    else:
        description = (
            f"{team['abbreviation']} promoted {player['first_name']} {player['last_name']} "
            "from the practice squad as injury replacement depth."
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
        description=description,
        contract_id=active_contract_id(con, int(player["player_id"])),
        source=source,
    )


def move_active_to_practice_squad_sanity(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    team: sqlite3.Row,
    season: int,
    reason: str,
) -> bool:
    rule_set = roster_rules.get_rule_set(con, season, PHASE)
    eligibility = roster_rules.practice_squad_eligibility(con, player, team, rule_set, season=season)
    if not eligibility["eligible"]:
        return False
    old_status = player["status"] or ACTIVE_STATUS
    con.execute("UPDATE players SET status = ? WHERE player_id = ?", (PRACTICE_SQUAD_STATUS, player["player_id"]))
    delete_depth_rows(con, int(player["player_id"]))
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=int(team["team_id"]),
        reason="Roster sanity move changed active roster composition.",
    )
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=PRACTICE_SQUAD_STATUS,
        season=season,
        reason=reason,
    )
    roster_rules.record_practice_squad_move(
        con,
        player_id=int(player["player_id"]),
        team_id=int(team["team_id"]),
        season=season,
        move_type="Sign",
        from_status=old_status,
        to_status=PRACTICE_SQUAD_STATUS,
        notes=reason,
    )
    log_roster_transaction(
        con,
        transaction_type="Practice Squad Signing",
        player=player,
        team_id=int(team["team_id"]),
        from_team_id=int(team["team_id"]),
        to_team_id=int(team["team_id"]),
        old_status=old_status,
        new_status=PRACTICE_SQUAD_STATUS,
        season=season,
        description=(
            f"{team['abbreviation']} moved {player['first_name']} {player['last_name']} "
            "to the practice squad after roster sanity review."
        ),
        contract_id=active_contract_id(con, int(player["player_id"])),
        source=PRACTICE_SQUAD_SANITY_SOURCE,
    )
    return True


def practice_squad_sanity_swap_candidate(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    group: str,
    promoted_candidate: PlayerCandidate,
) -> sqlite3.Row | None:
    rows = con.execute(
        """
        SELECT *
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
        """,
        (team_id,),
    ).fetchall()
    options: list[PlayerCandidate] = []
    row_by_id = {int(row["player_id"]): row for row in rows}
    for row in rows:
        candidate = player_candidate(con, row, season=season, team_id=team_id)
        if candidate.group != group:
            continue
        if not is_practice_squad_swap_demote_candidate(candidate, promoted_candidate):
            continue
        team = con.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
        rule_set = roster_rules.get_rule_set(con, season, PHASE)
        if not team or not roster_rules.practice_squad_eligibility(con, row, team, rule_set, season=season)["eligible"]:
            continue
        options.append(candidate)
    if not options:
        return None
    selected = sorted(
        options,
        key=lambda item: (item.keep_score, item.overall, item.potential, -item.age),
    )[0]
    return row_by_id[selected.player_id]


def sanitize_cpu_practice_squads(
    con: sqlite3.Connection,
    *,
    season: int,
    game_id: str | None = None,
    include_user_team: bool = False,
) -> dict[str, int]:
    ensure_cutdown_schema(con)
    user_team_id = active_user_team_id(con, game_id)
    rows = con.execute(
        """
        SELECT p.*, t.abbreviation AS team_abbreviation
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        WHERE p.status = 'Practice Squad'
        ORDER BY t.abbreviation, COALESCE(p.overall, 50) DESC, COALESCE(p.potential, p.overall, 50) DESC
        """
    ).fetchall()
    promoted = 0
    swapped = 0
    released = 0
    left = 0
    teams_touched: set[int] = set()
    for player in rows:
        team_id = int(player["team_id"])
        if not include_user_team and user_team_id is not None and team_id == user_team_id:
            continue
        team = con.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
        if not team:
            continue
        candidate = player_candidate(con, player, season=season, team_id=team_id)
        if is_practice_squad_stash_candidate(candidate):
            continue
        reason = "CPU practice squad sanity: active-roster caliber player should not be stashed."
        if active_roster_count(con, team_id) < 53:
            promote_practice_squad_replacement(
                con,
                player=player,
                team=team,
                season=season,
                reason=reason,
                source=PRACTICE_SQUAD_SANITY_SOURCE,
            )
            promoted += 1
            teams_touched.add(team_id)
            continue
        swap = practice_squad_sanity_swap_candidate(
            con,
            team_id=team_id,
            season=season,
            group=candidate.group,
            promoted_candidate=candidate,
        )
        if swap:
            promote_practice_squad_replacement(
                con,
                player=player,
                team=team,
                season=season,
                reason=reason,
                source=PRACTICE_SQUAD_SANITY_SOURCE,
            )
            if not move_active_to_practice_squad_sanity(con, player=swap, team=team, season=season, reason=reason):
                left += 1
                continue
            swapped += 1
            teams_touched.add(team_id)
        else:
            if candidate.age <= 25 or candidate.potential >= 74 or candidate.years_exp <= 2:
                left += 1
                continue
            release_player(
                con,
                int(player["player_id"]),
                season,
                reason,
                source=PRACTICE_SQUAD_SANITY_SOURCE,
            )
            fill_practice_squad_from_free_agents(
                con,
                team_id=team_id,
                season=season,
                practice_squad_limit=roster_rules.get_rule_set(con, season, PHASE)["practice_squad_limit"],
                notes=reason,
                exclude_ids={int(player["player_id"])},
            )
            released += 1
            teams_touched.add(team_id)
    return {"teams": len(teams_touched), "promoted": promoted, "swapped": swapped, "released": released, "left": left}


def trim_cpu_active_roster_overages(
    con: sqlite3.Connection,
    *,
    season: int,
    game_id: str | None = None,
    active_limit: int = 53,
    include_user_team: bool = False,
    team_id: int | None = None,
) -> dict[str, int]:
    ensure_cutdown_schema(con)
    user_team_id = active_user_team_id(con, game_id)
    if team_id is not None:
        teams = con.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchall()
    else:
        teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    teams_touched = 0
    moved_to_ps = 0
    released = 0
    for team in teams:
        team_id = int(team["team_id"])
        if not include_user_team and user_team_id is not None and team_id == user_team_id:
            continue
        protected_poaches = same_day_practice_squad_poach_ids(con, team_id)
        touched = False
        while active_roster_count(con, team_id) > active_limit:
            candidates = []
            emergency_candidates = []
            for row in con.execute(
                """
                SELECT *
                FROM players
                WHERE team_id = ?
                  AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
                """,
                (team_id,),
            ):
                if int(row["player_id"]) in protected_poaches:
                    continue
                candidate = player_candidate(con, row, season=season, team_id=team_id)
                if candidate.position in SPECIALIST_POSITIONS:
                    continue
                position_floor = MIN_ACTIVE_BY_POSITION.get(candidate.position)
                if position_floor is not None and active_position_count(con, team_id, candidate.position) <= position_floor:
                    continue
                group_floor = MIN_ACTIVE_BY_GROUP.get(candidate.group)
                if group_floor is not None and active_group_count(con, team_id, candidate.group) <= group_floor:
                    continue
                if candidate.depth_rank != 1:
                    emergency_candidates.append((candidate, row))
                    if not is_cutdown_release_protected(candidate):
                        candidates.append((candidate, row))
            if not candidates and emergency_candidates:
                candidates = emergency_candidates
            if not candidates:
                break
            candidate, player = sorted(candidates, key=lambda item: (item[0].keep_score, item[0].overall, item[0].potential))[0]
            reason = "CPU roster sanity: trim active roster back to 53 after injury/practice squad moves."
            current = count_row(con, team_id)
            ps_limit = int(roster_rules.get_rule_set(con, season, PHASE)["practice_squad_limit"])
            if (
                int(current["practice_squad_count"] or 0) < ps_limit
                and is_practice_squad_stash_candidate(candidate)
                and move_active_to_practice_squad_sanity(con, player=player, team=team, season=season, reason=reason)
            ):
                moved_to_ps += 1
            else:
                release_player(
                    con,
                    int(player["player_id"]),
                    season,
                    reason,
                    source=PRACTICE_SQUAD_SANITY_SOURCE,
                )
                released += 1
            touched = True
        if touched:
            teams_touched += 1
    return {"teams": teams_touched, "moved_to_ps": moved_to_ps, "released": released}


def same_day_practice_squad_poach_ids(con: sqlite3.Connection, team_id: int) -> set[int]:
    if not table_exists(con, "transaction_log"):
        return set()
    rows = con.execute(
        """
        SELECT DISTINCT player_id
        FROM transaction_log
        WHERE transaction_date = ?
          AND transaction_type = 'Practice Squad Poaching'
          AND source = ?
          AND player_id IS NOT NULL
          AND (team_id = ? OR to_team_id = ?)
        """,
        (current_date(con), PRACTICE_SQUAD_POACH_SOURCE, int(team_id), int(team_id)),
    ).fetchall()
    return {int(row["player_id"]) for row in rows}


def has_plausible_poach_corresponding_move(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    incoming: PlayerCandidate,
) -> bool:
    if active_roster_count(con, team_id) < 53:
        return True
    options: list[PlayerCandidate] = []
    for row in con.execute(
        """
        SELECT *
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
        """,
        (team_id,),
    ):
        candidate = player_candidate(con, row, season=season, team_id=team_id)
        if candidate.position in SPECIALIST_POSITIONS:
            continue
        position_floor = MIN_ACTIVE_BY_POSITION.get(candidate.position)
        if position_floor is not None and active_position_count(con, team_id, candidate.position) <= position_floor:
            continue
        group_floor = MIN_ACTIVE_BY_GROUP.get(candidate.group)
        if group_floor is not None and active_group_count(con, team_id, candidate.group) <= group_floor:
            continue
        if candidate.depth_rank == 1 or is_cutdown_release_protected(candidate):
            continue
        options.append(candidate)
    if not options:
        return False
    outgoing = sorted(options, key=lambda item: (item.keep_score, item.overall, item.potential))[0]
    return (
        incoming.overall >= outgoing.overall + 2
        or incoming.potential >= outgoing.potential + 5
        or incoming.keep_score >= outgoing.keep_score + 5
    )


def active_position_count(con: sqlite3.Connection, team_id: int, position: str) -> int:
    row = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
          AND position = ?
        """,
        (team_id, position),
    ).fetchone()
    return int(row["count"] or 0)


def process_cpu_position_depth_replacements(
    con: sqlite3.Connection,
    *,
    season: int,
    game_id: str | None = None,
    include_user_team: bool = False,
) -> dict[str, int]:
    ensure_cutdown_schema(con)
    user_team_id = active_user_team_id(con, game_id)
    teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    teams_touched = 0
    promoted = 0
    signed = 0
    for team in teams:
        team_id = int(team["team_id"])
        if not include_user_team and user_team_id is not None and team_id == user_team_id:
            continue
        touched = False
        for position, minimum in MIN_ACTIVE_BY_POSITION.items():
            while active_position_count(con, team_id, position) < minimum:
                reason = f"CPU injury replacement: active {position} depth fell below {minimum}."
                player = best_practice_squad_position_promotion(
                    con,
                    team_id=team_id,
                    season=season,
                    position=position,
                )
                if player:
                    promote_practice_squad_replacement(con, player=player, team=team, season=season, reason=reason)
                    promoted += 1
                    touched = True
                else:
                    player = best_external_practice_squad_replacement(
                        con,
                        target_team_id=team_id,
                        season=season,
                        groups=[POSITION_TO_GROUP.get(position, position)],
                    )
                    if player:
                        if sign_practice_squad_poach(con, player=player, team=team, season=season, reason=reason):
                            signed += 1
                            touched = True
                        else:
                            player = None
                    if not player:
                        player = best_free_agent_position_replacement(con, position=position)
                    if not player:
                        break
                    if player["status"] == FREE_AGENT_STATUS:
                        sign_free_agent_replacement(con, player=player, team=team, season=season, reason=reason)
                        signed += 1
                        touched = True
                trim_cpu_active_roster_overages(
                    con,
                    season=season,
                    game_id=game_id,
                    include_user_team=include_user_team,
                    team_id=team_id,
                )
                if not player:
                    break
        for group, minimum in MIN_ACTIVE_BY_GROUP.items():
            attempts = 0
            while active_group_count(con, team_id, group) < minimum and attempts < minimum:
                before_group_count = active_group_count(con, team_id, group)
                attempts += 1
                reason = f"CPU roster depth replacement: active {group} depth fell below {minimum}."
                player = best_practice_squad_group_promotion(
                    con,
                    team_id=team_id,
                    season=season,
                    group=group,
                )
                if player:
                    promote_practice_squad_replacement(con, player=player, team=team, season=season, reason=reason)
                    promoted += 1
                    touched = True
                else:
                    player = best_external_practice_squad_replacement(
                        con,
                        target_team_id=team_id,
                        season=season,
                        groups=[group],
                    )
                    if player:
                        if sign_practice_squad_poach(con, player=player, team=team, season=season, reason=reason):
                            signed += 1
                            touched = True
                        else:
                            player = None
                    if not player:
                        player = best_free_agent_group_replacement(con, group=group)
                    if not player:
                        break
                    if player["status"] == FREE_AGENT_STATUS:
                        sign_free_agent_replacement(con, player=player, team=team, season=season, reason=reason)
                        signed += 1
                        touched = True
                trim_cpu_active_roster_overages(
                    con,
                    season=season,
                    game_id=game_id,
                    include_user_team=include_user_team,
                    team_id=team_id,
                )
                if active_group_count(con, team_id, group) <= before_group_count:
                    break
        if touched:
            teams_touched += 1
    if promoted or signed:
        sync_team_cap_space(con)
    return {"teams": teams_touched, "promoted": promoted, "signed": signed}


def process_cpu_practice_squad_poaching(
    con: sqlite3.Connection,
    *,
    season: int,
    game_id: str | None = None,
    include_user_team: bool = False,
    max_teams: int = 8,
    max_poaches_per_team: int = 1,
) -> dict[str, int]:
    ensure_cutdown_schema(con)
    user_team_id = active_user_team_id(con, game_id)
    teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    teams_touched = 0
    poached = 0
    skipped_prospect_protection = 0
    for team in teams:
        if teams_touched >= max_teams:
            break
        team_id = int(team["team_id"])
        if not include_user_team and user_team_id is not None and team_id == user_team_id:
            continue
        if active_roster_count(con, team_id) > 53:
            continue
        need_groups = [
            group
            for group in DEFAULT_ACTIVE_TARGETS
            if group not in SPECIALIST_POSITIONS
            and active_group_count(con, team_id, group) < DEFAULT_ACTIVE_TARGETS[group]
        ]
        if not need_groups:
            continue
        team_poaches = 0
        for player in external_practice_squad_candidates(
            con,
            target_team_id=team_id,
            season=season,
            groups=need_groups,
            max_per_group=5,
        ):
            if team_poaches >= max_poaches_per_team:
                break
            candidate = player_candidate(con, player, season=season, team_id=team_id)
            if has_protected_young_depth(con, team_id=team_id, season=season, group=candidate.group):
                if active_group_count(con, team_id, candidate.group) >= MIN_ACTIVE_BY_GROUP.get(candidate.group, 0):
                    skipped_prospect_protection += 1
                    continue
            if not has_plausible_poach_corresponding_move(
                con,
                team_id=team_id,
                season=season,
                incoming=candidate,
            ):
                skipped_prospect_protection += 1
                continue
            reason = (
                f"CPU practice squad poach: {team['abbreviation']} needed {candidate.group} depth "
                "and signed an outside practice squad player to the active roster."
            )
            if not sign_practice_squad_poach(con, player=player, team=team, season=season, reason=reason):
                continue
            poached += 1
            team_poaches += 1
            trim_cpu_active_roster_overages(
                con,
                season=season,
                game_id=game_id,
                include_user_team=include_user_team,
                team_id=team_id,
            )
        if team_poaches:
            teams_touched += 1
    if poached:
        sync_team_cap_space(con)
    return {
        "teams": teams_touched,
        "poached": poached,
        "skipped_prospect_protection": skipped_prospect_protection,
    }


def optimize_cpu_same_position_depth(
    con: sqlite3.Connection,
    *,
    season: int,
    game_id: str | None = None,
    include_user_team: bool = False,
    overall_gap: int = 8,
    max_swaps_per_team: int = 3,
) -> dict[str, int]:
    ensure_cutdown_schema(con)
    user_team_id = active_user_team_id(con, game_id)
    teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    teams_touched = 0
    swaps = 0
    for team in teams:
        team_id = int(team["team_id"])
        if not include_user_team and user_team_id is not None and team_id == user_team_id:
            continue
        team_swaps = 0
        positions = [
            row["position"]
            for row in con.execute(
                "SELECT DISTINCT position FROM players WHERE team_id = ? AND status = 'Practice Squad'",
                (team_id,),
            )
        ]
        for position in positions:
            if team_swaps >= max_swaps_per_team:
                break
            ps = con.execute(
                """
                SELECT *
                FROM players
                WHERE team_id = ?
                  AND status = 'Practice Squad'
                  AND position = ?
                ORDER BY COALESCE(overall, 50) DESC, COALESCE(potential, overall, 50) DESC, age ASC
                LIMIT 1
                """,
                (team_id, position),
            ).fetchone()
            active_rows = con.execute(
                """
                SELECT *
                FROM players
                WHERE team_id = ?
                  AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
                  AND position = ?
                """,
                (team_id, position),
            ).fetchall()
            if not ps or not active_rows:
                continue
            ps_candidate = player_candidate(con, ps, season=season, team_id=team_id)
            active_options: list[tuple[PlayerCandidate, sqlite3.Row]] = []
            for row in active_rows:
                active_candidate = player_candidate(con, row, season=season, team_id=team_id)
                if not is_practice_squad_swap_demote_candidate(active_candidate, ps_candidate):
                    continue
                active_options.append((active_candidate, row))
            if not active_options:
                continue
            active_candidate, active = sorted(
                active_options,
                key=lambda item: (item[0].overall, item[0].keep_score, item[0].potential),
            )[0]
            if int(ps["overall"] or 0) < int(active["overall"] or 0) + overall_gap:
                continue
            reason = (
                "CPU depth sanity: promote a clearly stronger same-position practice squad player "
                "over fringe active depth."
            )
            promote_practice_squad_replacement(
                con,
                player=ps,
                team=team,
                season=season,
                reason=reason,
                source=PRACTICE_SQUAD_SANITY_SOURCE,
            )
            if not move_active_to_practice_squad_sanity(con, player=active, team=team, season=season, reason=reason):
                release_player(
                    con,
                    int(active["player_id"]),
                    season,
                    reason,
                    source=PRACTICE_SQUAD_SANITY_SOURCE,
                )
            swaps += 1
            team_swaps += 1
        if team_swaps:
            teams_touched += 1
    return {"teams": teams_touched, "swaps": swaps}


def sign_free_agent_replacement(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    team: sqlite3.Row,
    season: int,
    reason: str,
) -> None:
    old_status = player["status"] or FREE_AGENT_STATUS
    aav = minimum_contract_aav(player)
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
            aav,
            aav,
        ),
    )
    contract_id = int(cur.lastrowid)
    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (team["team_id"], player["player_id"]),
    )
    jersey_numbers.assign_player_number(
        con,
        int(player["player_id"]),
        team_id=int(team["team_id"]),
        source="injury_replacement_signing",
    )
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=int(team["team_id"]),
        reason="Injury replacement signing changed roster composition.",
    )
    status_history(
        con,
        player=player,
        old_status=old_status,
        new_status=ACTIVE_STATUS,
        season=season,
        reason=reason,
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
        description=(
            f"{team['abbreviation']} signed {player['first_name']} {player['last_name']} "
            "from free agency as injury replacement depth."
        ),
        contract_id=contract_id,
        source=INJURY_REPLACEMENT_SOURCE,
    )


def active_user_team_id(con: sqlite3.Connection, game_id: str | None) -> int | None:
    if not table_exists(con, "game_saves"):
        return None
    if game_id:
        row = con.execute("SELECT user_team_id FROM game_saves WHERE game_id = ?", (game_id,)).fetchone()
    else:
        row = con.execute(
            """
            SELECT user_team_id
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row and row["user_team_id"] is not None:
        return int(row["user_team_id"])
    return None


def process_cpu_injury_replacements(
    con: sqlite3.Connection,
    *,
    season: int,
    game_id: str | None = None,
    active_limit: int = 53,
    max_moves_per_team: int = 8,
    include_user_team: bool = False,
) -> dict[str, int]:
    ensure_cutdown_schema(con)
    user_team_id = active_user_team_id(con, game_id)
    teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    teams_touched = 0
    promoted = 0
    signed = 0
    skipped = 0
    for team in teams:
        team_id = int(team["team_id"])
        if not include_user_team and user_team_id is not None and team_id == user_team_id:
            continue
        moves_for_team = 0
        while active_roster_count(con, team_id) < active_limit and moves_for_team < max_moves_per_team:
            groups = replacement_group_order(con, team_id)
            player = best_practice_squad_promotion(con, team_id=team_id, season=season, groups=groups)
            reason = "CPU injury replacement: active roster below 53 due to injury statuses."
            if player:
                promote_practice_squad_replacement(con, player=player, team=team, season=season, reason=reason)
                promoted += 1
                moves_for_team += 1
                continue
            player = best_free_agent_replacement(con, season=season, groups=groups)
            if player:
                sign_free_agent_replacement(con, player=player, team=team, season=season, reason=reason)
                signed += 1
                moves_for_team += 1
                continue
            skipped += 1
            break
        if moves_for_team:
            teams_touched += 1
    if promoted or signed:
        sync_team_cap_space(con)
    return {"teams": teams_touched, "promoted": promoted, "signed": signed, "skipped": skipped}


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
    touched_ids = {candidate.player_id for candidate in candidates}
    for candidate in sorted(candidates, key=lambda item: item.keep_score):
        if candidate.player_id in active_ids:
            continue
        if candidate.player_id in ps_ids:
            if move_to_practice_squad(con, candidate.player_id, season, notes):
                moved_to_ps += 1
                continue
        release_player(con, candidate.player_id, season, notes)
        released += 1

    signed_to_ps = fill_practice_squad_from_free_agents(
        con,
        team_id=int(team["team_id"]),
        season=season,
        practice_squad_limit=practice_squad_limit,
        notes=notes,
        exclude_ids=touched_ids,
    )
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
        signed_to_ps=signed_to_ps,
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
    print(f"{'TEAM':<4} {'ACTIVE':>11} {'PS':>7} {'SIGN':>5} {'TO_PS':>6} {'FA_PS':>6} {'REL':>5} {'ERR':>4} {'WARN':>5}")
    for result in results:
        print(
            f"{result.team:<4} "
            f"{result.before_active:>3}->{result.after_active:<3} "
            f"{result.before_ps:>2}->{result.after_ps:<2} "
            f"{result.signed_specialists:>5} "
            f"{result.moved_to_ps:>6} "
            f"{result.signed_to_ps:>6} "
            f"{result.released:>5} "
            f"{result.validation_errors:>4} "
            f"{result.validation_warnings:>5}"
        )
    print("")
    print(
        f"Totals: signed specialists {sum(r.signed_specialists for r in results)}, "
        f"practice squad {sum(r.moved_to_ps + r.signed_to_ps for r in results)}, "
        f"free-agent practice squad signings {sum(r.signed_to_ps for r in results)}, "
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
