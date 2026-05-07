#!/usr/bin/env python3
"""Download NFL team logo assets from ESPN into graphics/teams.

The downloader uses the ESPN public team endpoint because it returns a
consistent logo catalog for every team, including primary, dark, scoreboard,
grayscale, and secondary variants when ESPN exposes them.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
GRAPHICS_ROOT = ROOT / "graphics"
TEAMS_GRAPHICS_DIR = GRAPHICS_ROOT / "teams"
ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
USER_AGENT = "NFL-GM-Sim/0.1 local asset importer"
ESPN_TO_DB_ABBR = {
    "WSH": "WAS",
}


@dataclass(frozen=True)
class TeamRow:
    team_id: int
    abbreviation: str
    city: str
    nickname: str


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=45) as response:
        return response.read()


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS team_graphics_assets (
            asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            asset_key TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'logo',
            variant TEXT NOT NULL,
            local_path TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            color TEXT,
            alternate_color TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(team_id, asset_key)
        );

        CREATE INDEX IF NOT EXISTS idx_team_graphics_assets_team
            ON team_graphics_assets(team_id, asset_type, variant);

        DROP VIEW IF EXISTS team_graphics_assets_view;
        CREATE VIEW team_graphics_assets_view AS
        SELECT
            tga.asset_id,
            tga.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            tga.asset_key,
            tga.asset_type,
            tga.variant,
            tga.local_path,
            tga.source_name,
            tga.source_url,
            tga.width,
            tga.height,
            tga.color,
            tga.alternate_color,
            tga.notes,
            tga.updated_at
        FROM team_graphics_assets tga
        JOIN teams t ON t.team_id = tga.team_id;
        """
    )


def load_teams(con: sqlite3.Connection) -> dict[str, TeamRow]:
    rows = con.execute(
        "SELECT team_id, abbreviation, city, nickname FROM teams ORDER BY abbreviation"
    ).fetchall()
    return {
        row["abbreviation"].upper(): TeamRow(
            team_id=int(row["team_id"]),
            abbreviation=row["abbreviation"].upper(),
            city=row["city"],
            nickname=row["nickname"],
        )
        for row in rows
    }


def clean_token(value: str) -> str:
    value = value.lower().replace("-", "_").replace(" ", "_")
    value = re.sub(r"[^a-z0-9_]+", "", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "logo"


def extension_from_url(url: str, content_type: str | None = None) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.partition(";")[0].strip())
        if guessed:
            return guessed
    return ".png"


def variant_from_logo(logo: dict) -> str:
    rel = [clean_token(item) for item in logo.get("rel", []) if item and clean_token(item) != "full"]
    href = logo.get("href", "")
    filename = clean_token(Path(urlparse(href).path).stem)

    if "default" in rel and "dark" not in rel:
        return "primary"
    if "default" in rel and "dark" in rel:
        return "primary_dark"
    if "scoreboard" in rel and "dark" not in rel:
        return "scoreboard"
    if "scoreboard" in rel and "dark" in rel:
        return "scoreboard_dark"
    if rel:
        return clean_token("_".join(rel))
    return filename


def unique_variant(base: str, seen: set[str]) -> str:
    if base not in seen:
        seen.add(base)
        return base
    idx = 2
    while f"{base}_{idx}" in seen:
        idx += 1
    value = f"{base}_{idx}"
    seen.add(value)
    return value


def rel_path(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_asset(con: sqlite3.Connection, *, team: TeamRow, asset: dict) -> None:
    con.execute(
        """
        INSERT INTO team_graphics_assets (
            team_id, asset_key, asset_type, variant, local_path, source_name,
            source_url, width, height, color, alternate_color, notes, updated_at
        )
        VALUES (?, ?, 'logo', ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(team_id, asset_key) DO UPDATE SET
            variant = excluded.variant,
            local_path = excluded.local_path,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            width = excluded.width,
            height = excluded.height,
            color = excluded.color,
            alternate_color = excluded.alternate_color,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            team.team_id,
            asset["asset_key"],
            asset["variant"],
            asset["local_path"],
            asset["source_name"],
            asset["source_url"],
            asset.get("width"),
            asset.get("height"),
            asset.get("color"),
            asset.get("alternate_color"),
            asset.get("notes"),
        ),
    )


def import_logos(
    con: sqlite3.Connection,
    *,
    output_dir: Path,
    include_variants: bool,
    force: bool,
    sleep_seconds: float,
    dry_run: bool,
) -> dict[str, int]:
    ensure_schema(con)
    teams = load_teams(con)
    payload = fetch_json(ESPN_TEAMS_URL)
    espn_teams = payload["sports"][0]["leagues"][0]["teams"]
    manifest = {
        "source_name": "ESPN public NFL teams endpoint",
        "source_url": ESPN_TEAMS_URL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "teams": {},
    }
    counts = {"teams": 0, "assets": 0, "downloaded": 0, "skipped": 0, "missing": 0}

    for item in espn_teams:
        espn_team = item["team"]
        abbr = espn_team["abbreviation"].upper()
        db_abbr = ESPN_TO_DB_ABBR.get(abbr, abbr)
        team = teams.get(db_abbr)
        if not team:
            counts["missing"] += 1
            continue

        team_dir = output_dir / team.abbreviation / "logos"
        if not dry_run:
            team_dir.mkdir(parents=True, exist_ok=True)
        seen_variants: set[str] = set()
        team_assets = []
        logos = espn_team.get("logos", [])
        if not include_variants:
            logos = [
                logo
                for logo in logos
                if set(logo.get("rel", [])) >= {"full", "default"}
                and "dark" not in set(logo.get("rel", []))
            ][:1]

        for logo in logos:
            href = logo["href"]
            base_variant = variant_from_logo(logo)
            variant = unique_variant(base_variant, seen_variants)
            suffix = extension_from_url(href)
            local_file = team_dir / f"{variant}{suffix}"
            rels = ",".join(logo.get("rel", []))
            asset = {
                "asset_key": f"logo_{variant}",
                "variant": variant,
                "local_path": rel_path(local_file),
                "source_name": "ESPN",
                "source_url": href,
                "width": logo.get("width"),
                "height": logo.get("height"),
                "color": espn_team.get("color"),
                "alternate_color": espn_team.get("alternateColor"),
                "notes": f"ESPN logo rel={rels}",
            }
            team_assets.append(asset)
            counts["assets"] += 1

            if local_file.exists() and not force:
                counts["skipped"] += 1
            elif dry_run:
                counts["skipped"] += 1
            else:
                try:
                    local_file.write_bytes(fetch_bytes(href))
                    counts["downloaded"] += 1
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                except (HTTPError, URLError, TimeoutError) as exc:
                    counts["missing"] += 1
                    asset["notes"] += f"; download_failed={exc}"
                    continue

            if not dry_run:
                upsert_asset(con, team=team, asset=asset)

        team_manifest = {
            "team_id": team.team_id,
            "abbreviation": team.abbreviation,
            "city": team.city,
            "nickname": team.nickname,
            "espn_id": espn_team.get("id"),
            "espn_uid": espn_team.get("uid"),
            "espn_slug": espn_team.get("slug"),
            "display_name": espn_team.get("displayName"),
            "color": espn_team.get("color"),
            "alternate_color": espn_team.get("alternateColor"),
            "logos": team_assets,
        }
        manifest["teams"][team.abbreviation] = team_manifest
        if not dry_run:
            write_json(output_dir / abbr / "team_graphics.json", team_manifest)
        counts["teams"] += 1

    if not dry_run:
        write_json(output_dir / "team_logos_manifest.json", manifest)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Download NFL team logo graphics from ESPN.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--output-dir", type=Path, default=TEAMS_GRAPHICS_DIR)
    parser.add_argument("--primary-only", action="store_true", help="Only download the primary default logo.")
    parser.add_argument("--force", action="store_true", help="Redownload existing files.")
    parser.add_argument("--sleep", type=float, default=0.02, help="Pause between downloads.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with connect(args.db) as con:
        counts = import_logos(
            con,
            output_dir=args.output_dir,
            include_variants=not args.primary_only,
            force=args.force,
            sleep_seconds=args.sleep,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            con.commit()

    print(f"Teams matched: {counts['teams']}")
    print(f"Logo assets discovered: {counts['assets']}")
    print(f"Downloaded: {counts['downloaded']}")
    print(f"Skipped existing/dry-run: {counts['skipped']}")
    print(f"Missing or failed: {counts['missing']}")
    print(f"Output: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
