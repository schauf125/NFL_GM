"""Import and attach saved draft-class portrait assets."""

from __future__ import annotations

import csv
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any


ASSET_KEY = "saved_class_portrait"
PLAYER_HEADSHOT_KEY = "headshot_espn_full"


def clean_token(value: str | None) -> str:
    value = (value or "").lower().strip()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "prospect"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS draft_prospect_graphics_assets (
            asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
            draft_class_id INTEGER NOT NULL REFERENCES draft_classes(draft_class_id) ON DELETE CASCADE,
            asset_key TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'portrait',
            variant TEXT NOT NULL,
            local_path TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_path TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(prospect_id, asset_key)
        );

        CREATE INDEX IF NOT EXISTS idx_draft_prospect_graphics_assets_class
            ON draft_prospect_graphics_assets(draft_class_id, asset_type, variant);

        CREATE TABLE IF NOT EXISTS player_graphics_assets (
            asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            asset_key TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'headshot',
            variant TEXT NOT NULL,
            local_path TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            external_player_id TEXT,
            width INTEGER,
            height INTEGER,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(player_id, asset_key)
        );

        CREATE INDEX IF NOT EXISTS idx_player_graphics_assets_player
            ON player_graphics_assets(player_id, asset_type, variant);
        """
    )


def _row_value(row: Any, name: str) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    if hasattr(row, "keys") and name in row.keys():
        return row[name]
    if isinstance(row, dict):
        return row.get(name)
    return None


def _prospect_key(row: Any) -> tuple[Any, ...]:
    return (
        _row_value(row, "true_rank"),
        _row_value(row, "public_board_rank"),
        _row_value(row, "scouting_rank"),
        str(_row_value(row, "first_name") or "").lower(),
        str(_row_value(row, "last_name") or "").lower(),
        str(_row_value(row, "college") or "").lower(),
    )


def _tracker_rows(package: Path) -> dict[int, dict[str, str]]:
    tracker_path = package / "portrait_tracker.csv"
    if not tracker_path.exists():
        return {}
    rows: dict[int, dict[str, str]] = {}
    with tracker_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                rank = int(row.get("player_id") or 0)
            except (TypeError, ValueError):
                continue
            if rank > 0:
                rows[rank] = row
    return rows


def _portrait_source_for_row(package: Path, tracker: dict[int, dict[str, str]], row: Any) -> tuple[Path | None, str]:
    rank = int(_row_value(row, "rank") or 0)
    tracked = tracker.get(rank, {})
    rel_path = tracked.get("approved_file") or tracked.get("generated_file") or ""
    variant = "approved_saved_class" if tracked.get("approved_file") else "generated_saved_class"
    if rel_path:
        candidate = package / rel_path
        if candidate.exists():
            return candidate, variant

    first = clean_token(str(_row_value(row, "first_name") or ""))
    last = clean_token(str(_row_value(row, "last_name") or ""))
    for candidate in (package / "portraits" / "generated").glob(f"csv_{rank}_*.png"):
        if first in candidate.stem.lower() and last in candidate.stem.lower():
            return candidate, "generated_saved_class"
    return None, variant


def _prospects_by_import_key(con: sqlite3.Connection, draft_class_id: int) -> dict[tuple[Any, ...], int]:
    rows = con.execute(
        """
        SELECT prospect_id, true_rank, public_board_rank, scouting_rank, first_name, last_name, college
        FROM draft_prospects
        WHERE draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchall()
    return {
        _prospect_key(row): int(row["prospect_id"])
        for row in rows
    }


def import_saved_class_portraits(
    con: sqlite3.Connection,
    *,
    package: Path,
    rows: list[Any],
    draft_class_id: int,
    draft_year: int,
    root: Path,
) -> dict[str, int]:
    """Copy package portraits into the game's graphics folder and map them to prospects."""

    ensure_schema(con)
    tracker = _tracker_rows(package)
    prospect_ids = _prospects_by_import_key(con, draft_class_id)
    target_dir = root / "graphics" / "draft_classes" / str(draft_year) / "prospects" / "generated"
    copied = 0
    missing = 0
    mapped = 0
    target_dir.mkdir(parents=True, exist_ok=True)

    for row in rows:
        prospect_id = prospect_ids.get(_prospect_key(row))
        if not prospect_id:
            missing += 1
            continue
        source, variant = _portrait_source_for_row(package, tracker, row)
        if source is None:
            missing += 1
            continue
        slug = clean_token(f"{_row_value(row, 'first_name')} {_row_value(row, 'last_name')}")
        suffix = source.suffix.lower() or ".png"
        target = target_dir / f"{prospect_id}_{slug}{suffix}"
        shutil.copy2(source, target)
        local_path = target.relative_to(root).as_posix()
        con.execute(
            """
            INSERT INTO draft_prospect_graphics_assets (
                prospect_id, draft_class_id, asset_key, asset_type, variant,
                local_path, source_name, source_path, notes, updated_at
            )
            VALUES (?, ?, ?, 'portrait', ?, ?, 'saved draft class package', ?, ?, datetime('now'))
            ON CONFLICT(prospect_id, asset_key) DO UPDATE SET
                variant = excluded.variant,
                local_path = excluded.local_path,
                source_name = excluded.source_name,
                source_path = excluded.source_path,
                notes = excluded.notes,
                updated_at = datetime('now')
            """,
            (
                prospect_id,
                draft_class_id,
                ASSET_KEY,
                variant,
                local_path,
                str(source),
                f"Imported from {package.name} portrait package.",
            ),
        )
        copied += 1
        mapped += 1
    return {"copied": copied, "mapped": mapped, "missing": missing}


def prospect_portrait_map(con: sqlite3.Connection, prospect_ids: list[int]) -> dict[int, str]:
    if not prospect_ids:
        return {}
    ensure_schema(con)
    placeholders = ",".join("?" for _ in prospect_ids)
    rows = con.execute(
        f"""
        SELECT prospect_id, local_path
        FROM draft_prospect_graphics_assets
        WHERE asset_key = ?
          AND prospect_id IN ({placeholders})
        """,
        (ASSET_KEY, *prospect_ids),
    ).fetchall()
    return {int(row["prospect_id"]): str(row["local_path"]) for row in rows}


def attach_prospect_portrait_to_player(
    con: sqlite3.Connection,
    *,
    prospect_id: int,
    player_id: int,
    team_abbr: str | None,
    root: Path,
) -> str | None:
    """Attach a saved-class prospect portrait to the drafted/converted player."""

    ensure_schema(con)
    row = con.execute(
        """
        SELECT local_path, source_path, variant
        FROM draft_prospect_graphics_assets
        WHERE prospect_id = ? AND asset_key = ?
        LIMIT 1
        """,
        (prospect_id, ASSET_KEY),
    ).fetchone()
    if not row:
        return None

    source = root / str(row["local_path"])
    if not source.exists():
        source_path = Path(str(row["source_path"] or ""))
        source = source_path if source_path.exists() else source
    if not source.exists():
        return None

    player = con.execute(
        "SELECT first_name, last_name FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    player_slug = clean_token(
        f"{player['first_name']} {player['last_name']}" if player else f"player {player_id}"
    )
    team = clean_token(team_abbr or "FA").upper()
    target_dir = root / "graphics" / "players" / team / "portraits"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{player_id}_{player_slug}{source.suffix.lower() or '.png'}"
    shutil.copy2(source, target)
    local_path = target.relative_to(root).as_posix()
    con.execute(
        """
        INSERT INTO player_graphics_assets (
            player_id, asset_key, asset_type, variant, local_path,
            source_name, source_url, external_player_id, width, height, notes, updated_at
        )
        VALUES (?, ?, 'headshot', 'saved_draft_portrait', ?, 'saved draft class package', ?, NULL, NULL, NULL, ?, datetime('now'))
        ON CONFLICT(player_id, asset_key) DO UPDATE SET
            asset_type = excluded.asset_type,
            variant = excluded.variant,
            local_path = excluded.local_path,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            player_id,
            PLAYER_HEADSHOT_KEY,
            local_path,
            str(source),
            f"Attached from draft prospect {prospect_id} saved-class portrait.",
        ),
    )
    return local_path
