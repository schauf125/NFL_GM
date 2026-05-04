#!/usr/bin/env python3
"""Hidden personality traits for draft prospects.

This mirrors the player personality system, but traits attach to draft_prospects
instead of players. These values are hidden flavor/scouting data for future draft
interviews, development, contract behavior, and AI GM logic.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SOURCE = "draft_personality_generator"
MAX_TRAITS_PER_PROSPECT = 3
TRAIT_COUNT_WEIGHTS = {
    0: 51,
    1: 35,
    2: 11,
    3: 3,
}

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.schema import ensure_schema as ensure_draft_schema

import player_personalities


BASE_TRAIT_WEIGHTS = {
    "lunch_pail": 0.070,
    "quiet_professional": 0.070,
    "chip_on_shoulder": 0.060,
    "film_junkie": 0.050,
    "natural_leader": 0.040,
    "jokester": 0.040,
    "streaky_confidence": 0.040,
    "mentor": 0.020,
    "big_stage": 0.030,
    "media_savvy": 0.030,
    "coach_connector": 0.030,
    "hometown_pull": 0.020,
    "ring_chaser": 0.010,
    "greedy": 0.030,
    "locker_room_distraction": 0.015,
    "off_field_issue": 0.010,
}

RARE_TRAITS = {"locker_room_distraction", "off_field_issue"}
SKILL_POSITIONS = {"WR", "CB", "NB", "RB"}
LEADERSHIP_POSITIONS = {"QB", "C", "ILB", "FS", "SS"}
SMALL_SCHOOL_TIERS = {"Small", "International"}
NEGATIVE_TRAITS = {"greedy", "locker_room_distraction", "off_field_issue"}
TRAIT_HARD_CONFLICTS = {
    "natural_leader": {"locker_room_distraction", "off_field_issue"},
    "mentor": {"locker_room_distraction", "off_field_issue", "streaky_confidence"},
    "quiet_professional": {"locker_room_distraction", "off_field_issue", "jokester"},
    "lunch_pail": {"off_field_issue"},
    "coach_connector": {"locker_room_distraction", "off_field_issue"},
    "locker_room_distraction": {"natural_leader", "mentor", "quiet_professional", "coach_connector"},
    "off_field_issue": {"natural_leader", "mentor", "quiet_professional", "lunch_pail", "coach_connector"},
    "jokester": {"quiet_professional"},
    "streaky_confidence": {"mentor"},
}
TRAIT_COMPATIBILITY_BOOSTS = {
    "natural_leader": {"film_junkie": 1.35, "big_stage": 1.25, "coach_connector": 1.35, "media_savvy": 1.15},
    "film_junkie": {"natural_leader": 1.25, "quiet_professional": 1.25, "coach_connector": 1.20},
    "lunch_pail": {"chip_on_shoulder": 1.30, "quiet_professional": 1.20},
    "chip_on_shoulder": {"lunch_pail": 1.25, "big_stage": 1.15, "streaky_confidence": 1.15},
    "media_savvy": {"big_stage": 1.25, "jokester": 1.15, "greedy": 1.10},
    "big_stage": {"media_savvy": 1.25, "natural_leader": 1.15, "greedy": 1.10},
    "greedy": {"media_savvy": 1.10, "big_stage": 1.10},
}
TRAIT_SOFT_CONFLICT_MULTIPLIERS = {
    "greedy": {"lunch_pail": 0.70, "quiet_professional": 0.75, "coach_connector": 0.75},
    "ring_chaser": {"chip_on_shoulder": 0.80},
    "streaky_confidence": {"quiet_professional": 0.72, "film_junkie": 0.82},
    "jokester": {"film_junkie": 0.85},
}


@dataclass(frozen=True)
class ProspectContext:
    row: sqlite3.Row
    ratings: dict[str, int]

    @property
    def prospect_id(self) -> int:
        return int(self.row["prospect_id"])

    @property
    def position(self) -> str:
        return str(self.row["position"] or "").upper()

    @property
    def age(self) -> int:
        return _int(self.row["age"], 22)

    @property
    def rank(self) -> int:
        return _int(self.row["scouting_rank"], 999)

    @property
    def true_grade(self) -> int:
        return _int(self.row["true_grade"], _int(self.row["overall"], 50))

    @property
    def ceiling_grade(self) -> int:
        return _int(self.row["ceiling_grade"], _int(self.row["potential"], self.true_grade + 5))

    @property
    def college_tier(self) -> str:
        return str(self.row["college_tier"] or "")

    @property
    def position_group(self) -> str:
        return str(self.row["position_group"] or "")

    def rating(self, key: str, default: int | None = None) -> int:
        if default is None:
            default = self.true_grade
        return int(self.ratings.get(key, default))

    def awareness_score(self) -> float:
        return _avg(
            [
                self.rating("play_recognition"),
                self.rating("processing_speed"),
                self.rating("discipline"),
                self.rating("composure"),
                self.rating("consistency"),
            ]
        )

    def work_score(self) -> float:
        return _avg(
            [
                self.rating("discipline"),
                self.rating("consistency"),
                self.rating("stamina"),
                self.rating("durability"),
            ]
        )

    def leadership_score(self) -> float:
        position_bonus = 4 if self.position in LEADERSHIP_POSITIONS else 0
        age_bonus = 3 if self.age >= 23 else 0
        return _avg(
            [
                self.rating("composure"),
                self.rating("discipline"),
                self.rating("play_recognition"),
                self.true_grade,
            ]
        ) + position_bonus + age_bonus

    def raw_projection(self) -> bool:
        return self.age <= 21 and (self.true_grade <= 58 or self.ceiling_grade - self.true_grade >= 12)

    def small_school_or_overlooked(self) -> bool:
        return self.college_tier in SMALL_SCHOOL_TIERS or self.rank >= 160


@dataclass(frozen=True)
class TraitAssignment:
    prospect_id: int
    trait_key: str
    intensity: int
    assignment_type: str
    notes: str


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_personality_schema(con: sqlite3.Connection) -> None:
    player_personalities.ensure_schema(con)
    player_personalities.seed_master_data(con)
    ensure_draft_schema(con)


def draft_class(con: sqlite3.Connection, draft_year: int) -> sqlite3.Row:
    row = con.execute(
        """
        SELECT *
        FROM draft_classes
        WHERE draft_year = ?
        """,
        (draft_year,),
    ).fetchone()
    if not row:
        raise ValueError(f"No draft class found for {draft_year}. Create/populate the draft class first.")
    return row


def load_prospects(con: sqlite3.Connection, draft_class_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            dp.*
        FROM draft_prospects dp
        WHERE dp.draft_class_id = ?
        ORDER BY
            CASE WHEN dp.scouting_rank IS NULL THEN 1 ELSE 0 END,
            dp.scouting_rank,
            dp.prospect_id
        """,
        (draft_class_id,),
    ).fetchall()


def load_ratings(con: sqlite3.Connection, prospect_ids: list[int]) -> dict[int, dict[str, int]]:
    if not prospect_ids:
        return {}
    placeholders = ",".join("?" for _ in prospect_ids)
    rows = con.execute(
        f"""
        SELECT prospect_id, rating_key, rating_value
        FROM draft_prospect_ratings
        WHERE prospect_id IN ({placeholders})
        """,
        prospect_ids,
    ).fetchall()
    ratings: dict[int, dict[str, int]] = {}
    for row in rows:
        ratings.setdefault(int(row["prospect_id"]), {})[str(row["rating_key"])] = int(row["rating_value"])
    return ratings


def load_trait_definitions(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        str(row["trait_key"]): row
        for row in con.execute("SELECT * FROM personality_trait_definitions ORDER BY trait_key")
    }


def existing_run(con: sqlite3.Connection, draft_class_id: int) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM draft_class_personality_runs
        WHERE draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchone()


def target_trait_count(rng: random.Random) -> int:
    counts = list(TRAIT_COUNT_WEIGHTS)
    weights = [TRAIT_COUNT_WEIGHTS[count] for count in counts]
    return int(rng.choices(counts, weights=weights, k=1)[0])


def adjusted_trait_weights(context: ProspectContext) -> dict[str, tuple[float, list[str]]]:
    weights: dict[str, tuple[float, list[str]]] = {}
    leadership = context.leadership_score()
    awareness = context.awareness_score()
    work = context.work_score()
    small_or_late = context.small_school_or_overlooked()
    position = context.position

    for trait_key, base_weight in BASE_TRAIT_WEIGHTS.items():
        weight = base_weight
        reasons: list[str] = []

        if trait_key in {"natural_leader", "mentor", "film_junkie", "quiet_professional"}:
            if leadership >= 72 or awareness >= 72:
                weight *= 1.8
                reasons.append("captain/high-awareness logic")
            elif leadership >= 66 or awareness >= 66:
                weight *= 1.35
                reasons.append("leadership/awareness bump")

        if trait_key == "lunch_pail" and work >= 70:
            weight *= 1.8
            reasons.append("work-ethic/high-motor logic")
        elif trait_key == "lunch_pail" and work >= 64:
            weight *= 1.35
            reasons.append("work-ethic bump")

        if trait_key == "chip_on_shoulder" and small_or_late:
            weight *= 1.9
            reasons.append("small-school/late-board logic")

        if position == "QB" and trait_key in {
            "natural_leader",
            "film_junkie",
            "media_savvy",
            "big_stage",
            "streaky_confidence",
        }:
            weight *= 1.55
            reasons.append("QB adjustment")

        if position in SKILL_POSITIONS and trait_key in {
            "big_stage",
            "media_savvy",
            "jokester",
            "greedy",
        }:
            weight *= 1.35
            reasons.append("skill-position adjustment")

        if context.age >= 24 and trait_key in {"mentor", "quiet_professional"}:
            weight *= 1.7
            reasons.append("older-prospect adjustment")

        if context.raw_projection() and trait_key in {"streaky_confidence", "chip_on_shoulder"}:
            weight *= 1.6
            reasons.append("young/raw adjustment")

        if context.rank <= 96 and trait_key in {"natural_leader", "media_savvy", "big_stage"}:
            weight *= 1.2
            reasons.append("high-profile prospect adjustment")

        if trait_key == "ring_chaser":
            weight *= 0.75
            reasons.append("rookie ring-chaser dampened")

        if trait_key in RARE_TRAITS:
            weight = min(weight, BASE_TRAIT_WEIGHTS[trait_key] * 1.35)

        weights[trait_key] = (max(0.0005, weight), reasons)
    return weights


def choose_traits(
    rng: random.Random,
    context: ProspectContext,
    traits: dict[str, sqlite3.Row],
    count: int,
) -> list[TraitAssignment]:
    weights = adjusted_trait_weights(context)
    available = [key for key in weights if key in traits]
    assignments: list[TraitAssignment] = []
    rare_selected = False

    for _ in range(min(count, MAX_TRAITS_PER_PROSPECT)):
        available = _compatible_available(available, [assignment.trait_key for assignment in assignments])
        if not available:
            break
        selected_trait_keys = [assignment.trait_key for assignment in assignments]
        values = [
            _compatibility_adjusted_weight(key, weights[key][0], selected_trait_keys)
            for key in available
        ]
        trait_key = rng.choices(available, weights=values, k=1)[0]
        if trait_key in RARE_TRAITS and rare_selected:
            available.remove(trait_key)
            continue
        available.remove(trait_key)
        if trait_key in RARE_TRAITS:
            rare_selected = True
        trait = traits[trait_key]
        reasons = list(weights[trait_key][1])
        compatibility_reasons = _compatibility_reasons(trait_key, selected_trait_keys)
        reasons.extend(compatibility_reasons)
        assignment_type = assignment_type_for_reasons(reasons)
        assignments.append(
            TraitAssignment(
                prospect_id=context.prospect_id,
                trait_key=trait_key,
                intensity=random_intensity(rng, trait, context, reasons),
                assignment_type=assignment_type,
                notes=notes_for_assignment(reasons),
            )
        )
    return assignments


def _compatible_available(available: list[str], selected_traits: list[str]) -> list[str]:
    return [
        trait_key
        for trait_key in available
        if not _has_hard_conflict(trait_key, selected_traits)
    ]


def _has_hard_conflict(trait_key: str, selected_traits: list[str]) -> bool:
    conflicts = TRAIT_HARD_CONFLICTS.get(trait_key, set())
    return any(
        selected in conflicts or trait_key in TRAIT_HARD_CONFLICTS.get(selected, set())
        for selected in selected_traits
    )


def _compatibility_adjusted_weight(
    trait_key: str,
    base_weight: float,
    selected_traits: list[str],
) -> float:
    weight = base_weight
    for selected in selected_traits:
        weight *= TRAIT_COMPATIBILITY_BOOSTS.get(selected, {}).get(trait_key, 1.0)
        weight *= TRAIT_COMPATIBILITY_BOOSTS.get(trait_key, {}).get(selected, 1.0)
        weight *= TRAIT_SOFT_CONFLICT_MULTIPLIERS.get(selected, {}).get(trait_key, 1.0)
        weight *= TRAIT_SOFT_CONFLICT_MULTIPLIERS.get(trait_key, {}).get(selected, 1.0)
    return max(0.0001, weight)


def _compatibility_reasons(trait_key: str, selected_traits: list[str]) -> list[str]:
    reasons: list[str] = []
    for selected in selected_traits:
        if (
            TRAIT_COMPATIBILITY_BOOSTS.get(selected, {}).get(trait_key)
            or TRAIT_COMPATIBILITY_BOOSTS.get(trait_key, {}).get(selected)
        ):
            reasons.append(f"compatible with {selected}")
        if (
            TRAIT_SOFT_CONFLICT_MULTIPLIERS.get(selected, {}).get(trait_key)
            or TRAIT_SOFT_CONFLICT_MULTIPLIERS.get(trait_key, {}).get(selected)
        ):
            reasons.append(f"soft conflict with {selected}")
    return reasons


def assignment_type_for_reasons(reasons: list[str]) -> str:
    joined = "; ".join(reasons)
    if "compatible with" in joined:
        return "compatibility_logic"
    if "soft conflict with" in joined:
        return "adjusted_random"
    if "captain/high-awareness logic" in joined or "leadership/awareness bump" in joined:
        return "captain_logic"
    if "work-ethic" in joined:
        return "work_ethic_logic"
    if "small-school/late-board" in joined:
        return "small_school_logic"
    if "QB adjustment" in joined or "skill-position adjustment" in joined:
        return "position_logic"
    if "older-prospect" in joined or "young/raw" in joined:
        return "age_logic"
    if reasons:
        return "adjusted_random"
    return "random"


def notes_for_assignment(reasons: list[str]) -> str:
    if not reasons:
        return "Generated randomly from draft prospect personality weights."
    return "Generated with adjustment: " + "; ".join(reasons) + "."


def random_intensity(
    rng: random.Random,
    trait: sqlite3.Row,
    context: ProspectContext,
    reasons: list[str],
) -> int:
    low = int(trait["min_intensity"])
    high = int(trait["max_intensity"])
    midpoint = (low + high) / 2
    if reasons:
        midpoint += 4
    if trait["trait_key"] in {"natural_leader", "film_junkie", "lunch_pail", "quiet_professional"}:
        midpoint += max(0, context.awareness_score() - 65) * 0.18
    if trait["trait_key"] in {"locker_room_distraction", "off_field_issue"}:
        midpoint -= 3
    value = rng.gauss(midpoint, max(2.5, (high - low) / 6))
    return max(low, min(high, int(round(value))))


def generate_assignments(
    prospects: list[sqlite3.Row],
    ratings_by_prospect: dict[int, dict[str, int]],
    traits: dict[str, sqlite3.Row],
    *,
    seed: str,
    draft_year: int,
) -> list[TraitAssignment]:
    rng = random.Random(f"{seed}:draft-personalities:{draft_year}")
    assignments: list[TraitAssignment] = []
    for prospect in prospects:
        context = ProspectContext(
            row=prospect,
            ratings=ratings_by_prospect.get(int(prospect["prospect_id"]), {}),
        )
        count = target_trait_count(rng)
        assignments.extend(choose_traits(rng, context, traits, count))
    return assignments


def trait_count_distribution(prospects: list[sqlite3.Row], assignments: list[TraitAssignment]) -> Counter[int]:
    counts_by_prospect = Counter(assignment.prospect_id for assignment in assignments)
    distribution: Counter[int] = Counter()
    for prospect in prospects:
        distribution[min(MAX_TRAITS_PER_PROSPECT, counts_by_prospect.get(int(prospect["prospect_id"]), 0))] += 1
    return distribution


def apply_assignments(
    con: sqlite3.Connection,
    *,
    draft_class_row: sqlite3.Row,
    prospects: list[sqlite3.Row],
    assignments: list[TraitAssignment],
    seed: str,
    notes: str | None,
    force: bool,
) -> int:
    draft_class_id = int(draft_class_row["draft_class_id"])
    run = existing_run(con, draft_class_id)
    if run and not force:
        raise ValueError(
            f"Draft personality run already exists for {draft_class_row['draft_year']} "
            f"(run_id={run['run_id']}). Use --force to replace it."
        )
    if force:
        con.execute(
            """
            DELETE FROM draft_prospect_personalities
            WHERE prospect_id IN (
                SELECT prospect_id FROM draft_prospects WHERE draft_class_id = ?
            )
            """,
            (draft_class_id,),
        )
        con.execute(
            "DELETE FROM draft_class_personality_runs WHERE draft_class_id = ?",
            (draft_class_id,),
        )

    distribution = trait_count_distribution(prospects, assignments)
    cur = con.execute(
        """
        INSERT INTO draft_class_personality_runs (
            draft_class_id,
            draft_year,
            rng_seed,
            prospect_count,
            zero_trait_count,
            one_trait_count,
            two_trait_count,
            three_trait_count,
            total_assignment_count,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_class_id,
            int(draft_class_row["draft_year"]),
            seed,
            len(prospects),
            distribution[0],
            distribution[1],
            distribution[2],
            distribution[3],
            len(assignments),
            notes,
        ),
    )
    run_id = int(cur.lastrowid)
    con.executemany(
        """
        INSERT INTO draft_prospect_personalities (
            prospect_id,
            trait_key,
            intensity,
            assignment_type,
            hidden,
            source,
            notes,
            run_id
        )
        VALUES (?, ?, ?, ?, 1, ?, ?, ?)
        """,
        [
            (
                assignment.prospect_id,
                assignment.trait_key,
                assignment.intensity,
                assignment.assignment_type,
                SOURCE,
                assignment.notes,
                run_id,
            )
            for assignment in assignments
        ],
    )
    return run_id


def build_generation_result(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    seed: str,
) -> tuple[sqlite3.Row, list[sqlite3.Row], list[TraitAssignment]]:
    ensure_personality_schema(con)
    class_row = draft_class(con, draft_year)
    prospects = load_prospects(con, int(class_row["draft_class_id"]))
    if not prospects:
        raise ValueError(f"Draft class {draft_year} has no prospects yet.")
    ratings = load_ratings(con, [int(row["prospect_id"]) for row in prospects])
    traits = load_trait_definitions(con)
    assignments = generate_assignments(prospects, ratings, traits, seed=seed, draft_year=draft_year)
    return class_row, prospects, assignments


def print_generation_summary(
    prospects: list[sqlite3.Row],
    assignments: list[TraitAssignment],
    traits_by_key: dict[str, sqlite3.Row],
    *,
    dry_run: bool,
    run_id: int | None = None,
) -> None:
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Prospects considered: {len(prospects)}")
    print(f"Total hidden traits: {len(assignments)}")
    distribution = trait_count_distribution(prospects, assignments)
    for count in range(4):
        pct = distribution[count] / len(prospects) * 100 if prospects else 0
        print(f"{count} traits: {distribution[count]} ({pct:.1f}%)")
    if run_id is not None:
        print(f"Draft personality run id: {run_id}")
    print("")
    print("Trait counts")
    for trait_key, count in Counter(a.trait_key for a in assignments).most_common():
        trait = traits_by_key[trait_key]
        print(f"{trait['display_name']:<24} {count}")
    print("")
    print("Assignment types")
    for assignment_type, count in Counter(a.assignment_type for a in assignments).most_common():
        print(f"{assignment_type:<24} {count}")


def action_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_personality_schema(con)
        con.commit()
    print(f"Draft personality schema ready: {args.db}")
    print(f"Trait definitions available: {len(player_personalities.TRAITS)}")


def action_apply(args: argparse.Namespace) -> None:
    seed = str(args.seed if args.seed is not None else args.draft_year)
    with connect(args.db) as con:
        class_row, prospects, assignments = build_generation_result(
            con,
            draft_year=args.draft_year,
            seed=seed,
        )
        traits_by_key = load_trait_definitions(con)
        run = existing_run(con, int(class_row["draft_class_id"]))
        if run and not args.force and args.apply:
            raise ValueError(
                f"Draft personality run already exists for {args.draft_year} "
                f"(run_id={run['run_id']}). Use --force to replace it."
            )
        run_id = None
        if args.apply:
            run_id = apply_assignments(
                con,
                draft_class_row=class_row,
                prospects=prospects,
                assignments=assignments,
                seed=seed,
                notes=args.notes,
                force=args.force,
            )
            con.commit()
    print(f"Draft year: {args.draft_year}")
    print(f"Seed: {seed}")
    print_generation_summary(
        prospects,
        assignments,
        traits_by_key,
        dry_run=not args.apply,
        run_id=run_id,
    )


def action_summary(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_personality_schema(con)
        class_row = draft_class(con, args.draft_year)
        run = existing_run(con, int(class_row["draft_class_id"]))
        prospects = load_prospects(con, int(class_row["draft_class_id"]))
        if not run:
            print(f"No draft personality run found for {args.draft_year}.")
            print(f"Prospects in class: {len(prospects)}")
            return
        rows = con.execute(
            """
            SELECT
                ptd.display_name,
                ptd.polarity,
                COUNT(dpp.prospect_id) AS prospect_count
            FROM personality_trait_definitions ptd
            LEFT JOIN draft_prospect_personalities dpp
              ON dpp.trait_key = ptd.trait_key
             AND dpp.prospect_id IN (
                SELECT prospect_id FROM draft_prospects WHERE draft_class_id = ?
             )
            GROUP BY ptd.trait_key
            ORDER BY prospect_count DESC, ptd.display_name
            """,
            (int(class_row["draft_class_id"]),),
        ).fetchall()
    print(f"Draft year: {args.draft_year}")
    print(f"Run id: {run['run_id']}")
    print(f"Seed: {run['rng_seed']}")
    print(f"Prospects: {run['prospect_count']}")
    print(f"Traits: {run['total_assignment_count']}")
    print(
        f"Trait distribution: 0={run['zero_trait_count']}, 1={run['one_trait_count']}, "
        f"2={run['two_trait_count']}, 3={run['three_trait_count']}"
    )
    print("")
    for row in rows:
        print(f"{row['display_name']:<24} {row['polarity']:<8} {row['prospect_count']}")


def action_show(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_personality_schema(con)
        filters = ["draft_year = ?"]
        params: list[Any] = [args.draft_year]
        if args.prospect:
            filters.append("lower(prospect_name) LIKE ?")
            params.append(f"%{args.prospect.lower()}%")
        if args.position:
            filters.append("position = ?")
            params.append(args.position.upper())
        if args.trait:
            filters.append("(trait_key = ? OR lower(display_name) = lower(?))")
            params.extend([args.trait, args.trait])
        rows = con.execute(
            f"""
            SELECT *
            FROM draft_prospect_personalities_view
            WHERE {' AND '.join(filters)}
            ORDER BY
                CASE WHEN scouting_rank IS NULL THEN 1 ELSE 0 END,
                scouting_rank,
                prospect_name,
                display_name
            LIMIT ?
            """,
            (*params, args.limit),
        ).fetchall()
    if not rows:
        print("No draft personality traits found.")
        return
    for row in rows:
        print(
            f"{row['scouting_rank'] or '-':>3} {row['prospect_name']:<24} {row['position']:<4} "
            f"{row['display_name']:<24} {row['intensity']:>3} {row['assignment_type']:<18} "
            f"{row['notes'] or ''}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create draft personality tables and seed trait definitions.")
    setup_parser.set_defaults(func=action_setup)

    apply_parser = subparsers.add_parser("apply", help="Generate hidden draft-prospect personalities.")
    apply_parser.add_argument("--draft-year", type=int, required=True)
    apply_parser.add_argument("--seed", default=None)
    apply_parser.add_argument("--apply", action="store_true", help="Persist the traits. Omit for dry run.")
    apply_parser.add_argument("--force", action="store_true", help="Replace an existing run for this draft class.")
    apply_parser.add_argument("--notes", default="Generated hidden draft prospect personalities.")
    apply_parser.set_defaults(func=action_apply)

    summary_parser = subparsers.add_parser("summary", help="Summarize hidden personalities for a draft class.")
    summary_parser.add_argument("--draft-year", type=int, required=True)
    summary_parser.set_defaults(func=action_summary)

    show_parser = subparsers.add_parser("show", help="Show hidden traits for debugging.")
    show_parser.add_argument("--draft-year", type=int, required=True)
    show_parser.add_argument("--prospect")
    show_parser.add_argument("--position")
    show_parser.add_argument("--trait")
    show_parser.add_argument("--limit", type=int, default=50)
    show_parser.set_defaults(func=action_show)

    return parser


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _avg(values: list[int] | list[float]) -> float:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else 0.0


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
