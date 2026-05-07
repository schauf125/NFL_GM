# 2027 Real Projected Draft Class Starter

This folder is a research/projection sandbox. It is intentionally separate from
`data/draft/generated/` and is not imported into `database/nfl_gm.db`.

The CSV is a starter board built on April 30-May 1, 2026 from public early 2027
draft boards and mock drafts. It is meant to become source material for a later
real-class importer, not a polished playable draft class.

## Files

- `2027_real_projected_class_starter.csv` - first 50-prospect starter board.

## Source Keys

- `PFF_2026_04_28` - PFF early 2027 top 75 big board.
- `PFSN_2027_BIG_BOARD` - PFSN 2027 big board/prospect rankings.
- `NBC_2026_04_27` - NBC Sports way-too-early 2027 Round 1 mock.
- `CBS_2026_04_26` - CBS Sports way-too-early 2027 Round 1 mock.
- `FOX_KLATT_2026_04_28` - Joel Klatt/Fox Sports way-too-early top 10.
- `SPORTING_VIA_CHARGERS_2026_04_25` - Sporting News top 25 as reposted by Chargers.com.

## Import Notes

- `starter_rank` is a hand-built consensus starting point, not a claim that the
  board is final.
- Heights/weights are public listed values when available and should be
  rechecked before import.
- `projected_round` is deliberately broad. Most early projection error will come
  from juniors returning to school, transfers, injuries, and 2026 breakouts.
- `archetype_hint` is for future rating generation. It is not yet a real
  `draft_prospects` archetype.
- `risk_note` should eventually be converted into true hidden risk, public
  scouting risk, and medical/workout variance.

