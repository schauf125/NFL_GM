(function () {
  let data = window.GAME_CENTER_DATA || {};
  const state = {
    view: "calendar",
    runnerAvailable: location.protocol.startsWith("http"),
    runnerBusy: false,
    busyAction: null,
    cancelRequested: false,
    lastResult: null,
    selectedDraftProspectId: null,
    draftProspectPopoverOpen: false,
    draftBoardSort: { key: "rank", direction: "asc" },
    draftBoardPositionFilter: "all",
    scoutingBoardSort: { key: "rank", direction: "asc" },
    scoutingPositionFilter: "all",
    scoutingConfidenceFilter: "all",
    selectedDepthSlot: null,
    depthOffensePersonnel: "11",
    depthDefensePackage: "nickel",
    selectedCalendarItem: null,
    calendarBoxScores: {},
    calendarBoxScoreLoadingId: null,
    calendarLiveFocus: false,
    boxScoreModal: null,
    injuryModal: null,
    rosterCutdownPrompt: null,
    rosterCutdownPromptDismissedKey: null,
    selectedAiGmReviewId: null,
    newsFilter: "all",
    statsLiveSeason: null,
    statsLoading: false,
    seasonLiveSeason: null,
    seasonLoading: false,
    calendarLiveKey: null,
    calendarLoading: false,
    inboxLiveKey: null,
    inboxLoading: false,
    leagueNewsLiveKey: null,
    leagueNewsLoading: false,
    transactionsLiveKey: null,
    transactionsLoading: false,
    transactionsCategoryFilter: "all",
    injuriesLiveKey: null,
    injuriesLoading: false,
    injuriesScopeFilter: "all",
    practiceSquadLiveKey: null,
    practiceSquadLoading: false,
    practiceSquadFilter: "eligible",
    draftLiveKey: null,
    draftLoading: false,
    scoutingLiveKey: null,
    scoutingLoading: false,
    freeAgencyLiveKey: null,
    freeAgencyLoading: false,
    freeAgencyPositionFilter: "all",
    freeAgencyTierFilter: "all",
    rosterPositionFilter: "all",
    rosterGroupFilter: "all",
    rosterStatusFilter: "all",
    selectedRosterPlayerId: null,
    rosterSort: { key: "role", direction: "desc" },
    localScoutingKey: null,
    localScoutingSelections: [],
    contractsLiveKey: null,
    contractsLoading: false,
    depthChartLiveKey: null,
    depthChartLoading: false,
    aiGmLiveKey: null,
    aiGmLoading: false,
    liveErrors: {},
    lastLiveRefreshAt: null,
    lastRenderedView: null,
    viewHistory: [],
  };

  const liveApi = {
    inflight: new Map(),
    lastLoaded: new Map(),
  };
  let viewRefreshInFlight = null;

  const refs = {
    seasonLabel: document.getElementById("seasonLabel"),
    phaseText: document.getElementById("phaseText"),
    title: document.getElementById("title"),
    subhead: document.getElementById("subhead"),
    dateText: document.getElementById("dateText"),
    saveText: document.getElementById("saveText"),
    liveStatus: document.getElementById("liveStatus"),
    content: document.getElementById("content"),
    toast: document.getElementById("runnerToast"),
    railToggle: document.getElementById("railToggle"),
    backButton: document.getElementById("backButton"),
    buttons: Array.from(document.querySelectorAll(".nav button")),
  };

  const SEASON_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "advance_to_draft",
    "auto_cutdown_continue",
  ]);
  const STATS_REFRESH_ACTIONS = new Set([
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "advance_next_league_year",
  ]);
  const CALENDAR_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "advance_to_draft",
    "auto_cutdown_continue",
    "free_agency_start",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "league_news_seed",
    "event_generate_week",
  ]);
  const INBOX_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "inbox_mark_read",
    "event_generate_week",
  ]);
  const LEAGUE_NEWS_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "league_news_seed",
    "event_generate_week",
  ]);
  const TRANSACTIONS_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "advance_to_draft",
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_offer",
    "draft_pick",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "contract_extend",
    "contract_release",
    "contract_restructure",
    "roster_release_player",
    "practice_squad_assign",
    "practice_squad_release",
    "depth_chart_set",
    "depth_chart_move",
    "auto_cutdown",
    "auto_cutdown_continue",
  ]);
  const INJURIES_REFRESH_ACTIONS = new Set([
    "load_game",
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
  ]);
  const DRAFT_REFRESH_ACTIONS = new Set([
    "advance_to_draft",
    "auto_cutdown_continue",
    "draft_start",
    "draft_pick",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "auto_cutdown_continue",
    "advance_next_league_year",
  ]);
  const SCOUTING_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "sim_week",
    "sim_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "advance_to_draft",
    "scouting_setup",
    "scouting_auto",
    "scouting_one",
    "scouting_assign_batch",
    "scouting_unassign",
    "scouting_random_two",
    "scouting_discover_four",
    "scouting_senior_bowl_setup",
    "scouting_senior_bowl_process",
    "scouting_top30_visit",
    "scouting_top30_auto",
  ]);
  const SCOUTING_FLUSH_ACTIONS = new Set([
    "sim_week",
    "sim_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "postseason",
    "complete_season",
  ]);
  const FREE_AGENCY_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "complete_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "advance_to_draft",
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_offer",
    "ai_gm_free_agent_plan",
    "ai_gm_free_agent_plan_persist",
    "ai_gm_apply_free_agent_plan",
  ]);
  const CONTRACTS_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "complete_season",
    "advance_next_event",
    "advance_next_league_year",
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_offer",
    "contract_extend",
    "contract_release",
    "contract_restructure",
    "ai_gm_contract_plan",
    "ai_gm_contract_plan_persist",
    "ai_gm_apply_contract_plan",
    "ai_gm_free_agent_plan",
    "ai_gm_free_agent_plan_persist",
    "ai_gm_apply_free_agent_plan",
    "draft_finish",
  ]);
  const DEPTH_CHART_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "advance_next_event",
    "advance_next_league_year",
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_offer",
    "draft_finish",
    "depth_chart_set",
    "depth_chart_move",
    "contract_release",
    "practice_squad_assign",
    "practice_squad_release",
    "ai_gm_cutdown_plan",
    "ai_gm_cutdown_plan_persist",
    "ai_gm_apply_cutdown_plan",
    "ai_gm_review_apply",
    "ai_gm_daily_run",
    "auto_cutdown",
    "auto_cutdown_continue",
  ]);
  const DRAFT_ACTIONS = new Set([
    "advance_to_draft",
    "draft_start",
    "draft_pick",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "draft_pause",
    "draft_resume",
  ]);

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }

  function navShortLabel(label) {
    const clean = String(label || "").trim();
    const exact = {
      "League Table": "Tbl",
      "Stats": "Sta",
      "Inbox": "In",
      "League News": "News",
      "Transactions": "Txn",
      "Scouting": "Sct",
      "Roster Hub": "Ros",
      "Depth Chart": "Dep",
      "Contract Talks": "Con",
      "Free Agency": "FA",
      "Draft Room": "Drf",
      "AI GMs": "AI",
      "Calendar": "Cal",
    };
    if (exact[clean]) return exact[clean];
    return clean.split(/\s+/).map((part) => part[0]).join("").slice(0, 3).toUpperCase();
  }

  function quickLinkShortLabel(label) {
    const clean = String(label || "").trim();
    const exact = {
      "Main Menu": "Menu",
      "Front Office": "FO",
      "Player Profiles": "Ply",
      "Player Cards": "Card",
    };
    return exact[clean] || navShortLabel(clean);
  }

  function setRailCollapsed(collapsed) {
    document.body.classList.toggle("rail-collapsed", collapsed);
    if (refs.railToggle) {
      refs.railToggle.setAttribute("aria-pressed", collapsed ? "true" : "false");
      refs.railToggle.setAttribute("aria-label", collapsed ? "Expand navigation" : "Collapse navigation");
      const text = refs.railToggle.querySelector(".rail-toggle-text");
      if (text) text.textContent = collapsed ? "Expand" : "Collapse";
    }
  }

  function setupRailToggle() {
    document.querySelectorAll(".nav button").forEach((button) => {
      const label = button.textContent || "";
      button.dataset.fullLabel = label;
      button.dataset.shortLabel = navShortLabel(label);
      button.title = label;
    });
    document.querySelectorAll(".links a").forEach((link) => {
      const label = link.textContent || "";
      link.dataset.fullLabel = label;
      link.dataset.shortLabel = quickLinkShortLabel(label);
      link.title = label;
    });
    const stored = localStorage.getItem("nflGmRailCollapsed");
    setRailCollapsed(stored === "1");
    refs.railToggle?.addEventListener("click", () => {
      const collapsed = !document.body.classList.contains("rail-collapsed");
      localStorage.setItem("nflGmRailCollapsed", collapsed ? "1" : "0");
      setRailCollapsed(collapsed);
    });
  }

  function append(parent, children) {
    if (!Array.isArray(children)) {
      if (children !== null && children !== undefined) parent.append(children);
      return parent;
    }
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

  function shortDateTime(value) {
    if (!value) return "-";
    const [datePart, timePart] = String(value).split(" ");
    const time = timePart ? ` ${timePart.slice(0, 5)}` : "";
    return `${shortDate(datePart)}${time}`;
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

  function asList(value) {
    if (Array.isArray(value)) return value;
    if (value === null || value === undefined || value === "") return [];
    return [value];
  }

  function listText(value, limit = 3, separator = ", ") {
    return asList(value)
      .slice(0, limit)
      .map((item) => (item === null || item === undefined ? "" : String(item)))
      .filter(Boolean)
      .join(separator);
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

  const FOOTBALL_POSITION_ORDER = [
    "QB",
    "RB",
    "FB",
    "WR",
    "TE",
    "OT",
    "OG",
    "C",
    "EDGE",
    "IDL",
    "DT",
    "DE",
    "ILB",
    "OLB",
    "LB",
    "CB",
    "NB",
    "FS",
    "SS",
    "S",
    "K",
    "P",
    "LS",
  ];
  const ROSTER_GROUPS = [
    { value: "all", label: "All", positions: [] },
    { value: "PS", label: "Practice Squad", positions: [] },
    { value: "QB", label: "QB", positions: ["QB"] },
    { value: "RB", label: "RB", positions: ["RB", "FB"] },
    { value: "WR", label: "WR", positions: ["WR"] },
    { value: "TE", label: "TE", positions: ["TE"] },
    { value: "OL", label: "OL", positions: ["OT", "OG", "C"] },
    { value: "DL", label: "DL", positions: ["IDL", "DT", "DE"] },
    { value: "EDGE", label: "EDGE", positions: ["EDGE"] },
    { value: "LB", label: "LB", positions: ["LB", "ILB", "OLB", "MLB"] },
    { value: "CB", label: "CB", positions: ["CB"] },
    { value: "S", label: "S", positions: ["S", "FS", "SS"] },
    { value: "ST", label: "ST", positions: ["K", "PK", "P", "LS"] },
  ];

  const DEPTH_UNIT_ORDER = ["Offense", "Defense", "Special Teams"];
  const DEPTH_SLOT_ORDER = [
    "QB",
    "RB",
    "FB",
    "LWR",
    "RWR",
    "SWR",
    "TE",
    "LT",
    "LG",
    "C",
    "RG",
    "RT",
    "LEDGE",
    "LDL",
    "NT",
    "RDL",
    "REDGE",
    "WLB",
    "MLB",
    "SLB",
    "LCB",
    "RCB",
    "NB",
    "FS",
    "SS",
    "PK",
    "KO",
    "P",
    "LS",
    "KR",
    "PR",
  ];
  const OFFENSE_FORMATION_SLOTS = [
    { slot: "LWR", label: "X", row: 3, col: "1 / span 2" },
    { slot: "SWR", label: "Slot", row: 4, col: "3 / span 2" },
    { slot: "LT", label: "LT", row: 3, col: "5" },
    { slot: "LG", label: "LG", row: 3, col: "6" },
    { slot: "C", label: "C", row: 3, col: "7" },
    { slot: "RG", label: "RG", row: 3, col: "8" },
    { slot: "RT", label: "RT", row: 3, col: "9" },
    { slot: "TE", label: "TE", row: 3, col: "10" },
    { slot: "RWR", label: "Z", row: 3, col: "12 / span 2" },
    { slot: "QB", label: "QB", row: 5, col: "7" },
    { slot: "RB", label: "HB", row: 6, col: "7" },
  ];
  const OFFENSE_12_FORMATION_SLOTS = [
    { slot: "LWR", label: "X", row: 3, col: "1 / span 2" },
    { slot: "LT", label: "LT", row: 3, col: "5" },
    { slot: "LG", label: "LG", row: 3, col: "6" },
    { slot: "C", label: "C", row: 3, col: "7" },
    { slot: "RG", label: "RG", row: 3, col: "8" },
    { slot: "RT", label: "RT", row: 3, col: "9" },
    { slot: "TE", label: "Y", row: 3, col: "10", rank: 1 },
    { slot: "TE", label: "F", row: 4, col: "11", rank: 2 },
    { slot: "RWR", label: "Z", row: 3, col: "12 / span 2" },
    { slot: "QB", label: "QB", row: 5, col: "7" },
    { slot: "RB", label: "HB", row: 6, col: "7" },
  ];
  const OFFENSE_21_FORMATION_SLOTS = [
    { slot: "LWR", label: "X", row: 3, col: "1 / span 2" },
    { slot: "LT", label: "LT", row: 3, col: "5" },
    { slot: "LG", label: "LG", row: 3, col: "6" },
    { slot: "C", label: "C", row: 3, col: "7" },
    { slot: "RG", label: "RG", row: 3, col: "8" },
    { slot: "RT", label: "RT", row: 3, col: "9" },
    { slot: "TE", label: "TE", row: 3, col: "10" },
    { slot: "RWR", label: "Z", row: 3, col: "12 / span 2" },
    { slot: "QB", label: "QB", row: 5, col: "7" },
    { slot: "FB", label: "FB", row: 6, col: "6" },
    { slot: "RB", label: "HB", row: 7, col: "7" },
  ];
  const DEFENSE_FORMATION_SLOTS = [
    { slot: "LEDGE", label: "LEO", row: 1, col: "3" },
    { slot: "LDL", label: "DT", row: 1, col: "5" },
    { slot: "NT", label: "NT", row: 1, col: "6 / span 2" },
    { slot: "RDL", label: "DT", row: 1, col: "8" },
    { slot: "REDGE", label: "REO", row: 1, col: "10" },
    { slot: "WLB", label: "WLB", row: 2, col: "5" },
    { slot: "MLB", label: "MLB", row: 2, col: "7" },
    { slot: "SLB", label: "SLB", row: 2, col: "9", optional: true },
    { slot: "NB", label: "Nickel", row: 4, col: "4" },
    { slot: "LCB", label: "LCB", row: 5, col: "1 / span 2" },
    { slot: "RCB", label: "RCB", row: 5, col: "11 / span 2" },
    { slot: "FS", label: "FS", row: 7, col: "3 / span 2" },
    { slot: "SS", label: "SS", row: 7, col: "9 / span 2" },
  ];
  const BASE_DEFENSE_FORMATION_SLOTS = [
    { slot: "LEDGE", label: "LEO", row: 1, col: "3" },
    { slot: "LDL", label: "DT", row: 1, col: "5" },
    { slot: "NT", label: "NT", row: 1, col: "6 / span 2" },
    { slot: "RDL", label: "DT", row: 1, col: "8" },
    { slot: "REDGE", label: "REO", row: 1, col: "10" },
    { slot: "WLB", label: "WILB", row: 2, col: "6" },
    { slot: "MLB", label: "MILB", row: 2, col: "8" },
    { slot: "LCB", label: "LCB", row: 5, col: "1 / span 2" },
    { slot: "RCB", label: "RCB", row: 5, col: "11 / span 2" },
    { slot: "FS", label: "FS", row: 7, col: "3 / span 2" },
    { slot: "SS", label: "SS", row: 7, col: "9 / span 2" },
  ];
  const BASE_43_DEFENSE_FORMATION_SLOTS = [
    { slot: "LEDGE", label: "LE", row: 1, col: "4" },
    { slot: "LDL", label: "DT", row: 1, col: "6" },
    { slot: "RDL", label: "DT", row: 1, col: "7" },
    { slot: "REDGE", label: "RE", row: 1, col: "9" },
    { slot: "WLB", label: "WLB", row: 2, col: "4" },
    { slot: "MLB", label: "MLB", row: 2, col: "7" },
    { slot: "SLB", label: "SLB", row: 2, col: "10" },
    { slot: "LCB", label: "LCB", row: 5, col: "1 / span 2" },
    { slot: "RCB", label: "RCB", row: 5, col: "11 / span 2" },
    { slot: "FS", label: "FS", row: 7, col: "3 / span 2" },
    { slot: "SS", label: "SS", row: 7, col: "9 / span 2" },
  ];
  const SPECIAL_TEAMS_FORMATION_SLOTS = [
    { slot: "PK", label: "Kicker" },
    { slot: "KO", label: "Kickoff" },
    { slot: "PT", label: "Punter" },
    { slot: "H", label: "Holder" },
    { slot: "LS", label: "Long Snapper" },
    { slot: "KR", label: "Kick Returner" },
    { slot: "PR", label: "Punt Returner" },
  ];

  function footballPositionSort(left, right) {
    const l = String(left || "");
    const r = String(right || "");
    const leftIndex = FOOTBALL_POSITION_ORDER.indexOf(l);
    const rightIndex = FOOTBALL_POSITION_ORDER.indexOf(r);
    const leftRank = leftIndex >= 0 ? leftIndex : FOOTBALL_POSITION_ORDER.length;
    const rightRank = rightIndex >= 0 ? rightIndex : FOOTBALL_POSITION_ORDER.length;
    if (leftRank !== rightRank) return leftRank - rightRank;
    return l.localeCompare(r);
  }

  function orderedDepthUnits(depth) {
    return [...(depth.units || [])]
      .sort((a, b) => {
        const leftIndex = DEPTH_UNIT_ORDER.indexOf(a.unit);
        const rightIndex = DEPTH_UNIT_ORDER.indexOf(b.unit);
        const leftRank = leftIndex >= 0 ? leftIndex : DEPTH_UNIT_ORDER.length;
        const rightRank = rightIndex >= 0 ? rightIndex : DEPTH_UNIT_ORDER.length;
        if (leftRank !== rightRank) return leftRank - rightRank;
        return String(a.unit || "").localeCompare(String(b.unit || ""));
      })
      .map((unit) => ({
        ...unit,
        slots: [...(unit.slots || [])].sort((a, b) => {
          const left = String(a.slot || "").toUpperCase();
          const right = String(b.slot || "").toUpperCase();
          const leftIndex = DEPTH_SLOT_ORDER.indexOf(left);
          const rightIndex = DEPTH_SLOT_ORDER.indexOf(right);
          const leftRank = leftIndex >= 0 ? leftIndex : DEPTH_SLOT_ORDER.length;
          const rightRank = rightIndex >= 0 ? rightIndex : DEPTH_SLOT_ORDER.length;
          if (leftRank !== rightRank) return leftRank - rightRank;
          return left.localeCompare(right);
        }),
      }));
  }

  function orderedDepthSlots(depth) {
    return orderedDepthUnits(depth).flatMap((unit) => unit.slots || []);
  }

  function scrollableSnapshot() {
    const selectors = [".table-wrap", ".draft-pick-queue", ".top30-list", ".activity-list", ".message-list"];
    const snapshot = [];
    selectors.forEach((selector) => {
      document.querySelectorAll(selector).forEach((element, index) => {
        snapshot.push({
          selector,
          index,
          top: element.scrollTop,
          left: element.scrollLeft,
        });
      });
    });
    return snapshot;
  }

  function restoreScrollableSnapshot(snapshot) {
    (snapshot || []).forEach((item) => {
      const element = document.querySelectorAll(item.selector)[item.index];
      if (!element) return;
      element.scrollTop = item.top;
      element.scrollLeft = item.left;
    });
  }

  function currentSeasonSectionLabel() {
    const phase = String(data.currentPhase || "").toLowerCase();
    const scoutingPeriod = data.scouting?.period?.label;
    if (phase.includes("regular")) {
      if (scoutingPeriod && /^Week \d+/i.test(String(scoutingPeriod))) return String(scoutingPeriod).replace(" Scouting", "");
      if (data.season?.nextWeek) return `Week ${data.season.nextWeek}`;
      return "Regular Season";
    }
    if (phase.includes("postseason") || phase.includes("playoff")) return "Postseason";
    if (data.draft?.state?.status === "in_progress" || data.draft?.currentPick) return "Draft";
    const fa = data.freeAgency || {};
    const faStage = String(fa.period?.current_stage || fa.status || "").toLowerCase();
    if (faStage && !faStage.includes("not_started") && !faStage.includes("not started")) return "Free Agency";
    if (phase.includes("draft")) return "Draft";
    if (phase.includes("free")) return "Free Agency";
    if (phase.includes("offseason")) return "Offseason";
    return data.currentPhase || "";
  }

  function currentDateDisplay() {
    const date = shortDate(data.currentDate);
    const section = currentSeasonSectionLabel();
    return section ? `${date} | ${section}` : date;
  }

  function setHeader(title, subhead) {
    document.body.dataset.view = state.view;
    refs.seasonLabel.textContent = String(data.currentSeason || "");
    refs.phaseText.textContent = data.currentPhase || "";
    refs.title.textContent = title;
    refs.subhead.textContent = subhead;
    refs.dateText.textContent = currentDateDisplay();
    refs.saveText.textContent = data.activeSave?.display_name || data.registry?.activeGameId || "Master DB";
    if (refs.backButton) {
      refs.backButton.disabled = state.viewHistory.length === 0;
      refs.backButton.title = state.viewHistory.length ? `Back to ${viewLabel(state.viewHistory[state.viewHistory.length - 1])}` : "No previous screen";
    }
    updateConditionalNav();
    refs.buttons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
    updateLiveStatus();
  }

  function viewLabel(view) {
    const button = refs.buttons.find((item) => item.dataset.view === view);
    return button?.dataset.fullLabel || button?.textContent || view || "previous screen";
  }

  function playoffTreeVisible() {
    const season = data.season || {};
    const postseason = season.postseason || {};
    const regularGames = Number(season.totals?.games || 0);
    const regularRemaining = Number(season.totals?.remaining || 0);
    return Boolean(postseason.visible || Number(postseason.games || 0) > 0 || (regularGames > 0 && regularRemaining === 0));
  }

  function updateConditionalNav() {
    refs.buttons.forEach((button) => {
      if (button.dataset.view === "playoffTree") {
        button.hidden = !playoffTreeVisible();
      }
    });
  }

  function setLiveError(scope, message) {
    if (!scope) return;
    if (message) state.liveErrors[scope] = message;
    else delete state.liveErrors[scope];
    updateLiveStatus();
  }

  function loadingLabels() {
    return [
      ["seasonLoading", "season"],
      ["statsLoading", "stats"],
      ["calendarLoading", "calendar"],
      ["inboxLoading", "inbox"],
      ["leagueNewsLoading", "news"],
      ["draftLoading", "draft"],
      ["scoutingLoading", "scouting"],
      ["freeAgencyLoading", "free agency"],
      ["contractsLoading", "contracts"],
      ["depthChartLoading", "depth"],
      ["aiGmLoading", "AI GMs"],
    ].filter(([key]) => state[key]).map(([, label]) => label);
  }

  function updateLiveStatus() {
    if (!refs.liveStatus) return;
    const loading = loadingLabels();
    const errors = Object.entries(state.liveErrors || {});
    refs.liveStatus.className = "live-status";
    if (loading.length) {
      refs.liveStatus.hidden = false;
      refs.liveStatus.textContent = `Refreshing ${loading.slice(0, 3).join(", ")}${loading.length > 3 ? ` +${loading.length - 3}` : ""}`;
      return;
    }
    if (errors.length) {
      const [scope, message] = errors[0];
      refs.liveStatus.hidden = false;
      refs.liveStatus.classList.add("error");
      refs.liveStatus.textContent = `${scope}: ${message}`;
      return;
    }
    if (location.protocol.startsWith("http") && !state.runnerAvailable) {
      refs.liveStatus.hidden = false;
      refs.liveStatus.classList.add("offline");
      refs.liveStatus.textContent = "Live actions unavailable; showing latest saved data";
      return;
    }
    refs.liveStatus.hidden = true;
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

  function playerProfileHref({ playerId, name, team, position }) {
    const params = new URLSearchParams();
    if (playerId) params.set("player", playerId);
    if (name) params.set("name", name);
    if (team) params.set("team", team);
    if (position) params.set("position", position);
    const query = params.toString();
    return `../player_profile/index.html${query ? `?${query}` : ""}`;
  }

  function playerCardHref({ playerId, name, team, position }) {
    const params = new URLSearchParams();
    if (playerId) params.set("player", playerId);
    if (name) params.set("name", name);
    if (team) params.set("team", team);
    if (position) params.set("position", position);
    const query = params.toString();
    return `../player_card/index.html${query ? `?${query}` : ""}`;
  }

  function playerLink(playerId, name, className, hints) {
    if (!playerId) return node("span", className || "", name || "-");
    const link = node("a", className || "player-link", name || "-");
    link.href = playerProfileHref({
      playerId,
      name,
      team: hints?.team,
      position: hints?.position,
    });
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
    state.draftProspectPopoverOpen = true;
    const scoutingHasProspect = (data.scouting?.board || []).some((prospect) => String(prospect.prospect_id) === id);
    const draftHasProspect = (data.draft?.board || []).some((prospect) => String(prospect.prospect_id) === id);
    const targetView = scoutingHasProspect || !draftHasProspect ? "scouting" : "draft";
    if (!scoutingHasProspect && !draftHasProspect && runnerMode()) {
      switchView(targetView);
      loadLiveScouting({ limit: 500, quiet: true }).then(() => {
        state.selectedDraftProspectId = prospectId;
        state.draftProspectPopoverOpen = true;
        render();
      });
      return;
    }
    switchView(targetView);
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

  function smallPlayerCell(playerId, name, detail, hints) {
    const wrap = node("span", "player-name-stack");
    append(wrap, [
      playerLink(playerId, name, "player-link strong-link", hints),
      detail ? node("small", null, detail) : null,
    ]);
    return wrap;
  }

  function statPlayerLink(player) {
    return playerLink(player?.player_id, player?.player_name || "-", undefined, {
      team: player?.team,
      position: player?.position,
    });
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

  function switchView(view, options = {}) {
    if (view !== state.view && options.record !== false) {
      state.viewHistory.push(state.view);
      if (state.viewHistory.length > 30) state.viewHistory.shift();
    }
    state.view = view;
    render();
    if (options.scroll !== false) {
      refs.content?.scrollIntoView({ block: "start" });
    }
    if (options.refresh) refreshCurrentView();
  }

  function goBackView() {
    while (state.viewHistory.length) {
      const previous = state.viewHistory.pop();
      if (previous && previous !== state.view) {
        switchView(previous, { record: false, refresh: true });
        return;
      }
    }
    render();
  }

  function isDraftAction(action) {
    return DRAFT_ACTIONS.has(action);
  }

  function apiUrl(path, params) {
    const query = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") query.set(key, value);
    });
    const queryText = query.toString();
    return `${path}${queryText ? `?${queryText}` : ""}`;
  }

  async function apiGet(scope, path, options = {}) {
    if (!location.protocol.startsWith("http")) return null;
    const url = apiUrl(path, options.params);
    const key = `${scope}:${url}`;
    if (liveApi.inflight.has(key)) return liveApi.inflight.get(key);
    if (options.loadingKey && !options.quiet) {
      state[options.loadingKey] = true;
      updateLiveStatus();
    }
    const request = fetch(url, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText || "request failed"}`.trim());
        const payload = await response.json();
        state.runnerAvailable = true;
        state.lastLiveRefreshAt = new Date().toISOString();
        liveApi.lastLoaded.set(scope, state.lastLiveRefreshAt);
        setLiveError(scope, null);
        return payload;
      })
      .catch((error) => {
        if (scope === "state") state.runnerAvailable = false;
        setLiveError(scope, String(error.message || error));
        return null;
      })
      .finally(() => {
        liveApi.inflight.delete(key);
        if (options.loadingKey && !options.quiet) {
          state[options.loadingKey] = false;
          updateLiveStatus();
        }
      });
    liveApi.inflight.set(key, request);
    return request;
  }

  async function apiPost(scope, path, body) {
    if (!location.protocol.startsWith("http")) return null;
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText || "request failed"}`.trim());
      const payload = await response.json();
      state.runnerAvailable = true;
      setLiveError(scope, null);
      return payload;
    } catch (error) {
      setLiveError(scope, String(error.message || error));
      return null;
    }
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
    const payload = await apiGet("state", "/api/state");
    if (!payload) return false;
    data = payload;
    return true;
  }

  async function loadLiveLeaders() {
    if (!location.protocol.startsWith("http") || state.statsLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const payload = await apiGet("stats", "/api/league-leaders", {
      params: { season },
      loadingKey: "statsLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      stats: payload.stats || data.stats || {},
      statsGeneratedAt: payload.generatedAt,
    };
    state.statsLiveSeason = String(payload.season || season || "");
    return true;
  }

  async function loadLiveSeason() {
    if (!location.protocol.startsWith("http") || state.seasonLoading) return false;
    const season = data.season?.season || data.currentSeason || "";
    const payload = await apiGet("season", "/api/season", {
      params: { season },
      loadingKey: "seasonLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      season: payload.seasonData || data.season || {},
      seasonGeneratedAt: payload.generatedAt,
    };
    state.seasonLiveSeason = String(payload.season || season || "");
    return true;
  }

  async function loadLiveCalendar(options = {}) {
    if (!location.protocol.startsWith("http") || state.calendarLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const liveFocus = Boolean(options.liveFocus);
    const currentDate = liveFocus ? "" : (data.currentDate || data.calendar?.focusDate || "");
    const payload = await apiGet("calendar", "/api/calendar", {
      params: { season, date: currentDate, live: liveFocus ? "1" : "" },
      loadingKey: "calendarLoading",
      quiet: Boolean(options.quiet),
    });
    if (!payload) return false;
    data = {
      ...data,
      calendar: payload.calendar || data.calendar || {},
      events: payload.events || data.events || [],
      calendarGeneratedAt: payload.generatedAt,
      currentDate: payload.currentDate || data.currentDate,
      saveCurrentDate: payload.saveCurrentDate || data.saveCurrentDate,
    };
    state.calendarLiveKey = `${payload.season || season || ""}:${payload.currentDate || currentDate || ""}`;
    state.calendarLiveFocus = Boolean(payload.liveFocus);
    return true;
  }

  function inboxLiveKey() {
    const scouting = data.scouting || {};
    const first = (scouting.inbox || [])[0] || {};
    return `${scouting.gameId || ""}:${data.currentDate || ""}:${scouting.counts?.unread || 0}:${first.message_id || ""}`;
  }

  function leagueNewsLiveKey() {
    const news = data.leagueNews || {};
    const first = (news.items || [])[0] || {};
    return `${news.gameId || ""}:${news.updatedAt || data.currentDate || ""}:${news.counts?.total || 0}:${first.news_id || ""}`;
  }

  function transactionsLiveKey() {
    const tx = data.transactions || {};
    const first = (tx.items || [])[0] || {};
    return `${data.activeSave?.game_id || data.activeSave?.save_id || ""}:${tx.counts?.total || 0}:${first.id || ""}:${first.date || ""}:${state.transactionsCategoryFilter}`;
  }

  function injuriesLiveKey() {
    const injuries = data.injuries || {};
    const active = (injuries.active || [])[0] || {};
    const recent = (injuries.recent || [])[0] || {};
    return `${data.activeSave?.game_id || data.activeSave?.save_id || ""}:${injuries.counts?.active || 0}:${active.activeInjuryId || ""}:${recent.eventId || ""}:${injuries.updatedAt || ""}:${state.injuriesScopeFilter}`;
  }

  function practiceSquadLiveKey() {
    const ps = data.practiceSquad || {};
    const usage = ps.usage || {};
    return `${data.activeSave?.game_id || data.activeSave?.save_id || ""}:${data.currentDate || ""}:${ps.activeCount || 0}:${usage.total || 0}:${usage.developmental_count || 0}:${usage.veteran_exception_count || 0}:${usage.international_exemption_count || 0}:${(ps.candidates || []).length}`;
  }

  function draftLiveKey() {
    const draft = data.draft || {};
    const stateRow = draft.state || {};
    return [
      draft.year || "",
      stateRow.current_pick_number || "",
      stateRow.current_team || "",
      stateRow.status || "",
      draft.pickTotals?.used || 0,
      draft.pickTotals?.remaining || 0,
      (draft.selections || []).length,
      (draft.userSelections || []).length,
    ].join(":");
  }

  function scoutingLiveKey() {
    const scouting = data.scouting || {};
    const boardFirst = (scouting.board || [])[0] || {};
    return [
      scouting.gameId || "",
      scouting.draftYear || "",
      scouting.currentDate || data.currentDate || "",
      scouting.weeklyChoiceUsed ? 1 : 0,
      scouting.usedAction || "",
      scouting.counts?.visible || 0,
      scouting.counts?.pending || 0,
      scouting.counts?.hiddenRemaining || 0,
      scouting.counts?.unread || 0,
      boardFirst.prospect_id || "",
      boardFirst.scouting_confidence || "",
    ].join(":");
  }

  function currentScoutingSelectionKey() {
    const scouting = data.scouting || {};
    const period = scouting.period || {};
    return `${scouting.gameId || ""}:${scouting.draftYear || ""}:${period.season || ""}:${period.week || ""}`;
  }

  function ensureLocalScoutingSelections() {
    const key = currentScoutingSelectionKey();
    if (state.localScoutingKey !== key) {
      state.localScoutingKey = key;
      state.localScoutingSelections = [];
    }
    return state.localScoutingSelections || [];
  }

  function localScoutingSelectionIds() {
    return ensureLocalScoutingSelections().map((id) => Number(id)).filter(Boolean);
  }

  function freeAgencyLiveKey() {
    const fa = data.freeAgency || {};
    const period = fa.period || {};
    const event = (fa.events || [])[0] || {};
    return [
      period.league_year || data.draft?.year || data.currentSeason || "",
      period.current_date || data.currentDate || "",
      period.current_stage || "",
      period.current_hour || "",
      fa.counts?.available || 0,
      fa.counts?.signed || 0,
      fa.counts?.pendingOffers || 0,
      (fa.offers || []).length,
      event.created_at || event.message || "",
    ].join(":");
  }

  function contractsLiveKey() {
    const talks = data.contractNegotiations || {};
    const firstExpiring = (talks.expiring || [])[0] || {};
    const firstCap = (talks.capCasualties || [])[0] || {};
    return [
      talks.season || data.currentSeason || "",
      talks.team || data.activeSave?.user_team || "",
      talks.counts?.expiring || talks.counts?.total || 0,
      talks.counts?.capCasualties || 0,
      talks.counts?.restructures || 0,
      talks.currentCap?.cap_space || "",
      talks.projectedCap?.cap_space || "",
      firstExpiring.player_id || "",
      firstCap.player_id || "",
    ].join(":");
  }

  function depthChartLiveKey() {
    const depth = data.depthChart || {};
    const firstRow = (depth.rows || [])[0] || {};
    const firstRoster = (depth.roster || [])[0] || {};
    return [
      depth.team || data.activeSave?.user_team || "",
      (depth.rows || []).length,
      (depth.roster || []).length,
      (depth.units || []).length,
      firstRow.player_id || "",
      firstRow.rank || firstRow.depth_rank || "",
      firstRoster.player_id || "",
    ].join(":");
  }

  function aiGmLiveKey() {
    const ai = data.aiGm || {};
    const review = (ai.reviewInbox || [])[0] || {};
    const activity = (ai.reviewActivity || [])[0] || {};
    const run = (ai.dailyRuns || [])[0] || {};
    const queue = (ai.queue || [])[0] || {};
    return [
      ai.gameId || data.activeSave?.game_id || data.activeSave?.save_id || "",
      ai.team || data.activeSave?.user_team || "",
      ai.autonomy?.mode || "",
      ai.counts?.reviewInbox || 0,
      ai.counts?.reviewActivity || 0,
      ai.counts?.dailyRuns || 0,
      ai.counts?.queue || 0,
      review.review_id || "",
      activity.review_id || "",
      run.run_id || "",
      queue.decision_id || "",
    ].join(":");
  }

  async function loadLiveInbox() {
    if (!location.protocol.startsWith("http") || state.inboxLoading) return false;
    const payload = await apiGet("inbox", "/api/inbox", {
      params: { limit: 40 },
      loadingKey: "inboxLoading",
    });
    if (!payload) return false;
    const scouting = data.scouting || {};
    data = {
      ...data,
      scouting: {
        ...scouting,
        gameId: payload.gameId || scouting.gameId,
        inbox: payload.inbox || [],
        counts: {
          ...(scouting.counts || {}),
          ...(payload.counts || {}),
        },
      },
      inboxGeneratedAt: payload.generatedAt,
    };
    state.inboxLiveKey = inboxLiveKey();
    return true;
  }

  async function loadLiveLeagueNews() {
    if (!location.protocol.startsWith("http") || state.leagueNewsLoading) return false;
    const payload = await apiGet("league news", "/api/league-news", {
      params: { limit: 80 },
      loadingKey: "leagueNewsLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      leagueNews: payload.leagueNews || data.leagueNews || { items: [], categories: [], counts: {} },
      leagueNewsGeneratedAt: payload.generatedAt,
    };
    state.leagueNewsLiveKey = leagueNewsLiveKey();
    return true;
  }

  async function loadLiveTransactions() {
    if (!location.protocol.startsWith("http") || state.transactionsLoading) return false;
    const payload = await apiGet("transactions", "/api/transactions", {
      params: { limit: 500 },
      loadingKey: "transactionsLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      transactions: payload.transactions || data.transactions || { items: [], categories: [], counts: {} },
      transactionsGeneratedAt: payload.generatedAt,
    };
    state.transactionsLiveKey = transactionsLiveKey();
    return true;
  }

  async function loadLiveInjuries() {
    if (!location.protocol.startsWith("http") || state.injuriesLoading) return false;
    const payload = await apiGet("injuries", "/api/injuries", {
      params: { active_limit: 180, recent_limit: 140 },
      loadingKey: "injuriesLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      injuries: payload.injuries || data.injuries || { active: [], recent: [], counts: {} },
      injuriesGeneratedAt: payload.generatedAt,
    };
    state.injuriesLiveKey = injuriesLiveKey();
    return true;
  }

  async function loadLivePracticeSquad() {
    if (!location.protocol.startsWith("http") || state.practiceSquadLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const team = data.activeSave?.user_team || data.practiceSquad?.team || "";
    const payload = await apiGet("practice squad", "/api/practice-squad", {
      params: { season, team },
      loadingKey: "practiceSquadLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      practiceSquad: payload.practiceSquad || data.practiceSquad || { usage: {}, limits: {}, candidates: [] },
      practiceSquadGeneratedAt: payload.generatedAt,
    };
    state.practiceSquadLiveKey = practiceSquadLiveKey();
    return true;
  }

  async function loadLiveDraft() {
    if (!location.protocol.startsWith("http") || state.draftLoading) return false;
    const year = data.draft?.year || data.currentSeason || "";
    const payload = await apiGet("draft", "/api/draft", {
      params: { year },
      loadingKey: "draftLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      draft: payload.draft || data.draft || { pickTotals: {}, board: [], pickQueue: [], events: [] },
      rookieClass: payload.rookieClass || data.rookieClass || {},
      draftGeneratedAt: payload.generatedAt,
    };
    state.draftLiveKey = draftLiveKey();
    return true;
  }

  async function loadLiveScouting(options = {}) {
    if (!location.protocol.startsWith("http") || state.scoutingLoading) return false;
    const payload = await apiGet("scouting", "/api/scouting", {
      params: { limit: options.limit || 240 },
      loadingKey: "scoutingLoading",
      quiet: Boolean(options.quiet),
    });
    if (!payload) return false;
    data = {
      ...data,
      scouting: payload.scouting || data.scouting || {},
      scoutingGeneratedAt: payload.generatedAt,
    };
    state.scoutingLiveKey = scoutingLiveKey();
    state.inboxLiveKey = inboxLiveKey();
    return true;
  }

  async function loadLiveFreeAgency() {
    if (!location.protocol.startsWith("http") || state.freeAgencyLoading) return false;
    const leagueYear = data.freeAgency?.leagueYear || data.currentSeason || "";
    const payload = await apiGet("free agency", "/api/free-agency", {
      params: { league_year: leagueYear },
      loadingKey: "freeAgencyLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      freeAgency: payload.freeAgency || data.freeAgency || { counts: {}, board: [], offers: [], events: [] },
      freeAgencyGeneratedAt: payload.generatedAt,
    };
    state.freeAgencyLiveKey = freeAgencyLiveKey();
    return true;
  }

  async function loadLiveContracts() {
    if (!location.protocol.startsWith("http") || state.contractsLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const payload = await apiGet("contracts", "/api/contracts", {
      params: { season },
      loadingKey: "contractsLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      contractNegotiations: payload.contractNegotiations || data.contractNegotiations || { counts: {}, expiring: [], capCasualties: [], restructureCandidates: [] },
      contractsGeneratedAt: payload.generatedAt,
    };
    state.contractsLiveKey = contractsLiveKey();
    return true;
  }

  async function loadLiveDepthChart() {
    if (!location.protocol.startsWith("http") || state.depthChartLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const team = data.activeSave?.user_team || data.depthChart?.team || "";
    const payload = await apiGet("depth chart", "/api/depth-chart", {
      params: { season, team },
      loadingKey: "depthChartLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      depthChart: payload.depthChart || data.depthChart || { rows: [], roster: [], units: [] },
      depthChartGeneratedAt: payload.generatedAt,
    };
    state.depthChartLiveKey = depthChartLiveKey();
    return true;
  }

  async function loadLiveAiGm() {
    if (!location.protocol.startsWith("http") || state.aiGmLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const team = data.activeSave?.user_team || data.aiGm?.team || "";
    const payload = await apiGet("AI GMs", "/api/ai-gm", {
      params: { season, team },
      loadingKey: "aiGmLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      aiGm: payload.aiGm || data.aiGm || { counts: {}, logs: [] },
      aiGmGeneratedAt: payload.generatedAt,
    };
    state.aiGmLiveKey = aiGmLiveKey();
    return true;
  }

  async function refreshLiveAfterAction(action) {
    if (!runnerMode()) return;
    if (DEPTH_CHART_REFRESH_ACTIONS.has(action) || action.startsWith("depth_chart_") || action.startsWith("roster_")) {
      state.depthChartLiveKey = null;
      state.practiceSquadLiveKey = null;
      showToast("Refreshing depth chart...");
      await Promise.allSettled([loadLiveDepthChart(), loadLivePracticeSquad()]);
      return;
    }
    if (DRAFT_REFRESH_ACTIONS.has(action) || isDraftAction(action)) {
      state.draftLiveKey = null;
      showToast("Refreshing draft room...");
      await loadLiveDraft();
      return;
    }
    state.seasonLiveSeason = null;
    state.statsLiveSeason = null;
    state.calendarLiveKey = null;
    state.inboxLiveKey = null;
    state.leagueNewsLiveKey = null;
    state.draftLiveKey = null;
    state.scoutingLiveKey = null;
    state.freeAgencyLiveKey = null;
    state.contractsLiveKey = null;
    state.depthChartLiveKey = null;
    state.practiceSquadLiveKey = null;
    state.aiGmLiveKey = null;
    showToast("Refreshing live game data...");
    await loadLiveState();
    const refreshes = [];
    if (SEASON_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveSeason());
    }
    if (STATS_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveLeaders());
    }
    if (CALENDAR_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveCalendar());
    }
    if (INBOX_REFRESH_ACTIONS.has(action) || action.startsWith("scouting_") || action.startsWith("ai_gm_")) {
      refreshes.push(loadLiveInbox());
    }
    if (LEAGUE_NEWS_REFRESH_ACTIONS.has(action) || action.startsWith("ai_gm_")) {
      refreshes.push(loadLiveLeagueNews());
    }
    if (TRANSACTIONS_REFRESH_ACTIONS.has(action) || action.startsWith("ai_gm_")) {
      refreshes.push(loadLiveTransactions());
    }
    if (INJURIES_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveInjuries());
    }
    if (DRAFT_REFRESH_ACTIONS.has(action) || isDraftAction(action) || action === "ai_gm_draft_plan" || action === "ai_gm_draft_plan_persist") {
      refreshes.push(loadLiveDraft());
    }
    if (SCOUTING_REFRESH_ACTIONS.has(action) || action.startsWith("scouting_")) {
      refreshes.push(loadLiveScouting());
    }
    if (FREE_AGENCY_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveFreeAgency());
    }
    if (CONTRACTS_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveContracts());
    }
    if (DEPTH_CHART_REFRESH_ACTIONS.has(action) || action.startsWith("depth_chart_")) {
      refreshes.push(loadLiveDepthChart());
    }
    if (DEPTH_CHART_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLivePracticeSquad());
    }
    if (action.startsWith("ai_gm_")) {
      refreshes.push(loadLiveAiGm());
    }
    if (refreshes.length) {
      await Promise.allSettled(refreshes);
    }
  }

  function startDraftProgressPolling(action) {
    if (!["draft_skip_to_user", "draft_finish"].includes(action) || !runnerMode()) return null;
    let stopped = false;
    const tick = async () => {
      if (stopped || state.draftLoading) return;
      const changed = await loadLiveDraft();
      if (changed && state.view === "draft") render();
    };
    tick();
    const interval = window.setInterval(tick, 900);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }

  function startSimProgressPolling(action) {
    if (!["sim_week", "sim_season", "advance_to_draft", "auto_cutdown_continue"].includes(action) || !runnerMode()) return null;
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      const changed = await loadLiveCalendar({ liveFocus: true, quiet: true });
      if (changed && state.view === "calendar") render();
    };
    tick();
    const interval = window.setInterval(tick, 1600);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }

  function activeViewRefreshers() {
    if (state.view === "season" || state.view === "playoffTree") return [loadLiveSeason, loadLiveCalendar];
    if (state.view === "stats") return [loadLiveLeaders];
    if (state.view === "inbox") return [loadLiveInbox];
    if (state.view === "leagueNews") return [loadLiveLeagueNews];
    if (state.view === "transactions") return [loadLiveTransactions];
    if (state.view === "injuries") return [loadLiveInjuries];
    if (state.view === "practiceSquad") return [loadLivePracticeSquad, loadLiveDepthChart];
    if (state.view === "scouting") return [loadLiveScouting, loadLiveInbox];
    if (state.view === "roster") return [loadLiveDepthChart, loadLiveContracts];
    if (state.view === "depth") return [loadLiveDepthChart];
    if (state.view === "contracts") return [loadLiveContracts];
    if (state.view === "freeAgency") return [loadLiveFreeAgency];
    if (state.view === "draft") return [loadLiveDraft, loadLiveScouting];
    if (state.view === "aiGm") return [loadLiveAiGm, loadLiveInbox, loadLiveLeagueNews];
    if (state.view === "calendar") return [loadLiveCalendar, loadLiveLeagueNews];
    return [loadLiveSeason, loadLiveCalendar, loadLiveInbox];
  }

  async function refreshCurrentView() {
    if (!runnerMode() || state.runnerBusy) return false;
    if (viewRefreshInFlight) return viewRefreshInFlight;
    viewRefreshInFlight = (async () => {
      await loadLiveState();
      const refreshers = activeViewRefreshers();
      if (refreshers.length) {
        await Promise.allSettled(refreshers.map((refresher) => refresher()));
      }
      render();
      return true;
    })().finally(() => {
      viewRefreshInFlight = null;
    });
    return viewRefreshInFlight;
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
    if (!confirmBeforeAction(action, params || {})) return;
    const calendarProgressAction = action === "sim_week" || action === "sim_season" || action === "advance_to_draft" || action === "auto_cutdown_continue";
    if (calendarProgressAction) {
      state.calendarLiveFocus = true;
      switchView("calendar", { refresh: true });
    }
    state.runnerBusy = true;
    state.busyAction = action;
    state.cancelRequested = false;
    showToast(`${actionLabel(action)} in progress...`);
    const renderBusyState = action !== "box_score";
    if (renderBusyState) render();
    const stopDraftProgressPolling = startDraftProgressPolling(action);
    const stopSimProgressPolling = startSimProgressPolling(action);
    try {
      await flushLocalScoutingSelections(action);
      const payload = await apiPost("runner", "/api/run", { action, params: params || {} });
      if (!payload) throw new Error("Runner request failed");
      payload.params = params || {};
      if (payload.state) {
        data = payload.state;
      }
      if (payload.statePatch) {
        applyStatePatch(payload.statePatch);
      }
      state.lastResult = payload;
      if (Array.isArray(payload.injuryAlerts) && payload.injuryAlerts.length) {
        state.injuryModal = payload.injuryAlerts;
      }
      if (payload.rosterGate && rosterGateStillRelevant()) {
        payload.returncode = 1;
        state.rosterCutdownPrompt = payload.rosterGate;
        state.lastResult = {
          ...payload,
          returncode: 1,
          summary: {
            ...(payload.summary || {}),
            title: payload.rosterGate.title || "Roster Cutdown Needed",
            message: payload.rosterGate.message || payload.summary?.message,
            status: "warning",
          },
        };
      }
      state.runnerAvailable = true;
      if (payload.returncode === 0 && action !== "box_score") {
        await refreshLiveAfterAction(action);
      } else if (isDraftAction(action) || action.startsWith("ai_gm_")) {
        await refreshLiveAfterAction(action);
      }
    if (payload.returncode === 0 && action === "sim_season" && playoffTreeVisible() && Number(data.season?.postseason?.games || 0) > 0) {
        switchView("playoffTree", { refresh: false });
      }
      showToast(payload.summary?.message || (payload.returncode === 0 ? `${actionLabel(action)} complete` : `${actionLabel(action)} needs attention`));
    } catch (error) {
      state.lastResult = { error: String(error) };
      showToast("Action could not be completed");
    } finally {
      if (stopDraftProgressPolling) stopDraftProgressPolling();
      if (stopSimProgressPolling) stopSimProgressPolling();
      state.runnerBusy = false;
      state.busyAction = null;
      state.cancelRequested = false;
      state.calendarLiveFocus = false;
      render();
    }
  }

  function cancellableRunnerAction(action) {
    return action === "sim_week" || action === "sim_season";
  }

  async function requestRunnerCancel() {
    if (!runnerMode() || !state.runnerBusy || !cancellableRunnerAction(state.busyAction) || state.cancelRequested) return;
    state.cancelRequested = true;
    showToast("Stop requested. Waiting for the next safe spot...");
    render();
    const payload = await apiPost("cancel", "/api/cancel", { action: state.busyAction });
    if (payload?.message) {
      showToast(payload.message);
    } else if (!payload) {
      state.cancelRequested = false;
      showToast("Stop request could not be sent");
    }
    render();
  }

  async function flushLocalScoutingSelections(action) {
    if (!SCOUTING_FLUSH_ACTIONS.has(action)) return;
    const prospectIds = localScoutingSelectionIds();
    if (!prospectIds.length) return;
    showToast(`Saving ${prospectIds.length} scouting selection${prospectIds.length === 1 ? "" : "s"}...`);
    const payload = await apiPost("runner", "/api/run", {
      action: "scouting_assign_batch",
      params: { prospect_ids: prospectIds },
    });
    if (!payload || payload.returncode !== 0) {
      const message = payload?.summary?.message || payload?.stderr || payload?.error || "Scouting selections could not be saved.";
      throw new Error(message);
    }
    state.localScoutingSelections = [];
    state.localScoutingKey = currentScoutingSelectionKey();
  }

  async function loadCalendarBoxScore(gameId) {
    if (!runnerMode() || !gameId) return;
    state.selectedCalendarItem = { type: "game", id: gameId };
    state.calendarBoxScoreLoadingId = String(gameId);
    render();
    try {
      const params = { game_id: gameId, show_plays: 16 };
      const payload = await apiGet("box score", "/api/box-score", {
        params: { game_id: gameId, show_plays: 16 },
      });
      if (!payload) throw new Error("Box score request failed");
      payload.params = params;
      state.calendarBoxScores = {
        ...(state.calendarBoxScores || {}),
        [String(gameId)]: payload,
      };
      state.boxScoreModal = payload;
      state.lastResult = payload;
      showToast(payload.returncode === 0 ? "Box score loaded" : "Box score needs attention");
    } catch (error) {
      const payload = { action: "box_score", returncode: 1, error: String(error), params: { game_id: gameId } };
      state.calendarBoxScores = {
        ...(state.calendarBoxScores || {}),
        [String(gameId)]: payload,
      };
      state.boxScoreModal = payload;
      showToast("Box score could not be loaded");
    } finally {
      state.calendarBoxScoreLoadingId = null;
      render();
    }
  }

  function draftNeedsAdvanceWarning(action) {
    if (!["advance_next_event", "advance_to_date", "advance_next_league_year", "sim_week", "sim_season"].includes(action)) return false;
    const draft = data.draft || {};
    const remaining = Number(draft.pickTotals?.remaining || 0);
    if (remaining <= 0) return false;
    const draftDate = String(draft.draftDate || "").slice(0, 10);
    const currentDate = String(data.currentDate || "").slice(0, 10);
    return Boolean(draftDate && currentDate && currentDate === draftDate);
  }

  function confirmBeforeAction(action, params = {}) {
    if (draftNeedsAdvanceWarning(action)) {
      const remaining = Number(data.draft?.pickTotals?.remaining || 0);
      return window.confirm(
        `The ${data.draft?.year || ""} draft still has ${remaining} pick(s) remaining. ` +
        "Advancing the calendar will auto-sim the rest of the draft, including any user-team picks that are still open.\n\nContinue?",
      );
    }
    if (action === "ai_gm_review_apply" && params.apply) {
      return window.confirm(
        "This will apply approved AI GM review item(s) and may change rosters, contracts, offers, cap, or transactions.\n\nContinue?",
      );
    }
    if (action === "roster_release_player") {
      return window.confirm("Release this player from your active roster?\n\nContinue?");
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
      roster_release_player: "Release Player",
      roster_change_number: "Change Number",
      practice_squad_assign: "Assign Practice Squad",
      practice_squad_release: "Release Practice Squad Player",
      auto_cutdown: "Auto Cutdown",
      auto_cutdown_continue: "Auto Cutdown And Continue",
      depth_chart_set: "Set Depth Chart",
      depth_chart_move: "Move Depth Chart",
      postseason: "Run Postseason",
      postseason_round: "Sim Playoff Round",
      validate_rosters: "Validate Rosters",
      advance_next_event: "Advance To Next Date",
      advance_to_date: "Advance To Date",
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
      ai_gm_autonomy_show: "Show AI GM Autonomy",
      ai_gm_autonomy_config: "Set AI GM Autonomy",
      ai_gm_daily_run: "Run AI GM Daily Check",
      ai_gm_review_inbox: "Show AI GM Review Inbox",
      ai_gm_review_history: "Show AI GM Review History",
      ai_gm_review_show: "Show AI GM Review",
      ai_gm_review_update: "Update AI GM Review",
      ai_gm_review_apply: "Apply AI GM Review",
      ai_gm_profiles: "Show AI GM Profile",
      ai_gm_evaluate: "Evaluate Team",
      ai_gm_cutdown_plan: "Build Cutdown Plan",
      ai_gm_cutdown_plan_persist: "Save Cutdown Plan",
      ai_gm_cutdown_plans: "List Cutdown Plans",
      ai_gm_contract_plan: "Build Contract Plan",
      ai_gm_contract_plan_persist: "Save Contract Plan",
      ai_gm_contract_plans: "List Contract Plans",
      ai_gm_apply_contract_plan: "Apply Contract Plan",
      ai_gm_draft_plan: "Build Draft Plan",
      ai_gm_draft_plan_persist: "Save Draft Plan",
      ai_gm_draft_plans: "List Draft Plans",
      ai_gm_free_agent_plan: "Build FA Plan",
      ai_gm_free_agent_plan_persist: "Save FA Plan",
      ai_gm_free_agent_plans: "List FA Plans",
      ai_gm_apply_free_agent_plan: "Apply FA Plan",
      ai_gm_offseason_run: "Run CPU Offseason",
      ai_gm_ops: "Scan AI GM Ops",
      ai_gm_queue: "Show AI GM Queue",
      ai_gm_process_queue: "Process AI GM Queue",
      ai_gm_context: "Build AI GM Context",
      ai_gm_run: "Run AI GM Decision",
      ai_gm_logs: "Show AI GM Logs",
      scouting_setup: "Scouting Setup",
      scouting_assign: "Scouting Assignment",
      scouting_process_week: "Scouting Week",
      scouting_auto: "Auto Assign Scouts",
      scouting_one: "Select Scout Player",
      scouting_assign_batch: "Save Scout Selections",
      scouting_unassign: "Unselect Scout Player",
      scouting_random_two: "Scout 8 Random Players",
      scouting_discover_four: "Scout 4 + 8 Discoveries",
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
      ? "\n\nFull-season sims can take a few minutes. The league is playing every remaining game and updating standings, stats, scouting, roster checks, and front-office activity."
      : "\n\nThe page will refresh the affected screens when this finishes.";
    return `${label} is running...${extra}`;
  }

  function commandBox(label, command, action, params, options = {}) {
    const box = node("div", "command-box action-command");
    const top = node("div", "command-bar");
    const children = [node("span", "tag", label)];
    if (action && runnerMode()) {
      const run = node("button", "run-button", state.runnerBusy ? "Running" : (options.runLabel || "Run"));
      run.type = "button";
      run.disabled = state.runnerBusy || Boolean(options.disabledReason);
      if (options.disabledReason) run.title = options.disabledReason;
      run.addEventListener("click", () => runAction(action, params));
      children.push(run);
    } else if (action) {
      children.push(node("span", "muted", "Live actions are unavailable"));
    }
    append(top, children);
    const content = [top];
    if (options.detail) content.push(node("span", "muted", options.detail));
    append(box, content);
    return box;
  }

  function draftActionAvailability(action, draft, selected) {
    const draftState = draft?.state || null;
    const remaining = Number(draft?.pickTotals?.remaining || 0);
    if (action === "advance_to_draft") {
      if (draftState) return { disabledReason: "Draft room is already active." };
      if (!draft?.draftDate) return { disabledReason: "No draft date is available." };
      return {};
    }
    if (action === "draft_start") {
      if (draftState) return { disabledReason: "Draft room is already started." };
      if (!dateReached(draft?.draftDate)) return { disabledReason: `Draft date is ${shortDate(draft?.draftDate)}.` };
      if (draft?.orderFinalized === false) return { disabledReason: draft.orderWarning || "Draft order is not finalized." };
      return {};
    }
    if (["draft_skip", "draft_skip_to_user", "draft_finish"].includes(action)) {
      if (!draftState) return { disabledReason: "Start the draft room first." };
      if (remaining <= 0) return { disabledReason: "Draft is complete." };
      if (action !== "draft_finish" && isUserOnClock()) return { disabledReason: "Your team is on the clock." };
      return {};
    }
    if (action === "draft_pick") {
      if (!draftState) return { disabledReason: "Start the draft room first." };
      if (!isUserOnClock()) return { disabledReason: "Only enabled when your team is on the clock." };
      if (!selected?.prospect_id) return { disabledReason: "Select a draft prospect first." };
      return {};
    }
    if (action === "advance_next_league_year" && remaining > 0) {
      return { disabledReason: "Finish the draft before advancing the league year." };
    }
    return {};
  }

  function draftCommandBox(label, command, action, params, draft, selected) {
    const availability = draftActionAvailability(action, draft, selected);
    if (action === "draft_pick" && selected?.prospect_id) {
      command = (command || "").replace("<id>", selected.prospect_id);
      params = { prospect_id: selected.prospect_id };
    }
    return commandBox(label, command, action, params, {
      ...availability,
      hideCommand: true,
      detail: availability.disabledReason || "The draft room refreshes after the action completes.",
    });
  }

  function liveCommandBox(label, command, action, params = {}, detail = "") {
    return commandBox(label, command, action, params, {
      hideCommand: true,
      detail: detail || "Affected panels refresh after it completes.",
    });
  }

  function controlButton({ label, action, params = {}, availability = {}, tone = "", className = "" }) {
    const classes = `control-button ${className || ""} ${tone || ""}`.trim();
    const button = node("button", classes, state.runnerBusy && state.busyAction === action ? "Running" : label);
    button.type = "button";
    button.disabled = state.runnerBusy || Boolean(availability.disabledReason) || !runnerMode();
    button.title = availability.disabledReason || (runnerMode() ? actionLabel(action) : "Live actions are unavailable");
    button.addEventListener("click", () => runAction(action, params || {}));
    return button;
  }

  function controlDisabledReasons(entries) {
    if (!runnerMode()) return ["Live actions are unavailable."];
    const reasons = entries
      .map((entry) => entry?.availability?.disabledReason)
      .filter(Boolean);
    return [...new Set(reasons)].slice(0, 3);
  }

  function controlMetaLine({ generatedAt, reasons = [], fallback = "" } = {}) {
    const wrap = node("div", "control-meta-line");
    if (generatedAt) wrap.append(tag(`Updated ${shortDateTime(String(generatedAt).replace("T", " "))}`, "good"));
    if (fallback) wrap.append(node("span", "muted", fallback));
    reasons.forEach((reason) => wrap.append(node("span", "control-reason", reason)));
    return wrap.children.length ? wrap : null;
  }

  function draftControlButton(label, action, params, draft, selected, tone = "") {
    return controlButton({
      label,
      action,
      params,
      availability: draftActionAvailability(action, draft, selected),
      tone,
      className: "draft-control-button",
    });
  }

  function draftControlPanel(draft, commands, selected) {
    const draftState = draft?.state || null;
    const currentTeam = draftState?.current_team || "-";
    const userTeam = draftState?.user_team || data.activeSave?.user_team || "User";
    const currentPick = draftState?.current_pick_number ? `#${draftState.current_pick_number}` : "-";
    const remaining = Number(draft?.pickTotals?.remaining || 0);
    const onClockTone = isUserOnClock() ? "good" : draftState ? "warn" : "";
    const p = panel("Draft Control", draftState ? `${currentTeam} on clock` : "Setup");
    const body = panelBody(p);
    const hero = node("div", "control-hero draft-control-hero");
    append(hero, [
      teamLogo(currentDraftQueuePick(draft, draftState)?.teamLogo, currentTeam, "draft-control-logo"),
      append(node("div", "control-copy draft-control-copy"), [
        node("span", "tag", draftState ? `Pick ${currentPick}` : `Draft ${draft?.year || ""}`),
        node("strong", null, draftState ? `${currentTeam} is on the clock` : dateReached(draft?.draftDate) ? "Draft room is ready" : `Draft date ${shortDate(draft?.draftDate)}`),
        node("small", null, draftState
          ? `${remaining} pick(s) remaining. ${isUserOnClock() ? `${userTeam} can submit a pick now.` : "Skip CPU picks until your team is up."}`
          : dateReached(draft?.draftDate) ? "Start the room paused before making selections." : "Advance the calendar when you are ready."),
      ]),
      tag(isUserOnClock() ? "Your Pick" : draftState ? "CPU Pick" : "Not Started", onClockTone),
    ]);
    const controls = node("div", "control-bar draft-control-bar");
    const controlEntries = [
      [dateReached(draft?.draftDate) ? "Start Draft" : "Sim To Draft", "advance_to_draft", {}, "good"],
      ["Start Room", "draft_start", {}, "good"],
      ["Skip Pick", "draft_skip", { count: 1 }, ""],
      [`Skip To ${userTeam}`, "draft_skip_to_user", {}, ""],
      ["Make Pick", "draft_pick", selected?.prospect_id ? { prospect_id: selected.prospect_id } : {}, "good"],
      ["Finish Draft", "draft_finish", {}, "warn"],
    ].map(([label, action, params, tone]) => ({
      label,
      action,
      params,
      tone,
      availability: draftActionAvailability(action, draft, selected),
    }));
    append(controls, [
      ...controlEntries.map((entry) => controlButton({ ...entry, className: "draft-control-button" })),
    ]);
    const secondary = node("div", "control-secondary draft-control-secondary");
    append(secondary, [
      draftControlButton("Next League Year", "advance_next_league_year", {}, draft, selected),
      draftState?.current_team ? actionCard(
        `Ask ${draftState.current_team} GM`,
        "Run a draft strategy advisory for the team currently on the clock.",
        commands.aiGmRunDraft?.replace(`--team ${data.activeSave?.user_team || "MIN"}`, `--team ${draftState.current_team}`),
        "ai_gm_run",
        { team: draftState.current_team, decision_type: "draft_strategy_update" },
        "",
      ) : null,
    ]);
    append(body, [
      hero,
      controls,
      controlMetaLine({
        generatedAt: data.draftGeneratedAt,
        reasons: controlDisabledReasons(controlEntries),
      }),
      secondary,
    ]);
    return p;
  }

  function actionCard(title, detail, command, action, params, tone, options = {}) {
    const card = node("div", `action-card ${tone || ""}`.trim());
    const text = append(node("div", "action-copy"), [
      node("strong", null, title),
      detail ? node("span", null, detail) : null,
    ]);
    const controls = node("div", "action-controls");
    if (action && runnerMode()) {
      const run = node("button", "primary-run-button", state.runnerBusy ? "Running" : (options.runLabel || "Run"));
      run.type = "button";
      run.disabled = state.runnerBusy || Boolean(options.disabledReason);
      if (options.disabledReason) run.title = options.disabledReason;
      run.addEventListener("click", () => runAction(action, params));
      controls.append(run);
    } else if (action) {
      controls.append(node("span", "muted", "Live actions unavailable"));
    }
    append(card, [text, controls]);
    return card;
  }

  function compactRunButton(label, action, params, tone) {
    const button = node("button", `run-button compact ${tone || ""}`.trim(), state.runnerBusy ? "Running" : label);
    button.type = "button";
    button.disabled = state.runnerBusy || !runnerMode();
    if (!runnerMode()) button.title = "Live actions are unavailable";
    button.addEventListener("click", () => runAction(action, params));
    return button;
  }

  function reviewItemActions(item) {
    const actions = node("div", "review-action-row");
    const reviewId = Number(item.review_id);
    const status = item.lifecycle_status || "pending_review";
    const show = node("button", "run-button compact", "Show");
    show.type = "button";
    show.addEventListener("click", (event) => {
      event.stopPropagation();
      state.selectedAiGmReviewId = reviewId;
      render();
    });
    actions.append(show);
    if (status === "pending_review") {
      actions.append(compactRunButton("Approve", "ai_gm_review_update", {
        review_id: reviewId,
        status: "approved",
        reviewed_by: "ui",
      }));
      const reject = node("button", "run-button compact danger", state.runnerBusy ? "Running" : "Reject");
      reject.type = "button";
      reject.disabled = state.runnerBusy || !runnerMode();
      if (!runnerMode()) reject.title = "Live actions are unavailable";
      reject.addEventListener("click", (event) => {
        event.stopPropagation();
        const note = window.prompt("Review note", "Rejected in Game Center");
        if (note === null) return;
        runAction("ai_gm_review_update", {
          review_id: reviewId,
          status: "rejected",
          note,
          reviewed_by: "ui",
        });
      });
      actions.append(reject);
    }
    if (status === "approved" || status === "blocked") {
      actions.append(compactRunButton("Dry Run", "ai_gm_review_apply", { review_id: reviewId }));
      actions.append(compactRunButton("Apply", "ai_gm_review_apply", { review_id: reviewId, apply: true }, "danger"));
    }
    return actions;
  }

  function aiGmReviewItems(ai) {
    const seen = new Set();
    const items = [];
    [...(ai.reviewInbox || []), ...(ai.reviewActivity || [])].forEach((item) => {
      const id = Number(item.review_id);
      if (!id || seen.has(id)) return;
      seen.add(id);
      items.push(item);
    });
    return items;
  }

  function selectedAiGmReview(ai) {
    const items = aiGmReviewItems(ai);
    if (!items.length) {
      state.selectedAiGmReviewId = null;
      return null;
    }
    let selected = items.find((item) => Number(item.review_id) === Number(state.selectedAiGmReviewId));
    if (!selected) {
      selected = items[0];
      state.selectedAiGmReviewId = Number(selected.review_id);
    }
    return selected;
  }

  function jsonBlock(value) {
    const details = node("details", "json-details");
    details.append(node("summary", null, "Raw payload"));
    details.append(node("pre", "runner-output", JSON.stringify(value || {}, null, 2)));
    return details;
  }

  function reviewDetailTextList(title, values) {
    const list = node("div", "list compact-list");
    asList(values).slice(0, 8).forEach((value) => list.append(row(String(value), "", "")));
    return sectionBlock(title, list.children.length ? list : node("div", "empty-state", "None"));
  }

  function renderReviewDetailPanel(item) {
    const panelEl = panel("Review Item Detail", item ? `#${item.review_id}` : "No Selection");
    const body = panelBody(panelEl);
    if (!item) {
      body.append(node("div", "empty-state", "No AI GM review item is selected."));
      return panelEl;
    }
    const detail = item.detail || {};
    const plan = detail.plan || {};
    const queue = detail.queue || {};
    const validation = detail.validation || plan.validation || {};
    const blockers = [...asList(detail.blockers), ...asList(validation.errors), ...asList(item.blockers)];
    const warnings = [...asList(detail.warnings), ...asList(validation.warnings)];
    const title = node("div", "review-detail-head");
    append(title, [
      node("strong", null, item.title || item.summary || "AI GM review item"),
      tag(item.lifecycle_status || "-", reviewStatusTone(item.lifecycle_status)),
      tag(item.risk_tier || "risk", item.risk_tier === "high" ? "bad" : item.risk_tier === "medium" ? "warn" : ""),
    ]);
    body.append(title);
    body.append(detailGrid([
      ["Team", item.team || "-"],
      ["Type", item.item_type || item.operation_type || "-"],
      ["Artifact", item.artifact_label || item.artifact_type || "-"],
      ["Decision", item.decision_type || "-"],
      ["Operation", item.operation_type || "-"],
      ["Updated", shortDateTime(item.activity_time || item.updated_at || item.created_at)],
    ], "compact"));
    body.append(node("p", "review-detail-summary", item.summary || item.result_summary || "-"));

    const controls = node("div", "review-detail-actions");
    controls.append(reviewItemActions(item));
    controls.append(compactRunButton("CLI Show", "ai_gm_review_show", { review_id: Number(item.review_id) }));
    body.append(controls);

    const grid = node("div", "scout-note-grid");
    grid.append(reviewDetailTextList("Blockers", blockers));
    grid.append(reviewDetailTextList("Warnings", warnings));
    const facts = node("div", "list compact-list");
    [
      ["Validation", validation.status || item.plan_validation_status || "-"],
      ["Queue", queue.status || item.queued_status || "-"],
      ["Result", item.result_summary || "-"],
      ["Reviewed By", item.reviewed_by || "-"],
      ["Review Note", item.review_note || "-"],
      ["Apply Error", item.apply_error || "-"],
    ].forEach(([label, value]) => facts.append(row(label, value, "")));
    grid.append(sectionBlock("Decision Facts", facts));
    body.append(grid);
    body.append(jsonBlock({
      detail,
      apply_result: item.apply_result || {},
    }));
    return panelEl;
  }

  function reviewItemType(item) {
    const artifact = item.artifact_id ? `${item.artifact_type || "item"} #${item.artifact_id}` : (item.artifact_type || "-");
    return append(node("span", "player-name-stack"), [
      node("strong", null, item.item_type || item.operation_type || "-"),
      node("small", null, artifact),
    ]);
  }

  function reviewItemSummary(item) {
    const summary = item.summary || item.title || "-";
    const wrap = node("span", "review-summary", summary);
    wrap.title = summary;
    return wrap;
  }

  function reviewStatusTone(status) {
    return {
      applied: "good",
      approved: "good",
      pending_review: "warn",
      blocked: "bad",
      rejected: "bad",
      stale: "warn",
      expired: "warn",
    }[status] || "";
  }

  function reviewActivityOutcome(item) {
    const wrap = node("span", "review-summary");
    append(wrap, [
      node("strong", null, item.result_summary || item.summary || item.title || "-"),
      item.review_note ? node("small", null, item.review_note) : null,
    ]);
    return wrap;
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
      street_market: "Street Free Agency",
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

  function freeAgencyActionAvailability(action, fa) {
    const period = fa?.period || null;
    const stage = String(period?.current_stage || "");
    if (action === "free_agency_start") {
      if (period) return { disabledReason: "Free agency is already open." };
      return {};
    }
    if (action === "free_agency_cpu_seed") {
      if (!period) return { disabledReason: "Open free agency first." };
      if (Number(fa?.counts?.available || 0) <= 0) return { disabledReason: "No available free agents." };
      return {};
    }
    if (action === "free_agency_advance_hour") {
      if (!period) return { disabledReason: "Open free agency first." };
      if (stage !== "day_one_hourly") return { disabledReason: "Hourly advance is only for Day 1." };
      return {};
    }
    if (action === "free_agency_advance_day") {
      if (!period) return { disabledReason: "Open free agency first." };
      if (stage === "day_one_hourly") return { disabledReason: "Finish Day 1 hourly windows first." };
      return {};
    }
    if (action === "advance_to_draft") {
      if (data.draft?.state) return { disabledReason: "Draft room is already active." };
      return {};
    }
    return {};
  }

  function freeAgencyControlButton(label, action, params, fa, tone = "") {
    return controlButton({
      label,
      action,
      params,
      availability: freeAgencyActionAvailability(action, fa),
      tone,
      className: "fa-control-button",
    });
  }

  function freeAgencyControlPanel(fa, commands) {
    const period = fa?.period || null;
    const stageLabel = period ? freeAgencyStageLabel(period.current_stage) : "Not Started";
    const pending = Number(fa?.counts?.pendingOffers || 0);
    const available = Number(fa?.counts?.available || 0);
    const signed = Number(fa?.counts?.signed || 0);
    const clock = period
      ? `${shortDate(period.current_date)}${period.current_stage === "day_one_hourly" ? ` ${period.current_hour || 12}:00` : ""}`
      : shortDate(fa?.startDate);
    const p = panel("Free Agency Control", stageLabel);
    const body = panelBody(p);
    const hero = node("div", "control-hero fa-control-hero");
    append(hero, [
      append(node("div", "control-copy fa-control-copy"), [
        node("span", "tag", stageLabel),
        node("strong", null, period ? `Market clock: ${clock}` : `Scheduled start: ${clock}`),
        node("small", null, period
          ? `${available} available, ${pending} pending offer(s), ${signed} signing(s) logged.`
          : "Open free agency to process expiring contracts and create the market pool."),
      ]),
      tag(pending ? `${pending} Pending` : "No Pending", pending ? "warn" : "good"),
    ]);
    const controls = node("div", "control-bar fa-control-bar");
    const controlEntries = [
      ["Open FA", "free_agency_start", {}, "good"],
      ["Seed CPU Offers", "free_agency_cpu_seed", {}, ""],
      ["Advance Hour", "free_agency_advance_hour", {}, period?.current_stage === "day_one_hourly" ? "good" : ""],
      ["Advance Day", "free_agency_advance_day", {}, period?.current_stage === "daily" ? "good" : ""],
      ["Advance To Draft", "advance_to_draft", {}, "warn"],
    ].map(([label, action, params, tone]) => ({
      label,
      action,
      params,
      tone,
      availability: freeAgencyActionAvailability(action, fa),
    }));
    append(controls, [
      ...controlEntries.map((entry) => controlButton({ ...entry, className: "fa-control-button" })),
    ]);
    const secondary = node("div", "control-secondary fa-control-secondary");
    append(secondary, [
      node("span", "muted", "Manual player offers stay on the free-agent board rows."),
    ]);
    append(body, [
      hero,
      controls,
      controlMetaLine({
        generatedAt: data.freeAgencyGeneratedAt,
        reasons: controlDisabledReasons(controlEntries),
      }),
      secondary,
    ]);
    return p;
  }

  function seasonPhaseState(season) {
    const totalGames = Number(season?.totals?.games || 0);
    const remaining = Number(season?.totals?.remaining || 0);
    const nextWeek = Number(season?.nextWeek || 0);
    const postseasonGames = Number(season?.postseason?.games || 0);
    const postseasonRemaining = Number(season?.postseason?.remaining || 0);
    const regularStarted = totalGames > 0;
    const regularDone = regularStarted && remaining === 0;
    const playoffsDone = regularDone && postseasonGames > 0 && postseasonRemaining === 0;
    if (nextWeek > 0 || remaining > 0) return "regular";
    if (regularDone && postseasonGames === 0) return "postseason_setup";
    if (regularDone && postseasonRemaining > 0) return "postseason";
    if (playoffsDone && !season?.completion) return "completion";
    if (season?.completion) return "completed";
    return "idle";
  }

  function seasonActionAvailability(action, season) {
    const phase = seasonPhaseState(season);
    if (action === "sim_week") {
      if (!Number(season?.nextWeek || 0)) return { disabledReason: "No regular-season week is queued." };
      return {};
    }
    if (action === "sim_season") {
      if (phase !== "regular" && phase !== "postseason_setup") return { disabledReason: "Regular season is already complete." };
      return {};
    }
    if (action === "postseason") {
      if (phase !== "postseason") return { disabledReason: "Postseason is not ready to run." };
      return {};
    }
    if (action === "complete_season") {
      if (phase !== "completion") return { disabledReason: "Complete the postseason first." };
      return {};
    }
    return {};
  }

  function seasonControlButton(label, action, params, season, tone = "") {
    return controlButton({
      label,
      action,
      params,
      availability: seasonActionAvailability(action, season),
      tone,
      className: "season-control-button",
    });
  }

  function seasonControlPanel(season) {
    const phase = seasonPhaseState(season);
    const nextWeek = Number(season?.nextWeek || 0);
    const remaining = Number(season?.totals?.remaining || 0);
    const postseasonRemaining = Number(season?.postseason?.remaining || 0);
    const title = {
      regular: nextWeek ? `Week ${nextWeek} Ready` : "Regular Season",
      postseason_setup: "Playoff Tree Ready",
      postseason: "Postseason Ready",
      completion: "Season Completion Ready",
      completed: "Season Complete",
      idle: "Season Idle",
    }[phase] || "Season";
    const detail = {
      regular: `${remaining} regular-season game(s) remaining.`,
      postseason_setup: "Generate playoff seedings and Wild Card matchups.",
      postseason: `${postseasonRemaining} postseason game(s) remaining.`,
      completion: "Progression, draft order, and offseason rollover are ready.",
      completed: "The season has already been rolled forward.",
      idle: "No immediate season simulation action is queued.",
    }[phase] || "";
    const p = panel("Season Control", title);
    const body = panelBody(p);
    const hero = node("div", "control-hero season-control-hero");
    append(hero, [
      append(node("div", "control-copy season-control-copy"), [
        node("span", "tag", season?.season || data.currentSeason || "Season"),
        node("strong", null, title),
        node("small", null, detail),
      ]),
      tag(phase === "completed" ? "Complete" : phase === "idle" ? "Idle" : "Actionable", phase === "completed" ? "good" : phase === "idle" ? "" : "warn"),
    ]);
    const controls = node("div", "control-bar season-control-bar");
    const controlEntries = [
      ["Sim Next Week", "sim_week", { week: nextWeek }, "good"],
      ["Sim Regular Season", "sim_season", {}, "warn"],
      ["Run Postseason", "postseason", {}, "good"],
      ["Complete Season", "complete_season", {}, "good"],
    ].map(([label, action, params, tone]) => ({
      label,
      action,
      params,
      tone,
      availability: seasonActionAvailability(action, season),
    }));
    append(controls, [
      ...controlEntries.map((entry) => controlButton({ ...entry, className: "season-control-button" })),
    ]);
    const secondary = node("div", "control-secondary season-control-secondary");
    append(secondary, [
      node("span", "muted", "Full regular-season sims can take a few minutes because weekly hooks, stats, and staff systems run after games."),
    ]);
    append(body, [
      hero,
      controls,
      controlMetaLine({
        generatedAt: data.seasonGeneratedAt,
        reasons: controlDisabledReasons(controlEntries),
      }),
      secondary,
    ]);
    return p;
  }

  function calendarActionAvailability(action, calendar, nextWeek) {
    const nextEvent = calendar?.nextEvent || (data.events || [])[0];
    if (action === "advance_next_event" && !nextEvent) {
      return { disabledReason: "No next calendar event is available." };
    }
    if (action === "advance_to_date") {
      return {};
    }
    if (action === "sim_week" && !nextWeek) {
      return { disabledReason: "No regular-season week is queued." };
    }
    if (action === "sim_season") {
      return seasonActionAvailability(action, data.season || {});
    }
    if (action === "event_generate_week") {
      if (!data.currentSeason && !data.season?.season) return { disabledReason: "No active season is available." };
      return {};
    }
    return {};
  }

  function calendarControlButton(label, action, params, calendar, nextWeek, tone = "") {
    return controlButton({
      label,
      action,
      params,
      availability: calendarActionAvailability(action, calendar, nextWeek),
      tone,
      className: "calendar-control-button",
    });
  }

  function calendarControlPanel(calendar, nextEvent, nextWeek) {
    const eventDate = nextEvent?.event_start_date ? shortDate(nextEvent.event_start_date) : "No date";
    const p = panel("Calendar Control", nextEvent ? eventDate : "No Advance Target");
    const body = panelBody(p);
    const hero = node("div", "control-hero calendar-control-hero");
    append(hero, [
      append(node("div", "control-copy calendar-control-copy"), [
        node("span", "tag", data.currentDate ? `Current ${shortDate(data.currentDate)}` : "Calendar"),
        node("strong", null, nextEvent?.event_name || (nextWeek ? `Week ${nextWeek} Ready` : "No immediate calendar action")),
        node("small", null, nextEvent
          ? `${eventDate}${nextEvent.phase_name ? ` | ${nextEvent.phase_name}` : ""}${nextEvent.notes ? ` | ${nextEvent.notes}` : ""}`
          : nextWeek ? "Sim the next unfinished regular-season week." : "The active save has no exported next event or week."),
      ]),
      tag("Calendar", "good"),
    ]);
    const controls = node("div", "control-bar calendar-control-bar");
    const controlEntries = [
      ["Advance Date", "advance_next_event", {}, "good"],
      ["Sim Rest Season", "sim_season", {}, "warn"],
      [nextWeek ? `Sim Week ${nextWeek}` : "Sim Week", "sim_week", { week: nextWeek }, "good"],
    ];
    if (data.draft?.draftDate && !data.draft?.state && String(data.currentPhase || "").toLowerCase().includes("offseason")) {
      controlEntries.push([
        dateReached(data.draft.draftDate) ? "Start Draft" : "Sim To Draft",
        "advance_to_draft",
        {},
        "good",
      ]);
    }
    const mappedControlEntries = controlEntries.map(([label, action, params, tone]) => ({
      label,
      action,
      params,
      tone,
      availability: calendarActionAvailability(action, calendar, nextWeek),
    }));
    append(controls, [
      ...mappedControlEntries.map((entry) => controlButton({ ...entry, className: "calendar-control-button" })),
    ]);
    const upcoming = (calendar?.upcomingEvents || []).slice(0, 12);
    const milestoneStrip = node("div", "calendar-milestone-strip");
    if (upcoming.length) {
      milestoneStrip.append(node("span", "muted", "Advance to"));
      upcoming.forEach((event) => {
        const label = `${shortDate(event.event_start_date)} ${event.event_name || "Calendar Event"}`;
        milestoneStrip.append(controlButton({
          label,
          action: "advance_to_date",
          params: { date: event.event_start_date },
          availability: {},
          tone: String(event.event_code || "").includes("PRESEASON") || String(event.event_code || "").includes("CAMP") ? "good" : "",
          className: "calendar-milestone-button",
        }));
      });
    }
    append(body, [
      hero,
      controls,
      milestoneStrip,
      controlMetaLine({
        generatedAt: data.calendarGeneratedAt,
        reasons: controlDisabledReasons(mappedControlEntries),
      }),
    ]);
    return p;
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
    const awayScore = game.away_score ?? "-";
    const homeScore = game.home_score ?? "-";
    const title = played
      ? `${away} ${awayScore} at ${home} ${homeScore}`
      : `${away} at ${home}`;
    const detail = `Week ${game.week || "-"} | ${shortDate(game.game_date)}${game.game_time_et ? ` | ${game.game_time_et} ET` : ""}`;
    let right = played ? `Final ${awayScore}-${homeScore}` : "Upcoming";
    let tone = played ? "good" : "";
    if (userTeam && (away === userTeam || home === userTeam)) {
      if (played) {
        const userScore = away === userTeam ? Number(game.away_score || 0) : Number(game.home_score || 0);
        const oppScore = away === userTeam ? Number(game.home_score || 0) : Number(game.away_score || 0);
        right = `${userScore > oppScore ? "Win" : userScore < oppScore ? "Loss" : "Tie"} ${userScore}-${oppScore}`;
        tone = userScore > oppScore ? "good" : userScore < oppScore ? "bad" : "warn";
      } else {
        right = "Vikings";
        tone = "warn";
      }
    }
    const item = row(title, detail, right, tone);
    if (played && runnerMode() && game.game_id) {
      item.classList.add("clickable-row");
      item.title = "Show box score";
      item.addEventListener("click", () => {
        if (state.view === "calendar") {
          loadCalendarBoxScore(game.game_id);
          return;
        }
        runAction("box_score", { game_id: game.game_id });
      });
    }
    return item;
  }

  function runnerOutputPanel() {
    if (state.runnerBusy) {
      const p = panel("Action Status", actionLabel(state.busyAction));
      const body = node("div", "action-status-card running");
      append(body, [
        node("span", "spinner"),
        append(node("div"), [
          node("strong", null, `${actionLabel(state.busyAction)} in progress`),
          node("p", "muted", busyMessage()),
        ]),
      ]);
      panelBody(p).append(body);
      return p;
    }
    if (!state.lastResult) return null;
    const result = state.lastResult;
    const ok = result.returncode === 0 && !result.error;
    if (result.action === "box_score" && ok) {
      const p = panel("Box Score", result.summary?.message || "Stored game result");
      const body = panelBody(p);
      const text = String(result.stdout || "").trim();
      body.append(text ? node("pre", "box-score-output", text) : node("div", "empty-state", "No stored box score text was returned for this game."));
      return p;
    }
    const p = panel("Action Status", result.summary?.title || actionLabel(result.action) || "Latest");
    const status = node("div", `action-status-card ${ok ? "good" : "bad"}`);
    const summary = result.summary;
    const title = summary?.title || actionLabel(result.action) || "Recent Action";
    const message = summary?.message || (ok ? "Completed successfully." : "The action could not be completed.");
    append(status, [
      node("span", "status-dot"),
      append(node("div"), [
        node("strong", null, title),
        node("p", "muted", message),
      ]),
    ]);
    panelBody(p).append(status);
    const facts = node("div", "result-facts");
    append(facts, [
      summary?.affectedPanels?.length ? metric("Updated", summary.affectedPanels.join(", "), "Refreshed screens") : null,
      summary?.durationSeconds !== undefined ? metric("Time", `${summary.durationSeconds}s`, "Action duration") : null,
      !ok ? metric("Status", "Needs Attention", "Check the screen and try again", "bad") : null,
    ]);
    if (facts.children.length) panelBody(p).append(facts);
    if (!ok) {
      const cleanError = summary?.message || result.error || "Something went wrong.";
      panelBody(p).append(node("div", "friendly-error", cleanError));
    }
    return p;
  }

  function runnerBusyBanner() {
    if (!state.runnerBusy) return null;
    const cancellable = cancellableRunnerAction(state.busyAction);
    const banner = node("div", "runner-busy-banner");
    const text = append(node("div"), [
      node("strong", null, `${actionLabel(state.busyAction)} is running`),
      node(
        "span",
        null,
        cancellable
          ? "Keep this page open. Stop safely pauses after the current game or weekly hook finishes."
          : "Keep this page open. The affected panels will refresh when the action finishes."
      ),
    ]);
    const stop = cancellable ? node("button", "runner-cancel-button", state.cancelRequested ? "Stop requested" : "Stop safely") : null;
    if (stop) {
      stop.type = "button";
      stop.disabled = state.cancelRequested;
      stop.addEventListener("click", requestRunnerCancel);
    }
    append(banner, [node("span", "spinner"), text, stop]);
    return banner;
  }

  function boxScoreModal() {
    const result = state.boxScoreModal;
    if (!result) return null;
    const overlay = node("div", "box-score-modal-overlay");
    const modal = node("section", "box-score-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("strong", null, result.returncode === 0 ? "Box Score" : "Box Score Unavailable"),
      node("small", null, result.params?.game_id ? `Game ${result.params.game_id}` : "Stored game result"),
    ]);
    const close = node("button", "icon-button close-button", "Close");
    close.type = "button";
    close.addEventListener("click", () => {
      state.boxScoreModal = null;
      render();
    });
    append(top, [title, close]);
    const body = node("div", "box-score-modal-body");
    if (result.returncode === 0 && !result.error) {
      const text = String(result.stdout || "").trim();
      body.append(text ? node("pre", "box-score-output box-score-modal-output", text) : node("div", "empty-state", "No stored box score text was returned for this game."));
    } else {
      body.append(node("div", "friendly-error", result.summary?.message || result.stderr || result.error || "Box score could not be loaded."));
    }
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        state.boxScoreModal = null;
        render();
      }
    });
    return overlay;
  }

  function injuryAlertModal() {
    const alerts = Array.isArray(state.injuryModal) ? state.injuryModal : [];
    if (!alerts.length) return null;
    const overlay = node("div", "box-score-modal-overlay injury-alert-overlay");
    const modal = node("section", "box-score-modal injury-alert-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("strong", null, "Injury Report"),
      node("small", null, `${alerts.length} player${alerts.length === 1 ? "" : "s"} expected to miss time`),
    ]);
    const close = node("button", "icon-button close-button", "Close");
    close.type = "button";
    close.addEventListener("click", () => {
      state.injuryModal = null;
      render();
    });
    append(top, [title, close]);

    const body = node("div", "box-score-modal-body injury-alert-body");
    const list = node("div", "injury-alert-list");
    alerts.forEach((alert) => {
      const card = node("article", "injury-alert-card");
      const meta = [
        alert.team,
        alert.position,
        alert.severity ? String(alert.severity).toUpperCase() : null,
      ].filter(Boolean).join(" | ");
      const expected = Number(alert.expectedGames || 0);
      const cardTop = node("div", "injury-alert-card-top");
      append(cardTop, [
        playerLink(alert.playerId, alert.playerName, "player-link strong-link", {
          team: alert.team,
          position: alert.position,
        }),
        node("span", "injury-alert-duration", `${expected} game${expected === 1 ? "" : "s"}`),
      ]);
      append(card, [
        cardTop,
        node("div", "injury-alert-meta", meta),
        node("p", null, alert.message || `${alert.injury || "Injury"}; status ${alert.status || "Unavailable"}.`),
      ]);
      list.append(card);
    });
    append(body, [
      node("div", "injury-alert-summary", "These updates were added to your inbox. Major league injuries were also sent to league news."),
      list,
    ]);
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        state.injuryModal = null;
        render();
      }
    });
    return overlay;
  }

  function rosterCutdownModal() {
    const prompt = state.rosterCutdownPrompt;
    if (!prompt) return null;
    if (!rosterGateStillRelevant()) {
      state.rosterCutdownPrompt = null;
      state.rosterCutdownPromptDismissedKey = prompt.key || currentRosterGateKey();
      return null;
    }
    const overlay = node("div", "box-score-modal-overlay injury-alert-overlay");
    const modal = node("section", "box-score-modal injury-alert-modal roster-cutdown-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("strong", null, prompt.title || "Roster Cutdown Needed"),
      node("small", null, "Final cuts and practice squad decisions are due"),
    ]);
    const close = node("button", "icon-button close-button", "Close");
    close.type = "button";
    close.addEventListener("click", () => {
      state.rosterCutdownPromptDismissedKey = prompt.key || currentRosterGateKey();
      state.rosterCutdownPrompt = null;
      render();
    });
    append(top, [title, close]);

    const body = node("div", "box-score-modal-body injury-alert-body");
    const actions = node("div", "control-bar roster-cutdown-actions");
    const manage = node("button", "control-button good", "Open Practice Squad Selection");
    manage.type = "button";
    manage.addEventListener("click", () => {
      state.rosterCutdownPromptDismissedKey = prompt.key || currentRosterGateKey();
      state.rosterCutdownPrompt = null;
      switchView("practiceSquad", { refresh: true });
    });
    const auto = node("button", "control-button warn", "Auto Cutdown And Continue");
    auto.type = "button";
    auto.disabled = state.runnerBusy || !runnerMode();
    auto.addEventListener("click", () => {
      const stoppedAction = prompt.stoppedAction || state.lastResult?.action || "sim_season";
      const stoppedParams = prompt.stoppedParams || state.lastResult?.params || {};
      state.rosterCutdownPrompt = null;
      runAction("auto_cutdown_continue", {
        continue_action: stoppedAction,
        continue_params: stoppedParams,
      });
    });
    append(actions, [manage, auto]);
    append(body, [
      node("p", null, prompt.message || "Your active roster and practice squad need to be settled before the regular season can continue."),
      node("p", "muted", "Handle it yourself in Roster Hub, or let the CPU apply a cutdown/practice-squad plan and continue the sim."),
      actions,
    ]);
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        state.rosterCutdownPromptDismissedKey = prompt.key || currentRosterGateKey();
        state.rosterCutdownPrompt = null;
        render();
      }
    });
    return overlay;
  }

  function rosterGateCountsFromState() {
    const depth = data.depthChart || {};
    const rows = rosterRows(depth);
    if (!rows.length) return null;
    const activeStatuses = new Set(["active", "questionable", "doubtful", "out"]);
    const activeCount = rows.filter((player) => activeStatuses.has(String(player.status || "Active").toLowerCase())).length;
    const practiceSquadCount = rows.filter((player) => isPracticeSquadPlayer(player)).length;
    return { activeCount, practiceSquadCount };
  }

  function rosterGateStillRelevant() {
    const currentDate = String(data.currentDate || data.activeSave?.current_date || "");
    const season = Number(data.currentSeason || data.season?.season || data.activeSave?.current_league_year || 0);
    if (!currentDate || !season) return true;
    return currentDate <= `${season}-09-15`;
  }

  function currentRosterGateKey() {
    const counts = rosterGateCountsFromState();
    if (!counts) return "";
    return [
      data.activeSave?.game_id || data.activeSave?.save_id || "",
      data.currentDate || data.activeSave?.current_date || "",
      counts.activeCount,
      counts.practiceSquadCount,
    ].join(":");
  }

  function maybeShowRosterGatePromptFromState() {
    if (state.rosterCutdownPrompt || state.runnerBusy) return;
    const phase = String(data.currentPhase || data.activeSave?.current_phase_code || data.settings?.current_calendar_phase || "").toLowerCase();
    if (!phase.includes("cutdown") && !phase.includes("practice squad")) return;
    if (!rosterGateStillRelevant()) return;
    const counts = rosterGateCountsFromState();
    if (!counts) return;
    const activeLimit = 53;
    const practiceSquadLimit = 16;
    const issues = [];
    if (counts.activeCount > activeLimit) issues.push(`cut active roster from ${counts.activeCount} to ${activeLimit}`);
    if (counts.practiceSquadCount < practiceSquadLimit) issues.push(`fill practice squad (${counts.practiceSquadCount}/${practiceSquadLimit})`);
    if (!issues.length) return;
    const key = currentRosterGateKey();
    if (key && state.rosterCutdownPromptDismissedKey === key) return;
    const team = data.activeSave?.user_team || data.depthChart?.team || "your team";
    state.rosterCutdownPrompt = {
      key,
      title: "Roster Cutdown Needed",
      message: `Roster cutdown/practice squad setup required for ${team}: ${issues.join("; ")}.`,
      stoppedAction: "sim_season",
      stoppedParams: {},
    };
  }

  function finishRender(root) {
    const banner = runnerBusyBanner();
    if (banner) root.prepend(banner);
    const boxScore = boxScoreModal();
    if (boxScore) root.append(boxScore);
    const injuryAlerts = injuryAlertModal();
    if (injuryAlerts) root.append(injuryAlerts);
    const cutdownPrompt = rosterCutdownModal();
    if (cutdownPrompt) root.append(cutdownPrompt);
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
        switchView(item.view || "leagueNews");
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
      switchView(view);
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
    setHeader("League Table", "Compact standings by conference and division.");
    const root = document.createDocumentFragment();
    const season = data.season || { weeks: [], totals: {}, postseason: {} };
    const currentSeason = season.season || data.currentSeason || "";
    if (runnerMode() && String(state.seasonLiveSeason || "") !== String(currentSeason || "") && !state.seasonLoading) {
      loadLiveSeason().then(render);
    }

    const summary = node("section", "league-table-summary");
    append(summary, [
      metric("Season", String(currentSeason || "-"), data.seasonGeneratedAt ? `Updated ${shortDateTime(data.seasonGeneratedAt.replace("T", " "))}` : "Current year"),
      metric("Regular Season", `${season.totals?.played || 0}/${season.totals?.games || 0}`, `${season.totals?.remaining || 0} left`, season.totals?.remaining ? "warn" : "good"),
      metric("Next Week", season.nextWeek ? `Week ${season.nextWeek}` : "Done", "Schedule queue"),
      metric("Postseason", season.postseason?.remaining ? `${season.postseason.remaining} left` : "Idle", `${season.postseason?.games || 0} games`),
    ]);
    if (state.seasonLoading) summary.append(node("div", "empty-state", "Refreshing live season table..."));
    root.append(summary);

    root.append(standingsBoard(season.standings || []));

    const scheduleGrid = node("div", "grid league-table-lower-grid");
    const userTeam = data.activeSave?.user_team;
    const userSchedule = panel(userTeam ? `${userTeam} Schedule` : "User Team Schedule", "Regular Season");
    const scheduleList = node("div", "list compact-list");
    (season.userTeamSchedule || []).slice(0, 18).forEach((game) => {
      scheduleList.append(gameLine(game, userTeam));
    });
    panelBody(userSchedule).append(scheduleList.children.length ? scheduleList : node("div", "empty-state", "No user-team schedule exported."));

    const resultsPanel = panel("Recent Finals", "League");
    const resultsList = node("div", "list compact-list");
    (season.recentResults || []).slice(0, 12).forEach((game) => {
      resultsList.append(gameLine(game, userTeam));
    });
    panelBody(resultsPanel).append(resultsList.children.length ? resultsList : node("div", "empty-state", "No completed games yet."));
    append(scheduleGrid, [userSchedule, resultsPanel]);
    root.append(scheduleGrid);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function playoffRoundOrder(code) {
    return { WC: 1, DIV: 2, CONF: 3, SB: 4 }[String(code || "").toUpperCase()] || 9;
  }

  function playoffRoundShortName(code, name) {
    return {
      WC: "Wild Card",
      DIV: "Divisional",
      CONF: "Conference",
      SB: "Super Bowl",
    }[String(code || "").toUpperCase()] || name || "Round";
  }

  function playoffTeamLine(game, side) {
    const prefix = side === "away" ? "away" : "home";
    const team = game[`${prefix}_team`] || "-";
    const score = game[`${prefix}_score`];
    const winner = game.winner_team && game.winner_team === team;
    const line = node("div", `playoff-team-line ${winner ? "winner" : ""}`.trim());
    append(line, [
      teamLogo(game[`${prefix}Logo`], team, "playoff-team-logo"),
      node("strong", null, team),
      node("span", "playoff-seed", side === "away" ? seedLabel(game.low_seed) : seedLabel(game.high_seed)),
      node("span", "playoff-score", score ?? "-"),
    ]);
    return line;
  }

  function seedLabel(seed) {
    const value = Number(seed || 0);
    return value ? `#${value}` : "";
  }

  function playoffGameCard(game) {
    const played = Number(game.played || 0) === 1 || Boolean(game.winner_team);
    const card = node("article", `playoff-game-card ${played ? "final" : "pending"}`.trim());
    const meta = node("div", "playoff-game-meta");
    append(meta, [
      tag(game.conference || "NFL", game.conference === "AFC" ? "warn" : game.conference === "NFC" ? "good" : ""),
      node("span", null, game.game_date ? shortDate(game.game_date) : "TBD"),
    ]);
    append(card, [
      meta,
      playoffTeamLine(game, "away"),
      playoffTeamLine(game, "home"),
    ]);
    if (played && runnerMode() && game.game_id) {
      const button = node("button", "copy-button playoff-box-score", "Box Score");
      button.type = "button";
      button.addEventListener("click", () => loadCalendarBoxScore(game.game_id));
      card.append(button);
      card.classList.add("clickable-card");
      card.addEventListener("dblclick", () => loadCalendarBoxScore(game.game_id));
    } else {
      card.append(node("small", "muted", "Awaiting result"));
    }
    return card;
  }

  function renderPlayoffTree() {
    setHeader("Playoffs Tree", "Bracket view with box scores as each playoff round finishes.");
    const root = document.createDocumentFragment();
    const season = data.season || { postseason: {}, totals: {} };
    const postseason = season.postseason || {};
    const currentSeason = season.season || data.currentSeason || "";
    if (runnerMode() && String(state.seasonLiveSeason || "") !== String(currentSeason || "") && !state.seasonLoading) {
      loadLiveSeason().then(render);
    }

    const summary = node("section", "league-table-summary playoff-summary");
    append(summary, [
      metric("Regular Season", Number(season.totals?.remaining || 0) ? "In Progress" : "Complete", `${season.totals?.played || 0}/${season.totals?.games || 0}`),
      metric("Playoff Games", `${postseason.played || 0}/${postseason.games || 0}`, `${postseason.remaining || 0} remaining`, Number(postseason.remaining || 0) ? "warn" : Number(postseason.games || 0) ? "good" : ""),
      metric("Rounds", String((postseason.rounds || []).length || "-"), "Bracket stages"),
    ]);
    const controls = node("div", "control-bar playoff-tree-actions");
    const simRound = node("button", "control-button good", "Sim Playoff Round");
    simRound.type = "button";
    simRound.disabled = !runnerMode() || state.runnerBusy || Number(postseason.remaining || 0) <= 0;
    simRound.title = Number(postseason.remaining || 0) > 0 ? "Sim the next unplayed playoff round." : "No unplayed playoff games remain.";
    simRound.addEventListener("click", () => runAction("postseason_round", {}));
    controls.append(simRound);
    summary.append(controls);
    if (state.seasonLoading) summary.append(node("div", "empty-state", "Refreshing playoff tree..."));
    root.append(summary);

    const matchups = postseason.matchups || [];
    if (!playoffTreeVisible()) {
      root.append(node("div", "empty-state", "The playoff tree appears after the regular season is complete."));
      finishRender(root);
      return;
    }
    if (!matchups.length) {
      const waiting = panel("Bracket Pending", "Regular season complete");
      panelBody(waiting).append(node("div", "empty-state", "No playoff matchups are generated yet. Run postseason setup or advance to the playoff round."));
      root.append(waiting);
      const output = runnerOutputPanel();
      if (output) root.append(output);
      finishRender(root);
      return;
    }

    const bracket = panel("Bracket", `${shortDateTime((data.seasonGeneratedAt || "").replace("T", " ")) || "Live"}`);
    bracket.classList.add("playoff-tree-panel");
    const grid = node("div", "playoff-tree-grid");
    const roundKeys = [...new Set(matchups.map((game) => `${playoffRoundOrder(game.round_code)}:${game.round_code}:${game.round_name}`))]
      .sort((a, b) => Number(a.split(":")[0]) - Number(b.split(":")[0]));
    roundKeys.forEach((key) => {
      const [, code, ...nameParts] = key.split(":");
      const roundName = nameParts.join(":");
      const column = node("section", "playoff-round-column");
      column.append(node("h3", null, playoffRoundShortName(code, roundName)));
      matchups
        .filter((game) => String(game.round_code || "") === code)
        .forEach((game) => column.append(playoffGameCard(game)));
      grid.append(column);
    });
    panelBody(bracket).append(grid);
    root.append(bracket);
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function standingsBoard(standings) {
    const p = panel("Standings", "Division View");
    p.classList.add("standings-board-panel");
    const body = panelBody(p);
    const conferences = ["AFC", "NFC"];
    const board = node("div", "standings-conference-grid");
    conferences.forEach((conference) => {
      const teams = standings.filter((team) => String(team.conference || "").toUpperCase() === conference);
      if (!teams.length) return;
      board.append(standingsConference(conference, teams));
    });
    body.append(board.children.length ? board : node("div", "empty-state", "Standings will populate after games are simmed."));
    return p;
  }

  function standingsConference(conference, teams) {
    const wrap = node("section", "standings-conference");
    wrap.append(node("h3", null, conference));
    const divisions = [...new Set(teams.map((team) => team.division || "Division"))].sort(divisionSort);
    const divisionGrid = node("div", "standings-division-grid");
    divisions.forEach((division) => {
      const rows = teams
        .filter((team) => (team.division || "Division") === division)
        .sort(standingsSort);
      divisionGrid.append(standingsDivision(division, rows));
    });
    wrap.append(divisionGrid);
    return wrap;
  }

  function divisionSort(left, right) {
    const order = ["East", "North", "South", "West"];
    const l = String(left || "").split(/\s+/).pop();
    const r = String(right || "").split(/\s+/).pop();
    const li = order.indexOf(l);
    const ri = order.indexOf(r);
    const lr = li >= 0 ? li : order.length;
    const rr = ri >= 0 ? ri : order.length;
    if (lr !== rr) return lr - rr;
    return String(left || "").localeCompare(String(right || ""));
  }

  function standingsSort(a, b) {
    const winPctA = standingsWinPct(a);
    const winPctB = standingsWinPct(b);
    if (winPctA !== winPctB) return winPctB - winPctA;
    if (Number(a.wins || 0) !== Number(b.wins || 0)) return Number(b.wins || 0) - Number(a.wins || 0);
    const diffA = Number(a.points_for || 0) - Number(a.points_against || 0);
    const diffB = Number(b.points_for || 0) - Number(b.points_against || 0);
    if (diffA !== diffB) return diffB - diffA;
    return String(a.abbreviation || "").localeCompare(String(b.abbreviation || ""));
  }

  function standingsWinPct(team) {
    const wins = Number(team.wins || 0);
    const losses = Number(team.losses || 0);
    const ties = Number(team.ties || 0);
    const games = wins + losses + ties;
    if (!games) return 0;
    return (wins + ties * 0.5) / games;
  }

  function standingsDivision(division, teams) {
    const card = node("article", "standings-division-card");
    card.append(node("h4", null, division));
    const tableEl = node("table", "standings-table");
    const thead = node("thead");
    const headRow = node("tr");
    ["Team", "W", "L", "T", "Pct", "PF", "PA", "Diff"].forEach((label) => headRow.append(node("th", null, label)));
    thead.append(headRow);
    const tbody = node("tbody");
    teams.forEach((team, index) => {
      const diff = Number(team.points_for || 0) - Number(team.points_against || 0);
      const tr = node("tr", index === 0 ? "division-leader" : "");
      append(tr, [
        append(node("td", null), [
          append(node("div", "standings-team"), [
            teamLogo(team.teamLogo, team.abbreviation, "standings-team-logo"),
            append(node("span", "standings-team-copy"), [
              node("strong", null, team.abbreviation || "-"),
              node("span", null, team.team_name || ""),
            ]),
          ]),
        ]),
        node("td", null, whole(team.wins)),
        node("td", null, whole(team.losses)),
        node("td", null, whole(team.ties)),
        node("td", null, standingsWinPct(team).toFixed(3).replace(/^0/, "")),
        node("td", null, whole(team.points_for)),
        node("td", null, whole(team.points_against)),
        node("td", diff >= 0 ? "standings-diff good" : "standings-diff bad", `${diff >= 0 ? "+" : ""}${diff}`),
      ]);
      tbody.append(tr);
    });
    append(tableEl, [thead, tbody]);
    card.append(tableEl);
    return card;
  }

  function renderFreeAgency() {
    setHeader("Free Agency", "Manage the market, track offers, and shop by position or tier.");
    const root = document.createDocumentFragment();
    const fa = data.freeAgency || { counts: {}, board: [], offers: [], events: [] };
    if (runnerMode() && state.freeAgencyLiveKey !== freeAgencyLiveKey() && !state.freeAgencyLoading) {
      loadLiveFreeAgency().then(render);
    }
    const period = fa.period;
    const userTeam = data.activeSave?.user_team || data.contractNegotiations?.team || "User";
    const currentCap = data.contractNegotiations?.currentCap || data.contractNegotiations?.cap || {};
    const capSpace = Number(currentCap.cap_space || 0);
    const status = panel("Market Desk", `${period ? freeAgencyStageLabel(period.current_stage) : "Not Started"}${data.freeAgencyGeneratedAt ? ` | refreshed ${shortDateTime(data.freeAgencyGeneratedAt.replace("T", " "))}` : ""}`);
    status.classList.add("fa-status-panel");
    if (state.freeAgencyLoading) {
      panelBody(status).append(node("div", "empty-state", "Refreshing live free agency..."));
    }
    const metrics = node("section", "metric-grid fa-market-metrics");
    append(metrics, [
      metric("Available", String(fa.counts.available || 0), "Market pool"),
      metric("Signed", String(fa.counts.signed || 0), "Processor signings"),
      metric("Pending Offers", String(fa.counts.pendingOffers || 0), "Awaiting decisions", fa.counts.pendingOffers ? "warn" : ""),
      metric(`${userTeam} Cap`, money(capSpace), `Current ${currentCap.season || data.currentSeason || ""}`.trim(), capSpace < 0 ? "bad" : ""),
      metric("Clock", period ? `${shortDate(period.current_date)} ${period.current_stage === "day_one_hourly" ? `${period.current_hour}:00` : ""}` : shortDate(fa.startDate), period ? "FA state" : "Scheduled start"),
    ]);
    panelBody(status).append(metrics);

    const commands = data.commands || {};
    const dashboard = node("div", "fa-dashboard");
    const sideStack = node("div", "fa-dashboard-stack");
    const controlsPanel = freeAgencyControlPanel(fa, commands);
    append(sideStack, [
      controlsPanel,
      freeAgencyOfferQueuePanel(fa),
      freeAgencyEventPanel(fa),
    ]);
    append(dashboard, [status, sideStack]);
    root.append(dashboard);

    const availableRows = (fa.board || []).filter((player) => !player.market_status || player.market_status === "available");
    const positions = [...new Set(availableRows.map((player) => player.position).filter(Boolean))]
      .sort(footballPositionSort);
    const activePosition = positions.includes(state.freeAgencyPositionFilter) ? state.freeAgencyPositionFilter : "all";
    state.freeAgencyPositionFilter = activePosition;
    const positionRows = activePosition === "all"
      ? availableRows
      : availableRows.filter((player) => player.position === activePosition);
    const tiers = freeAgencyTierOptions(availableRows);
    const activeTier = tiers.some((tier) => tier.value === state.freeAgencyTierFilter) ? state.freeAgencyTierFilter : "all";
    state.freeAgencyTierFilter = activeTier;
    const boardRows = activeTier === "all"
      ? positionRows
      : positionRows.filter((player) => freeAgencyTierValue(player.market_tier) === activeTier);
    const boardPanel = panel("Market Board", marketPanelKicker(activePosition, activeTier, boardRows.length, availableRows.length));
    boardPanel.classList.add("fa-board-panel");
    const filterRow = node("div", "fa-board-toolbar");
    const filterLabel = node("label", "fa-position-filter");
    filterLabel.append(node("span", null, "Position"));
    const select = node("select");
    select.append(node("option", null, "All positions"));
    select.firstChild.value = "all";
    positions.forEach((position) => {
      const option = node("option", null, position);
      option.value = position;
      select.append(option);
    });
    select.value = activePosition;
    select.addEventListener("change", () => {
      state.freeAgencyPositionFilter = select.value;
      render();
    });
    filterLabel.append(select);
    const tierTabs = node("div", "fa-tier-tabs");
    tiers.forEach((tier) => {
      const tab = node("button", `fa-tier-tab ${tier.value === activeTier ? "active" : ""}`.trim(), `${tier.label} ${tier.count}`);
      tab.type = "button";
      tab.addEventListener("click", () => {
        state.freeAgencyTierFilter = tier.value;
        render();
      });
      tierTabs.append(tab);
    });
    filterRow.append(filterLabel, tierTabs);
    panelBody(boardPanel).append(filterRow);
    panelBody(boardPanel).append(freeAgencyMarketGrid(boardRows.slice(0, 48), capSpace));
    root.append(boardPanel);
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function freeAgencyEventPanel(fa) {
    const eventPanel = panel("Market Log", "Recent");
    eventPanel.classList.add("fa-mini-panel");
    const eventList = node("div", "fa-log-list");
    (fa.events || []).slice(0, 8).forEach((event) => {
      const item = node("div", "fa-log-item");
      append(item, [
        node("strong", null, event.message || "Free agency event"),
        node("span", null, `${event.event_type || "event"} | ${event.event_hour !== null && event.event_hour !== undefined ? `${event.event_hour}:00` : shortDate(event.event_date)}`),
      ]);
      eventList.append(item);
    });
    panelBody(eventPanel).append(eventList.children.length ? eventList : node("div", "empty-state", "No free agency events yet."));
    return eventPanel;
  }

  function freeAgencyOfferQueuePanel(fa) {
    const offers = (fa.offers || []).filter((offer) => String(offer.status || "").toLowerCase() === "pending");
    const offerPanel = panel("Offer Queue", `${offers.length} pending`);
    offerPanel.classList.add("fa-mini-panel");
    const list = node("div", "fa-offer-list");
    offers.slice(0, 6).forEach((offer) => {
      const item = node("div", "fa-offer-item");
      append(item, [
        append(node("span", "fa-offer-player"), [
          node("strong", null, offer.player_name || offer.name || "Player"),
          node("small", null, `${offer.team || offer.team_abbr || "-"} | ${offer.status || "pending"}`),
        ]),
        node("span", "fa-offer-money", `${offer.years || offer.contract_years || 1} yr ${money(offer.aav || offer.average_annual_value || 0)}`),
      ]);
      list.append(item);
    });
    panelBody(offerPanel).append(list.children.length ? list : node("div", "empty-state", "No pending offers."));
    return offerPanel;
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

  function freeAgencyTierValue(value) {
    const text = String(value || "").toLowerCase();
    if (text.includes("premium") || text.includes("elite")) return "premium";
    if (text.includes("starter")) return "starter";
    if (text.includes("rotation")) return "rotation";
    if (text.includes("depth")) return "depth";
    return "other";
  }

  function freeAgencyTierOptions(players) {
    const counts = { all: players.length, premium: 0, starter: 0, rotation: 0, depth: 0, other: 0 };
    players.forEach((player) => {
      counts[freeAgencyTierValue(player.market_tier)] += 1;
    });
    return [
      { value: "all", label: "All", count: counts.all },
      { value: "premium", label: "Premium", count: counts.premium },
      { value: "starter", label: "Starters", count: counts.starter },
      { value: "rotation", label: "Rotation", count: counts.rotation },
      { value: "depth", label: "Depth", count: counts.depth },
    ].filter((tier) => tier.value === "all" || tier.count > 0);
  }

  function marketPanelKicker(position, tier, shown, total) {
    const bits = [];
    if (tier !== "all") bits.push(tier[0].toUpperCase() + tier.slice(1));
    if (position !== "all") bits.push(position);
    bits.push(`${shown}/${total} shown`);
    return bits.join(" | ");
  }

  function freeAgencyMarketGrid(players, capSpace) {
    if (!players.length) return node("div", "empty-state", "No free agents match these filters.");
    const grid = node("div", "fa-market-grid");
    players.forEach((player) => grid.append(freeAgencyMarketCard(player, capSpace)));
    return grid;
  }

  function freeAgencyMarketCard(player, capSpace) {
    const card = node("article", "fa-market-card");
    const ask = Number(player.asking_aav || player.offer_floor_aav || 0);
    const capAfter = Number(capSpace || 0) - ask;
    const top = node("div", "fa-market-card-top");
    append(top, [
      append(node("div", "fa-player-main"), [
        smallPlayerCell(player.player_id, player.player_name, `${player.age || "-"} | ${player.previous_team || player.team || "FA"}`, {
          team: player.previous_team || player.team,
          position: player.position,
        }),
        append(node("div", "fa-card-tags"), [
          tag(player.position || "-"),
          tag(player.market_tier || "Market"),
          freeAgencyPreferenceTag(player),
        ]),
      ]),
      append(node("div", "fa-ask-box"), [
        node("span", null, "Ask"),
        node("strong", null, money(ask)),
        node("small", capAfter < 0 ? `${money(Math.abs(capAfter))} over cap` : `${money(capAfter)} after`),
      ]),
    ]);
    const middle = node("div", "fa-card-intel");
    append(middle, [
      freeAgencyIntelItem("Leader", leadingBidText(player)),
      freeAgencyIntelItem("Offers", String(player.pending_offers || 0)),
      freeAgencyIntelItem("Years", `${player.contract_year_preference || player.preferred_years || 1}`),
      freeAgencyIntelItem("Role", `${player.role_priority || 10}/20`),
    ]);
    const notes = player.signing_notes || player.motivation;
    append(card, [top, middle]);
    if (notes) {
      append(card, [append(node("div", "fa-player-notes"), [
        node("span", null, "Read"),
        node("strong", null, notes),
      ])]);
    }
    card.append(freeAgencyOfferButton(player));
    return card;
  }

  function freeAgencyIntelItem(label, value) {
    return append(node("div", "fa-intel-item"), [
      node("span", null, label),
      node("strong", null, value || "-"),
    ]);
  }

  function leadingBidText(player) {
    if (!player.best_aav) return "No bid";
    return `${player.best_offer_team || "-"} ${money(player.best_aav)}`;
  }

  function freeAgencyPreferenceTag(player) {
    const archetype = String(player.preference_archetype || "balanced").replaceAll("_", " ");
    const tone = archetype.includes("money") ? "warn" : archetype.includes("ring") ? "good" : "";
    return tag(archetype, tone);
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
    const defaultAav = Number(player.offer_floor_aav || Math.max(Number(player.asking_aav || 0), Number(player.minimum_aav || 0)));
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
      return {
        years,
        aav,
        bonus,
      };
    };

    wrap.append(node("span", "offer-label", "Yrs"));
    wrap.append(yearsInput);
    wrap.append(node("span", "offer-label", "AAV"));
    wrap.append(aavInput);
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
    } else {
      const run = node("button", "run-button", "Offer");
      run.type = "button";
      run.disabled = true;
      run.title = "Live actions are unavailable.";
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
    if (runnerMode() && state.contractsLiveKey !== contractsLiveKey() && !state.contractsLoading) {
      loadLiveContracts().then(render);
    }

    const summary = panel("Negotiation Snapshot", `${talks.team || data.activeSave?.user_team || ""}${data.contractsGeneratedAt ? ` | refreshed ${shortDateTime(data.contractsGeneratedAt.replace("T", " "))}` : ""}`);
    if (state.contractsLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live contracts..."));
    }
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
    expiringBody.append(table(["Player", "Pos", "Age", "Role", "Current", "Ask", "Years", "Priority", "Action"], (talks.expiring || []).map((player) => [
      playerLink(player.player_id, player.player_name, undefined, { team: talks.team, position: player.position }),
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
      playerLink(player.player_id, player.player_name, undefined, { team: talks.team, position: player.position }),
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
      playerLink(player.player_id, player.player_name, undefined, { team: talks.team, position: player.position }),
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
    } else {
      const run = node("button", "run-button", "Extend");
      run.type = "button";
      run.disabled = true;
      run.title = "Live actions are unavailable.";
      wrap.append(run);
    }
    return wrap;
  }

  function contractReleaseButton(player) {
    const wrap = node("span", "action-cell");
    if (runnerMode()) {
      const run = node("button", "run-button danger", state.runnerBusy ? "Running" : "Release");
      run.type = "button";
      run.disabled = state.runnerBusy;
      run.addEventListener("click", () => runAction("contract_release", {
        player_id: player.player_id,
      }));
      wrap.append(run);
    } else {
      const run = node("button", "run-button danger", "Release");
      run.type = "button";
      run.disabled = true;
      run.title = "Live actions are unavailable.";
      wrap.append(run);
    }
    return wrap;
  }

  function contractRestructureButton(player) {
    const wrap = node("span", "action-cell");
    if (runnerMode()) {
      const run = node("button", "run-button", state.runnerBusy ? "Running" : "Restructure");
      run.type = "button";
      run.disabled = state.runnerBusy;
      run.addEventListener("click", () => runAction("contract_restructure", {
        player_id: player.player_id,
        amount: player.suggested_convert || 0,
      }));
      wrap.append(run);
    } else {
      const run = node("button", "run-button", "Restructure");
      run.type = "button";
      run.disabled = true;
      run.title = "Live actions are unavailable.";
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
    const slots = orderedDepthSlots(depth);
    if (!slots.length) return null;
    const selected = slots.find((slot) => slot.slot === state.selectedDepthSlot);
    if (selected) return selected;
    state.selectedDepthSlot = slots[0].slot;
    return slots[0];
  }

  function depthRoleName(rank) {
    const value = Number(rank || 0);
    if (value <= 1) return "Starter";
    if (value === 2) return "Primary backup";
    if (value === 3) return "Rotation";
    return "Depth";
  }

  function depthRoleTone(rank) {
    const value = Number(rank || 0);
    if (value <= 1) return "good";
    if (value === 2) return "warn";
    return "";
  }

  function depthEligiblePlayers(depth, selected, limit) {
    return [...(depth.roster || [])]
      .sort((a, b) => {
        const fit = Number(playerFitsSlot(b, selected.slot)) - Number(playerFitsSlot(a, selected.slot));
        if (fit) return fit;
        return Number(b.role?.score || 0) - Number(a.role?.score || 0);
      })
      .slice(0, limit || 24);
  }

  function depthRankTargets(selected) {
    const assignedCount = (selected?.players || []).length;
    const maxRank = Math.min(8, Math.max(5, assignedCount + 1));
    return Array.from({ length: maxRank }, (_, index) => index + 1);
  }

  function depthSetRankButton(slot, rank, player, label) {
    const currentRank = Number(player.depth_rank || 0);
    const button = node("button", "depth-rank-button", label || `#${rank}`);
    button.type = "button";
    button.disabled = state.runnerBusy || !runnerMode() || currentRank === Number(rank);
    button.title = currentRank === Number(rank)
      ? `${player.player_name} is already ${slot} #${rank}`
      : `Set ${player.player_name} as ${slot} #${rank}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      runAction("depth_chart_set", {
        position: slot,
        rank,
        player_id: player.player_id,
      });
    });
    return button;
  }

  function depthRosterChip(depth, selected, player) {
    const currentAssignment = (selected.players || []).find((assigned) => String(assigned.player_id) === String(player.player_id));
    const currentRank = Number(currentAssignment?.depth_rank || 0);
    const item = node("div", `depth-roster-chip ${playerFitsSlot(player, selected.slot) ? "fit" : ""}`.trim());
    const top = node("div", "depth-roster-chip-top");
    append(top, [
      playerLink(player.player_id, player.player_name, "player-link strong-link", {
        team: depth.team,
        position: player.position,
      }),
      tag(currentRank ? `#${currentRank}` : "Bench", currentRank ? depthRoleTone(currentRank) : ""),
    ]);
    const meta = node(
      "span",
      null,
      `${player.position} | ${player.role?.score ? oneDecimal(player.role.score) : "-"} role fit${playerFitsSlot(player, selected.slot) ? " | natural fit" : ""}`,
    );
    const actions = node("div", "depth-chip-actions");
    depthRankTargets(selected).forEach((rank) => {
      actions.append(depthSetRankButton(selected.slot, rank, player));
    });
    append(item, [top, meta, actions]);
    return item;
  }

  function depthPlayerCard(depth, selected, player) {
    const card = node("article", "depth-player-card");
    const roleScore = player.role?.score ? oneDecimal(player.role.score) : "-";
    const header = node("div", "depth-player-card-top");
    append(header, [
      tag(`#${player.depth_rank}`, depthRoleTone(player.depth_rank)),
      smallPlayerCell(player.player_id, player.player_name, `${player.position} | Age ${player.age || "-"}`, {
        team: depth.team,
        position: player.position,
      }),
      tag(depthRoleName(player.depth_rank), depthRoleTone(player.depth_rank)),
    ]);
    const detail = node("div", "depth-player-card-detail");
    append(detail, [
      node("span", null, `Role fit ${roleScore}`),
      node("span", null, player.role?.key ? roleLabel(player.role.key) : "No role read"),
    ]);
    const controls = node("div", "depth-player-card-controls");
    append(controls, [
      depthMoveButtons(selected.slot, player),
      depthReplacementControl(selected.slot, player.depth_rank, depth.roster || [], player.player_id),
    ]);
    append(card, [header, detail, controls]);
    return card;
  }

  function depthSlotButton(depth, selected, slot) {
    const button = node("button", `slot-card ${slot.slot === selected?.slot ? "active" : ""}`.trim());
    button.type = "button";
    const starter = (slot.players || [])[0];
    append(button, [
      node("strong", null, slot.slot),
      node("span", null, starter ? starter.player_name : "Empty"),
      node("small", null, `${(slot.players || []).length} deep${starter?.position ? ` | ${starter.position}` : ""}`),
    ]);
    button.addEventListener("click", () => {
      state.selectedDepthSlot = slot.slot;
      render();
    });
    return button;
  }

  function depthRoomSummary(depth, selected) {
    const summary = node("div", "depth-room-summary");
    const selectedPlayers = selected?.players || [];
    const starter = selectedPlayers[0];
    const eligible = selected ? depthEligiblePlayers(depth, selected, 6) : [];
    append(summary, [
      metric("Slot", selected?.slot || "-", selected ? `${selectedPlayers.length} assigned` : "Choose a position"),
      metric("Starter", starter?.player_name || "-", starter ? `${starter.position} | Age ${starter.age || "-"}` : "No player assigned"),
      metric("Best Fit", eligible[0]?.player_name || "-", eligible[0] ? `${eligible[0].position} | ${eligible[0].role?.score ? oneDecimal(eligible[0].role.score) : "-"} role fit` : "No roster read"),
      metric("Roster", String((depth.roster || []).length), "Active players"),
    ]);
    return summary;
  }

  function depthSlotMap(depth) {
    const map = new Map();
    orderedDepthSlots(depth).forEach((slot) => map.set(String(slot.slot || "").toUpperCase(), slot));
    return map;
  }

  function depthFormationLabel(depth, unitName) {
    const map = depthSlotMap(depth);
    if (unitName === "Offense") {
      const wrs = ["LWR", "RWR", "SWR"].filter((slot) => map.has(slot)).length;
      const tes = map.has("TE") ? 1 : 0;
      const rbs = (map.has("RB") ? 1 : 0) + (map.has("FB") ? 1 : 0);
      if (wrs >= 3 && tes >= 1 && rbs === 1) return "11 personnel";
      if (wrs >= 2 && tes >= 2) return "12 personnel";
      if (wrs >= 2 && rbs >= 2) return "21 personnel";
      return "Base offense";
    }
    if (unitName === "Defense") {
      const corners = ["LCB", "RCB", "NB"].filter((slot) => map.has(slot)).length;
      const safeties = ["FS", "SS"].filter((slot) => map.has(slot)).length;
      const downLinemen = ["LDL", "NT", "RDL"].filter((slot) => map.has(slot)).length;
      const edges = ["LEDGE", "REDGE"].filter((slot) => map.has(slot)).length;
      const linebackers = ["WLB", "MLB", "SLB"].filter((slot) => map.has(slot)).length;
      if (corners >= 3 && safeties >= 2) return `Nickel ${downLinemen + edges}-${linebackers}`;
      if (corners >= 2 && safeties >= 2) return `Base ${downLinemen + edges}-${linebackers}`;
      return "Base defense";
    }
    return "Game-day specialists";
  }

  function defensePackageSlots() {
    if (state.depthDefensePackage === "base43") return BASE_43_DEFENSE_FORMATION_SLOTS;
    if (state.depthDefensePackage === "base") return BASE_DEFENSE_FORMATION_SLOTS;
    return DEFENSE_FORMATION_SLOTS;
  }

  function offensePersonnelSlots() {
    if (state.depthOffensePersonnel === "12") return OFFENSE_12_FORMATION_SLOTS;
    if (state.depthOffensePersonnel === "21") return OFFENSE_21_FORMATION_SLOTS;
    return OFFENSE_FORMATION_SLOTS;
  }

  function offensePersonnelToggle() {
    const wrap = node("div", "formation-toggle");
    [
      { value: "11", label: "11", detail: "3 WR 1 TE" },
      { value: "12", label: "12", detail: "2 WR 2 TE" },
      { value: "21", label: "21", detail: "2 WR FB" },
    ].forEach((option) => {
      const button = node("button", state.depthOffensePersonnel === option.value ? "active" : "");
      button.type = "button";
      append(button, [
        node("strong", null, option.label),
        node("span", null, option.detail),
      ]);
      button.addEventListener("click", () => {
        state.depthOffensePersonnel = option.value;
        if (option.value !== "11" && state.selectedDepthSlot === "SWR") state.selectedDepthSlot = "TE";
        if (option.value !== "21" && state.selectedDepthSlot === "FB") state.selectedDepthSlot = "RB";
        render();
      });
      wrap.append(button);
    });
    return wrap;
  }

  function defensePackageToggle() {
    const wrap = node("div", "formation-toggle");
    [
      { value: "nickel", label: "Nickel", detail: "NB + 2 LB" },
      { value: "base", label: "3-4", detail: "2 ILB" },
      { value: "base43", label: "4-3", detail: "3 LB" },
    ].forEach((option) => {
      const button = node("button", state.depthDefensePackage === option.value ? "active" : "");
      button.type = "button";
      append(button, [
        node("strong", null, option.label),
        node("span", null, option.detail),
      ]);
      button.addEventListener("click", () => {
        state.depthDefensePackage = option.value;
        if (option.value === "nickel" && state.selectedDepthSlot === "SLB") state.selectedDepthSlot = "NB";
        if (option.value !== "nickel" && state.selectedDepthSlot === "NB") state.selectedDepthSlot = option.value === "base43" ? "SLB" : "MLB";
        if (option.value === "base" && state.selectedDepthSlot === "SLB") state.selectedDepthSlot = "MLB";
        render();
      });
      wrap.append(button);
    });
    return wrap;
  }

  function formationPlayerLabel(player) {
    if (!player) return "Empty";
    const number = player.jersey_number === null || player.jersey_number === undefined ? "" : `#${player.jersey_number} `;
    return `${number}${player.player_name || "Player"}`;
  }

  function formationSlotTile(depth, selected, slot, meta) {
    const rankIndex = Math.max(0, Number(meta.rank || 1) - 1);
    const starter = (slot.players || [])[rankIndex];
    const button = node("button", `formation-slot ${slot.slot === selected?.slot ? "active" : ""}`.trim());
    button.type = "button";
    if (meta.row) button.style.gridRow = String(meta.row);
    if (meta.col) button.style.gridColumn = String(meta.col);
    append(button, [
      node("span", "formation-slot-label", meta.label || slot.slot),
      node("strong", null, formationPlayerLabel(starter)),
      node("small", null, starter ? `${starter.position || slot.slot}${starter.overall ? ` | ${starter.overall} OVR` : ""}${meta.rank ? ` | #${meta.rank}` : ""}` : `${slot.slot} depth${meta.rank ? ` #${meta.rank}` : ""}`),
    ]);
    button.addEventListener("click", () => {
      state.selectedDepthSlot = slot.slot;
      render();
    });
    return button;
  }

  function depthFormationPanel(depth, selected) {
    const map = depthSlotMap(depth);
    const panelNode = panel("Formation Board", "Click a position on the field");
    const body = panelBody(panelNode);
    const unitBlocks = [
      { unit: "Offense", slots: offensePersonnelSlots() },
      { unit: "Defense", slots: defensePackageSlots() },
    ];
    unitBlocks.forEach((unit) => {
      const section = node("section", "formation-section");
      const title = node("div", "formation-section-title");
      append(title, [
        node("strong", null, unit.unit),
        unit.unit === "Defense" ? defensePackageToggle() : offensePersonnelToggle(),
      ]);
      const field = node("div", `formation-field ${unit.unit === "Defense" ? "defense" : "offense"}`);
      unit.slots.forEach((meta) => {
        const slot = map.get(meta.slot);
        if (!slot && meta.optional) return;
        field.append(formationSlotTile(depth, selected, slot || { slot: meta.slot, players: [] }, meta));
      });
      append(section, [title, field]);
      body.append(section);
    });
    const specialists = node("div", "specialists-strip");
    SPECIAL_TEAMS_FORMATION_SLOTS.forEach((meta) => {
      const slot = map.get(meta.slot);
      if (!slot) return;
      specialists.append(formationSlotTile(depth, selected, slot, meta));
    });
    if (specialists.children.length) body.append(sectionBlock("Special Teams", specialists));
    return panelNode;
  }

  function rosterDepthMap(depth) {
    const map = new Map();
    orderedDepthUnits(depth).forEach((unit) => {
      (unit.slots || []).forEach((slot) => {
        (slot.players || []).forEach((player) => {
          const id = String(player.player_id);
          const existing = map.get(id) || [];
          existing.push({
            unit: unit.unit,
            slot: slot.slot,
            rank: Number(player.depth_rank || 0),
            role: depthRoleName(player.depth_rank),
          });
          map.set(id, existing);
        });
      });
    });
    return map;
  }

  function rosterContractMap() {
    const talks = data.contractNegotiations || {};
    const map = new Map();
    const merge = (player, type) => {
      if (!player?.player_id) return;
      const id = String(player.player_id);
      map.set(id, { ...(map.get(id) || {}), ...player, type });
    };
    (talks.expiring || []).forEach((player) => merge(player, "Expiring"));
    (talks.capCasualties || []).forEach((player) => merge(player, "Cap Watch"));
    (talks.restructureCandidates || []).forEach((player) => merge(player, "Restructure"));
    return map;
  }

  function rosterRows(depth) {
    const depthMap = rosterDepthMap(depth);
    const contractMap = rosterContractMap();
    return (depth.roster || []).map((player) => {
      const assignments = depthMap.get(String(player.player_id)) || [];
      const primary = assignments
        .filter((item) => item.rank > 0)
        .sort((a, b) => a.rank - b.rank)[0];
      const contract = contractMap.get(String(player.player_id)) || {};
      return {
        ...player,
        assignments,
        primaryAssignment: primary,
        roleScore: Number(player.role?.score || 0),
        contract,
      };
    });
  }

  function rosterSortValue(player, key) {
    if (key === "name") return String(player.player_name || "");
    if (key === "pos") return FOOTBALL_POSITION_ORDER.indexOf(player.position) >= 0 ? FOOTBALL_POSITION_ORDER.indexOf(player.position) : 99;
    if (key === "age") return Number(player.age || 0);
    if (key === "number") return Number(player.jersey_number ?? 999);
    if (key === "overall") return Number(player.overall || 0);
    if (key === "potential") return Number(player.potential || 0);
    if (key === "contract") return Number(player.contract?.cap_hit || player.contract?.asking_aav || 0);
    if (key === "status") return String(player.status || "");
    if (key === "depth") return Number(player.primaryAssignment?.rank || 99);
    return Number(player.roleScore || 0);
  }

  function sortedRosterRows(rows) {
    const { key, direction } = state.rosterSort || { key: "role", direction: "desc" };
    const dir = direction === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = rosterSortValue(a, key);
      const bv = rosterSortValue(b, key);
      if (typeof av === "string" || typeof bv === "string") return String(av).localeCompare(String(bv)) * dir;
      if (av !== bv) return (av - bv) * dir;
      return footballPositionSort(a.position, b.position) || String(a.player_name || "").localeCompare(String(b.player_name || ""));
    });
  }

  function rosterSortButton(label, key) {
    const active = state.rosterSort?.key === key;
    const direction = active ? state.rosterSort.direction : "desc";
    const button = node("button", `table-sort-button ${active ? "active" : ""}`.trim(), `${label}${active ? (direction === "asc" ? " ↑" : " ↓") : ""}`);
    button.type = "button";
    button.addEventListener("click", () => {
      state.rosterSort = {
        key,
        direction: active && direction === "desc" ? "asc" : "desc",
      };
      render();
    });
    return button;
  }

  function rosterPositionFilter(rows) {
    const positions = [...new Set(rows.map((player) => player.position).filter(Boolean))].sort(footballPositionSort);
    const active = positions.includes(state.rosterPositionFilter) ? state.rosterPositionFilter : "all";
    state.rosterPositionFilter = active;
    const wrap = node("label", "roster-filter");
    append(wrap, [node("span", null, "Position")]);
    const select = node("select");
    select.append(node("option", null, "All positions"));
    select.firstChild.value = "all";
    positions.forEach((position) => {
      const option = node("option", null, position);
      option.value = position;
      option.selected = position === active;
      select.append(option);
    });
    select.addEventListener("change", () => {
      state.rosterPositionFilter = select.value;
      render();
    });
    wrap.append(select);
    return wrap;
  }

  function rosterStatusFilter(rows) {
    const statuses = [...new Set(rows.map((player) => player.status || "Active"))].sort();
    const active = statuses.includes(state.rosterStatusFilter) ? state.rosterStatusFilter : "all";
    state.rosterStatusFilter = active;
    const wrap = node("label", "roster-filter");
    append(wrap, [node("span", null, "Status")]);
    const select = node("select");
    select.append(node("option", null, "All statuses"));
    select.firstChild.value = "all";
    statuses.forEach((status) => {
      const option = node("option", null, status);
      option.value = status;
      option.selected = status === active;
      select.append(option);
    });
    select.addEventListener("change", () => {
      state.rosterStatusFilter = select.value;
      render();
    });
    wrap.append(select);
    return wrap;
  }

  function rosterGroupForPosition(position) {
    const pos = String(position || "");
    return ROSTER_GROUPS.find((group) => group.value !== "all" && group.positions.includes(pos))?.value || "all";
  }

  function isPracticeSquadPlayer(player) {
    return String(player?.status || "").toLowerCase() === "practice squad";
  }

  function rosterPlayerMatchesGroup(player, group) {
    if (!group || group.value === "all") return true;
    if (group.value === "PS") return isPracticeSquadPlayer(player);
    return group.positions.includes(player.position);
  }

  function rosterGroupTabs(rows) {
    const wrap = node("div", "roster-group-tabs");
    ROSTER_GROUPS.forEach((group) => {
      const count = group.value === "all"
        ? rows.length
        : rows.filter((player) => rosterPlayerMatchesGroup(player, group)).length;
      if (!count && group.value !== "all") return;
      const button = node("button", `roster-group-tab ${state.rosterGroupFilter === group.value ? "active" : ""}`.trim());
      button.type = "button";
      append(button, [
        node("span", null, group.label),
        node("strong", null, String(count)),
      ]);
      button.addEventListener("click", () => {
        state.rosterGroupFilter = group.value;
        state.rosterPositionFilter = "all";
        state.rosterStatusFilter = "all";
        state.selectedRosterPlayerId = null;
        render();
      });
      wrap.append(button);
    });
    return wrap;
  }

  function filteredRosterRows(rows) {
    const group = ROSTER_GROUPS.find((item) => item.value === state.rosterGroupFilter) || ROSTER_GROUPS[0];
    return rows
      .filter((player) => rosterPlayerMatchesGroup(player, group))
      .filter((player) => state.rosterPositionFilter === "all" || player.position === state.rosterPositionFilter)
      .filter((player) => state.rosterStatusFilter === "all" || (player.status || "Active") === state.rosterStatusFilter);
  }

  function selectedRosterPlayer(rows) {
    const selected = rows.find((player) => String(player.player_id) === String(state.selectedRosterPlayerId));
    if (selected) return selected;
    const first = sortedRosterRows(rows)[0] || null;
    state.selectedRosterPlayerId = first?.player_id ? String(first.player_id) : null;
    return first;
  }

  function rosterTableHeader(label, key) {
    const th = node("th");
    if (!key) {
      th.textContent = label || "";
      return th;
    }
    th.append(rosterSortButton(label, key));
    return th;
  }

  function rosterHeadshot(player) {
    const wrap = node("span", "roster-headshot");
    if (player.headshot) {
      const img = node("img");
      img.src = player.headshot;
      img.alt = "";
      img.loading = "lazy";
      wrap.append(img);
    } else {
      const initials = String(player.player_name || "?")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0])
        .join("")
        .toUpperCase();
      wrap.append(node("span", null, initials || "?"));
    }
    return wrap;
  }

  function rosterPlayerTable(depth, rows, selected) {
    const wrap = node("div", "table-wrap roster-table-wrap");
    const table = node("table", "data-table roster-table");
    const head = node("thead");
    const headRow = node("tr");
    [
      rosterTableHeader("", null),
      rosterTableHeader("#", "number"),
      rosterTableHeader("Player", "name"),
      rosterTableHeader("Pos", "pos"),
      rosterTableHeader("OVR", "overall"),
      rosterTableHeader("POT", "potential"),
      rosterTableHeader("Age", "age"),
      rosterTableHeader("Depth", "depth"),
      rosterTableHeader("Role", "role"),
      rosterTableHeader("Contract", "contract"),
      rosterTableHeader("Status", "status"),
    ].forEach((cell) => headRow.append(cell));
    head.append(headRow);
    const body = node("tbody");
    sortedRosterRows(rows).forEach((player) => {
      const assignment = player.primaryAssignment;
      const contract = player.contract || {};
      const tr = node("tr", String(player.player_id) === String(selected?.player_id) ? "selected" : "");
      tr.tabIndex = 0;
      tr.addEventListener("click", () => {
        state.selectedRosterPlayerId = String(player.player_id);
        render();
      });
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          state.selectedRosterPlayerId = String(player.player_id);
          render();
        }
      });
      const playerCell = node("td");
      playerCell.append(smallPlayerCell(player.player_id, player.player_name, `${assignment ? `${assignment.slot} #${assignment.rank}` : "No depth role"} | ${player.is_rookie ? "Rookie" : "Veteran"}`, {
        team: depth.team,
        position: player.position,
      }));
      append(tr, [
        append(node("td", "roster-photo-cell"), [rosterHeadshot(player)]),
        node("td", "numeric roster-number-cell", player.jersey_number === null || player.jersey_number === undefined ? "-" : `#${player.jersey_number}`),
        playerCell,
        node("td", "numeric", player.position || "-"),
        node("td", "numeric rating-cell", player.overall ?? "-"),
        node("td", "numeric rating-cell", player.potential ?? "-"),
        node("td", "numeric", player.age ?? "-"),
        node("td", null, assignment ? `${assignment.slot} #${assignment.rank}` : "-"),
        node("td", null, player.role?.key ? `${roleLabel(player.role.key)} ${oneDecimal(player.roleScore)}` : "-"),
        node("td", null, contract.end_year ? `${money(contract.cap_hit || contract.asking_aav || 0)} / ${contract.end_year}` : (contract.type || "-")),
        node("td", null, player.status || "Active"),
      ]);
      body.append(tr);
    });
    table.append(head, body);
    wrap.append(table);
    return wrap;
  }

  function rosterActionButton(label, action, params = {}, className = "") {
    const button = node("button", `run-button compact ${className}`.trim(), state.runnerBusy ? "Running" : label);
    button.type = "button";
    button.disabled = !runnerMode() || state.runnerBusy;
    if (!runnerMode()) button.title = "Live actions are unavailable.";
    button.addEventListener("click", () => runAction(action, params));
    return button;
  }

  function rosterJerseyControl(player) {
    const wrap = node("form", "jersey-control");
    const input = node("input");
    input.type = "number";
    input.min = "0";
    input.max = "99";
    input.value = player.jersey_number ?? "";
    input.disabled = !runnerMode() || state.runnerBusy;
    const button = node("button", "run-button compact", "Change #");
    button.type = "submit";
    button.disabled = !runnerMode() || state.runnerBusy;
    wrap.addEventListener("submit", (event) => {
      event.preventDefault();
      runAction("roster_change_number", {
        player_id: player.player_id,
        number: input.value,
      });
    });
    append(wrap, [input, button]);
    return wrap;
  }

  function rosterPlayerPanel(depth, player) {
    const panelNode = panel("Player Actions", player ? "Roster tools" : "Select a player");
    const body = panelBody(panelNode);
    if (!player) {
      body.append(node("div", "empty-state", "Choose a player from the roster table."));
      return panelNode;
    }
    const assignment = player.primaryAssignment;
    const contract = player.contract || {};
    const title = node("div", "roster-player-panel-head");
    const identity = node("div");
    identity.append(smallPlayerCell(player.player_id, player.player_name, `${player.position} | Age ${player.age || "-"} | ${player.status || "Active"}`, {
      team: depth.team,
      position: player.position,
    }));
    append(title, [
      node("div", "jersey-badge", player.jersey_number === null || player.jersey_number === undefined ? "--" : `#${player.jersey_number}`),
      identity,
    ]);
    const facts = node("section", "metric-grid roster-card-facts roster-action-facts");
    append(facts, [
      metric("Overall", player.overall ?? "-", `Potential ${player.potential ?? "-"}`),
      metric("Depth", assignment ? `${assignment.slot} #${assignment.rank}` : "Unassigned", assignment?.unit || "No room"),
      metric("Role Fit", player.roleScore ? oneDecimal(player.roleScore) : "-", player.role?.key ? roleLabel(player.role.key) : "No role read"),
      metric("Contract", contract.end_year ? `Through ${contract.end_year}` : (contract.type || "Rostered"), contract.cap_hit ? `Cap ${money(contract.cap_hit)}` : (contract.market_tier || "")),
    ]);
    const actions = node("div", "roster-action-grid");
    const profile = node("a", "run-button compact", "View Profile");
    profile.href = playerProfileHref({ playerId: player.player_id, name: player.player_name, team: depth.team, position: player.position });
    const cardLink = node("a", "run-button compact", "View Card");
    cardLink.href = playerCardHref({ playerId: player.player_id, name: player.player_name, team: depth.team, position: player.position });
    const depthButton = node("button", "run-button compact", "Depth Chart");
    depthButton.type = "button";
    depthButton.addEventListener("click", () => {
      state.selectedDepthSlot = assignment?.slot || null;
      switchView("depth");
    });
    append(actions, [
      profile,
      cardLink,
      depthButton,
      rosterActionButton("Extend", "contract_extend", {
        player_id: player.player_id,
        years: contract.suggested_years || player.suggested_years || 1,
        aav: contract.asking_aav || player.asking_aav || 0,
      }),
      rosterActionButton("Restructure", "contract_restructure", {
        player_id: player.player_id,
        amount: contract.suggested_convert || player.suggested_convert || 0,
      }),
      rosterActionButton("Release", "roster_release_player", {
        player_id: player.player_id,
      }, "danger"),
    ]);
    const assignments = node("div", "roster-assignment-strip");
    (player.assignments || []).slice(0, 8).forEach((item) => assignments.append(tag(`${item.slot} #${item.rank}`, depthRoleTone(item.rank))));
    if (!assignments.children.length) assignments.append(tag("No depth role", ""));
    append(body, [
      title,
      facts,
      sectionBlock("Jersey Number", rosterJerseyControl(player)),
      sectionBlock("Actions", actions),
      sectionBlock("Depth Usage", assignments),
    ]);
    return panelNode;
  }

  function rosterCard(depth, player) {
    const card = node("article", "roster-card");
    const roleScore = player.roleScore ? oneDecimal(player.roleScore) : "-";
    const assignment = player.primaryAssignment;
    const contract = player.contract || {};
    const top = node("div", "roster-card-top");
    append(top, [
      smallPlayerCell(player.player_id, player.player_name, `${player.position} | Age ${player.age || "-"}`, {
        team: depth.team,
        position: player.position,
      }),
      tag(player.is_rookie ? "Rookie" : (assignment?.role || "Reserve"), player.is_rookie ? "warn" : depthRoleTone(assignment?.rank)),
    ]);
    const facts = node("div", "roster-card-facts");
    append(facts, [
      metric("Role Fit", roleScore, player.role?.key ? roleLabel(player.role.key) : "No role read"),
      metric("Depth", assignment ? `${assignment.slot} #${assignment.rank}` : "Unassigned", assignment?.unit || "Not on chart", assignment?.rank === 1 ? "good" : ""),
      metric("Status", player.status || "Active", player.is_rookie ? "Rookie" : "Veteran"),
      metric("Contract", contract.type || "Rostered", contract.end_year ? `Through ${contract.end_year}` : (contract.market_tier || "No alert")),
    ]);
    const assignments = node("div", "roster-assignment-strip");
    (player.assignments || []).slice(0, 5).forEach((item) => {
      assignments.append(tag(`${item.slot} #${item.rank}`, depthRoleTone(item.rank)));
    });
    if (!assignments.children.length) assignments.append(tag("No depth role", ""));
    append(card, [top, facts, assignments]);
    return card;
  }

  function bucketLabel(bucket) {
    return {
      developmental: "Developmental",
      veteran_exception: "Veteran exception",
      international_exemption: "International",
    }[bucket] || valueOrDash(bucket);
  }

  function practiceSquadTone(candidate) {
    if (candidate.current_status === "Practice Squad") return "good";
    if (candidate.eligible) return "warn";
    return "bad";
  }

  function practiceSquadFilteredRows(rows) {
    if (state.practiceSquadFilter === "current") return rows.filter((row) => row.current_status === "Practice Squad");
    if (state.practiceSquadFilter === "eligible") return rows.filter((row) => row.eligible || row.current_status === "Practice Squad");
    if (state.practiceSquadFilter === "blocked") return rows.filter((row) => !row.eligible && row.current_status !== "Practice Squad");
    return rows;
  }

  function practiceSquadFilterTabs(rows) {
    const options = [
      ["eligible", "Eligible", rows.filter((row) => row.eligible || row.current_status === "Practice Squad").length],
      ["current", "Current PS", rows.filter((row) => row.current_status === "Practice Squad").length],
      ["blocked", "Blocked", rows.filter((row) => !row.eligible && row.current_status !== "Practice Squad").length],
      ["all", "All", rows.length],
    ];
    const wrap = node("div", "roster-group-tabs ps-filter-tabs");
    options.forEach(([value, label, count]) => {
      const button = node("button", `roster-group-tab ${state.practiceSquadFilter === value ? "active" : ""}`.trim());
      button.type = "button";
      append(button, [node("span", null, label), node("strong", null, String(count))]);
      button.addEventListener("click", () => {
        state.practiceSquadFilter = value;
        render();
      });
      wrap.append(button);
    });
    return wrap;
  }

  function practiceSquadActionCell(candidate) {
    const wrap = node("div", "row-actions");
    if (candidate.current_status === "Practice Squad") {
      wrap.append(rosterActionButton("Release PS", "practice_squad_release", { player_id: candidate.player_id }, "warn"));
      return wrap;
    }
    if (candidate.eligible) {
      wrap.append(rosterActionButton("Assign PS", "practice_squad_assign", { player_id: candidate.player_id }, "good"));
    }
    if (candidate.current_status === "Active") {
      wrap.append(rosterActionButton("Release", "roster_release_player", { player_id: candidate.player_id }, "warn"));
    }
    if (!wrap.children.length) wrap.append(node("span", "muted", "No action"));
    return wrap;
  }

  function practiceSquadTable(ps) {
    const rows = practiceSquadFilteredRows(ps.candidates || []);
    if (!rows.length) return node("div", "empty-state", "No players match this practice squad view.");
    const wrap = node("div", "table-wrap ps-registration-table");
    const tableEl = node("table", "data-table compact-table");
    const head = node("thead");
    const trHead = node("tr");
    ["Player", "Pos", "Ovr", "Pot", "Age", "Exp", "Status", "Bucket", "Read", "Action"].forEach((label) => trHead.append(node("th", null, label)));
    head.append(trHead);
    const body = node("tbody");
    rows.forEach((candidate) => {
      const tr = node("tr", candidate.eligible ? "" : "muted-row");
      const read = candidate.eligible ? (candidate.reasons || []).join(" ") : (candidate.blockers || []).join(" ");
      append(tr, [
        node("td", null, candidate.player_name || "-"),
        node("td", "numeric", candidate.position || "-"),
        node("td", "numeric rating-cell", candidate.overall ?? "-"),
        node("td", "numeric rating-cell", candidate.potential ?? "-"),
        node("td", "numeric", candidate.age ?? "-"),
        node("td", "numeric", candidate.years_exp ?? "-"),
        append(node("td"), [tag(candidate.current_status || "Active", practiceSquadTone(candidate))]),
        node("td", null, bucketLabel(candidate.bucket)),
        node("td", "small-note", read || "-"),
        append(node("td"), [practiceSquadActionCell(candidate)]),
      ]);
      body.append(tr);
    });
    append(tableEl, [head, body]);
    wrap.append(tableEl);
    return wrap;
  }

  function practiceSquadRecentMoves(ps) {
    const moves = ps.recentMoves || [];
    const card = panel("Recent Practice Squad Movement", moves.length ? `${moves.length} recent` : "No recent movement");
    const body = panelBody(card);
    if (!moves.length) {
      body.append(node("div", "empty-state", "Practice squad signings and poaches will show here."));
      return card;
    }
    const list = node("div", "transaction-list compact-list");
    moves.slice(0, 8).forEach((move) => {
      const item = node("article", "transaction-item");
      const title = node("div", "transaction-title");
      title.append(
        tag(move.transaction_type || "Practice Squad", move.transaction_type === "Practice Squad Poaching" ? "warn" : "good"),
        node("strong", null, move.player_name || "Player"),
      );
      const route = [move.from_team, move.to_team].filter(Boolean).join(" -> ");
      append(item, [
        title,
        node("div", "small-note", `${move.transaction_date || ""}${route ? ` | ${route}` : ""}${move.player_position ? ` | ${move.player_position}` : ""}`),
        node("p", null, move.description || ""),
      ]);
      list.append(item);
    });
    body.append(list);
    return card;
  }

  function renderPracticeSquad() {
    const team = data.activeSave?.user_team || data.practiceSquad?.team || "MIN";
    setHeader("Practice Squad Selection", `Set ${team}'s 53-man roster and practice squad before the regular season.`);
    const root = document.createDocumentFragment();
    if (runnerMode() && state.practiceSquadLiveKey !== practiceSquadLiveKey() && !state.practiceSquadLoading) {
      loadLivePracticeSquad().then(render);
    }
    const ps = data.practiceSquad || { usage: {}, limits: {}, candidates: [] };
    const usage = ps.usage || {};
    const limits = ps.limits || {};
    const activeLimit = ps.activeLimit || limits.active || 53;
    const summary = panel("Registration Counter", `${ps.phase || "Regular Season"}${data.practiceSquadGeneratedAt ? ` | refreshed ${shortDateTime(data.practiceSquadGeneratedAt.replace("T", " "))}` : ""}`);
    if (state.practiceSquadLoading) panelBody(summary).append(node("div", "empty-state", "Refreshing practice squad rules..."));
    const metrics = node("section", "metric-grid compact-metrics ps-registration-metrics");
    append(metrics, [
      metric("Active Roster", `${ps.activeCount ?? 0}/${activeLimit}`, "Must be 53 or fewer", Number(ps.activeCount || 0) > Number(activeLimit) ? "warn" : "good"),
      metric("Practice Squad", `${usage.total || 0}/${limits.total || 17}`, "Normal slots plus IPP exemption", Number(usage.total || 0) >= Number(limits.total || 17) ? "warn" : ""),
      metric("Developmental", `${usage.developmental_count || 0}/${limits.developmental || 10}`, "Rookies and one/two-year players"),
      metric("Veteran Exceptions", `${usage.veteran_exception_count || 0}/${limits.veteranException || 6}`, "Three-plus accrued-season proxy"),
      metric("International", `${usage.international_exemption_count || 0}/${limits.internationalExemption || 1}`, "Exempt IPP slot"),
    ]);
    panelBody(summary).append(metrics);
    root.append(summary);

    const board = panel("Practice Squad Board", `${practiceSquadFilteredRows(ps.candidates || []).length} shown`);
    const body = panelBody(board);
    body.append(practiceSquadFilterTabs(ps.candidates || []));
    body.append(practiceSquadTable(ps));
    root.append(board);
    root.append(practiceSquadRecentMoves(ps));
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderRosterHub() {
    const team = data.activeSave?.user_team || data.depthChart?.team || "MIN";
    setHeader("View Roster", `${team} roster by position group, ratings, depth role, and player actions.`);
    const root = document.createDocumentFragment();
    const depth = data.depthChart || { rows: [], roster: [], units: [] };
    if (runnerMode() && state.depthChartLiveKey !== depthChartLiveKey() && !state.depthChartLoading) {
      loadLiveDepthChart().then(render);
    }
    const rows = rosterRows(depth);
    const starters = rows.filter((player) => player.primaryAssignment?.rank === 1).length;
    const rookies = rows.filter((player) => player.is_rookie).length;
    const contractAlerts = rows.filter((player) => player.contract?.type).length;
    const summary = panel("Roster Snapshot", `${depth.teamName || team}${data.depthChartGeneratedAt ? ` | refreshed ${shortDateTime(data.depthChartGeneratedAt.replace("T", " "))}` : ""}`);
    if (state.depthChartLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live roster..."));
    }
    const metrics = node("section", "metric-grid roster-metrics");
    append(metrics, [
      metric("Players", String(rows.length), "Active roster pool"),
      metric("Starters", String(starters), "Current depth chart #1s"),
      metric("Rookies", String(rookies), "Development watch"),
      metric("Contract Alerts", String(contractAlerts), "Expiring, cap, or restructure watch", contractAlerts ? "warn" : ""),
    ]);
    panelBody(summary).append(metrics);
    root.append(summary);

    const filteredRows = filteredRosterRows(rows);
    const selected = selectedRosterPlayer(filteredRows);
    const board = panel("Roster Board", `${filteredRows.length} players shown`);
    const body = panelBody(board);
    const toolbar = node("div", "roster-toolbar");
    const depthLink = node("button", "run-button compact", "Open Depth Chart");
    depthLink.type = "button";
    depthLink.addEventListener("click", () => {
      switchView("depth");
    });
    append(toolbar, [
      rosterGroupTabs(rows),
      rosterStatusFilter(rows),
      depthLink,
    ]);
    body.append(toolbar);
    body.append(filteredRows.length ? rosterPlayerTable(depth, filteredRows, selected) : node("div", "empty-state", "No players match the current filters."));
    const layout = node("div", "roster-manager-layout");
    append(layout, [board, rosterPlayerPanel(depth, selected)]);
    root.append(layout);
    finishRender(root);
  }

  function renderDepthChart() {
    const team = data.activeSave?.user_team || data.depthChart?.team || "MIN";
    setHeader("Depth Chart", `Set ${team}'s depth chart from the offensive and defensive formations you are running.`);
    const root = document.createDocumentFragment();
    const depth = data.depthChart || { rows: [], roster: [], units: [] };
    const selected = selectedDepthSlot(depth);
    if (runnerMode() && state.depthChartLiveKey !== depthChartLiveKey() && !state.depthChartLoading) {
      loadLiveDepthChart().then(render);
    }

    const summary = panel("Coach Room", `${depth.teamName || team}${data.depthChartGeneratedAt ? ` | refreshed ${shortDateTime(data.depthChartGeneratedAt.replace("T", " "))}` : ""}`);
    if (state.depthChartLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live depth chart..."));
    }
    panelBody(summary).append(depthRoomSummary(depth, selected));
    root.append(summary);

    const layout = node("div", "depth-editor-layout formation-depth-layout");
    const formationPanel = depthFormationPanel(depth, selected);

    const editorPanel = panel(selected ? `${selected.slot} Room` : "Depth Board", "Adjust roles");
    const editorBody = panelBody(editorPanel);
    if (!selected) {
      editorBody.append(node("div", "empty-state", "No depth chart slots are available."));
    } else {
      const assigned = node("div", "depth-player-stack");
      (selected.players || []).forEach((player) => {
        assigned.append(depthPlayerCard(depth, selected, player));
      });
      editorBody.append(sectionBlock("Assigned Roles", assigned));

      const rosterPanel = node("div", "depth-roster-strip");
      const eligible = depthEligiblePlayers(depth, selected, 48);
      eligible.forEach((player) => {
        rosterPanel.append(depthRosterChip(depth, selected, player));
      });
      editorBody.append(sectionBlock("Best Roster Fits", rosterPanel));
    }
    append(layout, [formationPanel, editorPanel]);
    root.append(layout);
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
    setHeader("Draft Room", "Work the board, make picks, and track CPU movement.");
    const root = document.createDocumentFragment();
    const draft = data.draft || { pickTotals: {}, board: [], pickQueue: [], events: [] };
    if (runnerMode() && state.draftLiveKey !== draftLiveKey() && !state.draftLoading) {
      loadLiveDraft().then(render);
    }
    const stateRow = draft.state;
    const board = draft.board || [];
    const sortedBoard = sortedDraftBoard(board);
    const draftPositions = [...new Set(board.map((player) => player.position).filter(Boolean))]
      .sort(footballPositionSort);
    const activeDraftPosition = draftPositions.includes(state.draftBoardPositionFilter) ? state.draftBoardPositionFilter : "all";
    state.draftBoardPositionFilter = activeDraftPosition;
    const visibleDraftBoard = activeDraftPosition === "all"
      ? sortedBoard
      : sortedBoard.filter((player) => player.position === activeDraftPosition);
    const selected = selectedDraftProspect(visibleDraftBoard);
    root.append(draftRoomTopline(draft, visibleDraftBoard, selected));

    const commands = data.commands || {};
    const draftMain = node("div", "draft-room-main");
    const sideRail = node("aside", "draft-side-rail");
    const controlsPanel = draftControlPanel(draft, commands, selected);
    const queuePanel = panel("Pick Queue", `${(draft.pickQueue || []).length || 0} picks`);
    queuePanel.classList.add("draft-queue-panel");
    const queueList = node("div", "list draft-queue-list");
    const currentQueuePick = Number(stateRow?.current_pick_number || (draft.pickQueue || []).find((pick) => !Number(pick.is_used))?.effective_pick_number || 0);
    (draft.pickQueue || []).forEach((pick) => {
      queueList.append(draftQueueRow(pick, currentQueuePick));
    });
    panelBody(queuePanel).append(queueList.children.length ? queueList : node("div", "empty-state", "No draft room queue exported."));

    const boardPanel = panel(
      "Draft Board",
      activeDraftPosition === "all"
        ? `${visibleDraftBoard.length || 0} shown`
        : `${visibleDraftBoard.length || 0} ${activeDraftPosition}`
    );
    boardPanel.classList.add("draft-board-panel");
    panelBody(boardPanel).append(draftBoardToolbar(board, visibleDraftBoard, draftPositions, activeDraftPosition));
    panelBody(boardPanel).append(draftBoardTable(visibleDraftBoard, selected));
    append(sideRail, [controlsPanel, queuePanel]);
    append(draftMain, [boardPanel, sideRail]);
    root.append(draftMain);
    if ((draft.userSelections || []).length) {
      root.append(draftUserSelectionsPanel(draft.userSelections || [], draft));
    }
    if (state.draftProspectPopoverOpen && selected) {
      root.append(draftProspectPopover(selected));
    }
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
    window.requestAnimationFrame(() => centerCurrentDraftQueuePick());
  }

  function draftRoomTopline(draft, visibleBoard, selected) {
    const draftState = draft?.state || null;
    const userTeam = draftState?.user_team || data.activeSave?.user_team || "MIN";
    const currentTeam = draftState?.current_team || "-";
    const onClock = isUserOnClock();
    const currentPick = draftState?.current_pick_number ? `#${draftState.current_pick_number}` : "-";
    const highConfidence = visibleBoard.filter((player) => confidenceSortValue(player.scout_confidence || player.scouting_confidence) >= 3).length;
    const mediumConfidence = visibleBoard.filter((player) => confidenceSortValue(player.scout_confidence || player.scouting_confidence) === 2).length;
    const selectedName = selected?.player_name || `${selected?.first_name || ""} ${selected?.last_name || ""}`.trim() || "No prospect selected";
    const wrap = node("section", "draft-room-topline");
    append(wrap, [
      teamLogo(currentDraftQueuePick(draft, draftState)?.teamLogo, currentTeam, "draft-room-topline-logo"),
      append(node("div", "draft-room-topline-main"), [
        node("span", "tag", draftState ? `Pick ${currentPick}` : `Draft ${draft?.year || ""}`),
        node("strong", null, draftState ? (onClock ? `${userTeam} is on the clock` : `${currentTeam} is on the clock`) : "Draft room ready"),
        node("small", null, draftState
          ? `${draft.pickTotals?.used || 0}/${draft.pickTotals?.total || 0} picks used. Board target: ${selectedName}.`
          : dateReached(draft?.draftDate) ? "Start the draft when ready." : `Draft date ${shortDate(draft?.draftDate)}.`),
      ]),
      append(node("div", "draft-room-topline-tags"), [
        tag(state.draftLoading ? "Refreshing" : (draftState?.status || "Not Started"), state.draftLoading ? "warn" : ""),
        tag(`${draft.pickTotals?.remaining || 0} left`, ""),
        tag(`${visibleBoard.length || 0} board`, ""),
        tag(`${highConfidence} high / ${mediumConfidence} med`, highConfidence ? "good" : ""),
        draft.orderFinalized === false ? tag("Order Not Final", "warn") : null,
      ]),
    ]);
    if (state.draftLoading) {
      wrap.append(node("div", "draft-room-topline-note", "Refreshing live draft room..."));
    } else if (draft.orderWarning) {
      wrap.append(node("div", "draft-room-topline-note warn", draft.orderWarning));
    }
    return wrap;
  }

  function draftWarRoomPanel(draft, visibleBoard, selected) {
    const draftState = draft?.state || null;
    const userTeam = draftState?.user_team || data.activeSave?.user_team || "MIN";
    const currentTeam = draftState?.current_team || "-";
    const onClock = isUserOnClock();
    const currentPick = draftState?.current_pick_number ? `#${draftState.current_pick_number}` : "-";
    const boardTop = visibleBoard.slice(0, 5);
    const highConfidence = visibleBoard.filter((player) => confidenceSortValue(player.scout_confidence || player.scouting_confidence) >= 3).length;
    const mediumConfidence = visibleBoard.filter((player) => confidenceSortValue(player.scout_confidence || player.scouting_confidence) === 2).length;
    const selectedName = selected?.player_name || `${selected?.first_name || ""} ${selected?.last_name || ""}`.trim() || "No prospect selected";
    const panelEl = panel("War Room", onClock ? `${userTeam} on the clock` : draftState ? `${currentTeam} on the clock` : "Pre-Draft");
    const body = panelBody(panelEl);
    const top = node("div", "war-room-hero");
    append(top, [
      teamLogo(currentDraftQueuePick(draft, draftState)?.teamLogo, currentTeam, "war-room-logo"),
      append(node("div", "war-room-copy"), [
        node("span", "tag", draftState ? `Pick ${currentPick}` : `Draft ${draft?.year || ""}`),
        node("strong", null, draftState ? (onClock ? "Your board is live" : `${currentTeam} is deciding`) : "Set the board before the clock starts"),
        node("small", null, draftState
          ? `${draft.pickTotals?.remaining || 0} picks remaining. Selected board target: ${selectedName}.`
          : dateReached(draft?.draftDate) ? "Start the room when you are ready to pick through the class." : `Draft date ${shortDate(draft?.draftDate)}.`),
      ]),
      tag(onClock ? "Make A Pick" : draftState ? "CPU Clock" : "Setup", onClock ? "good" : draftState ? "warn" : ""),
    ]);
    const intel = node("div", "war-room-intel");
    append(intel, [
      metric("Visible Board", String(visibleBoard.length || 0), "Current filter"),
      metric("High Confidence", String(highConfidence), `${mediumConfidence} medium`),
      metric("Current Target", selected?.position || "-", selectedName),
      metric("Your Picks", String((draft.userSelections || []).length), "Selections made"),
    ]);
    const targetStrip = node("div", "war-room-target-strip");
    boardTop.forEach((player, index) => {
      const chip = node("button", `war-room-target ${String(player.prospect_id) === String(selected?.prospect_id) ? "active" : ""}`.trim());
      chip.type = "button";
      chip.addEventListener("click", () => {
        openDraftProspectPopover(player.prospect_id);
      });
      append(chip, [
        node("span", null, `#${player.public_board_rank || player.scouting_rank || index + 1}`),
        node("strong", null, player.player_name || `${player.first_name || ""} ${player.last_name || ""}`.trim() || "Prospect"),
        node("small", null, `${player.position || "-"} | ${player.scout_confidence || player.scouting_confidence || "Low"}`),
      ]);
      targetStrip.append(chip);
    });
    append(body, [top, intel, targetStrip.children.length ? targetStrip : null]);
    return panelEl;
  }

  function centerCurrentDraftQueuePick() {
    const queue = document.querySelector(".draft-queue-list");
    const current = queue?.querySelector(".draft-queue-row.current-pick");
    if (!queue || !current) return;
    const queueRect = queue.getBoundingClientRect();
    const currentRect = current.getBoundingClientRect();
    const currentTop = currentRect.top - queueRect.top + queue.scrollTop;
    const rowHeight = Math.max(currentRect.height || 0, 1);
    const contextAbove = Math.min(rowHeight * 2.25, queue.clientHeight * 0.28);
    queue.scrollTop = Math.max(0, currentTop - contextAbove);
  }

  function draftQueueRow(pick, currentQueuePick) {
    const overallPick = pick.effective_pick_number || pick.pick_number;
    const isCurrent = Number(overallPick || 0) === Number(currentQueuePick || 0);
    const item = node("div", `row draft-queue-row ${pick.is_used ? "is-used" : ""} ${isCurrent ? "current-pick" : ""}`.trim());
    if (overallPick) item.dataset.pickNumber = String(overallPick);
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
        false,
        pick.current_team,
      ));
    } else {
      detail.textContent = pick.is_used ? "Selection recorded" : "Upcoming pick";
    }
    append(left, [teamLine, detail]);
    append(item, [left, tag(pick.is_used ? "Used" : "On Deck", pick.is_used ? "good" : "")]);
    return item;
  }

  function draftSelectionNameLink(playerId, prospectId, name, position, preferPlayer, team) {
    const label = `${name || "Selected Player"}${position ? ` (${position})` : ""}`;
    if (preferPlayer && playerId) return playerLink(playerId, label, "player-link strong-link", { team, position });
    if (prospectId) return prospectLink(prospectId, label, "prospect-link strong-link");
    if (playerId) return playerLink(playerId, label, "player-link strong-link", { team, position });
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
        draftSelectionNameLink(pick.playerId, pick.prospectId, pick.playerName || "Selected Player", pick.position, complete, pick.team),
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
        playerLink(selection.playerId, selection.playerName || "Selected Player", "player-link strong-link", {
          team: selection.team,
          position: selection.position,
        }),
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

  function sortedDraftBoard(players) {
    const sort = state.draftBoardSort || { key: "rank", direction: "asc" };
    return sortedProspectBoard(players, sort);
  }

  function sortedScoutingBoard(players) {
    const sort = state.scoutingBoardSort || { key: "rank", direction: "asc" };
    return sortedProspectBoard(players, sort);
  }

  function draftBoardToolbar(allPlayers, visiblePlayers, positions, activePosition) {
    const filterRow = node("div", "draft-board-toolbar");
    const filterLabel = node("label", "draft-position-filter");
    filterLabel.append(node("span", null, "Position"));
    const select = node("select");
    const allOption = node("option", null, "All positions");
    allOption.value = "all";
    select.append(allOption);
    positions.forEach((position) => {
      const option = node("option", null, position);
      option.value = position;
      select.append(option);
    });
    select.value = activePosition;
    select.addEventListener("change", () => {
      state.draftBoardPositionFilter = select.value;
      render();
    });
    filterLabel.append(select);
    filterRow.append(filterLabel);
    filterRow.append(node(
      "span",
      "quiet",
      activePosition === "all"
        ? `${allPlayers.length} prospects`
        : `${visiblePlayers.length} ${activePosition} prospects`
    ));
    return filterRow;
  }

  function sortedProspectBoard(players, sort) {
    const direction = sort.direction === "desc" ? -1 : 1;
    return [...players].sort((a, b) => {
      const left = draftSortValue(a, sort.key);
      const right = draftSortValue(b, sort.key);
      const result = compareDraftSortValues(left, right);
      if (result) return result * direction;
      return compareDraftSortValues(draftSortValue(a, "rank"), draftSortValue(b, "rank"));
    });
  }

  function draftSortValue(player, key) {
    if (!player) return null;
    const name = player.player_name || `${player.first_name || ""} ${player.last_name || ""}`.trim();
    const projectedPick = Number(player.projected_pick || 0);
    const projectedRound = Number(player.projected_round || 0);
    const projectedOverall = projectedRound ? ((projectedRound - 1) * 32) + (projectedPick || 32) : null;
    const values = {
      rank: firstNumber(player.public_board_rank, player.scouting_rank),
      player: name,
      position: player.position || "",
      size: firstNumber(player.height_in, 0) * 400 + firstNumber(player.weight_lbs, 0),
      age: firstNumber(player.age),
      class: player.college_class || "",
      school: player.college || "",
      projection: projectedOverall,
      role: roleLabel(player.primary_role || player.archetype),
      forty: firstNumber(player.forty_yard_dash),
      ten: firstNumber(player.ten_yard_split),
      vertical: firstNumber(player.vertical_jump_in),
      broad: firstNumber(player.broad_jump_in),
      athletic: firstNumber(player.athletic_score),
      grade: firstNumber(player.scout_grade),
      potential: firstNumber(player.scout_ceiling),
      confidence: confidenceSortValue(player.scout_confidence || player.scouting_confidence),
      risk: riskSortValue(player.scout_risk),
      seniorBowl: seniorBowlSortValue(seniorBowlLabel(player)),
    };
    return values[key] ?? null;
  }

  function compareDraftSortValues(left, right) {
    const leftMissing = left === null || left === undefined || left === "";
    const rightMissing = right === null || right === undefined || right === "";
    if (leftMissing && rightMissing) return 0;
    if (leftMissing) return 1;
    if (rightMissing) return -1;
    if (typeof left === "number" && typeof right === "number") return left - right;
    return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
  }

  function firstNumber(...values) {
    for (const value of values) {
      if (value === null || value === undefined || value === "") continue;
      const number = Number(value);
      if (Number.isFinite(number)) return number;
    }
    return null;
  }

  function confidenceSortValue(confidence) {
    const text = String(confidence || "").toLowerCase();
    if (text.includes("very high")) return 4;
    if (text.includes("high")) return 3;
    if (text.includes("medium")) return 2;
    if (text.includes("low")) return 1;
    return 0;
  }

  function riskSortValue(risk) {
    const text = String(risk || "").toLowerCase();
    if (text.includes("high")) return 3;
    if (text.includes("medium")) return 2;
    if (text.includes("low")) return 1;
    return 0;
  }

  function seniorBowlSortValue(label) {
    if (label === "Accepted") return 3;
    if (label === "Skipped") return 2;
    if (label && label !== "-") return 1;
    return 0;
  }

  function draftBoardTable(players, selected) {
    if (!players.length) return node("div", "empty-state", "No draft prospects exported.");
    const wrap = node("div", "table-wrap draft-table-wrap");
    const tableEl = node("table", "data-table draft-board-table");
    const thead = node("thead");
    const headerRow = node("tr");
    [
      ["rank", "Rank"],
      ["player", "Player"],
      ["position", "Pos"],
      ["size", "Ht/Wt"],
      ["age", "Age"],
      ["class", "Class"],
      ["school", "School"],
      ["projection", "Proj"],
      ["forty", "40"],
      ["ten", "10"],
      ["vertical", "Vert"],
      ["broad", "Broad"],
      ["athletic", "Ath"],
      ["grade", "Rating"],
      ["potential", "Potential"],
      ["confidence", "Confidence"],
      ["risk", "Risk"],
      ["seniorBowl", "SB"],
      [null, "Pick"],
    ].forEach(([key, label]) => {
      const th = node("th");
      th.append(key ? draftSortHeader(key, label) : node("span", null, label));
      headerRow.append(th);
    });
    thead.append(headerRow);
    const tbody = node("tbody");
    players.forEach((player) => {
      const tr = node("tr", String(player.prospect_id) === String(selected?.prospect_id) ? "selected-row" : "");
      tr.addEventListener("click", () => {
        openDraftProspectPopover(player.prospect_id);
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
        valueOrDash(player.scout_grade),
        valueOrDash(player.scout_ceiling),
        draftConfidenceCell(player),
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

  function draftSortHeader(key, label) {
    const active = state.draftBoardSort?.key === key;
    const direction = active ? state.draftBoardSort.direction : "asc";
    const button = node("button", `table-sort-button ${active ? "active" : ""}`.trim());
    button.type = "button";
    button.title = `Sort draft board by ${label}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const current = state.draftBoardSort || {};
      state.draftBoardSort = {
        key,
        direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
      };
      render();
    });
    append(button, [
      node("span", null, label),
      node("small", "sort-indicator", active ? (direction === "asc" ? "^" : "v") : "-"),
    ]);
    return button;
  }

  function prospectNameButton(player) {
    const wrap = node("span", "prospect-name-cell");
    const button = node("button", "prospect-link", player.player_name || `${player.first_name || ""} ${player.last_name || ""}`.trim());
    button.type = "button";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openDraftProspectPopover(player.prospect_id);
    });
    append(wrap, [
      button,
      node("small", null, roleLabel(player.primary_role || player.archetype)),
    ]);
    return wrap;
  }

  function openDraftProspectPopover(prospectId) {
    state.selectedDraftProspectId = prospectId;
    state.draftProspectPopoverOpen = true;
    render();
  }

  function closeDraftProspectPopover() {
    state.draftProspectPopoverOpen = false;
    render();
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

  function draftConfidenceCell(player) {
    const confidence = player.scout_confidence || player.scouting_confidence || "Low";
    const level = player.scouting_level ?? player.scout_confidence_score ?? player.confidence_score;
    const times = Number(player.times_scouted || 0);
    const wrap = node("span", "stacked-cell confidence-cell");
    append(wrap, [
      node("strong", null, confidence),
      level !== undefined && level !== null && level !== ""
        ? node("small", null, `${Number(level)}%${times ? ` | ${times}x` : ""}`)
        : times
        ? node("small", null, `${times}x`)
        : null,
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

  function combineStatusAvailable() {
    return Boolean(data.scouting?.workoutVisibility?.combineAvailable);
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

    if (combineStatusAvailable()) {
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
    } else {
      const workoutDate = data.scouting?.workoutVisibility?.combineDate || data.scouting?.workoutVisibility?.combineEndDate;
      body.append(sectionBlock("Combine", node("div", "empty-state", workoutDate ? `Combine data unlocks after ${shortDate(workoutDate)}.` : "Combine data is not available yet.")));
    }

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

  function draftProspectPopover(player, options = {}) {
    const overlay = node("div", "prospect-popover-overlay");
    overlay.setAttribute("role", "presentation");
    overlay.addEventListener("click", closeDraftProspectPopover);
    const drawer = node("aside", "prospect-popover");
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-label", options.ariaLabel || "Draft prospect card");
    drawer.addEventListener("click", (event) => event.stopPropagation());
    const top = node("div", "prospect-popover-top");
    append(top, [
      append(node("div", "prospect-popover-title"), [
        node("span", "tag", `#${player.public_board_rank || player.scouting_rank || "-"}`),
        node("strong", null, player.player_name || "Prospect"),
        node("small", null, `${player.position || "-"} | ${player.college || "-"} | ${player.scout_confidence || player.scouting_confidence || "Low"} confidence`),
      ]),
      closeButton(closeDraftProspectPopover),
    ]);
    const card = prospectCard(player, options.cardOptions || {});
    card.classList.add("prospect-popover-card");
    append(drawer, [top, card]);
    overlay.append(drawer);
    return overlay;
  }

  function closeButton(onClick) {
    const button = node("button", "icon-button close-button", "x");
    button.type = "button";
    button.title = "Close";
    button.setAttribute("aria-label", "Close");
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      onClick();
    });
    return button;
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
    if (!runnerMode()) {
      const unavailable = node("button", "run-button", "Pick");
      unavailable.type = "button";
      unavailable.disabled = true;
      unavailable.title = "Live actions are unavailable.";
      wrap.append(unavailable);
    } else {
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

  function currentDraftQueuePick(draft, draftState) {
    const queue = draft?.pickQueue || [];
    const currentNumber = Number(draftState?.current_pick_number || 0);
    if (currentNumber) {
      const match = queue.find((pick) => Number(pick.effective_pick_number || pick.pick_number || 0) === currentNumber);
      if (match) return match;
    }
    return queue.find((pick) => !Number(pick.is_used || 0)) || queue[0] || null;
  }

  function renderInbox() {
    setHeader("Inbox", "Messages from scouting, staff, league events, and future front-office systems.");
    const root = document.createDocumentFragment();
    if (runnerMode() && state.inboxLiveKey !== inboxLiveKey() && !state.inboxLoading) {
      loadLiveInbox().then(render);
    }
    root.append(renderInboxPanel({
      limit: 40,
      title: "Inbox",
      kicker: `All Messages${data.inboxGeneratedAt ? ` | refreshed ${shortDateTime(data.inboxGeneratedAt.replace("T", " "))}` : ""}`,
    }));
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
    if (state.inboxLoading) {
      body.append(node("div", "empty-state", "Refreshing live inbox..."));
    }

    const list = node("div", "inbox-list");
    if (!messages.length) {
      list.append(node("div", "empty-state", "No messages yet. Scouts, league events, and staff notes will land here."));
    } else {
      messages.slice(0, limit).forEach((message) => {
        const card = node("article", `message-card ${Number(message.is_read || 0) ? "" : "unread"}`.trim());
        append(card, [
          append(node("div", "message-top"), [
            append(node("strong", null), inboxLinkedText(message.title || "Inbox Message", message)),
            node("span", "event-date", shortDate(message.message_date)),
          ]),
          append(node("p", null), inboxLinkedText(message.body || "", message)),
          append(node("div", "message-meta"), [
            node("span", null, message.category || "Inbox"),
            node("span", null, message.source || "Front Office"),
            inboxRelatedLink(message),
          ]),
        ]);
        list.append(card);
      });
    }
    body.append(list);
    return p;
  }

  function inboxRelatedLink(message) {
    const player = message.relatedPlayer;
    if (player?.player_id) {
      return playerLink(player.player_id, player.player_name || "Player", "message-related-link", {
        team: player.team,
        position: player.position,
      });
    }
    const prospect = message.relatedProspect;
    if (prospect?.prospect_id) {
      return prospectLink(prospect.prospect_id, prospect.player_name || "Prospect", "message-related-link");
    }
    return null;
  }

  function inboxLinkedText(text, message) {
    const fragment = document.createDocumentFragment();
    const cleanText = String(text || "");
    const entities = [];
    const seen = new Set();
    const addEntity = (key, name, makeLink) => {
      const label = String(name || "").trim();
      if (!label || seen.has(key)) return;
      seen.add(key);
      entities.push({ name: label, lowerName: label.toLowerCase(), makeLink });
    };
    if (message.relatedPlayer?.player_id) {
      addEntity(
        `player:${message.relatedPlayer.player_id}`,
        message.relatedPlayer.player_name,
        () => playerLink(
          message.relatedPlayer.player_id,
          message.relatedPlayer.player_name,
          "message-inline-link",
          { team: message.relatedPlayer.team, position: message.relatedPlayer.position },
        ),
      );
    }
    (message.mentionedPlayers || []).forEach((player) => {
      if (!player?.player_id) return;
      addEntity(
        `player:${player.player_id}`,
        player.player_name,
        () => playerLink(player.player_id, player.player_name, "message-inline-link", {
          team: player.team,
          position: player.position,
        }),
      );
    });
    if (message.relatedProspect?.prospect_id) {
      addEntity(
        `prospect:${message.relatedProspect.prospect_id}`,
        message.relatedProspect.player_name,
        () => prospectLink(
          message.relatedProspect.prospect_id,
          message.relatedProspect.player_name,
          "message-inline-link",
        ),
      );
    }
    if (!entities.length) {
      fragment.append(cleanText);
      return fragment;
    }
    const lowerText = cleanText.toLowerCase();
    let cursor = 0;
    const sortedEntities = [...entities].sort((a, b) => b.name.length - a.name.length);
    while (cursor < cleanText.length) {
      let next = null;
      sortedEntities.forEach((entity) => {
        const index = lowerText.indexOf(entity.lowerName, cursor);
        if (index < 0) return;
        if (!next || index < next.index || (index === next.index && entity.name.length > next.entity.name.length)) {
          next = { index, entity };
        }
      });
      if (!next) break;
      if (next.index > cursor) fragment.append(cleanText.slice(cursor, next.index));
      fragment.append(next.entity.makeLink());
      cursor = next.index + next.entity.name.length;
    }
    if (cursor < cleanText.length) fragment.append(cleanText.slice(cursor));
    return fragment;
  }

  function renderLeagueNews() {
    setHeader("League News", "Public league-wide stories: prospect buzz, injuries, suspensions, holdouts, trades, roster moves, rumors, and market noise.");
    const root = document.createDocumentFragment();
    const news = data.leagueNews || { items: [], categories: [], counts: {} };
    if (runnerMode() && state.leagueNewsLiveKey !== leagueNewsLiveKey() && !state.leagueNewsLoading) {
      loadLiveLeagueNews().then(render);
    }
    const items = news.items || [];
    const filtered = state.newsFilter === "all"
      ? items
      : items.filter((item) => String(item.category || "League") === state.newsFilter);

    const summary = panel("League Wire", `${news.updatedAt ? `Updated ${shortDate(news.updatedAt)}` : "Public Feed"}${data.leagueNewsGeneratedAt ? ` | refreshed ${shortDateTime(data.leagueNewsGeneratedAt.replace("T", " "))}` : ""}`);
    const body = panelBody(summary);
    if (state.leagueNewsLoading) {
      body.append(node("div", "empty-state", "Refreshing live league news..."));
    }
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
    if (item.player_id && item.player_name) return playerLink(item.player_id, title, "news-title-link", {
      team: item.team,
      position: item.player_position,
    });
    if (item.prospect_id && item.prospect_name) return prospectLink(item.prospect_id, title, "news-title-link");
    return node("strong", null, title);
  }

  function newsSubjectNode(item) {
    if (item.player_id && item.player_name) return playerLink(item.player_id, item.player_name, "news-subject-link", {
      team: item.team,
      position: item.player_position,
    });
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

  function renderTransactions() {
    setHeader("League Transactions", "A chronological league ledger of signings, releases, trades, contract moves, waivers, draft picks, and roster status changes.");
    const root = document.createDocumentFragment();
    const transactions = data.transactions || { items: [], categories: [], counts: {} };
    if (runnerMode() && state.transactionsLiveKey !== transactionsLiveKey() && !state.transactionsLoading) {
      loadLiveTransactions().then(render);
    }
    const items = transactions.items || [];
    const filtered = state.transactionsCategoryFilter === "all"
      ? items
      : items.filter((item) => String(item.category || "Other") === state.transactionsCategoryFilter);

    const summary = panel("Transaction Ledger", `${data.transactionsGeneratedAt ? `refreshed ${shortDateTime(data.transactionsGeneratedAt.replace("T", " "))}` : "Newest first"}${transactions.includeBaseline ? " | including baseline imports" : " | baseline imports hidden"}`);
    const body = panelBody(summary);
    if (state.transactionsLoading) body.append(node("div", "empty-state", "Refreshing live transactions..."));
    append(body, [
      append(node("section", "metric-grid transaction-metrics"), [
        metric("Transactions", String(transactions.counts?.total || items.length), "Logged league moves"),
        metric("Roster", String(transactions.counts?.Roster || 0), "Signings, releases, waivers"),
        metric("Contracts", String(transactions.counts?.Contract || 0), "Extensions and restructures"),
        metric("Draft", String(transactions.counts?.Draft || 0), "Selections and pick moves"),
      ]),
      transactionFilterRow(transactions),
    ]);
    root.append(summary);

    const listPanel = panel("All Transactions", `${filtered.length} shown`);
    const list = node("div", "transaction-list");
    groupTransactionsByDate(filtered).forEach((group) => list.append(transactionDateGroup(group)));
    panelBody(listPanel).append(list.children.length ? list : node("div", "empty-state", "No transactions match this filter yet."));
    root.append(listPanel);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function transactionFilterRow(transactions) {
    const filters = node("div", "transaction-filter-row");
    filters.append(transactionFilterButton("all", "All"));
    (transactions.categories || []).forEach((category) => filters.append(transactionFilterButton(category, category)));
    return filters;
  }

  function transactionFilterButton(filter, label) {
    const button = node("button", `transaction-filter ${state.transactionsCategoryFilter === filter ? "active" : ""}`.trim(), label);
    button.type = "button";
    button.addEventListener("click", () => {
      state.transactionsCategoryFilter = filter;
      render();
    });
    return button;
  }

  function groupTransactionsByDate(items) {
    const groups = [];
    const byDate = new Map();
    items.forEach((item) => {
      const key = item.date || "Unknown";
      if (!byDate.has(key)) {
        const group = { date: key, items: [] };
        byDate.set(key, group);
        groups.push(group);
      }
      byDate.get(key).items.push(item);
    });
    return groups;
  }

  function transactionDateGroup(group) {
    const wrap = node("section", "transaction-date-group");
    wrap.append(append(node("div", "transaction-date-header"), [
      node("strong", null, shortDate(group.date)),
      node("span", null, `${group.items.length} move${group.items.length === 1 ? "" : "s"}`),
    ]));
    group.items.forEach((item) => wrap.append(transactionRow(item)));
    return wrap;
  }

  function transactionRow(item) {
    const row = node("article", "transaction-row");
    const player = item.playerId && item.player
      ? playerLink(item.playerId, `${item.player}${item.position ? `, ${item.position}` : ""}`, "transaction-player-link", {
        team: item.team || item.toTeam || item.fromTeam,
        position: item.position,
      })
      : node("span", "transaction-player-empty", item.player || "League move");
    const teams = [item.fromTeam, item.toTeam].filter(Boolean).join(" -> ")
      || [item.team, item.secondaryTeam].filter(Boolean).join(" / ")
      || "-";
    const moneyBits = [];
    if (Number(item.capDeltaCurrent || 0)) moneyBits.push(`Cap ${money(item.capDeltaCurrent)}`);
    if (Number(item.cashDelta || 0)) moneyBits.push(`Cash ${money(item.cashDelta)}`);
    append(row, [
      node("span", `transaction-type-pill ${transactionTypeClass(item.category || item.type)}`, item.type || "Transaction"),
      append(node("div", "transaction-main"), [
        append(node("div", "transaction-line"), [
          player,
          node("span", "transaction-team-flow", teams),
        ]),
        node("p", null, item.description || transactionFallbackDescription(item)),
        append(node("div", "transaction-meta"), [
          node("span", null, item.category || "Other"),
          item.phase ? node("span", null, item.phase) : null,
          item.week ? node("span", null, `Week ${item.week}`) : null,
          moneyBits.length ? node("span", null, moneyBits.join(" | ")) : null,
        ]),
      ]),
    ]);
    return row;
  }

  function transactionFallbackDescription(item) {
    if (item.oldStatus || item.newStatus) return `${item.oldStatus || "-"} to ${item.newStatus || "-"}`;
    return [item.team, item.secondaryTeam, item.fromTeam, item.toTeam].filter(Boolean).join(" / ") || "Transaction logged.";
  }

  function transactionTypeClass(value) {
    const text = String(value || "").toLowerCase();
    if (text.includes("sign")) return "signing";
    if (text.includes("release") || text.includes("waiver")) return "release";
    if (text.includes("trade") || text.includes("draft")) return "trade";
    if (text.includes("contract") || text.includes("extension") || text.includes("restructure")) return "contract";
    return "other";
  }

  function renderInjuries() {
    setHeader("Injuries", "Current availability and recent injury reports across the league.");
    const root = document.createDocumentFragment();
    const injuries = data.injuries || { active: [], recent: [], counts: {} };
    if (runnerMode() && state.injuriesLiveKey !== injuriesLiveKey() && !state.injuriesLoading) {
      loadLiveInjuries().then(render);
    }
    const activeItems = filterInjuryItems(injuries.active || []);
    const recentItems = filterInjuryItems(injuries.recent || []);
    const summary = panel("Medical Center", `${data.injuriesGeneratedAt ? `refreshed ${shortDateTime(data.injuriesGeneratedAt.replace("T", " "))}` : injuries.updatedAt ? `updated ${shortDateTime(String(injuries.updatedAt).replace("T", " "))}` : "Live save data"}`);
    const body = panelBody(summary);
    if (state.injuriesLoading) body.append(node("div", "empty-state", "Refreshing injury report..."));
    append(body, [
      append(node("section", "metric-grid injury-metrics"), [
        metric("Active", String(injuries.counts?.active || 0), "League injuries"),
        metric("Your Team", String(injuries.counts?.userActive || 0), "Active injuries", injuries.counts?.userActive ? "warn" : "good"),
        metric("Major", String(injuries.counts?.majorActive || 0), "Longer absences", injuries.counts?.majorActive ? "warn" : ""),
        metric("Recent", String(injuries.counts?.recent || 0), "Latest injury events"),
      ]),
      injuryFilterRow(),
    ]);
    root.append(summary);

    const activePanel = panel("Active Injuries", `${activeItems.length} shown`);
    panelBody(activePanel).append(activeItems.length ? injuryActiveTable(activeItems) : node("div", "empty-state", "No active injuries match this filter."));
    root.append(activePanel);

    const recentPanel = panel("Recent Injury Reports", `${recentItems.length} shown`);
    const recentList = node("div", "injury-report-list");
    recentItems.slice(0, 80).forEach((item) => recentList.append(injuryReportRow(item)));
    panelBody(recentPanel).append(recentList.children.length ? recentList : node("div", "empty-state", "No recent injury reports match this filter."));
    root.append(recentPanel);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function injuryFilterRow() {
    const filters = node("div", "injury-filter-row");
    [
      ["all", "All"],
      ["user", "Your Team"],
      ["major", "Major"],
    ].forEach(([value, label]) => {
      const button = node("button", `injury-filter ${state.injuriesScopeFilter === value ? "active" : ""}`.trim(), label);
      button.type = "button";
      button.addEventListener("click", () => {
        state.injuriesScopeFilter = value;
        render();
      });
      filters.append(button);
    });
    return filters;
  }

  function filterInjuryItems(items) {
    if (state.injuriesScopeFilter === "user") return items.filter((item) => item.isUserTeam);
    if (state.injuriesScopeFilter === "major") {
      return items.filter((item) => Number(item.expectedGames || 0) >= 4 || ["major", "severe"].includes(String(item.severity || "").toLowerCase()));
    }
    return items;
  }

  function injuryActiveTable(items) {
    return table(
      ["Player", "Team", "Injury", "Status", "Return", "Expected"],
      items.map((item) => [
        playerLink(item.playerId, `${item.playerName || "Player"}${item.position ? `, ${item.position}` : ""}`, "player-link strong-link", {
          team: item.team,
          position: item.position,
        }),
        item.team || "-",
        injuryLabelNode(item),
        item.status || "-",
        item.returnDate ? shortDate(item.returnDate) : "-",
        injuryExpectedText(item),
      ]),
    );
  }

  function injuryReportRow(item) {
    const row = node("article", `injury-report-row ${item.isUserTeam ? "user-team" : ""}`.trim());
    const player = playerLink(item.playerId, `${item.playerName || "Player"}${item.position ? `, ${item.position}` : ""}`, "player-link strong-link", {
      team: item.team,
      position: item.position,
    });
    append(row, [
      append(node("div", "injury-report-main"), [
        append(node("div", "injury-report-top"), [
          player,
          node("span", `injury-severity ${injurySeverityClass(item.severity)}`, item.severity || "injury"),
        ]),
        node("p", null, item.description || `${item.injury || "Injury"}; expected to miss ${injuryExpectedText(item).toLowerCase()}.`),
        append(node("div", "injury-report-meta"), [
          node("span", null, item.team || "-"),
          item.week ? node("span", null, `Week ${item.week}`) : null,
          item.date ? node("span", null, shortDate(item.date)) : null,
          item.source ? node("span", null, sourceLabel(item.source)) : null,
        ]),
      ]),
    ]);
    return row;
  }

  function injuryLabelNode(item) {
    const wrap = node("span", "injury-label-stack");
    append(wrap, [
      node("strong", null, item.injury || "Injury"),
      node("small", null, [item.bodyPart, item.severity].filter(Boolean).join(" | ")),
    ]);
    return wrap;
  }

  function injuryExpectedText(item) {
    const games = Number(item.expectedGames || 0);
    if (games > 0) return `${games} game${games === 1 ? "" : "s"}`;
    const days = Number(item.expectedDays || item.daysRemaining || 0);
    if (days > 0) return `${days} day${days === 1 ? "" : "s"}`;
    return "TBD";
  }

  function injurySeverityClass(value) {
    const text = String(value || "").toLowerCase();
    if (text.includes("severe") || text.includes("major")) return "major";
    if (text.includes("moderate")) return "moderate";
    return "minor";
  }

  function sourceLabel(value) {
    const text = String(value || "").replaceAll("_", " ");
    return text.replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function renderScouting() {
    setHeader("Scouting", "Build your draft board, raise confidence, and choose where your staff spends the week.");
    const root = document.createDocumentFragment();
    if (runnerMode() && state.scoutingLiveKey !== scoutingLiveKey() && !state.scoutingLoading) {
      loadLiveScouting().then(render);
    }
    root.append(renderScoutingDesk({ limit: 240 }));
    const selected = selectedDraftProspect(data.scouting?.board || []);
    if (state.draftProspectPopoverOpen && selected) {
      root.append(draftProspectPopover(selected, {
        ariaLabel: "Scouting prospect card",
        cardOptions: { showDraftActions: false },
      }));
    }
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderScoutingDesk(options = {}) {
    const limit = Number(options.limit || 40);
    const scouting = data.scouting || {};
    const p = panel("Scouting HQ", `${scouting.draftYear ? `${scouting.draftYear} Draft` : "Draft Class"}${data.scoutingGeneratedAt ? ` | refreshed ${shortDateTime(data.scoutingGeneratedAt.replace("T", " "))}` : ""}`);
    p.classList.add("scouting-desk-panel");
    const body = panelBody(p);
    if (state.scoutingLoading) {
      body.append(node("div", "empty-state", "Refreshing live scouting..."));
    }
    const metrics = node("section", "metric-grid scouting-metrics");
    append(metrics, [
      metric("Period", scouting.period?.label || "-", scouting.weeklyWindow?.open ? "Weekly scouting open" : "Weekly scouting closed"),
      metric("Weekly Action", actionCountText(scouting), scouting.weeklyWindow?.open ? (scouting.usedAction ? roleLabel(scouting.usedAction) : "Available") : scouting.weeklyWindow?.reason || "Closed"),
      metric("Top 30", `${scouting.top30?.used || 0}/30`, scouting.top30?.locked ? "Closed" : "Visits used"),
      metric("Board", String(scouting.counts?.visible || (scouting.board || []).length || 0), `${scouting.counts?.hiddenRemaining || 0} undiscovered`),
      metric("Unread", String(scouting.counts?.unread || 0), "staff notes", scouting.counts?.unread ? "warn" : ""),
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
    const localSpecificUses = localScoutingSpecificCost(scouting.board || []);
    const actionStarted = Boolean(scouting.weeklyActionStarted) || localSpecificUses > 0;
    const nonSpecificActionUsed = Boolean(scouting.nonSpecificActionUsed);
    const specificUses = Number(scouting.actionUses?.specific || 0) + localSpecificUses;
    const specificLimit = Number(scouting.actionLimits?.specific || scouting.weeklyWindow?.specificCount || 2);
    const weeklyOpen = Boolean(scouting.weeklyWindow?.open);
    const closedReason = scouting.weeklyWindow?.reason || "Weekly scouting is currently closed.";
    const autoCount = Number(scouting.weeklyWindow?.autoAssignCount || 3);
    const randomCount = Number(scouting.weeklyWindow?.randomCount || 3);
    const discoverRandomCount = Number(scouting.weeklyWindow?.discoverRandomCount || 2);
    const discoverCount = Number(scouting.weeklyWindow?.discoverCount || 4);
    body.append(scoutingWindowBanner(scouting, { weeklyOpen, closedReason, choiceUsed, autoCount }));
    const controls = node("div", "scouting-choice-grid");
    append(controls, [
      scoutingActionButton(`Auto Assign ${autoCount}`, "scouting_auto", used.auto_assign, weeklyOpen ? `Staff advances ${autoCount} priority prospects one confidence tier.` : closedReason, !weeklyOpen || (actionStarted && !used.auto_assign)),
      scoutingActionButton(`Scout ${randomCount} Random`, "scouting_random_two", used.random_two, weeklyOpen ? `${randomCount} fresh cross-checks from the visible board.` : closedReason, !weeklyOpen || (actionStarted && !used.random_two)),
      scoutingActionButton(
        `Scout ${discoverRandomCount} + ${discoverCount} Discoveries`,
        "scouting_discover_four",
        used.discover_four,
        !weeklyOpen ? closedReason : Number(scouting.counts?.hiddenRemaining || 0) <= 0 ? "No hidden prospects remain." : `Scout ${discoverRandomCount} random visible prospects and discover ${discoverCount} random off-board prospects at low confidence.`,
        !weeklyOpen || (actionStarted && !used.discover_four) || Number(scouting.counts?.hiddenRemaining || 0) <= 0,
      ),
      append(node("div", "scouting-specific-card"), [
        node("strong", null, `Scout ${specificLimit} Specific Players`),
        node("small", null, !weeklyOpen ? closedReason : nonSpecificActionUsed ? "Weekly scouting choice already used." : `${Math.min(specificUses, specificLimit)}/${specificLimit} used. QB deep dives consume the full week.`),
      ]),
    ]);

    const eventGrid = node("div", "grid scouting-event-grid");
    append(eventGrid, [renderSeniorBowlPanel(scouting), renderTop30Visits(scouting)]);
    const controlDeck = node("div", "scouting-control-deck");
    append(controlDeck, [metrics, controls, eventGrid]);
    body.append(controlDeck);

    normalizeScoutingFilters(scouting.board || []);
    const filteredBoard = filteredScoutingBoard(scouting.board || []);
    const visibleBoard = sortedScoutingBoard(filteredBoard).slice(0, limit);
    const selected = selectedDraftProspect(visibleBoard);
    const layout = node("div", "scouting-layout");
    const boardPanel = panel("Prospect Board", `${visibleBoard.length}/${(scouting.board || []).length} shown`);
    boardPanel.classList.add("scouting-board-panel");
    panelBody(boardPanel).append(scoutingBoardToolbar(scouting.board || [], filteredBoard));
    panelBody(boardPanel).append(scoutingBoardTable(visibleBoard, selected));
    append(layout, [boardPanel]);
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

  function scoutingFilterSelect(label, value, options, onChange) {
    const wrap = node("label", "scouting-filter");
    wrap.append(node("span", null, label));
    const select = node("select");
    options.forEach((option) => {
      const item = node("option", null, option.label);
      item.value = option.value;
      item.selected = option.value === value;
      select.append(item);
    });
    select.addEventListener("change", () => onChange(select.value));
    wrap.append(select);
    return wrap;
  }

  function scoutingBoardToolbar(allProspects, filteredProspects) {
    const positions = scoutingBoardPositions(allProspects);
    const activePosition = state.scoutingPositionFilter;
    const confidenceOptions = ["Very High", "High", "Medium", "Low", "Unscouted"]
      .filter((confidence) => (allProspects || []).some((prospect) => String(prospect.scouting_confidence || "Low") === confidence));
    const toolbar = node("div", "scouting-board-toolbar");
    const filters = node("div", "scouting-filter-row");
    append(filters, [
      scoutingFilterSelect(
        "Position",
        activePosition,
        [{ value: "all", label: "All positions" }, ...positions.map((position) => ({ value: position, label: position }))],
        (value) => {
          state.scoutingPositionFilter = value;
          render();
        },
      ),
      scoutingFilterSelect(
        "Confidence",
        state.scoutingConfidenceFilter,
        [{ value: "all", label: "All confidence" }, ...confidenceOptions.map((confidence) => ({ value: confidence, label: confidence }))],
        (value) => {
          state.scoutingConfidenceFilter = value;
          render();
        },
      ),
    ]);
    const summary = node("div", "scouting-board-summary");
    append(summary, [
      tag(`${filteredProspects.length} prospects`),
      tag(`${filteredProspects.filter((prospect) => confidenceSortValue(prospect.scouting_confidence) >= 3).length} high confidence`, "good"),
      tag(`${filteredProspects.filter((prospect) => prospect.queued).length} selected`, filteredProspects.some((prospect) => prospect.queued) ? "warn" : ""),
    ]);
    append(toolbar, [filters, summary]);
    return toolbar;
  }

  function filteredScoutingBoard(prospects) {
    return [...(prospects || [])]
      .filter((prospect) => state.scoutingPositionFilter === "all" || prospect.position === state.scoutingPositionFilter)
      .filter((prospect) => state.scoutingConfidenceFilter === "all" || String(prospect.scouting_confidence || "Low") === state.scoutingConfidenceFilter);
  }

  function scoutingBoardPositions(prospects) {
    return [...new Set((prospects || []).map((prospect) => prospect.position).filter(Boolean))]
      .sort(footballPositionSort);
  }

  function normalizeScoutingFilters(prospects) {
    const positions = scoutingBoardPositions(prospects);
    if (state.scoutingPositionFilter !== "all" && !positions.includes(state.scoutingPositionFilter)) {
      state.scoutingPositionFilter = "all";
    }
    const confidences = ["Very High", "High", "Medium", "Low", "Unscouted"]
      .filter((confidence) => (prospects || []).some((prospect) => String(prospect.scouting_confidence || "Low") === confidence));
    if (state.scoutingConfidenceFilter !== "all" && !confidences.includes(state.scoutingConfidenceFilter)) {
      state.scoutingConfidenceFilter = "all";
    }
  }

  function actionCountText(scouting) {
    const specificUses = Number(scouting.actionUses?.specific || 0) + localScoutingSpecificCost(scouting.board || []);
    const specificLimit = Number(scouting.actionLimits?.specific || scouting.weeklyWindow?.specificCount || 2);
    if (specificUses > 0) return `${Math.min(specificUses, specificLimit)}/${specificLimit}`;
    return scouting.weeklyChoiceUsed ? "1/1" : "0/1";
  }

  function localScoutingSpecificCost(board) {
    const ids = new Set(localScoutingSelectionIds().map(String));
    return (board || []).reduce((total, prospect) => {
      if (!ids.has(String(prospect.prospect_id)) || prospect.queued) return total;
      return total + (String(prospect.position || "").toUpperCase() === "QB" ? Number(data.scouting?.actionLimits?.specific || data.scouting?.weeklyWindow?.specificCount || 4) : 1);
    }, 0);
  }

  function toggleLocalScoutingSelection(prospect) {
    const selections = ensureLocalScoutingSelections();
    const id = Number(prospect?.prospect_id || 0);
    if (!id) return;
    const existing = selections.map(String).indexOf(String(id));
    if (existing >= 0) {
      selections.splice(existing, 1);
      showToast(`${prospect.player_name || "Prospect"} removed from weekly scouting`);
      render();
      return;
    }
    selections.push(id);
    showToast(`${prospect.player_name || "Prospect"} selected for weekly scouting`);
    render();
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
    const showCombine = combineStatusAvailable();
    const headers = [
      ["rank", "Rank"],
      ["player", "Player"],
      ["position", "Pos"],
      ["size", "Ht/Wt"],
      ["age", "Age"],
      ["class", "Class"],
      ["school", "School"],
      ["projection", "Proj"],
      ["role", "Role"],
      ["grade", "Rating"],
      ["potential", "Potential"],
      ["confidence", "Confidence"],
    ];
    if (showCombine) headers.push(["forty", "40"], ["vertical", "Vert"], ["athletic", "Ath"]);
    headers.push(["risk", "Risk"]);
    if (showSeniorBowl) headers.push(["seniorBowl", "SB"]);
    headers.push([null, "Latest"], [null, "Actions"]);
    headers.forEach(([key, label]) => {
      const th = node("th");
      th.append(key ? scoutingSortHeader(key, label) : node("span", null, label));
      headerRow.append(th);
    });
    thead.append(headerRow);
    const tbody = node("tbody");
    prospects.forEach((prospect) => {
      const locallyQueued = localScoutingSelectionIds().map(String).includes(String(prospect.prospect_id));
      const rowClass = [
        String(prospect.prospect_id) === String(selected?.prospect_id) ? "selected-row" : "",
        prospect.queued || locallyQueued ? "queued-scouting-row" : "",
      ].filter(Boolean).join(" ");
      const tr = node("tr", rowClass);
      tr.addEventListener("click", () => {
        openDraftProspectPopover(prospect.prospect_id);
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
        valueOrDash(prospect.scout_grade),
        valueOrDash(prospect.scout_ceiling),
        scoutingConfidenceCell(prospect),
      ];
      if (showCombine) {
        cells.push(
          decimalOrDash(prospect.forty_yard_dash, 2),
          inchesText(prospect.vertical_jump_in),
          whole(prospect.athletic_score),
        );
      }
      cells.push(riskCell(prospect.scout_risk));
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

  function scoutingSortHeader(key, label) {
    const active = state.scoutingBoardSort?.key === key;
    const direction = active ? state.scoutingBoardSort.direction : "asc";
    const button = node("button", `table-sort-button ${active ? "active" : ""}`.trim());
    button.type = "button";
    button.title = `Sort scouting board by ${label}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const current = state.scoutingBoardSort || {};
      state.scoutingBoardSort = {
        key,
        direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
      };
      render();
    });
    append(button, [
      node("span", null, label),
      node("small", "sort-indicator", active ? (direction === "asc" ? "^" : "v") : "-"),
    ]);
    return button;
  }

  function scoutingProspectNameButton(prospect) {
    const wrap = node("span", "prospect-name-cell");
    const button = node("button", "prospect-link", prospect.player_name || `${prospect.first_name || ""} ${prospect.last_name || ""}`.trim() || "Prospect");
    button.type = "button";
    button.title = "Open prospect card";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openDraftProspectPopover(prospect.prospect_id);
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
    const workoutsAvailable = combineStatusAvailable();
    const athletic = workoutsAvailable ? Number(prospect.athletic_score || 0) : 0;
    const risk = String(prospect.scout_risk || "").toLowerCase();
    const status = workoutsAvailable ? String(prospect.combine_status || "") : "";
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
        workoutsAvailable
          ? `${name}'s international path has scouts leaning harder on verified athletic testing and role projection than the board rank.`
          : `${name}'s international path has scouts leaning harder on tape context and role projection than the board rank.`,
        `Cross-checkers like the tools, but ${name}'s translation to an NFL ${role} role needs live exposure.`,
        `${name} is generating quiet curiosity because the trait profile is easier to like than the competition jump.`,
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
        `Latest note on ${name}: injury context matters more than the raw grade right now.`,
        `${name} still has fans in the room, but medical confidence is the swing factor.`,
      ], seed);
    }
    if (status.toLowerCase().includes("not invited")) {
      return choose([
        `${name} missed the main event circuit, so the staff is leaning on tape and any later pro-day signal.`,
        `No combine invite for ${name}; scouts want one more verified athletic data point before trusting the grade.`,
        `${name}'s file is mostly tape-driven right now, with workout confirmation still pending.`,
      ], seed);
    }
    if (athletic >= 82) {
      return choose([
        `${name}'s verified testing is starting to support the ${role} projection.`,
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
    const specificUses = Number(data.scouting?.actionUses?.specific || 0) + localScoutingSpecificCost(data.scouting?.board || []);
    const specificLimit = Number(data.scouting?.actionLimits?.specific || data.scouting?.weeklyWindow?.specificCount || 2);
    const nonSpecificActionUsed = Boolean(data.scouting?.nonSpecificActionUsed);
    const weeklyChoiceUsed = Boolean(data.scouting?.weeklyChoiceUsed);
    const weeklyOpen = Boolean(data.scouting?.weeklyWindow?.open);
    const top30 = data.scouting?.top30 || {};
    const actions = node("div", "prospect-actions compact-prospect-actions");
    const locallyQueued = localScoutingSelectionIds().map(String).includes(String(prospect.prospect_id));
    const queued = Boolean(prospect.queued) || locallyQueued;
    const scoutCost = String(prospect.position || "").toUpperCase() === "QB" ? specificLimit : 1;
    const scoutButton = node("button", `copy-button mini-button ${queued ? "queued-button" : ""}`.trim(), queued ? "Selected" : used ? "Used" : "Scout");
    scoutButton.type = "button";
    scoutButton.disabled = state.runnerBusy || !runnerMode() || !weeklyOpen || nonSpecificActionUsed || (!queued && specificUses + scoutCost > specificLimit) || prospect.scouting_confidence === "Very High";
    scoutButton.title = !weeklyOpen
      ? data.scouting?.weeklyWindow?.reason || "Weekly scouting is locked right now."
      : nonSpecificActionUsed
      ? "This week's scouting choice has already been used on another scouting package."
      : queued
      ? "Selected for weekly scouting. Click to unselect before simming the week."
      : specificUses >= specificLimit || weeklyChoiceUsed
      ? "All specific-player scouting slots have already been used this week."
      : prospect.scouting_confidence === "Very High"
      ? "This player is already at very high confidence."
      : `Scout this player. ${specificUses}/${specificLimit} specific scouts used this week.`;
    scoutButton.addEventListener("click", (event) => {
      event.stopPropagation();
      if (prospect.queued && !locallyQueued) {
        runAction("scouting_unassign", { prospect_id: prospect.prospect_id });
        return;
      }
      toggleLocalScoutingSelection(prospect);
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
        switchView("draft");
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
    if (runnerMode() && state.aiGmLiveKey !== aiGmLiveKey() && !state.aiGmLoading) {
      loadLiveAiGm().then(render);
    }
    const config = ai.config || {};
    const autonomy = ai.autonomy || {};
    const profile = ai.profile || {};
    const evaluation = ai.evaluation || {};
    const cutdownPlan = ai.cutdownPlan || {};
    const practiceSquad = ai.practiceSquad || {};
    const contractPlan = ai.contractPlan || {};
    const draftPlan = ai.draftPlan || {};
    const freeAgentPlan = ai.freeAgentPlan || {};
    const reviewInbox = ai.reviewInbox || [];
    const reviewActivity = ai.reviewActivity || [];
    const reviewStatusCounts = ai.reviewStatusCounts || {};
    const commands = data.commands || {};

    const summary = panel("Local LLM Status", ai.gameId || data.registry?.activeGameId || "Active Save");
    const metrics = node("section", "metric-grid compact-metrics");
    append(metrics, [
      metric("Profiles", String(ai.counts?.profiles || 0), "Team GM profiles"),
      metric("Enabled", config.enabled ? "Yes" : "No", config.provider || "No config", config.enabled ? "good" : "warn"),
      metric("Autonomy", autonomy.mode || "advisory_only", autonomy.auto_apply_low_risk ? "low-risk auto allowed" : "review first", autonomy.auto_apply_low_risk ? "warn" : ""),
      metric("Review", String(ai.counts?.reviewInbox || 0), "Open AI GM items", ai.counts?.reviewInbox ? "warn" : ""),
      metric("Applied", String(reviewStatusCounts.applied || 0), "Committed review items", reviewStatusCounts.applied ? "good" : ""),
      metric("Blocked", String(reviewStatusCounts.blocked || 0), "Needs follow-up", reviewStatusCounts.blocked ? "bad" : ""),
      metric("Model", config.model || "llama3.1:8b", config.endpoint || "Ollama default"),
      metric("Recent Logs", String(ai.counts?.logs || 0), "Advisory decisions"),
      metric("League Office", state.aiGmLoading ? "Refreshing" : "Ready", data.aiGmGeneratedAt ? `Updated ${shortDateTime(data.aiGmGeneratedAt.replace("T", " "))}` : "Latest data"),
    ]);
    panelBody(summary).append(metrics);
    panelBody(summary).append(node("div", "quiet cap-context", "The LLM can produce structured recommendations, but it is still advisory-only. It logs validated actions and does not directly mutate rosters, contracts, cap, or draft tables."));
    root.append(summary);

    const workflowGrid = node("div", "ai-workflow-grid");
    const workflowPanel = (title, kicker, items, extra) => {
      const workPanel = panel(title, kicker);
      const body = panelBody(workPanel);
      items.forEach((item) => {
        body.append(actionCard(
          item.title,
          item.detail,
          item.command,
          item.action,
          item.params,
          item.tone || "",
          { runLabel: item.runLabel || "Run" }
        ));
      });
      if (extra) body.append(extra);
      return workPanel;
    };

    const setupPanel = workflowPanel("Setup & Autonomy", autonomy.mode || "Review First", [
      { title: "Prepare Tables", detail: "Create or update the AI GM database tables for this save.", command: commands.aiGmSetup, action: "ai_gm_setup", tone: "good" },
      { title: "Enable Ollama", detail: "Turn on local model support for advisory decisions.", command: commands.aiGmEnableOllama, action: "ai_gm_enable_ollama" },
      { title: "Advisory Mode", detail: "Keep AI GM decisions in review-only mode.", command: commands.aiGmAutonomyAdvisory, action: "ai_gm_autonomy_config", params: { mode: "advisory_only" } },
      { title: "Allow Low-Risk Auto", detail: "Permit low-risk validated actions to apply automatically.", command: commands.aiGmAutonomyLowRisk, action: "ai_gm_autonomy_config", params: { mode: "auto_apply_low_risk" }, tone: "warn" },
    ]);

    const dailyOpsPanel = workflowPanel("Daily Ops", `${ai.counts?.ops || 0} Recommended`, [
      { title: "Scan Team Ops", detail: "Find recommended AI GM work for the selected team.", command: commands.aiGmOps, action: "ai_gm_ops", params: { team, limit: 20 }, tone: "good" },
      { title: "Run Team Daily Check", detail: "Create a persisted daily AI GM scan for this team.", command: commands.aiGmDailyRunPersist, action: "ai_gm_daily_run", params: { team, phase: "auto", persist: true } },
      { title: "Run CPU League Check", detail: "Scan CPU teams and persist queueable work.", command: commands.aiGmDailyRunAllPersist, action: "ai_gm_daily_run", params: { all: true, phase: "auto", persist: true, limit: 20 } },
      { title: "Apply Low-Risk CPU Work", detail: "Run league scan and apply only validated low-risk actions.", command: commands.aiGmDailyRunApply, action: "ai_gm_daily_run", params: { all: true, phase: "auto", mode: "auto_apply_low_risk", apply: true, limit: 20 }, tone: "warn" },
      { title: "Show Queue", detail: "Review pending AI GM work for this team.", command: commands.aiGmQueue, action: "ai_gm_queue", params: { team, limit: 12 } },
      { title: "Process Queue", detail: "Process the next few queued team operations.", command: commands.aiGmProcessQueue, action: "ai_gm_process_queue", params: { team, limit: 3 } },
    ]);

    const rosterPanel = workflowPanel("Roster", team, [
      { title: "Evaluate Team", detail: "Refresh needs, surplus, cut watch, and extension watch.", command: commands.aiGmEvaluate, action: "ai_gm_evaluate", params: { team }, tone: "good" },
      { title: "Build Cutdown Plan", detail: "Build active roster, practice squad, and release recommendations.", command: commands.aiGmCutdownPlan, action: "ai_gm_cutdown_plan", params: { team } },
      { title: "Save Cutdown Plan", detail: "Persist the current cutdown recommendation for review.", command: commands.aiGmCutdownPlanPersist, action: "ai_gm_cutdown_plan_persist", params: { team } },
      { title: "Apply Reviewed Plan", detail: "Apply the saved cutdown plan after review.", command: commands.aiGmApplyCutdownPlan, action: null, tone: "warn" },
      { title: "Practice Squad Check", detail: "Check current practice squad eligibility and limits.", command: commands.practiceSquadEligibility, action: null },
    ]);

    const contractsPanel = workflowPanel("Contracts", `${contractPlan.counts?.extension_targets || 0} Extensions`, [
      { title: "Build Contract Plan", detail: "Identify extension, tag, trade-before-walk, and walk decisions.", command: commands.aiGmContractPlan, action: "ai_gm_contract_plan", params: { team }, tone: "good" },
      { title: "Save Contract Plan", detail: "Persist extension recommendations for review.", command: commands.aiGmContractPlanPersist, action: "ai_gm_contract_plan_persist", params: { team } },
      { title: "Dry-Run Extensions", detail: "Preview extension actions without committing them.", command: commands.aiGmDryRunContractApply, action: null },
      { title: "Apply Extensions", detail: "Commit reviewed contract extensions.", command: commands.aiGmApplyContractPlan, action: null, tone: "warn" },
      { title: "CPU Extension Pass", detail: "Apply validated CPU pre-free-agency extensions.", command: commands.aiGmOffseasonPreFaApply, action: null, tone: "warn" },
    ]);

    const freeAgencyPanel = workflowPanel("Free Agency", `${freeAgentPlan.counts?.primary_targets || 0} Primary`, [
      { title: "Build FA Plan", detail: "Rank primary, value, bridge, and monitor targets.", command: commands.aiGmFreeAgentPlan, action: "ai_gm_free_agent_plan", params: { team }, tone: "good" },
      { title: "Save FA Plan", detail: "Persist offer recommendations for review.", command: commands.aiGmFreeAgentPlanPersist, action: "ai_gm_free_agent_plan_persist", params: { team } },
      { title: "Dry-Run Offers", detail: "Preview offer submissions without committing them.", command: commands.aiGmDryRunFreeAgentApply, action: null },
      { title: "Submit Offers", detail: "Submit reviewed free-agent offers.", command: commands.aiGmApplyFreeAgentPlan, action: null, tone: "warn" },
      { title: "CPU FA Wave 1", detail: "Apply validated CPU opening-wave free-agent work.", command: commands.aiGmOffseasonFaWave1Apply, action: null, tone: "warn" },
    ]);

    const draftWorkflowPanel = workflowPanel("Draft", `${draftPlan.counts?.picks || 0} Picks`, [
      { title: "Build Draft Plan", detail: "Build board fits and position priorities for the selected team.", command: commands.aiGmDraftPlan, action: "ai_gm_draft_plan", params: { team }, tone: "good" },
      { title: "Save Draft Plan", detail: "Persist this team's draft strategy.", command: commands.aiGmDraftPlanPersist, action: "ai_gm_draft_plan_persist", params: { team } },
      { title: "Save CPU Draft Plans", detail: "Create draft plans for CPU teams before the draft.", command: commands.aiGmDraftPlanAll, action: null },
      { title: "Ask Draft Strategy", detail: "Ask the current GM to rank draft priorities from needs, contracts, and pick value.", command: commands.aiGmRunDraft, action: "ai_gm_run", params: { team, decision_type: "draft_strategy_update" } },
      { title: "Build Context Packet", detail: "Generate the data packet used for a draft strategy decision.", command: commands.aiGmContext, action: "ai_gm_context", params: { team, decision_type: "draft_strategy_update" } },
    ]);

    const reviewWorkflowPanel = workflowPanel("Review Queue", `${ai.counts?.reviewInbox || 0} Open`, [
      { title: "Review Team Inbox", detail: "Load pending, blocked, and approved AI GM items for this team.", command: commands.aiGmReviewInbox, action: "ai_gm_review_inbox", params: { team, limit: 20 }, tone: ai.counts?.reviewInbox ? "warn" : "" },
      { title: "Review League Inbox", detail: "Load pending review items across the league.", command: commands.aiGmReviewInboxAll, action: "ai_gm_review_inbox", params: { status: "pending_review", limit: 40 } },
      { title: "Review History", detail: "Show recent AI GM review outcomes for this team.", command: commands.aiGmReviewHistory, action: "ai_gm_review_history", params: { team, status: "all", limit: 20 } },
      { title: "Apply Approved Team", detail: "Apply already approved review items for this team.", command: commands.aiGmReviewApplyAllApprovedCommit, action: "ai_gm_review_apply", params: { all_approved: true, team, apply: true, limit: 20 }, tone: "warn" },
      { title: "Seed Dev Review", detail: "Create one safe non-mutating review item to test this workflow.", command: commands.aiGmDevSeedReview, action: "ai_gm_dev_seed_review", params: { team, clear_existing: true } },
      { title: "Clear Dev Reviews", detail: "Delete development-only review seed items for this team.", command: commands.aiGmDevClearReviews, action: "ai_gm_dev_clear_reviews", params: { team } },
    ]);

    const askPanel = workflowPanel("Ask A GM", profile.gm_name || team, [
      { title: "Depth Chart Review", detail: "Ask for promotions and demotions based on role fit, youth, and current ability.", command: commands.aiGmRunDepth, action: "ai_gm_run", params: { team, decision_type: "depth_chart_review" } },
      { title: "Free-Agent Shortlist", detail: "Ask for sensible FA targets using need fit and cap discipline.", command: commands.aiGmRunFreeAgency, action: "ai_gm_run", params: { team, decision_type: "free_agent_shortlist" } },
      { title: "Recent AI Logs", detail: "Show recent advisory output for this team.", command: commands.aiGmLogs, action: "ai_gm_logs", params: { team, limit: 12 } },
      { title: "Team Profile", detail: "Inspect the current GM profile and operating model.", command: commands.aiGmProfiles, action: "ai_gm_profiles", params: { team } },
    ]);

    append(workflowGrid, [
      setupPanel,
      dailyOpsPanel,
      rosterPanel,
      contractsPanel,
      freeAgencyPanel,
      draftWorkflowPanel,
      reviewWorkflowPanel,
      askPanel,
    ]);
    root.append(workflowGrid);

    const runs = panel("Recent Daily Runs", "Last 5");
    const runsBody = panelBody(runs);
    if ((ai.dailyRuns || []).length) {
      const dailyList = node("div", "list compact-list");
      (ai.dailyRuns || []).slice(0, 5).forEach((run) => {
        dailyList.append(row(
          `${run.phase || "auto"} ${run.scope || run.scope_team || (run.all_teams ? "ALL" : "")}`.trim(),
          `${run.mode || "advisory_only"} | planned ${run.planned_operations ?? 0}, applied ${run.applied_operations ?? 0}, queued ${run.queued_operations ?? 0}, blocked ${run.blocked_operations ?? 0}`,
          shortDateTime(run.created_at)
        ));
      });
      runsBody.append(dailyList);
    } else {
      runsBody.append(node("div", "empty-state", "No AI GM daily runs have been logged yet."));
    }
    root.append(runs);

    const reviewPanel = panel("AI GM Review Inbox", `${ai.counts?.reviewInbox || 0} Open`);
    if (reviewInbox.length) {
      panelBody(reviewPanel).append(table(
        ["ID", "Team", "Risk", "Status", "Type", "Summary", "Actions"],
        reviewInbox.map((item) => [
          item.review_id,
          item.team || "-",
          item.risk_tier || "-",
          item.lifecycle_status || "-",
          reviewItemType(item),
          reviewItemSummary(item),
          reviewItemActions(item),
        ])
      ));
    } else {
      panelBody(reviewPanel).append(node("div", "empty-state", "No AI GM items need review."));
    }
    root.append(reviewPanel);
    root.append(renderReviewDetailPanel(selectedAiGmReview(ai)));

    const activityPanel = panel("AI GM Review Activity", `${ai.counts?.reviewActivity || 0} Recent`);
    if (reviewActivity.length) {
      const statusMetrics = node("section", "metric-grid compact-metrics");
      append(statusMetrics, [
        metric("Pending", String(reviewStatusCounts.pending_review || 0), "Awaiting decision", reviewStatusCounts.pending_review ? "warn" : ""),
        metric("Approved", String(reviewStatusCounts.approved || 0), "Ready for dry-run/apply", reviewStatusCounts.approved ? "good" : ""),
        metric("Applied", String(reviewStatusCounts.applied || 0), "Committed", reviewStatusCounts.applied ? "good" : ""),
        metric("Rejected", String(reviewStatusCounts.rejected || 0), "Declined", reviewStatusCounts.rejected ? "bad" : ""),
        metric("Blocked", String(reviewStatusCounts.blocked || 0), "Needs attention", reviewStatusCounts.blocked ? "bad" : ""),
      ]);
      panelBody(activityPanel).append(statusMetrics);
      panelBody(activityPanel).append(table(
        ["Updated", "ID", "Team", "Status", "Type", "Outcome"],
        reviewActivity.map((item) => [
          shortDateTime(item.activity_time || item.updated_at || item.created_at),
          item.review_id,
          item.team || "-",
          tag(item.lifecycle_status || "-", reviewStatusTone(item.lifecycle_status)),
          reviewItemType(item),
          reviewActivityOutcome(item),
        ])
      ));
    } else {
      panelBody(activityPanel).append(node("div", "empty-state", "No AI GM review activity has been logged yet."));
    }
    root.append(activityPanel);

    const evalPanel = panel("Team Evaluation", evaluation.team_direction?.team_phase || team);
    if (evaluation.summary) {
      const metricsData = evaluation.metrics || {};
      const direction = evaluation.team_direction || {};
      panelBody(evalPanel).append(detailGrid([
        ["Phase", direction.team_phase || "-"],
        ["Posture", direction.recommended_posture || "-"],
        ["Competitiveness", metricsData.competitiveness_score ?? "-"],
        ["Cap", `${metricsData.cap_band || "-"} ${metricsData.cap_space_display || ""}`.trim()],
        ["Roster Quality", metricsData.roster_quality_score ?? "-"],
        ["Avg Age", metricsData.avg_roster_age ?? "-"],
      ]));
      const evalGrid = node("div", "scout-note-grid");
      const buildEvalList = (title, items, formatter) => {
        const list = node("div", "list compact-list");
        (items || []).slice(0, 5).forEach((item) => {
          const formatted = formatter(item);
          list.append(row(formatted.title, formatted.detail, formatted.meta, formatted.tone || ""));
        });
        return sectionBlock(title, list.children.length ? list : node("div", "empty-state", "None"));
      };
      evalGrid.append(buildEvalList("Needs", evaluation.roster_needs, (item) => ({
        title: `${item.position_group || "-"} ${item.priority || ""}`.trim(),
        detail: listText(item.drivers, 6),
        meta: item.need_score ?? "",
        tone: item.priority === "urgent" || item.priority === "high" ? "warn" : "",
      })));
      evalGrid.append(buildEvalList("Surplus", evaluation.roster_surplus, (item) => ({
        title: item.position_group || "-",
        detail: listText(item.drivers, 6),
        meta: item.surplus_score ?? "",
      })));
      evalGrid.append(buildEvalList("Cut Watch", evaluation.cut_candidates, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: listText(item.reasons, 6),
        meta: item.score ?? "",
        tone: "warn",
      })));
      evalGrid.append(buildEvalList("Extension Watch", evaluation.extension_candidates, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: listText(item.reasons, 6),
        meta: item.score ?? "",
        tone: "good",
      })));
      panelBody(evalPanel).append(evalGrid);
    } else {
      panelBody(evalPanel).append(node("div", "empty-state", ai.evaluationError || "Run Evaluate Team to inspect the deterministic AI GM baseline."));
    }
    root.append(evalPanel);

    const cutdownPanel = panel("Cutdown Plan", cutdownPlan.validation?.status || "Advisory");
    if (cutdownPlan.summary) {
      const buckets = cutdownPlan.plan || {};
      const validation = cutdownPlan.validation || {};
      const limit = cutdownPlan.limits || {};
      panelBody(cutdownPanel).append(detailGrid([
        ["Active", `${(buckets.active_roster || []).length}/${limit.active_roster_limit || 53}`],
        ["Practice Squad", `${validation.counts?.practice_squad ?? (buckets.practice_squad_priorities || []).length}/${limit.practice_squad_limit || 16}`],
        ["Release/Waive", String((buckets.release_or_waive || []).length)],
        ["Validation", validation.status || "-"],
        ["Diff vs Fallback", `${(cutdownPlan.comparison_to_deterministic_fallback?.ai_active_over_fallback || []).length} active changes`],
      ]));
      if ((validation.errors || []).length || (validation.warnings || []).length) {
        const messages = [...(validation.errors || []), ...(validation.warnings || [])].slice(0, 4);
        panelBody(cutdownPanel).append(node("div", "quiet cap-context", messages.join(" ")));
      }
      if (practiceSquad.rules) {
        const usage = practiceSquad.usage || {};
        panelBody(cutdownPanel).append(detailGrid([
          ["PS Normal", `${usage.base_count ?? 0}/${practiceSquad.rules.base_limit ?? 16}`],
          ["Development", `${usage.developmental_count ?? 0}/${practiceSquad.rules.developmental_limit ?? 10}`],
          ["Veterans", `${usage.veteran_exception_count ?? 0}/${practiceSquad.rules.veteran_exception_limit ?? 6}`],
          ["IPP", `${usage.international_exemption_count ?? 0}/${practiceSquad.rules.international_exemption_limit ?? 1}`],
          ["Elevations", `${practiceSquad.rules.elevation_limit ?? 3}/player, ${practiceSquad.rules.weekly_elevation_limit ?? 2}/week`],
        ]));
      } else if (ai.practiceSquadError) {
        panelBody(cutdownPanel).append(node("div", "quiet cap-context", ai.practiceSquadError));
      }
      const cutdownGrid = node("div", "scout-note-grid");
      const buildPlanList = (title, items, formatter) => {
        const list = node("div", "list compact-list");
        (items || []).slice(0, 6).forEach((item) => {
          const formatted = formatter(item);
          list.append(row(formatted.title, formatted.detail, formatted.meta, formatted.tone || ""));
        });
        return sectionBlock(title, list.children.length ? list : node("div", "empty-state", "None"));
      };
      cutdownGrid.append(buildPlanList("PS Priorities", buckets.practice_squad_priorities, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: item.practice_squad_eligibility_reason || listText(item.reasons, 3),
        meta: item.practice_squad_bucket || item.waiver_claim_risk || item.adjusted_ps_score || "",
        tone: item.waiver_claim_risk === "high" ? "warn" : "",
      })));
      cutdownGrid.append(buildPlanList("Release/Waive", buckets.release_or_waive, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: listText(item.reasons, 3),
        meta: item.waiver_claim_risk || "",
        tone: item.waiver_claim_risk === "high" || item.waiver_claim_risk === "medium" ? "warn" : "",
      })));
      cutdownGrid.append(buildPlanList("Active FA Fixes", buckets.free_agent_active_options, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: listText(item.reasons, 6),
        meta: item.recommended_action || "",
        tone: "warn",
      })));
      cutdownGrid.append(buildPlanList("FA PS Options", buckets.free_agent_practice_squad_options, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: item.practice_squad_eligibility_reason || listText(item.reasons, 6),
        meta: item.practice_squad_bucket || item.ps_score || "",
      })));
      cutdownGrid.append(buildPlanList("PS Eligible Now", practiceSquad.candidates, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: listText(item.eligible ? item.reasons : item.blockers, 2, " "),
        meta: item.bucket || "",
        tone: item.eligible ? "good" : "warn",
      })));
      cutdownGrid.append(buildPlanList("AI Keeps Over Fallback", cutdownPlan.comparison_to_deterministic_fallback?.ai_active_over_fallback, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: `${item.position_group || ""} OVR ${item.overall || "-"}`,
        meta: "AI",
      })));
      cutdownGrid.append(buildPlanList("Fallback Keeps Over AI", cutdownPlan.comparison_to_deterministic_fallback?.fallback_active_over_ai, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: `${item.position_group || ""} OVR ${item.overall || "-"}`,
        meta: "Fallback",
      })));
      panelBody(cutdownPanel).append(cutdownGrid);
    } else {
      panelBody(cutdownPanel).append(node("div", "empty-state", ai.cutdownPlanError || "Build a cutdown plan to inspect active roster, practice squad, and release recommendations."));
    }
    root.append(cutdownPanel);

    const contractPanel = panel("Contract Plan", contractPlan.counts ? `${contractPlan.counts.extension_targets || 0} Extend` : "Advisory");
    if (contractPlan.summary) {
      const buckets = contractPlan.plan || {};
      const counts = contractPlan.counts || {};
      const totalExpiring = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
      panelBody(contractPanel).append(detailGrid([
        ["Expiring", String(totalExpiring)],
        ["Extend", String(counts.extension_targets || 0)],
        ["Tag", String(counts.tag_candidates || 0)],
        ["Trade/Walk", `${counts.trade_before_walk || 0}/${counts.let_walk || 0}`],
        ["Budget", contractPlan.budget?.recommended_extension_aav_display || "-"],
      ]));
      const contractGrid = node("div", "scout-note-grid");
      const buildContractList = (title, items) => {
        const list = node("div", "list compact-list");
        (items || []).slice(0, 6).forEach((item) => {
          list.append(row(
            `${item.player_name || "-"} ${item.position || ""}`.trim(),
            listText(item.reasons, 3),
            item.estimated_aav ? money(item.estimated_aav) : money(item.asking_aav),
            item.recommended_action === "let_walk" ? "warn" : ""
          ));
        });
        return sectionBlock(title, list.children.length ? list : node("div", "empty-state", "None"));
      };
      contractGrid.append(buildContractList("Extend", buckets.extension_targets));
      contractGrid.append(buildContractList("Tag", buckets.tag_candidates));
      contractGrid.append(buildContractList("Trade Before Walk", buckets.trade_before_walk));
      contractGrid.append(buildContractList("Let Walk", buckets.let_walk));
      panelBody(contractPanel).append(contractGrid);
    } else {
      panelBody(contractPanel).append(node("div", "empty-state", ai.contractPlanError || "Build a contract plan to review expiring-player decisions."));
    }
    root.append(contractPanel);

    const draftPanel = panel("Draft Plan", draftPlan.counts ? `${draftPlan.counts.picks || 0} Picks` : "Advisory");
    if (draftPlan.summary) {
      const portfolio = draftPlan.pick_portfolio || {};
      panelBody(draftPanel).append(detailGrid([
        ["Draft Year", String(draftPlan.draft_year || "-")],
        ["Picks", String(portfolio.pick_count || 0)],
        ["Premium Picks", String(portfolio.premium_picks_rounds_1_to_3 || 0)],
        ["Day Three", String(portfolio.day_three_picks_rounds_4_to_7 || 0)],
        ["Earliest", portfolio.earliest_pick ? `#${portfolio.earliest_pick}` : "-"],
      ]));
      const draftGrid = node("div", "scout-note-grid");
      const priorityList = node("div", "list compact-list");
      (draftPlan.position_priorities || []).slice(0, 6).forEach((item) => {
        priorityList.append(row(
          `${item.position_group || "-"} ${item.priority || ""}`.trim(),
          listText(item.drivers, 3),
          item.draft_priority_score ?? "",
          item.priority === "urgent" || item.priority === "contract_cliff" ? "warn" : ""
        ));
      });
      draftGrid.append(sectionBlock("Position Priorities", priorityList.children.length ? priorityList : node("div", "empty-state", "None")));
      const boardList = node("div", "list compact-list");
      (draftPlan.board || []).slice(0, 8).forEach((item) => {
        boardList.append(row(
          `${item.player_name || "-"} ${item.position || ""}`.trim(),
          listText(item.reasons, 3),
          `#${item.board_rank || "-"} ${item.score || ""}`,
          item.risk === "High" ? "warn" : ""
        ));
      });
      draftGrid.append(sectionBlock("Board Fits", boardList.children.length ? boardList : node("div", "empty-state", "None")));
      panelBody(draftPanel).append(draftGrid);
    } else {
      panelBody(draftPanel).append(node("div", "empty-state", ai.draftPlanError || "Build a draft plan to review board fits, position priorities, and pick-round targets."));
    }
    root.append(draftPanel);

    const faPanel = panel("Free-Agent Plan", freeAgentPlan.counts ? `${freeAgentPlan.counts.primary_targets || 0} Primary` : "Advisory");
    if (freeAgentPlan.summary) {
      const buckets = freeAgentPlan.plan || {};
      const counts = freeAgentPlan.counts || {};
      panelBody(faPanel).append(detailGrid([
        ["Market", freeAgentPlan.market_source || "-"],
        ["Primary", String(counts.primary_targets || 0)],
        ["Value", String(counts.value_targets || 0)],
        ["Bridge", String(counts.bridge_or_depth || 0)],
        ["Budget", freeAgentPlan.budget?.recommended_offer_aav_display || "-"],
      ]));
      const faGrid = node("div", "scout-note-grid");
      const buildFaList = (title, items) => {
        const list = node("div", "list compact-list");
        (items || []).slice(0, 6).forEach((item) => {
          const offer = item.offer || {};
          list.append(row(
            `${item.player_name || "-"} ${item.position || ""}`.trim(),
            listText(item.reasons, 3),
            offer.initial_aav ? money(offer.initial_aav) : money(item.asking_aav),
            item.recommended_action === "monitor" ? "warn" : ""
          ));
        });
        return sectionBlock(title, list.children.length ? list : node("div", "empty-state", "None"));
      };
      faGrid.append(buildFaList("Primary", buckets.primary_targets));
      faGrid.append(buildFaList("Value", buckets.value_targets));
      faGrid.append(buildFaList("Bridge/Depth", buckets.bridge_or_depth));
      faGrid.append(buildFaList("Monitor", buckets.monitor));
      panelBody(faPanel).append(faGrid);
    } else {
      panelBody(faPanel).append(node("div", "empty-state", ai.freeAgentPlanError || "Build a free-agent plan to review target fits, offer shapes, and monitor prices."));
    }
    root.append(faPanel);

    const savedPlansPanel = panel("Saved Cutdown Plans", `${ai.counts?.cutdownPlans || 0} Recent`);
    const savedPlans = ai.cutdownPlans || [];
    if (savedPlans.length) {
      panelBody(savedPlansPanel).append(table(
        ["ID", "Team", "Date", "Active", "PS", "Valid", "Apply"],
        savedPlans.map((plan) => [
          plan.plan_id,
          plan.team,
          plan.plan_date,
          plan.active_count,
          plan.practice_squad_count,
          plan.validation_status,
          plan.apply_status || "pending",
        ])
      ));
    } else {
      panelBody(savedPlansPanel).append(node("div", "empty-state", "No saved cutdown plans yet."));
    }
    root.append(savedPlansPanel);

    const savedContractPlansPanel = panel("Saved Contract Plans", `${ai.counts?.contractPlans || 0} Recent`);
    const savedContractPlans = ai.contractPlans || [];
    if (savedContractPlans.length) {
      panelBody(savedContractPlansPanel).append(table(
        ["ID", "Team", "Date", "Ext", "Tag", "Trade", "Walk", "AAV", "Apply"],
        savedContractPlans.map((plan) => [
          plan.plan_id,
          plan.team,
          plan.plan_date,
          plan.extension_count,
          plan.tag_count,
          plan.trade_count,
          plan.walk_count,
          money(plan.recommended_extension_aav),
          plan.apply_status || "pending",
        ])
      ));
    } else {
      panelBody(savedContractPlansPanel).append(node("div", "empty-state", "No saved contract plans yet."));
    }
    root.append(savedContractPlansPanel);

    const savedDraftPlansPanel = panel("Saved Draft Plans", `${ai.counts?.draftPlans || 0} Recent`);
    const savedDraftPlans = ai.draftPlans || [];
    if (savedDraftPlans.length) {
      panelBody(savedDraftPlansPanel).append(table(
        ["ID", "Team", "Year", "Picks", "Pri", "Top", "Pos", "Score"],
        savedDraftPlans.map((plan) => [
          plan.plan_id,
          plan.team,
          plan.draft_year,
          plan.pick_count,
          plan.priority_count,
          plan.top_prospect_name || "-",
          plan.top_position || "-",
          plan.top_score ?? "-",
        ])
      ));
    } else {
      panelBody(savedDraftPlansPanel).append(node("div", "empty-state", "No saved draft plans yet."));
    }
    root.append(savedDraftPlansPanel);

    const savedFaPlansPanel = panel("Saved FA Plans", `${ai.counts?.freeAgentPlans || 0} Recent`);
    const savedFaPlans = ai.freeAgentPlans || [];
    if (savedFaPlans.length) {
      panelBody(savedFaPlansPanel).append(table(
        ["ID", "Team", "Date", "Pri", "Val", "Bridge", "Monitor", "AAV", "Apply"],
        savedFaPlans.map((plan) => [
          plan.plan_id,
          plan.team,
          plan.plan_date,
          plan.primary_count,
          plan.value_count,
          plan.bridge_count,
          plan.monitor_count,
          money(plan.recommended_offer_aav),
          plan.apply_status || "pending",
        ])
      ));
    } else {
      panelBody(savedFaPlansPanel).append(node("div", "empty-state", "No saved free-agent plans yet."));
    }
    root.append(savedFaPlansPanel);

    const opsPanel = panel("AI GM Operations", ai.ops?.resolved_phase || "Auto");
    const ops = ai.ops?.operations || [];
    if (ops.length) {
      panelBody(opsPanel).append(table(
        ["Pri", "Team", "Operation", "Decision", "Driver"],
        ops.map((op) => [
          op.priority,
          op.team,
          op.operation_type,
          op.decision_type,
          listText(op.drivers, 2),
        ])
      ));
    } else {
      panelBody(opsPanel).append(node("div", "empty-state", ai.opsError || "No AI GM operations recommended for the current team."));
    }
    root.append(opsPanel);

    const queuePanel = panel("AI GM Queue", `${ai.counts?.queue || 0} Pending`);
    const queueRows = ai.queue || [];
    if (queueRows.length) {
      panelBody(queuePanel).append(table(
        ["ID", "Pri", "Team", "Status", "Decision", "Operation"],
        queueRows.map((item) => [
          item.decision_id,
          item.priority,
          item.team,
          item.status,
          item.decision_type,
          item.operation_type || "-",
        ])
      ));
    } else {
      panelBody(queuePanel).append(node("div", "empty-state", "No queued AI GM tasks."));
    }
    root.append(queuePanel);

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
    if (runnerMode() && String(state.statsLiveSeason || "") !== String(season || "") && !state.statsLoading) {
      loadLiveLeaders().then(render);
    }

    const summary = panel("Season Leaders", `${season}${data.statsGeneratedAt ? ` | refreshed ${shortDateTime(data.statsGeneratedAt.replace("T", " "))}` : ""}`);
    if (state.statsLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live league leaders..."));
    }
    panelBody(summary).append(table(["Category", "Leader", "Team", "Total"], [
      ["Passing", statPlayerLink(stats.passing?.[0]), stats.passing?.[0]?.team || "-", stats.passing?.[0] ? `${whole(stats.passing[0].pass_yards)} yards` : "-"],
      ["Rushing", statPlayerLink(stats.rushing?.[0]), stats.rushing?.[0]?.team || "-", stats.rushing?.[0] ? `${whole(stats.rushing[0].rush_yards)} yards` : "-"],
      ["Receiving", statPlayerLink(stats.receiving?.[0]), stats.receiving?.[0]?.team || "-", stats.receiving?.[0] ? `${whole(stats.receiving[0].receiving_yards)} yards` : "-"],
      ["Sacks", statPlayerLink(stats.sacks?.[0]), stats.sacks?.[0]?.team || "-", stats.sacks?.[0] ? `${whole(stats.sacks[0].sacks)} sacks` : "-"],
      ["Tackles", statPlayerLink(stats.tackles?.[0]), stats.tackles?.[0]?.team || "-", stats.tackles?.[0] ? `${whole(stats.tackles[0].tackles)} tackles` : "-"],
      ["Snaps", statPlayerLink(stats.snaps?.[0]), stats.snaps?.[0]?.team || "-", stats.snaps?.[0] ? `${whole(stats.snaps[0].total_snaps)} snaps` : "-"],
    ]));
    root.append(summary);

    const passing = panel("Passing", "Yards");
    panelBody(passing).append(table(["#", "Player", "Team", "Comp", "Att", "Pct", "Yds", "TD", "INT", "Sacks"], (stats.passing || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
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
      statPlayerLink(p),
      p.team,
      whole(p.rush_attempts),
      whole(p.rush_yards),
      oneDecimal(Number(p.rush_yards || 0) / Math.max(1, Number(p.rush_attempts || 0))),
      whole(p.rush_tds),
    ])));

    const receiving = panel("Receiving", "Yards");
    panelBody(receiving).append(table(["#", "Player", "Team", "Rec", "Tgt", "Yds", "Avg", "TD"], (stats.receiving || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
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
      statPlayerLink(p),
      p.team,
      whole(p.sacks),
      whole(p.tackles),
      whole(p.forced_fumbles),
    ])));
    const interceptions = panel("Coverage", "Interceptions");
    panelBody(interceptions).append(table(["#", "Player", "Team", "INT", "PD", "Solo", "Ast", "Tkl"], (stats.interceptions || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      p.team,
      whole(p.interceptions),
      whole(p.pass_deflections),
      p.solo_tackles === undefined ? "-" : whole(p.solo_tackles),
      p.assisted_tackles === undefined ? "-" : whole(p.assisted_tackles),
      whole(p.tackles),
    ])));
    append(defenseGrid, [sacks, interceptions]);
    root.append(defenseGrid);

    const kicking = panel("Kicking", "Field Goals");
    panelBody(kicking).append(table(["#", "Player", "Team", "FG", "FGA", "XP", "XPA", "Long"], (stats.kicking || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
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
      statPlayerLink(p),
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
    const calendarKey = `${data.currentSeason || data.season?.season || ""}:${data.currentDate || calendar.focusDate || ""}`;
    if (runnerMode() && state.calendarLiveKey !== calendarKey && !state.calendarLoading) {
      loadLiveCalendar().then(render);
    }
    const nextEvent = calendar.nextEvent || (data.events || [])[0];
    const nextWeek = data.season?.nextWeek;
    root.append(calendarControlPanel(calendar, nextEvent, nextWeek));
    if (state.runnerBusy && cancellableRunnerAction(state.busyAction)) {
      root.append(calendarSimProgressStrip(calendar));
    }

    const scopeLabel = calendar.scope === "user_team" ? `${userTeam} Calendar` : "League Calendar";
    const monthPanel = panel(calendar.monthLabel || "Calendar", `${scopeLabel} | ${shortDate(calendar.rangeStart)} - ${shortDate(calendar.rangeEnd)}${data.calendarGeneratedAt ? ` | refreshed ${shortDateTime(data.calendarGeneratedAt.replace("T", " "))}` : ""}`);
    const monthBody = panelBody(monthPanel);
    if (state.calendarLoading) {
      monthBody.append(node("div", "empty-state", "Refreshing live calendar..."));
    }
    const weekdayRow = node("div", "calendar-weekdays");
    ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach((label) => weekdayRow.append(node("span", null, label)));
    const monthGrid = node("div", "calendar-grid");
    (calendar.days || []).forEach((day) => monthGrid.append(calendarDayCell(day)));
    append(monthBody, [weekdayRow, monthGrid]);
    root.append(monthPanel);
    finishRender(root);
  }

  function calendarDayCell(day) {
    const classes = [
      "calendar-day",
      day.isCurrentMonth ? "" : "outside-month",
      day.isToday ? "today" : "",
      state.calendarLiveFocus && day.date === calendarLiveFocusDate() ? "sim-focus" : "",
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

  function calendarLiveFocusDate() {
    return data.calendar?.focusDate || data.currentDate || "";
  }

  function calendarSimProgressStrip(calendar) {
    const games = calendar?.gamesInView || [];
    const played = games.filter((game) => Number(game.played || 0) === 1).length;
    const total = games.length;
    const strip = node("div", "calendar-sim-progress");
    append(strip, [
      append(node("div"), [
        node("strong", null, "Live sim calendar"),
        node("span", null, `Focused on ${shortDate(calendarLiveFocusDate())}. Finished games will appear here as they commit.`),
      ]),
      node("span", "tag", total ? `${played}/${total} in view final` : "Finding games"),
    ]);
    return strip;
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
    button.title = item.prospect_id ? "Open prospect profile" : (item.body || item.source || "");
    button.addEventListener("click", () => {
      if (item.prospect_id) {
        openProspect(item.prospect_id);
        return;
      }
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
        loadCalendarBoxScore(game.game_id);
        return;
      }
      state.selectedCalendarItem = { type: "game", id: game.game_id };
      render();
    });
    return button;
  }

  function calendarGameRow(game) {
    const item = gameLine(game, data.activeSave?.user_team);
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

  function selectedGameBoxScore(gameId) {
    const result = state.calendarBoxScores?.[String(gameId)] || state.lastResult;
    if (!result || result.action !== "box_score") return null;
    const resultGameId = result.params?.game_id || result.params?.schedule_game_id;
    if (String(resultGameId) !== String(gameId)) return null;
    if (result.returncode !== 0 || result.error) return node("div", "friendly-error", result.summary?.message || result.stderr || result.error || "Box score could not be loaded.");
    const text = String(result.stdout || "").trim();
    return text ? node("pre", "box-score-output calendar-box-score-output", text) : node("div", "empty-state", "No stored box score text was returned for this game.");
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
        switchView("leagueNews");
      });
      const openProspectButton = item.prospect_id ? node("button", "copy-button", "Open Prospect") : null;
      if (openProspectButton) {
        openProspectButton.type = "button";
        openProspectButton.addEventListener("click", () => openProspect(item.prospect_id));
      }
      const subject = newsSubjectNode(item);
      return append(node("div", "calendar-detail"), [
        node("span", "tag", item.category || "League News"),
        newsTitleNode(item),
        node("p", "muted", `${shortDate(item.news_date)} | ${item.source || "League Wire"}`),
        subject ? append(node("div", "calendar-news-subject"), [node("span", null, "Subject"), subject]) : null,
        node("p", null, item.body || ""),
        openProspectButton,
        open,
      ]);
    }
    const played = Number(item.played || 0) === 1;
    const loadingBoxScore = String(state.calendarBoxScoreLoadingId || "") === String(item.game_id);
    const showBox = node("button", "copy-button", loadingBoxScore ? "Loading Box Score" : played ? "Show Box Score" : "Box Score After Sim");
    showBox.type = "button";
    showBox.disabled = !played || !runnerMode() || loadingBoxScore;
    showBox.addEventListener("click", () => {
      loadCalendarBoxScore(item.game_id);
    });
    const boxScore = loadingBoxScore
      ? node("div", "empty-state", "Loading stored box score...")
      : selectedGameBoxScore(item.game_id);
    return append(node("div", "calendar-detail"), [
      node("span", "tag", played ? "Final" : "Scheduled"),
      node("strong", null, played
        ? `${item.away_team} ${item.away_score ?? "-"} at ${item.home_team} ${item.home_score ?? "-"}`
        : `${item.away_team} at ${item.home_team}`),
      node("p", "muted", `Week ${item.week || "-"} | ${shortDate(item.game_date)}${item.game_time_et ? ` | ${item.game_time_et} ET` : ""}`),
      showBox,
      boxScore,
    ]);
  }

  function renderCommands() {
    setHeader("System", "Player-facing tools are now handled from their season screens.");
    const root = document.createDocumentFragment();
    const p = panel("System Tools", "Moved");
    panelBody(p).append(node("div", "empty-state", "Direct technical commands have been removed from the game UI. Use the season, roster, draft, free-agency, and AI GM screens for actions."));
    root.append(p);
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
      postseasonRound: "postseason_round",
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
      aiGmEvaluate: "ai_gm_evaluate",
      aiGmCutdownPlan: "ai_gm_cutdown_plan",
      aiGmCutdownPlanPersist: "ai_gm_cutdown_plan_persist",
      aiGmCutdownPlans: "ai_gm_cutdown_plans",
      aiGmContractPlan: "ai_gm_contract_plan",
      aiGmContractPlanPersist: "ai_gm_contract_plan_persist",
      aiGmContractPlans: "ai_gm_contract_plans",
      aiGmDryRunContractApply: null,
      aiGmApplyContractPlan: null,
      aiGmFreeAgentPlan: "ai_gm_free_agent_plan",
      aiGmFreeAgentPlanPersist: "ai_gm_free_agent_plan_persist",
      aiGmFreeAgentPlans: "ai_gm_free_agent_plans",
      aiGmDryRunFreeAgentApply: null,
      aiGmApplyFreeAgentPlan: null,
      aiGmDraftPlan: "ai_gm_draft_plan",
      aiGmDraftPlanPersist: "ai_gm_draft_plan_persist",
      aiGmDraftPlans: "ai_gm_draft_plans",
      aiGmDraftPlanAll: null,
      aiGmOffseasonPreFaDryRun: null,
      aiGmOffseasonPreFaApply: null,
      aiGmOffseasonFaWave1DryRun: null,
      aiGmOffseasonFaWave1Apply: null,
      aiGmDryRunCutdownApply: null,
      aiGmApplyCutdownPlan: null,
      aiGmOps: "ai_gm_ops",
      aiGmOpsAll: null,
      aiGmOpsEnqueue: null,
      aiGmOpsEnqueueAll: null,
      aiGmQueue: "ai_gm_queue",
      aiGmProcessQueue: "ai_gm_process_queue",
      aiGmProcessQueueAll: null,
      aiGmEnableOllama: "ai_gm_enable_ollama",
      aiGmShowConfig: "ai_gm_show_config",
      aiGmAutonomyShow: "ai_gm_autonomy_show",
      aiGmAutonomyAdvisory: null,
      aiGmAutonomyLowRisk: null,
      aiGmDailyRun: null,
      aiGmDailyRunPersist: null,
      aiGmDailyRunAllPersist: null,
      aiGmDailyRunApply: null,
      aiGmReviewInbox: "ai_gm_review_inbox",
      aiGmReviewInboxAll: null,
      aiGmReviewHistory: "ai_gm_review_history",
      aiGmReviewHistoryAll: null,
      aiGmReviewShow: null,
      aiGmReviewApprove: null,
      aiGmReviewReject: null,
      aiGmReviewApply: null,
      aiGmReviewApplyCommit: null,
      aiGmReviewApplyAllApproved: "ai_gm_review_apply",
      aiGmReviewApplyAllApprovedCommit: "ai_gm_review_apply",
      aiGmDevSeedReview: "ai_gm_dev_seed_review",
      aiGmDevClearReviews: "ai_gm_dev_clear_reviews",
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
    maybeShowRosterGatePromptFromState();
    const previousView = state.lastRenderedView;
    const shouldRestoreScroll = previousView === state.view;
    const scrollElement = document.scrollingElement || document.documentElement;
    const scrollTop = scrollElement ? scrollElement.scrollTop : window.scrollY;
    const scrollLeft = scrollElement ? scrollElement.scrollLeft : window.scrollX;
    const nestedScroll = shouldRestoreScroll ? scrollableSnapshot() : [];
    if (state.view === "season") renderSeason();
    else if (state.view === "playoffTree") renderPlayoffTree();
    else if (state.view === "stats") renderStats();
    else if (state.view === "inbox") renderInbox();
    else if (state.view === "leagueNews") renderLeagueNews();
    else if (state.view === "transactions") renderTransactions();
    else if (state.view === "injuries") renderInjuries();
    else if (state.view === "scouting") renderScouting();
    else if (state.view === "roster") renderRosterHub();
    else if (state.view === "practiceSquad") renderPracticeSquad();
    else if (state.view === "depth") renderDepthChart();
    else if (state.view === "contracts") renderContracts();
    else if (state.view === "freeAgency") renderFreeAgency();
    else if (state.view === "draft") renderDraft();
    else if (state.view === "aiGm") renderAiGm();
    else if (state.view === "calendar") renderCalendar();
    else renderCalendar();
    state.lastRenderedView = state.view;
    if (shouldRestoreScroll) {
      requestAnimationFrame(() => {
        const target = document.scrollingElement || document.documentElement;
        if (target) {
          target.scrollTop = scrollTop;
          target.scrollLeft = scrollLeft;
        } else {
          window.scrollTo(scrollLeft, scrollTop);
        }
        restoreScrollableSnapshot(nestedScroll);
      });
    }
  }

  refs.buttons.forEach((button) => {
    button.addEventListener("click", () => {
      if (button.hidden) return;
      switchView(button.dataset.view, { refresh: false });
      refreshCurrentView();
    });
  });

  refs.backButton?.addEventListener("click", goBackView);

  setupRailToggle();

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshCurrentView();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.draftProspectPopoverOpen) {
      closeDraftProspectPopover();
    }
  });
  window.addEventListener("focus", () => refreshCurrentView());
  window.setInterval(() => {
    if (!document.hidden) refreshCurrentView();
  }, 45000);

  loadLiveState().then(() => {
    render();
    refreshCurrentView();
  });
}());

