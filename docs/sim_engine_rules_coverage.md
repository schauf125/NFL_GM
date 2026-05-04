# Sim Engine Rules Coverage Matrix

This document tracks whether `engine/match_engine.py` covers the major rules and game-flow mechanics needed for an NFL-style simulation.

Rule references were checked on 2026-05-04 against NFL Football Operations:

- 2026 approved rules summary: https://operations.nfl.com/updates/the-game/approved-2026-playing-rules-bylaws-and-resolutions/
- NFL rulebook / Rule 16 overtime: https://operations.nfl.com/the-rules/nfl-rulebook/
- NFL overtime explainer: https://operations.nfl.com/the-rules/nfl-overtime-rules/
- Dynamic kickoff explainer: https://operations.nfl.com/the-rules/rules-changes/new-dynamic-kickoff-rule-explainer/
- NFL video rulebook index: https://operations.nfl.com/the-rules/nfl-video-rulebook/kickoff-rules/

## Status Key

- `Implemented`: Present in the engine with usable game impact.
- `Abstracted`: Represented at a coarse level, but missing detail or individual stat fidelity.
- `Partial`: Some important pieces exist, but the rule family is incomplete.
- `Missing`: Not represented.
- `Incorrect`: Present, but conflicts with current NFL rules or intended game behavior.

## Current Engine Baseline

- File: `engine/match_engine.py`
- Version: `0.1.6`
- Core model: per-play probabilistic engine with tenths-of-a-second game clock.
- Not yet a true spatial 0.1-second player-movement simulation.

## Coverage Matrix

| Area | Mechanic | Current Status | Notes / Gap |
| --- | --- | --- | --- |
| Game structure | Four 15-minute quarters | Implemented | Regulation clock runs in tenths. |
| Game structure | Halftime transition | Implemented | Possession flips to second-half receiver at halftime. |
| Game structure | Coin toss / opening choice | Abstracted | Randomly chooses first-half receiver. No kick/defer/receive choice. |
| Game structure | Game clock runoff | Partial | Live time, runoff, timeout interruption, and two-minute-warning stoppage exist; restart rules remain simplified. |
| Game structure | Play clock / delay of game | Partial | Delay of game exists as a penalty branch, but there is no explicit play-clock state. |
| Game structure | Timeouts | Partial | Half and OT inventories exist with basic late-game usage. Strategy is still coarse. |
| Game structure | Two-minute warning | Implemented | Q2/Q4 clock stops at 2:00 once when a play would cross it. |
| Game structure | End-of-half strategy | Partial | Late pass-rate adjustments, timeouts, spikes, and kneels exist; hurry-up/field-goal unit timing is still simplified. |
| Overtime | 10-minute regular-season OT | Implemented | Uses 10-minute overtime period. |
| Overtime | Current both-teams-possession rule | Implemented | First-possession TD no longer walks off. Defensive/special-teams return TDs and safeties can still end OT immediately. |
| Overtime | OT timeouts | Implemented | Each team resets to two OT timeouts. |
| Overtime | Regular-season tie | Implemented | OT can expire tied after the possession rules are satisfied. |
| Possession | Starting drives after scoring | Implemented | Scores now trigger free kicks instead of automatic own-25 starts. |
| Possession | Opening kickoff | Implemented | Game begins with an opening kickoff event. |
| Possession | Second-half kickoff | Implemented | Halftime resumes with a kickoff to the second-half receiver. |
| Possession | Dynamic kickoff alignment / landing zone | Partial | Landing-zone, short kick, out-of-bounds, touchback, return, and TD branches exist. Alignment/player movement is abstracted. |
| Possession | Touchback spots | Implemented | Dynamic kickoff B20/B35/B40-style outcomes are modeled. |
| Possession | Kickoff returns | Partial | Returner, return yardage, return TDs, and return stats exist; coverage tackles/blocks are not yet attributed. |
| Possession | Onside kicks | Partial | Declared late-game onside attempts/recoveries exist; detailed 2026 declaration and formation rules are abstracted. |
| Possession | Safety kick | Implemented | Safeties restart with a safety-kick free kick. |
| Possession | Punt | Partial | Punts now include returner stats, fair catches, blocks, and return TDs. Coverage player stats and detailed punt rules remain abstracted. |
| Possession | Punt touchback | Implemented | Touchback to 20 exists. |
| Possession | Fair catch | Partial | Punt fair catches exist. Kickoff fair-catch/free-catch nuance is not modeled. |
| Possession | Turnover on downs | Implemented | Basic field-position flip exists. |
| Scoring | Touchdown | Implemented | Rushing and passing TDs exist. |
| Scoring | Extra point | Implemented | XP attempts/makes exist. |
| Scoring | Two-point conversion | Implemented | Two-point attempts, makes, and player stat attribution exist with score-aware late-game decisions. |
| Scoring | Field goal | Implemented | FG attempts/makes/long exist. |
| Scoring | Missed field goal spot | Partial | Basic miss spot and blocked-FG return branches exist; missed-FG returns are deferred. |
| Scoring | Safety | Implemented | Offensive losses into own end zone score safeties and trigger safety kicks. |
| Scoring | Defensive / special teams TD | Implemented | INT, fumble, punt, kickoff, and blocked-kick return TDs can score. |
| Scoring | Defensive conversion score | Missing | No returned try result. |
| Downs | Down and distance | Implemented | Basic progression works. |
| Downs | First downs | Implemented | First-down team stat is tracked. |
| Downs | Sacks | Implemented | Sack stat tuning improved in engine 0.1.1. |
| Downs | Kneel-downs | Implemented | Late leading teams can close games once the defense is out of timeouts. |
| Downs | Spikes | Implemented | Late trailing teams without timeouts can spike in field-goal territory. |
| Passing | Completion/incompletion | Implemented | Completion chance uses QB, receiver, coverage, depth, pressure. |
| Passing | Interceptions | Implemented | QB `interceptions_thrown` and defender `interceptions` are split. |
| Passing | Pass breakups | Implemented | Defender pass deflections are tracked. |
| Passing | Intentional grounding | Partial | Exists as an offensive penalty branch; pressure/pocket/receiver proximity are not explicitly modeled. |
| Passing | Catch rules | Abstracted | Catch/no-catch is folded into completion chance. |
| Rushing | Designed runs | Implemented | RB/QB run outcomes exist. |
| Rushing | Fumbles | Implemented | Fumbles and recoveries exist. |
| Rushing | Fumble return TDs | Implemented | Rush and catch fumbles can be returned for defensive touchdowns. |
| Rushing | Fumble through end zone | Missing | No touchback/safety/end-zone fumble rule. |
| Penalties | False start | Implemented | Basic offensive pre-snap penalty exists. |
| Penalties | Offensive holding | Implemented | Basic offensive live-ball penalty exists. |
| Penalties | Defensive offside | Implemented | Basic defensive penalty exists. |
| Penalties | Defensive holding | Implemented | Basic automatic first down exists. |
| Penalties | DPI / OPI | Partial | DPI is a spot-foul style automatic first down; OPI is enforced from the previous spot. Receiver/contact details remain abstracted. |
| Penalties | Illegal contact | Partial | Illegal contact exists as a defensive pass penalty branch with automatic-first-down enforcement. |
| Penalties | Roughing passer/kicker | Partial | Roughing the passer exists; roughing/running into the kicker is still missing. |
| Penalties | Facemask / personal fouls | Partial | Facemask and unnecessary roughness branches exist with simplified enforcement. |
| Penalties | Intentional grounding | Partial | Intentional grounding exists as an offensive pass penalty with loss-of-down enforcement. Pocket/pass-location details remain abstracted. |
| Penalties | Offset/decline/enforcement complexity | Partial | Accepted, declined, offsetting, half-distance, previous-spot, spot-foul, dead-ball, automatic-first-down, and loss-of-down handling exist for scrimmage plays. Full NFL special cases are not complete. |
| Special teams | FG/XP snap counts | Implemented | Specialist snap counts added in engine 0.1.2. |
| Special teams | Punt snap counts | Implemented | Specialist snap counts added in engine 0.1.2. |
| Special teams | Kickoff snap counts | Implemented | Kickoff/safety-kick and return-team special teams snaps are counted. |
| Special teams | Blocks | Partial | Blocked punts and field goals exist. Blocked extra points and detailed recovery rules are deferred. |
| Special teams | Long snapper usage | Partial | LS snaps are counted when depth chart has LS, but specialist play resolution does not depend on LS quality. |
| Strategy | Fourth-down decisions | Implemented | Basic go/FG/punt logic exists. |
| Strategy | Two-point decisions | Implemented | Late-game score-aware two-point choices exist. |
| Strategy | Timeout usage | Partial | Basic trailing-offense and trailing-defense timeout usage exists. |
| Strategy | Hurry-up / chew-clock | Partial | Pass/run rate shifts late; no explicit tempo state. |
| Strategy | Onside-kick decisions | Partial | Late trailing teams can attempt declared onside kicks. |
| Personnel | Offensive starters | Implemented | Uses depth charts and fallback slot scoring. |
| Personnel | Defensive starters | Implemented | Uses front/LB/secondary depth slots. |
| Personnel | Snap counts | Implemented | Offense, defense, special teams, and total snaps are tracked. |
| Personnel | Substitution packages | Partial | Heavy/nickel personnel are approximated; no stamina or rotational substitution. |
| Personnel | Injuries | Missing | Durability exists as a rating but no injury events. |
| Environment | Weather | Missing | No weather/stadium effects on passing/kicking/fumbles. |
| Environment | Home field | Missing | No crowd/noise/travel effects. |
| Review | Replay/challenge | Missing | Can be safely deferred. |
| Stats | Team/player box score | Implemented | Core passing/rushing/receiving/defense/kicking stats exist. Defensive tackles now split solo/assisted while keeping combined tackles. |
| Stats | Return stats | Partial | Kickoff, punt, INT, fumble, and blocked-kick return stats exist. Coverage tackles and return-team blocking stats are missing. |
| Stats | Penalty player attribution | Missing | Team penalties exist; no offender/drawn-by player stats. |
| Stats | Drive/play persistence | Implemented | Game runs, drives, plays, team stats, and player stats persist. |

## Completed `0.1.3` Milestone

Engine version `0.1.3` adds the first broad rules pass:

- Current-style overtime possession opportunity rules, including defensive/special-teams score and safety walk-off exceptions.
- Opening, second-half, post-score, overtime, onside, and safety-kick free kicks.
- Dynamic kickoff touchback, landing-zone, short-kick, out-of-bounds, return, and return-TD outcomes.
- Two-point conversion decisions, attempts, makes, and player/team stats.
- INT, fumble, kickoff, punt, blocked-punt, and blocked-FG return touchdowns.
- Timeouts, two-minute warnings, kneel-downs, and spikes.
- Expanded first-pass penalty branches for DPI, OPI, illegal contact, roughing the passer, facemask, unnecessary roughness, intentional grounding, delay of game, and illegal formation.
- Punt returns, fair catches, blocked punts, blocked field goals, and broader special-teams snap counts.

Engine version `0.1.4` adds the first penalty-enforcement pass:

- Dead-ball/no-play flags such as false start, delay of game, and neutral zone infraction.
- Live-ball flags that can be accepted or declined after the play result is known.
- Offsetting live-ball fouls that replay the down.
- Half-distance enforcement near the goal line.
- Previous-spot, spot-foul, dead-ball, and best-of-previous/dead-ball style enforcement.
- Defensive penalty first downs, declined-penalty counts, and offsetting-penalty counts.

Engine version `0.1.5` adds the first NFL-style defensive tackle-crediting pass:

- `tackles` now represents combined tackles for compatibility with existing leaders and progression logic.
- `solo_tackles` and `assisted_tackles` are credited separately on run, pass, scramble, sack, and catch-fumble stops.
- Assisted tackle chances vary by play type, yardage, and the primary defender's solo/open-field/assist tackle ratings.
- Assisted stops can credit one or two additional nearby defenders using `assist_tackle`, `pursuit_angle`, `tackle_wrap`, and play-recognition ratings.

Engine version `0.1.6` adds the first turnover-rate calibration pass:

- Interception odds are tuned toward NFL-like per-attempt rates and now react to QB behavior profile risk, deep aggression, throwaway discipline, pressure, depth, and coverage advantage.
- Fumbles are no longer automatically lost; offenses can recover their own run, catch, scramble, and strip-sack fumbles.
- Lost fumbles are calibrated separately from total fumbles, with recovery odds influenced by defender play recognition/forced-fumble skill and ball-carrier security/composure.
- Strip-sack fumbles can produce normal offensive recoveries, defensive recoveries, or defensive touchdowns.

## Recommended Next Code Task

The next engine pass should refine the pieces that are now present but still too coarse:

1. Add special-teams penalty enforcement: roughing/running into kicker, kick-catch interference, return holding, and free-kick replay/add-on choices.
2. Improve timeout and tempo strategy with explicit hurry-up, chew-clock, field-goal unit, and sideline/out-of-bounds clock behavior.
3. Add blocked extra points, defensive conversion scores, and missed-FG returns.
4. Attribute return coverage tackles, assisted tackles on returns, return-team blocking quality, and long-snapper quality effects.
5. Start using weather, stadium, stamina, and durability so game conditions and roster construction matter more.
