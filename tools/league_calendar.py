#!/usr/bin/env python3
"""Build the NFL GM Sim league calendar.

The sim league year starts on June 1 and ends on May 31. That is different
from the real NFL contract league year, so the calendar stores both:

- league_year: the sim/season year that begins on June 1.
- event dates: real Gregorian calendar dates.

Future NFL dates are not all officially published years in advance, so this
script stores official 2026 anchors where available and generated recurring
dates for future years with is_official = 0.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_START_YEAR = 2026
DEFAULT_YEARS = 10

NFL_OPS_2026_DATES_URL = (
    "https://operations.nfl.com/gameday/nfl-schedule/2026-important-nfl-dates/"
)
NFL_OPS_2026_KICKOFF_URL = (
    "https://operations.nfl.com/updates/the-game/"
    "2026-regular-season-to-kick-off-wednesday-sept-9-in-seattle/"
)
NFL_TRADE_DEADLINE_URL = (
    "https://www.nfl.com/news/nfl-owners-extend-trade-deadline-to-follow-week-9-games-of-2024-nfl-season"
)
NFL_ROSTER_RULES_URL = (
    "https://operations.nfl.com/inside-football-ops/nfl-operations/"
    "nfl-free-agency/contract-language/"
)


@dataclass(frozen=True)
class EventSeed:
    event_code: str
    event_name: str
    event_category: str
    start_date: date
    end_date: date | None = None
    time_et: str | None = None
    phase_code: str | None = None
    roster_limits_enforced_after: int | None = None
    roster_rule_phase_after: str | None = None
    is_official: int = 0
    source_name: str = "Projected recurring NFL calendar"
    source_url: str | None = None
    notes: str | None = None
    sort_order: int = 0


@dataclass(frozen=True)
class PhaseSeed:
    phase_code: str
    phase_name: str
    start_date: date
    end_date: date
    roster_limits_enforced: int
    roster_rule_phase: str | None
    transactions_open: int
    salary_cap_mode: str
    notes: str
    sort_order: int


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return nth weekday in a month. Monday is 0."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def last_weekday(year: int, month: int, weekday: int) -> date:
    """Return last weekday in a month. Monday is 0."""
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def labor_day(year: int) -> date:
    return nth_weekday(year, 9, 0, 1)


def regular_season_kickoff(year: int) -> date:
    # 2026 has an officially announced Wednesday kickoff. Future years use the
    # usual Thursday after Labor Day anchor.
    if year == 2026:
        return date(2026, 9, 9)
    return labor_day(year) + timedelta(days=3)


def second_wednesday_march(year: int) -> date:
    return nth_weekday(year, 3, 2, 2)


def draft_start(year: int) -> date:
    # The NFL Draft usually lands on the fourth Thursday in April.
    return nth_weekday(year, 4, 3, 4)


def first_friday_after(value: date) -> date:
    days = (4 - value.weekday()) % 7
    if days == 0:
        days = 7
    return value + timedelta(days=days)


def official_2026_source(event_code: str) -> tuple[int, str, str | None]:
    official_codes = {
        "JUNE_1_TENDER_DEADLINE",
        "JUNE_15_TENDER_DEADLINE",
        "FRANCHISE_MULTYEAR_DEADLINE",
        "TRANSITION_TENDER_SIGNING_ENDS",
        "UFA_TENDER_SIGNING_ENDS",
    }
    if event_code in official_codes:
        return 1, "NFL Football Operations 2026 Important Dates", NFL_OPS_2026_DATES_URL
    if event_code == "REGULAR_SEASON_KICKOFF":
        return 1, "NFL Football Operations 2026 Kickoff announcement", NFL_OPS_2026_KICKOFF_URL
    return 0, "Projected recurring NFL calendar", None


def events_for_league_year(league_year: int) -> list[EventSeed]:
    next_year = league_year + 1
    year_start = date(league_year, 6, 1)
    year_end = date(next_year, 5, 31)
    kickoff = regular_season_kickoff(league_year)
    week1_monday = kickoff - timedelta(days=kickoff.weekday())
    cutdown = week1_monday - timedelta(days=6)
    waiver_claims = cutdown + timedelta(days=1)
    week18_sunday = week1_monday + timedelta(weeks=17, days=6)
    trade_deadline = week1_monday + timedelta(weeks=9, days=1)
    postseason_freeze = week18_sunday + timedelta(days=3)
    wild_card_start = week18_sunday + timedelta(days=6)
    wild_card_end = week18_sunday + timedelta(days=8)
    divisional_start = week18_sunday + timedelta(days=13)
    divisional_end = week18_sunday + timedelta(days=14)
    conference_championships = week18_sunday + timedelta(days=21)
    super_bowl = week18_sunday + timedelta(days=35)

    real_league_year = second_wednesday_march(next_year)
    franchise_tag_open = real_league_year - timedelta(days=22)
    franchise_tag_deadline = real_league_year - timedelta(days=8)
    combine_start = real_league_year - timedelta(days=16)
    combine_end = real_league_year - timedelta(days=9)
    free_agency_negotiation_start = real_league_year - timedelta(days=2)
    annual_meeting_start = last_weekday(next_year, 3, 6)
    annual_meeting_end = annual_meeting_start + timedelta(days=3)
    new_hc_programs = nth_weekday(next_year, 4, 0, 1)

    draft = draft_start(next_year)
    draft_end = draft + timedelta(days=2)
    facility_visit_deadline = draft - timedelta(days=8)
    rfa_offer_sheet_deadline = draft - timedelta(days=6)
    returning_hc_programs = draft - timedelta(days=3)
    rofr_deadline = draft - timedelta(days=1)
    rookie_minicamp_1 = first_friday_after(draft_end)
    rookie_minicamp_2 = rookie_minicamp_1 + timedelta(days=7)
    rookie_development = rookie_minicamp_2 + timedelta(days=3)
    rookie_premiere_start = rookie_development + timedelta(days=2)
    rookie_premiere_end = rookie_premiere_start + timedelta(days=4)
    spring_meeting_start = rookie_premiere_end + timedelta(days=2)

    hall_of_fame_game = nth_weekday(league_year, 8, 3, 1)
    preseason_week_1 = hall_of_fame_game + timedelta(days=7)
    preseason_week_2 = hall_of_fame_game + timedelta(days=14)
    preseason_week_3 = hall_of_fame_game + timedelta(days=21)

    raw_events = [
        EventSeed(
            "SIM_YEAR_START",
            f"{league_year} Sim League Year Opens",
            "System",
            year_start,
            phase_code="OFFSEASON_OPEN",
            roster_limits_enforced_after=0,
            notes="Game year begins. Roster limits are intentionally disabled at this point.",
            sort_order=10,
        ),
        EventSeed(
            "JUNE_1_TENDER_DEADLINE",
            "June 1 Tender Deadline",
            "Contract",
            date(league_year, 6, 1),
            time_et="4:00 PM",
            phase_code="OFFSEASON_OPEN",
            notes="Prior club June 1 tender deadline for certain unsigned RFAs.",
            sort_order=20,
        ),
        EventSeed(
            "JUNE_15_TENDER_DEADLINE",
            "June 15 Tender Deadline",
            "Contract",
            date(league_year, 6, 15),
            time_et="4:00 PM",
            phase_code="OFFSEASON_OPEN",
            notes="Deadline to withdraw higher RFA qualifying offer and substitute June 15 tender.",
            sort_order=30,
        ),
        EventSeed(
            "ROOKIE_READINESS_PROGRAM",
            "Rookie Readiness Program",
            "Player Development",
            nth_weekday(league_year, 6, 2, 4),
            phase_code="OFFSEASON_OPEN",
            notes="Projected late-June rookie readiness program window.",
            sort_order=40,
        ),
        EventSeed(
            "FRANCHISE_MULTYEAR_DEADLINE",
            "Franchise Player Multiyear Contract Deadline",
            "Contract",
            date(league_year, 7, 15),
            time_et="4:00 PM",
            phase_code="OFFSEASON_OPEN",
            notes="Deadline for a franchise player to sign a multiyear contract or extension.",
            sort_order=50,
        ),
        EventSeed(
            "ROOKIE_TRAINING_CAMP_REPORTING_OPENS",
            "Rookie Training Camp Reporting Opens",
            "Training Camp",
            date(league_year, 7, 15),
            phase_code="CAMP_REPORTING",
            roster_limits_enforced_after=0,
            notes="Modeled as seven days before veteran camp reporting.",
            sort_order=60,
        ),
        EventSeed(
            "TRANSITION_TENDER_SIGNING_ENDS",
            "Transition Tender Signing Period Ends",
            "Contract",
            date(league_year, 7, 22),
            time_et="4:00 PM",
            phase_code="CAMP_REPORTING",
            notes="Transition player signing period ends; prior club retains exclusive rights until trade deadline window.",
            sort_order=70,
        ),
        EventSeed(
            "UFA_TENDER_SIGNING_ENDS",
            "UFA Tender Signing Period Ends",
            "Contract",
            date(league_year, 7, 22),
            time_et="4:00 PM",
            phase_code="CAMP_REPORTING",
            notes="UFA tender signing period ends on July 22 or first scheduled day of first NFL training camp, whichever is later.",
            sort_order=80,
        ),
        EventSeed(
            "VETERAN_TRAINING_CAMP_REPORTING",
            "Veteran Training Camp Reporting Opens",
            "Training Camp",
            date(league_year, 7, 22),
            phase_code="TRAINING_CAMP",
            roster_limits_enforced_after=1,
            roster_rule_phase_after="Preseason",
            notes="Training camp phase begins. The sim starts enforcing the 90-player preseason roster rule here.",
            sort_order=90,
        ),
        EventSeed(
            "HALL_OF_FAME_GAME",
            "Hall of Fame Game / Preseason Opens",
            "Game Phase",
            hall_of_fame_game,
            phase_code="TRAINING_CAMP",
            notes="Projected first Thursday in August.",
            sort_order=100,
        ),
        EventSeed(
            "PRESEASON_WEEK_1",
            "Preseason Week 1",
            "Game Phase",
            preseason_week_1,
            preseason_week_1 + timedelta(days=3),
            phase_code="TRAINING_CAMP",
            sort_order=110,
        ),
        EventSeed(
            "PRESEASON_WEEK_2",
            "Preseason Week 2",
            "Game Phase",
            preseason_week_2,
            preseason_week_2 + timedelta(days=3),
            phase_code="TRAINING_CAMP",
            sort_order=120,
        ),
        EventSeed(
            "PRESEASON_WEEK_3",
            "Preseason Week 3",
            "Game Phase",
            preseason_week_3,
            preseason_week_3 + timedelta(days=3),
            phase_code="TRAINING_CAMP",
            sort_order=130,
        ),
        EventSeed(
            "FINAL_ROSTER_CUTDOWN_53",
            "Final Roster Cutdown To 53",
            "Roster",
            cutdown,
            time_et="4:00 PM",
            phase_code="FINAL_CUTDOWN",
            roster_limits_enforced_after=1,
            roster_rule_phase_after="Regular Season",
            source_name="Projected from NFL cutdown convention",
            source_url=NFL_ROSTER_RULES_URL,
            notes="Tuesday of the week before kickoff. Active/inactive roster must be reduced to 53.",
            sort_order=140,
        ),
        EventSeed(
            "WAIVER_CLAIM_DEADLINE_AFTER_CUTDOWN",
            "Post-Cutdown Waiver Claim Deadline",
            "Roster",
            waiver_claims,
            time_et="12:00 PM",
            phase_code="FINAL_CUTDOWN",
            source_name="Projected from NFL cutdown convention",
            notes="Waiver deadline after final roster cutdown.",
            sort_order=150,
        ),
        EventSeed(
            "PRACTICE_SQUADS_ESTABLISHED",
            "Practice Squads May Be Established",
            "Roster",
            waiver_claims,
            time_et="1:00 PM",
            phase_code="FINAL_CUTDOWN",
            roster_limits_enforced_after=1,
            roster_rule_phase_after="Regular Season",
            source_name="Projected from NFL cutdown convention",
            source_url=NFL_ROSTER_RULES_URL,
            notes="Practice squad construction opens after waivers process.",
            sort_order=160,
        ),
        EventSeed(
            "REGULAR_SEASON_KICKOFF",
            "Regular Season Kickoff",
            "Game Phase",
            kickoff,
            time_et="8:20 PM" if league_year == 2026 else None,
            phase_code="REGULAR_SEASON",
            roster_limits_enforced_after=1,
            roster_rule_phase_after="Regular Season",
            notes="Regular season begins.",
            sort_order=170,
        ),
        EventSeed(
            "TRADE_DEADLINE",
            "NFL Trade Deadline",
            "Transaction",
            trade_deadline,
            time_et="4:00 PM",
            phase_code="REGULAR_SEASON",
            source_name="NFL.com trade deadline rule change",
            source_url=NFL_TRADE_DEADLINE_URL,
            notes="Modeled as Tuesday after Week 9 games, matching the current trade deadline placement.",
            sort_order=180,
        ),
        EventSeed(
            "WEEK_18_FINAL_WEEKEND",
            "Week 18 Final Regular Season Weekend",
            "Game Phase",
            week18_sunday - timedelta(days=1),
            week18_sunday,
            phase_code="REGULAR_SEASON",
            notes="Projected final weekend of an 18-week regular season.",
            sort_order=190,
        ),
        EventSeed(
            "POSTSEASON_ROSTER_FREEZE",
            "Postseason Roster Freeze",
            "Roster",
            postseason_freeze,
            time_et="4:00 PM",
            phase_code="POSTSEASON",
            notes="Postseason roster freeze for clubs participating in the playoffs.",
            sort_order=200,
        ),
        EventSeed(
            "WILD_CARD_WEEKEND",
            "Wild Card Weekend",
            "Postseason",
            wild_card_start,
            wild_card_end,
            phase_code="POSTSEASON",
            sort_order=210,
        ),
        EventSeed(
            "DIVISIONAL_PLAYOFFS",
            "Divisional Playoffs",
            "Postseason",
            divisional_start,
            divisional_end,
            phase_code="POSTSEASON",
            sort_order=220,
        ),
        EventSeed(
            "CONFERENCE_CHAMPIONSHIPS",
            "AFC and NFC Championship Games",
            "Postseason",
            conference_championships,
            phase_code="POSTSEASON",
            sort_order=230,
        ),
        EventSeed(
            "SUPER_BOWL",
            "Super Bowl",
            "Postseason",
            super_bowl,
            phase_code="POSTSEASON",
            sort_order=240,
        ),
        EventSeed(
            "POST_SUPER_BOWL_OFFSEASON_START",
            "Post-Super Bowl Offseason Begins",
            "System",
            super_bowl + timedelta(days=1),
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            roster_limits_enforced_after=0,
            notes="Roster limits turn off for offseason roster building in this sim.",
            sort_order=250,
        ),
        EventSeed(
            "FRANCHISE_TAG_WINDOW_OPENS",
            f"{next_year} Franchise / Transition Tag Window Opens",
            "Contract",
            franchise_tag_open,
            time_et="4:00 PM",
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            notes="Projected as the 22nd day before the real NFL league year.",
            sort_order=260,
        ),
        EventSeed(
            "SCOUTING_COMBINE",
            f"{next_year} NFL Scouting Combine",
            "Draft",
            combine_start,
            combine_end,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=270,
        ),
        EventSeed(
            "FRANCHISE_TAG_DEADLINE",
            f"{next_year} Franchise / Transition Tag Deadline",
            "Contract",
            franchise_tag_deadline,
            time_et="4:00 PM",
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            notes="Projected as the eighth day before the real NFL league year.",
            sort_order=280,
        ),
        EventSeed(
            "FREE_AGENCY_NEGOTIATION_WINDOW",
            f"{next_year} Free Agency Negotiation Window",
            "Transaction",
            free_agency_negotiation_start,
            real_league_year,
            time_et="12:00 PM",
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            notes="Clubs may negotiate with certified agents of pending UFAs.",
            sort_order=290,
        ),
        EventSeed(
            "NEXT_NFL_LEAGUE_YEAR_START",
            f"{next_year} NFL League Year / Free Agency Begins",
            "Transaction",
            real_league_year,
            time_et="4:00 PM",
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            notes="Real NFL league year, free agency, and trading period begin.",
            sort_order=300,
        ),
        EventSeed(
            "ANNUAL_LEAGUE_MEETING",
            "Annual League Meeting",
            "League Meeting",
            annual_meeting_start,
            annual_meeting_end,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=310,
        ),
        EventSeed(
            "NEW_HC_OFFSEASON_PROGRAMS_BEGIN",
            "Offseason Programs Begin For Clubs With New Head Coaches",
            "Workout",
            new_hc_programs,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=320,
        ),
        EventSeed(
            "DRAFT_FACILITY_VISIT_DEADLINE",
            "Draft Prospect Facility Visit Deadline",
            "Draft",
            facility_visit_deadline,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=330,
        ),
        EventSeed(
            "RFA_OFFER_SHEET_DEADLINE",
            "Restricted Free Agent Offer Sheet Deadline",
            "Contract",
            rfa_offer_sheet_deadline,
            time_et="4:00 PM",
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=340,
        ),
        EventSeed(
            "RETURNING_HC_OFFSEASON_PROGRAMS_BEGIN",
            "Offseason Programs Begin For Returning Head Coaches",
            "Workout",
            returning_hc_programs,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=350,
        ),
        EventSeed(
            "RFA_ROFR_DEADLINE",
            "Prior Club RFA Right Of First Refusal Deadline",
            "Contract",
            rofr_deadline,
            time_et="4:00 PM",
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=360,
        ),
        EventSeed(
            "NFL_DRAFT",
            f"{next_year} NFL Draft",
            "Draft",
            draft,
            draft_end,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=370,
        ),
        EventSeed(
            "FIFTH_YEAR_OPTION_DEADLINE",
            "First-Round Fifth-Year Option Deadline",
            "Contract",
            date(next_year, 5, 1),
            time_et="4:00 PM",
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=380,
        ),
        EventSeed(
            "ROOKIE_MINICAMP_WINDOW_1",
            "Post-Draft Rookie Minicamp Window 1",
            "Workout",
            rookie_minicamp_1,
            rookie_minicamp_1 + timedelta(days=3),
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            notes="Clubs may choose one three-day rookie minicamp in this window.",
            sort_order=390,
        ),
        EventSeed(
            "ROOKIE_MINICAMP_WINDOW_2",
            "Post-Draft Rookie Minicamp Window 2",
            "Workout",
            rookie_minicamp_2,
            rookie_minicamp_2 + timedelta(days=3),
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            notes="Clubs may choose one three-day rookie minicamp in this window.",
            sort_order=400,
        ),
        EventSeed(
            "ROOKIE_DEVELOPMENT_PROGRAM_BEGIN",
            "Rookie Football Development Program Begins",
            "Player Development",
            rookie_development,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=410,
        ),
        EventSeed(
            "NFLPA_ROOKIE_PREMIERE",
            "NFLPA Rookie Premiere",
            "Player Development",
            rookie_premiere_start,
            rookie_premiere_end,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=420,
        ),
        EventSeed(
            "SPRING_LEAGUE_MEETING",
            "Spring League Meeting",
            "League Meeting",
            spring_meeting_start,
            spring_meeting_start + timedelta(days=1),
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            sort_order=430,
        ),
        EventSeed(
            "SIM_YEAR_END",
            f"{league_year} Sim League Year Ends",
            "System",
            year_end,
            phase_code="POST_SUPER_BOWL_OFFSEASON",
            roster_limits_enforced_after=0,
            notes="Final day before the next June 1 sim league year.",
            sort_order=440,
        ),
    ]

    events: list[EventSeed] = []
    for event in raw_events:
        if league_year == 2026:
            is_official, source_name, source_url = official_2026_source(event.event_code)
            if is_official:
                event = EventSeed(
                    event.event_code,
                    event.event_name,
                    event.event_category,
                    event.start_date,
                    event.end_date,
                    event.time_et,
                    event.phase_code,
                    event.roster_limits_enforced_after,
                    event.roster_rule_phase_after,
                    is_official,
                    source_name,
                    source_url,
                    event.notes,
                    event.sort_order,
                )
        events.append(event)
    return events


def phases_for_league_year(league_year: int) -> list[PhaseSeed]:
    next_year = league_year + 1
    kickoff = regular_season_kickoff(league_year)
    week1_monday = kickoff - timedelta(days=kickoff.weekday())
    cutdown = week1_monday - timedelta(days=6)
    week18_sunday = week1_monday + timedelta(weeks=17, days=6)
    super_bowl = week18_sunday + timedelta(days=35)

    return [
        PhaseSeed(
            "OFFSEASON_OPEN",
            "Open Offseason / Roster Building",
            date(league_year, 6, 1),
            date(league_year, 7, 14),
            0,
            None,
            1,
            "TOP_51_ALWAYS",
            "The sim year begins here. No roster limits are enforced, so every team starts compliant.",
            10,
        ),
        PhaseSeed(
            "CAMP_REPORTING",
            "Camp Reporting Window",
            date(league_year, 7, 15),
            date(league_year, 7, 21),
            0,
            None,
            1,
            "TOP_51_ALWAYS",
            "Rookies and early reporters can report. Roster limits remain off until veteran camp opens.",
            20,
        ),
        PhaseSeed(
            "TRAINING_CAMP",
            "Training Camp / Preseason",
            date(league_year, 7, 22),
            cutdown - timedelta(days=1),
            1,
            "Preseason",
            1,
            "TOP_51_ALWAYS",
            "Preseason roster rules apply, including the 90-player camp roster limit.",
            30,
        ),
        PhaseSeed(
            "FINAL_CUTDOWN",
            "Final Cutdown / Practice Squad Build",
            cutdown,
            kickoff - timedelta(days=1),
            1,
            "Regular Season",
            1,
            "TOP_51_ALWAYS",
            "Teams must reach the regular-season active roster and build practice squads.",
            40,
        ),
        PhaseSeed(
            "REGULAR_SEASON",
            "Regular Season",
            kickoff,
            week18_sunday,
            1,
            "Regular Season",
            1,
            "TOP_51_ALWAYS",
            "Regular-season roster rules apply.",
            50,
        ),
        PhaseSeed(
            "POSTSEASON",
            "Postseason",
            week18_sunday + timedelta(days=1),
            super_bowl,
            1,
            "Regular Season",
            1,
            "TOP_51_ALWAYS",
            "Playoff roster rules and postseason transaction restrictions apply.",
            60,
        ),
        PhaseSeed(
            "POST_SUPER_BOWL_OFFSEASON",
            "Post-Super Bowl Offseason",
            super_bowl + timedelta(days=1),
            date(next_year, 5, 31),
            0,
            None,
            1,
            "TOP_51_ALWAYS",
            "Roster limits turn off for offseason roster building until the next training camp.",
            70,
        ),
    ]


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS game_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS league_years (
            league_year INTEGER PRIMARY KEY,
            sim_year_start TEXT NOT NULL,
            sim_year_end TEXT NOT NULL,
            nfl_season INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'projected',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS league_phase_windows (
            phase_window_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL REFERENCES league_years(league_year) ON DELETE CASCADE,
            phase_code TEXT NOT NULL,
            phase_name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            roster_limits_enforced INTEGER NOT NULL DEFAULT 0,
            roster_rule_phase TEXT,
            transactions_open INTEGER NOT NULL DEFAULT 1,
            salary_cap_mode TEXT NOT NULL DEFAULT 'TOP_51_ALWAYS',
            notes TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(league_year, phase_code)
        );

        CREATE TABLE IF NOT EXISTS league_calendar_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL REFERENCES league_years(league_year) ON DELETE CASCADE,
            event_code TEXT NOT NULL,
            event_name TEXT NOT NULL,
            event_category TEXT NOT NULL,
            event_start_date TEXT NOT NULL,
            event_end_date TEXT,
            event_time_et TEXT,
            phase_code TEXT,
            roster_limits_enforced_after INTEGER,
            roster_rule_phase_after TEXT,
            is_official INTEGER NOT NULL DEFAULT 0,
            source_name TEXT,
            source_url TEXT,
            notes TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(league_year, event_code)
        );

        CREATE INDEX IF NOT EXISTS idx_league_calendar_events_date
            ON league_calendar_events(event_start_date, league_year, sort_order);

        CREATE INDEX IF NOT EXISTS idx_league_phase_windows_date
            ON league_phase_windows(start_date, end_date, league_year);

        DROP VIEW IF EXISTS league_calendar_view;
        CREATE VIEW league_calendar_view AS
        SELECT
            e.event_id,
            e.league_year,
            y.sim_year_start,
            y.sim_year_end,
            e.event_code,
            e.event_name,
            e.event_category,
            e.event_start_date,
            e.event_end_date,
            e.event_time_et,
            e.phase_code,
            pw.phase_name,
            e.roster_limits_enforced_after,
            e.roster_rule_phase_after,
            e.is_official,
            e.source_name,
            e.source_url,
            e.notes,
            e.sort_order,
            e.updated_at
        FROM league_calendar_events e
        JOIN league_years y ON y.league_year = e.league_year
        LEFT JOIN league_phase_windows pw
            ON pw.league_year = e.league_year
           AND pw.phase_code = e.phase_code;

        DROP VIEW IF EXISTS current_league_phase_view;
        CREATE VIEW current_league_phase_view AS
        SELECT
            gs.setting_value AS current_game_date,
            pw.*
        FROM game_settings gs
        JOIN league_phase_windows pw
          ON date(gs.setting_value) BETWEEN date(pw.start_date) AND date(pw.end_date)
        WHERE gs.setting_key = 'current_game_date';

        DROP VIEW IF EXISTS upcoming_league_events_view;
        CREATE VIEW upcoming_league_events_view AS
        SELECT
            gs.setting_value AS current_game_date,
            lcv.*
        FROM game_settings gs
        JOIN league_calendar_view lcv
          ON date(lcv.event_start_date) >= date(gs.setting_value)
        WHERE gs.setting_key = 'current_game_date';
        """
    )


def upsert_setting(con: sqlite3.Connection, key: str, value: str, *, overwrite: bool) -> None:
    if overwrite:
        con.execute(
            """
            INSERT INTO game_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = datetime('now')
            """,
            (key, value),
        )
    else:
        con.execute(
            """
            INSERT OR IGNORE INTO game_settings (setting_key, setting_value)
            VALUES (?, ?)
            """,
            (key, value),
        )


def insert_event(con: sqlite3.Connection, league_year: int, event: EventSeed) -> None:
    con.execute(
        """
        INSERT INTO league_calendar_events (
            league_year, event_code, event_name, event_category,
            event_start_date, event_end_date, event_time_et, phase_code,
            roster_limits_enforced_after, roster_rule_phase_after, is_official,
            source_name, source_url, notes, sort_order, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(league_year, event_code) DO UPDATE SET
            event_name = excluded.event_name,
            event_category = excluded.event_category,
            event_start_date = excluded.event_start_date,
            event_end_date = excluded.event_end_date,
            event_time_et = excluded.event_time_et,
            phase_code = excluded.phase_code,
            roster_limits_enforced_after = excluded.roster_limits_enforced_after,
            roster_rule_phase_after = excluded.roster_rule_phase_after,
            is_official = excluded.is_official,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            notes = excluded.notes,
            sort_order = excluded.sort_order,
            updated_at = datetime('now')
        """,
        (
            league_year,
            event.event_code,
            event.event_name,
            event.event_category,
            iso(event.start_date),
            iso(event.end_date),
            event.time_et,
            event.phase_code,
            event.roster_limits_enforced_after,
            event.roster_rule_phase_after,
            event.is_official,
            event.source_name,
            event.source_url,
            event.notes,
            event.sort_order,
        ),
    )


def insert_phase(con: sqlite3.Connection, league_year: int, phase: PhaseSeed) -> None:
    con.execute(
        """
        INSERT INTO league_phase_windows (
            league_year, phase_code, phase_name, start_date, end_date,
            roster_limits_enforced, roster_rule_phase, transactions_open,
            salary_cap_mode, notes, sort_order, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(league_year, phase_code) DO UPDATE SET
            phase_name = excluded.phase_name,
            start_date = excluded.start_date,
            end_date = excluded.end_date,
            roster_limits_enforced = excluded.roster_limits_enforced,
            roster_rule_phase = excluded.roster_rule_phase,
            transactions_open = excluded.transactions_open,
            salary_cap_mode = excluded.salary_cap_mode,
            notes = excluded.notes,
            sort_order = excluded.sort_order,
            updated_at = datetime('now')
        """,
        (
            league_year,
            phase.phase_code,
            phase.phase_name,
            iso(phase.start_date),
            iso(phase.end_date),
            phase.roster_limits_enforced,
            phase.roster_rule_phase,
            phase.transactions_open,
            phase.salary_cap_mode,
            phase.notes,
            phase.sort_order,
        ),
    )


def seed_calendar(
    con: sqlite3.Connection,
    *,
    start_year: int,
    years: int,
    set_current_date: bool,
) -> dict[str, int]:
    ensure_schema(con)
    end_year = start_year + years - 1
    target_years = list(range(start_year, end_year + 1))

    qmarks = ",".join("?" for _ in target_years)
    con.execute(f"DELETE FROM league_calendar_events WHERE league_year IN ({qmarks})", target_years)
    con.execute(f"DELETE FROM league_phase_windows WHERE league_year IN ({qmarks})", target_years)
    con.execute(f"DELETE FROM league_years WHERE league_year IN ({qmarks})", target_years)

    event_count = 0
    phase_count = 0
    for league_year in target_years:
        status = "official_and_projected" if league_year == 2026 else "projected"
        con.execute(
            """
            INSERT INTO league_years (
                league_year, sim_year_start, sim_year_end, nfl_season,
                status, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                league_year,
                f"{league_year}-06-01",
                f"{league_year + 1}-05-31",
                league_year,
                status,
                "Sim league year starts June 1 and ends May 31.",
            ),
        )
        for phase in phases_for_league_year(league_year):
            insert_phase(con, league_year, phase)
            phase_count += 1
        for event in events_for_league_year(league_year):
            insert_event(con, league_year, event)
            event_count += 1

    upsert_setting(con, "calendar_start_month_day", "06-01", overwrite=True)
    upsert_setting(con, "calendar_years_seeded", str(years), overwrite=True)
    upsert_setting(con, "calendar_start_year", str(start_year), overwrite=True)
    upsert_setting(con, "current_league_year", str(start_year), overwrite=set_current_date)
    upsert_setting(con, "current_game_date", f"{start_year}-06-01", overwrite=set_current_date)
    upsert_setting(con, "roster_limits_enforced", "0", overwrite=set_current_date)
    upsert_setting(con, "current_calendar_phase", "OFFSEASON_OPEN", overwrite=set_current_date)

    return {"league_years": years, "phases": phase_count, "events": event_count}


def phase_for_date(con: sqlite3.Connection, game_date: str) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM league_phase_windows
        WHERE date(?) BETWEEN date(start_date) AND date(end_date)
        ORDER BY league_year
        LIMIT 1
        """,
        (game_date,),
    ).fetchone()


def action_setup(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    counts = seed_calendar(
        con,
        start_year=args.start_year,
        years=args.years,
        set_current_date=not args.keep_current_date,
    )
    con.commit()
    print(f"League years seeded: {counts['league_years']}")
    print(f"Phase windows seeded: {counts['phases']}")
    print(f"Calendar events seeded: {counts['events']}")
    if not args.keep_current_date:
        print(f"Current game date set to {args.start_year}-06-01")


def action_show_year(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    year = args.year
    row = con.execute(
        "SELECT * FROM league_years WHERE league_year = ?",
        (year,),
    ).fetchone()
    if not row:
        raise ValueError(f"League year not found: {year}. Run setup first.")
    print(f"{year} League Year: {row['sim_year_start']} to {row['sim_year_end']} ({row['status']})")
    print("Phases:")
    for phase in con.execute(
        """
        SELECT *
        FROM league_phase_windows
        WHERE league_year = ?
        ORDER BY sort_order
        """,
        (year,),
    ):
        enforced = "on" if phase["roster_limits_enforced"] else "off"
        print(
            f"  {phase['start_date']} to {phase['end_date']}: "
            f"{phase['phase_name']} | roster limits {enforced}"
        )
    print("Events:")
    for event in con.execute(
        """
        SELECT *
        FROM league_calendar_view
        WHERE league_year = ?
        ORDER BY event_start_date, sort_order
        """,
        (year,),
    ):
        end = f" to {event['event_end_date']}" if event["event_end_date"] else ""
        time = f" {event['event_time_et']} ET" if event["event_time_et"] else ""
        official = "official" if event["is_official"] else "projected"
        print(f"  {event['event_start_date']}{end}{time}: {event['event_name']} ({official})")


def action_current(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game_date = args.date
    if game_date is None:
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
        ).fetchone()
        if not row:
            raise ValueError("current_game_date is not set. Run setup first.")
        game_date = row["setting_value"]
    phase = phase_for_date(con, game_date)
    if not phase:
        raise ValueError(f"No phase found for {game_date}.")
    enforced = "ON" if phase["roster_limits_enforced"] else "OFF"
    print(f"Date: {game_date}")
    print(f"League year: {phase['league_year']}")
    print(f"Phase: {phase['phase_name']} ({phase['phase_code']})")
    print(f"Roster limits: {enforced}")
    print(f"Roster rule phase: {phase['roster_rule_phase'] or 'None'}")
    print(f"Salary cap mode: {phase['salary_cap_mode']}")


def action_set_date(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    phase = phase_for_date(con, args.date)
    if not phase:
        raise ValueError(f"No phase found for {args.date}.")
    upsert_setting(con, "current_game_date", args.date, overwrite=True)
    upsert_setting(con, "current_league_year", str(phase["league_year"]), overwrite=True)
    upsert_setting(con, "current_calendar_phase", phase["phase_code"], overwrite=True)
    upsert_setting(con, "roster_limits_enforced", str(phase["roster_limits_enforced"]), overwrite=True)
    con.commit()
    print(f"Current game date set to {args.date}")
    print(f"Current phase: {phase['phase_name']}")
    print(f"Roster limits enforced: {phase['roster_limits_enforced']}")


def action_next(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    if not row:
        raise ValueError("current_game_date is not set. Run setup first.")
    game_date = row["setting_value"]
    for event in con.execute(
        """
        SELECT *
        FROM league_calendar_view
        WHERE date(event_start_date) >= date(?)
        ORDER BY event_start_date, sort_order
        LIMIT ?
        """,
        (game_date, args.limit),
    ):
        end = f" to {event['event_end_date']}" if event["event_end_date"] else ""
        time = f" {event['event_time_et']} ET" if event["event_time_et"] else ""
        official = "official" if event["is_official"] else "projected"
        print(f"{event['event_start_date']}{end}{time}: {event['event_name']} ({official})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up and inspect the NFL GM Sim league calendar.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Seed 10 years of league calendar data.")
    setup_parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    setup_parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    setup_parser.add_argument("--keep-current-date", action="store_true")

    show_parser = subparsers.add_parser("show-year", help="Print one league year's phases and events.")
    show_parser.add_argument("--year", type=int, default=DEFAULT_START_YEAR)

    current_parser = subparsers.add_parser("current", help="Show the phase for the current or supplied date.")
    current_parser.add_argument("--date")

    set_date_parser = subparsers.add_parser("set-date", help="Set the current game date.")
    set_date_parser.add_argument("--date", required=True)

    next_parser = subparsers.add_parser("next", help="Show upcoming calendar events.")
    next_parser.add_argument("--limit", type=int, default=12)

    args = parser.parse_args()
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if args.command == "setup":
            action_setup(con, args)
        elif args.command == "show-year":
            action_show_year(con, args)
        elif args.command == "current":
            action_current(con, args)
        elif args.command == "set-date":
            action_set_date(con, args)
        elif args.command == "next":
            action_next(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
