#!/usr/bin/env python3
"""Download player headshots from ESPN roster data into graphics/players.

ESPN's public roster endpoint is not an official licensed asset feed, but it is
consistent across NFL teams and works well for a local prototype. The DB mapping
table keeps the source URL so the asset provider can be replaced later.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
GRAPHICS_ROOT = ROOT / "graphics"
PLAYERS_GRAPHICS_DIR = GRAPHICS_ROOT / "players"
ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ESPN_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{espn_id}/roster"
USER_AGENT = "NFL-GM-Sim/0.1 local player asset importer"
ESPN_TO_DB_ABBR = {
    "WSH": "WAS",
}

POSITION_ALIASES = {
    "G": "OG",
    "T": "OT",
    "DT": "IDL",
    "NT": "IDL",
    "DE": "EDGE",
    "OLB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "FS": "S",
    "SS": "S",
    "PK": "K",
}


@dataclass(frozen=True)
class DbTeam:
    team_id: int
    abbreviation: str
    city: str
    nickname: str


@dataclass(frozen=True)
class EspnTeam:
    espn_id: str
    abbreviation: str
    display_name: str


@dataclass(frozen=True)
class DbPlayer:
    player_id: int
    team_id: int
    team_abbr: str
    full_name: str
    first_name: str
    last_name: str
    position: str
    status: str


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str) -> tuple[bytes, str | None]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=45) as response:
        return response.read(), response.headers.get("Content-Type")


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

        DROP VIEW IF EXISTS player_graphics_assets_view;
        CREATE VIEW player_graphics_assets_view AS
        SELECT
            pga.asset_id,
            pga.player_id,
            p.first_name,
            p.last_name,
            p.position,
            t.abbreviation,
            pga.asset_key,
            pga.asset_type,
            pga.variant,
            pga.local_path,
            pga.source_name,
            pga.source_url,
            pga.external_player_id,
            pga.width,
            pga.height,
            pga.notes,
            pga.updated_at
        FROM player_graphics_assets pga
        JOIN players p ON p.player_id = pga.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id;
        """
    )


def load_db_teams(con: sqlite3.Connection) -> dict[str, DbTeam]:
    rows = con.execute(
        "SELECT team_id, abbreviation, city, nickname FROM teams ORDER BY abbreviation"
    ).fetchall()
    return {
        row["abbreviation"].upper(): DbTeam(
            team_id=int(row["team_id"]),
            abbreviation=row["abbreviation"].upper(),
            city=row["city"],
            nickname=row["nickname"],
        )
        for row in rows
    }


def load_espn_teams() -> dict[str, EspnTeam]:
    payload = fetch_json(ESPN_TEAMS_URL)
    espn_teams = payload["sports"][0]["leagues"][0]["teams"]
    teams: dict[str, EspnTeam] = {}
    for item in espn_teams:
        team = item["team"]
        espn_abbr = team["abbreviation"].upper()
        db_abbr = ESPN_TO_DB_ABBR.get(espn_abbr, espn_abbr)
        teams[db_abbr] = EspnTeam(
            espn_id=str(team["id"]),
            abbreviation=db_abbr,
            display_name=team.get("displayName", db_abbr),
        )
    return teams


def normalize_name(value: str | None) -> str:
    value = (value or "").lower()
    value = value.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", value)


def clean_token(value: str) -> str:
    value = value.lower().replace("-", "_").replace(" ", "_")
    value = re.sub(r"[^a-z0-9_]+", "", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "player"


def normalize_position(value: str | None) -> str:
    value = (value or "").upper()
    return POSITION_ALIASES.get(value, value)


def extension_from_url(url: str, content_type: str | None = None) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.partition(";")[0].strip())
        if guessed:
            return guessed
    return ".png"


def rel_path(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def image_size(data: bytes) -> tuple[int | None, int | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if data.startswith(b"\xff\xd8"):
        idx = 2
        while idx + 9 < len(data):
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            idx += 2
            if marker in {0xD8, 0xD9}:
                continue
            segment_length = int.from_bytes(data[idx : idx + 2], "big")
            if marker in {0xC0, 0xC2} and idx + 7 < len(data):
                height = int.from_bytes(data[idx + 3 : idx + 5], "big")
                width = int.from_bytes(data[idx + 5 : idx + 7], "big")
                return width, height
            idx += segment_length
    return None, None


def load_db_players(con: sqlite3.Connection) -> dict[str, list[DbPlayer]]:
    rows = con.execute(
        """
        SELECT
            p.player_id,
            p.first_name,
            p.last_name,
            p.position,
            p.team_id,
            COALESCE(p.status, 'Active') AS status,
            t.abbreviation
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        WHERE COALESCE(p.status, 'Active') != 'Retired'
        ORDER BY t.abbreviation, p.last_name, p.first_name
        """
    ).fetchall()

    by_team: dict[str, list[DbPlayer]] = {}
    for row in rows:
        player = DbPlayer(
            player_id=int(row["player_id"]),
            team_id=int(row["team_id"]),
            team_abbr=row["abbreviation"].upper(),
            full_name=f"{row['first_name']} {row['last_name']}".strip(),
            first_name=row["first_name"],
            last_name=row["last_name"],
            position=row["position"],
            status=row["status"],
        )
        by_team.setdefault(player.team_abbr, []).append(player)
    return by_team


def find_match(athlete: dict, db_players: list[DbPlayer]) -> DbPlayer | None:
    espn_name = athlete.get("displayName") or athlete.get("fullName")
    espn_position = normalize_position((athlete.get("position") or {}).get("abbreviation"))
    normalized = normalize_name(espn_name)

    exact = [player for player in db_players if normalize_name(player.full_name) == normalized]
    if len(exact) == 1:
        return exact[0]

    if exact:
        position_matches = [player for player in exact if normalize_position(player.position) == espn_position]
        if len(position_matches) == 1:
            return position_matches[0]

    espn_first = normalize_name(athlete.get("firstName") or (espn_name or "").split(" ")[0])
    espn_last = normalize_name(athlete.get("lastName") or (espn_name or "").split(" ")[-1])
    espn_initial = espn_first[:1]
    fallback = []
    for player in db_players:
        if normalize_name(player.last_name) != espn_last:
            continue
        if normalize_name(player.first_name)[:1] != espn_initial:
            continue
        if espn_position and normalize_position(player.position) != espn_position:
            continue
        fallback.append(player)
    if len(fallback) == 1:
        return fallback[0]

    last_name_matches = [
        player
        for player in db_players
        if normalize_name(player.last_name) == espn_last
        and (not espn_position or normalize_position(player.position) == espn_position)
    ]
    if len(last_name_matches) == 1:
        return last_name_matches[0]

    return None


def iter_roster_athletes(payload: dict) -> list[tuple[str, dict]]:
    athletes: list[tuple[str, dict]] = []
    for group in payload.get("athletes", []):
        group_name = group.get("position", "unknown")
        for athlete in group.get("items", []):
            athletes.append((group_name, athlete))
    return athletes


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_asset(con: sqlite3.Connection, *, player: DbPlayer, asset: dict) -> None:
    con.execute(
        """
        INSERT INTO player_graphics_assets (
            player_id, asset_key, asset_type, variant, local_path, source_name,
            source_url, external_player_id, width, height, notes, updated_at
        )
        VALUES (?, ?, 'headshot', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(player_id, asset_key) DO UPDATE SET
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
        (
            player.player_id,
            asset["asset_key"],
            asset["variant"],
            asset["local_path"],
            asset["source_name"],
            asset["source_url"],
            asset.get("external_player_id"),
            asset.get("width"),
            asset.get("height"),
            asset.get("notes"),
        ),
    )


def import_headshots(
    con: sqlite3.Connection,
    *,
    output_dir: Path,
    teams_filter: set[str] | None,
    force: bool,
    sleep_seconds: float,
    dry_run: bool,
    limit: int | None,
) -> dict[str, int]:
    ensure_schema(con)
    db_teams = load_db_teams(con)
    espn_teams = load_espn_teams()
    db_players_by_team = load_db_players(con)
    selected_teams = sorted(teams_filter or set(db_teams))
    manifest = {
        "source_name": "ESPN public NFL team roster endpoint",
        "source_url_template": ESPN_ROSTER_URL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "teams": {},
    }
    counts = {
        "teams": 0,
        "athletes_seen": 0,
        "matched": 0,
        "downloaded": 0,
        "skipped_existing": 0,
        "missing_headshot": 0,
        "unmatched": 0,
        "errors": 0,
    }

    processed = 0
    for team_abbr in selected_teams:
        if limit is not None and processed >= limit:
            break
        team_abbr = team_abbr.upper()
        db_team = db_teams.get(team_abbr)
        espn_team = espn_teams.get(team_abbr)
        if not db_team or not espn_team:
            counts["errors"] += 1
            continue

        print(f"[{team_abbr}] Fetching ESPN roster...")
        roster_url = ESPN_ROSTER_URL.format(espn_id=espn_team.espn_id)
        try:
            roster_payload = fetch_json(roster_url)
        except (HTTPError, URLError, TimeoutError) as exc:
            counts["errors"] += 1
            manifest["teams"][team_abbr] = {"error": str(exc), "assets": []}
            continue

        team_players = db_players_by_team.get(team_abbr, [])
        team_dir = output_dir / team_abbr / "headshots"
        if not dry_run:
            team_dir.mkdir(parents=True, exist_ok=True)

        team_assets = []
        team_unmatched = []
        counts["teams"] += 1
        for group_name, athlete in iter_roster_athletes(roster_payload):
            if limit is not None and processed >= limit:
                break
            counts["athletes_seen"] += 1
            headshot = athlete.get("headshot") or {}
            headshot_url = headshot.get("href")
            if not headshot_url:
                counts["missing_headshot"] += 1
                continue

            player = find_match(athlete, team_players)
            if not player:
                counts["unmatched"] += 1
                team_unmatched.append({
                    "espn_id": athlete.get("id"),
                    "name": athlete.get("displayName") or athlete.get("fullName"),
                    "position": (athlete.get("position") or {}).get("abbreviation"),
                    "group": group_name,
                })
                continue

            processed += 1
            base_name = clean_token(player.full_name)
            extension = extension_from_url(headshot_url)
            image_path = team_dir / f"{player.player_id}_{base_name}{extension}"
            should_download = force or not image_path.exists()
            width = None
            height = None
            if should_download and not dry_run:
                try:
                    image_bytes, content_type = fetch_bytes(headshot_url)
                    extension = extension_from_url(headshot_url, content_type)
                    if image_path.suffix != extension:
                        image_path = image_path.with_suffix(extension)
                    width, height = image_size(image_bytes)
                    image_path.write_bytes(image_bytes)
                    counts["downloaded"] += 1
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                except (HTTPError, URLError, TimeoutError) as exc:
                    counts["errors"] += 1
                    team_unmatched.append({
                        "espn_id": athlete.get("id"),
                        "name": athlete.get("displayName") or athlete.get("fullName"),
                        "position": (athlete.get("position") or {}).get("abbreviation"),
                        "group": group_name,
                        "error": str(exc),
                    })
                    continue
            else:
                counts["skipped_existing"] += 1
                if image_path.exists():
                    width, height = image_size(image_path.read_bytes())

            asset = {
                "asset_key": "headshot_espn_full",
                "variant": "espn_full",
                "local_path": rel_path(image_path),
                "source_name": "ESPN public NFL team roster endpoint",
                "source_url": headshot_url,
                "external_player_id": str(athlete.get("id")) if athlete.get("id") else None,
                "width": width,
                "height": height,
                "notes": f"Matched from ESPN {team_abbr} roster group {group_name}.",
            }
            counts["matched"] += 1
            team_assets.append({
                "player_id": player.player_id,
                "name": player.full_name,
                "position": player.position,
                **asset,
            })
            if not dry_run:
                upsert_asset(con, player=player, asset=asset)

        manifest["teams"][team_abbr] = {
            "espn_id": espn_team.espn_id,
            "display_name": espn_team.display_name,
            "assets": team_assets,
            "unmatched": team_unmatched,
        }

    if not dry_run:
        con.commit()
        write_json(output_dir / "player_headshots_manifest.json", manifest)

    return counts


def parse_team_filter(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    teams: set[str] = set()
    for value in values:
        teams.update(part.strip().upper() for part in value.split(",") if part.strip())
    return teams or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Download ESPN player headshots into graphics/players.")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to nfl_gm.db")
    parser.add_argument("--output-dir", default=str(PLAYERS_GRAPHICS_DIR), help="Graphics output directory")
    parser.add_argument("--team", action="append", help="Team abbreviation filter, repeat or comma-separate")
    parser.add_argument("--force", action="store_true", help="Redownload existing image files")
    parser.add_argument("--sleep", type=float, default=0.02, help="Seconds to sleep between image downloads")
    parser.add_argument("--dry-run", action="store_true", help="Fetch rosters and match players without writing files or DB rows")
    parser.add_argument("--limit", type=int, help="Limit matched downloads for a quick test")
    args = parser.parse_args()

    con = connect(Path(args.db))
    try:
        counts = import_headshots(
            con,
            output_dir=Path(args.output_dir),
            teams_filter=parse_team_filter(args.team),
            force=args.force,
            sleep_seconds=args.sleep,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    finally:
        con.close()

    print(json.dumps(counts, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
