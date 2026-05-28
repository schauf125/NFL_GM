(function () {
  let data = window.PLAYER_PROFILE_DATA || { season: 2026, players: [] };
  let players = data.players || [];
  const playerDetails = new Map(players.map((player) => [String(playerId(player)), player]));
  const state = {
    selectedId: null,
    view: "overview",
    query: "",
    team: "ALL",
    position: "ALL",
    liveGeneratedAt: data.generatedAt || null,
    loadingDetails: false,
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
    backButton: document.getElementById("backButton"),
    railBackButton: document.getElementById("railBackButton"),
  };

  const ATTRIBUTE_GROUPS_BY_POSITION = {
    QB: new Set(["universal", "passer", "ball_carrier"]),
    RB: new Set(["universal", "ball_carrier", "receiver", "blocker"]),
    FB: new Set(["universal", "ball_carrier", "receiver", "blocker"]),
    WR: new Set(["universal", "receiver", "ball_carrier", "blocker"]),
    TE: new Set(["universal", "receiver", "blocker", "ball_carrier"]),
    OT: new Set(["universal", "blocker"]),
    OG: new Set(["universal", "blocker"]),
    C: new Set(["universal", "blocker"]),
    IDL: new Set(["universal", "pass_rusher", "run_defender", "tackler"]),
    DT: new Set(["universal", "pass_rusher", "run_defender", "tackler"]),
    DE: new Set(["universal", "pass_rusher", "run_defender", "tackler"]),
    NT: new Set(["universal", "pass_rusher", "run_defender", "tackler"]),
    EDGE: new Set(["universal", "pass_rusher", "run_defender", "tackler"]),
    LB: new Set(["universal", "run_defender", "coverage", "tackler", "pass_rusher"]),
    ILB: new Set(["universal", "run_defender", "coverage", "tackler", "pass_rusher"]),
    OLB: new Set(["universal", "run_defender", "coverage", "tackler", "pass_rusher"]),
    CB: new Set(["universal", "coverage", "tackler"]),
    NB: new Set(["universal", "coverage", "tackler"]),
    S: new Set(["universal", "coverage", "tackler", "run_defender"]),
    FS: new Set(["universal", "coverage", "tackler", "run_defender"]),
    SS: new Set(["universal", "coverage", "tackler", "run_defender"]),
    K: new Set(["universal", "specialist"]),
    P: new Set(["universal", "specialist"]),
    LS: new Set(["universal", "specialist", "tackler"]),
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

  function shortDateTime(value) {
    if (!value) {
      return "";
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function pct(value) {
    if (value === null || value === undefined) {
      return "-";
    }
    return `${Number(value).toFixed(1)}%`;
  }

  function rate(numerator, denominator) {
    const bottom = Number(denominator || 0);
    if (bottom <= 0) {
      return "-";
    }
    return (Number(numerator || 0) / bottom).toFixed(1);
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
    return playerDetails.get(String(state.selectedId)) || players.find((player) => samePlayerId(playerId(player), state.selectedId)) || players[0];
  }

  function norm(value) {
    return String(value || "").trim().toLowerCase();
  }

  function playerTeam(player) {
    return player?.team?.abbr || "";
  }

  function playerMatchesIdentityHints(player, hints) {
    if (!player || !hints.name) {
      return false;
    }
    return norm(player.name) === norm(hints.name)
      && (!hints.team || norm(playerTeam(player)) === norm(hints.team))
      && (!hints.position || norm(player.position) === norm(hints.position));
  }

  function identityHintsFromUrl() {
    const params = new URLSearchParams(window.location.search);
    return {
      name: params.get("name") || "",
      team: params.get("team") || "",
      position: params.get("position") || params.get("pos") || "",
    };
  }

  function currentPageHref() {
    return `${window.location.pathname}${window.location.search}${window.location.hash}`;
  }

  function safeLocalHref(value) {
    if (!value) return "";
    try {
      const url = new URL(value, window.location.href);
      if (url.origin !== window.location.origin) return "";
      const target = `${url.pathname}${url.search}${url.hash}`;
      return target === currentPageHref() ? "" : target;
    } catch (_error) {
      return "";
    }
  }

  function explicitReturnHref() {
    const params = new URLSearchParams(window.location.search);
    return safeLocalHref(params.get("returnTo") || params.get("return") || "");
  }

  function referrerReturnHref() {
    return safeLocalHref(document.referrer || "");
  }

  function hintedPlayerUrl(basePath, player) {
    const url = new URL(basePath, window.location.href);
    url.searchParams.set("player", playerId(player));
    url.searchParams.set("name", player.name || "");
    url.searchParams.set("team", playerTeam(player));
    url.searchParams.set("position", player.position || "");
    url.searchParams.set("returnTo", currentPageHref());
    return `${url.pathname}${url.search}`;
  }

  function replacePlayerUrl(player) {
    const url = new URL(window.location.href);
    url.searchParams.set("player", playerId(player));
    url.searchParams.set("name", player.name || "");
    url.searchParams.set("team", playerTeam(player));
    url.searchParams.set("position", player.position || "");
    window.history.replaceState({}, "", url);
  }

  function resolveRequestedPlayer(requested, hints) {
    const requestedPlayer = requested
      ? players.find((player) => samePlayerId(playerId(player), requested))
      : null;
    const hintedPlayer = hints.name
      ? players.find((player) => playerMatchesIdentityHints(player, hints))
      : null;
    if (requestedPlayer && (!hints.name || playerMatchesIdentityHints(requestedPlayer, hints))) {
      return requestedPlayer;
    }
    return hintedPlayer || requestedPlayer;
  }

  function productionScore(player) {
    const career = player.career || {};
    const seasons = player.seasonStats || [];
    const statScore = seasons.reduce((total, row) => {
      return total
        + (Number(row.games) || 0) * 4
        + (Number(row.receiving_yards) || 0) * 0.04
        + (Number(row.rushing_yards) || 0) * 0.04
        + (Number(row.passing_yards) || 0) * 0.012
        + (Number(row.def_sacks) || 0) * 4
        + (Number(row.def_interceptions) || 0) * 5;
    }, 0);
    return statScore + (Number(career.career_games) || 0) * 8 + (Number(career.total_tds) || 0) * 5;
  }

  function roleBar(value, scale) {
    const raw = Number(value || 0);
    const normalized = scale ? (raw / scale) * 100 : raw;
    const bar = node("div", "skill-bar");
    bar.classList.add(ratingTierClass(normalized));
    applyRatingColor(bar, normalized, "--skill-color", "--skill-glow");
    const width = ratingScalePercent(normalized);
    bar.style.setProperty("--rating", `${width}%`);
    return bar;
  }

  const RATING_COLOR_STOPS = [
    { value: 0, color: [194, 57, 70] },
    { value: 50, color: [224, 108, 47] },
    { value: 62, color: [224, 169, 52] },
    { value: 72, color: [212, 198, 74] },
    { value: 82, color: [80, 185, 111] },
    { value: 88, color: [31, 183, 166] },
    { value: 94, color: [79, 141, 247] },
    { value: 100, color: [96, 166, 255] },
  ];

  function ratingColor(value) {
    const rating = Math.max(0, Math.min(100, Number(value) || 0));
    let lower = RATING_COLOR_STOPS[0];
    let upper = RATING_COLOR_STOPS[RATING_COLOR_STOPS.length - 1];
    for (let index = 1; index < RATING_COLOR_STOPS.length; index += 1) {
      if (rating <= RATING_COLOR_STOPS[index].value) {
        lower = RATING_COLOR_STOPS[index - 1];
        upper = RATING_COLOR_STOPS[index];
        break;
      }
    }
    const span = Math.max(1, upper.value - lower.value);
    const mix = (rating - lower.value) / span;
    const rgb = lower.color.map((channel, index) => Math.round(channel + (upper.color[index] - channel) * mix));
    return { solid: `rgb(${rgb.join(", ")})`, glow: `rgba(${rgb.join(", ")}, 0.28)` };
  }

  function applyRatingColor(element, value, colorVar, glowVar) {
    const color = ratingColor(value);
    element.style.setProperty(colorVar, color.solid);
    element.style.setProperty(glowVar, color.glow);
  }

  function ratingScalePercent(value) {
    const rating = Number(value);
    if (!Number.isFinite(rating)) return 6;
    const floor = 45;
    const ceiling = 99;
    const zoomed = ((rating - floor) / (ceiling - floor)) * 100;
    return Math.max(6, Math.min(100, zoomed));
  }

  function ratingTierClass(value) {
    const rating = Number(value);
    if (!Number.isFinite(rating)) return "rating-unknown";
    if (rating >= 94) return "rating-elite";
    if (rating >= 88) return "rating-excellent";
    if (rating >= 82) return "rating-great";
    if (rating >= 74) return "rating-good";
    if (rating >= 66) return "rating-solid";
    if (rating >= 58) return "rating-developing";
    if (rating >= 50) return "rating-raw";
    return "rating-concern";
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
    refs.seasonLabel.textContent = `${data.season || ""}`;
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
    refs.teamFilter.value = state.team;
    refs.positionFilter.value = state.position;
  }

  function renderPlayerList() {
    const sorted = players
      .filter(playerMatches)
      .sort((a, b) => {
        const roleDiff = (bestRole(b).value || 0) - (bestRole(a).value || 0);
        if (Math.abs(roleDiff) > 3) {
          return roleDiff;
        }
        const productionDiff = productionScore(b) - productionScore(a);
        if (productionDiff !== 0) {
          return productionDiff;
        }
        return a.name.localeCompare(b.name) || a.team.abbr.localeCompare(b.team.abbr);
      })
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
        replacePlayerUrl(player);
        loadSelectedPlayer(state.selectedId, { force: true }).finally(() => {
          renderFilters();
          render();
        });
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
      refs.cardViewLink.href = hintedPlayerUrl("../player_card/index.html", player);
    }
    refs.heroFacts.replaceChildren();
    [
      player.profile.jersey,
      player.profile.age === "--" ? null : `Age ${player.profile.age}`,
      player.profile.height,
      player.profile.weight,
      player.profile.experience,
      currentRead(player),
      player.evaluation?.confidenceLabel ? `${player.evaluation.confidenceLabel} eval` : null,
      player.profile.status,
    ].forEach((value) => {
      if (value && value !== "--") {
        refs.heroFacts.append(node("span", "chip", value));
      }
    });
    refs.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.view === state.view));
  }

  function topRatings(player, count) {
    return relevantRatings(player).sort((a, b) => b.value - a.value).slice(0, count);
  }

  function weakRatings(player, count) {
    return relevantRatings(player).sort((a, b) => a.value - b.value).slice(0, count);
  }

  function relevantRatings(player) {
    const allowed = attributeGroupsForPosition(player?.position);
    if (!allowed) return [...(player?.ratings || [])];
    return (player?.ratings || []).filter((rating) => allowed.has(String(rating.group || "")));
  }

  function attributeGroupsForPosition(position) {
    const key = String(position || "").toUpperCase();
    if (ATTRIBUTE_GROUPS_BY_POSITION[key]) {
      return ATTRIBUTE_GROUPS_BY_POSITION[key];
    }
    if (["DL", "DT", "DE", "NT"].includes(key)) {
      return ATTRIBUTE_GROUPS_BY_POSITION.IDL;
    }
    if (key.endsWith("LB")) {
      return ATTRIBUTE_GROUPS_BY_POSITION.LB;
    }
    if (["DB", "CB", "NB"].includes(key)) {
      return ATTRIBUTE_GROUPS_BY_POSITION.CB;
    }
    if (["SAF", "FS", "SS"].includes(key)) {
      return ATTRIBUTE_GROUPS_BY_POSITION.S;
    }
    if (["OL", "LT", "RT", "LG", "RG", "G"].includes(key)) {
      return ATTRIBUTE_GROUPS_BY_POSITION.OG;
    }
    return null;
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
      const label = flexLabel(item.position);
      const gradeText = item.potentialHidden ? `${item.current}/?` : `${item.current}/${item.potential}`;
      const note = item.potentialHidden ? "Potential hidden" : item.primary ? "Primary position" : item.notes || "Secondary fit";
      const name = append(node("div"), [node("strong", null, label), node("div", "subtle", note)]);
      append(row, [name, roleBar(item.current, 10), node("span", "grade", gradeText)]);
      stack.append(row);
    });
    return stack;
  }

  function flexLabel(position) {
    const key = String(position || "").toUpperCase();
    const labels = {
      GUN: "Gunner",
      PR: "Punt Return",
      KR: "Kick Return",
      ST: "General ST",
    };
    return labels[key] || position;
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
      metric("Evaluation", player.evaluation?.confidenceLabel || "Cloudy", player.evaluation?.confidenceNote || "Limited pro evidence."),
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

    const panelRoot = panel("Attributes", "Position-Relevant View");
    const wrap = node("div", "attributes-wrap");
    const groups = {};
    relevantRatings(player).forEach((rating) => {
      if (!groups[rating.group]) {
        groups[rating.group] = [];
      }
      groups[rating.group].push(rating);
    });

    const groupKeys = Object.keys(groups)
      .sort((a, b) => (groups[a][0].groupOrder || 99) - (groups[b][0].groupOrder || 99));
    if (!groupKeys.length) {
      wrap.append(node("div", "empty-state", "No position-relevant attributes are available."));
    }
    groupKeys.forEach((groupKey) => {
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

  function statsColumns(player, rows = []) {
    const pos = player.position;
    const hasReturns = rows.some((row) => Number(row.kickoff_returns || 0) > 0 || Number(row.punt_returns || 0) > 0);
    const returnColumns = hasReturns
      ? [["kickoff_returns", "KR"], ["kickoff_return_yards", "KR Yds"], ["kr_avg", "KR Avg"], ["punt_returns", "PR"], ["punt_return_yards", "PR Yds"], ["pr_avg", "PR Avg"]]
      : [];
    let columns;
    if (pos === "QB") {
      columns = [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["completions", "Cmp"], ["passing_attempts", "Att"], ["passing_yards", "Yds"], ["passing_tds", "TD"], ["passing_interceptions", "INT"], ["sacks_suffered", "Sck"], ["rushing_yards", "Rush"], ["rushing_tds", "RuTD"]];
      return columns;
    }
    if (["RB", "FB"].includes(pos)) {
      columns = [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["carries", "Car"], ["rushing_yards", "Rush"], ["ypc", "YPC"], ["rushing_tds", "TD"], ["receptions", "Rec"], ["targets", "Tgt"], ["receiving_yards", "Rec Yds"], ["receiving_tds", "Rec TD"]];
      return columns.concat(returnColumns);
    }
    if (["WR", "TE"].includes(pos)) {
      columns = [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["receptions", "Rec"], ["targets", "Tgt"], ["receiving_yards", "Yds"], ["ypr", "Avg"], ["receiving_tds", "TD"], ["carries", "Car"], ["rushing_yards", "Rush"]];
      return columns.concat(returnColumns);
    }
    if (["K", "P"].includes(pos)) {
      return [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["fg_made", "FGM"], ["fg_att", "FGA"], ["fg_pct", "FG%"], ["pat_made", "XPM"], ["pat_att", "XPA"]];
    }
    columns = [["season", "Year"], ["stat_team", "Team"], ["games", "G"], ["def_tackles_solo", "Solo"], ["def_tackles_with_assist", "Ast"], ["def_sacks", "Sck"], ["def_qb_hits", "QB Hit"], ["def_interceptions", "INT"], ["def_pass_defended", "PD"]];
    return columns.concat(returnColumns);
  }

  function renderStats(player) {
    const root = document.createDocumentFragment();
    const career = player.career || {};
    const careerPanel = panel("Career Totals", `${fmt(career.teams_played_for || player.team.abbr)}`);
    const metrics = node("section", "metric-grid");
    const statBits = careerMetricSet(player, career);
    statBits.forEach(([label, value, note]) => metrics.append(metric(label, fmt(value), note)));
    careerPanel.append(metrics);
    root.append(careerPanel);

    const seasonPanel = panel("Year By Year", "Regular Season");
    if (!player.seasonStats || !player.seasonStats.length) {
      seasonPanel.append(node("div", "empty-state", "No season stat rows yet."));
    } else {
      seasonPanel.append(statTable(player.seasonStats, statsColumns(player, player.seasonStats)));
    }
    root.append(seasonPanel);
    refs.view.replaceChildren(root);
  }

  function careerMetricSet(player, career) {
    const returns = returnMetricSet(career);
    if (player.position === "QB") {
      return [["Games", career.career_games], ["Pass Yards", career.passing_yards], ["Pass TD", career.passing_tds], ["INT", career.passing_interceptions], ["Rush Yards", career.rushing_yards], ["Rush TD", career.rushing_tds], ["Sacks Taken", career.sacks_suffered], ["PPR", career.fantasy_points_ppr]];
    }
    if (["RB", "FB", "WR", "TE"].includes(player.position)) {
      return [["Games", career.career_games], ["Rush Yards", career.rushing_yards], ["Rush TD", career.rushing_tds], ["Receptions", career.receptions], ["Targets", career.targets], ["Rec Yards", career.receiving_yards], ["Rec TD", career.receiving_tds], ["Scrimmage", career.scrimmage_yards]].concat(returns);
    }
    if (["K", "P"].includes(player.position)) {
      return [["Games", career.career_games], ["FG Made", career.fg_made], ["FG Att", career.fg_att], ["FG%", pct(career.fg_pct)], ["FG Long", career.fg_long], ["PAT Made", career.pat_made], ["PAT Att", career.pat_att], ["PAT%", pct(career.pat_pct)]];
    }
    return [["Games", career.career_games], ["Solo", career.def_tackles_solo], ["Combined", career.def_tackles_combined], ["TFL", career.def_tackles_for_loss], ["Sacks", career.def_sacks], ["QB Hits", career.def_qb_hits], ["INT", career.def_interceptions], ["PD", career.def_pass_defended]].concat(returns);
  }

  function returnMetricSet(career) {
    const kickReturns = Number(career.kickoff_returns || 0);
    const puntReturns = Number(career.punt_returns || 0);
    if (!kickReturns && !puntReturns) return [];
    return [
      ["Kick Returns", kickReturns],
      ["KR Yards", career.kickoff_return_yards],
      ["Punt Returns", puntReturns],
      ["PR Yards", career.punt_return_yards],
    ];
  }

  function renderMedical(player) {
    const root = document.createDocumentFragment();
    const medical = player.medical || { active: [], history: [], bodyRisk: [], recentEvents: [] };

    const activePanel = panel("Current Availability", `${(medical.active || []).length} active`);
    if (!medical.active || !medical.active.length) {
      activePanel.append(node("div", "empty-state", "No active injury designation."));
    } else {
      activePanel.append(statTable(medical.active, [
        ["injury", "Injury"],
        ["bodyPart", "Area"],
        ["severity", "Severity"],
        ["status", "Status"],
        ["startDate", "Start"],
        ["returnEarliestDate", "Earliest Return"],
        ["expectedGames", "G"],
      ]));
    }
    root.append(activePanel);

    const recentPanel = panel("Recent Injury Notes", `${(medical.recentEvents || []).length} notes`);
    if (!medical.recentEvents || !medical.recentEvents.length) {
      recentPanel.append(node("div", "empty-state", "No recent game or practice injury notes."));
    } else {
      recentPanel.append(statTable(medical.recentEvents, [
        ["date", "Date"],
        ["week", "Wk"],
        ["source", "Source"],
        ["injury", "Injury"],
        ["bodyPart", "Area"],
        ["status", "Status"],
        ["expectedGames", "G"],
        ["description", "Note"],
      ]));
    }
    root.append(recentPanel);

    const riskPanel = panel("Body Area Risk", `${(medical.bodyRisk || []).length} areas`);
    if (!medical.bodyRisk || !medical.bodyRisk.length) {
      riskPanel.append(node("div", "empty-state", "No injury history logged."));
    } else {
      riskPanel.append(statTable(medical.bodyRisk, [
        ["bodyPart", "Area"],
        ["injuryCount", "Inj"],
        ["majorCount", "Major"],
        ["gamesMissed", "Games"],
        ["recurrenceRisk", "Recurrence"],
        ["lastInjuryDate", "Last"],
        ["activeStatus", "Active"],
      ]));
    }
    root.append(riskPanel);

    const historyPanel = panel("Injury History", `${(medical.history || []).length} rows`);
    if (!medical.history || !medical.history.length) {
      historyPanel.append(node("div", "empty-state", "No prior injury rows found."));
    } else {
      historyPanel.append(statTable(medical.history, [
        ["startDate", "Date"],
        ["injury", "Injury"],
        ["bodyPart", "Area"],
        ["severity", "Severity"],
        ["gamesMissed", "Games"],
        ["recurrenceRisk", "Recurrence"],
        ["source", "Source"],
      ]));
    }
    root.append(historyPanel);
    refs.view.replaceChildren(root);
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
        let value = fmt(row[key]);
        if (key.endsWith("pct") && row[key] !== null && row[key] !== undefined) {
          value = pct(row[key]);
        } else if (key === "ypc") {
          value = rate(row.rushing_yards, row.carries);
        } else if (key === "ypr") {
          value = rate(row.receiving_yards, row.receptions);
        } else if (key === "kr_avg") {
          value = rate(row.kickoff_return_yards, row.kickoff_returns);
        } else if (key === "pr_avg") {
          value = rate(row.punt_return_yards, row.punt_returns);
        }
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
      const contractRange = `${contract.startYear || "-"}-${contract.endYear || "-"}`;
      const hero = node("section", "contract-hero");
      append(hero, [
        contractSummaryItem("Cap Hit", money(contract.capHit), String(contract.season || "Current")),
        contractSummaryItem("Total Value", money(contract.totalValue), contractRange),
        contractSummaryItem("Guaranteed", money(contract.guaranteedSalary), "current year"),
        contractSummaryItem("Dead Cap", money(contract.deadPreJune1), "pre-June 1"),
      ]);
      contractPanel.append(hero);

      const metrics = node("section", "metric-grid contract-detail-grid");
      [
        ["Cash Due", money(contract.cashDue), "current year"],
        ["AAV", money(contract.aav), `${contract.startYear || "-"}-${contract.endYear || "-"}`],
        ["Base Salary", money(contract.baseSalary), ""],
        ["Signing Proration", money(contract.signingBonusProration), ""],
      ].forEach(([label, value, note]) => metrics.append(metric(label, value, note)));
      contractPanel.append(metrics);
      if (contract.notes) {
        contractPanel.append(node("p", "summary-text", contract.notes));
      }
      const years = contract.years || [];
      if (years.length) {
        const breakdown = node("section", "contract-breakdown");
        append(breakdown, [
          node("h3", null, "Year By Year"),
          contractYearTable(years, contract.season),
        ]);
        contractPanel.append(breakdown);
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

  function contractSummaryItem(label, value, note) {
    const item = node("article", "contract-summary-item");
    append(item, [
      node("span", null, label),
      node("strong", null, value),
      node("small", null, note || ""),
    ]);
    return item;
  }

  function contractYearTable(years, currentSeason) {
    const wrap = node("div", "table-wrap contract-year-table-wrap");
    const table = node("table", "data-table contract-year-table");
    const thead = node("thead");
    const headRow = node("tr");
    [
      ["Year", ""],
      ["Base", "numeric"],
      ["Bonus", "numeric"],
      ["Guaranteed", "numeric"],
      ["Cash", "numeric"],
      ["Cap Hit", "numeric"],
      ["Dead Cap", "numeric"],
      ["Status", ""],
    ].forEach(([label, className]) => headRow.append(node("th", className, label)));
    thead.append(headRow);
    const tbody = node("tbody");
    years.forEach((year) => {
      const tr = node("tr", Number(year.season) === Number(currentSeason) ? "current-contract-year" : "");
      const bonus = Number(year.signingBonusProration || 0)
        + Number(year.rosterBonus || 0)
        + Number(year.workoutBonus || 0)
        + Number(year.optionBonusProration || 0)
        + Number(year.otherBonus || 0);
      const notes = [];
      if (Number(year.season) === Number(currentSeason)) notes.push("Current");
      if (year.optionYear) notes.push(year.optionExercised ? "Option exercised" : "Option");
      if (year.voidYear) notes.push("Void");
      if (!year.active) notes.push("Inactive");
      const seasonCell = node("td");
      seasonCell.append(year.season ? seasonBadge(year.season, Number(year.season) === Number(currentSeason)) : "-");
      const statusCell = node("td");
      statusCell.append(statusPills(notes));
      append(tr, [
        seasonCell,
        node("td", "numeric", money(year.baseSalary)),
        node("td", "numeric", money(bonus)),
        node("td", "numeric", money(year.guaranteedSalary)),
        node("td", "numeric", money(year.cashDue)),
        node("td", "numeric strong-money", money(year.capHit)),
        node("td", "numeric", money(year.deadPreJune1)),
        statusCell,
      ]);
      tbody.append(tr);
    });
    table.append(thead, tbody);
    wrap.append(table);
    return wrap;
  }

  function seasonBadge(season, current) {
    const badge = node("span", current ? "season-badge current" : "season-badge", String(season));
    return badge;
  }

  function statusPills(notes) {
    const wrap = node("div", "contract-status-pills");
    if (!notes.length) {
      wrap.append(node("span", "contract-status-pill muted", "Scheduled"));
      return wrap;
    }
    notes.forEach((note) => wrap.append(node("span", "contract-status-pill", note)));
    return wrap;
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
    } else if (state.view === "medical") {
      renderMedical(player);
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
    if (state.loadingDetails) {
      renderHeader(player);
      refs.view.replaceChildren(node("div", "empty-state", "Refreshing live player data."));
      return;
    }
    if (window.location.protocol.startsWith("http") && state.selectedId && !playerDetails.has(String(state.selectedId))) {
      renderHeader(player);
      refs.view.replaceChildren(node("div", "empty-state", "Loading player details."));
      loadSelectedPlayer(state.selectedId)
        .catch((error) => console.warn("Using list player profile data.", error))
        .finally(render);
      return;
    }
    renderHeader(player);
    renderMain(player);
  }

  function initFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("player") || params.get("player_id") || params.get("id");
    const hints = identityHintsFromUrl();
    const resolved = resolveRequestedPlayer(requested, hints);
    if (resolved) {
      state.selectedId = playerId(resolved);
      replacePlayerUrl(resolved);
      return;
    }
    const preferred = players.find((player) => player.name === "Justin Jefferson" && player.team.abbr === "MIN")
      || players.find((player) => player.name === "Patrick Mahomes")
      || players[0];
    state.selectedId = playerId(preferred);
    if (preferred) {
      replacePlayerUrl(preferred);
    }
  }

  async function loadLiveData() {
    if (!window.location.protocol.startsWith("http")) {
      return;
    }
    const response = await fetch("/api/player-search", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Player search API returned ${response.status}`);
    }
    data = await response.json();
    players = data.players || [];
    playerDetails.clear();
    state.liveGeneratedAt = data.generatedAt || null;
  }

  async function loadSelectedPlayer(id, options = {}) {
    if (!id || !window.location.protocol.startsWith("http") || (!options.force && playerDetails.has(String(id)))) {
      return;
    }
    state.loadingDetails = true;
    try {
      const response = await fetch(`/api/player-profile?id=${encodeURIComponent(id)}`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Player profile API returned ${response.status}`);
      }
      const payload = await response.json();
      const player = (payload.players || [])[0];
      state.liveGeneratedAt = payload.generatedAt || state.liveGeneratedAt;
      if (player) {
        playerDetails.set(String(playerId(player)), player);
        players = players.map((item) => (samePlayerId(playerId(item), playerId(player)) ? { ...item, ...player } : item));
        if (!players.some((item) => samePlayerId(playerId(item), playerId(player)))) {
          players = [player, ...players];
        }
      }
    } finally {
      state.loadingDetails = false;
    }
  }

  function bindEvents() {
    const goBack = () => {
      window.location.href = explicitReturnHref() || referrerReturnHref() || "../game_center/index.html";
    };
    refs.backButton?.addEventListener("click", goBack);
    refs.railBackButton?.addEventListener("click", goBack);
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
  }

  bindEvents();
  loadLiveData()
    .catch((error) => console.warn("Using bundled player profile data.", error))
    .finally(async () => {
      renderFilters();
      initFromQuery();
      await loadSelectedPlayer(state.selectedId, { force: true }).catch((error) => console.warn("Using list player profile data.", error));
      renderFilters();
      render();
    });
}());
