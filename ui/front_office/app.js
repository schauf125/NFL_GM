(function () {
  const data = window.FRONT_OFFICE_DATA || { season: 2026, teams: [], events: [] };
  const teams = data.teams || [];
  const state = {
    teamAbbr: (teams.find((team) => team.abbr === "MIN") || teams[0] || {}).abbr,
    view: "overview",
    selectedPlayerId: null,
    rosterSearch: "",
    rosterPosition: "ALL",
    freeAgentSearch: "",
    freeAgentPosition: "ALL",
  };

  const refs = {
    seasonLabel: document.getElementById("seasonLabel"),
    teamList: document.getElementById("teamList"),
    teamLogo: document.getElementById("teamLogo"),
    teamMeta: document.getElementById("teamMeta"),
    teamName: document.getElementById("teamName"),
    stadiumText: document.getElementById("stadiumText"),
    tabs: Array.from(document.querySelectorAll(".view-tabs button")),
    mainView: document.getElementById("mainView"),
    playerPanel: document.getElementById("playerPanel"),
  };

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
    return teams.find((team) => team.abbr === state.teamAbbr) || teams[0];
  }

  function money(amount) {
    const value = Number(amount || 0);
    const sign = value < 0 ? "-" : "";
    const absolute = Math.abs(value);
    if (absolute >= 1000000) {
      return `${sign}$${(absolute / 1000000).toFixed(1)}M`;
    }
    if (absolute >= 1000) {
      return `${sign}$${Math.round(absolute / 1000)}K`;
    }
    return `${sign}$${absolute}`;
  }

  function shortDate(value) {
    if (!value) {
      return "-";
    }
    const parts = value.split("-");
    if (parts.length !== 3) {
      return value;
    }
    return `${parts[1]}/${parts[2]}`;
  }

  function initials(name) {
    return name.split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
  }

  function setTheme(team) {
    document.documentElement.style.setProperty("--team-primary", team.colors.primary || "#75808f");
    document.documentElement.style.setProperty("--team-secondary", team.colors.secondary || "#d6dde6");
  }

  function roleBar(value, scale) {
    const bar = node("div", "skill-bar");
    const width = Math.max(0, Math.min(100, (Number(value || 0) / scale) * 100));
    bar.style.setProperty("--rating", `${width}%`);
    return bar;
  }

  function playerAvatar(player, className) {
    const avatar = node("div", className || "avatar");
    if (player.headshot) {
      const image = node("img");
      image.src = player.headshot;
      image.alt = "";
      avatar.append(image);
    } else {
      avatar.textContent = initials(player.name);
    }
    return avatar;
  }

  function playerProfileHref(player, fallbackTeam) {
    const params = new URLSearchParams();
    const playerId = player.id || player.playerId || player.player_id;
    const name = player.name || player.player || player.playerName || player.player_name;
    const team = player.team || fallbackTeam || player.previousTeam || player.previous_team;
    if (playerId) params.set("player", playerId);
    if (name) params.set("name", name);
    if (team) params.set("team", team);
    if (player.position) params.set("position", player.position);
    const query = params.toString();
    return `../player_profile/index.html${query ? `?${query}` : ""}`;
  }

  function playerProfileLink(player, className, fallbackTeam) {
    const link = node("a", className || "player-link", player.name);
    link.href = playerProfileHref(player, fallbackTeam);
    link.addEventListener("click", (event) => event.stopPropagation());
    return link;
  }

  function findPlayer(team, playerId) {
    return (team.roster || []).find((player) => player.id === playerId);
  }

  function topPlayers(team, count) {
    return [...(team.roster || [])]
      .sort((a, b) => (b.role.value || 0) - (a.role.value || 0))
      .slice(0, count);
  }

  function selectDefaultPlayer(team) {
    const selected = findPlayer(team, state.selectedPlayerId);
    if (selected) {
      return selected;
    }
    const top = topPlayers(team, 1)[0] || (team.roster || [])[0] || null;
    state.selectedPlayerId = top ? top.id : null;
    return top;
  }

  function renderTeams() {
    refs.teamList.replaceChildren();
    teams.forEach((team) => {
      const button = node("button", "team-button");
      button.type = "button";
      button.classList.toggle("active", team.abbr === state.teamAbbr);

      const logo = node("img");
      logo.src = team.logo || "";
      logo.alt = "";
      logo.hidden = !team.logo;

      const identity = node("span");
      append(identity, [node("strong", null, team.abbr), node("small", null, team.nickname)]);

      const cap = node("span", null, money(team.cap.capSpace));
      cap.classList.toggle("negative", Number(team.cap.capSpace) < 0);
      cap.classList.toggle("positive", Number(team.cap.capSpace) >= 0);

      append(button, [logo, identity, cap]);
      button.addEventListener("click", () => {
        state.teamAbbr = team.abbr;
        state.selectedPlayerId = null;
        render();
      });
      refs.teamList.append(button);
    });
  }

  function renderHeader(team) {
    setTheme(team);
    refs.teamLogo.src = team.logo || "";
    refs.teamLogo.hidden = !team.logo;
    refs.teamMeta.textContent = `${team.conference} | ${team.division}`;
    refs.teamName.textContent = team.name;
    refs.stadiumText.textContent = team.stadium;
    refs.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === state.view));
  }

  function metric(label, value, note, toneClass) {
    const item = node("div", "metric");
    const strong = node("strong", toneClass || "", value);
    append(item, [node("span", "metric-label", label), strong, node("small", null, note)]);
    return item;
  }

  function panel(title, kicker) {
    const section = node("section", "panel");
    const header = node("div", "panel-header");
    append(header, [node("h2", null, title), node("span", "panel-kicker", kicker || "")]);
    section.append(header);
    return section;
  }

  function renderOverview(team) {
    const root = document.createDocumentFragment();
    const record = team.record || {};
    const capTone = Number(team.cap.capSpace) < 0 ? "negative" : "positive";

    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Record", `${record.wins}-${record.losses}-${record.ties}`, `${record.pointsFor} PF / ${record.pointsAgainst} PA`),
      metric("Cap Space", money(team.cap.capSpace), team.cap.mode, capTone),
      metric("Roster", String(team.rosterCount), `${team.cap.activeContracts} active contracts`),
      metric("Top 51", money(team.cap.top51PlayerCap), `${team.cap.contractsCounted} counted`),
    ]);

    const overview = panel("Team Snapshot", "Front Office");
    overview.append(metrics);
    root.append(overview);

    const desk = panel("Front Office Desk", "Common Tasks");
    const shortcuts = node("div", "shortcut-grid");
    [
      ["Roster", "Audit the active roster by position, age, cap hit, and role read.", "roster"],
      ["Depth Chart", "Move starters and backups into the current football shape.", "depth"],
      ["Cap", "Review Top 51, active contract charges, and cap space.", "cap"],
      ["Free Agency", "Browse the market and compare fit, price, and motivation.", "freeAgents"],
      ["Draft Capital", "Review future picks and team-controlled assets.", "draft"],
      ["Transactions", "Read the recent team and league move log.", "transactions"],
    ].forEach(([title, detail, view]) => {
      const card = node("button", "shortcut-card");
      card.type = "button";
      card.addEventListener("click", () => {
        state.view = view;
        render();
      });
      append(card, [node("strong", null, title), node("span", null, detail)]);
      shortcuts.append(card);
    });
    desk.append(shortcuts);
    root.append(desk);

    const split = node("div", "split-grid");
    const topPanel = panel("Core Players", "Role Read");
    const topList = node("div", "list-stack");
    topPlayers(team, 6).forEach((player) => {
      const row = node("div", "list-row");
      row.addEventListener("click", () => {
        state.selectedPlayerId = player.id;
        renderPlayerPanel(team);
      });
      const left = node("div");
      append(left, [playerProfileLink(player, "player-link strong-link", team.abbr), node("div", "subtle", `${player.position} | ${player.role.label}`)]);
      append(row, [left, node("span", "grade", player.role.grade)]);
      topList.append(row);
    });
    topPanel.append(topList);

    const nextPanel = panel("Upcoming", "Schedule");
    const nextList = node("div", "list-stack");
    (team.schedule || []).slice(0, 5).forEach((game) => nextList.append(gameRow(game)));
    nextPanel.append(nextList);
    append(split, [topPanel, nextPanel]);
    root.append(split);

    if (data.events && data.events.length) {
      const eventPanel = panel("League Calendar", "Next Events");
      const events = node("div", "list-stack");
      data.events.slice(0, 6).forEach((event) => {
        const row = node("div", "list-row");
        append(row, [
          append(node("div"), [node("strong", null, event.title), node("div", "subtle", `${event.phase || ""} | ${event.type || ""}`)]),
          node("span", "grade", shortDate(event.date)),
        ]);
        events.append(row);
      });
      eventPanel.append(events);
      root.append(eventPanel);
    }

    refs.mainView.replaceChildren(root);
  }

  function positionOptions(team) {
    return Array.from(new Set((team.roster || []).map((player) => player.position)))
      .sort((a, b) => ((team.roster || []).find((p) => p.position === a)?.sortOrder || 99) - ((team.roster || []).find((p) => p.position === b)?.sortOrder || 99));
  }

  function rosterMatches(player) {
    const query = state.rosterSearch.trim().toLowerCase();
    const searchBlob = `${player.name} ${player.position} ${player.college} ${player.role.label}`.toLowerCase();
    const queryMatch = !query || searchBlob.includes(query);
    const positionMatch = state.rosterPosition === "ALL" || player.position === state.rosterPosition;
    return queryMatch && positionMatch;
  }

  function renderRosterRows(team, tbody) {
    tbody.replaceChildren();
    (team.roster || []).filter(rosterMatches).forEach((player) => {
      const row = node("tr");
      row.classList.toggle("selected", player.id === state.selectedPlayerId);
      row.addEventListener("click", () => {
        state.selectedPlayerId = player.id;
        renderRosterRows(team, tbody);
        renderPlayerPanel(team);
      });

      const playerCell = node("td");
      const playerWrap = node("div", "player-cell");
      const info = node("div");
      append(info, [playerProfileLink(player, "player-link strong-link", team.abbr), node("div", "subtle", `${player.jersey} | ${player.college}`)]);
      append(playerWrap, [playerAvatar(player), info]);
      playerCell.append(playerWrap);

      append(row, [
        playerCell,
        node("td", null, player.position),
        node("td", null, String(player.age || "-")),
        node("td", null, player.exp),
        node("td", null, money(player.contract.capHit)),
        append(node("td"), [node("span", "grade", player.role.grade)]),
      ]);
      tbody.append(row);
    });
  }

  function renderRoster(team) {
    const root = panel("Roster", `${team.rosterCount} players`);

    const toolbar = node("div", "toolbar");
    const search = node("input");
    search.type = "search";
    search.placeholder = "Search roster";
    search.value = state.rosterSearch;
    const select = node("select");
    const allOption = node("option", null, "All positions");
    allOption.value = "ALL";
    select.append(allOption);
    positionOptions(team).forEach((position) => {
      const option = node("option", null, position);
      option.value = position;
      option.selected = position === state.rosterPosition;
      select.append(option);
    });
    append(toolbar, [search, select]);

    const tableWrap = node("div", "scroll-table");
    const table = node("table", "roster-table");
    const thead = node("thead");
    const headRow = node("tr");
    ["Player", "Pos", "Age", "Exp", "Cap Hit", "Read"].forEach((label) => headRow.append(node("th", null, label)));
    thead.append(headRow);
    const tbody = node("tbody");
    append(table, [thead, tbody]);
    tableWrap.append(table);
    append(root, [toolbar, tableWrap]);

    search.addEventListener("input", () => {
      state.rosterSearch = search.value;
      renderRosterRows(team, tbody);
    });
    select.addEventListener("change", () => {
      state.rosterPosition = select.value;
      renderRosterRows(team, tbody);
    });

    refs.mainView.replaceChildren(root);
    renderRosterRows(team, tbody);
  }

  function depthPositions(team) {
    return Object.keys(team.depth || {}).sort((a, b) => {
      const aOrder = (team.roster || []).find((player) => player.position === a)?.sortOrder || 99;
      const bOrder = (team.roster || []).find((player) => player.position === b)?.sortOrder || 99;
      return aOrder - bOrder || a.localeCompare(b);
    });
  }

  function renderDepth(team) {
    const root = panel("Depth Chart", "Projected");
    const grid = node("div", "depth-grid");
    depthPositions(team).forEach((position) => {
      const card = node("section", "depth-card");
      card.append(node("h3", null, position));
      const stack = node("div", "list-stack");
      (team.depth[position] || []).slice(0, 5).forEach((playerId, index) => {
        const player = findPlayer(team, playerId);
        if (!player) {
          return;
        }
        const row = node("div", "depth-player");
        row.addEventListener("click", () => {
          state.selectedPlayerId = player.id;
          renderPlayerPanel(team);
        });
        append(row, [
          node("span", "depth-rank", String(index + 1)),
          append(node("div"), [playerProfileLink(player, "player-link strong-link", team.abbr), node("div", "subtle", player.role.label)]),
          node("span", "grade", player.role.grade),
        ]);
        stack.append(row);
      });
      card.append(stack);
      grid.append(card);
    });
    root.append(grid);
    refs.mainView.replaceChildren(root);
  }

  function gameRow(game) {
    const row = node("div", "game-row");
    const side = game.side === "home" ? "vs" : "at";
    const result = game.played ? `${game.awayScore}-${game.homeScore}` : game.time || "TBD";
    append(row, [
      node("span", "game-week", `Week ${game.week}`),
      append(node("div"), [node("strong", null, `${side.toUpperCase()} ${game.opponent}`), node("div", "subtle", game.opponentName)]),
      append(node("div"), [node("strong", null, shortDate(game.date)), node("div", "subtle", result)]),
    ]);
    return row;
  }

  function renderSchedule(team) {
    const root = panel("Schedule", `${team.schedule.length} games`);
    const list = node("div", "list-stack");
    (team.schedule || []).forEach((game) => list.append(gameRow(game)));
    root.append(list);
    refs.mainView.replaceChildren(root);
  }

  function renderCoaches(team) {
    const root = panel("Coaching Staff", "Position Groups");
    const grid = node("div", "coach-grid");
    (team.coaches || []).forEach((coach) => {
      const card = node("section", "coach-row");
      append(card, [node("h3", null, coach.name), node("div", "subtle", `${coach.role} | ${coach.specialty}`)]);
      const ratings = node("div", "coach-ratings");
      (coach.ratings || []).forEach((rating) => {
        const row = node("div", "coach-rating");
        append(row, [node("strong", null, rating.group), roleBar(rating.value, 20), node("span", "grade", rating.grade)]);
        ratings.append(row);
      });
      card.append(ratings);
      grid.append(card);
    });
    root.append(grid);
    refs.mainView.replaceChildren(root);
  }

  function capLineTable(lines) {
    const tableWrap = node("div", "scroll-table");
    const table = node("table", "roster-table");
    const thead = node("thead");
    const headRow = node("tr");
    ["Rank", "Type", "Player", "Amount", "Counted", "Notes"].forEach((label) => headRow.append(node("th", null, label)));
    thead.append(headRow);
    const tbody = node("tbody");

    (lines || []).forEach((line) => {
      const row = node("tr");
      const counted = node("td", Number(line.counted) < 0 ? "negative" : "", money(line.counted));
      append(row, [
        node("td", null, line.rank || "-"),
        node("td", null, line.type || "-"),
        node("td", null, line.player ? `${line.player} (${line.position || "-"})` : "-"),
        node("td", null, money(line.amount)),
        counted,
        node("td", "subtle", line.description || ""),
      ]);
      tbody.append(row);
    });

    append(table, [thead, tbody]);
    tableWrap.append(table);
    return tableWrap;
  }

  function renderCap(team) {
    const root = document.createDocumentFragment();
    const capTone = Number(team.cap.capSpace) < 0 ? "negative" : "positive";
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Salary Cap", money(team.cap.salaryCap), team.cap.mode),
      metric("Cap Space", money(team.cap.capSpace), team.cap.label, capTone),
      metric("Committed", money(team.cap.totalCommitted), `${team.cap.contractsCounted} counted`),
      metric("Other Charges", money(team.cap.otherCharges), "Reconciliation / dead / reserve"),
    ]);
    const summary = panel("Cap Summary", "Top 51");
    summary.append(metrics);
    root.append(summary);

    const ledger = panel("Cap Ledger", `${(team.capLines || []).length} lines`);
    ledger.append(capLineTable(team.capLines || []));
    root.append(ledger);
    refs.mainView.replaceChildren(root);
  }

  function pickLabel(pick) {
    const pickNumber = pick.pickNumber ? `Pick ${pick.pickNumber}` : "Slot TBD";
    const original = pick.originalTeam && pick.originalTeam !== pick.currentTeam ? `from ${pick.originalTeam}` : "own pick";
    const condition = pick.conditional ? "conditional" : pick.comp ? "comp" : original;
    return `${pick.year} R${pick.round} | ${pickNumber} | ${condition}`;
  }

  function renderDraft(team) {
    const root = panel("Draft Capital", `${(team.draftPicks || []).length} picks`);
    const years = Array.from(new Set((team.draftPicks || []).map((pick) => pick.year))).sort();
    const grid = node("div", "pick-grid");

    years.forEach((year) => {
      const card = node("section", "pick-card");
      card.append(node("h3", null, String(year)));
      const stack = node("div", "list-stack");
      (team.draftPicks || []).filter((pick) => pick.year === year).forEach((pick) => {
        const row = node("div", "list-row");
        const details = node("div");
        append(details, [
          node("strong", null, `Round ${pick.round}`),
          node("div", "subtle", pickLabel(pick)),
          pick.tradeNote ? node("div", "subtle", pick.tradeNote) : null,
          pick.condition ? node("div", "subtle", pick.condition) : null,
        ]);
        append(row, [details, node("span", "grade", pick.traded ? "Moved" : "Held")]);
        stack.append(row);
      });
      card.append(stack);
      grid.append(card);
    });

    root.append(grid);
    refs.mainView.replaceChildren(root);
  }

  function freeAgentMatches(player) {
    const query = state.freeAgentSearch.trim().toLowerCase();
    const searchBlob = `${player.name} ${player.position} ${player.group} ${player.marketTier} ${player.previousTeam} ${player.motivation} ${player.notes}`.toLowerCase();
    const queryMatch = !query || searchBlob.includes(query);
    const positionMatch = state.freeAgentPosition === "ALL" || player.group === state.freeAgentPosition || player.position === state.freeAgentPosition;
    return queryMatch && positionMatch;
  }

  function renderFreeAgents(team) {
    const freeAgents = (data.league && data.league.freeAgents) || [];
    const root = panel("Free Agency", `${freeAgents.length} players`);

    const toolbar = node("div", "toolbar");
    const search = node("input");
    search.type = "search";
    search.placeholder = "Search free agents";
    search.value = state.freeAgentSearch;
    const select = node("select");
    const allOption = node("option", null, "All groups");
    allOption.value = "ALL";
    select.append(allOption);
    Array.from(new Set(freeAgents.map((player) => player.group || player.position))).sort().forEach((group) => {
      const option = node("option", null, group);
      option.value = group;
      option.selected = group === state.freeAgentPosition;
      select.append(option);
    });
    append(toolbar, [search, select]);

    const tableWrap = node("div", "scroll-table");
    const table = node("table", "roster-table");
    const thead = node("thead");
    const headRow = node("tr");
    ["Player", "Pos", "Age", "Market", "Ask", "Fit", "Motivation"].forEach((label) => headRow.append(node("th", null, label)));
    thead.append(headRow);
    const tbody = node("tbody");
    append(table, [thead, tbody]);
    tableWrap.append(table);
    append(root, [toolbar, tableWrap]);

    function renderRows() {
      tbody.replaceChildren();
      freeAgents.filter(freeAgentMatches).slice(0, 140).forEach((player) => {
        const row = node("tr");
        const preferred = `${player.preferredTeams || ""},${player.hometownTeams || ""}`.split(",").includes(team.abbr);
        append(row, [
          append(node("td"), [playerProfileLink(player, "player-link strong-link", team.abbr), node("div", "subtle", player.previousTeam ? `Prev ${player.previousTeam}` : "Open market")]),
          node("td", null, player.position),
          node("td", null, String(player.age || "-")),
          append(node("td"), [node("span", "grade", player.marketTier)]),
          node("td", null, money(player.askingAav)),
          append(node("td"), [node("span", "grade", preferred ? "Target" : player.role.grade)]),
          node("td", "subtle", player.notes || player.motivation || ""),
        ]);
        tbody.append(row);
      });
    }

    search.addEventListener("input", () => {
      state.freeAgentSearch = search.value;
      renderRows();
    });
    select.addEventListener("change", () => {
      state.freeAgentPosition = select.value;
      renderRows();
    });
    refs.mainView.replaceChildren(root);
    renderRows();
  }

  function renderLeague() {
    const root = document.createDocumentFragment();
    const standings = ((data.league && data.league.standings) || []);
    const divisions = Array.from(new Set(standings.map((row) => row.division))).sort();

    const standingsPanel = panel("Standings", String(data.season));
    const grid = node("div", "division-grid");
    divisions.forEach((division) => {
      const card = node("section", "pick-card");
      card.append(node("h3", null, division));
      const table = node("table", "mini-table");
      const head = node("tr");
      ["Team", "W-L-T", "PF", "PA", "+/-"].forEach((label) => head.append(node("th", null, label)));
      const thead = node("thead");
      thead.append(head);
      const tbody = node("tbody");
      standings.filter((row) => row.division === division).forEach((row) => {
        const tr = node("tr");
        append(tr, [
          node("td", null, row.abbr),
          node("td", null, `${row.wins}-${row.losses}-${row.ties}`),
          node("td", null, String(row.pointsFor)),
          node("td", null, String(row.pointsAgainst)),
          node("td", Number(row.pointDiff) < 0 ? "negative" : "positive", String(row.pointDiff)),
        ]);
        tbody.append(tr);
      });
      append(table, [thead, tbody]);
      card.append(table);
      grid.append(card);
    });
    standingsPanel.append(grid);
    root.append(standingsPanel);

    const waivers = ((data.league && data.league.waivers) || []);
    const waiverPanel = panel("Waiver Wire", waivers.length ? `${waivers.length} open` : "Clear");
    if (!waivers.length) {
      waiverPanel.append(node("div", "empty-state", "No current waiver entries."));
    } else {
      const list = node("div", "list-stack");
      waivers.forEach((item) => {
        const row = node("div", "list-row");
        append(row, [
          append(node("div"), [node("strong", null, `${item.player} (${item.position})`), node("div", "subtle", `${item.originalTeam || "-"} | deadline ${shortDate(item.claimDeadline)}`)]),
          node("span", "grade", item.status),
        ]);
        list.append(row);
      });
      waiverPanel.append(list);
    }
    root.append(waiverPanel);
    refs.mainView.replaceChildren(root);
  }

  function transactionRow(item) {
    const row = node("div", "transaction-row");
    const left = node("div");
    append(left, [
      node("strong", null, item.type || "Transaction"),
      node("div", "subtle", item.description || [item.player, item.team].filter(Boolean).join(" | ")),
    ]);
    const right = node("div");
    append(right, [
      node("span", "grade", item.team || item.category || "-"),
      node("div", Number(item.capDeltaCurrent) < 0 ? "subtle positive" : "subtle", item.capDeltaCurrent ? money(item.capDeltaCurrent) : shortDate(item.date)),
    ]);
    append(row, [left, right]);
    return row;
  }

  function renderTransactions(team) {
    const root = document.createDocumentFragment();
    const teamPanel = panel("Team Transactions", team.abbr);
    const teamList = node("div", "list-stack");
    (team.transactions || []).slice(0, 80).forEach((item) => teamList.append(transactionRow(item)));
    teamPanel.append(teamList.children.length ? teamList : node("div", "empty-state", "No team transactions logged yet."));
    root.append(teamPanel);

    const leaguePanel = panel("League Log", "Latest");
    const leagueList = node("div", "list-stack");
    (((data.league && data.league.transactions) || [])).slice(0, 80).forEach((item) => leagueList.append(transactionRow(item)));
    leaguePanel.append(leagueList);
    root.append(leaguePanel);
    refs.mainView.replaceChildren(root);
  }

  function renderPlayerPanel(team) {
    const player = selectDefaultPlayer(team);
    refs.playerPanel.replaceChildren();
    if (!player) {
      refs.playerPanel.append(node("div", "empty-state", "No player selected."));
      return;
    }

    const photo = playerAvatar(player, "panel-photo");
    const title = node("h2", null, player.name);
    const meta = node("div", "panel-meta", `${player.position} | ${player.jersey} | ${player.role.label}`);

    const facts = node("div", "player-facts");
    [
      ["Age", player.age || "-"],
      ["Exp", player.exp],
      ["Cap Hit", money(player.contract.capHit)],
      ["Through", player.contract.through || "-"],
      ["AAV", money(player.contract.aav)],
      ["Status", player.status],
    ].forEach(([label, value]) => {
      const fact = node("div", "fact");
      append(fact, [node("span", null, label), node("strong", null, value)]);
      facts.append(fact);
    });

    const role = node("div", "panel-role");
    append(role, [
      append(node("div", "list-row"), [
        append(node("div"), [node("strong", null, "Role Read"), node("div", "subtle", player.role.label)]),
        node("span", "grade", player.role.grade),
      ]),
      roleBar(player.role.value, 100),
    ]);

    const profileLink = node("a", "ghost-link", "Full Profile");
    profileLink.href = playerProfileHref(player, team.abbr);

    append(refs.playerPanel, [photo, title, meta, facts, role, profileLink]);
  }

  function renderMain(team) {
    if (state.view === "roster") {
      renderRoster(team);
    } else if (state.view === "depth") {
      renderDepth(team);
    } else if (state.view === "cap") {
      renderCap(team);
    } else if (state.view === "draft") {
      renderDraft(team);
    } else if (state.view === "freeAgents") {
      renderFreeAgents(team);
    } else if (state.view === "schedule") {
      renderSchedule(team);
    } else if (state.view === "coaches") {
      renderCoaches(team);
    } else if (state.view === "league") {
      renderLeague();
    } else if (state.view === "transactions") {
      renderTransactions(team);
    } else {
      renderOverview(team);
    }
  }

  function render() {
    const team = currentTeam();
    if (!team) {
      return;
    }
    refs.seasonLabel.textContent = String(data.season);
    renderTeams();
    renderHeader(team);
    selectDefaultPlayer(team);
    renderMain(team);
    renderPlayerPanel(team);
  }

  refs.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.view = tab.dataset.view;
      render();
    });
  });

  render();
}());
