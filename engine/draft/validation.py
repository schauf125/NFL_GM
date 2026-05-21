"""Validation reports for generated draft-class previews."""

from __future__ import annotations

import csv
import ast
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .college import (
    BLUE_BLOOD_DRAFT_PIPELINE_COLLEGES,
    ELITE_DRAFT_PIPELINE_COLLEGES,
    STRONG_DRAFT_PIPELINE_COLLEGES,
)
from .names import NameGenerator, normalize_name_key


AGE_RANGES = (
    ("Round 1", 1, 32),
    ("Rounds 2-3", 33, 96),
    ("Rounds 4-5", 97, 160),
    ("Rounds 6-7", 161, 256),
    ("Leftovers", 257, 9999),
)


def read_preview_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_preview_report(rows: list[Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_preview_report(rows), encoding="utf-8")


def build_preview_report(rows: list[Any]) -> str:
    data = [_row_dict(row) for row in rows]
    if not data:
        return "No prospects found.\n"

    lines: list[str] = []
    lines.append("Draft Class Preview Validation")
    lines.append("=" * 30)
    lines.append(f"Prospects: {len(data)}")
    versions = Counter(str(row.get("generation_version", "")) for row in data)
    if any(versions):
        lines.append(f"Generation version: {_format_counter(versions, len(data), 3)}")

    lines.append("")
    lines.append("Age Buckets")
    for label, start_rank, end_rank in AGE_RANGES:
        subset = [
            row
            for row in data
            if start_rank <= _int(row.get("rank")) <= end_rank
        ]
        if subset:
            lines.append(f"{label}: {_format_counter(_age_bucket_counts(subset), len(subset), 4)}")

    lines.append("")
    lines.append("Position Counts")
    lines.extend(_counter_lines(Counter(row.get("position", "") for row in data), len(data)))

    lines.append("")
    lines.append("Position Group Counts")
    lines.extend(_counter_lines(Counter(row.get("position_group", "") for row in data), len(data)))

    if any(str(row.get("true_grade", "")).strip() for row in data):
        lines.append("")
        lines.append("Normalized Sim-Rating Summary")
        lines.append(f"Dev traits: {_format_counter(Counter(row.get('dev_trait', '') for row in data), len(data), 5)}")
        lines.append(f"Risk levels: {_format_counter(Counter(row.get('risk_level', '') for row in data), len(data), 3)}")
        for label, start_rank, end_rank in AGE_RANGES:
            subset = [
                row
                for row in data
                if start_rank <= _int(row.get("rank")) <= end_rank
            ]
            if subset:
                true_grades = [_int(row.get("true_grade")) for row in subset]
                ceiling_grades = [_int(row.get("ceiling_grade")) for row in subset]
                star_plus = sum(
                    str(row.get("dev_trait", "")) in {"Star", "Superstar", "X-Factor"}
                    for row in subset
                )
                lines.append(
                    f"{label}: true {_avg(true_grades):.1f}, ceiling {_avg(ceiling_grades):.1f}, "
                    f"Star+ {star_plus} ({star_plus / len(subset) * 100:.1f}%)"
                )
        top_96 = [row for row in data if _int(row.get("rank")) <= 96]
        round_1 = [row for row in data if _int(row.get("rank")) <= 32]
        if round_1:
            high_risk_round_1 = sum(row.get("risk_level") == "High" for row in round_1)
            low_true_round_1 = sum(_int(row.get("true_grade")) <= 62 for row in round_1)
            lines.append(
                f"Round-1 high-risk prospects: {high_risk_round_1} "
                f"({high_risk_round_1 / len(round_1) * 100:.1f}%)"
            )
            lines.append(
                f"Round-1 true grade 62 or lower: {low_true_round_1} "
                f"({low_true_round_1 / len(round_1) * 100:.1f}%)"
            )
        if top_96:
            high_risk_top_96 = sum(row.get("risk_level") == "High" for row in top_96)
            lines.append(
                f"Top-96 high-risk prospects: {high_risk_top_96} "
                f"({high_risk_top_96 / len(top_96) * 100:.1f}%)"
            )
        lines.append("Primary roles")
        lines.extend(_counter_lines(Counter(row.get("primary_role", "") for row in data), len(data), limit=12))

    if any(str(row.get("archetype_identity_status", "")).strip() for row in data):
        lines.append("")
        lines.append("Archetype Identity QA")
        lines.append(
            f"Status: {_format_counter(Counter(row.get('archetype_identity_status', '') for row in data), len(data), 5)}"
        )
        relabeled = sum(row.get("archetype_identity_status") == "Relabeled" for row in data)
        illusions = sum(row.get("archetype_identity_status") == "Illusion" for row in data)
        thin = sum(row.get("archetype_identity_status") == "Thin" for row in data)
        lines.append(f"Relabeled identity mismatches: {relabeled} ({relabeled / len(data) * 100:.1f}%)")
        lines.append(f"Allowed illusion prospects: {illusions} ({illusions / len(data) * 100:.1f}%)")
        lines.append(f"Thin-but-kept identities: {thin} ({thin / len(data) * 100:.1f}%)")
        if illusions > 3:
            lines.append(f"WARNING: illusion prospect budget exceeded ({illusions} > 3).")

    if any(str(row.get("true_rank", "")).strip() for row in data):
        lines.append("")
        lines.append("Public Board Noise")
        status_counts = Counter(row.get("public_board_status", "") for row in data if row.get("public_board_status"))
        discovery_counts = Counter(row.get("discovery_status", "") for row in data if row.get("discovery_status"))
        if status_counts:
            lines.append(f"Public-board status: {_format_counter(status_counts, len(data), 4)}")
        if discovery_counts:
            lines.append(f"Discovery status: {_format_counter(discovery_counts, len(data), 4)}")
        rank_misses = [
            abs(_int(row.get("rank")) - _int(row.get("true_rank")))
            for row in data
            if _int(row.get("rank")) and _int(row.get("true_rank"))
        ]
        if rank_misses:
            lines.append(f"Average public-vs-true rank gap: {_avg(rank_misses):.1f}")
            lines.append(f"Rank gap 50+: {sum(value >= 50 for value in rank_misses)}")
            lines.append(f"Rank gap 100+: {sum(value >= 100 for value in rank_misses)}")
        round_1 = [row for row in data if _int(row.get("rank")) <= 32]
        if round_1:
            true_round_1 = sum(_int(row.get("true_rank")) <= 32 for row in round_1)
            lines.append(
                f"Public Round 1 also true top-32: {true_round_1} "
                f"({true_round_1 / len(round_1) * 100:.1f}%)"
            )

    if any(str(row.get("combine_status", "")).strip() for row in data):
        lines.append("")
        lines.append("Combine Summary")
        lines.append(f"Status: {_format_counter(Counter(row.get('combine_status', '') for row in data), len(data), 6)}")
        lines.append(f"Workout variance: {_format_counter(Counter(row.get('workout_variance', '') for row in data), len(data), 4)}")
        combine_grades = [_int(row.get("combine_grade")) for row in data if _has_value(row.get("combine_grade"))]
        if combine_grades:
            lines.append(f"Average combine grade: {_avg(combine_grades):.1f}")
        full_skips = sum(_int(row.get("drills_completed")) == 0 for row in data)
        injured = _count_true(data, "combine_injured")
        top_skips = _count_true(data, "combine_top_skip")
        lines.append(f"No workout drills: {full_skips} ({full_skips / len(data) * 100:.1f}%)")
        lines.append(f"Injury-limited or DNP: {injured} ({injured / len(data) * 100:.1f}%)")
        lines.append(f"Strategic top/pro-day skips: {top_skips} ({top_skips / len(data) * 100:.1f}%)")
        for label, start_rank, end_rank in AGE_RANGES:
            subset = [
                row
                for row in data
                if start_rank <= _int(row.get("rank")) <= end_rank
            ]
            grades = [_int(row.get("combine_grade")) for row in subset if _has_value(row.get("combine_grade"))]
            if subset and grades:
                skipped = sum(_int(row.get("drills_completed")) == 0 for row in subset)
                lines.append(
                    f"{label}: combine {_avg(grades):.1f}, no drills {skipped} "
                    f"({skipped / len(subset) * 100:.1f}%)"
                )

    if any(str(row.get("pro_day_status", "")).strip() for row in data):
        lines.append("")
        lines.append("Pro Day Summary")
        lines.append(f"Status: {_format_counter(Counter(row.get('pro_day_status', '') for row in data), len(data), 6)}")
        lines.append(
            f"Workout variance: {_format_counter(Counter(row.get('pro_day_workout_variance', '') for row in data), len(data), 4)}"
        )
        pro_day_grades = [_int(row.get("pro_day_grade")) for row in data if _has_value(row.get("pro_day_grade"))]
        if pro_day_grades:
            lines.append(f"Average pro-day grade: {_avg(pro_day_grades):.1f}")
        improvers = _count_true(data, "pro_day_improved_from_combine")
        medical = _count_true(data, "pro_day_medical_recheck")
        lines.append(f"Pro-day improvers: {improvers} ({improvers / len(data) * 100:.1f}%)")
        lines.append(f"Medical rechecks: {medical} ({medical / len(data) * 100:.1f}%)")
        for label, start_rank, end_rank in AGE_RANGES:
            subset = [
                row
                for row in data
                if start_rank <= _int(row.get("rank")) <= end_rank
            ]
            grades = [_int(row.get("pro_day_grade")) for row in subset if _has_value(row.get("pro_day_grade"))]
            if subset and grades:
                lines.append(
                    f"{label}: pro day {_avg(grades):.1f}, participated {len(grades)} "
                    f"({len(grades) / len(subset) * 100:.1f}%)"
                )

    if any(str(row.get("scout_grade", "")).strip() for row in data):
        lines.append("")
        lines.append("Scouting Report Lens")
        lines.append(f"Scout lenses: {_format_counter(Counter(row.get('scout_lens', '') for row in data), len(data), 6)}")
        lines.append(f"Scout confidence: {_format_counter(Counter(row.get('scout_confidence', '') for row in data), len(data), 3)}")
        lines.append(f"Scout risk: {_format_counter(Counter(row.get('scout_risk', '') for row in data), len(data), 3)}")
        has_true_grades = any(str(row.get("true_grade", "")).strip() for row in data)
        for label, start_rank, end_rank in AGE_RANGES:
            subset = [
                row
                for row in data
                if start_rank <= _int(row.get("rank")) <= end_rank
            ]
            if subset:
                scout_grades = [_int(row.get("scout_grade")) for row in subset]
                if has_true_grades:
                    true_grades = [_int(row.get("true_grade")) for row in subset]
                    misses = [
                        abs(_int(row.get("true_grade")) - _int(row.get("scout_grade")))
                        for row in subset
                    ]
                    lines.append(
                        f"{label}: scout grade {_avg(scout_grades):.1f} vs true {_avg(true_grades):.1f}, "
                        f"avg miss {_avg(misses):.1f}"
                    )
                else:
                    lines.append(f"{label}: scout grade {_avg(scout_grades):.1f}")

    lines.append("")
    lines.append("Ethnicity Keys")
    lines.extend(_counter_lines(Counter(row.get("ethnicity_key", "") for row in data), len(data)))

    lines.append("")
    lines.append("Name / Appearance QA")
    name_qa = _name_appearance_qa(data)
    lines.extend(name_qa["lines"])
    for warning in name_qa["warnings"]:
        lines.append(f"WARNING: {warning}")

    lines.append("")
    lines.append("Countries")
    lines.extend(_counter_lines(Counter(row.get("birth_country", "") for row in data), len(data), limit=12))

    lines.append("")
    lines.append("College / Talent Pipeline QA")
    lines.extend(_college_pipeline_qa(data))

    lines.append("")
    lines.append("Position Ethnicity Snapshot")
    for position, subset in _group_rows(data, "position").items():
        counts = Counter(row.get("ethnicity_key", "") for row in subset)
        lines.append(f"{position}: {_format_counter(counts, len(subset), 5)}")

    lines.append("")
    lines.append("Physical Averages By Position Group")
    for group, subset in _group_rows(data, "position_group").items():
        heights = [_int(row.get("height_in")) for row in subset]
        weights = [_int(row.get("weight_lbs")) for row in subset]
        hands = [_float(row.get("hand_size_in")) for row in subset]
        arms = [_float(row.get("arm_length_in")) for row in subset]
        lines.append(
            f"{group}: ht {_avg(heights):.1f} in, wt {_avg(weights):.1f} lb, "
            f"arm {_avg(arms):.2f}, hand {_avg(hands):.2f}"
        )

    lines.append("")
    lines.append("Body / Attribute QA")
    body_flags = _body_attribute_flags(data)
    if body_flags:
        lines.extend(body_flags[:16])
        if len(body_flags) > 16:
            lines.append(f"... {len(body_flags) - 16} more body/attribute flag(s).")
    else:
        lines.append("No major body/attribute contradiction flags.")

    lines.append("")
    lines.append("Appearance")
    lines.append(
        f"Two-ethnicity: {_count_truthy(data, 'secondary_ethnicity')} "
        f"({_count_truthy(data, 'secondary_ethnicity') / len(data) * 100:.1f}%)"
    )
    lines.append(
        f"Hairstyle outliers: {_count_true(data, 'hairstyle_outlier')} "
        f"({_count_true(data, 'hairstyle_outlier') / len(data) * 100:.1f}%)"
    )
    lines.append(
        f"Facial-hair outliers: {_count_true(data, 'facial_hair_outlier')} "
        f"({_count_true(data, 'facial_hair_outlier') / len(data) * 100:.1f}%)"
    )
    lines.append(
        f"Any facial hair: {_style_count(data, {'Clean shaven'}, invert=True)} "
        f"({_style_count(data, {'Clean shaven'}, invert=True) / len(data) * 100:.1f}%)"
    )
    beard_styles = {"Short beard", "Goatee", "Full beard", "Chinstrap", "Patchy beard"}
    lines.append(
        f"Beard styles excluding stubble: {_style_count(data, beard_styles)} "
        f"({_style_count(data, beard_styles) / len(data) * 100:.1f}%)"
    )
    lines.append("Facial hair by age")
    for label, subset in _age_groups(data).items():
        any_facial_hair = _style_count(subset, {"Clean shaven"}, invert=True)
        beard_count = _style_count(subset, beard_styles)
        lines.append(
            f"{label}: any {any_facial_hair / len(subset) * 100:.1f}%, "
            f"beard no stubble {beard_count / len(subset) * 100:.1f}%"
        )
    lines.append(f"Eye color: {_format_counter(Counter(row.get('eye_color', '') for row in data), len(data), 6)}")
    lines.append(f"Hair color: {_format_counter(Counter(row.get('hair_color', '') for row in data), len(data), 8)}")
    lines.append(f"Hair: {_format_counter(Counter(row.get('hairstyle', '') for row in data), len(data), 8)}")
    lines.append(
        f"Facial hair: {_format_counter(Counter(row.get('facial_hair', '') for row in data), len(data), 8)}"
    )

    lines.append("")
    lines.append("Handedness")
    lines.extend(_counter_lines(Counter(row.get("handedness", "") for row in data), len(data)))
    return "\n".join(lines) + "\n"


def _name_appearance_qa(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    warnings: list[str] = []
    lines: list[str] = []
    try:
        generator = NameGenerator(seed="validation-name-style-lookup")
        style_lookup = generator.component_style_lookup
    except Exception as exc:
        return {
            "lines": [f"Name style lookup unavailable: {exc}"],
            "warnings": [],
        }

    black_coded = {"african_american", "west_african", "caribbean"}
    culturally_flagged_white: list[str] = []
    culturally_flagged_white_primary: list[str] = []
    international_mismatches: list[str] = []
    repeated_rare = Counter[str]()
    hyphenated_last = 0
    suffix_count = 0
    for row in rows:
        first = str(row.get("first_name") or "")
        last = str(row.get("last_name") or "")
        styles = style_lookup.get(("first", normalize_name_key(first)), set())
        full_name = str(row.get("full_name") or f"{first} {last}").strip()
        ethnicity_key = str(row.get("ethnicity_key") or "")
        primary = str(row.get("primary_ethnicity") or "")
        country = str(row.get("birth_country") or "")
        if ethnicity_key == "white" and styles & black_coded:
            culturally_flagged_white.append(f"{full_name} ({', '.join(sorted(styles & black_coded))})")
        if primary == "White" and styles & black_coded:
            culturally_flagged_white_primary.append(f"{full_name} ({ethnicity_key}; {', '.join(sorted(styles & black_coded))})")
        if country and country != "United States":
            origin_key = str(row.get("origin_ethnicity_key") or "")
            if origin_key and ethnicity_key and origin_key != ethnicity_key:
                international_mismatches.append(f"{full_name}: {country} maps {origin_key}, row {ethnicity_key}")
        if any(marker in str(row.get("name_background_note") or "").lower() for marker in ("distinctive", "nickname")):
            repeated_rare[first] += 1
        if "-" in last:
            hyphenated_last += 1
        if any(last.endswith(f" {suffix}") for suffix in ("Jr.", "II", "III", "IV")):
            suffix_count += 1

    lines.append(f"White ethnicity with strongly Black-style first name: {len(culturally_flagged_white)}")
    lines.append(f"White-primary appearance with strongly Black-style first name: {len(culturally_flagged_white_primary)}")
    lines.append(f"International country/ethnicity mismatches: {len(international_mismatches)}")
    lines.append(f"Hyphenated last names: {hyphenated_last} ({hyphenated_last / len(rows) * 100:.1f}%)")
    lines.append(f"Family suffixes: {suffix_count} ({suffix_count / len(rows) * 100:.1f}%)")
    repeated = [f"{name} x{count}" for name, count in repeated_rare.items() if count > 1]
    lines.append(f"Repeated rare/nickname first names: {', '.join(repeated[:8]) if repeated else 'none'}")
    if culturally_flagged_white:
        warnings.append("White ethnicity/name-culture mismatch: " + "; ".join(culturally_flagged_white[:6]))
    if culturally_flagged_white_primary:
        warnings.append("White-primary appearance/name-culture mismatch: " + "; ".join(culturally_flagged_white_primary[:6]))
    if len(international_mismatches) > 2:
        warnings.append("Too many international country/name ethnicity mismatches: " + "; ".join(international_mismatches[:6]))
    if hyphenated_last > max(4, int(len(rows) * 0.025)):
        warnings.append(f"Hyphenated last names are high ({hyphenated_last}).")
    if suffix_count > max(28, int(len(rows) * 0.095)):
        warnings.append(f"Family suffixes are high ({suffix_count}).")
    if repeated:
        warnings.append("Rare/nickname first names repeated: " + ", ".join(repeated[:8]))
    return {"lines": lines, "warnings": warnings}


def _college_pipeline_qa(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    top_50 = [row for row in rows if 1 <= _int(row.get("rank")) <= 50]
    top_100 = [row for row in rows if 1 <= _int(row.get("rank")) <= 100]
    first_four_rounds = [row for row in rows if 1 <= _int(row.get("rank")) <= 140]
    if top_50:
        tiers = Counter(str(row.get("college_tier") or "") for row in top_50)
        powerish = tiers.get("Power", 0)
        blue_blood = [
            row
            for row in top_50
            if str(row.get("college") or "") in BLUE_BLOOD_DRAFT_PIPELINE_COLLEGES
        ]
        elite_pipeline = [
            row
            for row in top_50
            if str(row.get("college") or "") in ELITE_DRAFT_PIPELINE_COLLEGES
        ]
        strong_plus = [
            row
            for row in top_50
            if str(row.get("college") or "") in ELITE_DRAFT_PIPELINE_COLLEGES
            or str(row.get("college") or "") in STRONG_DRAFT_PIPELINE_COLLEGES
        ]
        lines.append(f"Top-50 college tiers: {_format_counter(tiers, len(top_50), 6)}")
        lines.append(f"Top-50 Power share: {powerish}/{len(top_50)} ({powerish / len(top_50) * 100:.1f}%)")
        lines.append(
            f"Top-50 blue-blood share: {len(blue_blood)}/{len(top_50)} "
            f"({len(blue_blood) / len(top_50) * 100:.1f}%)"
        )
        lines.append(
            f"Top-50 elite pipeline share: {len(elite_pipeline)}/{len(top_50)} "
            f"({len(elite_pipeline) / len(top_50) * 100:.1f}%)"
        )
        lines.append(
            f"Top-50 elite/strong pipeline share: {len(strong_plus)}/{len(top_50)} "
            f"({len(strong_plus) / len(top_50) * 100:.1f}%)"
        )
        small_top_50 = [row for row in top_50 if str(row.get("college_tier") or "") in {"Small", "International"}]
        if small_top_50:
            lines.append("Top-50 small/international outliers: " + "; ".join(_player_label(row) for row in small_top_50[:8]))
        if powerish / len(top_50) < 0.86:
            lines.append("WARNING: Top-50 Power-school share is lower than target.")
        if len(blue_blood) / len(top_50) < 0.45:
            lines.append("WARNING: Top-50 blue-blood share is lower than target.")
        if len(small_top_50) > 2:
            lines.append("WARNING: Too many top-50 small/international prospects.")
    if top_100:
        lines.append(f"Top-100 schools: {_format_counter(Counter(str(row.get('college') or '') for row in top_100), len(top_100), 12)}")
    if first_four_rounds:
        first_four_tiers = Counter(str(row.get("college_tier") or "") for row in first_four_rounds)
        first_four_power = first_four_tiers.get("Power", 0)
        first_four_small = [
            row
            for row in first_four_rounds
            if str(row.get("college_tier") or "") in {"Small", "International"}
        ]
        lines.append(
            f"Rounds 1-4 Power share: {first_four_power}/{len(first_four_rounds)} "
            f"({first_four_power / len(first_four_rounds) * 100:.1f}%)"
        )
        lines.append(
            f"Rounds 1-4 small/international: {len(first_four_small)}/{len(first_four_rounds)} "
            f"({len(first_four_small) / len(first_four_rounds) * 100:.1f}%)"
        )
        if first_four_power / len(first_four_rounds) < 0.74:
            lines.append("WARNING: Rounds 1-4 Power-school share is lower than target.")
        if len(first_four_small) > 12:
            lines.append("WARNING: Rounds 1-4 has too many small/international prospects.")
    senior_top_50 = [
        row
        for row in top_50
        if _int(row.get("age")) >= 23
    ]
    if top_50:
        lines.append(
            f"Top-50 older prospects age 23+: {len(senior_top_50)} "
            f"({len(senior_top_50) / len(top_50) * 100:.1f}%)"
        )
        if len(senior_top_50) > 14:
            lines.append("WARNING: Top of class is heavy on older/senior prospects.")
    return lines


def _body_attribute_flags(rows: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    for row in rows:
        ratings = _ratings_dict(row.get("ratings"))
        if not ratings:
            continue
        position = str(row.get("position") or "").upper()
        weight = _int(row.get("weight_lbs"))
        height = _int(row.get("height_in"))
        strength = _rating(ratings, "strength")
        speed = _rating(ratings, "speed")
        agility = _rating(ratings, "agility")
        hands = _rating(ratings, "hands")
        hand_size = _float(row.get("hand_size_in"))
        if position in {"WR", "CB", "NB", "FS"} and weight >= 222 and speed >= 90 and agility >= 88:
            flags.append(f"{_player_label(row)}: big {position} still has elite speed/agility ({weight} lb, SPD {speed}, AGI {agility}).")
        if position in {"OT", "OG", "C", "IDL"} and weight < 292 and strength >= 86:
            flags.append(f"{_player_label(row)}: light trench body with elite strength ({weight} lb, STR {strength}).")
        if position in {"IDL", "OT", "OG"} and height >= 78 and weight >= 320 and strength < 62:
            flags.append(f"{_player_label(row)}: huge trench body with low strength ({height} in/{weight} lb, STR {strength}).")
        if position in {"WR", "TE"} and hand_size >= 10.25 and hands <= 55:
            flags.append(f"{_player_label(row)}: large hands but poor hands rating ({hand_size:.2f}, hands {hands}).")
        if position in {"RB", "FB"} and weight >= 230 and _rating(ratings, "contact_power") <= 56:
            flags.append(f"{_player_label(row)}: big back with low contact power ({weight} lb).")
    return flags


def _ratings_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _rating(ratings: dict[str, Any], key: str) -> int:
    try:
        return int(ratings.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _player_label(row: dict[str, Any]) -> str:
    rank = _int(row.get("rank"))
    name = str(row.get("full_name") or f"{row.get('first_name', '')} {row.get('last_name', '')}").strip()
    position = str(row.get("position") or "")
    college = str(row.get("college") or "")
    return f"#{rank} {name}, {position}, {college}"


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if is_dataclass(row):
        return asdict(row)
    raise TypeError(f"Unsupported preview row type: {type(row)!r}")


def _age_bucket_counts(rows: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        age = _int(row.get("age"))
        if age <= 21:
            counts["20-21"] += 1
        elif age == 22:
            counts["22"] += 1
        elif age == 23:
            counts["23"] += 1
        else:
            counts["24+"] += 1
    return counts


def _age_groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {"20-21": [], "22": [], "23": [], "24+": []}
    for row in rows:
        age = _int(row.get("age"))
        if age <= 21:
            grouped["20-21"].append(row)
        elif age == 22:
            grouped["22"].append(row)
        elif age == 23:
            grouped["23"].append(row)
        else:
            grouped["24+"].append(row)
    return {key: value for key, value in grouped.items() if value}


def _counter_lines(counter: Counter[str], total: int, limit: int | None = None) -> list[str]:
    return [
        f"{key or '(blank)'}: {count} ({count / total * 100:.1f}%)"
        for key, count in counter.most_common(limit)
    ]


def _format_counter(counter: Counter[str], total: int, limit: int) -> str:
    return ", ".join(
        f"{key or '(blank)'} {count} ({count / total * 100:.1f}%)"
        for key, count in counter.most_common(limit)
    )


def _group_rows(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field, ""))].append(row)
    return dict(sorted(grouped.items()))


def _count_true(rows: list[dict[str, Any]], field: str) -> int:
    return sum(str(row.get(field, "")).lower() in {"1", "true", "yes"} for row in rows)


def _count_truthy(rows: list[dict[str, Any]], field: str) -> int:
    return sum(_has_value(row.get(field, "")) for row in rows)


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() not in {"", "None"}


def _style_count(
    rows: list[dict[str, Any]],
    styles: set[str],
    *,
    invert: bool = False,
) -> int:
    if invert:
        return sum(str(row.get("facial_hair", "")) not in styles for row in rows)
    return sum(str(row.get("facial_hair", "")) in styles for row in rows)


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _avg(values: list[int] | list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
