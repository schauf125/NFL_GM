#!/usr/bin/env python3
"""Generate staged draft-prospect portraits from queued portrait jobs.

This worker is intentionally conservative:

- dry run is the default;
- applying more than one job requires --allow-batch;
- generating every queued job requires both --all and --allow-batch;
- existing staged image files are never overwritten unless --force is passed.

The generated images remain staged for review. This tool does not import them
into player_graphics_assets or make them active in the game UI.
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PORTRAIT_JOBS_DIR = ROOT / "graphics" / "players" / "portrait_jobs"
DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "high"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_BACKGROUND = "opaque"
USER_AGENT = "NFL-GM-Sim/0.1 draft portrait worker"


class PortraitGenerationError(RuntimeError):
    """Raised when the portrait worker cannot safely continue."""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rel_path(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def resolve_repo_path(path_text: str) -> Path:
    path = (ROOT / path_text).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise PortraitGenerationError(f"Refusing path outside repo: {path_text}") from exc
    return path


def queue_path(jobs_dir: Path, run_id: str) -> Path:
    return jobs_dir / "queued" / f"{run_id}.jsonl"


def manifest_path(jobs_dir: Path, run_id: str) -> Path:
    return jobs_dir / "manifests" / f"{run_id}.json"


def load_jobs(jobs_dir: Path, run_id: str) -> list[dict[str, Any]]:
    path = queue_path(jobs_dir, run_id)
    if not path.exists():
        raise FileNotFoundError(path)
    jobs: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                jobs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise PortraitGenerationError(f"Bad JSON in {path} line {line_number}: {exc}") from exc
    return jobs


def write_jobs(jobs_dir: Path, run_id: str, jobs: list[dict[str, Any]]) -> None:
    path = queue_path(jobs_dir, run_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for job in jobs:
            handle.write(json.dumps(job, sort_keys=True) + "\n")
    tmp.replace(path)


def update_manifest(
    jobs_dir: Path,
    run_id: str,
    jobs: list[dict[str, Any]],
    *,
    generated: int,
    skipped: int,
    failed: int,
    args: argparse.Namespace,
) -> None:
    path = manifest_path(jobs_dir, run_id)
    manifest: dict[str, Any] = {}
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))

    counts = collections.Counter(str(job.get("status", "unknown")) for job in jobs)
    staged_count = sum(
        count
        for status, count in counts.items()
        if status in {"staged", "staged_example", "approved", "imported"}
    )
    if failed:
        status = "generation_errors"
    elif staged_count and staged_count < len(jobs):
        status = "partially_staged"
    elif staged_count == len(jobs) and jobs:
        status = "staged"
    else:
        status = manifest.get("status", "queued")

    manifest.update(
        {
            "run_id": run_id,
            "job_count": len(jobs),
            "queued_jsonl": rel_path(queue_path(jobs_dir, run_id)),
            "status": status,
            "job_status_counts": dict(sorted(counts.items())),
            "last_generation_run": {
                "at": utc_now(),
                "provider": "openai",
                "api": "image_api",
                "model": args.model,
                "size": args.size,
                "quality": args.quality,
                "output_format": args.output_format,
                "background": args.background,
                "generated": generated,
                "skipped": skipped,
                "failed": failed,
            },
            "notes": (
                "Generated portraits remain staged until reviewed and imported. "
                "This worker does not update player_graphics_assets."
            ),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def job_destination(job: dict[str, Any], output_format: str) -> Path:
    output = job.setdefault("output", {})
    staged_text = output.get("staged_path")
    if not staged_text:
        raise PortraitGenerationError(f"Job {job.get('job_id', '<unknown>')} has no output.staged_path")
    path = resolve_repo_path(str(staged_text))
    desired_suffix = "." + output_format.lower().lstrip(".")
    if path.suffix.lower() != desired_suffix:
        path = path.with_suffix(desired_suffix)
    return path


def image_size(data: bytes) -> tuple[int | None, int | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return width, height
    if data.startswith(b"\xff\xd8"):
        idx = 2
        while idx + 9 < len(data):
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            idx += 2
            if marker in {0xD8, 0xD9}:
                continue
            if idx + 2 > len(data):
                break
            segment_len = int.from_bytes(data[idx : idx + 2], "big")
            if marker in range(0xC0, 0xC4) and idx + 7 < len(data):
                height = int.from_bytes(data[idx + 3 : idx + 5], "big")
                width = int.from_bytes(data[idx + 5 : idx + 7], "big")
                return width, height
            idx += max(segment_len, 2)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return None, None
    return None, None


def validate_image_bytes(data: bytes, output_format: str) -> None:
    if len(data) < 1024:
        raise PortraitGenerationError("Image response was unexpectedly small.")
    fmt = output_format.lower()
    if fmt == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PortraitGenerationError("Expected a PNG response, but the bytes are not PNG.")
    if fmt in {"jpg", "jpeg"} and not data.startswith(b"\xff\xd8"):
        raise PortraitGenerationError("Expected a JPEG response, but the bytes are not JPEG.")
    if fmt == "webp" and not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise PortraitGenerationError("Expected a WebP response, but the bytes are not WebP.")


def generation_prompt(job: dict[str, Any]) -> str:
    prompt = str(job.get("prompt") or "").strip()
    if not prompt:
        raise PortraitGenerationError(f"Job {job.get('job_id', '<unknown>')} has an empty prompt.")
    quality_guard = "\n".join(
        [
            "",
            "Final output requirement: photorealistic pro football media-day headshot, not an illustration, not a cartoon, not a 3D render, not video-game art.",
            "Use believable human facial structure, natural skin texture, realistic hair detail, studio lighting, and a neutral gray or dark matte backdrop similar to a professional team roster photo.",
            "Do not include official league logos, team logos, jersey text, watermarks, captions, or brand marks.",
        ]
    )
    return prompt + quality_guard


def openai_headers(api_key: str, content_type: str | None = "application/json") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
        "X-Client-Request-Id": str(uuid.uuid4()),
    }
    if content_type:
        headers["Content-Type"] = content_type
    organization = os.environ.get("OPENAI_ORG_ID") or os.environ.get("OPENAI_ORGANIZATION")
    project = os.environ.get("OPENAI_PROJECT_ID") or os.environ.get("OPENAI_PROJECT")
    if organization:
        headers["OpenAI-Organization"] = organization
    if project:
        headers["OpenAI-Project"] = project
    return headers


def reference_image_for_job(job: dict[str, Any], args: argparse.Namespace) -> Path | None:
    if args.reference_image:
        return resolve_repo_path(str(args.reference_image))
    if args.no_reference_image:
        return None
    reference = job.get("reference") or {}
    image_path = reference.get("image_path")
    if image_path:
        return resolve_repo_path(str(image_path))
    return None


def encode_multipart_form(
    fields: dict[str, Any],
    files: list[tuple[str, Path]],
) -> tuple[bytes, str]:
    boundary = f"----nfl-gm-sim-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for field_name, path in files:
        if not path.exists():
            raise PortraitGenerationError(f"Reference image does not exist: {rel_path(path)}")
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{path.name}"\r\n'
            ).encode("utf-8")
        )
        chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def post_openai_image_request(
    args: argparse.Namespace,
    prompt: str,
    *,
    reference_image: Path | None,
) -> tuple[dict[str, Any], str | None, str]:
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise PortraitGenerationError(
            f"{args.api_key_env} is not set. Refusing to call OpenAI without an explicit API key."
        )

    fields: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "n": 1,
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format,
        "background": args.background,
    }
    if args.output_format in {"jpeg", "webp"} and args.output_compression is not None:
        fields["output_compression"] = args.output_compression

    if reference_image:
        url = args.api_base.rstrip("/") + "/images/edits"
        body, content_type = encode_multipart_form(fields, [("image", reference_image)])
        headers = openai_headers(api_key, content_type)
        api_name = "image_edits"
        request = Request(url, data=body, headers=headers, method="POST")
    else:
        url = args.api_base.rstrip("/") + "/images/generations"
        request = Request(
            url,
            data=json.dumps(fields).encode("utf-8"),
            headers=openai_headers(api_key),
            method="POST",
        )
        api_name = "image_generations"
    try:
        with urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8")
            request_id = response.headers.get("x-request-id")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PortraitGenerationError(f"OpenAI request failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise PortraitGenerationError(f"OpenAI request failed: {exc}") from exc

    try:
        return json.loads(body), request_id, api_name
    except json.JSONDecodeError as exc:
        raise PortraitGenerationError("OpenAI returned non-JSON response.") from exc


def extract_image(payload: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise PortraitGenerationError("OpenAI response did not include data[0].")
    first = data[0]
    if not isinstance(first, dict) or not first.get("b64_json"):
        raise PortraitGenerationError("OpenAI response did not include data[0].b64_json.")
    try:
        image_bytes = base64.b64decode(first["b64_json"])
    except ValueError as exc:
        raise PortraitGenerationError("OpenAI returned invalid base64 image data.") from exc
    metadata = {key: value for key, value in first.items() if key != "b64_json"}
    if "usage" in payload:
        metadata["usage"] = payload["usage"]
    if "created" in payload:
        metadata["created"] = payload["created"]
    return image_bytes, metadata


def selected_jobs(jobs: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected = jobs
    if args.job_id:
        allowed = set(args.job_id)
        selected = [job for job in selected if job.get("job_id") in allowed]
    if not args.all:
        selected = selected[: args.limit]
    if not selected:
        raise PortraitGenerationError("No jobs matched the requested selection.")
    if len(selected) > 1 and not args.allow_batch:
        raise PortraitGenerationError(
            f"Refusing to process {len(selected)} jobs without --allow-batch. "
            "Use --limit 1 for a single portrait or add --allow-batch deliberately."
        )
    return selected


def print_plan(selected: list[dict[str, Any]], args: argparse.Namespace) -> None:
    print("Portrait generation plan")
    print(f"Run id: {args.run_id}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print("Provider: openai image_api (edits when a reference image is present)")
    print(f"Model: {args.model}")
    print(f"Size/quality/format: {args.size}, {args.quality}, {args.output_format}")
    print(f"Jobs selected: {len(selected)}")
    print("")
    for job in selected:
        player = job.get("player", {})
        output_path = job_destination(job, args.output_format)
        exists = output_path.exists()
        print(f"- {job.get('job_id')}")
        print(f"  Player: {player.get('full_name', 'Unknown')} ({player.get('position', 'UNK')})")
        print(f"  Status: {job.get('status', 'unknown')}")
        print(f"  Output: {rel_path(output_path)}{' [exists]' if exists else ''}")
        reference_image = reference_image_for_job(job, args)
        if reference_image:
            print(f"  Reference: {rel_path(reference_image)}")
    if not args.apply:
        print("")
        print("Dry run only. Add --apply to call OpenAI for the selected portrait(s).")


def action_summary(args: argparse.Namespace) -> None:
    jobs = load_jobs(args.jobs_dir, args.run_id)
    counts = collections.Counter(str(job.get("status", "unknown")) for job in jobs)
    existing = sum(1 for job in jobs if job_destination(job, DEFAULT_OUTPUT_FORMAT).exists())
    print(f"Run id: {args.run_id}")
    print(f"Jobs: {len(jobs)}")
    print(f"Existing staged files: {existing}")
    print("Statuses:")
    for status, count in sorted(counts.items()):
        print(f"- {status}: {count}")
    if jobs:
        first = jobs[0]
        player = first.get("player", {})
        print("")
        print(f"First job: {first.get('job_id')}")
        print(f"First player: {player.get('full_name', 'Unknown')} ({player.get('position', 'UNK')})")


def action_generate(args: argparse.Namespace) -> None:
    jobs = load_jobs(args.jobs_dir, args.run_id)
    selected = selected_jobs(jobs, args)
    print_plan(selected, args)
    if not args.apply:
        return
    if not os.environ.get(args.api_key_env):
        raise PortraitGenerationError(
            f"{args.api_key_env} is not set. Refusing to call OpenAI or update job status."
        )

    generated = 0
    skipped = 0
    failed = 0
    job_lookup = {id(job): job for job in jobs}
    for index, job in enumerate(selected, start=1):
        destination = job_destination(job, args.output_format)
        if destination.exists() and not args.force:
            print(f"Skipping existing file without --force: {rel_path(destination)}")
            skipped += 1
            continue

        print(f"Generating {index}/{len(selected)}: {job.get('job_id')}")
        try:
            prompt = generation_prompt(job)
            reference_image = reference_image_for_job(job, args)
            payload, request_id, api_name = post_openai_image_request(
                args,
                prompt,
                reference_image=reference_image,
            )
            image_bytes, metadata = extract_image(payload)
            validate_image_bytes(image_bytes, args.output_format)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(image_bytes)
            width, height = image_size(image_bytes)
            output = job.setdefault("output", {})
            output.update(
                {
                    "staged_path": rel_path(destination),
                    "requested_size": args.size,
                    "requested_format": args.output_format,
                    "requested_quality": args.quality,
                    "requested_background": args.background,
                    "width": width,
                    "height": height,
                    "import_ready": False,
                }
            )
            job.update(
                {
                    "status": "staged",
                    "provider": "openai",
                    "api": api_name,
                    "model": args.model,
                    "updated_at": utc_now(),
                    "generation": {
                        "request_id": request_id,
                        "api": api_name,
                        "model": args.model,
                        "size": args.size,
                        "quality": args.quality,
                        "output_format": args.output_format,
                        "background": args.background,
                        "reference_image": rel_path(reference_image) if reference_image else None,
                        "metadata": metadata,
                    },
                    "review": {
                        "requires_human_approval": True,
                        "approved": False,
                        "notes": "Generated by OpenAI image API; staged only, not imported into the playable game.",
                    },
                }
            )
            # Keep the same object identity explicit for readability.
            job_lookup[id(job)] = job
            generated += 1
            print(f"  Wrote {rel_path(destination)}")
        except PortraitGenerationError as exc:
            failed += 1
            job["status"] = "generation_failed"
            job["updated_at"] = utc_now()
            job["generation_error"] = str(exc)
            print(f"  Failed: {exc}")
            if not args.continue_on_error:
                break
        if args.sleep and index < len(selected):
            time.sleep(args.sleep)

    write_jobs(args.jobs_dir, args.run_id, jobs)
    update_manifest(
        args.jobs_dir,
        args.run_id,
        jobs,
        generated=generated,
        skipped=skipped,
        failed=failed,
        args=args,
    )
    print("")
    print(f"Generated: {generated}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")


def manual_packet_destination(args: argparse.Namespace, job: dict[str, Any]) -> Path:
    player = job.get("player", {})
    player_name = str(player.get("full_name") or job.get("job_id") or "portrait")
    safe_name = "".join(ch.lower() if ch.isalnum() else "_" for ch in player_name)
    safe_name = "_".join(part for part in safe_name.split("_") if part) or "portrait"
    output_dir = resolve_repo_path(str(args.output_dir)) / args.run_id
    return output_dir / f"{safe_name}_{job.get('job_id', 'job')}.md"


def action_manual_packet(args: argparse.Namespace) -> None:
    jobs = load_jobs(args.jobs_dir, args.run_id)
    selected = selected_jobs(jobs, args)
    print("Manual portrait packet export")
    print(f"Run id: {args.run_id}")
    print(f"Jobs selected: {len(selected)}")
    print("This does not call OpenAI's API.")
    print("")

    written = 0
    for job in selected:
        player = job.get("player", {})
        output = job.get("output", {})
        prompt = generation_prompt(job)
        reference_image = reference_image_for_job(job, args)
        destination = manual_packet_destination(args, job)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged_path = output.get("staged_path", "")
        future_import_path = output.get("future_import_path", "")
        lines = [
            f"# Manual Portrait Packet: {player.get('full_name', 'Unknown Player')}",
            "",
            "Use this packet in ChatGPT/Codex when you want to generate a portrait without calling the local OpenAI API.",
            "",
            "## Player",
            "",
            f"- Name: {player.get('full_name', 'Unknown')}",
            f"- Position: {player.get('position', 'UNK')}",
            f"- Team: {player.get('team_name', 'Unknown')} ({player.get('team_abbr', 'UNK')})",
            f"- Height/weight: {player.get('listed_height') or player.get('height_in') or 'unknown'}, {player.get('weight_lbs') or player.get('listed_weight') or 'unknown'} lbs",
            "",
            "## Reference Image",
            "",
            (
                f"Upload or attach this reference image: `{rel_path(reference_image)}`"
                if reference_image
                else "No reference image is attached to this job. Add one for better media-day realism."
            ),
            "",
            "The reference image is for composition, lighting, crop, background, and jersey-color direction only. Do not copy the reference person's face or identity.",
            "",
            "## Prompt",
            "",
            "```text",
            "Use the attached reference image as the base roster-photo style.",
            "Generate a similar photorealistic portrait based on the following player metadata while remembering height and weight as factors.",
            "",
            prompt,
            "```",
            "",
            "## Save Result",
            "",
            f"- Staged output path: `{staged_path}`",
            f"- Future approved/import path: `{future_import_path}`",
            "",
            "After generating the image manually, save the PNG to the staged output path above. Do not import it into the game until reviewed.",
        ]
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written += 1
        print(f"- {player.get('full_name', 'Unknown')}: {rel_path(destination)}")
        if reference_image:
            print(f"  Reference: {rel_path(reference_image)}")
        if staged_path:
            print(f"  Save result to: {staged_path}")

    print("")
    print(f"Packets written: {written}")


def add_generation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--jobs-dir", type=Path, default=PORTRAIT_JOBS_DIR)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--job-id", action="append", help="Generate only this job id. Can be repeated.")
    parser.add_argument("--limit", type=int, default=1, help="Max jobs to select unless --all is used. Default: 1.")
    parser.add_argument("--all", action="store_true", help="Select every matching job. Requires --allow-batch.")
    parser.add_argument("--allow-batch", action="store_true", help="Allow more than one job to be processed.")
    parser.add_argument("--apply", action="store_true", help="Actually call OpenAI and write staged images.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing staged image for selected jobs.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between generated jobs.")
    parser.add_argument("--model", default=os.environ.get("NFL_GM_IMAGE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--quality", default=DEFAULT_QUALITY, choices=["low", "medium", "high", "auto"])
    parser.add_argument("--output-format", default=DEFAULT_OUTPUT_FORMAT, choices=["png", "jpeg", "webp"])
    parser.add_argument("--output-compression", type=int, choices=range(0, 101), metavar="0-100")
    parser.add_argument("--background", default=DEFAULT_BACKGROUND, choices=["opaque", "auto"])
    parser.add_argument(
        "--reference-image",
        type=Path,
        help="Override/use one repo-local reference image for the selected jobs. Uses OpenAI image edits.",
    )
    parser.add_argument(
        "--no-reference-image",
        action="store_true",
        help="Ignore reference images stored in queued jobs and force text-only generation.",
    )
    parser.add_argument("--api-base", default=os.environ.get("OPENAI_BASE_URL", DEFAULT_API_BASE))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--timeout", type=int, default=180)


def add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--jobs-dir", type=Path, default=PORTRAIT_JOBS_DIR)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--job-id", action="append", help="Export only this job id. Can be repeated.")
    parser.add_argument("--limit", type=int, default=1, help="Max jobs to select unless --all is used. Default: 1.")
    parser.add_argument("--all", action="store_true", help="Select every matching job. Requires --allow-batch.")
    parser.add_argument("--allow-batch", action="store_true", help="Allow more than one job to be processed.")
    parser.add_argument(
        "--reference-image",
        type=Path,
        help="Override/use one repo-local reference image for the selected jobs.",
    )
    parser.add_argument(
        "--no-reference-image",
        action="store_true",
        help="Ignore reference images stored in queued jobs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PORTRAIT_JOBS_DIR / "manual_packets",
        help="Directory where manual prompt packets are written.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="Summarize a queued portrait run.")
    summary.add_argument("--jobs-dir", type=Path, default=PORTRAIT_JOBS_DIR)
    summary.add_argument("--run-id", required=True)
    summary.set_defaults(func=action_summary)

    generate = subparsers.add_parser("generate", help="Dry-run or apply OpenAI image generation.")
    add_generation_args(generate)
    generate.set_defaults(func=action_generate)

    manual = subparsers.add_parser("manual-packet", help="Export a no-API prompt/reference packet for ChatGPT/Codex.")
    add_selection_args(manual)
    manual.set_defaults(func=action_manual_packet)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (FileNotFoundError, PortraitGenerationError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
