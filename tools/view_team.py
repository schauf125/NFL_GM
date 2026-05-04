# /NFL_GM_Sim/tools/view_team.py
# UPDATED — fixes IDL/NB sorting and display grouping

import sqlite3
import os
import sys

SIM_SEASON = 2026

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "nfl_gm.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def format_money(amount):
    if amount is None:
        return "—"
    if amount < 0:
        return "-" + format_money(abs(amount))
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"

def format_height(inches):
    if not inches:
        return "—"
    return f"{inches // 12}'{inches % 12}\""

def get_team(cursor, abbr):
    cursor.execute("SELECT * FROM teams WHERE abbreviation = ?", (abbr.upper(),))
    return cursor.fetchone()

def get_roster(cursor, team_id):
    cursor.execute("""
        WITH best_role AS (
            SELECT player_id, role_name, role_score
            FROM (
                SELECT
                    prs.player_id,
                    rsd.display_name AS role_name,
                    prs.role_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY prs.player_id
                        ORDER BY prs.role_score DESC, prs.role_key
                    ) AS rn
                FROM player_role_scores prs
                LEFT JOIN role_score_definitions rsd
                    ON rsd.role_key = prs.role_key
                WHERE prs.season = ?
                  AND prs.scheme_key = 'default'
            )
            WHERE rn = 1
        ),
        rating_pivot AS (
            SELECT
                player_id,
                MAX(CASE WHEN rating_key = 'kick_power' THEN rating_value END) AS kick_power,
                MAX(CASE WHEN rating_key = 'kick_accuracy' THEN rating_value END) AS kick_accuracy,
                MAX(CASE WHEN rating_key = 'composure' THEN rating_value END) AS composure,
                AVG(CASE WHEN rating_key IN (
                    'play_recognition', 'processing_speed', 'discipline',
                    'composure', 'consistency', 'speed', 'acceleration',
                    'agility', 'strength', 'stamina'
                ) THEN rating_value END) AS general_rating
            FROM player_ratings
            WHERE season = ?
            GROUP BY player_id
        )
        SELECT
            p.player_id, p.first_name, p.last_name, p.position,
            p.age, p.years_exp, p.potential,
            p.dev_trait, p.height_in, p.weight_lbs,
            p.jersey_number, p.status, p.accolades,
            c.aav, c.end_year, c.contract_type,
            COALESCE(
                ROUND(br.role_score),
                CASE
                    WHEN p.position = 'K'
                        THEN ROUND(((COALESCE(rp.kick_power, 50) * 9.0) + (COALESCE(rp.kick_accuracy, 50) * 12.0) + (COALESCE(rp.composure, 50) * 4.0)) / 25.0)
                    WHEN p.position = 'P'
                        THEN ROUND(((COALESCE(rp.kick_power, 50) * 12.0) + (COALESCE(rp.kick_accuracy, 50) * 8.0) + (COALESCE(rp.composure, 50) * 3.0)) / 23.0)
                    ELSE ROUND(COALESCE(rp.general_rating, 50))
                END
            ) AS sim_rating,
            COALESCE(
                br.role_name,
                CASE
                    WHEN p.position IN ('K', 'P') THEN 'Specialist'
                    WHEN p.position = 'LS' THEN 'Long Snapper'
                    ELSE 'General'
                END
            ) AS sim_role
        FROM players p
        LEFT JOIN contracts c
            ON c.player_id = p.player_id AND c.is_active = 1
        LEFT JOIN best_role br
            ON br.player_id = p.player_id
        LEFT JOIN rating_pivot rp
            ON rp.player_id = p.player_id
        WHERE p.team_id = ?
        ORDER BY
            CASE p.position
                WHEN 'QB'   THEN 1
                WHEN 'RB'   THEN 2
                WHEN 'FB'   THEN 3
                WHEN 'WR'   THEN 4
                WHEN 'TE'   THEN 5
                WHEN 'OT'   THEN 6
                WHEN 'OG'   THEN 7
                WHEN 'C'    THEN 8
                WHEN 'EDGE' THEN 9
                WHEN 'IDL'  THEN 10
                WHEN 'ILB'  THEN 11
                WHEN 'OLB'  THEN 12
                WHEN 'CB'   THEN 13
                WHEN 'NB'   THEN 14
                WHEN 'SS'   THEN 15
                WHEN 'FS'   THEN 16
                WHEN 'K'    THEN 17
                WHEN 'P'    THEN 18
                WHEN 'LS'   THEN 19
                ELSE 20
            END,
            sim_rating DESC,
            p.last_name,
            p.first_name
    """, (SIM_SEASON, SIM_SEASON, team_id))
    return cursor.fetchall()

def get_depth_chart(cursor, team_id):
    cursor.execute("""
        WITH best_role AS (
            SELECT player_id, role_score
            FROM (
                SELECT
                    prs.player_id,
                    prs.role_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY prs.player_id
                        ORDER BY prs.role_score DESC, prs.role_key
                    ) AS rn
                FROM player_role_scores prs
                WHERE prs.season = ?
                  AND prs.scheme_key = 'default'
            )
            WHERE rn = 1
        ),
        rating_pivot AS (
            SELECT
                player_id,
                MAX(CASE WHEN rating_key = 'kick_power' THEN rating_value END) AS kick_power,
                MAX(CASE WHEN rating_key = 'kick_accuracy' THEN rating_value END) AS kick_accuracy,
                MAX(CASE WHEN rating_key = 'composure' THEN rating_value END) AS composure,
                AVG(CASE WHEN rating_key IN (
                    'play_recognition', 'processing_speed', 'discipline',
                    'composure', 'consistency', 'speed', 'acceleration',
                    'agility', 'strength', 'stamina'
                ) THEN rating_value END) AS general_rating
            FROM player_ratings
            WHERE season = ?
            GROUP BY player_id
        )
        SELECT
            dc.unit, dc.position, dc.depth_rank,
            p.first_name, p.last_name,
            p.jersey_number, p.age,
            COALESCE(
                ROUND(br.role_score),
                CASE
                    WHEN p.position = 'K'
                        THEN ROUND(((COALESCE(rp.kick_power, 50) * 9.0) + (COALESCE(rp.kick_accuracy, 50) * 12.0) + (COALESCE(rp.composure, 50) * 4.0)) / 25.0)
                    WHEN p.position = 'P'
                        THEN ROUND(((COALESCE(rp.kick_power, 50) * 12.0) + (COALESCE(rp.kick_accuracy, 50) * 8.0) + (COALESCE(rp.composure, 50) * 3.0)) / 23.0)
                    ELSE ROUND(COALESCE(rp.general_rating, 50))
                END
            ) AS sim_rating
        FROM depth_charts dc
        JOIN players p ON p.player_id = dc.player_id
        LEFT JOIN best_role br
            ON br.player_id = p.player_id
        LEFT JOIN rating_pivot rp
            ON rp.player_id = p.player_id
        WHERE dc.team_id = ?
        ORDER BY
            CASE dc.unit
                WHEN 'Offense'       THEN 1
                WHEN 'Defense'       THEN 2
                WHEN 'Special Teams' THEN 3
            END,
            CASE dc.position
                -- Offense
                WHEN 'QB'   THEN 1  WHEN 'RB'   THEN 2
                WHEN 'LWR'  THEN 3  WHEN 'SWR'  THEN 4  WHEN 'RWR' THEN 5
                WHEN 'TE'   THEN 6
                WHEN 'LT'   THEN 7  WHEN 'LG'   THEN 8  WHEN 'C'   THEN 9
                WHEN 'RG'   THEN 10 WHEN 'RT'   THEN 11
                -- Defense
                WHEN 'LDE'   THEN 12 WHEN 'LDL'  THEN 13
                WHEN 'NT'    THEN 14
                WHEN 'RDL'   THEN 15 WHEN 'RDE'  THEN 16
                WHEN 'LEDGE' THEN 17 WHEN 'REDGE' THEN 18
                WHEN 'WLB'   THEN 19 WHEN 'MLB'  THEN 20
                WHEN 'LILB'  THEN 21 WHEN 'RILB' THEN 22
                WHEN 'LCB'   THEN 23 WHEN 'NB'   THEN 24 WHEN 'RCB' THEN 25
                WHEN 'SS'    THEN 26 WHEN 'FS'   THEN 27
                -- ST
                WHEN 'PK' THEN 28 WHEN 'PT' THEN 29
                WHEN 'LS' THEN 30 WHEN 'H'  THEN 31
                WHEN 'KO' THEN 32 WHEN 'PR' THEN 33 WHEN 'KR' THEN 34
                ELSE 99
            END,
            dc.depth_rank
    """, (SIM_SEASON, SIM_SEASON, team_id))
    return cursor.fetchall()

def get_flex(cursor, team_id):
    cursor.execute("""
        SELECT
            p.first_name, p.last_name, p.position AS primary_pos,
            f.position AS flex_pos, f.experience, f.potential,
            f.is_primary, f.source, f.notes
        FROM player_position_flex f
        JOIN players p ON p.player_id = f.player_id
        WHERE p.team_id = ? AND f.is_primary = 0
        ORDER BY p.last_name, f.experience DESC
    """, (team_id,))
    return cursor.fetchall()

def get_cap(cursor, team_id):
    cursor.execute("""
        SELECT
            salary_cap,
            active_contracts,
            contracts_counted,
            contracts_excluded,
            top51_cap_hit,
            excluded_contract_cap_hit,
            top51_cutoff_cap_hit,
            other_cap_charges,
            total_committed,
            cap_space,
            cap_accounting_mode
        FROM team_cap_view
        WHERE team_id = ?
    """, (team_id,))
    return cursor.fetchone()

def print_header(team):
    print("\n" + "═" * 70)
    print(f"  {team['city'].upper()} {team['nickname'].upper()}")
    print(f"  {team['conference']} | {team['division']} | {team['stadium']}")
    print("═" * 70)

def print_cap_summary(team, cap_row):
    salary_cap = cap_row['salary_cap'] if cap_row and cap_row['salary_cap'] is not None else team['salary_cap']
    committed  = cap_row['total_committed'] if cap_row and cap_row['total_committed'] is not None else 0
    space      = salary_cap - committed
    pct        = (committed / salary_cap * 100) if salary_cap else 0
    mode       = cap_row['cap_accounting_mode'] if cap_row and cap_row['cap_accounting_mode'] else "TOP_51_ALWAYS"

    print(f"\n  💰 CAP SUMMARY")
    print(f"  {'Cap Limit:':<20} {format_money(salary_cap)}")
    print(f"  {'Accounting:':<20} {mode}")
    if cap_row:
        print(f"  {'Top 51 Players:':<20} {format_money(cap_row['top51_cap_hit'] or 0)}")
    print(f"  {'Total Committed:':<20} {format_money(committed)}  ({pct:.1f}%)")
    print(f"  {'Cap Space:':<20} {format_money(space)}")
    if cap_row:
        print(f"  {'Active Contracts:':<20} {cap_row['active_contracts']}")
        print(f"  {'Counted Contracts:':<20} {cap_row['contracts_counted']}")
        print(f"  {'Excluded Contracts:':<20} {cap_row['contracts_excluded']}")
        print(f"  {'Top 51 Cutoff:':<20} {format_money(cap_row['top51_cutoff_cap_hit'] or 0)}")
        if cap_row['other_cap_charges']:
            print(f"  {'Cap Adjustments:':<20} {format_money(cap_row['other_cap_charges'])}")

def print_roster(players):
    print("\n" + "─" * 70)
    print(f"  {'FULL ROSTER':^68}")
    print("─" * 70)

    col = f"  {'#':<4} {'NAME':<22} {'POS':<6} {'SIM':<5} {'POT':<5} {'AGE':<4} {'HT':<6} {'WT':<5} {'AAV':<10} {'THRU':<6} {'ROLE'}"
    print(col)
    print("  " + "-" * 66)

    # Position group labels — ordered to match the sort above
    pos_groups = {
        'QB':   '── QUARTERBACKS ──',
        'RB':   '── RUNNING BACKS ──',
        'FB':   '── FULLBACKS ──',
        'WR':   '── WIDE RECEIVERS ──',
        'TE':   '── TIGHT ENDS ──',
        'OT':   '── OFFENSIVE TACKLES ──',
        'OG':   '── OFFENSIVE GUARDS ──',
        'C':    '── CENTERS ──',
        'EDGE': '── EDGE RUSHERS ──',
        'IDL':  '── INTERIOR D-LINE ──',
        'ILB':  '── INSIDE LINEBACKERS ──',
        'OLB':  '── OUTSIDE LINEBACKERS ──',
        'CB':   '── CORNERBACKS ──',
        'NB':   '── NICKEL BACKS ──',
        'SS':   '── STRONG SAFETIES ──',
        'FS':   '── FREE SAFETIES ──',
        'K':    '── SPECIALISTS ──',
        'P':    '── SPECIALISTS ──',
        'LS':   '── SPECIALISTS ──',
    }

    current_pos_group = None
    specialist_printed = False

    for p in players:
        pos = p['position']

        # Handle specialists as one group
        if pos in ('K', 'P', 'LS'):
            if not specialist_printed:
                print(f"\n  ── SPECIALISTS ──")
                specialist_printed = True
        elif pos != current_pos_group:
            label = pos_groups.get(pos, f'── {pos} ──')
            print(f"\n  {label}")
            current_pos_group = pos

        num   = f"#{p['jersey_number']}" if p['jersey_number'] is not None else "—"
        name  = f"{p['first_name']} {p['last_name']}"
        ht    = format_height(p['height_in'])
        aav   = format_money(p['aav'])
        thru  = str(p['end_year']) if p['end_year'] else "—"
        role = p['sim_role'] or "—"

        print(f"  {num:<4} {name:<22} {pos:<6} {int(p['sim_rating'] or 50):<5} {p['potential']:<5} "
              f"{p['age'] or '—':<4} {ht:<6} {p['weight_lbs'] or '—':<5} "
              f"{aav:<10} {thru:<6} {role}")

def print_depth_chart(rows):
    print("\n" + "─" * 70)
    print(f"  {'DEPTH CHART':^68}")
    print("─" * 70)

    current_unit = None
    current_pos  = None
    # Track all (unit, position) combos we've already opened
    opened = set()

    for r in rows:
        if r['unit'] != current_unit:
            current_unit = r['unit']
            current_pos  = None
            print(f"\n  ◆ {current_unit.upper()}")

        key = (r['unit'], r['position'])
        if key not in opened:
            opened.add(key)
            current_pos = r['position']
            print(f"\n    {current_pos}")

        num  = f"#{r['jersey_number']}" if r['jersey_number'] else " "
        name = f"{r['first_name']} {r['last_name']}"
        print(f"      {r['depth_rank']}. {num:<4} {name:<24} SIM: {int(r['sim_rating'] or 50)}  Age: {r['age']}")

def print_flex(rows):
    if not rows:
        return

    print("\n" + "─" * 70)
    print(f"  {'POSITION FLEXIBILITY (secondary positions only)':^68}")
    print("─" * 70)
    print(f"  {'NAME':<24} {'PRIMARY':<8} {'FLEX POS':<10} {'EXP':>4} {'POT':>4}  SOURCE")
    print("  " + "-" * 62)

    for r in rows:
        name = f"{r['first_name']} {r['last_name']}"
        print(f"  {name:<24} {r['primary_pos']:<8} {r['flex_pos']:<10} "
              f"{r['experience']:>4} {r['potential']:>4}  {r['source']}")

def view_team(abbr):
    conn = get_connection()
    cursor = conn.cursor()

    team = get_team(cursor, abbr)
    if not team:
        print(f"\n❌ Team '{abbr}' not found.")
        conn.close()
        return

    team_id = team['team_id']
    players = get_roster(cursor, team_id)
    depth   = get_depth_chart(cursor, team_id)
    flex    = get_flex(cursor, team_id)
    cap     = get_cap(cursor, team_id)

    print_header(team)
    print_cap_summary(team, cap)
    print_roster(players)
    print_depth_chart(depth)
    print_flex(flex)

    print("\n" + "═" * 70)
    print(f"  {len(players)} players on roster")
    print("═" * 70 + "\n")

    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("\nUsage: python tools/view_team.py <TEAM_ABBR>")
        sys.exit(1)
    view_team(sys.argv[1])
