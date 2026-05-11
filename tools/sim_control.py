#!/usr/bin/env python3
"""Cooperative controls for long-running simulation actions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class SimCancelled(RuntimeError):
    """Raised when a sim should stop at the next safe checkpoint."""


def cancel_file_for_db(db_path: Path) -> Path:
    return Path(db_path).with_name(".sim_cancel_requested")


def request_cancel(db_path: Path, *, reason: str = "") -> Path:
    marker = cancel_file_for_db(db_path)
    marker.write_text(
        "\n".join(
            [
                f"requested_at={datetime.now(timezone.utc).isoformat()}",
                f"reason={reason}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return marker


def clear_cancel(db_path: Path) -> None:
    marker = cancel_file_for_db(db_path)
    try:
        marker.unlink()
    except FileNotFoundError:
        return


def cancel_requested(db_path: Path) -> bool:
    return cancel_file_for_db(db_path).exists()


def raise_if_cancelled(db_path: Path, detail: str = "") -> None:
    if cancel_requested(db_path):
        message = "Simulation stop requested"
        if detail:
            message = f"{message} {detail}"
        raise SimCancelled(message)
