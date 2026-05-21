"""Create draft classes automatically at save/year start."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.class_preview import DraftClassPreviewGenerator  # noqa: E402
from engine.draft.persistence import persist_draft_class  # noqa: E402
from engine.draft.schema import ensure_schema  # noqa: E402
import draft_personalities  # noqa: E402


DEFAULT_PUBLIC_PROSPECT_COUNT = 310
DEFAULT_CLASS_STRENGTH = 50


@dataclass(frozen=True)
class DraftClassBootstrapResult:
    draft_year: int
    created: bool
    draft_class_id: int | None
    prospect_count: int
    public_board_count: int
    off_board_count: int
    seed: str
    personality_run_id: int | None
    message: str


def ensure_draft_class(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    seed: str,
    public_count: int = DEFAULT_PUBLIC_PROSPECT_COUNT,
    hidden_count: int | None = None,
    class_strength: int = DEFAULT_CLASS_STRENGTH,
    notes: str | None = None,
    refresh_legacy_without_offboard: bool = False,
    replace_existing: bool = False,
    generate_personalities: bool = True,
) -> DraftClassBootstrapResult:
    """Generate a draft class when needed, optionally replacing a copied/template class."""

    ensure_schema(con)
    existing = con.execute(
        """
        SELECT dc.draft_class_id,
               COUNT(dp.prospect_id) AS prospect_count,
               SUM(CASE WHEN dp.public_board_status = 'off_public_board' THEN 1 ELSE 0 END) AS off_board_count,
               SUM(CASE WHEN dp.status <> 'Available' OR dp.selected_pick_id IS NOT NULL THEN 1 ELSE 0 END) AS selected_count
        FROM draft_classes dc
        LEFT JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
        WHERE dc.draft_year = ?
        GROUP BY dc.draft_class_id
        """,
        (draft_year,),
    ).fetchone()
    force_replace = replace_existing
    should_replace_existing = False
    if existing and int(existing["prospect_count"] or 0) > 0:
        prospect_count = int(existing["prospect_count"] or 0)
        off_board_count = int(existing["off_board_count"] or 0)
        selected_count = int(existing["selected_count"] or 0)
        if force_replace and selected_count:
            raise ValueError(
                f"Refusing to replace {draft_year} draft class because {selected_count} "
                "prospect(s) are already selected or unavailable."
            )
        if force_replace and selected_count == 0:
            should_replace_existing = True
        elif refresh_legacy_without_offboard and off_board_count == 0 and selected_count == 0:
            should_replace_existing = True
        else:
            return DraftClassBootstrapResult(
                draft_year=draft_year,
                created=False,
                draft_class_id=int(existing["draft_class_id"]),
                prospect_count=prospect_count,
                public_board_count=prospect_count - off_board_count,
                off_board_count=off_board_count,
                seed=seed,
                personality_run_id=_existing_personality_run_id(con, int(existing["draft_class_id"])),
                message=(
                    f"{draft_year} draft class already exists with {prospect_count} prospects "
                    f"({off_board_count} off-board)."
                ),
            )

    generator = DraftClassPreviewGenerator(seed=seed)
    rows = generator.generate(
        draft_year=draft_year,
        count=public_count,
        hidden_count=hidden_count,
        class_strength=class_strength,
    )
    result = persist_draft_class(
        con,
        rows,
        draft_year=draft_year,
        class_strength=class_strength,
        generation_seed=seed,
        notes=notes or "Automatically generated at save/year start.",
        force=should_replace_existing,
    )
    personality_run_id = None
    if generate_personalities:
        class_row, prospects, assignments = draft_personalities.build_generation_result(
            con,
            draft_year=draft_year,
            seed=seed,
        )
        personality_run_id = draft_personalities.apply_assignments(
            con,
            draft_class_row=class_row,
            prospects=prospects,
            assignments=assignments,
            seed=seed,
            notes="Generated automatically with the draft class.",
            force=True,
        )
    off_board_count = sum(row.public_board_status == "off_public_board" for row in rows)
    public_board_count = len(rows) - off_board_count
    verb = "Refreshed" if should_replace_existing else "Generated"
    return DraftClassBootstrapResult(
        draft_year=draft_year,
        created=True,
        draft_class_id=result.draft_class_id,
        prospect_count=len(rows),
        public_board_count=public_board_count,
        off_board_count=off_board_count,
        seed=seed,
        personality_run_id=personality_run_id,
        message=(
            f"{verb} {draft_year} draft class with {public_board_count} public-board "
            f"and {off_board_count} off-board prospects."
        ),
    )


def _existing_personality_run_id(con: sqlite3.Connection, draft_class_id: int) -> int | None:
    row = con.execute(
        """
        SELECT run_id
        FROM draft_class_personality_runs
        WHERE draft_class_id = ?
        ORDER BY run_id DESC
        LIMIT 1
        """,
        (draft_class_id,),
    ).fetchone()
    return int(row["run_id"]) if row else None
