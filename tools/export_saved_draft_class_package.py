#!/usr/bin/env python3
"""Export a persisted draft class into a reusable saved-class package."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_PACKAGE_ROOT = Path(r"Z:\NFL_GM_SIM_MISC_Files\Saved Draft Classes")
SAVED_CLASS_SCHEMA_VERSION = 4
SAVED_CLASS_MIN_IMPORT_SCHEMA_VERSION = 3
GENERATOR_COMPATIBILITY_VERSION = "2026-05-name-appearance-qc"

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.class_preview import (  # noqa: E402
    DraftClassPreviewRow,
    format_height,
    format_measurement,
    write_csv,
    write_html,
)
from engine.draft.schema import ensure_schema  # noqa: E402
from engine.draft.validation import write_preview_report  # noqa: E402


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    while "__" in clean:
        clean = clean.replace("__", "_")
    return clean.strip("_") or "saved_draft_class"


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def active_draft_year(con: sqlite3.Connection) -> int:
    row = con.execute(
        """
        SELECT draft_year
        FROM draft_classes
        ORDER BY draft_year DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise ValueError("No draft class exists in this database.")
    return int(row["draft_year"])


def load_class(con: sqlite3.Connection, draft_year: int) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM draft_classes WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()
    if not row:
        raise ValueError(f"No draft class found for {draft_year}.")
    return row


def load_rating_maps(con: sqlite3.Connection, draft_class_id: int) -> tuple[dict[int, dict[str, int]], dict[int, dict[str, str]]]:
    ratings: dict[int, dict[str, int]] = {}
    confidence: dict[int, dict[str, str]] = {}
    if not table_exists(con, "draft_prospect_ratings"):
        return ratings, confidence
    rows = con.execute(
        """
        SELECT dpr.prospect_id, dpr.rating_key, dpr.rating_value, dpr.confidence
        FROM draft_prospect_ratings dpr
        JOIN draft_prospects dp ON dp.prospect_id = dpr.prospect_id
        WHERE dp.draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchall()
    for row in rows:
        prospect_id = int(row["prospect_id"])
        ratings.setdefault(prospect_id, {})[str(row["rating_key"])] = int(row["rating_value"])
        confidence.setdefault(prospect_id, {})[str(row["rating_key"])] = str(row["confidence"] or "")
    return ratings, confidence


def load_role_scores(con: sqlite3.Connection, draft_class_id: int) -> dict[int, dict[str, float]]:
    scores: dict[int, dict[str, float]] = {}
    if not table_exists(con, "draft_prospect_role_scores"):
        return scores
    rows = con.execute(
        """
        SELECT dprs.prospect_id, dprs.role_key, dprs.role_score
        FROM draft_prospect_role_scores dprs
        JOIN draft_prospects dp ON dp.prospect_id = dprs.prospect_id
        WHERE dp.draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchall()
    for row in rows:
        scores.setdefault(int(row["prospect_id"]), {})[str(row["role_key"])] = float(row["role_score"])
    return scores


def load_special_teams_flex(con: sqlite3.Connection, draft_class_id: int) -> dict[int, dict[str, dict[str, Any]]]:
    flex: dict[int, dict[str, dict[str, Any]]] = {}
    if not table_exists(con, "draft_prospect_special_teams_flex"):
        return flex
    rows = con.execute(
        """
        SELECT st.prospect_id, st.role_key, st.experience, st.potential, st.notes
        FROM draft_prospect_special_teams_flex st
        JOIN draft_prospects dp ON dp.prospect_id = st.prospect_id
        WHERE dp.draft_class_id = ?
        ORDER BY st.prospect_id, st.role_key
        """,
        (draft_class_id,),
    ).fetchall()
    for row in rows:
        prospect_id = int(row["prospect_id"])
        role_key = str(row["role_key"] or "").upper()
        flex.setdefault(prospect_id, {})[role_key] = {
            "role": role_key,
            "current": int(row["experience"] or 0),
            "potential": int(row["potential"] or 0),
            "notes": row["notes"] or "",
        }
    return flex


def load_single_row_map(con: sqlite3.Connection, table: str, draft_class_id: int, order_by: str = "") -> dict[int, sqlite3.Row]:
    if not table_exists(con, table):
        return {}
    suffix = f" ORDER BY {order_by}" if order_by else ""
    rows = con.execute(
        f"""
        SELECT child.*
        FROM {table} child
        JOIN draft_prospects dp ON dp.prospect_id = child.prospect_id
        WHERE dp.draft_class_id = ?
        {suffix}
        """,
        (draft_class_id,),
    ).fetchall()
    result: dict[int, sqlite3.Row] = {}
    for row in rows:
        result.setdefault(int(row["prospect_id"]), row)
    return result


def top_and_weak_ratings(ratings: dict[str, int]) -> tuple[str, str]:
    if not ratings:
        return "", ""
    sorted_items = sorted(ratings.items(), key=lambda item: (-int(item[1]), item[0]))
    top = ", ".join(f"{key} {value}" for key, value in sorted_items[:5])
    weak = ", ".join(f"{key} {value}" for key, value in sorted(ratings.items(), key=lambda item: (int(item[1]), item[0]))[:4])
    return top, weak


def draft_rows(con: sqlite3.Connection, draft_class: sqlite3.Row) -> list[DraftClassPreviewRow]:
    draft_class_id = int(draft_class["draft_class_id"])
    ratings_by_prospect, _confidence_by_prospect = load_rating_maps(con, draft_class_id)
    role_scores_by_prospect = load_role_scores(con, draft_class_id)
    special_teams_flex_by_prospect = load_special_teams_flex(con, draft_class_id)
    combine_by_prospect = load_single_row_map(con, "draft_prospect_combine_results", draft_class_id)
    pro_day_by_prospect = load_single_row_map(con, "draft_prospect_pro_day_results", draft_class_id)
    private_by_prospect = load_single_row_map(
        con,
        "draft_prospect_private_workouts",
        draft_class_id,
        order_by="COALESCE(hidden, 0) DESC, workout_id",
    )
    prospects = con.execute(
        """
        SELECT *
        FROM draft_prospects
        WHERE draft_class_id = ?
        ORDER BY COALESCE(public_board_rank, scouting_rank, true_rank, prospect_id), prospect_id
        """,
        (draft_class_id,),
    ).fetchall()
    rows: list[DraftClassPreviewRow] = []
    for idx, dp in enumerate(prospects, start=1):
        prospect_id = int(dp["prospect_id"])
        ratings = ratings_by_prospect.get(prospect_id, {})
        role_scores = role_scores_by_prospect.get(prospect_id, {})
        combine = combine_by_prospect.get(prospect_id)
        pro_day = pro_day_by_prospect.get(prospect_id)
        private = private_by_prospect.get(prospect_id)
        top_ratings, weak_ratings = top_and_weak_ratings(ratings)
        primary_role = str(dp["primary_role"] or "")
        secondary_role = str(dp["secondary_role"] or "")
        values: dict[str, Any] = {
            "rank": idx,
            "true_rank": int(dp["true_rank"] or idx),
            "public_board_rank": dp["public_board_rank"],
            "scouting_rank": dp["scouting_rank"],
            "public_board_status": dp["public_board_status"] or ("off_public_board" if dp["public_board_rank"] is None else "ranked"),
            "discovery_status": dp["discovery_status"] or ("undiscovered" if dp["public_board_rank"] is None else "public_board"),
            "scouting_variance": int(dp["scouting_variance"] or 0),
            "discovery_notes": dp["discovery_notes"] or "",
            "development_pathway": dp["development_pathway"] or "Traditional pipeline",
            "pipeline_note": dp["pipeline_note"] or "",
            "display_name": dp["display_name"] or f"{dp['first_name']} {dp['last_name']}",
            "preferred_name": dp["preferred_name"] or dp["first_name"],
            "name_pronunciation_note": dp["name_pronunciation_note"] or "",
            "name_background_note": dp["name_background_note"] or "",
            "family_football_type": dp["family_football_type"] or "",
            "family_football_background": dp["family_football_background"] or "",
            "name_storyline_note": dp["name_storyline_note"] or "",
            "draft_year": int(draft_class["draft_year"]),
            "first_name": dp["first_name"],
            "last_name": dp["last_name"],
            "full_name": f"{dp['first_name']} {dp['last_name']}",
            "position": dp["position"],
            "position_group": dp["position_group"] or dp["position"],
            "age": int(dp["age"] or 22),
            "college": dp["college"] or "",
            "college_tier": dp["college_tier"] or "",
            "hometown": dp["hometown"] or "",
            "hometown_city": dp["hometown_city"] or "",
            "hometown_state": dp["hometown_state"] or "",
            "hometown_region": dp["hometown_region"] or "",
            "height": format_height(int(dp["height_in"] or 72)),
            "height_in": int(dp["height_in"] or 72),
            "weight_lbs": int(dp["weight_lbs"] or 220),
            "arm_length": format_measurement(float(dp["arm_length_in"] or 0)),
            "arm_length_in": float(dp["arm_length_in"] or 0),
            "hand_size": format_measurement(float(dp["hand_size_in"] or 0)),
            "hand_size_in": float(dp["hand_size_in"] or 0),
            "handedness": dp["handedness"] or "Right",
            "combine_status": combine["combine_status"] if combine else "No combine data",
            "combine_note": combine["participation_note"] if combine else "",
            "combine_grade": combine["combine_grade"] if combine else None,
            "athletic_score": combine["athletic_score"] if combine else None,
            "drills_completed": int(combine["drills_completed"] or 0) if combine else 0,
            "drills_skipped": combine["drills_skipped"] if combine else "",
            "workout_variance": combine["workout_variance"] if combine else "",
            "combine_summary": combine["participation_note"] if combine else "",
            "forty_yard_dash": combine["forty_yard_dash"] if combine else None,
            "ten_yard_split": combine["ten_yard_split"] if combine else None,
            "bench_press_reps": combine["bench_press_reps"] if combine else None,
            "vertical_jump_in": combine["vertical_jump_in"] if combine else None,
            "broad_jump_in": combine["broad_jump_in"] if combine else None,
            "three_cone_sec": combine["three_cone_sec"] if combine else None,
            "twenty_yard_shuttle_sec": combine["twenty_yard_shuttle_sec"] if combine else None,
            "sixty_yard_shuttle_sec": combine["sixty_yard_shuttle_sec"] if combine else None,
            "combine_injured": bool(combine["is_injured"]) if combine else False,
            "combine_top_skip": bool(combine["is_top_skip"]) if combine else False,
            "pro_day_status": pro_day["pro_day_status"] if pro_day else "No pro day data",
            "pro_day_note": pro_day["participation_note"] if pro_day else "",
            "pro_day_grade": pro_day["pro_day_grade"] if pro_day else None,
            "pro_day_athletic_score": pro_day["athletic_score"] if pro_day else None,
            "pro_day_drills_completed": int(pro_day["drills_completed"] or 0) if pro_day else 0,
            "pro_day_drills_skipped": pro_day["drills_skipped"] if pro_day else "",
            "pro_day_workout_variance": pro_day["workout_variance"] if pro_day else "",
            "pro_day_summary": pro_day["summary"] if pro_day else "",
            "pro_day_improved_from_combine": bool(pro_day["improved_from_combine"]) if pro_day else False,
            "pro_day_medical_recheck": bool(pro_day["medical_recheck"]) if pro_day else False,
            "pro_day_forty_yard_dash": pro_day["forty_yard_dash"] if pro_day else None,
            "pro_day_ten_yard_split": pro_day["ten_yard_split"] if pro_day else None,
            "pro_day_bench_press_reps": pro_day["bench_press_reps"] if pro_day else None,
            "pro_day_vertical_jump_in": pro_day["vertical_jump_in"] if pro_day else None,
            "pro_day_broad_jump_in": pro_day["broad_jump_in"] if pro_day else None,
            "pro_day_three_cone_sec": pro_day["three_cone_sec"] if pro_day else None,
            "pro_day_twenty_yard_shuttle_sec": pro_day["twenty_yard_shuttle_sec"] if pro_day else None,
            "pro_day_sixty_yard_shuttle_sec": pro_day["sixty_yard_shuttle_sec"] if pro_day else None,
            "private_workout_status": private["status"] if private else "None",
            "private_workout_type": private["workout_type"] if private else "",
            "private_workout_interest": private["interest_level"] if private else "",
            "private_workout_grade": private["outcome_grade"] if private else None,
            "private_workout_note": private["notes"] if private else "",
            "medical_flag": dp["medical_flag"] or "None",
            "medical_risk": dp["medical_risk"] or "Low",
            "medical_notes": dp["medical_notes"] or "",
            "interview_trait": dp["interview_trait"] or "Unknown",
            "interview_grade": dp["interview_grade"],
            "interview_notes": dp["interview_notes"] or "",
            "late_process_status": dp["late_process_status"] or "Stable",
            "late_process_note": dp["late_process_note"] or "",
            "public_board_delta": int(dp["public_board_delta"] or 0),
            "archetype": dp["archetype"] or "",
            "original_archetype": dp["original_archetype"] or dp["archetype"] or "",
            "archetype_identity_status": dp["archetype_identity_status"] or "",
            "archetype_identity_note": dp["archetype_identity_note"] or "",
            "true_grade": int(dp["true_grade"] or dp["overall"] or 50),
            "ceiling_grade": int(dp["ceiling_grade"] or dp["potential"] or 50),
            "dev_trait": dp["dev_trait"] or "Normal",
            "risk_level": dp["risk_level"] or "Medium",
            "projected_round": dp["projected_round"],
            "projected_pick": dp["projected_pick"],
            "primary_role": primary_role,
            "secondary_role": secondary_role,
            "primary_role_score": role_scores.get(primary_role),
            "secondary_role_score": role_scores.get(secondary_role),
            "ratings": ratings,
            "role_scores": role_scores,
            "special_teams_flex": special_teams_flex_by_prospect.get(prospect_id, {}),
            "top_ratings": top_ratings,
            "weak_ratings": weak_ratings,
            "scout_lens": dp["scout_lens"] or "",
            "scout_confidence": dp["scout_confidence"] or "",
            "scout_grade": int(dp["scout_grade"] or dp["overall"] or 50),
            "scout_ceiling": int(dp["scout_ceiling"] or dp["potential"] or 50),
            "scout_risk": dp["scout_risk"] or dp["risk_level"] or "Medium",
            "scouting_summary": dp["scouting_summary"] or "",
            "scouting_strengths": dp["scouting_strengths"] or "",
            "scouting_concerns": dp["scouting_concerns"] or "",
            "scouting_projection": dp["scouting_projection"] or "",
            "scouting_report": dp["scouting_report"] or "",
            "ethnicity_key": dp["ethnicity_key"] or "other_unknown",
            "ethnicity": dp["ethnicity_note"] or dp["ethnicity_label"] or "",
            "primary_ethnicity": dp["ethnicity_label"] or "",
            "secondary_ethnicity": dp["secondary_ethnicity_label"] or "",
            "origin_ethnicity_key": dp["origin_ethnicity_key"] or dp["ethnicity_key"] or "other_unknown",
            "birth_country": dp["birth_country"] or "United States",
            "is_international": bool(dp["is_international"]),
            "generation_version": dp["generation_version"] or dp["normalized_rating_version"] or "",
            "eye_color": dp["eye_color"] or "",
            "hair_color": dp["hair_color"] or "",
            "hairstyle": dp["hairstyle"] or "",
            "facial_hair": dp["facial_hair_style"] or "",
            "skin_tone": dp["skin_tone"] or "",
            "complexion": dp["complexion"] or "",
            "face_shape": dp["face_shape"] or "",
            "jawline": dp["jawline"] or "",
            "brow_profile": dp["brow_profile"] or "",
            "nose_profile": dp["nose_profile"] or "",
            "smile_profile": dp["smile_profile"] or "",
            "media_style": dp["media_style"] or "",
            "accessory_style": dp["accessory_style"] or "",
            "has_mustache": bool(dp["has_mustache"]),
            "has_beard": bool(dp["has_beard"]),
            "photo_prompt_traits": dp["appearance_notes"] or "",
            "physical_outlier": False,
            "hairstyle_outlier": bool(dp["hairstyle_outlier"]),
            "facial_hair_outlier": False,
        }
        field_names = {field.name for field in fields(DraftClassPreviewRow)}
        rows.append(DraftClassPreviewRow(**{field: values[field] for field in field_names}))
    return rows


def write_manifest(
    package_dir: Path,
    *,
    draft_class: sqlite3.Row,
    rows: list[DraftClassPreviewRow],
    package_name: str,
    source_db: Path,
) -> dict[str, Any]:
    public_count = sum(row.public_board_rank is not None for row in rows)
    off_board_count = len(rows) - public_count
    manifest = {
        "class_name": draft_class["class_name"] or f"{draft_class['draft_year']} Saved Draft Class",
        "class_strength": int(draft_class["class_strength"] or 50),
        "created_at": now_utc(),
        "draft_year": int(draft_class["draft_year"]),
        "files": {
            "full_csv": "draft_class_full.csv",
            "full_html": "draft_class_full.html",
            "full_json": "draft_class_full.json",
            "portrait_prompts_dir": "portrait_prompts",
            "portrait_refs_dir": "portrait_refs",
            "portrait_tracker": "portrait_tracker.csv",
            "portraits_approved_dir": "portraits/approved",
            "portraits_generated_dir": "portraits/generated",
            "portraits_rejected_dir": "portraits/rejected",
            "public_csv": "draft_class_public.csv",
            "public_html": "draft_class_public.html",
            "validation": "validation.txt",
        },
        "generation_seed": draft_class["generation_seed"],
        "package_name": package_name,
        "prospect_count": len(rows),
        "public_board_count": public_count,
        "off_board_count": off_board_count,
        "schema_version": SAVED_CLASS_SCHEMA_VERSION,
        "min_import_schema_version": SAVED_CLASS_MIN_IMPORT_SCHEMA_VERSION,
        "generator_compatibility": {
            "version": GENERATOR_COMPATIBILITY_VERSION,
            "supports_year_remap": True,
            "requires_fields": [
                "generation_version",
                "archetype",
                "ratings",
                "photo_prompt_traits",
                "hometown",
            ],
        },
        "source": {
            "type": "persisted_draft_class_export",
            "db_path": str(source_db),
            "draft_class_id": int(draft_class["draft_class_id"]),
            "draft_class_status": draft_class["status"],
            "exported_at": now_utc(),
        },
        "portrait_workflow": {
            "status": "pending",
            "notes": "Save generated player portraits in portraits/generated and update portrait_tracker.csv as each player is completed.",
        },
        "quality_report": {
            "validation_file": "validation.txt",
            "name_appearance_qa": True,
            "college_pipeline_qa": True,
            "body_attribute_qa": True,
        },
    }
    (package_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def write_json_payload(package_dir: Path, manifest: dict[str, Any], rows: list[DraftClassPreviewRow]) -> None:
    payload = {
        "manifest": manifest,
        "schema_version": manifest["schema_version"],
        "rows": [asdict(row) for row in rows],
    }
    (package_dir / "draft_class_full.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_portrait_tracker(package_dir: Path, rows: list[DraftClassPreviewRow]) -> None:
    with (package_dir / "portrait_tracker.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "player_id",
                "full_name",
                "position",
                "college",
                "college_tier",
                "photo_prompt_traits",
                "portrait_status",
                "portrait_file",
                "notes",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "rank": row.rank,
                    "player_id": f"csv_{row.rank}",
                    "full_name": row.full_name,
                    "position": row.position,
                    "college": row.college,
                    "college_tier": row.college_tier,
                    "photo_prompt_traits": row.photo_prompt_traits,
                    "portrait_status": "pending",
                    "portrait_file": "",
                    "notes": "",
                }
            )


def write_readme(package_dir: Path, manifest: dict[str, Any]) -> None:
    text = f"""# {manifest['class_name']}

Reusable saved draft-class package exported from a persisted NFL GM Sim draft class.

## Import

```powershell
python tools\\saved_draft_class_package.py import --package "{package_dir}" --apply
```

Use `--draft-year YEAR` to import this class into a different league year.

## Contents

- `draft_class_full.json`: full import payload.
- `draft_class_full.csv/html`: full audit view.
- `draft_class_public.csv/html`: public-board view.
- `portrait_tracker.csv`: portrait workflow tracker.
- `portraits/`, `portrait_prompts/`, `portrait_refs/`: portrait staging folders.
"""
    (package_dir / "README.md").write_text(text, encoding="utf-8")


def export_package(db_path: Path, package_root: Path, draft_year: int | None, name: str | None) -> Path:
    with connect(db_path) as con:
        ensure_schema(con)
        selected_year = draft_year or active_draft_year(con)
        draft_class = load_class(con, selected_year)
        rows = draft_rows(con, draft_class)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    class_slug = slug(name or draft_class["class_name"] or f"{selected_year} Saved Draft Class")
    package_name = f"{selected_year}_{class_slug}_{timestamp}"
    package_dir = package_root / package_name
    package_dir.mkdir(parents=True, exist_ok=False)
    for folder in (
        "portrait_prompts",
        "portrait_refs",
        "portraits/approved",
        "portraits/generated",
        "portraits/rejected",
    ):
        (package_dir / folder).mkdir(parents=True, exist_ok=True)
    manifest = write_manifest(package_dir, draft_class=draft_class, rows=rows, package_name=package_name, source_db=db_path)
    write_json_payload(package_dir, manifest, rows)
    public_rows = [row for row in rows if row.public_board_rank is not None]
    write_csv(rows, package_dir / "draft_class_full.csv", include_hidden=True)
    write_html(rows, package_dir / "draft_class_full.html", include_hidden=True)
    write_csv(public_rows, package_dir / "draft_class_public.csv", include_hidden=False)
    write_html(public_rows, package_dir / "draft_class_public.html", include_hidden=False)
    write_preview_report(rows, package_dir / "validation.txt")
    write_portrait_tracker(package_dir, rows)
    write_readme(package_dir, manifest)
    return package_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--package-root", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--draft-year", type=int)
    parser.add_argument("--name")
    args = parser.parse_args(argv)
    package_dir = export_package(args.db, args.package_root, args.draft_year, args.name)
    print(f"Exported saved draft class package: {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
