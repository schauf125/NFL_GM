# Draft Engine

The draft engine owns generated draft classes and prospects before they enter
the main player universe.

## Current Boundary

- `draft_classes` stores one row per draft year and tracks generation metadata.
- `draft_prospects` stores generated prospect identity, projections, summary
  grades, scouting status, fictional ethnicity/origin metadata used by name
  generation, and the eventual selected pick/team.
- `draft_prospect_ratings`, `draft_prospect_role_assignments`, and
  `draft_prospect_role_scores` store the normalized hidden match-engine profile
  for prospects before they become real players.
- `draft_prospect_combine_results` stores public workout participation, skipped
  drills, drill results, and a combine grade that usually follows the hidden
  athletic profile but can be noisy or unavailable.
- `draft_prospect_pro_day_results` stores supplemental pro-day testing, which
  can fill combine gaps, confirm testing, or create a noisy better/worse second
  workout signal.
- `draft_prospect_private_workouts` stores hidden private-workout/interview
  hooks for future scouting, medical, development, contract, and AI GM logic.
- `draft_prospect_scouting_notes` stores user or system scouting notes.
- Prospects should not become rows in `players` until a draft pick is made or
  a later undrafted-free-agent flow intentionally signs them.

This keeps future draft classes out of active rosters, free agency, cap
accounting, and stats until the game has a real transaction for them.

Name generation uses `data/draft/names/name_pool.db`. The ethnicity and origin
fields are fictional prospect-generation metadata and should not be used to
classify real people. Preview generation keeps the overall class mix near the
configured targets, but softly weights U.S.-born ethnicity assignment by
position so QBs, specialists, corners, running backs, and line positions feel
closer to modern NFL roster patterns while still allowing outliers.
Position-aware preview tuning lives in
`data/draft/generation/preview_config.json`, which also stores generation
version and handedness/kicking-foot weights.

Physical generation uses `data/draft/physical/physical_profiles.db`. Height,
weight, arm length, and hand size are sampled from position-specific NFL
measurement distributions with a small chance of outlier tails. Strong safeties
and long snappers get small generation-time weight nudges so sparse source pools
do not make those roles too light.

Appearance generation uses `data/draft/appearance/appearance_traits.json` for
eye color, ethnicity-weighted hair color and hairstyle, facial hair, and one or
occasionally two fictional ethnicity descriptors for later AI portrait prompts.
Facial hair is age-aware so older prospects carry slightly more beard/stubble
variation.

College and age generation uses `data/draft/colleges/college_pool.db`. The age
model uses draft-board buckets: Round 1 is mostly 21-22, Rounds 2-3 loosen toward
23, Rounds 4-5 add older breakout prospects, and Rounds 6-7 plus leftovers have
the largest older tail until real ratings can drive draft-board placement.

Archetype generation is body-aware. Weighted choices still allow unusual
profiles, but clear body mismatches are relabeled after selection so heavy edge
players skew toward power/run-setting roles, light nose tackles become
penetrators, big tight ends become inline/blocking types, and fullbacks get
fullback-specific labels instead of generic balanced profiles.

After ratings are rolled, prospects also pass an archetype identity QA check.
This keeps toolsy projection risks intact, but prevents common contradictions
where the label says one thing and the core traits say another. If at least two
core identity traits miss the threshold, the player is usually relabeled to the
closest honest archetype. A tiny capped illusion bucket can keep rare public
projection labels for future scouting misses, but the validation report flags
those counts.

Kickers and punters use a specialist-specific rating curve instead of the normal
late-round development curve. Drafted specialists are usually near low-end NFL
starter quality right away with small ceiling gaps, while late leftovers can
still be camp legs, misses, or occasional specialists who outplay their board
slot.

Hidden draft-prospect personalities use the shared
`personality_trait_definitions` catalog from the NFL player personality system,
but attach traits to `draft_prospects` through `draft_prospect_personalities`.
Use `tools/draft_personalities.py setup` to create the tables and
`tools/draft_personalities.py apply --draft-year YEAR --seed SEED --apply` once a
draft class has persisted prospects. Runs are deterministic by seed and refuse
to overwrite an existing class run unless `--force` is passed. The normal draft
reports do not display these traits; they are hidden future scouting/interview,
development, contract, and AI GM inputs. The generator applies basic
compatibility rules so hard-clashing trait combinations are avoided and natural
trait clusters are more likely.

Scouting-report language uses `data/draft/scouting/`. The report generator is a
noisy lens over hidden prospect ratings: scout lenses can be more accurate,
optimistic, conservative, traits-focused, or wrong by a tier. Future game systems
can swap those lenses by team, scout, visit, interview, budget, or report source
without changing the underlying true player profile.

Combine generation uses `engine/draft/combine.py`. Drill results are position
aware and tied to hidden speed, acceleration, agility, strength, balance,
durability, kicking, and body size, but workout-day variance can create false
positives or false negatives. Players can skip individual drills, work out only
partially, miss the workout because of injury, or strategically wait for pro day,
with top prospects more likely to opt out of parts of the process.

Pro-day and private-workout generation lives in `engine/draft/workouts.py`.
Pro days are public-ish and can improve, confirm, or muddy the combine profile.
Private workouts are hidden rows by default and should not appear in normal user
draft screens.

Use `tools/generate_draft_class.py --year 2027 --count 330 --seed 2027 --apply`
to persist a class into SQLite. Omit `--apply` for a dry run. The tool writes a
public HTML/CSV board and a developer validation report unless `--no-preview` is
passed. Use `--include-hidden-preview` only for developer QA; public outputs hide
true rank, true grade, ceiling, dev trait, true risk, role scores, raw ratings,
and private-workout notes.

Use `tools/generate_draft_class_preview.py` when you only want preview files.
Preview/generation starts from a hidden true rank, then creates a noisy public
board rank from scouting reports, workouts, position value, and variance. That
lets first-round busts, scouting misses, and late sleepers exist before the user
ever sees the class. Top true tiers still shape birthplace/age tendencies: top
prospects skew heavily U.S.-born, the second tier favors Canada/Nigeria with
specialist Australia/Europe kickers and punters, the third tier favors
Germany/UK/Australia, and late-tier international outliers come from a wider set
of countries. International origin weights are also position-aware, so Polynesian
origins land more often on OL/DL/front-seven prospects and Australia/Europe are
more common for specialists.

Use `tools/validate_draft_class.py preview --csv PATH` for a preview artifact or
`tools/validate_draft_class.py db --draft-year YEAR` for a persisted class. The
DB validation checks public-vs-true board noise, workout distributions, side
table row counts, duplicate public ranks, and missing hidden rating rows.

Use `tools/select_draft_pick.py` when the draft is actually making selections.
`board` lists available prospects, `picks` lists owned picks, and `select`
promotes one available prospect into the main game universe. Selection creates
the `players` row, copies normalized ratings and role scores, adds primary
position flex, creates a rookie-scale contract, marks the pick and prospect as
used, writes a transaction, and carries over hidden draft personalities when an
active save game id exists. Omit `--apply` for a dry run.
