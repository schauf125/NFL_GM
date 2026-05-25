"""Generate viewable draft-class preview rows."""

from __future__ import annotations

import csv
import html
import json
import random
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .appearance import AppearanceGenerator
from .attributes import DraftAttributeGenerator
from .combine import CombineGenerator
from .college import CollegeGenerator
from .hometown import HometownGenerator
from .names import GeneratedName, NameGenerator, UNITED_STATES
from .physical import PhysicalProfileGenerator, PhysicalTraits, format_height, format_measurement
from .scouting import ScoutingReportGenerator
from .workouts import PrivateWorkoutGenerator, ProDayGenerator
from engine.special_teams_flex import SPECIAL_TEAMS_FLEX_LABELS, flex_json_for_profile
from engine.specialist_behavior import generated_specialist_behavior_profile


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "draft" / "generated"
DEFAULT_PREVIEW_CONFIG = ROOT / "data" / "draft" / "generation" / "preview_config.json"


def _load_preview_config(path: Path = DEFAULT_PREVIEW_CONFIG) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Draft preview config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


PREVIEW_CONFIG = _load_preview_config()

POSITION_WEIGHTS: dict[str, float] = {
    "QB": 6.0,
    "RB": 8.0,
    "FB": 1.0,
    "WR": 16.0,
    "TE": 7.0,
    "OT": 9.0,
    "OG": 7.0,
    "C": 4.0,
    "IDL": 11.0,
    "EDGE": 11.0,
    "ILB": 7.0,
    "CB": 12.0,
    "NB": 2.0,
    "FS": 4.0,
    "SS": 4.0,
    "K": 1.0,
    "P": 1.0,
    "LS": 0.5,
}

POSITION_BUCKET_END_RANKS = {
    "round_1": 32,
    "round_2_3": 96,
    "round_4_5": 160,
    "round_6_7": 256,
}

POSITION_WEIGHTS_BY_BUCKET: dict[str, dict[str, float]] = {
    "round_1": {
        "QB": 9.0,
        "RB": 5.0,
        "WR": 13.0,
        "TE": 5.0,
        "OT": 12.0,
        "OG": 5.0,
        "C": 3.0,
        "IDL": 9.0,
        "EDGE": 13.0,
        "ILB": 4.0,
        "CB": 12.0,
        "NB": 1.0,
        "FS": 3.0,
        "SS": 3.0,
        "FB": 0.2,
    },
    "round_2_3": {
        "QB": 5.0,
        "RB": 8.0,
        "FB": 0.5,
        "WR": 16.0,
        "TE": 8.0,
        "OT": 10.0,
        "OG": 8.0,
        "C": 5.0,
        "IDL": 11.0,
        "EDGE": 12.0,
        "ILB": 8.0,
        "CB": 14.0,
        "NB": 2.0,
        "FS": 5.0,
        "SS": 5.0,
        "K": 0.2,
        "P": 0.2,
        "LS": 0.1,
    },
    "round_4_5": {
        "QB": 4.0,
        "RB": 8.0,
        "FB": 1.0,
        "WR": 15.0,
        "TE": 8.0,
        "OT": 9.0,
        "OG": 8.0,
        "C": 5.0,
        "IDL": 11.0,
        "EDGE": 10.0,
        "ILB": 8.0,
        "CB": 13.0,
        "NB": 2.5,
        "FS": 5.0,
        "SS": 5.0,
        "K": 0.7,
        "P": 0.7,
        "LS": 0.3,
    },
    "round_6_7": {
        "QB": 3.0,
        "RB": 8.0,
        "FB": 1.5,
        "WR": 14.0,
        "TE": 7.0,
        "OT": 8.0,
        "OG": 7.0,
        "C": 5.0,
        "IDL": 10.0,
        "EDGE": 9.0,
        "ILB": 8.0,
        "CB": 12.0,
        "NB": 3.0,
        "FS": 5.0,
        "SS": 5.0,
        "K": 1.5,
        "P": 1.5,
        "LS": 1.0,
    },
    "leftover": POSITION_WEIGHTS,
}

GENERATION_VERSION = str(PREVIEW_CONFIG["generation_version"])
POSITION_GROUPS: dict[str, str] = {
    str(key): str(value)
    for key, value in dict(PREVIEW_CONFIG["position_groups"]).items()
}
POSITION_ETHNICITY_MULTIPLIERS: dict[str, dict[str, float]] = {
    str(position): {str(key): float(value) for key, value in weights.items()}
    for position, weights in dict(PREVIEW_CONFIG["position_ethnicity_multipliers"]).items()
}
POSITION_ETHNICITY_ASSIGNMENT_PRIORITY: dict[str, int] = {
    str(position): int(priority)
    for position, priority in dict(
        PREVIEW_CONFIG["position_ethnicity_assignment_priority"]
    ).items()
}

COUNTRY_TIER_WEIGHTS: dict[str, dict[str, float]] = {
    "tier_2": {
        "Nigeria": 32,
        "Canada": 30,
        "Australia": 9,
        "Germany": 8,
        "United Kingdom": 8,
        "Ghana": 5,
        "Cameroon": 4,
        "American Samoa": 2,
        "Samoa": 2,
    },
    "tier_2_specialist": {
        "Australia": 34,
        "Canada": 20,
        "Germany": 15,
        "United Kingdom": 14,
        "Ireland": 8,
        "New Zealand": 5,
        "Netherlands": 2,
        "France": 2,
    },
    "tier_3": {
        "Germany": 30,
        "United Kingdom": 28,
        "Australia": 26,
        "Canada": 6,
        "France": 3,
        "Nigeria": 2,
        "Netherlands": 2,
        "Ireland": 2,
        "New Zealand": 1,
    },
    "tier_4": {
        "American Samoa": 13,
        "Samoa": 11,
        "Tonga": 10,
        "Ghana": 10,
        "Cameroon": 8,
        "Mexico": 8,
        "New Zealand": 8,
        "Japan": 6,
        "Philippines": 5,
        "India": 4,
        "Brazil": 5,
        "Jamaica": 5,
        "France": 4,
        "Netherlands": 3,
        "Germany": 2,
        "United Kingdom": 2,
    },
}

COUNTRY_POSITION_MULTIPLIERS: dict[str, dict[str, float]] = {
    str(country): {str(key): float(value) for key, value in weights.items()}
    for country, weights in dict(PREVIEW_CONFIG["country_position_multipliers"]).items()
}
HANDEDNESS_WEIGHTS: dict[str, dict[str, float]] = {
    str(position): {str(key): float(value) for key, value in weights.items()}
    for position, weights in dict(PREVIEW_CONFIG["handedness_weights"]).items()
}

INTERNATIONAL_CHANCE_FACTORS: dict[str, float] = {
    "tier_1": 0.10,
    "tier_2": 0.45,
    "tier_3": 0.65,
    "tier_4": 1.80,
}

DEFAULT_HIDDEN_PROSPECT_MIN = 36
DEFAULT_HIDDEN_PROSPECT_MAX = 44
HIDDEN_DISCOVERY_PROFILE = "hidden_unlisted"
PUBLIC_DISCOVERY_PROFILE = "public_board"
HIDDEN_COLLEGE_TIER_WEIGHTS = {
    "Small": 75.0,
    "Regular": 2.5,
    "Power": 0.15,
}
HIDDEN_TALENT_RANK_BUCKETS: tuple[tuple[int, int, float], ...] = (
    # Off-public-board players should mostly feel like late draft/priority
    # UDFA names, with just enough variance for an area scout to uncover a
    # real day-three gem.
    (80, 102, 0.2),
    (103, 140, 13.0),
    (141, 178, 24.0),
    (179, 216, 32.0),
    (217, 256, 28.0),
    (257, 310, 2.8),
)

PUBLIC_COLLEGE_TIER_WEIGHTS_BY_BUCKET = {
    "round_1": {"Power": 5.0, "Regular": 0.12, "Small": 0.006},
    "round_2_3": {"Power": 3.0, "Regular": 0.45, "Small": 0.035},
    "round_4_5": {"Power": 1.8, "Regular": 0.78, "Small": 0.14},
    "round_6_7": {"Power": 0.80, "Regular": 1.20, "Small": 1.00},
    "leftover": {"Power": 0.40, "Regular": 1.50, "Small": 2.50},
}

DISPLAY_NICKNAMES: tuple[tuple[str, float], ...] = (
    ("Ace", 8.0),
    ("Bam", 4.0),
    ("Boss", 3.0),
    ("Deuce", 5.0),
    ("Jet", 6.0),
    ("Kool-Aid", 1.0),
    ("Smoke", 4.0),
    ("Tank", 7.0),
    ("Flash", 2.0),
)

FOOTBALL_FAMILY_TYPES: tuple[tuple[str, float], ...] = (
    ("father_college", 30.0),
    ("older_brother_fbs", 24.0),
    ("uncle_pro_camp", 18.0),
    ("coach_family", 16.0),
    ("multi_sport_family", 12.0),
)

NAME_CONTEXT_ETHNICITY_NOTES: dict[str, str] = {
    "polynesian": "Polynesian family background lines up with the hometown and regional profile.",
    "hawaiian": "Hawaii/Polynesian naming background gives scouts an extra hometown context note.",
    "hispanic_latino": "Spanish-language naming background; hometown and college context may matter in regional scouting.",
    "african_west": "West African naming background; teams often log pronunciation and family context during interviews.",
    "asian_east": "East Asian naming background; teams may add pronunciation notes during spring visits.",
    "asian_south": "South Asian naming background; teams may add pronunciation notes during spring visits.",
    "caribbean": "Caribbean naming background adds a little more regional context.",
    "native": "Indigenous/Native naming background; regional context is a useful cross-check.",
}

COUNTRY_NAME_CONTEXT_NOTES: dict[str, str] = {
    "American Samoa": "American Samoa pipeline; evaluators tend to cross-check family football background and relocation path.",
    "Samoa": "Samoa pipeline; evaluators tend to cross-check family football background and relocation path.",
    "Tonga": "Tonga pipeline; evaluators tend to cross-check family football background and relocation path.",
    "Australia": "Australian pathway; pronunciation and prior-sport background often show up in visit notes.",
    "Nigeria": "Nigerian background; scouts usually log pronunciation and family relocation context.",
    "Ghana": "Ghanaian background; scouts usually log pronunciation and family relocation context.",
    "Mexico": "Mexican background; regional scouting may add more context than public media.",
    "Canada": "Canadian development path; name/background notes are usually cleaner than most international files.",
}


@dataclass(frozen=True)
class DraftClassPreviewRow:
    rank: int
    true_rank: int
    public_board_rank: int | None
    scouting_rank: int | None
    public_board_status: str
    discovery_status: str
    scouting_variance: int
    discovery_notes: str
    development_pathway: str
    pipeline_note: str
    display_name: str
    preferred_name: str
    name_pronunciation_note: str
    name_background_note: str
    family_football_type: str
    family_football_background: str
    name_storyline_note: str
    draft_year: int
    first_name: str
    last_name: str
    full_name: str
    position: str
    position_group: str
    age: int
    college: str
    college_tier: str
    hometown: str
    hometown_city: str
    hometown_state: str
    hometown_region: str
    height: str
    height_in: int
    weight_lbs: int
    arm_length: str
    arm_length_in: float
    hand_size: str
    hand_size_in: float
    handedness: str
    combine_status: str
    combine_note: str
    combine_grade: int | None
    athletic_score: int | None
    drills_completed: int
    drills_skipped: str
    workout_variance: str
    combine_summary: str
    forty_yard_dash: float | None
    ten_yard_split: float | None
    bench_press_reps: int | None
    vertical_jump_in: float | None
    broad_jump_in: int | None
    three_cone_sec: float | None
    twenty_yard_shuttle_sec: float | None
    sixty_yard_shuttle_sec: float | None
    combine_injured: bool
    combine_top_skip: bool
    pro_day_status: str
    pro_day_note: str
    pro_day_grade: int | None
    pro_day_athletic_score: int | None
    pro_day_drills_completed: int
    pro_day_drills_skipped: str
    pro_day_workout_variance: str
    pro_day_summary: str
    pro_day_improved_from_combine: bool
    pro_day_medical_recheck: bool
    pro_day_forty_yard_dash: float | None
    pro_day_ten_yard_split: float | None
    pro_day_bench_press_reps: int | None
    pro_day_vertical_jump_in: float | None
    pro_day_broad_jump_in: int | None
    pro_day_three_cone_sec: float | None
    pro_day_twenty_yard_shuttle_sec: float | None
    pro_day_sixty_yard_shuttle_sec: float | None
    private_workout_status: str
    private_workout_type: str
    private_workout_interest: str
    private_workout_grade: int | None
    private_workout_note: str
    medical_flag: str
    medical_risk: str
    medical_notes: str
    interview_trait: str
    interview_grade: int | None
    interview_notes: str
    late_process_status: str
    late_process_note: str
    public_board_delta: int
    archetype: str
    original_archetype: str
    archetype_identity_status: str
    archetype_identity_note: str
    true_grade: int
    ceiling_grade: int
    dev_trait: str
    risk_level: str
    projected_round: int | None
    projected_pick: int | None
    primary_role: str
    secondary_role: str
    primary_role_score: float | None
    secondary_role_score: float | None
    ratings: dict[str, int]
    role_scores: dict[str, float]
    special_teams_flex: dict[str, dict[str, object]]
    top_ratings: str
    weak_ratings: str
    scout_lens: str
    scout_confidence: str
    scout_grade: int
    scout_ceiling: int
    scout_risk: str
    scouting_summary: str
    scouting_strengths: str
    scouting_concerns: str
    scouting_projection: str
    scouting_report: str
    ethnicity_key: str
    ethnicity: str
    primary_ethnicity: str
    secondary_ethnicity: str
    origin_ethnicity_key: str
    birth_country: str
    is_international: bool
    generation_version: str
    eye_color: str
    hair_color: str
    hairstyle: str
    facial_hair: str
    skin_tone: str
    complexion: str
    face_shape: str
    jawline: str
    brow_profile: str
    nose_profile: str
    smile_profile: str
    media_style: str
    accessory_style: str
    has_mustache: bool
    has_beard: bool
    photo_prompt_traits: str
    physical_outlier: bool
    hairstyle_outlier: bool
    facial_hair_outlier: bool


class DraftClassPreviewGenerator:
    """Generate a draft class preview with normalized sim-rating summaries."""

    def __init__(self, *, seed: str | int | None = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.name_generator = NameGenerator(seed=f"{seed}:names")
        self.physical_generator = PhysicalProfileGenerator(seed=f"{seed}:physical")
        self.appearance_generator = AppearanceGenerator(seed=f"{seed}:appearance")
        self.college_generator = CollegeGenerator(seed=f"{seed}:college")
        self.hometown_generator = HometownGenerator(seed=f"{seed}:hometown")
        self.attribute_generator = DraftAttributeGenerator(seed=f"{seed}:attributes")
        self.combine_generator = CombineGenerator(seed=f"{seed}:combine")
        self.pro_day_generator = ProDayGenerator(seed=f"{seed}:pro-day")
        self.private_workout_generator = PrivateWorkoutGenerator(seed=f"{seed}:private-workouts")
        self.scouting_generator = ScoutingReportGenerator(seed=f"{seed}:scouting")

    def generate(
        self,
        *,
        draft_year: int,
        count: int = 310,
        hidden_count: int | None = None,
        hidden_min: int = DEFAULT_HIDDEN_PROSPECT_MIN,
        hidden_max: int = DEFAULT_HIDDEN_PROSPECT_MAX,
        international_chance: float = 0.05,
        physical_outlier_chance: float = 0.045,
        class_strength: int = 50,
    ) -> list[DraftClassPreviewRow]:
        if hidden_count is None:
            if hidden_min > hidden_max:
                hidden_min, hidden_max = hidden_max, hidden_min
            hidden_count = self.rng.randint(hidden_min, hidden_max)
        hidden_count = max(0, hidden_count)

        public_positions = self._position_list(count)
        hidden_positions = self._position_bucket_list("leftover", hidden_count) if hidden_count else []
        positions = public_positions + hidden_positions
        total_count = len(positions)
        birth_countries = [
            self._birth_country_for_rank(
                rank=index,
                position=position,
                base_international_chance=international_chance,
            )
            for index, position in enumerate(positions, start=1)
        ]
        ethnicity_keys = self._ethnicity_key_list(positions, birth_countries)
        ages = self.college_generator.ranked_age_plan(count)
        if hidden_count:
            ages.extend(self._hidden_age_plan(hidden_count))
        rows: list[DraftClassPreviewRow] = []
        position_rank_counts: Counter[str] = Counter()
        for index, position in enumerate(positions, start=1):
            is_hidden = index > count
            evaluation_rank = self._hidden_talent_rank() if is_hidden else index
            discovery_profile = HIDDEN_DISCOVERY_PROFILE if is_hidden else PUBLIC_DISCOVERY_PROFILE
            position_rank_counts[position] += 1
            position_rank = position_rank_counts[position]
            birth_country = birth_countries[index - 1]
            ethnicity_key = ethnicity_keys[index - 1]
            origin_ethnicity_key = (
                self.name_generator.ethnicity_key_for_country(birth_country)
                or ethnicity_key
            )
            ethnicity_label = str(
                self.name_generator.ethnicity_profiles.get(ethnicity_key, {}).get("label")
                or ethnicity_key
            )
            handedness = self._choose_handedness(position)
            college = self.college_generator.generate(
                rank=index,
                is_international=birth_country != UNITED_STATES,
                age=ages[index - 1],
                tier_weights=HIDDEN_COLLEGE_TIER_WEIGHTS
                if is_hidden
                else self._college_tier_weights_for_rank(index),
                position=position,
            )
            development_pathway, pipeline_note = self._development_pathway(
                position=position,
                birth_country=birth_country,
                is_international=birth_country != UNITED_STATES,
                college=college.college,
                college_tier=college.college_tier,
                rank=index,
            )
            physical = self.physical_generator.generate(
                position,
                outlier_chance=physical_outlier_chance,
            )
            physical = self._apply_pathway_physical_flavor(
                physical=physical,
                position=position,
                birth_country=birth_country,
                development_pathway=development_pathway,
            )
            hometown = self.hometown_generator.generate(
                college=college.college,
                college_tier=college.college_tier,
                birth_country=birth_country,
                is_international=birth_country != UNITED_STATES,
                ethnicity_key=ethnicity_key,
                ethnicity_label=ethnicity_label,
                origin_ethnicity_key=origin_ethnicity_key,
            )
            generated_name = self.name_generator.generate(
                ethnicity_key=ethnicity_key,
                country=birth_country,
                position=position,
                hometown_state=hometown.state,
                hometown_region=hometown.region,
                international_chance=0.0,
            )
            name_metadata = self._name_metadata(
                generated_name=generated_name,
                position=position,
                rank=index,
                college=college.college,
                college_tier=college.college_tier,
                hometown_state=hometown.state,
                hometown_region=hometown.region,
                development_pathway=development_pathway,
            )
            appearance = self.appearance_generator.generate(
                ethnicity_key=generated_name.ethnicity_key,
                ethnicity_label=generated_name.ethnicity_label,
                name_culture_styles=self.name_generator.culture_styles_for_name(
                    first_name=generated_name.first_name,
                    last_name=generated_name.last_name,
                ),
                position=position,
                age=college.age,
                birth_country=generated_name.country,
                is_international=generated_name.is_international,
                college_tier=college.college_tier,
                rank=index,
            )
            attributes = self.attribute_generator.generate(
                position=position,
                rank=evaluation_rank,
                age=college.age,
                height_in=physical.height_in,
                weight_lbs=physical.weight_lbs,
                arm_length_in=physical.arm_length_in,
                hand_size_in=physical.hand_size_in,
                handedness=handedness,
                class_strength=class_strength,
                talent_profile=discovery_profile,
            )
            combine = self.combine_generator.generate(
                position=position,
                rank=evaluation_rank,
                position_rank=position_rank,
                height_in=physical.height_in,
                weight_lbs=physical.weight_lbs,
                attributes=attributes,
                invitation_profile=discovery_profile,
            )
            pro_day = self.pro_day_generator.generate(
                position=position,
                rank=evaluation_rank,
                height_in=physical.height_in,
                weight_lbs=physical.weight_lbs,
                attributes=attributes,
                combine=combine,
                college_tier=college.college_tier,
            )
            private_workout = self.private_workout_generator.generate(
                position=position,
                rank=evaluation_rank,
                college_tier=college.college_tier,
                attributes=attributes,
                combine=combine,
                pro_day=pro_day,
            )
            medical_flag, medical_risk, medical_notes = self._medical_profile(
                attributes=attributes,
                combine=combine,
                pro_day=pro_day,
            )
            interview_trait, interview_grade, interview_notes = self._interview_profile(
                position=position,
                attributes=attributes,
                private_workout=private_workout,
            )
            scouting_report = self.scouting_generator.generate(
                name=generated_name.full_name,
                position=position,
                rank=index,
                attributes=attributes,
                lens_key=self.scouting_generator.choose_lens_key(
                    evaluation_rank,
                    position,
                    discovery_profile=discovery_profile,
                    college_tier=college.college_tier,
                ),
                discovery_profile=discovery_profile,
                college_tier=college.college_tier,
            )
            special_teams_flex = self._special_teams_flex(
                position=position,
                attributes=attributes,
                age=college.age,
                draft_year=draft_year,
                rank=index,
                college_tier=college.college_tier,
                discovery_profile=discovery_profile,
            )
            special_teams_note = self._special_teams_scouting_note(
                position=position,
                rank=index,
                college_tier=college.college_tier,
                discovery_profile=discovery_profile,
                flex=special_teams_flex,
            )
            special_teams_story = self._special_teams_storyline_note(
                rank=index,
                college_tier=college.college_tier,
                discovery_profile=discovery_profile,
                flex=special_teams_flex,
            )
            scouting_summary = self._append_sentence(
                self._append_pathway_summary(
                    self._append_archetype_summary(
                        scouting_report.summary,
                        archetype=attributes.archetype,
                        primary_role=attributes.primary_role,
                        secondary_role=attributes.secondary_role,
                        identity_status=attributes.archetype_identity_status,
                    ),
                    development_pathway=development_pathway,
                    pipeline_note=pipeline_note,
                    birth_country=generated_name.country,
                    college_tier=college.college_tier,
                ),
                special_teams_note,
            )
            scouting_full_report = self._append_sentence(scouting_report.full_text, special_teams_note)
            discovery_notes = self._append_sentence(
                self._discovery_notes(
                    college_tier=college.college_tier,
                    discovery_profile=discovery_profile,
                    development_pathway=development_pathway,
                    pipeline_note=pipeline_note,
                    birth_country=generated_name.country,
                    is_international=generated_name.is_international,
                ),
                special_teams_story,
            )
            origin_ethnicity_key = (
                self.name_generator.ethnicity_key_for_country(generated_name.country)
                or generated_name.ethnicity_key
            )
            rows.append(
                DraftClassPreviewRow(
                    rank=index,
                    true_rank=index,
                    public_board_rank=None if is_hidden else index,
                    scouting_rank=None if is_hidden else index,
                    public_board_status="off_public_board" if is_hidden else "ranked",
                    discovery_status="undiscovered" if is_hidden else "public_board",
                    scouting_variance=self._scouting_variance_score(
                        rank=index,
                        college_tier=college.college_tier,
                        scout_confidence=scouting_report.scout_confidence,
                        discovery_profile=discovery_profile,
                    ),
                    discovery_notes=discovery_notes,
                    development_pathway=development_pathway,
                    pipeline_note=pipeline_note,
                    display_name=name_metadata["display_name"],
                    preferred_name=name_metadata["preferred_name"],
                    name_pronunciation_note=name_metadata["name_pronunciation_note"],
                    name_background_note=name_metadata["name_background_note"],
                    family_football_type=name_metadata["family_football_type"],
                    family_football_background=name_metadata["family_football_background"],
                    name_storyline_note="",
                    draft_year=draft_year,
                    first_name=generated_name.first_name,
                    last_name=generated_name.last_name,
                    full_name=generated_name.full_name,
                    position=position,
                    position_group=self._position_group(position),
                    age=college.age,
                    college=college.college,
                    college_tier=college.college_tier,
                    hometown=hometown.label,
                    hometown_city=hometown.city,
                    hometown_state=hometown.state,
                    hometown_region=hometown.region,
                    height=format_height(physical.height_in),
                    height_in=physical.height_in,
                    weight_lbs=physical.weight_lbs,
                    arm_length=format_measurement(physical.arm_length_in),
                    arm_length_in=physical.arm_length_in,
                    hand_size=format_measurement(physical.hand_size_in),
                    hand_size_in=physical.hand_size_in,
                    handedness=handedness,
                    combine_status=combine.status,
                    combine_note=combine.participation_note,
                    combine_grade=combine.combine_grade,
                    athletic_score=combine.athletic_score,
                    drills_completed=combine.drills_completed,
                    drills_skipped=combine.drills_skipped,
                    workout_variance=combine.workout_variance,
                    combine_summary=combine.summary,
                    forty_yard_dash=combine.forty_yard_dash,
                    ten_yard_split=combine.ten_yard_split,
                    bench_press_reps=combine.bench_press_reps,
                    vertical_jump_in=combine.vertical_jump_in,
                    broad_jump_in=combine.broad_jump_in,
                    three_cone_sec=combine.three_cone_sec,
                    twenty_yard_shuttle_sec=combine.twenty_yard_shuttle_sec,
                    sixty_yard_shuttle_sec=combine.sixty_yard_shuttle_sec,
                    combine_injured=combine.is_injured,
                    combine_top_skip=combine.is_top_skip,
                    pro_day_status=pro_day.status,
                    pro_day_note=pro_day.participation_note,
                    pro_day_grade=pro_day.pro_day_grade,
                    pro_day_athletic_score=pro_day.athletic_score,
                    pro_day_drills_completed=pro_day.drills_completed,
                    pro_day_drills_skipped=pro_day.drills_skipped,
                    pro_day_workout_variance=pro_day.workout_variance,
                    pro_day_summary=pro_day.summary,
                    pro_day_improved_from_combine=pro_day.improved_from_combine,
                    pro_day_medical_recheck=pro_day.medical_recheck,
                    pro_day_forty_yard_dash=pro_day.forty_yard_dash,
                    pro_day_ten_yard_split=pro_day.ten_yard_split,
                    pro_day_bench_press_reps=pro_day.bench_press_reps,
                    pro_day_vertical_jump_in=pro_day.vertical_jump_in,
                    pro_day_broad_jump_in=pro_day.broad_jump_in,
                    pro_day_three_cone_sec=pro_day.three_cone_sec,
                    pro_day_twenty_yard_shuttle_sec=pro_day.twenty_yard_shuttle_sec,
                    pro_day_sixty_yard_shuttle_sec=pro_day.sixty_yard_shuttle_sec,
                    private_workout_status=private_workout.status,
                    private_workout_type=private_workout.workout_type,
                    private_workout_interest=private_workout.interest_level,
                    private_workout_grade=private_workout.outcome_grade,
                    private_workout_note=private_workout.note,
                    medical_flag=medical_flag,
                    medical_risk=medical_risk,
                    medical_notes=medical_notes,
                    interview_trait=interview_trait,
                    interview_grade=interview_grade,
                    interview_notes=interview_notes,
                    late_process_status="Stable",
                    late_process_note="Initial public board placement.",
                    public_board_delta=0,
                    archetype=attributes.archetype,
                    original_archetype=attributes.original_archetype,
                    archetype_identity_status=attributes.archetype_identity_status,
                    archetype_identity_note=attributes.archetype_identity_note,
                    true_grade=attributes.true_grade,
                    ceiling_grade=attributes.ceiling_grade,
                    dev_trait=attributes.dev_trait,
                    risk_level=attributes.risk_level,
                    projected_round=None,
                    projected_pick=None,
                    primary_role=attributes.primary_role,
                    secondary_role=attributes.secondary_role,
                    primary_role_score=attributes.primary_role_score,
                    secondary_role_score=attributes.secondary_role_score,
                    ratings=dict(attributes.ratings),
                    role_scores=dict(attributes.role_scores),
                    special_teams_flex=special_teams_flex,
                    top_ratings=attributes.top_ratings,
                    weak_ratings=attributes.weak_ratings,
                    scout_lens=scouting_report.scout_label,
                    scout_confidence=scouting_report.scout_confidence,
                    scout_grade=scouting_report.perceived_grade,
                    scout_ceiling=scouting_report.perceived_ceiling,
                    scout_risk=scouting_report.perceived_risk,
                    scouting_summary=scouting_summary,
                    scouting_strengths=scouting_report.strengths_text,
                    scouting_concerns=scouting_report.concerns_text,
                    scouting_projection=scouting_report.projection,
                    scouting_report=scouting_full_report,
                    ethnicity_key=generated_name.ethnicity_key,
                    ethnicity=appearance.ethnicity_note,
                    primary_ethnicity=appearance.primary_ethnicity_label,
                    secondary_ethnicity=appearance.secondary_ethnicity_label or "",
                    origin_ethnicity_key=origin_ethnicity_key,
                    birth_country=generated_name.country,
                    is_international=generated_name.is_international,
                    generation_version=GENERATION_VERSION,
                    eye_color=appearance.eye_color,
                    hair_color=appearance.hair_color,
                    hairstyle=appearance.hairstyle,
                    facial_hair=appearance.facial_hair_style,
                    skin_tone=appearance.skin_tone,
                    complexion=appearance.complexion,
                    face_shape=appearance.face_shape,
                    jawline=appearance.jawline,
                    brow_profile=appearance.brow_profile,
                    nose_profile=appearance.nose_profile,
                    smile_profile=appearance.smile_profile,
                    media_style=appearance.media_style,
                    accessory_style=appearance.accessory_style,
                    has_mustache=appearance.has_mustache,
                    has_beard=appearance.has_beard,
                    photo_prompt_traits=appearance.photo_prompt_traits,
                    physical_outlier=physical.is_outlier,
                    hairstyle_outlier=appearance.is_hairstyle_outlier,
                    facial_hair_outlier=appearance.is_facial_hair_outlier,
                )
            )
        rows = self._apply_name_storylines(rows)
        return self._apply_public_board(rows)

    def _name_metadata(
        self,
        *,
        generated_name: GeneratedName,
        position: str,
        rank: int,
        college: str,
        college_tier: str,
        hometown_state: str,
        hometown_region: str,
        development_pathway: str,
    ) -> dict[str, str]:
        preferred_name = generated_name.first_name
        display_name = generated_name.full_name
        background_notes: list[str] = []

        first_source = generated_name.first_source.lower()
        if "distinctive_flair" in first_source:
            background_notes.append(
                f"Distinctive given name; area scouts may hear more local story than public-board detail."
            )
        elif generated_name.first_name.endswith("."):
            background_notes.append("Uses initials publicly; full given name is not emphasized in the draft file.")
        else:
            nickname_chance = 0.012
            if position.upper() in {"RB", "WR", "CB", "NB", "EDGE", "SS"}:
                nickname_chance += 0.005
            if rank <= 80:
                nickname_chance += 0.002
            if self.rng.random() < nickname_chance:
                preferred_name = self._weighted_choice(dict(DISPLAY_NICKNAMES))
                display_name = f"{preferred_name} {generated_name.last_name}"
                background_notes.append(
                    f'Goes by "{preferred_name}" in team notes; nickname traces back to high school or family.'
                )

        context_note = self._name_context_note(
            generated_name=generated_name,
            hometown_state=hometown_state,
            hometown_region=hometown_region,
        )
        if context_note:
            background_notes.append(context_note)

        family_type, family_note = self._football_family_profile(
            generated_name=generated_name,
            position=position,
            rank=rank,
            college=college,
            college_tier=college_tier,
            development_pathway=development_pathway,
        )

        pronunciation_note = self._pronunciation_note(
            generated_name=generated_name,
            preferred_name=preferred_name,
        )

        return {
            "display_name": display_name,
            "preferred_name": preferred_name,
            "name_pronunciation_note": pronunciation_note,
            "name_background_note": " ".join(background_notes),
            "family_football_type": family_type,
            "family_football_background": family_note,
        }

    def _name_context_note(
        self,
        *,
        generated_name: GeneratedName,
        hometown_state: str,
        hometown_region: str,
    ) -> str:
        country_note = COUNTRY_NAME_CONTEXT_NOTES.get(generated_name.country)
        if country_note:
            return country_note
        key = generated_name.ethnicity_key
        if key in NAME_CONTEXT_ETHNICITY_NOTES:
            return NAME_CONTEXT_ETHNICITY_NOTES[key]
        if hometown_state == "HI" and key in {"polynesian", "hawaiian", "asian_east"}:
            return "Hawaii hometown context supports the name/background profile."
        if hometown_region == "West" and key in {"polynesian", "hawaiian", "hispanic_latino", "asian_east"}:
            return "West Coast hometown context gives the naming background a plausible regional fit."
        if hometown_region == "Texas" and key == "hispanic_latino":
            return "Texas hometown context supports the Spanish-language naming background."
        return ""

    def _football_family_profile(
        self,
        *,
        generated_name: GeneratedName,
        position: str,
        rank: int,
        college: str,
        college_tier: str,
        development_pathway: str,
    ) -> tuple[str, str]:
        chance = 0.055
        if rank <= 64:
            chance += 0.025
        elif rank <= 160:
            chance += 0.010
        if college_tier == "Power":
            chance += 0.012
        if development_pathway == "Football family":
            chance += 0.22
        if any(suffix in generated_name.last_name for suffix in (" Jr.", " II", " III", " IV")):
            chance += 0.04
        if position.upper() == "QB":
            chance += 0.01
        if self.rng.random() >= min(chance, 0.32):
            return "", ""

        family_type = self._weighted_choice(dict(FOOTBALL_FAMILY_TYPES))
        if family_type == "father_college":
            return family_type, "Father played college football; scouts note a more mature football household."
        if family_type == "older_brother_fbs":
            return family_type, "Older brother played FBS football, giving teams a little extra background context."
        if family_type == "uncle_pro_camp":
            return family_type, "Uncle spent time in a pro camp; teams treat it as context, not proof of translation."
        if family_type == "coach_family":
            return family_type, f"Family coaching background around {college} gives interviews a more football-literate feel."
        return family_type, "Multi-sport family background shows up in the character and development file."

    @staticmethod
    def _pronunciation_note(
        *,
        generated_name: GeneratedName,
        preferred_name: str,
    ) -> str:
        if generated_name.country != UNITED_STATES:
            return f"Confirm pronunciation during visits; {generated_name.country} background is part of the file."
        if "'" in generated_name.first_name or "'" in generated_name.last_name:
            return "Pronunciation usually gets confirmed during team interviews because of the apostrophe styling."
        if "-" in generated_name.first_name or "-" in generated_name.last_name:
            return "Hyphenated name; teams confirm preferred pronunciation and jersey-display preference."
        if generated_name.first_name.endswith("."):
            return "Uses initials publicly; confirm full-name preference during interviews."
        if preferred_name != generated_name.first_name:
            return f'Uses "{preferred_name}" publicly; confirm legal/preferred name split during onboarding.'
        return ""

    def _apply_name_storylines(self, rows: list[DraftClassPreviewRow]) -> list[DraftClassPreviewRow]:
        by_last: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            key = row.last_name.split()[0].lower()
            by_last.setdefault(key, []).append(index)

        replacements: dict[int, str] = {}
        for indexes in by_last.values():
            if len(indexes) < 2:
                continue
            state_groups: dict[str, list[int]] = {}
            for index in indexes:
                state = rows[index].hometown_state or ""
                if state:
                    state_groups.setdefault(state, []).append(index)
            for state_indexes in state_groups.values():
                if len(state_indexes) < 2:
                    continue
                if self.rng.random() < 0.18:
                    chosen = state_indexes[:2]
                    relation_note = "Same-state surname overlap; team notes list no confirmed relation."
                    if self.rng.random() < 0.20:
                        relation_note = "Same-state surname overlap with a possible distant-family note from area scouts."
                    for index in chosen:
                        replacements[index] = relation_note

        if not replacements:
            return rows
        return [
            replace(row, name_storyline_note=replacements.get(index, row.name_storyline_note))
            for index, row in enumerate(rows)
        ]

    def _apply_public_board(self, rows: list[DraftClassPreviewRow]) -> list[DraftClassPreviewRow]:
        true_rank_by_index = {
            row_index: true_rank
            for true_rank, (row_index, _row) in enumerate(
                sorted(
                    enumerate(rows),
                    key=lambda item: (
                        -self._true_talent_score(item[1]),
                        item[1].rank,
                        item[1].full_name,
                    ),
                ),
                start=1,
            )
        }
        rows = [
            replace(row, true_rank=true_rank_by_index[row_index])
            for row_index, row in enumerate(rows)
        ]

        ranked_pool = [
            row
            for row in rows
            if row.public_board_status != "off_public_board"
        ]
        hidden_pool = [
            row
            for row in rows
            if row.public_board_status == "off_public_board"
        ]
        scored = [
            (self._public_board_score(row), row)
            for row in ranked_pool
        ]
        scored.sort(key=lambda item: (-item[0], item[1].true_rank, item[1].full_name))
        public_rows: list[DraftClassPreviewRow] = []
        for public_rank, (_score, row) in enumerate(scored, start=1):
            board_delta = int((row.public_board_rank or row.rank) - public_rank)
            late_process_status, late_process_note = self._late_process_profile(row, board_delta)
            scouting_projection = self.scouting_generator._projection(public_rank)
            scouting_report = row.scouting_report.replace(
                row.scouting_projection,
                scouting_projection,
                1,
            )
            public_rows.append(
                replace(
                    row,
                    rank=public_rank,
                    public_board_rank=public_rank,
                    scouting_rank=public_rank,
                    public_board_status="ranked",
                    discovery_status="public_board",
                    projected_round=self._projected_round(public_rank),
                    projected_pick=public_rank if public_rank <= 256 else None,
                    public_board_delta=board_delta,
                    late_process_status=late_process_status,
                    late_process_note=late_process_note,
                    scouting_projection=scouting_projection,
                    scouting_report=scouting_report,
                )
            )
        hidden_rows: list[DraftClassPreviewRow] = []
        hidden_scored = sorted(
            hidden_pool,
            key=lambda row: (
                -self._hidden_board_shadow_score(row),
                row.true_rank,
                row.full_name,
            ),
        )
        for hidden_index, row in enumerate(hidden_scored, start=1):
            scouting_projection = self._hidden_projection(row)
            scouting_report = row.scouting_report.replace(
                row.scouting_projection,
                scouting_projection,
                1,
            )
            hidden_rows.append(
                replace(
                    row,
                    rank=len(public_rows) + hidden_index,
                    public_board_rank=None,
                    scouting_rank=None,
                    public_board_status="off_public_board",
                    discovery_status="undiscovered",
                    projected_round=None,
                    projected_pick=None,
                    public_board_delta=0,
                    late_process_status="Area scout watch",
                    late_process_note="Off the public board; movement depends on discoveries, pro days, and team visits.",
                    scouting_projection=scouting_projection,
                    scouting_report=scouting_report,
                )
            )
        return public_rows + hidden_rows

    def _true_talent_score(self, row: DraftClassPreviewRow) -> float:
        role_score = max(row.primary_role_score or 0.0, row.secondary_role_score or 0.0)
        age_penalty = self._age_public_board_penalty(row.age, true_talent=True)
        if row.position.upper() in {"K", "P"}:
            return row.true_grade * 0.58 + row.ceiling_grade * 0.10 + role_score * 0.04 - 16.0 - age_penalty * 0.35
        if row.position.upper() == "LS":
            return row.true_grade * 0.45 + row.ceiling_grade * 0.06 + role_score * 0.03 - 24.0 - age_penalty * 0.35
        return (
            row.true_grade * 0.74
            + row.ceiling_grade * 0.18
            + role_score * 0.06
            + self._positional_value(row.position) * 0.35
            - age_penalty
        )

    def _hidden_board_shadow_score(self, row: DraftClassPreviewRow) -> float:
        workout_signal = _average_present(row.combine_grade, row.pro_day_grade, row.athletic_score)
        workout_component = 0.0 if workout_signal is None else (workout_signal - 58) * 0.08
        return (
            row.scout_grade * 0.56
            + row.scout_ceiling * 0.24
            + workout_component
            + self._positional_value(row.position) * 0.25
            + self.rng.gauss(0, max(3.5, row.scouting_variance / 16.0))
        )

    @staticmethod
    def _positional_value(position: str) -> float:
        return {
            "QB": 5.0,
            "EDGE": 3.2,
            "OT": 2.8,
            "CB": 2.6,
            "WR": 2.2,
            "IDL": 1.6,
            "TE": 1.0,
            "ILB": 0.7,
            "FS": 0.6,
            "SS": 0.5,
            "OG": 0.3,
            "C": 0.2,
            "RB": 0.1,
            "NB": -0.2,
            "FB": -2.4,
            "K": -10.0,
            "P": -10.0,
            "LS": -16.0,
        }.get(position.upper(), 0.0)

    def _public_board_score(self, row: DraftClassPreviewRow) -> float:
        positional_value = self._positional_value(row.position)
        risk_penalty = {"Low": 0.0, "Medium": 1.5, "High": 4.2}.get(row.scout_risk, 1.5)
        medical_penalty = {"Clear": 0.0, "Monitor": 1.2, "Concern": 3.4, "Red flag": 7.0}.get(row.medical_risk, 0.0)
        interview_component = self._interview_board_component(row)
        translation_penalty = self._translation_public_board_penalty(row)
        age_penalty = self._age_public_board_penalty(row.age)
        combine_signal = row.combine_grade if row.combine_grade is not None else row.athletic_score
        pro_day_signal = row.pro_day_grade if row.pro_day_grade is not None else row.pro_day_athletic_score
        workout_signal = _average_present(combine_signal, pro_day_signal)
        workout_component = 0.0 if workout_signal is None else (workout_signal - 60) * 0.12
        late_process_component = self._late_process_board_component(row)
        production_anchor = max(0.0, 28.0 - row.true_rank * 0.065)
        if row.position.upper() in {"K", "P"}:
            production_anchor *= 0.25
            workout_component *= 0.75
            late_process_component *= 0.65
        elif row.position.upper() == "LS":
            production_anchor = 0.0
            workout_component *= 0.50
            late_process_component *= 0.50
        confidence_sigma = {"High": 1.8, "Medium": 3.2, "Low": 5.2}.get(row.scout_confidence, 3.2)
        if row.true_rank > 256:
            confidence_sigma += 2.0
        board_noise = self.rng.gauss(0, confidence_sigma)
        return (
            row.scout_grade * 0.60
            + row.scout_ceiling * 0.22
            + production_anchor
            + workout_component
            + interview_component
            + late_process_component
            + positional_value
            - risk_penalty
            - medical_penalty
            - translation_penalty
            - age_penalty
            + board_noise
        )

    def _late_process_board_component(self, row: DraftClassPreviewRow) -> float:
        component = 0.0
        if row.pro_day_improved_from_combine:
            component += 1.1
        if row.pro_day_grade is not None and row.combine_grade is not None:
            component += max(-2.4, min(2.4, (row.pro_day_grade - row.combine_grade) * 0.08))
        if row.pro_day_workout_variance == "Confirmed":
            component += 0.4
        elif row.pro_day_workout_variance == "Muddy":
            component -= 0.9
        if row.college_tier == "Small":
            if row.pro_day_grade is not None and row.pro_day_grade >= 78:
                component += 1.5
            elif row.pro_day_status in {"DNP", "Limited"}:
                component -= 1.2
        if row.private_workout_grade is not None:
            component += max(-2.6, min(2.8, (row.private_workout_grade - 60) * 0.07))
        if row.medical_risk == "Concern":
            component -= 1.4
        elif row.medical_risk == "Red flag":
            component -= 3.5
        component += self._special_teams_board_component(row)
        return component

    def _special_teams_board_component(self, row: DraftClassPreviewRow) -> float:
        best_role = self._notable_special_teams_role(row.special_teams_flex)
        if not best_role:
            return 0.0
        role, current, _potential = best_role
        rank = int(row.public_board_rank or row.rank or 999)
        if rank <= 32 and role not in {"PR", "KR"}:
            return 0.0
        component = max(0.0, current - 5) * 0.22
        if row.college_tier == "Small":
            component += 0.22
        if row.public_board_status == "off_public_board":
            component += 0.18
        if role in {"PR", "KR"} and row.position.upper() in {"WR", "RB", "CB", "NB", "FS", "SS"}:
            component += 0.18
        return min(component, 1.15)

    def _development_pathway(
        self,
        *,
        position: str,
        birth_country: str,
        is_international: bool,
        college: str,
        college_tier: str,
        rank: int,
    ) -> tuple[str, str]:
        position_key = position.upper()
        if is_international or birth_country != UNITED_STATES or college_tier == "International":
            country = birth_country
            if country in {"Australia", "New Zealand", "Samoa", "Tonga", "American Samoa"}:
                options = {
                    "International Pathway": 34,
                    "Rugby convert": 28 if position_key in {"TE", "EDGE", "ILB", "SS", "FB", "OT"} else 14,
                    "Regional pipeline": 20,
                    "Late football starter": 10,
                    "Track background": 8 if position_key in {"WR", "CB", "FS", "RB"} else 2,
                }
            elif country == "Canada":
                options = {
                    "Canadian prep": 48,
                    "Regional pipeline": 24,
                    "International Pathway": 12,
                    "Late football starter": 8,
                    "Track background": 8 if position_key in {"WR", "CB", "FS", "RB"} else 2,
                }
            elif country in {"Nigeria", "Ghana", "Cameroon"}:
                options = {
                    "International Pathway": 34,
                    "Late football starter": 24,
                    "Regional pipeline": 18,
                    "Track background": 14 if position_key in {"WR", "CB", "FS", "RB", "EDGE"} else 5,
                    "Football family": 5,
                }
            elif country in {"Mexico", "Brazil", "Jamaica"}:
                options = {
                    "International Pathway": 36,
                    "Regional pipeline": 24,
                    "Late football starter": 16,
                    "Track background": 16 if position_key in {"WR", "CB", "FS", "RB"} else 6,
                    "Specialist pipeline": 10 if position_key in {"K", "P"} else 2,
                }
            elif country in {"Japan", "Philippines", "India"}:
                options = {
                    "International Pathway": 42,
                    "Specialist pipeline": 28 if position_key in {"K", "P", "LS"} else 8,
                    "Late football starter": 22,
                    "Regional pipeline": 16,
                }
            else:
                options = {
                    "International Pathway": 42,
                    "Regional pipeline": 22,
                    "Late football starter": 18,
                    "Track background": 8 if position_key in {"WR", "CB", "FS", "RB"} else 3,
                }
            pathway = self._weighted_choice(options)
            note = self._pipeline_note(
                pathway=pathway,
                country=country,
                college=college,
                college_tier=college_tier,
            )
            return pathway, note

        if college in {"Army", "Navy", "Air Force"}:
            return "Military academy", "Service-academy background adds discipline signals and a less common evaluation path."
        domestic_options = {"Traditional pipeline": 84}
        if college_tier in {"Small", "Regular"} or rank > 96:
            domestic_options["JUCO route"] = 5 if rank <= 160 else 9
            domestic_options["Late football starter"] = 3 if rank <= 96 else 6
            domestic_options["Regional pipeline"] = 4
        if position_key in {"WR", "CB", "FS", "RB", "EDGE"}:
            domestic_options["Track background"] = 4 if rank <= 96 else 7
        if rank > 64:
            domestic_options["Football family"] = 3
        pathway = self._weighted_choice(domestic_options)
        if pathway == "Traditional pipeline":
            return pathway, ""
        return pathway, self._pipeline_note(
            pathway=pathway,
            country=UNITED_STATES,
            college=college,
            college_tier=college_tier,
        )

    def _pipeline_note(
        self,
        *,
        pathway: str,
        country: str,
        college: str,
        college_tier: str,
    ) -> str:
        if pathway == "International Pathway":
            return f"{country} background gives teams a longer projection curve and more private-workout value."
        if pathway == "Rugby convert":
            return f"{country} rugby background shows in contact balance, but football instincts may need cross-checks."
        if pathway == "Canadian prep":
            return "Canadian development path gives cleaner football exposure than most international files."
        if pathway == "Track background":
            return "Track background can inflate testing, so teams will want to separate speed from play speed."
        if pathway == "Late football starter":
            return "Late football starter with more variance between tools, instincts, and future growth."
        if pathway == "Specialist pipeline":
            return f"{country} specialist path makes kicking operation and pressure context more important."
        if pathway == "JUCO route":
            return f"{college_tier.lower()}-profile JUCO route creates more year-to-year context than a normal college file."
        if pathway == "Military academy":
            return "Service-academy path adds maturity indicators, but role translation still needs careful scouting."
        if pathway == "Football family":
            return "Football-family background creates a small instincts bump without removing normal variance."
        if pathway == "Regional pipeline":
            return f"{country if country != UNITED_STATES else college} regional pipeline gives area scouts more influence than public media."
        return ""

    def _apply_pathway_physical_flavor(
        self,
        *,
        physical: PhysicalTraits,
        position: str,
        birth_country: str,
        development_pathway: str,
    ) -> PhysicalTraits:
        position_key = position.upper()
        height_delta = 0
        weight_delta = 0
        arm_delta = 0.0
        hand_delta = 0.0

        pacific = birth_country in {"Samoa", "Tonga", "American Samoa", "New Zealand", "Australia"}
        west_african = birth_country in {"Nigeria", "Ghana", "Cameroon"}
        if pacific and position_key in {"OT", "OG", "C", "IDL", "EDGE", "ILB", "TE"}:
            weight_delta += round(self.rng.gauss(10, 7))
            arm_delta += self.rng.choice([0.0, 0.125, 0.25])
            hand_delta += self.rng.choice([0.0, 0.125, 0.25])
        if birth_country in {"Australia", "New Zealand"} and position_key in {"P", "K"}:
            height_delta += 1 if self.rng.random() < 0.35 else 0
        if west_african and position_key in {"EDGE", "IDL", "OT", "ILB", "TE"}:
            height_delta += 1 if self.rng.random() < 0.30 else 0
            arm_delta += self.rng.choice([0.125, 0.25, 0.375])
        if birth_country in {"Japan", "Philippines", "India"} and position_key not in {"OT", "OG", "C", "IDL"}:
            weight_delta -= round(abs(self.rng.gauss(3, 3)))
        if development_pathway == "Track background" and position_key in {"WR", "CB", "FS", "RB"}:
            weight_delta -= round(abs(self.rng.gauss(2, 2)))
        if not any([height_delta, weight_delta, arm_delta, hand_delta]):
            return physical

        profile = self.physical_generator._profile(position_key)
        height = self.physical_generator._clamp(
            physical.height_in + height_delta,
            int(profile["gen_height_min"]),
            int(profile["gen_height_max"]),
        )
        weight = self.physical_generator._clamp(
            physical.weight_lbs + weight_delta,
            int(profile["gen_weight_min"]),
            int(profile["gen_weight_max"]),
        )
        arm_length = self.physical_generator._round_to_nearest_float(
            self.physical_generator._clamp_float(
                physical.arm_length_in + arm_delta,
                float(profile["gen_arm_length_min"]),
                float(profile["gen_arm_length_max"]),
            ),
            0.125,
        )
        hand_size = self.physical_generator._round_to_nearest_float(
            self.physical_generator._clamp_float(
                physical.hand_size_in + hand_delta,
                float(profile["gen_hand_size_min"]),
                float(profile["gen_hand_size_max"]),
            ),
            0.125,
        )
        return replace(
            physical,
            height_in=height,
            weight_lbs=weight,
            arm_length_in=arm_length,
            hand_size_in=hand_size,
            is_height_outlier=physical.is_height_outlier or abs(height - physical.height_in) >= 2,
            is_weight_outlier=physical.is_weight_outlier or abs(weight - physical.weight_lbs) >= 12,
            is_arm_outlier=physical.is_arm_outlier or abs(arm_length - physical.arm_length_in) >= 0.375,
            is_hand_outlier=physical.is_hand_outlier or abs(hand_size - physical.hand_size_in) >= 0.25,
        )

    def _append_pathway_summary(
        self,
        summary: str,
        *,
        development_pathway: str,
        pipeline_note: str,
        birth_country: str,
        college_tier: str,
    ) -> str:
        if development_pathway == "Traditional pipeline" and not pipeline_note:
            return summary
        prefix = development_pathway
        if birth_country != UNITED_STATES:
            prefix = f"{development_pathway} ({birth_country})"
        if college_tier == "International" and birth_country == UNITED_STATES:
            prefix = f"{development_pathway} (international exposure)"
        note = pipeline_note or "Evaluation path adds a little more context variance than a standard file."
        return f"{summary} {prefix}: {note}"

    @staticmethod
    def _append_archetype_summary(
        summary: str,
        *,
        archetype: str,
        primary_role: str,
        secondary_role: str,
        identity_status: str,
    ) -> str:
        if not archetype:
            return summary
        role_label = primary_role.replace("_", " ").title() if primary_role else archetype
        secondary = secondary_role.replace("_", " ").title() if secondary_role else ""
        if identity_status == "Relabeled":
            return (
                f"{summary} Best-fit archetype reads as {archetype} ({role_label}); "
                "the original label was corrected by trait fit."
            )
        if identity_status == "Illusion":
            return (
                f"{summary} Public archetype is {archetype}, but internal traits make it "
                "a boom/bust projection label."
            )
        if secondary:
            return (
                f"{summary} Archetype: {archetype}, with {role_label} as the cleanest role "
                f"and {secondary} as the secondary path."
            )
        return f"{summary} Archetype: {archetype}, with {role_label} as the cleanest role."

    def _medical_profile(self, *, attributes, combine, pro_day) -> tuple[str, str, str]:
        durability = int(attributes.ratings.get("durability", 60))
        risk_roll = self.rng.random()
        flagged = bool(combine.is_injured or pro_day.medical_recheck)
        if not flagged:
            flagged = durability < 48 and risk_roll < 0.28
        if not flagged:
            flagged = attributes.risk_level == "High" and risk_roll < 0.10
        if not flagged:
            return "Clean file", "Clear", "No major pre-draft medical flag generated."

        area = self.rng.choices(
            ["knee", "ankle", "hamstring", "shoulder", "back", "foot", "soft-tissue"],
            weights=[20, 16, 15, 14, 11, 10, 14],
            k=1,
        )[0]
        if combine.is_injured and pro_day.medical_recheck:
            risk = "Red flag" if durability < 55 or self.rng.random() < 0.35 else "Concern"
        elif durability < 45:
            risk = "Concern"
        else:
            risk = "Monitor"
        flag = f"{area.title()} flagged"
        note_map = {
            "Monitor": f"{area.title()} was noted by medical staff, but the file is more follow-up than hard downgrade.",
            "Concern": f"{area.title()} concern creates real durability variance and should matter for early picks.",
            "Red flag": f"{area.title()} red flag may push conservative teams down the board unless checks clear.",
        }
        return flag, risk, note_map[risk]

    def _interview_profile(self, *, position: str, attributes, private_workout) -> tuple[str, int | None, str]:
        processing = int(attributes.ratings.get("processing_speed", attributes.true_grade))
        recognition = int(attributes.ratings.get("play_recognition", attributes.true_grade))
        composure = int(attributes.ratings.get("composure", attributes.true_grade))
        discipline = int(attributes.ratings.get("discipline", attributes.true_grade))
        consistency = int(attributes.ratings.get("consistency", attributes.true_grade))
        base = (
            processing * 0.28
            + recognition * 0.24
            + composure * 0.18
            + discipline * 0.16
            + consistency * 0.14
        )
        if private_workout.outcome_grade is not None:
            base = base * 0.72 + private_workout.outcome_grade * 0.28
        if position.upper() == "QB":
            base += 2.0
        grade = max(25, min(95, round(base + self.rng.gauss(0, 6.0))))
        if grade >= 78:
            trait = self.rng.choice(["High football IQ", "Coachable leader", "Preparation standout", "Rapid processor"])
            note = "Private exposure gives teams a positive football-character signal."
        elif grade <= 46:
            trait = self.rng.choice(["Playbook concern", "Entitlement concern", "Adaptability question", "Coachability question"])
            note = "Interview/workout context may make teams want extra conviction before investing premium capital."
        elif grade >= 66:
            trait = self.rng.choice(["Steady interview", "Good learner", "Mature approach", "Competitive makeup"])
            note = "Interview/workout context is mildly positive without becoming a headline."
        else:
            trait = self.rng.choice(["Mixed interview", "Needs structure", "Quiet room", "Uneven whiteboard"])
            note = "Private context is mixed and should add uncertainty rather than a hard label."
        return trait, grade, note

    def _interview_board_component(self, row: DraftClassPreviewRow) -> float:
        grade = row.interview_grade
        if grade is None:
            return 0.0
        component = max(-4.5, min(4.5, (grade - 60) * 0.10))
        if row.position.upper() == "QB":
            component *= 1.45
        elif row.public_board_rank is not None and row.public_board_rank <= 64:
            component *= 1.15
        if "concern" in row.interview_trait.lower():
            component -= 1.0
        return component

    def _late_process_profile(self, row: DraftClassPreviewRow, board_delta: int) -> tuple[str, str]:
        if board_delta >= 55:
            reasons = [
                "late cross-checks created real momentum",
                "teams appear more comfortable after private exposure",
                "role fit pushed him up boards",
            ]
            if row.pro_day_improved_from_combine or (row.pro_day_grade is not None and row.pro_day_grade >= 82):
                reasons.append("pro-day testing gave teams a cleaner athletic signal")
            if row.private_workout_grade is not None and row.private_workout_grade >= 76:
                reasons.append("private workouts gave teams more conviction")
            if row.college_tier == "Small":
                reasons.append("area scouts kept pushing his small-school file up the board")
            if self._notable_special_teams_role(row.special_teams_flex):
                reasons.append("special-teams value gave coaches a cleaner active-day path")
            reason = self.rng.choice(reasons)
            return "Riser", f"Moved up about {board_delta} slots as {reason}."
        if board_delta <= -55:
            reasons = [
                "late cross-checks cooled the public-board grade",
                "teams became less convinced the traits translate cleanly",
                "decision makers became less comfortable with the range",
            ]
            if row.medical_risk in {"Concern", "Red flag"} or row.pro_day_medical_recheck:
                reasons.append("medical context introduced real uncertainty")
            if row.interview_grade is not None and row.interview_grade <= 48:
                reasons.append("interview and whiteboard feedback came back uneven")
            if row.workout_variance == "Muddy" or row.pro_day_workout_variance == "Muddy":
                reasons.append("testing signals muddied the projection")
            reason = self.rng.choice(reasons)
            return "Faller", f"Slipped about {abs(board_delta)} slots as {reason}."
        if abs(board_delta) >= 24:
            direction = "up" if board_delta > 0 else "down"
            return "Minor movement", f"Moved {direction} about {abs(board_delta)} slots during late-process cross-checks."
        return "Stable", "Late-process information mostly confirmed the early board range."

    @staticmethod
    def _age_public_board_penalty(age: int | None, *, true_talent: bool = False) -> float:
        """Keep the top of the board closer to real NFL draft-age patterns."""
        try:
            prospect_age = int(age) if age is not None else 22
        except (TypeError, ValueError):
            prospect_age = 22
        if prospect_age <= 21:
            return 0.0
        if prospect_age == 22:
            return 0.80 if true_talent else 0.60
        if prospect_age == 23:
            return 3.80 if true_talent else 3.00
        if prospect_age == 24:
            return 7.00 if true_talent else 5.80
        if prospect_age == 25:
            return 9.00 if true_talent else 7.80
        return 10.50 if true_talent else 9.00

    def _translation_public_board_penalty(self, row: DraftClassPreviewRow) -> float:
        """Make early small-school/international board jumps rarer without banning them."""
        rank = row.true_rank
        tier = row.college_tier
        penalty = 0.0
        if tier == "Small":
            if rank <= 20:
                penalty += 13.0
            elif rank <= 32:
                penalty += 9.0
            elif rank <= 50:
                penalty += 7.0
            elif rank <= 96:
                penalty += 4.5
            elif rank <= 160:
                penalty += 2.2
        elif tier == "Regular":
            if rank <= 20:
                penalty += 5.5
            elif rank <= 32:
                penalty += 3.5
            elif rank <= 50:
                penalty += 2.4
            elif rank <= 96:
                penalty += 0.8

        if tier == "International" or row.is_international or row.birth_country != UNITED_STATES:
            if rank <= 32:
                penalty += 16.0
            elif rank <= 64:
                penalty += 11.0
            elif rank <= 128:
                penalty += 8.0
            elif rank <= 160:
                penalty += 5.0
        pathway = row.development_pathway
        if pathway in {"International Pathway", "Late football starter", "Rugby convert", "Specialist pipeline"}:
            if rank <= 64:
                penalty += 2.4
            elif rank <= 160:
                penalty += 1.5
            else:
                penalty += 0.7
        elif pathway in {"JUCO route", "Track background"} and rank <= 96:
            penalty += 0.9

        outlier_relief = 0.0
        if row.scout_grade >= 75:
            outlier_relief += min(3.0, (row.scout_grade - 74) * 0.55)
        if row.scout_ceiling >= 84:
            outlier_relief += min(2.5, (row.scout_ceiling - 83) * 0.45)
        if row.combine_grade is not None and row.combine_grade >= 82:
            outlier_relief += 1.8
        if row.pro_day_grade is not None and row.pro_day_grade >= 84:
            outlier_relief += 1.2
        return max(0.0, penalty - outlier_relief)

    def _scouting_variance_score(
        self,
        *,
        rank: int,
        college_tier: str,
        scout_confidence: str,
        discovery_profile: str,
    ) -> int:
        if discovery_profile == HIDDEN_DISCOVERY_PROFILE:
            base = 74
        elif rank > 256:
            base = 43
        elif rank > 160:
            base = 34
        elif rank > 96:
            base = 27
        elif rank > 32:
            base = 21
        else:
            base = 13
        if college_tier == "Small":
            base += 12
        elif college_tier == "Regular":
            base += 5
        elif college_tier == "International":
            base += 14
        base += {"High": -5, "Medium": 2, "Low": 11}.get(scout_confidence, 2)
        return max(5, min(100, round(base + self.rng.gauss(0, 5.5))))

    def _discovery_notes(
        self,
        *,
        college_tier: str,
        discovery_profile: str,
        development_pathway: str,
        pipeline_note: str,
        birth_country: str,
        is_international: bool,
    ) -> str:
        pathway_note = ""
        if development_pathway != "Traditional pipeline":
            pathway_note = f" {development_pathway}: {pipeline_note}"
        elif is_international or birth_country != UNITED_STATES:
            pathway_note = f" International background from {birth_country} adds scouting variance."
        if discovery_profile == HIDDEN_DISCOVERY_PROFILE:
            if college_tier == "Small":
                source = "small-school area-scout"
            elif college_tier == "Regular":
                source = "regional cross-check"
            elif college_tier == "International":
                source = "international development"
            else:
                source = "late-cycle area-scout"
            return (
                f"Not listed on the initial public big board; starts as a {source} name "
                f"with high scouting variance.{pathway_note}"
            )
        if college_tier == "Small":
            return f"Public-board prospect, but small-school translation still needs extra live scouting.{pathway_note}"
        if college_tier == "Regular":
            return f"Public-board prospect with moderate scouting noise outside the national spotlight.{pathway_note}"
        if college_tier == "International":
            return f"Public-board prospect whose international context needs extra cross-checking.{pathway_note}"
        return f"Public-board prospect with normal early scouting coverage.{pathway_note}"

    def _hidden_projection(self, row: DraftClassPreviewRow) -> str:
        if row.scout_grade >= 62 or row.scout_ceiling >= 72:
            return (
                "Off the initial public board, but early area notes suggest a "
                "draftable profile if follow-up scouting confirms the traits."
            )
        if row.scout_grade >= 52:
            return (
                "Off the initial public board and better treated as a late draft "
                "or priority UDFA watch-list player until more information arrives."
            )
        return (
            "Off the initial public board and currently a camp-list name unless "
            "future scouting discovers a cleaner role."
        )

    @staticmethod
    def _append_sentence(text: str, sentence: str) -> str:
        text = (text or "").strip()
        sentence = (sentence or "").strip()
        if not sentence:
            return text
        if sentence in text:
            return text
        return f"{text} {sentence}".strip()

    def _special_teams_scouting_note(
        self,
        *,
        position: str,
        rank: int,
        college_tier: str,
        discovery_profile: str,
        flex: dict[str, dict[str, object]],
    ) -> str:
        position = str(position or "").upper()
        if position in {"QB", "K", "P", "LS"}:
            return ""
        notable = self._notable_special_teams_role(flex)
        if not notable:
            return ""

        role, current, potential = notable
        return_role = role in {"PR", "KR"}
        hidden_like = discovery_profile in {HIDDEN_DISCOVERY_PROFILE, "undiscovered", "off_public_board"}
        late_or_hidden = rank > 96 or hidden_like
        small_school = college_tier == "Small"
        chance = 0.0
        if current >= 7:
            chance = 0.42
        elif current >= 6 and (small_school or late_or_hidden):
            chance = 0.26
        elif return_role and current >= 6:
            chance = 0.22
        elif current >= 5 and small_school and late_or_hidden:
            chance = 0.10

        if rank <= 32:
            chance = 0.16 if return_role and current >= 6 else 0.03
        elif rank <= 64 and not (return_role or small_school):
            chance *= 0.55
        if potential >= 9 and (small_school or late_or_hidden):
            chance += 0.04
        if self.rng.random() >= min(chance, 0.55):
            return ""

        label = SPECIAL_TEAMS_FLEX_LABELS.get(role, role)
        if return_role:
            return self.rng.choice([
                f"Special teams note: scouts have him in the {label.lower()} conversation if ball security checks out.",
                "Special teams note: return-game traits are visible enough to matter on game-day roster math.",
                f"Special teams note: he has a real {label.lower()} path, though offensive/defensive value still drives the grade.",
            ])
        if small_school:
            return self.rng.choice([
                f"Special teams note: area scouts think {label.lower()} work could be his first Sunday ticket.",
                "Special teams note: small-school tape shows enough coverage-unit value to help him stick early.",
                f"Special teams note: coaches may notice the {label.lower()} profile before the main role fully translates.",
            ])
        if late_or_hidden:
            return self.rng.choice([
                f"Special teams note: the cleanest early path may be {label.lower()} work while the main position develops.",
                "Special teams note: teams looking late could see a core-unit contributor, not just a camp body.",
            ])
        return f"Special teams note: {label.lower()} value is a small but real part of the evaluation."

    def _special_teams_storyline_note(
        self,
        *,
        rank: int,
        college_tier: str,
        discovery_profile: str,
        flex: dict[str, dict[str, object]],
    ) -> str:
        notable = self._notable_special_teams_role(flex)
        if not notable:
            return ""
        _role, current, potential = notable
        if current < 6 and potential < 9:
            return ""
        hidden_like = discovery_profile in {HIDDEN_DISCOVERY_PROFILE, "undiscovered", "off_public_board"}
        if not (college_tier == "Small" or hidden_like or rank > 160):
            return ""
        if self.rng.random() > 0.16:
            return ""
        return "Area notes include a special-teams path that could help him dress before the full position grade arrives."

    @staticmethod
    def _notable_special_teams_role(
        flex: dict[str, dict[str, object]] | None,
    ) -> tuple[str, int, int] | None:
        best: tuple[str, int, int] | None = None
        best_score = -1
        for role, item in (flex or {}).items():
            try:
                current = int(item.get("current") or item.get("experience") or 0)
                potential = int(item.get("potential") or current)
            except (AttributeError, TypeError, ValueError):
                continue
            role_key = str(role or "").upper()
            score = current * 10 + min(potential, 10)
            if role_key in {"PR", "KR"}:
                score += 3
            if score > best_score:
                best = (role_key, current, potential)
                best_score = score
        if best and (best[1] >= 6 or best[2] >= 9):
            return best
        return None

    @staticmethod
    def _projected_round(public_rank: int) -> int | None:
        if public_rank > 256:
            return None
        # Approximate a seven-round NFL draft with compensatory-pick depth.
        round_cutoffs = (32, 64, 102, 140, 178, 216, 256)
        for round_number, cutoff in enumerate(round_cutoffs, start=1):
            if public_rank <= cutoff:
                return round_number
        return None

    def _hidden_talent_rank(self) -> int:
        bucket = self.rng.choices(
            HIDDEN_TALENT_RANK_BUCKETS,
            weights=[weight for _low, _high, weight in HIDDEN_TALENT_RANK_BUCKETS],
            k=1,
        )[0]
        low, high, _weight = bucket
        # Within each bucket, lean toward the later/weaker end. Hidden names can
        # still pop, but the typical player should look more like a sixth or
        # seventh round evaluation than a clean fourth rounder.
        return round(self.rng.triangular(low, high, high))

    def _hidden_age_plan(self, count: int) -> list[int]:
        ages: list[int] = []
        for _ in range(count):
            if self.rng.random() < 0.12:
                # A few underclassmen or unusual eligibility cases keep the
                # hidden pool from feeling mechanically old.
                age = self.rng.choice([20, 21, 21, 22, 25])
            else:
                age = round(self.rng.gauss(22.8, 0.75))
                age = max(22, min(24, age))
            ages.append(age)
        self.rng.shuffle(ages)
        return ages

    def _special_teams_flex(
        self,
        *,
        position: str,
        attributes: object,
        age: int,
        draft_year: int,
        rank: int,
        college_tier: str = "",
        discovery_profile: str = "",
    ) -> dict[str, dict[str, object]]:
        specialist_profile = generated_specialist_behavior_profile(
            attributes.archetype,
            attributes.ratings,
            position=position.upper(),
        )
        return flex_json_for_profile(
            position=position,
            ratings=attributes.ratings,
            specialist_profile=specialist_profile.as_dict(),
            role_scores=attributes.role_scores,
            overall=attributes.true_grade,
            potential_overall=attributes.ceiling_grade,
            age=age,
            is_rookie=True,
            draft_rank=rank,
            college_tier=college_tier,
            discovery_profile=discovery_profile,
            seed_key=f"{self.seed}:{draft_year}:{rank}:special-teams-flex",
        )

    def _position_list(self, count: int) -> list[str]:
        positions: list[str] = []
        start_rank = 1
        while start_rank <= count:
            bucket = self._position_bucket_for_rank(start_rank)
            end_rank = min(count, POSITION_BUCKET_END_RANKS.get(bucket, count))
            positions.extend(self._position_bucket_list(bucket, end_rank - start_rank + 1))
            start_rank = end_rank + 1
        return positions

    def _position_bucket_list(self, bucket: str, count: int) -> list[str]:
        base_weights = POSITION_WEIGHTS_BY_BUCKET.get(bucket, POSITION_WEIGHTS)
        weights = {
            position: max(0.05, self.rng.gauss(weight, max(0.05, weight * 0.10)))
            for position, weight in base_weights.items()
            if weight > 0
        }
        total = sum(weights.values())
        raw_counts = {
            position: weight / total * count
            for position, weight in weights.items()
        }
        counts = {position: int(value) for position, value in raw_counts.items()}
        remaining = count - sum(counts.values())
        remainders = sorted(
            raw_counts,
            key=lambda position: raw_counts[position] - counts[position],
            reverse=True,
        )
        for position in remainders[:remaining]:
            counts[position] += 1
        positions = [
            position
            for position, position_count in counts.items()
            for _ in range(position_count)
        ]
        self.rng.shuffle(positions)
        return positions

    @staticmethod
    def _position_bucket_for_rank(rank: int) -> str:
        if rank <= 32:
            return "round_1"
        if rank <= 96:
            return "round_2_3"
        if rank <= 160:
            return "round_4_5"
        if rank <= 256:
            return "round_6_7"
        return "leftover"

    def _college_tier_weights_for_rank(self, rank: int) -> dict[str, float]:
        return PUBLIC_COLLEGE_TIER_WEIGHTS_BY_BUCKET[self._position_bucket_for_rank(rank)]

    def _position_group(self, position: str) -> str:
        return POSITION_GROUPS.get(position.upper(), position.upper())

    def _choose_handedness(self, position: str) -> str:
        position_key = position.upper()
        weights = HANDEDNESS_WEIGHTS.get(
            position_key,
            HANDEDNESS_WEIGHTS.get(self._position_group(position), HANDEDNESS_WEIGHTS["default"]),
        )
        return self._weighted_choice(weights)

    def _ethnicity_key_list(
        self,
        positions: list[str],
        birth_countries: list[str],
    ) -> list[str | None]:
        desired_counts = Counter(self.name_generator.sample_ethnicity_mix(len(positions)))
        international_counts = Counter(
            self.name_generator.ethnicity_key_for_country(country)
            for country in birth_countries
            if country != UNITED_STATES
        )
        international_counts.pop(None, None)

        available_counts = Counter(
            {
                ethnicity_key: max(
                    0,
                    desired_counts.get(ethnicity_key, 0)
                    - international_counts.get(ethnicity_key, 0),
                )
                for ethnicity_key in self.name_generator.ethnicity_profiles
            }
        )
        us_count = sum(country == UNITED_STATES for country in birth_countries)
        self._balance_available_ethnicity_counts(available_counts, us_count)

        ethnicity_keys: list[str | None] = [None] * len(positions)
        us_indexes = [
            index
            for index, country in enumerate(birth_countries)
            if country == UNITED_STATES
        ]
        us_indexes.sort(
            key=lambda index: POSITION_ETHNICITY_ASSIGNMENT_PRIORITY.get(
                positions[index],
                50,
            ),
            reverse=True,
        )
        for index in us_indexes:
            ethnicity_keys[index] = self._choose_position_ethnicity(
                positions[index],
                available_counts,
            )
        return ethnicity_keys

    def _balance_available_ethnicity_counts(
        self,
        available_counts: Counter[str],
        target_total: int,
    ) -> None:
        profile_keys = list(self.name_generator.ethnicity_profiles)
        while sum(available_counts.values()) > target_total:
            keys = [key for key in profile_keys if available_counts[key] > 0]
            weights = [available_counts[key] for key in keys]
            chosen = self.rng.choices(keys, weights=weights, k=1)[0]
            available_counts[chosen] -= 1
        while sum(available_counts.values()) < target_total:
            weights = [
                float(self.name_generator.ethnicity_profiles[key]["target_pct"])
                for key in profile_keys
            ]
            chosen = self.rng.choices(profile_keys, weights=weights, k=1)[0]
            available_counts[chosen] += 1

    def _choose_position_ethnicity(
        self,
        position: str,
        available_counts: Counter[str],
    ) -> str:
        keys = [key for key, count in available_counts.items() if count > 0]
        if not keys:
            profile_keys = list(self.name_generator.ethnicity_profiles)
            weights = [
                float(self.name_generator.ethnicity_profiles[key]["target_pct"])
                for key in profile_keys
            ]
            return self.rng.choices(profile_keys, weights=weights, k=1)[0]
        multipliers = POSITION_ETHNICITY_MULTIPLIERS.get(position.upper(), {})
        weights = [
            max(0.01, available_counts[key] * multipliers.get(key, 1.0))
            for key in keys
        ]
        chosen = self.rng.choices(keys, weights=weights, k=1)[0]
        available_counts[chosen] -= 1
        return chosen

    def _birth_country_for_rank(
        self,
        *,
        rank: int,
        position: str,
        base_international_chance: float,
    ) -> str:
        tier = self._rank_tier(rank)
        chance = min(0.35, base_international_chance * INTERNATIONAL_CHANCE_FACTORS[tier])
        if self.rng.random() >= chance:
            return UNITED_STATES
        country_weights = self._country_weights_for_tier(tier, position)
        return self._weighted_choice(country_weights)

    @staticmethod
    def _rank_tier(rank: int) -> str:
        if rank <= 32:
            return "tier_1"
        if rank <= 96:
            return "tier_2"
        if rank <= 160:
            return "tier_3"
        return "tier_4"

    def _country_weights_for_tier(self, tier: str, position: str) -> dict[str, float]:
        if tier == "tier_1":
            weights = {
                "Canada": 35,
                "Nigeria": 25,
                "Australia": 15,
                "Germany": 10,
                "United Kingdom": 10,
                "American Samoa": 5,
            }
            return self._apply_country_position_multipliers(weights, position)
        if tier != "tier_1" and position.upper() in {"K", "P"}:
            return self._apply_country_position_multipliers(
                COUNTRY_TIER_WEIGHTS["tier_2_specialist"],
                position,
            )
        if tier in COUNTRY_TIER_WEIGHTS:
            return self._apply_country_position_multipliers(
                COUNTRY_TIER_WEIGHTS[tier],
                position,
            )
        return self._apply_country_position_multipliers(
            COUNTRY_TIER_WEIGHTS["tier_4"],
            position,
        )

    def _apply_country_position_multipliers(
        self,
        weights: dict[str, float],
        position: str,
    ) -> dict[str, float]:
        position_key = position.upper()
        position_group = POSITION_GROUPS.get(position_key, position_key)
        adjusted = dict(weights)
        for country in list(adjusted):
            multipliers = COUNTRY_POSITION_MULTIPLIERS.get(country, {})
            adjusted[country] *= float(
                multipliers.get(
                    position_key,
                    multipliers.get(position_group, 1.0),
                )
            )
        return adjusted

    def _weighted_choice(self, weights: dict[str, float]) -> str:
        countries = list(weights)
        values = [float(weights[country]) for country in countries]
        return self.rng.choices(countries, weights=values, k=1)[0]


def _average_present(*values: int | float | None) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


HIDDEN_EXPORT_FIELDS = {
    "true_rank",
    "true_grade",
    "ceiling_grade",
    "dev_trait",
    "risk_level",
    "original_archetype",
    "archetype_identity_status",
    "archetype_identity_note",
    "primary_role_score",
    "secondary_role_score",
    "ratings",
    "role_scores",
    "special_teams_flex",
    "top_ratings",
    "weak_ratings",
    "private_workout_status",
    "private_workout_type",
    "private_workout_interest",
    "private_workout_grade",
    "private_workout_note",
}


def write_csv(rows: list[DraftClassPreviewRow], path: Path, *, include_hidden: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(rows[0]).keys())
    if not include_hidden:
        fields = [field for field in fields if field not in HIDDEN_EXPORT_FIELDS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            data = asdict(row)
            if not include_hidden:
                data = {field: data[field] for field in fields}
            writer.writerow(data)


def _html_optional(value: object, *, precision: int | None = None) -> str:
    if value is None:
        return ""
    if precision is not None and isinstance(value, float):
        return f"{value:.{precision}f}"
    return html.escape(str(value))


def write_html(rows: list[DraftClassPreviewRow], path: Path, *, include_hidden: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body_rows = []
    for row in rows:
        flags = []
        if row.is_international:
            flags.append("INT")
        if row.physical_outlier:
            flags.append("BODY")
        if row.combine_injured:
            flags.append("MED")
        if row.combine_top_skip:
            flags.append("SKIP")
        if row.pro_day_medical_recheck:
            flags.append("MED RECHECK")
        if row.pro_day_improved_from_combine:
            flags.append("PRO+")
        if row.public_board_status == "off_public_board":
            flags.append("OFF BOARD")
        if row.hairstyle_outlier:
            flags.append("HAIR")
        if row.facial_hair_outlier:
            flags.append("LOOK")
        flag_text = ", ".join(flags)
        role_score_text = "" if row.primary_role_score is None else f"{row.primary_role_score:.1f}"
        projected_round_text = "UDFA" if row.projected_round is None else str(row.projected_round)
        board_rank_text = str(row.public_board_rank) if row.public_board_rank is not None else "Off"
        hidden_cells = ""
        if include_hidden:
            hidden_cells = (
                f"<td>{row.true_rank}</td>"
                f"<td>{row.true_grade}</td>"
                f"<td>{row.ceiling_grade}</td>"
                f"<td>{html.escape(row.dev_trait)}</td>"
                f"<td>{html.escape(row.risk_level)}</td>"
                f"<td>{html.escape(row.original_archetype)}</td>"
                f"<td>{html.escape(row.archetype_identity_status)}</td>"
                f"<td class=\"text-cell\">{html.escape(row.archetype_identity_note)}</td>"
                f"<td>{role_score_text}</td>"
                f"<td class=\"text-cell\">{html.escape(row.top_ratings)}</td>"
                f"<td class=\"text-cell\">{html.escape(row.weak_ratings)}</td>"
                f"<td>{html.escape(row.private_workout_status)}</td>"
                f"<td>{html.escape(row.private_workout_type)}</td>"
                f"<td>{html.escape(row.private_workout_interest)}</td>"
                f"<td>{_html_optional(row.private_workout_grade)}</td>"
                f"<td class=\"text-cell\">{html.escape(row.private_workout_note)}</td>"
            )
        body_rows.append(
            "<tr>"
            f"<td>{board_rank_text}</td>"
            f"<td>{html.escape(row.public_board_status)}</td>"
            f"<td>{html.escape(row.discovery_status)}</td>"
            f"<td>{row.scouting_variance}</td>"
            f"<td class=\"text-cell\">{html.escape(row.discovery_notes)}</td>"
            f"<td>{projected_round_text}</td>"
            f"<td>{_html_optional(row.projected_pick)}</td>"
            f"<td>{html.escape(row.display_name or row.full_name)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.name_background_note or row.name_pronunciation_note or row.name_storyline_note)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.family_football_background)}</td>"
            f"<td>{row.position}</td>"
            f"<td>{row.position_group}</td>"
            f"<td>{row.age}</td>"
            f"<td>{html.escape(row.college)}</td>"
            f"<td>{html.escape(row.hometown)}</td>"
            f"<td>{row.height}</td>"
            f"<td>{row.weight_lbs}</td>"
            f"<td>{row.arm_length}</td>"
            f"<td>{row.hand_size}</td>"
            f"<td>{html.escape(row.handedness)}</td>"
            f"<td>{html.escape(row.combine_status)}</td>"
            f"<td>{_html_optional(row.combine_grade)}</td>"
            f"<td>{_html_optional(row.athletic_score)}</td>"
            f"<td>{row.drills_completed}</td>"
            f"<td>{_html_optional(row.forty_yard_dash, precision=2)}</td>"
            f"<td>{_html_optional(row.ten_yard_split, precision=2)}</td>"
            f"<td>{_html_optional(row.bench_press_reps)}</td>"
            f"<td>{_html_optional(row.vertical_jump_in, precision=1)}</td>"
            f"<td>{_html_optional(row.broad_jump_in)}</td>"
            f"<td>{_html_optional(row.three_cone_sec, precision=2)}</td>"
            f"<td>{_html_optional(row.twenty_yard_shuttle_sec, precision=2)}</td>"
            f"<td>{_html_optional(row.sixty_yard_shuttle_sec, precision=2)}</td>"
            f"<td>{html.escape(row.workout_variance)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.combine_summary)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.drills_skipped)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.combine_note)}</td>"
            f"<td>{html.escape(row.pro_day_status)}</td>"
            f"<td>{_html_optional(row.pro_day_grade)}</td>"
            f"<td>{_html_optional(row.pro_day_athletic_score)}</td>"
            f"<td>{row.pro_day_drills_completed}</td>"
            f"<td>{_html_optional(row.pro_day_forty_yard_dash, precision=2)}</td>"
            f"<td>{_html_optional(row.pro_day_ten_yard_split, precision=2)}</td>"
            f"<td>{_html_optional(row.pro_day_bench_press_reps)}</td>"
            f"<td>{_html_optional(row.pro_day_vertical_jump_in, precision=1)}</td>"
            f"<td>{_html_optional(row.pro_day_broad_jump_in)}</td>"
            f"<td>{_html_optional(row.pro_day_three_cone_sec, precision=2)}</td>"
            f"<td>{_html_optional(row.pro_day_twenty_yard_shuttle_sec, precision=2)}</td>"
            f"<td>{html.escape(row.pro_day_workout_variance)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.pro_day_summary)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.pro_day_drills_skipped)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.pro_day_note)}</td>"
            f"<td>{html.escape(row.archetype)}</td>"
            f"<td>{html.escape(row.primary_role)}</td>"
            f"<td>{html.escape(row.scout_lens)}</td>"
            f"<td>{html.escape(row.scout_confidence)}</td>"
            f"<td>{row.scout_grade}</td>"
            f"<td>{row.scout_ceiling}</td>"
            f"<td>{html.escape(row.scout_risk)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.scouting_summary)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.scouting_strengths)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.scouting_concerns)}</td>"
            f"<td class=\"text-cell\">{html.escape(row.scouting_projection)}</td>"
            f"<td class=\"text-cell report-cell\">{html.escape(row.scouting_report)}</td>"
            f"<td>{html.escape(row.ethnicity)}</td>"
            f"<td>{html.escape(row.birth_country)}</td>"
            f"<td>{html.escape(row.eye_color)}</td>"
            f"<td>{html.escape(row.hair_color)}</td>"
            f"<td>{html.escape(row.hairstyle)}</td>"
            f"<td>{html.escape(row.facial_hair)}</td>"
            f"<td>{html.escape(row.skin_tone)}</td>"
            f"<td>{html.escape(row.complexion)}</td>"
            f"<td>{html.escape(row.face_shape)}</td>"
            f"<td>{html.escape(row.jawline)}</td>"
            f"<td>{html.escape(row.brow_profile)}</td>"
            f"<td>{html.escape(row.nose_profile)}</td>"
            f"<td>{html.escape(row.smile_profile)}</td>"
            f"<td>{html.escape(row.media_style)}</td>"
            f"<td>{html.escape(row.accessory_style)}</td>"
            f"<td>{html.escape(row.photo_prompt_traits)}</td>"
            f"<td>{flag_text}</td>"
            f"{hidden_cells}"
            "</tr>"
        )
    hidden_headers = ""
    if include_hidden:
        hidden_headers = (
            "<th>True Rank</th><th>True</th><th>Ceiling</th><th>Dev</th><th>Risk</th>"
            "<th>Original Archetype</th><th>Identity</th><th>Identity Note</th>"
            "<th>Role Score</th><th>Top Ratings</th><th>Weak Ratings</th>"
            "<th>Private</th><th>Private Type</th><th>Private Interest</th>"
            "<th>Private Grade</th><th>Private Note</th>"
        )
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{rows[0].draft_year} Draft Class Preview</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Arial, Helvetica, sans-serif;
      background: #f5f7f8;
      color: #172026;
    }}
    body {{ margin: 0; padding: 24px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    .meta {{ margin: 0 0 20px; color: #52616b; }}
    .table-wrap {{ overflow-x: auto; background: white; border: 1px solid #d7dee3; border-radius: 8px; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 6400px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #e6ebef; text-align: left; font-size: 13px; white-space: nowrap; }}
    th {{ position: sticky; top: 0; background: #edf2f5; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #34444f; }}
    tr:nth-child(even) td {{ background: #fafcfd; }}
    td:nth-child(8) {{ font-weight: 700; }}
    .text-cell {{ white-space: normal; min-width: 240px; max-width: 420px; line-height: 1.35; }}
    .report-cell {{ min-width: 420px; }}
  </style>
</head>
<body>
  <h1>{rows[0].draft_year} Draft Class Preview</h1>
  <p class="meta">{len(rows)} fictional prospects with public scouting, combine, and pro-day views over hidden normalized sim-rating profiles.</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Board</th><th>Status</th><th>Discovery</th><th>Scout Var</th><th>Discovery Note</th><th>Proj Rd</th><th>Proj Pick</th><th>Name</th><th>Name Context</th><th>Family</th><th>Pos</th><th>Group</th><th>Age</th><th>College</th><th>Hometown</th><th>Ht</th><th>Wt</th><th>Arm</th><th>Hand Size</th><th>Handed</th>
          <th>Combine</th><th>Comb Grade</th><th>Ath Score</th><th>Drills</th><th>40</th><th>10 Split</th><th>Bench</th><th>Vert</th><th>Broad</th><th>3-Cone</th><th>Shuttle</th><th>60 Sh</th><th>Workout</th><th>Combine Summary</th><th>Skipped</th><th>Combine Note</th>
          <th>Pro Day</th><th>Pro Grade</th><th>Pro Ath</th><th>Pro Drills</th><th>Pro 40</th><th>Pro 10</th><th>Pro Bench</th><th>Pro Vert</th><th>Pro Broad</th><th>Pro 3-Cone</th><th>Pro Shuttle</th><th>Pro Workout</th><th>Pro Summary</th><th>Pro Skipped</th><th>Pro Note</th>
          <th>Archetype</th><th>Primary Role</th>
          <th>Scout</th><th>Conf</th><th>Scout Grade</th><th>Scout Ceiling</th><th>Scout Risk</th><th>Summary</th><th>Strengths</th><th>Concerns</th><th>Projection</th><th>Full Report</th>
          <th>Ethnicity</th><th>Country</th><th>Eyes</th><th>Hair Color</th><th>Hair Style</th><th>Facial Hair</th>
          <th>Skin Tone</th><th>Complexion</th><th>Face</th><th>Jawline</th><th>Brow</th><th>Nose</th><th>Smile</th><th>Media</th><th>Accessories</th>
          <th>Photo Traits</th><th>Flags</th>{hidden_headers}
        </tr>
      </thead>
      <tbody>
        {''.join(body_rows)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")
