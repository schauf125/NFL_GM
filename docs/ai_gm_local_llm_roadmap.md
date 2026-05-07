# Local LLM AI GM Roadmap

This is the future path for making CPU-controlled teams feel like real front offices instead of generic transaction bots.

## Goal

Each AI GM should have:

- a team-specific personality
- roster-building philosophy
- short-term owner pressure
- cap-risk tolerance
- draft tendencies
- patience level with young players
- willingness to trade stars, picks, or future cap
- memory of past decisions and outcomes

The local LLM should not directly edit the database. It should propose structured decisions, and the game engine should validate/apply them.

## Architecture

The game should use an AI GM orchestrator:

1. Build a team context packet from the save DB.
2. Send the packet to a local LLM endpoint.
3. Require strict JSON back from the model.
4. Validate every proposed move with game rules.
5. Apply legal moves through existing roster/contract tools.
6. Log the prompt, response, validation result, and final action.

This keeps the LLM creative, but the sim engine authoritative.

## Likely Local LLM Options

- Ollama local server
- LM Studio local server
- llama.cpp server
- any OpenAI-compatible local endpoint

The code should eventually support a config like:

```json
{
  "provider": "ollama",
  "endpoint": "http://localhost:11434/api/chat",
  "model": "llama3.1:8b",
  "temperature": 0.7,
  "max_tokens": 2000
}
```

## Future Tables

Suggested DB tables:

- `ai_gm_profiles`
  - team_id
  - gm_name
  - personality
  - roster_philosophy
  - cap_tolerance
  - draft_tendency
  - trade_aggression
  - patience_with_young_players

- `ai_gm_objectives`
  - team_id
  - season
  - objective_type
  - priority
  - description
  - deadline_date

- `ai_gm_memory`
  - team_id
  - memory_date
  - memory_type
  - summary
  - importance

- `ai_gm_decision_queue`
  - game_id
  - team_id
  - decision_date
  - decision_type
  - context_json
  - status

- `ai_gm_decision_log`
  - game_id
  - team_id
  - decision_date
  - prompt_json
  - response_json
  - validation_result
  - action_taken

- `ai_gm_llm_config`
  - game_id
  - provider
  - endpoint
  - model
  - temperature
  - enabled

## First AI GM Use Cases

Start with low-risk advisory decisions:

1. Camp cutdown recommendations
2. Practice squad priorities
3. Trade block generation
4. Extension interest
5. Free-agent shortlists

Later, allow higher-impact decisions:

1. Trade offers
2. Draft board changes
3. Contract negotiations
4. Scheme/staffing preferences
5. Long-term rebuild or win-now planning

## Decision JSON Shape

Example future response:

```json
{
  "team": "MIN",
  "decision_type": "trade_block_update",
  "summary": "Prioritize cap flexibility and add mid-round picks.",
  "actions": [
    {
      "action_type": "add_to_trade_block",
      "player_id": 1,
      "reason": "Contract and age do not match the team's timeline.",
      "minimum_return": "2027 round 3 pick"
    }
  ],
  "confidence": 0.72
}
```

## Guardrails

- Never let the LLM write SQL.
- Never let the LLM bypass cap, roster, or trade validation.
- Every LLM action should be reversible or logged.
- Prompts should include only the relevant team context, not the whole database.
- The same seed/config should be able to reproduce a decision when possible.

## Save-System Hook

Each save manifest already has an `ai_gm_llm` placeholder. That gives every save its own future AI settings without changing the master database.

## Implemented First Slice

The initial local AI GM tool is `tools\ai_gm.py`.

It currently supports:

- AI GM tables for profiles, objectives, memory, decision queue, decision log, and per-save LLM config.
- Default generated AI GM profiles/objectives for all 32 teams.
- Real-life GM identity fields with source metadata, plus editable team tendency/policy fields.
- Universal GM operating model template plus all 32 current real/de facto GM overlays in `tools\ai_gm_operating_models.py`; the human editing guide lives in `docs\ai_gm_operating_model_template.md`.
- Practice squad instructions that balance fringe-player development with useful veteran injury call-up coverage.
- Save-scoped team context packets with roster, cap, draft, role-score, objective, memory, validation, and transaction context.
- Local LLM calls through Ollama or OpenAI-compatible chat endpoints.
- Strict JSON parsing and validation for advisory decision types.
- Trade chart setup through the trade engine, with randomly assigned GM chart preferences and per-GM deviation factors.
- Trade proposal/response context that includes assigned chart details, round-value references, trade-block candidates, tradeable picks, weak positions, and pending proposals.
- Acquisition decision context for draft and free agency: scheme needs, investment tier, cap band, practical free-agent budget, recommended in-season buffer, premium-pick count, day-three pick count, contract cliffs, and low-cost role needs.
- Deterministic team evaluation context from `tools\ai_gm_team_evaluator.py`: team phase, recommended posture, competitiveness, cap health, age curve, roster needs, surplus rooms, contract pressure, cut candidates, practice squad priorities, extension candidates, trade-block candidates, and risk flags.
- Phase-aware operations scanning from `tools\ai_gm_ops_controller.py`: the controller reads the calendar phase and evaluator output, then recommends prioritized advisory tasks for cutdowns, weekly roster repair, free-agent plans, contract plans, trade-block review, and draft strategy. `--enqueue` stores those tasks in `ai_gm_decision_queue` for later review/LLM runs without applying roster moves.
- Queue processing through `tools\ai_gm.py`: `ai-gm queue` lists queued/running/completed work, and `ai-gm process-queue` processes saved context packets in priority order. Processing calls the configured local LLM only when enabled, validates strict JSON, logs the result, and marks each queue row `completed`, `invalid`, or `failed`.
- AI GM autonomy settings and daily runs: `ai-gm autonomy-config` stores per-game or per-team autonomy policy, while `ai-gm daily-run` scans one team or the league, plans phase-appropriate operations, persists review artifacts only with `--persist`, and limits auto-apply to explicitly allowed low-risk operations.
- AI GM review inbox: persisted daily-run plans and queued advisory decisions are indexed in `ai_gm_review_items` with lifecycle states for `pending_review`, `approved`, `rejected`, `expired`, `stale`, `blocked`, and `applied`. `review-show` exposes the linked artifact and suggested commands; `review-update` records the user's decision; `review-history` keeps the lifecycle/apply ledger visible after items leave the active inbox. Game Center exposes the same row-level show, approve, reject, dry-run, and apply flow when the UI runner is active, plus status counts and recent review activity.
- Approved review execution: `review-apply` dry-runs by default and commits only with `--apply`. It routes approved cutdown, contract, free-agent, and selected queued trade review items through existing validated bridges, then stores the apply result or blocking error back on the review item. Queued trade-response decisions can accept, reject, or counter existing proposals; queued trade-proposal decisions can create proposed player-for-pick or player-swap trades. Accepted trades are not executed automatically.
- Scheduler hooks: calendar-event processing calls the daily AI GM autonomy loop at cutdown, practice-squad, free-agency, franchise/extension, trade-deadline, combine, and draft windows, persisting review inbox items while skipping duplicate open review items for the same team/operation. Weekly hooks run the same review-generation path for regular-season roster maintenance after completed weeks.
- Advisory cutdown planning from `tools\ai_gm_cutdown_planner.py`: a review-first 53-man roster recommendation, 16-player practice squad priority list, release/waive list, waiver-risk notes, validation status, and comparison against the deterministic cutdown fallback.
- Persisted cutdown-plan review flow: saved plan rows can be listed by team/game, dry-run applied against current roster state, and committed only with an explicit `--apply`. Warning or stale plans are rejected unless the reviewer supplies the matching override flag.
- Advisory contract planning from `tools\ai_gm_contract_planner.py`: a review-first expiring-contract board that groups players into extension targets, franchise-tag candidates, trade-before-walk candidates, let-walk recommendations, and defers using projected cap room, estimated asks, player age, premium-position value, evaluator pressure, and GM cap posture. `--persist` stores a snapshot in `ai_gm_contract_plans`, and `apply-contract-plan` can dry-run or explicitly execute extension targets after player/team ownership, expiring status, future-extension, cap-reserve, extension-count, total-AAV, and stale-plan checks. Tag, trade-before-walk, let-walk, and defer buckets remain advisory.
- Advisory free-agent planning from `tools\ai_gm_free_agent_planner.py`: a review-first market board that groups available players into primary targets, value targets, bridge/depth options, monitors, and avoids using projected FA budget, roster needs, surplus rooms, player age, asking AAV, and GM free-agent/cap posture. `--persist` stores a snapshot in `ai_gm_free_agent_plans`, and `apply-free-agent-plan` can dry-run or explicitly submit pending offers after active-period, player-availability, duplicate-offer, cap-reserve, offer-count, budget, and stale-plan checks. It does not sign players directly.
- Advisory draft planning from `tools\ai_gm_draft_planner.py`: a team-specific board that weighs roster needs, contract cliffs, pick inventory, prospect board value, GM draft tendencies, premium-position value, upside, and risk. `--persist` stores a snapshot in `ai_gm_draft_plans`; CPU draft-room auto-picks consult the latest saved plan for that team before falling back to the generic board/need selector.
- CPU offseason driving from `tools\ai_gm_offseason_driver.py`: a dry-run-first wrapper that runs the reviewed contract and free-agent plan/apply bridges across one team or all CPU teams, skips the active user team by default, persists plan snapshots only when the transaction is committed, and limits extension/offer counts plus optional total AAV per team.
- Free-agent options are annotated with `need_fit`, `cap_fit`, budget delta, and AI GM posture so the model can prefer logical scheme/cap fits over expensive name value.
- Guardrails that reject SQL/code-like actions, mutating action types, wrong-team player IDs, non-existent player IDs, invalid trade partners, invalid proposal IDs, and invalid draft-pick references.
- Decision queue/log rows that preserve prompt JSON, response JSON, validation result, and final action status.

The current decision types are advisory only:

- `camp_cutdown_recommendation`
- `practice_squad_priorities`
- `trade_block_update`
- `extension_interest`
- `free_agent_shortlist`
- `depth_chart_review`
- `draft_strategy_update`
- `trade_proposal`
- `trade_response`

The generated profile now gives each AI GM operating instructions for:

- Super Bowl roster building, salary-cap discipline, QB priority, contender aggression, rebuild value behavior, and draft-trade negotiation
- practice squad development stashes and veteran injury-depth call-ups
- depth-chart placement
- release/waive decisions
- youth versus veteran tiebreakers
- future roster building
- draft strategy based on team needs and existing contracts
- draft-pick value discipline by round, scheme role, and contract cliff
- free-agent cap discipline by practical budget, asking AAV, and likely role
- contract extension discipline
- free-agent usage
- trade aggressiveness
- staff/scheme alignment
- risk appetite

Those policies are seeded into `ai_gm_profiles` and sent in the context packet
as `ai_gm_profile` plus `ai_gm_operating_directives`.

Useful commands through the save-aware wrapper:

```powershell
python tools\ai_gm.py setup
python tools\play.py ai-gm evaluate --team MIN
python tools\play.py ai-gm evaluate --all --persist
python tools\play.py ai-gm ops --team MIN
python tools\play.py ai-gm ops --all --limit 40
python tools\play.py ai-gm ops --team MIN --enqueue
python tools\play.py ai-gm queue --team MIN
python tools\play.py ai-gm process-queue --team MIN --limit 3
python tools\play.py ai-gm autonomy-show
python tools\play.py ai-gm autonomy-config --mode advisory_only --queue-llm --no-auto-apply-low-risk
python tools\play.py ai-gm autonomy-config --mode auto_apply_low_risk --queue-llm --auto-apply-low-risk
python tools\play.py ai-gm daily-run --team MIN --phase auto
python tools\play.py ai-gm daily-run --team MIN --phase auto --persist
python tools\play.py ai-gm daily-run --all --phase auto --persist --limit 20
python tools\play.py ai-gm daily-run --all --phase auto --mode auto_apply_low_risk --apply --limit 20
python tools\play.py ai-gm review-inbox --team MIN
python tools\play.py ai-gm review-history --team MIN --limit 20
python tools\play.py ai-gm review-show --review-id <review_id>
python tools\play.py ai-gm review-update --review-id <review_id> --status approved
python tools\play.py ai-gm review-update --review-id <review_id> --status rejected --note "reason"
python tools\play.py ai-gm review-apply --review-id <review_id>
python tools\play.py ai-gm review-apply --review-id <review_id> --apply
python tools\play.py ai-gm review-apply --all-approved --team MIN
python tools\play.py ai-gm review-apply --all-approved --team MIN --apply
python tools\play.py ai-gm cutdown-plan --team MIN
python tools\play.py ai-gm cutdown-plan --team MIN --persist
python tools\play.py ai-gm cutdown-plans --team MIN
python tools\play.py ai-gm apply-cutdown-plan --plan-id <id>
python tools\play.py ai-gm apply-cutdown-plan --plan-id <id> --allow-warning --apply
python tools\play.py ai-gm contract-plan --team MIN
python tools\play.py ai-gm contract-plan --team MIN --persist
python tools\play.py ai-gm contract-plans --team MIN
python tools\play.py ai-gm apply-contract-plan --plan-id <id>
python tools\play.py ai-gm apply-contract-plan --plan-id <id> --apply
python tools\play.py ai-gm free-agent-plan --team MIN --league-year 2026 --season 2026
python tools\play.py ai-gm free-agent-plan --team MIN --league-year 2026 --season 2026 --persist
python tools\play.py ai-gm free-agent-plans --team MIN
python tools\play.py ai-gm apply-free-agent-plan --plan-id <id>
python tools\play.py ai-gm apply-free-agent-plan --plan-id <id> --apply
python tools\play.py ai-gm draft-plan --team MIN --draft-year 2027 --season 2026
python tools\play.py ai-gm draft-plan --team MIN --draft-year 2027 --season 2026 --persist
python tools\play.py ai-gm draft-plans --team MIN --draft-year 2027
python tools\play.py ai-gm draft-plan --all --draft-year 2027 --season 2026 --persist
python tools\play.py ai-gm offseason-run --all --phase pre-free-agency
python tools\play.py ai-gm offseason-run --all --phase pre-free-agency --apply
python tools\play.py ai-gm offseason-run --all --phase free-agency-wave1 --league-year 2027 --season 2027
python tools\play.py ai-gm offseason-run --all --phase free-agency-wave1 --league-year 2027 --season 2027 --apply
python tools\play.py ai-gm config --provider ollama --endpoint http://localhost:11434/api/chat --model llama3.1:8b --enable
python tools\play.py ai-gm context --team MIN --decision-type trade_block_update
python tools\play.py ai-gm context --team MIN --decision-type trade_proposal
python tools\play.py ai-gm run --team MIN --decision-type trade_block_update
python tools\play.py ai-gm logs --team MIN
python tools\play.py trade setup
python tools\play.py trade assign-charts
python tools\play.py trade ai-propose --team MIN --max-proposals 2
python tools\play.py trade list --team MIN
python tools\play.py trade respond --proposal-id 1 --ai-respond
```

Use the direct `tools\ai_gm.py setup` command for the master DB. Use
`tools\play.py ai-gm ...` once a save is active, so the AI GM operates on the
isolated save DB. Use `tools\play.py trade ...` for executable trade proposal,
response, negotiation-log, and execution workflows; AI GM LLM decisions remain
advisory unless routed through those validated trade-engine commands.
