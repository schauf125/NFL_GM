(function () {
  const data = window.PLAYER_CARD_DATA || { players: [], playerCount: 0 };
  const players = data.players || [];

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
    attributeBoard: document.getElementById("attributeBoard"),
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

  function setTeamTheme(player) {
    document.documentElement.style.setProperty("--team-primary", player.team.primary || "#75808f");
    document.documentElement.style.setProperty("--team-secondary", player.team.secondary || "#d6dde6");
  }

  function metricRow(metric) {
    const row = document.createElement("div");
    row.className = "metric-row";

    const name = document.createElement("div");
    name.className = "metric-name";
    name.textContent = metric.label;

    const bar = document.createElement("div");
    bar.className = "skill-bar";
    bar.style.setProperty("--rating", `${Math.max(0, Math.min(100, metric.value))}%`);
    bar.setAttribute("aria-label", `${metric.label}: ${metric.grade}`);

    const grade = document.createElement("div");
    grade.className = "metric-grade";
    grade.textContent = metric.grade;

    row.append(name, bar, grade);
    return row;
  }

  function roleRow(role, label) {
    const row = document.createElement("div");
    row.className = "role-row";

    const name = document.createElement("div");
    name.className = "role-name";
    name.textContent = label ? `${label}: ${role.label}` : role.label;

    const bar = document.createElement("div");
    bar.className = "skill-bar";
    bar.style.setProperty("--rating", `${Math.max(0, Math.min(100, role.value))}%`);
    bar.setAttribute("aria-label", `${role.label}: ${role.grade}`);

    const grade = document.createElement("div");
    grade.className = "role-grade";
    grade.textContent = role.grade;

    row.append(name, bar, grade);
    return row;
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

  function renderList() {
    const query = elements.searchInput.value.trim().toLowerCase();
    const team = elements.teamFilter.value || "ALL";
    const filtered = players
      .filter((player) => playerMatches(player, query, team))
      .sort((a, b) => {
        const roleDiff = (b.role.value || 0) - (a.role.value || 0);
        if (roleDiff !== 0) {
          return roleDiff;
        }
        return a.name.localeCompare(b.name);
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
        const url = new URL(window.location.href);
        url.searchParams.set("player", selectedId);
        window.history.replaceState({}, "", url);
        renderPlayer(player);
      });
      elements.playerList.append(button);
    });

    const selected = filtered.find((player) => samePlayerId(playerId(player), selectedId)) || filtered[0];
    selectedId = playerId(selected);
    renderPlayer(selected);
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

  function init() {
    elements.playerCount.textContent = `${data.playerCount || players.length} players`;
    populateTeams();
    elements.searchInput.addEventListener("input", renderList);
    elements.teamFilter.addEventListener("change", renderList);

    const params = new URLSearchParams(window.location.search);
    const requested = params.get("player") || params.get("player_id") || params.get("id");
    const preferred = (requested ? players.find((player) => samePlayerId(playerId(player), requested)) : null)
      || players.find((player) => player.name === "Patrick Mahomes" && player.team.abbr === "KC")
      || players.find((player) => player.name === "Justin Jefferson" && (player.role.value || 0) >= 82)
      || players.find((player) => (player.role.value || 0) >= 90)
      || players.find((player) => player.position === "QB")
      || players[0];
    if (preferred) {
      selectedId = playerId(preferred);
    }
    renderList();
  }

  init();
}());
