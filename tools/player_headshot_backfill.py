#!/usr/bin/env python3
"""Backfill missing player headshot mappings.

Real roster players should use downloaded headshots when available. Fictional
drafted players do not have a real source image, so this module creates a small
team-colored SVG fallback and registers it in player_graphics_assets.
"""

from __future__ import annotations

import argparse
import html
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
GRAPHICS_ROOT = ROOT / "graphics" / "players"
ASSET_KEY = "headshot_espn_full"


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
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


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def slug(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return token or "player"


def clean_hex(value: str | None, fallback: str) -> str:
    value = re.sub(r"[^0-9a-fA-F]", "", value or "")
    if len(value) == 6:
        return f"#{value.lower()}"
    return fallback


def player_row(con: sqlite3.Connection, player_id: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT
            p.player_id,
            p.first_name,
            p.last_name,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.team_id,
            COALESCE(t.abbreviation, 'FA') AS team,
            COALESCE(t.nickname, 'Free Agent') AS nickname,
            COALESCE(tga.color, '') AS primary_color,
            COALESCE(tga.alternate_color, '') AS alternate_color
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN team_graphics_assets tga
          ON tga.team_id = p.team_id
         AND tga.asset_type = 'logo'
         AND tga.variant IN ('primary', 'dark')
        WHERE p.player_id = ?
        ORDER BY CASE tga.variant WHEN 'primary' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()


def existing_asset_path(con: sqlite3.Connection, player_id: int) -> str | None:
    if not table_exists(con, "player_graphics_assets"):
        return None
    row = con.execute(
        """
        SELECT local_path
        FROM player_graphics_assets
        WHERE player_id = ? AND asset_key = ? AND asset_type = 'headshot'
        LIMIT 1
        """,
        (player_id, ASSET_KEY),
    ).fetchone()
    return str(row["local_path"]) if row and row["local_path"] else None


def initials(row: sqlite3.Row) -> str:
    first = str(row["first_name"] or "").strip()
    last = str(row["last_name"] or "").strip()
    value = f"{first[:1]}{last[:1]}".upper()
    return value or str(row["position"] or "FA")[:2].upper()


def fallback_svg(row: sqlite3.Row) -> str:
    primary = clean_hex(row["primary_color"], "#242a36")
    secondary = clean_hex(row["alternate_color"], "#d7dde8")
    name = html.escape(str(row["player_name"]))
    pos = html.escape(str(row["position"] or ""))
    team = html.escape(str(row["team"] or "FA"))
    init = html.escape(initials(row))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 436" width="600" height="436" role="img" aria-label="{name} headshot placeholder">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{primary}"/>
      <stop offset="1" stop-color="#111722"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="28%" r="62%">
      <stop offset="0" stop-color="{secondary}" stop-opacity="0.34"/>
      <stop offset="1" stop-color="{secondary}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="600" height="436" fill="url(#bg)"/>
  <rect width="600" height="436" fill="url(#glow)"/>
  <circle cx="300" cy="154" r="76" fill="#d9dee7" opacity="0.92"/>
  <path d="M164 394c13-87 67-137 136-137s123 50 136 137" fill="#d9dee7" opacity="0.92"/>
  <path d="M184 394c19-54 62-86 116-86s97 32 116 86" fill="{secondary}" opacity="0.30"/>
  <text x="300" y="166" text-anchor="middle" dominant-baseline="middle" font-family="Arial, Helvetica, sans-serif" font-size="70" font-weight="800" fill="#111722">{init}</text>
  <text x="300" y="344" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="30" font-weight="800" fill="#f7f9fc">{team} {pos}</text>
  <text x="300" y="382" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="22" font-weight="700" fill="#f7f9fc" opacity="0.82">ROOKIE PROFILE</text>
</svg>
"""


def register_asset(con: sqlite3.Connection, *, player_id: int, rel_path: str, notes: str) -> None:
    ensure_schema(con)
    con.execute(
        """
        INSERT INTO player_graphics_assets (
            player_id, asset_key, asset_type, variant, local_path, source_name,
            source_url, external_player_id, width, height, notes, updated_at
        )
        VALUES (?, ?, 'headshot', 'generated_placeholder', ?, 'Generated local fallback', '', NULL, 600, 436, ?, datetime('now'))
        ON CONFLICT(player_id, asset_key) DO UPDATE SET
            asset_type = excluded.asset_type,
            variant = excluded.variant,
            local_path = excluded.local_path,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            external_player_id = excluded.external_player_id,
            width = excluded.width,
            height = excluded.height,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (player_id, ASSET_KEY, rel_path, notes),
    )


def ensure_fallback_headshot(
    con: sqlite3.Connection,
    *,
    player_id: int,
    root: Path = ROOT,
    force: bool = False,
) -> str | None:
    """Create/register a local fallback headshot for one player if needed."""
    ensure_schema(con)
    current = existing_asset_path(con, player_id)
    if current and not force and (root / current).exists():
        return current
    row = player_row(con, player_id)
    if not row:
        return None
    team = str(row["team"] or "FA").upper()
    file_name = f"{player_id}_{slug(str(row['player_name']))}.svg"
    path = root / "graphics" / "players" / team / "headshots" / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    if force or not path.exists():
        path.write_text(fallback_svg(row), encoding="utf-8")
    rel = path.relative_to(root).as_posix()
    register_asset(
        con,
        player_id=player_id,
        rel_path=rel,
        notes="Generated local SVG placeholder for a fictional player without a real headshot source.",
    )
    return rel


def missing_player_ids(con: sqlite3.Connection) -> list[int]:
    ensure_schema(con)
    return [
        int(row["player_id"])
        for row in con.execute(
            """
            SELECT p.player_id
            FROM players p
            WHERE COALESCE(p.status, 'Active') != 'Retired'
              AND NOT EXISTS (
                SELECT 1 FROM player_graphics_assets a
                WHERE a.player_id = p.player_id
                  AND a.asset_key = ?
                  AND a.asset_type = 'headshot'
              )
            ORDER BY p.player_id
            """,
            (ASSET_KEY,),
        ).fetchall()
    ]


def backfill_missing(con: sqlite3.Connection, *, root: Path = ROOT, force: bool = False) -> dict[str, int]:
    created = 0
    for player_id in missing_player_ids(con):
        if ensure_fallback_headshot(con, player_id=player_id, root=root, force=force):
            created += 1
    con.commit()
    return {"created": created, "remaining": len(missing_player_ids(con))}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing player headshot mappings with local fallback SVGs.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--player-id", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    con = connect(args.db)
    try:
        if args.player_id:
            rel = ensure_fallback_headshot(con, player_id=args.player_id, root=args.root, force=args.force)
            con.commit()
            print({"player_id": args.player_id, "path": rel})
        else:
            print(backfill_missing(con, root=args.root, force=args.force))
    finally:
        con.close()


if __name__ == "__main__":
    main()
