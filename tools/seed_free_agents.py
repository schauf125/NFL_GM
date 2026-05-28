#!/usr/bin/env python3
"""Build a live free-agent pool with contract-behavior profiles.

The pool is sourced from public 2026 free-agent lists, then shaped for game
balance: older veterans are toned down, younger upside players keep modest
potential, and premium names get signing friction so they cannot be scooped up
on cheap one-year deals without a realistic fit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import re
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from import_player_yearly_stats import (
    PLAYERS_URL,
    STATS_URL_TEMPLATE,
    ensure_schema,
    get_csv_reader,
    normalize_name,
    now_utc,
    row_stat_values,
    upsert_stat_row,
)


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DATABASE_DIR = ROOT / "database"
if str(DATABASE_DIR) not in sys.path:
    sys.path.insert(0, str(DATABASE_DIR))

from migrate_legacy_sim_ratings import ensure_player_normalized_ratings, ensure_sim_rating_schema  # noqa: E402

USER_AGENT = "NFL_GM_SIM free agent pool builder"
NFLTR_URL = "https://nfltraderumors.co/2026-nfl-free-agent-list/"
SHARP_URL = (
    "https://www.sharpfootballanalysis.com/analysis/"
    "nfl-free-agents-best-available-players/"
)

POSITION_GROUPS = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "OT": "OT",
    "T": "OT",
    "LT": "OT",
    "RT": "OT",
    "G": "IOL",
    "OG": "IOL",
    "C": "IOL",
    "EDGE": "EDGE",
    "DE": "EDGE",
    "DL": "IDL",
    "DT": "IDL",
    "NT": "IDL",
    "LB": "LB",
    "ILB": "LB",
    "OLB": "LB",
    "MLB": "LB",
    "CB": "CB",
    "S": "S",
    "FS": "S",
    "SS": "S",
    "K": "ST",
    "P": "ST",
    "LS": "ST",
}

TARGETS = {
    "QB": 16,
    "RB": 25,
    "WR": 35,
    "TE": 20,
    "OT": 24,
    "IOL": 24,
    "EDGE": 28,
    "IDL": 28,
    "LB": 30,
    "CB": 30,
    "S": 24,
    "ST": 15,
}

GROUP_ORDER = [
    "QB",
    "RB",
    "WR",
    "TE",
    "OT",
    "IOL",
    "EDGE",
    "IDL",
    "LB",
    "CB",
    "S",
    "ST",
]

SHARP_PRIORITY = {
    "QB": [
        "Derek Carr",
        "Aaron Rodgers",
        "Russell Wilson",
        "Tyrod Taylor",
        "Jimmy Garoppolo",
        "Josh Dobbs",
        "Cooper Rush",
        "Jeff Driskel",
        "Skylar Thompson",
        "John Wolford",
        "Easton Stick",
        "Brett Rypien",
        "Taylor Heinicke",
        "Clayton Tune",
        "Dorian Thompson-Robinson",
        "Tim Boyle",
    ],
    "RB": [
        "Najee Harris",
        "Antonio Gibson",
        "Joe Mixon",
        "Kareem Hunt",
        "Miles Sanders",
        "Cam Akers",
        "Austin Ekeler",
        "Ameer Abdullah",
        "Khalil Herbert",
        "Nick Chubb",
        "Raheem Mostert",
        "Zamir White",
        "Alexander Mattison",
        "Gus Edwards",
        "Jamaal Williams",
        "Elijah Mitchell",
    ],
    "WR": [
        "Jauan Jennings",
        "Tyreek Hill",
        "Stefon Diggs",
        "Brandon Aiyuk",
        "Deebo Samuel",
        "Keenan Allen",
        "DeAndre Hopkins",
        "Brandin Cooks",
        "Curtis Samuel",
        "Cedrick Wilson",
        "Sterling Shepard",
        "Tim Patrick",
        "JuJu Smith-Schuster",
        "Noah Brown",
        "Tyler Lockett",
        "Josh Reynolds",
        "Zay Jones",
        "Braxton Berrios",
        "Hunter Renfrow",
        "Allen Lazard",
        "Diontae Johnson",
        "Gabe Davis",
    ],
    "TE": [
        "David Njoku",
        "Jonnu Smith",
        "Will Dissly",
        "Zach Ertz",
        "Darren Waller",
        "Taysom Hill",
        "Marcedes Lewis",
        "Nick Vannett",
        "Harrison Bryant",
        "Pharaoh Brown",
        "Donald Parham",
        "Jordan Akins",
        "C.J. Uzomah",
    ],
    "OT": [
        "Taylor Decker",
        "Jonah Williams",
        "Jack Conklin",
        "Cam Robinson",
        "Kendall Lamm",
        "Landon Young",
        "Andrus Peat",
        "Kelvin Beachum",
        "Joseph Noteboom",
        "Thayer Munford Jr.",
        "Lucas Niang",
        "George Fant",
        "D.J. Humphries",
        "Cornelius Lucas",
    ],
    "IOL": [
        "Mekhi Becton",
        "Joel Bitonio",
        "Kevin Zeitler",
        "Graham Glasgow",
        "Greg Van Roten",
        "Andre James",
        "Brady Christensen",
        "Ethan Pocic",
        "Liam Eichenberg",
        "James Daniels",
        "Daniel Brunskill",
        "Will Hernandez",
        "Sam Mustipher",
        "Alex Cappa",
    ],
    "EDGE": [
        "Joey Bosa",
        "Kyle Van Noy",
        "Jadeveon Clowney",
        "Haason Reddick",
        "Cameron Jordan",
        "Von Miller",
        "Mike Danna",
        "Dante Fowler Jr.",
        "Derek Barnett",
        "Preston Smith",
        "Leonard Floyd",
        "Brandon Graham",
        "Tyquan Lewis",
        "Clelin Ferrell",
        "Yetur Gross-Matos",
        "Denico Autry",
        "Emmanuel Ogbah",
        "Ogbonnia Okoronkwo",
        "Marcus Davenport",
        "Yannick Ngakoue",
    ],
    "IDL": [
        "Christian Wilkins",
        "Calais Campbell",
        "DaQuan Jones",
        "D.J. Reader",
        "Levi Onwuzurike",
        "Larry Ogunjobi",
        "Bilal Nichols",
        "Austin Johnson",
        "Greg Gaines",
        "Foley Fatukasi",
        "Daniel Ekuale",
        "Mike Pennel",
        "Khalen Saunders",
        "Johnathan Hankins",
        "Shelby Harris",
        "Sebastian Joseph-Day",
    ],
    "LB": [
        "Bobby Okereke",
        "Josey Jewell",
        "Bobby Wagner",
        "Germaine Pratt",
        "Matt Milano",
        "Eric Kendricks",
        "Jahlani Tavai",
        "Kenneth Murray",
        "Elandon Roberts",
        "Devin White",
        "Shaq Thompson",
        "Denzel Perryman",
        "Krys Barnes",
        "Mykal Walker",
        "Jerome Baker",
        "Zach Cunningham",
        "Deion Jones",
        "Duke Riley",
        "Nick Vigil",
    ],
    "CB": [
        "L'Jarius Sneed",
        "Trevon Diggs",
        "Marshon Lattimore",
        "Rasul Douglas",
        "Mike Hilton",
        "Tre'Davious White",
        "Shaq Griffin",
        "Fabian Moreau",
        "Dane Jackson",
        "Adoree' Jackson",
        "Jeff Okudah",
        "Michael Davis",
        "Artie Burns",
        "Troy Hill",
        "Kendall Fuller",
    ],
    "S": [
        "Jabrill Peppers",
        "Xavier Woods",
        "Donovan Wilson",
        "Quandre Diggs",
        "Taylor Rapp",
        "Isaiah Oliver",
        "Ashtyn Davis",
        "Jordan Poyer",
        "Mike Edwards",
        "Jamal Adams",
        "Rayshawn Jenkins",
        "Terrell Edmunds",
        "Jordan Whitehead",
        "Vonn Bell",
        "John Johnson",
        "Adrian Amos",
    ],
    "ST": [
        "Brandon Aubrey",
        "Daniel Carlson",
        "Matt Prater",
        "Riley Patterson",
        "Braden Mann",
        "Corey Bojorquez",
        "Johnny Hekker",
        "Thomas Morstead",
        "Bradley Pinion",
        "Matt Haack",
        "Aaron Brewer",
        "Casey Kreiter",
        "Jake McQuaide",
        "JJ Jansen",
        "Josh Harris",
    ],
}

MANUAL_ADDITIONS = [
    ("Derek Carr", "QB", "NO", 35, "manual_top_available"),
    ("Aaron Rodgers", "QB", "PIT", 42, "manual_top_available"),
    ("Josh Dobbs", "QB", "SF", 31, "manual_top_available"),
    ("Cooper Rush", "QB", "BAL", 33, "manual_top_available"),
    ("Taylor Heinicke", "QB", "LAC", 33, "manual_top_available"),
    ("Dorian Thompson-Robinson", "QB", "CLE", 26, "manual_top_available"),
    ("Tim Boyle", "QB", "NYG", 31, "manual_top_available"),
    ("Cam Akers", "RB", "MIN", 27, "manual_top_available"),
    ("Gus Edwards", "RB", "LAC", 31, "manual_top_available"),
    ("Jamaal Williams", "RB", "NO", 31, "manual_top_available"),
    ("Elijah Mitchell", "RB", "KC", 28, "manual_top_available"),
    ("Stefon Diggs", "WR", "NE", 32, "manual_top_available"),
    ("Brandon Aiyuk", "WR", "SF", 28, "manual_top_available"),
    ("Tyler Lockett", "WR", "SEA", 33, "manual_top_available"),
    ("Allen Lazard", "WR", "NYJ", 30, "manual_top_available"),
    ("Diontae Johnson", "WR", "CLE", 30, "manual_top_available"),
    ("C.J. Uzomah", "TE", "PHI", 33, "manual_top_available"),
    ("Jordan Akins", "TE", "CLE", 34, "manual_top_available"),
    ("Rasheed Walker", "OT", "GB", 26, "manual_top_available"),
    ("D.J. Humphries", "OT", "LAR", 32, "manual_top_available"),
    ("Thayer Munford Jr.", "OT", "NE", 26, "manual_top_available"),
    ("D.J. Reader", "DL", "DET", 32, "manual_top_available"),
    ("DaQuan Jones", "DL", "BUF", 34, "manual_top_available"),
    ("Levi Onwuzurike", "DL", "DET", 28, "manual_top_available"),
    ("Christian Wilkins", "DL", "LV", 30, "manual_top_available"),
    ("Josey Jewell", "LB", "CAR", 31, "manual_top_available"),
    ("Duke Riley", "LB", "MIA", 32, "manual_top_available"),
    ("Nick Vigil", "LB", "DAL", 33, "manual_top_available"),
    ("L'Jarius Sneed", "CB", "TEN", 29, "manual_top_available"),
    ("Shaq Griffin", "CB", "MIN", 31, "manual_top_available"),
    ("Troy Hill", "CB", "CAR", 35, "manual_top_available"),
    ("Kendall Fuller", "CB", "MIA", 31, "manual_top_available"),
    ("Quandre Diggs", "S", "TEN", 33, "manual_top_available"),
    ("Jordan Whitehead", "S", "TB", 29, "manual_top_available"),
    ("Vonn Bell", "S", "CIN", 31, "manual_top_available"),
    ("John Johnson", "S", "LAR", 30, "manual_top_available"),
]

NAME_ALIASES = {
    "joshdobbs": "Joshua Dobbs",
    "zeketurner": "Ezekiel Turner",
    "shaqgriffin": "Shaquill Griffin",
}

SPECIAL_FLEX_PROFILES = {
    "deebosamuel": [
        ("SWR", 7, 7, "Power slot / motion receiver fit"),
        ("RB", 5, 5, "Real gadget-back usage, but not a full-time RB"),
        ("FB", 3, 3, "Emergency backfield/motion package"),
    ],
    "tyreekhill": [
        ("SWR", 8, 8, "Slot/motion explosive package"),
        ("PR", 5, 5, "Veteran return ability, age/usage limited"),
        ("KR", 4, 4, "Emergency kickoff return option"),
    ],
    "jauanjennings": [
        ("SWR", 7, 8, "Big slot/red-zone usage"),
        ("RWR", 6, 7, "Can survive outside but best inside/dirty-work role"),
    ],
    "stefondiggs": [
        ("SWR", 7, 7, "Veteran route runner can win from slot"),
        ("LWR", 6, 6, "Outside receiver fit is declining but playable"),
    ],
    "curtissamuel": [
        ("SWR", 6, 7, "Slot/motion receiver role"),
        ("RB", 4, 5, "Manufactured-touch backfield package"),
        ("KR", 3, 3, "Emergency return option"),
    ],
    "braxtonberrios": [
        ("SWR", 5, 6, "Slot-only offensive fit"),
        ("PR", 6, 6, "Primary return specialist value"),
        ("KR", 6, 6, "Primary return specialist value"),
    ],
    "deandrecarter": [
        ("SWR", 4, 4, "Depth slot fit"),
        ("PR", 6, 6, "Return specialist value"),
        ("KR", 6, 6, "Return specialist value"),
    ],
    "tylerlockett": [
        ("SWR", 6, 6, "Aging but polished slot/outside separator"),
        ("PR", 3, 3, "Emergency return option only at this age"),
    ],
    "dantepettis": [
        ("SWR", 4, 4, "Depth slot fit"),
        ("PR", 5, 5, "Return background keeps special-teams path alive"),
    ],
    "austinekeler": [
        ("WR", 3, 3, "Receiving back, not a real WR conversion"),
        ("SWR", 3, 3, "Package-only slot usage"),
    ],
    "antoniogibson": [
        ("WR", 3, 4, "College WR background, package-only NFL usage"),
        ("SWR", 3, 4, "Package-only slot usage"),
        ("KR", 4, 4, "Kick-return experience"),
    ],
    "taysomhill": [
        ("QB", 5, 5, "Gadget QB package, not a full-time passer"),
        ("RB", 5, 5, "Real rushing package value"),
        ("FB", 5, 5, "H-back/fullback/gadget fit"),
        ("WR", 3, 3, "Emergency receiver alignment"),
    ],
    "darrenwaller": [
        ("SWR", 4, 4, "Big slot package if healthy"),
    ],
    "brandonaiyuk": [
        ("LWR", 7, 8, "Outside receiver fit"),
        ("RWR", 7, 8, "Outside receiver fit"),
        ("SWR", 5, 6, "Usable slot package"),
    ],
}

PREMIUM_PROFILES = {
    "jauanjennings": {
        "overall": 78,
        "potential": 80,
        "asking": 18_000_000,
        "minimum": 12_000_000,
        "years": 3,
        "motivation": "contract_chaser",
        "preferred": "SF,TEN,DEN,LV,KC",
        "hometown": "TEN",
        "notes": "Wants a true WR2/featured red-zone role and a multi-year payday; will not rush into a cheap contender discount.",
    },
    "tyreekhill": {
        "overall": 78,
        "potential": 78,
        "asking": 13_500_000,
        "minimum": 8_000_000,
        "years": 1,
        "motivation": "rehab_star_contender",
        "preferred": "KC,MIA,BAL,DAL,PHI",
        "hometown": "KC,MIA",
        "notes": "Still dangerous, but age and injury risk are baked in. Prefers contender visibility, incentives, and a role that does not ask him to carry 140 targets.",
    },
    "stefondiggs": {
        "overall": 76,
        "potential": 76,
        "asking": 10_500_000,
        "minimum": 6_500_000,
        "years": 1,
        "motivation": "prove_it_star",
        "preferred": "BAL,DAL,ATL,HOU,WAS",
        "hometown": "BAL,WAS",
        "notes": "One-year prove-it market; expects a real passing-game role and veteran respect.",
    },
    "brandonaiyuk": {
        "overall": 79,
        "potential": 81,
        "asking": 17_000_000,
        "minimum": 11_000_000,
        "years": 2,
        "motivation": "prove_it_starter",
        "preferred": "SF,ARI,LAC,NE,PIT",
        "hometown": "ARI",
        "notes": "Younger than most premium WRs in the pool; injury/availability concerns keep the rating from getting too high.",
    },
    "deebosamuel": {
        "overall": 76,
        "potential": 76,
        "asking": 9_500_000,
        "minimum": 5_500_000,
        "years": 1,
        "motivation": "role_specific",
        "preferred": "SF,CAR,WAS,BAL,KC",
        "hometown": "CAR",
        "notes": "Needs a creative offensive fit. More valuable to teams with motion/RAC usage than as a traditional outside WR.",
    },
    "davidnjoku": {
        "overall": 80,
        "potential": 80,
        "asking": 14_500_000,
        "minimum": 9_500_000,
        "years": 2,
        "motivation": "starter_contract",
        "preferred": "CLE,LAC,CIN,IND,NYG",
        "hometown": "CLE",
        "notes": "Top tight end in this pool; will expect starter targets, not TE2 money.",
    },
    "taylordecker": {
        "overall": 78,
        "potential": 78,
        "asking": 13_000_000,
        "minimum": 8_500_000,
        "years": 1,
        "motivation": "veteran_left_tackle",
        "preferred": "DET,KC,CIN,LAC,WAS",
        "hometown": "DET",
        "notes": "Older but still a real tackle option. Wants a clean playoff shot or a premium left-tackle shortage contract.",
    },
    "joeybosa": {
        "overall": 76,
        "potential": 76,
        "asking": 11_000_000,
        "minimum": 6_500_000,
        "years": 1,
        "motivation": "contender_pass_rush",
        "preferred": "LAC,SF,BUF,KC,PHI",
        "hometown": "LAC,SF",
        "notes": "Still flashes, but availability risk caps his rating. Prefers a rotational rush plan on a contender.",
    },
    "christianwilkins": {
        "overall": 80,
        "potential": 80,
        "asking": 18_000_000,
        "minimum": 12_000_000,
        "years": 2,
        "motivation": "premium_interior",
        "preferred": "LV,MIA,HOU,DET,PHI",
        "hometown": "MIA",
        "notes": "Rare interior talent for a free-agent pool. Not cheap unless medicals or market patience break his way.",
    },
    "ljariussneed": {
        "overall": 79,
        "potential": 80,
        "asking": 15_500_000,
        "minimum": 10_000_000,
        "years": 2,
        "motivation": "premium_corner",
        "preferred": "TEN,KC,DET,BAL,PHI",
        "hometown": "KC,TEN",
        "notes": "Physical corner who wants starter money and a defensive staff that lets him press and match.",
    },
    "trevondiggs": {
        "overall": 78,
        "potential": 79,
        "asking": 14_000_000,
        "minimum": 9_000_000,
        "years": 1,
        "motivation": "ballhawk_market",
        "preferred": "DAL,WAS,LV,BUF,DET",
        "hometown": "DAL",
        "notes": "High-variance ballhawk; big market, but teams will price in coverage volatility and recent health.",
    },
    "marshonlattimore": {
        "overall": 78,
        "potential": 78,
        "asking": 13_000_000,
        "minimum": 8_500_000,
        "years": 1,
        "motivation": "veteran_corner",
        "preferred": "NO,WAS,BAL,KC,PHI",
        "hometown": "NO,WAS",
        "notes": "Veteran CB1 name, but decline and availability risk make this a short-term, fit-sensitive signing.",
    },
    "bobbywagner": {
        "overall": 75,
        "potential": 74,
        "asking": 6_000_000,
        "minimum": 3_500_000,
        "years": 1,
        "motivation": "last_ring_leader",
        "preferred": "SEA,LAR,WAS,SF,PHI",
        "hometown": "SEA,LAR",
        "notes": "Leadership and run fits still matter; age keeps the overall controlled. Strong contender preference.",
    },
    "aaronrodgers": {
        "overall": 70,
        "potential": 70,
        "asking": 12_000_000,
        "minimum": 7_000_000,
        "years": 1,
        "motivation": "selective_veteran_qb",
        "preferred": "PIT,LV,NYJ,SF,MIN",
        "hometown": "SF",
        "notes": "Selective late-career starter/mentor. Will choose fit and control over a generic backup offer.",
    },
}


@dataclass
class Candidate:
    name: str
    position: str
    previous_team: str
    age: int | None
    note: str
    source: str
    source_order: int

    @property
    def group(self) -> str:
        return POSITION_GROUPS.get(self.position.upper(), self.position.upper())

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    @property
    def alias_key(self) -> str:
        alias = NAME_ALIASES.get(self.key)
        return normalize_name(alias) if alias else self.key


def clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", text).strip()


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_age(value: str) -> int | None:
    try:
        return int(float(value.strip()))
    except (TypeError, ValueError, AttributeError):
        return None


def parse_nfltr_candidates() -> list[Candidate]:
    html_text = fetch_text(NFLTR_URL)
    candidates: list[Candidate] = []
    rows = re.findall(r"<tr>\s*(.*?)</tr>", html_text, flags=re.I | re.S)
    order = 0
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.I | re.S)
        if len(cells) < 5:
            continue
        player, pos, team, age, note = [clean_text(cell) for cell in cells[:5]]
        if not player or player.lower() == "player":
            continue
        pos = pos.upper()
        if pos not in POSITION_GROUPS:
            continue
        note_clean = note.strip()
        if "retired" in note_clean.lower():
            continue
        if note_clean in {"ROFR", "RFA", "2nd", "5th"}:
            continue
        order += 1
        candidates.append(
            Candidate(
                name=player,
                position={"G": "OG", "DL": "IDL"}.get(pos, pos),
                previous_team=team.upper(),
                age=parse_age(age),
                note=note_clean,
                source="NFLTradeRumors 2026 available FA list",
                source_order=order,
            )
        )
    return candidates


def add_manual_candidates(candidates: list[Candidate]) -> list[Candidate]:
    by_key = {candidate.key: candidate for candidate in candidates}
    order = max((candidate.source_order for candidate in candidates), default=0)
    for name, pos, team, age, note in MANUAL_ADDITIONS:
        key = normalize_name(name)
        if key in by_key:
            continue
        order += 1
        candidate = Candidate(
            name=name,
            position={"G": "OG", "DL": "IDL"}.get(pos.upper(), pos.upper()),
            previous_team=team,
            age=age,
            note=note,
            source="Manual top-available supplement",
            source_order=order,
        )
        by_key[key] = candidate
        candidates.append(candidate)
    return candidates


def priority_score(candidate: Candidate) -> int:
    group_list = SHARP_PRIORITY.get(candidate.group, [])
    score = 10_000 - candidate.source_order
    for index, name in enumerate(group_list):
        if normalize_name(name) == candidate.key:
            score += 50_000 - (index * 500)
            break
    if candidate.key in PREMIUM_PROFILES:
        score += 25_000
    if candidate.note == "manual_top_available":
        score += 10_000
    if candidate.age and candidate.age > 34:
        score -= (candidate.age - 34) * 300
    return score


def existing_player_maps(con: sqlite3.Connection) -> tuple[set[str], set[str]]:
    active_keys: set[str] = set()
    free_agent_keys: set[str] = set()
    for name, team_id, status in con.execute(
        """
        SELECT first_name || ' ' || last_name, team_id, status
        FROM players
        """
    ):
        key = normalize_name(name)
        if status == "Free Agent" or team_id is None:
            free_agent_keys.add(key)
        else:
            active_keys.add(key)
    return active_keys, free_agent_keys


def select_candidates(
    con: sqlite3.Connection, candidates: list[Candidate]
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    active_keys, _ = existing_player_maps(con)
    grouped: dict[str, list[Candidate]] = {group: [] for group in GROUP_ORDER}
    skipped: list[tuple[Candidate, str]] = []
    seen: set[str] = set()

    for candidate in sorted(candidates, key=priority_score, reverse=True):
        if candidate.group not in grouped:
            skipped.append((candidate, "unsupported_position_group"))
            continue
        if candidate.key in seen:
            skipped.append((candidate, "duplicate_source_name"))
            continue
        if candidate.key in active_keys or candidate.alias_key in active_keys:
            skipped.append((candidate, "already_on_roster_in_db"))
            continue
        if len(grouped[candidate.group]) >= TARGETS[candidate.group]:
            skipped.append((candidate, "group_target_met"))
            continue
        grouped[candidate.group].append(candidate)
        seen.add(candidate.key)

    selected: list[Candidate] = []
    for group in GROUP_ORDER:
        selected.extend(grouped[group])
    return selected, skipped


def load_nflverse_players() -> dict[str, list[dict[str, str]]]:
    by_key: dict[str, list[dict[str, str]]] = {}
    request = urllib.request.Request(PLAYERS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        reader = csv.DictReader(io.TextIOWrapper(response, encoding="utf-8-sig"))
        for row in reader:
            possible_names = {
                row.get("display_name", ""),
                f"{row.get('first_name', '')} {row.get('last_name', '')}",
                f"{row.get('common_first_name', '')} {row.get('last_name', '')}",
                f"{row.get('football_name', '')} {row.get('last_name', '')}",
            }
            for name in possible_names:
                key = normalize_name(name)
                if key:
                    by_key.setdefault(key, []).append(row)
    return by_key


def compatible_position(candidate: Candidate, row: dict[str, str]) -> bool:
    row_pos = (row.get("position") or row.get("pff_position") or "").upper()
    if not row_pos:
        return True
    row_group = POSITION_GROUPS.get(row_pos, row_pos)
    return row_group == candidate.group or row_pos == candidate.position


def choose_nflverse_row(
    candidate: Candidate, players_by_name: dict[str, list[dict[str, str]]]
) -> dict[str, str] | None:
    keys = [candidate.key]
    if candidate.alias_key != candidate.key:
        keys.append(candidate.alias_key)
    rows: list[dict[str, str]] = []
    for key in keys:
        rows.extend(players_by_name.get(key, []))
    if not rows:
        return None

    scored: list[tuple[int, dict[str, str]]] = []
    for row in rows:
        score = 0
        if compatible_position(candidate, row):
            score += 30
        if (row.get("latest_team") or "").upper() == candidate.previous_team:
            score += 20
        last_season = parse_age(row.get("last_season", ""))
        if last_season and last_season >= 2024:
            score += 15
        if row.get("gsis_id"):
            score += 5
        scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def split_name(name: str) -> tuple[str, str]:
    parts = name.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def inches_from_height(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return int(text)
    match = re.match(r"(\d+)-(\d+)", text)
    if match:
        return int(match.group(1)) * 12 + int(match.group(2))
    return None


DEFAULT_BODY = {
    "QB": (74, 220),
    "RB": (70, 215),
    "WR": (73, 205),
    "TE": (76, 250),
    "OT": (77, 315),
    "IOL": (76, 310),
    "EDGE": (76, 260),
    "IDL": (75, 305),
    "LB": (74, 235),
    "CB": (71, 195),
    "S": (72, 205),
    "ST": (73, 220),
}


def stable_int(key: str, low: int, high: int) -> int:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    number = int(digest[:8], 16)
    return low + (number % (high - low + 1))


def base_overall(candidate: Candidate, rank: int) -> int:
    group = candidate.group
    premium = PREMIUM_PROFILES.get(candidate.key)
    if premium:
        return int(premium["overall"])

    if rank <= 3:
        overall = 74
    elif rank <= 8:
        overall = 70
    elif rank <= 15:
        overall = 67
    elif rank <= 24:
        overall = 63
    else:
        overall = 59

    group_bump = {
        "QB": -3,
        "RB": -1,
        "WR": 0,
        "TE": 0,
        "OT": 1,
        "IOL": 1,
        "EDGE": 1,
        "IDL": 1,
        "LB": 0,
        "CB": 0,
        "S": 0,
        "ST": -1,
    }.get(group, 0)
    overall += group_bump

    age = candidate.age or 29
    if age >= 37:
        overall -= 5
    elif age >= 34:
        overall -= 3
    elif age >= 31:
        overall -= 1
    elif age <= 25 and rank > 10:
        overall += 1

    return max(52, min(80, overall))


def potential_from_overall(candidate: Candidate, overall: int) -> int:
    premium = PREMIUM_PROFILES.get(candidate.key)
    if premium:
        return int(premium["potential"])
    age = candidate.age or 29
    if age <= 25:
        return min(82, overall + 4)
    if age <= 27:
        return min(80, overall + 2)
    if age <= 30:
        return min(78, overall + 1)
    if age <= 33:
        return overall
    return max(48, overall - 2)


def attributes_for(candidate: Candidate, overall: int) -> dict[str, int | None]:
    group = candidate.group
    age = candidate.age or 29
    variance = stable_int(candidate.key, -4, 4)
    awareness = min(90, max(45, overall + (age // 4) + variance))
    injury = 42 + max(0, age - 29) * 3
    if candidate.key in {
        "tyreekhill",
        "joeybosa",
        "brandonaiyuk",
        "darrenwaller",
        "zachertz",
        "nickchubb",
        "mattmilano",
        "trevondiggs",
    }:
        injury += 12
    injury = max(35, min(88, injury))

    attrs = {
        "speed": 50,
        "strength": 50,
        "agility": 50,
        "awareness": awareness,
        "injury_prone": injury,
        "throw_power": None,
        "throw_acc": None,
        "route_running": None,
        "catching": None,
        "run_blocking": None,
        "pass_blocking": None,
        "trucking": None,
        "tackle": None,
        "pass_rush": None,
        "coverage": None,
        "kick_power": None,
        "kick_acc": None,
    }

    if group == "QB":
        attrs.update(
            speed=max(48, 65 - age // 2 + stable_int(candidate.key, -5, 6)),
            agility=max(48, 66 - age // 3 + stable_int(candidate.key, -4, 5)),
            strength=58,
            throw_power=max(60, min(88, overall + 9 + stable_int(candidate.key, -3, 5))),
            throw_acc=max(55, min(86, overall + 4 + stable_int(candidate.key, -4, 4))),
        )
    elif group == "RB":
        attrs.update(
            speed=max(62, min(90, overall + 14 - max(0, age - 29) + stable_int(candidate.key, -4, 4))),
            agility=max(62, min(90, overall + 13 - max(0, age - 29) + stable_int(candidate.key, -4, 5))),
            strength=max(55, min(82, overall + 3 + stable_int(candidate.key, -4, 4))),
            trucking=max(55, min(86, overall + 5 + stable_int(candidate.key, -5, 6))),
            catching=max(48, min(78, overall - 2 + stable_int(candidate.key, -5, 5))),
        )
    elif group == "WR":
        speed_boost = 18
        if candidate.key == "tyreekhill":
            speed_boost = 23
        attrs.update(
            speed=max(60, min(94, overall + speed_boost - max(0, age - 30) + stable_int(candidate.key, -4, 4))),
            agility=max(58, min(92, overall + 14 - max(0, age - 30) + stable_int(candidate.key, -4, 4))),
            strength=max(48, min(76, overall - 8 + stable_int(candidate.key, -3, 5))),
            route_running=max(52, min(88, overall + 5 + stable_int(candidate.key, -4, 5))),
            catching=max(52, min(88, overall + 4 + stable_int(candidate.key, -4, 5))),
        )
    elif group == "TE":
        attrs.update(
            speed=max(52, min(84, overall + 6 - max(0, age - 30) + stable_int(candidate.key, -4, 5))),
            agility=max(50, min(82, overall + 2 - max(0, age - 30) + stable_int(candidate.key, -4, 5))),
            strength=max(62, min(88, overall + 8 + stable_int(candidate.key, -4, 4))),
            route_running=max(48, min(82, overall + stable_int(candidate.key, -4, 4))),
            catching=max(50, min(84, overall + 3 + stable_int(candidate.key, -4, 4))),
            run_blocking=max(45, min(82, overall - 1 + stable_int(candidate.key, -5, 5))),
        )
    elif group in {"OT", "IOL"}:
        attrs.update(
            speed=max(40, min(62, 56 - age // 8 + stable_int(candidate.key, -3, 3))),
            agility=max(42, min(68, 58 - age // 10 + stable_int(candidate.key, -4, 4))),
            strength=max(68, min(92, overall + 15 + stable_int(candidate.key, -4, 4))),
            run_blocking=max(52, min(86, overall + 4 + stable_int(candidate.key, -5, 5))),
            pass_blocking=max(52, min(88, overall + 5 + stable_int(candidate.key, -5, 5))),
        )
    elif group == "EDGE":
        attrs.update(
            speed=max(55, min(84, overall + 8 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            agility=max(52, min(84, overall + 6 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            strength=max(62, min(88, overall + 8 + stable_int(candidate.key, -4, 4))),
            tackle=max(50, min(82, overall + stable_int(candidate.key, -5, 5))),
            pass_rush=max(54, min(88, overall + 7 + stable_int(candidate.key, -5, 5))),
            coverage=max(35, min(65, overall - 12 + stable_int(candidate.key, -5, 5))),
        )
    elif group == "IDL":
        attrs.update(
            speed=max(42, min(70, overall - 1 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            agility=max(42, min(72, overall - 2 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            strength=max(68, min(93, overall + 16 + stable_int(candidate.key, -4, 4))),
            tackle=max(52, min(86, overall + 4 + stable_int(candidate.key, -5, 5))),
            pass_rush=max(48, min(84, overall + 2 + stable_int(candidate.key, -5, 5))),
        )
    elif group == "LB":
        attrs.update(
            speed=max(54, min(84, overall + 8 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            agility=max(52, min(84, overall + 6 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            strength=max(58, min(84, overall + 5 + stable_int(candidate.key, -4, 4))),
            tackle=max(52, min(86, overall + 6 + stable_int(candidate.key, -5, 5))),
            coverage=max(45, min(80, overall - 1 + stable_int(candidate.key, -6, 5))),
            pass_rush=max(42, min(78, overall - 2 + stable_int(candidate.key, -6, 5))),
        )
    elif group == "CB":
        attrs.update(
            speed=max(58, min(91, overall + 14 - max(0, age - 30) + stable_int(candidate.key, -4, 4))),
            agility=max(58, min(91, overall + 13 - max(0, age - 30) + stable_int(candidate.key, -4, 4))),
            strength=max(45, min(72, overall - 7 + stable_int(candidate.key, -4, 4))),
            tackle=max(44, min(78, overall - 4 + stable_int(candidate.key, -5, 5))),
            coverage=max(52, min(88, overall + 7 + stable_int(candidate.key, -5, 5))),
        )
    elif group == "S":
        attrs.update(
            speed=max(56, min(88, overall + 11 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            agility=max(54, min(88, overall + 8 - max(0, age - 31) + stable_int(candidate.key, -4, 4))),
            strength=max(52, min(78, overall - 1 + stable_int(candidate.key, -4, 4))),
            tackle=max(50, min(84, overall + 4 + stable_int(candidate.key, -5, 5))),
            coverage=max(50, min(86, overall + 4 + stable_int(candidate.key, -5, 5))),
        )
    elif group == "ST":
        attrs.update(
            speed=max(35, min(58, 48 + stable_int(candidate.key, -4, 4))),
            agility=max(35, min(58, 48 + stable_int(candidate.key, -4, 4))),
            strength=max(45, min(70, 55 + stable_int(candidate.key, -4, 4))),
        )
        if candidate.position == "K":
            attrs.update(
                kick_power=max(58, min(88, overall + 14 + stable_int(candidate.key, -4, 5))),
                kick_acc=max(58, min(88, overall + 11 + stable_int(candidate.key, -4, 5))),
            )
        elif candidate.position == "P":
            attrs.update(
                kick_power=max(58, min(88, overall + 13 + stable_int(candidate.key, -4, 5))),
                kick_acc=max(55, min(84, overall + 8 + stable_int(candidate.key, -4, 5))),
            )

    return attrs


def market_tier(overall: int, key: str) -> str:
    if key in PREMIUM_PROFILES or overall >= 78:
        return "Premium"
    if overall >= 72:
        return "Starter"
    if overall >= 66:
        return "Rotation"
    if overall >= 60:
        return "Depth"
    return "Camp"


BASE_AAV = {
    "QB": 5_500_000,
    "RB": 2_400_000,
    "WR": 4_000_000,
    "TE": 3_200_000,
    "OT": 5_500_000,
    "IOL": 3_800_000,
    "EDGE": 5_500_000,
    "IDL": 4_500_000,
    "LB": 3_200_000,
    "CB": 4_500_000,
    "S": 3_400_000,
    "ST": 1_500_000,
}


def profile_for(candidate: Candidate, overall: int, rank: int) -> dict[str, object]:
    premium = PREMIUM_PROFILES.get(candidate.key)
    age = candidate.age or 29
    tier = market_tier(overall, candidate.key)
    if premium:
        asking = int(premium["asking"])
        minimum = int(premium["minimum"])
        preferred_years = int(premium["years"])
        motivation = str(premium["motivation"])
        preferred = str(premium["preferred"])
        hometown = str(premium["hometown"])
        notes = str(premium["notes"])
    else:
        base = BASE_AAV[candidate.group]
        multiplier = max(0.55, 1 + ((overall - 64) * 0.11))
        if tier == "Starter":
            multiplier += 0.45
        elif tier == "Rotation":
            multiplier += 0.15
        elif tier == "Camp":
            multiplier -= 0.25
        if age >= 34:
            multiplier -= 0.25
        asking = int(round(base * multiplier / 100_000) * 100_000)
        minimum = int(round(asking * (0.55 if age >= 32 else 0.65) / 100_000) * 100_000)
        preferred_years = 1 if age >= 31 or tier in {"Depth", "Camp"} else 2
        if tier in {"Premium", "Starter"} and age <= 30:
            motivation = "starter_contract"
        elif age >= 34:
            motivation = "last_ring_or_familiar_fit"
        elif overall <= 62:
            motivation = "camp_competition"
        else:
            motivation = "role_chaser"
        contender_list = "KC,PHI,BAL,BUF,DET,HOU,SF,WAS"
        preferred = f"{candidate.previous_team},{contender_list}"
        hometown = candidate.previous_team
        notes = "Market generated from available-free-agent list; rating is intentionally conservative for a post-draft free-agent pool."

    contract_priority = 12
    contender_priority = 10
    role_priority = 11
    hometown_priority = 7
    patience = 8
    if tier == "Premium":
        contract_priority += 5
        patience += 6
    if age >= 33:
        contender_priority += 5
        contract_priority -= 2
        patience += 2
    if candidate.group in {"QB", "WR", "OT", "EDGE", "CB"} and tier in {"Premium", "Starter"}:
        contract_priority += 2
    if motivation in {"contract_chaser", "starter_contract", "premium_corner", "premium_interior"}:
        contract_priority += 2
    if "contender" in motivation or "ring" in motivation:
        contender_priority += 3
    if overall <= 63:
        role_priority += 4
        patience -= 2

    guarantee_pct = 10
    if tier == "Premium":
        guarantee_pct = 50 if age <= 31 else 35
    elif tier == "Starter":
        guarantee_pct = 32
    elif tier == "Rotation":
        guarantee_pct = 20
    elif tier == "Depth":
        guarantee_pct = 12

    return {
        "tier": tier,
        "asking": max(915_000, asking),
        "minimum": max(840_000, min(minimum, asking)),
        "preferred_years": preferred_years,
        "guarantee_pct": guarantee_pct,
        "contract_priority": max(1, min(20, contract_priority)),
        "contender_priority": max(1, min(20, contender_priority)),
        "role_priority": max(1, min(20, role_priority)),
        "hometown_priority": max(1, min(20, hometown_priority)),
        "patience": max(1, min(20, patience)),
        "preferred": preferred,
        "hometown": hometown,
        "motivation": motivation,
        "notes": notes,
    }


def ensure_free_agent_schema(con: sqlite3.Connection) -> None:
    ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS free_agent_profiles (
            player_id INTEGER PRIMARY KEY,
            position_group TEXT NOT NULL,
            previous_team TEXT,
            market_tier TEXT NOT NULL,
            asking_aav INTEGER NOT NULL,
            minimum_aav INTEGER NOT NULL,
            preferred_years INTEGER NOT NULL DEFAULT 1,
            guarantee_pct INTEGER NOT NULL DEFAULT 0,
            contract_priority INTEGER NOT NULL DEFAULT 10,
            contender_priority INTEGER NOT NULL DEFAULT 10,
            role_priority INTEGER NOT NULL DEFAULT 10,
            hometown_priority INTEGER NOT NULL DEFAULT 5,
            patience INTEGER NOT NULL DEFAULT 10,
            preferred_teams TEXT,
            hometown_teams TEXT,
            motivation TEXT,
            signing_notes TEXT,
            source TEXT NOT NULL,
            source_url TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE
        );

        DROP VIEW IF EXISTS free_agent_pool_view;
        CREATE VIEW free_agent_pool_view AS
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            f.position_group,
            p.age,
            p.years_exp,
            p.is_rookie,
            p.overall,
            p.potential,
            p.dev_trait,
            p.injury_prone,
            f.market_tier,
            f.asking_aav,
            f.minimum_aav,
            f.preferred_years,
            f.guarantee_pct,
            f.contract_priority,
            f.contender_priority,
            f.role_priority,
            f.hometown_priority,
            f.patience,
            f.previous_team,
            f.preferred_teams,
            f.hometown_teams,
            f.motivation,
            f.signing_notes,
            x.gsis_id,
            x.pfr_id
        FROM free_agent_profiles f
        JOIN players p ON p.player_id = f.player_id
        LEFT JOIN player_external_ids x ON x.player_id = p.player_id
        WHERE p.status = 'Free Agent' AND p.team_id IS NULL;
        """
    )


def dev_trait_for(candidate: Candidate, overall: int) -> str:
    if candidate.key in PREMIUM_PROFILES and overall >= 78:
        return "Impact"
    if candidate.age and candidate.age <= 25 and overall >= 66:
        return "Normal+"
    if candidate.age and candidate.age >= 34:
        return "Declining"
    return "Normal"


def upsert_free_agent(
    con: sqlite3.Connection,
    candidate: Candidate,
    nflverse_row: dict[str, str] | None,
    rank: int,
) -> tuple[int, str]:
    overall = base_overall(candidate, rank)
    potential = potential_from_overall(candidate, overall)
    attrs = attributes_for(candidate, overall)
    profile = profile_for(candidate, overall, rank)
    first_name, last_name = split_name(candidate.name)

    height, weight = DEFAULT_BODY[candidate.group]
    college = None
    years_exp = max(0, (candidate.age or 27) - 22)
    if nflverse_row:
        height = inches_from_height(nflverse_row.get("height")) or height
        weight = parse_age(nflverse_row.get("weight", "")) or weight
        college = nflverse_row.get("college_name") or None
        years_exp = parse_age(nflverse_row.get("years_of_experience", "")) or years_exp

    existing = None
    for row in con.execute(
        """
        SELECT player_id, first_name || ' ' || last_name
        FROM players
        WHERE status = 'Free Agent' AND team_id IS NULL
        """
    ):
        if normalize_name(row[1]) == candidate.key:
            existing = int(row[0])
            break

    values = (
        first_name,
        last_name,
        candidate.position,
        None,
        candidate.age,
        years_exp,
        college,
        height,
        weight,
        overall,
        potential,
        dev_trait_for(candidate, overall),
        attrs["speed"],
        attrs["strength"],
        attrs["agility"],
        attrs["awareness"],
        attrs["injury_prone"],
        attrs["throw_power"],
        attrs["throw_acc"],
        attrs["route_running"],
        attrs["catching"],
        attrs["run_blocking"],
        attrs["pass_blocking"],
        attrs["trucking"],
        attrs["tackle"],
        attrs["pass_rush"],
        attrs["coverage"],
        attrs["kick_power"],
        attrs["kick_acc"],
        "Free Agent",
        0,
        None,
    )

    if existing:
        player_id = existing
        con.execute(
            """
            UPDATE players
            SET first_name=?, last_name=?, position=?, team_id=?, age=?,
                years_exp=?, college=?, height_in=?, weight_lbs=?,
                overall=?, potential=?, dev_trait=?, speed=?, strength=?,
                agility=?, awareness=?, injury_prone=?, throw_power=?,
                throw_acc=?, route_running=?, catching=?, run_blocking=?,
                pass_blocking=?, trucking=?, tackle=?, pass_rush=?,
                coverage=?, kick_power=?, kick_acc=?, status=?,
                is_rookie=?, accolades=?
            WHERE player_id=?
            """,
            (*values, player_id),
        )
        action = "updated"
    else:
        cur = con.execute(
            """
            INSERT INTO players (
                first_name, last_name, position, team_id, age, years_exp,
                college, height_in, weight_lbs, overall, potential, dev_trait,
                speed, strength, agility, awareness, injury_prone,
                throw_power, throw_acc, route_running, catching,
                run_blocking, pass_blocking, trucking, tackle, pass_rush,
                coverage, kick_power, kick_acc, status, is_rookie, accolades
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        player_id = int(cur.lastrowid)
        action = "inserted"

    if nflverse_row:
        con.execute(
            """
            INSERT INTO player_external_ids (
                player_id, gsis_id, pfr_id, pff_id, otc_id, espn_id,
                matched_name, latest_team, source, last_matched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'nflverse_players', ?)
            ON CONFLICT(player_id) DO UPDATE SET
                gsis_id = excluded.gsis_id,
                pfr_id = excluded.pfr_id,
                pff_id = excluded.pff_id,
                otc_id = excluded.otc_id,
                espn_id = excluded.espn_id,
                matched_name = excluded.matched_name,
                latest_team = excluded.latest_team,
                source = excluded.source,
                last_matched_at = excluded.last_matched_at
            """,
            (
                player_id,
                nflverse_row.get("gsis_id") or None,
                nflverse_row.get("pfr_id") or None,
                nflverse_row.get("pff_id") or None,
                nflverse_row.get("otc_id") or None,
                nflverse_row.get("espn_id") or None,
                nflverse_row.get("display_name") or candidate.name,
                nflverse_row.get("latest_team") or candidate.previous_team,
                now_utc(),
            ),
        )

    con.execute(
        """
        INSERT INTO free_agent_profiles (
            player_id, position_group, previous_team, market_tier,
            asking_aav, minimum_aav, preferred_years, guarantee_pct,
            contract_priority, contender_priority, role_priority,
            hometown_priority, patience, preferred_teams, hometown_teams,
            motivation, signing_notes, source, source_url, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            position_group = excluded.position_group,
            previous_team = excluded.previous_team,
            market_tier = excluded.market_tier,
            asking_aav = excluded.asking_aav,
            minimum_aav = excluded.minimum_aav,
            preferred_years = excluded.preferred_years,
            guarantee_pct = excluded.guarantee_pct,
            contract_priority = excluded.contract_priority,
            contender_priority = excluded.contender_priority,
            role_priority = excluded.role_priority,
            hometown_priority = excluded.hometown_priority,
            patience = excluded.patience,
            preferred_teams = excluded.preferred_teams,
            hometown_teams = excluded.hometown_teams,
            motivation = excluded.motivation,
            signing_notes = excluded.signing_notes,
            source = excluded.source,
            source_url = excluded.source_url,
            updated_at = excluded.updated_at
        """,
        (
            player_id,
            candidate.group,
            candidate.previous_team,
            profile["tier"],
            profile["asking"],
            profile["minimum"],
            profile["preferred_years"],
            profile["guarantee_pct"],
            profile["contract_priority"],
            profile["contender_priority"],
            profile["role_priority"],
            profile["hometown_priority"],
            profile["patience"],
            profile["preferred"],
            profile["hometown"],
            profile["motivation"],
            profile["notes"],
            f"{candidate.source}; Sharp best-available priority where applicable",
            f"{NFLTR_URL}; {SHARP_URL}",
            now_utc(),
        ),
    )

    con.execute("DELETE FROM player_position_flex WHERE player_id = ?", (player_id,))
    insert_flex(con, player_id, candidate, overall, potential)
    ensure_player_normalized_ratings(
        con,
        player_id,
        source="free_agent_pool_2026",
        schema_ready=True,
    )
    return player_id, action


def flex_score(overall: int) -> int:
    if overall >= 78:
        return 8
    if overall >= 72:
        return 7
    if overall >= 66:
        return 6
    if overall >= 60:
        return 5
    return 4


def insert_flex(
    con: sqlite3.Connection, player_id: int, candidate: Candidate, overall: int, potential: int
) -> None:
    primary_exp = flex_score(overall)
    primary_pot = max(primary_exp, min(9, primary_exp + (1 if potential > overall else 0)))
    flexes = [(candidate.position, primary_exp, primary_pot, 1, "Primary FA position")]

    group = candidate.group
    if group == "OT":
        flexes.extend([("LT", max(3, primary_exp - 1), primary_pot, 0, "Tackle-side flexibility"), ("RT", primary_exp, primary_pot, 0, "Tackle-side flexibility")])
    elif group == "IOL":
        flexes.extend([("OG", primary_exp, primary_pot, 0, "Interior OL flexibility"), ("C", max(3, primary_exp - 2), max(4, primary_pot - 1), 0, "Limited center/guard flexibility")])
    elif group == "EDGE":
        flexes.extend([("OLB", primary_exp, primary_pot, 0, "Edge/OLB projection"), ("DE", max(4, primary_exp - 1), primary_pot, 0, "Hand-down rush package")])
    elif group == "IDL":
        flexes.extend([("DT", primary_exp, primary_pot, 0, "Interior DL flexibility"), ("NT", max(3, primary_exp - 2), max(4, primary_pot - 1), 0, "Nose/anchor package")])
    elif group == "LB":
        flexes.extend([("ILB", primary_exp, primary_pot, 0, "Off-ball LB flexibility"), ("OLB", max(3, primary_exp - 2), max(4, primary_pot - 1), 0, "Limited outside fit")])
    elif group == "CB":
        flexes.extend([("NB", max(4, primary_exp - 1), primary_pot, 0, "Nickel/package flexibility"), ("FS", max(2, primary_exp - 3), max(3, primary_pot - 2), 0, "Emergency safety fit")])
    elif group == "S":
        flexes.extend([("FS", primary_exp, primary_pot, 0, "Safety flexibility"), ("SS", primary_exp, primary_pot, 0, "Safety flexibility"), ("NB", max(3, primary_exp - 2), max(4, primary_pot - 1), 0, "Big nickel fit")])
    elif group == "TE":
        flexes.append(("FB", max(2, primary_exp - 3), max(3, primary_pot - 2), 0, "Emergency H-back/fullback package"))
    elif group == "RB":
        flexes.append(("FB", max(2, primary_exp - 4), max(3, primary_pot - 3), 0, "Emergency backfield package"))

    for position, exp, pot, notes in SPECIAL_FLEX_PROFILES.get(candidate.key, []):
        flexes.append((position, exp, pot, 0, notes))

    seen: set[str] = set()
    for position, exp, pot, primary, notes in flexes:
        if position in seen:
            continue
        seen.add(position)
        con.execute(
            """
            INSERT INTO player_position_flex (
                player_id, position, experience, potential, is_primary, source, notes
            )
            VALUES (?, ?, ?, ?, ?, 'free_agent_pool_2026', ?)
            """,
            (player_id, position, exp, pot, primary, notes),
        )


def import_free_agent_stats(
    con: sqlite3.Connection, player_ids: list[int], seasons: range
) -> int:
    gsis_to_player_id: dict[str, int] = {}
    for player_id, gsis_id in con.execute(
        """
        SELECT player_id, gsis_id
        FROM player_external_ids
        WHERE player_id IN ({})
          AND gsis_id IS NOT NULL
        """.format(",".join("?" for _ in player_ids)),
        player_ids,
    ):
        gsis_to_player_id[str(gsis_id)] = int(player_id)

    if not gsis_to_player_id:
        return 0

    imported = 0
    for season in seasons:
        source_url = STATS_URL_TEMPLATE.format(season=season)
        reader = get_csv_reader(source_url)
        for row in reader:
            player_id = gsis_to_player_id.get(row.get("player_id") or "")
            if not player_id:
                continue
            external = con.execute(
                """
                SELECT gsis_id, pfr_id
                FROM player_external_ids
                WHERE player_id = ?
                """,
                (player_id,),
            ).fetchone()
            external_dict = {
                "gsis_id": external[0] if external else None,
                "pfr_id": external[1] if external else None,
            }
            upsert_stat_row(
                con,
                player_id,
                int(row.get("season") or season),
                row_stat_values(row),
                external_dict,
                source_url,
            )
            imported += 1
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the live free-agent pool.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--dry-run", action="store_true", help="Show selected counts without modifying the DB.")
    parser.add_argument("--skip-stats", action="store_true", help="Do not import yearly stats for free agents.")
    parser.add_argument("--seasons", default="1999-2025", help="Season range for stats import. Default: 1999-2025.")
    args = parser.parse_args()

    if "-" in args.seasons:
        start, end = [int(part) for part in args.seasons.split("-", 1)]
        seasons = range(start, end + 1)
    else:
        year = int(args.seasons)
        seasons = range(year, year + 1)

    candidates = add_manual_candidates(parse_nfltr_candidates())
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        ensure_free_agent_schema(con)
        ensure_sim_rating_schema(con)
        selected, skipped = select_candidates(con, candidates)

        print("Selected free agents by group:")
        for group in GROUP_ORDER:
            count = sum(1 for candidate in selected if candidate.group == group)
            print(f"  {group}: {count}")
        print(f"Total selected: {len(selected)}")
        print(f"Skipped already rostered in DB: {sum(1 for _, reason in skipped if reason == 'already_on_roster_in_db')}")

        if args.dry_run:
            return 0

        players_by_name = load_nflverse_players()
        actions = {"inserted": 0, "updated": 0}
        player_ids: list[int] = []
        rank_by_group = {group: 0 for group in GROUP_ORDER}
        unmatched_external: list[str] = []

        for candidate in selected:
            rank_by_group[candidate.group] += 1
            row = choose_nflverse_row(candidate, players_by_name)
            if row is None:
                unmatched_external.append(candidate.name)
            player_id, action = upsert_free_agent(
                con, candidate, row, rank_by_group[candidate.group]
            )
            actions[action] += 1
            player_ids.append(player_id)

        stats_rows = 0
        if not args.skip_stats and player_ids:
            stats_rows = import_free_agent_stats(con, player_ids, seasons)

        con.commit()

    print(f"Inserted: {actions['inserted']}")
    print(f"Updated: {actions['updated']}")
    print(f"Stats rows imported/updated: {stats_rows}")
    if unmatched_external:
        print("No nflverse external ID match for:")
        print(", ".join(unmatched_external[:40]))
        if len(unmatched_external) > 40:
            print(f"...and {len(unmatched_external) - 40} more")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
