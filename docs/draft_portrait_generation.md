# Draft Portrait Generation Pipeline

Goal: after a generated draftee is assigned to an NFL team, prepare a realistic
fictional portrait prompt in the background, stage the image, and only import it
into the playable game after review.

The pipeline has two separate tools:

- `prepare_draft_portrait_jobs.py` creates prompt/job files only.
- `generate_draft_portraits.py` can call OpenAI's Image API, but defaults to a
  dry run and keeps all images staged for review.

## Why A Job Queue

Portrait generation should not block the draft, roster screens, or save loading.
The game can queue portrait jobs after selections, let a background worker create
images later, then show a placeholder until an image is approved.

The important separation is:

- `queued` - prompt payloads waiting for a future image worker.
- `staged` - generated image files that exist but are not used by the game.
- `approved/imported` - images copied to the normal player graphics location and
  written to `player_graphics_assets`.

## Current Tool

Prepare jobs from selected draftees in the database:

```powershell
python tools\prepare_draft_portrait_jobs.py from-db --draft-year 2027 --apply
```

Prepare jobs for one team:

```powershell
python tools\prepare_draft_portrait_jobs.py from-db --draft-year 2027 --team MIN --apply
```

Prepare sample jobs from a CSV without touching the database:

```powershell
python tools\prepare_draft_portrait_jobs.py from-csv --csv data\draft\real_projection\2027\2027_real_projected_class_starter.csv --team MIN --team-name "Minnesota Vikings" --limit 5 --apply
```

Omit `--apply` for a dry run.

## Reference Images

For more realistic team-style portraits, add a roster-photo reference image.
The reference is used for crop, lighting, background, jersey-color direction,
and media-day composition only. The prompt still tells the model to create a
new fictional player from the prospect metadata and not copy the reference
person's face or identity.

Recommended layout:

```text
graphics\players\portrait_refs\MIN\media_day_base.png
graphics\players\portrait_refs\CHI\media_day_base.png
```

When a queued player's team is `MIN`, `prepare_draft_portrait_jobs.py` will
automatically attach the Minnesota reference if that file exists:

```powershell
python tools\prepare_draft_portrait_jobs.py from-csv --csv data\draft\generated\2027_draft_class_preview.csv --team MIN --team-name "Minnesota Vikings" --limit 1 --apply
```

You can also force one reference image for a run:

```powershell
python tools\prepare_draft_portrait_jobs.py from-csv --csv data\draft\generated\2027_draft_class_preview.csv --team MIN --team-name "Minnesota Vikings" --limit 1 --reference-image graphics\players\portrait_refs\MIN\media_day_base.png --apply
```

## OpenAI Generation Worker

Summarize an existing queued run:

```powershell
python tools\generate_draft_portraits.py summary --run-id example_2027_melvin_baccellia
```

Preview what would generate. This does not call OpenAI:

```powershell
python tools\generate_draft_portraits.py generate --run-id example_2027_melvin_baccellia
```

Generate exactly one staged portrait:

```powershell
$env:OPENAI_API_KEY="sk-..."
python tools\generate_draft_portraits.py generate --run-id example_2027_melvin_baccellia --apply
```

If a queued job has a reference image, the worker uses OpenAI image edits
instead of plain text-to-image generation. You can also provide the reference at
generation time:

```powershell
python tools\generate_draft_portraits.py generate --run-id example_2027_melvin_baccellia --reference-image graphics\players\portrait_refs\MIN\media_day_base.png --apply
```

Use `--no-reference-image` only when you deliberately want text-only image
generation.

The worker defaults to one selected job. More than one job requires
`--allow-batch`, and generating an entire queued class requires both `--all` and
`--allow-batch`:

```powershell
python tools\generate_draft_portraits.py generate --run-id draft_2027_portraits --limit 5 --allow-batch --apply
python tools\generate_draft_portraits.py generate --run-id draft_2027_portraits --all --allow-batch --apply
```

Existing staged image files are skipped unless `--force` is passed. This is
intentional so a placeholder, reviewed image, or earlier paid generation is not
overwritten by accident.

The default model is `gpt-image-2`, with 1024x1024 PNG output at high quality.
You can override this:

```powershell
python tools\generate_draft_portraits.py generate --run-id draft_2027_portraits --model gpt-image-2 --quality medium --apply
```

## No-API Manual Generation

ChatGPT/Codex subscriptions and OpenAI API billing are separate. If you want to
avoid local API billing, export a manual packet and generate the image in the
ChatGPT/Codex interface instead:

```powershell
python tools\generate_draft_portraits.py manual-packet --run-id example_2027_melvin_baccellia --reference-image graphics\players\portrait_refs\MIN\media_day_base.png
```

The packet is written under:

```text
graphics\players\portrait_jobs\manual_packets\<run_id>\
```

Open the Markdown packet, upload or attach the reference image listed there,
paste the prompt into ChatGPT/Codex, then save the generated PNG to the packet's
staged output path. This path does not call `OPENAI_API_KEY`, so it avoids API
usage charges from the project scripts.

## Output Layout

The tool writes under `graphics\players\portrait_jobs`:

- `queued\<run_id>.jsonl`
- `prompts\<run_id>\*.txt`
- `manifests\<run_id>.json`
- `staged\<run_id>\`

Future approved portraits should land at:

```text
graphics\players\<TEAM>\portraits\<player_id>_<player_slug>.png
```

Then a future importer should add a `player_graphics_assets` row with:

- `asset_type = 'portrait'`
- `variant = 'generated_rookie'`
- `source_name = 'generated portrait pipeline'`
- `local_path = graphics/players/<TEAM>/portraits/...`

## Prompt Rules

The prompt builder intentionally includes height, weight, position, college,
team assignment, archetype, and appearance metadata when available. For generated
draft prospects, it can also include eye color, hair color, hairstyle, facial
hair, handedness, and appearance notes from `draft_prospects`.

For CSV-driven jobs, the prompt also includes a compact copy of the full CSV row
so fields such as combine notes, role, risk, ethnicity, hairstyle, facial hair,
height, and weight stay available to the model.

Prompts should always say:

- The person is fictional and original.
- Do not resemble a real athlete, celebrity, or public figure.
- If a reference image is supplied, use it for roster-photo style only and do
  not copy that person's face or identity.
- Use generic team-inspired colors only.
- Do not include official NFL marks, team logos, brand marks, captions, or
  watermarks.
- Keep the output as a square media-day headshot.

For real-player draft classes, prefer licensed real headshots or explicitly
generate fictional placeholder portraits that do not attempt to match the real
person.

## Worker Behavior

The generation worker reads each selected JSONL job, appends a photorealistic
media-day quality guard to the prompt, calls OpenAI's Image API only when
`--apply` is present, writes the returned image bytes to `staged_path`, and
updates the queue/manifest with generation status, model, request id, dimensions,
and review notes.

The worker does not approve, import, or attach the portrait to the playable
database. That remains a separate review/import step.

## Future Approval Import

A later import command should:

1. Read staged images for a run.
2. Require human approval or an explicit `--approve-all`.
3. Copy approved images into `graphics\players\<TEAM>\portraits`.
4. Insert or update `player_graphics_assets`.
5. Leave rejected images in staging or move them to `rejected`.
