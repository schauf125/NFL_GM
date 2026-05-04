# NFL GM Sim

Local Python/SQLite NFL front-office simulation project inspired by OOTP and Football Manager.

The current project is focused on building the database foundation: real teams, rosters, contracts, cap accounting, draft picks, free agents, roster rules, and the league calendar.

## Current Contents

- `database/nfl_gm.db` - working SQLite database snapshot.
- `database/` - team seed scripts, depth charts, flex ratings, and database setup helpers.
- `tools/` - maintenance and gameplay tools for importing stats, cap accounting, roster moves, transactions, future picks, roster validation, and league calendar setup.
- `engine/draft/` - draft-class schema and fictional prospect generation helpers before prospects become real players.
- `data/`, `graphics/`, `engine/`, `finance/`, `ui/`, `saves/` - project folders reserved for the game structure as it grows.

## Useful Commands

Run these from the project root:

```powershell
python tools\play.py new --game-id vikings_test --name "Vikings Test" --user-team MIN
python tools\play.py status
python tools\play.py view-team MIN
python tools\play.py cap --team MIN
python tools\play.py advance-to-next-event
python tools\play.py alerts
python tools\play.py process-events --from-date 2026-06-01 --to-date 2026-09-13 --include-start
python tools\play.py weekly-hooks 1 --season 2026 --apply
python tools\play.py process-today
python tools\play.py roster find-player Jefferson
python tools\play.py roster-rules waiver-wire
python tools\play.py roster-rules sign-ps --player "Player Name" --team MIN --phase "Regular Season"
python tools\play.py roster-cutdown --season 2026 --apply
python tools\play.py schedule MIN
python tools\play.py week 1
python tools\play.py sim-matchup MIN CHI --seed 125
python tools\play.py sim-game 1 --seed 127
python tools\play.py sim-audit --games 100 --season 2026 --seed 3000
python tools\play.py manual-playtest --team MIN
python tools\play.py playtest-logs latest
python tools\play.py playtest-logs bundle --latest
python tools\play.py sim-week 1 --apply
python tools\play.py sim-season --season 2026 --apply --seed 2600
python tools\play.py complete-season --season 2026 --apply --seed 9900
python tools\play.py history standings --season 2026
python tools\play.py history leaders --season 2026 --stat pass_yards
python tools\play.py personalities summary
python tools\play.py personalities show --player Mahomes
python tools\play.py draft --year 2027 --count 330 --seed 2027 --apply
python tools\play.py validate-draft db --draft-year 2027
python tools\play.py draft-select board --draft-year 2027 --available-only --limit 20
python tools\play.py draft-select picks --draft-year 2027 --team MIN --unused-only
python tools\play.py draft-select select --draft-year 2027 --pick-id 673 --prospect-id 1 --overall-pick 1 --apply
python tools\play.py draft-room setup
python tools\play.py draft-room start --draft-year 2027 --user-team MIN --paused --apply
python tools\play.py draft-room status --draft-year 2027
python tools\play.py draft-room board --draft-year 2027 --limit 30
python tools\play.py draft-room pick --draft-year 2027 --prospect-id 1 --apply
python tools\play.py draft-room skip --draft-year 2027 --count 10 --until-user-pick --apply
python tools\play.py draft-room pause --draft-year 2027 --apply
python tools\play.py draft-room resume --draft-year 2027 --apply
python tools\play.py free-agency setup
python tools\play.py free-agency start --league-year 2027 --start-date 2027-03-17 --apply
python tools\play.py free-agency board --league-year 2027 --limit 30
python tools\play.py free-agency offer --league-year 2027 --team MIN --player "Player Name" --years 2 --aav 4000000 --apply
python tools\play.py free-agency advance-hour --league-year 2027 --apply
python tools\play.py free-agency advance-day --league-year 2027 --apply
python tools\ui_runner.py --port 8765
python tools\play.py ai-gm config --provider ollama --endpoint http://localhost:11434/api/chat --model llama3.1:8b --enable
python tools\play.py ai-gm context --team MIN --decision-type trade_block_update
python tools\play.py ai-gm run --team MIN --decision-type trade_block_update
python tools\play.py ai-gm context --team MIN --decision-type draft_strategy_update
python tools\play.py ai-gm context --team MIN --decision-type free_agent_shortlist
python tools\play.py ai-gm context --team MIN --decision-type trade_proposal
python tools\play.py trade setup
python tools\play.py trade show-chart --chart jimmy_johnson --limit 12
python tools\play.py trade propose --proposing-team MIN --receiving-team CHI --offering player:1 --requesting pick:2027:4

python tools\ai_gm.py setup
python tools\trade_engine.py setup
python tools\view_team.py DEN
python tools\roster_rules.py validate --all --phase Preseason --summary-only
python tools\roster_rules.py waive --player "Player Name" --team MIN --dry-run
python tools\roster_rules.py claim-waiver --player "Player Name" --team CHI --dry-run
python tools\roster_rules.py process-waivers --all-open --dry-run
python tools\stat_history.py rebuild --season 2026
python tools\player_personalities.py setup
python tools\player_personalities.py apply --game-id test_save --season 2026 --seed 123 --apply
python tools\download_team_logos.py
python tools\download_player_headshots.py
python tools\prepare_draft_portrait_jobs.py from-db --draft-year 2027 --team MIN --apply
python tools\generate_draft_portraits.py summary --run-id draft_2027_portraits
python tools\generate_draft_portraits.py generate --run-id draft_2027_portraits
python tools\generate_draft_portraits.py generate --run-id draft_2027_portraits --apply
python tools\export_game_center_ui_data.py
python tools\export_app_shell_ui_data.py
python tools\export_front_office_ui_data.py
python tools\export_player_card_ui_data.py
python tools\export_player_profile_ui_data.py
python tools\league_calendar.py current
python tools\league_calendar.py next --limit 18
python tools\league_schedule.py validate
python tools\sim_game.py matchup MIN CHI --seed 125
python tools\sim_audit.py --games 100 --season 2026 --seed 3000
python tools\roster_actions.py cap --team DEN
python tools\setup_draft_classes.py apply
python tools\setup_draft_classes.py create-class 2027 --seed 2027-default
python tools\build_name_pool.py build
python tools\build_physical_profiles.py build
python tools\build_college_pool.py build
python tools\generate_draft_class.py --year 2027 --count 330 --seed 2027 --class-strength 50
python tools\generate_draft_class.py --year 2027 --count 330 --seed 2027 --class-strength 50 --apply
python tools\validate_draft_class.py db --draft-year 2027
python tools\select_draft_pick.py board --draft-year 2027 --available-only --limit 20
python tools\select_draft_pick.py picks --draft-year 2027 --team MIN --unused-only
python tools\select_draft_pick.py select --draft-year 2027 --pick-id 673 --prospect-id 1 --overall-pick 1 --apply
python tools\draft_room.py ui-data --draft-year 2027 --output data\ui\draft_room_2027.json
python tools\free_agency_processor.py ui-data --league-year 2027 --output data\ui\free_agency_2027.json
python tools\generate_draft_class_preview.py --year 2027 --count 330 --seed 2027-preview --class-strength 50
python tools\report_draft_class_preview.py --csv data\draft\generated\2027_draft_class_preview.csv
python tools\draft_personalities.py apply --draft-year 2027 --seed 2027 --apply
```

`tools\play.py` is the preferred gameplay wrapper. It reads the active save from `saves\save_registry.json` and automatically points commands at the save DB instead of the master DB. The older direct tools still work for maintenance and debugging.

## Database Notes

The database currently includes live working data and should be treated as the master snapshot. Playable games should be created through `tools\save_manager.py`, which copies the master database into `saves\<game_id>\nfl_gm_save.db` and applies new-game variance only inside that save database. Runtime SQLite sidecar files such as WAL, SHM, and journal files are ignored.

The sim calendar starts each year on June 1, and new saves default to `<start-year>-06-01`. Roster limits are disabled at that point so the game can begin with every team compliant, then preseason and regular-season roster rules activate later in the calendar. The Season Hub includes a `Start Fresh June 1 Save` action for creating a new active save at that default starting point.

Date advancement now runs lightweight calendar-event hooks instead of full daily roster work. The event processor logs important calendar dates once per save, creates alerts, and flips basic phase settings. Full daily processing remains available as a manual/debug command with `python tools\play.py process-today`.

Roster compliance and other heavier save maintenance now run through weekly hooks. `sim-week --apply` and `sim-season --apply` run weekly hooks by default after completed weeks, and `python tools\play.py weekly-hooks <week> --season 2026 --apply` can run them manually. Use `--no-weekly-hooks` on sim commands when testing raw game results only.

Automatic regular-season cutdowns live in `tools\roster_cutdown.py` and the save-aware `python tools\play.py roster-cutdown --season 2026 --apply` wrapper. It is a deterministic fallback for now: it keeps a balanced 53-man active roster, moves developmental/fringe players to the practice squad, releases the rest, logs transactions, rebuilds cap views, resolves roster-compliance alerts, and leaves room for a later `--use-ai-gm` local LLM decision layer.

The 2026 schedule tables use the official NFL.com home/away opponent matrix with generated provisional weeks, dates, and 1:00 PM placeholder times. Future schedules can be generated from the NFL formula using actual prior-season standings once a season has been simulated. Once the full NFL schedule is released, week/date/time fields can be overwritten without changing the core matchup records.

The first match engine lives in `engine\match_engine.py` and uses the normalized `player_ratings` table, depth charts, generated schedules, and a tenths-of-a-second game clock. Direct simulations are dry runs unless `--apply` is provided, which keeps testing separate from actual save progression.

Manual match-engine playtesting lives in `tools\manual_playthrough.py` and the save-aware `python tools\play.py manual-playtest --team MIN` wrapper. It chooses the next scheduled Vikings game by default, lets you call Vikings offensive run/pass concepts and fourth-down decisions, keeps the opponent CPU-controlled, and writes a complete log bundle to `logs\playtests\<session>`. Add `--pause-defense` if you want to stop before opponent snaps too. Use `python tools\play.py playtest-logs latest` to find the newest bundle and `python tools\play.py playtest-logs bundle --latest` to zip it for debugging.

Season completion now lives in `tools\season_rollover.py` and the save-aware `python tools\play.py complete-season ...` wrapper. It verifies that all 272 regular-season games are played, simulates or confirms the 13-game postseason, writes the next draft order, rebuilds the next season schedule from actual standings, logs the champion, and fast-advances the active save to the post-Super Bowl offseason unless told not to. This is the handoff point for own-team contract talks before free agency. Use `--process-days-on-advance` only when you intentionally want every daily hook to run during that long date jump.

Own-team contract talks live in `tools\contract_negotiations.py` and the save-aware `python tools\play.py contract ...` wrapper. `contract list` shows expiring players before free agency, estimated market asks, suggested years, and keep/walk guidance using projected next-year Top 51 space. `contract extend` adds a future extension that starts the next contract year while preserving the current contract row, then rebuilds cap views and logs the transaction. `contract release` handles projected cap-casualty cuts before free agency. `contract restructure` converts projected salary into prorated bonus to create near-term cap relief while moving cap to future years. `contract expire` is the offseason bridge that moves unextended expired contracts to the free-agent pool, clears depth-chart rows, logs `Contract Expired` transactions, and shifts cap accounting to the next `current_contract_year`.

Future AI GM work is sketched in `docs\ai_gm_local_llm_roadmap.md`, with room for locally hosted LLMs to propose team-specific decisions through validated game actions.
Match-engine rules coverage is tracked in `docs\sim_engine_rules_coverage.md`. Use it as the checklist for NFL game-flow completeness before deeper ratings or tick-physics tuning.
The first AI GM tool now lives at `tools\ai_gm.py`. It creates AI GM profile/config/queue/log tables, builds save-scoped team context packets, calls a local Ollama or OpenAI-compatible endpoint, validates strict JSON advisory decisions, and logs the prompt/response/validation result without directly applying roster or contract changes. AI GM profiles include sourced real-life GM identity fields plus editable operating models for job security, owner pressure, coach alignment, negotiation style, scheme fit, depth charts, releases, youth-versus-veteran calls, future building, draft planning, contracts, free agency, trades, and risk appetite.
Trade support lives at `tools\trade_engine.py`. It seeds multiple draft-pick value charts, assigns each AI GM a chart/deviation profile, validates player and pick ownership, stores proposal/counter/accept/reject lifecycles, and can execute accepted trades through the normal roster, contract, draft-pick, cap, and transaction tables. Trade execution should still be tested in dry-run/smoke mode before it becomes a routine CPU automation.

Hidden player personalities live in `tools\player_personalities.py`. The master DB stores trait definitions and positive/neutral baseline suggestions, while each new save rolls its own hidden trait assignments with a 15% baseline omission chance and additional random league-wide traits. These are simulation flavor only and do not affect the match engine yet.

Generated draft classes live outside the player table until a draft/UDFA flow promotes them. `tools\generate_draft_class.py` creates the hidden true profile, noisy public board, scouting reports, combine, pro day, private-workout hooks, and normalized match-engine ratings for each prospect. Public draft exports hide true grade, dev trait, true risk, raw ratings, and private-workout data; `--include-hidden-preview` is only for developer QA.

Draft selections are promoted with `tools\select_draft_pick.py` or the save-aware `python tools\play.py draft-select ...` wrapper. A selection turns one available prospect into a normal `players` row, copies hidden normalized ratings and role scores, adds conservative primary-position flex, creates an estimated rookie-scale contract, marks the draft pick used, logs the transaction, and carries over hidden draft personalities when the save has an active game id.

Draft-room flow lives in `tools\draft_room.py` and the save-aware `python tools\play.py draft-room ...` wrapper. It stores the current pick, clock status, seconds remaining by round, user-team stop, and draft-room event log. The clock is intentionally pause/resume state for now rather than a real-time countdown. `pick` uses the existing draft-selection bridge, and `skip` can auto-pick through CPU selections until the user team is on the clock. `ui-data` exports the board, pick queue, current room state, and recent events as JSON for a future visual draft room.

Free-agency flow lives in `tools\free_agency_processor.py` and the save-aware `python tools\play.py free-agency ...` wrapper. Starting free agency advances the active save to the NFL league-year start date, lets CPU teams retain some own expiring players, processes unextended expired contracts, builds a market from `free_agent_profiles`, supports manual offers, creates CPU market and response offers for bidding wars, resolves accepted deals through the normal contracts/cap/transaction tables, and advances by hour on the first busy day before switching to daily advancement. `ui-data` exports the market board, pending offers, period state, and recent events for the Game Center free-agency screen.

Team logo files live under `graphics\teams\<TEAM>\logos`. They are imported from ESPN's public NFL team endpoint by `tools\download_team_logos.py`, with a JSON manifest at `graphics\teams\team_logos_manifest.json` and DB mappings in `team_graphics_assets`.

Player headshots live under `graphics\players\<TEAM>\headshots`. They are imported from ESPN's public team roster endpoint by `tools\download_player_headshots.py`, with a JSON manifest at `graphics\players\player_headshots_manifest.json` and DB mappings in `player_graphics_assets`.

Generated rookie portrait preparation lives in `tools\prepare_draft_portrait_jobs.py`. It creates per-player prompt/job files under `graphics\players\portrait_jobs` for selected draftees. `tools\generate_draft_portraits.py` can then call OpenAI's Image API to create staged portraits, but it is dry-run by default, selects only one job by default, and requires `--allow-batch` before processing more than one portrait. It never imports assets into the playable database; review/import remains separate. See `docs\draft_portrait_generation.md` for the staged background workflow.

The basic app shell lives at `ui\app_shell\index.html`. It is the first start/load wrapper and uses `ui\app_shell\app-shell-data.js`, regenerated with `tools\export_app_shell_ui_data.py`. It includes a splash/loading screen, Home, New Game command builder, Load Game browser, Settings, and links into the deeper UI pages.

The playable loop cockpit lives at `ui\game_center\index.html`. It is backed by `ui\game_center\game-center-data.js`, regenerated with `tools\export_game_center_ui_data.py`. Its default Season Hub is intentionally simple: sim the next week, sim the rest of the regular season, validate rosters, review recent finals, scan the Vikings schedule, check standings, review league leaders, handle own-team contract talks, and watch calendar gates without managing individual games.

The optional local browser runner lives at `tools\ui_runner.py`. Start it with `python tools\ui_runner.py --port 8765`, then open `http://127.0.0.1:8765/ui/game_center/index.html`. When served through the runner, the Season Hub shows Run buttons for whitelisted local actions such as simming a week, simming the season, extending expiring players, advancing free agency, starting/skipping draft-room picks, and refreshing UI data. Opening the HTML file directly still works, but it stays copy-command only.

The first front-office UI prototype lives at `ui\front_office\index.html`. It is a static browser page backed by `ui\front_office\front-office-data.js`, which can be regenerated from the current database with `tools\export_front_office_ui_data.py`. The screen includes a team dashboard, roster table, depth chart, schedule, coaches, cap snapshot, and player detail panel.

The first player-card UI prototype lives at `ui\player_card\index.html`. It is a static browser page backed by `ui\player_card\player-data.js`, which can be regenerated from the current database with `tools\export_player_card_ui_data.py`. The card uses scouting labels and red-to-green skill bars instead of showing raw GM-facing overall numbers.

The FM-style player profile prototype lives at `ui\player_profile\index.html`. It is backed by `ui\player_profile\player-profile-data.js`, which can be regenerated with `tools\export_player_profile_ui_data.py`. It collects player information, role fits, full attribute groups, position flexibility, career stats, year-by-year stats, contract details, free-agent market context when applicable, and transaction history.

## Requirements

Python 3.12 or newer is recommended. The current tools use the Python standard library only.
