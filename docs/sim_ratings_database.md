# Sim Ratings Database Integration

The sim-rating system lives alongside the legacy ratings on `players`.

Do not remove or rewrite the legacy columns yet:

- `players.overall`
- `players.speed`
- `players.strength`
- `players.agility`
- `players.awareness`
- `players.throw_power`
- `players.throw_acc`
- other broad position ratings

Those columns can keep existing UI/tools working while the tick engine moves to normalized ratings.

## Setup Script

Run:

```powershell
python database\setup_sim_ratings.py
```

The script is idempotent. It:

- Creates a timestamped backup of `database\nfl_gm.db`.
- Creates normalized sim-rating tables.
- Seeds rating definitions.
- Seeds hidden role-score definitions.
- Seeds role-score weights.
- Seeds Vikings pilot role assignments for the 2026 season.

Use this only when intentionally changing the schema/seed definitions:

```powershell
python database\setup_sim_ratings.py --no-backup
```

## Sim Rating Seed

Run:

```powershell
python database\seed_sim_ratings.py --team MIN
```

Or seed every rostered team:

```powershell
python database\seed_sim_ratings.py --all-teams
```

The script is idempotent for generated 2026 rows. It:

- Creates a timestamped backup of `database\nfl_gm.db`.
- Replaces generated sim-rating rows from `sim_ratings_generated`, plus the earlier Vikings pilot sources.
- Generates sim ratings for current rostered players in the database.
- Assigns hidden roles for non-specialists.
- Calculates cached hidden role scores.

Specialists are rated but do not receive hidden role scores yet because specialist role weights are intentionally deferred.

Historical note: `database\seed_vikings_sim_ratings.py` still contains the implementation, but `database\seed_sim_ratings.py` is the preferred entry point.

## New Tables

### `rating_definitions`

One row per engine rating.

Examples:

- `speed`
- `processing_speed`
- `pass_accuracy_short`
- `route_snap`
- `pass_block_speed`
- `speed_rush`
- `zone_coverage`
- `solo_tackle`

### `player_ratings`

Long-form player rating values.

Primary key:

```text
player_id, season, rating_key
```

This is where regenerated player ratings should go.

Example:

```sql
INSERT INTO player_ratings (
  player_id, season, rating_key, rating_value, confidence, source, notes
)
VALUES (
  10, 2026, 'route_snap', 97, 'high', 'vikings_rating_pilot', 'Initial calibrated rating'
);
```

### `role_score_definitions`

Hidden role definitions.

Examples:

- `boundary_wr`
- `slot_wr`
- `scrambling_qb`
- `move_te`
- `interior_rusher`
- `coverage_lb`
- `deep_safety`

These are internal and should not be shown as public Overall ratings.

### `role_score_weights`

Rating weights for each hidden role score.

Every seeded role currently totals 100 weight points.

### `player_role_assignments`

Assigns player-role fits by season.

Primary Vikings pilot roles have `priority = 1`.
Secondary roles have `priority = 2`.

### `player_role_scores`

Cached hidden role scores once numeric ratings exist.

Primary key:

```text
player_id, season, role_key, scheme_key
```

Use `scheme_key = 'default'` until the scheme system exists.

## Views

### `player_sim_ratings_view`

Readable player ratings with player/team/rating metadata.

### `player_role_assignments_view`

Readable role assignments with player/team/role metadata.

### `player_role_scores_view`

Readable cached role scores.

## Engine Read Path

The tick engine should read from:

```text
player_ratings
rating_definitions
player_role_assignments
role_score_weights
player_role_scores
```

The engine should use `players` for physical profile fields:

- `height_in`
- `weight_lbs`
- `age`
- `position`
- `team_id`

## Hidden Role Score Calculation

Once a player has enough rows in `player_ratings`, a role score can be calculated as:

```text
role_score =
  sum(player_rating_value * role_weight)
  / sum(role_weight)
```

Later modifiers can be added around that base:

- scheme fit
- availability
- age/development curve
- injury limitations
- coach preference

The score should remain internal.

The first generated Vikings pass uses a temporary readiness anchor:

```text
generated_role_score =
  weighted_rating_score * 0.75
  + legacy_overall_readiness * 0.25
```

This keeps early generated role scores from overvaluing one elite athletic trait before the true sim ratings are manually reviewed.

The generator also applies position-aware relevance caps. Ratings outside a player's real football usage are capped by position and body type. For example:

- QBs keep passer ratings, and mobile QBs can keep ball-carrier ratings.
- QB defensive ratings are capped in the teens, with smaller players capped lower.
- Bigger offensive players like TEs/FBs can cap a little higher for contact/emergency situations, but still stay far below real defensive players.
- Defensive players keep defensive ratings, while unrelated offensive skill ratings are capped unless the position plausibly uses them after turnovers.

## Current State

After running `setup_sim_ratings.py`:

- `rating_definitions` is seeded.
- `role_score_definitions` is seeded.
- `role_score_weights` is seeded.
- Vikings pilot `player_role_assignments` are seeded.

After running `seed_sim_ratings.py --all-teams`:

- Rostered-team `player_ratings` rows are generated for the 2026 season.
- Non-specialist `player_role_assignments` are generated for the 2026 season.
- Non-specialist `player_role_scores` are calculated for the 2026 season.

Useful review queries:

```sql
SELECT player_name, position, role_name, role_score
FROM player_role_scores_view
WHERE team = 'MIN'
  AND season = 2026
ORDER BY role_score DESC, player_name;
```

```sql
SELECT player_name, rating_group, display_name, rating_value, confidence
FROM player_sim_ratings_view
WHERE team = 'MIN'
  AND season = 2026
  AND player_name = 'Justin Jefferson'
ORDER BY rating_group, display_name;
```

## Next Step

Review and manually tune the Vikings generated ratings in:

```text
player_ratings
```

Start with high-impact players and role anchors:

- Justin Jefferson
- Christian Darrisaw
- Kyler Murray
- T.J. Hockenson
- Andrew Van Ginkel
- Byron Murphy Jr.
- Aaron Jones Sr.

Then recalculate and cache hidden role scores in:

```text
player_role_scores
```
