(function () {
  let data = window.GAME_CENTER_DATA || {};
  const state = {
    view: "overview",
    runnerAvailable: false,
    runnerBusy: false,
    busyAction: null,
    lastResult: null,
    selectedDraftProspectId: null,
    selectedDepthSlot: null,
    selectedCalendarItem: null,
    newsFilter: "all",
  };

  const refs = {
    seasonLabel: document.getElementById("seasonLabel"),
    phaseText: document.getElementById("phaseText"),
    title: document.getElementById("title"),
    subhead: document.getElementById("subhead"),
    dateText: document.getElementById("dateText"),
    saveText: document.getElementById("saveText"),
    content: document.getElementById("content"),
    toast: document.getElementById("runnerToast"),
    buttons: Array.from(document.querySelectorAll(".nav button")),
  };

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }

  function append(parent, children) {
    children.forEach((child) => {
      if (child !== null && child !== undefined) parent.append(child);
    });
    return parent;
  }

  function shortDate(value) {
    if (!value) return "-";
    const parts = String(value).split("-");
    if (parts.length !== 3) return value;
    return `${parts[1]}/${parts[2]}/${parts[0]}`;
  }

  function dateReached(value) {
    if (!value || !data.currentDate) return false;
    return new Date(`${data.currentDate}T00:00:00`) >= new Date(`${value}T00:00:00`);
  }

  function money(value) {
    if (value === null || value === undefined || value === "") return "-";
    const amount = Number(value);
    if (!Number.isFinite(amount)) return String(value);
    if (Math.abs(amount) >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}M`;
    return `$${amount.toLocaleString()}`;
  }

  function whole(value) {
    const amount = Number(value || 0);
    return Number.isFinite(amount) ? String(Math.round(amount)) : "-";
  }

  function roundTo(value, increment) {
    const amount = Number(value || 0);
    const step = Number(increment || 1);
    if (!Number.isFinite(amount) || !Number.isFinite(step) || step <= 0) return 0;
    return Math.round(amount / step) * step;
  }

  function oneDecimal(value) {
    const amount = Number(value || 0);
    return Number.isFinite(amount) ? amount.toFixed(1) : "-";
  }

  function valueOrDash(value) {
    if (value === null || value === undefined || value === "") return "-";
    return String(value);
  }

  function decimalOrDash(value, digits = 2) {
    if (value === null || value === undefined || value === "") return "-";
    const amount = Number(value);
    return Number.isFinite(amount) ? amount.toFixed(digits) : String(value);
  }

  function heightText(inches) {
    const total = Number(inches || 0);
    if (!Number.isFinite(total) || total <= 0) return "-";
    const feet = Math.floor(total / 12);
    const remainder = Math.round(total - feet * 12);
    return `${feet}'${remainder}"`;
  }

  function weightText(weight) {
    return weight ? `${whole(weight)} lb` : "-";
  }

  function inchesText(value) {
    return value ? `${decimalOrDash(value, Number(value) % 1 ? 1 : 0)}"` : "-";
  }

  function inchesToFeetText(value) {
    const inches = Number(value || 0);
    if (!Number.isFinite(inches) || inches <= 0) return "-";
    const feet = Math.floor(inches / 12);
    const remainder = inches - feet * 12;
    return `${feet}'${decimalOrDash(remainder, remainder % 1 ? 1 : 0)}"`;
  }

  function roleLabel(value) {
    if (!value) return "-";
    return String(value).replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function rate(part, total) {
    const p = Number(part || 0);
    const t = Number(total || 0);
    if (!t) return "-";
    return `${Math.round((p / t) * 100)}%`;
  }

  function pct(part, total) {
    if (!total) return 0;
    return Math.max(0, Math.min(100, Math.round((Number(part || 0) / Number(total)) * 100)));
  }

  function setHeader(title, subhead) {
    refs.seasonLabel.textContent = String(data.currentSeason || "");
    refs.phaseText.textContent = data.currentPhase || "";
    refs.title.textContent = title;
    refs.subhead.textContent = subhead;
    refs.dateText.textContent = shortDate(data.currentDate);
    refs.saveText.textContent = data.activeSave?.display_name || data.registry?.activeGameId || "Master DB";
    refs.buttons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
  }

  function panel(title, kicker) {
    const section = node("section", "panel");
    const header = node("div", "panel-header");
    append(header, [node("h2", null, title), node("span", "panel-kicker", kicker || "")]);
    section.append(header, node("div", "panel-body"));
    return section;
  }

  function panelBody(panelEl) {
    return panelEl.querySelector(".panel-body");
  }

  function metric(label, value, note, tone) {
    const item = node("div", `metric ${tone ? `tone-${tone}` : ""}`.trim());
    append(item, [
      node("span", null, label),
      node("strong", null, value),
      note ? node("small", null, note) : null,
    ]);
    return item;
  }

  function tag(text, tone) {
    return node("span", `tag ${tone || ""}`.trim(), text);
  }

  function playerLink(playerId, name, className) {
    if (!playerId) return node("span", className || "", name || "-");
    const link = node("a", className || "player-link", name || "-");
    link.href = `../player_profile/index.html?player=${encodeURIComponent(playerId)}`;
    link.addEventListener("click", (event) => event.stopPropagation());
    return link;
  }

  function prospectLink(prospectId, name, className) {
    if (!prospectId) return node("span", className || "", name || "-");
    const button = node("button", className || "prospect-link", name || "Prospect");
    button.type = "button";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openProspect(prospectId);
    });
    return button;
  }

  function openProspect(prospectId) {
    const id = String(prospectId || "");
    state.selectedDraftProspectId = prospectId;
    state.selectedCalendarItem = null;
    const scoutingHasProspect = (data.scouting?.board || []).some((prospect) => String(prospect.prospect_id) === id);
    const draftHasProspect = (data.draft?.board || []).some((prospect) => String(prospect.prospect_id) === id);
    state.view = scoutingHasProspect || !draftHasProspect ? "scouting" : "draft";
    render();
  }

  function teamLogo(src, team, className) {
    if (src) {
      const img = node("img", className || "team-mini-logo");
      img.src = src;
      img.alt = team || "Team";
      return img;
    }
    return node("span", `${className || "team-mini-logo"} logo-fallback`.trim(), team || "-");
  }

  function smallPlayerCell(playerId, name, detail) {
    const wrap = node("span", "player-name-stack");
    append(wrap, [
      playerLink(playerId, name, "player-link strong-link"),
      detail ? node("small", null, detail) : null,
    ]);
    return wrap;
  }

  function row(title, detail, right, tone) {
    const item = node("div", "row");
    const left = append(node("div"), [
      node("strong", null, title),
      detail ? node("div", "muted", detail) : null,
    ]);
    append(item, [left, right ? tag(right, tone) : null]);
    return item;
  }

  function runnerMode() {
    return state.runnerAvailable && location.protocol.startsWith("http");
  }

  function showToast(message) {
    if (!refs.toast) return;
    refs.toast.textContent = message;
    refs.toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => {
      refs.toast.hidden = true;
    }, 3600);
  }

  async function loadLiveState() {
    if (!location.protocol.startsWith("http")) return false;
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (!response.ok) return false;
      data = await response.json();
      state.runnerAvailable = true;
      return true;
    } catch (_error) {
      state.runnerAvailable = false;
      return false;
    }
  }

  function applyStatePatch(patch) {
    if (!patch) return;
    data = {
      ...data,
      ...patch,
      settings: { ...(data.settings || {}), ...(patch.settings || {}) },
      activeSave: patch.activeSave || data.activeSave,
      commands: { ...(data.commands || {}), ...(patch.commands || {}) },
    };
  }

  async function runAction(action, params) {
    if (!runnerMode() || state.runnerBusy) return;
    if (!confirmBeforeAction(action)) return;
    state.runnerBusy = true;
    state.busyAction = action;
    showToast(`Running ${action}...`);
    render();
    try {
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, params: params || {} }),
      });
      const payload = await response.json();
      if (payload.state) {
        data = payload.state;
      }
      if (payload.statePatch) {
        applyStatePatch(payload.statePatch);
      }
      state.lastResult = payload;
      state.runnerAvailable = true;
      showToast(payload.returncode === 0 ? `${action} complete` : `${action} returned an issue`);
    } catch (error) {
      state.lastResult = { error: String(error) };
      showToast("Runner request failed");
    } finally {
      state.runnerBusy = false;
      state.busyAction = null;
      render();
    }
  }

  function draftNeedsAdvanceWarning(action) {
    if (!["advance_next_event", "advance_next_league_year"].includes(action)) return false;
    const draft = data.draft || {};
    const remaining = Number(draft.pickTotals?.remaining || 0);
    if (remaining <= 0) return false;
    const draftDate = draft.draftDate ? new Date(`${draft.draftDate}T00:00:00`) : null;
    const currentDate = data.currentDate ? new Date(`${data.currentDate}T00:00:00`) : null;
    return !draftDate || !currentDate || currentDate >= draftDate;
  }

  function confirmBeforeAction(action) {
    if (draftNeedsAdvanceWarning(action)) {
      const remaining = Number(data.draft?.pickTotals?.remaining || 0);
      return window.confirm(
        `The ${data.draft?.year || ""} draft still has ${remaining} pick(s) remaining. ` +
        "Advancing the calendar will auto-sim the rest of the draft, including any user-team picks that are still open.\n\nContinue?",
      );
    }
    return true;
  }

  function actionLabel(action) {
    return {
      sim_season: "Sim Rest Of Regular Season",
      sim_week: "Sim Week",
      complete_season: "Complete Season",
      contract_extend: "Extend Player",
      contract_release: "Release Player",
      contract_restructure: "Restructure Contract",
      depth_chart_set: "Set Depth Chart",
      depth_chart_move: "Move Depth Chart",
      postseason: "Run Postseason",
      validate_rosters: "Validate Rosters",
      advance_next_event: "Advance To Next Date",
      box_score: "Show Box Score",
      advance_to_draft: "Advance To Draft",
      free_agency_start: "Advance To Free Agency",
      free_agency_offer: "Submit FA Offer",
      free_agency_cpu_seed: "Seed CPU Offers",
      free_agency_advance_hour: "Advance FA Hour",
      free_agency_advance_day: "Advance FA Day",
      draft_skip: "Skip Draft Pick",
      draft_skip_to_user: "Skip To User Pick",
      draft_finish: "Finish Draft",
      draft_pick: "Make Draft Pick",
      draft_start: "Start Draft Room",
      ai_gm_setup: "Prepare AI GMs",
      ai_gm_enable_ollama: "Enable Ollama",
      ai_gm_show_config: "Show AI GM Config",
      ai_gm_profiles: "Show AI GM Profile",
      ai_gm_context: "Build AI GM Context",
      ai_gm_run: "Run AI GM Decision",
      ai_gm_logs: "Show AI GM Logs",
      scouting_setup: "Scouting Setup",
      scouting_assign: "Scouting Assignment",
      scouting_process_week: "Scouting Week",
      scouting_auto: "Auto Assign Scouts",
      scouting_one: "Scout Player",
      scouting_random_two: "Scout 3 Random Players",
      scouting_discover_four: "Discover 4 Non-Public Players",
      scouting_senior_bowl_setup: "Senior Bowl Setup",
      scouting_senior_bowl_process: "Senior Bowl",
      scouting_top30_visit: "Top 30 Visit",
      scouting_top30_auto: "Auto-Fill Top 30 Visits",
      inbox_mark_read: "Inbox Update",
      league_news_seed: "Refresh League News",
      event_generate_week: "Roll Weekly Events",
      new_june1_save: "Start Fresh June 1 Save",
      status: "Refresh Status",
      preflight: "Run Preflight",
      advance_next_league_year: "Advance To Next League Year",
      refresh: "Refresh UI",
    }[action] || action || "Command";
  }

  function busyMessage() {
    const action = state.busyAction || "command";
    const label = actionLabel(action);
    const extra = action === "sim_season"
      ? "\n\nFull-season sims can take a few minutes because the runner saves every game, rebuilds season stats, and runs weekly hooks. Output appears when the backend command returns."
      : "\n\nOutput appears when the backend command returns.";
    return `${label} is running...${extra}`;
  }

  function commandBox(label, command, action, params) {
    const box = node("div", "command-box");
    const top = node("div", "command-bar");
    const copy = node("button", "copy-button", "Copy");
    copy.type = "button";
    copy.addEventListener("click", () => navigator.clipboard?.writeText(command));
    const children = [node("span", "tag", label), copy];
    if (action && runnerMode()) {
      const run = node("button", "run-button", state.runnerBusy ? "Running" : "Run");
      run.type = "button";
      run.disabled = state.runnerBusy;
      run.addEventListener("click", () => runAction(action, params));
      children.push(run);
    }
    append(top, children);
    const code = node("code", null, command);
    append(box, [top, code]);
    return box;
  }

  function actionCard(title, detail, command, action, params, tone, options = {}) {
    const card = node("div", `action-card ${tone || ""}`.trim());
    const text = append(node("div", "action-copy"), [
      node("strong", null, title),
      detail ? node("span", null, detail) : null,
    ]);
    const controls = node("div", "action-controls");
    const copy = node("button", "copy-button", "Copy Command");
    copy.type = "button";
    copy.addEventListener("click", () => navigator.clipboard?.writeText(command || ""));
    controls.append(copy);
    if (action && runnerMode()) {
      const run = node("button", "primary-run-button", state.runnerBusy ? "Running" : (options.runLabel || "Run"));
      run.type = "button";
      run.disabled = state.runnerBusy || Boolean(options.disabledReason);
      if (options.disabledReason) run.title = options.disabledReason;
      run.addEventListener("click", () => runAction(action, params));
      controls.append(run);
    } else if (action) {
      controls.append(node("span", "muted", "Start the UI runner for one-click actions"));
    }
    append(card, [text, controls]);
    return card;
  }

  function nextStepPanel(title, kicker, cards) {
    const p = panel(title, kicker);
    const body = panelBody(p);
    const grid = node("div", "next-step-grid");
    cards.filter(Boolean).forEach((card) => grid.append(card));
    body.append(grid.children.length ? grid : node("div", "empty-state", "No next action available."));
    return p;
  }

  function freeAgencyStageLabel(stage) {
    return {
      day_one_hourly: "Day 1 Hourly Market",
      daily: "Daily Free Agency",
    }[stage] || roleLabel(stage || "Not started");
  }

  function freeAgencyNextCards(fa, commands) {
    const period = fa.period;
    if (!period) {
      return [
        actionCard(
          "Open Free Agency",
          "Process unextended expiring contracts into the market and start the first busy day.",
          commands.freeAgencyStart,
          "free_agency_start",
          {},
          "good",
          { runLabel: "Open Free Agency" },
        ),
      ];
    }

    const cards = [];
    const pending = Number(fa.counts?.pendingOffers || 0);
    const available = Number(fa.counts?.available || 0);
    if (period.current_stage === "day_one_hourly") {
      cards.push(actionCard(
        "Advance Free Agency Hour",
        `${freeAgencyStageLabel(period.current_stage)} at ${period.current_hour || 12}:00. CPU teams respond, counters can appear, and players may sign.`,
        commands.freeAgencyHour,
        "free_agency_advance_hour",
        {},
        pending ? "warn" : "good",
        { runLabel: "Advance Hour" },
      ));
    } else if (period.current_stage === "daily") {
      cards.push(actionCard(
        "Advance Free Agency Day",
        `${freeAgencyStageLabel(period.current_stage)} on ${shortDate(period.current_date)}. The market moves in daily chunks from here.`,
        commands.freeAgencyDay,
        "free_agency_advance_day",
        {},
        pending ? "warn" : "good",
        { runLabel: "Advance Day" },
      ));
    }

    cards.push(actionCard(
      "Advance To Draft",
      available
        ? `${available} players are still available. Use this when you are done shopping and want to jump to draft week.`
        : "Free agency is mostly cleared. Jump to draft week when ready.",
      commands.advanceToDraft,
      "advance_to_draft",
      {},
      "",
      { runLabel: "Go To Draft" },
    ));
    return cards;
  }

  function draftNextCards(draft, commands, selected) {
    const draftState = draft.state;
    const remaining = Number(draft.pickTotals?.remaining || 0);
    const cards = [];

    if (!draftState) {
      if (!dateReached(draft.draftDate)) {
        cards.push(actionCard(
          "Advance To Draft",
          `Jump from ${shortDate(data.currentDate)} to the ${draft.year || ""} draft on ${shortDate(draft.draftDate)}.`,
          commands.advanceToDraft,
          "advance_to_draft",
          {},
          "good",
          { runLabel: "Advance To Draft" },
        ));
      } else {
        cards.push(actionCard(
          "Start Draft Room",
          "Open the draft clock paused so you can inspect the board before picks begin.",
          commands.draftStart,
          "draft_start",
          {},
          "good",
          { runLabel: "Start Draft" },
        ));
      }
      return cards;
    }

    if (remaining <= 0) {
      cards.push(actionCard(
        "Advance To Next League Year",
        "All picks have been recorded. Jump to June 1, process post-draft calendar hooks, and generate the next draft class.",
        commands.advanceNextLeagueYear,
        "advance_next_league_year",
        {},
        "good",
        { runLabel: "Advance To June 1" },
      ));
      return cards;
    }

    if (isUserOnClock()) {
      const command = selected ? (commands.draftPick || "").replace("<id>", selected.prospect_id) : commands.draftPick;
      cards.push(actionCard(
        selected ? `Make Pick: ${selected.player_name}` : "Make Your Pick",
        selected
          ? `${draftState.current_team || data.activeSave?.user_team || "Your team"} is on the clock at pick #${draftState.current_pick_number || "-"}.`
          : "Your team is on the clock. Select a prospect from the board first.",
        command,
        selected ? "draft_pick" : null,
        selected ? { prospect_id: selected.prospect_id } : {},
        "warn",
        { runLabel: "Draft Player" },
      ));
    } else {
      cards.push(actionCard(
        "Skip Next Pick",
        `${draftState.current_team || "CPU"} is on the clock. This advances one pick at a time and stops when your team is up.`,
        commands.draftSkipOne,
        "draft_skip",
        { count: 1 },
        "good",
        { runLabel: "Skip Next Pick" },
      ));
      cards.push(actionCard(
        "Skip To Next User Pick",
        `Auto-pick CPU selections until ${draftState.user_team || data.activeSave?.user_team || "your team"} is back on the clock.`,
        commands.draftSkipToUser || commands.draftSkip,
        "draft_skip_to_user",
        {},
        "good",
        { runLabel: "Skip To User Pick" },
      ));
    }

    cards.push(actionCard(
      "Auto Finish Draft",
      "Sim the rest of the draft, including any remaining user-team picks, and then review your haul.",
      commands.draftFinish,
      "draft_finish",
      {},
      "",
      { runLabel: "Finish Draft" },
    ));
    return cards;
  }

  function gameLine(game, userTeam) {
    const played = Number(game.played || 0) === 1;
    const away = game.away_team || "AWAY";
    const home = game.home_team || "HOME";
    const title = played
      ? `${away} ${game.away_score ?? "-"} at ${home} ${game.home_score ?? "-"}`
      : `${away} at ${home}`;
    const detail = `Week ${game.week || "-"} | ${shortDate(game.game_date)}${game.game_time_et ? ` | ${game.game_time_et} ET` : ""}`;
    let right = played ? "Final" : "Upcoming";
    let tone = played ? "good" : "";
    if (userTeam && (away === userTeam || home === userTeam)) {
      if (played) {
        const userScore = away === userTeam ? Number(game.away_score || 0) : Number(game.home_score || 0);
        const oppScore = away === userTeam ? Number(game.home_score || 0) : Number(game.away_score || 0);
        right = userScore > oppScore ? "Win" : userScore < oppScore ? "Loss" : "Tie";
        tone = userScore > oppScore ? "good" : userScore < oppScore ? "bad" : "warn";
      } else {
        right = "Vikings";
        tone = "warn";
      }
    }
    return row(title, detail, right, tone);
  }

  function runnerOutputPanel() {
    if (state.runnerBusy) {
      const p = panel("Runner Output", actionLabel(state.busyAction));
      panelBody(p).append(node("pre", "runner-output", busyMessage()));
      return p;
    }
    if (!state.lastResult) return null;
    const p = panel("Runner Output", state.lastResult.action || "Latest");
    const result = state.lastResult;
    const body = [
      result.command ? `> ${result.command}` : "",
      result.returncode !== undefined ? `return code: ${result.returncode}` : "",
      result.error ? `error: ${result.error}` : "",
      result.stdout ? `\nstdout:\n${result.stdout}` : "",
      result.stderr ? `\nstderr:\n${result.stderr}` : "",
    ].filter(Boolean).join("\n");
    panelBody(p).append(node("pre", "runner-output", body || "No output."));
    return p;
  }

  function runnerBusyBanner() {
    if (!state.runnerBusy) return null;
    const banner = node("div", "runner-busy-banner");
    const text = append(node("div"), [
      node("strong", null, `${actionLabel(state.busyAction)} is running`),
      node("span", null, "Keep this page open. Results will appear in Runner Output when the command finishes."),
    ]);
    append(banner, [node("span", "spinner"), text]);
    return banner;
  }

  function finishRender(root) {
    const banner = runnerBusyBanner();
    if (banner) root.prepend(banner);
    refs.content.replaceChildren(root);
  }

  function renderMetrics() {
    const season = data.season || { totals: {} };
    const draft = data.draft || { pickTotals: {} };
    const fa = data.freeAgency || { counts: {} };
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Regular Season", `${season.totals.played || 0}/${season.totals.games || 0}`, `${season.totals.remaining || 0} games left`, season.totals.remaining ? "warn" : "good"),
      metric("Next Week", season.nextWeek ? `Week ${season.nextWeek}` : "Done", "Regular season queue"),
      metric("Free Agents", String(fa.counts.available || 0), `${fa.counts.pendingOffers || 0} pending offers`, fa.counts.pendingOffers ? "warn" : ""),
      metric("Draft Picks", `${draft.pickTotals.used || 0}/${draft.pickTotals.total || 0}`, `${draft.pickTotals.remaining || 0} remaining`, draft.pickTotals.remaining ? "warn" : "good"),
    ]);
    return metrics;
  }

  function latestActivityItems() {
    const items = [];
    (data.leagueNews?.items || []).slice(0, 12).forEach((item) => {
      items.push({
        date: item.news_date,
        type: item.category || "News",
        title: item.title || "League news",
        detail: item.body || "",
        view: "leagueNews",
        tone: Number(item.is_major || 0) ? "warn" : "good",
        weight: Number(item.is_major || 0) ? 3 : 1,
      });
    });
    (data.scouting?.inbox || []).slice(0, 8).forEach((message) => {
      items.push({
        date: message.message_date,
        type: message.category || "Inbox",
        title: message.title || "Inbox message",
        detail: message.body || "",
        view: "inbox",
        tone: Number(message.is_read || 0) ? "" : "warn",
        weight: Number(message.is_read || 0) ? 1 : 2,
      });
    });
    (data.alerts || []).slice(0, 8).forEach((alert) => {
      items.push({
        date: alert.alert_date || alert.due_date,
        type: alert.severity || "Alert",
        title: alert.title || "Alert",
        detail: alert.message || "",
        view: "calendar",
        tone: alert.severity === "ERROR" ? "bad" : "warn",
        weight: alert.severity === "ERROR" ? 4 : 2,
      });
    });
    (data.log || []).slice(0, 8).forEach((entry) => {
      items.push({
        date: entry.game_date,
        type: entry.log_type || "Log",
        title: entry.title || "Game flow",
        detail: entry.details || "",
        view: "calendar",
        tone: "",
        weight: 0,
      });
    });
    return items
      .filter((item) => item.title)
      .sort((a, b) => {
        const byDate = String(b.date || "").localeCompare(String(a.date || ""));
        if (byDate) return byDate;
        return Number(b.weight || 0) - Number(a.weight || 0);
      })
      .slice(0, 8);
  }

  function renderLatestActivity() {
    const newsCount = Number(data.leagueNews?.counts?.total || 0);
    const unread = Number(data.scouting?.counts?.unread || 0);
    const alertCount = Number((data.alerts || []).length);
    const p = panel("Latest Activity", "News, Inbox, Alerts");
    const body = panelBody(p);
    const summary = node("section", "activity-summary");
    append(summary, [
      metric("League News", String(newsCount), `${Number(data.leagueNews?.counts?.major || 0)} major`, newsCount ? "good" : ""),
      metric("Inbox", String(unread), unread ? "unread messages" : "caught up", unread ? "warn" : "good"),
      metric("Alerts", String(alertCount), alertCount ? "open items" : "none open", alertCount ? "warn" : "good"),
    ]);
    body.append(summary);

    const list = node("div", "activity-list");
    latestActivityItems().forEach((item) => {
      const button = node("button", `activity-item ${item.tone ? `tone-${item.tone}` : ""}`.trim());
      button.type = "button";
      button.addEventListener("click", () => {
        state.view = item.view || "leagueNews";
        render();
      });
      append(button, [
        append(node("div", "activity-copy"), [
          append(node("div", "activity-topline"), [
            node("strong", null, item.title),
            node("span", "event-date", shortDate(item.date)),
          ]),
          item.detail ? node("p", null, item.detail) : null,
        ]),
        tag(item.type, item.tone),
      ]);
      list.append(button);
    });
    body.append(list.children.length ? list : node("div", "empty-state", "No recent activity yet."));
    return p;
  }

  function currentPhaseKey() {
    const phase = String(data.currentPhase || "").toLowerCase();
    const currentDate = String(data.currentDate || "");
    const season = data.season || {};
    const draft = data.draft || {};
    const fa = data.freeAgency || {};
    const draftYear = Number(draft.year || data.currentSeason + 1 || 2027);
    const freeAgencyDate = `${draftYear}-03-10`;
    const draftDate = String(draft.draftDate || `${draftYear}-04-22`);
    if ((season.nextWeek || 0) > 0 || Number(season.totals?.remaining || 0) > 0) return "season";
    if (Number(season.postseason?.remaining || 0) > 0) return "playoffs";
    if (phase.includes("offseason") && (Number(fa.counts?.pendingOffers || 0) > 0 || (currentDate >= freeAgencyDate && currentDate < draftDate))) return "freeAgency";
    if (Number(draft.pickTotals?.remaining || 0) > 0 && Number(draft.pickTotals?.used || 0) > 0) return "draft";
    if (phase.includes("offseason") && Number(draft.pickTotals?.remaining || 0) > 0) return "draft";
    if (phase.includes("offseason")) return "rosterBuild";
    return "season";
  }

  function workflowStep(key, label, detail, view, complete) {
    const current = currentPhaseKey();
    const card = node("button", `workflow-step ${complete ? "complete" : ""} ${current === key ? "active" : ""}`.trim());
    card.type = "button";
    card.addEventListener("click", () => {
      state.view = view;
      render();
    });
    append(card, [
      node("span", "workflow-state", complete ? "Done" : current === key ? "Now" : "Next"),
      node("strong", null, label),
      node("small", null, detail),
    ]);
    return card;
  }

  function renderWorkflowPanel() {
    const season = data.season || {};
    const draft = data.draft || {};
    const currentDate = String(data.currentDate || "");
    const draftYear = Number(draft.year || data.currentSeason + 1 || 2027);
    const freeAgencyDate = `${draftYear}-03-10`;
    const regularDone = Number(season.totals?.games || 0) > 0 && Number(season.totals?.remaining || 0) === 0;
    const playoffsDone = regularDone && Number(season.postseason?.games || 0) > 0 && Number(season.postseason?.remaining || 0) === 0;
    const draftDone = Number(draft.pickTotals?.total || 0) > 0 && Number(draft.pickTotals?.remaining || 0) === 0;
    const p = panel("Playable Flow", "Guided Path");
    const strip = node("div", "workflow-strip");
    append(strip, [
      workflowStep("season", "Season", season.nextWeek ? `Next: Week ${season.nextWeek}` : "Regular season complete", "season", regularDone),
      workflowStep("playoffs", "Playoffs", playoffsDone ? "Bracket complete" : "Run after Week 18", "season", playoffsDone),
      workflowStep("contracts", "Contracts", "Handle expiring players", "contracts", currentDate >= freeAgencyDate),
      workflowStep("freeAgency", "Free Agency", `${data.freeAgency?.counts?.pendingOffers || 0} pending offers`, "freeAgency", currentDate > freeAgencyDate),
      workflowStep("draft", "Draft", `${draft.pickTotals?.remaining || 0} picks left`, "draft", draftDone),
      workflowStep("rosterBuild", "Roster Build", "Depth, cuts, camp", "depth", false),
    ]);
    panelBody(p).append(strip);
    return p;
  }

  function renderOverview() {
    setHeader("Season Hub", "A simple control room for testing seasons: sim weeks, check the league table, move through the calendar, and keep an eye on your Vikings save.");
    const root = document.createDocumentFragment();
    const summary = panel("Save Snapshot", "Current Run");
    panelBody(summary).append(renderMetrics());
    root.append(summary);
    root.append(renderWorkflowPanel());

    const next = panel("Quick Sim", "One-Click Season Flow");
    const nextBody = panelBody(next);
    const commands = data.commands || {};
    const season = data.season || {};
    nextBody.append(
      actionCard(
        "Start Fresh June 1 Save",
        "Create a new active Vikings save on June 1, with offseason roster limits off. This is the default starting point.",
        commands.newJune1Save || commands.newGame,
        "new_june1_save",
        {
          start_year: data.currentSeason || 2026,
          user_team: data.activeSave?.user_team || "MIN",
          name: `${data.activeSave?.user_team || "MIN"} June 1 Start`,
        },
        "",
      )
    );
    if (season.nextWeek) {
      nextBody.append(
        actionCard(
          `Sim Week ${season.nextWeek}`,
          "Play every game in the next unplayed regular-season week, then run weekly hooks.",
          commands.simNextWeek.replace("<week>", season.nextWeek),
          "sim_week",
          { week: season.nextWeek },
          "good",
        )
      );
      nextBody.append(
        actionCard(
          "Sim Rest Of Regular Season",
          "Run all remaining regular-season games. This is the fastest stress test for standings and season flow.",
          commands.simSeason,
          "sim_season",
          {},
          "warn",
        )
      );
    } else if ((season.totals?.games || 0) > 0 && (season.totals?.remaining || 0) === 0 && (season.postseason?.remaining || 0) > 0) {
      nextBody.append(actionCard("Run Postseason", "Sim the playoff bracket from the current season state.", commands.postseason, "postseason", {}, "good"));
    } else if (
      (season.totals?.games || 0) > 0
      && (season.totals?.remaining || 0) === 0
      && (season.postseason?.games || 0) > 0
      && (season.postseason?.remaining || 0) === 0
      && !season.completion
    ) {
      nextBody.append(actionCard("Complete Season", "Write draft order, build next season's schedule, run progression/regression, and advance to the post-Super-Bowl offseason.", commands.completeSeason, "complete_season", {}, "good"));
    } else {
      nextBody.append(actionCard("Refresh Status", "Check the active save and current calendar phase.", commands.status, "status", {}, ""));
    }
    if (String(data.currentPhase || "").toLowerCase().includes("offseason") || season.completion) {
      nextBody.append(
        actionCard(
          "Advance To Free Agency",
          "Process your own expired contracts and open the free-agent market.",
          commands.freeAgencyStart,
          "free_agency_start",
          {},
          "good",
        )
      );
    }
    if (data.draft?.draftDate && String(data.currentPhase || "").toLowerCase().includes("offseason")) {
      nextBody.append(
        actionCard(
          "Advance To Draft",
          `Fast-forward to ${shortDate(data.draft.draftDate)}, resolve the current free-agency tick, and open the draft room paused.`,
          commands.advanceToDraft,
          "advance_to_draft",
          {},
          "good",
        )
      );
    }
    nextBody.append(actionCard("Validate Rosters", "Confirm every team is legal before or after a sim chunk.", commands.validateRosters, "validate_rosters", {}, ""));
    nextBody.append(actionCard("Run Preflight Check", "Read-only sanity check for the active save, schedule, draft class, hooks, and UI export.", commands.preflight, "preflight", {}, ""));
    const draftRemaining = Number(data.draft?.pickTotals?.remaining || 0);
    const advanceDetail = draftRemaining > 0
      ? `Warning: advancing past the draft will auto-sim ${draftRemaining} remaining pick(s).`
      : "Move to the next calendar event when there are no games to sim.";
    nextBody.append(actionCard("Advance To Next Date", advanceDetail, commands.advanceNextEvent, "advance_next_event", {}, draftRemaining > 0 ? "warn" : ""));
    const nextLeagueYear = Number(data.draft?.year || data.currentSeason || 0);
    if (data.currentDate && nextLeagueYear && data.currentDate < `${nextLeagueYear}-06-01`) {
      nextBody.append(actionCard(
        "Advance To Next League Year",
        "Jump to June 1 after the draft. If the draft is unfinished, the remaining picks will be auto-simmed first.",
        commands.advanceNextLeagueYear,
        "advance_next_league_year",
        {},
        draftRemaining > 0 ? "warn" : "good",
      ));
    }
    root.append(next);

    const rookieClass = data.rookieClass || {};
    if ((rookieClass.selections || []).length) {
      root.append(draftUserSelectionsPanel(
        rookieClass.selections,
        {
          state: { user_team: data.activeSave?.user_team },
          pickTotals: { total: rookieClass.selections.length, used: rookieClass.selections.length, remaining: 0 },
        },
      ));
    }

    const grid = node("div", "grid");
    const upcoming = panel("Next Week Slate", season.nextWeek ? `Week ${season.nextWeek}` : "No Regular-Season Games");
    const upcomingList = node("div", "list compact-list");
    (season.nextWeekGames || []).slice(0, 16).forEach((game) => {
      upcomingList.append(gameLine(game, data.activeSave?.user_team));
    });
    panelBody(upcoming).append(upcomingList.children.length ? upcomingList : node("div", "empty-state", "No upcoming week games found."));

    const recent = panel("Recent Results", "Latest Finals");
    const recentList = node("div", "list compact-list");
    (season.recentResults || []).slice(0, 12).forEach((game) => {
      recentList.append(gameLine(game, data.activeSave?.user_team));
    });
    panelBody(recent).append(recentList.children.length ? recentList : node("div", "empty-state", "No games have been played yet."));
    append(grid, [upcoming, recent]);
    root.append(grid);
    root.append(renderLatestActivity());

    const events = panel("Important Dates", "Upcoming");
    const eventList = node("div", "list compact-list");
    (data.events || []).slice(0, 6).forEach((event) => {
      eventList.append(row(event.event_name, `${event.phase_name || ""} | ${event.event_category || ""}`, shortDate(event.event_start_date)));
    });
    panelBody(events).append(eventList.children.length ? eventList : node("div", "empty-state", "No upcoming events exported."));
    root.append(events);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderSeason() {
    setHeader("League Table", "Weekly progress, standings, Vikings schedule, and recent finals.");
    const root = document.createDocumentFragment();
    const season = data.season || { weeks: [], totals: {}, postseason: {} };
    const metrics = panel("Season Progress", `${season.season || data.currentSeason}`);
    panelBody(metrics).append(renderMetrics());
    root.append(metrics);

    const weeksPanel = panel("Weeks", "Regular Season");
    const weeks = node("div", "week-grid");
    (season.weeks || []).forEach((item) => {
      const week = node("div", "week");
      const percent = pct(item.played, item.games);
      const bar = node("div", "progress");
      const fill = node("span");
      fill.style.width = `${percent}%`;
      bar.append(fill);
      append(week, [
        node("strong", null, `Week ${item.week}`),
        node("div", "muted", `${item.played || 0}/${item.games || 0} games`),
        bar,
      ]);
      weeks.append(week);
    });
    panelBody(weeksPanel).append(weeks.children.length ? weeks : node("div", "empty-state", "No schedule weeks found."));
    root.append(weeksPanel);

    const grid = node("div", "grid");
    const commandsPanel = panel("Season Controls", "Sim Chunks");
    const cb = panelBody(commandsPanel);
    const commands = data.commands || {};
    cb.append(actionCard("Sim Next Week", season.nextWeek ? `Week ${season.nextWeek}` : "No regular-season week is queued.", commands.simNextWeek.replace("<week>", season.nextWeek || "<week>"), season.nextWeek ? "sim_week" : null, { week: season.nextWeek }, "good"));
    cb.append(actionCard("Sim Regular Season", "Run all remaining regular-season games and weekly hooks.", commands.simSeason, "sim_season", {}, "warn"));
    cb.append(actionCard("Complete Season", "Roll the completed season into the next league year when ready.", commands.completeSeason, "complete_season", {}, ""));

    const standingsPanel = panel("Top Standings", "Current");
    const list = node("div", "list");
    (season.standings || []).slice(0, 10).forEach((team) => {
      const diff = Number(team.points_for || 0) - Number(team.points_against || 0);
      list.append(row(team.team_name, `${team.conference} | ${team.division}`, `${team.wins}-${team.losses}-${team.ties} (${diff >= 0 ? "+" : ""}${diff})`));
    });
    panelBody(standingsPanel).append(list.children.length ? list : node("div", "empty-state", "Standings will populate after games are simmed."));
    append(grid, [commandsPanel, standingsPanel]);
    root.append(grid);

    const scheduleGrid = node("div", "grid");
    const userTeam = data.activeSave?.user_team;
    const userSchedule = panel(userTeam ? `${userTeam} Schedule` : "User Team Schedule", "Regular Season");
    const scheduleList = node("div", "list compact-list");
    (season.userTeamSchedule || []).forEach((game) => {
      scheduleList.append(gameLine(game, userTeam));
    });
    panelBody(userSchedule).append(scheduleList.children.length ? scheduleList : node("div", "empty-state", "No user-team schedule exported."));

    const resultsPanel = panel("Recent Finals", "League");
    const resultsList = node("div", "list compact-list");
    (season.recentResults || []).slice(0, 18).forEach((game) => {
      resultsList.append(gameLine(game, userTeam));
    });
    panelBody(resultsPanel).append(resultsList.children.length ? resultsList : node("div", "empty-state", "No completed games yet."));
    append(scheduleGrid, [userSchedule, resultsPanel]);
    root.append(scheduleGrid);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderFreeAgency() {
    setHeader("Free Agency", "Opening day can move hour by hour. After the busy day, use daily advancement.");
    const root = document.createDocumentFragment();
    const fa = data.freeAgency || { counts: {}, board: [], offers: [], events: [] };
    const period = fa.period;
    const status = panel("Market Status", period ? period.current_stage : "Not Started");
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Available", String(fa.counts.available || 0), "Market pool"),
      metric("Signed", String(fa.counts.signed || 0), "Processor signings"),
      metric("Pending Offers", String(fa.counts.pendingOffers || 0), "Awaiting decisions", fa.counts.pendingOffers ? "warn" : ""),
      metric("Clock", period ? `${shortDate(period.current_date)} ${period.current_stage === "day_one_hourly" ? `${period.current_hour}:00` : ""}` : shortDate(fa.startDate), period ? "FA state" : "Scheduled start"),
    ]);
    panelBody(status).append(metrics);
    root.append(status);

    const commands = data.commands || {};
    root.append(nextStepPanel(
      "Next Free Agency Step",
      period ? freeAgencyStageLabel(period.current_stage) : "Not Started",
      freeAgencyNextCards(fa, commands),
    ));

    const grid = node("div", "grid");
    const commandsPanel = panel("Market Commands", "Advanced / Manual");
    const commandBody = panelBody(commandsPanel);
    commandBody.append(commandBox("Advance To Free Agency", commands.freeAgencyStart, "free_agency_start"));
    commandBody.append(commandBox("Seed CPU Offers", commands.freeAgencyCpuSeed, "free_agency_cpu_seed"));
    commandBody.append(commandBox("Advance Hour", commands.freeAgencyHour, "free_agency_advance_hour"));
    commandBody.append(commandBox("Advance Day", commands.freeAgencyDay, "free_agency_advance_day"));
    commandBody.append(commandBox("Advance To Draft", commands.advanceToDraft, "advance_to_draft"));
    commandBody.append(commandBox("Manual Offer", commands.freeAgencyOffer));

    const eventPanel = panel("Market Log", "Recent");
    const eventList = node("div", "list");
    (fa.events || []).slice(0, 10).forEach((event) => {
      eventList.append(row(event.message, event.event_type, event.event_hour !== null && event.event_hour !== undefined ? `${event.event_hour}:00` : shortDate(event.event_date)));
    });
    panelBody(eventPanel).append(eventList.children.length ? eventList : node("div", "empty-state", "No free agency events yet."));
    append(grid, [commandsPanel, eventPanel]);
    root.append(grid);

    const boardRows = (fa.board || []).filter((player) => !player.market_status || player.market_status === "available");
    const boardPanel = panel("Top Market", `${boardRows.length || 0} available`);
    panelBody(boardPanel).append(table(["Player", "Pos", "Tier", "Ask", "Pref", "Leading Bid", "Offers", "Action"], boardRows.slice(0, 30).map((player) => [
      smallPlayerCell(player.player_id, player.player_name, `${player.age || "-"} | ${player.college || ""}`),
      player.position,
      player.market_tier,
      money(player.asking_aav),
      freeAgencyPreferenceCell(player),
      leadingBidCell(player),
      String(player.pending_offers || 0),
      freeAgencyOfferButton(player),
    ])));
    root.append(boardPanel);
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function leadingBidCell(player) {
    if (!player.best_aav) return "-";
    const wrap = node("span", "bid-leader");
    if (player.best_offer_team_logo) {
      const img = node("img", "team-mini-logo");
      img.src = player.best_offer_team_logo;
      img.alt = player.best_offer_team || "Team";
      wrap.append(img);
    }
    wrap.append(node("span", null, `${player.best_offer_team || "-"} ${money(player.best_aav)}`));
    return wrap;
  }

  function freeAgencyPreferenceCell(player) {
    const wrap = node("span", "fa-preference-cell");
    const archetype = String(player.preference_archetype || "balanced").replaceAll("_", " ");
    wrap.append(node("strong", null, archetype));
    wrap.append(node("small", null, `${player.contract_year_preference || player.preferred_years || 1} yr pref | role ${player.role_priority || 10}/20`));
    return wrap;
  }

  function freeAgencyOfferButton(player) {
    const wrap = node("span", "fa-offer-controls");
    const defaultYears = Number(player.contract_year_preference || player.preferred_years || 1);
    const defaultAav = Number(player.asking_aav || player.minimum_aav || 0);
    const guaranteePct = Number(player.guarantee_pct || 0);

    const yearsInput = node("input", "offer-input offer-years");
    yearsInput.type = "number";
    yearsInput.min = "1";
    yearsInput.max = "5";
    yearsInput.step = "1";
    yearsInput.value = String(defaultYears);
    yearsInput.title = "Years";

    const aavInput = node("input", "offer-input offer-aav");
    aavInput.type = "number";
    aavInput.min = "0";
    aavInput.step = "50000";
    aavInput.value = String(defaultAav);
    aavInput.title = "AAV";

    const currentOffer = () => {
      const years = Math.max(1, Math.min(5, Number(yearsInput.value || defaultYears || 1)));
      const aav = Math.max(0, Number(aavInput.value || defaultAav || 0));
      const bonus = roundTo(aav * years * 0.08, 50_000);
      const command = (data.commands?.freeAgencyOffer || "")
        .replace("<id>", player.player_id)
        .replace("<years>", years)
        .replace("<aav>", aav);
      return {
        years,
        aav,
        bonus,
        command: `${command} --bonus ${bonus} --guarantee-pct ${guaranteePct}`,
      };
    };

    const copy = node("button", "copy-button", "Copy");
    copy.type = "button";
    copy.addEventListener("click", () => navigator.clipboard?.writeText(currentOffer().command));
    wrap.append(node("span", "offer-label", "Yrs"));
    wrap.append(yearsInput);
    wrap.append(node("span", "offer-label", "AAV"));
    wrap.append(aavInput);
    wrap.append(copy);
    if (runnerMode()) {
      const run = node("button", "run-button", state.runnerBusy ? "Running" : "Offer");
      run.type = "button";
      run.disabled = state.runnerBusy || !defaultAav;
      run.addEventListener("click", () => {
        const offer = currentOffer();
        runAction("free_agency_offer", {
          player_id: player.player_id,
          years: offer.years,
          aav: offer.aav,
          bonus: offer.bonus,
          guarantee_pct: guaranteePct,
          cpu_response_offers: 2,
        });
      });
      wrap.append(run);
    }
    return wrap;
  }

  function renderContracts() {
    setHeader("Contract Talks", "Own-team expiring contracts up top, projected cap-casualty decisions below.");
    const root = document.createDocumentFragment();
    const talks = data.contractNegotiations || { counts: {}, expiring: [], capCasualties: [], restructureCandidates: [] };
    const cap = talks.projectedCap || talks.cap || {};
    const currentCap = talks.currentCap || {};
    const counts = talks.counts || {};
    const contractYear = talks.extensionStartYear || cap.season || "";

    const summary = panel("Negotiation Snapshot", talks.team || data.activeSave?.user_team || "");
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Expiring", String(counts.total || 0), `${contractYear} contract decisions`),
      metric("Priority", String(counts.priority || 0), "Core retain targets", counts.priority ? "warn" : ""),
      metric("Cap Casualties", String(counts.capCasualties || 0), "Release candidates"),
      metric("Restructures", String(counts.restructures || 0), "Move cap forward"),
      metric("Projected Cap", money(cap.cap_space), `Top 51 ${cap.season || contractYear}`, Number(cap.cap_space || 0) < 0 ? "bad" : ""),
    ]);
    panelBody(summary).append(metrics);
    const note = node("div", "quiet cap-context", `Current ${currentCap.season || data.currentSeason || ""} space: ${money(currentCap.cap_space)}. Extensions and cap-casualty decisions are shown against projected ${contractYear} space because that is where new deals begin.`);
    panelBody(summary).append(note);
    if (talks.error) panelBody(summary).append(node("div", "empty-state", talks.error));
    panelBody(summary).append(
      actionCard(
        "Advance To Free Agency",
        "Process unextended expiring contracts into the market and open the first busy day of free agency.",
        (data.commands || {}).freeAgencyStart || "",
        "free_agency_start",
        {},
        Number(cap.cap_space || 0) < 0 ? "warn" : "good",
        { runLabel: "Open Free Agency" },
      )
    );
    root.append(summary);

    const commands = data.commands || {};
    const split = node("div", "contract-split");

    const expiringPanel = panel("Expiring Players", `${(talks.expiring || []).length} shown`);
    const expiringBody = panelBody(expiringPanel);
    expiringBody.append(commandBox("List Expiring", commands.contractList || ""));
    expiringBody.append(table(["Player", "Pos", "Age", "Role", "Current", "Ask", "Years", "Priority", "Action"], (talks.expiring || []).map((player) => [
      playerLink(player.player_id, player.player_name),
      player.position,
      whole(player.age),
      player.market_tier || "-",
      money(player.aav),
      money(player.asking_aav),
      `${player.suggested_years || 1}`,
      player.priority || "-",
      contractExtendButton(player),
    ])));
    split.append(expiringPanel);

    const casualtyPanel = panel("Projected Cap Actions", `${(talks.capCasualties || []).length} releases, ${(talks.restructureCandidates || []).length} restructures`);
    const casualtyBody = panelBody(casualtyPanel);
    casualtyBody.append(node("h3", "subsection-title", "Release Candidates"));
    casualtyBody.append(table(["Player", "Pos", "Age", "Role", "Cap Hit", "Dead", "Est Save", "Thru", "Action"], (talks.capCasualties || []).map((player) => [
      playerLink(player.player_id, player.player_name),
      player.position,
      whole(player.age),
      player.market_tier || "-",
      money(player.cap_hit),
      money(player.dead_cap_if_cut_pre_june1),
      money(player.net_savings_pre_june1),
      player.end_year || "-",
      contractReleaseButton(player),
    ])));
    casualtyBody.append(node("h3", "subsection-title", "Restructure Candidates"));
    casualtyBody.append(table(["Player", "Pos", "Age", "Cap Hit", "Convert", "Save Now", "Prorate", "Thru", "Action"], (talks.restructureCandidates || []).map((player) => [
      playerLink(player.player_id, player.player_name),
      player.position,
      whole(player.age),
      money(player.cap_hit),
      money(player.suggested_convert),
      money(player.estimated_current_savings),
      `${player.proration_years || 1} yr`,
      player.end_year || "-",
      contractRestructureButton(player),
    ])));
    casualtyBody.append(node("div", "quiet cap-context", "Release savings account for the current Top 51 replacement. Restructures convert salary into bonus and push prorated cap into future years."));
    split.append(casualtyPanel);
    root.append(split);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function contractExtendButton(player) {
    const wrap = node("span", "action-cell");
    const base = (data.commands?.contractExtend || "").replace("<id>", player.player_id);
    const command = `${base} --years ${player.suggested_years || 1} --aav ${player.asking_aav || 0}`;
    const copy = node("button", "copy-button", "Copy");
    copy.type = "button";
    copy.addEventListener("click", () => navigator.clipboard?.writeText(command));
    wrap.append(copy);
    if (runnerMode()) {
      const run = node("button", "run-button", state.runnerBusy ? "Running" : "Extend");
      run.type = "button";
      run.disabled = state.runnerBusy;
      run.addEventListener("click", () => runAction("contract_extend", {
        player_id: player.player_id,
        years: player.suggested_years || 1,
        aav: player.asking_aav || 0,
      }));
      wrap.append(run);
    }
    return wrap;
  }

  function contractReleaseButton(player) {
    const wrap = node("span", "action-cell");
    const base = (data.commands?.contractRelease || "").replace("<id>", player.player_id);
    const copy = node("button", "copy-button", "Copy");
    copy.type = "button";
    copy.addEventListener("click", () => navigator.clipboard?.writeText(base));
    wrap.append(copy);
    if (runnerMode()) {
      const run = node("button", "run-button danger", state.runnerBusy ? "Running" : "Release");
      run.type = "button";
      run.disabled = state.runnerBusy;
      run.addEventListener("click", () => runAction("contract_release", {
        player_id: player.player_id,
      }));
      wrap.append(run);
    }
    return wrap;
  }

  function contractRestructureButton(player) {
    const wrap = node("span", "action-cell");
    const base = (data.commands?.contractRestructure || "").replace("<id>", player.player_id);
    const command = `${base} --amount ${player.suggested_convert || 0}`;
    const copy = node("button", "copy-button", "Copy");
    copy.type = "button";
    copy.addEventListener("click", () => navigator.clipboard?.writeText(command));
    wrap.append(copy);
    if (runnerMode()) {
      const run = node("button", "run-button", state.runnerBusy ? "Running" : "Restructure");
      run.type = "button";
      run.disabled = state.runnerBusy;
      run.addEventListener("click", () => runAction("contract_restructure", {
        player_id: player.player_id,
        amount: player.suggested_convert || 0,
      }));
      wrap.append(run);
    }
    return wrap;
  }

  function slotBasePositions(slot) {
    const key = String(slot || "").toUpperCase();
    if (["LWR", "RWR", "SWR", "KR", "PR"].includes(key)) return ["WR", "RB", "CB"];
    if (["LT", "RT"].includes(key)) return ["OT"];
    if (["LG", "RG"].includes(key)) return ["OG", "C"];
    if (key === "C") return ["C", "OG"];
    if (["LEDGE", "REDGE"].includes(key)) return ["EDGE", "OLB"];
    if (["LDL", "RDL", "NT"].includes(key)) return ["IDL", "DT", "DE"];
    if (["WLB", "MLB", "SLB"].includes(key)) return ["ILB", "LB", "OLB", "SS"];
    if (["LCB", "RCB", "NB"].includes(key)) return ["CB", "S"];
    if (["FS", "SS"].includes(key)) return ["FS", "SS", "S", "CB"];
    if (["PK", "KO"].includes(key)) return ["K", "PK"];
    return [key];
  }

  function playerFitsSlot(player, slot) {
    const bases = slotBasePositions(slot);
    if (bases.includes(player.position)) return true;
    return (player.flex || []).some((item) => bases.includes(item.position) || String(item.position).toUpperCase() === String(slot).toUpperCase());
  }

  function selectedDepthSlot(depth) {
    const slots = (depth.units || []).flatMap((unit) => unit.slots || []);
    if (!slots.length) return null;
    const selected = slots.find((slot) => slot.slot === state.selectedDepthSlot);
    if (selected) return selected;
    state.selectedDepthSlot = slots[0].slot;
    return slots[0];
  }

  function renderDepthChart() {
    const team = data.activeSave?.user_team || data.depthChart?.team || "MIN";
    setHeader("Depth Chart", `Adjust ${team}'s playable depth chart. These changes write to the active save.`);
    const root = document.createDocumentFragment();
    const depth = data.depthChart || { rows: [], roster: [], units: [] };
    const selected = selectedDepthSlot(depth);
    const commands = data.commands || {};

    const summary = panel("Depth Snapshot", depth.teamName || team);
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Roster", String((depth.roster || []).length), "Available players"),
      metric("Depth Rows", String((depth.rows || []).length), "Assigned slots"),
      metric("Selected Slot", selected?.slot || "-", selected ? `${selected.players?.length || 0} players` : "Choose a slot"),
      metric("Team", depth.team || team, "Active save"),
    ]);
    panelBody(summary).append(metrics);
    root.append(summary);

    const layout = node("div", "depth-editor-layout");
    const slotsPanel = panel("Slots", "Click a slot to edit");
    const slotBody = panelBody(slotsPanel);
    (depth.units || []).forEach((unit) => {
      const unitTitle = node("h3", "subsection-title", unit.unit);
      slotBody.append(unitTitle);
      const slotGrid = node("div", "slot-grid");
      (unit.slots || []).forEach((slot) => {
        const button = node("button", `slot-card ${slot.slot === selected?.slot ? "active" : ""}`.trim());
        button.type = "button";
        const starter = (slot.players || [])[0];
        append(button, [
          node("strong", null, slot.slot),
          node("span", null, starter ? starter.player_name : "Empty"),
          node("small", null, `${(slot.players || []).length} deep`),
        ]);
        button.addEventListener("click", () => {
          state.selectedDepthSlot = slot.slot;
          render();
        });
        slotGrid.append(button);
      });
      slotBody.append(slotGrid);
    });

    const editorPanel = panel(selected ? `${selected.slot} Slot` : "Slot Editor", "Move or replace");
    const editorBody = panelBody(editorPanel);
    if (!selected) {
      editorBody.append(node("div", "empty-state", "No depth chart slots are available."));
    } else {
      editorBody.append(commandBox("Show Depth Chart", commands.depthChartShow || ""));
      const rows = (selected.players || []).map((player) => [
        `#${player.depth_rank}`,
        smallPlayerCell(player.player_id, player.player_name, `${player.position} | Age ${player.age || "-"}`),
        player.role?.score ? `${oneDecimal(player.role.score)} ${roleLabel(player.role.key)}` : "-",
        depthMoveButtons(selected.slot, player),
        depthReplacementControl(selected.slot, player.depth_rank, depth.roster || [], player.player_id),
      ]);
      editorBody.append(table(["Rank", "Player", "Role Fit", "Move", "Replace With"], rows));

      const rosterPanel = node("div", "depth-roster-strip");
      const eligible = [...(depth.roster || [])]
        .sort((a, b) => {
          const fit = Number(playerFitsSlot(b, selected.slot)) - Number(playerFitsSlot(a, selected.slot));
          if (fit) return fit;
          return Number(b.role?.score || 0) - Number(a.role?.score || 0);
        })
        .slice(0, 18);
      eligible.forEach((player) => {
        const item = node("div", `depth-roster-chip ${playerFitsSlot(player, selected.slot) ? "fit" : ""}`.trim());
        append(item, [
          playerLink(player.player_id, player.player_name, "player-link strong-link"),
          node("span", null, `${player.position} | ${player.role?.score ? oneDecimal(player.role.score) : "-"}`),
        ]);
        rosterPanel.append(item);
      });
      editorBody.append(sectionBlock("Best Roster Fits", rosterPanel));
    }
    append(layout, [slotsPanel, editorPanel]);
    root.append(layout);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function depthMoveButtons(slot, player) {
    const wrap = node("span", "action-cell");
    ["up", "down"].forEach((direction) => {
      const button = node("button", "run-button compact", direction === "up" ? "Up" : "Down");
      button.type = "button";
      button.disabled = state.runnerBusy || (direction === "up" && Number(player.depth_rank) <= 1);
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        runAction("depth_chart_move", {
          position: slot,
          player_id: player.player_id,
          direction,
        });
      });
      wrap.append(button);
    });
    return wrap;
  }

  function depthReplacementControl(slot, rank, roster, currentPlayerId) {
    const wrap = node("span", "replace-control");
    const select = node("select", "depth-select");
    const sorted = [...roster].sort((a, b) => {
      const fit = Number(playerFitsSlot(b, slot)) - Number(playerFitsSlot(a, slot));
      if (fit) return fit;
      return Number(b.role?.score || 0) - Number(a.role?.score || 0);
    });
    sorted.forEach((player) => {
      const option = node("option", null, `${playerFitsSlot(player, slot) ? "*" : " "} ${player.player_name} (${player.position})`);
      option.value = String(player.player_id);
      option.selected = String(player.player_id) === String(currentPlayerId);
      select.append(option);
    });
    const set = node("button", "run-button compact", "Set");
    set.type = "button";
    set.disabled = state.runnerBusy;
    set.addEventListener("click", (event) => {
      event.stopPropagation();
      runAction("depth_chart_set", {
        position: slot,
        rank,
        player_id: Number(select.value),
      });
    });
    append(wrap, [select, set]);
    return wrap;
  }

  function renderDraft() {
    setHeader("Draft Room", "Pause the clock, inspect the board, make your pick, or skip CPU picks until your team is on the clock.");
    const root = document.createDocumentFragment();
    const draft = data.draft || { pickTotals: {}, board: [], pickQueue: [], events: [] };
    const stateRow = draft.state;
    const board = draft.board || [];
    const selected = selectedDraftProspect(board);
    const status = panel("Room Status", `Draft ${draft.year || ""}`);
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Room", stateRow ? stateRow.status : "Not Started", stateRow ? stateRow.clock_status : "Run start command"),
      metric("Current Pick", stateRow?.current_pick_number ? `#${stateRow.current_pick_number}` : "-", stateRow?.current_team || "No team on clock"),
      metric("Picks Used", `${draft.pickTotals.used || 0}/${draft.pickTotals.total || 0}`, `${draft.pickTotals.remaining || 0} remaining`),
      metric("Clock", stateRow ? `${stateRow.seconds_remaining || 0}s` : "-", "Stored state only"),
    ]);
    panelBody(status).append(metrics);
    root.append(status);

    const commands = data.commands || {};
    root.append(nextStepPanel(
      "Next Draft Step",
      stateRow ? (isUserOnClock() ? "Your Pick" : "Draft Clock") : (dateReached(draft.draftDate) ? "Ready To Start" : "Calendar"),
      draftNextCards(draft, commands, selected),
    ));
    const rookieClass = data.rookieClass || {};
    if ((rookieClass.selections || []).length && Number(rookieClass.year || 0) !== Number(draft.year || 0)) {
      root.append(draftUserSelectionsPanel(
        rookieClass.selections,
        {
          state: { user_team: data.activeSave?.user_team },
          pickTotals: { total: rookieClass.selections.length, used: rookieClass.selections.length, remaining: 0 },
        },
      ));
    }

    const grid = node("div", "grid");
    const commandsPanel = panel("Draft Commands", "Advanced / Manual");
    const commandBody = panelBody(commandsPanel);
    commandBody.append(commandBox("Advance To Draft", commands.advanceToDraft, "advance_to_draft"));
    commandBody.append(commandBox("Start Room", commands.draftStart, "draft_start"));
    commandBody.append(commandBox("Skip Next Pick", commands.draftSkipOne, "draft_skip", { count: 1 }));
    commandBody.append(commandBox("Skip To User Pick", commands.draftSkipToUser || commands.draftSkip, "draft_skip_to_user"));
    commandBody.append(commandBox("Finish Draft", commands.draftFinish, "draft_finish"));
    commandBody.append(commandBox("Advance To Next League Year", commands.advanceNextLeagueYear, "advance_next_league_year"));
    commandBody.append(commandBox("Make Pick", commands.draftPick));
    commandBody.append(commandBox("Validate Class", commands.draftValidate));
    if (stateRow?.current_team) {
      commandBody.append(actionCard(
        `Ask ${stateRow.current_team} GM`,
        "Run a local-LLM draft strategy advisory for the team currently on the clock.",
        commands.aiGmRunDraft?.replace(`--team ${data.activeSave?.user_team || "MIN"}`, `--team ${stateRow.current_team}`),
        "ai_gm_run",
        { team: stateRow.current_team, decision_type: "draft_strategy_update" },
        "",
      ));
    }

    const queuePanel = panel("Pick Queue", "Next 40");
    const queueList = node("div", "list");
    (draft.pickQueue || []).slice(0, 12).forEach((pick) => {
      queueList.append(draftQueueRow(pick));
    });
    panelBody(queuePanel).append(queueList.children.length ? queueList : node("div", "empty-state", "No draft room queue exported."));
    append(grid, [commandsPanel, queuePanel]);
    root.append(grid);
    root.append(draftUserSelectionsPanel(draft.userSelections || [], draft));

    const draftLayout = node("div", "draft-layout");
    const boardPanel = panel("Draft Board", `${board.length || 0} shown`);
    panelBody(boardPanel).append(draftBoardTable(board.slice(0, 100), selected));
    append(draftLayout, [boardPanel, prospectCard(selected)]);
    root.append(draftLayout);
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function draftQueueRow(pick) {
    const item = node("div", `row draft-queue-row ${pick.is_used ? "is-used" : ""}`.trim());
    const overallPick = pick.effective_pick_number || pick.pick_number;
    const pickInRound = pick.effective_pick_in_round || pick.pick_in_round;
    const pickText = overallPick ? `Pick ${overallPick}` : "Pick -";
    const slotText = `Round ${pick.round || "-"} · ${pickText}`;
    const left = node("div", "draft-queue-main");
    const teamLine = append(node("div", "draft-queue-team-line"), [
      teamLogo(pick.teamLogo, pick.current_team, "queue-team-logo"),
      append(node("span"), [
        node("strong", "draft-queue-pick-slot", slotText),
        node("small", null, `${pick.current_team || "-"} · ${pick.current_team_name || "Team on clock"}${pickInRound ? ` · R${pick.round || "-"}P${pickInRound}` : ""}`),
      ]),
    ]);
    const detail = node("div", "muted draft-queue-detail");
    if (pick.selected_player_name) {
      detail.append(draftSelectionNameLink(
        pick.selected_player_id,
        pick.selected_prospect_id,
        pick.selected_player_name,
        pick.selected_player_position,
      ));
    } else {
      detail.textContent = pick.is_used ? "Selection recorded" : "Upcoming pick";
    }
    append(left, [teamLine, detail]);
    append(item, [left, tag(pick.is_used ? "Used" : "On Deck", pick.is_used ? "good" : "")]);
    return item;
  }

  function draftSelectionNameLink(playerId, prospectId, name, position, preferPlayer) {
    const label = `${name || "Selected Player"}${position ? ` (${position})` : ""}`;
    if (preferPlayer && playerId) return playerLink(playerId, label, "player-link strong-link");
    if (prospectId) return prospectLink(prospectId, label, "prospect-link strong-link");
    if (playerId) return playerLink(playerId, label, "player-link strong-link");
    return node("span", "strong-link", label);
  }

  function draftUserSelectionsPanel(selections, draft) {
    const userTeam = data.activeSave?.user_team || draft.state?.user_team || "User";
    const complete = Number(draft.pickTotals?.remaining || 0) === 0 && Number(draft.pickTotals?.total || 0) > 0;
    const haulPanel = panel(`${userTeam} Draft Class`, complete ? "Final Haul" : "Selections So Far");
    const body = panelBody(haulPanel);
    if (!selections.length) {
      body.append(node("div", "empty-state", complete ? "No user-team selections were found." : "Your picks will appear here as they are made."));
      return haulPanel;
    }
    const grid = node("div", "draft-haul-grid");
    selections.forEach((pick) => {
      const card = node("article", "draft-haul-card");
      const top = append(node("div", "draft-haul-top"), [
        teamLogo(pick.teamLogo, pick.team, "queue-team-logo"),
        append(node("div", "draft-haul-pick"), [
          node("strong", null, `#${pick.pickNumber || "-"} | R${pick.round || "-"}`),
          node("small", null, pick.publicGrade ? `Public grade ${pick.publicGrade}` : "Public grade pending"),
        ]),
      ]);
      const identity = append(node("div", "draft-haul-player"), [
        draftSelectionNameLink(pick.playerId, pick.prospectId, pick.playerName || "Selected Player", pick.position, complete),
        node("small", null, `${pick.position || "-"} | ${pick.college || "-"} | ${heightText(pick.heightIn)} / ${weightText(pick.weightLbs)}`),
      ]);
      const details = detailGrid([
        ["Scout", `${valueOrDash(pick.scoutGrade)} / ${valueOrDash(pick.scoutCeiling)}`],
        ["Risk", valueOrDash(pick.scoutRisk)],
        ["Role", roleLabel(pick.primaryRole || pick.archetype)],
        ["Need", `${pick.needGroup || "-"} ${pick.needScore ? whole(pick.needScore) : "-"}`],
      ], "compact");
      const report = node("p", null, pick.scoutingSummary || pick.scoutingProjection || pick.publicGradeNote || "No scouting summary exported.");
      append(card, [top, identity, details, report]);
      grid.append(card);
    });
    body.append(grid);
    return haulPanel;
  }

  function draftSelectionTicker(selections) {
    const tickerPanel = panel("Selection Ticker", `${selections.length || 0} picks made`);
    const body = panelBody(tickerPanel);
    if (!selections.length) {
      body.append(node("div", "empty-state", "Selections will appear here once the draft starts."));
      return tickerPanel;
    }
    const strip = node("div", "selection-ticker");
    selections.forEach((selection) => {
      const card = node("article", "selection-card");
      const top = node("div", "selection-card-top");
      if (selection.teamLogo) {
        const img = node("img", "selection-team-logo");
        img.src = selection.teamLogo;
        img.alt = selection.team || "Team";
        top.append(img);
      } else {
        top.append(node("span", "selection-logo-fallback", selection.team || "-"));
      }
      append(top, [
        append(node("div", "selection-pick-meta"), [
          node("strong", null, `#${selection.pickNumber || "-"}`),
          node("small", null, `R${selection.round || "-"} | ${selection.team || "-"}`),
        ]),
        node("span", `public-grade ${publicGradeClass(selection.publicGradeScore)}`, selection.publicGrade || "-"),
      ]);

      const player = append(node("div", "selection-player"), [
        playerLink(selection.playerId, selection.playerName || "Selected Player", "player-link strong-link"),
        node("small", null, `${selection.position || "-"}${selection.college ? ` | ${selection.college}` : ""}`),
      ]);
      const context = append(node("div", "selection-context"), [
        node("span", null, `${selection.publicGradeNote || "Public grade pending"}`),
        node("small", null, `${selection.needGroup || "Need"} ${selection.needScore ? whole(selection.needScore) : "-"} | Board ${selection.publicBoardRank || "-"}`),
      ]);
      append(card, [top, player, context]);
      strip.append(card);
    });
    body.append(strip);
    return tickerPanel;
  }

  function publicGradeClass(score) {
    const amount = Number(score || 0);
    if (amount >= 84) return "elite";
    if (amount >= 76) return "good";
    if (amount >= 64) return "mid";
    return "low";
  }

  function selectedDraftProspect(board) {
    if (!board.length) return null;
    const selected = board.find((player) => String(player.prospect_id) === String(state.selectedDraftProspectId));
    if (selected) return selected;
    state.selectedDraftProspectId = board[0].prospect_id;
    return board[0];
  }

  function draftBoardTable(players, selected) {
    if (!players.length) return node("div", "empty-state", "No draft prospects exported.");
    const wrap = node("div", "table-wrap draft-table-wrap");
    const tableEl = node("table", "data-table draft-board-table");
    const thead = node("thead");
    const headerRow = node("tr");
    ["Rank", "Player", "Pos", "Ht/Wt", "Age", "Class", "School", "Proj", "40", "10", "Vert", "Broad", "Ath", "Grade", "Risk", "SB", "Pick"].forEach((header) => {
      headerRow.append(node("th", null, header));
    });
    thead.append(headerRow);
    const tbody = node("tbody");
    players.forEach((player) => {
      const tr = node("tr", String(player.prospect_id) === String(selected?.prospect_id) ? "selected-row" : "");
      tr.addEventListener("click", () => {
        state.selectedDraftProspectId = player.prospect_id;
        render();
      });
      [
        player.public_board_rank || player.scouting_rank || "-",
        prospectNameButton(player),
        player.position || "-",
        `${heightText(player.height_in)} / ${weightText(player.weight_lbs)}`,
        whole(player.age),
        player.college_class || "-",
        collegeCell(player),
        player.projected_round ? `R${player.projected_round}.${player.projected_pick || "-"}` : "-",
        decimalOrDash(player.forty_yard_dash, 2),
        decimalOrDash(player.ten_yard_split, 2),
        inchesText(player.vertical_jump_in),
        inchesToFeetText(player.broad_jump_in),
        whole(player.athletic_score),
        gradeCell(player),
        riskCell(player.scout_risk),
        seniorBowlTag(player),
        draftPickButton(player),
      ].forEach((value) => {
        const td = node("td");
        if (value instanceof Node) td.append(value);
        else td.textContent = value;
        tr.append(td);
      });
      tbody.append(tr);
    });
    append(tableEl, [thead, tbody]);
    wrap.append(tableEl);
    return wrap;
  }

  function prospectNameButton(player) {
    const wrap = node("span", "prospect-name-cell");
    const button = node("button", "prospect-link", player.player_name || `${player.first_name || ""} ${player.last_name || ""}`.trim());
    button.type = "button";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      state.selectedDraftProspectId = player.prospect_id;
      render();
    });
    append(wrap, [
      button,
      node("small", null, roleLabel(player.primary_role || player.archetype)),
    ]);
    return wrap;
  }

  function collegeCell(player) {
    const wrap = node("span", "stacked-cell");
    append(wrap, [
      node("strong", null, player.college || "-"),
      player.college_tier ? node("small", null, player.college_tier) : null,
    ]);
    return wrap;
  }

  function gradeCell(player) {
    const wrap = node("span", "stacked-cell grade-cell");
    append(wrap, [
      node("strong", null, valueOrDash(player.scout_grade)),
      player.scout_ceiling ? node("small", null, `C ${player.scout_ceiling}`) : null,
    ]);
    return wrap;
  }

  function riskCell(risk) {
    const tone = String(risk || "").toLowerCase();
    return tag(risk || "-", tone.includes("high") ? "bad" : tone.includes("medium") ? "warn" : "good");
  }

  function seniorBowlLabel(player) {
    if (!Number(player.senior_bowl_eligible || 0)) return "Not eligible";
    if (!seniorBowlStatusAvailable()) return "Eligible";
    if (Number(player.senior_bowl_accepted || 0)) return "Accepted";
    if (Number(player.senior_bowl_invited || 0)) return "Skipped";
    return "Eligible";
  }

  function seniorBowlStatusAvailable() {
    const senior = data.scouting?.seniorBowl || {};
    return Boolean(senior.processed) || dateReached(senior.eventDate);
  }

  function seniorBowlTag(player) {
    const label = seniorBowlLabel(player);
    const tone = label === "Accepted" ? "good" : label === "Skipped" ? "warn" : "";
    return tag(label, tone);
  }

  function prospectCard(player, options = {}) {
    const card = panel("Prospect Card", player ? `#${player.public_board_rank || player.scouting_rank || "-"}` : "Scouting");
    const body = panelBody(card);
    body.classList.add("prospect-card");
    if (!player) {
      body.append(node("div", "empty-state", "Select a prospect to inspect his card."));
      return card;
    }
    const identity = node("div", "prospect-identity");
    append(identity, [
      node("h3", null, player.player_name),
      node("div", "prospect-tags"),
    ]);
    const tags = identity.querySelector(".prospect-tags");
    append(tags, [
      tag(player.position || "-"),
      player.college_class ? tag(player.college_class) : null,
      seniorBowlTag(player),
      tag(player.archetype || "-"),
      riskCell(player.scout_risk),
    ]);
    body.append(identity);

    body.append(detailGrid([
      ["Grade", valueOrDash(player.scout_grade)],
      ["Ceiling", valueOrDash(player.scout_ceiling)],
      ["Confidence", valueOrDash(player.scout_confidence)],
      ["Lens", valueOrDash(player.scout_lens)],
      ["Age", whole(player.age)],
      ["Class", valueOrDash(player.college_class)],
      ["Senior Bowl", seniorBowlLabel(player)],
      ["School", `${player.college || "-"}${player.college_tier ? ` (${player.college_tier})` : ""}`],
      ["Height", heightText(player.height_in)],
      ["Weight", weightText(player.weight_lbs)],
      ["Arm", inchesText(player.arm_length_in)],
      ["Hand", inchesText(player.hand_size_in)],
      ["Primary", roleLabel(player.primary_role)],
      ["Secondary", roleLabel(player.secondary_role)],
    ]));

    body.append(sectionBlock("Combine", detailGrid([
      ["Status", valueOrDash(player.combine_status)],
      ["Athletic", valueOrDash(player.athletic_score)],
      ["40", decimalOrDash(player.forty_yard_dash, 2)],
      ["10", decimalOrDash(player.ten_yard_split, 2)],
      ["Bench", valueOrDash(player.bench_press_reps)],
      ["Vert", inchesText(player.vertical_jump_in)],
      ["Broad", inchesToFeetText(player.broad_jump_in)],
      ["3 Cone", decimalOrDash(player.three_cone_sec, 2)],
      ["Shuttle", decimalOrDash(player.twenty_yard_shuttle_sec, 2)],
      ["Medical", Number(player.combine_injured || 0) ? "Flag" : "Clear"],
    ], "compact")));

    body.append(sectionBlock(
      "Scouted Attributes",
      player.details_exported === false
        ? node("div", "empty-state", "Full attribute export is limited to the top board slice for speed. Use the scouting page or player profile exports for deeper checks.")
        : prospectAttributeRows(player.scout_attributes || []),
    ));

    const report = node("div", "scouting-copy");
    append(report, [
      node("p", null, player.scouting_report || player.scouting_summary || "No scouting report available."),
    ]);
    body.append(sectionBlock("Scouting Report", report));

    const notes = node("div", "scout-note-grid");
    notes.append(sectionBlock("Strengths", node("p", null, player.scouting_strengths || "-")));
    notes.append(sectionBlock("Concerns", node("p", null, player.scouting_concerns || "-")));
    notes.append(sectionBlock("Projection", node("p", null, player.scouting_projection || "-")));
    body.append(notes);

    const action = node("div", "prospect-card-actions");
    if (options.showDraftActions === false) {
      action.append(scoutingProspectActionButtons(player, { includeDraftRoomLink: true }));
    } else {
      action.append(draftPickButton(player));
    }
    body.append(action);
    return card;
  }

  function prospectAttributeRows(attributes) {
    const stack = node("div", "prospect-attribute-list");
    if (!attributes.length) {
      stack.append(node("div", "empty-state", "No scouted attributes exported."));
      return stack;
    }
    attributes.forEach((rating) => {
      const row = node("div", "prospect-attribute-row");
      const rangeLow = Number.isFinite(Number(rating.rangeLow)) ? Number(rating.rangeLow) : null;
      const rangeHigh = Number.isFinite(Number(rating.rangeHigh)) ? Number(rating.rangeHigh) : null;
      const rangeText = rangeLow !== null && rangeHigh !== null ? `Likely ${Math.round(rangeLow)}-${Math.round(rangeHigh)}` : "Range pending";
      const left = append(node("div", "attribute-label"), [
        node("strong", null, rating.label),
        node("small", null, `${rating.grade} | ${rangeText} | ${rating.confidence || "Medium"} confidence`),
      ]);
      const bar = node("div", "scout-gradient-bar");
      const score = Math.max(5, Math.min(98, Number(rating.displayValue || 0)));
      const rangeDisplayLow = Math.max(5, Math.min(98, Number(rating.rangeDisplayLow ?? score)));
      const rangeDisplayHigh = Math.max(rangeDisplayLow, Math.min(98, Number(rating.rangeDisplayHigh ?? score)));
      bar.style.setProperty("--score", `${score}%`);
      bar.style.setProperty("--range-low", `${rangeDisplayLow}%`);
      bar.style.setProperty("--range-width", `${Math.max(2, rangeDisplayHigh - rangeDisplayLow)}%`);
      const range = node("i", "scout-range-band");
      range.title = `${rating.label} likely range: ${rangeLow !== null ? Math.round(rangeLow) : "-"}-${rangeHigh !== null ? Math.round(rangeHigh) : "-"}`;
      const marker = node("span");
      marker.title = `${rating.label}: ${rating.grade}${rangeLow !== null ? `, likely ${Math.round(rangeLow)}-${Math.round(rangeHigh)}` : ""}`;
      bar.append(range, marker);
      append(row, [left, bar]);
      stack.append(row);
    });
    return stack;
  }

  function detailGrid(items, className) {
    const grid = node("div", `prospect-detail-grid ${className || ""}`.trim());
    items.forEach(([label, value]) => {
      const item = node("div", "prospect-detail");
      append(item, [node("span", null, label), node("strong", null, value)]);
      grid.append(item);
    });
    return grid;
  }

  function sectionBlock(title, content) {
    const block = node("section", "prospect-section");
    block.append(node("h4", null, title));
    block.append(content);
    return block;
  }

  function draftPickButton(player) {
    const wrap = node("span", "action-cell");
    const copy = node("button", "copy-button", "Copy");
    copy.type = "button";
    copy.textContent = "Copy";
    const command = (data.commands?.draftPick || "").replace("<id>", player.prospect_id);
    copy.addEventListener("click", (event) => {
      event.stopPropagation();
      navigator.clipboard?.writeText(command);
    });
    wrap.append(copy);
    if (runnerMode()) {
      const userOnClock = isUserOnClock();
      const run = node("button", "run-button", state.runnerBusy ? "Running" : userOnClock ? "Pick" : "Wait");
      run.type = "button";
      run.disabled = state.runnerBusy || !userOnClock;
      run.title = userOnClock
        ? "Make this selection for your team."
        : "Your team is not on the clock. Use Skip Next Pick.";
      run.addEventListener("click", (event) => {
        event.stopPropagation();
        runAction("draft_pick", { prospect_id: player.prospect_id });
      });
      wrap.append(run);
    }
    return wrap;
  }

  function isUserOnClock() {
    const draftState = data.draft?.state;
    if (!draftState) return false;
    return String(draftState.current_team || "").toUpperCase() === String(draftState.user_team || data.activeSave?.user_team || "").toUpperCase();
  }

  function renderInbox() {
    setHeader("Inbox", "Messages from scouting, staff, league events, and future front-office systems.");
    const root = document.createDocumentFragment();
    root.append(renderInboxPanel({ limit: 40, title: "Inbox", kicker: "All Messages" }));
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderInboxPanel(options = {}) {
    const scouting = data.scouting || {};
    const messages = scouting.inbox || [];
    const unread = Number(scouting.counts?.unread || 0);
    const limit = Number(options.limit || 12);
    const p = panel(options.title || "Inbox", options.kicker || (unread ? `${unread} Unread` : "Caught Up"));
    const body = panelBody(p);
    const actions = node("div", "command-actions compact-actions");
    const markRead = node("button", "copy-button", "Mark All Read");
    markRead.type = "button";
    markRead.disabled = state.runnerBusy || !runnerMode() || !messages.length;
    markRead.addEventListener("click", () => runAction("inbox_mark_read", {}));
    actions.append(markRead);
    body.append(actions);

    const list = node("div", "inbox-list");
    if (!messages.length) {
      list.append(node("div", "empty-state", "No messages yet. Scouts, league events, and staff notes will land here."));
    } else {
      messages.slice(0, limit).forEach((message) => {
        const card = node("article", `message-card ${Number(message.is_read || 0) ? "" : "unread"}`.trim());
        append(card, [
          append(node("div", "message-top"), [
            node("strong", null, message.title || "Inbox Message"),
            node("span", "event-date", shortDate(message.message_date)),
          ]),
          node("p", null, message.body || ""),
          append(node("div", "message-meta"), [
            node("span", null, message.category || "Inbox"),
            node("span", null, message.source || "Front Office"),
          ]),
        ]);
        list.append(card);
      });
    }
    body.append(list);
    return p;
  }

  function renderLeagueNews() {
    setHeader("League News", "Public league-wide stories: prospect buzz, injuries, suspensions, holdouts, trades, roster moves, rumors, and market noise.");
    const root = document.createDocumentFragment();
    const news = data.leagueNews || { items: [], categories: [], counts: {} };
    const items = news.items || [];
    const filtered = state.newsFilter === "all"
      ? items
      : items.filter((item) => String(item.category || "League") === state.newsFilter);

    const summary = panel("League Wire", news.updatedAt ? `Updated ${shortDate(news.updatedAt)}` : "Public Feed");
    const body = panelBody(summary);
    const metrics = node("section", "metric-grid news-metrics");
    append(metrics, [
      metric("Stories", String(news.counts?.total || items.length), "Current feed"),
      metric("Major", String(news.counts?.major || 0), "High visibility"),
      metric("Rumors", String(news.counts?.rumors || 0), "Unconfirmed buzz"),
      metric("Prospects", String(news.counts?.prospects || 0), "Draft cycle"),
    ]);
    body.append(metrics);

    const controls = node("div", "news-control-row");
    const filters = node("div", "news-filter-row");
    filters.append(newsFilterButton("all", "All"));
    (news.categories || []).forEach((category) => {
      filters.append(newsFilterButton(category, category));
    });
    const refresh = node("button", "copy-button", state.runnerBusy ? "Running" : "Seed Current Stories");
    refresh.type = "button";
    refresh.disabled = state.runnerBusy || !runnerMode();
    refresh.addEventListener("click", () => runAction("league_news_seed", {}));
    const rollWeek = node("button", "copy-button", state.runnerBusy ? "Running" : "Roll Weekly Events");
    rollWeek.type = "button";
    rollWeek.disabled = state.runnerBusy || !runnerMode();
    rollWeek.addEventListener("click", () => runAction("event_generate_week", {
      season: data.currentSeason || data.season?.season || 2026,
      week: data.season?.nextWeek || 1,
    }));
    append(controls, [filters, refresh, rollWeek]);
    body.append(controls);
    root.append(summary);

    const topStories = filtered.filter((item) => Number(item.is_major || 0)).slice(0, 6);
    if (topStories.length) {
      const topPanel = panel("Top Stories", `${topStories.length} major`);
      const topGrid = node("div", "news-top-grid");
      topStories.forEach((item) => topGrid.append(newsCard(item, true)));
      panelBody(topPanel).append(topGrid);
      root.append(topPanel);
    }

    const feedPanel = panel(state.newsFilter === "all" ? "Full Feed" : state.newsFilter, `${filtered.length} shown`);
    const feed = node("div", "news-feed");
    filtered.forEach((item) => feed.append(newsCard(item, false)));
    panelBody(feedPanel).append(feed.children.length ? feed : node("div", "empty-state", "No league news items match this filter yet."));
    root.append(feedPanel);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function newsFilterButton(filter, label) {
    const button = node("button", `news-filter ${state.newsFilter === filter ? "active" : ""}`.trim(), label);
    button.type = "button";
    button.addEventListener("click", () => {
      state.newsFilter = filter;
      render();
    });
    return button;
  }

  function newsCard(item, compact) {
    const major = Number(item.is_major || 0) ? "major" : "";
    const rumor = (item.tags || []).map((tagValue) => String(tagValue).toLowerCase()).includes("rumor") ? "rumor" : "";
    const card = node("article", `news-card ${major} ${rumor} ${compact ? "compact" : ""}`.trim());
    const top = append(node("div", "news-card-top"), [
      append(node("div", "news-title-stack"), [
        node("span", `news-category ${newsCategoryClass(item.category)}`, item.category || "League"),
        newsTitleNode(item),
      ]),
      node("span", "event-date", shortDate(item.news_date)),
    ]);
    const body = node("p", null, item.body || "");
    const subject = newsSubjectNode(item);
    const meta = append(node("div", "news-meta"), [
      node("span", null, item.source || "League Wire"),
      subject,
      Number(item.synthetic || 0) ? node("span", null, "Live digest") : null,
    ]);
    const tags = node("div", "news-tags");
    (item.tags || []).slice(0, 5).forEach((tagValue) => tags.append(tag(String(tagValue))));
    append(card, [top, body, meta, tags.children.length ? tags : null]);
    return card;
  }

  function newsTitleNode(item) {
    const title = item.title || "League News";
    if (item.player_id && item.player_name) return playerLink(item.player_id, title, "news-title-link");
    if (item.prospect_id && item.prospect_name) return prospectLink(item.prospect_id, title, "news-title-link");
    return node("strong", null, title);
  }

  function newsSubjectNode(item) {
    if (item.player_id && item.player_name) return playerLink(item.player_id, item.player_name, "news-subject-link");
    if (item.prospect_name) {
      const label = `${item.prospect_name}${item.prospect_position ? ` (${item.prospect_position})` : ""}${item.prospect_college ? `, ${item.prospect_college}` : ""}`;
      return prospectLink(item.prospect_id, label, "news-subject-link");
    }
    if (item.team) return node("span", null, item.team_name || item.team);
    return null;
  }

  function newsCategoryClass(category) {
    const value = String(category || "").toLowerCase();
    if (value.includes("prospect") || value.includes("draft")) return "draft";
    if (value.includes("transaction") || value.includes("roster")) return "move";
    if (value.includes("injur") || value.includes("discipline") || value.includes("suspension")) return "risk";
    if (value.includes("rumor")) return "rumor";
    return "";
  }

  function renderScouting() {
    setHeader("Scouting", "Manage weekly scouting, off-board discoveries, Senior Bowl exposure, and Top 30 visits.");
    const root = document.createDocumentFragment();
    root.append(renderScoutingDesk({ limit: 80 }));
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderScoutingDesk(options = {}) {
    const limit = Number(options.limit || 40);
    const scouting = data.scouting || {};
    const p = panel("Scouting Desk", scouting.draftYear ? `${scouting.draftYear} Draft` : "Draft Class");
    p.classList.add("scouting-desk-panel");
    const body = panelBody(p);
    const metrics = node("section", "metric-grid scouting-metrics");
    append(metrics, [
      metric("Period", scouting.period?.label || "-", scouting.weeklyWindow?.open ? "Weekly scouting open" : "Weekly scouting closed"),
      metric("Weekly Action", actionCountText(scouting), scouting.weeklyWindow?.open ? (scouting.usedAction ? roleLabel(scouting.usedAction) : "Available") : scouting.weeklyWindow?.reason || "Closed"),
      metric("Top 30", `${scouting.top30?.used || 0}/30`, scouting.top30?.locked ? "Closed" : "Visits used"),
      metric("Senior Bowl", `${scouting.seniorBowl?.accepted || 0}`, scouting.seniorBowl?.processed ? "Processed" : "Accepted"),
      metric("Hidden", String(scouting.counts?.hiddenRemaining || 0), "Still undiscovered"),
    ]);
    if (!scouting.available) {
      body.append(metrics);
      const empty = node("div", "empty-state", scouting.needsSetup
        ? "Scouting needs to be initialized for this save before the board can track weekly assignments."
        : "No generated draft class is available for scouting yet.");
      body.append(empty);
      if (scouting.needsSetup) {
        const init = node("button", "primary-run-button", "Initialize Scouting");
        init.type = "button";
        init.disabled = state.runnerBusy || !runnerMode();
        init.addEventListener("click", () => runAction("scouting_setup", { draft_year: scouting.draftYear }));
        body.append(append(node("div", "command-actions"), [init]));
      }
      return p;
    }

    const used = scouting.actionsUsed || {};
    const choiceUsed = Boolean(scouting.weeklyChoiceUsed);
    const weeklyOpen = Boolean(scouting.weeklyWindow?.open);
    const closedReason = scouting.weeklyWindow?.reason || "Weekly scouting is currently closed.";
    const autoCount = Number(scouting.weeklyWindow?.autoAssignCount || 3);
    const randomCount = Number(scouting.weeklyWindow?.randomCount || 3);
    const discoverCount = Number(scouting.weeklyWindow?.discoverCount || 4);
    body.append(scoutingWindowBanner(scouting, { weeklyOpen, closedReason, choiceUsed, autoCount }));
    const controls = node("div", "scouting-choice-grid");
    append(controls, [
      scoutingActionButton(`Auto Assign ${autoCount}`, "scouting_auto", used.auto_assign, weeklyOpen ? `Staff advances ${autoCount} priority prospects one confidence tier.` : closedReason, !weeklyOpen || (choiceUsed && !used.auto_assign)),
      scoutingActionButton(`Scout ${randomCount} Random`, "scouting_random_two", used.random_two, weeklyOpen ? `${randomCount} fresh cross-checks from the visible board.` : closedReason, !weeklyOpen || (choiceUsed && !used.random_two)),
      scoutingActionButton(
        `Discover ${discoverCount} Non-Public`,
        "scouting_discover_four",
        used.discover_four,
        !weeklyOpen ? closedReason : Number(scouting.counts?.hiddenRemaining || 0) <= 0 ? "No hidden prospects remain." : `Reveal ${discoverCount} off-board prospects at low confidence.`,
        !weeklyOpen || (choiceUsed && !used.discover_four) || Number(scouting.counts?.hiddenRemaining || 0) <= 0,
      ),
      append(node("div", "scouting-specific-card"), [
        node("strong", null, "Scout Specific Player"),
        node("small", null, !weeklyOpen ? closedReason : used.specific ? "Used this week." : choiceUsed ? "Weekly scouting choice already used." : "Use Scout Player on a prospect row."),
      ]),
    ]);

    const eventGrid = node("div", "grid scouting-event-grid");
    append(eventGrid, [renderSeniorBowlPanel(scouting), renderTop30Visits(scouting)]);
    const controlDeck = node("div", "scouting-control-deck");
    append(controlDeck, [metrics, controls, eventGrid]);
    body.append(controlDeck);
    body.append(renderScoutingAudit(scouting.audit));

    const visibleBoard = (scouting.board || []).slice(0, limit);
    const selected = selectedDraftProspect(visibleBoard);
    const layout = node("div", "scouting-layout");
    const boardPanel = panel("Visible Board", `${visibleBoard.length} shown`);
    panelBody(boardPanel).append(scoutingBoardTable(visibleBoard, selected));
    append(layout, [boardPanel, prospectCard(selected, { showDraftActions: false })]);
    body.append(layout);
    return p;
  }

  function scoutingWindowBanner(scouting, options) {
    const { weeklyOpen, closedReason, choiceUsed, autoCount } = options;
    const wrap = node("div", `scouting-window-banner ${weeklyOpen ? "open" : "locked"}`);
    const title = weeklyOpen
      ? choiceUsed
        ? "Weekly scouting used"
        : "Weekly scouting open"
      : "Weekly scouting locked";
    const detail = weeklyOpen
      ? choiceUsed
        ? "Advance to the next regular-season week to unlock another scouting choice."
        : `Choose one action this week. If you skip it, your staff will auto-assign ${autoCount} priority reports when the week processes.`
      : closedReason;
    append(wrap, [
      append(node("div"), [
        node("strong", null, title),
        node("span", null, detail),
      ]),
      node("small", null, scouting.weeklyWindow?.ruleSummary || "Weekly scouting is calendar-gated."),
    ]);
    return wrap;
  }

  function renderSeniorBowlPanel(scouting) {
    const senior = scouting.seniorBowl || {};
    const wrap = node("div", "top30-panel senior-bowl-panel");
    append(wrap, [
      append(node("div", "top30-header"), [
        append(node("div"), [
          node("strong", null, "Senior Bowl"),
          node("small", null, senior.locked && !senior.processed
            ? senior.lockedReason || "Senior Bowl processing is not open yet."
            : senior.processed
            ? `${senior.userReports?.length || 0} recent user-team report(s) shown below.`
            : `${senior.eligible || 0} eligible, ${senior.invited || 0} invited, ${senior.accepted || 0} accepted.`),
        ]),
        node("span", "top30-count", senior.eventDate ? shortDate(senior.eventDate) : "-"),
      ]),
    ]);
    const actions = node("div", "command-actions compact-actions");
    const setupButton = node("button", "copy-button mini-button", "Refresh Labels");
    setupButton.type = "button";
    setupButton.disabled = state.runnerBusy || !runnerMode();
    setupButton.addEventListener("click", () => runAction("scouting_senior_bowl_setup", {}));
    const processButton = node("button", "run-button mini-button", senior.processed ? "Processed" : "Process Event");
    processButton.type = "button";
    processButton.disabled = state.runnerBusy || !runnerMode() || senior.processed || Boolean(senior.locked) || Number(senior.accepted || 0) <= 0;
    processButton.addEventListener("click", () => runAction("scouting_senior_bowl_process", {}));
    append(actions, [setupButton, processButton]);
    wrap.append(actions);

    const reports = senior.userReports || [];
    if (!reports.length) {
      wrap.append(node("div", "empty-state compact-empty", senior.processed ? "No useful Senior Bowl notes for your staff this time." : "Process the event when the Senior Bowl arrives to generate team-specific notes."));
      return wrap;
    }
    const list = node("div", "top30-list");
    reports.slice(0, 6).forEach((report) => {
      const card = node("article", `top30-card ${report.result_type || ""}`.trim());
      append(card, [
        append(node("div", "message-top"), [
          node("strong", null, report.player_name || "Prospect"),
          node("span", "event-date", report.result_type === "trait" ? "Trait" : "Confidence"),
        ]),
        node("small", null, `${report.position || "-"} | ${report.college || "-"} | ${shortDate(report.event_date)}`),
        node("p", null, report.notes || "Senior Bowl report logged."),
      ]);
      list.append(card);
    });
    wrap.append(list);
    return wrap;
  }

  function renderTop30Visits(scouting) {
    const top30 = scouting.top30 || {};
    const wrap = node("div", "top30-panel");
    append(wrap, [
      append(node("div", "top30-header"), [
        append(node("div"), [
          node("strong", null, "Top 30 Visits"),
          node("small", null, top30.locked
            ? top30.lockedReason || "Visits are closed."
            : `${top30.remaining ?? 30} visits remaining for ${top30.team || "your team"}. Unused visits auto-fill when you advance past the facility-visit deadline.`),
        ]),
        node("span", "top30-count", `${top30.used || 0}/${top30.limit || 30}`),
      ]),
    ]);
    const visits = top30.visits || [];
    if (!visits.length) {
      wrap.append(node("div", "empty-state compact-empty", top30.locked ? "Top 30 visits are closed for this class." : "No Top 30 visits logged yet."));
      return wrap;
    }
    const list = node("div", "top30-list");
    visits.slice(0, 6).forEach((visit) => {
      const card = node("article", `top30-card ${visit.result_type || ""}`.trim());
      append(card, [
        append(node("div", "message-top"), [
          node("strong", null, visit.player_name || "Prospect"),
          node("span", "event-date", top30OutcomeLabel(visit.result_type)),
        ]),
        node("small", null, `${visit.position || "-"} | ${visit.college || "-"} | ${shortDate(visit.visit_date)}`),
        node("p", null, visit.notes || "Visit completed."),
      ]);
      list.append(card);
    });
    wrap.append(list);
    return wrap;
  }

  function top30OutcomeLabel(result) {
    return {
      full: "Full Reveal",
      personality: "Traits",
      inconclusive: "Inconclusive",
    }[result] || "Visit";
  }

  function renderScoutingAudit(audit = {}) {
    if (!audit.available) {
      const empty = node("section", "scouting-audit compact-empty", audit.reason || "Scouting audit will appear after the draft class initializes.");
      return empty;
    }
    const counts = audit.counts || {};
    const wrap = node("section", "scouting-audit");
    const head = node("div", "scouting-audit-head");
    append(head, [
      append(node("div"), [
        node("strong", null, "Scouting Audit"),
        node("small", null, "Debug view for discovery spread, confidence, and scout-vs-true gaps."),
      ]),
      node("span", "top30-count", `${counts.hiddenRemaining ?? 0} hidden left`),
    ]);
    const metrics = node("section", "metric-grid scouting-audit-metrics");
    append(metrics, [
      metric("Off-Board", String(counts.offBoardProspects || 0), `${counts.userHiddenFound || 0} user found`),
      metric("CPU Finds", String(counts.cpuHiddenUniqueFound || 0), `${counts.cpuHiddenDiscoveryEvents || 0} team discoveries`),
      metric("Max Overlap", String(counts.maxTeamsOnOneHidden || 0), "teams on one hidden"),
      metric("Global Buzz", String(counts.globallyDiscoveredHidden || 0), "user-visible discoveries"),
    ]);

    const tables = node("div", "scouting-audit-grid");
    const teamRows = (audit.teamHiddenFinds || []).slice(0, 8).map((row) => [
      row.team || "-",
      whole(row.hidden_found),
      `${whole(row.very_high)}/${whole(row.high)}/${whole(row.medium)}/${whole(row.low)}`,
    ]);
    const duplicateRows = (audit.mostDiscoveredHidden || []).slice(0, 6).map((row) => [
      whole(row.totalFound),
      row.user_found ? "User" : "-",
      node("span", "audit-player-name", `${row.player_name || "Prospect"} (${row.position || "-"})`),
      `${row.scout_grade ?? "-"}/${row.scout_ceiling ?? "-"}`,
      whole(row.scouting_variance),
    ]);
    const gapRows = (audit.largestGradeGaps || []).slice(0, 6).map((row) => [
      row.public_board_rank || "DISC",
      prospectLink(row.prospect_id, `${row.player_name || "Prospect"} (${row.position || "-"})`, "prospect-link audit-link"),
      `${row.scout_grade ?? "-"}/${row.scout_ceiling ?? "-"}`,
      `${row.true_grade ?? "-"}/${row.ceiling_grade ?? "-"}`,
      `${row.grade_gap ?? "-"}/${row.ceiling_gap ?? "-"}`,
    ]);
    append(tables, [
      append(node("div", "audit-table-block"), [
        node("strong", null, "CPU Hidden Finds"),
        table(["Team", "Found", "VH/H/M/L"], teamRows),
      ]),
      append(node("div", "audit-table-block"), [
        node("strong", null, "Most Duplicated Hidden"),
        table(["Finds", "User", "Prospect", "Scout", "Var"], duplicateRows),
      ]),
      append(node("div", "audit-table-block"), [
        node("strong", null, "Largest Grade Gaps"),
        table(["Rank", "Prospect", "Scout", "True", "Gap"], gapRows),
      ]),
    ]);
    append(wrap, [head, metrics, tables]);
    return wrap;
  }

  function actionCountText(scouting) {
    return scouting.weeklyChoiceUsed ? "1/1" : "0/1";
  }

  function scoutingActionButton(label, action, used, hint, disabled) {
    const button = node("button", `scouting-choice ${used ? "used" : ""}`.trim());
    button.type = "button";
    button.disabled = state.runnerBusy || !runnerMode() || Boolean(used) || Boolean(disabled);
    append(button, [
      node("strong", null, used ? `${label} Used` : label),
      node("small", null, hint),
    ]);
    button.addEventListener("click", () => runAction(action, {}));
    return button;
  }

  function scoutingBoardTable(prospects, selected) {
    if (!prospects.length) return node("div", "empty-state", "No visible prospects yet.");
    const showSeniorBowl = seniorBowlStatusAvailable();
    const wrap = node("div", "table-wrap scouting-table-wrap");
    const tableEl = node("table", "data-table scouting-board-table");
    const thead = node("thead");
    const headerRow = node("tr");
    const headers = ["Rank", "Player", "Pos", "Ht/Wt", "Age", "Class", "School", "Proj", "Role", "Grade", "Scouted", "40", "Vert", "Ath", "Risk"];
    if (showSeniorBowl) headers.push("SB");
    headers.push("Latest", "Actions");
    headers.forEach((header) => {
      headerRow.append(node("th", null, header));
    });
    thead.append(headerRow);
    const tbody = node("tbody");
    prospects.forEach((prospect) => {
      const tr = node("tr", String(prospect.prospect_id) === String(selected?.prospect_id) ? "selected-row" : "");
      tr.addEventListener("click", () => {
        state.selectedDraftProspectId = prospect.prospect_id;
        render();
      });
      const cells = [
        prospect.public_board_rank || "DISC",
        scoutingProspectNameButton(prospect),
        prospect.position || "-",
        `${heightText(prospect.height_in)} / ${weightText(prospect.weight_lbs)}`,
        whole(prospect.age),
        prospect.college_class || "-",
        collegeCell(prospect),
        prospect.projected_round ? `R${prospect.projected_round}.${prospect.projected_pick || "-"}` : "-",
        roleLabel(prospect.primary_role || prospect.archetype),
        gradeCell(prospect),
        scoutingConfidenceCell(prospect),
        decimalOrDash(prospect.forty_yard_dash, 2),
        inchesText(prospect.vertical_jump_in),
        whole(prospect.athletic_score),
        riskCell(prospect.scout_risk),
      ];
      if (showSeniorBowl) cells.push(seniorBowlTag(prospect));
      cells.push(
        scoutingLatestCell(prospect),
        scoutingProspectActionButtons(prospect),
      );
      cells.forEach((value) => {
        const td = node("td");
        if (value instanceof Node) td.append(value);
        else td.textContent = value;
        tr.append(td);
      });
      tbody.append(tr);
    });
    append(tableEl, [thead, tbody]);
    wrap.append(tableEl);
    return wrap;
  }

  function scoutingProspectNameButton(prospect) {
    const wrap = node("span", "prospect-name-cell");
    const button = node("button", "prospect-link", prospect.player_name || `${prospect.first_name || ""} ${prospect.last_name || ""}`.trim() || "Prospect");
    button.type = "button";
    button.title = "Open prospect card";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      state.selectedDraftProspectId = prospect.prospect_id;
      render();
    });
    append(wrap, [
      button,
      node("small", null, prospect.archetype || roleLabel(prospect.primary_role)),
    ]);
    return wrap;
  }

  function scoutingConfidenceCell(prospect) {
    const wrap = node("span", "stacked-cell");
    append(wrap, [
      node("strong", null, prospect.scouting_confidence || "Low"),
      node("small", null, `${Number(prospect.scouting_level || 0)}%${prospect.times_scouted ? ` | ${prospect.times_scouted}x` : ""}`),
    ]);
    return wrap;
  }

  function scoutingLatestCell(prospect) {
    const text = prospect.top30_notes || prospect.last_report || prospectBuzzLine(prospect);
    const wrap = node("span", "scouting-latest-cell", text);
    wrap.title = text;
    return wrap;
  }

  function prospectBuzzLine(prospect) {
    const name = prospect.player_name || "This prospect";
    const role = roleLabel(prospect.primary_role || prospect.archetype || prospect.position);
    const trait = prospectTraitLabel(prospect);
    const seed = stableNumber(`${prospect.prospect_id || ""}:${name}:${prospect.position || ""}`);
    const grade = Number(prospect.scout_grade || 0);
    const ceiling = Number(prospect.scout_ceiling || 0);
    const variance = Number(prospect.scouting_variance || 0);
    const athletic = Number(prospect.athletic_score || 0);
    const risk = String(prospect.scout_risk || "").toLowerCase();
    const status = String(prospect.combine_status || "");
    const isHidden = String(prospect.public_board_status || "") === "off_public_board"
      || String(prospect.visibility_status || "") === "discovered";

    if (isHidden) {
      return choose([
        `Area scout pushed ${name} onto the board after a late tape pass; ${role} projection is still wide.`,
        `${name} was not in the public-board stack, but the staff flagged ${trait} as worth a follow-up.`,
        `Discovery note: ${name} has enough ${role} traits to keep alive, but the grade needs another source.`,
        `Small-circle buzz on ${name}: one scout sees a late-round role, another wants more evidence.`,
      ], seed);
    }
    if (String(prospect.college_tier || "").toLowerCase().includes("international")) {
      return choose([
        `${name}'s international path has scouts leaning harder on workouts and role projection than the board rank.`,
        `Cross-checkers like the tools, but ${name}'s translation to an NFL ${role} role needs live exposure.`,
        `${name} is generating quiet curiosity because the athletic profile is easier to like than the competition jump.`,
      ], seed);
    }
    if (variance >= 75 || ceiling - grade >= 15) {
      return choose([
        `Scouts are split on ${name}: ${trait} pops, but the week-to-week grade still swings.`,
        `${name}'s ceiling keeps him in the conversation, though the floor grade is still being argued.`,
        `Board room note: ${name} has real supporters and real skeptics, mostly tied to ${role} translation.`,
        `${name} is one of the more volatile files in this range; the next exposure could move him either way.`,
      ], seed);
    }
    if (status.toLowerCase().includes("injured") || Number(prospect.combine_injured || 0)) {
      return choose([
        `${name}'s medical file is driving the next check; the staff wants cleaner availability before moving him up.`,
        `Latest note on ${name}: injury context matters more than the raw workout line right now.`,
        `${name} still has fans in the room, but medical confidence is the swing factor.`,
      ], seed);
    }
    if (status.toLowerCase().includes("not invited")) {
      return choose([
        `${name} missed the main workout circuit, so the staff is leaning on tape and any pro-day signal.`,
        `No combine invite for ${name}; scouts want one more verified athletic data point before trusting the grade.`,
        `${name}'s file is mostly tape-driven right now, with workout confirmation still pending.`,
      ], seed);
    }
    if (athletic >= 82) {
      return choose([
        `${name}'s workout numbers are starting to support the ${role} projection.`,
        `Athletic testing helped ${name}; the question is whether ${trait} shows up consistently on tape.`,
        `${name} has measurable juice, and scouts are checking whether the game speed matches the stopwatch.`,
        `Workout buzz is positive on ${name}, especially for teams that value ${trait}.`,
      ], seed);
    }
    if (risk.includes("high")) {
      return choose([
        `${name} remains on the board, but the risk tag is forcing extra cross-checks.`,
        `The staff likes parts of ${name}'s tape, but the downside case is still too loud to ignore.`,
        `${name}'s grade is less about ability and more about how much volatility the room can stomach.`,
      ], seed);
    }
    if (String(prospect.college_class || "").toLowerCase().includes("junior")) {
      return choose([
        `${name}'s early-entry profile has projection appeal, but scouts want to see how mature the role is.`,
        `Junior tape on ${name} shows enough upside; the staff is testing whether the instincts are ready.`,
        `${name} has a younger-player curve, so the next read is more about growth path than finished grade.`,
      ], seed);
    }
    if (String(prospect.college_class || "").toLowerCase().includes("graduated")) {
      return choose([
        `${name} is viewed as more finished than flashy; scouts are checking if the ceiling is already mostly baked in.`,
        `Older-prospect note: ${name}'s polish helps, but teams may cap the upside unless ${trait} carries.`,
        `${name}'s experience is a plus, though the room is debating how much development runway remains.`,
      ], seed);
    }
    if (grade >= 72) {
      return choose([
        `${name} has a clean starter conversation if the ${role} fit holds.`,
        `The staff sees a dependable profile on ${name}, with ${trait} driving the current grade.`,
        `${name}'s report is stabilizing near the top of this tier; fewer scouts are fighting the projection.`,
      ], seed);
    }
    return choose([
      `${name}'s file is steady but unspectacular; scouts want one trait to separate from the tier.`,
      `Current buzz on ${name}: role fit matters more than raw board slot.`,
      `${name} is holding his range for now, with ${trait} as the main reason to keep watching.`,
      `The next check on ${name} is about narrowing the role, not chasing a big board jump.`,
    ], seed);
  }

  function prospectTraitLabel(prospect) {
    const strength = (prospect.scout_strengths || prospect.scout_attributes || [])[0];
    if (strength?.label) return String(strength.label).toLowerCase();
    const summary = String(prospect.scouting_strengths || "").split(";")[0].trim();
    if (summary) return summary.toLowerCase();
    return roleLabel(prospect.primary_role || prospect.archetype || prospect.position).toLowerCase();
  }

  function stableNumber(value) {
    let hash = 0;
    String(value).split("").forEach((char) => {
      hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    });
    return hash;
  }

  function choose(options, seed) {
    if (!options.length) return "";
    return options[seed % options.length];
  }

  function scoutingProspectActionButtons(prospect, options = {}) {
    const used = Boolean(data.scouting?.actionsUsed?.specific);
    const weeklyChoiceUsed = Boolean(data.scouting?.weeklyChoiceUsed);
    const weeklyOpen = Boolean(data.scouting?.weeklyWindow?.open);
    const top30 = data.scouting?.top30 || {};
    const actions = node("div", "prospect-actions compact-prospect-actions");
    const scoutButton = node("button", "copy-button mini-button", used ? "Used" : "Scout");
    scoutButton.type = "button";
    scoutButton.disabled = state.runnerBusy || !runnerMode() || !weeklyOpen || weeklyChoiceUsed || prospect.scouting_confidence === "Very High";
    scoutButton.title = !weeklyOpen
      ? data.scouting?.weeklyWindow?.reason || "Weekly scouting is locked right now."
      : weeklyChoiceUsed
      ? "This week's scouting choice has already been used."
      : prospect.scouting_confidence === "Very High"
      ? "This player is already at very high confidence."
      : "Scout this player.";
    scoutButton.addEventListener("click", (event) => {
      event.stopPropagation();
      runAction("scouting_one", { prospect_id: prospect.prospect_id });
    });
    const visitButton = node("button", "copy-button mini-button", prospect.top30_visit_id ? "Visited" : "Top 30");
    visitButton.type = "button";
    visitButton.disabled = state.runnerBusy || !runnerMode() || Boolean(prospect.top30_visit_id) || Boolean(prospect.selected_pick_id) || Boolean(top30.locked) || Number(top30.remaining || 0) <= 0;
    visitButton.addEventListener("click", (event) => {
      event.stopPropagation();
      runAction("scouting_top30_visit", { prospect_id: prospect.prospect_id });
    });
    append(actions, [scoutButton, visitButton]);
    if (options.includeDraftRoomLink) {
      const draftLink = node("button", "copy-button mini-button", "Draft Room");
      draftLink.type = "button";
      draftLink.addEventListener("click", (event) => {
        event.stopPropagation();
        state.selectedDraftProspectId = prospect.prospect_id;
        state.view = "draft";
        render();
      });
      actions.append(draftLink);
    }
    return actions;
  }

  function renderAiGm() {
    const team = data.activeSave?.user_team || "MIN";
    setHeader("AI GMs", "Local LLM advisory layer for GM personality, draft strategy, free agency, trades, and depth-chart logic.");
    const root = document.createDocumentFragment();
    const ai = data.aiGm || { counts: {}, logs: [] };
    const config = ai.config || {};
    const profile = ai.profile || {};
    const commands = data.commands || {};

    const summary = panel("Local LLM Status", ai.gameId || data.registry?.activeGameId || "Active Save");
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Profiles", String(ai.counts?.profiles || 0), "Team GM profiles"),
      metric("Enabled", config.enabled ? "Yes" : "No", config.provider || "No config", config.enabled ? "good" : "warn"),
      metric("Model", config.model || "llama3.1:8b", config.endpoint || "Ollama default"),
      metric("Recent Logs", String(ai.counts?.logs || 0), "Advisory decisions"),
    ]);
    panelBody(summary).append(metrics);
    panelBody(summary).append(node("div", "quiet cap-context", "The LLM can produce structured recommendations, but it is still advisory-only. It logs validated actions and does not directly mutate rosters, contracts, cap, or draft tables."));
    root.append(summary);

    const grid = node("div", "grid");
    const controls = panel("AI GM Controls", "Start Here");
    const controlsBody = panelBody(controls);
    controlsBody.append(commandBox("Prepare AI GM Tables", commands.aiGmSetup, "ai_gm_setup"));
    controlsBody.append(commandBox("Enable Ollama", commands.aiGmEnableOllama, "ai_gm_enable_ollama"));
    controlsBody.append(commandBox("Show Config", commands.aiGmShowConfig, "ai_gm_show_config"));
    controlsBody.append(commandBox("Show Team Profile", commands.aiGmProfiles, "ai_gm_profiles", { team }));
    controlsBody.append(commandBox("Build Context Packet", commands.aiGmContext, "ai_gm_context", { team, decision_type: "draft_strategy_update" }));

    const runs = panel("Ask A GM", team);
    const runsBody = panelBody(runs);
    runsBody.append(actionCard("Draft Strategy", "Ask the current GM to rank draft priorities from needs, contracts, and pick value.", commands.aiGmRunDraft, "ai_gm_run", { team, decision_type: "draft_strategy_update" }, "good"));
    runsBody.append(actionCard("Depth Chart Review", "Ask for promotions/demotions based on role fit, youth, and current ability.", commands.aiGmRunDepth, "ai_gm_run", { team, decision_type: "depth_chart_review" }, ""));
    runsBody.append(actionCard("Free-Agent Shortlist", "Ask for sensible FA targets using need fit and cap discipline.", commands.aiGmRunFreeAgency, "ai_gm_run", { team, decision_type: "free_agent_shortlist" }, ""));
    runsBody.append(commandBox("Recent AI Logs", commands.aiGmLogs, "ai_gm_logs", { team, limit: 12 }));
    append(grid, [controls, runs]);
    root.append(grid);

    const profilePanel = panel("GM Operating Model", profile.gm_name || profile.real_life_gm_name || team);
    if (profile.abbreviation) {
      panelBody(profilePanel).append(detailGrid([
        ["Team", `${profile.abbreviation} ${profile.city || ""} ${profile.nickname || ""}`.trim()],
        ["Real GM", profile.real_life_gm_name || "-"],
        ["Personality", profile.personality || "-"],
        ["Build State", profile.team_build_state || "-"],
        ["Negotiation", profile.negotiation_style || "-"],
        ["Trade Chart", profile.trade_value_chart || "-"],
      ]));
      const policyGrid = node("div", "scout-note-grid");
      policyGrid.append(sectionBlock("Mandate", node("p", null, profile.current_mandate || profile.team_tendency_summary || "-")));
      policyGrid.append(sectionBlock("Draft", node("p", null, profile.draft_pick_policy || profile.draft_policy || "-")));
      policyGrid.append(sectionBlock("Free Agency", node("p", null, profile.free_agent_cap_policy || profile.free_agency_policy || "-")));
      profilePanel.querySelector(".panel-body").append(policyGrid);
    } else {
      panelBody(profilePanel).append(node("div", "empty-state", "Run Prepare AI GM Tables to seed profiles."));
    }
    root.append(profilePanel);

    const logsPanel = panel("Recent AI Decisions", "Validated advisory output");
    const logList = node("div", "list compact-list");
    (ai.logs || []).forEach((log) => {
      logList.append(row(`${log.team || "-"} ${log.decision_type || "-"}`, log.action_taken || log.error_message || "", log.status || "-", log.status === "valid" || log.status === "completed" ? "good" : log.status === "failed" ? "bad" : "warn"));
    });
    panelBody(logsPanel).append(logList.children.length ? logList : node("div", "empty-state", "No AI GM decisions have been logged yet."));
    root.append(logsPanel);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderStats() {
    setHeader("League Leaders", "Quick realism check for the completed season: passing, rushing, receiving, defense, kicking, and snap leaders.");
    const root = document.createDocumentFragment();
    const stats = data.stats || {};
    const season = data.currentSeason || data.season?.season || "";

    const summary = panel("Season Leaders", `${season}`);
    panelBody(summary).append(table(["Category", "Leader", "Team", "Total"], [
      ["Passing", playerLink(stats.passing?.[0]?.player_id, stats.passing?.[0]?.player_name || "-"), stats.passing?.[0]?.team || "-", stats.passing?.[0] ? `${whole(stats.passing[0].pass_yards)} yards` : "-"],
      ["Rushing", playerLink(stats.rushing?.[0]?.player_id, stats.rushing?.[0]?.player_name || "-"), stats.rushing?.[0]?.team || "-", stats.rushing?.[0] ? `${whole(stats.rushing[0].rush_yards)} yards` : "-"],
      ["Receiving", playerLink(stats.receiving?.[0]?.player_id, stats.receiving?.[0]?.player_name || "-"), stats.receiving?.[0]?.team || "-", stats.receiving?.[0] ? `${whole(stats.receiving[0].receiving_yards)} yards` : "-"],
      ["Sacks", playerLink(stats.sacks?.[0]?.player_id, stats.sacks?.[0]?.player_name || "-"), stats.sacks?.[0]?.team || "-", stats.sacks?.[0] ? `${whole(stats.sacks[0].sacks)} sacks` : "-"],
      ["Tackles", playerLink(stats.tackles?.[0]?.player_id, stats.tackles?.[0]?.player_name || "-"), stats.tackles?.[0]?.team || "-", stats.tackles?.[0] ? `${whole(stats.tackles[0].tackles)} tackles` : "-"],
      ["Snaps", playerLink(stats.snaps?.[0]?.player_id, stats.snaps?.[0]?.player_name || "-"), stats.snaps?.[0]?.team || "-", stats.snaps?.[0] ? `${whole(stats.snaps[0].total_snaps)} snaps` : "-"],
    ]));
    root.append(summary);

    const passing = panel("Passing", "Yards");
    panelBody(passing).append(table(["#", "Player", "Team", "Comp", "Att", "Pct", "Yds", "TD", "INT", "Sacks"], (stats.passing || []).map((p, idx) => [
      idx + 1,
      playerLink(p.player_id, p.player_name),
      p.team,
      whole(p.pass_completions),
      whole(p.pass_attempts),
      rate(p.pass_completions, p.pass_attempts),
      whole(p.pass_yards),
      whole(p.pass_tds),
      whole(p.interceptions_thrown ?? p.interceptions),
      whole(p.sacks_taken),
    ])));
    root.append(passing);

    const grid = node("div", "grid");
    const rushing = panel("Rushing", "Yards");
    panelBody(rushing).append(table(["#", "Player", "Team", "Car", "Yds", "Avg", "TD"], (stats.rushing || []).map((p, idx) => [
      idx + 1,
      playerLink(p.player_id, p.player_name),
      p.team,
      whole(p.rush_attempts),
      whole(p.rush_yards),
      oneDecimal(Number(p.rush_yards || 0) / Math.max(1, Number(p.rush_attempts || 0))),
      whole(p.rush_tds),
    ])));

    const receiving = panel("Receiving", "Yards");
    panelBody(receiving).append(table(["#", "Player", "Team", "Rec", "Tgt", "Yds", "Avg", "TD"], (stats.receiving || []).map((p, idx) => [
      idx + 1,
      playerLink(p.player_id, p.player_name),
      p.team,
      whole(p.receptions),
      whole(p.targets),
      whole(p.receiving_yards),
      oneDecimal(Number(p.receiving_yards || 0) / Math.max(1, Number(p.receptions || 0))),
      whole(p.receiving_tds),
    ])));
    append(grid, [rushing, receiving]);
    root.append(grid);

    const defenseGrid = node("div", "grid");
    const sacks = panel("Pass Rush", "Sacks");
    panelBody(sacks).append(table(["#", "Player", "Team", "Sacks", "Tkl", "FF"], (stats.sacks || []).map((p, idx) => [
      idx + 1,
      playerLink(p.player_id, p.player_name),
      p.team,
      whole(p.sacks),
      whole(p.tackles),
      whole(p.forced_fumbles),
    ])));
    const interceptions = panel("Coverage", "Interceptions");
    panelBody(interceptions).append(table(["#", "Player", "Team", "INT", "PD", "Tkl"], (stats.interceptions || []).map((p, idx) => [
      idx + 1,
      playerLink(p.player_id, p.player_name),
      p.team,
      whole(p.interceptions),
      whole(p.pass_deflections),
      whole(p.tackles),
    ])));
    append(defenseGrid, [sacks, interceptions]);
    root.append(defenseGrid);

    const kicking = panel("Kicking", "Field Goals");
    panelBody(kicking).append(table(["#", "Player", "Team", "FG", "FGA", "XP", "XPA", "Long"], (stats.kicking || []).map((p, idx) => [
      idx + 1,
      playerLink(p.player_id, p.player_name),
      p.team,
      whole(p.fg_made),
      whole(p.fg_attempts),
      whole(p.xp_made),
      whole(p.xp_attempts),
      whole(p.long_fg),
    ])));
    root.append(kicking);

    const snaps = panel("Snaps", "Usage");
    panelBody(snaps).append(table(["#", "Player", "Team", "Off", "Def", "ST", "Total"], (stats.snaps || []).map((p, idx) => [
      idx + 1,
      playerLink(p.player_id, p.player_name),
      p.team,
      whole(p.offensive_snaps),
      whole(p.defensive_snaps),
      whole(p.special_teams_snaps),
      whole(p.total_snaps),
    ])));
    root.append(snaps);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderCalendar() {
    const userTeam = data.activeSave?.user_team || "User";
    setHeader("Calendar", `${userTeam} schedule with league-wide dates, news, and the next useful advance target.`);
    const root = document.createDocumentFragment();
    const calendar = data.calendar || {};
    const nextEvent = calendar.nextEvent || (data.events || [])[0];
    const nextWeek = data.season?.nextWeek;
    const commands = data.commands || {};

    const quickPanel = panel("Next Step", nextEvent ? shortDate(nextEvent.event_start_date) : "Calendar Control");
    const quickBody = panelBody(quickPanel);
    if (nextEvent) {
      quickBody.append(actionCard(
        `Advance To ${nextEvent.event_name || "Next League Date"}`,
        `${shortDate(nextEvent.event_start_date)}${nextEvent.phase_name ? ` | ${nextEvent.phase_name}` : ""}${nextEvent.notes ? ` | ${nextEvent.notes}` : ""}`,
        commands.advanceNextEvent || "python tools\\play.py advance-to-next-event",
        "advance_next_event",
        {},
        "warn",
      ));
    }
    if (nextWeek) {
      quickBody.append(actionCard(
        `Sim Week ${nextWeek}`,
        "Run the next unfinished regular-season week and refresh the season state.",
        commands.simNextWeek || `python tools\\play.py sim-week ${nextWeek} --season ${data.currentSeason || ""} --apply`,
        "sim_week",
        { week: nextWeek },
        "good",
      ));
    }
    if (!nextEvent && !nextWeek) {
      quickBody.append(node("div", "empty-state", "No immediate advance target is exported for the current save state."));
    }
    root.append(quickPanel);

    const scopeLabel = calendar.scope === "user_team" ? `${userTeam} Calendar` : "League Calendar";
    const monthPanel = panel(calendar.monthLabel || "Calendar", `${scopeLabel} | ${shortDate(calendar.rangeStart)} - ${shortDate(calendar.rangeEnd)}`);
    const monthBody = panelBody(monthPanel);
    const weekdayRow = node("div", "calendar-weekdays");
    ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach((label) => weekdayRow.append(node("span", null, label)));
    const monthGrid = node("div", "calendar-grid");
    (calendar.days || []).forEach((day) => monthGrid.append(calendarDayCell(day)));
    append(monthBody, [weekdayRow, monthGrid]);
    root.append(monthPanel);

    const lowerGrid = node("div", "grid calendar-lower-grid");
    const detailsPanel = panel("Date Details", "Click a calendar item");
    panelBody(detailsPanel).append(calendarDetail());

    const upcomingPanel = panel("Upcoming", "Events And Games");
    const upcomingList = node("div", "list compact-list");
    (data.events || []).slice(0, 8).forEach((event) => {
      const item = row(
        event.event_name,
        `${event.phase_name || event.event_category || "League"}${event.notes ? ` | ${event.notes}` : ""}`,
        shortDate(event.event_start_date),
      );
      upcomingList.append(item);
    });
    (calendar.upcomingGames || []).slice(0, 8).forEach((game) => upcomingList.append(calendarGameRow(game)));
    panelBody(upcomingPanel).append(upcomingList.children.length ? upcomingList : node("div", "empty-state", "No upcoming events or games in the exported state."));
    append(lowerGrid, [detailsPanel, upcomingPanel]);
    root.append(lowerGrid);

    const alertsLogGrid = node("div", "grid");
    const alertsPanel = panel("Open Alerts", "Save Hooks");
    const alertsList = node("div", "list compact-list");
    (data.alerts || []).forEach((alert) => {
      alertsList.append(row(alert.title, alert.message, alert.severity, alert.severity === "ERROR" ? "bad" : "warn"));
    });
    panelBody(alertsPanel).append(alertsList.children.length ? alertsList : node("div", "empty-state", "No open alerts."));

    const logPanel = panel("Recent Log", "Game Flow");
    const logList = node("div", "list compact-list");
    (data.log || []).slice(0, 8).forEach((entry) => {
      logList.append(row(entry.title, `${entry.log_type}${entry.details ? ` | ${entry.details}` : ""}`, shortDate(entry.game_date)));
    });
    panelBody(logPanel).append(logList.children.length ? logList : node("div", "empty-state", "No game-flow log entries yet."));
    append(alertsLogGrid, [alertsPanel, logPanel]);
    root.append(alertsLogGrid);
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function calendarDayCell(day) {
    const classes = [
      "calendar-day",
      day.isCurrentMonth ? "" : "outside-month",
      day.isToday ? "today" : "",
      (day.events || []).length || (day.games || []).length || (day.news || []).length ? "has-items" : "",
    ].filter(Boolean).join(" ");
    const cell = node("article", classes);
    const top = append(node("div", "calendar-day-top"), [
      node("strong", null, String(day.dayNumber || "")),
      node("span", null, day.weekday || ""),
    ]);
    const items = node("div", "calendar-items");
    (day.events || []).slice(0, 3).forEach((event) => items.append(calendarEventChip(event)));
    (day.games || []).slice(0, 4).forEach((game) => items.append(calendarGameChip(game)));
    (day.news || []).slice(0, 3).forEach((item) => items.append(calendarNewsChip(item)));
    const overflow = (day.events || []).length + (day.games || []).length + (day.news || []).length - items.children.length;
    if (overflow > 0) items.append(node("span", "calendar-more", `+${overflow} more`));
    append(cell, [top, items]);
    return cell;
  }

  function calendarEventChip(event) {
    const button = node("button", "calendar-chip event", event.event_name || "League Event");
    button.type = "button";
    button.title = event.notes || event.phase_name || event.event_category || "";
    button.addEventListener("click", () => {
      state.selectedCalendarItem = { type: "event", id: event.event_id };
      render();
    });
    return button;
  }

  function calendarNewsChip(item) {
    const button = node("button", `calendar-chip news ${Number(item.is_major || 0) ? "major" : ""}`.trim(), item.title || "League News");
    button.type = "button";
    button.title = item.body || item.source || "";
    button.addEventListener("click", () => {
      state.selectedCalendarItem = { type: "news", id: item.news_id };
      render();
    });
    return button;
  }

  function calendarGameChip(game) {
    const played = Number(game.played || 0) === 1;
    const button = node("button", `calendar-chip game ${played ? "final" : ""}`.trim());
    button.type = "button";
    append(button, [
      teamLogo(game.awayLogo, game.away_team, "calendar-logo"),
      node("span", null, played
        ? `${game.away_team} ${game.away_score ?? "-"} - ${game.home_team} ${game.home_score ?? "-"}`
        : `${game.away_team} @ ${game.home_team}`),
      teamLogo(game.homeLogo, game.home_team, "calendar-logo"),
    ]);
    button.addEventListener("click", () => {
      if (played && runnerMode()) {
        runAction("box_score", { game_id: game.game_id });
        return;
      }
      state.selectedCalendarItem = { type: "game", id: game.game_id };
      render();
    });
    return button;
  }

  function calendarGameRow(game) {
    const item = gameLine(game, data.activeSave?.user_team);
    if (Number(game.played || 0) === 1) {
      item.classList.add("clickable-row");
      item.addEventListener("click", () => runAction("box_score", { game_id: game.game_id }));
    }
    return item;
  }

  function findCalendarItem() {
    const selection = state.selectedCalendarItem;
    const calendar = data.calendar || {};
    if (!selection) return null;
    if (selection.type === "event") {
      return { type: "event", item: (calendar.eventsInView || []).find((event) => Number(event.event_id) === Number(selection.id)) };
    }
    if (selection.type === "news") {
      return { type: "news", item: (calendar.newsInView || []).find((item) => Number(item.news_id) === Number(selection.id)) };
    }
    if (selection.type === "game") {
      return { type: "game", item: (calendar.gamesInView || []).find((game) => Number(game.game_id) === Number(selection.id)) };
    }
    return null;
  }

  function calendarDetail() {
    const selected = findCalendarItem();
    if (!selected || !selected.item) {
      const next = data.calendar?.nextEvent;
      if (next) {
        return append(node("div", "calendar-detail"), [
          node("span", "tag", next.event_category || "Next Event"),
          node("strong", null, next.event_name || "Next League Date"),
          node("p", "muted", `${shortDate(next.event_start_date)}${next.phase_name ? ` | ${next.phase_name}` : ""}`),
          next.notes ? node("p", null, next.notes) : null,
        ]);
      }
      return node("div", "empty-state", "Select an event, game, or story from the calendar.");
    }
    const { type, item } = selected;
    if (type === "event") {
      return append(node("div", "calendar-detail"), [
        node("span", "tag", item.event_category || "League Event"),
        node("strong", null, item.event_name || "League Event"),
        node("p", "muted", `${shortDate(item.event_start_date)}${item.event_time_et ? ` | ${item.event_time_et} ET` : ""}${item.phase_name ? ` | ${item.phase_name}` : ""}`),
        item.notes ? node("p", null, item.notes) : null,
      ]);
    }
    if (type === "news") {
      const open = node("button", "copy-button", "Open League News");
      open.type = "button";
      open.addEventListener("click", () => {
        state.newsFilter = item.category || "all";
        state.view = "leagueNews";
        render();
      });
      const subject = newsSubjectNode(item);
      return append(node("div", "calendar-detail"), [
        node("span", "tag", item.category || "League News"),
        newsTitleNode(item),
        node("p", "muted", `${shortDate(item.news_date)} | ${item.source || "League Wire"}`),
        subject ? append(node("div", "calendar-news-subject"), [node("span", null, "Subject"), subject]) : null,
        node("p", null, item.body || ""),
        open,
      ]);
    }
    const played = Number(item.played || 0) === 1;
    const showBox = node("button", "copy-button", played ? "Show Box Score" : "Box Score After Sim");
    showBox.type = "button";
    showBox.disabled = !played || !runnerMode();
    showBox.addEventListener("click", () => runAction("box_score", { game_id: item.game_id }));
    return append(node("div", "calendar-detail"), [
      node("span", "tag", played ? "Final" : "Scheduled"),
      node("strong", null, played
        ? `${item.away_team} ${item.away_score ?? "-"} at ${item.home_team} ${item.home_score ?? "-"}`
        : `${item.away_team} at ${item.home_team}`),
      node("p", "muted", `Week ${item.week || "-"} | ${shortDate(item.game_date)}${item.game_time_et ? ` | ${item.game_time_et} ET` : ""}`),
      showBox,
    ]);
  }

  function renderCommands() {
    setHeader("Commands", "Copyable commands for the current exported state.");
    const root = document.createDocumentFragment();
    const commands = data.commands || {};
    const groups = [
      ["Save and Calendar", ["newJune1Save", "newGame", "status", "preflight", "advanceNextEvent", "advanceNextLeagueYear", "validateRosters", "autoCutdown", "exportGameCenter", "exportFrontOffice"]],
      ["Season", ["simNextWeek", "simSeason", "postseason", "completeSeason", "boxScore"]],
      ["Contracts", ["contractList", "contractExtend", "contractRelease", "contractRestructure"]],
      ["Depth Chart", ["depthChartShow", "depthChartSet", "depthChartMove"]],
      ["News, Inbox, and Scouting", ["leagueNewsList", "leagueNewsSeed", "eventGenerateWeek", "inboxMarkRead", "scoutingSetup", "scoutingAuto", "scoutingOne", "scoutingRandomTwo", "scoutingDiscoverFour", "scoutingSeniorBowlSetup", "scoutingSeniorBowlProcess", "scoutingTop30Visit", "scoutingTop30Auto"]],
      ["Free Agency", ["freeAgencyStart", "freeAgencyCpuSeed", "freeAgencyHour", "freeAgencyDay", "freeAgencyOffer"]],
      ["Draft", ["draftGenerate", "draftValidate", "advanceToDraft", "draftStart", "draftSkipOne", "draftSkipToUser", "draftFinish", "advanceNextLeagueYear", "draftPick"]],
      ["AI GMs", ["aiGmSetup", "aiGmProfiles", "aiGmEnableOllama", "aiGmShowConfig", "aiGmContext", "aiGmRunDraft", "aiGmRunDepth", "aiGmRunFreeAgency", "aiGmLogs"]],
    ];
    groups.forEach(([title, keys]) => {
      const p = panel(title, "Command Set");
      keys.forEach((key) => p.querySelector(".panel-body").append(commandBox(key, commands[key] || "", actionForCommandKey(key))));
      root.append(p);
    });
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function actionForCommandKey(key) {
    return {
      status: "status",
      preflight: "preflight",
      advanceNextEvent: "advance_next_event",
      advanceNextLeagueYear: "advance_next_league_year",
      validateRosters: "validate_rosters",
      autoCutdown: "auto_cutdown",
      exportGameCenter: "refresh",
      exportFrontOffice: "export_front_office",
      simNextWeek: data.season?.nextWeek ? "sim_week" : null,
      simSeason: "sim_season",
      boxScore: null,
      postseason: "postseason",
      completeSeason: "complete_season",
      contractList: null,
      contractRelease: null,
      contractRestructure: null,
      depthChartShow: null,
      depthChartSet: null,
      depthChartMove: null,
      inboxMarkRead: "inbox_mark_read",
      leagueNewsList: null,
      leagueNewsSeed: "league_news_seed",
      eventGenerateWeek: "event_generate_week",
      scoutingSetup: "scouting_setup",
      scoutingAuto: "scouting_auto",
      scoutingOne: null,
      scoutingRandomTwo: "scouting_random_two",
      scoutingDiscoverFour: "scouting_discover_four",
      scoutingSeniorBowlSetup: "scouting_senior_bowl_setup",
      scoutingSeniorBowlProcess: "scouting_senior_bowl_process",
      scoutingTop30Visit: null,
      scoutingTop30Auto: "scouting_top30_auto",
      freeAgencyStart: "free_agency_start",
      freeAgencyCpuSeed: "free_agency_cpu_seed",
      freeAgencyHour: "free_agency_advance_hour",
      freeAgencyDay: "free_agency_advance_day",
      advanceToDraft: "advance_to_draft",
      draftSkipOne: "draft_skip",
      draftSkipToUser: "draft_skip_to_user",
      draftFinish: "draft_finish",
      draftStart: "draft_start",
      draftSkip: "draft_skip",
      aiGmSetup: "ai_gm_setup",
      aiGmProfiles: "ai_gm_profiles",
      aiGmEnableOllama: "ai_gm_enable_ollama",
      aiGmShowConfig: "ai_gm_show_config",
      aiGmContext: "ai_gm_context",
      aiGmRunDraft: "ai_gm_run",
      aiGmRunDepth: null,
      aiGmRunFreeAgency: null,
      aiGmLogs: "ai_gm_logs",
    }[key] || null;
  }

  function table(headers, rows) {
    if (!rows.length) return node("div", "empty-state", "No rows to show.");
    const wrap = node("div", "table-wrap");
    const tableEl = node("table", "data-table");
    const thead = node("thead");
    const headerRow = node("tr");
    headers.forEach((header) => headerRow.append(node("th", null, header)));
    thead.append(headerRow);
    const tbody = node("tbody");
    rows.forEach((values) => {
      const tr = node("tr");
      values.forEach((value) => {
        const td = node("td");
        if (value instanceof Node) td.append(value);
        else td.textContent = value;
        tr.append(td);
      });
      tbody.append(tr);
    });
    append(tableEl, [thead, tbody]);
    wrap.append(tableEl);
    return wrap;
  }

  function render() {
    if (state.view === "season") renderSeason();
    else if (state.view === "stats") renderStats();
    else if (state.view === "inbox") renderInbox();
    else if (state.view === "leagueNews") renderLeagueNews();
    else if (state.view === "scouting") renderScouting();
    else if (state.view === "depth") renderDepthChart();
    else if (state.view === "contracts") renderContracts();
    else if (state.view === "freeAgency") renderFreeAgency();
    else if (state.view === "draft") renderDraft();
    else if (state.view === "aiGm") renderAiGm();
    else if (state.view === "calendar") renderCalendar();
    else if (state.view === "commands") renderCommands();
    else renderOverview();
  }

  refs.buttons.forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      render();
    });
  });

  loadLiveState().then(render);
}());

