"""Import real-world player accolades into the player_accolades table.

This importer is intentionally separate from the sim award generator. It seeds
historical real-life honors for existing NFL players in the base database, while
future simulated seasons continue to be written by player_accolades.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import player_accolades


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "database" / "nfl_gm.db"
DEFAULT_CACHE_DIR = ROOT / "data" / "real_player_accolades" / "wiki_cache"
USER_AGENT = "NFLGMSim/1.0 historical accolade import"
SOURCE_LEGACY = "real_legacy_player_accolades_v1"
SOURCE_WIKI_AWARDS = "wikipedia_nfl_awards_v1"
SOURCE_WIKI_ALL_PRO = "wikipedia_ap_all_pro_v1"
SOURCE_WIKI_PRO_BOWL = "wikipedia_pro_bowl_rosters_v1"
SOURCE_WIKI_SUPER_BOWL = "wikipedia_super_bowl_rosters_v1"
IMPORTED_SOURCES = (
    SOURCE_LEGACY,
    SOURCE_WIKI_AWARDS,
    SOURCE_WIKI_ALL_PRO,
    SOURCE_WIKI_PRO_BOWL,
    SOURCE_WIKI_SUPER_BOWL,
)

ROMAN_SUPER_BOWLS = {
    "L": 50,
    "LI": 51,
    "LII": 52,
    "LIII": 53,
    "LIV": 54,
    "LV": 55,
    "LVI": 56,
    "LVII": 57,
    "LVIII": 58,
    "LIX": 59,
    "LX": 60,
    "LXI": 61,
}

POSITION_ALIASES = {
    "quarterback": "QB",
    "running back": "RB",
    "halfback": "RB",
    "fullback": "FB",
    "wide receiver": "WR",
    "tight end": "TE",
    "left tackle": "OT",
    "right tackle": "OT",
    "offensive tackle": "OT",
    "tackle": "OT",
    "left guard": "OG",
    "right guard": "OG",
    "guard": "OG",
    "center": "C",
    "defensive tackle": "IDL",
    "interior defensive line": "IDL",
    "interior lineman": "IDL",
    "nose tackle": "IDL",
    "defensive end": "EDGE",
    "edge rusher": "EDGE",
    "edge defender": "EDGE",
    "linebacker": "LB",
    "inside linebacker": "LB",
    "outside linebacker": "LB",
    "cornerback": "CB",
    "nickelback": "CB",
    "defensive back": "CB",
    "safety": "S",
    "free safety": "S",
    "strong safety": "S",
    "placekicker": "K",
    "kicker": "K",
    "punter": "P",
}

POSITION_COMPATIBILITY = {
    "OFFENSE": {"QB", "RB", "FB", "WR", "TE", "OT", "OG", "C", "K", "P"},
    "DEFENSE": {"IDL", "EDGE", "LB", "CB", "S"},
    "QB": {"QB"},
    "RB": {"RB", "FB"},
    "FB": {"FB", "RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "OT": {"OT", "OG", "C"},
    "OG": {"OG", "OT", "C"},
    "C": {"C", "OG"},
    "IDL": {"IDL", "EDGE", "LB"},
    "EDGE": {"EDGE", "IDL", "LB"},
    "LB": {"LB", "EDGE", "IDL"},
    "CB": {"CB", "S"},
    "S": {"S", "CB"},
    "K": {"K"},
    "P": {"P"},
}


@dataclass(frozen=True)
class PlayerRef:
    player_id: int
    name: str
    position: str
    team_id: int | None
    status: str
    pfr_id: str | None


@dataclass(frozen=True)
class ImportRow:
    player_id: int
    season: int
    award_key: str
    award_name: str
    award_group: str
    badge_label: str
    badge_tier: str
    sort_order: int
    source: str
    award_position: str | None = None
    notes: str | None = None


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&amp;", "&")
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^A-Za-z0-9]+", "", text).lower()
    return text


def strip_markup(value: str) -> str:
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", value, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/>", "", text)
    text = re.sub(r"\{\{small\|([^{}]*)\}\}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{Small\|([^{}]*)\}\}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{center\|([^{}]*)\}\}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{nowrap\|([^{}]*)\}\}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = text.replace("&nbsp;", " ")
    return text.strip()


def link_display(link: str) -> str:
    target = link.split("#", 1)[0]
    display = target.split("|", 1)[1] if "|" in target else target
    return strip_markup(display).strip()


def extract_links(value: str) -> list[str]:
    names: list[str] = []
    for raw in re.findall(r"\[\[([^\]]+)\]\]", value):
        if raw.startswith(("File:", "Image:", "Category:")):
            continue
        display = link_display(raw)
        if display:
            names.append(display)
    return names


def first_player_link(value: str) -> str | None:
    for name in extract_links(value):
        if not re.search(r"\b(season|team|conference|league|award|football|bowl|pro bowl)\b", name, re.IGNORECASE):
            return name
    names = extract_links(value)
    return names[0] if names else None


def years_from_text(value: str) -> list[int]:
    years: set[int] = set()
    for start, end in re.findall(r"\b(20\d{2}|19\d{2})\s*[-–]\s*(20\d{2}|19\d{2})\b", value):
        a, b = int(start), int(end)
        if a <= b and b - a <= 20:
            years.update(range(a, b + 1))
    for year in re.findall(r"\b(20\d{2}|19\d{2})\b", value):
        years.add(int(year))
    return sorted(years)


def super_bowl_season(label: str) -> int | None:
    match = re.search(r"\bSB\s+([IVXLCDM]+)\b", label, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"\bSuper Bowl\s+([IVXLCDM]+)\b", label, flags=re.IGNORECASE)
    if not match:
        return None
    number = ROMAN_SUPER_BOWLS.get(match.group(1).upper())
    if not number:
        return None
    # Super Bowl I followed the 1966 NFL season.
    return 1965 + number


def load_players(con: sqlite3.Connection) -> tuple[list[PlayerRef], dict[str, list[PlayerRef]]]:
    rows = con.execute(
        """
        SELECT p.player_id, p.first_name, p.last_name, p.position, p.team_id,
               COALESCE(p.status, '') AS status, x.pfr_id
        FROM players p
        LEFT JOIN player_external_ids x ON x.player_id = p.player_id
        WHERE COALESCE(p.status, '') != 'Retired'
           OR COALESCE(p.accolades, '') NOT IN ('', 'null')
        """
    ).fetchall()
    players: list[PlayerRef] = []
    by_name: dict[str, list[PlayerRef]] = {}
    for row in rows:
        name = f"{row['first_name']} {row['last_name']}".strip()
        ref = PlayerRef(
            player_id=int(row["player_id"]),
            name=name,
            position=player_accolades.normalize_award_position(row["position"]),
            team_id=int(row["team_id"]) if row["team_id"] is not None else None,
            status=str(row["status"] or ""),
            pfr_id=row["pfr_id"],
        )
        players.append(ref)
        keys = {normalize_name(name)}
        if "." in name:
            keys.add(normalize_name(name.replace(".", "")))
        for key in keys:
            if key:
                by_name.setdefault(key, []).append(ref)
    return players, by_name


def match_player(by_name: dict[str, list[PlayerRef]], name: str, position: str | None = None) -> PlayerRef | None:
    key = normalize_name(name)
    candidates = by_name.get(key, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if position:
        allowed = POSITION_COMPATIBILITY.get(position, {position})
        positional = [candidate for candidate in candidates if candidate.position in allowed]
        if len(positional) == 1:
            return positional[0]
        if positional:
            candidates = positional
    with_pfr_id = [candidate for candidate in candidates if candidate.pfr_id]
    if len(with_pfr_id) == 1:
        return with_pfr_id[0]
    active = [candidate for candidate in candidates if candidate.status in {"Active", "IR", "Suspended", "PUP", "Questionable"}]
    if len(active) == 1:
        return active[0]
    return None


def insert_import_row(con: sqlite3.Connection, row: ImportRow) -> bool:
    fingerprint = f"{row.season}:{row.player_id}:{row.award_key}:{row.award_position or ''}"
    before = con.total_changes
    con.execute(
        """
        INSERT INTO player_accolades (
            player_id, season, team_id, award_key, award_name, award_group,
            award_position, badge_label, badge_tier, sort_order, source,
            notes, fingerprint, updated_at
        )
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(fingerprint) DO UPDATE SET
            award_name = excluded.award_name,
            award_group = excluded.award_group,
            award_position = excluded.award_position,
            badge_label = excluded.badge_label,
            badge_tier = excluded.badge_tier,
            sort_order = excluded.sort_order,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            row.player_id,
            row.season,
            row.award_key,
            row.award_name,
            row.award_group,
            row.award_position,
            row.badge_label,
            row.badge_tier,
            row.sort_order,
            row.source,
            row.notes,
            fingerprint,
        ),
    )
    return con.total_changes > before


def imported_row(
    player_id: int,
    season: int,
    award_key: str,
    *,
    source: str,
    award_position: str | None = None,
    award_name: str | None = None,
    badge_label: str | None = None,
    notes: str | None = None,
) -> ImportRow:
    meta = player_accolades.AWARD_META[award_key]
    name = award_name or player_accolades.award_name(award_key, award_position)
    badge = badge_label or player_accolades.badge_label(award_key, award_position)
    return ImportRow(
        player_id=player_id,
        season=season,
        award_key=award_key,
        award_name=name,
        award_group=str(meta["group"]),
        award_position=award_position,
        badge_label=badge,
        badge_tier=str(meta["tier"]),
        sort_order=int(meta["sort"]),
        source=source,
        notes=notes,
    )


def import_legacy(con: sqlite3.Connection, *, first_year: int, last_year: int) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT player_id, accolades
        FROM players
        WHERE COALESCE(accolades, '') NOT IN ('', 'null')
        """
    ).fetchall()
    inserted = 0
    parsed = 0
    skipped = 0
    for row in rows:
        try:
            labels = json.loads(row["accolades"] or "[]")
        except json.JSONDecodeError:
            labels = []
        if not isinstance(labels, list):
            continue
        for raw_label in labels:
            label = str(raw_label or "").strip()
            label_lower = label.lower()
            emitted = False
            player_id = int(row["player_id"])

            if "pro bowl" in label_lower:
                for year in years_from_text(label):
                    if first_year <= year <= last_year:
                        continue
                    emitted = insert_import_row(
                        con,
                        imported_row(
                            player_id,
                            year,
                            "PRO_BOWL",
                            source=SOURCE_LEGACY,
                            notes=f"Historical accolade: {label}",
                        ),
                    ) or emitted

            if "first-team all-pro" in label_lower or "first team all-pro" in label_lower:
                for year in years_from_text(label):
                    if first_year <= year <= last_year:
                        continue
                    emitted = insert_import_row(
                        con,
                        imported_row(
                            player_id,
                            year,
                            "FIRST_TEAM_ALL_PRO",
                            source=SOURCE_LEGACY,
                            notes=f"Historical accolade: {label}",
                        ),
                    ) or emitted
            elif "second-team all-pro" in label_lower or "second team all-pro" in label_lower:
                for year in years_from_text(label):
                    if first_year <= year <= last_year:
                        continue
                    emitted = insert_import_row(
                        con,
                        imported_row(
                            player_id,
                            year,
                            "SECOND_TEAM_ALL_PRO",
                            source=SOURCE_LEGACY,
                            notes=f"Historical accolade: {label}",
                        ),
                    ) or emitted

            sb_season = super_bowl_season(label)
            if sb_season and "champion" in label_lower:
                emitted = insert_import_row(
                    con,
                    imported_row(
                        player_id,
                        sb_season,
                        "SUPER_BOWL_TITLE",
                        source=SOURCE_LEGACY,
                        notes=f"Historical accolade: {label}",
                    ),
                ) or emitted

            if emitted:
                parsed += 1
                inserted += 1
            else:
                skipped += 1
    return {"labels_parsed": parsed, "labels_skipped": skipped, "rows_touched": inserted}


def wiki_wikitext(title: str, cache_dir: Path, refresh: bool = False) -> str | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", title).strip("_") + ".wiki"
    cache_path = cache_dir / cache_name
    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8")
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
        "redirects": "1",
    }
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    data = None
    for attempt in range(4):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            time.sleep(0.75)
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(6 + attempt * 4)
                continue
            return None
        except Exception:
            return None
    if data is None:
        return None
    if "error" in data or "parse" not in data:
        return None
    text = data["parse"]["wikitext"]["*"]
    cache_path.write_text(text, encoding="utf-8")
    return text


def split_wiki_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("|-"):
            if current:
                rows.append(current)
                current = []
            continue
        if line.startswith("!"):
            cells = re.split(r"\s*!!\s*", line.lstrip("!"))
            current.extend(cells)
            continue
        if line.startswith("|") and not line.startswith("|}"):
            line = re.sub(r"^\|\s*[^|]*\|\s*", "|", line) if "style=" in line[:35] or "align=" in line[:35] or "bgcolor=" in line[:35] else line
            cells = re.split(r"\s*\|\|\s*", line.lstrip("|"))
            current.extend(cells)
    if current:
        rows.append(current)
    return rows


def first_table_text(text: str, after: str | None = None) -> str:
    tables = table_texts(text, after=after)
    return tables[0] if tables else ""


def table_texts(text: str, after: str | None = None) -> list[str]:
    offset = text.find(after) if after else 0
    if offset < 0:
        offset = 0
    tables: list[str] = []
    search_from = offset
    while True:
        start = text.find("{|", search_from)
        if start < 0:
            break
        end = text.find("|}", start)
        if end < 0:
            tables.append(text[start:])
            break
        tables.append(text[start : end + 2])
        search_from = end + 2
    return tables


def table_containing(text: str, needle: str, after: str | None = None, fallback_index: int = 0) -> str:
    tables = table_texts(text, after=after)
    for table in tables:
        if needle in table:
            return table
    if 0 <= fallback_index < len(tables):
        return tables[fallback_index]
    return ""


def next_heading_index(text: str, start: int) -> int:
    for match in re.finditer(r"^==[^=\n].*?==\s*$", text[start + 1 :], flags=re.MULTILINE):
        return start + 1 + match.start()
    return len(text)


def wiki_position(cell: str, fallback: str | None = None) -> str | None:
    text = strip_markup(cell).lower()
    for raw, key in sorted(POSITION_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if raw in text:
            return key
    if fallback:
        return fallback
    return None


def segment_player_links(cell: str) -> list[tuple[str, str]]:
    cell = re.sub(r"\[\[(?:19|20)\d{2} [^\]]*season\|([^\]]+)\]\]", r"\1", cell)
    matches = list(re.finditer(r"\[\[([^\]]+)\]\]", cell))
    segments: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        name = link_display(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cell)
        segments.append((name, cell[start:end]))
    return segments


def nflplayer_template_names(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"\{\{\s*NFLplayer\s*\|([^{}]+)\}\}", text, flags=re.IGNORECASE):
        parts = [part.strip() for part in match.group(1).split("|")]
        if len(parts) < 2:
            continue
        name = parts[1]
        name = re.sub(r"\b(?:dab|d)\s*=.*$", "", name).strip()
        name = strip_markup(name.replace("&nbsp;", " "))
        if name:
            names.append(name)
    return names


def import_wiki_all_pro(
    con: sqlite3.Connection,
    by_name: dict[str, list[PlayerRef]],
    *,
    first_year: int,
    last_year: int,
    cache_dir: Path,
    refresh: bool = False,
) -> dict[str, int]:
    touched = 0
    pages = 0
    matched = 0
    for season in range(first_year, last_year + 1):
        text = wiki_wikitext(f"{season} All-Pro Team", cache_dir, refresh=refresh)
        if not text:
            continue
        pages += 1
        for cells in split_wiki_rows(text):
            if len(cells) < 3:
                continue
            position = wiki_position(cells[0])
            if not position or position not in player_accolades.ALL_PRO_SLOTS:
                continue
            first_cell = cells[1]
            second_cell = cells[2]
            for name, segment in segment_player_links(first_cell):
                marker = segment.upper()
                if "AP" not in marker or "AP-2" in marker:
                    continue
                player = match_player(by_name, name, position)
                if not player:
                    continue
                matched += 1
                touched += int(insert_import_row(
                    con,
                    imported_row(
                        player.player_id,
                        season,
                        "FIRST_TEAM_ALL_PRO",
                        award_position=position,
                        source=SOURCE_WIKI_ALL_PRO,
                        notes=f"{season} AP first-team All-Pro.",
                    ),
                ))
            for name, segment in segment_player_links(second_cell):
                marker = segment.upper()
                if "AP-2" not in marker:
                    continue
                player = match_player(by_name, name, position)
                if not player:
                    continue
                matched += 1
                touched += int(insert_import_row(
                    con,
                    imported_row(
                        player.player_id,
                        season,
                        "SECOND_TEAM_ALL_PRO",
                        award_position=position,
                        source=SOURCE_WIKI_ALL_PRO,
                        notes=f"{season} AP second-team All-Pro.",
                    ),
                ))
    return {"pages": pages, "matches": matched, "rows_touched": touched}


def import_wiki_major_awards(
    con: sqlite3.Connection,
    by_name: dict[str, list[PlayerRef]],
    *,
    cache_dir: Path,
    refresh: bool = False,
) -> dict[str, int]:
    touched = 0
    matched = 0

    def add_from_row(cells: list[str], player_cell_index: int, award_key: str, **kwargs: Any) -> None:
        nonlocal touched, matched
        import_kwargs = dict(kwargs)
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", cells[0])
        if not year_match or len(cells) <= player_cell_index:
            return
        season = int(year_match.group(1))
        name = first_player_link(cells[player_cell_index])
        if not name:
            return
        position = import_kwargs.pop("_match_position", None)
        player = match_player(by_name, name, position)
        if not player:
            return
        matched += 1
        touched += int(insert_import_row(
            con,
            imported_row(player.player_id, season, award_key, source=SOURCE_WIKI_AWARDS, **import_kwargs),
        ))

    mvp = wiki_wikitext("List of NFL Most Valuable Player awards", cache_dir, refresh=refresh)
    if mvp:
        for cells in split_wiki_rows(table_containing(mvp, "AP NFL Most Valuable Player", after="==List of winners==", fallback_index=1)):
            if len(cells) >= 2 and "NFL season" in cells[0]:
                add_from_row(cells, 1, "MVP", notes="AP NFL MVP.")

    for title, name, pos, badge in (
        ("List of NFL Offensive Player of the Year awards", "Offensive Player of the Year", "OFFENSE", "OPOY"),
        ("List of NFL Defensive Player of the Year awards", "Defensive Player of the Year", "DEFENSE", "DPOY"),
    ):
        text = wiki_wikitext(title, cache_dir, refresh=refresh)
        if not text:
            continue
        for cells in split_wiki_rows(first_table_text(text)):
            if len(cells) >= 2 and "NFL season" in cells[0]:
                add_from_row(
                    cells,
                    1,
                    "POSITION_OF_YEAR",
                    award_position=pos,
                    award_name=name,
                    badge_label=badge,
                    _match_position=pos,
                    notes=f"{name}.",
                )

    rookie = wiki_wikitext("List of NFL Rookie of the Year awards", cache_dir, refresh=refresh)
    if rookie:
        for cells in split_wiki_rows(first_table_text(rookie)):
            if len(cells) >= 5 and "NFL season" in cells[0]:
                add_from_row(
                    cells,
                    1,
                    "ROOKIE_OF_YEAR",
                    award_position="OFFENSE",
                    award_name="Offensive Rookie of the Year",
                    badge_label="OROY",
                    notes="NFL Rookie of the Year.",
                )
                add_from_row(
                    cells,
                    4,
                    "ROOKIE_OF_YEAR",
                    award_position="DEFENSE",
                    award_name="Defensive Rookie of the Year",
                    badge_label="DROY",
                    notes="NFL Rookie of the Year.",
                )

    comeback = wiki_wikitext("List of NFL Comeback Player of the Year awards", cache_dir, refresh=refresh)
    if comeback:
        comeback_tables = table_texts(comeback, after="==Associated Press==")
        comeback_table = comeback_tables[1] if len(comeback_tables) > 1 else (comeback_tables[0] if comeback_tables else "")
        for cells in split_wiki_rows(comeback_table):
            if len(cells) >= 2 and "NFL season" in cells[0]:
                add_from_row(cells, 1, "COMEBACK_PLAYER_OF_YEAR", notes="NFL Comeback Player of the Year.")

    return {"matches": matched, "rows_touched": touched}


def pro_bowl_title(game_year: int) -> str:
    return f"{game_year} Pro Bowl Games" if game_year >= 2023 else f"{game_year} Pro Bowl"


def import_wiki_pro_bowls(
    con: sqlite3.Connection,
    by_name: dict[str, list[PlayerRef]],
    *,
    first_season: int,
    last_season: int,
    cache_dir: Path,
    refresh: bool = False,
) -> dict[str, int]:
    touched = 0
    matched = 0
    pages = 0
    for season in range(first_season, last_season + 1):
        game_year = season + 1
        text = wiki_wikitext(pro_bowl_title(game_year), cache_dir, refresh=refresh)
        if not text and game_year >= 2023:
            text = wiki_wikitext(f"{game_year} Pro Bowl", cache_dir, refresh=refresh)
        if not text:
            continue
        start = -1
        for match in re.finditer(r"^==+\s*[^=\n]*rosters?[^=\n]*\s*==+$", text, flags=re.IGNORECASE | re.MULTILINE):
            start = match.start()
            break
        if start < 0:
            continue
        end = len(text)
        for match in re.finditer(
            r"^==+\s*(number of selections|notes|references|external links|see also|statistics|game notes)[^=\n]*\s*==+$",
            text[start + 1 :],
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            end = start + 1 + match.start()
            break
        roster_text = text[start:end]
        pages += 1
        seen_player_ids: set[int] = set()

        for cells in split_wiki_rows(roster_text):
            if len(cells) < 2:
                continue
            position = wiki_position(cells[0])
            if not position:
                continue
            for cell in cells[1:]:
                for name, _segment in segment_player_links(cell):
                    player = match_player(by_name, name, position)
                    if not player or player.player_id in seen_player_ids:
                        continue
                    seen_player_ids.add(player.player_id)
                    matched += 1
                    touched += int(insert_import_row(
                        con,
                        imported_row(
                            player.player_id,
                            season,
                            "PRO_BOWL",
                            source=SOURCE_WIKI_PRO_BOWL,
                            notes=f"{season} Pro Bowl selection.",
                        ),
                    ))

        if seen_player_ids:
            continue

        for name in extract_links(roster_text):
            player = match_player(by_name, name)
            if not player or player.player_id in seen_player_ids:
                continue
            seen_player_ids.add(player.player_id)
            matched += 1
            touched += int(insert_import_row(
                con,
                imported_row(
                    player.player_id,
                    season,
                    "PRO_BOWL",
                    source=SOURCE_WIKI_PRO_BOWL,
                    notes=f"{season} Pro Bowl selection.",
                ),
            ))
    return {"pages": pages, "matches": matched, "rows_touched": touched}


def import_wiki_super_bowl_rosters(
    con: sqlite3.Connection,
    by_name: dict[str, list[PlayerRef]],
    *,
    first_season: int,
    last_season: int,
    cache_dir: Path,
    refresh: bool = False,
) -> dict[str, int]:
    text = wiki_wikitext("List of Super Bowl champions", cache_dir, refresh=refresh)
    if not text:
        return {"champions": 0, "pages": 0, "matches": 0, "rows_touched": 0}

    champion_pages: dict[int, str] = {}
    for block in re.split(r"\n\|-\s*\n", text):
        season_match = re.search(r"\[\[(19\d{2}|20\d{2}) NFL season", block)
        if not season_match:
            continue
        season = int(season_match.group(1))
        if season < first_season or season > last_season:
            continue
        after_season = block[season_match.end() :]
        page_match = re.search(r"\[\[((?:19|20)\d{2} [^\]|]+ season)(?:\|[^\]]+)?\]\]", after_season)
        if not page_match:
            continue
        champion_pages[season] = page_match.group(1)

    touched = 0
    matched = 0
    pages = 0
    for season, page_title in sorted(champion_pages.items()):
        page = wiki_wikitext(page_title, cache_dir, refresh=refresh)
        if not page:
            continue
        start = page.lower().find("==final roster==")
        if start < 0:
            start = page.lower().find("{{nfl final roster")
        if start < 0:
            continue
        pages += 1
        roster_text = page[start:next_heading_index(page, start)]
        seen_player_ids: set[int] = set()
        for name in nflplayer_template_names(roster_text):
            player = match_player(by_name, name)
            if not player or player.player_id in seen_player_ids:
                continue
            seen_player_ids.add(player.player_id)
            matched += 1
            touched += int(insert_import_row(
                con,
                imported_row(
                    player.player_id,
                    season,
                    "SUPER_BOWL_TITLE",
                    source=SOURCE_WIKI_SUPER_BOWL,
                    notes=f"{season} Super Bowl champion roster.",
                ),
            ))
    return {
        "champions": len(champion_pages),
        "pages": pages,
        "matches": matched,
        "rows_touched": touched,
    }


def import_real_accolades(args: argparse.Namespace) -> dict[str, dict[str, int]]:
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    player_accolades.ensure_schema(con)
    try:
        if args.clear_imported:
            placeholders = ",".join("?" for _ in IMPORTED_SOURCES)
            con.execute(f"DELETE FROM player_accolades WHERE source IN ({placeholders})", IMPORTED_SOURCES)
        _players, by_name = load_players(con)
        results: dict[str, dict[str, int]] = {}
        if not args.no_legacy:
            results["legacy"] = import_legacy(con, first_year=args.first_year, last_year=args.last_year)
        if not args.no_wikipedia:
            results["wiki_awards"] = import_wiki_major_awards(con, by_name, cache_dir=args.cache_dir, refresh=args.refresh)
            results["wiki_all_pro"] = import_wiki_all_pro(
                con,
                by_name,
                first_year=args.first_year,
                last_year=args.last_year,
                cache_dir=args.cache_dir,
                refresh=args.refresh,
            )
            results["wiki_pro_bowl"] = import_wiki_pro_bowls(
                con,
                by_name,
                first_season=args.first_year,
                last_season=args.last_year,
                cache_dir=args.cache_dir,
                refresh=args.refresh,
            )
            results["wiki_super_bowl"] = import_wiki_super_bowl_rosters(
                con,
                by_name,
                first_season=args.first_year,
                last_season=args.last_year,
                cache_dir=args.cache_dir,
                refresh=args.refresh,
            )
        if args.apply:
            con.commit()
        else:
            con.rollback()
        return results
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import real-world historical accolades for existing players.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--first-year", type=int, default=2010, help="First NFL season to fetch from yearly Wikipedia pages.")
    parser.add_argument("--last-year", type=int, default=2025, help="Last NFL season to fetch from yearly Wikipedia pages.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--clear-imported", action="store_true", help="Delete prior real-world import rows before importing.")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached Wikipedia wikitext.")
    parser.add_argument("--no-legacy", action="store_true")
    parser.add_argument("--no-wikipedia", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    results = import_real_accolades(args)
    mode = "Saved" if args.apply else "Dry run"
    print(mode)
    for source, result in results.items():
        parts = ", ".join(f"{key}={value}" for key, value in sorted(result.items()))
        print(f"{source}: {parts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
