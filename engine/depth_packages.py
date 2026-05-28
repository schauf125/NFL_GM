"""Shared depth-chart package definitions.

The saved depth chart is still one table, but these helpers decide which slots
are actually part of a team's game-day offense/defense based on scheme.
"""

from __future__ import annotations

import sqlite3
from typing import Any


OFFENSE_PACKAGE_ORDER = ["11", "12", "21", "10", "13"]
DEFENSE_PACKAGE_ORDER = ["nickel", "base34", "base43"]

DEFENSE_PACKAGE_PREFIXES = {
    "nickel": "NICKEL",
    "base34": "BASE34",
    "base43": "BASE43",
}

OFFENSE_PACKAGE_SNAP_SLOTS = {
    "10": ["LWR", "RWR", "SWR", "SWR"],
    "11": ["TE", "LWR", "RWR", "SWR"],
    "12": ["TE", "TE", "LWR", "RWR"],
    "13": ["TE", "TE", "TE", "LWR"],
    "21": ["TE", "FB", "LWR", "RWR"],
}

DEFENSE_PACKAGE_BASE_SNAP_SLOTS = {
    "nickel": ["LEDGE", "LDL", "RDL", "REDGE", "MLB", "WLB", "LCB", "RCB", "NB", "FS", "SS"],
    "base34": ["LEDGE", "LDL", "NT", "RDL", "REDGE", "MLB", "WLB", "LCB", "RCB", "FS", "SS"],
    "base43": ["LEDGE", "LDL", "RDL", "REDGE", "WLB", "MLB", "SLB", "LCB", "RCB", "FS", "SS"],
}


def canonical_slot(slot: str | None) -> str:
    key = str(slot or "").upper()
    for prefix in DEFENSE_PACKAGE_PREFIXES.values():
        marker = f"{prefix}_"
        if key.startswith(marker):
            return key[len(marker):]
    return key


def package_depth_slot(package: str | None, slot: str | None) -> str:
    key = canonical_slot(slot)
    prefix = DEFENSE_PACKAGE_PREFIXES.get(str(package or "").lower())
    return f"{prefix}_{key}" if prefix else key


DEFENSE_PACKAGE_SNAP_SLOTS = {
    package: [package_depth_slot(package, slot) for slot in slots]
    for package, slots in DEFENSE_PACKAGE_BASE_SNAP_SLOTS.items()
}


def legacy_fallback_slots(slots: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    fallbacks: list[str] = []
    for slot in slots:
        canonical = canonical_slot(slot)
        if canonical != slot and canonical not in fallbacks:
            fallbacks.append(canonical)
    return fallbacks

SPECIAL_TEAMS_SLOTS = ["PK", "KO", "PT", "P", "LS", "KR", "PR", "H"]

TEAM_PACKAGE_OVERRIDES = {
    # Rough nickel-heavy Vikings defensive profile: keep base 3-4 available,
    # but let nickel drive most snaps until a richer playbook layer exists.
    "MIN": {
        # Kevin O'Connell's offense should keep the slot WR meaningfully involved
        # even when the user keeps the same two wideouts atop the boundary slots.
        "offense": {
            "11": 0.74,
            "12": 0.22,
            "21": 0.04,
        },
        "defense": {
            "nickel": 0.82,
            "base34": 0.18,
        },
    },
}


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


def _ordered_packages(packages: list[str], order: list[str]) -> list[str]:
    package_set = {str(package) for package in packages if package}
    return [package for package in order if package in package_set]


def _normalize_shares(
    packages: list[str],
    shares: dict[str, float],
    *,
    order: list[str],
    fallback: dict[str, float] | None = None,
) -> dict[str, float]:
    ordered = _ordered_packages(packages, order)
    if not ordered:
        return {}
    raw = {package: max(0.0, float(shares.get(package, 0.0) or 0.0)) for package in ordered}
    if sum(raw.values()) <= 0 and fallback:
        raw = {package: max(0.0, float(fallback.get(package, 0.0) or 0.0)) for package in ordered}
    total = sum(raw.values())
    if total <= 0:
        even = 1.0 / len(ordered)
        return {package: even for package in ordered}
    return {package: round(raw[package] / total, 4) for package in ordered}


def default_offense_package_shares(
    packages: list[str],
    scheme_key: str | None = None,
    personnel: str | None = None,
) -> dict[str, float]:
    ordered = _ordered_packages(packages, OFFENSE_PACKAGE_ORDER)
    text = f"{scheme_key or ''} {personnel or ''}".lower()
    weights: dict[str, float] = {}
    for package in ordered:
        weight = 1.0
        if package == "11":
            weight *= 1.35
        if package == "12":
            weight *= 1.05
        if package == "21":
            weight *= 0.72
        if package in {"10", "11"} and ("spread" in text or "air" in text):
            weight *= 1.45
        if package in {"12", "13", "21"} and ("heavy" in text or "power" in text or "gap" in text):
            weight *= 1.45
        weights[package] = weight
    return _normalize_shares(ordered, weights, order=OFFENSE_PACKAGE_ORDER)


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


def default_defense_package_shares(
    packages: list[str],
    scheme_key: str | None = None,
    personnel: str | None = None,
) -> dict[str, float]:
    ordered = _ordered_packages(packages, DEFENSE_PACKAGE_ORDER)
    if not ordered:
        return {}
    if ordered == ["nickel"]:
        return {"nickel": 1.0}
    text = f"{scheme_key or ''} {personnel or ''}".lower()
    weights: dict[str, float] = {}
    for package in ordered:
        if package == "nickel":
            weights[package] = 0.72
        elif package == "base34":
            weights[package] = 0.28 if "3-4" in text or "odd_front" in text else 0.18
        elif package == "base43":
            weights[package] = 0.28 if "4-3" in text or "four_man" in text or "tampa2" in text else 0.18
    return _normalize_shares(ordered, weights, order=DEFENSE_PACKAGE_ORDER)


def _default_from_shares(shares: dict[str, float], fallback: str) -> str:
    if not shares:
        return fallback
    return max(shares.items(), key=lambda item: item[1])[0]


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
        offense_packages = ["11", "12"]
        defense_packages = ["nickel"]
        offense_shares = default_offense_package_shares(offense_packages)
        defense_shares = default_defense_package_shares(defense_packages)
        return {
            "offensePackages": ["11", "12"],
            "defensePackages": ["nickel"],
            "offensePackageShares": offense_shares,
            "defensePackageShares": defense_shares,
            "defaultOffensePackage": _default_from_shares(offense_shares, "11"),
            "defaultDefensePackage": _default_from_shares(defense_shares, "nickel"),
        }
    get = row.get if isinstance(row, dict) else lambda key, default=None: row[key] if key in row.keys() else default
    offense_key = get("offense_scheme_key", "")
    offense_personnel = get("offense_personnel", "")
    defense_key = get("defense_scheme_key", "")
    defense_personnel = get("defense_personnel", "")
    offense_packages = infer_offense_packages(offense_key, offense_personnel)
    defense_packages = infer_defense_packages(defense_key, defense_personnel)
    offense_shares = default_offense_package_shares(offense_packages, offense_key, offense_personnel)
    defense_shares = default_defense_package_shares(defense_packages, defense_key, defense_personnel)
    return {
        "offenseSchemeKey": offense_key,
        "offenseScheme": get("offense_scheme", ""),
        "offensePersonnel": offense_personnel,
        "defenseSchemeKey": defense_key,
        "defenseScheme": get("defense_scheme", ""),
        "defensePersonnel": defense_personnel,
        "offensePackages": offense_packages,
        "defensePackages": defense_packages,
        "offensePackageShares": offense_shares,
        "defensePackageShares": defense_shares,
        "defaultOffensePackage": _default_from_shares(
            offense_shares,
            default_offense_package(offense_key, offense_personnel),
        ),
        "defaultDefensePackage": _default_from_shares(
            defense_shares,
            default_defense_package(defense_key, defense_personnel),
        ),
    }


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def _team_abbr_from_row(row: Any | None) -> str:
    if not row:
        return ""
    if isinstance(row, dict):
        return str(row.get("team") or row.get("abbreviation") or "").upper()
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    for key in ("team", "abbreviation"):
        if key in keys:
            return str(row[key] or "").upper()
    return ""


def team_package_profile_from_db(
    con: sqlite3.Connection | None,
    team_id: int | None,
    season: int | None,
    row: Any | None = None,
    *,
    team_abbr: str | None = None,
) -> dict[str, Any]:
    profile = scheme_packages_from_row(row)
    abbr = str(team_abbr or _team_abbr_from_row(row) or "").upper()
    preference_rows: list[Any] = []
    if con is not None and team_id is not None and season is not None and _table_exists(con, "team_depth_package_preferences"):
        preference_rows = con.execute(
            """
            SELECT side, package_key, snap_share, is_default
            FROM team_depth_package_preferences
            WHERE team_id = ?
              AND season = ?
              AND COALESCE(is_visible, 1) = 1
            ORDER BY side, is_default DESC, snap_share DESC, package_key
            """,
            (int(team_id), int(season)),
        ).fetchall()

    for side, order, package_key, share_key, default_key in (
        ("offense", OFFENSE_PACKAGE_ORDER, "offensePackages", "offensePackageShares", "defaultOffensePackage"),
        ("defense", DEFENSE_PACKAGE_ORDER, "defensePackages", "defensePackageShares", "defaultDefensePackage"),
    ):
        db_shares: dict[str, float] = {}
        default_package = ""
        for pref in preference_rows:
            pref_side = str(pref["side"] if not isinstance(pref, dict) else pref.get("side", "")).lower()
            if pref_side != side:
                continue
            package = str(pref["package_key"] if not isinstance(pref, dict) else pref.get("package_key", ""))
            if package not in order:
                continue
            db_shares[package] = float(pref["snap_share"] if not isinstance(pref, dict) else pref.get("snap_share", 0.0) or 0.0)
            is_default = int(pref["is_default"] if not isinstance(pref, dict) else pref.get("is_default", 0) or 0)
            if is_default:
                default_package = package
        if not db_shares and abbr in TEAM_PACKAGE_OVERRIDES:
            db_shares = dict(TEAM_PACKAGE_OVERRIDES[abbr].get(side, {}))
        if db_shares:
            packages = _ordered_packages(list(db_shares), order)
            shares = _normalize_shares(packages, db_shares, order=order)
            if packages:
                profile[package_key] = packages
                profile[share_key] = shares
                profile[default_key] = default_package if default_package in packages else _default_from_shares(shares, packages[0])
    return profile
