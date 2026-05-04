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
- Save-scoped team context packets with roster, cap, draft, role-score, objective, memory, validation, and transaction context.
- Local LLM calls through Ollama or OpenAI-compatible chat endpoints.
- Strict JSON parsing and validation for advisory decision types.
- Trade chart setup through the trade engine, with randomly assigned GM chart preferences and per-GM deviation factors.
- Trade proposal/response context that includes assigned chart details, round-value references, trade-block candidates, tradeable picks, weak positions, and pending proposals.
- Acquisition decision context for draft and free agency: scheme needs, investment tier, cap band, practical free-agent budget, recommended in-season buffer, premium-pick count, day-three pick count, contract cliffs, and low-cost role needs.
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
