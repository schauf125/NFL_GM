(function () {
  let data = window.APP_SHELL_DATA || { teams: [], registry: { saves: [] }, settings: {}, events: [] };
  const state = {
    view: "home",
    selectedTeam: "MIN",
    gameId: defaultGameId("MIN"),
    saveName: "MIN June 1 Start",
    seed: "",
    variance: true,
    personalityVariance: true,
    developmentModifiers: true,
    runnerAvailable: false,
    runnerBusy: false,
    busyAction: null,
    busyStartedAt: 0,
    elapsedSeconds: 0,
    lastResult: null,
  };

  const refs = {
    splash: document.getElementById("splash"),
    app: document.getElementById("app"),
    seasonText: document.getElementById("seasonText"),
    phaseText: document.getElementById("phaseText"),
    screenTitle: document.getElementById("screenTitle"),
    screenSubhead: document.getElementById("screenSubhead"),
    dateText: document.getElementById("dateText"),
    content: document.getElementById("content"),
    toast: document.getElementById("runnerToast"),
    buttons: Array.from(document.querySelectorAll(".menu-buttons button")),
  };

  let elapsedTimer = null;

  function defaultGameId(team) {
    const stamp = new Date()
      .toISOString()
      .slice(0, 19)
      .replace(/[-:T]/g, "")
      .slice(0, 14);
    return `${String(team || "MIN").toLowerCase()}_june1_${stamp}`;
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (text !== undefined && text !== null) {
      element.textContent = text;
    }
    return element;
  }

  function append(parent, children) {
    children.forEach((child) => {
      if (child !== null && child !== undefined) {
        parent.append(child);
      }
    });
    return parent;
  }

  function currentTeam() {
    return data.teams.find((team) => team.abbr === state.selectedTeam) || data.teams[0];
  }

  function setTheme(team) {
    document.documentElement.style.setProperty("--team-primary", team?.primary || "#75808f");
    document.documentElement.style.setProperty("--team-secondary", team?.secondary || "#d6dde6");
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

  function actionLaunchTarget(action, payload) {
    if (!payload || payload.returncode !== 0) return null;
    if (action === "new_june1_save" || action === "load_game") {
      return "../game_center/index.html";
    }
    return null;
  }

  async function loadLiveState() {
    if (!location.protocol.startsWith("http")) return false;
    try {
      const response = await fetch("/api/app-shell-state", { cache: "no-store" });
      if (!response.ok) return false;
      data = await response.json();
      state.runnerAvailable = true;
      return true;
    } catch (_error) {
      state.runnerAvailable = false;
      return false;
    }
  }

  function startElapsedTimer() {
    window.clearInterval(elapsedTimer);
    state.busyStartedAt = Date.now();
    state.elapsedSeconds = 0;
    elapsedTimer = window.setInterval(() => {
      if (!state.runnerBusy) {
        window.clearInterval(elapsedTimer);
        return;
      }
      state.elapsedSeconds = Math.floor((Date.now() - state.busyStartedAt) / 1000);
      const output = document.getElementById("runnerOutput");
      if (output) output.textContent = busyMessage();
    }, 500);
  }

  async function runAction(action, params) {
    if (!runnerMode() || state.runnerBusy) return;
    state.runnerBusy = true;
    state.busyAction = action;
    state.lastResult = null;
    startElapsedTimer();
    showToast(`${actionLabel(action)} in progress...`);
    render();
    try {
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, params: params || {} }),
      });
      const payload = await response.json();
      if (payload.app_shell_state) data = payload.app_shell_state;
      state.lastResult = payload;
      state.runnerAvailable = true;
      showToast(payload.returncode === 0 ? `${actionLabel(action)} complete` : `${actionLabel(action)} needs attention`);
      const target = actionLaunchTarget(action, payload);
      if (target) {
        showToast("Opening Game Center...");
        window.setTimeout(() => {
          window.location.assign(target);
        }, 450);
      }
    } catch (error) {
      state.lastResult = { error: String(error) };
      showToast("Action could not be completed");
    } finally {
      state.runnerBusy = false;
      state.busyAction = null;
      window.clearInterval(elapsedTimer);
      render();
    }
  }

  function actionLabel(action) {
    return {
      new_june1_save: "Start Game",
      load_game: "Load Game",
      delete_save: "Delete Save",
      refresh: "Refresh",
    }[action] || action || "Command";
  }

  function busyMessage() {
    return `${actionLabel(state.busyAction)} is running...\n\nElapsed: ${state.elapsedSeconds}s\n\nThe league is preparing a playable save with roster variance, personalities, development traits, scheme fits, and draft class setup. This page will move forward when it is ready.`;
  }

  function shortDate(value) {
    if (!value) {
      return "-";
    }
    const parts = value.split("-");
    if (parts.length !== 3) {
      return value;
    }
    return `${parts[1]}/${parts[2]}/${parts[0]}`;
  }

  function panel(title, kicker) {
    const section = node("section", "panel");
    const header = node("div", "panel-header");
    append(header, [node("h2", null, title), node("span", "panel-kicker", kicker || "")]);
    section.append(header);
    return section;
  }

  function runnerOutputPanel() {
    const p = panel("Action Status", state.runnerBusy ? "Running" : "Last Action");
    if (state.runnerBusy) {
      const status = node("div", "action-status-card running");
      const message = node("p", "muted", busyMessage());
      message.id = "runnerOutput";
      append(status, [
        node("span", "spinner"),
        append(node("div"), [
          node("strong", null, `${actionLabel(state.busyAction)} in progress`),
          message,
        ]),
      ]);
      p.append(status);
      return p;
    }
    if (!state.lastResult) {
      p.append(node("div", "empty-state", runnerMode() ? "No action has been run yet." : "Live actions are unavailable."));
      return p;
    }
    const ok = state.lastResult.returncode === 0 && !state.lastResult.error;
    const status = node("div", `action-status-card ${ok ? "good" : "bad"}`);
    append(status, [
      node("span", "status-dot"),
      append(node("div"), [
        node("strong", null, actionLabel(state.lastResult.action)),
        node("p", "muted", ok ? "Completed successfully." : (state.lastResult.error || "The action could not be completed.")),
      ]),
    ]);
    const summary = node("div", "runner-summary");
    append(summary, [
      metric("Action", actionLabel(state.lastResult.action), ok ? "Completed" : "Needs attention"),
      metric("Elapsed", `${state.lastResult.duration_seconds ?? "-"}s`, "Action time"),
    ]);
    append(p, [status, summary]);
    return p;
  }

  function metric(label, value, note) {
    const item = node("div", "metric");
    append(item, [node("span", null, label), node("strong", null, value), note ? node("small", null, note) : null]);
    return item;
  }

  function updateHeader(title, subhead) {
    refs.seasonText.textContent = data.currentSeason || "";
    refs.phaseText.textContent = data.currentPhase || "";
    refs.screenTitle.textContent = title;
    refs.screenSubhead.textContent = subhead;
    refs.dateText.textContent = shortDate(data.currentDate);
    refs.buttons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
  }

  function renderHome() {
    updateHeader("League Home", "Start a save, load an existing one, or jump into the season hub.");
    const root = document.createDocumentFragment();
    const saves = data.registry.saves || [];
    const active = saves.find((save) => save.active);
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Active Save", active ? active.name : "None", active ? active.gameId : "Create or load a save"),
      metric("Registered Saves", String(saves.length), "Local save registry"),
      metric("Calendar", data.currentPhase || "-", data.settings?.salary_cap_rule || "TOP_51_ALWAYS"),
      metric("Teams", String(data.teams.length), "NFL database"),
    ]);
    const overview = panel("Start Desk", "Main Menu");
    overview.append(metrics);
    root.append(overview);

    const grid = node("div", "grid");
    const startPanel = panel("Start Game", runnerMode() ? "Local Runner Ready" : "Runner Offline");
    const startCopy = node("div", "start-copy");
    append(startCopy, [
      node("strong", null, "Create a fresh June 1 save"),
      node("p", "muted", "Build a playable league file with roster variance, hidden personalities, development modifiers, scheme fits, and draft prep already seeded."),
    ]);
    const startControls = node("div", "command-actions");
    const newButton = node("button", "primary-button", runnerMode() ? "Start Game" : "Open UI Runner");
    newButton.type = "button";
    newButton.disabled = state.runnerBusy || !runnerMode();
    newButton.addEventListener("click", () => {
      const team = currentTeam()?.abbr || state.selectedTeam || "MIN";
      state.selectedTeam = team;
      state.gameId = defaultGameId(team);
      state.saveName = `${team} June 1 Start`;
      runAction("new_june1_save", newGameParams());
    });
    const configButton = node("button", "copy-button", "Configure");
    configButton.type = "button";
    configButton.addEventListener("click", () => {
      state.view = "newGame";
      render();
    });
    append(startControls, [newButton, configButton]);
    append(startPanel, [startCopy, startControls]);

    const eventsPanel = panel("Upcoming Dates", "Calendar");
    const eventList = node("div", "list-stack");
    (data.events || []).slice(0, 7).forEach((event) => {
      const row = node("div", "event-row");
      append(row, [
        append(node("div"), [node("strong", null, event.name), node("div", "muted", `${event.phase || ""} | ${event.category || ""}`)]),
        node("span", "event-date", shortDate(event.date)),
      ]);
      eventList.append(row);
    });
    eventsPanel.append(eventList);

    const actionsPanel = panel("Continue", "Workspace");
    const actions = node("div", "list-stack");
    [
      ["Game Center", "../game_center/index.html", "Season, free agency, draft, calendar, and league control."],
      ["Front Office", "../front_office/index.html", "Team dashboard, roster, cap, draft, free agency."],
      ["Player Profiles", "../player_profile/index.html", "FM-style player pages with attributes and career data."],
      ["Player Cards", "../player_card/index.html", "Compact player card view."],
    ].forEach(([label, href, description]) => {
      const link = node("a", "action", label);
      link.href = href;
      const row = node("div", "event-row");
      append(row, [append(node("div"), [link, node("div", "muted", description)]), node("span", "event-date", "Open")]);
      actions.append(row);
    });
    actionsPanel.append(actions);
    append(grid, [startPanel, actionsPanel, eventsPanel]);
    root.append(grid);

    const flowPanel = panel("Recommended Playtest Flow", "Season Loop");
    const flow = node("div", "flow-grid");
    [
      ["1", "Start or Continue", "Use a June 1 save and confirm the current date, cap, and roster state.", "Home"],
      ["2", "Run the Season", "Use Game Center to sim weeks, review stats, and complete the playoffs.", "Game Center"],
      ["3", "Work the Offseason", "Handle expiring contracts, free agency, draft, and rookie roster additions.", "Game Center"],
      ["4", "Review the Team", "Use Front Office and Player Profiles to inspect roster, cap, depth, and development.", "Front Office"],
    ].forEach(([number, title, detail, destination]) => {
      const step = node("div", "flow-step");
      append(step, [
        node("span", "flow-number", number),
        append(node("div"), [node("strong", null, title), node("p", "muted", detail)]),
        node("em", null, destination),
      ]);
      flow.append(step);
    });
    flowPanel.append(flow);
    root.append(flowPanel);

    root.append(runnerOutputPanel());
    refs.content.replaceChildren(root);
  }

  function newGameParams() {
    return {
      game_id: state.gameId,
      name: state.saveName,
      user_team: state.selectedTeam,
      start_year: 2026,
      seed: state.seed ? Number(state.seed) : undefined,
      no_variance: !state.variance,
      no_personality_variance: !state.personalityVariance,
      no_development_modifiers: !state.developmentModifiers,
      timeout_seconds: 3600,
    };
  }

  function renderNewGame() {
    const team = currentTeam();
    setTheme(team);
    updateHeader("New Game", "Choose a team and start a fresh June 1 save.");
    const root = document.createDocumentFragment();
    const formGrid = node("div", "form-grid");

    const formPanel = panel("Save Setup", "New League");
    const stack = node("div", "form-stack");
    const gameId = inputLabel("Game ID", "text", state.gameId, (value) => { state.gameId = value; });
    const saveName = inputLabel("Save Name", "text", state.saveName, (value) => { state.saveName = value; });
    const seed = inputLabel("Seed", "number", state.seed, (value) => { state.seed = value; });
    const variance = checkboxLabel("Rating Variance", state.variance, (checked) => { state.variance = checked; });
    const personality = checkboxLabel("Personality Variance", state.personalityVariance, (checked) => { state.personalityVariance = checked; });
    const development = checkboxLabel("Development Modifiers", state.developmentModifiers, (checked) => { state.developmentModifiers = checked; });
    append(stack, [gameId, saveName, seed, variance, personality, development]);
    formPanel.append(stack);
    const command = node("div", "command-box setup-actions");
    const note = node("div", "command-note", runnerMode() ? "Start the save when your setup looks right." : "Live actions are unavailable.");
    const actions = node("div", "command-actions");
    const start = node("button", "primary-button", state.runnerBusy ? "Running" : "Start Game");
    start.type = "button";
    start.disabled = state.runnerBusy || !runnerMode();
    start.addEventListener("click", () => runAction("new_june1_save", newGameParams()));
    append(actions, [start]);
    append(command, [note, actions]);
    formPanel.append(command);

    const teamsPanel = panel("Team Select", team ? team.name : "");
    const teamGrid = node("div", "team-grid");
    data.teams.forEach((item) => {
      const card = node("button", "team-choice");
      card.type = "button";
      card.classList.toggle("active", item.abbr === state.selectedTeam);
      const logo = node("img");
      logo.src = item.logo || "";
      logo.alt = "";
      logo.hidden = !item.logo;
      append(card, [logo, append(node("div"), [node("strong", null, item.abbr), node("div", "muted", item.name)])]);
      card.addEventListener("click", () => {
        state.selectedTeam = item.abbr;
        if (!state.gameId || state.gameId.startsWith("min_") || state.gameId.includes("_june1_")) {
          state.gameId = defaultGameId(item.abbr);
        }
        if (!state.saveName || state.saveName.includes("June 1")) {
          state.saveName = `${item.abbr} June 1 Start`;
        }
        renderNewGame();
      });
      teamGrid.append(card);
    });
    teamsPanel.append(teamGrid);
    append(formGrid, [formPanel, teamsPanel]);
    root.append(formGrid);
    root.append(runnerOutputPanel());
    refs.content.replaceChildren(root);
  }

  function inputLabel(label, type, value, onInput) {
    const wrapper = node("label", "form-label", label);
    const input = node("input");
    input.type = type;
    input.value = value;
    input.addEventListener("input", () => onInput(input.value));
    wrapper.append(input);
    return wrapper;
  }

  function checkboxLabel(label, checked, onInput) {
    const wrapper = node("label", "form-label", label);
    const input = node("input");
    input.type = "checkbox";
    input.checked = checked;
    input.addEventListener("change", () => onInput(input.checked));
    wrapper.append(input);
    return wrapper;
  }

  function renderLoadGame() {
    updateHeader("Load Game", "Registered saves from the local save registry. Empty here means this copy does not have saves yet.");
    const root = document.createDocumentFragment();
    const savePanel = panel("Save Browser", `${(data.registry.saves || []).length} saves`);
    const list = node("div", "list-stack");
    const saves = data.registry.saves || [];
    if (!saves.length) {
      list.append(node("div", "empty-state", "No saves registered yet."));
    } else {
      saves.forEach((save) => {
        const row = node("div", "save-row");
        const load = node("button", "copy-button", save.active ? "Active" : "Load");
        load.type = "button";
        load.disabled = state.runnerBusy || save.active || !runnerMode();
        load.addEventListener("click", () => runAction("load_game", { game_id: save.gameId }));
        const del = node("button", "copy-button danger-button", save.active ? "Delete Active" : "Delete");
        del.type = "button";
        del.disabled = state.runnerBusy || !runnerMode();
        del.addEventListener("click", () => {
          const label = save.name || save.gameId;
          if (!window.confirm(`Delete save "${label}"? This removes the local save folder.`)) {
            return;
          }
          runAction("delete_save", { game_id: save.gameId });
        });
        const left = append(node("div"), [
          node("strong", null, save.name),
          node("div", "muted", `${save.userTeam || "-"} | ${save.currentDate || "-"} | ${save.phase || "-"}`),
        ]);
        append(row, [left, append(node("div", "save-actions"), [load, del])]);
        list.append(row);
      });
    }
    savePanel.append(list);
    root.append(savePanel);
    root.append(runnerOutputPanel());
    refs.content.replaceChildren(root);
  }

  function renderSettings() {
    updateHeader("Settings", "Current database-level settings and prototype UI notes.");
    const root = document.createDocumentFragment();
    const settingsPanel = panel("Game Settings", "Database");
    const stack = node("div", "list-stack");
    Object.entries(data.settings || {}).sort(([a], [b]) => a.localeCompare(b)).forEach(([key, value]) => {
      const row = node("div", "setting-row");
      append(row, [node("strong", null, key), node("div", "muted", value)]);
      stack.append(row);
    });
    settingsPanel.append(stack);
    root.append(settingsPanel);

    const uiPanel = panel("UI Status", "Prototype");
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Mode", runnerMode() ? "Live" : "Static", runnerMode() ? "Actions available" : "Showing saved export"),
      metric("Runner", runnerMode() ? "Online" : "Offline", "One-click local actions"),
      metric("Data Source", data.database ? "Active DB" : "Master DB", data.database || "Exported JS payloads"),
      metric("Theme", "Front Office Dark", "Shared with profiles"),
      metric("Next Step", "Backend", "Create/load from buttons"),
    ]);
    uiPanel.append(metrics);
    root.append(uiPanel);
    refs.content.replaceChildren(root);
  }

  function render() {
    refs.buttons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
    if (state.view === "newGame") {
      renderNewGame();
    } else if (state.view === "loadGame") {
      renderLoadGame();
    } else if (state.view === "settings") {
      renderSettings();
    } else {
      setTheme(currentTeam());
      renderHome();
    }
  }

  refs.buttons.forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      render();
    });
  });

  loadLiveState().finally(() => setTimeout(() => {
    refs.splash.classList.add("hidden");
    refs.app.classList.remove("hidden");
    render();
  }, 900));
}());
