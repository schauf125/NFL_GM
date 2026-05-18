"""Hometown generation for draft prospects.

The goal is football-plausible, not census-perfect. State weights roughly blend
population, high-school football density, and recruiting footprint. College
proximity then nudges prospects toward the school's state or region, especially
for smaller programs, while national powers still recruit broadly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


UNITED_STATES = "United States"


@dataclass(frozen=True)
class HometownProfile:
    city: str
    state: str
    region: str

    @property
    def label(self) -> str:
        return f"{self.city}, {self.state}" if self.state else self.city


STATE_REGIONS: dict[str, str] = {
    "AL": "Southeast", "AR": "Southeast", "FL": "Southeast", "GA": "Southeast", "KY": "Southeast",
    "LA": "Southeast", "MS": "Southeast", "NC": "Southeast", "SC": "Southeast", "TN": "Southeast",
    "VA": "Southeast", "WV": "Southeast", "TX": "Texas",
    "IL": "Midwest", "IN": "Midwest", "IA": "Midwest", "KS": "Midwest", "MI": "Midwest",
    "MN": "Midwest", "MO": "Midwest", "NE": "Midwest", "ND": "Midwest", "OH": "Midwest",
    "SD": "Midwest", "WI": "Midwest",
    "CA": "West", "OR": "West", "WA": "West", "AZ": "West", "CO": "West", "ID": "West",
    "MT": "West", "NV": "West", "NM": "West", "UT": "West", "WY": "West", "HI": "West",
    "CT": "Northeast", "DE": "Northeast", "MA": "Northeast", "MD": "Northeast", "ME": "Northeast",
    "NH": "Northeast", "NJ": "Northeast", "NY": "Northeast", "PA": "Northeast", "RI": "Northeast",
    "VT": "Northeast", "DC": "Northeast",
    "OK": "Plains", "AK": "West",
}


# Approximate recruiting output/population blend. Large states and football
# hotbeds are intentionally heavier; tiny states still exist, just rarely.
STATE_WEIGHTS: dict[str, float] = {
    "TX": 16.5, "FL": 13.8, "CA": 12.2, "GA": 8.2, "OH": 5.5, "LA": 5.2,
    "NC": 4.9, "PA": 4.8, "AL": 4.5, "IL": 4.4, "MI": 4.2, "NY": 4.1,
    "SC": 3.9, "VA": 3.8, "TN": 3.6, "NJ": 3.4, "MS": 3.2, "AZ": 3.0,
    "MO": 2.8, "MD": 2.7, "IN": 2.5, "WA": 2.5, "OK": 2.4, "WI": 2.3,
    "KY": 2.1, "MN": 2.0, "CO": 1.9, "AR": 1.8, "IA": 1.7, "NV": 1.5,
    "OR": 1.4, "KS": 1.3, "MA": 1.3, "UT": 1.2, "CT": 0.9, "NE": 0.85,
    "NM": 0.72, "WV": 0.68, "ID": 0.55, "HI": 0.52, "NH": 0.34, "ME": 0.32,
    "RI": 0.30, "MT": 0.28, "DE": 0.27, "SD": 0.26, "ND": 0.24, "AK": 0.20,
    "VT": 0.18, "WY": 0.16, "DC": 0.15,
}


STATE_CITY_WEIGHTS: dict[str, dict[str, float]] = {
    "AL": {"Birmingham": 24, "Montgomery": 14, "Mobile": 12, "Huntsville": 13, "Tuscaloosa": 8, "Hoover": 6, "Auburn": 5, "Dothan": 4, "Decatur": 3},
    "AR": {"Little Rock": 30, "Fayetteville": 13, "Fort Smith": 11, "Jonesboro": 8, "Pine Bluff": 7, "Conway": 7, "Bentonville": 6},
    "AZ": {"Phoenix": 45, "Tucson": 18, "Mesa": 12, "Chandler": 8, "Glendale": 7, "Scottsdale": 5, "Tempe": 4},
    "CA": {"Los Angeles": 30, "San Diego": 12, "San Jose": 9, "Sacramento": 8, "Fresno": 7, "Long Beach": 6, "Oakland": 5, "Bakersfield": 5, "Riverside": 5, "Anaheim": 4, "Stockton": 3, "Modesto": 3},
    "CO": {"Denver": 34, "Colorado Springs": 20, "Aurora": 12, "Fort Collins": 8, "Lakewood": 6, "Boulder": 5, "Pueblo": 5},
    "CT": {"Bridgeport": 18, "New Haven": 16, "Hartford": 15, "Stamford": 13, "Waterbury": 9, "Norwalk": 7},
    "DC": {"Washington": 100},
    "DE": {"Wilmington": 34, "Dover": 20, "Newark": 15, "Middletown": 8},
    "FL": {"Miami": 22, "Jacksonville": 18, "Tampa": 14, "Orlando": 14, "Fort Lauderdale": 8, "St. Petersburg": 7, "Tallahassee": 6, "Pensacola": 4, "Lakeland": 4, "Bradenton": 3},
    "GA": {"Atlanta": 34, "Savannah": 10, "Augusta": 10, "Columbus": 9, "Macon": 8, "Athens": 7, "Marietta": 6, "Valdosta": 5, "Warner Robins": 4},
    "HI": {"Honolulu": 62, "Hilo": 10, "Kailua": 9, "Waipahu": 8},
    "IA": {"Des Moines": 28, "Cedar Rapids": 18, "Davenport": 11, "Sioux City": 10, "Iowa City": 9, "Ames": 7, "Waterloo": 6},
    "ID": {"Boise": 42, "Meridian": 14, "Nampa": 13, "Idaho Falls": 8, "Pocatello": 7, "Twin Falls": 5},
    "IL": {"Chicago": 44, "Aurora": 8, "Naperville": 7, "Rockford": 7, "Joliet": 6, "Springfield": 5, "Peoria": 5, "Champaign": 4},
    "IN": {"Indianapolis": 39, "Fort Wayne": 13, "Evansville": 8, "South Bend": 8, "Carmel": 6, "Bloomington": 5, "Gary": 5},
    "KS": {"Wichita": 34, "Overland Park": 14, "Kansas City": 12, "Topeka": 11, "Lawrence": 8, "Manhattan": 6},
    "KY": {"Louisville": 36, "Lexington": 24, "Bowling Green": 8, "Owensboro": 7, "Covington": 5, "Frankfort": 4},
    "LA": {"New Orleans": 26, "Baton Rouge": 21, "Shreveport": 13, "Lafayette": 10, "Lake Charles": 7, "Monroe": 6, "Alexandria": 5},
    "MA": {"Boston": 34, "Worcester": 16, "Springfield": 11, "Lowell": 7, "Cambridge": 6, "Brockton": 6, "Quincy": 5},
    "MD": {"Baltimore": 32, "Silver Spring": 12, "Frederick": 9, "Rockville": 8, "Gaithersburg": 7, "Bowie": 6, "Annapolis": 5},
    "ME": {"Portland": 34, "Lewiston": 13, "Bangor": 12, "South Portland": 7, "Auburn": 6},
    "MI": {"Detroit": 31, "Grand Rapids": 15, "Warren": 8, "Lansing": 8, "Ann Arbor": 7, "Flint": 7, "Kalamazoo": 6, "Saginaw": 5},
    "MN": {"Minneapolis": 30, "St. Paul": 22, "Rochester": 9, "Duluth": 7, "Bloomington": 6, "Eden Prairie": 5},
    "MO": {"Kansas City": 29, "St. Louis": 28, "Springfield": 10, "Columbia": 8, "Independence": 6, "Jefferson City": 4},
    "MS": {"Jackson": 28, "Gulfport": 12, "Southaven": 10, "Hattiesburg": 9, "Biloxi": 7, "Meridian": 6, "Starkville": 5, "Oxford": 5},
    "MT": {"Billings": 28, "Missoula": 18, "Great Falls": 14, "Bozeman": 12, "Helena": 8},
    "NC": {"Charlotte": 31, "Raleigh": 18, "Greensboro": 11, "Durham": 10, "Winston-Salem": 9, "Fayetteville": 7, "Asheville": 4},
    "ND": {"Fargo": 32, "Bismarck": 20, "Grand Forks": 14, "Minot": 10},
    "NE": {"Omaha": 42, "Lincoln": 27, "Bellevue": 6, "Grand Island": 5, "Kearney": 4},
    "NH": {"Manchester": 32, "Nashua": 20, "Concord": 11, "Dover": 8, "Portsmouth": 6},
    "NJ": {"Newark": 18, "Jersey City": 18, "Paterson": 10, "Elizabeth": 9, "Trenton": 8, "Camden": 6, "Atlantic City": 4},
    "NM": {"Albuquerque": 45, "Las Cruces": 17, "Rio Rancho": 9, "Santa Fe": 8, "Roswell": 5},
    "NV": {"Las Vegas": 58, "Henderson": 14, "Reno": 13, "North Las Vegas": 8, "Sparks": 4},
    "NY": {"New York": 45, "Buffalo": 10, "Rochester": 9, "Yonkers": 7, "Syracuse": 6, "Albany": 5, "New Rochelle": 3},
    "OH": {"Columbus": 28, "Cleveland": 16, "Cincinnati": 15, "Toledo": 8, "Akron": 7, "Dayton": 7, "Youngstown": 5, "Canton": 4},
    "OK": {"Oklahoma City": 38, "Tulsa": 26, "Norman": 9, "Broken Arrow": 7, "Stillwater": 5, "Lawton": 5},
    "OR": {"Portland": 38, "Eugene": 14, "Salem": 13, "Gresham": 7, "Hillsboro": 6, "Bend": 6, "Corvallis": 4},
    "PA": {"Philadelphia": 34, "Pittsburgh": 17, "Allentown": 7, "Erie": 6, "Reading": 6, "Scranton": 5, "Harrisburg": 5, "State College": 4},
    "RI": {"Providence": 42, "Warwick": 16, "Cranston": 13, "Pawtucket": 9},
    "SC": {"Columbia": 20, "Charleston": 18, "Greenville": 14, "Spartanburg": 9, "Rock Hill": 8, "Myrtle Beach": 6, "Florence": 5},
    "SD": {"Sioux Falls": 38, "Rapid City": 19, "Aberdeen": 8, "Brookings": 7, "Pierre": 5},
    "TN": {"Nashville": 31, "Memphis": 29, "Knoxville": 11, "Chattanooga": 9, "Murfreesboro": 8, "Clarksville": 6},
    "TX": {"Houston": 22, "Dallas": 17, "San Antonio": 14, "Austin": 11, "Fort Worth": 10, "El Paso": 5, "Arlington": 5, "Plano": 4, "Lubbock": 4, "Waco": 3, "College Station": 3},
    "UT": {"Salt Lake City": 26, "West Valley City": 12, "Provo": 11, "West Jordan": 9, "Orem": 8, "Ogden": 7, "St. George": 6},
    "VA": {"Virginia Beach": 18, "Richmond": 17, "Norfolk": 13, "Chesapeake": 11, "Arlington": 8, "Newport News": 7, "Alexandria": 6, "Roanoke": 4},
    "VT": {"Burlington": 34, "South Burlington": 11, "Rutland": 9, "Montpelier": 7},
    "WA": {"Seattle": 32, "Spokane": 12, "Tacoma": 11, "Vancouver": 8, "Bellevue": 7, "Everett": 6, "Yakima": 4},
    "WI": {"Milwaukee": 30, "Madison": 18, "Green Bay": 10, "Kenosha": 7, "Racine": 6, "Appleton": 5, "Eau Claire": 4},
    "WV": {"Charleston": 25, "Huntington": 18, "Morgantown": 13, "Parkersburg": 7, "Wheeling": 6},
    "WY": {"Cheyenne": 34, "Casper": 22, "Laramie": 12, "Gillette": 7},
    "AK": {"Anchorage": 62, "Fairbanks": 16, "Juneau": 9},
}


COLLEGE_STATE_OVERRIDES: dict[str, str] = {
    "Alabama": "AL", "Auburn": "AL", "UAB": "AL", "Troy": "AL", "South Alabama": "AL",
    "Arizona": "AZ", "Arizona State": "AZ", "Northern Arizona": "AZ",
    "Arkansas": "AR", "Arkansas State": "AR", "Central Arkansas": "AR",
    "USC": "CA", "UCLA": "CA", "California": "CA", "Stanford": "CA", "Fresno State": "CA", "San Diego State": "CA", "San Jose State": "CA",
    "Colorado": "CO", "Colorado State": "CO", "Air Force": "CO",
    "Connecticut": "CT", "Yale": "CT",
    "Delaware": "DE",
    "Florida": "FL", "Florida State": "FL", "Miami": "FL", "UCF": "FL", "USF": "FL", "Florida Atlantic": "FL", "Florida International": "FL",
    "Georgia": "GA", "Georgia Tech": "GA", "Georgia Southern": "GA", "Georgia State": "GA", "Kennesaw State": "GA",
    "Hawaii": "HI",
    "Boise State": "ID", "Idaho": "ID",
    "Illinois": "IL", "Northwestern": "IL", "Northern Illinois": "IL",
    "Indiana": "IN", "Purdue": "IN", "Notre Dame": "IN", "Ball State": "IN",
    "Iowa": "IA", "Iowa State": "IA", "Northern Iowa": "IA",
    "Kansas": "KS", "Kansas State": "KS",
    "Kentucky": "KY", "Louisville": "KY", "Western Kentucky": "KY", "Eastern Kentucky": "KY",
    "LSU": "LA", "Louisiana": "LA", "Louisiana Tech": "LA", "Tulane": "LA", "UL Monroe": "LA",
    "Maine": "ME",
    "Maryland": "MD", "Navy": "MD", "Towson": "MD",
    "Boston College": "MA", "Massachusetts": "MA", "Harvard": "MA", "Holy Cross": "MA",
    "Michigan": "MI", "Michigan State": "MI", "Western Michigan": "MI", "Central Michigan": "MI", "Eastern Michigan": "MI",
    "Minnesota": "MN",
    "Ole Miss": "MS", "Mississippi State": "MS", "Southern Miss": "MS", "Jackson State": "MS",
    "Missouri": "MO", "Missouri State": "MO",
    "Nebraska": "NE",
    "UNLV": "NV", "Nevada": "NV",
    "Rutgers": "NJ", "Princeton": "NJ",
    "New Mexico": "NM", "New Mexico State": "NM",
    "Syracuse": "NY", "Buffalo": "NY", "Army": "NY", "Fordham": "NY",
    "North Carolina": "NC", "NC State": "NC", "Duke": "NC", "Wake Forest": "NC", "Appalachian State": "NC", "East Carolina": "NC", "Charlotte": "NC",
    "North Dakota State": "ND", "North Dakota": "ND",
    "Ohio State": "OH", "Cincinnati": "OH", "Toledo": "OH", "Bowling Green": "OH", "Ohio": "OH", "Miami (OH)": "OH", "Kent State": "OH", "Akron": "OH",
    "Oklahoma": "OK", "Oklahoma State": "OK", "Tulsa": "OK",
    "Oregon": "OR", "Oregon State": "OR", "Portland State": "OR",
    "Penn State": "PA", "Pittsburgh": "PA", "Temple": "PA", "Villanova": "PA",
    "Clemson": "SC", "South Carolina": "SC", "Coastal Carolina": "SC", "Furman": "SC",
    "South Dakota State": "SD", "South Dakota": "SD",
    "Tennessee": "TN", "Vanderbilt": "TN", "Memphis": "TN", "Middle Tennessee State": "TN", "Chattanooga": "TN",
    "Texas": "TX", "Texas A&M": "TX", "TCU": "TX", "Baylor": "TX", "Houston": "TX", "Texas Tech": "TX", "SMU": "TX", "UTSA": "TX", "Rice": "TX", "North Texas": "TX", "UTEP": "TX", "Sam Houston State": "TX",
    "Utah": "UT", "BYU": "UT", "Utah State": "UT",
    "Virginia": "VA", "Virginia Tech": "VA", "Old Dominion": "VA", "Liberty": "VA", "James Madison": "VA", "Richmond": "VA",
    "Washington": "WA", "Washington State": "WA", "Eastern Washington": "WA",
    "Wisconsin": "WI",
    "West Virginia": "WV", "Marshall": "WV",
    "Wyoming": "WY",
}

NATIONAL_POWER_STATES = {"AL", "FL", "GA", "LA", "OH", "TX", "CA", "MI", "PA", "OK", "OR", "SC", "TN"}
INTERNATIONAL_HOMETOWNS = {
    "Canada": {"Toronto, Ontario": 28, "Vancouver, British Columbia": 18, "Montreal, Quebec": 14, "Calgary, Alberta": 12, "Ottawa, Ontario": 8},
    "Australia": {"Sydney, New South Wales": 34, "Melbourne, Victoria": 30, "Brisbane, Queensland": 14, "Perth, Western Australia": 10},
    "Germany": {"Berlin": 24, "Munich": 18, "Frankfurt": 16, "Hamburg": 14, "Dusseldorf": 8},
    "United Kingdom": {"London": 40, "Birmingham": 14, "Manchester": 14, "Leeds": 8, "Glasgow": 8},
    "Nigeria": {"Lagos": 46, "Abuja": 18, "Port Harcourt": 10, "Ibadan": 9},
    "Ghana": {"Accra": 52, "Kumasi": 18, "Tema": 8},
    "Cameroon": {"Douala": 38, "Yaounde": 34, "Bamenda": 8},
    "American Samoa": {"Pago Pago": 78, "Tafuna": 12},
    "Samoa": {"Apia": 72, "Faleula": 8},
    "Tonga": {"Nuku'alofa": 74, "Neiafu": 8},
}


class HometownGenerator:
    def __init__(self, *, seed: str | int | None = None) -> None:
        self.rng = random.Random(seed)

    def generate(
        self,
        *,
        college: str,
        college_tier: str,
        birth_country: str,
        is_international: bool,
    ) -> HometownProfile:
        if is_international and birth_country != UNITED_STATES and self.rng.random() < 0.72:
            return self._international_hometown(birth_country)
        college_state = COLLEGE_STATE_OVERRIDES.get(college)
        state = self._choose_state(college_state=college_state, college_tier=college_tier)
        city = self._weighted_choice(STATE_CITY_WEIGHTS.get(state) or {"Springfield": 1.0})
        return HometownProfile(city=city, state=state, region=STATE_REGIONS.get(state, "Unknown"))

    def _choose_state(self, *, college_state: str | None, college_tier: str) -> str:
        if college_state and college_state in STATE_WEIGHTS:
            local_chance = self._local_chance(college_state, college_tier)
            roll = self.rng.random()
            if roll < local_chance:
                return college_state
            if roll < local_chance + self._regional_chance(college_tier):
                return self._weighted_state(
                    {
                        state: weight
                        for state, weight in STATE_WEIGHTS.items()
                        if STATE_REGIONS.get(state) == STATE_REGIONS.get(college_state)
                    }
                )
        return self._weighted_state(STATE_WEIGHTS)

    @staticmethod
    def _local_chance(college_state: str, college_tier: str) -> float:
        tier = str(college_tier or "").lower()
        if tier == "small":
            base = 0.56
        elif tier == "regular":
            base = 0.42
        elif tier == "power":
            base = 0.23
        else:
            base = 0.18
        if college_state in NATIONAL_POWER_STATES and tier == "power":
            base -= 0.07
        if college_state in {"TX", "FL", "CA", "GA", "OH", "LA"}:
            base += 0.04
        return max(0.08, min(0.64, base))

    @staticmethod
    def _regional_chance(college_tier: str) -> float:
        tier = str(college_tier or "").lower()
        if tier == "small":
            return 0.24
        if tier == "regular":
            return 0.24
        if tier == "power":
            return 0.20
        return 0.14

    def _international_hometown(self, birth_country: str) -> HometownProfile:
        pool = INTERNATIONAL_HOMETOWNS.get(birth_country)
        if not pool:
            return HometownProfile(city=str(birth_country), state="", region="International")
        city = self._weighted_choice(pool)
        return HometownProfile(city=city, state="", region="International")

    def _weighted_state(self, weights: dict[str, float]) -> str:
        return self._weighted_choice(weights)

    def _weighted_choice(self, weights: dict[str, float]) -> str:
        choices = list(weights)
        values = [float(weights[item]) for item in choices]
        return self.rng.choices(choices, weights=values, k=1)[0]
