# QB Behavior Traits

These traits describe how a quarterback behaves inside the tick engine. They
are not hidden personality traits and do not represent off-field character.
They are engine-facing football tendencies.

All traits use a 0-100 scale. Around 50 is neutral.

- `rhythm`: How quickly the QB plays on schedule when the concept is available.
- `pocket_discipline`: How reliably the QB stays tied to the intended pocket and timing.
- `pocket_drift`: How often the QB moves, resets, or dances behind the line before pressure fully arrives.
- `checkdown_willingness`: How readily the QB takes outlets and hot answers.
- `deep_aggression`: How much the QB prefers deeper throws and late-developing routes.
- `pressure_escape`: Ability to avoid or shake pressure once it arrives.
- `broken_play_creation`: Ability to turn disrupted plays into throws, scrambles, or explosives.
- `scramble_trigger`: Tendency to leave the pocket and become a runner.
- `sack_risk`: Chance that drifting, holding the ball, or poor pressure answers become sacks.
- `throwaway_discipline`: Tendency to end the play safely instead of taking a sack or forcing a throw.

## How The Tick Engine Uses Them

- Concept selection tilts toward quick game/checkdowns for rhythm/checkdown QBs and toward deep concepts for aggressive creators.
- Decision cadence is faster for rhythm pocket passers and slower for high-drift, low-rhythm creators.
- Read progression delays outlets for low-checkdown QBs and lets deep/aggressive QBs keep deeper routes alive.
- Pressure timing and pressure chance rise for high-drift/high-sack-risk QBs.
- Under pressure, high-checkdown QBs are more likely to throw hot or throw away.
- High-creation QBs are more likely to extend, scramble, or break out of pressure.
- High-sack-risk QBs take deeper negative sacks when they fail to escape.
- Completion probability is trimmed for off-schedule, high-drift throws, while broken-play skill can recover a little of that penalty.

## Current Examples

- Caleb Williams: `Backfield Creator`
  High pocket drift, high broken-play creation, high sack risk, lower checkdown willingness. This should produce lower completion rate, more pressure volatility, more broken-play throws, occasional big escapes, and some deep negative sacks.
- Kyler Murray: `Compact Escape Artist`
  Elite pressure escape and scramble trigger, but less extreme backfield drift than Caleb. This should create more escape/scramble value without the same degree of sack volatility.
- Joe Burrow: `Rhythm Surgeon`
  High rhythm, high pocket discipline, high throwaway discipline. This should create quick decisions, fewer chaotic sacks, and more schedule throws.
- Patrick Mahomes: `Controlled Improviser`
  High broken-play creation without Caleb-level sack risk. This should keep the chaos positive more often.

## Coverage

The engine resolves QB behavior in three layers:

- Named real-QB overrides in `engine/qb_behavior.py` for starters and notable backups.
- Stored profiles in `player_qb_behavior_profiles`, used for generated rookies and any manually seeded player profiles.
- Rating-inferred fallback from current QB ratings when neither of the above exists.

Generated draft classes also create `draft_prospect_qb_behavior_profiles` for
QB prospects. When a QB prospect is drafted or signed as a UDFA, that profile is
copied into `player_qb_behavior_profiles`, so fictional rookies keep their
archetype-shaped behavior after becoming normal players.
