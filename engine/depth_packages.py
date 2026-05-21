"""Shared depth-chart package definitions.

The saved depth chart is still one table, but these helpers decide which slots
are actually part of a team's game-day offense/defense based on scheme.
"""

from __future__ import annotations

from typing import Any


OFFENSE_PACKAGE_ORDER = ["11", "12", "21", "10", "13"]
DEFENSE_PACKAGE_ORDER = ["nickel", "base34", "base43"]

OFFENSE_PACKAGE_SNAP_SLOTS = {
    "10": ["LWR", "RWR", "SWR", "SWR"],
    "11": ["TE", "LWR", "RWR", "SWR"],
    "12": ["TE", "TE", "LWR", "RWR"],
    "13": ["TE", "TE", "TE", "LWR"],
    "21": ["TE", "FB", "LWR", "RWR"],
}

DEFENSE_PACKAGE_SNAP_SLOTS = {
    "nickel": ["LEDGE", "LDL", "RDL", "REDGE", "MLB", "WLB", "LCB", "RCB", "NB", "FS", "SS"],
    "base34": ["LEDGE", "LDL", "NT", "RDL", "REDGE", "MLB", "WLB", "LCB", "RCB", "FS", "SS"],
    "base43": ["LEDGE", "LDL", "RDL", "REDGE", "WLB", "MLB", "SLB", "LCB", "RCB", "FS", "SS"],
}

SPECIAL_TEAMS_SLOTS = ["PK", "KO", "PT", "P", "LS", "KR", "PR", "H"]


def _tokens(value: str | None) -> set[str]:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in str(value or "").lower())
    return {token for token in cleaned.split() if token}


def infer_offense_packages(scheme_key: str | None = None, personnel: str | None = None) -> list[str]:
    text = f"{scheme_key or ''} {personnel or ''}".lower()
    tokens = _tokens(text)
    packages = [pkg for pkg in OFFENSE_PACKAGE_ORDER if pkg in tokens]
    if not packages:
        if "heavy" in text:
            packages = ["12", "13", "21"]
        elif "power" in text or "gap" in text:
            packages = ["12", "21", "11"]
        elif "spread" in text or "air" in text:
            packages = ["11", "10"]
        else:
            packages = ["11", "12"]
    return [pkg for pkg in OFFENSE_PACKAGE_ORDER if pkg in packages]


def default_offense_package(scheme_key: str | None = None, personnel: str | None = None) -> str:
    packages = infer_offense_packages(scheme_key, personnel)
    text = f"{scheme_key or ''} {personnel or ''}".lower()
    if "heavy" in text or "power" in text or "gap" in text:
        for preferred in ("12", "21", "13", "11"):
            if preferred in packages:
                return preferred
    if "spread" in text or "air" in text:
        for preferred in ("11", "10", "12"):
            if preferred in packages:
                return preferred
    return packages[0] if packages else "11"


def infer_defense_packages(scheme_key: str | None = None, personnel: str | None = None) -> list[str]:
    text = f"{scheme_key or ''} {personnel or ''}".lower()
    if "3-4" in text or "three_four" in text or "odd_front" in text:
        packages = ["base34", "nickel"]
    elif "4-3" in text or "four_man" in text or "tampa2" in text:
        packages = ["base43", "nickel"]
    elif "multiple" in text and "nickel" not in text:
        packages = ["nickel", "base34", "base43"]
    else:
        packages = ["nickel"]
    return [pkg for pkg in DEFENSE_PACKAGE_ORDER if pkg in packages]


def default_defense_package(scheme_key: str | None = None, personnel: str | None = None) -> str:
    packages = infer_defense_packages(scheme_key, personnel)
    text = f"{scheme_key or ''} {personnel or ''}".lower()
    if "3-4" in text or "three_four" in text or "odd_front" in text:
        return "base34" if "base34" in packages else packages[0]
    if "4-3" in text or "four_man" in text or "tampa2" in text:
        return "base43" if "base43" in packages else packages[0]
    return packages[0] if packages else "nickel"


def active_offense_slots(packages: list[str]) -> list[str]:
    slots: list[str] = ["QB", "RB", "LT", "LG", "C", "RG", "RT"]
    for package in packages:
        for slot in OFFENSE_PACKAGE_SNAP_SLOTS.get(package, []):
            if slot not in slots:
                slots.append(slot)
    return slots


def active_defense_slots(packages: list[str]) -> list[str]:
    slots: list[str] = []
    for package in packages:
        for slot in DEFENSE_PACKAGE_SNAP_SLOTS.get(package, []):
            if slot not in slots:
                slots.append(slot)
    return slots


def active_depth_slots(
    offense_packages: list[str],
    defense_packages: list[str],
    *,
    include_special: bool = True,
) -> list[str]:
    slots = active_offense_slots(offense_packages) + active_defense_slots(defense_packages)
    if include_special:
        slots += SPECIAL_TEAMS_SLOTS
    deduped: list[str] = []
    for slot in slots:
        if slot not in deduped:
            deduped.append(slot)
    return deduped


def scheme_packages_from_row(row: Any | None) -> dict[str, Any]:
    if not row:
        return {
            "offensePackages": ["11", "12"],
            "defensePackages": ["nickel"],
            "defaultOffensePackage": "11",
            "defaultDefensePackage": "nickel",
        }
    get = row.get if isinstance(row, dict) else lambda key, default=None: row[key] if key in row.keys() else default
    offense_key = get("offense_scheme_key", "")
    offense_personnel = get("offense_personnel", "")
    defense_key = get("defense_scheme_key", "")
    defense_personnel = get("defense_personnel", "")
    offense_packages = infer_offense_packages(offense_key, offense_personnel)
    defense_packages = infer_defense_packages(defense_key, defense_personnel)
    return {
        "offenseSchemeKey": offense_key,
        "offenseScheme": get("offense_scheme", ""),
        "offensePersonnel": offense_personnel,
        "defenseSchemeKey": defense_key,
        "defenseScheme": get("defense_scheme", ""),
        "defensePersonnel": defense_personnel,
        "offensePackages": offense_packages,
        "defensePackages": defense_packages,
        "defaultOffensePackage": default_offense_package(offense_key, offense_personnel),
        "defaultDefensePackage": default_defense_package(defense_key, defense_personnel),
    }
