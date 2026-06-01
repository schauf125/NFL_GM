(function () {
  let data = window.GAME_CENTER_DATA || {};
  const GAME_CENTER_VIEWS = new Set([
    "today",
    "calendar",
    "inbox",
    "season",
    "playoffTree",
    "stats",
    "awards",
    "history",
    "leagueNews",
    "transactions",
    "injuries",
    "roster",
    "practiceSquad",
    "depth",
    "contracts",
    "scouting",
    "freeAgency",
    "waivers",
    "trades",
    "draft",
    "aiGm",
  ]);

  function urlParams() {
    return new URLSearchParams(window.location.search);
  }

  function normalizeView(view) {
    const key = String(view || "").trim();
    return GAME_CENTER_VIEWS.has(key) ? key : "today";
  }

  function initialView() {
    return normalizeView(urlParams().get("view"));
  }

  function initialRosterTeam() {
    return String(urlParams().get("team") || "").trim().toUpperCase();
  }

  function initialPlayerSelection() {
    return String(urlParams().get("player") || urlParams().get("player_id") || "").trim();
  }
  const state = {
    view: initialView(),
    runnerAvailable: location.protocol.startsWith("http"),
    runnerBusy: false,
    busyAction: null,
    cancelRequested: false,
    runnerStartedAt: null,
    simProgressTick: 0,
    lastResult: null,
    selectedDraftProspectId: null,
    selectedDraftClassPackage: "",
    draftProspectPopoverOpen: false,
    draftTradeModal: null,
    draftTradeAlertDismissed: {},
    draftBoardSort: { key: "rank", direction: "asc" },
    draftBoardPositionFilter: "all",
    scoutingBoardSort: { key: "rank", direction: "asc" },
    scoutingPositionFilter: "all",
    scoutingConfidenceFilter: "all",
    selectedDepthSlot: null,
    depthOffensePersonnel: "11",
    depthDefensePackage: "nickel",
    depthLayoutUnlocked: false,
    depthLayoutOverrides: loadDepthLayoutOverrides(),
    selectedCalendarItem: null,
    historyTeam: "",
    inboxTab: "priority",
    calendarBoxScores: {},
    calendarBoxScoreLoadingId: null,
    calendarLiveFocus: false,
    boxScoreModal: null,
    injuryModal: null,
    injuryModalContext: null,
    injuryAutoManageChecked: false,
    injuryAutoManageSaving: false,
    rosterCutdownPrompt: null,
    rosterCutdownPromptDismissedKey: null,
    fifthYearOptionPromptDismissedKey: null,
    pendingRosterCutdownAction: null,
    pendingSimAdvancePrompt: null,
    selectedAiGmReviewId: null,
    newsFilter: "all",
    statsLiveSeason: null,
    statsLoading: false,
    awardsLiveSeason: null,
    awardsLoading: false,
    seasonLiveSeason: null,
    seasonLiveKey: null,
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
    whyModal: null,
    injuriesLiveKey: null,
    injuriesLoading: false,
    injuriesScopeFilter: "all",
    practiceSquadLiveKey: null,
    practiceSquadLoading: false,
    practiceSquadFilter: "eligible",
    pendingCutdownMoves: {},
    pendingRosterActions: {},
    pendingDepthActions: [],
    draftLiveKey: null,
    draftLoading: false,
    scoutingLiveKey: null,
    scoutingLoading: false,
    freeAgencyLiveKey: null,
    freeAgencyLoading: false,
    freeAgencyPositionFilter: "all",
    freeAgencyTierFilter: "all",
    freeAgencySort: { key: "heat", direction: "desc" },
    selectedFreeAgentPlayerId: normalizeView(urlParams().get("view")) === "freeAgency" ? initialPlayerSelection() : null,
    pendingFreeAgencyOffers: {},
    waiversLiveKey: null,
    waiversLoading: false,
    tradeLiveKey: null,
    tradeLoading: false,
    tradePartnerTeam: "",
    tradeUserSlots: Array(5).fill(""),
    tradePartnerSlots: Array(5).fill(""),
    takeoverTeam: "",
    rosterPositionFilter: "all",
    rosterGroupFilter: "all",
    rosterStatusFilter: "all",
    rosterTeam: initialRosterTeam(),
    selectedRosterPlayerId: normalizeView(urlParams().get("view")) === "roster" ? initialPlayerSelection() : null,
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
  const DRAFT_TRADE_MAX_OFFER_PICKS = 4;
  let viewRefreshInFlight = null;
  let renderScheduled = false;
  const SIM_PROGRESS_POLL_ACTIONS = new Set([
    "sim_week",
    "sim_season",
    "advance_next_event",
    "advance_to_date",
    "advance_to_draft",
    "advance_next_league_year",
    "auto_cutdown_continue",
    "postseason",
    "postseason_round",
    "complete_season",
  ]);
  const USER_CPU_MANAGED_SIM_ACTIONS = new Set([
    "sim_week",
    "sim_season",
    "advance_next_event",
    "advance_to_date",
    "advance_to_draft",
    "advance_next_league_year",
    "complete_season",
  ]);

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
    "take_over_team",
    "draft_class_generate",
    "draft_class_import",
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
  const AWARDS_REFRESH_ACTIONS = new Set([
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
    "take_over_team",
    "draft_class_generate",
    "draft_class_import",
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
    "take_over_team",
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
    "take_over_team",
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
    "trade_submit",
    "trade_cpu_market",
    "draft_pick",
    "draft_user_trade",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "contract_extend",
    "contract_tag",
    "contract_option_exercise",
    "contract_option_decline",
    "contract_release",
    "contract_restructure",
    "roster_release_player",
    "roster_cutdown_apply",
    "practice_squad_assign",
    "practice_squad_promote",
    "practice_squad_release",
    "waiver_claim",
    "waiver_cpu_seed",
    "waiver_process",
    "depth_chart_set",
    "depth_chart_move",
    "depth_chart_swap",
    "auto_cutdown",
    "auto_cutdown_continue",
    "take_over_team",
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
    "take_over_team",
  ]);
  const DRAFT_REFRESH_ACTIONS = new Set([
    "draft_class_generate",
    "draft_class_import",
    "advance_to_draft",
    "auto_cutdown_continue",
    "draft_start",
    "draft_pick",
    "draft_user_trade",
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
    "take_over_team",
    "draft_class_generate",
    "draft_class_import",
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
    "take_over_team",
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_offer",
    "trade_submit",
    "trade_cpu_market",
    "ai_gm_free_agent_plan",
    "ai_gm_free_agent_plan_persist",
    "ai_gm_apply_free_agent_plan",
  ]);
  const WAIVERS_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "advance_next_event",
    "advance_to_date",
    "advance_to_draft",
    "sim_week",
    "sim_season",
    "waiver_claim",
    "waiver_cpu_seed",
    "waiver_process",
    "roster_release_player",
    "roster_cutdown_apply",
    "contract_release",
    "auto_cutdown",
    "auto_cutdown_continue",
  ]);
  const CONTRACTS_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "complete_season",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "take_over_team",
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_offer",
    "trade_submit",
    "contract_extend",
    "contract_tag",
    "contract_option_exercise",
    "contract_option_decline",
    "contract_release",
    "contract_restructure",
    "roster_cutdown_apply",
    "waiver_claim",
    "waiver_process",
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
    "trade_submit",
    "draft_finish",
    "depth_chart_set",
    "depth_chart_move",
    "depth_chart_swap",
    "contract_release",
    "waiver_claim",
    "waiver_process",
    "roster_cutdown_apply",
    "practice_squad_assign",
    "practice_squad_promote",
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
    "draft_user_trade",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "draft_pause",
    "draft_resume",
  ]);
  const TRADE_REFRESH_ACTIONS = new Set([
    "new_june1_save",
    "load_game",
    "trade_submit",
    "trade_cpu_market",
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "take_over_team",
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
      "Standings": "Std",
      "Stats": "Sta",
      "Awards": "Awd",
      "History": "His",
      "Inbox": "In",
      "League News": "News",
      "Transactions": "Txn",
      "Scouting": "Sct",
      "Roster": "Ros",
      "Depth Chart": "Dep",
      "Contracts": "Con",
      "Free Agency": "FA",
      "Waivers": "Wav",
      "Roster Cutdown": "Cut",
      "Draft Room": "Drf",
      "CPU Front Offices": "CPU",
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

  function loadDepthLayoutOverrides() {
    try {
      return JSON.parse(localStorage.getItem("nflGmDepthLayoutOverrides") || "{}");
    } catch (_err) {
      return {};
    }
  }

  function saveDepthLayoutOverrides() {
    try {
      localStorage.setItem("nflGmDepthLayoutOverrides", JSON.stringify(state.depthLayoutOverrides || {}));
    } catch (_err) {
      // Cosmetic layout preferences can safely fail without blocking depth-chart edits.
    }
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

  function activeSeasonYear() {
    return Number(
      data.currentSeason ||
      data.season?.season ||
      data.activeSave?.current_league_year ||
      data.draft?.year - 1 ||
      2026,
    );
  }

  function seasonDate(monthDay) {
    const season = activeSeasonYear();
    return season && monthDay ? `${season}-${monthDay}` : "";
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

  function experienceLabel(player) {
    const rookie = Boolean(Number(player?.is_rookie ?? player?.isRookie ?? 0));
    const years = Number(player?.years_exp ?? player?.yearsExp ?? 0);
    if (rookie || !Number.isFinite(years) || years <= 0) return "Rookie";
    if (years === 1) return "1 yr exp";
    return `${Math.round(years)} yrs exp`;
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
    "PT",
    "P",
    "LS",
    "KR",
    "PR",
    "H",
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
  const OFFENSE_10_FORMATION_SLOTS = [
    { slot: "LWR", label: "X", row: 3, col: "1 / span 2" },
    { slot: "SWR", label: "Slot", row: 4, col: "3 / span 2", rank: 1 },
    { slot: "LT", label: "LT", row: 3, col: "5" },
    { slot: "LG", label: "LG", row: 3, col: "6" },
    { slot: "C", label: "C", row: 3, col: "7" },
    { slot: "RG", label: "RG", row: 3, col: "8" },
    { slot: "RT", label: "RT", row: 3, col: "9" },
    { slot: "SWR", label: "WR4", row: 4, col: "10 / span 2", rank: 2 },
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
  const OFFENSE_13_FORMATION_SLOTS = [
    { slot: "LWR", label: "X", row: 3, col: "1 / span 2" },
    { slot: "LT", label: "LT", row: 3, col: "5" },
    { slot: "LG", label: "LG", row: 3, col: "6" },
    { slot: "C", label: "C", row: 3, col: "7" },
    { slot: "RG", label: "RG", row: 3, col: "8" },
    { slot: "RT", label: "RT", row: 3, col: "9" },
    { slot: "TE", label: "Y", row: 3, col: "10", rank: 1 },
    { slot: "TE", label: "F", row: 4, col: "11", rank: 2 },
    { slot: "TE", label: "U", row: 4, col: "3", rank: 3 },
    { slot: "QB", label: "QB", row: 5, col: "7" },
    { slot: "RB", label: "HB", row: 6, col: "7" },
  ];
  const DEFENSE_FORMATION_SLOTS = [
    { slot: "NICKEL_LEDGE", sourceSlot: "LEDGE", label: "LEO", row: 1, col: "4" },
    { slot: "NICKEL_LDL", sourceSlot: "LDL", label: "DT", row: 1, col: "6" },
    { slot: "NICKEL_RDL", sourceSlot: "RDL", label: "DT", row: 1, col: "7" },
    { slot: "NICKEL_REDGE", sourceSlot: "REDGE", label: "REO", row: 1, col: "9" },
    { slot: "NICKEL_WLB", sourceSlot: "WLB", label: "WLB", row: 2, col: "5" },
    { slot: "NICKEL_MLB", sourceSlot: "MLB", label: "MLB", row: 2, col: "7" },
    { slot: "NICKEL_NB", sourceSlot: "NB", label: "Nickel", row: 4, col: "4" },
    { slot: "NICKEL_LCB", sourceSlot: "LCB", label: "LCB", row: 5, col: "1 / span 2" },
    { slot: "NICKEL_RCB", sourceSlot: "RCB", label: "RCB", row: 5, col: "11 / span 2" },
    { slot: "NICKEL_FS", sourceSlot: "FS", label: "FS", row: 7, col: "3 / span 2" },
    { slot: "NICKEL_SS", sourceSlot: "SS", label: "SS", row: 7, col: "9 / span 2" },
  ];
  const BASE_DEFENSE_FORMATION_SLOTS = [
    { slot: "BASE34_LEDGE", sourceSlot: "LEDGE", label: "LEO", row: 1, col: "3" },
    { slot: "BASE34_LDL", sourceSlot: "LDL", label: "DT", row: 1, col: "5" },
    { slot: "BASE34_NT", sourceSlot: "NT", label: "NT", row: 1, col: "6 / span 2" },
    { slot: "BASE34_RDL", sourceSlot: "RDL", label: "DT", row: 1, col: "8" },
    { slot: "BASE34_REDGE", sourceSlot: "REDGE", label: "REO", row: 1, col: "10" },
    { slot: "BASE34_WLB", sourceSlot: "WLB", label: "WILB", row: 2, col: "6" },
    { slot: "BASE34_MLB", sourceSlot: "MLB", label: "MILB", row: 2, col: "8" },
    { slot: "BASE34_LCB", sourceSlot: "LCB", label: "LCB", row: 5, col: "1 / span 2" },
    { slot: "BASE34_RCB", sourceSlot: "RCB", label: "RCB", row: 5, col: "11 / span 2" },
    { slot: "BASE34_FS", sourceSlot: "FS", label: "FS", row: 7, col: "3 / span 2" },
    { slot: "BASE34_SS", sourceSlot: "SS", label: "SS", row: 7, col: "9 / span 2" },
  ];
  const BASE_43_DEFENSE_FORMATION_SLOTS = [
    { slot: "BASE43_LEDGE", sourceSlot: "LEDGE", label: "LE", row: 1, col: "4" },
    { slot: "BASE43_LDL", sourceSlot: "LDL", label: "DT", row: 1, col: "6" },
    { slot: "BASE43_RDL", sourceSlot: "RDL", label: "DT", row: 1, col: "7" },
    { slot: "BASE43_REDGE", sourceSlot: "REDGE", label: "RE", row: 1, col: "9" },
    { slot: "BASE43_WLB", sourceSlot: "WLB", label: "WLB", row: 2, col: "4" },
    { slot: "BASE43_MLB", sourceSlot: "MLB", label: "MLB", row: 2, col: "7" },
    { slot: "BASE43_SLB", sourceSlot: "SLB", label: "SLB", row: 2, col: "10" },
    { slot: "BASE43_LCB", sourceSlot: "LCB", label: "LCB", row: 5, col: "1 / span 2" },
    { slot: "BASE43_RCB", sourceSlot: "RCB", label: "RCB", row: 5, col: "11 / span 2" },
    { slot: "BASE43_FS", sourceSlot: "FS", label: "FS", row: 7, col: "3 / span 2" },
    { slot: "BASE43_SS", sourceSlot: "SS", label: "SS", row: 7, col: "9 / span 2" },
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
    if (phase.includes("preseason")) {
      if (data.season?.nextGameType === "PRE" && data.season?.nextWeek) return `Preseason Week ${data.season.nextWeek}`;
      return "Preseason";
    }
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

  function isObserveMode() {
    return String(data.activeSave?.control_mode || data.registry?.controlMode || data.settings?.control_mode || "").toLowerCase() === "observe"
      || (!data.activeSave?.user_team && data.activeSave?.game_id);
  }

  function clearObserveInterruptions() {
    if (!isObserveMode()) return;
    state.injuryModal = null;
    state.injuryModalContext = null;
    state.rosterCutdownPrompt = null;
    state.pendingRosterCutdownAction = null;
    state.pendingSimAdvancePrompt = null;
    state.draftTradeModal = null;
  }

  function hasUserTeam() {
    return Boolean(data.activeSave?.user_team) && !isObserveMode();
  }

  function observeHiddenViews() {
    return new Set(["practiceSquad", "depth", "contracts", "scouting", "trades"]);
  }

  function updateHeaderChrome() {
    refs.seasonLabel.textContent = String(data.currentSeason || "");
    refs.phaseText.textContent = data.currentPhase || "";
    refs.dateText.textContent = currentDateDisplay();
    const saveName = data.activeSave?.display_name || data.registry?.activeGameId || "Master DB";
    refs.saveText.textContent = isObserveMode() ? `${saveName} (Observe)` : saveName;
    updateConditionalNav();
    updateLiveStatus();
  }

  function setHeader(title, subhead) {
    document.body.dataset.view = state.view;
    refs.title.textContent = title;
    refs.subhead.textContent = subhead;
    if (refs.backButton) {
      refs.backButton.disabled = state.viewHistory.length === 0;
      refs.backButton.title = state.viewHistory.length ? `Back to ${viewLabel(state.viewHistory[state.viewHistory.length - 1])}` : "No previous screen";
    }
    refs.buttons.forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
    updateHeaderChrome();
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
    const hiddenForObserve = observeHiddenViews();
    refs.buttons.forEach((button) => {
      if (button.dataset.view === "playoffTree") {
        button.hidden = !playoffTreeVisible();
      } else if (isObserveMode() && hiddenForObserve.has(button.dataset.view)) {
        button.hidden = true;
      } else if (hiddenForObserve.has(button.dataset.view)) {
        button.hidden = false;
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
      ["awardsLoading", "awards"],
      ["calendarLoading", "calendar"],
      ["inboxLoading", "inbox"],
      ["leagueNewsLoading", "news"],
      ["draftLoading", "draft"],
      ["scoutingLoading", "scouting"],
      ["freeAgencyLoading", "free agency"],
      ["contractsLoading", "contracts"],
      ["depthChartLoading", "depth"],
      ["aiGmLoading", "CPU front offices"],
    ].filter(([key]) => state[key]).map(([, label]) => label);
  }

  function updateLiveStatus() {
    if (!refs.liveStatus) return;
    const loading = loadingLabels();
    const errors = Object.entries(state.liveErrors || {});
    refs.liveStatus.className = "live-status";
    if (state.runnerBusy) {
      refs.liveStatus.hidden = false;
      refs.liveStatus.classList.add("running");
      refs.liveStatus.textContent = `${actionLabel(state.busyAction)} running${state.cancelRequested ? " | stop requested" : ""}`;
      return;
    }
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
      refs.liveStatus.textContent = "League office connection paused; screens are read-only for now.";
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

  function numberText(value, fallback = "0") {
    const num = Number(value);
    if (!Number.isFinite(num)) return fallback;
    return Number.isInteger(num) ? String(num) : num.toFixed(1);
  }

  function boxScorePayload(result) {
    return result?.boxScore || result?.box_score || null;
  }

  function boxScoreMeta(boxScore) {
    const parts = [];
    if (boxScore?.gameType) parts.push(String(boxScore.gameType));
    if (boxScore?.week !== undefined && boxScore?.week !== null && String(boxScore.week) !== "") parts.push(`Week ${boxScore.week}`);
    if (boxScore?.gameDate) parts.push(shortDate(boxScore.gameDate));
    return parts.join(" | ");
  }

  function boxScoreTeamCard(team) {
    const card = node("div", `box-score-team-card ${team?.result === "Win" ? "winner" : ""}`.trim());
    const identity = node("div", "box-score-team-identity");
    append(identity, [
      teamLogo(team?.logo, team?.abbr, "box-score-team-logo"),
      append(node("div"), [
        node("strong", null, team?.abbr || "-"),
        node("small", null, team?.name || ""),
      ]),
    ]);
    append(card, [
      identity,
      append(node("div", "box-score-team-score"), [
        node("span", null, numberText(team?.score)),
        node("small", null, team?.result || ""),
      ]),
    ]);
    return card;
  }

  function boxScoreComparisonRow(row) {
    const away = Number(row?.away || 0);
    const home = Number(row?.home || 0);
    const max = Math.max(Math.abs(away), Math.abs(home), 1);
    const item = node("div", "box-score-stat-compare-row");
    const awayBar = node("span", "box-score-stat-bar away");
    awayBar.style.width = `${Math.max(6, Math.round((Math.abs(away) / max) * 100))}%`;
    const homeBar = node("span", "box-score-stat-bar home");
    homeBar.style.width = `${Math.max(6, Math.round((Math.abs(home) / max) * 100))}%`;
    append(item, [
      append(node("div", "box-score-stat-value away"), [node("strong", null, numberText(away)), awayBar]),
      node("span", "box-score-stat-label", row?.label || "-"),
      append(node("div", "box-score-stat-value home"), [node("strong", null, numberText(home)), homeBar]),
    ]);
    return item;
  }

  function boxScorePlayerName(row, team) {
    return playerLink(row?.playerId, row?.name || "-", "player-link strong-link", {
      team,
      position: row?.position,
    });
  }

  function boxScoreTable(title, rows, columns, team, limit) {
    const visible = (rows || []).slice(0, limit || 3);
    if (!visible.length) return null;
    const section = node("section", "box-score-player-section");
    const table = node("table", "box-score-player-table");
    const thead = node("thead");
    append(thead, [append(node("tr"), columns.map((column) => node("th", column.align === "right" ? "right" : "", column.label)))]);
    const tbody = node("tbody");
    visible.forEach((row) => {
      const tr = node("tr");
      columns.forEach((column) => {
        const td = node("td", column.align === "right" ? "right" : "");
        const value = column.render(row, team);
        append(td, value instanceof Node ? value : String(value ?? "-"));
        tr.append(td);
      });
      tbody.append(tr);
    });
    append(table, [thead, tbody]);
    append(section, [node("h4", null, title), table]);
    return section;
  }

  function boxScoreTeamLeaders(boxScore, team, compact) {
    const sections = boxScore?.players?.[team?.abbr] || {};
    const limit = compact ? 2 : 4;
    const card = node("div", "box-score-leader-card");
    append(card, [
      append(node("div", "box-score-leader-team"), [
        teamLogo(team?.logo, team?.abbr, "box-score-leader-logo"),
        append(node("div"), [
          node("strong", null, team?.abbr || "-"),
          node("small", null, "Player leaders"),
        ]),
      ]),
      boxScoreTable("Passing", sections.passing, [
        { label: "Player", render: (row) => boxScorePlayerName(row, team?.abbr) },
        { label: "C/A", align: "right", render: (row) => `${numberText(row.completions)}/${numberText(row.attempts)}` },
        { label: "Yds", align: "right", render: (row) => numberText(row.yards) },
        { label: "TD", align: "right", render: (row) => numberText(row.td) },
        { label: "INT", align: "right", render: (row) => numberText(row.int) },
      ], team?.abbr, limit),
      boxScoreTable("Rushing", sections.rushing, [
        { label: "Player", render: (row) => boxScorePlayerName(row, team?.abbr) },
        { label: "Car", align: "right", render: (row) => numberText(row.attempts) },
        { label: "Yds", align: "right", render: (row) => numberText(row.yards) },
        { label: "Avg", align: "right", render: (row) => numberText(row.avg) },
        { label: "TD", align: "right", render: (row) => numberText(row.td) },
      ], team?.abbr, limit),
      boxScoreTable("Receiving", sections.receiving, [
        { label: "Player", render: (row) => boxScorePlayerName(row, team?.abbr) },
        { label: "Rec", align: "right", render: (row) => `${numberText(row.receptions)}/${numberText(row.targets)}` },
        { label: "Yds", align: "right", render: (row) => numberText(row.yards) },
        { label: "Avg", align: "right", render: (row) => numberText(row.avg) },
        { label: "TD", align: "right", render: (row) => numberText(row.td) },
      ], team?.abbr, limit),
      boxScoreTable("Defense", sections.defense, [
        { label: "Player", render: (row) => boxScorePlayerName(row, team?.abbr) },
        { label: "Tkl", align: "right", render: (row) => numberText(row.tackles) },
        { label: "Sk", align: "right", render: (row) => numberText(row.sacks) },
        { label: "INT", align: "right", render: (row) => numberText(row.int) },
        { label: "PD", align: "right", render: (row) => numberText(row.pd) },
      ], team?.abbr, limit),
      boxScoreTable("Kicking", sections.kicking, [
        { label: "Player", render: (row) => boxScorePlayerName(row, team?.abbr) },
        { label: "FG", align: "right", render: (row) => `${numberText(row.fgMade)}/${numberText(row.fgAttempts)}` },
        { label: "XP", align: "right", render: (row) => `${numberText(row.xpMade)}/${numberText(row.xpAttempts)}` },
        { label: "Long", align: "right", render: (row) => numberText(row.long) },
      ], team?.abbr, compact ? 1 : 2),
      boxScoreTable("Punting", sections.punting, [
        { label: "Player", render: (row) => boxScorePlayerName(row, team?.abbr) },
        { label: "Punts", align: "right", render: (row) => numberText(row.punts) },
        { label: "Yds", align: "right", render: (row) => numberText(row.yards) },
        { label: "Avg", align: "right", render: (row) => numberText(row.avg) },
      ], team?.abbr, compact ? 1 : 2),
    ]);
    return card;
  }

  function boxScoreDrives(boxScore, compact) {
    const drives = boxScore?.drives || [];
    if (!drives.length) return null;
    const section = node("section", "box-score-flow-card");
    const list = node("div", "box-score-drive-list");
    drives.slice(0, compact ? 10 : drives.length).forEach((drive) => {
      append(list, [
        append(node("div", `box-score-drive ${Number(drive.points || 0) > 0 ? "scoring" : ""}`.trim()), [
          node("span", "box-score-drive-num", String(drive.driveNumber || "")),
          append(node("div"), [
            node("strong", null, `${drive.offense || "-"}: ${drive.result || "Drive"}`),
            node("small", null, `Q${drive.quarter || "-"} ${drive.clock || "--:--"} | ${numberText(drive.plays)} plays, ${numberText(drive.yards)} yards`),
          ]),
          Number(drive.points || 0) ? node("span", "box-score-drive-points", `+${drive.points}`) : null,
        ]),
      ]);
    });
    append(section, [
      node("h3", null, "Drive Summary"),
      list,
    ]);
    return section;
  }

  function boxScoreRecentPlays(boxScore, compact) {
    const plays = compact ? [] : (boxScore?.plays || []);
    if (!plays.length) return null;
    const section = node("section", "box-score-flow-card");
    const list = node("div", "box-score-play-list");
    plays.forEach((play) => {
      append(list, [
        append(node("div", `box-score-play ${play.touchdown ? "touchdown" : ""} ${play.turnover ? "turnover" : ""}`.trim()), [
          node("span", "box-score-play-clock", `Q${play.quarter || "-"} ${play.clock || "--:--"}`),
          node("strong", null, `${play.offense || "-"} ${play.down || "-"}&${play.distance || "-"}`),
          node("span", null, play.description || ""),
        ]),
      ]);
    });
    append(section, [
      node("h3", null, "Recent Plays"),
      list,
    ]);
    return section;
  }

  function renderBoxScore(result, options = {}) {
    const boxScore = boxScorePayload(result);
    if (!boxScore) {
      const text = String(result?.stdout || "").trim();
      return text ? node("pre", "box-score-output", text) : node("div", "empty-state", "No stored box score text was returned for this game.");
    }
    if (!boxScore.run) {
      return append(node("div", "box-score-view compact"), [
        append(node("div", "box-score-scoreboard"), [
          boxScoreTeamCard(boxScore.teams?.[0] || {}),
          append(node("div", "box-score-game-meta"), [
            node("span", "tag", boxScore.status === "scheduled" ? "Scheduled" : "No Box Score"),
            node("strong", null, boxScore.matchup || "Game"),
            node("small", null, boxScoreMeta(boxScore)),
          ]),
          boxScoreTeamCard(boxScore.teams?.[1] || {}),
        ]),
        node("div", "empty-state", "This game does not have a stored play-by-play box score yet."),
      ]);
    }
    const compact = Boolean(options.compact);
    const wrap = node("div", `box-score-view ${compact ? "compact" : ""}`.trim());
    append(wrap, [
      append(node("div", "box-score-scoreboard"), [
        boxScoreTeamCard(boxScore.teams?.[0] || {}),
        append(node("div", "box-score-game-meta"), [
          node("span", "tag good", "Final"),
          node("strong", null, boxScore.matchup || "Game"),
          node("small", null, boxScoreMeta(boxScore)),
        ]),
        boxScoreTeamCard(boxScore.teams?.[1] || {}),
      ]),
      append(node("section", "box-score-compare-card"), [
        node("h3", null, "Team Stats"),
        append(node("div", "box-score-stat-compare"), (boxScore.comparison || []).map(boxScoreComparisonRow)),
      ]),
      append(node("section", "box-score-leaders-grid"), (boxScore.teams || []).map((team) => boxScoreTeamLeaders(boxScore, team, compact))),
      boxScoreDrives(boxScore, compact),
      boxScoreRecentPlays(boxScore, compact),
    ]);
    return wrap;
  }

  function playerProfileHref({ playerId, name, team, position }) {
    const params = new URLSearchParams();
    if (playerId) params.set("player", playerId);
    if (name) params.set("name", name);
    if (team) params.set("team", team);
    if (position) params.set("position", position);
    const query = params.toString();
    return withReturnTarget(`../player_profile/index.html${query ? `?${query}` : ""}`);
  }

  function playerCardHref({ playerId, name, team, position }) {
    const params = new URLSearchParams();
    if (playerId) params.set("player", playerId);
    if (name) params.set("name", name);
    if (team) params.set("team", team);
    if (position) params.set("position", position);
    const query = params.toString();
    return withReturnTarget(`../player_card/index.html${query ? `?${query}` : ""}`);
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

  function prospectLink(prospectId, name, className, options = {}) {
    if (!prospectId) return node("span", className || "", name || "-");
    const button = node("button", className || "prospect-link", name || "Prospect");
    button.type = "button";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openProspect(prospectId, options);
    });
    return button;
  }

  function openProspect(prospectId, options = {}) {
    const id = String(prospectId || "");
    state.selectedDraftProspectId = prospectId;
    state.selectedCalendarItem = null;
    state.draftProspectPopoverOpen = true;
    const scoutingHasProspect = (data.scouting?.board || []).some((prospect) => String(prospect.prospect_id) === id);
    const draftHasProspect = (data.draft?.board || []).some((prospect) => String(prospect.prospect_id) === id);
    const preferredView = options.preferredView === "draft" || options.preferredView === "scouting"
      ? options.preferredView
      : null;
    const targetView = preferredView || (scoutingHasProspect || !draftHasProspect ? "scouting" : "draft");
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

  function openRosterTeam(abbr) {
    const team = String(abbr || "").trim().toUpperCase();
    if (!team || team === "-") return;
    state.rosterTeam = team;
    state.rosterGroupFilter = "all";
    state.rosterStatusFilter = "all";
    state.selectedRosterPlayerId = null;
    state.depthChartLiveKey = null;
    switchView("roster", { refresh: true });
  }

  function statTeamLink(team) {
    const abbr = String(team || "").trim().toUpperCase();
    if (!abbr || abbr === "-") return "-";
    const button = node("button", "text-link team-stat-link", abbr);
    button.type = "button";
    button.title = `Open ${abbr} roster`;
    button.addEventListener("click", () => openRosterTeam(abbr));
    return button;
  }

  function normalizeTeamOption(team) {
    const abbr = String(team?.abbr || team?.abbreviation || team?.team || "").trim().toUpperCase();
    if (!abbr || abbr === "-") return null;
    const name = team?.name
      || team?.team_name
      || [team?.city, team?.nickname].filter(Boolean).join(" ")
      || abbr;
    return {
      abbr,
      name,
      conference: team?.conference || "",
      division: team?.division || "",
      logo: team?.teamLogo || team?.logo || "",
    };
  }

  function rosterTeamOptions() {
    const map = new Map();
    const add = (team) => {
      const normalized = normalizeTeamOption(team);
      if (normalized && !map.has(normalized.abbr)) map.set(normalized.abbr, normalized);
    };
    (data.teams || []).forEach(add);
    (data.season?.standings || []).forEach(add);
    (data.tradeCenter?.teams || []).forEach(add);
    add({
      abbreviation: data.depthChart?.team,
      team_name: data.depthChart?.teamName,
    });
    add({
      abbreviation: data.activeSave?.user_team,
      team_name: data.activeSave?.user_team,
    });
    return [...map.values()].sort((a, b) => {
      const aLabel = String(a.name && a.name !== a.abbr ? a.name : a.abbr).toLowerCase();
      const bLabel = String(b.name && b.name !== b.abbr ? b.name : b.abbr).toLowerCase();
      return aLabel.localeCompare(bLabel) || a.abbr.localeCompare(b.abbr);
    });
  }

  function rosterTeamSelector(activeTeam) {
    let teams = rosterTeamOptions();
    const firstTeam = teams[0]?.abbr || "MIN";
    const userTeam = String(data.activeSave?.user_team || firstTeam).toUpperCase();
    const active = String(activeTeam || userTeam || firstTeam).toUpperCase();
    if (!teams.some((team) => team.abbr === active)) {
      teams = [{ abbr: active, name: active, conference: "", division: "", logo: "" }, ...teams];
      teams.sort((a, b) => {
        const aLabel = String(a.name && a.name !== a.abbr ? a.name : a.abbr).toLowerCase();
        const bLabel = String(b.name && b.name !== b.abbr ? b.name : b.abbr).toLowerCase();
        return aLabel.localeCompare(bLabel) || a.abbr.localeCompare(b.abbr);
      });
    }
    const selected = teams.find((team) => team.abbr === active) || teams[0];
    const wrap = node("div", "roster-team-switcher");
    const logo = teamLogo(selected?.logo, selected?.abbr, "roster-team-logo");
    const field = node("label", "roster-filter roster-team-filter");
    append(field, [node("span", null, "Change Team")]);
    const select = node("select");
    teams.forEach((team) => {
      const option = node("option", null, `${team.abbr} | ${team.name}`);
      option.value = team.abbr;
      option.selected = team.abbr === active;
      select.append(option);
    });
    select.addEventListener("change", () => {
      state.rosterTeam = select.value;
      state.selectedRosterPlayerId = null;
      state.depthChartLiveKey = null;
      if (runnerMode()) {
        loadLiveDepthChart().then(render);
      } else {
        render();
      }
    });
    field.append(select);
    const myTeam = node("button", "run-button compact roster-my-team-button", isObserveMode() ? "Observe" : "My Team");
    myTeam.type = "button";
    myTeam.disabled = isObserveMode() || active === userTeam;
    myTeam.title = isObserveMode() ? "Observe Mode has no user-controlled team." : `Show ${userTeam} roster`;
    myTeam.addEventListener("click", () => openRosterTeam(userTeam));
    append(wrap, [
      logo,
      field,
      myTeam,
      node("small", null, selected?.division ? `${selected.conference || "NFL"} ${selected.division}` : "Roster view"),
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

  function syncGameCenterUrl() {
    if (!window.history?.replaceState) return;
    const url = new URL(window.location.href);
    const view = normalizeView(state.view);
    if (view === "today") url.searchParams.delete("view");
    else url.searchParams.set("view", view);
    if (view === "roster" && state.rosterTeam) {
      url.searchParams.set("team", state.rosterTeam);
    } else {
      url.searchParams.delete("team");
    }
    if (view === "roster" && state.selectedRosterPlayerId) {
      url.searchParams.set("player", state.selectedRosterPlayerId);
    } else if (view === "freeAgency" && state.selectedFreeAgentPlayerId) {
      url.searchParams.set("player", state.selectedFreeAgentPlayerId);
    } else {
      url.searchParams.delete("player");
      url.searchParams.delete("player_id");
    }
    const next = `${url.pathname}${url.search}${url.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState({ view, team: state.rosterTeam || "" }, "", next);
    }
  }

  function currentReturnHref() {
    const url = new URL("../game_center/index.html", window.location.href);
    const view = normalizeView(state.view);
    if (view !== "today") url.searchParams.set("view", view);
    if (view === "roster" && state.rosterTeam) url.searchParams.set("team", state.rosterTeam);
    return `${url.pathname}${url.search}`;
  }

  function withReturnTarget(href) {
    const url = new URL(href, window.location.href);
    url.searchParams.set("returnTo", currentReturnHref());
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function switchView(view, options = {}) {
    view = normalizeView(view);
    if (view !== state.view && options.record !== false) {
      state.viewHistory.push(state.view);
      if (state.viewHistory.length > 30) state.viewHistory.shift();
    }
    state.view = view;
    syncGameCenterUrl();
    render();
    if (
      state.runnerBusy
      && SIM_PROGRESS_POLL_ACTIONS.has(state.busyAction)
      && (view === "season" || view === "playoffTree")
    ) {
      loadLiveSeason({ quiet: true }).then((changed) => {
        if (changed) scheduleRender();
      });
    }
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

  function tradeLiveKey() {
    return [
      data.activeSave?.game_id || "",
      data.currentDate || "",
      data.currentSeason || "",
      state.tradePartnerTeam || data.tradeCenter?.partnerTeam?.abbr || "",
      data.tradeGeneratedAt || "",
    ].join("|");
  }

  function apiUrl(path, params) {
    const query = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") query.set(key, value);
    });
    const queryText = query.toString();
    return `${path}${queryText ? `?${queryText}` : ""}`;
  }

  function scheduleRender() {
    if (renderScheduled) return;
    renderScheduled = true;
    window.requestAnimationFrame(() => {
      renderScheduled = false;
      render();
    });
  }

  function calendarDataKey(calendar, currentDate = "") {
    const days = (calendar?.days || []).map((day) => [
      day.date,
      day.isFocusDate ? 1 : 0,
      day.isToday ? 1 : 0,
      (day.events || []).map((event) => [event.event_id, event.event_code, event.event_name].join(":")).join(";"),
      (day.games || []).map((game) => [
        game.game_id,
        game.played,
        game.away_score,
        game.home_score,
        game.away_team,
        game.home_team,
      ].join(":")).join(";"),
      (day.news || []).map((item) => item.news_id).join(";"),
    ]);
    return JSON.stringify([
      currentDate || "",
      calendar?.focusDate || "",
      calendar?.monthLabel || "",
      calendar?.rangeStart || "",
      calendar?.rangeEnd || "",
      days,
    ]);
  }

  function seasonDataKey(season) {
    const standings = (season?.standings || []).map((team) => [
      team.team_id || team.abbreviation || "",
      team.wins || 0,
      team.losses || 0,
      team.ties || 0,
      team.points_for || 0,
      team.points_against || 0,
    ].join(":"));
    const recentResults = (season?.recentResults || []).map((game) => [
      game.game_id,
      game.played,
      game.away_score,
      game.home_score,
    ].join(":"));
    const postseason = season?.postseason || {};
    return JSON.stringify([
      season?.season || "",
      season?.totals?.played || 0,
      season?.totals?.remaining || 0,
      season?.nextWeek || "",
      postseason.played || 0,
      postseason.remaining || 0,
      standings,
      recentResults,
    ]);
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
      const payload = await response.json();
      if (!response.ok) {
        if (payload?.state) data = payload.state;
        throw new Error(payload?.error || `${response.status} ${response.statusText || "request failed"}`.trim());
      }
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
    updateHeaderChrome();
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

  async function loadLiveAwards() {
    if (!location.protocol.startsWith("http") || state.awardsLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const payload = await apiGet("awards", "/api/awards", {
      params: { season },
      loadingKey: "awardsLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      awards: payload.awards || data.awards || {},
      awardsGeneratedAt: payload.generatedAt,
    };
    state.awardsLiveSeason = String(payload.season || season || "");
    return true;
  }

  async function loadLiveSeason(options = {}) {
    if (!location.protocol.startsWith("http") || state.seasonLoading) return false;
    const season = data.season?.season || data.currentSeason || "";
    const previousKey = seasonDataKey(data.season || {});
    const payload = await apiGet("season", "/api/season", {
      params: { season },
      loadingKey: "seasonLoading",
      quiet: Boolean(options.quiet),
    });
    if (!payload) return false;
    data = {
      ...data,
      season: payload.seasonData || data.season || {},
      seasonGeneratedAt: payload.generatedAt,
    };
    state.seasonLiveSeason = String(payload.season || season || "");
    state.seasonLiveKey = seasonDataKey(data.season || {});
    return previousKey !== state.seasonLiveKey;
  }

  async function loadLiveCalendar(options = {}) {
    if (!location.protocol.startsWith("http") || state.calendarLoading) return false;
    const season = data.currentSeason || data.season?.season || "";
    const liveFocus = Boolean(options.liveFocus);
    const currentDate = liveFocus ? "" : (data.currentDate || data.calendar?.focusDate || "");
    const previousKey = calendarDataKey(data.calendar || {}, data.currentDate || "");
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
      currentDate: payload.saveCurrentDate || payload.currentDate || data.currentDate,
      currentPhase: payload.currentPhase || data.currentPhase,
      saveCurrentDate: payload.saveCurrentDate || data.saveCurrentDate,
    };
    state.calendarLiveKey = `${payload.season || season || ""}:${payload.currentDate || currentDate || ""}`;
    state.calendarLiveFocus = Boolean(payload.liveFocus);
    updateHeaderChrome();
    return previousKey !== calendarDataKey(data.calendar || {}, data.currentDate || "");
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
    const firstEvent = (draft.events || [])[0] || {};
    return [
      draft.year || "",
      stateRow.current_pick_number || "",
      stateRow.current_team || "",
      stateRow.status || "",
      draft.pickTotals?.used || 0,
      draft.pickTotals?.remaining || 0,
      (draft.selections || []).length,
      (draft.userSelections || []).length,
      firstEvent.event_id || "",
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

  function waiversLiveKey() {
    const waivers = data.waivers || {};
    const first = (waivers.wire || [])[0] || {};
    const firstClaim = (waivers.claims || [])[0] || {};
    return [
      waivers.season || data.currentSeason || "",
      data.currentDate || "",
      waivers.counts?.open || 0,
      waivers.counts?.claims || 0,
      first.waiver_id || "",
      first.status || "",
      first.claim_count || 0,
      firstClaim.claim_id || "",
      firstClaim.status || "",
    ].join(":");
  }

  function contractsLiveKey() {
    const talks = data.contractNegotiations || {};
    const firstExpiring = (talks.expiring || [])[0] || {};
    const firstCap = (talks.capCasualties || [])[0] || {};
    const firstOption = (talks.fifthYearOptions || [])[0] || {};
    return [
      data.currentDate || data.activeSave?.current_date || "",
      talks.season || data.currentSeason || "",
      talks.team || data.activeSave?.user_team || "",
      talks.counts?.expiring || talks.counts?.total || 0,
      talks.counts?.fifthYearOptions || (talks.fifthYearOptions || []).length || 0,
      talks.counts?.capCasualties || 0,
      talks.counts?.restructures || 0,
      talks.currentCap?.cap_space || "",
      talks.projectedCap?.cap_space || "",
      firstExpiring.player_id || "",
      firstOption.player_id || "",
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

  function rosterContractSeason() {
    return Number(
      data.currentContractYear
      || data.contractNegotiations?.currentCap?.season
      || data.freeAgency?.leagueYear
      || data.currentSeason
      || data.season?.season
      || 0
    );
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

  async function loadLiveInbox(options = {}) {
    if (!location.protocol.startsWith("http") || state.inboxLoading) return false;
    const payload = await apiGet("inbox", "/api/inbox", {
      params: { limit: 160 },
      loadingKey: "inboxLoading",
      quiet: Boolean(options.quiet),
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

  async function loadLiveLeagueNews(options = {}) {
    if (!location.protocol.startsWith("http") || state.leagueNewsLoading) return false;
    const payload = await apiGet("league news", "/api/league-news", {
      params: { limit: 80 },
      loadingKey: "leagueNewsLoading",
      quiet: Boolean(options.quiet),
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

  async function loadLiveTransactions(options = {}) {
    if (!location.protocol.startsWith("http") || state.transactionsLoading) return false;
    const payload = await apiGet("transactions", "/api/transactions", {
      params: { limit: 500 },
      loadingKey: "transactionsLoading",
      quiet: Boolean(options.quiet),
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

  async function loadLiveInjuries(options = {}) {
    if (!location.protocol.startsWith("http") || state.injuriesLoading) return false;
    const payload = await apiGet("injuries", "/api/injuries", {
      params: { active_limit: 180, recent_limit: 140 },
      loadingKey: "injuriesLoading",
      quiet: Boolean(options.quiet),
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

  async function loadLiveWaivers() {
    if (!location.protocol.startsWith("http") || state.waiversLoading) return false;
    const season = data.waivers?.season || data.currentSeason || "";
    const payload = await apiGet("waivers", "/api/waivers", {
      params: { season },
      loadingKey: "waiversLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      waivers: payload.waivers || data.waivers || { wire: [], claims: [], claimOrder: [], counts: {} },
      waiversGeneratedAt: payload.generatedAt,
    };
    state.waiversLiveKey = waiversLiveKey();
    return true;
  }

  async function loadLiveTrades() {
    if (!location.protocol.startsWith("http") || state.tradeLoading) return false;
    const partner = state.tradePartnerTeam || data.tradeCenter?.partnerTeam?.abbr || "";
    const payload = await apiGet("trade center", "/api/trade-center", {
      params: { partner },
      loadingKey: "tradeLoading",
    });
    if (!payload) return false;
    data = {
      ...data,
      tradeCenter: payload,
      tradeGeneratedAt: payload.generatedAt,
    };
    if (!state.tradePartnerTeam && payload.partnerTeam?.abbr) state.tradePartnerTeam = payload.partnerTeam.abbr;
    state.tradeLiveKey = tradeLiveKey();
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
    const contractSeason = rosterContractSeason() || season;
    const team = state.view === "roster" && state.rosterTeam
      ? state.rosterTeam
      : data.activeSave?.user_team || data.depthChart?.team || rosterTeamOptions()[0]?.abbr || "";
    const payload = await apiGet("depth chart", "/api/depth-chart", {
      params: { season, contractSeason, team },
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
    const payload = await apiGet("CPU Front Offices", "/api/ai-gm", {
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
      if (action === "roster_cutdown_apply") {
        state.waiversLiveKey = null;
        state.contractsLiveKey = null;
      }
      showToast("Refreshing depth chart...");
      const refreshes = [loadLiveDepthChart(), loadLivePracticeSquad()];
      if (action === "roster_cutdown_apply") refreshes.push(loadLiveWaivers(), loadLiveContracts());
      await Promise.allSettled(refreshes);
      return;
    }
    if (DRAFT_REFRESH_ACTIONS.has(action) || isDraftAction(action)) {
      state.draftLiveKey = null;
      showToast("Refreshing draft room...");
      await loadLiveDraft();
      return;
    }
    state.seasonLiveSeason = null;
    state.seasonLiveKey = null;
    state.statsLiveSeason = null;
    state.awardsLiveSeason = null;
    state.calendarLiveKey = null;
    state.inboxLiveKey = null;
    state.leagueNewsLiveKey = null;
    state.draftLiveKey = null;
    state.scoutingLiveKey = null;
    state.freeAgencyLiveKey = null;
    state.waiversLiveKey = null;
    state.tradeLiveKey = null;
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
    if (AWARDS_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveAwards());
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
    if (WAIVERS_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveWaivers());
    }
    if (TRADE_REFRESH_ACTIONS.has(action)) {
      refreshes.push(loadLiveTrades());
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
    if (!["draft_skip", "draft_skip_to_user", "draft_finish"].includes(action) || !runnerMode()) return null;
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
    if (!SIM_PROGRESS_POLL_ACTIONS.has(action) || !runnerMode()) return null;
    let stopped = false;
    let tickCount = 0;
    const tick = async () => {
      if (stopped) return;
      tickCount += 1;
      state.simProgressTick = tickCount;
      const refreshes = [loadLiveCalendar({ liveFocus: true, quiet: true })];
      if (state.view === "today" || state.view === "season" || state.view === "playoffTree") {
        refreshes.push(loadLiveSeason({ quiet: true }));
      }
      if (state.view === "awards") {
        refreshes.push(loadLiveAwards());
      }
      if (tickCount % 3 === 0) {
        refreshes.push(loadLiveLeagueNews({ quiet: true }));
        refreshes.push(loadLiveTransactions({ quiet: true }));
        refreshes.push(loadLiveInjuries({ quiet: true }));
      }
      const results = await Promise.allSettled(refreshes);
      const calendarChanged = results[0]?.status === "fulfilled" && Boolean(results[0].value);
      const seasonChanged = results.some((result, index) => index > 0 && result.status === "fulfilled" && Boolean(result.value));
      const streamChanged = tickCount % 3 === 0 && results.some((result, index) => index > 0 && result.status === "fulfilled" && Boolean(result.value));
      if ((calendarChanged && (state.view === "today" || state.view === "calendar")) || (seasonChanged && (state.view === "today" || state.view === "season" || state.view === "playoffTree" || state.view === "awards")) || streamChanged) {
        scheduleRender();
      }
    };
    tick();
    const interval = window.setInterval(tick, 1600);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }

  function activeViewRefreshers() {
    if (state.view === "today") return [loadLiveCalendar, loadLiveSeason, loadLiveInbox, loadLiveLeagueNews, loadLiveInjuries, loadLiveFreeAgency];
    if (state.view === "season" || state.view === "playoffTree") return [loadLiveSeason, loadLiveCalendar];
    if (state.view === "stats") return [loadLiveLeaders];
    if (state.view === "awards") return [loadLiveAwards];
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
    if (state.view === "waivers") return [loadLiveWaivers];
    if (state.view === "trades") return [loadLiveTrades];
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

  function userInjuryAutoManageEnabled() {
    return ["1", "true", "yes", "on"].includes(String(data.settings?.user_injury_auto_depth_chart || "").toLowerCase());
  }

  async function setUserInjuryAutoManage(enabled) {
    state.injuryAutoManageSaving = true;
    render();
    const payload = await apiPost("runner", "/api/run", {
      action: "injury_auto_manage",
      params: { enabled: Boolean(enabled) },
    });
    state.injuryAutoManageSaving = false;
    if (!payload || payload.returncode !== 0) {
      showToast("Injury staff setting could not be saved");
      render();
      return false;
    }
    if (payload.state) data = payload.state;
    if (payload.statePatch) applyStatePatch(payload.statePatch);
    state.lastResult = payload;
    state.injuryAutoManageChecked = Boolean(enabled);
    showToast(payload.summary?.message || "Injury staff setting updated");
    render();
    return true;
  }

  function handleDraftTradeResult(payload, params = {}) {
    const summary = payload?.summary || {};
    const status = String(summary.status || "").toLowerCase();
    const rejected = status === "warning" || /rejected/i.test(`${summary.title || ""} ${summary.message || ""} ${payload?.stdout || ""}`);
    const accepted = status === "success" || /accepted/i.test(`${summary.title || ""} ${summary.message || ""} ${payload?.stdout || ""}`);
    if (accepted) {
      state.draftTradeModal = null;
      return;
    }
    if (!rejected) return;
    const targetPickId = Number(params.target_pick_id || state.draftTradeModal?.targetPickId || 0);
    const existing = state.draftTradeModal || {};
    state.draftTradeModal = {
      ...existing,
      targetPickId,
      offerPickIds: (params.offer_pick_ids || existing.offerPickIds || []).map(Number).filter(Boolean),
      addPickId: "",
      status: "rejected",
      message: summary.message || "The other GM rejected that package. Add more draft capital and send it back.",
    };
  }

  function injuryModalContextForAction(action, params = {}, payload = {}) {
    const paused = /Simulation paused for injury review/i.test(String(payload.stdout || payload.summary?.message || ""));
    const gameType = String(params.game_type || data.season?.nextGameType || "REG").toUpperCase();
    if (action === "sim_week") {
      return {
        action,
        continueAction: "sim_week",
        continueParams: {
          game_type: gameType,
          advance_preflight_confirmed: true,
          roster_short_warning_confirmed: true,
        },
        paused,
      };
    }
    if (action === "sim_season") {
      return {
        action,
        continueAction: "sim_season",
        continueParams: {
          ...params,
          game_type: gameType,
          advance_preflight_confirmed: true,
          roster_short_warning_confirmed: true,
        },
        paused,
      };
    }
    return { action, continueAction: null, continueParams: {}, paused };
  }

  async function runAction(action, params) {
    if (!runnerMode() || state.runnerBusy) return;
    params = { ...(params || {}) };
    const setup = data.draftClassSetup || {};
    if (setup.required && !["draft_class_generate", "draft_class_import", "refresh", "load_game", "delete_save"].includes(action)) {
      if (isObserveMode()) {
        params.observe_auto_generate_draft_class = true;
      } else {
        showToast("Choose Generate or Import Draft Class before advancing.");
        render();
        return;
      }
    }
    if (!confirmBeforeAction(action, params)) return;
    if (queueSimAdvancePrompt(action, params)) return;
    if (queueRosterCutdownModePrompt(action, params)) return;
    const calendarProgressAction = SIM_PROGRESS_POLL_ACTIONS.has(action);
    const playoffProgressAction = action === "postseason_round";
    const keepLeagueTableLive = state.view === "season" || state.view === "playoffTree";
    if (playoffProgressAction && state.view !== "playoffTree") {
      switchView("playoffTree", { refresh: false });
    }
    if (calendarProgressAction && !keepLeagueTableLive && !playoffProgressAction) {
      state.calendarLiveFocus = true;
      switchView("calendar", { refresh: false });
    } else if (calendarProgressAction) {
      state.calendarLiveFocus = !playoffProgressAction;
    }
    state.runnerBusy = true;
    state.busyAction = action;
    state.cancelRequested = false;
    state.runnerStartedAt = Date.now();
    state.simProgressTick = 0;
    updateLiveStatus();
    showToast(`${actionLabel(action)} in progress...`);
    const renderBusyState = action !== "box_score";
    if (renderBusyState) render();
    const stopDraftProgressPolling = startDraftProgressPolling(action);
    const stopSimProgressPolling = startSimProgressPolling(action);
    try {
      await flushLocalScoutingSelections(action);
      const payload = await apiPost("runner", "/api/run", { action, params });
      if (!payload) throw new Error("Action request failed");
      payload.params = params;
      if (payload.state) {
        data = payload.state;
      }
      if (payload.statePatch) {
        applyStatePatch(payload.statePatch);
      }
      if (action === "roster_cutdown_apply" && payload.returncode === 0) {
        state.pendingCutdownMoves = {};
      }
      state.lastResult = payload;
      if (action === "draft_user_trade") {
        handleDraftTradeResult(payload, params);
      }
      if (!isObserveMode() && Array.isArray(payload.injuryAlerts) && payload.injuryAlerts.length) {
        state.injuryModal = payload.injuryAlerts;
        state.injuryModalContext = injuryModalContextForAction(action, params, payload);
      }
      if (!isObserveMode() && payload.rosterGate && rosterGateStillRelevant() && !suppressRosterGatePrompt(action)) {
        payload.returncode = 1;
        payload.rosterGate.key = payload.rosterGate.key || currentRosterGateKey();
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
      } else if (payload.rosterGate) {
        state.rosterCutdownPromptDismissedKey = currentRosterGateKey();
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
      state.runnerStartedAt = null;
      state.simProgressTick = 0;
      state.calendarLiveFocus = false;
      updateLiveStatus();
      render();
    }
  }

  function cancellableRunnerAction(action) {
    return SIM_PROGRESS_POLL_ACTIONS.has(action)
      || action === "draft_skip"
      || action === "draft_skip_to_user"
      || action === "draft_finish";
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

  function pendingFreeAgencyOfferKey(playerOrId) {
    const id = typeof playerOrId === "object" ? playerOrId?.player_id : playerOrId;
    return String(id || "");
  }

  function pendingFreeAgencyOffer(playerOrId) {
    const key = pendingFreeAgencyOfferKey(playerOrId);
    return key ? (state.pendingFreeAgencyOffers || {})[key] || null : null;
  }

  function pendingFreeAgencyOfferEntries() {
    return Object.values(state.pendingFreeAgencyOffers || {});
  }

  function setPendingFreeAgencyOffer(player, offer) {
    const key = pendingFreeAgencyOfferKey(player);
    if (!key || state.runnerBusy) return;
    state.pendingFreeAgencyOffers = { ...(state.pendingFreeAgencyOffers || {}) };
    state.pendingFreeAgencyOffers[key] = {
      player_id: Number(player.player_id),
      player_name: player.player_name || player.name || "Free agent",
      position: player.position || "",
      previous_team: player.previous_team || player.team || "FA",
      market_tier: player.market_tier || "",
      years: offer.years,
      aav: offer.aav,
      bonus: offer.bonus,
      guarantee_pct: offer.guarantee,
      structure: offer.structure,
    };
    showToast(`${player.player_name || "Free agent"} offer added to Pending Changes`);
    render();
  }

  function removePendingFreeAgencyOffer(playerOrId) {
    const key = pendingFreeAgencyOfferKey(playerOrId);
    if (!key || state.runnerBusy) return;
    state.pendingFreeAgencyOffers = { ...(state.pendingFreeAgencyOffers || {}) };
    delete state.pendingFreeAgencyOffers[key];
    render();
  }

  function clearPendingFreeAgencyOffers() {
    state.pendingFreeAgencyOffers = {};
  }

  function pendingRosterActionKey(playerOrId, action) {
    const id = typeof playerOrId === "object" ? playerOrId?.player_id : playerOrId;
    return `${action || ""}:${id || ""}`;
  }

  function pendingRosterAction(playerOrId, action) {
    const key = pendingRosterActionKey(playerOrId, action);
    return key ? (state.pendingRosterActions || {})[key] || null : null;
  }

  function pendingRosterActionEntries() {
    return Object.values(state.pendingRosterActions || {});
  }

  function rosterQueueLabel(action) {
    return {
      roster_release_player: "Release",
      roster_send_ir: "Send to IR",
      roster_activate_ir: "Activate from IR",
      practice_squad_promote: "Promote to active",
    }[action] || actionLabel(action);
  }

  function queueRosterAction(player, action, params = {}) {
    if (!player?.player_id || state.runnerBusy) return;
    const key = pendingRosterActionKey(player, action);
    state.pendingRosterActions = { ...(state.pendingRosterActions || {}) };
    state.pendingRosterActions[key] = {
      key,
      action,
      params: { ...params, player_id: Number(player.player_id) },
      player_id: Number(player.player_id),
      player_name: player.player_name || player.name || "Player",
      position: player.position || "",
      team: player.team || data.depthChart?.team || data.activeSave?.user_team || "",
      label: rosterQueueLabel(action),
    };
    showToast(`${player.player_name || "Player"} ${rosterQueueLabel(action).toLowerCase()} staged`);
    render();
  }

  function removePendingRosterAction(key) {
    if (!key || state.runnerBusy) return;
    state.pendingRosterActions = { ...(state.pendingRosterActions || {}) };
    delete state.pendingRosterActions[key];
    render();
  }

  function clearPendingRosterActions() {
    state.pendingRosterActions = {};
  }

  function pendingDepthActionEntries() {
    return Array.isArray(state.pendingDepthActions) ? state.pendingDepthActions : [];
  }

  function depthActionKey(action, params = {}) {
    if (action === "depth_chart_set") {
      return `${action}:${params.position || ""}:${params.rank || ""}`;
    }
    if (action === "depth_chart_swap") {
      return `${action}:${params.first_position || ""}:${params.first_rank || ""}:${params.second_position || ""}:${params.second_rank || ""}`;
    }
    if (action === "depth_chart_move") {
      return `${action}:${params.position || ""}:${params.player_id || ""}:${params.direction || ""}`;
    }
    return `${action}:${Date.now()}`;
  }

  function queueDepthAction(action, params = {}, meta = {}) {
    if (state.runnerBusy) return;
    const key = depthActionKey(action, params);
    const entry = {
      key,
      action,
      params: { ...params },
      title: meta.title || actionLabel(action),
      detail: meta.detail || "",
      tone: meta.tone || "",
    };
    const existing = pendingDepthActionEntries().filter((item) => item.key !== key);
    state.pendingDepthActions = [...existing, entry].slice(-24);
    showToast(`${entry.title} staged`);
    render();
  }

  function removePendingDepthAction(key) {
    if (!key || state.runnerBusy) return;
    state.pendingDepthActions = pendingDepthActionEntries().filter((item) => item.key !== key);
    render();
  }

  function clearPendingDepthActions() {
    state.pendingDepthActions = [];
  }

  function clonePlain(value) {
    if (typeof structuredClone === "function") {
      try {
        return structuredClone(value);
      } catch (_err) {
        // Fall through to JSON clone for plain exported state.
      }
    }
    return JSON.parse(JSON.stringify(value || {}));
  }

  function depthPlayerLookup(depth) {
    const map = new Map();
    (depth.roster || []).forEach((player) => {
      if (player?.player_id) map.set(String(player.player_id), player);
    });
    orderedDepthSlots(depth).forEach((slot) => {
      (slot.players || []).forEach((player) => {
        if (player?.player_id && !map.has(String(player.player_id))) map.set(String(player.player_id), player);
      });
    });
    return map;
  }

  function findProjectedDepthSlot(depth, slotName) {
    const target = String(slotName || "").toUpperCase();
    return orderedDepthSlots(depth).find((slot) => String(slot.slot || "").toUpperCase() === target) || null;
  }

  function normalizeProjectedSlot(slot, preserveOrder = false) {
    if (!slot) return;
    const seen = new Set();
    let players = [...(slot.players || [])]
      .filter((player) => {
        const id = String(player?.player_id || "");
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
      });
    if (!preserveOrder) players = players.sort((a, b) => Number(a.depth_rank || 99) - Number(b.depth_rank || 99));
    slot.players = players.map((player, index) => ({
      ...player,
      depth_rank: index + 1,
    }));
  }

  function projectedDepthPlayer(depth, playerLookup, playerId, rank) {
    const player = playerLookup.get(String(playerId));
    if (!player) return null;
    return {
      ...player,
      depth_rank: Number(rank || player.depth_rank || 1),
      _queuedProjection: true,
    };
  }

  function projectDepthSet(depth, action, playerLookup) {
    const slot = findProjectedDepthSlot(depth, action.params?.position);
    const rank = Math.max(1, Number(action.params?.rank || 1));
    const player = projectedDepthPlayer(depth, playerLookup, action.params?.player_id, rank);
    if (!slot || !player) return;
    const players = [...(slot.players || [])]
      .sort((a, b) => Number(a.depth_rank || 99) - Number(b.depth_rank || 99))
      .filter((item) => String(item.player_id) !== String(player.player_id));
    players.splice(rank - 1, 0, player);
    slot.players = players;
    slot._queuedProjection = true;
    normalizeProjectedSlot(slot, true);
  }

  function projectDepthMove(depth, action) {
    const slot = findProjectedDepthSlot(depth, action.params?.position);
    if (!slot) return;
    const players = [...(slot.players || [])].sort((a, b) => Number(a.depth_rank || 99) - Number(b.depth_rank || 99));
    const index = players.findIndex((player) => String(player.player_id) === String(action.params?.player_id));
    if (index < 0) return;
    const direction = String(action.params?.direction || "").toLowerCase();
    const target = direction === "up" ? index - 1 : index + 1;
    if (target < 0 || target >= players.length) return;
    [players[index], players[target]] = [players[target], players[index]];
    players[index] = { ...players[index], _queuedProjection: true };
    players[target] = { ...players[target], _queuedProjection: true };
    slot.players = players;
    slot._queuedProjection = true;
    normalizeProjectedSlot(slot, true);
  }

  function projectDepthSwap(depth, action) {
    const firstSlot = findProjectedDepthSlot(depth, action.params?.first_position);
    const secondSlot = findProjectedDepthSlot(depth, action.params?.second_position);
    if (!firstSlot || !secondSlot) return;
    const firstRank = Math.max(1, Number(action.params?.first_rank || 1));
    const secondRank = Math.max(1, Number(action.params?.second_rank || 1));
    const firstPlayers = [...(firstSlot.players || [])].sort((a, b) => Number(a.depth_rank || 99) - Number(b.depth_rank || 99));
    const secondPlayers = firstSlot === secondSlot ? firstPlayers : [...(secondSlot.players || [])].sort((a, b) => Number(a.depth_rank || 99) - Number(b.depth_rank || 99));
    const first = firstPlayers[firstRank - 1];
    const second = secondPlayers[secondRank - 1];
    if (!first || !second) return;
    firstPlayers[firstRank - 1] = { ...second, _queuedProjection: true };
    secondPlayers[secondRank - 1] = { ...first, _queuedProjection: true };
    firstSlot.players = firstPlayers;
    secondSlot.players = secondPlayers;
    firstSlot._queuedProjection = true;
    secondSlot._queuedProjection = true;
    normalizeProjectedSlot(firstSlot, true);
    if (secondSlot !== firstSlot) normalizeProjectedSlot(secondSlot, true);
  }

  function projectedDepthChart(depth) {
    const actions = pendingDepthActionEntries();
    if (!actions.length) return depth;
    const projected = clonePlain(depth || { rows: [], roster: [], units: [] });
    projected._hasQueuedProjection = true;
    const playerLookup = depthPlayerLookup(projected);
    actions.forEach((action) => {
      if (action.action === "depth_chart_set") projectDepthSet(projected, action, playerLookup);
      else if (action.action === "depth_chart_move") projectDepthMove(projected, action);
      else if (action.action === "depth_chart_swap") projectDepthSwap(projected, action);
    });
    return projected;
  }

  function queuedScoutingEntries() {
    const ids = localScoutingSelectionIds();
    if (!ids.length) return [];
    const prospectsById = new Map((data.scouting?.board || []).map((prospect) => [String(prospect.prospect_id), prospect]));
    return ids.map((id) => {
      const prospect = prospectsById.get(String(id)) || {};
      return {
        prospect_id: Number(id),
        player_name: prospect.player_name || `Prospect #${id}`,
        position: prospect.position || "",
        confidence: prospect.scouting_confidence || "",
      };
    });
  }

  function pendingActionQueueItems() {
    const items = [];
    pendingCutdownEntries().forEach((move) => {
      const label = pendingCutdownLabel(move).replace(/^Pending\s+/i, "");
      items.push({
        id: `cutdown:${move.player_id}`,
        type: "roster",
        title: move.player_name || `Player #${move.player_id}`,
        detail: `${label || "roster move"} queued for roster cutdown.`,
        tag: "Roster",
        tone: move.move === "release" || move.move === "release_ps" ? "warn" : "good",
        view: "practiceSquad",
        undo: () => {
          state.pendingCutdownMoves = { ...(state.pendingCutdownMoves || {}) };
          delete state.pendingCutdownMoves[String(move.player_id)];
        },
      });
    });
    pendingRosterActionEntries().forEach((action) => {
      items.push({
        id: `roster:${action.key}`,
        type: "roster",
        title: action.player_name || `Player #${action.player_id}`,
        detail: `${action.label || actionLabel(action.action)} queued for ${action.position || "roster"}${action.team ? ` | ${action.team}` : ""}.`,
        tag: "Roster",
        tone: action.action === "roster_release_player" ? "warn" : "good",
        view: "roster",
        undo: () => removePendingRosterAction(action.key),
      });
    });
    pendingDepthActionEntries().forEach((action) => {
      items.push({
        id: `depth:${action.key}`,
        type: "depth",
        title: action.title || actionLabel(action.action),
        detail: action.detail || "Depth chart change queued.",
        tag: "Depth",
        tone: action.tone || "",
        view: "depth",
        undo: () => removePendingDepthAction(action.key),
      });
    });
    queuedScoutingEntries().forEach((prospect) => {
      items.push({
        id: `scouting:${prospect.prospect_id}`,
        type: "scouting",
        title: prospect.player_name,
        detail: `${prospect.position || "Prospect"} selected for the next weekly scouting pass.`,
        tag: "Scouting",
        tone: "",
        view: "scouting",
        undo: () => {
          state.localScoutingSelections = ensureLocalScoutingSelections().filter((id) => String(id) !== String(prospect.prospect_id));
        },
      });
    });
    pendingFreeAgencyOfferEntries().forEach((offer) => {
      items.push({
        id: `fa:${offer.player_id}`,
        type: "freeAgency",
        title: offer.player_name || `Player #${offer.player_id}`,
        detail: `${offer.years || 1} yr ${money(offer.aav || 0)} AAV, ${String(offer.structure || "balanced").replaceAll("_", " ")}.`,
        tag: "Free Agency",
        tone: "warn",
        view: "freeAgency",
        undo: () => removePendingFreeAgencyOffer(offer.player_id),
      });
    });
    return items;
  }

  function pendingActionQueueCounts() {
    const counts = { total: 0, roster: 0, depth: 0, scouting: 0, freeAgency: 0 };
    pendingActionQueueItems().forEach((item) => {
      counts.total += 1;
      counts[item.type] = (counts[item.type] || 0) + 1;
    });
    return counts;
  }

  function pendingActionQueueWarnings() {
    const warnings = [];
    const rosterActionCounts = new Map();
    pendingRosterActionEntries().forEach((action) => {
      const id = String(action.player_id || "");
      if (!id) return;
      rosterActionCounts.set(id, (rosterActionCounts.get(id) || 0) + 1);
    });
    if ([...rosterActionCounts.values()].some((count) => count > 1)) {
      warnings.push({
        text: "One player has multiple roster actions queued. Undo one before applying.",
        tone: "bad",
        blocking: true,
      });
    }
    const scouting = data.scouting || {};
    const specificLimit = Number(scouting.actionLimits?.specific || scouting.weeklyWindow?.specificCount || 2);
    const specificUses = Number(scouting.actionUses?.specific || 0) + localScoutingSpecificCost(scouting.board || []);
    if (specificLimit > 0 && specificUses > specificLimit) {
      warnings.push({
        text: `Scouting selections are over the weekly limit (${specificUses}/${specificLimit}).`,
        tone: "bad",
        blocking: true,
      });
    }

    const queuedFa = pendingFreeAgencyOfferEntries();
    if (queuedFa.length) {
      const currentCap = data.contractNegotiations?.currentCap || data.contractNegotiations?.cap || {};
      const capSpace = Number(currentCap.cap_space || currentCap.capSpace || currentCap.available_cap || 0);
      const firstYearCommitment = queuedFa.reduce((total, offer) => total + Number(offer.aav || 0), 0);
      if (capSpace && capSpace - firstYearCommitment < 0) {
        warnings.push({
          text: `Queued FA offers project past current cap by ${money(Math.abs(capSpace - firstYearCommitment))}.`,
          tone: "warn",
          blocking: false,
        });
      }
    }

    const ps = data.practiceSquad || {};
    if (pendingCutdownEntries().length && (ps.candidates || []).length) {
      const projected = projectedCutdownState(ps);
      const limits = ps.limits || {};
      const activeLimit = Number(ps.activeLimit || limits.active || 53);
      const psLimit = Number(limits.total || 17);
      if (Number(projected.activeCount || 0) > activeLimit) {
        warnings.push({
          text: `Active roster still projects over the limit (${projected.activeCount}/${activeLimit}).`,
          tone: "warn",
          blocking: false,
        });
      }
      if (Number(projected.usage?.total || 0) > psLimit) {
        warnings.push({
          text: `Practice squad still projects over the limit (${projected.usage.total}/${psLimit}).`,
          tone: "warn",
          blocking: false,
        });
      }
    }
    return warnings;
  }

  function actionQueueApplyBlocker() {
    const blocking = pendingActionQueueWarnings().find((warning) => warning.blocking);
    if (blocking) return blocking.text;
    if (!runnerMode()) return "Actions unavailable right now.";
    if (state.runnerBusy) return "Another action is running.";
    return "";
  }

  function clearPendingActionQueue() {
    state.pendingCutdownMoves = {};
    state.localScoutingSelections = [];
    state.localScoutingKey = currentScoutingSelectionKey();
    clearPendingRosterActions();
    clearPendingDepthActions();
    clearPendingFreeAgencyOffers();
    render();
  }

  async function runPendingQueueMutation(action, params) {
    const payload = await apiPost("runner", "/api/run", { action, params });
    if (!payload) throw new Error(`${actionLabel(action)} could not be completed.`);
    payload.params = params;
    if (payload.state) data = payload.state;
    if (payload.statePatch) applyStatePatch(payload.statePatch);
    state.lastResult = payload;
    if (payload.returncode !== 0) {
      throw new Error(payload.summary?.message || payload.stderr || payload.error || `${actionLabel(action)} needs attention.`);
    }
    return payload;
  }

  async function refreshAfterPendingQueue(kinds) {
    const refreshes = [];
    if (kinds.has("roster")) {
      refreshes.push(loadLivePracticeSquad(), loadLiveDepthChart(), loadLiveWaivers(), loadLiveContracts());
    }
    if (kinds.has("depth")) {
      refreshes.push(loadLiveDepthChart(), loadLivePracticeSquad());
    }
    if (kinds.has("scouting")) {
      refreshes.push(loadLiveScouting(), loadLiveInbox());
    }
    if (kinds.has("freeAgency")) {
      refreshes.push(loadLiveFreeAgency(), loadLiveContracts(), loadLiveTransactions());
    }
    if (refreshes.length) await Promise.allSettled(refreshes);
  }

  async function applyPendingActionQueue() {
    const blocker = actionQueueApplyBlocker();
    const items = pendingActionQueueItems();
    if (!items.length) return;
    if (blocker) {
      showToast(blocker);
      render();
      return;
    }
    state.runnerBusy = true;
    state.busyAction = "pending_queue_apply";
    showToast(`Applying ${items.length} pending change${items.length === 1 ? "" : "s"}...`);
    render();
    const appliedKinds = new Set();
    let applied = 0;
    try {
      const cutdownMoves = pendingCutdownEntries();
      if (cutdownMoves.length) {
        await runPendingQueueMutation("roster_cutdown_apply", { moves: cutdownMoves });
        state.pendingCutdownMoves = {};
        applied += cutdownMoves.length;
        appliedKinds.add("roster");
      }

      for (const action of pendingRosterActionEntries()) {
        await runPendingQueueMutation(action.action, action.params || {});
        delete state.pendingRosterActions[action.key];
        applied += 1;
        appliedKinds.add("roster");
      }

      for (const action of pendingDepthActionEntries()) {
        await runPendingQueueMutation(action.action, action.params || {});
        state.pendingDepthActions = pendingDepthActionEntries().filter((item) => item.key !== action.key);
        applied += 1;
        appliedKinds.add("depth");
      }

      const prospectIds = localScoutingSelectionIds();
      if (prospectIds.length) {
        await runPendingQueueMutation("scouting_assign_batch", { prospect_ids: prospectIds });
        state.localScoutingSelections = [];
        state.localScoutingKey = currentScoutingSelectionKey();
        applied += prospectIds.length;
        appliedKinds.add("scouting");
      }

      for (const offer of pendingFreeAgencyOfferEntries()) {
        await runPendingQueueMutation("free_agency_offer", {
          player_id: offer.player_id,
          years: offer.years,
          aav: offer.aav,
          bonus: offer.bonus,
          guarantee_pct: offer.guarantee_pct,
          structure: offer.structure,
          cpu_response_offers: 2,
        });
        delete state.pendingFreeAgencyOffers[String(offer.player_id)];
        applied += 1;
        appliedKinds.add("freeAgency");
      }

      await refreshAfterPendingQueue(appliedKinds);
      showToast(`${applied} pending change${applied === 1 ? "" : "s"} applied`);
    } catch (error) {
      state.lastResult = { error: String(error) };
      showToast(String(error).replace(/^Error:\s*/, "") || "Pending changes could not be applied");
      await refreshAfterPendingQueue(appliedKinds);
    } finally {
      state.runnerBusy = false;
      state.busyAction = null;
      state.cancelRequested = false;
      render();
    }
  }

  function renderPendingActionQueuePanel() {
    const items = pendingActionQueueItems();
    if (!items.length) return null;
    const counts = pendingActionQueueCounts();
    const warnings = pendingActionQueueWarnings();
    const p = panel("Pending Changes", `${counts.total} staged`);
    p.classList.add("pending-queue-panel");
    const body = panelBody(p);
    const summary = node("div", "pending-queue-summary");
    [
      ["Roster", counts.roster],
      ["Depth", counts.depth],
      ["Scouting", counts.scouting],
      ["Free Agency", counts.freeAgency],
    ].filter(([, count]) => count > 0).forEach(([label, count]) => summary.append(tag(`${label} ${count}`, count > 0 ? "warn" : "")));
    if (warnings.length) {
      const warningList = node("div", "pending-queue-warnings");
      warnings.forEach((warning) => warningList.append(tag(warning.text, warning.tone || "warn")));
      summary.append(warningList);
    }
    body.append(summary);

    const list = node("div", "pending-queue-list");
    items.slice(0, 8).forEach((item) => {
      const row = node("button", `pending-queue-item ${item.tone ? `tone-${item.tone}` : ""}`.trim());
      row.type = "button";
      row.addEventListener("click", () => switchView(item.view || "today", { refresh: true }));
      const undo = node("button", "mini-action-button", "Undo");
      undo.type = "button";
      undo.addEventListener("click", (event) => {
        event.stopPropagation();
        item.undo();
        render();
      });
      append(row, [
        append(node("span", "pending-queue-copy"), [
          node("strong", null, item.title),
          node("small", null, item.detail),
        ]),
        tag(item.tag || "Queued", item.tone),
        undo,
      ]);
      list.append(row);
    });
    if (items.length > 8) {
      list.append(node("div", "pending-queue-more", `${items.length - 8} more staged change${items.length - 8 === 1 ? "" : "s"}.`));
    }
    body.append(list);

    const controls = node("div", "pending-queue-controls");
    const clear = node("button", "run-button compact ghost", "Clear All");
    clear.type = "button";
    clear.disabled = state.runnerBusy;
    clear.addEventListener("click", clearPendingActionQueue);
    const apply = node("button", "run-button compact good", state.runnerBusy && state.busyAction === "pending_queue_apply" ? "Applying" : "Apply All");
    apply.type = "button";
    const blocker = actionQueueApplyBlocker();
    apply.disabled = Boolean(blocker);
    if (blocker) apply.title = blocker;
    apply.addEventListener("click", applyPendingActionQueue);
    append(controls, [clear, apply]);
    body.append(controls);
    return p;
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

  function draftIsComplete(draft = data.draft || {}) {
    const totals = draft?.pickTotals || {};
    return Number(totals.total || 0) > 0 && Number(totals.remaining || 0) <= 0;
  }

  function draftRoomStatus(draft = data.draft || {}) {
    return String(draft?.state?.status || "").toLowerCase();
  }

  function draftRoomIsActive(draft = data.draft || {}) {
    const status = draftRoomStatus(draft);
    return Boolean(draft?.state) && !["complete", "completed"].includes(status);
  }

  function draftCanAdvanceToCurrentDraft(draft = data.draft || {}) {
    if (!draft?.draftDate) return false;
    if (draftIsComplete(draft)) return false;
    if (draftRoomIsActive(draft)) return false;
    return true;
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

  function calendarEventDateByCode(code) {
    const targetCode = String(code || "").toUpperCase();
    const calendar = data.calendar || {};
    const buckets = [
      calendar.upcomingEvents || [],
      calendar.eventsInView || [],
      ...(calendar.days || []).map((day) => day.events || []),
      data.events || [],
    ];
    for (const events of buckets) {
      const match = (events || []).find((event) => String(event.event_code || "").toUpperCase() === targetCode);
      if (match?.event_start_date) return String(match.event_start_date).slice(0, 10);
    }
    const fallbackDates = {
      FINAL_ROSTER_CUTDOWN_53: seasonDate("09-01"),
      PRACTICE_SQUADS_ESTABLISHED: seasonDate("09-02"),
      REGULAR_SEASON_KICKOFF: seasonDate("09-09"),
    };
    if (fallbackDates[targetCode]) return fallbackDates[targetCode];
    return "";
  }

  function firstUpcomingEventDate() {
    const events = data.calendar?.upcomingEvents || [];
    const match = events.find((event) => event?.event_start_date);
    return match ? String(match.event_start_date).slice(0, 10) : "";
  }

  function actionCrossesRosterCutdown(action, params = {}) {
    if (isObserveMode() || !data.activeSave?.user_team) return false;
    if (
      params.cpu_manage_user_team ||
      params.auto_roster_cutdown ||
      params.skip_roster_gate ||
      params.roster_cutdown_choice ||
      action === "auto_cutdown_continue"
    ) return false;
    const cutdownDate = calendarEventDateByCode("FINAL_ROSTER_CUTDOWN_53");
    const currentDate = String(data.currentDate || data.activeSave?.current_date || "").slice(0, 10);
    if (!cutdownDate || !currentDate || currentDate >= cutdownDate) return false;
    const gameType = String(params.game_type || data.season?.nextGameType || "REG").toUpperCase();
    if ((action === "sim_week" || action === "sim_season") && gameType === "REG") return true;
    if (action === "advance_to_draft" || action === "advance_next_league_year") return true;
    if (action === "advance_to_date") {
      const targetDate = String(params.date || "").slice(0, 10);
      return Boolean(targetDate && targetDate >= cutdownDate);
    }
    if (action === "advance_next_event") {
      const targetDate = firstUpcomingEventDate();
      return Boolean(targetDate && targetDate >= cutdownDate);
    }
    return false;
  }

  function canOfferUserCpuManagement(action, params = {}) {
    if (isObserveMode() || !data.activeSave?.user_team) return false;
    if (params.cpu_manage_user_team || params.cpu_management_confirmed) return false;
    if (!USER_CPU_MANAGED_SIM_ACTIONS.has(action)) return false;
    if (action === "sim_week") return false;
    if (actionCrossesRosterCutdown(action, params)) return true;
    if (["advance_to_draft", "advance_next_league_year", "complete_season"].includes(action)) return true;
    if (action === "sim_season") return true;
    if (action === "advance_to_date" || action === "advance_next_event") {
      return actionReachesRegularSeason(action, params);
    }
    return false;
  }

  function userCpuManagementParams(params = {}) {
    return {
      ...params,
      cpu_manage_user_team: true,
      cpu_management_confirmed: true,
      advance_preflight_confirmed: true,
      roster_short_warning_confirmed: true,
      auto_roster_cutdown: true,
      skip_roster_gate: true,
      injury_auto_manage: true,
      roster_cutdown_choice: "auto",
    };
  }

  function actionReachesRegularSeason(action, params = {}) {
    if (isObserveMode() || !data.activeSave?.user_team) return false;
    const kickoffDate = calendarEventDateByCode("REGULAR_SEASON_KICKOFF");
    const currentDate = String(data.currentDate || data.activeSave?.current_date || "").slice(0, 10);
    const gameType = String(params.game_type || data.season?.nextGameType || "REG").toUpperCase();
    if ((action === "sim_week" || action === "sim_season") && gameType === "REG") return true;
    if (action === "advance_to_draft" || action === "advance_next_league_year") return true;
    if (!kickoffDate || !currentDate) return false;
    if (currentDate >= kickoffDate) return false;
    if (action === "advance_to_date") {
      const targetDate = String(params.date || "").slice(0, 10);
      return Boolean(targetDate && targetDate >= kickoffDate);
    }
    if (action === "advance_next_event") {
      const targetDate = firstUpcomingEventDate();
      return Boolean(targetDate && targetDate >= kickoffDate);
    }
    return false;
  }

  function rosterShortfallWarningsForAdvance(action, params = {}) {
    if (params.roster_short_warning_confirmed || params.auto_roster_cutdown) return [];
    if (!actionReachesRegularSeason(action, params)) return [];
    const currentDate = String(data.currentDate || data.activeSave?.current_date || "").slice(0, 10);
    const cutdownDate = calendarEventDateByCode("FINAL_ROSTER_CUTDOWN_53");
    const kickoffDate = calendarEventDateByCode("REGULAR_SEASON_KICKOFF");
    if (!currentDate || !cutdownDate || !kickoffDate || currentDate < cutdownDate || currentDate > kickoffDate) return [];
    const counts = rosterGateCountsFromState();
    if (!counts) return [];
    const warnings = [];
    const activeLimit = 53;
    const practiceSquadLimit = 16;
    if (counts.activeCount < activeLimit) {
      warnings.push(`Active roster is short: ${counts.activeCount}/${activeLimit}. You can continue, but injuries or specialist depth may force emergency signings later.`);
    }
    if (counts.practiceSquadCount < practiceSquadLimit) {
      warnings.push(`Practice squad is not full: ${counts.practiceSquadCount}/${practiceSquadLimit}. You can continue and fill it later.`);
    }
    return warnings;
  }

  function queueRosterCutdownModePrompt(action, params = {}) {
    if (!actionCrossesRosterCutdown(action, params)) return false;
    state.pendingRosterCutdownAction = {
      action,
      params: { ...params },
      cutdownDate: calendarEventDateByCode("FINAL_ROSTER_CUTDOWN_53"),
      practiceSquadDate: calendarEventDateByCode("PRACTICE_SQUADS_ESTABLISHED"),
    };
    render();
    return true;
  }

  function simAdvancePromptDetails(action, params = {}) {
    if (params.advance_preflight_confirmed) return null;
    const season = data.season || {};
    const draft = data.draft || {};
    const gameType = String(params.game_type || season.nextGameType || "REG").toUpperCase();
    const draftRemaining = Number(draft.pickTotals?.remaining || 0);
    const currentDate = String(data.currentDate || data.activeSave?.current_date || "").slice(0, 10);
    const warnings = [];
    const checkpoints = [];
    let title = "";
    let detail = "";
    let primaryLabel = "Continue";
    let tone = "warn";
    const rosterShortfallWarnings = rosterShortfallWarningsForAdvance(action, params);
    const offerCpuManagement = canOfferUserCpuManagement(action, params);

    if (draftNeedsAdvanceWarning(action)) {
      title = "Draft Still In Progress";
      detail = `The ${draft.year || ""} draft still has ${draftRemaining} pick(s) remaining. Continuing will auto-sim the rest of the draft, including any user-team picks still open.`;
      primaryLabel = "Auto Finish Draft And Continue";
      warnings.push("Remaining draft picks will be selected automatically.");
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone, offerCpuManagement };
    }

    if (action === "advance_to_draft") {
      title = "Sim To Draft";
      detail = `This will fast-forward from ${shortDate(currentDate)} to the ${draft.year || activeSeasonYear() + 1} NFL Draft${draft.draftDate ? ` on ${shortDate(draft.draftDate)}` : ""}.`;
      primaryLabel = "Sim To Draft";
      const regularRemaining = Number(season.totals?.remaining || 0);
      const postseasonRemaining = Number(season.postseason?.remaining || 0);
      if (regularRemaining > 0) checkpoints.push(`Complete ${regularRemaining} regular-season game(s).`);
      if (postseasonRemaining > 0 || Number(season.postseason?.games || 0) === 0) checkpoints.push("Finalize playoff results and draft order if needed.");
      checkpoints.push("Resolve offseason free agency movement up to draft day.");
      checkpoints.push("Run pre-draft scouting sweep and open the draft room paused.");
      if (actionCrossesRosterCutdown(action, params)) warnings.push("This crosses roster cutdown; you will choose auto cutdown or pause before the sim starts.");
      warnings.push(...rosterShortfallWarnings);
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone, offerCpuManagement };
    }

    if (action === "sim_week" && gameType === "REG" && rosterShortfallWarnings.length) {
      title = "Roster Is Short";
      detail = "Your roster is under the normal regular-season target. You can continue anyway, but you may want to fill active or practice squad spots first.";
      primaryLabel = "Continue Anyway";
      warnings.push(...rosterShortfallWarnings);
      checkpoints.push("Sim the next regular-season week.");
      checkpoints.push("Emergency specialist checks can still run before games.");
      return {
        action,
        params: { ...params, roster_short_warning_confirmed: true },
        title,
        detail,
        primaryLabel,
        warnings,
        checkpoints,
        tone,
        offerCpuManagement,
      };
    }

    if (action === "sim_season") {
      title = gameType === "PRE" ? "Sim Rest Of Preseason" : "Sim Rest Of Season";
      detail = gameType === "PRE"
        ? "This will play all remaining preseason games and keep regular-season roster deadlines intact."
        : "This will play the remaining regular-season schedule and update stats, scouting, injuries, standings, and CPU front-office activity.";
      primaryLabel = gameType === "PRE" ? "Sim Preseason" : "Sim Season";
      if (gameType === "REG" && actionCrossesRosterCutdown(action, params)) warnings.push("This crosses roster cutdown; you will choose auto cutdown or pause before games begin.");
      if (gameType === "REG") warnings.push(...rosterShortfallWarnings);
      if (gameType === "REG" && actionCrossesRosterCutdown(action, params)) {
        checkpoints.push("Stop at roster cutdown unless you choose automatic cutdown.");
      }
      if (gameType === "REG" && rosterShortfallWarnings.length) {
        checkpoints.push("Continue with the roster below the normal 53/16 targets.");
      }
      checkpoints.push("Update calendar, standings, stats, injuries, inbox, and league news as games finish.");
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone, offerCpuManagement };
    }

    if (action === "advance_next_league_year") {
      title = "Advance To Next League Year";
      detail = "This moves the league to the next June 1 and processes the remaining post-draft offseason events.";
      primaryLabel = "Advance League Year";
      if (draftRemaining > 0) warnings.push(`The draft still has ${draftRemaining} remaining pick(s); advancing may auto-finish them.`);
      warnings.push(...rosterShortfallWarnings);
      checkpoints.push("Sync calendar phase, league year, rosters, contracts, and generated offseason setup.");
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone, offerCpuManagement };
    }

    if (action === "postseason") {
      const remaining = Number(season.postseason?.remaining || 0);
      title = "Run Postseason";
      detail = remaining
        ? `This will sim the remaining ${remaining} playoff game(s).`
        : "This will generate and sim the playoff bracket from the completed regular season.";
      primaryLabel = "Run Postseason";
      checkpoints.push("Update playoff bracket, box scores, league news, stats, and season state.");
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone: "good" };
    }

    if (action === "complete_season") {
      title = "Complete Season";
      detail = "This finalizes the season and moves the league into the next offseason flow.";
      primaryLabel = "Complete Season";
      checkpoints.push("Finalize draft order and playoff results.");
      checkpoints.push("Apply progression/regression when eligible.");
      checkpoints.push("Prepare contracts, free agency, calendar state, and next offseason checkpoints.");
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone: "good", offerCpuManagement };
    }

    if (action === "advance_to_date" || action === "advance_next_event") {
      const targetDate = action === "advance_to_date" ? String(params.date || "").slice(0, 10) : firstUpcomingEventDate();
      const targetLabel = action === "advance_next_event" ? (data.calendar?.nextEvent?.event_name || "next calendar event") : shortDate(targetDate);
      if (!offerCpuManagement && !actionCrossesRosterCutdown(action, params) && !draftNeedsAdvanceWarning(action) && !rosterShortfallWarnings.length) return null;
      title = action === "advance_next_event" ? "Advance To Next Event" : "Advance Calendar";
      detail = `This will advance from ${shortDate(currentDate)} to ${targetLabel}.`;
      primaryLabel = rosterShortfallWarnings.length ? "Continue Anyway" : "Advance";
      if (actionCrossesRosterCutdown(action, params)) warnings.push("This crosses roster cutdown; you will choose auto cutdown or pause before advancing.");
      warnings.push(...rosterShortfallWarnings);
      if (!checkpoints.length) checkpoints.push("Process calendar events due before the target date.");
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone, offerCpuManagement };
    }

    if (offerCpuManagement) {
      title = actionLabel(action);
      detail = "This will advance the save and process league activity. You can run it normally or let CPU staff manage your team through user-decision gates during this sim.";
      primaryLabel = "Run Normally";
      checkpoints.push("Process the selected sim action and refresh affected screens.");
      return { action, params: { ...params }, title, detail, primaryLabel, warnings, checkpoints, tone: "good", offerCpuManagement };
    }

    return null;
  }

  function queueSimAdvancePrompt(action, params = {}) {
    if (isObserveMode()) return false;
    const prompt = simAdvancePromptDetails(action, params);
    if (!prompt) return false;
    state.pendingSimAdvancePrompt = prompt;
    render();
    return true;
  }

  function confirmBeforeAction(action, params = {}) {
    if (action === "ai_gm_review_apply" && params.apply) {
      return window.confirm(
        "This will apply approved CPU front-office decision(s) and may change rosters, contracts, offers, cap, or transactions.\n\nContinue?",
      );
    }
    if (action === "roster_release_player") {
      return window.confirm("Release this player from your active roster?\n\nContinue?");
    }
    if (action === "roster_send_ir") {
      return window.confirm("Place this injured player on IR?\n\nThey will no longer count against the active roster. Return eligibility depends on NFL IR rules and timing.");
    }
    if (action === "roster_activate_ir") {
      return window.confirm("Activate this player from IR?\n\nYou need an open active roster spot.");
    }
    if (action === "practice_squad_promote") {
      return window.confirm("Promote this player from the practice squad to the active roster?\n\nContinue?");
    }
    if (action === "roster_cutdown_apply") {
      const count = Array.isArray(params.moves) ? params.moves.length : 0;
      return window.confirm(`Apply ${count} selected roster cutdown move${count === 1 ? "" : "s"}?\n\nPlayers marked for waive/release will enter the normal release and waiver flow.`);
    }
    return true;
  }

  function actionLabel(action) {
    return {
      sim_season: "Sim Rest Of Regular Season",
      sim_week: "Sim Week",
      complete_season: "Complete Season",
      contract_extend: "Extend Player",
      contract_tag: "Apply Tag",
      contract_option_exercise: "Exercise Option",
      contract_option_decline: "Decline Option",
      contract_release: "Release Player",
      contract_restructure: "Restructure Contract",
      roster_release_player: "Release Player",
      roster_send_ir: "Send To IR",
      roster_activate_ir: "Activate From IR",
      pending_queue_apply: "Apply Pending Changes",
      roster_cutdown_apply: "Apply Roster Cutdown",
      roster_change_number: "Change Number",
      practice_squad_assign: "Assign Practice Squad",
      practice_squad_promote: "Promote To Active Roster",
      practice_squad_release: "Release Practice Squad Player",
      auto_cutdown: "Auto Cutdown",
      auto_cutdown_continue: "Auto Cutdown And Continue",
      depth_chart_set: "Set Depth Chart",
      depth_chart_move: "Move Depth Chart",
      depth_chart_swap: "Swap Depth Chart",
      postseason: "Run Postseason",
      postseason_round: "Sim Playoff Round",
      validate_rosters: "Validate Rosters",
      advance_next_event: "Advance To Next Date",
      advance_to_date: "Advance To Date",
      box_score: "Show Box Score",
      advance_to_draft: "Advance To Draft",
      free_agency_start: "Advance To Free Agency",
      free_agency_offer: "Submit FA Offer",
      free_agency_cpu_seed: "Generate Team Offers",
      free_agency_advance_hour: "Advance FA Hour",
      free_agency_advance_day: "Advance FA Day",
      waiver_claim: "Submit Waiver Claim",
      waiver_cpu_seed: "Review League Claims",
      waiver_process: "Process Waivers",
      trade_submit: "Submit Trade",
      trade_cpu_market: "Open Trade Market",
      draft_skip: "Skip Draft Pick",
      draft_skip_to_user: "Skip To User Pick",
      draft_finish: "Finish Draft",
      draft_class_generate: "Generate Draft Class",
      draft_class_import: "Import Draft Class",
      draft_pick: "Make Draft Pick",
      draft_user_trade: "Trade For Draft Pick",
      draft_start: "Start Draft Room",
      ai_gm_setup: "Prepare Front Offices",
      ai_gm_enable_ollama: "Connect Local GM Engine",
      ai_gm_show_config: "Show Front Office Settings",
      ai_gm_autonomy_show: "Show Management Settings",
      ai_gm_autonomy_config: "Set Management Level",
      ai_gm_daily_run: "Run Front Office Check",
      ai_gm_review_inbox: "Show Front Office Inbox",
      ai_gm_review_history: "Show Decision History",
      ai_gm_review_show: "Show Decision",
      ai_gm_review_update: "Update Decision",
      ai_gm_review_apply: "Apply Decision",
      ai_gm_profiles: "Show GM Profile",
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
      ai_gm_ops: "Review Front Office Needs",
      ai_gm_queue: "Show Decision Queue",
      ai_gm_process_queue: "Process Decision Queue",
      ai_gm_context: "Build Decision Brief",
      ai_gm_run: "Run GM Decision",
      ai_gm_logs: "Show Decision History",
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
      event_generate_week: "Refresh Weekly Stories",
      new_june1_save: "Start Fresh June 1 Save",
      take_over_team: "Take Over Team",
      status: "Refresh Status",
      preflight: "League Health Check",
      advance_next_league_year: "Advance To Next League Year",
      refresh: "Refresh Screen",
    }[action] || action || "Action";
  }

  function busyMessage() {
    const action = state.busyAction || "command";
    const label = actionLabel(action);
    const extra = action === "sim_season"
      ? "\n\nFull-season sims can take a few minutes. The league is playing every remaining game and updating standings, stats, scouting, roster checks, and front-office activity."
      : "\n\nThe league office will update the affected screens when this finishes.";
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
      children.push(node("span", "muted", "Actions unavailable right now"));
    }
    append(top, children);
    const content = [top];
    if (options.detail) content.push(node("span", "muted", options.detail));
    append(box, content);
    return box;
  }

  function draftActionAvailability(action, draft, selected) {
    const draftState = draftRoomIsActive(draft) ? draft?.state : null;
    const remaining = Number(draft?.pickTotals?.remaining || 0);
    if (isObserveMode() && ["draft_skip_to_user", "draft_pick", "draft_user_trade"].includes(action)) {
      return { disabledReason: "Observe Mode has no user-controlled draft pick." };
    }
    if (action === "advance_to_draft") {
      if (draftIsComplete(draft)) return { disabledReason: "Draft is complete." };
      if (draftState) return { disabledReason: "Draft room is already active." };
      if (!draft?.draftDate) return { disabledReason: "No draft date is available." };
      return {};
    }
    if (action === "draft_start") {
      if (draftIsComplete(draft)) return { disabledReason: "Draft is complete." };
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
    if (action === "draft_user_trade") {
      if (!draftState) return { disabledReason: "Start the draft room first." };
      if (remaining <= 0) return { disabledReason: "Draft is complete." };
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
      detail: availability.disabledReason || "The draft room updates after the action finishes.",
    });
  }

  function liveCommandBox(label, command, action, params = {}, detail = "") {
    return commandBox(label, command, action, params, {
      hideCommand: true,
      detail: detail || "The screen updates after it finishes.",
    });
  }

  function controlButton({ label, action, params = {}, availability = {}, tone = "", className = "" }) {
    const classes = `control-button ${className || ""} ${tone || ""}`.trim();
    const button = node("button", classes, state.runnerBusy && state.busyAction === action ? "Running" : label);
    button.type = "button";
    button.disabled = state.runnerBusy || Boolean(availability.disabledReason) || !runnerMode();
    button.title = availability.disabledReason || (runnerMode() ? actionLabel(action) : "Actions unavailable right now");
    button.addEventListener("click", () => runAction(action, params || {}));
    return button;
  }

  function controlDisabledReasons(entries) {
    if (!runnerMode()) return ["Actions unavailable right now."];
    const reasons = entries
      .map((entry) => entry?.availability?.disabledReason)
      .filter(Boolean);
    return [...new Set(reasons)].slice(0, 3);
  }

  function controlMetaLine({ generatedAt: _generatedAt, reasons = [], fallback = "" } = {}) {
    const wrap = node("div", "control-meta-line");
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
    const draftState = draftRoomIsActive(draft) ? draft?.state : null;
    const currentTeam = draftState?.current_team || "-";
    const userTeam = draftState?.user_team || data.activeSave?.user_team || (isObserveMode() ? "CPU" : "User");
    const currentPick = draftState?.current_pick_number ? `#${draftState.current_pick_number}` : "-";
    const remaining = Number(draft?.pickTotals?.remaining || 0);
    const onClockTone = isUserOnClock() ? "good" : draftState ? "warn" : "";
    const complete = draftIsComplete(draft);
    const p = panel("Draft Control", complete ? "Complete" : draftState ? `${currentTeam} on clock` : "Setup");
    const body = panelBody(p);
    const hero = node("div", "control-hero draft-control-hero");
    append(hero, [
      teamLogo(currentDraftQueuePick(draft, draftState)?.teamLogo, currentTeam, "draft-control-logo"),
      append(node("div", "control-copy draft-control-copy"), [
        node("span", "tag", draftState ? `Pick ${currentPick}` : `Draft ${draft?.year || ""}`),
        node("strong", null, complete ? "Draft is complete" : draftState ? `${currentTeam} is on the clock` : dateReached(draft?.draftDate) ? "Draft room is ready" : `Draft date ${shortDate(draft?.draftDate)}`),
        node("small", null, draftState
          ? `${remaining} pick(s) remaining. ${isObserveMode() ? "Observe or sim CPU selections." : isUserOnClock() ? `${userTeam} can submit a pick now.` : "Skip CPU picks until your team is up."}`
          : complete ? "Advance the calendar to continue the offseason." : dateReached(draft?.draftDate) ? "Start the room paused before making selections." : "Advance the calendar when you are ready."),
      ]),
      tag(complete ? "Complete" : isObserveMode() && draftState ? "Observe" : isUserOnClock() ? "Your Pick" : draftState ? "CPU Pick" : "Not Started", complete ? "good" : onClockTone),
    ]);
    const controls = node("div", "control-bar draft-control-bar");
    const controlEntries = [
      [dateReached(draft?.draftDate) ? "Start Draft" : "Sim To Draft", "advance_to_draft", {}, "good"],
      ["Start Room", "draft_start", {}, "good"],
      ["Skip Pick", "draft_skip", { count: 1 }, ""],
      ...(isObserveMode() ? [] : [
        [`Skip To ${userTeam}`, "draft_skip_to_user", {}, ""],
        ["Make Pick", "draft_pick", selected?.prospect_id ? { prospect_id: selected.prospect_id } : {}, "good"],
      ]),
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
    const draftPause = draftSimPauseButton();
    if (draftPause) controls.append(draftPause);
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

  function draftSimPauseButton() {
    if (!state.runnerBusy || !["draft_skip", "draft_skip_to_user", "draft_finish"].includes(state.busyAction)) return null;
    const button = node("button", "control-button draft-control-button warn", state.cancelRequested ? "Pause Requested" : "Pause Draft Sim");
    button.type = "button";
    button.disabled = state.cancelRequested;
    button.title = "Pause after the current draft pick finishes.";
    button.addEventListener("click", requestRunnerCancel);
    return button;
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
      controls.append(node("span", "muted", "Actions unavailable right now"));
    }
    append(card, [text, controls]);
    return card;
  }

  function compactRunButton(label, action, params, tone) {
    const button = node("button", `run-button compact ${tone || ""}`.trim(), state.runnerBusy ? "Running" : label);
    button.type = "button";
    button.disabled = state.runnerBusy || !runnerMode();
    if (!runnerMode()) button.title = "Actions unavailable right now";
    button.addEventListener("click", () => runAction(action, params));
    return button;
  }

  function queueableRosterAction(action) {
    return new Set([
      "roster_release_player",
      "roster_send_ir",
      "roster_activate_ir",
      "practice_squad_promote",
    ]).has(action);
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
      if (!runnerMode()) reject.title = "Actions unavailable right now";
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
    details.append(node("summary", null, "Full details"));
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
      body.append(node("div", "empty-state", "No front-office review item is selected."));
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
      node("strong", null, item.title || item.summary || "Front office review item"),
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
    controls.append(compactRunButton("Show Details", "ai_gm_review_show", { review_id: Number(item.review_id) }));
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

    if (draftCanAdvanceToCurrentDraft(data.draft)) {
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
    }
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
      if (draftIsComplete(data.draft)) return { disabledReason: "Draft is complete." };
      if (draftRoomIsActive(data.draft)) return { disabledReason: "Draft room is already active." };
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
    ];
    if (draftCanAdvanceToCurrentDraft(data.draft)) {
      controlEntries.push(["Advance To Draft", "advance_to_draft", {}, "warn"]);
    }
    const mappedControlEntries = controlEntries.map(([label, action, params, tone]) => ({
      label,
      action,
      params,
      tone,
      availability: freeAgencyActionAvailability(action, fa),
    }));
    append(controls, [
      ...mappedControlEntries.map((entry) => controlButton({ ...entry, className: "fa-control-button" })),
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
        reasons: controlDisabledReasons(mappedControlEntries),
      }),
      secondary,
    ]);
    return p;
  }

  function seasonPhaseState(season) {
    const preseasonRemaining = Number(season?.preseasonTotals?.remaining || 0);
    const totalGames = Number(season?.totals?.games || 0);
    const remaining = Number(season?.totals?.remaining || 0);
    const nextWeek = Number(season?.nextWeek || 0);
    const postseasonGames = Number(season?.postseason?.games || 0);
    const postseasonRemaining = Number(season?.postseason?.remaining || 0);
    const regularStarted = totalGames > 0;
    const regularDone = regularStarted && remaining === 0;
    const playoffsDone = regularDone && postseasonGames > 0 && postseasonRemaining === 0;
    if (preseasonRemaining > 0 && String(season?.nextGameType || "").toUpperCase() === "PRE") return "preseason";
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
      if (!Number(season?.nextWeek || 0)) return { disabledReason: "No scheduled week is queued." };
      return {};
    }
    if (action === "sim_season") {
      if (phase !== "preseason" && phase !== "regular" && phase !== "postseason_setup") return { disabledReason: "Season schedule is already complete." };
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
    const nextGameType = String(season?.nextGameType || "REG").toUpperCase();
    const nextWeekLabel = nextGameType === "PRE" ? `Preseason Week ${nextWeek}` : `Week ${nextWeek}`;
    const remaining = Number(season?.totals?.remaining || 0);
    const preseasonRemaining = Number(season?.preseasonTotals?.remaining || 0);
    const postseasonRemaining = Number(season?.postseason?.remaining || 0);
    const title = {
      preseason: nextWeek ? `${nextWeekLabel} Ready` : "Preseason",
      regular: nextWeek ? `Week ${nextWeek} Ready` : "Regular Season",
      postseason_setup: "Playoff Tree Ready",
      postseason: "Postseason Ready",
      completion: "Season Completion Ready",
      completed: "Season Complete",
      idle: "Season Idle",
    }[phase] || "Season";
    const detail = {
      preseason: `${preseasonRemaining} preseason game(s) remaining.`,
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
      [nextGameType === "PRE" ? "Sim Preseason Week" : "Sim Next Week", "sim_week", { week: nextWeek, game_type: nextGameType }, "good"],
      [nextGameType === "PRE" ? "Sim Rest Preseason" : "Sim Regular Season", "sim_season", { game_type: nextGameType }, "warn"],
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
      node("span", "muted", "Full regular-season sims can take a few minutes because stats, scouting, injuries, and staff updates run after games."),
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
      return { disabledReason: "No scheduled week is queued." };
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
    const nextGameType = String(data.season?.nextGameType || "REG").toUpperCase();
    const nextWeekLabel = nextWeek ? (nextGameType === "PRE" ? `Preseason Week ${nextWeek}` : `Week ${nextWeek}`) : "";
    const eventDate = nextEvent?.event_start_date ? shortDate(nextEvent.event_start_date) : "No date";
    const p = panel("Calendar Control", nextEvent ? "Next Step" : "No Advance Target");
    const body = panelBody(p);
    const hero = node("div", "control-hero calendar-control-hero");
    append(hero, [
      append(node("div", "control-copy calendar-control-copy"), [
        node("span", "tag", data.currentDate ? `Current ${shortDate(data.currentDate)}` : "Calendar"),
        node("strong", null, nextEvent?.event_name || (nextWeek ? `${nextWeekLabel} Ready` : "No immediate calendar action")),
        node("small", null, nextEvent
          ? `${eventDate}${nextEvent.phase_name ? ` | ${nextEvent.phase_name}` : ""}${nextEvent.notes ? ` | ${nextEvent.notes}` : ""}`
          : nextWeek ? `Sim the next unfinished ${nextGameType === "PRE" ? "preseason" : "regular-season"} week.` : "The active save has no exported next event or week."),
      ]),
      tag("Calendar", "good"),
    ]);
    const controls = node("div", "control-bar calendar-control-bar");
    const seasonPhase = seasonPhaseState(data.season || {});
    const controlEntries = [
      ["Advance Date", "advance_next_event", {}, "good"],
    ];
    if (["preseason", "regular", "postseason_setup"].includes(seasonPhase)) {
      controlEntries.push([nextGameType === "PRE" ? "Sim Rest Preseason" : "Sim Rest Season", "sim_season", { game_type: nextGameType }, "warn"]);
    }
    if (nextWeek && ["preseason", "regular"].includes(seasonPhase)) {
      controlEntries.push([`Sim ${nextWeekLabel}`, "sim_week", { week: nextWeek, game_type: nextGameType }, "good"]);
    }
    if (draftIsComplete(data.draft) && data.currentDate && data.draft?.year && data.currentDate < `${data.draft.year}-06-01`) {
      controlEntries.push([
        "Next League Year",
        "advance_next_league_year",
        {},
        "good",
      ]);
    } else if (draftCanAdvanceToCurrentDraft(data.draft) && String(data.currentPhase || "").toLowerCase().includes("offseason")) {
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
        milestoneStrip.append(calendarMilestoneButton(event));
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
    const draftState = draftRoomIsActive(draft) ? draft.state : null;
    const remaining = Number(draft.pickTotals?.remaining || 0);
    const cards = [];

    if (remaining <= 0 && Number(draft.pickTotals?.total || 0) > 0) {
      cards.push(actionCard(
        "Advance To Next League Year",
        "All picks have been recorded. Jump to June 1, process post-draft offseason events, and generate the next draft class.",
        commands.advanceNextLeagueYear,
        "advance_next_league_year",
        {},
        "good",
        { runLabel: "Advance To June 1" },
      ));
      return cards;
    }

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
      const p = panel("League Office", actionLabel(state.busyAction));
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
      body.append(renderBoxScore(result, { compact: true }));
      return p;
    }
    const p = panel("League Office", result.summary?.title || actionLabel(result.action) || "Latest");
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
      summary?.affectedPanels?.length ? metric("Updated", summary.affectedPanels.join(", "), "Refreshed sections") : null,
      summary?.durationSeconds !== undefined ? metric("Time", `${summary.durationSeconds}s`, "Elapsed") : null,
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
        cancellable && String(state.busyAction || "").startsWith("draft_")
          ? "Keep this page open. Pause stops the draft after the current pick."
          : cancellable
          ? "Keep this page open. Stop safely pauses after the current game or weekly update finishes."
          : "Keep this page open. The affected screens will update when the action finishes."
      ),
    ]);
    const stop = cancellable ? node(
      "button",
      "runner-cancel-button",
      state.cancelRequested
        ? (String(state.busyAction || "").startsWith("draft_") ? "Pause requested" : "Stop requested")
        : (String(state.busyAction || "").startsWith("draft_") ? "Pause draft" : "Stop safely")
    ) : null;
    if (stop) {
      stop.type = "button";
      stop.disabled = state.cancelRequested;
      stop.addEventListener("click", requestRunnerCancel);
    }
    append(banner, [node("span", "spinner"), text, stop]);
    return banner;
  }

  function simProgressOverlay() {
    if (!state.runnerBusy) return null;
    const model = simProgressModel();
    const overlay = node("aside", "sim-progress-overlay");
    overlay.setAttribute("aria-live", "polite");
    const top = node("div", "sim-progress-top");
    append(top, [
      append(node("div", "sim-progress-heading"), [
        node("span", "spinner small"),
        append(node("div"), [
          node("strong", null, model.title),
          node("span", null, model.step),
        ]),
      ]),
      simProgressStopButton(),
    ]);
    const bar = node("div", `sim-progress-bar ${model.percent === null ? "indeterminate" : ""}`.trim());
    const fill = node("span");
    if (model.percent !== null) fill.style.width = `${Math.max(4, Math.min(100, model.percent))}%`;
    bar.append(fill);
    const facts = node("div", "sim-progress-facts");
    append(facts, [
      simProgressFact("Date", currentDateDisplay()),
      simProgressFact("Elapsed", model.elapsed),
      model.progressLabel ? simProgressFact("Progress", model.progressLabel) : null,
    ]);
    const activity = simProgressActivityList();
    append(overlay, [
      top,
      bar,
      facts,
      activity,
    ]);
    return overlay;
  }

  function simProgressStopButton() {
    const cancellable = cancellableRunnerAction(state.busyAction);
    if (!cancellable) return node("span", "sim-progress-lock", "Updating");
    const label = state.cancelRequested
      ? (String(state.busyAction || "").startsWith("draft_") ? "Pause requested" : "Stop requested")
      : (String(state.busyAction || "").startsWith("draft_") ? "Pause" : "Stop safely");
    const button = node("button", "runner-cancel-button compact", label);
    button.type = "button";
    button.disabled = state.cancelRequested;
    button.addEventListener("click", requestRunnerCancel);
    return button;
  }

  function simProgressModel() {
    const action = state.busyAction || "";
    const season = data.season || {};
    const draft = data.draft || {};
    const postseason = season.postseason || {};
    const draftTotals = draft.pickTotals || {};
    let percent = null;
    let progressLabel = "";
    const draftAction = String(action).startsWith("draft_");
    if (draftAction && Number(draftTotals.total || 0) > 0) {
      const used = Number(draftTotals.used || 0);
      const total = Number(draftTotals.total || 0);
      percent = (used / Math.max(1, total)) * 100;
      progressLabel = `${used}/${total} picks`;
    } else if ((action === "postseason" || action === "postseason_round" || action === "complete_season") && Number(postseason.games || 0) > 0) {
      const played = Number(postseason.played || 0);
      const total = Number(postseason.games || 0);
      percent = (played / Math.max(1, total)) * 100;
      progressLabel = `${played}/${total} playoff games`;
    } else if (Number(season.totals?.games || 0) > 0) {
      const played = Number(season.totals?.played || 0);
      const total = Number(season.totals?.games || 0);
      percent = (played / Math.max(1, total)) * 100;
      progressLabel = `${played}/${total} regular-season games`;
    } else if (data.calendar?.gamesInView?.length) {
      const games = data.calendar.gamesInView;
      const played = games.filter((game) => Number(game.played || 0) === 1).length;
      percent = (played / Math.max(1, games.length)) * 100;
      progressLabel = `${played}/${games.length} visible games`;
    }
    return {
      title: actionLabel(action),
      step: simProgressStepText(action),
      elapsed: formatElapsed(state.runnerStartedAt),
      percent,
      progressLabel,
    };
  }

  function simProgressStepText(action) {
    const season = data.season || {};
    const remaining = Number(season.totals?.remaining || 0);
    if (String(action || "").startsWith("draft_")) return "Draft room selections are being processed.";
    if (action === "advance_to_draft") return "Moving through postseason, offseason free agency, scouting, and draft prep.";
    if (action === "sim_season") {
      return remaining > 0
        ? "Playing games and running weekly league operations."
        : "Regular season complete. Refreshing postseason and league state.";
    }
    if (action === "sim_week") return "Playing this week and running staff updates.";
    if (action === "postseason_round") return "Playing the current playoff round and updating the bracket.";
    if (action === "postseason" || action === "complete_season") return "Processing postseason results, awards, and season closeout.";
    if (action === "auto_cutdown_continue") return "Applying roster cleanup, then continuing the sim.";
    if (action === "advance_next_event" || action === "advance_to_date" || action === "advance_next_league_year") return "Advancing the calendar and processing due league events.";
    return "The league office is applying this action.";
  }

  function formatElapsed(startedAt) {
    if (!startedAt) return "0:00";
    const seconds = Math.max(0, Math.floor((Date.now() - Number(startedAt)) / 1000));
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }

  function simProgressFact(label, value) {
    const fact = node("span", "sim-progress-fact");
    append(fact, [
      node("small", null, label),
      node("strong", null, value || "-"),
    ]);
    return fact;
  }

  function simProgressActivityList() {
    const items = simProgressActivityItems();
    const wrap = node("div", "sim-progress-activity");
    wrap.append(node("span", "sim-progress-activity-title", items.length ? "Live League Feed" : "Waiting for league updates"));
    if (!items.length) {
      wrap.append(node("p", null, "Games, injuries, transactions, and major stories will appear here as they commit."));
      return wrap;
    }
    items.slice(0, 5).forEach((item) => {
      const row = node("div", `sim-progress-activity-row ${item.tone || ""}`.trim());
      append(row, [
        node("span", "sim-progress-activity-type", item.type),
        append(node("div"), [
          node("strong", null, item.title),
          node("small", null, [item.detail, item.date ? shortDate(item.date) : ""].filter(Boolean).join(" | ")),
        ]),
      ]);
      wrap.append(row);
    });
    return wrap;
  }

  function simProgressActivityItems() {
    const items = [];
    (data.season?.recentResults || []).slice(0, 6).forEach((game) => {
      if (Number(game.played || 0) !== 1) return;
      items.push({
        date: game.game_date,
        type: "Final",
        title: `${game.away_team || "AWAY"} at ${game.home_team || "HOME"}`,
        detail: `${game.away_team || "AWAY"} ${game.away_score ?? "-"} - ${game.home_team || "HOME"} ${game.home_score ?? "-"}`,
        tone: "good",
      });
    });
    (data.injuries?.recent || []).slice(0, 6).forEach((item) => {
      items.push({
        date: item.date || item.reportDate,
        type: "Medical",
        title: item.playerName ? `${item.playerName}${item.position ? `, ${item.position}` : ""}` : "Injury report",
        detail: item.description || `${item.injury || "Injury"}; ${item.team || "League"}`,
        tone: Number(item.expectedGames || 0) >= 4 ? "bad" : "warn",
      });
    });
    (data.transactions?.items || []).slice(0, 6).forEach((item) => {
      items.push({
        date: item.date,
        type: item.type || "Move",
        title: item.player || [item.team, item.fromTeam, item.toTeam].filter(Boolean).join(" / ") || "League transaction",
        detail: item.description || transactionFallbackDescription(item),
        tone: "note",
      });
    });
    (data.leagueNews?.items || [])
      .filter((item) => Number(item.is_major || 0))
      .slice(0, 6)
      .forEach((item) => {
        items.push({
          date: item.news_date,
          type: item.category || "News",
          title: item.title || "League story",
          detail: item.source || "League Wire",
          tone: "warn",
        });
      });
    return items
      .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))
      .slice(0, 6);
  }

  function boxScoreModal() {
    const result = state.boxScoreModal;
    if (!result) return null;
    const overlay = node("div", "box-score-modal-overlay");
    const modal = node("section", "box-score-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    const boxScore = boxScorePayload(result);
    append(title, [
      node("strong", null, result.returncode === 0 ? "Box Score" : "Box Score Unavailable"),
      node("small", null, boxScore ? `${boxScore.matchup || "Stored game result"}${boxScoreMeta(boxScore) ? ` | ${boxScoreMeta(boxScore)}` : ""}` : (result.params?.game_id ? `Game ${result.params.game_id}` : "Stored game result")),
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
      body.append(renderBoxScore(result));
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
    const context = state.injuryModalContext || {};
    const overlay = node("div", "box-score-modal-overlay injury-alert-overlay");
    const modal = node("section", "box-score-modal injury-alert-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("strong", null, "Injury Report"),
      node("small", null, `${alerts.length} player${alerts.length === 1 ? "" : "s"} expected to miss time`),
    ]);
    const close = node("button", "icon-button close-button", "Stop");
    close.type = "button";
    close.addEventListener("click", () => {
      state.injuryModal = null;
      state.injuryModalContext = null;
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
    const actions = node("div", "injury-alert-actions");
    const autoChecked = userInjuryAutoManageEnabled() || Boolean(state.injuryAutoManageChecked);
    const autoControl = node("label", "injury-auto-control");
    const autoBox = node("input");
    autoBox.type = "checkbox";
    autoBox.checked = autoChecked;
    autoBox.disabled = state.runnerBusy || !runnerMode();
    autoBox.addEventListener("change", () => {
      state.injuryAutoManageChecked = autoBox.checked;
    });
    append(autoControl, [
      autoBox,
      append(node("span", null), [
        node("strong", null, "Let staff handle injury adjustments"),
        node("small", null, "CPU will reorder your depth chart and future injury popups will stay off."),
      ]),
    ]);
    async function persistAutoPreferenceIfNeeded() {
      if (!autoBox.checked || userInjuryAutoManageEnabled()) return true;
      return setUserInjuryAutoManage(true);
    }
    const review = node("button", "control-button secondary", "Review Injuries");
    review.type = "button";
    review.addEventListener("click", async () => {
      if (!(await persistAutoPreferenceIfNeeded())) return;
      state.injuryModal = null;
      state.injuryModalContext = null;
      switchView("injuries", { refresh: true });
    });
    const depth = node("button", "control-button secondary", "Adjust Depth Chart");
    depth.type = "button";
    depth.addEventListener("click", async () => {
      if (!(await persistAutoPreferenceIfNeeded())) return;
      state.injuryModal = null;
      state.injuryModalContext = null;
      switchView("depth", { refresh: true });
    });
    const continueSim = node("button", "control-button good", "Continue Sim");
    continueSim.type = "button";
    continueSim.disabled = state.runnerBusy || !runnerMode() || !context.continueAction;
    continueSim.title = context.continueAction
      ? "Resume the sim from the next safe checkpoint."
      : "No sim action is available to continue.";
    continueSim.addEventListener("click", async () => {
      if (!context.continueAction) return;
      if (!(await persistAutoPreferenceIfNeeded())) return;
      const nextAction = context.continueAction;
      const nextParams = { ...(context.continueParams || {}) };
      state.injuryModal = null;
      state.injuryModalContext = null;
      runAction(nextAction, nextParams);
    });
    append(actions, [autoControl, review, depth, continueSim]);
    append(body, [
      node("div", "injury-alert-summary", context.paused
        ? "The sim paused at a safe weekly checkpoint. Continue the sim, or stop here to adjust the roster and depth chart."
        : "These updates were added to your inbox. Continue to the next week, or stop here to make roster and depth chart changes."),
      list,
      actions,
    ]);
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        state.injuryModal = null;
        state.injuryModalContext = null;
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
    const counts = rosterGateCountsFromState();
    const canContinueShort = counts
      && Number(counts.activeCount || 0) <= 53
      && Number(counts.practiceSquadCount || 0) <= 17;
    const manage = node("button", "control-button good", "Open Roster Cutdown");
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
      state.rosterCutdownPromptDismissedKey = prompt.key || currentRosterGateKey();
      state.rosterCutdownPrompt = null;
      runAction("auto_cutdown_continue", {
        continue_action: stoppedAction,
        continue_params: stoppedParams,
      });
    });
    const continueAnyway = node("button", "control-button secondary", "Continue Anyway");
    continueAnyway.type = "button";
    continueAnyway.disabled = state.runnerBusy || !runnerMode() || !canContinueShort;
    continueAnyway.title = canContinueShort
      ? "Continue with the roster below the normal active/practice squad targets."
      : "Roster is over a hard limit and must be fixed first.";
    continueAnyway.addEventListener("click", () => {
      const stoppedAction = prompt.stoppedAction || state.lastResult?.action || "sim_season";
      const stoppedParams = prompt.stoppedParams || state.lastResult?.params || {};
      state.rosterCutdownPromptDismissedKey = prompt.key || currentRosterGateKey();
      state.rosterCutdownPrompt = null;
      runAction(stoppedAction, {
        ...stoppedParams,
        skip_roster_gate: true,
        roster_short_warning_confirmed: true,
        advance_preflight_confirmed: true,
      });
    });
    append(actions, [manage, auto]);
    if (canContinueShort) actions.append(continueAnyway);
    append(body, [
      node("p", null, prompt.message || "Your active roster and practice squad need to be settled before the regular season can continue."),
      node("p", "muted", canContinueShort
        ? "You are under the normal roster targets, not over the hard limits. You can fill the roster now, let the CPU handle it, or continue anyway."
        : "Handle it yourself in Roster Hub, or let the CPU apply a cutdown/practice-squad plan and continue the sim."),
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

  function rosterCutdownModeModal() {
    const pending = state.pendingRosterCutdownAction;
    if (!pending) return null;
    const action = pending.action;
    const params = pending.params || {};
    const overlay = node("div", "box-score-modal-overlay roster-cutdown-choice-overlay");
    const modal = node("section", "box-score-modal roster-cutdown-choice-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("strong", null, "Roster Cutdown Decision"),
      node("small", null, `${actionLabel(action)} will advance past preseason roster deadlines`),
    ]);
    const close = node("button", "icon-button close-button", "Close");
    close.type = "button";
    close.addEventListener("click", () => {
      state.pendingRosterCutdownAction = null;
      render();
    });
    append(top, [title, close]);

    const deadlineLines = [];
    if (pending.cutdownDate) deadlineLines.push(`Final roster cutdown: ${shortDate(pending.cutdownDate)}`);
    if (pending.practiceSquadDate) deadlineLines.push(`Practice squad opens: ${shortDate(pending.practiceSquadDate)}`);

    const body = node("div", "box-score-modal-body roster-cutdown-choice-body");
    const choices = node("div", "roster-cutdown-choice-grid");
    const auto = node("button", "roster-cutdown-choice-card good");
    auto.type = "button";
    append(auto, [
      node("strong", null, "Auto Cutdown & Continue"),
      node("span", null, "Let the CPU trim the user roster, assign the practice squad, and keep simming."),
    ]);
    auto.addEventListener("click", () => {
      const nextParams = {
        ...params,
        advance_preflight_confirmed: true,
        auto_roster_cutdown: true,
        roster_cutdown_choice: "auto",
      };
      if (action === "sim_week" || action === "sim_season") {
        nextParams.skip_roster_gate = true;
      }
      state.rosterCutdownPromptDismissedKey = currentRosterGateKey();
      state.pendingRosterCutdownAction = null;
      runAction(action, nextParams);
    });

    const pause = node("button", "roster-cutdown-choice-card warn");
    pause.type = "button";
    append(pause, [
      node("strong", null, "Pause At Cutdown"),
      node("span", null, "Stop on cutdown day so you can make final cuts and practice squad decisions yourself."),
    ]);
    pause.addEventListener("click", () => {
      state.pendingRosterCutdownAction = null;
      runAction(action, {
        ...params,
        roster_cutdown_choice: "pause",
      });
    });
    append(choices, [auto, pause]);
    append(body, [
      node("p", null, "This action can move the save beyond preseason roster deadlines. Choose how the user-controlled team should handle roster cutdown before the sim starts."),
      deadlineLines.length ? node("p", "muted", deadlineLines.join(" | ")) : null,
      choices,
    ]);
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        state.pendingRosterCutdownAction = null;
        render();
      }
    });
    return overlay;
  }

  function simAdvancePromptModal() {
    const prompt = state.pendingSimAdvancePrompt;
    if (!prompt) return null;
    const overlay = node("div", "box-score-modal-overlay sim-advance-prompt-overlay");
    const modal = node("section", "box-score-modal sim-advance-prompt-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("strong", null, prompt.title || actionLabel(prompt.action)),
      node("small", null, "Review what this sim-to action will process"),
    ]);
    const close = node("button", "icon-button close-button", "Close");
    close.type = "button";
    close.addEventListener("click", () => {
      state.pendingSimAdvancePrompt = null;
      render();
    });
    append(top, [title, close]);

    const body = node("div", "box-score-modal-body sim-advance-prompt-body");
    const warningList = node("div", "sim-advance-warning-list");
    (prompt.warnings || []).forEach((warning) => warningList.append(node("span", "sim-advance-warning", warning)));
    const checkpointList = node("ul", "sim-advance-checkpoints");
    (prompt.checkpoints || []).forEach((checkpoint) => {
      const item = document.createElement("li");
      item.textContent = checkpoint;
      checkpointList.append(item);
    });
    const actions = node("div", "sim-advance-actions");
    const cancel = node("button", "control-button secondary", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      state.pendingSimAdvancePrompt = null;
      render();
    });
    const run = node("button", `control-button ${prompt.tone || "warn"}`.trim(), prompt.primaryLabel || "Continue");
    run.type = "button";
    run.disabled = state.runnerBusy || !runnerMode();
    run.addEventListener("click", () => {
      const nextAction = prompt.action;
      const nextParams = {
        ...(prompt.params || {}),
        advance_preflight_confirmed: true,
      };
      state.pendingSimAdvancePrompt = null;
      runAction(nextAction, nextParams);
    });
    const actionButtons = [cancel, run];
    if (prompt.offerCpuManagement) {
      const cpuRun = node("button", "control-button good", "CPU Manage & Sim");
      cpuRun.type = "button";
      cpuRun.disabled = state.runnerBusy || !runnerMode();
      cpuRun.title = "Let CPU staff handle user-team roster cutdown, practice squad, and injury depth-chart gates during this sim.";
      cpuRun.addEventListener("click", () => {
        const nextAction = prompt.action;
        const nextParams = userCpuManagementParams(prompt.params || {});
        state.pendingSimAdvancePrompt = null;
        state.pendingRosterCutdownAction = null;
        runAction(nextAction, nextParams);
      });
      actionButtons.push(cpuRun);
    }
    append(actions, actionButtons);
    const cpuManagementNote = prompt.offerCpuManagement
      ? node("div", "sim-advance-cpu-note", "CPU Manage & Sim turns on user-team CPU management for this run: automatic roster cutdown/practice squad, roster gate skipping, and staff injury depth-chart handling.")
      : null;
    append(body, [
      node("p", null, prompt.detail || "This action will advance the save and process any required league events on the way."),
      cpuManagementNote,
      warningList.children.length ? warningList : null,
      checkpointList.children.length ? checkpointList : null,
      actions,
    ]);
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        state.pendingSimAdvancePrompt = null;
        render();
      }
    });
    return overlay;
  }

  function draftClassSetupModal() {
    const setup = data.draftClassSetup || {};
    if (isObserveMode()) return null;
    if (!setup.required) return null;
    const packages = (setup.packages || []).filter((item) => item.valid);
    if (!state.selectedDraftClassPackage && packages.length) {
      state.selectedDraftClassPackage = packages[0].path || "";
    }
    const overlay = node("div", "box-score-modal-overlay draft-class-setup-overlay");
    const modal = node("section", "box-score-modal draft-class-setup-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("span", "tag warn", "Required"),
      node("h3", null, "Draft Class Setup"),
      node("small", null, `${setup.draftYear || data.draft?.year || ""} Draft`),
    ]);
    top.append(title);

    const body = node("div", "box-score-modal-body draft-class-setup-body");
    append(body, [
      node("p", null, "Choose how this draft class enters the league before advancing the calendar."),
      node("p", "muted", "Generate a fresh fictional class, or import a saved package. Imported packages are remapped to this save’s draft year."),
    ]);

    const actions = node("div", "draft-class-actions");
    const generate = node("button", "control-button good", "Generate Draft Class");
    generate.type = "button";
    generate.disabled = state.runnerBusy || !runnerMode();
    generate.addEventListener("click", () => runAction("draft_class_generate", { draft_year: setup.draftYear || data.draft?.year }));

    const select = node("select", "draft-class-package-select");
    select.disabled = state.runnerBusy || !runnerMode() || packages.length === 0;
    packages.forEach((item) => {
      const label = `${item.name || item.packageName} (${item.prospectCount || "?"} prospects${item.draftYear ? `, saved ${item.draftYear}` : ""})`;
      const option = node("option", null, label);
      option.value = item.path || "";
      if (option.value === state.selectedDraftClassPackage) option.selected = true;
      select.append(option);
    });
    select.addEventListener("change", () => {
      state.selectedDraftClassPackage = select.value;
    });

    const importButton = node("button", "control-button primary", "Import Selected Class");
    importButton.type = "button";
    importButton.disabled = state.runnerBusy || !runnerMode() || !state.selectedDraftClassPackage;
    importButton.addEventListener("click", () => runAction("draft_class_import", {
      draft_year: setup.draftYear || data.draft?.year,
      package: state.selectedDraftClassPackage,
    }));
    append(actions, [generate, select, importButton]);
    body.append(actions);
    body.append(node("small", "muted", packages.length
      ? `Saved classes: ${setup.packageRoot || "Saved Draft Classes"}`
      : `No saved classes found under ${setup.packageRoot || "Saved Draft Classes"}.`));
    if (state.runnerBusy && (state.busyAction === "draft_class_generate" || state.busyAction === "draft_class_import")) {
      body.append(node("div", "empty-state", `${actionLabel(state.busyAction)} is running...`));
    }
    append(modal, [top, body]);
    overlay.append(modal);
    return overlay;
  }

  function isFifthYearOptionDeadlineToday() {
    const currentDate = String(data.currentDate || data.activeSave?.current_date || "").slice(0, 10);
    if (!currentDate) return false;
    const eventDate = calendarEventDateByCode("FIFTH_YEAR_OPTION_DEADLINE");
    if (eventDate) return currentDate === eventDate;
    const phase = String(data.currentPhase || data.activeSave?.current_phase_code || data.settings?.current_calendar_phase || "").toLowerCase();
    const isOffseason = phase.includes("offseason") || phase.includes("post") || phase.includes("free") || phase.includes("draft");
    return currentDate.slice(5) === "05-01" && isOffseason;
  }

  function fifthYearOptionCandidates() {
    return (data.contractNegotiations?.fifthYearOptions || [])
      .filter((player) => player && (player.player_id || player.player_name));
  }

  function fifthYearOptionPromptKey(players = fifthYearOptionCandidates()) {
    return [
      data.activeSave?.game_id || data.activeSave?.save_id || data.registry?.activeGameId || "",
      data.currentDate || data.activeSave?.current_date || "",
      data.contractNegotiations?.team || data.activeSave?.user_team || "",
      players.map((player) => `${player.player_id || player.player_name}:${player.option_season || ""}`).join(","),
    ].join(":");
  }

  function maybeLoadFifthYearOptionPromptData() {
    if (!runnerMode() || state.runnerBusy || state.contractsLoading || !isFifthYearOptionDeadlineToday()) return;
    if (state.contractsLiveKey === contractsLiveKey()) return;
    loadLiveContracts().then(() => scheduleRender());
  }

  function fifthYearOptionModal() {
    if (state.runnerBusy || !isFifthYearOptionDeadlineToday()) return null;
    const players = fifthYearOptionCandidates();
    if (!players.length) return null;
    const key = fifthYearOptionPromptKey(players);
    if (key && state.fifthYearOptionPromptDismissedKey === key) return null;
    const talks = data.contractNegotiations || {};
    const team = talks.team || data.activeSave?.user_team || "your team";
    const overlay = node("div", "box-score-modal-overlay fifth-option-overlay");
    const modal = node("section", "box-score-modal fifth-option-modal");
    const top = node("div", "box-score-modal-top");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("span", "tag warn", "Deadline"),
      node("strong", null, "Fifth-Year Options"),
      node("small", null, `${team} decision${players.length === 1 ? "" : "s"} due today`),
    ]);
    const close = node("button", "icon-button close-button", "Decide Later");
    close.type = "button";
    close.addEventListener("click", () => {
      state.fifthYearOptionPromptDismissedKey = key;
      render();
    });
    append(top, [title, close]);

    const body = node("div", "box-score-modal-body fifth-option-body");
    body.append(node("p", null, "The fifth-year option deadline is today. Exercise the option to lock in the guaranteed option year, or decline it and let the player continue toward normal contract talks."));
    const list = node("div", "fifth-option-list");
    players.forEach((player) => {
      const card = node("article", "fifth-option-card");
      const cardTop = node("div", "fifth-option-card-top");
      append(cardTop, [
        playerLink(player.player_id, player.player_name, "player-link strong-link", {
          team,
          position: player.position,
        }),
        tag(player.recommendation || "Review", String(player.recommendation || "").toLowerCase().includes("exercise") ? "good" : "warn"),
      ]);
      const details = node("div", "fifth-option-details");
      append(details, [
        append(node("span"), [node("b", null, player.position || "-"), document.createTextNode(" Position")]),
        append(node("span"), [node("b", null, player.market_score || "-"), document.createTextNode(" Score")]),
        append(node("span"), [node("b", null, player.option_season || "-"), document.createTextNode(" Option Year")]),
        append(node("span"), [node("b", null, money(player.option_salary)), document.createTextNode(" Salary")]),
      ]);
      const actions = node("div", "fifth-option-actions");
      const exercise = node("button", "control-button good", "Exercise Option");
      exercise.type = "button";
      exercise.disabled = state.runnerBusy || !runnerMode();
      exercise.addEventListener("click", () => runAction("contract_option_exercise", {
        player_id: player.player_id,
      }));
      const decline = node("button", "control-button secondary", "Decline");
      decline.type = "button";
      decline.disabled = state.runnerBusy || !runnerMode();
      decline.addEventListener("click", () => runAction("contract_option_decline", {
        player_id: player.player_id,
      }));
      append(actions, [exercise, decline]);
      append(card, [cardTop, details, actions]);
      list.append(card);
    });
    const footer = node("div", "fifth-option-footer");
    const openContracts = node("button", "control-button", "Open Contract Talks");
    openContracts.type = "button";
    openContracts.addEventListener("click", () => {
      state.fifthYearOptionPromptDismissedKey = key;
      switchView("contracts", { refresh: true });
    });
    footer.append(openContracts);
    append(body, [list, footer]);
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        state.fifthYearOptionPromptDismissedKey = key;
        render();
      }
    });
    return overlay;
  }

  function rosterGateCountsFromState() {
    const depth = projectedDepthChart(data.depthChart || {});
    const rows = rosterRows(depth);
    if (!rows.length) return null;
    const activeCount = rows.filter((player) => activeRosterStatus(player.status)).length;
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
    return [
      data.activeSave?.game_id || data.activeSave?.save_id || "",
      data.currentDate || data.activeSave?.current_date || "",
      data.currentPhase || data.activeSave?.current_phase_code || data.settings?.current_calendar_phase || "",
      "roster-cutdown",
    ].join(":");
  }

  function suppressRosterGatePrompt(action = "") {
    const name = String(action || "");
    if (state.view === "practiceSquad") return true;
    return name === "practice_squad_assign"
      || name === "practice_squad_promote"
      || name === "practice_squad_release"
      || name === "roster_cutdown_apply"
      || name === "auto_cutdown"
      || name === "auto_cutdown_continue";
  }

  function maybeShowRosterGatePromptFromState() {
    if (isObserveMode()) return;
    if (state.rosterCutdownPrompt || state.runnerBusy) return;
    if (suppressRosterGatePrompt()) return;
    const phase = String(data.currentPhase || data.activeSave?.current_phase_code || data.settings?.current_calendar_phase || "").toLowerCase();
    if (!phase.includes("cutdown") && !phase.includes("practice squad")) return;
    if (!rosterGateStillRelevant()) return;
    const counts = rosterGateCountsFromState();
    if (!counts) return;
    const activeLimit = 53;
    const practiceSquadLimit = 17;
    const issues = [];
    if (counts.activeCount > activeLimit) issues.push(`cut active roster from ${counts.activeCount} to ${activeLimit}`);
    if (counts.practiceSquadCount > practiceSquadLimit) issues.push(`trim practice squad from ${counts.practiceSquadCount} to ${practiceSquadLimit}`);
    if (!issues.length) return;
    const key = currentRosterGateKey();
    if (key && state.rosterCutdownPromptDismissedKey === key) return;
    const team = data.activeSave?.user_team || data.depthChart?.team || "your team";
    state.rosterCutdownPrompt = {
      key,
      title: "Roster Limit Cleanup Needed",
      message: `Roster limit cleanup required for ${team}: ${issues.join("; ")}.`,
      stoppedAction: "sim_season",
      stoppedParams: {},
    };
  }

  function finishRender(root) {
    maybeLoadFifthYearOptionPromptData();
    const queue = renderPendingActionQueuePanel();
    if (queue) root.prepend(queue);
    const banner = runnerBusyBanner();
    if (banner) root.prepend(banner);
    const progressOverlay = simProgressOverlay();
    if (progressOverlay) root.append(progressOverlay);
    const boxScore = boxScoreModal();
    if (boxScore) root.append(boxScore);
    const whyModal = whyThisHappenedModal();
    if (whyModal) root.append(whyModal);
    if (!isObserveMode()) {
      const injuryAlerts = injuryAlertModal();
      if (injuryAlerts) root.append(injuryAlerts);
      const cutdownPrompt = rosterCutdownModal();
      if (cutdownPrompt) root.append(cutdownPrompt);
      const simPrompt = simAdvancePromptModal();
      if (simPrompt) root.append(simPrompt);
      const cutdownMode = rosterCutdownModeModal();
      if (cutdownMode) root.append(cutdownMode);
      const draftSetup = draftClassSetupModal();
      if (draftSetup) root.append(draftSetup);
      const fifthOption = fifthYearOptionModal();
      if (fifthOption) root.append(fifthOption);
      const draftTradeAlert = draftTradeAlertModal();
      if (draftTradeAlert) root.append(draftTradeAlert);
    }
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

  function guidedOffseasonActive() {
    const phase = String(data.currentPhase || data.activeSave?.current_phase_code || data.settings?.current_calendar_phase || "").toLowerCase();
    const season = data.season || {};
    const draft = data.draft || {};
    const regularDone = Number(season.totals?.games || 0) > 0 && Number(season.totals?.remaining || 0) === 0;
    const draftActive = Number(draft.pickTotals?.remaining || 0) > 0 || Number(draft.pickTotals?.used || 0) > 0;
    return Boolean(data.draftClassSetup?.required)
      || regularDone
      || draftActive
      || phase.includes("offseason")
      || phase.includes("free")
      || phase.includes("draft")
      || phase.includes("camp")
      || phase.includes("preseason")
      || phase.includes("cutdown")
      || phase.includes("practice squad");
  }

  function offseasonGuideYear() {
    return Number(data.draft?.year || data.currentContractYear || data.currentSeason + 1 || activeSeasonYear() + 1 || 2027);
  }

  function eventDateByKeywords(...keywords) {
    const events = [
      ...(data.events || []),
      ...(data.calendar?.upcomingEvents || []),
    ];
    const lowerKeywords = keywords.map((keyword) => String(keyword || "").toLowerCase()).filter(Boolean);
    const match = events.find((event) => {
      const text = [
        event.event_code,
        event.event_name,
        event.event_category,
        event.phase_name,
        event.notes,
      ].map((value) => String(value || "").toLowerCase()).join(" ");
      return lowerKeywords.every((keyword) => text.includes(keyword));
    });
    return match?.event_start_date || "";
  }

  function dateOnOrAfter(left, right) {
    if (!left || !right) return false;
    return String(left).slice(0, 10) >= String(right).slice(0, 10);
  }

  function dateBefore(left, right) {
    if (!left || !right) return false;
    return String(left).slice(0, 10) < String(right).slice(0, 10);
  }

  function offseasonGuideDates(year = offseasonGuideYear()) {
    const draft = data.draft || {};
    const fa = data.freeAgency || {};
    return {
      freeAgency: String(data.freeAgencyStart || fa.startDate || eventDateByKeywords("free", "agency") || `${year}-03-10`).slice(0, 10),
      draft: String(draft.draftDate || eventDateByKeywords("draft") || `${year}-04-22`).slice(0, 10),
      fifthOption: String(eventDateByKeywords("fifth", "option") || `${year}-05-01`).slice(0, 10),
      juneOne: `${year}-06-01`,
      camp: String(eventDateByKeywords("training", "camp") || eventDateByKeywords("camp") || `${year}-07-22`).slice(0, 10),
      preseason: String(eventDateByKeywords("preseason", "week", "1") || `${year}-08-01`).slice(0, 10),
      cutdown: String(eventDateByKeywords("cutdown") || eventDateByKeywords("roster", "cut") || `${year}-08-31`).slice(0, 10),
      weekOne: String(eventDateByKeywords("week", "1") || `${year}-09-10`).slice(0, 10),
    };
  }

  function safeGuideView(view) {
    if (isObserveMode() && observeHiddenViews().has(view)) return "calendar";
    return view || "calendar";
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

  function guideStepCard(step) {
    const card = node("button", `offseason-guide-step ${step.status || "upcoming"}`.trim());
    card.type = "button";
    card.addEventListener("click", () => {
      if (step.onOpen) step.onOpen();
      switchView(safeGuideView(step.view), { refresh: true });
    });
    const top = node("div", "offseason-guide-step-top");
    append(top, [
      node("span", `workflow-state ${step.status || "upcoming"}`, step.statusLabel || guideStatusLabel(step.status)),
      step.date ? node("span", "event-date", shortDate(step.date)) : null,
    ]);
    const copy = node("div", "offseason-guide-copy");
    append(copy, [
      node("strong", null, step.label),
      node("small", null, step.detail),
      step.note ? node("p", null, step.note) : null,
    ]);
    append(card, [top, copy]);
    return card;
  }

  function guideStatusLabel(status) {
    return {
      complete: "Done",
      current: "Now",
      blocked: "Required",
      upcoming: "Next",
    }[status] || "Next";
  }

  function markOffseasonStatuses(steps) {
    let foundCurrent = false;
    return steps.map((step) => {
      let status = step.complete ? "complete" : "upcoming";
      if (!step.complete && !foundCurrent) {
        status = step.blocked ? "blocked" : "current";
        foundCurrent = true;
      }
      return {
        ...step,
        status,
        statusLabel: step.statusLabel || guideStatusLabel(status),
      };
    });
  }

  function offseasonGuideSteps() {
    const phase = String(data.currentPhase || data.activeSave?.current_phase_code || data.settings?.current_calendar_phase || "").toLowerCase();
    const currentDate = String(data.currentDate || "");
    const season = data.season || {};
    const draft = data.draft || {};
    const fa = data.freeAgency || {};
    const counts = data.contractNegotiations?.counts || {};
    const year = offseasonGuideYear();
    const dates = offseasonGuideDates(year);
    const regularDone = Number(season.totals?.games || 0) > 0 && Number(season.totals?.remaining || 0) === 0;
    const postseason = season.postseason || {};
    const playoffsDone = regularDone && (Number(postseason.games || 0) === 0 || Number(postseason.remaining || 0) === 0);
    const draftTotals = draft.pickTotals || {};
    const draftStarted = Number(draftTotals.used || 0) > 0;
    const draftDone = Number(draftTotals.total || 0) > 0 && Number(draftTotals.remaining || 0) === 0;
    const rosterCounts = rosterGateCountsFromState();
    const rosterIssue = Boolean(rosterCounts && (rosterCounts.activeCount > 53 || rosterCounts.practiceSquadCount > 17));
    const faStarted = dateOnOrAfter(currentDate, dates.freeAgency) || Number(fa.counts?.available || 0) > 0 || Boolean(fa.period);
    const faPastEarly = dateOnOrAfter(currentDate, dates.draft) || draftStarted || draftDone;
    const preseasonDone = phase.includes("regular") || (dateOnOrAfter(currentDate, dates.cutdown) && String(season.nextGameType || "").toUpperCase() !== "PRE");
    const weekOneReady = phase.includes("regular") && !rosterIssue;
    const steps = [
      {
        key: "seasonReview",
        label: "Season Review",
        detail: "Review progression/regression, retirements, awards, staff notes, and the league history that now matters.",
        note: Number(data.scouting?.counts?.unread || 0) ? `${data.scouting.counts.unread} staff message${Number(data.scouting.counts.unread) === 1 ? "" : "s"} waiting in the inbox.` : "Start here after the Super Bowl to understand what changed.",
        view: "history",
        date: dates.freeAgency,
        complete: faStarted,
      },
      {
        key: "contracts",
        label: "Re-Signings, Tags & Tenders",
        detail: "Make calls on expiring players before they become part of the market.",
        note: `${Number(counts.priority || counts.total || 0)} contract decision${Number(counts.priority || counts.total || 0) === 1 ? "" : "s"} currently visible.`,
        view: "contracts",
        date: dates.freeAgency,
        complete: faStarted,
      },
      {
        key: "freeAgency",
        label: "Early Free Agency",
        detail: "Attack core roster holes while the best players still set the market.",
        note: Number(fa.counts?.pendingOffers || 0) ? `${fa.counts.pendingOffers} offer${Number(fa.counts.pendingOffers) === 1 ? "" : "s"} awaiting decisions.` : "Teams should spend with a plan, not just fill every depth slot early.",
        view: "freeAgency",
        date: dates.freeAgency,
        complete: faPastEarly,
      },
      {
        key: "draftPrep",
        label: "Draft Prep",
        detail: "Finish scouting, visits, QB due diligence, medical flags, and late-board movement.",
        note: data.draftClassSetup?.required ? "Choose Generate or Import before the calendar can move past June 1." : `${Number(data.scouting?.counts?.visible || data.scouting?.board?.length || 0)} prospects visible on the board.`,
        view: "scouting",
        date: dates.draft,
        blocked: Boolean(data.draftClassSetup?.required),
        complete: dateOnOrAfter(currentDate, dates.draft) || draftStarted,
      },
      {
        key: "draft",
        label: "NFL Draft",
        detail: "Run the room, review trades, and judge each pick against team need and scouting confidence.",
        note: Number(draftTotals.total || 0) ? `${Number(draftTotals.remaining || 0)} pick${Number(draftTotals.remaining || 0) === 1 ? "" : "s"} remaining.` : "The draft room opens when the calendar reaches draft day.",
        view: "draft",
        date: dates.draft,
        complete: draftDone,
      },
      {
        key: "fifthOptions",
        label: "Fifth-Year Options",
        detail: "Decide whether former first-rounders get the guaranteed option year.",
        note: `${Number(counts.fifthYearOptions || 0)} eligible player${Number(counts.fifthYearOptions || 0) === 1 ? "" : "s"} currently on the user-team list.`,
        view: "contracts",
        date: dates.fifthOption,
        complete: dateBefore(currentDate, dates.fifthOption) ? false : Number(counts.fifthYearOptions || 0) === 0 || dateOnOrAfter(currentDate, dates.juneOne),
      },
      {
        key: "postDraftMarket",
        label: "Post-Draft Market",
        detail: "Fill the holes the draft did not solve while veterans adjust their demands.",
        note: `${Number(fa.counts?.available || 0)} free agent${Number(fa.counts?.available || 0) === 1 ? "" : "s"} available.`,
        view: "freeAgency",
        date: dates.juneOne,
        complete: dateOnOrAfter(currentDate, dates.juneOne) && !data.draftClassSetup?.required,
      },
      {
        key: "nextClass",
        label: "Next Draft Class",
        detail: "Generate or import the next class before the new football year moves forward.",
        note: data.draftClassSetup?.required ? "The league is waiting for your Generate or Import choice." : "Once selected, scouting can begin building the next board.",
        view: "calendar",
        date: dates.juneOne,
        blocked: Boolean(data.draftClassSetup?.required),
        complete: dateOnOrAfter(currentDate, dates.juneOne) && !data.draftClassSetup?.required,
      },
      {
        key: "camp",
        label: "Training Camp",
        detail: "Watch position battles, development notes, trait reveals, and camp injuries.",
        note: "Camp events should help explain depth-chart decisions before preseason games start.",
        view: "inbox",
        date: dates.camp,
        complete: dateOnOrAfter(currentDate, dates.preseason) || phase.includes("preseason") || phase.includes("regular"),
      },
      {
        key: "preseason",
        label: "Preseason",
        detail: "Give young players, roster bubble pieces, and specialists enough snaps to evaluate them.",
        note: season.nextGameType === "PRE" && season.nextWeek ? `Next up: preseason week ${season.nextWeek}.` : "Preseason box scores and injuries should be reviewable from the calendar.",
        view: "calendar",
        date: dates.preseason,
        complete: preseasonDone,
      },
      {
        key: "cutdown",
        label: "Cutdown, Waivers & Practice Squad",
        detail: "Trim to 53, queue practice squad decisions, then let waiver claims resolve.",
        note: rosterIssue ? `${rosterCounts.activeCount}/53 active | ${rosterCounts.practiceSquadCount}/17 practice squad.` : "Roster counts are inside the current guardrails.",
        view: "practiceSquad",
        date: dates.cutdown,
        blocked: rosterIssue,
        complete: dateOnOrAfter(currentDate, dates.weekOne) && !rosterIssue,
      },
      {
        key: "weekOne",
        label: "Week 1 Readiness",
        detail: "Confirm depth charts, special teams duties, injury plans, and cap/roster compliance.",
        note: weekOneReady ? "The regular season can proceed." : "Use roster and depth chart screens to clean up final football operations.",
        view: "depth",
        date: dates.weekOne,
        complete: weekOneReady,
      },
    ];
    const marked = markOffseasonStatuses(steps);
    const current = marked.find((step) => step.status === "blocked" || step.status === "current") || marked[marked.length - 1];
    return { steps: marked, current, dates, year };
  }

  function renderGuidedOffseasonPanel() {
    const guide = offseasonGuideSteps();
    const p = panel("Offseason Roadmap", guide.current ? guide.current.label : `${guide.year} league calendar`);
    p.classList.add("offseason-guide-panel");
    const body = panelBody(p);
    const current = guide.current;
    if (current) {
      const hero = node("div", `offseason-guide-hero ${current.status || ""}`.trim());
      append(hero, [
        append(node("div"), [
          node("span", `workflow-state ${current.status || "current"}`, current.statusLabel || guideStatusLabel(current.status)),
          node("strong", null, current.label),
          node("p", null, current.detail),
          current.note ? node("small", null, current.note) : null,
        ]),
        actionButtonForGuideStep(current),
      ]);
      body.append(hero);
    }
    const grid = node("div", "offseason-guide-grid");
    guide.steps.forEach((step) => grid.append(guideStepCard(step)));
    body.append(grid);
    return p;
  }

  function actionButtonForGuideStep(step) {
    const button = node("button", "primary-run-button", step.status === "complete" ? "Review" : step.blocked ? "Resolve" : "Open");
    button.type = "button";
    button.addEventListener("click", () => {
      if (step.onOpen) step.onOpen();
      switchView(safeGuideView(step.view), { refresh: true });
    });
    return button;
  }

  function renderWorkflowPanel() {
    if (guidedOffseasonActive()) return renderGuidedOffseasonPanel();
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

  function todayTeamAbbr() {
    return String(data.activeSave?.user_team || data.registry?.userTeam || "").trim().toUpperCase();
  }

  function todayTeamInfo() {
    const abbr = todayTeamAbbr();
    if (!abbr) return null;
    return (data.teams || []).find((team) => String(team.abbreviation || team.abbr || "").toUpperCase() === abbr) || { abbreviation: abbr, team_name: abbr };
  }

  function todayTeamRecord() {
    const abbr = todayTeamAbbr();
    if (!abbr) return null;
    return (data.season?.standings || []).find((row) => String(row.abbreviation || "").toUpperCase() === abbr) || null;
  }

  function recordLabel(record) {
    if (!record) return "0-0";
    const wins = Number(record.wins || 0);
    const losses = Number(record.losses || 0);
    const ties = Number(record.ties || 0);
    return ties ? `${wins}-${losses}-${ties}` : `${wins}-${losses}`;
  }

  function nextTeamGame() {
    const abbr = todayTeamAbbr();
    if (!abbr) return null;
    const games = [
      ...(data.calendar?.nextGames || []),
      ...(data.season?.nextWeekGames || []),
      ...(data.season?.userTeamSchedule || []),
    ];
    return games.find((game) => {
      if (Number(game.played || 0) === 1) return false;
      return String(game.away_team || "").toUpperCase() === abbr || String(game.home_team || "").toUpperCase() === abbr;
    }) || null;
  }

  function capSpaceLabel() {
    const cap = data.contractNegotiations?.currentCap || data.contractNegotiations?.cap || {};
    const value = cap.cap_space ?? cap.capSpace ?? cap.available_cap ?? cap.availableCap ?? cap.effective_cap_space;
    if (value === undefined || value === null || value === "") return "Unavailable";
    return money(value);
  }

  function todayUnreadCounts() {
    const messages = data.scouting?.inbox || [];
    const buckets = inboxMessageBuckets(messages);
    return {
      total: messages.filter((message) => !Number(message.is_read || 0)).length,
      priority: buckets.priority.length,
      scouting: buckets.scouting.length,
      frontOffice: buckets.team.length + buckets.medical.length + buckets.league.length,
    };
  }

  function todayPhaseLabel() {
    return currentSeasonSectionLabel() || data.currentPhase || "League Office";
  }

  function attentionItems() {
    const items = [];
    const phase = String(data.currentPhase || data.activeSave?.current_phase_code || data.settings?.current_calendar_phase || "").toLowerCase();
    const draftSetup = data.draftClassSetup || {};
    const contractCounts = data.contractNegotiations?.counts || {};
    const injuryCounts = data.injuries?.counts || {};
    const faCounts = data.freeAgency?.counts || {};
    const draftTotals = data.draft?.pickTotals || {};
    const unread = todayUnreadCounts();
    const rosterCounts = rosterGateCountsFromState();

    if (draftSetup.required) {
      items.push({
        title: "Choose this year's draft class",
        detail: "Generate or import the class before the league moves past June 1.",
        tag: "Required",
        tone: "bad",
        view: "calendar",
      });
    }
    if (!isObserveMode() && rosterCounts && rosterGateStillRelevant()) {
      if (phase.includes("cutdown") || phase.includes("practice squad")) {
        const issues = [];
        if (rosterCounts.activeCount > 53) issues.push(`${rosterCounts.activeCount}/53 active`);
        if (rosterCounts.practiceSquadCount > 17) issues.push(`${rosterCounts.practiceSquadCount}/17 practice squad`);
        if (issues.length) {
          items.push({
            title: "Roster limits need attention",
            detail: issues.join(" | "),
            tag: "Cutdown",
            tone: "bad",
            view: "practiceSquad",
          });
        }
      }
    }
    if (!isObserveMode() && Number(injuryCounts.userActive || 0) > 0) {
      items.push({
        title: "Injury report changed",
        detail: `${injuryCounts.userActive} active user-team injury${Number(injuryCounts.userActive) === 1 ? "" : "ies"}.`,
        tag: "Medical",
        tone: Number(injuryCounts.majorActive || 0) ? "bad" : "warn",
        view: "injuries",
      });
    }
    if (!isObserveMode() && Number(contractCounts.fifthYearOptions || 0) > 0) {
      items.push({
        title: "Fifth-year option decision available",
        detail: `${contractCounts.fifthYearOptions} eligible player${Number(contractCounts.fifthYearOptions) === 1 ? "" : "s"} on your roster.`,
        tag: "Contracts",
        tone: "warn",
        view: "contracts",
      });
    }
    if (!isObserveMode() && Number(contractCounts.priority || 0) > 0) {
      items.push({
        title: "Priority contract talks",
        detail: `${contractCounts.priority} priority expiring player${Number(contractCounts.priority) === 1 ? "" : "s"} to review.`,
        tag: "Contracts",
        tone: "warn",
        view: "contracts",
      });
    }
    if (Number(faCounts.pendingOffers || 0) > 0) {
      items.push({
        title: "Free-agent offers pending",
        detail: `${faCounts.pendingOffers} offer${Number(faCounts.pendingOffers) === 1 ? "" : "s"} awaiting a decision.`,
        tag: "Free Agency",
        tone: "warn",
        view: "freeAgency",
      });
    }
    if (Number(draftTotals.remaining || 0) > 0 && Number(draftTotals.used || 0) > 0) {
      items.push({
        title: "Draft is in progress",
        detail: `${draftTotals.remaining} pick${Number(draftTotals.remaining) === 1 ? "" : "s"} remaining.`,
        tag: "Draft",
        tone: "bad",
        view: "draft",
      });
    }
    if (unread.frontOffice > 0) {
      items.push({
        title: "Front office messages unread",
        detail: `${unread.frontOffice} staff or league message${unread.frontOffice === 1 ? "" : "s"} waiting.`,
        tag: "Inbox",
        tone: "warn",
        view: "inbox",
        onOpen: () => { state.inboxTab = unread.priority ? "priority" : "team"; },
      });
    }
    if (unread.scouting > 0) {
      items.push({
        title: "Scouting reports unread",
        detail: `${unread.scouting} prospect note${unread.scouting === 1 ? "" : "s"} ready for review.`,
        tag: "Scouting",
        tone: "warn",
        view: "inbox",
        onOpen: () => { state.inboxTab = "scouting"; },
      });
    }
    if (isObserveMode() && data.activeSave?.game_id) {
      items.push({
        title: "Observe mode active",
        detail: "You can take over a team from the calendar when you want to play hands-on.",
        tag: "Observe",
        tone: "",
        view: "calendar",
      });
    }

    return items.slice(0, 7);
  }

  function renderNeedsAttentionPanel() {
    const items = attentionItems();
    const p = panel("Needs Attention", items.length ? `${items.length} item${items.length === 1 ? "" : "s"}` : "Clear");
    const body = panelBody(p);
    const list = node("div", "today-attention-list");
    if (!items.length) {
      list.append(node("div", "empty-state", isObserveMode() ? "No league blockers right now." : "No urgent front-office decisions right now."));
    } else {
      items.forEach((item) => {
        const button = node("button", `today-attention-item ${item.tone ? `tone-${item.tone}` : ""}`.trim());
        button.type = "button";
        button.addEventListener("click", () => {
          if (item.onOpen) item.onOpen();
          switchView(item.view || "calendar", { refresh: true });
        });
        append(button, [
          append(node("div"), [
            node("strong", null, item.title),
            item.detail ? node("span", null, item.detail) : null,
          ]),
          tag(item.tag || "Open", item.tone),
        ]);
        list.append(button);
      });
    }
    body.append(list);
    return p;
  }

  function renderTeamPulsePanel() {
    const team = todayTeamInfo();
    const record = todayTeamRecord();
    const nextGame = nextTeamGame();
    const season = data.season || {};
    const injuries = data.injuries?.counts || {};
    const p = panel(isObserveMode() || !team ? "League Pulse" : `${team.abbreviation || team.abbr} Pulse`, todayPhaseLabel());
    const body = panelBody(p);
    const head = node("div", "today-pulse-head");
    if (team && !isObserveMode()) {
      append(head, [
        teamLogo(team.teamLogo || team.logo, team.abbreviation || team.abbr, "today-team-logo"),
        append(node("div"), [
          node("strong", null, team.team_name || team.name || team.abbreviation || team.abbr),
          node("span", null, `${team.conference || "NFL"}${team.division ? ` | ${team.division}` : ""}`),
        ]),
      ]);
    } else {
      append(head, [
        node("div", "today-league-mark", "NFL"),
        append(node("div"), [
          node("strong", null, isObserveMode() ? "Observe Mode" : "League Office"),
          node("span", null, isObserveMode() ? "All teams are CPU managed." : "No active user save loaded."),
        ]),
      ]);
    }
    body.append(head);
    const metrics = node("section", "metric-grid today-pulse-metrics");
    append(metrics, [
      metric("Record", recordLabel(record), record ? `${Number(record.points_for || 0)} PF / ${Number(record.points_against || 0)} PA` : "Season not started"),
      metric("Next Game", nextGame ? `${nextGame.away_team} @ ${nextGame.home_team}` : "None", nextGame ? `Week ${nextGame.week || "-"} | ${shortDate(nextGame.game_date)}` : "No matchup queued"),
      metric("Cap Space", capSpaceLabel(), data.contractNegotiations?.error ? "Needs refresh" : "Current team cap"),
      metric("Active Injuries", String(isObserveMode() ? injuries.active || 0 : injuries.userActive || 0), Number(injuries.majorActive || 0) ? `${injuries.majorActive} major league-wide` : "Medical board"),
    ]);
    body.append(metrics);
    return p;
  }

  function navigationActionCard(title, detail, view, tone, options = {}) {
    const card = node("div", `action-card ${tone || ""}`.trim());
    const text = append(node("div", "action-copy"), [
      node("strong", null, title),
      detail ? node("span", null, detail) : null,
    ]);
    const controls = node("div", "action-controls");
    const open = node("button", "primary-run-button", options.label || "Open");
    open.type = "button";
    open.addEventListener("click", () => switchView(view, { refresh: true }));
    controls.append(open);
    append(card, [text, controls]);
    return card;
  }

  function renderTodayActionsPanel() {
    const commands = data.commands || {};
    const season = data.season || {};
    const draft = data.draft || {};
    const fa = data.freeAgency || {};
    const bodyCards = [];
    const draftRemaining = Number(draft.pickTotals?.remaining || 0);
    const draftStarted = Number(draft.pickTotals?.used || 0) > 0;
    if (data.draftClassSetup?.required) {
      bodyCards.push(navigationActionCard("Choose Draft Class", "Generate a class or import a saved one before continuing the league calendar.", "calendar", "bad", { label: "Choose" }));
    } else if (draftRemaining > 0 && draftStarted) {
      bodyCards.push(navigationActionCard("Resume Draft", `${draftRemaining} picks remain. Review the board and current pick queue.`, "draft", "bad", { label: "Draft Room" }));
    } else if (Number(season.nextWeek || 0) > 0 && season.nextGameType === "REG") {
      bodyCards.push(actionCard(
        `Sim Week ${season.nextWeek}`,
        "Play the next regular-season week and refresh standings, injuries, transactions, and news.",
        commands.simNextWeek?.replace("<week>", season.nextWeek),
        "sim_week",
        { week: season.nextWeek },
        "good",
        { runLabel: "Sim Week" },
      ));
      bodyCards.push(actionCard(
        "Sim Rest Of Regular Season",
        "Run through Week 18, then stop with the playoff tree ready.",
        commands.simSeason,
        "sim_season",
        {},
        "warn",
        { runLabel: "Sim Season" },
      ));
    } else if (Number(season.postseason?.remaining || 0) > 0) {
      bodyCards.push(navigationActionCard("Continue Playoffs", "Open the playoff tree and sim the next round from there.", "playoffTree", "good", { label: "Playoffs" }));
    } else if (String(fa.status || fa.period?.current_stage || "").toLowerCase().includes("active") || Number(fa.counts?.available || 0) > 0) {
      bodyCards.push(navigationActionCard("Work Free Agency", "Review the market, team cap space, and offer decisions.", "freeAgency", "good", { label: "Free Agency" }));
    } else {
      bodyCards.push(actionCard(
        "Advance To Next Date",
        "Move to the next meaningful league event and stop for required decisions.",
        commands.advanceNextEvent,
        "advance_next_event",
        {},
        "",
        { runLabel: "Advance" },
      ));
    }
    bodyCards.push(navigationActionCard("Open Calendar", "See upcoming games, key league dates, and calendar-specific sim controls.", "calendar", "", { label: "Calendar" }));
    if (!isObserveMode()) {
      bodyCards.push(navigationActionCard("Review Roster", "Check roster health, roles, and player actions.", "roster", "", { label: "Roster" }));
    }
    const p = panel("Next Best Actions", "Context Aware");
    const body = panelBody(p);
    const grid = node("div", "today-action-grid");
    bodyCards.filter(Boolean).slice(0, 4).forEach((card) => grid.append(card));
    body.append(grid);
    return p;
  }

  function renderImportantDatesPanel() {
    const p = panel("Upcoming Dates", "League Calendar");
    const body = panelBody(p);
    const list = node("div", "list compact-list");
    (data.events || []).slice(0, 6).forEach((event) => {
      const item = row(event.event_name, `${event.phase_name || event.event_category || "League"}${event.event_time_et ? ` | ${event.event_time_et} ET` : ""}`, shortDate(event.event_start_date));
      item.classList.add("clickable-row");
      item.addEventListener("click", () => switchView("calendar", { refresh: true }));
      list.append(item);
    });
    body.append(list.children.length ? list : node("div", "empty-state", "No upcoming league dates exported."));
    return p;
  }

  function renderToday() {
    setHeader("Today", isObserveMode() ? "A live league-office dashboard for the current save." : "Your front-office dashboard for the next useful decision.");
    const root = document.createDocumentFragment();
    const season = data.season || {};
    if (runnerMode() && !state.runnerBusy && !state.calendarLoading && state.calendarLiveKey !== `${data.currentSeason || data.season?.season || ""}:${data.currentDate || data.calendar?.focusDate || ""}`) {
      loadLiveCalendar().then((changed) => {
        if (changed) scheduleRender();
      });
    }
    const heroGrid = node("div", "today-hero-grid");
    append(heroGrid, [renderTeamPulsePanel(), renderNeedsAttentionPanel()]);
    root.append(heroGrid);

    root.append(renderTodayActionsPanel());
    root.append(renderWorkflowPanel());

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

    const grid = node("div", "grid today-lower-grid");
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
    root.append(renderImportantDatesPanel());

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
      metric("Season", String(currentSeason || "-"), "Current year"),
      metric("Regular Season", `${season.totals?.played || 0}/${season.totals?.games || 0}`, `${season.totals?.remaining || 0} left`, season.totals?.remaining ? "warn" : "good"),
      metric("Next Week", season.nextWeek ? `Week ${season.nextWeek}` : "Done", "Schedule queue"),
      metric("Postseason", season.postseason?.remaining ? `${season.postseason.remaining} left` : "Idle", `${season.postseason?.games || 0} games`),
    ]);
    if (state.seasonLoading) summary.append(node("div", "empty-state", "Refreshing live season table..."));
    if (state.runnerBusy && SIM_PROGRESS_POLL_ACTIONS.has(state.busyAction)) {
      summary.append(node("div", "empty-state compact-empty", "Live standings are refreshing as games finish."));
    }
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
    const matchups = postseason.matchups || [];
    const unplayedMatchups = matchups.filter((game) => Number(game.played || 0) !== 1 && !game.winner_team).length;
    const remainingPlayoffGames = Math.max(Number(postseason.remaining || 0), unplayedMatchups);
    const currentSeason = season.season || data.currentSeason || "";
    if (runnerMode() && String(state.seasonLiveSeason || "") !== String(currentSeason || "") && !state.seasonLoading) {
      loadLiveSeason().then(render);
    }

    const summary = node("section", "league-table-summary playoff-summary");
    append(summary, [
      metric("Regular Season", Number(season.totals?.remaining || 0) ? "In Progress" : "Complete", `${season.totals?.played || 0}/${season.totals?.games || 0}`),
      metric("Playoff Games", `${postseason.played || 0}/${postseason.games || 0}`, `${remainingPlayoffGames} remaining`, remainingPlayoffGames ? "warn" : Number(postseason.games || 0) ? "good" : ""),
      metric("Rounds", String((postseason.rounds || []).length || "-"), "Bracket stages"),
    ]);
    const controls = node("div", "control-bar playoff-tree-actions");
    const simRound = node("button", "control-button good", "Sim Playoff Round");
    simRound.type = "button";
    simRound.disabled = !runnerMode() || state.runnerBusy || remainingPlayoffGames <= 0;
    simRound.title = remainingPlayoffGames > 0 ? "Sim the next unplayed playoff round." : "No unplayed playoff games remain.";
    simRound.addEventListener("click", () => runAction("postseason_round", {}));
    controls.append(simRound);
    summary.append(controls);
    if (state.seasonLoading) summary.append(node("div", "empty-state", "Refreshing playoff tree..."));
    root.append(summary);

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

    const bracket = panel("Bracket", "Playoff path");
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
      const teamButton = node("button", "standings-team", null);
      teamButton.type = "button";
      teamButton.title = `Open ${team.abbreviation || "team"} roster`;
      teamButton.addEventListener("click", () => openRosterTeam(team.abbreviation));
      append(teamButton, [
        teamLogo(team.teamLogo, team.abbreviation, "standings-team-logo"),
        append(node("span", "standings-team-copy"), [
          node("strong", null, team.abbreviation || "-"),
          node("span", null, team.team_name || ""),
        ]),
      ]);
      append(tr, [
        append(node("td", null), [teamButton]),
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
    const availableRows = (fa.board || []).filter((player) => !player.market_status || player.market_status === "available");
    const period = fa.period;
    const userTeam = data.activeSave?.user_team || data.contractNegotiations?.team || "User";
    const currentCap = data.contractNegotiations?.currentCap || data.contractNegotiations?.cap || {};
    const capSpace = Number(currentCap.cap_space ?? currentCap.capSpace ?? currentCap.available_cap ?? currentCap.availableCap ?? 0);
    const stagedSpend = freeAgencyQueuedOfferSpend();
    const projectedCap = capSpace - stagedSpend;
    const status = panel("Market Desk", period ? freeAgencyStageLabel(period.current_stage) : "Not Started");
    status.classList.add("fa-status-panel");
    if (state.freeAgencyLoading) {
      panelBody(status).append(node("div", "empty-state", "Refreshing live free agency..."));
    }
    const metrics = node("section", "metric-grid fa-market-metrics");
    append(metrics, [
      metric("Available", String(fa.counts.available || 0), "Open market"),
      metric("Signed", String(fa.counts.signed || 0), "Completed deals"),
      metric("Pending Offers", String(fa.counts.pendingOffers || 0), "Awaiting decisions", fa.counts.pendingOffers ? "warn" : ""),
      metric(`${userTeam} Cap`, money(capSpace), `Current ${currentCap.season || data.currentSeason || ""}`.trim(), capSpace < 0 ? "bad" : ""),
      metric("After Queued", money(projectedCap), `${pendingFreeAgencyOfferEntries().length} queued offer(s)`, projectedCap < 0 ? "bad" : stagedSpend ? "warn" : ""),
      metric("Clock", period ? `${shortDate(period.current_date)} ${period.current_stage === "day_one_hourly" ? `${period.current_hour}:00` : ""}` : shortDate(fa.startDate), period ? "Market clock" : "Scheduled start"),
    ]);
    panelBody(status).append(metrics);
    panelBody(status).append(freeAgencyMarketBrief(fa, availableRows, capSpace, stagedSpend));

    const commands = data.commands || {};
    const dashboard = node("div", "fa-dashboard");
    const sideStack = node("div", "fa-dashboard-stack");
    const controlsPanel = freeAgencyControlPanel(fa, commands);
    append(sideStack, [
      controlsPanel,
      freeAgencyOfferQueuePanel(fa),
      freeAgencyReasonPanel(fa),
      freeAgencyEventPanel(fa),
    ]);
    append(dashboard, [status, sideStack]);
    root.append(dashboard);

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
    const boardPanel = panel("Available Free Agents", marketPanelKicker(activePosition, activeTier, boardRows.length, Number(fa.counts.available || availableRows.length)));
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
      state.selectedFreeAgentPlayerId = null;
      render();
    });
    filterLabel.append(select);
    const tierTabs = node("div", "fa-tier-tabs");
    tiers.forEach((tier) => {
      const tab = node("button", `fa-tier-tab ${tier.value === activeTier ? "active" : ""}`.trim(), `${tier.label} ${tier.count}`);
      tab.type = "button";
      tab.addEventListener("click", () => {
        state.freeAgencyTierFilter = tier.value;
        state.selectedFreeAgentPlayerId = null;
        render();
      });
      tierTabs.append(tab);
    });
    filterRow.append(filterLabel, tierTabs);
    panelBody(boardPanel).append(filterRow);
    const sortedRows = sortedFreeAgencyRows(boardRows);
    const selectedFreeAgent = selectedFreeAgencyPlayer(sortedRows);
    const marketLayout = node("div", "fa-market-layout");
    append(marketLayout, [
      freeAgencyMarketTable(sortedRows, selectedFreeAgent, capSpace),
      freeAgencyPlayerPanel(selectedFreeAgent, capSpace),
    ]);
    panelBody(boardPanel).append(marketLayout);
    root.append(boardPanel);
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function freeAgencyQueuedOfferSpend() {
    return pendingFreeAgencyOfferEntries().reduce((total, offer) => total + Number(offer.aav || 0), 0);
  }

  function freeAgencyMarketCounts(players) {
    const counts = { premium: 0, starter: 0, rotation: 0, depth: 0, other: 0 };
    players.forEach((player) => {
      counts[freeAgencyTierValue(player.market_tier)] += 1;
    });
    return counts;
  }

  function freeAgencyPositionMarket(players) {
    const byPosition = new Map();
    players.forEach((player) => {
      const position = player.position || "-";
      const row = byPosition.get(position) || { position, impact: 0, total: 0 };
      row.total += 1;
      const tier = freeAgencyTierValue(player.market_tier);
      if (tier === "premium" || tier === "starter") row.impact += 1;
      byPosition.set(position, row);
    });
    return [...byPosition.values()]
      .filter((row) => row.impact > 0)
      .sort((a, b) => b.impact - a.impact || footballPositionSort(a.position, b.position))
      .slice(0, 5);
  }

  function freeAgencyUserNeeds() {
    const userTeam = String(data.activeSave?.user_team || "").toUpperCase();
    const depth = data.depthChart || {};
    if (!userTeam || String(depth.team || "").toUpperCase() !== userTeam) return [];
    const activeRows = (depth.roster || []).filter((player) => {
      const status = String(player.status || "").toLowerCase();
      return status && status !== "practice squad" && !status.includes("retired") && !status.includes("free");
    });
    if (!activeRows.length) return [];
    const targets = {
      QB: { min: 2, quality: 78 },
      RB: { min: 3, quality: 74 },
      WR: { min: 5, quality: 76 },
      TE: { min: 3, quality: 74 },
      OL: { min: 8, quality: 76 },
      DL: { min: 5, quality: 75 },
      EDGE: { min: 4, quality: 76 },
      LB: { min: 4, quality: 73 },
      CB: { min: 5, quality: 76 },
      S: { min: 4, quality: 74 },
      ST: { min: 3, quality: 70 },
    };
    const groups = new Map();
    activeRows.forEach((player) => {
      const group = rosterGroupForPosition(player.position);
      if (!targets[group]) return;
      const bucket = groups.get(group) || { group, count: 0, top: 0 };
      bucket.count += 1;
      bucket.top = Math.max(bucket.top, Number(player.overall || 0));
      groups.set(group, bucket);
    });
    return Object.entries(targets).map(([group, target]) => {
      const bucket = groups.get(group) || { group, count: 0, top: 0 };
      const shortage = Math.max(0, target.min - bucket.count);
      const qualityGap = Math.max(0, target.quality - bucket.top);
      const score = shortage * 35 + qualityGap * 2;
      const drivers = [];
      if (shortage) drivers.push(`${shortage} short`);
      if (qualityGap) drivers.push(`top ${bucket.top || "-"}`);
      return {
        group,
        score,
        count: bucket.count,
        top: bucket.top,
        detail: drivers.join(" | ") || `${bucket.count} rostered`,
      };
    }).filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score || footballPositionSort(a.group, b.group))
      .slice(0, 5);
  }

  function freeAgencyMarketGuidance(fa, capSpace, stagedSpend) {
    const period = fa.period;
    const stage = String(period?.current_stage || "");
    const projectedCap = Number(capSpace || 0) - Number(stagedSpend || 0);
    if (!period) return "Open the market when contract decisions are finished. Top players will move fastest once bidding begins.";
    if (projectedCap < 0) return "Queued offers would push the club over the cap. Clear space or lower guarantees before submitting.";
    if (stage === "day_one_hourly") return "Opening day rewards decisive bids for premium needs. Expect bidding wars before players start choosing.";
    if (stage === "daily") return "The top wave is thinning out. Balance need, price, and whether a cheaper post-draft fit may be available.";
    if (stage === "street_market") return "This is the value market. Demands are softer, but established veterans may wait for camp injuries.";
    return "Use this board to compare need, price, market pressure, and cap fit before staging offers.";
  }

  function freeAgencyMarketLabel(player) {
    const label = String(player?.market_temperature || "").trim();
    if (label) return label;
    const offers = Number(player?.pending_offers || 0);
    const heat = Number(player?.market_heat || 0);
    if (offers >= 3 || heat >= 90) return "Hot";
    if (offers > 0 || heat >= 74) return "Warm";
    if (heat <= 35) return "Cooling";
    return "Steady";
  }

  function freeAgencyMarketTone(player) {
    const tone = String(player?.market_temperature_tone || "").trim();
    if (tone) return tone;
    const label = freeAgencyMarketLabel(player).toLowerCase();
    if (label === "hot") return "warn";
    if (label === "warm") return "good";
    if (label === "cooling" || label === "cold" || label === "patient") return "quiet";
    return "";
  }

  function freeAgencyDemandLabel(player) {
    return String(player?.demand_movement || "").trim() || "Holding";
  }

  function freeAgencyDemandTone(player) {
    const tone = String(player?.demand_movement_tone || "").trim();
    if (tone) return tone;
    const label = freeAgencyDemandLabel(player).toLowerCase();
    if (label === "rising") return "warn";
    if (label === "softening" || label === "at floor") return "good";
    if (label === "patient") return "quiet";
    return "";
  }

  function freeAgencyMarketPill(player) {
    const label = freeAgencyMarketLabel(player);
    const pill = node("span", `fa-market-pill ${freeAgencyMarketTone(player)}`.trim());
    pill.textContent = label;
    pill.title = player?.market_temperature_note || player?.market_clock_label || "Current market temperature";
    return pill;
  }

  function freeAgencyDemandCell(player) {
    const wrap = node("span", `fa-demand-cell ${freeAgencyDemandTone(player)}`.trim());
    append(wrap, [
      node("strong", null, freeAgencyDemandLabel(player)),
      node("small", null, player?.market_clock_label || "Market watch"),
    ]);
    wrap.title = player?.demand_note || "Demand movement";
    return wrap;
  }

  function freeAgencyMarketPressureCounts(players) {
    const counts = { hot: 0, warm: 0, cooling: 0, softening: 0, rising: 0 };
    players.forEach((player) => {
      const market = freeAgencyMarketLabel(player).toLowerCase();
      const demand = freeAgencyDemandLabel(player).toLowerCase();
      if (market === "hot") counts.hot += 1;
      if (market === "warm") counts.warm += 1;
      if (market === "cooling" || market === "cold") counts.cooling += 1;
      if (demand === "softening" || demand === "at floor") counts.softening += 1;
      if (demand === "rising") counts.rising += 1;
    });
    return counts;
  }

  function freeAgencyBriefChip(label, value, detail, tone = "") {
    const item = node("div", `fa-brief-chip ${tone}`.trim());
    append(item, [
      node("span", null, label),
      node("strong", null, value),
      node("small", null, detail),
    ]);
    return item;
  }

  function freeAgencyMarketBrief(fa, players, capSpace, stagedSpend) {
    const counts = freeAgencyMarketCounts(players);
    const pressure = freeAgencyMarketPressureCounts(players);
    const projectedCap = Number(capSpace || 0) - Number(stagedSpend || 0);
    const brief = node("div", "fa-market-brief");
    const head = node("div", "fa-brief-head");
    append(head, [
      append(node("div", "fa-brief-copy"), [
        node("strong", null, fa.period ? freeAgencyStageLabel(fa.period.current_stage) : "Market Not Open"),
        node("span", null, freeAgencyMarketGuidance(fa, capSpace, stagedSpend)),
      ]),
      tag(projectedCap < 0 ? "Cap Move Needed" : stagedSpend ? "Offers Staged" : "Ready", projectedCap < 0 ? "bad" : stagedSpend ? "warn" : "good"),
    ]);
    const strip = node("div", "fa-brief-strip");
    append(strip, [
      freeAgencyBriefChip("Premium", String(counts.premium || 0), "impact players", counts.premium ? "warn" : ""),
      freeAgencyBriefChip("Starters", String(counts.starter || 0), "plug-in options", counts.starter ? "" : "quiet"),
      freeAgencyBriefChip("Hot Markets", String(pressure.hot + pressure.rising), `${pressure.rising} rising`, pressure.hot || pressure.rising ? "warn" : "quiet"),
      freeAgencyBriefChip("Softening", String(pressure.softening + pressure.cooling), `${pressure.cooling} cooling`, pressure.softening || pressure.cooling ? "good" : "quiet"),
      freeAgencyBriefChip("Queued Spend", money(stagedSpend || 0), `${pendingFreeAgencyOfferEntries().length} offer(s)`, stagedSpend ? "warn" : "quiet"),
      freeAgencyBriefChip("Projected Cap", money(projectedCap), "after queued AAV", projectedCap < 0 ? "bad" : ""),
    ]);
    const needs = freeAgencyUserNeeds();
    const marketPositions = freeAgencyPositionMarket(players);
    const bottom = node("div", "fa-brief-bottom");
    bottom.append(freeAgencySignalList("Roster Needs", needs.map((need) => ({
      label: need.group,
      value: need.detail,
      tone: need.score >= 30 ? "warn" : "",
    })), isObserveMode() ? "Observe mode is showing the league market without a user roster plan." : "No urgent roster holes detected from the loaded depth chart."));
    bottom.append(freeAgencySignalList("Market Supply", marketPositions.map((item) => ({
      label: item.position,
      value: `${item.impact} starter-tier | ${item.total} total`,
    })), "No starter-tier supply remains in the current market filters."));
    append(brief, [head, strip, bottom]);
    return brief;
  }

  function freeAgencySignalList(title, items, emptyText) {
    const wrap = node("div", "fa-signal-list");
    wrap.append(node("span", "fa-signal-title", title));
    if (!items.length) {
      wrap.append(node("small", "muted", emptyText));
      return wrap;
    }
    items.slice(0, 5).forEach((item) => {
      const rowNode = node("div", `fa-signal-row ${item.tone || ""}`.trim());
      rowNode.append(node("strong", null, item.label), node("span", null, item.value));
      wrap.append(rowNode);
    });
    return wrap;
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

  function freeAgencyReasonPanel(fa) {
    const seen = new Set();
    const reasons = [];
    (fa.decisionExplanations || [])
      .filter((item) => String(item.decision_type || "").startsWith("cpu_"))
      .forEach((item) => {
        const key = `${item.team_id || item.team || ""}:${item.player_id || item.player_name || ""}`;
        if (seen.has(key) || reasons.length >= 8) return;
        seen.add(key);
        reasons.push(item);
      });
    const reasonPanel = panel("Why Teams Are Bidding", `${reasons.length} recent`);
    reasonPanel.classList.add("fa-mini-panel", "fa-reason-panel");
    const list = node("div", "fa-reason-list");
    reasons.forEach((item) => {
      const details = item.details || {};
      const summary = item.reason_summary || "No explanation recorded.";
      const top = node("div", "fa-reason-top");
      append(top, [
        teamLogo(item.teamLogo, item.team, "team-mini-logo"),
        append(node("span", "fa-reason-title"), [
          node("strong", null, `${item.team || "-"} ${item.player_name || "Free agent"}`),
          node("small", null, `${freeAgencyDecisionLabel(item.decision_type)} | ${item.position_group || item.position || "-"} | ${item.years || 1} yr ${money(item.aav || 0)}`),
        ]),
      ]);
      const payload = whyPayloadFromDecision(item);
      const chips = node("div", "fa-reason-chips");
      [
        details.needLabel ? details.needLabel : item.need_score !== null && item.need_score !== undefined ? `need ${Number(item.need_score).toFixed(0)}` : "",
        details.valueLabel || "",
        details.capSpaceBefore !== undefined && details.projectedCapAfter !== undefined ? `${money(details.capSpaceBefore)} to ${money(details.projectedCapAfter)}` : "",
        details.schemeFitScore !== null && details.schemeFitScore !== undefined ? `fit ${Number(details.schemeFitScore).toFixed(0)}` : "",
        details.bridgeQbPlan ? "bridge QB" : "",
      ].filter(Boolean).forEach((label) => chips.append(tag(label, String(label).includes("premium") || String(label).includes("bidding") ? "warn" : "")));
      const itemNode = node("div", "fa-reason-item");
      append(itemNode, [top, node("p", null, summary), append(node("div", "fa-reason-actions"), [chips, whyButton(payload, "Details")])]);
      list.append(itemNode);
    });
    panelBody(reasonPanel).append(list.children.length ? list : node("div", "empty-state", "Team bidding explanations will appear as offers and signings come in."));
    return reasonPanel;
  }

  function freeAgencyDecisionLabel(value) {
    const text = String(value || "").replace(/^cpu_/, "").replaceAll("_", " ");
    return text ? text[0].toUpperCase() + text.slice(1) : "Decision";
  }

  function whyScoreText(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "";
    return Number.isInteger(number) ? String(number) : number.toFixed(1);
  }

  function whyPayloadFromDecision(item, context = {}) {
    if (!item) return null;
    const details = item.details || item.gm_reason || {};
    const decisionType = item.decisionType || item.decision_type || "";
    const team = item.team || context.team || "";
    const player = item.player || item.player_name || context.player || "";
    const position = item.position || item.positionGroup || item.position_group || context.position || "";
    const titleParts = [
      team,
      player || context.title || "Decision",
    ].filter(Boolean);
    const subtitleParts = [
      freeAgencyDecisionLabel(decisionType),
      position,
      item.marketTier || item.market_tier || "",
      item.years || context.years ? `${item.years || context.years} yr` : "",
      item.aav || context.aav ? money(item.aav || context.aav) : "",
    ].filter(Boolean);
    return {
      title: context.title || titleParts.join(" | ") || "Why This Happened",
      subtitle: subtitleParts.join(" | "),
      summary: item.summary || item.reason_summary || context.summary || "No front-office explanation was archived for this move.",
      team,
      teamName: item.teamName || item.team_name || "",
      teamLogo: item.teamLogo || context.teamLogo || "",
      playerId: item.playerId || item.player_id || context.playerId || null,
      player,
      position,
      decisionType,
      details,
      chips: whyDecisionChips(item, details),
      rows: whyDecisionRows(item, details),
    };
  }

  function whyDecisionChips(item, details = {}) {
    const chips = [];
    const needScore = item.needScore ?? item.need_score;
    const valueScore = item.valueScore ?? item.value_score;
    const fitScore = item.schemeFitScore ?? item.scheme_fit_score;
    if (details.needLabel) chips.push({ label: details.needLabel, tone: "warn" });
    else if (needScore !== null && needScore !== undefined) chips.push({ label: `Need ${whyScoreText(needScore)}`, tone: Number(needScore) >= 75 ? "warn" : "" });
    if (details.valueLabel) chips.push({ label: details.valueLabel, tone: String(details.valueLabel).toLowerCase().includes("premium") ? "warn" : "" });
    else if (valueScore !== null && valueScore !== undefined) chips.push({ label: `Value ${whyScoreText(valueScore)}`, tone: Number(valueScore) >= 75 ? "good" : "" });
    if (fitScore !== null && fitScore !== undefined) chips.push({ label: `Fit ${whyScoreText(fitScore)}`, tone: Number(fitScore) >= 75 ? "good" : "" });
    if (item.bridgeQbPlan || item.bridge_qb_plan || details.bridgeQbPlan) chips.push({ label: "Bridge QB plan", tone: "warn" });
    if (item.rolePlan || item.role_plan) chips.push({ label: item.rolePlan || item.role_plan, tone: "" });
    if (item.marketTier || item.market_tier) chips.push({ label: item.marketTier || item.market_tier, tone: "" });
    return chips.slice(0, 7);
  }

  function whyDecisionRows(item, details = {}) {
    const rows = [];
    const before = item.capSpaceBefore ?? item.cap_space_before ?? details.capSpaceBefore;
    const after = item.projectedCapAfter ?? item.projected_cap_after ?? details.projectedCapAfter;
    if (before !== undefined && before !== null && after !== undefined && after !== null) {
      rows.push(["Cap Outlook", `${money(before)} before, ${money(after)} after`]);
    }
    const needScore = item.needScore ?? item.need_score;
    if (needScore !== null && needScore !== undefined) {
      rows.push(["Need Score", whyScoreText(needScore)]);
    }
    const valueScore = item.valueScore ?? item.value_score;
    if (valueScore !== null && valueScore !== undefined) {
      rows.push(["Value Score", whyScoreText(valueScore)]);
    }
    const fitScore = item.schemeFitScore ?? item.scheme_fit_score;
    if (fitScore !== null && fitScore !== undefined) {
      rows.push(["Scheme Fit", whyScoreText(fitScore)]);
    }
    if (item.rolePlan || item.role_plan) rows.push(["Role Plan", item.rolePlan || item.role_plan]);
    if (item.marketContext || item.market_context) rows.push(["Market Context", item.marketContext || item.market_context]);
    if (details.roomContext?.starterFloor !== undefined) rows.push(["Room Floor", `${details.roomContext.starterFloor} current floor`]);
    if (details.bridgeContext?.target) rows.push(["Draft Context", `Bridge plan for ${details.bridgeContext.target}`]);
    return rows;
  }

  function whyButton(payload, label = "Why?") {
    if (!payload) return null;
    const button = node("button", "why-inline-button", label);
    button.type = "button";
    button.title = "Show the front-office reasoning behind this move.";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      state.whyModal = payload;
      render();
    });
    return button;
  }

  function closeWhyModal() {
    state.whyModal = null;
    render();
  }

  function whyThisHappenedModal() {
    const payload = state.whyModal;
    if (!payload) return null;
    const overlay = node("div", "box-score-modal-overlay why-modal-overlay");
    const modal = node("section", "box-score-modal why-modal");
    const title = node("div", "box-score-modal-title");
    append(title, [
      node("strong", null, "Why This Happened"),
      node("small", null, payload.subtitle || "Front-office context"),
    ]);
    const close = node("button", "ghost-button", "Close");
    close.type = "button";
    close.addEventListener("click", closeWhyModal);
    const top = append(node("div", "box-score-modal-top"), [title, close]);
    const body = node("div", "box-score-modal-body why-modal-body");
    const header = node("div", "why-modal-subject");
    append(header, [
      teamLogo(payload.teamLogo, payload.team, "team-mini-logo"),
      append(node("div"), [
        node("strong", null, payload.title || "Decision"),
        payload.teamName ? node("span", null, payload.teamName) : null,
      ]),
    ]);
    body.append(header, node("p", "why-modal-summary", payload.summary || "No explanation was archived for this move."));
    if ((payload.chips || []).length) {
      const chips = node("div", "why-modal-chips");
      payload.chips.forEach((chip) => chips.append(tag(chip.label, chip.tone)));
      body.append(chips);
    }
    if ((payload.rows || []).length) {
      const details = node("div", "why-modal-details");
      payload.rows.forEach(([label, value]) => {
        details.append(append(node("div", "why-modal-detail-row"), [
          node("span", null, label),
          node("strong", null, value),
        ]));
      });
      body.append(details);
    }
    append(modal, [top, body]);
    overlay.append(modal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeWhyModal();
    });
    return overlay;
  }

  function freeAgencyOfferQueuePanel(fa) {
    const offers = (fa.offers || []).filter((offer) => String(offer.status || "").toLowerCase() === "pending");
    const staged = pendingFreeAgencyOfferEntries();
    const offerPanel = panel("Offer Status", `${staged.length} staged | ${offers.length} pending`);
    offerPanel.classList.add("fa-mini-panel");
    const list = node("div", "fa-offer-list");
    staged.slice(0, 4).forEach((offer) => {
      const item = node("div", "fa-offer-item staged-offer-item");
      append(item, [
        append(node("span", "fa-offer-player"), [
          node("strong", null, offer.player_name || "Player"),
          node("small", null, `${offer.previous_team || "FA"} | staged`),
        ]),
        append(node("span", "fa-offer-money"), [
          node("strong", null, `${offer.years || 1} yr ${money(offer.aav || 0)}`),
          tag("Pending Changes", "warn"),
        ]),
      ]);
      list.append(item);
    });
    offers.slice(0, 6).forEach((offer) => {
      const item = node("div", "fa-offer-item");
      append(item, [
        append(node("span", "fa-offer-player"), [
          node("strong", null, offer.player_name || offer.name || "Player"),
          node("small", null, `${offer.team || offer.team_abbr || "-"} | ${offer.status || "pending"}`),
        ]),
        node("span", "fa-offer-money", `${offer.years || offer.contract_years || 1} yr ${money(offer.aav || offer.average_annual_value || 0)}`),
      ]);
      if (offer.gm_reason_summary) {
        item.append(node("p", "fa-offer-reason", offer.gm_reason_summary));
      }
      list.append(item);
    });
    panelBody(offerPanel).append(list.children.length ? list : node("div", "empty-state", "No staged or pending offers."));
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

  function freeAgencySortValue(player, key) {
    if (key === "name") return String(player.player_name || "");
    if (key === "pos") return FOOTBALL_POSITION_ORDER.indexOf(player.position) >= 0 ? FOOTBALL_POSITION_ORDER.indexOf(player.position) : 99;
    if (key === "age") return Number(player.age || 0);
    if (key === "overall") return Number(player.overall || player.market_score || 0);
    if (key === "potential") return Number(player.potential || player.overall || 0);
    if (key === "tier") return ["premium", "starter", "rotation", "depth", "other"].indexOf(freeAgencyTierValue(player.market_tier));
    if (key === "ask") return Number(player.offer_floor_aav || player.asking_aav || player.minimum_aav || 0);
    if (key === "offers") return Number(player.pending_offers || 0);
    if (key === "leader") return Number(player.best_aav || 0);
    if (key === "temperature") {
      const order = { hot: 5, warm: 4, steady: 3, patient: 2, cooling: 1, cold: 0 };
      return (order[freeAgencyMarketLabel(player).toLowerCase()] ?? 2) * 100 + Number(player.market_heat || 0);
    }
    if (key === "demand") {
      const order = { rising: 5, "opening ask": 4, holding: 3, patient: 2, softening: 1, "at floor": 0 };
      return order[freeAgencyDemandLabel(player).toLowerCase()] ?? 2;
    }
    return Number(player.market_heat || player.market_score || player.overall || 0);
  }

  function sortedFreeAgencyRows(rows) {
    const { key, direction } = state.freeAgencySort || { key: "heat", direction: "desc" };
    const dir = direction === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = freeAgencySortValue(a, key);
      const bv = freeAgencySortValue(b, key);
      if (typeof av === "string" || typeof bv === "string") return String(av).localeCompare(String(bv)) * dir;
      if (av !== bv) return (av - bv) * dir;
      return footballPositionSort(a.position, b.position) || String(a.player_name || "").localeCompare(String(b.player_name || ""));
    });
  }

  function freeAgencySortButton(label, key) {
    const active = state.freeAgencySort?.key === key;
    const direction = active ? state.freeAgencySort.direction : "desc";
    const button = node("button", `table-sort-button ${active ? "active" : ""}`.trim(), `${label}${active ? (direction === "asc" ? " ↑" : " ↓") : ""}`);
    button.type = "button";
    button.addEventListener("click", () => {
      state.freeAgencySort = {
        key,
        direction: active && direction === "desc" ? "asc" : "desc",
      };
      render();
    });
    return button;
  }

  function freeAgencyTableHeader(label, key, className = "") {
    const th = node("th", className);
    if (!key) {
      th.textContent = label || "";
      return th;
    }
    th.append(freeAgencySortButton(label, key));
    return th;
  }

  function selectedFreeAgencyPlayer(rows) {
    const selected = rows.find((player) => String(player.player_id) === String(state.selectedFreeAgentPlayerId));
    if (selected) return selected;
    const first = rows[0] || null;
    state.selectedFreeAgentPlayerId = first?.player_id ? String(first.player_id) : null;
    return first;
  }

  function freeAgencyMarketTable(players, selected, capSpace) {
    if (!players.length) return node("div", "empty-state", "No free agents match these filters.");
    const wrap = node("div", "table-wrap roster-table-wrap fa-market-table-wrap");
    const tableEl = node("table", "data-table roster-table fa-market-table");
    const colGroup = node("colgroup");
    [
      "roster-col-photo",
      "fa-col-player",
      "roster-col-position",
      "roster-col-rating",
      "roster-col-rating",
      "roster-col-age",
      "fa-col-tier",
      "fa-col-money",
      "fa-col-market",
      "fa-col-demand",
      "fa-col-offers",
      "fa-col-leader",
    ].forEach((className) => colGroup.append(node("col", className)));
    const head = node("thead");
    const headRow = node("tr");
    [
      freeAgencyTableHeader("", null, "roster-photo-head"),
      freeAgencyTableHeader("Player", "name"),
      freeAgencyTableHeader("Pos", "pos", "center"),
      freeAgencyTableHeader("OVR", "overall", "center"),
      freeAgencyTableHeader("POT", "potential", "center"),
      freeAgencyTableHeader("Age", "age", "center"),
      freeAgencyTableHeader("Tier", "tier"),
      freeAgencyTableHeader("Ask", "ask"),
      freeAgencyTableHeader("Market", "temperature"),
      freeAgencyTableHeader("Demand", "demand"),
      freeAgencyTableHeader("Offers", "offers", "center"),
      freeAgencyTableHeader("Leader", "leader"),
    ].forEach((cell) => headRow.append(cell));
    head.append(headRow);
    const body = node("tbody");
    players.forEach((player) => {
      const ask = Number(player.offer_floor_aav || player.asking_aav || player.minimum_aav || 0);
      const capAfter = Number(capSpace || 0) - ask;
      const tr = node("tr", String(player.player_id) === String(selected?.player_id) ? "selected" : "");
      tr.tabIndex = 0;
      tr.addEventListener("click", () => {
        state.selectedFreeAgentPlayerId = String(player.player_id);
        render();
      });
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          state.selectedFreeAgentPlayerId = String(player.player_id);
          render();
        }
      });
      const playerCell = node("td");
      playerCell.append(smallPlayerCell(player.player_id, player.player_name, `${player.previous_team || player.team || "FA"} | ${experienceLabel(player)}`, {
        team: player.previous_team || player.team,
        position: player.position,
      }));
      append(tr, [
        append(node("td", "roster-photo-cell"), [rosterHeadshot(player)]),
        playerCell,
        node("td", "center", player.position || "-"),
        node("td", "center rating-cell", player.overall ?? player.market_score ?? "-"),
        node("td", "center rating-cell", player.potential ?? "-"),
        node("td", "center", player.age ?? "-"),
        append(node("td", null), [tag(player.market_tier || "Market")]),
        node("td", "numeric", money(ask)),
        append(node("td", null), [freeAgencyMarketPill(player)]),
        append(node("td", null), [freeAgencyDemandCell(player)]),
        node("td", "center", String(player.pending_offers || 0)),
        append(node("td", null), [player.best_aav ? leadingBidCell(player) : node("span", "muted", capAfter < 0 ? "No bid | over cap" : "No bid")]),
      ]);
      body.append(tr);
    });
    tableEl.append(colGroup, head, body);
    wrap.append(tableEl);
    return wrap;
  }

  function freeAgencyPlayerPanel(player, capSpace) {
    const p = panel("Free-Agent Detail", player ? `${player.position || "FA"} | ${player.market_tier || "Market"}` : "Select a player");
    p.classList.add("fa-player-panel");
    const body = panelBody(p);
    if (!player) {
      body.append(node("div", "empty-state", "Choose a free agent from the market table."));
      return p;
    }
    const ask = Number(player.offer_floor_aav || player.asking_aav || player.minimum_aav || 0);
    const minimum = Number(player.minimum_aav || 0);
    const capAfter = Number(capSpace || 0) - ask;
    const top = node("div", "roster-player-panel-head fa-player-panel-head");
    append(top, [
      rosterHeadshot(player),
      append(node("div", "fa-panel-title"), [
        playerLink(player.player_id, player.player_name, "player-link strong-link", {
          team: player.previous_team || player.team,
          position: player.position,
        }),
        node("span", null, `${player.previous_team || player.team || "FA"} | Age ${player.age ?? "-"} | ${experienceLabel(player)}`),
      ]),
    ]);
    const facts = node("section", "metric-grid roster-card-facts roster-action-facts fa-player-facts");
    append(facts, [
      metric("Ask", money(ask), minimum ? `Floor ${money(minimum)}` : "Current demand"),
      metric("Cap After", money(capAfter), "If signed at ask", capAfter < 0 ? "bad" : ""),
      metric("Rating", `${player.overall ?? player.market_score ?? "-"}/${player.potential ?? "-"}`, "OVR/POT"),
      metric("Market", freeAgencyMarketLabel(player), player.market_temperature_note || `${player.pending_offers || 0} offer(s)`, freeAgencyMarketTone(player)),
      metric("Demand", freeAgencyDemandLabel(player), player.demand_note || player.market_clock_label || "Market watch", freeAgencyDemandTone(player)),
      metric("Preference", String(player.preference_archetype || "balanced").replaceAll("_", " "), `${player.contract_year_preference || player.preferred_years || 1} yr pref`),
      metric("Role", `${player.role_priority || 10}/20`, "Priority"),
    ]);
    const notes = player.signing_notes || player.motivation || player.holdout_reason;
    append(body, [top, facts]);
    if (notes) {
      body.append(sectionBlock("Player Read", node("p", "fa-detail-note", notes)));
    }
    body.append(sectionBlock("Market Fit", freeAgencyPlayerFitList(player, capSpace)));
    body.append(sectionBlock("Offer", freeAgencyOfferButton(player)));
    return p;
  }

  function freeAgencyPreferenceDrivers(player) {
    return [
      ["Money", player.money_priority],
      ["Role", player.role_priority],
      ["Security", player.security_priority],
      ["Contender", player.contender_priority],
      ["Location", player.location_priority],
      ["Loyalty", player.loyalty_priority],
    ].map(([label, value]) => ({ label, value: Number(value || 0) }))
      .filter((item) => item.value > 0)
      .sort((a, b) => b.value - a.value)
      .slice(0, 2);
  }

  function freeAgencyPlayerFitNotes(player, capSpace) {
    const ask = Number(player.offer_floor_aav || player.asking_aav || player.minimum_aav || 0);
    const minimum = Number(player.minimum_aav || 0);
    const capAfter = Number(capSpace || 0) - ask;
    const notes = [];
    if (capAfter < 0) {
      notes.push({ label: "Cap Fit", value: `${money(Math.abs(capAfter))} over current space`, tone: "bad" });
    } else if (capAfter < 3_000_000) {
      notes.push({ label: "Cap Fit", value: "Leaves very little operating room", tone: "warn" });
    } else {
      notes.push({ label: "Cap Fit", value: `${money(capAfter)} remaining at ask`, tone: "" });
    }
    const offers = Number(player.pending_offers || 0);
    if (player.market_temperature_note) {
      notes.push({ label: "Market", value: player.market_temperature_note, tone: freeAgencyMarketTone(player) });
    }
    if (offers >= 3) {
      notes.push({ label: "Market", value: `${offers} active bids; expect a premium`, tone: "warn" });
    } else if (offers > 0) {
      notes.push({ label: "Market", value: `${offers} active bid${offers === 1 ? "" : "s"}`, tone: "" });
    } else {
      notes.push({ label: "Market", value: "Quiet market so far", tone: "" });
    }
    const tier = String(player.market_tier || "").toLowerCase();
    const age = Number(player.age || 0);
    if (tier.includes("premium") && age <= 29) {
      notes.push({ label: "Role", value: "Should be treated as an impact starter", tone: "good" });
    } else if (tier.includes("starter")) {
      notes.push({ label: "Role", value: "Starter-level option if the room has a hole", tone: "" });
    } else if (age >= 31) {
      notes.push({ label: "Role", value: "Veteran depth or bridge plan", tone: "" });
    }
    if (minimum && ask > minimum) {
      notes.push({ label: "Demand", value: `${money(ask - minimum)} above floor`, tone: ask > minimum * 1.35 ? "warn" : "" });
    }
    if (player.demand_note) {
      notes.push({ label: "Demand", value: player.demand_note, tone: freeAgencyDemandTone(player) });
    }
    freeAgencyPreferenceDrivers(player).forEach((driver) => {
      if (driver.value >= 13) notes.push({ label: "Preference", value: `${driver.label} matters`, tone: driver.label === "Money" ? "warn" : "" });
    });
    if (player.post_draft_strategy) {
      notes.push({ label: "Patience", value: roleLabel(player.post_draft_strategy), tone: "" });
    }
    if (player.holdout_until || player.holdout_reason) {
      notes.push({ label: "Timing", value: player.holdout_reason || `Waiting until ${shortDate(player.holdout_until)}`, tone: "warn" });
    }
    return notes.slice(0, 6);
  }

  function freeAgencyPlayerFitList(player, capSpace) {
    const list = node("div", "fa-fit-list");
    freeAgencyPlayerFitNotes(player, capSpace).forEach((item) => {
      const rowNode = node("div", `fa-fit-item ${item.tone || ""}`.trim());
      append(rowNode, [
        node("span", null, item.label),
        node("strong", null, item.value),
      ]);
      list.append(rowNode);
    });
    return list.children.length ? list : node("div", "empty-state", "No market fit read available yet.");
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
    const queuedOffer = pendingFreeAgencyOffer(player);
    const defaultYears = Number(player.contract_year_preference || player.preferred_years || 1);
    const defaultAav = Number(player.offer_floor_aav || Math.max(Number(player.asking_aav || 0), Number(player.minimum_aav || 0)));
    const guaranteePct = Number(player.guarantee_pct || 0);

    const yearsInput = node("input", "offer-input offer-years");
    yearsInput.type = "number";
    yearsInput.min = "1";
    yearsInput.max = "5";
    yearsInput.step = "1";
    yearsInput.value = String(queuedOffer?.years || defaultYears);
    yearsInput.title = "Years";

    const aavInput = node("input", "offer-input offer-aav");
    aavInput.type = "number";
    aavInput.min = "0";
    aavInput.step = "50000";
    aavInput.value = String(queuedOffer?.aav || defaultAav);
    aavInput.title = "AAV";

    const guaranteeInput = node("input", "offer-input offer-guarantee");
    guaranteeInput.type = "number";
    guaranteeInput.min = "0";
    guaranteeInput.max = "85";
    guaranteeInput.step = "5";
    guaranteeInput.value = String(Math.max(0, Math.min(85, queuedOffer?.guarantee_pct ?? (guaranteePct || 20))));
    guaranteeInput.title = "Guaranteed money percentage";

    const structureSelect = node("select", "offer-input offer-structure");
    [
      ["balanced", "Balanced"],
      ["backloaded", "Backloaded"],
      ["frontloaded", "Frontloaded"],
      ["bonus-heavy", "Low Yr 1"],
    ].forEach(([value, label]) => {
      const option = node("option", null, label);
      option.value = value;
      if (value === (queuedOffer?.structure || "balanced")) option.selected = true;
      structureSelect.append(option);
    });
    structureSelect.title = "Contract structure";

    const currentOffer = () => {
      const years = Math.max(1, Math.min(5, Number(yearsInput.value || defaultYears || 1)));
      const aav = Math.max(0, Number(aavInput.value || defaultAav || 0));
      const guarantee = Math.max(0, Math.min(85, Number(guaranteeInput.value || guaranteePct || 0)));
      const structure = structureSelect.value || "balanced";
      const bonusRate = structure === "bonus-heavy" ? 0.18 : structure === "backloaded" ? 0.12 : 0.08;
      const bonus = roundTo(aav * years * bonusRate, 50_000);
      return {
        years,
        aav,
        bonus,
        guarantee,
        structure,
      };
    };

    wrap.append(node("span", "offer-label", "Yrs"));
    wrap.append(yearsInput);
    wrap.append(node("span", "offer-label", "AAV"));
    wrap.append(aavInput);
    wrap.append(node("span", "offer-label", "Gtd"));
    wrap.append(guaranteeInput);
    wrap.append(node("span", "offer-label", "Shape"));
    wrap.append(structureSelect);
    if (isObserveMode()) {
      [yearsInput, aavInput, guaranteeInput, structureSelect].forEach((item) => { item.disabled = true; });
      const observe = node("button", "run-button", "Observe");
      observe.type = "button";
      observe.disabled = true;
      observe.title = "Observe Mode has no user-controlled free-agent offers.";
      wrap.append(observe);
      return wrap;
    }
    if (runnerMode()) {
      const run = node("button", `run-button ${queuedOffer ? "selected" : ""}`.trim(), state.runnerBusy ? "Running" : queuedOffer ? "Update Queue" : "Queue Offer");
      run.type = "button";
      run.disabled = state.runnerBusy || !defaultAav;
      run.addEventListener("click", () => {
        const offer = currentOffer();
        setPendingFreeAgencyOffer(player, offer);
      });
      wrap.append(run);
      if (queuedOffer) {
        const remove = node("button", "run-button ghost", "Remove");
        remove.type = "button";
        remove.disabled = state.runnerBusy;
        remove.addEventListener("click", () => removePendingFreeAgencyOffer(player));
        wrap.append(remove);
      }
    } else {
      const run = node("button", "run-button", "Queue Offer");
      run.type = "button";
      run.disabled = true;
      run.title = "Actions unavailable right now.";
      wrap.append(run);
    }
    return wrap;
  }

  function waiverStatusTone(status) {
    const value = String(status || "").toLowerCase();
    if (value === "open" || value === "pending") return "warn";
    if (value === "claimed" || value === "awarded") return "good";
    if (value === "denied") return "bad";
    return "";
  }

  function waiverClaimButton(row) {
    const userTeamId = Number(data.activeSave?.user_team_id || 0);
    const button = node("button", "run-button small", row.user_claim_status === "Pending" ? "Claim Filed" : "Claim");
    button.type = "button";
    const disabledReason = !runnerMode()
      ? "Actions unavailable right now."
      : !userTeamId
        ? "Observe Mode has no user-controlled waiver claims."
        : row.status !== "Open"
          ? "Waiver is already resolved."
          : Number(row.original_team_id || 0) === userTeamId
            ? "Original team cannot claim its own waiver entry."
            : row.user_claim_status === "Pending"
              ? "Claim already filed."
              : "";
    button.disabled = state.runnerBusy || Boolean(disabledReason);
    button.title = disabledReason || "Claim this player at his existing contract.";
    button.addEventListener("click", () => runAction("waiver_claim", { waiver_id: row.waiver_id }));
    return button;
  }

  function waiverWireTable(waivers) {
    const rows = waivers.wire || [];
    return table(["Player", "Pos", "Age", "Exp", "Read", "Old Team", "Contract", "Deadline", "Claims", "Status", "Action"], rows.map((row) => [
      playerLink(row.player_id, row.player_name, "player-link strong-link", {
        team: row.original_team,
        position: row.position,
      }),
      row.position || "-",
      row.age ?? "-",
      experienceLabel(row),
      `${row.overall ?? "-"} / ${row.potential ?? "-"}`,
      append(node("span", "team-inline"), [
        teamLogo(row.originalTeamLogo, row.original_team, "mini-team-logo"),
        node("span", null, row.original_team || "-"),
      ]),
      row.contractLabel || "-",
      shortDate(row.claim_deadline),
      String(row.claim_count || 0),
      tag(row.user_claim_status || row.status || "Open", waiverStatusTone(row.user_claim_status || row.status)),
      waiverClaimButton(row),
    ]));
  }

  function waiverClaimOrderPanel(waivers) {
    const basis = String(waivers.basis || "").replace(/_/g, " ") || "priority";
    const p = panel("Claim Order", basis);
    const rows = (waivers.claimOrder || []).slice(0, 12).map((row) => [
      `#${row.priority}`,
      append(node("span", "team-inline"), [
        teamLogo(row.teamLogo, row.team, "mini-team-logo"),
        node("span", null, row.team || "-"),
      ]),
      row.team_name || "",
    ]);
    panelBody(p).append(table(["Priority", "Team", "Club"], rows));
    return p;
  }

  function waiverRecentClaimsPanel(waivers) {
    const p = panel("Your Claims", `${(waivers.claims || []).length} recent`);
    const rows = (waivers.claims || []).slice(0, 12).map((claim) => [
      `#${claim.claim_order || "-"}`,
      playerLink(claim.player_id, claim.player_name, "player-link strong-link", {
        team: claim.original_team,
        position: claim.position,
      }),
      claim.position || "-",
      claim.original_team || "-",
      tag(claim.status || "Pending", waiverStatusTone(claim.status)),
      shortDate(claim.claim_date),
    ]);
    panelBody(p).append(table(["Order", "Player", "Pos", "From", "Status", "Date"], rows));
    return p;
  }

  function renderWaivers() {
    setHeader("Waivers", "Claim released players before they clear to free agency.");
    const root = document.createDocumentFragment();
    const waivers = data.waivers || { wire: [], claims: [], claimOrder: [], counts: {} };
    if (runnerMode() && state.waiversLiveKey !== waiversLiveKey() && !state.waiversLoading) {
      loadLiveWaivers().then((changed) => {
        if (changed && state.view === "waivers") render();
      });
    }
    const summary = panel("Waiver Wire", `${waivers.counts?.open || 0} open`);
    const body = panelBody(summary);
    const controls = node("div", "control-bar");
    append(controls, [
      controlButton({
        label: "Run CPU Claims",
        action: "waiver_cpu_seed",
        params: { post_cutdown: true },
        className: "draft-control-button",
      }),
      controlButton({
        label: "Process Waivers",
        action: "waiver_process",
        params: {},
        className: "draft-control-button",
        tone: "good",
      }),
    ]);
    body.append(controls);
    if (state.waiversLoading) body.append(node("div", "empty-state", "Refreshing waiver wire..."));
    body.append(waiverWireTable(waivers));
    root.append(summary);
    const side = node("div", "two-column");
    side.append(waiverClaimOrderPanel(waivers), waiverRecentClaimsPanel(waivers));
    root.append(side);
    finishRender(root);
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

    const summary = panel("Negotiation Snapshot", `${talks.team || data.activeSave?.user_team || ""}`);
    if (state.contractsLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live contracts..."));
    }
    const metrics = node("section", "metric-grid");
    append(metrics, [
      metric("Expiring", String(counts.total || 0), `${contractYear} contract decisions`),
      metric("Priority", String(counts.priority || 0), "Core retain targets", counts.priority ? "warn" : ""),
      metric("Tag Options", String(counts.tagCandidates || 0), "One franchise/transition tag"),
      metric("5th Options", String(counts.fifthYearOptions || 0), "First-round rookie calls"),
      metric("RFA / ERFA", `${counts.rfaCandidates || 0}/${counts.erfaCandidates || 0}`, "Rights tenders available"),
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

    if ((talks.fifthYearOptions || []).length) {
      const optionPanel = panel("Fifth-Year Options", `${(talks.fifthYearOptions || []).length} decision(s)`);
      const optionBody = panelBody(optionPanel);
      optionBody.append(table(["Player", "Pos", "Score", "Option Year", "Salary", "Lean", "Action"], (talks.fifthYearOptions || []).map((player) => [
        playerLink(player.player_id, player.player_name, undefined, { team: talks.team, position: player.position }),
        player.position,
        player.market_score || "-",
        player.option_season || contractYear + 1,
        money(player.option_salary),
        player.recommendation || "-",
        contractOptionButton(player),
      ])));
      root.append(optionPanel);
    }

    const expiringPanel = panel("Expiring Players", `${(talks.expiring || []).length} shown`);
    const expiringBody = panelBody(expiringPanel);
    expiringBody.append(table(["Player", "Pos", "Age", "Rights", "Role", "Current", "Ask", "Tags / Tenders", "Priority", "Action"], (talks.expiring || []).map((player) => [
      playerLink(player.player_id, player.player_name, undefined, { team: talks.team, position: player.position }),
      player.position,
      whole(player.age),
      player.rights_type || "UFA",
      player.market_tier || "-",
      money(player.aav),
      money(player.asking_aav),
      contractTagCell(player),
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
      run.title = "Actions unavailable right now.";
      wrap.append(run);
    }
    return wrap;
  }

  function contractTagCell(player) {
    const wrap = node("span", "action-cell");
    const score = Number(player.market_score || 0);
    const rights = String(player.rights_type || "UFA").toUpperCase();
    if (rights === "RFA" && Array.isArray(player.rfa_tender_options) && player.rfa_tender_options.length) {
      const select = node("select", "compact-select");
      player.rfa_tender_options.forEach((option) => {
        const opt = node("option", "", `${option.label} ${money(option.aav)}`);
        opt.value = option.type;
        select.append(opt);
      });
      wrap.append(select);
      const run = node("button", "run-button", "Tender");
      run.type = "button";
      run.disabled = state.runnerBusy || !runnerMode();
      run.title = "Restricted free-agent tender. Compensation and matching rights are tracked by tender level.";
      run.addEventListener("click", () => runAction("contract_tag", {
        player_id: player.player_id,
        tag_type: select.value || "rfa_rofr",
      }));
      wrap.append(run);
      return wrap;
    }
    if (rights === "ERFA") {
      wrap.append(node("span", "quiet", `ERFA ${money(player.erfa_tender_aav)}`));
      const run = node("button", "run-button", "Tender");
      run.type = "button";
      run.disabled = state.runnerBusy || !runnerMode();
      run.title = "Exclusive-rights tender keeps the player on a one-year tender.";
      run.addEventListener("click", () => runAction("contract_tag", {
        player_id: player.player_id,
        tag_type: "erfa",
      }));
      wrap.append(run);
      return wrap;
    }
    const tagText = score >= 82 ? `F ${money(player.franchise_tag_aav)}` : score >= 76 ? `T ${money(player.transition_tag_aav)}` : "-";
    wrap.append(node("span", "quiet", tagText));
    if (runnerMode() && score >= 76) {
      const type = score >= 82 ? "franchise" : "transition";
      const run = node("button", "run-button", type === "franchise" ? "Franchise" : "Transition");
      run.type = "button";
      run.disabled = state.runnerBusy;
      run.title = "A team can use only one franchise or transition tag per league year.";
      run.addEventListener("click", () => runAction("contract_tag", {
        player_id: player.player_id,
        tag_type: type,
      }));
      wrap.append(run);
      if (type === "franchise" && score >= 90) {
        const exclusive = node("button", "run-button", "Exclusive");
        exclusive.type = "button";
        exclusive.disabled = state.runnerBusy;
        exclusive.title = "Exclusive franchise tag: higher tender for no outside negotiation.";
        exclusive.addEventListener("click", () => runAction("contract_tag", {
          player_id: player.player_id,
          tag_type: "exclusive",
        }));
        wrap.append(exclusive);
      }
      if (type === "franchise" && Number(player.transition_tag_aav || 0) > 0) {
        const transition = node("button", "run-button", "Transition");
        transition.type = "button";
        transition.disabled = state.runnerBusy;
        transition.title = "Lower one-year tender with right-of-first-refusal logic represented in the sim.";
        transition.addEventListener("click", () => runAction("contract_tag", {
          player_id: player.player_id,
          tag_type: "transition",
        }));
        wrap.append(transition);
      }
    }
    return wrap;
  }

  function contractOptionButton(player) {
    const wrap = node("span", "action-cell");
    if (!runnerMode()) {
      const disabled = node("button", "run-button", "Unavailable");
      disabled.type = "button";
      disabled.disabled = true;
      wrap.append(disabled);
      return wrap;
    }
    const exercise = node("button", "run-button", state.runnerBusy ? "Running" : "Exercise");
    exercise.type = "button";
    exercise.disabled = state.runnerBusy;
    exercise.title = "Adds a fully guaranteed fifth-year option contract for the option season.";
    exercise.addEventListener("click", () => runAction("contract_option_exercise", {
      player_id: player.player_id,
    }));
    const decline = node("button", "run-button secondary", "Decline");
    decline.type = "button";
    decline.disabled = state.runnerBusy;
    decline.title = "Records the declined option. The player stays on his rookie deal and can expire normally.";
    decline.addEventListener("click", () => runAction("contract_option_decline", {
      player_id: player.player_id,
    }));
    wrap.append(exercise, decline);
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
      run.title = "Actions unavailable right now.";
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
      run.title = "Actions unavailable right now.";
      wrap.append(run);
    }
    return wrap;
  }

  function canonicalDepthSlot(slot) {
    return String(slot || "").toUpperCase().replace(/^(NICKEL|BASE34|BASE43)_/, "");
  }

  function slotBasePositions(slot) {
    const key = canonicalDepthSlot(slot);
    if (["LWR", "RWR", "SWR"].includes(key)) return ["WR", "RB", "CB"];
    if (["KR", "PR"].includes(key)) return ["WR", "RB", "CB", "NB", "FS", "SS", "S"];
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
    const canonical = canonicalDepthSlot(slot);
    return (player.flex || []).some((item) => bases.includes(item.position) || String(item.position).toUpperCase() === canonical);
  }

  function selectedDepthSlot(depth) {
    const slots = orderedDepthSlots(depth);
    const map = depthSlotMap(depth);
    const activeMetas = activeFormationMetas();
    if (!slots.length && !activeMetas.length) return null;
    const activeSlots = new Set(activeMetas.map((meta) => String(meta.slot || "").toUpperCase()));
    const selectedKey = String(state.selectedDepthSlot || "").toUpperCase();
    const selected = map.get(selectedKey);
    if (selected) return selected;
    const selectedMeta = activeMetas.find((meta) => String(meta.slot || "").toUpperCase() === selectedKey);
    if (selectedMeta) {
      return formationSlotForMeta(map, selectedMeta) || { slot: selectedMeta.slot, players: [] };
    }
    const active = activeMetas.map((meta) => formationSlotForMeta(map, meta)).find(Boolean)
      || slots.find((slot) => activeSlots.has(String(slot.slot || "").toUpperCase()));
    const fallback = active || slots[0];
    state.selectedDepthSlot = fallback.slot;
    return fallback;
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

  function formationRank(meta) {
    return Math.max(1, Number(meta?.rank || 1));
  }

  function depthLayoutKey(unitName) {
    const team = String(data.depthChart?.team || data.activeSave?.user_team || "TEAM").toUpperCase();
    const packageKey = unitName === "Defense" ? state.depthDefensePackage : state.depthOffensePersonnel;
    return `${team}:${unitName}:${packageKey}`;
  }

  function depthLayoutSlotKey(meta) {
    return `${String(meta?.slot || "").toUpperCase()}:${formationRank(meta)}`;
  }

  function depthLayoutSpan(meta) {
    const match = String(meta?.col || "").match(/span\s+(\d+)/i);
    return match ? Math.max(1, Number(match[1] || 1)) : 1;
  }

  function formationMetaWithLayout(meta, unitName) {
    const stored = state.depthLayoutOverrides?.[depthLayoutKey(unitName)]?.[depthLayoutSlotKey(meta)];
    if (!stored) return meta;
    return {
      ...meta,
      row: stored.row || meta.row,
      col: stored.col || meta.col,
    };
  }

  function depthLayoutGrid(unitName) {
    return unitName === "Defense" ? { cols: 12, rows: 7 } : { cols: 13, rows: 7 };
  }

  function activeFormationMetas() {
    return [...offensePersonnelSlots(), ...defensePackageSlots()].map((meta) => ({
      ...meta,
      rank: formationRank(meta),
    }));
  }

  function activeFormationEntries(depth) {
    const map = depthSlotMap(depth);
    return activeFormationMetas()
      .map((meta) => {
        const slot = formationSlotForMeta(map, meta);
        const player = (slot?.players || [])[formationRank(meta) - 1];
        return {
          slot: meta.slot,
          label: meta.label || meta.slot,
          rank: formationRank(meta),
          player,
          playerId: player?.player_id === undefined || player?.player_id === null ? "" : String(player.player_id),
        };
      })
      .filter((entry) => entry.player);
  }

  function formationDuplicateGroups(depth) {
    const groups = new Map();
    activeFormationEntries(depth).forEach((entry) => {
      if (!entry.playerId) return;
      const group = groups.get(entry.playerId) || {
        playerId: entry.playerId,
        player: entry.player,
        entries: [],
      };
      group.entries.push(entry);
      groups.set(entry.playerId, group);
    });
    return [...groups.values()].filter((group) => group.entries.length > 1);
  }

  function formationDuplicatePlayerIds(depth) {
    return new Set(formationDuplicateGroups(depth).map((group) => group.playerId));
  }

  function formationConflictEntry(depth, playerId, slot, rank) {
    const id = String(playerId || "");
    const targetSlot = String(slot || "").toUpperCase();
    const targetRank = Number(rank || 1);
    if (!id || !activeFormationMetas().some((meta) => String(meta.slot || "").toUpperCase() === targetSlot && formationRank(meta) === targetRank)) return null;
    return activeFormationEntries(depth).find((entry) => (
      entry.playerId === id
      && !(String(entry.slot || "").toUpperCase() === targetSlot && Number(entry.rank || 1) === targetRank)
    )) || null;
  }

  function formationConflictText(entry) {
    if (!entry) return "";
    const rankText = entry.rank > 1 ? ` #${entry.rank}` : "";
    return `${entry.player?.player_name || "Player"} is already on the field at ${entry.label}${rankText}.`;
  }

  function depthDragPayload(event) {
    const transfer = event?.dataTransfer;
    if (!transfer) return null;
    const raw = transfer.getData("application/x-nfl-gm-depth") || transfer.getData("text/plain");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (_err) {
      return null;
    }
  }

  function writeDepthDragPayload(event, payload) {
    event.dataTransfer.setData("application/x-nfl-gm-depth", JSON.stringify(payload));
    event.dataTransfer.setData("text/plain", JSON.stringify(payload));
  }

  function depthPlayerById(depth, playerId) {
    const id = String(playerId || "");
    if (!id) return null;
    const rosterPlayer = (depth.roster || []).find((player) => String(player.player_id) === id);
    const activePlayer = activeFormationEntries(depth).find((entry) => entry.playerId === id)?.player;
    if (rosterPlayer && activePlayer) return { ...activePlayer, ...rosterPlayer };
    return rosterPlayer || activePlayer || null;
  }

  function formationSwapReview(depth, source, target) {
    if (!source?.slot || !target?.slot || !source?.player_id || !target?.player_id) {
      return { ok: false, message: "Drag one occupied formation box onto another occupied box." };
    }
    if (String(source.slot).toUpperCase() === String(target.slot).toUpperCase() && Number(source.rank || 1) === Number(target.rank || 1)) {
      return { ok: false, message: "That is already the selected depth-chart spot." };
    }
    if (String(source.player_id) === String(target.player_id)) {
      return { ok: false, message: "That player is already in both spots." };
    }
    const sourcePlayer = source.player || depthPlayerById(depth, source.player_id);
    const targetPlayer = target.player || depthPlayerById(depth, target.player_id);
    if (sourcePlayer && !playerFitsSlot(sourcePlayer, target.slot)) {
      return { ok: false, message: `${sourcePlayer.player_name || "That player"} does not fit ${canonicalDepthSlot(target.slot)}.` };
    }
    if (targetPlayer && !playerFitsSlot(targetPlayer, source.slot)) {
      return { ok: false, message: `${targetPlayer.player_name || "That player"} does not fit ${canonicalDepthSlot(source.slot)}.` };
    }
    return { ok: true, message: "Swap depth-chart spots." };
  }

  function swapFormationPlayers(source, target) {
    const review = formationSwapReview(data.depthChart || {}, source, target);
    if (!review.ok) {
      showToast(review.message);
      return;
    }
    queueDepthAction("depth_chart_swap", {
      first_position: source.slot,
      first_rank: Number(source.rank || 1),
      second_position: target.slot,
      second_rank: Number(target.rank || 1),
    }, {
      title: "Swap Depth Spots",
      detail: `${source.slot} #${Number(source.rank || 1)} with ${target.slot} #${Number(target.rank || 1)}.`,
    });
  }

  function moveFormationBox(unitName, meta, field, event) {
    if (!state.depthLayoutUnlocked || !field || !meta) return;
    const rect = field.getBoundingClientRect();
    const grid = depthLayoutGrid(unitName);
    const span = depthLayoutSpan(meta);
    const x = Math.max(0, Math.min(rect.width - 1, event.clientX - rect.left));
    const y = Math.max(0, Math.min(rect.height - 1, event.clientY - rect.top));
    const col = Math.max(1, Math.min(grid.cols - span + 1, Math.floor((x / rect.width) * grid.cols) + 1));
    const row = Math.max(1, Math.min(grid.rows, Math.floor((y / rect.height) * grid.rows) + 1));
    const key = depthLayoutKey(unitName);
    const slotKey = depthLayoutSlotKey(meta);
    state.depthLayoutOverrides[key] = {
      ...(state.depthLayoutOverrides[key] || {}),
      [slotKey]: {
        row,
        col: span > 1 ? `${col} / span ${span}` : String(col),
      },
    };
    saveDepthLayoutOverrides();
    render();
  }

  function resetCurrentDepthLayout() {
    delete state.depthLayoutOverrides[depthLayoutKey("Offense")];
    delete state.depthLayoutOverrides[depthLayoutKey("Defense")];
    saveDepthLayoutOverrides();
    render();
  }

  function depthFormationToolbar() {
    const wrap = node("div", "formation-toolbar");
    const lock = node("button", `run-button compact ${state.depthLayoutUnlocked ? "active" : ""}`.trim(), state.depthLayoutUnlocked ? "Lock Layout" : "Unlock Layout");
    lock.type = "button";
    lock.addEventListener("click", () => {
      state.depthLayoutUnlocked = !state.depthLayoutUnlocked;
      render();
    });
    const reset = node("button", "run-button compact", "Reset Layout");
    reset.type = "button";
    reset.disabled = !state.depthLayoutOverrides[depthLayoutKey("Offense")] && !state.depthLayoutOverrides[depthLayoutKey("Defense")];
    reset.addEventListener("click", resetCurrentDepthLayout);
    const hint = node("span", "formation-toolbar-hint", state.depthLayoutUnlocked
      ? "Drag boxes to rearrange this formation visually."
      : "Drag one occupied box onto another to swap depth chart spots.");
    append(wrap, [lock, reset, hint]);
    return wrap;
  }

  function depthSetRankButton(depth, slot, rank, player, label) {
    const currentRank = player.isPackageFallback ? 0 : Number(player.depth_rank || 0);
    const conflict = formationConflictEntry(depth, player.player_id, slot, rank);
    const params = {
      position: slot,
      rank,
      player_id: player.player_id,
    };
    const queued = pendingDepthActionEntries().some((item) => item.key === depthActionKey("depth_chart_set", params));
    const button = node("button", `depth-rank-button ${queued ? "selected" : ""}`.trim(), queued ? "Queued" : (label || `#${rank}`));
    button.type = "button";
    button.disabled = state.runnerBusy || !runnerMode() || currentRank === Number(rank) || Boolean(conflict);
    button.title = conflict ? formationConflictText(conflict) : currentRank === Number(rank)
      ? `${player.player_name} is already ${slot} #${rank}`
      : `Set ${player.player_name} as ${slot} #${rank}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (conflict) {
        showToast(formationConflictText(conflict));
        return;
      }
      queueDepthAction("depth_chart_set", params, {
        title: "Set Depth Role",
        detail: `${player.player_name} to ${slot} #${rank}.`,
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
      actions.append(depthSetRankButton(depth, selected.slot, rank, player));
    });
    append(item, [top, meta, actions]);
    return item;
  }

  function depthPlayerCard(depth, selected, player) {
    const projected = Boolean(player?._queuedProjection || selected?._queuedProjection);
    const card = node("article", `depth-player-card ${projected ? "projected" : ""}`.trim());
    const roleScore = player.role?.score ? oneDecimal(player.role.score) : "-";
    const header = node("div", "depth-player-card-top");
    append(header, [
      tag(`#${player.depth_rank}`, depthRoleTone(player.depth_rank)),
      smallPlayerCell(player.player_id, player.player_name, `${player.position} | Age ${player.age || "-"}`, {
        team: depth.team,
        position: player.position,
      }),
      tag(depthRoleName(player.depth_rank), depthRoleTone(player.depth_rank)),
      projected ? tag("Projected", "warn") : null,
    ]);
    const detail = node("div", "depth-player-card-detail");
    append(detail, [
      node("span", null, `Role fit ${roleScore}`),
      node("span", null, player.role?.key ? roleLabel(player.role.key) : "No role read"),
    ]);
    const controls = node("div", "depth-player-card-controls");
    append(controls, [
      depthMoveButtons(selected.slot, player, Boolean(selected.isPackageFallback || player.isPackageFallback)),
      depthReplacementControl(depth, selected.slot, player.depth_rank, depth.roster || [], player.player_id),
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
    const metrics = [
      metric("Slot", selected?.slot || "-", selected ? `${selectedPlayers.length} assigned` : "Choose a position"),
      metric("Starter", starter?.player_name || "-", starter ? `${starter.position} | Age ${starter.age || "-"}` : "No player assigned"),
      metric("Best Fit", eligible[0]?.player_name || "-", eligible[0] ? `${eligible[0].position} | ${eligible[0].role?.score ? oneDecimal(eligible[0].role.score) : "-"} role fit` : "No roster read"),
      metric("Roster", String((depth.roster || []).length), "Active players"),
    ];
    if (depth._hasQueuedProjection) {
      metrics.push(metric("Projected", String(pendingDepthActionEntries().length), "Queued chart changes", "warn"));
    }
    append(summary, metrics);
    return summary;
  }

  function depthSlotMap(depth) {
    const map = new Map();
    orderedDepthSlots(depth).forEach((slot) => map.set(String(slot.slot || "").toUpperCase(), slot));
    return map;
  }

  function formationSlotForMeta(map, meta) {
    const key = String(meta?.slot || "").toUpperCase();
    const exact = map.get(key);
    const sourceKey = meta?.sourceSlot ? canonicalDepthSlot(meta.sourceSlot) : "";
    const fallback = sourceKey ? map.get(sourceKey) : null;
    if (exact) {
      if (!fallback) return exact;
      const players = [...(exact.players || [])];
      const seen = new Set(players.map((player) => String(player.player_id)));
      (fallback.players || []).forEach((player) => {
        if (seen.has(String(player.player_id))) return;
        players.push({
          ...player,
          depth_rank: players.length + 1,
          sourceDepthRank: player.depth_rank,
          isPackageFallback: true,
        });
        seen.add(String(player.player_id));
      });
      return { ...exact, players };
    }
    if (!fallback) return null;
    return {
      ...fallback,
      slot: key,
      sourceSlot: sourceKey,
      isPackageFallback: true,
    };
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
    if (state.depthDefensePackage === "base" || state.depthDefensePackage === "base34") return BASE_DEFENSE_FORMATION_SLOTS;
    return DEFENSE_FORMATION_SLOTS;
  }

  function defensePackageSlotName(packageKey, baseSlot) {
    const canonical = canonicalDepthSlot(baseSlot);
    if (packageKey === "base43") return `BASE43_${canonical}`;
    if (packageKey === "base" || packageKey === "base34") return `BASE34_${canonical}`;
    return `NICKEL_${canonical}`;
  }

  function offensePersonnelSlots() {
    if (state.depthOffensePersonnel === "10") return OFFENSE_10_FORMATION_SLOTS;
    if (state.depthOffensePersonnel === "12") return OFFENSE_12_FORMATION_SLOTS;
    if (state.depthOffensePersonnel === "13") return OFFENSE_13_FORMATION_SLOTS;
    if (state.depthOffensePersonnel === "21") return OFFENSE_21_FORMATION_SLOTS;
    return OFFENSE_FORMATION_SLOTS;
  }

  function offensePersonnelOptions(depth) {
    const labels = {
      "10": { label: "10", detail: "4 WR" },
      "11": { label: "11", detail: "3 WR 1 TE" },
      "12": { label: "12", detail: "2 WR 2 TE" },
      "13": { label: "13", detail: "3 TE" },
      "21": { label: "21", detail: "2 WR FB" },
    };
    const packages = depth?.scheme?.offensePackages?.length ? depth.scheme.offensePackages : ["11", "12"];
    const shares = depth?.scheme?.offensePackageShares || {};
    return packages.map((value) => {
      const base = labels[value] || { label: value, detail: "Package" };
      const share = Number(shares[value] || 0);
      const suffix = share > 0 ? ` | ${Math.round(share * 100)}%` : "";
      return { value, label: base.label, detail: `${base.detail}${suffix}` };
    });
  }

  function defensePackageOptions(depth) {
    const labels = {
      nickel: { label: "Nickel", detail: "NB + 2 LB" },
      base34: { label: "3-4", detail: "Odd front" },
      base43: { label: "4-3", detail: "3 LB" },
    };
    const packages = depth?.scheme?.defensePackages?.length ? depth.scheme.defensePackages : ["nickel"];
    const shares = depth?.scheme?.defensePackageShares || {};
    return packages.map((value) => {
      const base = labels[value] || { label: value, detail: "Package" };
      const share = Number(shares[value] || 0);
      const suffix = share > 0 ? ` | ${Math.round(share * 100)}%` : "";
      return { value, label: base.label, detail: `${base.detail}${suffix}` };
    });
  }

  function depthPackageUsageGroup(title, options, activeValue) {
    const group = node("div", "package-usage-group");
    group.append(node("strong", null, title));
    const chips = node("div", "package-usage-chips");
    options.forEach((option) => {
      const chip = node("button", `package-usage-chip ${option.value === activeValue ? "active" : ""}`.trim());
      chip.type = "button";
      append(chip, [
        node("span", null, option.label),
        node("small", null, option.detail),
      ]);
      chip.addEventListener("click", () => {
        if (title === "Offense") {
          state.depthOffensePersonnel = option.value;
        } else {
          state.depthDefensePackage = option.value;
        }
        render();
      });
      chips.append(chip);
    });
    append(group, [chips]);
    return group;
  }

  function depthPackageUsagePanel(depth) {
    const wrap = node("div", "package-usage-panel");
    append(wrap, [
      depthPackageUsageGroup("Offense", offensePersonnelOptions(depth), state.depthOffensePersonnel),
      depthPackageUsageGroup("Defense", defensePackageOptions(depth), state.depthDefensePackage),
    ]);
    return wrap;
  }

  function depthPackageOption(options, value) {
    return options.find((option) => String(option.value) === String(value)) || options[0] || { label: value || "-", detail: "Package" };
  }

  function depthChartClarityCard(title, value, detail, tone = "") {
    const card = node("div", `depth-clarity-card ${tone}`.trim());
    append(card, [
      node("span", null, title),
      node("strong", null, value),
      node("small", null, detail),
    ]);
    return card;
  }

  function depthSelectedPackageStatus(selected) {
    if (!selected) return { label: "No Slot", detail: "Choose a box on the field", tone: "" };
    const selectedSlot = String(selected.slot || "").toUpperCase();
    const selectedBase = canonicalDepthSlot(selectedSlot);
    const meta = activeFormationMetas().find((item) => {
      const itemSlot = String(item.slot || "").toUpperCase();
      return itemSlot === selectedSlot || (canonicalDepthSlot(itemSlot) === selectedBase && canonicalDepthSlot(item.sourceSlot || "") === selectedBase);
    });
    if (meta) {
      const rankText = formationRank(meta) > 1 ? ` #${formationRank(meta)}` : "";
      return { label: meta.label || selectedBase, detail: `Visible in current package${rankText}`, tone: "good" };
    }
    return { label: selectedBase || selectedSlot, detail: "Editable room, not on the current field package", tone: "warn" };
  }

  function depthChartClarityStrip(depth, selected) {
    const offenseOption = depthPackageOption(offensePersonnelOptions(depth), state.depthOffensePersonnel);
    const defenseOption = depthPackageOption(defensePackageOptions(depth), state.depthDefensePackage);
    const offenseSlots = offensePersonnelSlots();
    const defenseSlots = defensePackageSlots();
    const selectedStatus = depthSelectedPackageStatus(selected);
    const queued = pendingDepthActionEntries();
    const duplicateCount = formationDuplicateGroups(depth).length;
    const wrap = node("div", "depth-clarity-strip");
    const intro = node("div", "depth-clarity-intro");
    append(intro, [
      node("strong", null, "Package Logic"),
      node("span", null, "The sim reads the selected personnel packages below. Package shares show how often each group is expected to appear."),
    ]);
    const cards = node("div", "depth-clarity-cards");
    append(cards, [
      depthChartClarityCard("Offense", offenseOption.label, `${offenseOption.detail} | ${offenseSlots.length} spots`, "good"),
      depthChartClarityCard("Defense", defenseOption.label, `${defenseOption.detail} | ${defenseSlots.length} spots`, "good"),
      depthChartClarityCard("Selected", selectedStatus.label, selectedStatus.detail, selectedStatus.tone),
      depthChartClarityCard(
        "Staged",
        String(queued.length),
        queued.length ? queued.slice(0, 2).map((item) => item.detail || item.title).join(" | ") : "No queued chart changes",
        queued.length ? "warn" : "",
      ),
    ]);
    if (duplicateCount) {
      cards.append(depthChartClarityCard("Conflicts", String(duplicateCount), "Duplicate starters need attention", "bad"));
    }
    append(wrap, [intro, cards]);
    return wrap;
  }

  function ensureDepthPackageSelection(depth) {
    const offenseOptions = offensePersonnelOptions(depth).map((option) => option.value);
    const defenseOptions = defensePackageOptions(depth).map((option) => option.value);
    if (!offenseOptions.includes(state.depthOffensePersonnel)) {
      state.depthOffensePersonnel = depth?.scheme?.defaultOffensePackage || offenseOptions[0] || "11";
    }
    if (!defenseOptions.includes(state.depthDefensePackage)) {
      state.depthDefensePackage = depth?.scheme?.defaultDefensePackage || defenseOptions[0] || "nickel";
    }
  }

  function offensePersonnelToggle(depth) {
    const wrap = node("div", "formation-toggle");
    offensePersonnelOptions(depth).forEach((option) => {
      const button = node("button", state.depthOffensePersonnel === option.value ? "active" : "");
      button.type = "button";
      append(button, [
        node("strong", null, option.label),
        node("span", null, option.detail),
      ]);
      button.addEventListener("click", () => {
        state.depthOffensePersonnel = option.value;
        if (!["10", "11"].includes(option.value) && state.selectedDepthSlot === "SWR") state.selectedDepthSlot = "TE";
        if (option.value !== "21" && state.selectedDepthSlot === "FB") state.selectedDepthSlot = "RB";
        render();
      });
      wrap.append(button);
    });
    return wrap;
  }

  function defensePackageToggle(depth) {
    const wrap = node("div", "formation-toggle");
    defensePackageOptions(depth).forEach((option) => {
      const button = node("button", state.depthDefensePackage === option.value ? "active" : "");
      button.type = "button";
      append(button, [
        node("strong", null, option.label),
        node("span", null, option.detail),
      ]);
      button.addEventListener("click", () => {
        const currentBaseSlot = canonicalDepthSlot(state.selectedDepthSlot);
        state.depthDefensePackage = option.value;
        let nextBaseSlot = currentBaseSlot;
        if (option.value === "nickel" && currentBaseSlot === "SLB") nextBaseSlot = "NB";
        if (option.value !== "nickel" && currentBaseSlot === "NB") nextBaseSlot = option.value === "base43" ? "SLB" : "MLB";
        if ((option.value === "base" || option.value === "base34") && currentBaseSlot === "SLB") nextBaseSlot = "MLB";
        state.selectedDepthSlot = defensePackageSlotName(option.value, nextBaseSlot);
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

  function depthDuplicateWarningPanel(depth) {
    const groups = formationDuplicateGroups(depth);
    if (!groups.length) return null;
    const alert = node("div", "depth-duplicate-alert");
    alert.append(node("strong", null, "Duplicate starters in this package"));
    groups.forEach((group) => {
      const row = node("div", "depth-duplicate-row");
      row.append(node("span", null, group.player?.player_name || "Player"));
      const spots = node("div", "depth-duplicate-spots");
      group.entries.forEach((entry) => {
        const jump = node("button", "depth-conflict-chip", `${entry.label}${entry.rank > 1 ? ` #${entry.rank}` : ""}`);
        jump.type = "button";
        jump.addEventListener("click", () => {
          state.selectedDepthSlot = entry.slot;
          render();
        });
        spots.append(jump);
      });
      append(row, [spots]);
      alert.append(row);
    });
    return alert;
  }

  function formationSlotTile(depth, selected, slot, meta, unitName) {
    const rankIndex = Math.max(0, Number(meta.rank || 1) - 1);
    const starter = (slot.players || [])[rankIndex];
    const duplicateIds = formationDuplicatePlayerIds(depth);
    const isDuplicate = starter?.player_id !== undefined && duplicateIds.has(String(starter.player_id));
    const isProjected = Boolean(depth._hasQueuedProjection && (slot._queuedProjection || starter?._queuedProjection));
    const canSwap = Boolean(starter?.player_id) && runnerMode() && !state.runnerBusy;
    const button = node(
      "button",
      `formation-slot ${String(slot.slot || "").toUpperCase() === String(selected?.slot || "").toUpperCase() ? "active" : ""} ${isDuplicate ? "duplicate" : ""} ${isProjected ? "projected" : ""} ${state.depthLayoutUnlocked ? "layout-unlocked" : ""}`.trim(),
    );
    button.type = "button";
    button.draggable = state.depthLayoutUnlocked || canSwap;
    if (isDuplicate) button.title = `${starter.player_name} is starting in more than one spot in this formation.`;
    else if (state.depthLayoutUnlocked) button.title = "Drag to move this box cosmetically.";
    else if (canSwap) button.title = "Drag onto another occupied box to swap depth chart spots.";
    if (meta.row) button.style.gridRow = String(meta.row);
    if (meta.col) button.style.gridColumn = String(meta.col);
    append(button, [
      node("span", "formation-slot-label", meta.label || slot.slot),
      node("strong", null, formationPlayerLabel(starter)),
      node("small", null, starter ? `${starter.position || slot.slot}${starter.overall ? ` | ${starter.overall} OVR` : ""}${meta.rank ? ` | #${meta.rank}` : ""}${isDuplicate ? " | duplicate" : ""}${isProjected ? " | projected" : ""}` : `${slot.slot} depth${meta.rank ? ` #${meta.rank}` : ""}`),
    ]);
    button.addEventListener("dragstart", (event) => {
      if (state.depthLayoutUnlocked) {
        writeDepthDragPayload(event, {
          type: "layout",
          unit: unitName,
          slot: slot.slot,
          rank: formationRank(meta),
          row: meta.row,
          col: meta.col,
        });
        event.dataTransfer.effectAllowed = "move";
        return;
      }
      if (!canSwap) {
        event.preventDefault();
        return;
      }
      const payloadPlayer = depthPlayerById(depth, starter.player_id) || starter;
      writeDepthDragPayload(event, {
        type: "player",
        slot: slot.slot,
        rank: formationRank(meta),
        player_id: starter.player_id,
        player: payloadPlayer ? {
          player_id: payloadPlayer.player_id,
          player_name: payloadPlayer.player_name,
          position: payloadPlayer.position,
          flex: payloadPlayer.flex || [],
        } : null,
      });
      event.dataTransfer.effectAllowed = "move";
    });
    button.addEventListener("dragover", (event) => {
      if (state.depthLayoutUnlocked || !canSwap || state.runnerBusy) return;
      event.preventDefault();
      button.classList.remove("drop-target");
      button.classList.remove("drop-blocked");
      const payload = depthDragPayload(event);
      if (payload?.type === "player") {
        const review = formationSwapReview(depth, payload, {
          slot: slot.slot,
          rank: formationRank(meta),
          player_id: starter.player_id,
          player: depthPlayerById(depth, starter.player_id) || starter,
        });
        if (!review.ok) {
          event.dataTransfer.dropEffect = "none";
          button.classList.add("drop-blocked");
          return;
        }
      }
      event.dataTransfer.dropEffect = "move";
      button.classList.add("drop-target");
    });
    button.addEventListener("dragleave", () => {
      button.classList.remove("drop-target");
      button.classList.remove("drop-blocked");
    });
    button.addEventListener("drop", (event) => {
      button.classList.remove("drop-target");
      button.classList.remove("drop-blocked");
      if (state.depthLayoutUnlocked || !canSwap || state.runnerBusy) return;
      const payload = depthDragPayload(event);
      if (!payload || payload.type !== "player") return;
      event.preventDefault();
      const target = {
        slot: slot.slot,
        rank: formationRank(meta),
        player_id: starter.player_id,
        player: depthPlayerById(depth, starter.player_id) || starter,
      };
      const review = formationSwapReview(depth, payload, target);
      if (!review.ok) {
        showToast(review.message);
        return;
      }
      swapFormationPlayers(payload, target);
    });
    button.addEventListener("click", () => {
      state.selectedDepthSlot = slot.slot;
      render();
    });
    return button;
  }

  function depthFormationPanel(depth, selected) {
    const map = depthSlotMap(depth);
    const scheme = depth.scheme || {};
    const schemeText = [scheme.offenseScheme, scheme.defenseScheme].filter(Boolean).join(" | ") || "Click a position on the field";
    const panelNode = panel("Formation Board", schemeText);
    const body = panelBody(panelNode);
    body.append(depthFormationToolbar());
    body.append(depthPackageUsagePanel(depth));
    append(body, depthDuplicateWarningPanel(depth));
    const unitBlocks = [
      { unit: "Offense", slots: offensePersonnelSlots() },
      { unit: "Defense", slots: defensePackageSlots() },
    ];
    unitBlocks.forEach((unit) => {
      const section = node("section", "formation-section");
      const title = node("div", "formation-section-title");
      append(title, [
        node("strong", null, unit.unit),
        unit.unit === "Defense" ? defensePackageToggle(depth) : offensePersonnelToggle(depth),
      ]);
      const field = node("div", `formation-field ${unit.unit === "Defense" ? "defense" : "offense"} ${state.depthLayoutUnlocked ? "layout-unlocked" : ""}`.trim());
      field.addEventListener("dragover", (event) => {
        if (!state.depthLayoutUnlocked) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      });
      field.addEventListener("drop", (event) => {
        if (!state.depthLayoutUnlocked) return;
        const payload = depthDragPayload(event);
        if (!payload || payload.type !== "layout" || payload.unit !== unit.unit) return;
        event.preventDefault();
        const original = unit.slots.find((item) => depthLayoutSlotKey(item) === depthLayoutSlotKey(payload));
        moveFormationBox(unit.unit, original || payload, field, event);
      });
      unit.slots.forEach((meta) => {
        const displayMeta = formationMetaWithLayout(meta, unit.unit);
        const slot = formationSlotForMeta(map, meta);
        if (!slot && meta.optional) return;
        field.append(formationSlotTile(depth, selected, slot || { slot: meta.slot, players: [] }, displayMeta, unit.unit));
      });
      append(section, [title, field]);
      body.append(section);
    });
    const specialists = node("div", "specialists-strip");
    const activeSlots = new Set(depth.activeSlots || []);
    SPECIAL_TEAMS_FORMATION_SLOTS.forEach((meta) => {
      const slot = map.get(meta.slot);
      if (!slot && !activeSlots.has(meta.slot)) return;
      specialists.append(formationSlotTile(depth, selected, slot || { slot: meta.slot, players: [] }, meta, "Special Teams"));
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
    const rosterActions = new Map(pendingRosterActionEntries().map((action) => [String(action.player_id), action]));
    return (depth.roster || []).map((player) => {
      const assignments = depthMap.get(String(player.player_id)) || [];
      const primary = assignments
        .filter((item) => item.rank > 0)
        .sort((a, b) => a.rank - b.rank)[0];
      const alertContract = contractMap.get(String(player.player_id));
      const contract = alertContract
        ? { ...(player.contract || {}), ...alertContract, alertType: alertContract.type }
        : (player.contract || {});
      const queuedRosterAction = rosterActions.get(String(player.player_id));
      const projectedStatus = queuedRosterAction?.action === "roster_release_player"
        ? "Pending Release"
        : queuedRosterAction?.action === "roster_send_ir"
        ? "Pending IR"
        : queuedRosterAction?.action === "roster_activate_ir" || queuedRosterAction?.action === "practice_squad_promote"
        ? "Pending Active"
        : (player.status || "Active");
      return {
        ...player,
        status: projectedStatus,
        originalStatus: player.status || "Active",
        queuedRosterAction,
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
    if (key === "confidence") return confidenceSortValue(player.evaluation_confidence || player.evaluation?.confidenceLabel || "");
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
    if (String(player?.status || "").toLowerCase() === "pending active") return false;
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

  function rosterStatusCounts(rows) {
    return {
      active: rows.filter((player) => activeRosterStatus(player.status)).length,
      practiceSquad: rows.filter((player) => isPracticeSquadPlayer(player)).length,
      pending: rows.filter((player) => player.queuedRosterAction).length,
    };
  }

  function selectedRosterPlayer(rows) {
    const selected = rows.find((player) => String(player.player_id) === String(state.selectedRosterPlayerId));
    if (selected) return selected;
    const first = sortedRosterRows(rows)[0] || null;
    state.selectedRosterPlayerId = first?.player_id ? String(first.player_id) : null;
    return first;
  }

  function rosterTableHeader(label, key, className = "") {
    const th = node("th", className);
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
    const colGroup = node("colgroup");
    [
      "roster-col-photo",
      "roster-col-number",
      "roster-col-player",
      "roster-col-position",
      "roster-col-rating",
      "roster-col-rating",
      "roster-col-read",
      "roster-col-age",
      "roster-col-depth",
      "roster-col-role",
      "roster-col-contract",
      "roster-col-status",
    ].forEach((className) => colGroup.append(node("col", className)));
    const head = node("thead");
    const headRow = node("tr");
    [
      rosterTableHeader("", null, "roster-photo-head"),
      rosterTableHeader("#", "number", "center"),
      rosterTableHeader("Player", "name"),
      rosterTableHeader("Pos", "pos", "center"),
      rosterTableHeader("OVR", "overall", "center"),
      rosterTableHeader("POT", "potential", "center"),
      rosterTableHeader("Read", "confidence", "center"),
      rosterTableHeader("Age", "age", "center"),
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
      const tr = node("tr", `${String(player.player_id) === String(selected?.player_id) ? "selected" : ""} ${player.queuedRosterAction ? "projected-roster-row" : ""}`.trim());
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
      const contractText = contract.end_year
        ? `Cap ${money(contract.cap_hit || contract.asking_aav || 0)} | thru ${contract.end_year}`
        : (contract.type || "-");
      append(tr, [
        append(node("td", "roster-photo-cell"), [rosterHeadshot(player)]),
        node("td", "center roster-number-cell", player.jersey_number === null || player.jersey_number === undefined ? "-" : `#${player.jersey_number}`),
        playerCell,
        node("td", "center", player.position || "-"),
        node("td", "center rating-cell", player.overall ?? "-"),
        node("td", "center rating-cell", player.potential ?? "-"),
        node("td", "center", player.evaluation_confidence || player.evaluation?.confidenceLabel || "-"),
        node("td", "center", player.age ?? "-"),
        node("td", null, assignment ? `${assignment.slot} #${assignment.rank}` : "-"),
        node("td", null, player.role?.key ? `${roleLabel(player.role.key)} ${oneDecimal(player.roleScore)}` : "-"),
        node("td", null, contractText),
        append(node("td", null), [player.queuedRosterAction ? tag(player.status || "Pending", "warn") : node("span", null, player.status || "Active")]),
      ]);
      body.append(tr);
    });
    table.append(colGroup, head, body);
    wrap.append(table);
    return wrap;
  }

  function rosterActionButton(label, action, params = {}, className = "", options = {}) {
    const player = options.player || { player_id: params.player_id, player_name: params.player_name, position: params.position, team: params.team };
    const queued = queueableRosterAction(action) ? pendingRosterAction(player, action) : null;
    const button = node("button", `run-button compact ${className} ${queued ? "selected" : ""}`.trim(), state.runnerBusy ? "Running" : queued ? "Staged" : label);
    button.type = "button";
    button.disabled = !runnerMode() || state.runnerBusy;
    if (!runnerMode()) button.title = "Actions unavailable right now.";
    if (queued) button.title = "Queued in Pending Changes. Click to remove it.";
    button.addEventListener("click", () => {
      if (queueableRosterAction(action)) {
        if (queued) {
          removePendingRosterAction(queued.key);
        } else {
          queueRosterAction(player, action, params);
        }
      } else {
        runAction(action, params);
      }
    });
    return button;
  }

  function rosterIrAction(player) {
    const status = String(player?.status || "Active");
    const injury = player?.activeInjury;
    const ir = player?.ir || {};
    const isReserveStatus = ["IR", "PUP", "NFI"].includes(status);
    if (status === "IR") {
      const button = rosterActionButton("Activate From IR", "roster_activate_ir", {
        player_id: player.player_id,
      }, ir.eligible ? "good" : "secondary", { player });
      button.disabled = button.disabled || !ir.eligible;
      button.title = ir.reason || "Player is not eligible to activate yet.";
      return button;
    }
    if (!injury || isReserveStatus || isPracticeSquadPlayer(player)) return null;
    const expected = Number(injury.expectedGames || 0);
    if (expected < 1 && !["Out", "Doubtful"].includes(status)) return null;
    const button = rosterActionButton("Send To IR", "roster_send_ir", {
      player_id: player.player_id,
    }, "warn", { player });
    button.title = expected >= 4
      ? `${injury.injury || "Injury"}; projected ${expected} games.`
      : "Shorter injuries can go to IR, but that is usually only worth it for roster flexibility.";
    return button;
  }

  function rosterStatusNote(player) {
    const status = String(player?.status || "Active");
    if (player?.queuedRosterAction) return `Projected: ${player.queuedRosterAction.label || actionLabel(player.queuedRosterAction.action)}`;
    if (["Active", "Questionable", "Doubtful", "Out"].includes(status)) return "Counts toward active roster";
    if (status === "IR") {
      const ir = player?.ir || {};
      return ir.designatedToReturn ? (ir.reason || "Reserve; can return later") : "Reserve; season-ending";
    }
    if (status === "Practice Squad") return "Practice squad slot";
    if (["PUP", "NFI", "Suspended"].includes(status)) return "Reserve; off active roster";
    return "Roster status";
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
      metric("Staff Read", player.overall ?? "-", `Potential ${player.potential ?? "-"} | ${player.evaluation_confidence || player.evaluation?.confidenceLabel || "Cloudy"}`),
      metric("Depth", assignment ? `${assignment.slot} #${assignment.rank}` : "Unassigned", assignment?.unit || "No room"),
      metric("Role Fit", player.roleScore ? oneDecimal(player.roleScore) : "-", player.role?.key ? roleLabel(player.role.key) : "No role read"),
      metric("Status", player.status || "Active", rosterStatusNote(player)),
      metric(
        "Contract",
        contract.end_year ? `Through ${contract.end_year}` : (contract.type || "Rostered"),
        contract.cap_hit ? `${contract.season || rosterContractSeason() || ""} cap ${money(contract.cap_hit)}`.trim() : (contract.market_tier || "")
      ),
    ]);
    const actions = node("div", "roster-action-grid");
    const profile = node("a", "run-button compact", "View Profile");
    profile.href = playerProfileHref({ playerId: player.player_id, name: player.player_name, team: depth.team, position: player.position });
    const cardLink = node("a", "run-button compact", "View Card");
    cardLink.href = playerCardHref({ playerId: player.player_id, name: player.player_name, team: depth.team, position: player.position });
    const userTeam = String(data.activeSave?.user_team || "").toUpperCase();
    const viewedTeam = String(depth.team || "").toUpperCase();
    const isUserRoster = !userTeam || !viewedTeam || viewedTeam === userTeam;
    const practiceSquadPlayer = isPracticeSquadPlayer(player);
    const irAction = rosterIrAction(player);
    append(actions, [profile, cardLink]);
    if (isUserRoster) {
      const depthButton = node("button", "run-button compact", "Depth Chart");
      depthButton.type = "button";
      depthButton.addEventListener("click", () => {
        state.selectedDepthSlot = assignment?.slot || null;
        switchView("depth");
      });
      actions.append(depthButton);
      if (practiceSquadPlayer) {
        actions.append(rosterActionButton("Promote to Active", "practice_squad_promote", {
          player_id: player.player_id,
        }, "good", { player }));
      }
      if (irAction) actions.append(irAction);
      append(actions, [
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
        }, "danger", { player }),
      ]);
    } else {
      actions.append(node("div", "read-only-note", `${viewedTeam} roster is view-only from here.`));
    }
    const assignments = node("div", "roster-assignment-strip");
    (player.assignments || []).slice(0, 8).forEach((item) => assignments.append(tag(`${item.slot} #${item.rank}`, depthRoleTone(item.rank))));
    if (!assignments.children.length) assignments.append(tag("No depth role", ""));
    append(body, [
      title,
      facts,
      isUserRoster ? sectionBlock("Jersey Number", rosterJerseyControl(player)) : null,
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

  function activeRosterStatus(status) {
    return new Set(["active", "questionable", "doubtful", "out", "pending active"]).has(String(status || "Active").toLowerCase());
  }

  function pendingCutdownMove(candidateOrId) {
    const id = typeof candidateOrId === "object" ? candidateOrId?.player_id : candidateOrId;
    return (state.pendingCutdownMoves || {})[String(id || "")] || null;
  }

  function pendingCutdownEntries() {
    return Object.values(state.pendingCutdownMoves || {});
  }

  function pendingCutdownLabel(move) {
    if (!move) return "";
    return {
      practice_squad: "Pending PS",
      promote_ps: "Pending promote",
      release: "Pending cut",
      release_ps: "Pending PS release",
    }[move.move] || "Pending";
  }

  function setPendingCutdownMove(candidate, move) {
    if (!candidate?.player_id || state.runnerBusy) return;
    const key = String(candidate.player_id);
    const current = pendingCutdownMove(candidate);
    const next = { player_id: Number(candidate.player_id), move, player_name: candidate.player_name || "" };
    state.pendingCutdownMoves = { ...(state.pendingCutdownMoves || {}) };
    if (current?.move === move) {
      delete state.pendingCutdownMoves[key];
    } else {
      state.pendingCutdownMoves[key] = next;
    }
    render();
  }

  function clearPendingCutdownMoves() {
    state.pendingCutdownMoves = {};
    render();
  }

  function applyPendingCutdownMoves() {
    const moves = pendingCutdownEntries();
    if (!moves.length || state.runnerBusy) return;
    runAction("roster_cutdown_apply", { moves });
  }

  function cutdownMoveButton(candidate, move, label, selectedLabel, className = "") {
    const selected = pendingCutdownMove(candidate)?.move === move;
    const button = node("button", `run-button compact ${className} ${selected ? "selected" : ""}`.trim(), selected ? selectedLabel : label);
    button.type = "button";
    button.disabled = state.runnerBusy;
    button.addEventListener("click", () => setPendingCutdownMove(candidate, move));
    return button;
  }

  function adjustPracticeSquadUsage(usage, bucket, delta) {
    const next = { ...usage };
    const inc = (key) => {
      next[key] = Math.max(0, Number(next[key] || 0) + delta);
    };
    inc("total");
    if (bucket === "veteran_exception") inc("veteran_exception_count");
    else if (bucket === "international_exemption") inc("international_exemption_count");
    else inc("developmental_count");
    if (bucket !== "international_exemption") inc("base_count");
    return next;
  }

  function projectedCutdownState(ps) {
    const rowsById = new Map((ps.candidates || []).map((row) => [String(row.player_id), row]));
    let activeCount = Number(ps.activeCount || 0);
    let usage = { ...(ps.usage || {}) };
    pendingCutdownEntries().forEach((move) => {
      const row = rowsById.get(String(move.player_id));
      if (!row) return;
      const isActive = activeRosterStatus(row.current_status);
      const isPracticeSquad = row.current_status === "Practice Squad";
      if (move.move === "practice_squad") {
        if (isActive) activeCount -= 1;
        if (!isPracticeSquad) usage = adjustPracticeSquadUsage(usage, row.bucket, 1);
      } else if (move.move === "promote_ps") {
        if (isPracticeSquad) {
          activeCount += 1;
          usage = adjustPracticeSquadUsage(usage, row.bucket, -1);
        }
      } else if (move.move === "release") {
        if (isActive) activeCount -= 1;
      } else if (move.move === "release_ps" && isPracticeSquad) {
        usage = adjustPracticeSquadUsage(usage, row.bucket, -1);
      }
    });
    return { activeCount: Math.max(0, activeCount), usage };
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
      wrap.append(cutdownMoveButton(candidate, "promote_ps", "Promote Active", "Selected Promote", "good"));
      wrap.append(cutdownMoveButton(candidate, "release_ps", "Release PS", "Selected Release", "warn"));
      return wrap;
    }
    if (candidate.eligible) {
      wrap.append(cutdownMoveButton(candidate, "practice_squad", "Assign PS", "Selected PS", "good"));
    }
    if (activeRosterStatus(candidate.current_status)) {
      wrap.append(cutdownMoveButton(candidate, "release", "Waive/Release", "Selected Cut", "warn"));
    }
    if (!wrap.children.length) wrap.append(node("span", "muted", "No action"));
    return wrap;
  }

  function activeCutdownRows(ps) {
    return (ps.candidates || [])
      .filter((row) => activeRosterStatus(row.current_status))
      .slice()
      .sort((a, b) => {
        const aScore = Number(a.overall || 0) * 1.1 + Number(a.potential || 0) * 0.55 - Number(a.age || 0) * 0.12;
        const bScore = Number(b.overall || 0) * 1.1 + Number(b.potential || 0) * 0.55 - Number(b.age || 0) * 0.12;
        if (aScore !== bScore) return aScore - bScore;
        if (Number(a.overall || 0) !== Number(b.overall || 0)) return Number(a.overall || 0) - Number(b.overall || 0);
        return String(a.player_name || "").localeCompare(String(b.player_name || ""));
      });
  }

  function cutdownPathLabel(candidate) {
    if (candidate.eligible) return "PS eligible";
    const blockers = candidate.blockers || [];
    if (blockers.some((item) => String(item).toLowerCase().includes("waiver"))) return "Waiver first";
    if (blockers.some((item) => String(item).toLowerCase().includes("full"))) return "Slot full";
    return "Release only";
  }

  function activeCutdownActionCell(candidate) {
    const wrap = node("div", "row-actions cutdown-actions");
    if (candidate.eligible) {
      wrap.append(cutdownMoveButton(candidate, "practice_squad", "Move to PS", "Selected PS", "good"));
    }
    wrap.append(cutdownMoveButton(candidate, "release", "Waive/Release", "Selected Cut", "warn"));
    return wrap;
  }

  function activeCutdownTable(ps) {
    const rows = activeCutdownRows(ps);
    if (!rows.length) return node("div", "empty-state", "No active-roster players are available for cutdown.");
    const wrap = node("div", "table-wrap ps-registration-table cutdown-table-wrap");
    const tableEl = node("table", "data-table compact-table cutdown-table");
    const head = node("thead");
    const trHead = node("tr");
    ["Player", "Pos", "Ovr", "Pot", "Age", "Exp", "PS Path", "Read", "Action"].forEach((label) => trHead.append(node("th", null, label)));
    head.append(trHead);
    const body = node("tbody");
    rows.forEach((candidate) => {
      const read = candidate.eligible ? (candidate.reasons || []).join(" ") : (candidate.blockers || []).join(" ");
      const pending = pendingCutdownMove(candidate);
      const tr = node("tr", `${candidate.eligible ? "" : "muted-row"} ${pending ? "pending-cutdown-row" : ""}`.trim());
      const pathCell = node("td");
      pathCell.append(tag(cutdownPathLabel(candidate), candidate.eligible ? "good" : "warn"));
      if (pending) pathCell.append(tag(pendingCutdownLabel(pending), pending.move === "release" ? "warn" : "good"));
      append(tr, [
        node("td", null, candidate.player_name || "-"),
        node("td", "numeric", candidate.position || "-"),
        node("td", "numeric rating-cell", candidate.overall ?? "-"),
        node("td", "numeric rating-cell", candidate.potential ?? "-"),
        node("td", "numeric", candidate.age ?? "-"),
        node("td", "numeric", candidate.years_exp ?? "-"),
        pathCell,
        node("td", "small-note", read || "-"),
        append(node("td"), [activeCutdownActionCell(candidate)]),
      ]);
      body.append(tr);
    });
    append(tableEl, [head, body]);
    wrap.append(tableEl);
    return wrap;
  }

  function cutdownSelectionControls() {
    const moves = pendingCutdownEntries();
    const wrap = node("div", "cutdown-selection-controls");
    const summary = node("div", "small-note", moves.length ? `${moves.length} pending move${moves.length === 1 ? "" : "s"} selected.` : "Select players first, then apply the roster cutdown batch.");
    const actions = node("div", "row-actions");
    const clear = node("button", "run-button compact ghost", "Clear selections");
    clear.type = "button";
    clear.disabled = !moves.length || state.runnerBusy;
    clear.addEventListener("click", clearPendingCutdownMoves);
    const apply = node("button", "run-button compact good", state.runnerBusy ? "Running" : "Apply Selected Moves");
    apply.type = "button";
    apply.disabled = !moves.length || state.runnerBusy || !runnerMode();
    apply.addEventListener("click", applyPendingCutdownMoves);
    append(actions, [clear, apply]);
    append(wrap, [summary, actions]);
    return wrap;
  }

  function renderActiveCutdownPanel(ps, activeLimit, projected) {
    const activeCount = Number(projected?.activeCount ?? ps.activeCount ?? 0);
    const cutsNeeded = Math.max(0, activeCount - Number(activeLimit || 53));
    const rows = activeCutdownRows(ps);
    const card = panel("Active Roster Cutdown", cutsNeeded ? `${cutsNeeded} move${cutsNeeded === 1 ? "" : "s"} needed` : "53-man ready");
    const body = panelBody(card);
    body.append(cutdownSelectionControls());
    const guide = node("div", "cutdown-guide");
    append(guide, [
      tag(`${activeCount}/${activeLimit || 53} projected active`, cutsNeeded ? "warn" : "good"),
      tag(`${rows.filter((row) => row.eligible).length} PS eligible`, "good"),
      tag("Waive/Release keeps waiver rules", "warn"),
    ]);
    body.append(guide);
    body.append(node(
      "p",
      "small-note",
      cutsNeeded
        ? "Trim the active roster here, then use the practice-squad board below to fill the developmental and veteran slots."
        : "You can still make final swaps, but the active roster is already at or below the regular-season limit."
    ));
    body.append(activeCutdownTable(ps));
    return card;
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
      const pending = pendingCutdownMove(candidate);
      const tr = node("tr", `${candidate.eligible ? "" : "muted-row"} ${pending ? "pending-cutdown-row" : ""}`.trim());
      const read = candidate.eligible ? (candidate.reasons || []).join(" ") : (candidate.blockers || []).join(" ");
      const statusCell = node("td");
      statusCell.append(tag(candidate.current_status || "Active", practiceSquadTone(candidate)));
      if (pending) statusCell.append(tag(pendingCutdownLabel(pending), pending.move === "release" || pending.move === "release_ps" ? "warn" : "good"));
      append(tr, [
        node("td", null, candidate.player_name || "-"),
        node("td", "numeric", candidate.position || "-"),
        node("td", "numeric rating-cell", candidate.overall ?? "-"),
        node("td", "numeric rating-cell", candidate.potential ?? "-"),
        node("td", "numeric", candidate.age ?? "-"),
        node("td", "numeric", candidate.years_exp ?? "-"),
        statusCell,
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
    setHeader("Roster Cutdown", `Set ${team}'s 53-man roster and practice squad before the regular season.`);
    const root = document.createDocumentFragment();
    if (runnerMode() && state.practiceSquadLiveKey !== practiceSquadLiveKey() && !state.practiceSquadLoading) {
      loadLivePracticeSquad().then(render);
    }
    const ps = data.practiceSquad || { usage: {}, limits: {}, candidates: [] };
    const projected = projectedCutdownState(ps);
    const usage = projected.usage || ps.usage || {};
    const limits = ps.limits || {};
    const activeLimit = ps.activeLimit || limits.active || 53;
    const summary = panel("Registration Counter", `${ps.phase || "Regular Season"}`);
    if (state.practiceSquadLoading) panelBody(summary).append(node("div", "empty-state", "Refreshing practice squad rules..."));
    const metrics = node("section", "metric-grid compact-metrics ps-registration-metrics");
    append(metrics, [
      metric("Active Roster", `${projected.activeCount ?? ps.activeCount ?? 0}/${activeLimit}`, "Projected after selected moves", Number(projected.activeCount || 0) > Number(activeLimit) ? "warn" : "good"),
      metric("Practice Squad", `${usage.total || 0}/${limits.total || 17}`, "Normal slots plus IPP exemption", Number(usage.total || 0) >= Number(limits.total || 17) ? "warn" : ""),
      metric("Developmental", `${usage.developmental_count || 0}/${limits.developmental || 10}`, "Rookies and one/two-year players"),
      metric("Veteran Exceptions", `${usage.veteran_exception_count || 0}/${limits.veteranException || 6}`, "Three-plus accrued-season proxy"),
      metric("International", `${usage.international_exemption_count || 0}/${limits.internationalExemption || 1}`, "Exempt IPP slot"),
    ]);
    panelBody(summary).append(metrics);
    root.append(summary);

    root.append(renderActiveCutdownPanel(ps, activeLimit, projected));

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

  function tradeAssetKey(asset) {
    if (!asset) return "";
    if (asset.type === "player") return `player:${asset.playerId}`;
    if (asset.type === "pick") return `pick:${asset.pickId}`;
    return "";
  }

  function allTradeAssets(side) {
    const source = side === "user" ? data.tradeCenter?.userAssets : data.tradeCenter?.partnerAssets;
    return [...(source?.players || []), ...(source?.picks || [])];
  }

  function tradeAssetByKey(side, key) {
    return allTradeAssets(side).find((asset) => tradeAssetKey(asset) === key);
  }

  function selectedTradeAssets(side) {
    const slots = side === "user" ? state.tradeUserSlots : state.tradePartnerSlots;
    return slots.map((key) => tradeAssetByKey(side, key)).filter(Boolean);
  }

  function tradeValueTotal(assets) {
    return assets.reduce((total, asset) => total + Number(asset.value || 0), 0);
  }

  function tradeSlotSelect(side, index) {
    const slots = side === "user" ? state.tradeUserSlots : state.tradePartnerSlots;
    const assets = allTradeAssets(side);
    const taken = new Set(slots.filter(Boolean));
    const select = node("select", "trade-slot-select");
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = `Slot ${index + 1}: Empty`;
    select.append(empty);
    assets.forEach((asset) => {
      const key = tradeAssetKey(asset);
      if (taken.has(key) && slots[index] !== key) return;
      const option = document.createElement("option");
      option.value = key;
      option.textContent = `${asset.label || asset.name || "Asset"} | ${Number(asset.value || 0).toFixed(1)}`;
      select.append(option);
    });
    select.value = slots[index] || "";
    select.disabled = state.runnerBusy;
    select.addEventListener("change", () => {
      slots[index] = select.value;
      if (side === "user") state.tradeUserSlots = [...slots];
      else state.tradePartnerSlots = [...slots];
      render();
    });
    return select;
  }

  function tradeAssetSummaryCard(asset) {
    const item = node("div", "trade-asset-card");
    if (!asset) {
      item.append(node("span", "muted", "Empty slot"));
      return item;
    }
    append(item, [
      node("strong", null, asset.label || asset.name || "Asset"),
      node("small", null, asset.type === "player"
        ? `${asset.position || "-"} | ${asset.age || "-"} yrs | ${asset.potential || "-"} POT`
        : `${asset.draftYear || ""} round ${asset.round || "-"}`),
      tag(`${Number(asset.value || 0).toFixed(1)} value`, ""),
    ]);
    return item;
  }

  function tradeSidePanel(side, title, team) {
    const p = panel(title, team?.abbr || "");
    p.classList.add("trade-side-panel");
    const body = panelBody(p);
    const slots = side === "user" ? state.tradeUserSlots : state.tradePartnerSlots;
    const slotGrid = node("div", "trade-slot-grid");
    for (let index = 0; index < 5; index += 1) {
      const slot = node("div", "trade-slot");
      append(slot, [
        tradeSlotSelect(side, index),
        tradeAssetSummaryCard(tradeAssetByKey(side, slots[index])),
      ]);
      slotGrid.append(slot);
    }
    body.append(slotGrid);
    return p;
  }

  function renderTradeCenter() {
    setHeader("Trade Center", "Build player and pick packages, then test them against CPU GM logic.");
    const root = document.createDocumentFragment();
    const trade = data.tradeCenter || {};
    if (runnerMode() && state.tradeLiveKey !== tradeLiveKey() && !state.tradeLoading) {
      loadLiveTrades().then(render);
    }
    const userTeam = trade.userTeam || { abbr: data.activeSave?.user_team || "USER" };
    const partnerTeam = trade.partnerTeam || {};
    if (!state.tradePartnerTeam && partnerTeam.abbr) state.tradePartnerTeam = partnerTeam.abbr;

    const toolbar = panel("Trade Setup", "Live market");
    const toolbarBody = panelBody(toolbar);
    const partnerSelect = node("select", "trade-team-select");
    (trade.teams || []).filter((team) => team.abbr !== userTeam.abbr).forEach((team) => {
      const option = document.createElement("option");
      option.value = team.abbr;
      option.textContent = `${team.abbr} | ${team.name}`;
      partnerSelect.append(option);
    });
    partnerSelect.value = state.tradePartnerTeam || partnerTeam.abbr || "";
    partnerSelect.disabled = state.runnerBusy || state.tradeLoading;
    partnerSelect.addEventListener("change", () => {
      state.tradePartnerTeam = partnerSelect.value;
      state.tradeUserSlots = Array(5).fill("");
      state.tradePartnerSlots = Array(5).fill("");
      state.tradeLiveKey = null;
      loadLiveTrades().then(render);
    });
    const userAssets = selectedTradeAssets("user");
    const partnerAssets = selectedTradeAssets("partner");
    const userValue = tradeValueTotal(userAssets);
    const partnerValue = tradeValueTotal(partnerAssets);
    const submit = controlButton({
      label: "Submit Offer",
      action: "trade_submit",
      params: {
        partner_team: state.tradePartnerTeam || partnerTeam.abbr,
        user_assets: userAssets,
        partner_assets: partnerAssets,
      },
      availability: !userAssets.length || !partnerAssets.length ? { disabledReason: "Add assets to both sides first." } : {},
      tone: "good",
    });
    const cpuMarket = controlButton({
      label: "Run CPU Market",
      action: "trade_cpu_market",
      params: { max_proposals_per_team: 1, ignore_window: true },
      availability: {},
      tone: "",
    });
    append(toolbarBody, [
      append(node("div", "trade-toolbar"), [
        append(node("label", "trade-team-field"), [node("span", null, "Partner"), partnerSelect]),
        metric("Your Offer", userValue.toFixed(1), `${userAssets.length}/5 assets`, userValue >= partnerValue ? "good" : ""),
        metric("CPU Sends", partnerValue.toFixed(1), `${partnerAssets.length}/5 assets`, partnerValue > userValue ? "warn" : ""),
        metric("CPU Chart", partnerTeam.chart || "-", "Partner evaluates on this chart"),
        submit,
        cpuMarket,
      ]),
    ]);
    root.append(toolbar);

    const sides = node("div", "trade-builder-grid");
    append(sides, [
      tradeSidePanel("user", `${userTeam.abbr || "User"} Sends`, userTeam),
      tradeSidePanel("partner", `${partnerTeam.abbr || "CPU"} Sends`, partnerTeam),
    ]);
    root.append(sides);

    const intel = panel("GM Evaluation Notes", "Depth, need, QB caution");
    panelBody(intel).append(node("p", "summary-text",
      "The receiving CPU GM evaluates the package using its assigned trade chart, positional premiums, team needs, outgoing depth loss, and extra QB caution. Accepted user offers execute immediately; close offers can come back as counters."));
    root.append(intel);

    const recent = panel("Recent Trade Activity", `${(trade.recent || []).length} items`);
    const list = node("div", "list compact-list");
    (trade.recent || []).forEach((item) => {
      list.append(row(
        `#${item.proposalId} ${item.proposingTeam} -> ${item.receivingTeam}`,
        `${item.proposalDate || ""} | ${item.proposerNote || item.responderNote || ""}`,
        item.status || "-",
        item.status === "executed" ? "good" : item.status === "rejected" ? "warn" : "",
      ));
    });
    panelBody(recent).append(list.children.length ? list : node("div", "empty-state", "No trade proposals yet."));
    root.append(recent);
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderRosterHub() {
    const userTeam = data.activeSave?.user_team || rosterTeamOptions()[0]?.abbr || "MIN";
    const team = state.rosterTeam || userTeam;
    setHeader("View Roster", `${team} roster by position group, ratings, depth role, and player actions.`);
    const root = document.createDocumentFragment();
    const liveDepth = data.depthChart || { rows: [], roster: [], units: [] };
    const baseDepth = String(liveDepth.team || "").toUpperCase() === String(team).toUpperCase()
      ? liveDepth
      : { team, rows: [], roster: [], units: [] };
    const depth = projectedDepthChart(baseDepth);
    if (runnerMode() && (state.depthChartLiveKey !== depthChartLiveKey() || String(data.depthChart?.team || "") !== String(team)) && !state.depthChartLoading) {
      loadLiveDepthChart().then(render);
    }
    const rows = rosterRows(depth);
    const statusCounts = rosterStatusCounts(rows);
    const starters = rows.filter((player) => player.primaryAssignment?.rank === 1).length;
    const rookies = rows.filter((player) => player.is_rookie).length;
    const contractAlerts = rows.filter((player) => player.contract?.alertType).length;
    const summary = panel("Roster Snapshot", `${depth.teamName || team}`);
    if (state.depthChartLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live roster..."));
    }
    panelBody(summary).append(rosterTeamSelector(team));
    const metrics = node("section", "metric-grid roster-metrics");
    append(metrics, [
      metric("Players", String(rows.length), `${statusCounts.active} active | ${statusCounts.practiceSquad} PS`),
      metric("Starters", String(starters), "Current depth chart #1s"),
      metric("Rookies", String(rookies), "Development watch"),
      metric("Contract Alerts", String(contractAlerts), "Expiring, cap, or restructure watch", contractAlerts ? "warn" : ""),
      statusCounts.pending ? metric("Projected Moves", String(statusCounts.pending), "Queued roster actions", "warn") : null,
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
    depthLink.disabled = isObserveMode();
    depthLink.title = isObserveMode() ? "Depth chart editing is disabled in Observe Mode." : "Open depth chart";
    depthLink.addEventListener("click", () => {
      if (isObserveMode()) return;
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
    const depth = projectedDepthChart(data.depthChart || { rows: [], roster: [], units: [] });
    ensureDepthPackageSelection(depth);
    const selected = selectedDepthSlot(depth);
    if (runnerMode() && state.depthChartLiveKey !== depthChartLiveKey() && !state.depthChartLoading) {
      loadLiveDepthChart().then(render);
    }

    const summary = panel("Coach Room", `${depth.teamName || team}`);
    if (state.depthChartLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live depth chart..."));
    }
    panelBody(summary).append(depthRoomSummary(depth, selected), depthChartClarityStrip(depth, selected));
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

  function depthMoveButtons(slot, player, readOnlyFallback) {
    const wrap = node("span", "action-cell");
    ["up", "down"].forEach((direction) => {
      const params = {
        position: slot,
        player_id: player.player_id,
        direction,
      };
      const queued = pendingDepthActionEntries().some((item) => item.key === depthActionKey("depth_chart_move", params));
      const button = node("button", `run-button compact ${queued ? "selected" : ""}`.trim(), queued ? "Queued" : direction === "up" ? "Up" : "Down");
      button.type = "button";
      button.disabled = !runnerMode() || state.runnerBusy || readOnlyFallback || (direction === "up" && Number(player.depth_rank) <= 1);
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (readOnlyFallback) return;
        queueDepthAction("depth_chart_move", params, {
          title: direction === "up" ? "Move Up Depth Chart" : "Move Down Depth Chart",
          detail: `${player.player_name} ${direction === "up" ? "up" : "down"} in ${slot}.`,
        });
      });
      wrap.append(button);
    });
    return wrap;
  }

  function depthReplacementControl(depth, slot, rank, roster, currentPlayerId) {
    const wrap = node("span", "replace-control");
    const select = node("select", "depth-select");
    const sorted = [...roster].sort((a, b) => {
      const fit = Number(playerFitsSlot(b, slot)) - Number(playerFitsSlot(a, slot));
      if (fit) return fit;
      return Number(b.role?.score || 0) - Number(a.role?.score || 0);
    });
    sorted.forEach((player) => {
      const option = node("option", null, `${playerFitsSlot(player, slot) ? "*" : " "} ${player.player_name} (${player.position})`);
      const conflict = formationConflictEntry(depth, player.player_id, slot, rank);
      option.value = String(player.player_id);
      option.selected = String(player.player_id) === String(currentPlayerId);
      option.disabled = Boolean(conflict) && String(player.player_id) !== String(currentPlayerId);
      if (conflict) option.textContent += ` - already at ${conflict.label}${conflict.rank > 1 ? ` #${conflict.rank}` : ""}`;
      select.append(option);
    });
    const queuedForCurrent = () => pendingDepthActionEntries().some((item) => item.key === depthActionKey("depth_chart_set", {
      position: slot,
      rank,
      player_id: Number(select.value),
    }));
    const set = node("button", `run-button compact ${queuedForCurrent() ? "selected" : ""}`.trim(), queuedForCurrent() ? "Queued" : "Set");
    set.type = "button";
    set.disabled = !runnerMode() || state.runnerBusy;
    select.addEventListener("change", () => {
      const queued = queuedForCurrent();
      set.textContent = queued ? "Queued" : "Set";
      set.classList.toggle("selected", queued);
    });
    set.addEventListener("click", (event) => {
      event.stopPropagation();
      const conflict = formationConflictEntry(depth, select.value, slot, rank);
      if (conflict && String(select.value) !== String(currentPlayerId)) {
        showToast(formationConflictText(conflict));
        return;
      }
      const chosen = (roster || []).find((player) => String(player.player_id) === String(select.value)) || {};
      queueDepthAction("depth_chart_set", {
        position: slot,
        rank,
        player_id: Number(select.value),
      }, {
        title: "Replace Depth Spot",
        detail: `${chosen.player_name || "Selected player"} to ${slot} #${rank}.`,
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
    const eventPanel = draftEventPanel(draft.events || []);

    const boardPanel = panel(
      "Draft Board",
      activeDraftPosition === "all"
        ? `${visibleDraftBoard.length || 0} shown`
        : `${visibleDraftBoard.length || 0} ${activeDraftPosition}`
    );
    boardPanel.classList.add("draft-board-panel");
    panelBody(boardPanel).append(draftBoardToolbar(board, visibleDraftBoard, draftPositions, activeDraftPosition));
    panelBody(boardPanel).append(draftBoardTable(visibleDraftBoard, selected));
    append(sideRail, [controlsPanel, queuePanel, eventPanel]);
    append(draftMain, [boardPanel, sideRail]);
    root.append(draftMain);
    if ((draft.userSelections || []).length) {
      root.append(draftUserSelectionsPanel(draft.userSelections || [], draft));
    }
    if (state.draftProspectPopoverOpen && selected) {
      root.append(draftProspectPopover(selected));
    }
    if (state.draftTradeModal) {
      root.append(draftTradeModal());
    }
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
    window.requestAnimationFrame(() => centerCurrentDraftQueuePick());
  }

  function draftRoomTopline(draft, visibleBoard, selected) {
    const draftState = draft?.state || null;
    const userTeam = draftState?.user_team || data.activeSave?.user_team || (isObserveMode() ? "CPU" : "MIN");
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

  function draftEventPanel(events) {
    const p = panel("Draft Feed", events.length ? `${events.length} recent` : "Quiet");
    p.classList.add("draft-event-panel");
    const body = panelBody(p);
    const list = node("div", "list compact-list draft-event-list");
    events.slice(0, 10).forEach((event) => {
      const item = node("div", "row draft-event-row");
      append(item, [
        append(node("span"), [
          node("strong", null, draftEventLabel(event)),
          node("small", null, event.message || ""),
        ]),
      ]);
      list.append(item);
    });
    body.append(list.children.length ? list : node("div", "empty-state", "Draft actions and trade talks will appear here."));
    return p;
  }

  function draftEventLabel(event) {
    const type = String(event?.event_type || "");
    if (type === "user_draft_trade_rejected") return "Trade rejected";
    if (type === "user_draft_trade" || type === "draft_trade") return "Draft trade";
    if (type === "selection") return "Selection";
    return type ? type.replace(/_/g, " ") : "Draft event";
  }

  function draftWarRoomPanel(draft, visibleBoard, selected) {
    const draftState = draft?.state || null;
    const userTeam = draftState?.user_team || data.activeSave?.user_team || (isObserveMode() ? "CPU" : "MIN");
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
    const right = node("div", "draft-queue-actions");
    right.append(tag(pick.is_used ? "Used" : "On Deck", pick.is_used ? "good" : ""));
    if (canUserTradeForDraftPick(pick, currentQueuePick)) {
      const trade = node("button", "mini-action-button", state.runnerBusy ? "Running" : "Trade");
      trade.type = "button";
      trade.disabled = state.runnerBusy || !runnerMode();
      trade.title = `Offer user-owned picks to acquire pick ${overallPick}.`;
      trade.addEventListener("click", (event) => {
        event.stopPropagation();
        openDraftTradeModal(pick);
      });
      right.append(trade);
    }
    append(item, [left, right]);
    return item;
  }

  function canUserTradeForDraftPick(pick, currentQueuePick) {
    if (!pick?.pick_id || Number(pick.is_used || 0)) return false;
    const draftState = data.draft?.state;
    if (!draftState) return false;
    const userTeam = String(draftState.user_team || data.activeSave?.user_team || "").toUpperCase();
    const owner = String(pick.current_team || "").toUpperCase();
    if (!userTeam || !owner || owner === userTeam) return false;
    const pickNumber = Number(pick.effective_pick_number || pick.pick_number || 0);
    const currentNumber = Number(currentQueuePick || draftState.current_pick_number || 0);
    if (!pickNumber || !currentNumber || pickNumber < currentNumber) return false;
    return pickNumber <= currentNumber + 48;
  }

  function openDraftTradeModal(pick) {
    const offerPickIds = defaultDraftTradeOfferPickIds(pick);
    state.draftTradeModal = {
      targetPickId: Number(pick?.pick_id || 0),
      targetPick: { ...(pick || {}) },
      offerPickIds,
      addPickId: "",
      status: "draft",
      message: "Build a pick package and submit it to the GM on the clock.",
    };
    render();
  }

  function draftTradeAssets() {
    return (data.draft?.userTradeAssets || [])
      .filter((asset) => asset?.pickId && !Number(asset.isUsed || 0))
      .sort((a, b) => {
        const ay = Number(a.draftYear || 9999);
        const by = Number(b.draftYear || 9999);
        if (ay !== by) return ay - by;
        const ar = Number(a.round || 99);
        const br = Number(b.round || 99);
        if (ar !== br) return ar - br;
        return Number(a.effectivePickNumber || a.pickNumber || a.pickId || 9999) - Number(b.effectivePickNumber || b.pickNumber || b.pickId || 9999);
      });
  }

  function defaultDraftTradeOfferPickIds(targetPick) {
    const assets = draftTradeAssetsForTarget(targetPick);
    if (!assets.length) return [];
    const targetNumber = Number(targetPick?.effective_pick_number || targetPick?.pick_number || 9999);
    const targetRound = Number(targetPick?.round || 7);
    let maxPicks = targetRound <= 1 ? 4 : targetRound <= 2 ? 3 : 2;
    if (targetNumber <= 8) maxPicks = 4;
    return assets.slice(0, maxPicks).map((asset) => Number(asset.pickId)).filter(Boolean);
  }

  function draftTradeAssetsForTarget(targetPick) {
    const targetNumber = Number(targetPick?.effective_pick_number || targetPick?.pick_number || 0);
    const draftYear = Number(targetPick?.draft_year || data.draft?.year || 0);
    return draftTradeAssets().filter((asset) => {
      if (!targetNumber || Number(asset.draftYear || 0) !== draftYear) return true;
      return Number(asset.effectivePickNumber || asset.pickNumber || 0) > targetNumber;
    });
  }

  function draftTradeAssetLabel(asset) {
    if (!asset) return "Draft pick";
    if (asset.label) return asset.label;
    const year = asset.draftYear || data.draft?.year || "";
    const round = asset.round || "-";
    const pickNumber = asset.effectivePickNumber || asset.pickNumber;
    const original = asset.originalTeam && asset.originalTeam !== asset.currentTeam ? ` from ${asset.originalTeam}` : "";
    return pickNumber ? `${year} #${pickNumber} (R${round})${original}` : `${year} Round ${round}${original}`;
  }

  function draftTradeTargetLabel(pick) {
    if (!pick) return "Target pick";
    const pickNumber = pick.effective_pick_number || pick.pick_number;
    const round = pick.round || "-";
    const team = pick.current_team || pick.team || "-";
    const teamName = pick.current_team_name || "Team on clock";
    return `${pickNumber ? `Pick #${pickNumber}` : "Pick"} · R${round} · ${team} ${teamName}`;
  }

  function draftTradeModal() {
    const modal = state.draftTradeModal || {};
    const draft = data.draft || {};
    const targetPick = (draft.pickQueue || []).find((pick) => Number(pick.pick_id) === Number(modal.targetPickId)) || modal.targetPick || null;
    const selectedIds = [...new Set((modal.offerPickIds || []).map(Number).filter(Boolean))]
      .slice(0, DRAFT_TRADE_MAX_OFFER_PICKS);
    modal.offerPickIds = selectedIds;
    const selectedIdSet = new Set(selectedIds.map(String));
    const assets = draftTradeAssetsForTarget(targetPick);
    const selectedAssets = selectedIds
      .map((id) => assets.find((asset) => Number(asset.pickId) === Number(id)))
      .filter(Boolean);
    const availableAssets = assets.filter((asset) => !selectedIdSet.has(String(asset.pickId)));
    const select = node("select", "draft-trade-select");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    const atOfferLimit = selectedIds.length >= DRAFT_TRADE_MAX_OFFER_PICKS;
    placeholder.textContent = atOfferLimit
      ? `Maximum ${DRAFT_TRADE_MAX_OFFER_PICKS} picks`
      : availableAssets.length ? "Add another pick..." : "No more user picks available";
    select.append(placeholder);
    availableAssets.forEach((asset) => {
      const option = document.createElement("option");
      option.value = String(asset.pickId);
      option.textContent = draftTradeAssetLabel(asset);
      select.append(option);
    });
    select.value = modal.addPickId || "";
    select.disabled = atOfferLimit || !availableAssets.length || state.runnerBusy;
    select.addEventListener("change", () => {
      state.draftTradeModal.addPickId = select.value;
    });

    const addButton = node("button", "control-button secondary", "Add Pick");
    addButton.type = "button";
    addButton.disabled = state.runnerBusy || atOfferLimit || !availableAssets.length;
    addButton.addEventListener("click", () => {
      const pickId = Number(state.draftTradeModal?.addPickId || select.value || availableAssets[0]?.pickId || 0);
      if (!pickId) return;
      if (selectedIds.length >= DRAFT_TRADE_MAX_OFFER_PICKS) return;
      state.draftTradeModal.offerPickIds = [...selectedIds, pickId].slice(0, DRAFT_TRADE_MAX_OFFER_PICKS);
      state.draftTradeModal.addPickId = "";
      render();
    });

    const chips = node("div", "draft-trade-chip-list");
    selectedAssets.forEach((asset) => {
      const chip = node("button", "draft-trade-chip", draftTradeAssetLabel(asset));
      chip.type = "button";
      chip.disabled = state.runnerBusy;
      chip.title = "Remove this pick from the offer";
      chip.addEventListener("click", () => {
        state.draftTradeModal.offerPickIds = selectedIds.filter((id) => Number(id) !== Number(asset.pickId));
        render();
      });
      chips.append(chip);
    });
    if (!selectedAssets.length) {
      chips.append(node("div", "empty-state compact-empty", "Add at least one pick to make an offer."));
    }

    const close = node("button", "icon-button close-button", "Close");
    close.type = "button";
    close.disabled = state.runnerBusy;
    close.addEventListener("click", () => {
      state.draftTradeModal = null;
      render();
    });
    const submit = node("button", "control-button primary", state.runnerBusy ? "Sending" : "Submit Offer");
    submit.type = "button";
    submit.disabled = state.runnerBusy || !runnerMode() || !targetPick?.pick_id || !selectedIds.length || selectedIds.length > DRAFT_TRADE_MAX_OFFER_PICKS;
    submit.addEventListener("click", () => {
      runAction("draft_user_trade", {
        target_pick_id: Number(targetPick.pick_id),
        offer_pick_ids: selectedIds,
      });
    });

    const overlay = node("div", "box-score-modal-overlay draft-trade-modal-overlay");
    const dialog = node("section", "box-score-modal draft-trade-modal");
    append(dialog, [
      append(node("div", "box-score-modal-header draft-trade-modal-header"), [
        append(node("div"), [
          node("span", `tag ${modal.status === "rejected" ? "warn" : ""}`.trim(), modal.status === "rejected" ? "Rejected" : "Draft Trade"),
          node("h3", null, targetPick ? draftTradeTargetLabel(targetPick) : "Draft Pick Trade"),
          node("small", "muted", "The receiving GM evaluates this against their assigned trade value chart."),
        ]),
        close,
      ]),
      append(node("div", "draft-trade-status"), [
        node("strong", null, modal.status === "rejected" ? "The offer was declined." : "Build your package."),
        node("small", null, modal.message || ""),
        node("small", "muted", `Offer packages are capped at ${DRAFT_TRADE_MAX_OFFER_PICKS} picks so trade talks stay realistic.`),
      ]),
      append(node("div", "draft-trade-package"), [
        node("h4", null, "Your Offer"),
        chips,
        append(node("div", "draft-trade-add-row"), [select, addButton]),
      ]),
      append(node("div", "draft-trade-footer"), [
        node("small", "muted", `Future picks are eligible, but offers are capped at ${DRAFT_TRADE_MAX_OFFER_PICKS} total picks. Some premium picks simply may not be realistically available.`),
        submit,
      ]),
    ]);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay && !state.runnerBusy) {
        state.draftTradeModal = null;
        render();
      }
    });
    overlay.append(dialog);
    return overlay;
  }

  function firstRoundDraftTradeAlertEvent() {
    if (isObserveMode()) return null;
    if (state.view !== "draft") return null;
    const draft = data.draft || {};
    const event = (draft.events || []).find((item) => {
      const type = String(item?.event_type || "");
      return Number(item?.round || 0) === 1 && (type === "draft_trade" || type === "user_draft_trade");
    });
    if (!event) return null;
    const key = `${draft.year || ""}:${event.event_id || event.created_at || event.message || ""}`;
    if (state.draftTradeAlertDismissed[key]) return null;
    return { ...event, key };
  }

  function draftTradeAlertModal() {
    const event = firstRoundDraftTradeAlertEvent();
    if (!event) return null;
    const details = event.details || {};
    const buyerReceives = details.buyerReceives || [];
    const sellerReceives = details.sellerReceives || [];
    const buyerLabel = details.buyer || event.team || "Acquiring team";
    const sellerLabel = details.seller || event.original_team || "Original team";
    const pickLabel = event.pick_number ? `Pick #${event.pick_number}` : "First-round pick";
    const prospectName = details.target?.name || event.prospect_name || "";
    const prospectMeta = [details.target?.position || event.prospect_position, event.prospect_college].filter(Boolean).join(" | ");
    const valueText = Number(details.offerValue || 0) && Number(details.targetValue || 0)
      ? `Trade value: ${Number(details.offerValue).toFixed(1)} offered vs ${Number(details.targetValue).toFixed(1)} required`
      : "";
    const tradeTitle = `${buyerLabel || "Team"} - ${sellerLabel || "Team"} Trade`;
    const closeAlert = () => {
      state.draftTradeAlertDismissed[event.key] = true;
      render();
    };
    const close = node("button", "icon-button close-button", "Close");
    close.type = "button";
    close.addEventListener("click", closeAlert);
    const actions = node("div", "draft-trade-alert-actions");
    if (event.prospect_id) {
      const prospectButton = node("button", "control-button secondary", "Open Prospect");
      prospectButton.type = "button";
      prospectButton.addEventListener("click", () => {
        state.draftTradeAlertDismissed[event.key] = true;
        openProspect(event.prospect_id, { preferredView: "draft" });
      });
      actions.append(prospectButton);
    }
    const ok = node("button", "control-button primary", "Got It");
    ok.type = "button";
    ok.addEventListener("click", closeAlert);
    actions.append(ok);

    const overlay = node("div", "box-score-modal-overlay draft-trade-modal-overlay draft-trade-alert-overlay");
    const dialog = node("section", "box-score-modal draft-trade-modal draft-trade-alert-modal");
    append(dialog, [
      append(node("div", "box-score-modal-header draft-trade-modal-header"), [
        append(node("div"), [
          node("span", "tag good", "First-Round Trade"),
          node("h3", null, tradeTitle),
          node("small", "muted", event.created_at ? `Draft room update | ${event.created_at}` : "Draft room update"),
        ]),
        close,
      ]),
      append(node("div", "draft-trade-alert-body"), [
        append(node("div", "draft-trade-alert-summary"), [
          node("strong", null, `${buyerLabel} moved up for ${pickLabel}`),
          node("span", null, event.message || "A first-round draft trade has been completed."),
          details.legacyReconstructed ? node("em", null, "Compensation reconstructed from the draft ledger.") : null,
        ]),
        append(node("div", "draft-trade-alert-sides"), [
          draftTradeAlertSide(`${buyerLabel} Receives`, buyerReceives, [
            pickLabel,
            prospectName ? `Target: ${prospectName}${prospectMeta ? ` | ${prospectMeta}` : ""}` : "",
          ].filter(Boolean)),
          draftTradeAlertSide(`${sellerLabel} Receives`, sellerReceives, []),
        ]),
        append(node("div", "draft-trade-alert-grid"), [
          valueText ? append(node("div", "draft-trade-alert-card"), [
            node("small", null, "Value"),
            node("strong", null, valueText),
          ]) : null,
          append(node("div", "draft-trade-alert-card"), [
            node("small", null, "Why It Happened"),
            node("strong", null, details.reason || "Draft-room trade negotiation"),
          ]),
        ]),
      ]),
      append(node("div", "draft-trade-footer"), [
        node("small", "muted", "First-round trade alerts appear once when they happen."),
        actions,
      ]),
    ]);
    overlay.addEventListener("click", (clickEvent) => {
      if (clickEvent.target === overlay) closeAlert();
    });
    overlay.append(dialog);
    return overlay;
  }

  function draftTradeAlertSide(title, picks, fallbackLines = []) {
    const side = node("section", "draft-trade-alert-side");
    const pickCount = (picks || []).length;
    const list = node("div", "draft-trade-alert-picks");
    (picks || []).forEach((pick) => list.append(draftTradeAlertPick(pick)));
    if (!list.children.length) {
      fallbackLines.forEach((line) => {
        if (line) list.append(node("div", "draft-trade-alert-pick", line));
      });
    }
    if (!list.children.length) {
      list.append(node("div", "draft-trade-alert-pick muted", "Compensation details unavailable for this older trade event."));
    }
    append(side, [
      append(node("div", "draft-trade-alert-side-head"), [
        node("small", null, title),
        node("span", null, pickCount ? `${pickCount} asset${pickCount === 1 ? "" : "s"}` : "Fallback"),
      ]),
      list,
    ]);
    return side;
  }

  function draftTradeAlertPick(pick) {
    const pickNumber = Number(pick?.pickNumber || 0);
    const round = Number(pick?.round || 0);
    const draftYear = pick?.draftYear || "";
    const label = pick?.label || (pickNumber && round ? `${draftYear} R${round}, Pick ${pickNumber}` : `${draftYear} Round ${round}`.trim());
    const row = node("div", "draft-trade-alert-pick");
    append(row, [
      node("strong", null, label || "Draft pick"),
      pickNumber && round ? node("span", null, `Round ${round} | Pick ${pickNumber}`) : null,
    ]);
    return row;
  }

  function draftSelectionNameLink(playerId, prospectId, name, position, preferPlayer, team) {
    const label = `${name || "Selected Player"}${position ? ` (${position})` : ""}`;
    if (preferPlayer && playerId) return playerLink(playerId, label, "player-link strong-link", { team, position });
    if (prospectId) return prospectLink(prospectId, label, "prospect-link strong-link", { preferredView: "draft" });
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
    if (!board.length) return selectedDraftProspectFromDraftData(state.selectedDraftProspectId);
    const selected = board.find((player) => String(player.prospect_id) === String(state.selectedDraftProspectId));
    if (selected) return selected;
    const selectedFromDraft = selectedDraftProspectFromDraftData(state.selectedDraftProspectId);
    if (selectedFromDraft) return selectedFromDraft;
    state.selectedDraftProspectId = board[0].prospect_id;
    return board[0];
  }

  function selectedDraftProspectFromDraftData(prospectId) {
    const id = String(prospectId || "");
    if (!id) return null;
    const queueMatch = (data.draft?.pickQueue || []).find((pick) => String(pick.selected_prospect_id || pick.selectedProspect?.prospect_id || "") === id);
    if (queueMatch?.selectedProspect) return queueMatch.selectedProspect;
    if (queueMatch) {
      return {
        prospect_id: queueMatch.selected_prospect_id,
        player_name: queueMatch.selected_player_name || "Selected Player",
        position: queueMatch.selected_player_position,
        college: queueMatch.selected_player_college || queueMatch.prospect_college,
        scout_confidence: queueMatch.scout_confidence || queueMatch.scouting_confidence || "Drafted",
        status: "Drafted",
        scouting_summary: `${queueMatch.current_team || "Team"} selected this prospect at pick ${queueMatch.effective_pick_number || queueMatch.pick_number || "-"}.`,
        scouting_report: "Full draft profile is no longer on the active board, but this is the selected prospect tied to the pick.",
        details_exported: false,
      };
    }
    const selectionMatch = [...(data.draft?.selections || []), ...(data.draft?.userSelections || [])]
      .find((selection) => String(selection.prospectId || selection.prospect_id || "") === id);
    if (!selectionMatch) return null;
    return {
      prospect_id: selectionMatch.prospectId || selectionMatch.prospect_id,
      player_name: selectionMatch.playerName || selectionMatch.player_name || "Selected Player",
      position: selectionMatch.position,
      college: selectionMatch.college,
      height_in: selectionMatch.heightIn,
      weight_lbs: selectionMatch.weightLbs,
      scout_grade: selectionMatch.scoutGrade,
      scout_ceiling: selectionMatch.scoutCeiling,
      scout_risk: selectionMatch.scoutRisk,
      primary_role: selectionMatch.primaryRole,
      archetype: selectionMatch.archetype,
      public_board_rank: selectionMatch.publicBoardRank,
      scouting_summary: selectionMatch.scoutingSummary || selectionMatch.publicGradeNote,
      scouting_projection: selectionMatch.scoutingProjection,
      scout_confidence: selectionMatch.scoutConfidence || "Drafted",
      status: "Drafted",
      details_exported: false,
    };
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
    const wrap = node("span", "prospect-name-cell with-thumb draft-row-prospect");
    const copy = node("span", "prospect-name-copy");
    const button = node("button", "prospect-link", prospectDisplayName(player));
    button.type = "button";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openDraftProspectPopover(player.prospect_id);
    });
    append(copy, [
      button,
      node("small", null, roleLabel(player.primary_role || player.archetype)),
    ]);
    append(wrap, [prospectThumbnail(player, "tiny"), copy]);
    return wrap;
  }

  function prospectDisplayName(prospect) {
    return prospect?.player_name
      || prospect?.playerName
      || `${prospect?.first_name || prospect?.firstName || ""} ${prospect?.last_name || prospect?.lastName || ""}`.trim()
      || "Prospect";
  }

  function prospectInitials(prospect) {
    const name = prospectDisplayName(prospect);
    const pieces = name.split(/\s+/).filter(Boolean);
    if (!pieces.length) return "P";
    if (pieces.length === 1) return pieces[0].slice(0, 2).toUpperCase();
    return `${pieces[0][0] || ""}${pieces[pieces.length - 1][0] || ""}`.toUpperCase();
  }

  function prospectThumbnail(prospect, size = "") {
    const thumb = node("span", `prospect-thumb ${size}`.trim());
    const label = prospectDisplayName(prospect);
    thumb.title = label;
    thumb.setAttribute("aria-hidden", "true");
    if (prospect?.portrait) {
      const img = document.createElement("img");
      img.src = prospect.portrait;
      img.alt = "";
      img.loading = "lazy";
      img.addEventListener("error", () => {
        img.remove();
        thumb.classList.add("image-missing");
        thumb.textContent = prospectInitials(prospect);
      }, { once: true });
      thumb.append(img);
    } else {
      thumb.classList.add("image-missing");
      thumb.textContent = prospectInitials(prospect);
    }
    return thumb;
  }

  function prospectFromBoards(prospectId) {
    const id = String(prospectId || "");
    if (!id) return null;
    const pools = [
      data.scouting?.board || [],
      data.draft?.board || [],
      (data.draft?.pickQueue || []).map((pick) => pick.selectedProspect).filter(Boolean),
      data.draft?.selections || [],
      data.draft?.userSelections || [],
    ];
    for (const pool of pools) {
      const match = (pool || []).find((item) => String(item?.prospect_id || item?.prospectId || "") === id);
      if (match) return match;
    }
    return null;
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
      player.hometown ? node("small", null, player.hometown) : null,
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
    if (player.portrait) {
      const portrait = node("div", "prospect-portrait");
      const img = document.createElement("img");
      img.src = player.portrait;
      img.alt = `${player.player_name || "Prospect"} portrait`;
      img.loading = "lazy";
      portrait.append(img);
      identity.append(portrait);
    }
    const identityText = node("div", "prospect-identity-text");
    append(identityText, [
      node("h3", null, player.player_name),
      node("div", "prospect-tags"),
    ]);
    identity.append(identityText);
    const tags = identityText.querySelector(".prospect-tags");
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
      ["Hometown", valueOrDash(player.hometown)],
      ["Pathway", valueOrDash(player.development_pathway)],
      ["Origin", valueOrDash(player.birth_country)],
      ["Late Buzz", valueOrDash(player.late_process_status)],
      ["Board Move", boardMoveText(player.public_board_delta)],
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
        ["Medical", player.medical_risk || (Number(player.combine_injured || 0) ? "Flag" : "Clear")],
      ], "compact")));
    } else {
      const workoutDate = data.scouting?.workoutVisibility?.combineDate || data.scouting?.workoutVisibility?.combineEndDate;
      body.append(sectionBlock("Combine", node("div", "empty-state", workoutDate ? `Combine data unlocks after ${shortDate(workoutDate)}.` : "Combine data is not available yet.")));
    }

    body.append(sectionBlock("Process Notes", detailGrid([
      ["Medical", `${player.medical_flag || "Clean file"}${player.medical_risk ? ` | ${player.medical_risk}` : ""}`],
      ["Interview", `${player.interview_trait || "-"}${player.interview_grade ? ` | ${player.interview_grade}` : ""}`],
      ["Private", `${player.private_workout_type || "None"}${player.private_workout_interest ? ` | ${player.private_workout_interest}` : ""}`],
      ["Name", player.name_background_note || player.name_pronunciation_note || player.name_storyline_note || "-"],
      ["Family", player.family_football_background || "-"],
      ["Pipeline", player.pipeline_note || player.discovery_notes || "-"],
      ["Board", player.late_process_note || "-"],
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

  function boardMoveText(delta) {
    const value = Number(delta || 0);
    if (!value) return "Stable";
    return `${value > 0 ? "+" : ""}${value}`;
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
      unavailable.title = "Actions unavailable right now.";
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
    if (isObserveMode()) return false;
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
    setHeader("Staff Inbox", "Front office decisions, scouting notes, medical updates, and league communication.");
    const root = document.createDocumentFragment();
    if (runnerMode() && state.inboxLiveKey !== inboxLiveKey() && !state.inboxLoading) {
      loadLiveInbox().then(render);
    }
    root.append(renderInboxPanel({
      limit: 80,
      title: "Inbox",
    }));
    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderInboxPanel(options = {}) {
    const scouting = data.scouting || {};
    const allMessages = scouting.inbox || [];
    const buckets = inboxMessageBuckets(allMessages);
    const activeTab = activeInboxTab(buckets);
    const messages = buckets[activeTab] || [];
    const unread = messages.filter((message) => !Number(message.is_read || 0)).length;
    const limit = Number(options.limit || 12);
    const p = panel(
      options.title || "Inbox",
      inboxTabSubtitle(activeTab, buckets),
    );
    const body = panelBody(p);
    body.append(inboxSummaryGrid(buckets));
    body.append(inboxTabs(buckets, activeTab));
    const actions = node("div", "command-actions compact-actions");
    const activeDef = inboxTabDefinitions().find((tab) => tab.key === activeTab) || inboxTabDefinitions()[0];
    const unreadIds = messages
      .filter((message) => !Number(message.is_read || 0) && Number(message.message_id || 0))
      .map((message) => Number(message.message_id));
    const markRead = node("button", "copy-button", activeTab === "archived" ? "Already Read" : `Mark ${activeDef.shortLabel || activeDef.label} Read`);
    markRead.type = "button";
    markRead.disabled = state.runnerBusy || !runnerMode() || !unreadIds.length;
    markRead.addEventListener("click", () => runAction("inbox_mark_read", {
      message_ids: unreadIds,
      scope: activeTab,
    }));
    actions.append(markRead);
    actions.append(node("span", "inbox-action-note", inboxActionNote(activeTab, unread, messages.length)));
    body.append(actions);
    if (state.inboxLoading) {
      body.append(node("div", "empty-state", "Refreshing live inbox..."));
    }

    const list = node("div", "inbox-list");
    if (!messages.length) {
      list.append(node(
        "div",
        "empty-state",
        activeDef.empty || "No messages in this bucket.",
      ));
    } else {
      messages.slice(0, limit).forEach((message) => {
        list.append(inboxMessageCard(message, activeTab));
      });
      if (messages.length > limit) {
        list.append(node("div", "message-list-note", `${messages.length - limit} older message${messages.length - limit === 1 ? "" : "s"} hidden in this view.`));
      }
    }
    body.append(list);
    return p;
  }

  function inboxTabDefinitions() {
    return [
      {
        key: "priority",
        label: "Priority",
        shortLabel: "Priority",
        metric: "Priority",
        empty: "No priority items right now. Staff will bubble up injuries, deadlines, and decisions here.",
      },
      {
        key: "team",
        label: "Team",
        shortLabel: "Team",
        metric: "Team",
        empty: "No team messages yet. Staff, roster, contract, and development notes will collect here.",
      },
      {
        key: "scouting",
        label: "Scouting",
        shortLabel: "Scouting",
        metric: "Scouting",
        empty: "No scouting messages yet. Prospect reports will collect here.",
      },
      {
        key: "medical",
        label: "Medical",
        shortLabel: "Medical",
        metric: "Medical",
        empty: "No medical messages right now.",
      },
      {
        key: "league",
        label: "League",
        shortLabel: "League",
        metric: "League",
        empty: "No league messages yet. Major transactions, awards, and league notes will collect here.",
      },
      {
        key: "archived",
        label: "Archived",
        shortLabel: "Archived",
        metric: "Read",
        empty: "No read messages have been archived yet.",
      },
    ];
  }

  function activeInboxTab(buckets) {
    const requested = state.inboxTab === "main" || state.inboxTab === "frontOffice" ? "team" : state.inboxTab;
    const known = new Set(inboxTabDefinitions().map((tab) => tab.key));
    if (known.has(requested)) {
      state.inboxTab = requested;
      return requested;
    }
    const fallback = buckets.priority.length ? "priority" : "team";
    state.inboxTab = fallback;
    return fallback;
  }

  function inboxTabSubtitle(activeTab, buckets) {
    const totalUnread = buckets.team.length + buckets.scouting.length + buckets.medical.length + buckets.league.length;
    if (activeTab === "archived") {
      return `${buckets.archived.length} read message${buckets.archived.length === 1 ? "" : "s"}`;
    }
    if (activeTab === "priority") {
      return `${buckets.priority.length} priority item${buckets.priority.length === 1 ? "" : "s"} | ${totalUnread} unread`;
    }
    const messages = buckets[activeTab] || [];
    const def = inboxTabDefinitions().find((tab) => tab.key === activeTab);
    return `${messages.length} ${String(def?.label || activeTab).toLowerCase()} message${messages.length === 1 ? "" : "s"} | ${totalUnread} unread`;
  }

  function inboxSummaryGrid(buckets) {
    const grid = node("section", "inbox-summary-grid");
    inboxTabDefinitions().filter((tab) => tab.key !== "archived").forEach((tab) => {
      const count = (buckets[tab.key] || []).length;
      const card = node("button", `inbox-summary-card ${state.inboxTab === tab.key ? "active" : ""}`.trim());
      card.type = "button";
      card.addEventListener("click", () => {
        state.inboxTab = tab.key;
        render();
      });
      append(card, [
        node("span", null, tab.metric),
        node("strong", null, String(count)),
      ]);
      grid.append(card);
    });
    return grid;
  }

  function isScoutingInboxMessage(message) {
    const category = String(message.category || "").toLowerCase();
    const source = String(message.source || "").toLowerCase();
    const relatedTable = String(message.related_table || "").toLowerCase();
    return category === "scouting"
      || relatedTable === "draft_prospects"
      || relatedTable === "draft_classes"
      || source.includes("scout");
  }

  function inboxMessageText(message) {
    return [
      message.title,
      message.body,
      message.category,
      message.source,
      message.priority,
      message.related_table,
    ].map((value) => String(value || "").toLowerCase()).join(" ");
  }

  function isMedicalInboxMessage(message) {
    const text = inboxMessageText(message);
    return text.includes("medical")
      || text.includes("injury")
      || text.includes("injured")
      || text.includes("injuries")
      || text.includes("injury report")
      || text.includes("placed on ir")
      || text.includes("return date")
      || text.includes("out for")
      || text.includes("questionable")
      || text.includes("doubtful")
      || text.includes("concussion")
      || text.includes("knee")
      || text.includes("ankle")
      || text.includes("hamstring")
      || String(message.category || "").toLowerCase() === "medical";
  }

  function isTeamInboxMessage(message) {
    const text = inboxMessageText(message);
    const userTeam = String(data.activeSave?.user_team || data.userTeam || "").toUpperCase();
    const relatedTeam = String(message.relatedPlayer?.team || "").toUpperCase();
    if (userTeam && relatedTeam === userTeam) return true;
    return text.includes("front office")
      || text.includes("coaching")
      || text.includes("staff")
      || text.includes("team storyline")
      || text.includes("player development")
      || text.includes("contract")
      || text.includes("extension")
      || text.includes("roster")
      || text.includes("cutdown")
      || text.includes("practice squad")
      || text.includes("waiver")
      || text.includes("depth chart")
      || text.includes("position battle");
  }

  function inboxPrimaryBucket(message) {
    if (Number(message.is_read || 0)) return "archived";
    if (isScoutingInboxMessage(message)) return "scouting";
    if (isMedicalInboxMessage(message)) return "medical";
    if (isTeamInboxMessage(message)) return "team";
    return "league";
  }

  function inboxPriorityTone(message) {
    const priority = String(message.priority || "").toLowerCase();
    if (["critical", "urgent", "high"].includes(priority)) return "high";
    if (["low", "info"].includes(priority)) return "low";
    return "normal";
  }

  function inboxPriorityLabel(message) {
    const tone = inboxPriorityTone(message);
    if (tone === "high") return "High Priority";
    if (tone === "low") return "Low Priority";
    return "Normal";
  }

  function messageNeedsAction(message) {
    if (Number(message.is_read || 0)) return false;
    const text = inboxMessageText(message);
    const actionText = text.includes("required")
      || text.includes("deadline")
      || text.includes("decision")
      || text.includes("choose")
      || text.includes("must")
      || text.includes("cutdown")
      || text.includes("option")
      || text.includes("offer")
      || text.includes("claim")
      || text.includes("roster limit")
      || text.includes("out for")
      || text.includes("placed on ir");
    if (actionText) return true;
    if (isScoutingInboxMessage(message)) return false;
    return inboxPriorityTone(message) === "high";
  }

  function inboxMessageBuckets(messages) {
    const buckets = { priority: [], team: [], scouting: [], medical: [], league: [], archived: [] };
    (messages || []).forEach((message) => {
      const key = inboxPrimaryBucket(message);
      buckets[key].push(message);
      if (messageNeedsAction(message)) buckets.priority.push(message);
    });
    return buckets;
  }

  function inboxTabs(buckets, activeTab) {
    const tabs = node("div", "inbox-tabs");
    inboxTabDefinitions().forEach(({ key, label }) => {
      const messages = buckets[key] || [];
      const unread = messages.filter((message) => !Number(message.is_read || 0)).length;
      const button = node("button", `inbox-tab ${state.inboxTab === key ? "active" : ""}`.trim());
      button.type = "button";
      button.addEventListener("click", () => {
        state.inboxTab = key;
        render();
      });
      append(button, [
        node("strong", null, label),
        node("span", null, key === "archived" ? `${messages.length} read` : `${messages.length}${unread ? ` | ${unread} new` : ""}`),
      ]);
      if (activeTab === key) button.classList.add("active");
      tabs.append(button);
    });
    return tabs;
  }

  function inboxActionNote(activeTab, unread, total) {
    if (activeTab === "archived") return total ? `${total} read message${total === 1 ? "" : "s"}` : "Archive empty";
    if (unread) return `${unread} unread in this view`;
    return "Caught up";
  }

  function inboxMessageCard(message, activeTab) {
    const bucket = activeTab === "priority" ? inboxPrimaryBucket(message) : activeTab;
    const tone = inboxPriorityTone(message);
    const classes = [
      "message-card",
      Number(message.is_read || 0) ? "read" : "unread",
      `message-${bucket}`,
      `priority-${tone}`,
    ].join(" ");
    const card = node("article", classes);
    const titleStack = node("div", "message-title-stack");
    append(titleStack, [
      node("span", "message-kicker", inboxMessageKicker(message, bucket)),
      append(node("strong", null), inboxLinkedText(message.title || "Inbox Message", message)),
    ]);
    append(card, [
      append(node("div", "message-top"), [
        titleStack,
        node("span", "event-date", shortDate(message.message_date)),
      ]),
      append(node("p", null), inboxLinkedText(message.body || "", message)),
      inboxMessageChips(message, bucket),
    ]);
    return card;
  }

  function inboxMessageKicker(message, bucket) {
    const source = message.source || "Front Office";
    const category = message.category || inboxBucketLabel(bucket);
    return `${category} | ${source}`;
  }

  function inboxBucketLabel(bucket) {
    const def = inboxTabDefinitions().find((tab) => tab.key === bucket);
    return def?.label || "Inbox";
  }

  function inboxMessageChips(message, bucket) {
    const row = node("div", "message-chip-row");
    const category = message.category || inboxBucketLabel(bucket);
    const source = message.source || "Front Office";
    append(row, [
      node("span", `message-chip message-chip-${bucket}`, category),
      node("span", "message-chip", source),
      node("span", `message-chip message-priority priority-${inboxPriorityTone(message)}`, inboxPriorityLabel(message)),
    ]);
    const related = inboxRelatedLink(message);
    if (related) row.append(related);
    const team = message.relatedPlayer?.team || message.relatedProspect?.team;
    if (team) {
      const teamChip = statTeamLink(team);
      if (teamChip !== "-") {
        teamChip.classList.add("message-chip", "message-team-chip");
        row.append(teamChip);
      }
    }
    return row;
  }

  function inboxRelatedLink(message) {
    const player = message.relatedPlayer;
    if (player?.player_id) {
      return playerLink(player.player_id, player.player_name || "Player", "message-related-link message-chip", {
        team: player.team,
        position: player.position,
      });
    }
    const prospect = message.relatedProspect;
    if (prospect?.prospect_id) {
      return prospectLink(prospect.prospect_id, prospect.player_name || "Prospect", "message-related-link message-chip");
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

    const summary = panel("League Wire", news.updatedAt ? `Through ${shortDate(news.updatedAt)}` : "Public Feed");
    const body = panelBody(summary);
    if (state.leagueNewsLoading) {
      body.append(node("div", "empty-state", "Refreshing league news..."));
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
    const refresh = node("button", "copy-button", state.runnerBusy ? "Running" : "Refresh Stories");
    refresh.type = "button";
    refresh.disabled = state.runnerBusy || !runnerMode();
    refresh.addEventListener("click", () => runAction("league_news_seed", {}));
    const rollWeek = node("button", "copy-button", state.runnerBusy ? "Running" : "Refresh Weekly Stories");
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

    const summary = panel("Transaction Ledger", `${transactions.includeBaseline ? "including baseline imports" : "baseline imports hidden"}`);
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
    const whyPayload = item.why ? whyPayloadFromDecision(item.why, {
      title: item.player ? `${item.type || "Move"} | ${item.player}` : item.type || "Transaction",
      team: item.team || item.toTeam || item.fromTeam,
      player: item.player,
      playerId: item.playerId,
      position: item.position,
      summary: item.description || transactionFallbackDescription(item),
    }) : null;
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
          whyButton(whyPayload),
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

  function historyValue(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return value || "-";
    return Number.isInteger(number) ? number.toLocaleString() : number.toFixed(1);
  }

  function balanceMetricCard(item) {
    return metric(item.label || "-", item.text ?? historyValue(item.value), item.detail || "", item.tone === "bad" ? "bad" : item.tone === "warn" ? "warn" : "");
  }

  function balanceTeamLink(row) {
    return row?.abbreviation ? statTeamLink(row.abbreviation) : "-";
  }

  function balancePlayerLink(row) {
    if (row?.player_id && row?.player_name) {
      return playerLink(row.player_id, row.player_name, "player-link strong-link", {
        team: row.abbreviation,
        position: row.position,
      });
    }
    return row?.player_name || "-";
  }

  function balanceCategoryRows(category) {
    const rows = category.rows || [];
    const secondary = category.secondaryRows || [];
    if (category.key === "capHealth") {
      return [
        node("h3", "balance-subtitle", "Tightest Cap Rooms"),
        table(["Team", "Cap Room", "Cap %", "Committed", "Dead Cap"], rows.map((row) => [
          balanceTeamLink(row),
          money(row.cap_space || 0),
          `${historyValue(row.cap_space_pct)}%`,
          money(row.total_committed || 0),
          money(row.dead_cap_charges || 0),
        ])),
        secondary.length ? node("h3", "balance-subtitle", "Dead Cap Watch") : null,
        secondary.length ? table(["Team", "Dead Cap", "Dead %", "Cap Room"], secondary.slice(0, 6).map((row) => [
          balanceTeamLink(row),
          money(row.dead_cap_charges || 0),
          `${historyValue(row.dead_cap_pct)}%`,
          money(row.cap_space || 0),
        ])) : null,
      ];
    }
    if (category.key === "qbSupply") {
      return [
        node("h3", "balance-subtitle", "Rooms To Watch"),
        table(["Team", "Starter", "OVR", "POT", "Age", "Read"], rows.map((row) => [
          balanceTeamLink(row),
          balancePlayerLink(row),
          historyValue(row.overall),
          historyValue(row.potential),
          historyValue(row.age),
          row.supply_label || "-",
        ])),
        secondary.length ? node("h3", "balance-subtitle", "Top End") : null,
        secondary.length ? table(["Team", "Starter", "OVR", "POT", "Age"], secondary.slice(0, 6).map((row) => [
          balanceTeamLink(row),
          balancePlayerLink(row),
          historyValue(row.overall),
          historyValue(row.potential),
          historyValue(row.age),
        ])) : null,
      ];
    }
    if (category.key === "starterAge") {
      return [
        node("h3", "balance-subtitle", "Oldest Starting Cores"),
        table(["Team", "Avg Age", "30+", "Under 25", "Starters"], rows.map((row) => [
          balanceTeamLink(row),
          historyValue(row.avg_age),
          `${historyValue(row.over30_pct)}%`,
          `${historyValue(row.under25_pct)}%`,
          historyValue(row.starter_count),
        ])),
        secondary.length ? node("h3", "balance-subtitle", "Youngest Starting Cores") : null,
        secondary.length ? table(["Team", "Avg Age", "30+", "Under 25", "Starters"], secondary.slice(0, 6).map((row) => [
          balanceTeamLink(row),
          historyValue(row.avg_age),
          `${historyValue(row.over30_pct)}%`,
          `${historyValue(row.under25_pct)}%`,
          historyValue(row.starter_count),
        ])) : null,
      ];
    }
    if (category.key === "retirements") {
      return rows.length ? [
        node("h3", "balance-subtitle", "Notable Retirements"),
        table(["Player", "Team", "Pos", "Age", "Quality", "Reason"], rows.map((row) => [
          balancePlayerLink(row),
          balanceTeamLink(row),
          row.position || "-",
          historyValue(row.age),
          historyValue(row.quality_score || row.overall),
          row.reason_code || "-",
        ])),
      ] : [node("div", "empty-state", "No retirements recorded for this league year yet.")];
    }
    if (category.key === "rookieHitRate") {
      return [
        table(["Draft", "Selected", "Hit Rate", "Premium", "Day 2", "Day 3", "R1 Miss"], rows.map((row) => [
          row.draftYear || "-",
          historyValue(row.selected),
          `${historyValue(row.hitRate)}%`,
          historyValue(row.premium),
          historyValue(row.day2Hits),
          historyValue(row.day3Gems),
          historyValue(row.round1Misses),
        ])),
        secondary.length ? node("h3", "balance-subtitle", "Early Hits And Gems") : null,
        secondary.length ? table(["Player", "Team", "Pick", "Pos", "OVR", "POT", "Read"], secondary.slice(0, 8).map((row) => [
          balancePlayerLink(row),
          balanceTeamLink(row),
          `${row.round || "-"}${row.pick_in_round ? `.${row.pick_in_round}` : ""}`,
          row.position || "-",
          historyValue(row.overall),
          historyValue(row.potential),
          row.hit_class || "-",
        ])) : null,
      ];
    }
    return rows.length ? [table(["Item", "Value"], rows.map((row) => [row.label || row.key || "-", historyValue(row.value || row.count)]))] : [];
  }

  function balanceDashboardPanel(category) {
    const card = panel(category.title || "Balance", category.subtitle || "League snapshot");
    card.classList.add("balance-panel");
    const body = panelBody(card);
    body.append(append(node("section", "metric-grid compact-metrics balance-metrics"), (category.metrics || []).map(balanceMetricCard)));
    append(body, balanceCategoryRows(category));
    return card;
  }

  function renderBalanceDashboard(root) {
    const balance = data.leagueBalance || {};
    const counts = balance.counts || {};
    const summary = panel("Long-Term Balance", balance.leagueYear ? `${balance.leagueYear} live dashboard` : "Live dashboard");
    const body = panelBody(summary);
    append(body, [
      append(node("section", "metric-grid compact-metrics"), [
        metric("Dashboards", String(counts.categories || 0), "Cap, QB, age, retirements, rookies"),
        metric("Flags", String(counts.flags || 0), "Balance watch notes", counts.critical ? "bad" : counts.warning ? "warn" : "good"),
        metric("Critical", String(counts.critical || 0), "Immediate review"),
        metric("Warnings", String(counts.warning || 0), "Watch list"),
      ]),
    ]);
    if (balance.error) {
      body.append(node("div", "empty-state", balance.error));
    } else if (balance.flags && balance.flags.length) {
      body.append(table(["Severity", "Area", "Note"], balance.flags.slice(0, 8).map((row) => [
        row.severity || "-",
        String(row.category || "").replace(/([A-Z])/g, " $1").trim() || "-",
        row.message || "-",
      ])));
    } else {
      body.append(node("div", "empty-state", "Long-term balance checks are inside current guardrails."));
    }
    root.append(summary);

    const grid = node("section", "balance-dashboard-grid");
    (balance.categories || []).forEach((category) => grid.append(balanceDashboardPanel(category)));
    if (grid.children.length) root.append(grid);
  }

  function historyTone(tone) {
    const clean = String(tone || "note").toLowerCase();
    if (["gold", "silver", "bronze", "bad", "warn", "good"].includes(clean)) return clean;
    return "note";
  }

  function historyTeamMeta(abbr) {
    const key = String(abbr || "").trim().toUpperCase();
    return rosterTeamOptions().find((team) => team.abbr === key) || { abbr: key, name: key, logo: "" };
  }

  function historyPlayerNode(item, className = "player-link strong-link") {
    if (item?.playerId || item?.player_id) {
      return playerLink(item.playerId || item.player_id, item.playerName || item.player_name || "Player", className, {
        team: item.team,
        position: item.position,
      });
    }
    return node("span", className, item?.playerName || item?.player_name || "-");
  }

  function historyPickLabel(item) {
    const round = item?.round || "-";
    const inRound = item?.pickInRound || item?.pick_in_round;
    const pick = item?.pickNumber || item?.pick_number;
    if (round && inRound) return `${round}.${inRound}`;
    return pick ? `#${pick}` : "-";
  }

  function historyTimelineItem(item) {
    const row = node("article", `history-timeline-row tone-${historyTone(item.tone)}`);
    const marker = node("div", "history-timeline-marker", item.season || item.date || "-");
    const meta = node("div", "history-timeline-meta");
    append(meta, [
      tag(String(item.kind || "story").replace(/_/g, " "), historyTone(item.tone)),
      item.team ? statTeamLink(item.team) : null,
      item.playerId ? historyPlayerNode(item, "player-link") : null,
    ]);
    append(row, [
      marker,
      append(node("div", "history-timeline-copy"), [
        node("strong", null, item.title || "League moment"),
        item.summary ? node("p", null, item.summary) : null,
        meta,
      ]),
    ]);
    return row;
  }

  function renderHistoryFrontPage(root, history, counts) {
    const spotlight = panel("History Front Page", "Big-picture storylines and watch items");
    const body = panelBody(spotlight);
    const latestChampion = (history.champions || [])[0];
    const topStory = (history.timeline || [])[0];
    const topChase = (history.recordChases || [])[0];
    const topMilestone = (history.milestoneAlerts || [])[0];
    const grid = node("section", "history-front-grid");

    const championCard = node("article", "history-feature-card tone-gold");
    append(championCard, [
      node("span", "history-card-label", "Latest Champion"),
      latestChampion
        ? append(node("div", "history-feature-title"), [
            statTeamLink(latestChampion.team),
            node("strong", null, latestChampion.teamName || latestChampion.team || "-"),
          ])
        : node("strong", null, "No champion archived yet"),
      node("p", null, latestChampion ? `${latestChampion.season} | ${latestChampion.record || "-"} | ${historyValue(latestChampion.pointsFor)} PF` : "Complete a season to begin building league memory."),
    ]);

    const storyCard = node("article", `history-feature-card tone-${historyTone(topStory?.tone)}`);
    append(storyCard, [
      node("span", "history-card-label", "Top Story"),
      node("strong", null, topStory?.title || "No story beats yet"),
      node("p", null, topStory?.summary || "Career arcs, breakouts, retirements, and draft class notes will appear here."),
    ]);

    const chaseCard = node("article", `history-feature-card tone-${historyTone(topChase?.tone)}`);
    append(chaseCard, [
      node("span", "history-card-label", "Record Chase"),
      topChase ? historyPlayerNode(topChase) : node("strong", null, "No active chase"),
      node("p", null, topChase ? `${topChase.statName}: projected ${historyValue(topChase.projected)} vs ${historyValue(topChase.recordValue)} record.` : "Once records exist, in-season paces will be tracked here."),
    ]);

    const milestoneCard = node("article", `history-feature-card tone-${historyTone(topMilestone?.tone)}`);
    append(milestoneCard, [
      node("span", "history-card-label", "Milestone Watch"),
      topMilestone ? historyPlayerNode(topMilestone) : node("strong", null, "No one on the doorstep"),
      node("p", null, topMilestone ? `${historyValue(topMilestone.remaining)} away from ${historyValue(topMilestone.threshold)} ${topMilestone.statName}.` : "Career milestone alerts will surface when players close in."),
    ]);

    append(grid, [championCard, storyCard, chaseCard, milestoneCard]);
    append(body, [
      append(node("section", "metric-grid compact-metrics"), [
        metric("Seasons", String(counts.runs || 0), "Archived years"),
        metric("Timeline", String(counts.timeline || 0), "Story beats"),
        metric("Record Chases", String(counts.recordChases || 0), "Active pace watch"),
        metric("Milestone Alerts", String(counts.milestoneAlerts || 0), "Career watch"),
      ]),
      grid,
    ]);
    root.append(spotlight);
  }

  function renderHistoryTimeline(root, history) {
    const timeline = panel("League Timeline", "Championships, career stories, milestones, and draft memories");
    const body = panelBody(timeline);
    const items = history.timeline || [];
    if (!items.length) {
      body.append(node("div", "empty-state", "No timeline moments archived yet."));
    } else {
      const list = node("div", "history-timeline-list");
      items.slice(0, 14).forEach((item) => list.append(historyTimelineItem(item)));
      body.append(list);
    }
    root.append(timeline);
  }

  function renderHistoryTeamPage(root, history) {
    const teams = history.teamPages || [];
    const card = panel("Team History", "Franchise memory and record book");
    const body = panelBody(card);
    if (!teams.length) {
      body.append(node("div", "empty-state", "No team history archived yet."));
      root.append(card);
      return;
    }
    if (!state.historyTeam || !teams.some((team) => team.team === state.historyTeam)) {
      state.historyTeam = data.activeSave?.user_team && teams.some((team) => team.team === data.activeSave.user_team)
        ? data.activeSave.user_team
        : teams[0].team;
    }
    const selected = teams.find((team) => team.team === state.historyTeam) || teams[0];
    const meta = historyTeamMeta(selected.team);
    const select = node("select", "compact-select history-team-select");
    teams
      .slice()
      .sort((a, b) => String(a.teamName || a.team).localeCompare(String(b.teamName || b.team)))
      .forEach((team) => {
        const option = node("option", null, `${team.teamName || team.team} (${team.team})`);
        option.value = team.team;
        option.selected = team.team === selected.team;
        select.append(option);
      });
    select.addEventListener("change", () => {
      state.historyTeam = select.value;
      render();
    });
    const hero = node("section", `history-team-hero tone-${historyTone(selected.tone)}`);
    append(hero, [
      teamLogo(meta.logo, selected.team, "team-large-logo"),
      append(node("div"), [
        append(node("div", "history-team-title"), [
          node("strong", null, selected.teamName || selected.team),
          tag(selected.identity || "franchise profile", historyTone(selected.tone)),
        ]),
        node("p", null, `${selected.seasons || 0} archived seasons | ${selected.titles || 0} titles | ${selected.playoffApps || 0} playoff trips | ${selected.divisionTitles || 0} division crowns`),
      ]),
      select,
    ]);
    const latest = node("article", "history-mini-card");
    append(latest, [
      node("span", "history-card-label", "Latest Season"),
      node("strong", null, `${selected.latestSeason || "-"} | ${selected.latestRecord || "-"}`),
      node("p", null, `${selected.latestResult || "No result"} | point diff ${historyValue(selected.latestPointDiff)}`),
    ]);
    const best = node("article", "history-mini-card");
    append(best, [
      node("span", "history-card-label", "Best Season"),
      node("strong", null, `${selected.bestSeason || "-"} | ${selected.bestRecord || "-"}`),
      node("p", null, `${selected.bestResult || "No result"} | point diff ${historyValue(selected.bestPointDiff)}`),
    ]);
    append(body, [
      hero,
      append(node("section", "history-mini-grid"), [latest, best]),
      (selected.records || []).length
        ? table(["Record", "Holder", "Season", "Value"], selected.records.map((row) => [
            row.statName || "-",
            row.playerId ? playerLink(row.playerId, row.playerName || "Player", "player-link strong-link") : (row.playerName || selected.teamName || "-"),
            row.season || "-",
            historyValue(row.value),
          ]))
        : node("div", "empty-state", "No franchise records archived for this team yet."),
    ]);
    root.append(card);
  }

  function renderRecordAndMilestoneWatch(root, history) {
    const watch = panel("Record Watch", "Current season paces and career milestones");
    const body = panelBody(watch);
    const chases = history.recordChases || [];
    const alerts = history.milestoneAlerts || [];
    const grid = node("section", "history-watch-grid");
    const chaseCard = node("article", "history-watch-card");
    append(chaseCard, [
      node("h3", null, "Record Chases"),
      chases.length ? table(["Player", "Team", "Record", "Current", "Pace", "To Beat"], chases.slice(0, 8).map((row) => [
        historyPlayerNode(row),
        row.team ? statTeamLink(row.team) : "-",
        row.statName || "-",
        historyValue(row.current),
        historyValue(row.projected),
        `${historyValue(row.recordValue)} by ${row.recordHolder || "-"}`,
      ])) : node("div", "empty-state", "No active record chases yet."),
    ]);
    const milestoneCard = node("article", "history-watch-card");
    append(milestoneCard, [
      node("h3", null, "Milestone Alerts"),
      alerts.length ? table(["Player", "Team", "Milestone", "Current", "Away"], alerts.slice(0, 8).map((row) => [
        historyPlayerNode(row),
        row.team ? statTeamLink(row.team) : "-",
        `${historyValue(row.threshold)} ${row.statName || ""}`,
        historyValue(row.current),
        historyValue(row.remaining),
      ])) : node("div", "empty-state", "No career milestones are close enough to flag."),
    ]);
    append(grid, [chaseCard, milestoneCard]);
    body.append(grid);
    root.append(watch);
  }

  function renderDraftRetrospectives(root, history) {
    const retros = history.draftRetrospectives || [];
    const card = panel("Draft Class Retrospectives", "How classes are aging into the league");
    const body = panelBody(card);
    if (!retros.length) {
      body.append(node("div", "empty-state", "No draft retrospectives archived yet."));
      root.append(card);
      return;
    }
    const grid = node("section", "draft-retro-grid");
    retros.slice(0, 4).forEach((draftClass) => {
      const item = node("article", "draft-retro-card");
      const topPick = draftClass.topPickPlayerId
        ? playerLink(draftClass.topPickPlayerId, draftClass.topPickName || "Top Pick", "player-link strong-link")
        : node("strong", null, draftClass.topPickName || "No top pick");
      append(item, [
        append(node("div", "draft-retro-header"), [
          node("strong", null, `${draftClass.draftYear || "-"} Draft`),
          tag(`${draftClass.selectedCount || 0} selected`, "silver"),
        ]),
        append(node("div", "draft-retro-top"), [
          node("span", "history-card-label", "1.1"),
          topPick,
          node("small", null, [draftClass.topPickPosition, draftClass.topPickTeam].filter(Boolean).join(" | ")),
        ]),
        append(node("section", "metric-grid compact-metrics"), [
          metric("Premium", String(draftClass.premiumCount || 0), "High-end looks"),
          metric("Day 2", String(draftClass.day2StarterLooks || 0), "Starter/upside looks"),
          metric("QBs", `${draftClass.firstRoundQbs || 0}/${draftClass.qbsSelected || 0}`, "R1 / total"),
        ]),
        (draftClass.day3Gems || []).length
          ? table(["Gem", "Pick", "Team", "OVR/POT"], draftClass.day3Gems.slice(0, 3).map((pick) => [
              historyPlayerNode(pick),
              historyPickLabel(pick),
              pick.team ? statTeamLink(pick.team) : "-",
              `${historyValue(pick.trueGrade)} / ${historyValue(pick.potential)}`,
            ]))
          : node("div", "empty-state", "No day-three gems flagged yet."),
        (draftClass.firstRoundConcerns || []).length
          ? append(node("div", "draft-retro-note"), [
              node("strong", null, "First-round concern"),
              node("span", null, draftClass.firstRoundConcerns.map((pick) => `${pick.playerName} (${historyPickLabel(pick)})`).join(", ")),
            ])
          : null,
      ]);
      grid.append(item);
    });
    body.append(grid);
    root.append(card);
  }

  function renderLeagueHistory() {
    setHeader("League History", "Champions, franchise arcs, draft memories, record chases, and career milestones.");
    const history = data.history || {};
    const counts = history.counts || {};
    const root = document.createDocumentFragment();

    renderHistoryFrontPage(root, history, counts);

    const summary = panel("Historical Archive", `${counts.runs || 0} archived season${Number(counts.runs || 0) === 1 ? "" : "s"}`);
    panelBody(summary).append(append(node("section", "metric-grid compact-metrics"), [
      metric("Champions", String(counts.champions || 0), "Super Bowl winners"),
      metric("Team Pages", String(counts.teamPages || 0), "Franchise histories"),
      metric("Timeline", String(counts.timeline || 0), "Story moments"),
      metric("Draft Classes", String(counts.draftClasses || 0), "Archived classes"),
      metric("Record Watch", String(counts.recordChases || 0), "Current chases"),
      metric("Milestones", String(counts.milestoneAlerts || counts.milestones || 0), "Alerts and archive"),
    ]));
    root.append(summary);

    renderHistoryTimeline(root, history);
    renderHistoryTeamPage(root, history);
    renderRecordAndMilestoneWatch(root, history);
    renderDraftRetrospectives(root, history);

    const talentSupply = data.talentSupply || {};
    const talentCounts = talentSupply.counts || {};
    const talentPanel = panel("League Talent Balance", talentSupply.leagueYear ? `${talentSupply.leagueYear} review` : "Awaiting review");
    const talentBody = panelBody(talentPanel);
    append(talentBody, [
      append(node("section", "metric-grid compact-metrics"), [
        metric("Positions", String(talentCounts.positions || 0), "Tracked buckets"),
        metric("Watch Notes", String(talentCounts.flags || 0), "League balance notes", talentCounts.critical ? "bad" : talentCounts.warning ? "warn" : "good"),
        metric("Priority", String(talentCounts.critical || 0), "Needs review"),
        metric("Watch", String(talentCounts.warning || 0), "Monitor"),
        metric("League Year", String(talentSupply.leagueYear || "-"), "Current review"),
      ]),
    ]);
    if (talentSupply.error) {
      talentBody.append(node("div", "empty-state", talentSupply.error));
    } else if (talentSupply.flags && talentSupply.flags.length) {
      talentBody.append(table(["Severity", "Position", "Metric", "Direction", "Note"], talentSupply.flags.slice(0, 10).map((row) => [
        row.severity || "-",
        row.position || "-",
        row.metric_key || "-",
        row.direction || "-",
        row.message || "-",
      ])));
    } else if (talentCounts.positions) {
      talentBody.append(node("div", "empty-state", "League talent balance is within the expected range."));
    } else {
      talentBody.append(node("div", "empty-state", "League talent balance will populate after the yearly rollover review."));
    }
    if (talentSupply.positions && talentSupply.positions.length) {
      talentBody.append(table(["Pos", "Total", "90+", "85+", "80+", "Starter", "Replacement", "Avg"], talentSupply.positions.map((row) => [
        row.position || "-",
        historyValue(row.total_count),
        historyValue(row.count_90_plus),
        historyValue(row.count_85_plus),
        historyValue(row.count_80_plus),
        historyValue(row.count_starter_level),
        historyValue(row.count_replacement_level),
        historyValue(row.avg_overall),
      ])));
    }
    root.append(talentPanel);
    renderBalanceDashboard(root);

    const champions = panel("Champions Index", "Recent Super Bowl winners");
    panelBody(champions).append(table(["Season", "Team", "Record", "Seed", "PF", "PA"], (history.champions || []).map((row) => [
      row.season || "-",
      statTeamLink(row.team),
      row.record || "-",
      row.playoffSeed ? `#${row.playoffSeed}` : "-",
      historyValue(row.pointsFor),
      historyValue(row.pointsAgainst),
    ])));
    root.append(champions);

    const stories = panel("Story Archive", "Recent major moments");
    if (!history.stories || !history.stories.length) {
      panelBody(stories).append(node("div", "empty-state", "No career stories archived yet."));
    } else {
      const list = node("div", "history-story-list");
      history.stories.slice(0, 12).forEach((item) => {
        const row = node("article", `history-story-row story-${item.tier || "note"}`.trim());
        const player = item.playerId
          ? playerLink(item.playerId, item.playerName || "Player", "player-link strong-link", { team: item.team, position: item.position })
          : node("strong", null, item.title || "League Story");
        append(row, [
          append(node("div"), [
            append(node("div", "history-story-title"), [node("strong", null, item.title || "Story"), item.team ? statTeamLink(item.team) : null]),
            node("p", null, item.summary || ""),
            append(node("div", "transaction-meta"), [player, node("span", null, item.date || item.season || "-")]),
          ]),
          node("span", "season-badge", item.season || "-"),
        ]);
        list.append(row);
      });
      panelBody(stories).append(list);
    }
    root.append(stories);

    const draftClasses = panel("Draft Class Index", "Recent completed classes");
    panelBody(draftClasses).append(table(["Draft", "Top Pick", "Team", "QB", "Selected", "Top 50"], (history.draftClasses || []).map((row) => [
      row.draftYear || "-",
      row.topPickPlayerId
        ? playerLink(row.topPickPlayerId, `${row.topPickName || "Top Pick"}${row.topPickPosition ? `, ${row.topPickPosition}` : ""}`, "player-link strong-link")
        : (row.topPickName || "-"),
      row.topPickTeam ? statTeamLink(row.topPickTeam) : "-",
      `${row.firstRoundQbs || 0} R1 / ${row.qbsSelected || 0} total`,
      historyValue(row.selectedCount),
      `${row.top50PowerCount || 0} power / ${row.top50NonPowerCount || 0} other`,
    ])));
    root.append(draftClasses);

    const milestones = panel("Milestone Log", "Recent archived marks");
    panelBody(milestones).append(table(["Season", "Player", "Milestone", "Team", "Value"], (history.milestones || []).slice(0, 16).map((row) => [
      row.season || "-",
      playerLink(row.playerId, `${row.playerName || "Player"}${row.position ? `, ${row.position}` : ""}`, "player-link strong-link", { team: row.team, position: row.position }),
      row.name || "-",
      row.team ? statTeamLink(row.team) : "-",
      historyValue(row.value),
    ])));
    root.append(milestones);

    const records = panel("Record Book Index", "Current franchise single-season leaders");
    panelBody(records).append(table(["Team", "Category", "Record", "Season", "Holder", "Value"], (history.teamRecords || []).slice(0, 48).map((row) => [
      row.team ? statTeamLink(row.team) : "-",
      row.group || "-",
      row.statName || "-",
      row.season || "-",
      row.playerId
        ? playerLink(row.playerId, row.playerName || "Player", "player-link strong-link")
        : (row.teamName || row.team || "-"),
      historyValue(row.value),
    ])));
    root.append(records);

    finishRender(root);
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
    const summary = panel("Medical Center", "Current season");
    const body = panelBody(summary);
    if (state.injuriesLoading) body.append(node("div", "empty-state", "Refreshing injury report..."));
    append(body, [
      append(node("section", "metric-grid injury-metrics"), [
        metric("Active", String(injuries.counts?.active || 0), "League injuries"),
        metric("Your Team", String(injuries.counts?.userActive || 0), "Active injuries", injuries.counts?.userActive ? "warn" : "good"),
        metric("Major", String(injuries.counts?.majorActive || 0), "Longer absences", injuries.counts?.majorActive ? "warn" : ""),
        metric("Recent", String(injuries.counts?.recent || 0), "Latest injury events"),
      ]),
      injuryStaffControl(),
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

  function injuryStaffControl() {
    const enabled = userInjuryAutoManageEnabled();
    const effectiveEnabled = state.injuryAutoManageSaving ? Boolean(state.injuryAutoManageChecked) : enabled;
    const wrap = node("section", `injury-staff-control ${effectiveEnabled ? "enabled" : ""}`.trim());
    const copy = node("div", "injury-staff-copy");
    append(copy, [
      node("strong", null, "Staff Injury Response"),
      node("span", null, effectiveEnabled
        ? "Your staff is auto-adjusting the depth chart after user-team injuries."
        : "Pause for user-team injury popups, or let your staff handle depth-chart changes."),
    ]);
    const label = node("label", "injury-staff-toggle");
    const input = node("input");
    input.type = "checkbox";
    input.checked = effectiveEnabled;
    input.disabled = state.runnerBusy || state.injuryAutoManageSaving || !runnerMode();
    input.addEventListener("change", async () => {
      const previous = enabled;
      const next = input.checked;
      state.injuryAutoManageChecked = next;
      if (!(await setUserInjuryAutoManage(next))) {
        state.injuryAutoManageChecked = previous;
      }
    });
    append(label, [
      input,
      node("span", "injury-staff-switch"),
      node("small", null, state.injuryAutoManageSaving ? "Saving..." : (effectiveEnabled ? "Staff Handles" : "Ask Me")),
    ]);
    append(wrap, [copy, label]);
    return wrap;
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
    const p = panel("Scouting HQ", scouting.draftYear ? `${scouting.draftYear} Draft` : "Draft Class");
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
    body.append(renderScoutingTierBudgets(scouting));

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

  function renderScoutingTierBudgets(scouting) {
    const budgets = scouting.tierBudgets || [];
    if (!budgets.length) return node("div");
    const wrap = node("section", "scouting-tier-budget-strip");
    budgets.forEach((tier) => {
      const mediumUsed = Number(tier.mediumUsed || 0);
      const mediumLimit = Number(tier.mediumLimit || 0);
      const highUsed = Number(tier.highUsed || 0);
      const highLimit = Number(tier.highLimit || 0);
      const veryHighUsed = Number(tier.veryHighUsed || 0);
      const veryHighLimit = Number(tier.veryHighLimit || 0);
      const saturated = (mediumLimit && mediumUsed >= mediumLimit)
        || (highLimit && highUsed >= highLimit)
        || (veryHighLimit && veryHighUsed >= veryHighLimit);
      append(wrap, [
        append(node("article", `scouting-tier-chip ${saturated ? "full" : ""}`), [
          node("strong", null, `Board ${tier.label || "-"}`),
          node("span", null, `Med ${mediumUsed}/${mediumLimit}`),
          node("span", null, `High ${highUsed}/${highLimit}`),
          node("span", null, `VH ${veryHighUsed}/${veryHighLimit}`),
        ]),
      ]);
    });
    return wrap;
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
        scoutingMiniCardHeader(report, report.result_type === "trait" ? "Trait" : "Confidence"),
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
        scoutingMiniCardHeader(visit, top30OutcomeLabel(visit.result_type)),
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

  function scoutingMiniCardHeader(item, label) {
    const prospect = prospectFromBoards(item.prospect_id) || item;
    const header = node("div", "message-top scouting-card-top");
    append(header, [
      prospectThumbnail(prospect, "small"),
      append(node("span", "scouting-card-title"), [
        node("strong", null, item.player_name || prospectDisplayName(prospect)),
        node("small", null, `${item.position || prospect?.position || "-"} | ${item.college || prospect?.college || "-"}`),
      ]),
      node("span", "event-date", label || "Report"),
    ]);
    return header;
  }

  function renderScoutingAudit(audit = {}) {
    if (!audit.available) {
      const empty = node("section", "scouting-audit compact-empty", audit.reason || "Scouting coverage will appear after the draft class initializes.");
      return empty;
    }
    const counts = audit.counts || {};
    const wrap = node("section", "scouting-audit");
    const head = node("div", "scouting-audit-head");
    append(head, [
      append(node("div"), [
        node("strong", null, "Scouting Coverage"),
        node("small", null, "Coverage map for discovery spread, confidence, and scouting variance."),
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
    const wrap = node("span", "prospect-name-cell with-thumb scouting-row-prospect");
    const copy = node("span", "prospect-name-copy");
    const button = node("button", "prospect-link", prospectDisplayName(prospect));
    button.type = "button";
    button.title = "Open prospect card";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openDraftProspectPopover(prospect.prospect_id);
    });
    append(copy, [
      button,
      node("small", null, prospect.archetype || roleLabel(prospect.primary_role)),
    ]);
    append(wrap, [prospectThumbnail(prospect, "small"), copy]);
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
    setHeader("CPU Front Offices", "Review how CPU teams plan rosters, contracts, free agency, trades, and the draft.");
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

    const summary = panel("Front Office Control", ai.gameId || data.registry?.activeGameId || "Active Save");
    const metrics = node("section", "metric-grid compact-metrics");
    append(metrics, [
      metric("Profiles", String(ai.counts?.profiles || 0), "Team front offices"),
      metric("Enabled", config.enabled ? "Yes" : "No", config.provider ? "Decision engine connected" : "Standard logic", config.enabled ? "good" : "warn"),
      metric("Autonomy", autonomy.mode || "advisory_only", autonomy.auto_apply_low_risk ? "low-risk auto allowed" : "review first", autonomy.auto_apply_low_risk ? "warn" : ""),
      metric("Review", String(ai.counts?.reviewInbox || 0), "Open decisions", ai.counts?.reviewInbox ? "warn" : ""),
      metric("Applied", String(reviewStatusCounts.applied || 0), "Committed review items", reviewStatusCounts.applied ? "good" : ""),
      metric("Blocked", String(reviewStatusCounts.blocked || 0), "Needs follow-up", reviewStatusCounts.blocked ? "bad" : ""),
      metric("Decision Engine", config.enabled ? (config.model || "Connected") : "Standard", config.enabled ? "Enhanced planning" : "Built-in planning"),
      metric("Recent Decisions", String(ai.counts?.logs || 0), "Front office activity"),
      metric("League Office", state.aiGmLoading ? "Refreshing" : "Ready", "Latest data"),
    ]);
    panelBody(summary).append(metrics);
    panelBody(summary).append(node("div", "quiet cap-context", "CPU front offices can prepare plans, queue decisions, and apply approved low-risk moves depending on league settings."));
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
      { title: "Prepare Front Offices", detail: "Create or update team front-office profiles for this save.", command: commands.aiGmSetup, action: "ai_gm_setup", tone: "good" },
      { title: "Connect Local Engine", detail: "Enable enhanced local planning support.", command: commands.aiGmEnableOllama, action: "ai_gm_enable_ollama" },
      { title: "Review-Only Mode", detail: "Keep CPU front-office decisions in review-only mode.", command: commands.aiGmAutonomyAdvisory, action: "ai_gm_autonomy_config", params: { mode: "advisory_only" } },
      { title: "Allow Low-Risk Moves", detail: "Permit validated low-risk actions to apply automatically.", command: commands.aiGmAutonomyLowRisk, action: "ai_gm_autonomy_config", params: { mode: "auto_apply_low_risk" }, tone: "warn" },
    ]);

    const dailyOpsPanel = workflowPanel("Daily Ops", `${ai.counts?.ops || 0} Recommended`, [
      { title: "Review Team Needs", detail: "Find recommended front-office work for the selected team.", command: commands.aiGmOps, action: "ai_gm_ops", params: { team, limit: 20 }, tone: "good" },
      { title: "Run Team Check", detail: "Refresh today's plan for this team.", command: commands.aiGmDailyRunPersist, action: "ai_gm_daily_run", params: { team, phase: "auto", persist: true } },
      { title: "Run League Check", detail: "Review CPU teams and queue sensible work.", command: commands.aiGmDailyRunAllPersist, action: "ai_gm_daily_run", params: { all: true, phase: "auto", persist: true, limit: 20 } },
      { title: "Apply Low-Risk Moves", detail: "Apply only validated low-risk league moves.", command: commands.aiGmDailyRunApply, action: "ai_gm_daily_run", params: { all: true, phase: "auto", mode: "auto_apply_low_risk", apply: true, limit: 20 }, tone: "warn" },
      { title: "Decision Queue", detail: "Review pending work for this team.", command: commands.aiGmQueue, action: "ai_gm_queue", params: { team, limit: 12 } },
      { title: "Process Decisions", detail: "Process the next few queued team operations.", command: commands.aiGmProcessQueue, action: "ai_gm_process_queue", params: { team, limit: 3 } },
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
      { title: "Preview Extensions", detail: "Preview extension actions before committing them.", command: commands.aiGmDryRunContractApply, action: null },
      { title: "Apply Extensions", detail: "Commit reviewed contract extensions.", command: commands.aiGmApplyContractPlan, action: null, tone: "warn" },
      { title: "CPU Extension Pass", detail: "Apply validated pre-free-agency extensions.", command: commands.aiGmOffseasonPreFaApply, action: null, tone: "warn" },
    ]);

    const freeAgencyPanel = workflowPanel("Free Agency", `${freeAgentPlan.counts?.primary_targets || 0} Primary`, [
      { title: "Build FA Plan", detail: "Rank primary, value, bridge, and monitor targets.", command: commands.aiGmFreeAgentPlan, action: "ai_gm_free_agent_plan", params: { team }, tone: "good" },
      { title: "Save FA Plan", detail: "Persist offer recommendations for review.", command: commands.aiGmFreeAgentPlanPersist, action: "ai_gm_free_agent_plan_persist", params: { team } },
      { title: "Preview Offers", detail: "Preview offer submissions before committing them.", command: commands.aiGmDryRunFreeAgentApply, action: null },
      { title: "Submit Offers", detail: "Submit reviewed free-agent offers.", command: commands.aiGmApplyFreeAgentPlan, action: null, tone: "warn" },
      { title: "Opening FA Wave", detail: "Apply validated opening-wave free-agent work.", command: commands.aiGmOffseasonFaWave1Apply, action: null, tone: "warn" },
    ]);

    const draftWorkflowPanel = workflowPanel("Draft", `${draftPlan.counts?.picks || 0} Picks`, [
      { title: "Build Draft Plan", detail: "Build board fits and position priorities for the selected team.", command: commands.aiGmDraftPlan, action: "ai_gm_draft_plan", params: { team }, tone: "good" },
      { title: "Save Draft Plan", detail: "Persist this team's draft strategy.", command: commands.aiGmDraftPlanPersist, action: "ai_gm_draft_plan_persist", params: { team } },
      { title: "Prepare CPU Draft Plans", detail: "Create draft plans for CPU teams before the draft.", command: commands.aiGmDraftPlanAll, action: null },
      { title: "Draft Strategy", detail: "Rank draft priorities from needs, contracts, and pick value.", command: commands.aiGmRunDraft, action: "ai_gm_run", params: { team, decision_type: "draft_strategy_update" } },
      { title: "Build Decision Brief", detail: "Prepare the team context used for a draft strategy decision.", command: commands.aiGmContext, action: "ai_gm_context", params: { team, decision_type: "draft_strategy_update" } },
    ]);

    const reviewWorkflowPanel = workflowPanel("Review Queue", `${ai.counts?.reviewInbox || 0} Open`, [
      { title: "Review Team Inbox", detail: "Load pending, blocked, and approved front-office items for this team.", command: commands.aiGmReviewInbox, action: "ai_gm_review_inbox", params: { team, limit: 20 }, tone: ai.counts?.reviewInbox ? "warn" : "" },
      { title: "Review League Inbox", detail: "Load pending review items across the league.", command: commands.aiGmReviewInboxAll, action: "ai_gm_review_inbox", params: { status: "pending_review", limit: 40 } },
      { title: "Review History", detail: "Show recent front-office review outcomes for this team.", command: commands.aiGmReviewHistory, action: "ai_gm_review_history", params: { team, status: "all", limit: 20 } },
      { title: "Apply Approved Team", detail: "Apply already approved review items for this team.", command: commands.aiGmReviewApplyAllApprovedCommit, action: "ai_gm_review_apply", params: { all_approved: true, team, apply: true, limit: 20 }, tone: "warn" },
    ]);

    const askPanel = workflowPanel("Ask A GM", profile.gm_name || team, [
      { title: "Depth Chart Review", detail: "Ask for promotions and demotions based on role fit, youth, and current ability.", command: commands.aiGmRunDepth, action: "ai_gm_run", params: { team, decision_type: "depth_chart_review" } },
      { title: "Free-Agent Shortlist", detail: "Ask for sensible FA targets using need fit and cap discipline.", command: commands.aiGmRunFreeAgency, action: "ai_gm_run", params: { team, decision_type: "free_agent_shortlist" } },
      { title: "Decision History", detail: "Show recent front-office decisions for this team.", command: commands.aiGmLogs, action: "ai_gm_logs", params: { team, limit: 12 } },
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
      runsBody.append(node("div", "empty-state", "No front-office checks have been recorded yet."));
    }
    root.append(runs);

    const reviewPanel = panel("Front Office Review Inbox", `${ai.counts?.reviewInbox || 0} Open`);
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
      panelBody(reviewPanel).append(node("div", "empty-state", "No front-office items need review."));
    }
    root.append(reviewPanel);
    root.append(renderReviewDetailPanel(selectedAiGmReview(ai)));

    const activityPanel = panel("Front Office Review Activity", `${ai.counts?.reviewActivity || 0} Recent`);
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
      panelBody(activityPanel).append(node("div", "empty-state", "No front-office review activity has been recorded yet."));
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
      panelBody(evalPanel).append(node("div", "empty-state", ai.evaluationError || "Run Evaluate Team to inspect this front office's baseline plan."));
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
        meta: "CPU",
      })));
      cutdownGrid.append(buildPlanList("Fallback Keeps Over AI", cutdownPlan.comparison_to_deterministic_fallback?.fallback_active_over_ai, (item) => ({
        title: `${item.player_name || "-"} ${item.position || ""}`.trim(),
        detail: `${item.position_group || ""} OVR ${item.overall || "-"}`,
        meta: "Baseline",
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

    const opsPanel = panel("Front Office Operations", ai.ops?.resolved_phase || "Auto");
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
      panelBody(opsPanel).append(node("div", "empty-state", ai.opsError || "No front-office operations recommended for the current team."));
    }
    root.append(opsPanel);

    const queuePanel = panel("Decision Queue", `${ai.counts?.queue || 0} Pending`);
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
      panelBody(queuePanel).append(node("div", "empty-state", "No queued front-office tasks."));
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
      panelBody(profilePanel).append(node("div", "empty-state", "Prepare front offices to seed team profiles."));
    }
    root.append(profilePanel);

    const logsPanel = panel("Recent Front Office Decisions", "League decision history");
    const logList = node("div", "list compact-list");
    (ai.logs || []).forEach((log) => {
      logList.append(row(`${log.team || "-"} ${log.decision_type || "-"}`, log.action_taken || log.error_message || "", log.status || "-", log.status === "valid" || log.status === "completed" ? "good" : log.status === "failed" ? "bad" : "warn"));
    });
    panelBody(logsPanel).append(logList.children.length ? logList : node("div", "empty-state", "No front-office decisions have been recorded yet."));
    root.append(logsPanel);

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function accoladeTierClass(tier) {
    return `accolade-${String(tier || "default").replace(/_/g, "-")}`;
  }

  function awardBadge(item, compact = true) {
    const badge = node("span", `accolade-badge ${accoladeTierClass(item?.badgeTier)}${compact ? " compact" : ""}`);
    const label = item?.badgeLabel || item?.awardPosition || item?.awardName || "Award";
    const name = item?.awardName || "Accolade";
    badge.title = name;
    badge.append(node("strong", null, label));
    if (!compact) badge.append(node("span", null, name));
    return badge;
  }

  function awardPlayerCell(item, detail) {
    const wrap = node("span", "award-player-cell");
    const playerId = item?.playerId ?? item?.player_id;
    const playerName = item?.playerName || item?.player_name || "-";
    const position = item?.position || "";
    const team = item?.team || "";
    append(wrap, [
      playerLink(playerId, playerName, "player-link strong-link", { team, position }),
      node("small", null, detail || item?.line || [position, item?.record].filter(Boolean).join(" | ")),
    ]);
    return wrap;
  }

  function awardTeamCell(item) {
    const abbr = String(item?.team || "").trim().toUpperCase();
    if (!abbr || abbr === "-") return "-";
    const wrap = node("span", "award-team-cell");
    append(wrap, [
      teamLogo(item?.teamLogo, abbr, "award-team-logo"),
      statTeamLink(abbr),
    ]);
    return wrap;
  }

  function awardPlayerList(items, limit = 4) {
    const list = node("div", "award-inline-list");
    (items || []).slice(0, limit).forEach((item) => {
      list.append(awardPlayerCell(item, `${item.position || ""}${item.score !== undefined ? ` | ${oneDecimal(item.score)} score` : ""}`.trim()));
    });
    const remaining = (items || []).length - limit;
    if (remaining > 0) list.append(node("small", "muted", `+${remaining} more`));
    return list.children.length ? list : node("span", null, "-");
  }

  function awardBallotPanel(title, kicker, rows, limit = 8) {
    const p = panel(title, kicker);
    panelBody(p).append(table(["#", "Player", "Team", "Pos", "Score", "Line"], (rows || []).slice(0, limit).map((item, idx) => [
      idx + 1,
      awardPlayerCell(item),
      awardTeamCell(item),
      item.position || "-",
      oneDecimal(item.score),
      item.line || "-",
    ])));
    return p;
  }

  function awardAllProPanel(rows) {
    const p = panel("All-Pro Teams", "Projected 1st and 2nd team");
    panelBody(p).append(table(["Position", "1st Team", "2nd Team"], (rows || []).map((group) => [
      group.label || group.position || "-",
      awardPlayerList(group.firstTeam || [], Math.max(1, Number(group.slots || 1))),
      awardPlayerList(group.secondTeam || [], Math.max(1, Number(group.slots || 1))),
    ])));
    return p;
  }

  function awardProBowlPanel(rows) {
    const p = panel("Pro Bowl", "Projected selections");
    panelBody(p).append(table(["Position", "Players"], (rows || []).map((group) => [
      group.label || group.position || "-",
      awardPlayerList(group.players || [], Math.min(6, Math.max(2, Number(group.slots || 4)))),
    ])));
    return p;
  }

  function awardFinalPanel(title, kicker, rows) {
    const p = panel(title, kicker);
    panelBody(p).append(table(["Award", "Player", "Team", "Pos", "Season"], (rows || []).map((item) => [
      awardBadge(item),
      awardPlayerCell(item, item.awardName || item.awardPosition || item.position || ""),
      awardTeamCell(item),
      item.awardPosition || item.position || "-",
      item.season || "-",
    ])));
    return p;
  }

  function renderAwards() {
    setHeader("Awards", "Projected award races during the season, final winners after season completion.");
    const root = document.createDocumentFragment();
    const awards = data.awards || {};
    const ballots = awards.ballots || {};
    const finalAwards = awards.final || {};
    const counts = awards.counts || {};
    const season = awards.season || data.currentSeason || data.season?.season || "";
    if (runnerMode() && String(state.awardsLiveSeason || "") !== String(season || "") && !state.awardsLoading) {
      loadLiveAwards().then(render);
    }

    const summary = panel("Awards Desk", `${season || "-"} | ${awards.status || "Projected"}`);
    const metrics = node("div", "metric-grid compact-metrics awards-metrics");
    append(metrics, [
      metric("Status", awards.status || "Projected", awards.finalized ? "Locked after season completion" : "Current ballot", awards.finalized ? "good" : "warn"),
      metric("Candidates", whole(counts.candidateCount || ballots.candidateCount || 0), "Stat-qualified players"),
      metric("Major Awards", whole(counts.major || 0), "MVP, ROTY, CPOTY"),
      metric("All-Pro", whole(counts.allPro || 0), "1st and 2nd team"),
      metric("Pro Bowl", whole(counts.proBowl || 0), "Lowest badge tier"),
    ]);
    if (state.awardsLoading) metrics.append(node("div", "empty-state compact-empty", "Refreshing live awards..."));
    panelBody(summary).append(metrics);
    root.append(summary);

    if (awards.finalized) {
      root.append(awardFinalPanel("Final Major Awards", "Season honors", finalAwards.major || []));
      const finalGrid = node("div", "grid awards-grid");
      append(finalGrid, [
        awardFinalPanel("1st Team All-Pro", "Final", finalAwards.firstTeamAllPro || []),
        awardFinalPanel("2nd Team All-Pro", "Final", finalAwards.secondTeamAllPro || []),
      ]);
      root.append(finalGrid);
      root.append(awardFinalPanel("Pro Bowl", "Final selections", finalAwards.proBowl || []));
      if ((finalAwards.champions || []).length) {
        root.append(awardFinalPanel("Super Bowl Champions", "Title badges", finalAwards.champions || []));
      }
    }

    const hasProjectedRows = (ballots.mvp || []).length
      || (ballots.rookie || []).length
      || (ballots.comeback || []).length
      || (ballots.allPro || []).length
      || (ballots.proBowl || []).length;
    if (!hasProjectedRows && !awards.finalized) {
      const empty = panel("Projected Awards", "No ballot yet");
      panelBody(empty).append(node("div", "empty-state", "Award races will populate once season stats exist."));
      root.append(empty);
    } else {
      const majorGrid = node("div", "grid awards-grid");
      append(majorGrid, [
        awardBallotPanel("MVP", "Projected voting", ballots.mvp || []),
        awardBallotPanel("Rookie Of The Year", "Projected voting", ballots.rookie || []),
      ]);
      root.append(majorGrid);
      const comeback = awardBallotPanel("Comeback Player Of The Year", "Projected voting", ballots.comeback || [], 8);
      root.append(comeback);
      const teamGrid = node("div", "grid awards-grid wide-awards-grid");
      append(teamGrid, [
        awardAllProPanel(ballots.allPro || []),
        awardProBowlPanel(ballots.proBowl || []),
      ]);
      root.append(teamGrid);
    }

    const output = runnerOutputPanel();
    if (output) root.append(output);
    finishRender(root);
  }

  function renderStats() {
    setHeader("League Leaders", "Quick realism check for the completed season: passing, rushing, receiving, defense, returns, kicking, and snap leaders.");
    const root = document.createDocumentFragment();
    const stats = data.stats || {};
    const season = data.currentSeason || data.season?.season || "";
    if (runnerMode() && String(state.statsLiveSeason || "") !== String(season || "") && !state.statsLoading) {
      loadLiveLeaders().then(render);
    }

    const summary = panel("Season Leaders", `${season}`);
    if (state.statsLoading) {
      panelBody(summary).append(node("div", "empty-state", "Refreshing live league leaders..."));
    }
    panelBody(summary).append(table(["Category", "Leader", "Team", "Total"], [
      ["Passing", statPlayerLink(stats.passing?.[0]), statTeamLink(stats.passing?.[0]?.team), stats.passing?.[0] ? `${whole(stats.passing[0].pass_yards)} yards` : "-"],
      ["Rushing", statPlayerLink(stats.rushing?.[0]), statTeamLink(stats.rushing?.[0]?.team), stats.rushing?.[0] ? `${whole(stats.rushing[0].rush_yards)} yards` : "-"],
      ["Receiving", statPlayerLink(stats.receiving?.[0]), statTeamLink(stats.receiving?.[0]?.team), stats.receiving?.[0] ? `${whole(stats.receiving[0].receiving_yards)} yards` : "-"],
      ["Kick Returns", statPlayerLink(stats.kickReturns?.[0]), statTeamLink(stats.kickReturns?.[0]?.team), stats.kickReturns?.[0] ? `${whole(stats.kickReturns[0].kickoff_return_yards)} yards` : "-"],
      ["Punt Returns", statPlayerLink(stats.puntReturns?.[0]), statTeamLink(stats.puntReturns?.[0]?.team), stats.puntReturns?.[0] ? `${whole(stats.puntReturns[0].punt_return_yards)} yards` : "-"],
      ["Sacks", statPlayerLink(stats.sacks?.[0]), statTeamLink(stats.sacks?.[0]?.team), stats.sacks?.[0] ? `${whole(stats.sacks[0].sacks)} sacks` : "-"],
      ["Tackles", statPlayerLink(stats.tackles?.[0]), statTeamLink(stats.tackles?.[0]?.team), stats.tackles?.[0] ? `${whole(stats.tackles[0].tackles)} tackles` : "-"],
      ["Snaps", statPlayerLink(stats.snaps?.[0]), statTeamLink(stats.snaps?.[0]?.team), stats.snaps?.[0] ? `${whole(stats.snaps[0].total_snaps)} snaps` : "-"],
    ]));
    root.append(summary);

    const passing = panel("Passing", "Yards");
    panelBody(passing).append(table(["#", "Player", "Team", "Comp", "Att", "Pct", "Yds", "TD", "INT", "Sacks"], (stats.passing || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      statTeamLink(p.team),
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
      statTeamLink(p.team),
      whole(p.rush_attempts),
      whole(p.rush_yards),
      oneDecimal(Number(p.rush_yards || 0) / Math.max(1, Number(p.rush_attempts || 0))),
      whole(p.rush_tds),
    ])));

    const receiving = panel("Receiving", "Yards");
    panelBody(receiving).append(table(["#", "Player", "Team", "Rec", "Tgt", "Yds", "Avg", "TD"], (stats.receiving || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      statTeamLink(p.team),
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
      statTeamLink(p.team),
      whole(p.sacks),
      whole(p.tackles),
      whole(p.forced_fumbles),
    ])));
    const interceptions = panel("Coverage", "Interceptions");
    panelBody(interceptions).append(table(["#", "Player", "Team", "INT", "PD", "Solo", "Ast", "Tkl"], (stats.interceptions || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      statTeamLink(p.team),
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
      statTeamLink(p.team),
      whole(p.fg_made),
      whole(p.fg_attempts),
      whole(p.xp_made),
      whole(p.xp_attempts),
      whole(p.long_fg),
    ])));
    root.append(kicking);

    const returnsGrid = node("div", "grid");
    const kickReturns = panel("Kick Returns", "Yards");
    panelBody(kickReturns).append(table(["#", "Player", "Team", "KR", "Yds", "Avg", "TD"], (stats.kickReturns || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      statTeamLink(p.team),
      whole(p.kickoff_returns),
      whole(p.kickoff_return_yards),
      oneDecimal(Number(p.kickoff_return_yards || 0) / Math.max(1, Number(p.kickoff_returns || 0))),
      whole(p.kickoff_return_tds),
    ])));
    const puntReturns = panel("Punt Returns", "Yards");
    panelBody(puntReturns).append(table(["#", "Player", "Team", "PR", "Yds", "Avg", "TD", "FC"], (stats.puntReturns || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      statTeamLink(p.team),
      whole(p.punt_returns),
      whole(p.punt_return_yards),
      oneDecimal(Number(p.punt_return_yards || 0) / Math.max(1, Number(p.punt_returns || 0))),
      whole(p.punt_return_tds),
      whole(p.fair_catches),
    ])));
    append(returnsGrid, [kickReturns, puntReturns]);
    root.append(returnsGrid);

    const specialTeamsTackles = panel("Special Teams", "Coverage Tackles");
    panelBody(specialTeamsTackles).append(table(["#", "Player", "Team", "Tkl", "Solo", "Ast", "Stops", "ST"], (stats.specialTeamsTackles || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      statTeamLink(p.team),
      whole(p.special_teams_tackles),
      whole(p.special_teams_solo_tackles),
      whole(p.special_teams_assisted_tackles),
      whole(p.special_teams_stops),
      whole(p.special_teams_snaps),
    ])));
    root.append(specialTeamsTackles);

    const snaps = panel("Snaps", "Usage");
    panelBody(snaps).append(table(["#", "Player", "Team", "Off", "Def", "ST", "Total"], (stats.snaps || []).map((p, idx) => [
      idx + 1,
      statPlayerLink(p),
      statTeamLink(p.team),
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

  function observeTakeoverPanel() {
    if (!isObserveMode()) return null;
    const teams = rosterTeamOptions();
    if (!teams.length) return null;
    if (!state.takeoverTeam || !teams.some((team) => team.abbr === state.takeoverTeam)) {
      const preferred = teams.find((team) => team.abbr === "MIN") || teams[0];
      state.takeoverTeam = preferred.abbr;
    }
    const selected = teams.find((team) => team.abbr === state.takeoverTeam) || teams[0];
    const root = panel("Take Over Team", "Observe Mode");
    root.classList.add("observe-takeover-panel");
    const body = panelBody(root);
    const row = node("div", "roster-team-switcher observe-takeover-controls");
    const logo = teamLogo(selected?.logo, selected?.abbr, "roster-team-logo");
    const field = node("label", "roster-filter roster-team-filter");
    append(field, [node("span", null, "Team")]);
    const select = node("select");
    select.disabled = state.runnerBusy || !runnerMode();
    teams.forEach((team) => {
      const option = node("option", null, `${team.abbr} | ${team.name}`);
      option.value = team.abbr;
      option.selected = team.abbr === state.takeoverTeam;
      select.append(option);
    });
    select.addEventListener("change", () => {
      state.takeoverTeam = select.value;
      render();
    });
    field.append(select);
    const takeOver = node("button", "run-button", state.runnerBusy ? "Running" : "Take Over");
    takeOver.type = "button";
    takeOver.disabled = state.runnerBusy || !runnerMode() || !state.takeoverTeam;
    takeOver.title = runnerMode() ? "Switch this save to user control for the selected team." : "Actions unavailable right now";
    takeOver.addEventListener("click", () => runAction("take_over_team", { team: state.takeoverTeam }));
    append(row, [logo, field, takeOver]);
    append(body, [
      node("p", "muted", "Jump in at the current date. The team’s existing CPU scouting file becomes your scouting board."),
      row,
    ]);
    return root;
  }

  function renderCalendar() {
    const userTeam = data.activeSave?.user_team || "League";
    setHeader("Calendar", isObserveMode() ? "League calendar with games, news, and the next useful advance target." : `${userTeam} schedule with league-wide dates, news, and the next useful advance target.`);
    const root = document.createDocumentFragment();
    const calendar = data.calendar || {};
    const calendarKey = `${data.currentSeason || data.season?.season || ""}:${data.currentDate || calendar.focusDate || ""}`;
    if (runnerMode() && !state.runnerBusy && !state.calendarLiveFocus && state.calendarLiveKey !== calendarKey && !state.calendarLoading) {
      loadLiveCalendar().then((changed) => {
        if (changed) scheduleRender();
      });
    }
    const nextEvent = calendar.nextEvent || (data.events || [])[0];
    const nextWeek = data.season?.nextWeek;
    root.append(calendarControlPanel(calendar, nextEvent, nextWeek));
    if (guidedOffseasonActive()) root.append(renderGuidedOffseasonPanel());
    const takeover = observeTakeoverPanel();
    if (takeover) root.append(takeover);
    if (state.runnerBusy && cancellableRunnerAction(state.busyAction)) {
      root.append(calendarSimProgressStrip(calendar));
    }

    const scopeLabel = calendar.scope === "user_team" ? `${userTeam} Calendar` : "League Calendar";
    const monthPanel = panel(calendar.monthLabel || "Calendar", `${scopeLabel} | ${shortDate(calendar.rangeStart)} - ${shortDate(calendar.rangeEnd)}`);
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

  function draftClassSetupPanel() {
    const setup = data.draftClassSetup || {};
    if (!setup.required) return null;
    const packages = (setup.packages || []).filter((item) => item.valid);
    if (!state.selectedDraftClassPackage && packages.length) {
      state.selectedDraftClassPackage = packages[0].path || "";
    }
    const root = panel("Draft Class Setup", `${setup.draftYear || data.draft?.year || ""} Draft`);
    root.classList.add("draft-class-setup");
    const body = panelBody(root);
    const intro = append(node("div", "setup-copy"), [
      node("strong", null, "Choose how this year’s draft class enters the league."),
      node("span", null, `New saves and each June 1 now pause here instead of silently generating prospects.`),
    ]);
    const actions = node("div", "draft-class-actions");
    const generate = node("button", "control-button good", "Generate Draft Class");
    generate.type = "button";
    generate.disabled = state.runnerBusy || !runnerMode();
    generate.addEventListener("click", () => runAction("draft_class_generate", { draft_year: setup.draftYear || data.draft?.year }));
    const select = node("select", "draft-class-package-select");
    select.disabled = state.runnerBusy || !runnerMode() || packages.length === 0;
    packages.forEach((item) => {
      const label = `${item.name || item.packageName} (${item.prospectCount || "?"} prospects${item.draftYear ? `, saved ${item.draftYear}` : ""})`;
      const option = node("option", null, label);
      option.value = item.path || "";
      if (option.value === state.selectedDraftClassPackage) option.selected = true;
      select.append(option);
    });
    select.addEventListener("change", () => {
      state.selectedDraftClassPackage = select.value;
    });
    const importButton = node("button", "control-button", "Import Selected Class");
    importButton.type = "button";
    importButton.disabled = state.runnerBusy || !runnerMode() || !state.selectedDraftClassPackage;
    importButton.addEventListener("click", () => runAction("draft_class_import", {
      draft_year: setup.draftYear || data.draft?.year,
      package: state.selectedDraftClassPackage,
    }));
    append(actions, [generate, select, importButton]);
    const footer = node("small", "muted", packages.length
      ? `Saved classes are loaded from ${setup.packageRoot || "Saved Draft Classes"}. The package year can be remapped to this save’s draft year.`
      : `No saved packages were found under ${setup.packageRoot || "Saved Draft Classes"}.`);
    append(body, [intro, actions, footer]);
    return root;
  }

  function calendarDayCell(day) {
    const classes = [
      "calendar-day",
      day.isCurrentMonth ? "" : "outside-month",
      day.isToday ? "today" : "",
      day.isFocusDate ? "focus-date" : "",
      state.calendarLiveFocus && day.date === calendarLiveFocusDate() ? "sim-focus" : "",
      (day.events || []).length || (day.games || []).length || (day.news || []).length ? "has-items" : "",
    ].filter(Boolean).join(" ");
    const cell = node("article", classes);
    const top = append(node("div", "calendar-day-top"), [
      node("strong", null, String(day.dayNumber || "")),
      node("span", null, day.weekday || ""),
    ]);
    const items = node("div", "calendar-items");
    const visibleEvents = (day.events || []).filter((event) => {
      const code = String(event.event_code || "");
      return !code.startsWith("PRESEASON_WEEK_");
    });
    visibleEvents.slice(0, 3).forEach((event) => items.append(calendarEventChip(event)));
    (day.games || []).slice(0, 4).forEach((game) => items.append(calendarGameChip(game)));
    (day.news || []).slice(0, 3).forEach((item) => items.append(calendarNewsChip(item)));
    const overflow = visibleEvents.length + (day.games || []).length + (day.news || []).length - items.children.length;
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
    const matchup = event.matchup || null;
    const isPreseason = String(event.event_code || "").includes("PRESEASON");
    const button = node("button", `calendar-chip event ${matchup ? "with-logos" : ""}`.trim());
    button.type = "button";
    button.title = event.notes || event.phase_name || event.event_category || "";
    if (matchup && isPreseason) {
      append(button, [
        teamLogo(matchup.awayLogo, matchup.away_team, "calendar-logo"),
        append(node("span", "calendar-chip-stack"), [
          node("strong", null, event.event_name || "Preseason"),
          node("small", null, matchup.scoreLabel || `${matchup.away_team || "-"} @ ${matchup.home_team || "-"}`),
        ]),
        teamLogo(matchup.homeLogo, matchup.home_team, "calendar-logo"),
      ]);
    } else {
      button.append(event.event_name || "League Event");
    }
    button.addEventListener("click", () => {
      state.selectedCalendarItem = { type: "event", id: event.event_id };
      render();
    });
    return button;
  }

  function calendarMilestoneButton(event) {
    const matchup = event.matchup || null;
    const isPreseason = String(event.event_code || "").includes("PRESEASON");
    const button = node(
      "button",
      `control-button calendar-milestone-button ${isPreseason || String(event.event_code || "").includes("CAMP") ? "good" : ""} ${matchup ? "with-logos" : ""}`.trim(),
    );
    button.type = "button";
    button.disabled = state.runnerBusy || !runnerMode();
    button.title = runnerMode()
      ? (isPreseason ? "Sim through this preseason week and move the calendar there" : actionLabel("advance_to_date"))
      : "Actions unavailable right now";
    if (matchup && isPreseason) {
      append(button, [
        teamLogo(matchup.awayLogo, matchup.away_team, "calendar-logo"),
        append(node("span", "calendar-chip-stack"), [
          node("strong", null, `${shortDate(event.event_start_date)} ${event.event_name || "Preseason"}`),
          node("small", null, matchup.scoreLabel || `${matchup.away_team || "-"} @ ${matchup.home_team || "-"}`),
        ]),
        teamLogo(matchup.homeLogo, matchup.home_team, "calendar-logo"),
      ]);
    } else {
      button.textContent = `${shortDate(event.event_start_date)} ${event.event_name || "Calendar Event"}`;
    }
    button.addEventListener("click", () => runAction("advance_to_date", {
      date: event.event_start_date,
      auto_sim_preseason: isPreseason,
    }));
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
    const gameType = String(game.game_type || "").toUpperCase();
    const prefix = gameType === "PRE" ? "PRE " : gameType === "POST" ? "POST " : "";
    const button = node("button", `calendar-chip game ${played ? "final" : ""}`.trim());
    button.type = "button";
    append(button, [
      teamLogo(game.awayLogo, game.away_team, "calendar-logo"),
      node("span", null, played
        ? `${prefix}${game.away_team} ${game.away_score ?? "-"} - ${game.home_team} ${game.home_score ?? "-"}`
        : `${prefix}${game.away_team} @ ${game.home_team}`),
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
    return renderBoxScore(result, { compact: true });
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
      node("span", "tag", `${String(item.game_type || "REG").toUpperCase()} ${played ? "Final" : "Scheduled"}`),
      node("strong", null, played
        ? `${item.away_team} ${item.away_score ?? "-"} at ${item.home_team} ${item.home_score ?? "-"}`
        : `${item.away_team} at ${item.home_team}`),
      node("p", "muted", `Week ${item.week || "-"} | ${shortDate(item.game_date)}${item.game_time_et ? ` | ${item.game_time_et} ET` : ""}`),
      showBox,
      boxScore,
    ]);
  }

  function renderCommands() {
    setHeader("League Office", "Core tools now live on their season screens.");
    const root = document.createDocumentFragment();
    const p = panel("Office Directory", "Moved");
    panelBody(p).append(node("div", "empty-state", "Use the season, roster, draft, free-agency, and CPU front-office screens for league actions."));
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
    if (!rows.length) return node("div", "empty-state", "Nothing to show yet.");
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
    clearObserveInterruptions();
    if (isObserveMode() && observeHiddenViews().has(state.view)) {
      state.view = "today";
    }
    state.view = normalizeView(state.view);
    syncGameCenterUrl();
    maybeShowRosterGatePromptFromState();
    const previousView = state.lastRenderedView;
    const shouldRestoreScroll = previousView === state.view;
    const scrollElement = document.scrollingElement || document.documentElement;
    const scrollTop = scrollElement ? scrollElement.scrollTop : window.scrollY;
    const scrollLeft = scrollElement ? scrollElement.scrollLeft : window.scrollX;
    const nestedScroll = shouldRestoreScroll ? scrollableSnapshot() : [];
    if (state.view === "today") renderToday();
    else if (state.view === "season") renderSeason();
    else if (state.view === "playoffTree") renderPlayoffTree();
    else if (state.view === "stats") renderStats();
    else if (state.view === "awards") renderAwards();
    else if (state.view === "history") renderLeagueHistory();
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
    else if (state.view === "waivers") renderWaivers();
    else if (state.view === "trades") renderTradeCenter();
    else if (state.view === "draft") renderDraft();
    else if (state.view === "aiGm") renderAiGm();
    else if (state.view === "calendar") renderCalendar();
    else renderToday();
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
      if (button.dataset.view === "roster") {
        state.rosterTeam = "";
        state.depthChartLiveKey = null;
      }
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

