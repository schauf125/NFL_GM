(function () {
  let data = window.PLAYER_CARD_DATA || { players: [], playerCount: 0 };
  let players = data.players || [];
  const playerDetails = new Map(players.map((player) => [String(playerId(player)), player]));
  const state = {
    liveGeneratedAt: data.generatedAt || null,
    loadingDetails: false,
  };

  const elements = {
    card: document.getElementById("playerCard"),
    playerCount: document.getElementById("playerCount"),
    searchInput: document.getElementById("searchInput"),
    teamFilter: document.getElementById("teamFilter"),
    playerList: document.getElementById("playerList"),
    teamLogoHero: document.getElementById("teamLogoHero"),
    teamLogoSmall: document.getElementById("teamLogoSmall"),
    playerHeadshot: document.getElementById("playerHeadshot"),
    portraitInitials: document.getElementById("portraitInitials"),
    jerseyNumber: document.getElementById("jerseyNumber"),
    archetypeText: document.getElementById("archetypeText"),
    roleFit: document.getElementById("roleFit"),
    topTraits: document.getElementById("topTraits"),
    improveTraits: document.getElementById("improveTraits"),
    teamLine: document.getElementById("teamLine"),
    playerName: document.getElementById("playerName"),
    positionLine: document.getElementById("positionLine"),
    ageText: document.getElementById("ageText"),
    expText: document.getElementById("expText"),
    statusText: document.getElementById("statusText"),
    collegeText: document.getElementById("collegeText"),
    heightText: document.getElementById("heightText"),
    weightText: document.getElementById("weightText"),
    scoutingText: document.getElementById("scoutingText"),
    currentRead: document.getElementById("currentRead"),
    developmentRead: document.getElementById("developmentRead"),
    riskRead: document.getElementById("riskRead"),
    statsSeasonLabel: document.getElementById("statsSeasonLabel"),
    statsSnapshot: document.getElementById("statsSnapshot"),
    statsRecent: document.getElementById("statsRecent"),
    attributeBoard: document.getElementById("attributeBoard"),
    profileLink: document.querySelector('.module-links a[href*="player_profile"]'),
    backButton: document.getElementById("backButton"),
    railBackButton: document.getElementById("railBackButton"),
  };

  function playerId(player) {
    return player ? (player.id ?? player.player_id ?? player.playerId) : null;
  }

  function samePlayerId(left, right) {
    if (left === null || left === undefined || right === null || right === undefined || right === "") {
      return false;
    }
    return String(left) === String(right);
  }

  let selectedId = players[0] ? playerId(players[0]) : null;

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

  function hintedPlayerUrl(basePath, player) {
    const url = new URL(basePath, window.location.href);
    url.searchParams.set("player", playerId(player));
    url.searchParams.set("name", player.name || "");
    url.searchParams.set("team", playerTeam(player));
    url.searchParams.set("position", player.position || "");
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

  function setTeamTheme(player) {
    document.documentElement.style.setProperty("--team-primary", player.team.primary || "#75808f");
    document.documentElement.style.setProperty("--team-secondary", player.team.secondary || "#d6dde6");
  }

  function metricRow(metric) {
    const row = document.createElement("div");
    row.className = `metric-row ${ratingTierClass(metric.value)}`;
    applyRatingColor(row, metric.value);

    const name = document.createElement("div");
    name.className = "metric-name";
    name.textContent = metric.label;

    const bar = document.createElement("div");
    bar.className = "skill-bar";
    bar.style.setProperty("--rating", `${ratingScalePercent(metric.value)}%`);
    bar.setAttribute("aria-label", `${metric.label}: ${metric.grade}`);

    const grade = document.createElement("div");
    grade.className = "metric-grade";
    grade.textContent = metric.grade;

    row.append(name, bar, grade);
    return row;
  }

  function roleRow(role, label) {
    const row = document.createElement("div");
    row.className = `role-row ${ratingTierClass(role.value)}`;
    applyRatingColor(row, role.value);

    const name = document.createElement("div");
    name.className = "role-name";
    name.textContent = label ? `${label}: ${role.label}` : role.label;

    const bar = document.createElement("div");
    bar.className = "skill-bar";
    bar.style.setProperty("--rating", `${ratingScalePercent(role.value)}%`);
    bar.setAttribute("aria-label", `${role.label}: ${role.grade}`);

    const grade = document.createElement("div");
    grade.className = "role-grade";
    grade.textContent = role.grade;

    row.append(name, bar, grade);
    return row;
  }

  function ratingScalePercent(value) {
    const rating = Number(value);
    if (!Number.isFinite(rating)) return 6;
    const floor = 45;
    const ceiling = 99;
    const zoomed = ((rating - floor) / (ceiling - floor)) * 100;
    return Math.max(6, Math.min(100, zoomed));
  }

  const RATING_COLOR_STOPS = [
    { value: 0, color: [194, 57, 70] },
    { value: 50, color: [224, 108, 47] },
    { value: 62, color: [224, 169, 52] },
    { value: 72, color: [212, 198, 74] },
    { value: 82, color: [80, 185, 111] },
    { value: 90, color: [31, 183, 166] },
    { value: 97, color: [79, 141, 247] },
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

  function applyRatingColor(element, value) {
    const color = ratingColor(value);
    element.style.setProperty("--bar-color", color.solid);
    element.style.setProperty("--bar-glow", color.glow);
  }

  function ratingTierClass(value) {
    const rating = Number(value);
    if (!Number.isFinite(rating)) return "rating-unknown";
    if (rating >= 90) return "rating-elite";
    if (rating >= 82) return "rating-great";
    if (rating >= 74) return "rating-good";
    if (rating >= 66) return "rating-solid";
    if (rating >= 58) return "rating-developing";
    if (rating >= 50) return "rating-raw";
    return "rating-concern";
  }

  function renderMetricList(container, metrics, emptyText) {
    container.replaceChildren();
    if (!metrics || metrics.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = emptyText;
      container.append(empty);
      return;
    }
    metrics.forEach((metric) => container.append(metricRow(metric)));
  }

  function renderStats(player) {
    const stats = player.seasonStats || {};
    const season = stats.selectedSeason;
    elements.statsSeasonLabel.textContent = season
      ? `${season}${stats.isExportSeason ? "" : " latest"}${stats.selectedTeam ? ` | ${stats.selectedTeam}` : ""}${state.liveGeneratedAt ? ` | refreshed ${shortDateTime(state.liveGeneratedAt)}` : ""}`
      : "No stat line";

    elements.statsSnapshot.replaceChildren();
    const headline = stats.headline || [];
    if (headline.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No season stats are available yet.";
      elements.statsSnapshot.append(empty);
    } else {
      headline.forEach((item) => {
        const cell = document.createElement("div");
        cell.className = "stat-cell";
        const label = document.createElement("span");
        label.textContent = item.label;
        const value = document.createElement("strong");
        value.textContent = item.value;
        cell.append(label, value);
        elements.statsSnapshot.append(cell);
      });
    }

    elements.statsRecent.replaceChildren();
    (stats.recent || []).slice(0, 4).forEach((row) => {
      const item = document.createElement("div");
      item.className = "stat-season-row";
      const label = document.createElement("span");
      label.textContent = `${row.season} ${row.team || ""}`;
      const line = document.createElement("strong");
      line.textContent = (row.line || []).map((stat) => `${stat.value} ${stat.label}`).join(" | ");
      item.append(label, line);
      elements.statsRecent.append(item);
    });
  }

  function renderPlayer(player) {
    if (!player) {
      return;
    }

    setTeamTheme(player);

    elements.teamLogoHero.src = player.team.logo || "";
    elements.teamLogoHero.hidden = !player.team.logo;
    elements.teamLogoSmall.src = player.team.logo || "";
    elements.teamLogoSmall.hidden = !player.team.logo;
    elements.playerHeadshot.src = player.headshot || "";
    elements.playerHeadshot.hidden = !player.headshot;
    elements.card.classList.toggle("has-headshot", Boolean(player.headshot));
    elements.portraitInitials.textContent = player.initials;
    elements.jerseyNumber.textContent = player.profile.jersey;
    elements.archetypeText.textContent = player.role.label;

    elements.roleFit.replaceChildren(roleRow(player.role, "Primary"));
    if (player.secondaryRole) {
      elements.roleFit.append(roleRow(player.secondaryRole, "Secondary"));
    }

    renderMetricList(elements.topTraits, player.strengths, "No strengths graded yet.");
    renderMetricList(elements.improveTraits, player.improvements, "No development flags graded yet.");

    elements.teamLine.textContent = `${player.team.name} | ${player.team.abbr}`;
    elements.playerName.textContent = player.name;
    elements.positionLine.textContent = player.positionLabel;
    elements.ageText.textContent = player.profile.age;
    elements.expText.textContent = player.profile.experience;
    elements.statusText.textContent = player.profile.status;
    elements.collegeText.textContent = player.profile.college;
    elements.heightText.textContent = player.profile.height;
    elements.weightText.textContent = player.profile.weight;
    elements.scoutingText.textContent = player.scoutingReport;
    elements.currentRead.textContent = player.role.grade;
    elements.developmentRead.textContent = player.development;
    elements.riskRead.textContent = player.risk;
    if (elements.profileLink) {
      elements.profileLink.href = hintedPlayerUrl("../player_profile/index.html", player);
    }

    renderStats(player);
    renderMetricList(elements.attributeBoard, player.attributes, "No attribute board available.");

    Array.from(elements.playerList.querySelectorAll(".player-button")).forEach((button) => {
      button.classList.toggle("active", samePlayerId(button.dataset.playerId, playerId(player)));
    });
  }

  function playerMatches(player, query, team) {
    const searchBlob = `${player.name} ${player.position} ${player.positionLabel} ${player.team.abbr} ${player.team.name}`.toLowerCase();
    const queryMatch = !query || searchBlob.includes(query);
    const teamMatch = team === "ALL" || player.team.abbr === team;
    return queryMatch && teamMatch;
  }

  function statProductionScore(player) {
    const recent = (player.seasonStats && player.seasonStats.recent) || [];
    return recent.reduce((total, row) => {
      const lineScore = (row.line || []).reduce((lineTotal, item) => {
        const value = Number(String(item.value || "0").replace(/,/g, "")) || 0;
        if (["receiving_yards", "rushing_yards"].includes(item.key)) return lineTotal + value * 0.04;
        if (item.key === "passing_yards") return lineTotal + value * 0.012;
        if (["receiving_tds", "rushing_tds", "passing_tds", "def_interceptions"].includes(item.key)) return lineTotal + value * 5;
        if (item.key === "def_sacks") return lineTotal + value * 4;
        return lineTotal + value * 0.2;
      }, 0);
      return total + lineScore + (Number(row.games) || 0) * 4;
    }, 0);
  }

  function renderList() {
    const query = elements.searchInput.value.trim().toLowerCase();
    const team = elements.teamFilter.value || "ALL";
    const filtered = players
      .filter((player) => playerMatches(player, query, team))
      .sort((a, b) => {
        const roleDiff = (b.role.value || 0) - (a.role.value || 0);
        if (Math.abs(roleDiff) > 3) {
          return roleDiff;
        }
        const productionDiff = statProductionScore(b) - statProductionScore(a);
        if (productionDiff !== 0) {
          return productionDiff;
        }
        return a.name.localeCompare(b.name) || a.team.abbr.localeCompare(b.team.abbr);
      });
    elements.playerList.replaceChildren();

    if (filtered.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No matching players.";
      elements.playerList.append(empty);
      return;
    }

    filtered.slice(0, 180).forEach((player) => {
      const button = document.createElement("button");
      button.className = "player-button";
      button.type = "button";
      button.dataset.playerId = String(playerId(player));

      const identity = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = player.name;
      const meta = document.createElement("small");
      meta.textContent = `${player.position} | ${player.team.abbr}`;
      identity.append(name, meta);

      const grade = document.createElement("span");
      grade.textContent = player.role.grade;

      button.append(identity, grade);
      button.addEventListener("click", () => {
        selectedId = playerId(player);
        replacePlayerUrl(player);
        loadSelectedPlayer(selectedId, { force: true })
          .catch((error) => console.warn("Using list player card data.", error))
          .finally(() => {
            elements.playerCount.textContent = `${data.playerCount || players.length} players${state.liveGeneratedAt ? ` | refreshed ${shortDateTime(state.liveGeneratedAt)}` : ""}`;
            renderPlayer(playerDetails.get(String(selectedId)) || player);
          });
      });
      elements.playerList.append(button);
    });

    const selected = filtered.find((player) => samePlayerId(playerId(player), selectedId)) || filtered[0];
    selectedId = playerId(selected);
    if (state.loadingDetails) {
      elements.statsSnapshot.replaceChildren();
      const loading = document.createElement("p");
      loading.className = "empty-state";
      loading.textContent = "Refreshing live player data.";
      elements.statsSnapshot.append(loading);
      return;
    }
    if (window.location.protocol.startsWith("http") && selectedId && !playerDetails.has(String(selectedId))) {
      loadSelectedPlayer(selectedId)
        .catch((error) => console.warn("Using list player card data.", error))
        .finally(() => renderPlayer(playerDetails.get(String(selectedId)) || selected));
      return;
    }
    renderPlayer(playerDetails.get(String(selectedId)) || selected);
  }

  function populateTeams() {
    const teams = Array.from(new Set(players.map((player) => player.team.abbr))).sort();
    elements.teamFilter.replaceChildren();

    const all = document.createElement("option");
    all.value = "ALL";
    all.textContent = "All teams";
    elements.teamFilter.append(all);

    teams.forEach((team) => {
      const option = document.createElement("option");
      option.value = team;
      option.textContent = team;
      elements.teamFilter.append(option);
    });
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
    state.liveGeneratedAt = data.generatedAt || null;
    playerDetails.clear();
    players = (data.players || []).map((player) => ({
      ...player,
      role: player.role || (player.roles || [])[0] || { label: "Depth Role", value: 50, grade: "Raw" },
      secondaryRole: null,
      development: "-",
      risk: "-",
      strengths: [],
      improvements: [],
      attributes: [],
      scoutingReport: "",
      seasonStats: { selectedSeason: data.season, selectedTeam: player.team && player.team.abbr, headline: [], recent: [] },
      careerStats: null,
    }));
    selectedId = players[0] ? playerId(players[0]) : null;
  }

  async function loadSelectedPlayer(id, options = {}) {
    if (!id || !window.location.protocol.startsWith("http") || (!options.force && playerDetails.has(String(id)))) {
      return;
    }
    state.loadingDetails = true;
    try {
      const response = await fetch(`/api/player-card?id=${encodeURIComponent(id)}`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Player card API returned ${response.status}`);
      }
      const payload = await response.json();
      const player = (payload.players || [])[0];
      state.liveGeneratedAt = payload.generatedAt || state.liveGeneratedAt;
      if (player) {
        playerDetails.set(String(playerId(player)), player);
        players = players.map((item) => (samePlayerId(playerId(item), playerId(player)) ? {
          ...item,
          ...player,
        } : item));
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
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.href = "../game_center/index.html";
      }
    };
    elements.backButton?.addEventListener("click", goBack);
    elements.railBackButton?.addEventListener("click", goBack);
    elements.searchInput.addEventListener("input", renderList);
    elements.teamFilter.addEventListener("change", renderList);
  }

  async function init() {
    elements.playerCount.textContent = `${data.playerCount || players.length} players${state.liveGeneratedAt ? ` | refreshed ${shortDateTime(state.liveGeneratedAt)}` : ""}`;
    populateTeams();

    const params = new URLSearchParams(window.location.search);
    const requested = params.get("player") || params.get("player_id") || params.get("id");
    const hints = identityHintsFromUrl();
    const resolved = resolveRequestedPlayer(requested, hints);
    if (resolved) {
      selectedId = playerId(resolved);
      replacePlayerUrl(resolved);
    } else if (requested) {
      selectedId = requested;
    }
    const preferred = resolved
      || players.find((player) => player.name === "Patrick Mahomes" && player.team.abbr === "KC")
      || players.find((player) => player.name === "Justin Jefferson" && (player.role.value || 0) >= 82)
      || players.find((player) => (player.role.value || 0) >= 90)
      || players.find((player) => player.position === "QB")
      || players[0];
    if (!resolved && !requested && preferred) {
      selectedId = playerId(preferred);
      replacePlayerUrl(preferred);
    }
    await loadSelectedPlayer(selectedId, { force: true }).catch((error) => console.warn("Using list player card data.", error));
    populateTeams();
    elements.playerCount.textContent = `${data.playerCount || players.length} players${state.liveGeneratedAt ? ` | refreshed ${shortDateTime(state.liveGeneratedAt)}` : ""}`;
    renderList();
  }

  bindEvents();
  loadLiveData()
    .catch((error) => console.warn("Using bundled player card data.", error))
    .finally(init);
}());
