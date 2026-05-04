# Draft Scouting Language

This directory holds modular language and scout-lens tuning for generated
prospect reports.

- `rating_phrases.json` maps normalized match-engine rating keys to short
  strength and concern phrases.
- `report_templates.json` holds report framing, projection language, risk notes,
  usage notes, development notes, scout-lens notes, and general variance notes.
- `scout_lenses.json` defines how noisy or biased a report can be. The generator
  treats scouting reports as opinions over true prospect ratings, so future
  scouts can be more accurate, more optimistic, more conservative, or plainly
  wrong in specific areas.

The goal is to keep report text editable without changing Python code.
