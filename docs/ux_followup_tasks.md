# NFL GM Sim UX Follow-Up Tasks

This backlog tracks polish work that should make the game feel more like a finished GM experience and less like a collection of data tables. Keep tasks user-facing first: every item should either reduce friction, explain the sim better, or make the world feel more alive.

## 1. Decision Hub / Today Screen

Goal: make the default Game Center view answer, "What should I care about right now?"

- [x] Create a `Today`/`Decision Hub` view that can become the default landing tab.
- [x] Show current league phase, in-game date, week/event, user team record, next opponent, and next key date.
- [x] Add a `Needs Attention` stack for injuries, roster limits, cutdown, practice squad, expiring contracts, fifth-year options, free agency, draft class selection, scouting slots, and unread high-priority inbox items.
- [x] Add one-click navigation from each item to the correct screen.
- [x] Add a compact `Latest Around the League` panel from league news and transactions.
- [x] Add a compact `Team Pulse` panel for standings, cap health, major injuries, and recent game result.
- [x] Make sim controls context-aware so the primary button changes by phase.
- [ ] Hide developer logs, raw commands, and technical labels from the hub.

Acceptance criteria:
- Starting or loading a save lands on a useful hub instead of requiring the user to hunt through tabs.
- Every blocking item shown in the hub has a clear action or dismissible explanation.
- The hub remains useful in observe mode without user-team-only actions.

## 2. Action Queue

Goal: let users stage changes without the screen jumping or processing every click.

- [x] Define a shared queued-action model for UI actions that should not apply instantly.
- [x] Queue roster cuts, practice squad moves, active-roster promotions, waiver releases, and depth chart swaps.
- [x] Queue scouting assignments and allow toggling before weekly sim processes them.
- [x] Queue free-agent offers before submitting them as a batch.
- [x] Add a persistent `Pending Changes` drawer with counts by category.
- [x] Add `Apply`, `Undo`, and `Clear` actions.
- [x] Show roster, practice squad, cap, and scouting counters updating live from queued state.
- [x] Add guardrails so invalid queued states are explained before apply.
- [x] Render projected depth chart and roster status changes before applying queued actions.

Initial implementation covers cutdown/practice-squad selections, weekly scouting selections, free-agent offers, depth chart set/move/swap actions, active-roster promotions, release/waive actions, and IR send/activate actions.

Acceptance criteria:
- Clicking roster/scouting buttons no longer reloads the page or changes scroll position.
- Users can review every staged change before it mutates the save.
- Apply failures report the exact blocked item without discarding the rest of the queue.

## 3. Better "Why?" Panels

Goal: make CPU decisions and sim outcomes understandable without needing database audits.

- [x] Add a reusable `Why This Happened` component.
- [ ] Attach reason summaries to CPU signings, cuts, restructures, tags, extensions, draft picks, draft trades, waiver claims, and depth chart changes.
- [ ] Show need score, depth chart fit, cap impact, scheme fit, age curve, scouting confidence, pick value, and risk flags when relevant.
- [ ] Add player/prospect links inside reason panels.
- [ ] Add `Was this surprising?` tags for moves the AI considered risky but defensible.
- [ ] Persist CPU reason payloads in save data rather than rebuilding from current state only.
- [x] Add a compact reason tooltip in transaction lists and a fuller modal on click.

Initial implementation attaches archived free-agency CPU decision explanations to transaction ledger rows and reuses the same modal from free-agency bidding notes. Broader move classes still need persisted reason payloads.

Acceptance criteria:
- A user can inspect a weird CPU move and see the AI's stated logic in one click.
- Draft picks show what the team thought it knew at the time, not what is true after the fact.
- Reasons stay attached when the save advances.

## 4. Inbox And Message UX

Goal: turn the inbox into a useful front-office communication tool.

- [x] Split inbox into `Needs Action`, `Team`, `Scouting`, `Medical`, `League`, and `Archived`.
- [x] Add read/unread state and bulk mark-read.
- [x] Pin or prioritize urgent messages.
- [ ] Group scouting reports by prospect when several reports land close together.
- [x] Add linked player, prospect, and team chips.
- [ ] Add transaction and box score chips once messages include those related payloads.
- [ ] Add message threading for repeated updates on the same player or prospect.
- [x] Reduce low-value noise from minor events and keep minor flavor off the calendar.
- [ ] Add optional filters by source, phase, date, and priority.

Acceptance criteria:
- Important user decisions are visible without being buried under flavor notes.
- Every message with a player, prospect, team, game, or transaction has a working link.
- The inbox feels like staff communication, not a debug log.

## 5. Guided Offseason Flow

Goal: make the offseason feel like a structured football calendar.

- [x] Build a guided offseason checklist that appears after the season ends.
- [x] Include progression/regression review, retirements, staff notes, re-signings, tags, tenders, fifth-year options, free agency, draft prep, draft, post-draft free agency, camp, preseason, cutdown, waivers, and Week 1 readiness.
- [x] Gate phase advancement only when truly required.
- [x] Add clear prompts for draft class generate/import on June 1.
- [x] Add phase-specific help text written as football operations guidance, not technical instructions.
- [x] Allow CPU-managed skip-through for users who want to sim past a phase.
- [x] Preserve observe mode as a no-interruption flow.

Acceptance criteria:
- The user always understands the next offseason step.
- Sim-to buttons respect mandatory decisions but do not over-prompt for minor advances.
- CPU-managed mode can skip user-only decisions cleanly.

## 6. Player Card Polish

Goal: make the player profile the main storytelling and evaluation surface.

- [x] Add a compact player summary band with status, role, evaluation confidence, contract outlook, and recent trend.
- [x] Improve attribute group visibility by position and hide irrelevant groups.
- [x] Add context for fog-of-war confidence so users know how much to trust displayed ratings.
- [x] Add recent performance and snap trend cards.
- [x] Add contract year-by-year view with cap hit, salary, bonus, guarantees, dead cap, and savings.
- [x] Add accolades/history badges without duplicating the full trophy case.
- [x] Add development/personality notes when revealed by events.
- [x] Add roster action buttons where appropriate: extend, release, trade, IR, promote, number change, depth chart.

Acceptance criteria:
- A user can understand a player's value, risk, contract, role, and recent story within one screen.
- Rookie and young-player cards clearly show evaluation uncertainty.
- Position-irrelevant ratings no longer consume screen space.

## 7. Sim Progress Overlay

Goal: make long sims feel alive and controllable.

- [x] Add a global sim progress overlay that shows current in-game date, phase, event, and current processing step.
- [x] Stream latest completed games, injuries, transactions, and major league news into the overlay.
- [x] Keep the calendar and league table refreshing while simming.
- [x] Add a safe interrupt button to all sim actions.
- [ ] Add an optional "continue through non-critical popups" mode for long sims.
- [x] Keep observe mode free of interrupting prompts.
- [x] Make sim-to actions route to the most useful screen unless a screen-specific exception exists.

Acceptance criteria:
- The user can tell the sim is still moving without opening logs.
- Interrupts land at safe points and leave the save valid.
- Sim round/playoff actions keep the user on the playoff tree when appropriate.

## 8. Free Agency UX And CPU Clarity

Goal: make free agency easier to use and easier to trust.

- [x] Redesign free agency around market waves, team needs, cap health, and offer status.
- [x] Show user cap space, effective cap after queued offers, roster holes, and positional filters up front.
- [x] Make the player list resemble roster hub with headshots, age, experience, rating view, contract ask, and likely role.
- [x] Add CPU market explanations for major signings and failed bids.
- [x] Show demand movement by date and market temperature.
- [x] Add simple user controls for front-loaded, balanced, and back-loaded structures.
- [ ] Add clear tag, tender, fifth-year option, and offer-sheet surfaces.

Acceptance criteria:
- The user can make an offer without reading raw runner output.
- CPU bidding wars show why teams are involved.
- Post-draft free agency looks different from early free agency.

## 9. Depth Chart Clarity

Goal: make formation-specific depth charts feel powerful instead of fussy.

- [x] Keep active scheme formations visible by team and hide inactive packages behind an add/manage menu.
- [x] Add drag-to-swap players inside a formation.
- [x] Add duplicate prevention within a single formation while allowing true position-flex across different packages.
- [x] Add cosmetic unlock mode for moving formation boxes without changing football logic.
- [x] Show package usage percentages and which depth chart is used by sim logic.
- [ ] Add kick return, punt return, gunner, and general special teams duties.
- [x] Make formation boxes readable at all screen widths.
- [ ] Add a "CPU set sensible chart" button with preview before apply.

Acceptance criteria:
- Users can quickly see who is actually on the field in each package.
- Nickel, base, and offensive personnel changes do not accidentally overwrite each other.
- Depth chart actions explain when a player is blocked by position, flex, injury, or duplicate rules.

## 10. Long-Term Balance Dashboards

Goal: help long saves stay healthy and make balance issues visible before they ruin a universe.

- [ ] Add league talent supply dashboard by position and rating tier.
- [ ] Add cap health dashboard with over-cap teams, bad-contract risk, dead money, restructures, and rollover.
- [ ] Add QB supply dashboard with starters, bridge QBs, prospects, aging cliffs, and top draft needs.
- [ ] Add starter age dashboard by position.
- [ ] Add rookie hit-rate dashboard by draft round, position, and class.
- [ ] Add retirement and low-end cleanup dashboard.
- [ ] Add warnings when talent inflation, talent drain, cap stress, or position shortages cross thresholds.

Acceptance criteria:
- Long-term test saves can be evaluated from UI instead of ad hoc SQL.
- Balance flags explain what changed and where to inspect next.
- Dashboards are hidden from casual play unless surfaced as front-office reports.

## 11. Historical And Story Presentation

Goal: make careers, teams, drafts, and seasons feel remembered.

- [ ] Add league history page with champions, MVPs, award winners, standings, playoff trees, and leaders by year.
- [ ] Add team history page with records, playoff finishes, draft classes, major signings, trades, and franchise leaders.
- [ ] Add draft class retrospective pages after years one, three, and five.
- [ ] Add milestone alerts for career stats, franchise records, awards, and retirements.
- [ ] Add player career timeline entries for major injuries, awards, team changes, extensions, tags, trades, and rings.
- [ ] Add record books for single-season, career, team, rookie, and playoff records.
- [ ] Add "where are they now" stories for former high picks, late bloomers, and practice squad breakouts.

Acceptance criteria:
- A ten-year save has browsable history that makes the league feel continuous.
- Player and team pages carry meaningful past context.
- Major events are discoverable after they leave the inbox/news feed.

## Suggested Execution Order

1. Decision Hub / Today Screen
2. Action Queue
3. Inbox And Message UX
4. Sim Progress Overlay
5. Guided Offseason Flow
6. Free Agency UX And CPU Clarity
7. Depth Chart Clarity
8. Player Card Polish
9. Better "Why?" Panels
10. Long-Term Balance Dashboards
11. Historical And Story Presentation
