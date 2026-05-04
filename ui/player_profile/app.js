(function () {
  const data = window.PLAYER_PROFILE_DATA || { season: 2026, players: [] };
  const players = data.players || [];
  const state = {
    selectedId: null,
    view: "overview",
    query: "",
    team: "ALL",
    position: "ALL",
  };

  const refs = {
    seasonLabel: document.getElementById("seasonLabel"),
    searchInput: document.getElementById("searchInput"),
    teamFilter: document.getElementById("teamFilter"),
    positionFilter: document.getElementById("positionFilter"),
    playerList: document.getElementById("playerList"),
    heroLogo: document.getElementById("heroLogo"),
    heroPhoto: document.getElementById("heroPhoto"),
    teamLine: document.getElementById("teamLine"),
    playerName: document.getElementById("playerName"),
    positionLine: document.getElementById("positionLine"),
    heroFacts: document.getElementById("heroFacts"),
    tabs: Array.from(document.querySelectorAll(".profile-tabs button")),
    view: document.getElementById("profileView"),
    cardViewLink: document.getElementById("cardViewLink"),
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

  function money(value) {
    const amount = Number(value || 0);
    const sign = amount < 0 ? "-" : "";
    const absolute = Math.abs(amount);
    if (absolute >= 1000000) {
      return `${sign}$${(absolute / 1000000).toFixed(1)}M`;
    }
    if (absolute >= 1000) {
      return `${sign}$${Math.round(absolute / 1000)}K`;
    }
    return `${sign}$${absolute}`;
  }

  function fmt(value) {
    if (value === null || value === undefined || value === "") {
      return "-";
    }
    return String(value);
  }

  function pct(value) {
    if (value === null || value === undefined) {
      return "-";
    }
    return `${Number(value).toFixed(1)}%`;
  }

  function setTheme(player) {
    document.documentElement.style.setProperty("--team-primary", player.team.primary || "#75808f");
    document.documentElement.style.setProperty("--team-secondary", player.team.secondary || "#d6dde6");
  }

  function playerId(player) {
    return player ? (player.id ?? player.player_id ?? player.playerId) : null;
  }

  function samePlayerId(left, right) {
    if (left === null || left === undefined || right === null || right === undefined || right === "") {
      return false;
    }
    return String(left) === String(right);
  }

  function selectedPlayer() {
    return players.find((player) => samePlayerId(playerId(player), state.selectedId)) || players[0];
  }

  function roleBar(value, scale) {
    const bar = node("div", "skill-bar");
    const width = Math.max(0, Math.min(100, (Number(value || 0) / scale) * 100));
    bar.style.setProperty("--rating", `${width}%`);
    return bar;
  }

  function metric(label, value, note, tone) {
    const item = node("div", "metric");
    append(item, [node("span", "section-title", label), node("strong", tone || "", value), note ? node("small", null, note) : null]);
    return item;
  }

  function panel(title, kicker) {
    const section = node("section", "panel");
    const header = node("div", "panel-header");
    append(header, [node("h2", null, title), node("span", "panel-kicker", kicker || "")]);
    section.append(header);
    return section;
  }

  function playerMatches(player) {
    const query = state.query.trim().toLowerCase();
    const blob = `${player.name} ${player.position} ${player.positionLabel} ${player.team.abbr} ${player.team.name} ${player.profile.college}`.toLowerCase();
    return (!query || blob.includes(query))
      && (state.team === "ALL" || player.team.abbr === state.team)
      && (state.position === "ALL" || player.position === state.position);
  }

  function bestRole(player) {
    return (player.roles || [])[0] || { label: "Depth Role", value: 50, grade: "Raw" };
  }

  function currentRead(player) {
    return bestRole(player).grade;
  }

  function renderFilters() {
    refs.seasonLabel.textContent = String(data.season);
    const teams = Array.from(new Set(players.map((player) => player.team.abbr))).sort();
    const positions = Array.from(new Set(players.map((player) => player.position))).sort();

    refs.teamFilter.replaceChildren();
    const allTeams = node("option", null, "All teams");
    allTeams.value = "ALL";
    refs.teamFilter.append(allTeams);
    teams.forEach((team) => {
      const option = node("option", null, team);
      option.value = team;
      refs.teamFilter.append(option);
    });

    refs.positionFilter.replaceChildren();
    const allPositions = node("option", null, "All positions");
    allPositions.value = "ALL";
    refs.positionFilter.append(allPositions);
    positions.forEach((position) => {
      const option = node("option", null, position);
      option.value = position;
      refs.positionFilter.append(option);
    });
  }

  function renderPlayerList() {
    const sorted = players
      .filter(playerMatches)
      .sort((a, b) => (bestRole(b).value || 0) - (bestRole(a).value || 0) || a.name.localeCompare(b.name))
    let filtered = sorted.slice(0, 220);
    refs.playerList.replaceChildren();

    if (!sorted.length) {
      refs.playerList.append(node("div", "empty-state", "No players found."));
      return;
    }

    const selectedInFilteredSet = sorted.find((player) => samePlayerId(playerId(player), state.selectedId));
    if (!selectedInFilteredSet) {
      state.selectedId = playerId(sorted[0]);
    } else if (!filtered.some((player) => samePlayerId(playerId(player), state.selectedId))) {
      filtered = [selectedInFilteredSet, ...filtered];
    }

    filtered.forEach((player) => {
      const button = node("button", "player-button");
      button.type = "button";
      button.classList.toggle("active", samePlayerId(playerId(player), state.selectedId));
      const left = node("span");
      append(left, [node("strong", null, player.name), node("small", null, `${player.position} | ${player.team.abbr}`)]);
      append(button, [left, node("span", null, currentRead(player))]);
      button.addEventListener("click", () => {
        state.selectedId = playerId(player);
        const url = new URL(window.location.href);
        url.searchParams.set("player", state.selectedId);
        window.history.replaceState({}, "", url);
        render();
      });
      refs.playerList.append(button);
    });
  }

  function renderHeader(player) {
    setTheme(player);
    refs.heroLogo.src = player.team.logo || "";
    refs.heroLogo.hidden = !player.team.logo;
    refs.heroPhoto.replaceChildren();
    if (player.headshot) {
      const img = node("img");
      img.src = player.headshot;
      img.alt = "";
      refs.heroPhoto.append(img);
    } else {
      refs.heroPhoto.textContent = player.initials;
    }
    refs.teamLine.textContent = `${player.team.name} | ${player.team.abbr}`;
    refs.playerName.textContent = player.name;
    refs.positionLine.textContent = player.positionLabel;
    if (refs.cardViewLink) {
      refs.cardViewLink.href = `../player_card/index.html?player=${encodeURIComponent(playerId(player))}`;
    }
    refs.heroFacts.replaceChildren();
    [
      player.profile.jersey,
      player.profile.age === "--" ? null : `Age ${player.profile.age}`,
      player.profile.height,
      player.profile.weight,
      player.profile.experience,
      currentRead(player),
      player.profile.status,
    ].forEach((value) => {
      if (value && value !== "--") {
        refs.heroFacts.append(node("span", "chip", value));
      }
    });
    refs.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === state.view));
  }

  function topRatings(player, count) {
    return [...(player.ratings || [])].sort((a, b) => b.value - a.value).slice(0, count);
  }

  function weakRatings(player, count) {
    return [...(player.ratings || [])].sort((a, b) => a.value - b.value).slice(0, count);
  }

  function roleRows(roles) {
    const stack = node("div", "role-stack");
    (roles || []).slice(0, 6).forEach((role) => {
      const row = node("div", "role-row");
      append(row, [node("strong", null, role.label), roleBar(role.value, 100), node("span", "grade", role.grade)]);
      stack.append(row);
    });
    return stack;
  }

  function flexRows(flex) {
    const stack = node("div", "role-stack");
    if (!flex || !flex.length) {
      stack.append(node("div", "empty-state", "No position flex data."));
      return stack;
    }
    flex.forEach((item) => {
      const row = node("div", "flex-row");
      const name = append(node("div"), [node("strong", null, item.position), node("div", "subtle", item.primary ? "Primary position" : item.notes || "Secondary fit")]);
      append(row, [name, roleBar(item.current, 10), node("span", "grade", `${item.current}/${item.potential}`)]);
      stack.append(row);
    });
    return stack;
  }

  function renderOverview(player) {
    const root = document.createDocumentFragment();
    const career = player.career || {};
    const contract = player.contract;

    const summaryPanel = panel("Scouting Summary", "Profile");
    summaryPanel.append(node("p", "summary-text", player.summary));
    root.append(summaryPanel);

    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Current Read", currentRead(player), bestRole(player).label),
      metric("Career Games", fmt(career.career_games), `${fmt(career.first_season)}-${fmt(career.last_season)}`),
      metric("Cap Hit", contract ? money(contract.capHit) : "-", contract ? `through ${contract.endYear || "-"}` : "No active contract"),
      metric("Development", player.profile.devTrait, player.profile.isRookie ? "Rookie" : "Veteran"),
    ]);
    const metricsPanel = panel("Snapshot", player.position);
    metricsPanel.append(metrics);
    root.append(metricsPanel);

    const grid = node("div", "grid-row");
    const facts = panel("Information", player.team.abbr);
    const factGrid = node("div", "fact-grid");
    [
      ["College", player.profile.college],
      ["Height", player.profile.height],
      ["Weight", player.profile.weight],
      ["Experience", player.profile.experience],
      ["Teams Played For", career.teams_played_for || player.team.abbr],
      ["Status", player.profile.status],
    ].forEach(([label, value]) => {
      const fact = node("div", "fact");
      append(fact, [node("span", null, label), node("strong", null, value)]);
      factGrid.append(fact);
    });
    facts.append(factGrid);

    const roles = panel("Role Fit", "Scheme");
    roles.append(roleRows(player.roles));
    append(grid, [facts, roles]);
    root.append(grid);

    const traitGrid = node("div", "grid-row");
    const strengths = panel("Best Attributes", "Scouting");
    strengths.append(attributeMiniList(topRatings(player, 8)));
    const work = panel("Development Points", "Coaching");
    work.append(attributeMiniList(weakRatings(player, 8)));
    append(traitGrid, [strengths, work]);
    root.append(traitGrid);

    refs.view.replaceChildren(root);
  }

  function attributeMiniList(items) {
    const stack = node("div", "list-stack");
    items.forEach((rating) => {
      const row = node("div", "role-row");
      append(row, [node("strong", null, rating.label), roleBar(rating.value, 100), node("span", "grade", rating.grade)]);
      stack.append(row);
    });
    return stack;
  }

  function renderAttributes(player) {
    const root = document.createDocumentFragment();
    const flexPanel = panel("Position Flex", "Current / Potential");
    flexPanel.append(flexRows(player.flex));
    root.append(flexPanel);

    const panelRoot = panel("Attributes", "Full Scouting View");
    const wrap = node("div", "attributes-wrap");
    const groups = {};
    (player.ratings || []).forEach((rating) => {
      if (!groups[rating.group]) {
        groups[rating.group] = [];
      }
      groups[rating.group].push(rating);
    });

    Object.keys(groups)
      .sort((a, b) => (groups[a][0].groupOrder || 99) - (groups[b][0].groupOrder || 99))
      .forEach((groupKey) => {
        const card = node("section", "attribute-group");
        card.append(node("h3", null, groups[groupKey][0].groupLabel));
        groups[groupKey]
          .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
          .forEach((rating) => {
            const row = node("div", "attribute-row");
            append(row, [node("div", "attribute-name", rating.label), roleBar(rating.value, 100), node("span", "grade", rating.grade)]);
            card.append(row);
          });
        wrap.append(card);
      });
    panelRoot.append(wrap);
    root.append(panelRoot);
    refs.view.replaceChildren(root);
  }

  function statsColumns(player) {
    const pos = player.position;
    if (pos === "QB") {
      return [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["completions", "Cmp"], ["passing_attempts", "Att"], ["passing_yards", "Yds"], ["passing_tds", "TD"], ["passing_interceptions", "INT"], ["sacks_suffered", "Sck"], ["rushing_yards", "Rush"], ["rushing_tds", "RuTD"]];
    }
    if (["RB", "FB"].includes(pos)) {
      return [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["carries", "Car"], ["rushing_yards", "Rush"], ["rushing_tds", "TD"], ["receptions", "Rec"], ["targets", "Tgt"], ["receiving_yards", "Rec Yds"], ["receiving_tds", "Rec TD"]];
    }
    if (["WR", "TE"].includes(pos)) {
      return [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["receptions", "Rec"], ["targets", "Tgt"], ["receiving_yards", "Yds"], ["receiving_tds", "TD"], ["carries", "Car"], ["rushing_yards", "Rush"]];
    }
    if (["K", "P"].includes(pos)) {
      return [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["fg_made", "FGM"], ["fg_att", "FGA"], ["fg_pct", "FG%"], ["pat_made", "XPM"], ["pat_att", "XPA"]];
    }
    return [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["def_tackles_solo", "Solo"], ["def_tackles_with_assist", "Ast"], ["def_sacks", "Sck"], ["def_qb_hits", "QB Hit"], ["def_interceptions", "INT"], ["def_pass_defended", "PD"]];
  }

  function renderStats(player) {
    const root = document.createDocumentFragment();
    const career = player.career || {};
    const careerPanel = panel("Career Totals", fmt(career.teams_played_for || player.team.abbr));
    const metrics = node("section", "metric-grid");
    const statBits = careerMetricSet(player, career);
    statBits.forEach(([label, value, note]) => metrics.append(metric(label, fmt(value), note)));
    careerPanel.append(metrics);
    root.append(careerPanel);

    const seasonPanel = panel("Year By Year", "Regular Season");
    if (!player.seasonStats || !player.seasonStats.length) {
      seasonPanel.append(node("div", "empty-state", "No season stat rows imported yet."));
    } else {
      seasonPanel.append(statTable(player.seasonStats, statsColumns(player)));
    }
    root.append(seasonPanel);
    refs.view.replaceChildren(root);
  }

  function careerMetricSet(player, career) {
    if (player.position === "QB") {
      return [["Games", career.career_games], ["Pass Yards", career.passing_yards], ["Pass TD", career.passing_tds], ["INT", career.passing_interceptions], ["Rush Yards", career.rushing_yards], ["Rush TD", career.rushing_tds], ["Sacks Taken", career.sacks_suffered], ["PPR", career.fantasy_points_ppr]];
    }
    if (["RB", "FB", "WR", "TE"].includes(player.position)) {
      return [["Games", career.career_games], ["Rush Yards", career.rushing_yards], ["Rush TD", career.rushing_tds], ["Receptions", career.receptions], ["Targets", career.targets], ["Rec Yards", career.receiving_yards], ["Rec TD", career.receiving_tds], ["Scrimmage", career.scrimmage_yards]];
    }
    if (["K", "P"].includes(player.position)) {
      return [["Games", career.career_games], ["FG Made", career.fg_made], ["FG Att", career.fg_att], ["FG%", pct(career.fg_pct)], ["FG Long", career.fg_long], ["PAT Made", career.pat_made], ["PAT Att", career.pat_att], ["PAT%", pct(career.pat_pct)]];
    }
    return [["Games", career.career_games], ["Solo", career.def_tackles_solo], ["Combined", career.def_tackles_combined], ["TFL", career.def_tackles_for_loss], ["Sacks", career.def_sacks], ["QB Hits", career.def_qb_hits], ["INT", career.def_interceptions], ["PD", career.def_pass_defended]];
  }

  function statTable(rows, columns) {
    const wrap = node("div", "table-wrap");
    const table = node("table", "table");
    const thead = node("thead");
    const header = node("tr");
    columns.forEach(([, label]) => header.append(node("th", null, label)));
    thead.append(header);
    const tbody = node("tbody");
    rows.forEach((row) => {
      const tr = node("tr");
      columns.forEach(([key]) => {
        const value = key.endsWith("pct") && row[key] !== null && row[key] !== undefined ? pct(row[key]) : fmt(row[key]);
        tr.append(node("td", null, value));
      });
      tbody.append(tr);
    });
    append(table, [thead, tbody]);
    wrap.append(table);
    return wrap;
  }

  function renderContract(player) {
    const root = document.createDocumentFragment();
    const contract = player.contract;
    const freeAgency = player.freeAgency;
    const contractPanel = panel("Contract", contract ? contract.type : "No Active Contract");

    if (contract) {
      const metrics = node("section", "metric-grid");
      [
        ["Cap Hit", money(contract.capHit), String(contract.season)],
        ["Cash Due", money(contract.cashDue), "current year"],
        ["AAV", money(contract.aav), `${contract.startYear || "-"}-${contract.endYear || "-"}`],
        ["Dead Cap", money(contract.deadPreJune1), "pre-June 1"],
        ["Base Salary", money(contract.baseSalary), ""],
        ["Guarantee", money(contract.guaranteedSalary), ""],
        ["Signing Proration", money(contract.signingBonusProration), ""],
        ["Total Value", money(contract.totalValue), ""],
      ].forEach(([label, value, note]) => metrics.append(metric(label, value, note)));
      contractPanel.append(metrics);
      if (contract.notes) {
        contractPanel.append(node("p", "summary-text", contract.notes));
      }
    } else {
      contractPanel.append(node("div", "empty-state", "No current contract year found."));
    }
    root.append(contractPanel);

    if (freeAgency) {
      const faPanel = panel("Free Agent Market", freeAgency.marketTier || "Open");
      const metrics = node("section", "metric-grid");
      [
        ["Asking AAV", money(freeAgency.askingAav), `${freeAgency.preferredYears || "-"} years preferred`],
        ["Minimum AAV", money(freeAgency.minimumAav), `${freeAgency.guaranteePct || 0}% guarantee target`],
        ["Previous Team", freeAgency.previousTeam || "-", ""],
        ["Motivation", freeAgency.motivation || "-", ""],
      ].forEach(([label, value, note]) => metrics.append(metric(label, value, note)));
      faPanel.append(metrics);
      if (freeAgency.notes) {
        faPanel.append(node("p", "summary-text", freeAgency.notes));
      }
      root.append(faPanel);
    }

    refs.view.replaceChildren(root);
  }

  function renderHistory(player) {
    const root = document.createDocumentFragment();
    const historyPanel = panel("Transaction History", `${(player.transactions || []).length} rows`);
    if (!player.transactions || !player.transactions.length) {
      historyPanel.append(node("div", "empty-state", "No transaction history found."));
    } else {
      const stack = node("div", "list-stack");
      player.transactions.forEach((item) => {
        const row = node("div", "transaction-row");
        const left = append(node("div"), [node("strong", null, item.type), node("div", "subtle", item.description)]);
        const middle = append(node("div"), [node("strong", null, item.team || "-"), node("div", "subtle", item.date || "-")]);
        append(row, [left, middle, node("span", "grade", item.newStatus || item.category || "-")]);
        stack.append(row);
      });
      historyPanel.append(stack);
    }
    root.append(historyPanel);
    refs.view.replaceChildren(root);
  }

  function renderMain(player) {
    if (state.view === "attributes") {
      renderAttributes(player);
    } else if (state.view === "stats") {
      renderStats(player);
    } else if (state.view === "contract") {
      renderContract(player);
    } else if (state.view === "history") {
      renderHistory(player);
    } else {
      renderOverview(player);
    }
  }

  function render() {
    renderPlayerList();
    const player = selectedPlayer();
    if (!player) {
      return;
    }
    renderHeader(player);
    renderMain(player);
  }

  function initFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("player") || params.get("player_id") || params.get("id");
    const preferred = (requested ? players.find((player) => samePlayerId(playerId(player), requested)) : null)
      || players.find((player) => player.name === "Justin Jefferson" && player.team.abbr === "MIN")
      || players.find((player) => player.name === "Patrick Mahomes")
      || players[0];
    state.selectedId = playerId(preferred);
  }

  refs.searchInput.addEventListener("input", () => {
    state.query = refs.searchInput.value;
    render();
  });
  refs.teamFilter.addEventListener("change", () => {
    state.team = refs.teamFilter.value;
    render();
  });
  refs.positionFilter.addEventListener("change", () => {
    state.position = refs.positionFilter.value;
    render();
  });
  refs.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.view = tab.dataset.view;
      render();
    });
  });

  renderFilters();
  initFromQuery();
  render();
}());
