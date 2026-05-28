"""Save manager for NFL GM Sim.

The master database stays at database/nfl_gm.db. A playable save gets its own
SQLite copy under saves/<game_id>/nfl_gm_save.db, and game_flow operates on
that save DB.
"""

from __future__ import annotations

import argparse
import gc
import json
import re
import shutil
import sqlite3
import stat
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import game_flow
import repair_duplicate_player_names
import repair_player_data_quality


ROOT = Path(__file__).resolve().parents[1]
MASTER_DB = ROOT / "database" / "nfl_gm.db"
SAVES_DIR = ROOT / "saves"
SAVE_DB_NAME = "nfl_gm_save.db"
MANIFEST_NAME = "save_manifest.json"
REGISTRY_PATH = SAVES_DIR / "save_registry.json"
REGISTRY_VERSION = 1
GAME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def validate_game_id(game_id: str) -> str:
    if not GAME_ID_RE.match(game_id):
        raise ValueError(
            "Game ID must start with a letter or number and use only letters, "
            "numbers, underscores, or hyphens."
        )
    return game_id


def save_dir(game_id: str) -> Path:
    return SAVES_DIR / validate_game_id(game_id)


def save_db_path(game_id: str) -> Path:
    return save_dir(game_id) / SAVE_DB_NAME


def manifest_path(game_id: str) -> Path:
    return save_dir(game_id) / MANIFEST_NAME


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_registry() -> dict[str, Any]:
    registry = read_json(
        REGISTRY_PATH,
        {
            "version": REGISTRY_VERSION,
            "active_game_id": None,
            "saves": {},
        },
    )
    if "active_game_id" not in registry and "activeGameId" in registry:
        registry["active_game_id"] = registry.get("activeGameId")
    registry.pop("activeGameId", None)
    registry.setdefault("active_game_id", None)
    registry.setdefault("saves", {})
    return registry


def save_registry(registry: dict[str, Any]) -> None:
    registry["version"] = REGISTRY_VERSION
    registry.setdefault("saves", {})
    registry.pop("activeGameId", None)
    write_json(REGISTRY_PATH, registry)


def _make_writable(path: str) -> None:
    try:
        Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        return


def _rmtree_onerror(func, path: str, _exc_info) -> None:
    _make_writable(path)
    func(path)


def remove_tree_with_retries(path: Path, *, attempts: int = 6, delay_seconds: float = 0.5) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            gc.collect()
            shutil.rmtree(path, onerror=_rmtree_onerror)
            return
        except FileNotFoundError:
            return
        except (OSError, PermissionError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(delay_seconds * (attempt + 1))
    raise RuntimeError(
        f"Could not delete save folder after {attempts} attempts: {path}. "
        "Close any running sim or browser action that is using this save and try again."
    ) from last_error


def backup_sqlite(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def read_game_state(db_path: Path) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cols = {row["name"] for row in con.execute("PRAGMA table_info(game_saves)").fetchall()}
        control_mode_expr = "gs.control_mode" if "control_mode" in cols else "'team' AS control_mode"
        personality_expr = "gs.personality_run_id" if "personality_run_id" in cols else "NULL AS personality_run_id"
        development_expr = "gs.development_run_id" if "development_run_id" in cols else "NULL AS development_run_id"
        row = con.execute(
            f"""
            SELECT gs.game_id, gs.display_name, gs."current_date", gs.current_league_year,
                   gs.current_phase_code, gs.status, gs.rng_seed, gs.rating_variance_run_id,
                   {control_mode_expr},
                   {personality_expr},
                   {development_expr},
                   t.abbreviation AS user_team
            FROM game_saves gs
            LEFT JOIN teams t ON t.team_id = gs.user_team_id
            WHERE gs.status = 'active'
            ORDER BY gs.updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        state = dict(row)
        settings = {
            item["setting_key"]: item["setting_value"]
            for item in con.execute(
                """
                SELECT setting_key, setting_value
                FROM game_settings
                WHERE setting_key IN ('current_game_date', 'current_league_year', 'current_calendar_phase')
                """
            ).fetchall()
        }
        if settings.get("current_game_date") and settings["current_game_date"] > str(state.get("current_date") or ""):
            state["current_date"] = settings["current_game_date"]
            if settings.get("current_league_year"):
                state["current_league_year"] = int(settings["current_league_year"])
            if settings.get("current_calendar_phase"):
                state["current_phase_code"] = settings["current_calendar_phase"]
        return state
    finally:
        con.close()


def read_ai_gm_config(db_path: Path, game_id: str) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ai_gm_llm_config'"
        ).fetchone()
        if not exists:
            return None
        row = con.execute(
            """
            SELECT game_id, provider, endpoint, model, temperature, max_tokens,
                   request_timeout_sec, enabled, updated_at
            FROM ai_gm_llm_config
            WHERE game_id = ?
            """,
            (game_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def sync_manifest_from_db(game_id: str) -> dict[str, Any]:
    path = manifest_path(game_id)
    manifest = read_json(path, {})
    db_path = save_db_path(game_id)
    state = read_game_state(db_path)
    if state:
        manifest.update(
            {
                "game_id": game_id,
                "name": state["display_name"],
                "user_team": state["user_team"],
                "control_mode": state.get("control_mode", "team"),
                "current_date": state["current_date"],
                "current_league_year": state["current_league_year"],
                "current_phase_code": state["current_phase_code"],
                "status": state["status"],
                "seed": state["rng_seed"],
                "rating_variance_run_id": state["rating_variance_run_id"],
                "personality_run_id": state["personality_run_id"],
                "development_run_id": state["development_run_id"],
                "last_played_at": now_iso(),
            }
        )
        ai_config = read_ai_gm_config(db_path, game_id)
        if ai_config:
            manifest["ai_gm_llm"] = {
                "enabled": bool(ai_config["enabled"]),
                "provider": ai_config["provider"],
                "endpoint": ai_config["endpoint"],
                "model": ai_config["model"],
                "temperature": ai_config["temperature"],
                "max_tokens": ai_config["max_tokens"],
                "request_timeout_sec": ai_config["request_timeout_sec"],
                "updated_at": ai_config["updated_at"],
                "roadmap_doc": "docs/ai_gm_local_llm_roadmap.md",
            }
        write_json(path, manifest)
    return manifest


def register_save(manifest: dict[str, Any], *, activate: bool) -> None:
    registry = load_registry()
    game_id = manifest["game_id"]
    registry.setdefault("saves", {})[game_id] = {
        "game_id": game_id,
        "name": manifest.get("name") or game_id,
        "user_team": manifest.get("user_team"),
        "control_mode": manifest.get("control_mode", "team"),
        "db_path": manifest["db_path"],
        "manifest_path": manifest["manifest_path"],
        "current_date": manifest.get("current_date"),
        "current_phase_code": manifest.get("current_phase_code"),
        "status": manifest.get("status", "active"),
        "created_at": manifest.get("created_at"),
        "last_played_at": manifest.get("last_played_at"),
    }
    if activate:
        registry["active_game_id"] = game_id
    save_registry(registry)


def get_save_record(game_id: str | None) -> tuple[str, dict[str, Any]]:
    registry = load_registry()
    target = game_id or registry.get("active_game_id")
    if not target:
        raise ValueError("No active save. Use create or load first.")
    saves = registry.get("saves", {})
    if target not in saves:
        raise ValueError(f"Save not found in registry: {target}")
    return target, saves[target]


def create_save(args: argparse.Namespace) -> None:
    game_id = validate_game_id(args.game_id)
    folder = save_dir(game_id)
    db_path = save_db_path(game_id)
    if folder.exists():
        raise FileExistsError(f"Save folder already exists: {folder}")

    backup_sqlite(args.master_db, db_path)
    repair_player_data_quality.repair(db_path)
    con = game_flow.connect(db_path)
    try:
        repair_duplicate_player_names.repair(con, apply=True)
        flow_args = SimpleNamespace(
            game_id=game_id,
            name=args.name or game_id,
            user_team=args.user_team,
            control_mode=getattr(args, "control_mode", None),
            observe_mode=getattr(args, "observe_mode", False),
            start_year=args.start_year,
            calendar_years=args.calendar_years,
            seed=args.seed,
            notes=args.notes,
            no_variance=args.no_variance,
            no_personality_variance=args.no_personality_variance,
            no_development_modifiers=args.no_development_modifiers,
            no_draft_class_generation=args.no_draft_class_generation,
            draft_class_count=args.draft_class_count,
            draft_hidden_count=args.draft_hidden_count,
            no_hidden_draft_prospects=args.no_hidden_draft_prospects,
            draft_class_strength=args.draft_class_strength,
            rating_max_delta=args.rating_max_delta,
            rookie_potential_max_delta=args.rookie_potential_max_delta,
            young_potential_max_delta=args.young_potential_max_delta,
            young_age_cutoff=args.young_age_cutoff,
        )
        game_flow.start_game(con, flow_args)
    except Exception:
        con.close()
        raise
    finally:
        if con:
            con.close()

    state = read_game_state(db_path)
    manifest = {
        "version": 1,
        "game_id": game_id,
        "name": (state or {}).get("display_name") or args.name or game_id,
        "user_team": (state or {}).get("user_team") or (args.user_team.upper() if args.user_team else None),
        "control_mode": (state or {}).get("control_mode")
        or ("observe" if getattr(args, "observe_mode", False) or not args.user_team else "team"),
        "start_year": args.start_year,
        "current_date": (state or {}).get("current_date"),
        "current_league_year": (state or {}).get("current_league_year"),
        "current_phase_code": (state or {}).get("current_phase_code"),
        "status": (state or {}).get("status", "active"),
        "seed": (state or {}).get("rng_seed")
        if (
            not args.no_variance
            or not args.no_personality_variance
            or not args.no_development_modifiers
        )
        else None,
        "rating_variance_run_id": (state or {}).get("rating_variance_run_id"),
        "personality_run_id": (state or {}).get("personality_run_id"),
        "development_run_id": (state or {}).get("development_run_id"),
        "master_db_source": rel(args.master_db),
        "db_path": rel(db_path),
        "manifest_path": rel(manifest_path(game_id)),
        "created_at": now_iso(),
        "last_played_at": now_iso(),
        "notes": args.notes,
        "ai_gm_llm": {
            "enabled": False,
            "provider": None,
            "endpoint": None,
            "model": None,
            "temperature": None,
            "max_tokens": None,
            "request_timeout_sec": None,
            "roadmap_doc": "docs/ai_gm_local_llm_roadmap.md",
        },
    }
    write_json(manifest_path(game_id), manifest)
    register_save(manifest, activate=not args.no_activate)

    print(f"Save created: {game_id}")
    print(f"Save DB: {db_path}")
    print(f"Manifest: {manifest_path(game_id)}")
    if not args.no_activate:
        print("Active save set.")
    print()
    print("Use it with:")
    print(f"  python tools\\game_flow.py --db {rel(db_path)} status")


def list_saves(args: argparse.Namespace) -> None:
    registry = load_registry()
    saves = registry.get("saves", {})
    if not saves:
        print("No saves registered yet.")
        return
    active = registry.get("active_game_id")
    for game_id in sorted(saves):
        record = saves[game_id]
        marker = "*" if game_id == active else " "
        db_path = ROOT / record["db_path"]
        state = read_game_state(db_path)
        current_date = (state or {}).get("current_date") or record.get("current_date") or "-"
        phase = (state or {}).get("current_phase_code") or record.get("current_phase_code") or "-"
        mode = (state or {}).get("control_mode") or record.get("control_mode") or "team"
        team = (state or {}).get("user_team") or record.get("user_team") or ("OBS" if mode == "observe" else "-")
        print(f"{marker} {game_id:<24} {team:<3} {current_date:<10} {phase:<26} {record.get('name')}")
    print("* = active save")


def load_save(args: argparse.Namespace) -> None:
    game_id, record = get_save_record(args.game_id)
    db_path = ROOT / record["db_path"]
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    manifest = sync_manifest_from_db(game_id)
    register_save(manifest or {**record, "game_id": game_id}, activate=True)
    print(f"Active save: {game_id}")
    print(f"Save DB: {db_path}")
    print(f"Status command: python tools\\game_flow.py --db {rel(db_path)} status")


def delete_save(args: argparse.Namespace) -> None:
    registry = load_registry()
    game_id = validate_game_id(args.game_id)
    saves = registry.setdefault("saves", {})
    record = saves.pop(game_id, None)
    if registry.get("active_game_id") == game_id:
        registry["active_game_id"] = None
    save_registry(registry)
    target_dir = save_dir(game_id).resolve()
    saves_root = SAVES_DIR.resolve()
    if target_dir == saves_root or saves_root not in target_dir.parents:
        raise ValueError(f"Refusing to delete path outside saves directory: {target_dir}")
    existed = target_dir.exists()
    if existed:
        try:
            remove_tree_with_retries(target_dir)
        except RuntimeError as exc:
            print(f"Deleted save registry entry: {game_id}")
            print(f"Save folder is still locked and will need cleanup after the runner releases it: {target_dir}")
            print(str(exc))
            return
    if record or existed:
        print(f"Deleted save: {game_id}")
    else:
        print(f"Save not found: {game_id}")


def print_active(args: argparse.Namespace) -> None:
    game_id, record = get_save_record(None)
    db_path = ROOT / record["db_path"]
    state = read_game_state(db_path)
    mode = (state or {}).get("control_mode") or record.get("control_mode") or "team"
    print(f"Active save: {game_id}")
    print(f"Name: {(state or {}).get('display_name') or record.get('name')}")
    print(f"Mode: {mode}")
    print(f"Team: {(state or {}).get('user_team') or record.get('user_team') or ('Observe' if mode == 'observe' else '-')}")
    print(f"Date: {(state or {}).get('current_date') or record.get('current_date') or '-'}")
    print(f"Phase: {(state or {}).get('current_phase_code') or record.get('current_phase_code') or '-'}")
    print(f"DB: {db_path}")


def print_path(args: argparse.Namespace) -> None:
    game_id, record = get_save_record(args.game_id)
    print(ROOT / record["db_path"])


def status(args: argparse.Namespace) -> None:
    game_id, record = get_save_record(args.game_id)
    db_path = ROOT / record["db_path"]
    con = game_flow.connect(db_path)
    try:
        game_flow.status(con, SimpleNamespace(limit=args.limit))
    finally:
        con.close()
    sync_manifest_from_db(game_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and manage isolated NFL GM Sim saves.")
    parser.add_argument("--master-db", type=Path, default=MASTER_DB, help=f"Master DB path. Default: {MASTER_DB}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a new isolated save DB.")
    create_parser.add_argument("--game-id", required=True)
    create_parser.add_argument("--name")
    create_parser.add_argument("--user-team")
    create_parser.add_argument("--control-mode", choices=("team", "observe"))
    create_parser.add_argument("--observe-mode", action="store_true")
    create_parser.add_argument("--start-year", type=int, default=game_flow.DEFAULT_START_YEAR)
    create_parser.add_argument("--calendar-years", type=int, default=game_flow.DEFAULT_CALENDAR_YEARS)
    create_parser.add_argument("--seed", type=int)
    create_parser.add_argument("--notes")
    create_parser.add_argument("--no-variance", action="store_true")
    create_parser.add_argument("--no-personality-variance", action="store_true")
    create_parser.add_argument("--no-development-modifiers", action="store_true")
    create_parser.add_argument(
        "--no-draft-class-generation",
        dest="no_draft_class_generation",
        action="store_true",
        default=True,
    )
    create_parser.add_argument(
        "--generate-draft-class-at-start",
        dest="no_draft_class_generation",
        action="store_false",
    )
    create_parser.add_argument("--draft-class-count", type=int, default=game_flow.draft_class_bootstrap.DEFAULT_PUBLIC_PROSPECT_COUNT)
    create_parser.add_argument("--draft-hidden-count", type=int)
    create_parser.add_argument("--no-hidden-draft-prospects", action="store_true")
    create_parser.add_argument("--draft-class-strength", type=int, default=game_flow.draft_class_bootstrap.DEFAULT_CLASS_STRENGTH)
    create_parser.add_argument("--no-activate", action="store_true")
    create_parser.add_argument("--rating-max-delta", type=float, default=0.10)
    create_parser.add_argument("--rookie-potential-max-delta", type=float, default=0.25)
    create_parser.add_argument("--young-potential-max-delta", type=float, default=0.15)
    create_parser.add_argument("--young-age-cutoff", type=int, default=25)

    subparsers.add_parser("list", help="List registered saves.")
    load_parser = subparsers.add_parser("load", help="Set an existing save active.")
    load_parser.add_argument("--game-id", required=True)
    delete_parser = subparsers.add_parser("delete", help="Delete a registered save and its save folder.")
    delete_parser.add_argument("--game-id", required=True)
    subparsers.add_parser("active", help="Show the active save.")
    path_parser = subparsers.add_parser("path", help="Print a save DB path.")
    path_parser.add_argument("--game-id")
    status_parser = subparsers.add_parser("status", help="Show game_flow status for a save.")
    status_parser.add_argument("--game-id")
    status_parser.add_argument("--limit", type=int, default=8)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "create":
        create_save(args)
    elif args.command == "list":
        list_saves(args)
    elif args.command == "load":
        load_save(args)
    elif args.command == "delete":
        delete_save(args)
    elif args.command == "active":
        print_active(args)
    elif args.command == "path":
        print_path(args)
    elif args.command == "status":
        status(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
