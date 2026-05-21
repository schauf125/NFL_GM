#!/usr/bin/env python3
"""Deterministic CPU depth-chart rebuild and sanity tools."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import depth_packages, match_engine  # noqa: E402
import pro_player_fog  # noqa: E402


OFFENSE_STARTER_SLOTS = ["QB", "RB", "FB", "TE", "LWR", "RWR", "SWR", "LT", "LG", "C", "RG", "RT"]
DEFENSE_STARTER_SLOTS = ["LEDGE", "LDL", "NT", "RDL", "REDGE", "WLB", "MLB", "SLB", "LCB", "RCB", "NB", "FS", "SS"]
SPECIAL_STARTER_SLOTS = ["PK", "KO", "PT", "P", "LS", "KR", "PR", "H"]

SLOT_UNITS = {
    **{slot: "Offense" for slot in OFFENSE_STARTER_SLOTS},
    **{slot: "Defense" for slot in DEFENSE_STARTER_SLOTS},
    **{slot: "Special Teams" for slot in SPECIAL_STARTER_SLOTS},
}

SLOT_DEPTH_LIMITS = {
    "QB": 3,
    "RB": 4,
    "FB": 2,
    "TE": 4,
    "LWR": 5,
    "RWR": 5,
    "SWR": 5,
    "LT": 3,
    "LG": 3,
    "C": 3,
    "RG": 3,
    "RT": 3,
    "LEDGE": 4,
    "REDGE": 4,
    "LDL": 4,
    "RDL": 4,
    "NT": 3,
    "WLB": 3,
    "MLB": 3,
    "SLB": 3,
    "LCB": 5,
    "RCB": 5,
    "NB": 5,
    "FS": 3,
    "SS": 3,
    "PK": 1,
    "KO": 1,
    "PT": 1,
    "P": 1,
    "LS": 1,
    "KR": 4,
    "PR": 4,
    "H": 2,
}

EXCLUSIVE_STARTER_GROUPS = [
    ["LT", "LG", "C", "RG", "RT"],
    ["LWR", "RWR", "SWR", "TE", "FB"],
    ["LEDGE", "LDL", "NT", "RDL", "REDGE"],
    ["WLB", "MLB", "SLB"],
    ["LCB", "RCB", "NB", "FS", "SS"],
]


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


def upsert_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    if not table_exists(con, "game_settings"):
        return
    con.execute(
        """
        INSERT INTO game_settings (setting_key, setting_value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = excluded.setting_value,
            updated_at = datetime('now')
        """,
        (key, value),
    )


def current_season(con: sqlite3.Connection) -> int:
    if not table_exists(con, "game_settings"):
        return match_engine.DEFAULT_SEASON
    row = con.execute(
        """
        SELECT setting_value
        FROM game_settings
        WHERE setting_key IN ('current_contract_year', 'current_season')
        ORDER BY CASE setting_key WHEN 'current_contract_year' THEN 0 ELSE 1 END
        LIMIT 1
        """
    ).fetchone()
    return int(row["setting_value"]) if row else match_engine.DEFAULT_SEASON


def active_user_team(con: sqlite3.Connection, default: str | None = "MIN") -> str | None:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT user_team FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["user_team"]:
            return str(row["user_team"]).upper()
    if table_exists(con, "game_saves"):
        row = con.execute(
            """
            SELECT t.abbreviation
            FROM game_saves gs
            JOIN teams t ON t.team_id = gs.user_team_id
            WHERE gs.status = 'active'
            ORDER BY gs.updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["abbreviation"]:
            return str(row["abbreviation"]).upper()
    return default.upper() if default else None


def team_rows(con: sqlite3.Connection, team: str | None, user_team: str | None, include_user: bool) -> list[sqlite3.Row]:
    clauses = []
    params: list[object] = []
    if team:
        clauses.append("abbreviation = ?")
        params.append(team.upper())
    elif user_team and not include_user:
        clauses.append("abbreviation != ?")
        params.append(user_team.upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return con.execute(
        f"SELECT team_id, abbreviation FROM teams {where} ORDER BY abbreviation",
        params,
    ).fetchall()


def team_abbr_for_id(con: sqlite3.Connection, team_id: int | None) -> str | None:
    if team_id is None:
        return None
    row = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    return str(row["abbreviation"]).upper() if row and row["abbreviation"] else None


def mark_depth_chart_stale(
    con: sqlite3.Connection,
    team_abbr: str | None = None,
    *,
    team_id: int | None = None,
    reason: str | None = None,
) -> bool:
    abbr = (team_abbr or team_abbr_for_id(con, team_id) or "").upper()
    if not abbr:
        return False
    upsert_setting(con, f"depth_chart_needs_update_{abbr}", "1")
    upsert_setting(con, "depth_chart_needs_update", "1")
    if reason:
        upsert_setting(con, f"depth_chart_needs_update_reason_{abbr}", reason[:240])
    return True


def mark_all_cpu_depth_charts_stale(
    con: sqlite3.Connection,
    *,
    user_team: str | None = None,
    reason: str | None = None,
) -> int:
    user = (user_team or active_user_team(con) or "").upper()
    count = 0
    for row in con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall():
        abbr = str(row["abbreviation"]).upper()
        if user and abbr == user:
            continue
        if mark_depth_chart_stale(con, abbr, reason=reason):
            count += 1
    return count


def dirty_depth_chart_team_abbrs(
    con: sqlite3.Connection,
    *,
    user_team: str | None = None,
    include_user: bool = False,
) -> list[str]:
    if not table_exists(con, "game_settings"):
        return []
    user = (user_team or active_user_team(con) or "").upper()
    rows = con.execute(
        """
        SELECT setting_key
        FROM game_settings
        WHERE setting_key LIKE 'depth_chart_needs_update_%'
          AND setting_key NOT LIKE 'depth_chart_needs_update_reason_%'
          AND setting_value = '1'
        ORDER BY setting_key
        """
    ).fetchall()
    abbrs: list[str] = []
    for row in rows:
        abbr = str(row["setting_key"]).replace("depth_chart_needs_update_", "", 1).upper()
        if not abbr or (user and abbr == user and not include_user):
            continue
        abbrs.append(abbr)
    if abbrs:
        return sorted(set(abbrs))

    global_dirty = con.execute(
        """
        SELECT setting_value
        FROM game_settings
        WHERE setting_key = 'depth_chart_needs_update'
        """
    ).fetchone()
    if not global_dirty or str(global_dirty["setting_value"]) != "1":
        return []
    return [
        str(row["abbreviation"]).upper()
        for row in team_rows(con, None, user if user else None, include_user)
    ]


def clear_depth_chart_stale(con: sqlite3.Connection, team_abbrs: list[str]) -> None:
    if not table_exists(con, "game_settings"):
        return
    for abbr in team_abbrs:
        con.execute(
            "UPDATE game_settings SET setting_value = '0', updated_at = datetime('now') WHERE setting_key = ?",
            (f"depth_chart_needs_update_{abbr.upper()}",),
        )
        con.execute(
            "DELETE FROM game_settings WHERE setting_key = ?",
            (f"depth_chart_needs_update_reason_{abbr.upper()}",),
        )
    remaining = con.execute(
        """
        SELECT 1
        FROM game_settings
        WHERE setting_key LIKE 'depth_chart_needs_update_%'
          AND setting_key NOT LIKE 'depth_chart_needs_update_reason_%'
          AND setting_value = '1'
        LIMIT 1
        """
    ).fetchone()
    if not remaining:
        con.execute(
            "UPDATE game_settings SET setting_value = '0', updated_at = datetime('now') WHERE setting_key = 'depth_chart_needs_update'"
        )


def rebuild_dirty_depth_charts(
    con: sqlite3.Connection,
    *,
    season: int | None = None,
    user_team: str | None = None,
    include_user: bool = False,
    apply: bool = True,
) -> dict[str, object]:
    if not table_exists(con, "depth_charts"):
        return {"teams": 0, "rows": 0, "rebuilt": []}
    target_season = season or current_season(con)
    dirty_abbrs = dirty_depth_chart_team_abbrs(con, user_team=user_team, include_user=include_user)
    if not dirty_abbrs:
        return {"teams": 0, "rows": 0, "rebuilt": []}
    qmarks = ",".join("?" for _ in dirty_abbrs)
    teams = con.execute(
        f"SELECT team_id, abbreviation FROM teams WHERE abbreviation IN ({qmarks}) ORDER BY abbreviation",
        dirty_abbrs,
    ).fetchall()
    results = [
        rebuild_team_depth(con, team_row=team_row, season=target_season, apply=apply)
        for team_row in teams
    ]
    rebuilt = [str(item["team"]).upper() for item in results]
    if apply:
        clear_depth_chart_stale(con, rebuilt)
    return {
        "teams": len(results),
        "rows": sum(int(item["rows"]) for item in results),
        "rebuilt": rebuilt,
    }


def legal_candidates(team: match_engine.TeamSnapshot, slot: str) -> list[match_engine.PlayerSnapshot]:
    positions = match_engine.SLOT_POSITION_FALLBACKS.get(slot, [slot])
    if slot.upper() == "FB":
        primary_positions = ["FB", "TE"]
        primary = [player for player in team.roster if player.position in primary_positions]
        if primary:
            positions = primary_positions
    return sorted(
        [player for player in team.roster if player.position in positions],
        key=lambda player: depth_sort_score(team, player, slot),
        reverse=True,
    )


def depth_sort_score(
    team: match_engine.TeamSnapshot,
    player: match_engine.PlayerSnapshot,
    slot: str,
) -> tuple[float, float, float, float]:
    slot = slot.upper()
    role_score = team.score_for_slot(player, slot)
    overall = float(player.metadata.get("overall") or player.general_score())
    potential = float(player.metadata.get("potential") or overall)
    age = float(player.metadata.get("age") or 27)
    position_bonus = 0.0
    if slot == "FB":
        if player.position == "FB":
            position_bonus = 10.0
        elif player.position == "TE":
            position_bonus = 7.0
        elif player.position == "RB":
            position_bonus = -7.0
    elif slot == "NB":
        if player.position == "NB":
            position_bonus = 5.0
        elif player.position == "CB":
            position_bonus = 3.0
    elif slot in {"FS", "SS"} and player.position in {"FS", "SS", "S"}:
        position_bonus = 3.0
    elif slot in {"LEDGE", "REDGE"} and player.position == "EDGE":
        position_bonus = 3.0
    elif slot in {"LDL", "RDL", "NT"} and player.position in {"IDL", "DT", "NT"}:
        position_bonus = 3.0

    if slot == "QB":
        mental = (
            player.rating("processing_speed")
            + player.rating("play_recognition")
            + player.rating("composure")
            + player.rating("discipline")
        ) / 4.0
        years_exp = float(player.metadata.get("years_exp") or 0)
        contract_aav = float(player.metadata.get("contract_aav") or 0)
        starter_investment = min(2.25, max(0.0, (contract_aav - 5_000_000.0) / 7_500_000.0))
        veteran_trust = min(1.15, max(0.0, years_exp - 8.0) * 0.08) if overall >= 68 else 0.0
        veteran_decline = max(0.0, age - 38.0) * 0.08
        primary = (
            overall * 0.58
            + role_score * 0.28
            + mental * 0.11
            + potential * 0.02
            + starter_investment
            + veteran_trust
            - veteran_decline
        )
    elif slot == "RB":
        primary = overall * 0.62 + role_score * 0.38
        if overall < 70:
            primary -= (70.0 - overall) * 0.45
    elif slot == "FB":
        primary = overall * 0.72 + role_score * 0.18 + position_bonus
    else:
        primary = overall * 0.45 + role_score * 0.55 + position_bonus
    youth_tiebreak = max(0.0, potential - overall) * 0.35 - max(0.0, age - 30.0) * 0.20
    return (primary, overall, youth_tiebreak, potential)


def choose_starters(team: match_engine.TeamSnapshot, slots: list[str]) -> dict[str, match_engine.PlayerSnapshot]:
    chosen: dict[str, match_engine.PlayerSnapshot] = {}
    used: set[int] = set()
    slot_order = sorted(
        slots,
        key=lambda slot: legal_candidates(team, slot)[0].general_score() if legal_candidates(team, slot) else 0.0,
        reverse=True,
    )
    for slot in slot_order:
        candidates = legal_candidates(team, slot)
        if not candidates:
            continue
        starter = next((player for player in candidates if player.player_id not in used), candidates[0])
        chosen[slot] = starter
        used.add(starter.player_id)
    return chosen


def build_slot_depth(
    team: match_engine.TeamSnapshot,
    slot: str,
    starter: match_engine.PlayerSnapshot | None,
) -> list[match_engine.PlayerSnapshot]:
    candidates = legal_candidates(team, slot)
    if not candidates:
        return []
    limit = SLOT_DEPTH_LIMITS.get(slot, 3)
    ordered: list[match_engine.PlayerSnapshot] = []
    if starter:
        ordered.append(starter)
    for player in candidates:
        if player.player_id not in {item.player_id for item in ordered}:
            ordered.append(player)
        if len(ordered) >= limit:
            break
    return ordered


def apply_staff_evaluations_to_team(
    con: sqlite3.Connection,
    team: match_engine.TeamSnapshot,
    *,
    team_id: int,
    season: int,
    create_missing: bool,
) -> None:
    reads, _created = pro_player_fog.evaluations_for_team(
        con,
        game_id=pro_player_fog.active_game_id(con),
        season=season,
        evaluator_team_id=team_id,
        player_ids=[int(player.player_id) for player in team.roster],
        create_missing=create_missing,
    )
    for player in team.roster:
        read = reads.get(int(player.player_id))
        if not read:
            continue
        player.metadata["true_overall"] = player.metadata.get("overall")
        player.metadata["true_potential"] = player.metadata.get("potential")
        player.metadata["overall"] = int(read.get("overall") or player.metadata.get("overall") or 50)
        player.metadata["potential"] = int(read.get("potential") or player.metadata.get("potential") or player.metadata["overall"])
        player.metadata["evaluation_confidence"] = read.get("confidenceLabel") or read.get("confidence")


def rebuild_team_depth(
    con: sqlite3.Connection,
    *,
    team_row: sqlite3.Row,
    season: int,
    apply: bool,
) -> dict[str, int | str]:
    team = match_engine.load_team(con, int(team_row["team_id"]), season)
    apply_staff_evaluations_to_team(
        con,
        team,
        team_id=int(team_row["team_id"]),
        season=season,
        create_missing=apply,
    )
    active_slots = set(
        depth_packages.active_depth_slots(
            team.offense_packages(),
            team.defense_packages(),
            include_special=True,
        )
    )
    starters: dict[str, match_engine.PlayerSnapshot] = {}
    for group in EXCLUSIVE_STARTER_GROUPS:
        active_group = [slot for slot in group if slot in active_slots]
        if active_group:
            starters.update(choose_starters(team, active_group))
    for slot in ["QB", "RB", "PK", "KO", "PT", "P", "LS", "KR", "PR", "H"]:
        if slot in active_slots:
            candidates = legal_candidates(team, slot)
            if candidates:
                starters[slot] = candidates[0]

    rows: list[tuple[int, int, str, int, str]] = []
    all_slots = [
        slot
        for slot in OFFENSE_STARTER_SLOTS + DEFENSE_STARTER_SLOTS + SPECIAL_STARTER_SLOTS
        if slot in active_slots
    ]
    for slot in all_slots:
        for rank, player in enumerate(build_slot_depth(team, slot, starters.get(slot)), start=1):
            rows.append((int(team_row["team_id"]), int(player.player_id), slot, rank, SLOT_UNITS.get(slot, "Offense")))

    if apply:
        con.execute("DELETE FROM depth_charts WHERE team_id = ?", (int(team_row["team_id"]),))
        con.executemany(
            """
            INSERT INTO depth_charts (team_id, player_id, position, depth_rank, unit)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return {"team": team_row["abbreviation"], "rows": len(rows)}


def audit_team_depth(con: sqlite3.Connection, *, team_row: sqlite3.Row, season: int) -> list[dict[str, object]]:
    team = match_engine.load_team(con, int(team_row["team_id"]), season)
    apply_staff_evaluations_to_team(
        con,
        team,
        team_id=int(team_row["team_id"]),
        season=season,
        create_missing=False,
    )
    active_slots = set(
        depth_packages.active_depth_slots(
            team.offense_packages(),
            team.defense_packages(),
            include_special=False,
        )
    )
    ideal: dict[str, match_engine.PlayerSnapshot] = {}
    for group in EXCLUSIVE_STARTER_GROUPS:
        active_group = [slot for slot in group if slot in active_slots]
        if active_group:
            ideal.update(choose_starters(team, active_group))
    for slot in ["QB", "RB", "PK", "KO", "PT", "P", "LS", "KR", "PR", "H"]:
        candidates = legal_candidates(team, slot)
        if candidates:
            ideal[slot] = candidates[0]
    issues: list[dict[str, object]] = []
    for slot in [slot for slot in OFFENSE_STARTER_SLOTS + DEFENSE_STARTER_SLOTS if slot in active_slots]:
        best = ideal.get(slot)
        if not best:
            continue
        starter = team.starter(slot)
        gap = team.score_for_slot(best, slot) - team.score_for_slot(starter, slot)
        starter_overall = float(starter.metadata.get("overall") or starter.general_score())
        best_overall = float(best.metadata.get("overall") or best.general_score())
        if starter.player_id != best.player_id and (gap >= 7.0 or best_overall - starter_overall >= 8.0):
            issues.append({
                "team": team_row["abbreviation"],
                "slot": slot,
                "gap": round(gap, 1),
                "starter": starter.name,
                "starter_overall": round(starter_overall, 1),
                "best": best.name,
                "best_overall": round(best_overall, 1),
            })
    return issues


def cmd_rebuild(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        if not table_exists(con, "depth_charts"):
            raise RuntimeError("depth_charts table is missing.")
        season = args.season or current_season(con)
        teams = team_rows(con, args.team, args.user_team, args.include_user)
        results = [
            rebuild_team_depth(con, team_row=team_row, season=season, apply=args.apply)
            for team_row in teams
        ]
        if args.apply:
            con.commit()
    total_rows = sum(int(item["rows"]) for item in results)
    mode = "Rebuilt" if args.apply else "DRY RUN: would rebuild"
    print(f"{mode} CPU depth charts for {len(results)} team(s), {total_rows} row(s).")
    for item in results[:12]:
        print(f"  {item['team']}: {item['rows']} rows")
    if len(results) > 12:
        print(f"  ... {len(results) - 12} more team(s)")
    return 0


def cmd_rebuild_dirty(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        result = rebuild_dirty_depth_charts(
            con,
            season=args.season,
            user_team=args.user_team,
            include_user=args.include_user,
            apply=args.apply,
        )
        if args.apply:
            con.commit()
    mode = "Rebuilt" if args.apply else "DRY RUN: would rebuild"
    print(f"{mode} dirty CPU depth charts for {result['teams']} team(s), {result['rows']} row(s).")
    rebuilt = result.get("rebuilt") or []
    if rebuilt:
        print(f"  Teams: {', '.join(rebuilt[:16])}{' ...' if len(rebuilt) > 16 else ''}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    with connect(args.db) as con:
        season = args.season or current_season(con)
        teams = team_rows(con, args.team, args.user_team, args.include_user)
        issues: list[dict[str, object]] = []
        for team_row in teams:
            issues.extend(audit_team_depth(con, team_row=team_row, season=season))
    issues.sort(key=lambda item: float(item["gap"]), reverse=True)
    print(f"Depth chart audit: {len(issues)} issue(s) across {len(teams)} team(s).")
    for item in issues[: args.limit]:
        print(
            f"  {item['team']} {item['slot']}: {item['starter']} "
            f"OVR {item['starter_overall']} over {item['best']} OVR {item['best_overall']} "
            f"(gap {item['gap']})"
        )
    return 1 if args.strict and issues else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--season", type=int)
        subparser.add_argument("--team")
        subparser.add_argument("--user-team", default="MIN")
        subparser.add_argument("--include-user", action="store_true")

    rebuild = subparsers.add_parser("rebuild", help="Rebuild CPU depth charts deterministically.")
    add_common(rebuild)
    rebuild.add_argument("--apply", action="store_true")
    rebuild.set_defaults(func=cmd_rebuild)

    rebuild_dirty = subparsers.add_parser("rebuild-dirty", help="Rebuild only teams marked as needing depth-chart updates.")
    add_common(rebuild_dirty)
    rebuild_dirty.add_argument("--apply", action="store_true")
    rebuild_dirty.set_defaults(func=cmd_rebuild_dirty)

    audit = subparsers.add_parser("audit", help="Audit CPU depth charts for stale starters.")
    add_common(audit)
    audit.add_argument("--limit", type=int, default=40)
    audit.add_argument("--strict", action="store_true")
    audit.set_defaults(func=cmd_audit)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
