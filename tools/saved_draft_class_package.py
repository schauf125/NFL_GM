"""List and import saved draft-class packages.

Saved classes live outside the repo so they can be reused between installs:
``Z:\\NFL_GM_SIM_MISC_Files\\Saved Draft Classes`` by default.  Packages contain
``draft_class_full.json`` with the generator row payload, plus a manifest.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.class_preview import DraftClassPreviewRow  # noqa: E402
from engine.draft.persistence import persist_draft_class  # noqa: E402
from engine.draft.schema import ensure_schema  # noqa: E402
import draft_portrait_assets  # noqa: E402
import draft_personalities  # noqa: E402
import scouting  # noqa: E402


DEFAULT_PACKAGE_ROOT = Path(r"Z:\NFL_GM_SIM_MISC_Files\Saved Draft Classes")
DEFAULT_DB = ROOT / "database" / "nfl_gm.db"
SUPPORTED_SCHEMA_VERSION = 4
MIN_SUPPORTED_SCHEMA_VERSION = 3

ROW_DEFAULTS: dict[str, Any] = {
    "medical_flag": "None",
    "medical_risk": "Low",
    "medical_notes": "",
    "interview_trait": "Unknown",
    "interview_grade": None,
    "interview_notes": "",
    "late_process_status": "Stable",
    "late_process_note": "",
    "public_board_delta": 0,
    "development_pathway": "Traditional pipeline",
    "pipeline_note": "",
    "display_name": "",
    "preferred_name": "",
    "name_pronunciation_note": "",
    "name_background_note": "",
    "family_football_type": "",
    "family_football_background": "",
    "name_storyline_note": "",
    "hometown": "",
    "hometown_city": "",
    "hometown_state": "",
    "hometown_region": "",
    "skin_tone": "",
    "complexion": "",
    "face_shape": "",
    "jawline": "",
    "brow_profile": "",
    "nose_profile": "",
    "smile_profile": "",
    "media_style": "",
    "accessory_style": "",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def package_manifest(package: Path) -> dict[str, Any]:
    manifest_path = package / "manifest.json"
    if manifest_path.exists():
        return read_json(manifest_path)
    full_path = package / "draft_class_full.json"
    if full_path.exists():
        payload = read_json(full_path)
        manifest = payload.get("manifest")
        if isinstance(manifest, dict):
            return manifest
    raise FileNotFoundError(f"No manifest found in saved draft class package: {package}")


def manifest_schema_info(manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        schema_version = int(manifest.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 1
    compatible = MIN_SUPPORTED_SCHEMA_VERSION <= schema_version <= SUPPORTED_SCHEMA_VERSION
    warning = ""
    if schema_version > SUPPORTED_SCHEMA_VERSION:
        warning = (
            f"Package schema {schema_version} is newer than supported schema "
            f"{SUPPORTED_SCHEMA_VERSION}; update the game before importing."
        )
    elif schema_version < MIN_SUPPORTED_SCHEMA_VERSION:
        warning = (
            f"Package schema {schema_version} is older than the preferred schema "
            f"{MIN_SUPPORTED_SCHEMA_VERSION}; import will use compatibility defaults."
        )
        compatible = True
    return {
        "schemaVersion": schema_version,
        "supportedSchemaVersion": SUPPORTED_SCHEMA_VERSION,
        "compatible": compatible,
        "warning": warning,
    }


def list_packages(root: Path = DEFAULT_PACKAGE_ROOT) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    packages: list[dict[str, Any]] = []
    for package in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not package.is_dir():
            continue
        full_path = package / "draft_class_full.json"
        if not full_path.exists():
            continue
        try:
            manifest = package_manifest(package)
        except Exception as exc:
            packages.append(
                {
                    "name": package.name,
                    "path": str(package),
                    "valid": False,
                    "error": str(exc),
                }
            )
            continue
        schema_info = manifest_schema_info(manifest)
        packages.append(
            {
                "name": str(manifest.get("class_name") or package.name),
                "packageName": str(manifest.get("package_name") or package.name),
                "path": str(package),
                "valid": True,
                "draftYear": manifest.get("draft_year"),
                "prospectCount": manifest.get("prospect_count"),
                "publicBoardCount": manifest.get("public_board_count"),
                "offBoardCount": manifest.get("off_board_count"),
                "classStrength": manifest.get("class_strength"),
                "createdAt": manifest.get("created_at"),
                "seed": manifest.get("generation_seed"),
                "schemaVersion": schema_info["schemaVersion"],
                "supportedSchemaVersion": schema_info["supportedSchemaVersion"],
                "importCompatible": schema_info["compatible"],
                "schemaWarning": schema_info["warning"],
            }
        )
    return packages


def load_rows(package: Path, draft_year: int | None = None) -> tuple[dict[str, Any], list[DraftClassPreviewRow]]:
    full_path = package / "draft_class_full.json"
    if not full_path.exists():
        raise FileNotFoundError(full_path)
    payload = read_json(full_path)
    if isinstance(payload, list):
        manifest = package_manifest(package)
        raw_rows = payload
    elif isinstance(payload, dict):
        manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else package_manifest(package)
        raw_rows = payload.get("rows")
    else:
        raise ValueError(f"Unsupported saved draft class payload in {full_path}")
    schema_info = manifest_schema_info(manifest)
    if not schema_info["compatible"]:
        raise ValueError(schema_info["warning"] or "Saved draft class package schema is not compatible.")
    target_year = int(draft_year or manifest.get("draft_year") or 0)
    if target_year <= 0:
        raise ValueError("A target draft year is required for this package.")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError(f"No draft rows found in {full_path}")

    field_names = {field.name for field in fields(DraftClassPreviewRow)}
    rows: list[DraftClassPreviewRow] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        values = {key: raw.get(key) for key in field_names if key in raw}
        for key, default in ROW_DEFAULTS.items():
            if key in field_names and key not in values:
                values[key] = default
        if "display_name" in field_names and not values.get("display_name"):
            values["display_name"] = f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip()
        if "preferred_name" in field_names and not values.get("preferred_name"):
            values["preferred_name"] = str(raw.get("first_name") or "")
        values["draft_year"] = target_year
        missing = sorted(field_names - set(values))
        if missing:
            raise ValueError(
                f"Saved class row for {raw.get('first_name', '?')} {raw.get('last_name', '?')} "
                f"is missing field(s): {', '.join(missing[:8])}"
            )
        rows.append(DraftClassPreviewRow(**values))
    manifest = dict(manifest)
    manifest["schema_warning"] = schema_info["warning"]
    manifest["schema_version"] = schema_info["schemaVersion"]
    manifest["imported_from_package_year"] = manifest.get("draft_year")
    manifest["draft_year"] = target_year
    return manifest, rows


def existing_class_status(con: sqlite3.Connection, draft_year: int) -> dict[str, int | bool]:
    ensure_schema(con)
    row = con.execute(
        """
        SELECT dc.draft_class_id,
               COUNT(dp.prospect_id) AS prospect_count,
               SUM(CASE WHEN dp.status <> 'Available' OR dp.selected_pick_id IS NOT NULL THEN 1 ELSE 0 END) AS locked_count
        FROM draft_classes dc
        LEFT JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
        WHERE dc.draft_year = ?
        GROUP BY dc.draft_class_id
        """,
        (draft_year,),
    ).fetchone()
    if not row:
        return {"exists": False, "prospectCount": 0, "lockedCount": 0}
    return {
        "exists": True,
        "draftClassId": int(row["draft_class_id"]),
        "prospectCount": int(row["prospect_count"] or 0),
        "lockedCount": int(row["locked_count"] or 0),
    }


def import_package(
    con: sqlite3.Connection,
    *,
    package: Path,
    draft_year: int | None = None,
    force: bool = False,
    initialize_scouting: bool = False,
    game_id: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    manifest, rows = load_rows(package, draft_year=draft_year)
    target_year = int(manifest["draft_year"])
    status = existing_class_status(con, target_year)
    if status.get("exists") and status.get("lockedCount"):
        raise ValueError(
            f"Refusing to replace {target_year} draft class because "
            f"{status['lockedCount']} prospect(s) are already selected or unavailable."
        )
    if status.get("exists") and not force:
        raise ValueError(f"Draft class {target_year} already exists. Use --force to replace it.")

    result = {
        "draftYear": target_year,
        "package": str(package),
        "packageName": package.name,
        "className": manifest.get("class_name") or f"{target_year} Saved Draft Class",
        "prospectCount": len(rows),
        "publicBoardCount": sum(row.public_board_status != "off_public_board" for row in rows),
        "offBoardCount": sum(row.public_board_status == "off_public_board" for row in rows),
        "wouldReplace": bool(status.get("exists")),
        "applied": False,
        "scoutingInitialized": False,
        "portraitsCopied": 0,
        "portraitsMapped": 0,
        "portraitsMissing": 0,
        "schemaVersion": manifest.get("schema_version"),
        "schemaWarning": manifest.get("schema_warning") or "",
    }
    if not apply:
        return result

    seed = str(manifest.get("generation_seed") or f"saved-class:{package.name}:{target_year}")
    persisted = persist_draft_class(
        con,
        rows,
        draft_year=target_year,
        class_strength=int(manifest.get("class_strength") or 50),
        generation_seed=seed,
        class_name=str(manifest.get("class_name") or f"{target_year} Saved Draft Class"),
        notes=f"Imported from saved package {package}. Source package draft year: {manifest.get('imported_from_package_year')}.",
        force=bool(status.get("exists")),
    )
    portrait_result = draft_portrait_assets.import_saved_class_portraits(
        con,
        package=package,
        rows=rows,
        draft_class_id=persisted.draft_class_id,
        draft_year=target_year,
        root=ROOT,
    )
    class_row, prospects, assignments = draft_personalities.build_generation_result(
        con,
        draft_year=target_year,
        seed=seed,
    )
    personality_run_id = draft_personalities.apply_assignments(
        con,
        draft_class_row=class_row,
        prospects=prospects,
        assignments=assignments,
        seed=seed,
        notes=f"Generated on import from saved package {package.name}.",
        force=True,
    )
    scouting_result = None
    if initialize_scouting:
        scouting_result = scouting.initialize_for_game(
            con,
            game_id=game_id,
            draft_year=target_year,
            welcome_message=True,
        )
    con.commit()
    result.update(
        {
            "applied": True,
            "draftClassId": persisted.draft_class_id,
            "personalityRunId": personality_run_id,
            "portraitsCopied": int(portrait_result.get("copied", 0)),
            "portraitsMapped": int(portrait_result.get("mapped", 0)),
            "portraitsMissing": int(portrait_result.get("missing", 0)),
            "scoutingInitialized": scouting_result is not None,
            "scouting": scouting_result,
        }
    )
    return result


def print_packages(packages: list[dict[str, Any]]) -> None:
    if not packages:
        print(f"No saved draft classes found under {DEFAULT_PACKAGE_ROOT}")
        return
    for item in packages:
        status = "OK" if item.get("valid") else "INVALID"
        print(
            f"{status} | {item.get('name')} | year {item.get('draftYear', '-')} | "
            f"{item.get('prospectCount', '-')} prospects | {item.get('path')}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Saved draft-class package utilities.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List saved draft-class packages.")
    list_parser.add_argument("--root", type=Path, default=DEFAULT_PACKAGE_ROOT)
    list_parser.add_argument("--json", action="store_true")

    import_parser = sub.add_parser("import", help="Import a saved draft-class package.")
    import_parser.add_argument("--package", type=Path, required=True)
    import_parser.add_argument("--draft-year", type=int, help="Target draft year. Defaults to package manifest year.")
    import_parser.add_argument("--force", action="store_true", help="Replace an existing unstarted class.")
    import_parser.add_argument("--apply", action="store_true")
    import_parser.add_argument("--initialize-scouting", action="store_true")
    import_parser.add_argument("--game-id")
    import_parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "list":
        packages = list_packages(args.root)
        if args.json:
            print(json.dumps(packages, indent=2))
        else:
            print_packages(packages)
        return 0

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        result = import_package(
            con,
            package=args.package,
            draft_year=args.draft_year,
            force=args.force,
            initialize_scouting=args.initialize_scouting,
            game_id=args.game_id,
            apply=args.apply,
        )
    finally:
        con.close()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        action = "Imported" if result["applied"] else "Would import"
        print(
            f"{action} {result['className']} as {result['draftYear']} "
            f"({result['publicBoardCount']} public, {result['offBoardCount']} off-board)."
        )
        if result.get("scoutingInitialized"):
            print("Scouting initialized.")
        if result.get("portraitsCopied"):
            print(f"Portraits copied: {result['portraitsCopied']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
