#!/usr/bin/env python3
"""Prepare background portrait-generation jobs for drafted players.

This tool deliberately does not call an image API and does not import assets
into the database. It only builds prompt/job payloads that a future background
worker can process after draftees are assigned to teams.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
GRAPHICS_PLAYERS_DIR = ROOT / "graphics" / "players"
PORTRAIT_JOBS_DIR = GRAPHICS_PLAYERS_DIR / "portrait_jobs"
PORTRAIT_REFERENCE_DIR = GRAPHICS_PLAYERS_DIR / "portrait_refs"
REFERENCE_FILENAMES = (
    "media_day_base.png",
    "media_day_base.jpg",
    "media_day_base.jpeg",
    "media_day_base.webp",
    "reference.png",
    "reference.jpg",
    "reference.jpeg",
    "reference.webp",
)


@dataclass(frozen=True)
class PortraitSubject:
    player_id: str
    prospect_id: str | None
    full_name: str
    first_name: str
    last_name: str
    position: str
    team_abbr: str
    team_name: str
    college: str | None
    age: int | None
    height_in: int | None
    weight_lbs: int | None
    listed_height: str | None
    listed_weight: str | None
    archetype: str | None
    handedness: str | None
    eye_color: str | None
    hair_color: str | None
    hairstyle: str | None
    facial_hair_style: str | None
    appearance_notes: str | None
    scouting_note: str | None
    source: str
    raw_metadata: dict[str, str] | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def clean_token(value: str) -> str:
    value = value.lower().replace("-", "_").replace(" ", "_")
    value = re.sub(r"[^a-z0-9_]+", "", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "player"


def rel_path(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def resolve_repo_path(path: Path, *, must_exist: bool = False) -> Path:
    resolved = path if path.is_absolute() else ROOT / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Path must stay inside the project: {path}") from exc
    if must_exist and not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved


def team_reference_path(team_abbr: str, reference_dir: Path) -> Path | None:
    team = clean_token(team_abbr.upper())
    candidates: list[Path] = []
    for filename in REFERENCE_FILENAMES:
        candidates.append(reference_dir / team_abbr.upper() / filename)
        candidates.append(reference_dir / team / filename)
    for ext in ("png", "jpg", "jpeg", "webp"):
        candidates.append(reference_dir / f"{team_abbr.upper()}.{ext}")
        candidates.append(reference_dir / f"{team}.{ext}")
    for candidate in candidates:
        resolved = resolve_repo_path(candidate)
        if resolved.exists():
            return resolved
    return None


def parse_listed_height(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip().replace('"', "")
    match = re.match(r"^(\d+)[-'](\d+)$", value)
    if not match:
        return None
    return (int(match.group(1)) * 12) + int(match.group(2))


def parse_weight(value: str | int | None) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return int(digits) if digits else None


def format_height(height_in: int | None, listed_height: str | None = None) -> str:
    if height_in:
        return f"{height_in // 12}'{height_in % 12}\""
    return listed_height or "unknown height"


def format_weight(weight_lbs: int | None, listed_weight: str | None = None) -> str:
    if weight_lbs:
        return f"{weight_lbs} lbs"
    return listed_weight or "unknown weight"


def compact_metadata(metadata: dict[str, str] | None) -> str | None:
    if not metadata:
        return None
    parts = [f"{key}={value}" for key, value in metadata.items()]
    text = "; ".join(parts)
    max_len = 6500
    if len(text) > max_len:
        text = text[:max_len].rsplit("; ", 1)[0] + "; ..."
    return text


def team_display(row: sqlite3.Row) -> str:
    city = row["team_city"] or ""
    nickname = row["team_nickname"] or ""
    return f"{city} {nickname}".strip() or row["team_abbr"] or "Unassigned"


def existing_portrait_player_ids(con: sqlite3.Connection) -> set[int]:
    table = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'player_graphics_assets'
        """
    ).fetchone()
    if not table:
        return set()
    return {
        int(row["player_id"])
        for row in con.execute(
            """
            SELECT player_id
            FROM player_graphics_assets
            WHERE asset_type = 'portrait'
            """
        )
    }


def load_db_subjects(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    team: str | None,
    include_existing_assets: bool,
    limit: int | None,
) -> list[PortraitSubject]:
    params: list[Any] = [draft_year]
    filters = [
        "dc.draft_year = ?",
        "dp.status = 'Drafted'",
        "dp.player_id IS NOT NULL",
        "p.team_id IS NOT NULL",
    ]
    if team:
        filters.append("t.abbreviation = ?")
        params.append(team.upper())

    rows = con.execute(
        f"""
        SELECT
            p.player_id,
            p.first_name,
            p.last_name,
            p.position,
            p.age,
            p.college,
            p.height_in,
            p.weight_lbs,
            t.abbreviation AS team_abbr,
            t.city AS team_city,
            t.nickname AS team_nickname,
            dp.prospect_id,
            dp.archetype,
            dp.handedness,
            dp.eye_color,
            dp.hair_color,
            dp.hairstyle,
            dp.facial_hair_style,
            dp.appearance_notes,
            dp.scouting_summary,
            dp.scouting_report,
            picks.round,
            picks.pick_number
        FROM draft_prospects dp
        JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        JOIN players p ON p.player_id = dp.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN draft_picks picks ON picks.pick_id = dp.selected_pick_id
        WHERE {' AND '.join(filters)}
        ORDER BY
            CASE WHEN picks.pick_number IS NULL THEN 1 ELSE 0 END,
            picks.pick_number,
            p.last_name,
            p.first_name
        """,
        params,
    ).fetchall()

    existing = existing_portrait_player_ids(con) if not include_existing_assets else set()
    subjects: list[PortraitSubject] = []
    for row in rows:
        player_id = int(row["player_id"])
        if player_id in existing:
            continue
        note_parts = []
        if row["round"]:
            pick_text = f"Round {row['round']}"
            if row["pick_number"]:
                pick_text += f", pick {row['pick_number']}"
            note_parts.append(pick_text)
        if row["scouting_summary"]:
            note_parts.append(row["scouting_summary"])
        elif row["scouting_report"]:
            note_parts.append(str(row["scouting_report"])[:240])
        subjects.append(
            PortraitSubject(
                player_id=str(player_id),
                prospect_id=str(row["prospect_id"]) if row["prospect_id"] is not None else None,
                full_name=f"{row['first_name']} {row['last_name']}",
                first_name=row["first_name"],
                last_name=row["last_name"],
                position=row["position"],
                team_abbr=row["team_abbr"] or "FA",
                team_name=team_display(row),
                college=row["college"],
                age=int(row["age"]) if row["age"] is not None else None,
                height_in=int(row["height_in"]) if row["height_in"] is not None else None,
                weight_lbs=int(row["weight_lbs"]) if row["weight_lbs"] is not None else None,
                listed_height=None,
                listed_weight=None,
                archetype=row["archetype"],
                handedness=row["handedness"],
                eye_color=row["eye_color"],
                hair_color=row["hair_color"],
                hairstyle=row["hairstyle"],
                facial_hair_style=row["facial_hair_style"],
                appearance_notes=row["appearance_notes"],
                scouting_note=" ".join(note_parts) or None,
                source="drafted_player_db",
            )
        )
        if limit is not None and len(subjects) >= limit:
            break
    return subjects


def load_csv_subjects(csv_path: Path, *, team: str, team_name: str, limit: int | None) -> list[PortraitSubject]:
    subjects: list[PortraitSubject] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_metadata = {
                str(key): str(value).strip()
                for key, value in row.items()
                if key is not None and value is not None and str(value).strip()
            }
            first_name = row.get("first_name") or row.get("First") or ""
            last_name = row.get("last_name") or row.get("Last") or ""
            if not first_name and not last_name and row.get("full_name"):
                parts = row["full_name"].split()
                first_name = parts[0]
                last_name = " ".join(parts[1:])
            height_in = parse_listed_height(row.get("listed_height") or row.get("height"))
            weight_lbs = parse_weight(
                row.get("listed_weight")
                or row.get("weight")
                or row.get("listed_weight_lbs")
                or row.get("weight_lbs")
            )
            rank = row.get("starter_rank") or row.get("rank") or str(len(subjects) + 1)
            subjects.append(
                PortraitSubject(
                    player_id=f"csv_{rank}",
                    prospect_id=None,
                    full_name=f"{first_name} {last_name}".strip(),
                    first_name=first_name,
                    last_name=last_name,
                    position=(row.get("position") or "UNK").upper(),
                    team_abbr=team.upper(),
                    team_name=team_name,
                    college=row.get("school") or row.get("college"),
                    age=parse_weight(row.get("age")),
                    height_in=height_in,
                    weight_lbs=weight_lbs,
                    listed_height=row.get("listed_height") or row.get("height"),
                    listed_weight=row.get("listed_weight") or row.get("weight"),
                    archetype=row.get("archetype_hint") or row.get("archetype"),
                    handedness=row.get("handedness"),
                    eye_color=row.get("eye_color"),
                    hair_color=row.get("hair_color"),
                    hairstyle=row.get("hairstyle"),
                    facial_hair_style=row.get("facial_hair_style") or row.get("facial_hair"),
                    appearance_notes=row.get("appearance_notes") or row.get("photo_prompt_traits"),
                    scouting_note=(
                        row.get("scouting_note")
                        or row.get("scouting_summary")
                        or row.get("scouting_report")
                        or row.get("risk_note")
                    ),
                    source=f"csv:{rel_path(csv_path)}",
                    raw_metadata=raw_metadata,
                )
            )
            if limit is not None and len(subjects) >= limit:
                break
    return subjects


def prompt_for_subject(subject: PortraitSubject) -> str:
    height = format_height(subject.height_in, subject.listed_height)
    weight = format_weight(subject.weight_lbs, subject.listed_weight)
    build_instruction = (
        f"Use the listed football body type: {height}, {weight}, {subject.position}. "
        "Reflect that size in the neck, shoulders, and frame while keeping this a head-and-shoulders portrait."
    )
    appearance_bits = [
        f"age {subject.age}" if subject.age else None,
        f"college background: {subject.college}" if subject.college else None,
        f"archetype: {subject.archetype}" if subject.archetype else None,
        f"handedness: {subject.handedness}" if subject.handedness else None,
        f"eyes: {subject.eye_color}" if subject.eye_color else None,
        f"hair: {subject.hair_color} {subject.hairstyle}" if subject.hair_color or subject.hairstyle else None,
        f"facial hair: {subject.facial_hair_style}" if subject.facial_hair_style else None,
        subject.appearance_notes,
    ]
    appearance = "; ".join(bit for bit in appearance_bits if bit)
    raw_metadata = compact_metadata(subject.raw_metadata)
    scouting = f"Scouting flavor to guide posture and expression: {subject.scouting_note}" if subject.scouting_note else ""
    lines = [
        "Create a realistic fictional rookie American football player portrait for a local sports management simulation.",
        "This must be an original fictional person. Do not make the portrait resemble a real athlete, celebrity, or public figure.",
        f"Player context: {subject.full_name}, {subject.position}, assigned to {subject.team_name} ({subject.team_abbr}).",
        build_instruction,
        f"Key visual metadata: {appearance}" if appearance else "Key visual metadata: no detailed appearance metadata was provided; choose a believable rookie player look.",
    ]
    if raw_metadata:
        lines.append(
            "Full scouting/combine metadata row for consistency. Use visual/body fields most heavily, then position and scouting role for expression/posture: "
            + raw_metadata
        )
    if scouting:
        lines.append(scouting)
    lines.extend(
        [
            "If a reference image is supplied, use it only as a composition, lighting, background, crop, jersey-color, and media-day style reference. Do not copy the reference person's face, identity, hairstyle, ethnicity, or exact jersey marks.",
            "Visual style: photorealistic official team media-day headshot, chest-up, straight-on camera, expression follows the visual metadata and is usually neutral or closed-mouth unless a smile is explicitly specified, realistic skin texture, natural hair detail, studio lighting, dark matte or neutral gray background.",
            "Wardrobe: generic professional football jersey or training top in team-inspired colors, but no real team logos, NFL shields, brand marks, visible text, watermarks, or captions.",
            "Framing: square 1024x1024 composition, face centered, shoulders visible, no helmet, no action pose, no extra people, no illustration, no cartoon, no 3D render.",
        ]
    )
    return "\n".join(lines)


def build_job(
    run_id: str,
    subject: PortraitSubject,
    staged_root: Path,
    *,
    reference_image: Path | None,
) -> dict[str, Any]:
    slug = clean_token(subject.full_name)
    team = clean_token(subject.team_abbr.upper())
    stable = hashlib.sha1(f"{run_id}:{subject.player_id}:{subject.full_name}".encode("utf-8")).hexdigest()[:10]
    staged_path = staged_root / subject.team_abbr.upper() / f"{subject.player_id}_{slug}_{stable}.png"
    final_path = GRAPHICS_PLAYERS_DIR / subject.team_abbr.upper() / "portraits" / f"{subject.player_id}_{slug}.png"
    job = {
        "job_id": f"{run_id}_{team}_{subject.player_id}_{stable}",
        "status": "queued",
        "provider": "openai_or_compatible_image_provider",
        "model": "configured_at_runtime",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "player": asdict(subject),
        "prompt": prompt_for_subject(subject),
        "output": {
            "requested_size": "1024x1024",
            "requested_format": "png",
            "staged_path": rel_path(staged_path),
            "future_import_path": rel_path(final_path),
            "import_ready": False,
        },
        "review": {
            "requires_human_approval": True,
            "approved": False,
            "notes": "Generated image should remain staged until reviewed and imported into player_graphics_assets.",
        },
    }
    if reference_image:
        job["reference"] = {
            "image_path": rel_path(reference_image),
            "usage": "composition_lighting_background_crop_and_team_color_reference_only",
            "identity_policy": "Do not copy the reference person's face or identity; create the fictional player from the job metadata.",
        }
    return job


def write_jobs(
    subjects: list[PortraitSubject],
    *,
    run_id: str,
    jobs_dir: Path,
    dry_run: bool,
    reference_image: Path | None,
    reference_dir: Path,
    use_team_references: bool,
) -> dict[str, Any]:
    queued_dir = jobs_dir / "queued"
    prompts_dir = jobs_dir / "prompts" / run_id
    staged_root = jobs_dir / "staged" / run_id
    manifests_dir = jobs_dir / "manifests"
    jobs = []
    for subject in subjects:
        subject_reference = reference_image
        if subject_reference is None and use_team_references:
            subject_reference = team_reference_path(subject.team_abbr, reference_dir)
        jobs.append(
            build_job(
                run_id,
                subject,
                staged_root,
                reference_image=subject_reference,
            )
        )
    reference_count = sum(1 for job in jobs if job.get("reference", {}).get("image_path"))
    manifest = {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "job_count": len(jobs),
        "queued_jsonl": rel_path(queued_dir / f"{run_id}.jsonl"),
        "prompts_dir": rel_path(prompts_dir),
        "staged_dir": rel_path(staged_root),
        "status": "dry_run" if dry_run else "queued",
        "reference_image_count": reference_count,
        "reference_dir": rel_path(reference_dir),
        "notes": "Prompt/job prep only. No image API call has been made.",
    }
    if dry_run:
        return {"manifest": manifest, "jobs": jobs}

    queued_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir.mkdir(parents=True, exist_ok=True)
    staged_root.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    (staged_root / ".gitkeep").write_text("", encoding="utf-8")
    with (queued_dir / f"{run_id}.jsonl").open("w", encoding="utf-8") as handle:
        for job in jobs:
            handle.write(json.dumps(job, sort_keys=True) + "\n")
    for job in jobs:
        subject = PortraitSubject(**job["player"])
        prompt_name = f"{subject.team_abbr.upper()}_{subject.player_id}_{clean_token(subject.full_name)}.txt"
        (prompts_dir / prompt_name).write_text(job["prompt"] + "\n", encoding="utf-8")
    (manifests_dir / f"{run_id}.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"manifest": manifest, "jobs": jobs}


def default_run_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}"


def action_from_db(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        subjects = load_db_subjects(
            con,
            draft_year=args.draft_year,
            team=args.team,
            include_existing_assets=args.include_existing_assets,
            limit=args.limit,
        )
    run_id = args.run_id or default_run_id(f"draft_{args.draft_year}_portraits")
    reference_image = resolve_repo_path(args.reference_image, must_exist=True) if args.reference_image else None
    reference_dir = resolve_repo_path(args.reference_dir)
    result = write_jobs(
        subjects,
        run_id=run_id,
        jobs_dir=args.jobs_dir,
        dry_run=not args.apply,
        reference_image=reference_image,
        reference_dir=reference_dir,
        use_team_references=not args.no_team_reference,
    )
    print_summary(result)


def action_from_csv(args: argparse.Namespace) -> None:
    subjects = load_csv_subjects(args.csv, team=args.team, team_name=args.team_name, limit=args.limit)
    run_id = args.run_id or default_run_id("csv_portrait_preview")
    reference_image = resolve_repo_path(args.reference_image, must_exist=True) if args.reference_image else None
    reference_dir = resolve_repo_path(args.reference_dir)
    result = write_jobs(
        subjects,
        run_id=run_id,
        jobs_dir=args.jobs_dir,
        dry_run=not args.apply,
        reference_image=reference_image,
        reference_dir=reference_dir,
        use_team_references=not args.no_team_reference,
    )
    print_summary(result)


def print_summary(result: dict[str, Any]) -> None:
    manifest = result["manifest"]
    print(f"Run id: {manifest['run_id']}")
    print(f"Status: {manifest['status']}")
    print(f"Jobs: {manifest['job_count']}")
    print(f"Queue: {manifest['queued_jsonl']}")
    print(f"Prompts: {manifest['prompts_dir']}")
    print(f"Staging: {manifest['staged_dir']}")
    if manifest.get("reference_image_count"):
        print(f"Reference images: {manifest['reference_image_count']}")
    if result["jobs"]:
        first = result["jobs"][0]
        print("")
        print(f"First job: {first['job_id']}")
        print(first["prompt"].splitlines()[2])
        reference = first.get("reference", {}).get("image_path")
        if reference:
            print(f"First reference: {reference}")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--jobs-dir", type=Path, default=PORTRAIT_JOBS_DIR)
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true", help="Write queue files. Omit for dry run.")
    parser.add_argument(
        "--reference-image",
        type=Path,
        help="Use one explicit reference image for all queued jobs. The image is used for roster-photo style only.",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=PORTRAIT_REFERENCE_DIR,
        help="Directory for per-team reference images, e.g. graphics/players/portrait_refs/MIN/media_day_base.png.",
    )
    parser.add_argument(
        "--no-team-reference",
        action="store_true",
        help="Do not auto-attach per-team reference images from --reference-dir.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    from_db = subparsers.add_parser("from-db", help="Queue drafted players from the game database.")
    from_db.add_argument("--db", type=Path, default=DB_PATH)
    from_db.add_argument("--draft-year", type=int, required=True)
    from_db.add_argument("--team", help="Limit to one team abbreviation.")
    from_db.add_argument("--include-existing-assets", action="store_true")
    add_common_args(from_db)
    from_db.set_defaults(func=action_from_db)

    from_csv = subparsers.add_parser("from-csv", help="Queue prompt jobs from a projection CSV.")
    from_csv.add_argument("--csv", type=Path, required=True)
    from_csv.add_argument("--team", default="FAKE", help="Placeholder/current team abbreviation for prompts.")
    from_csv.add_argument("--team-name", default="Draft Preview Team")
    add_common_args(from_csv)
    from_csv.set_defaults(func=action_from_csv)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
