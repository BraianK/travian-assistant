"""Pure planning logic for executing TK-Tools attack plans in Travian Kingdoms.

TK-Tools (https://tk-tools.fr) publishes alliance attack plans: every planned
attack names a source village, a target, an exact arrival time and one or more
waves (fakes, real attacks with catapults, conquests). This module turns such
a plan into concrete troop sends:

* fake waves get ``N - 1`` filler units (cheapest first, never scouts) plus one
  siege unit whose speed matches the plan (3 = catapult, 4 = ram);
* real attacks take every offensive unit except the reserve that later sends
  from the same village still need, plus the catapults the plan asks for;
* catapults aim at the plan's targets, or at random buildings (two targets
  once the rally point reaches level 20);
* the departure time is recomputed from the game's own travel durations so
  the arrival time from the plan is respected exactly.

Nothing here talks to the game or to TK-Tools, which keeps it testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import hypot

TRIBE_ROMANS = 1
TRIBE_TEUTONS = 2
TRIBE_GAULS = 3
TRIBE_BY_NAME = {"ROMANS": TRIBE_ROMANS, "TEUTONS": TRIBE_TEUTONS, "GAULS": TRIBE_GAULS}
TRIBE_NAMES = {TRIBE_ROMANS: "Rzymianie", TRIBE_TEUTONS: "Germanie", TRIBE_GAULS: "Galowie"}

# Unit numbers are tribe-local (1-10) as used by ``troops/send``; 11 is the hero.
RAM = 7
CATAPULT = 8
CHIEF = 9
SETTLER = 10
HERO = 11

OFFENSIVE_UNITS = {
    TRIBE_ROMANS: (1, 3, 5, 6),   # Legionnaire, Imperian, E. Imperatoris, E. Caesaris
    TRIBE_TEUTONS: (1, 3, 6),     # Clubswinger, Axefighter, Teutonic Knight
    TRIBE_GAULS: (2, 4, 6),       # Swordsman, Theutates Thunder, Haeduan
}
FAKE_FILLER_UNITS = {
    TRIBE_ROMANS: (1, 5, 6),      # Legionnaire, E. Imperatoris, E. Caesaris
    TRIBE_TEUTONS: (1,),          # Clubswinger
    TRIBE_GAULS: (1, 2),          # Phalanx, Swordsman
}
SCOUT_UNITS = {TRIBE_ROMANS: 4, TRIBE_TEUTONS: 4, TRIBE_GAULS: 3}

UNIT_NAMES = {
    TRIBE_ROMANS: {
        1: "Legionista", 2: "Pretorianin", 3: "Imperianin", 4: "Equites Legati",
        5: "Equites Imperatoris", 6: "Equites Caesaris", 7: "Taran",
        8: "Katapulta ognista", 9: "Senator", 10: "Osadnik", 11: "Bohater",
    },
    TRIBE_TEUTONS: {
        1: "Pałkarz", 2: "Oszczepnik", 3: "Topornik", 4: "Zwiadowca",
        5: "Paladyn", 6: "Rycerz teutoński", 7: "Taran", 8: "Katapulta",
        9: "Wódz", 10: "Osadnik", 11: "Bohater",
    },
    TRIBE_GAULS: {
        1: "Falanga", 2: "Miecznik", 3: "Tropiciel", 4: "Grom Teutatesa",
        5: "Jeździec druidzki", 6: "Haeduan", 7: "Taran", 8: "Trebusz",
        9: "Herszt", 10: "Osadnik", 11: "Bohater",
    },
}

MOVEMENT_ATTACK = 3
MOVEMENT_SUPPORT = 5
MOVEMENT_SIEGE = 47
SIEGE_MIN_UNITS = 1000

RANDOM_TARGET = 99
NO_TARGET = -1
RALLY_POINT_MAX_LEVEL = 20
SECOND_TARGET_MIN_CATAPULTS = 20

# TK-Tools catapult target codes -> Travian Kingdoms building types.
CATAPULT_TARGETS = {
    "building_random": RANDOM_TARGET,
    "building_none": NO_TARGET,
    "building_woodCutter": 1,
    "building_clayPit": 2,
    "building_ironMine": 3,
    "building_cropland": 4,
    "building_sawmill": 5,
    "building_brickyard": 6,
    "building_ironFoundry": 7,
    "building_grainMill": 8,
    "building_bakery": 9,
    "building_warehouse": 10,
    "building_granary": 11,
    "building_smithy": 13,
    "building_tournamentSquare": 14,
    "building_mainBuilding": 15,
    "building_rallyPoint": 16,
    "building_marketPlace": 17,
    "building_embassy": 18,
    "building_barracks": 19,
    "building_stable": 20,
    "building_workshop": 21,
    "building_academy": 22,
    "building_cranny": 23,
    "building_townHall": 24,
    "building_residence": 25,
    "building_palace": 26,
    "building_treasury": 27,
    "building_tradeOffice": 28,
    "building_greatBarracks": 29,
    "building_greatStable": 30,
    "building_brewery": 35,
    "building_greatWarehouse": 38,
    "building_greatGranary": 39,
    "building_wonder": 40,
    "building_horseDrinkingTrough": 41,
    "building_healingTent": 46,
}

# TK-Tools "moveSpeed" of a planned attack is the base speed of its slowest
# unit: 3 fields/h is a catapult (trebuchet), 4 fields/h is a ram.
SIEGE_UNIT_BY_MOVE_SPEED = {3: CATAPULT, 4: RAM}

KIND_FAKE = "FAKE"
KIND_ATTACK = "ATTACK"
KIND_CONQUEST = "CONQUEST"
SUPPORTED_KINDS = {KIND_FAKE, KIND_ATTACK, KIND_CONQUEST}

# A send may still leave one second late: it then lands one second after the
# planned arrival, which the alliance accepts.
LATE_TOLERANCE_SECONDS = 1


class PlanError(ValueError):
    """The plan cannot be executed as written."""


@dataclass(frozen=True)
class PlanWave:
    index: int
    kind: str
    fake_units: int | None = None
    has_off: bool = False
    has_hero: bool = False
    catapults: int = 0
    chiefs: int = 0
    target1: str | None = None
    target2: str | None = None


@dataclass(frozen=True)
class PlannedAttack:
    id: str
    operation_id: str
    operation_name: str
    source_village_id: str
    source_village_name: str
    target_village_id: str
    target_name: str
    target_player: str
    target_x: int | None
    target_y: int | None
    departure: float
    arrival: float
    travel_seconds: int
    move_speed: int
    pt_level: int
    is_siege: bool
    state: str
    waves: tuple[PlanWave, ...]
    strict_arrival: bool = True

    @property
    def is_pending(self) -> bool:
        return self.state in ("NONE", "", None)

    @property
    def has_hero(self) -> bool:
        return any(wave.has_hero for wave in self.waves)

    @property
    def label(self) -> str:
        kinds = sorted({wave.kind for wave in self.waves})
        return f"{self.source_village_name} → {self.target_name} ({', '.join(kinds)})"


@dataclass(frozen=True)
class FakeRule:
    """Default fake size (filler units, the siege unit comes on top) by date."""

    tier2_at: datetime
    tier3_at: datetime
    base_units: int = 32
    tier2_units: int = 36
    tier3_units: int = 44

    def filler_units(self, when: float) -> int:
        moment = datetime.fromtimestamp(when, tz=timezone.utc)
        tier2 = _aware(self.tier2_at)
        tier3 = _aware(self.tier3_at)
        if moment >= tier3:
            return self.tier3_units
        if moment >= tier2:
            return self.tier2_units
        return self.base_units


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.astimezone(timezone.utc)
    return value


@dataclass
class VillageArmy:
    """Units currently at home in one village."""

    village_id: str
    tribe: int
    units: dict[int, int]
    hero_home: bool = False
    rally_point_level: int = 0
    tournament_square_level: int = 0
    unit_costs: dict[int, int] = field(default_factory=dict)

    def available(self, unit: int) -> int:
        return max(0, int(self.units.get(unit, 0)))


@dataclass
class Demand:
    """Units that later sends from the same village still need."""

    filler: int = 0
    units: dict[int, int] = field(default_factory=dict)

    def add(self, unit: int, count: int) -> None:
        if count > 0:
            self.units[unit] = self.units.get(unit, 0) + count


@dataclass(frozen=True)
class WaveSend:
    attack_id: str
    wave_index: int
    kind: str
    units: dict[int, int]
    movement_type: int
    catapult_targets: tuple[int, ...]
    notes: tuple[str, ...] = ()

    @property
    def total_units(self) -> int:
        return sum(count for unit, count in self.units.items() if unit != HERO)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        raise PlanError("Brak czasu w planie.")
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text).timestamp()


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_wave(row: dict) -> PlanWave:
    kind = str(row.get("attackType") or "").upper()
    if kind == KIND_CONQUEST:
        return PlanWave(
            index=_int(row.get("waveIndex")),
            kind=kind,
            has_off=bool(row.get("conquestHasOff")),
            has_hero=bool(row.get("conquestHasHero")),
            catapults=_int(row.get("conquestNbCatapults")),
            chiefs=_int(row.get("conquestNbChiefs")),
            target1=row.get("conquestTarget1") or None,
            target2=row.get("conquestTarget2") or None,
        )
    return PlanWave(
        index=_int(row.get("waveIndex")),
        kind=kind,
        fake_units=_int(row.get("fakeNbUnits")) or None,
        has_off=bool(row.get("attackHasOff")),
        has_hero=bool(row.get("attackHasHero")),
        catapults=_int(row.get("attackNbCatapults")),
        target1=row.get("attackTarget1") or None,
        target2=row.get("attackTarget2") or None,
    )


def parse_planned_attack(row: dict) -> PlannedAttack:
    """Parse one sanitized planner row (see ``TkToolsSession``)."""
    waves = tuple(sorted(
        (parse_wave(wave) for wave in row.get("waves") or []),
        key=lambda wave: wave.index,
    ))
    if not waves:
        raise PlanError("Atak w planie nie ma fal.")
    return PlannedAttack(
        id=str(row["id"]),
        operation_id=str(row.get("operationId") or ""),
        operation_name=str(row.get("operationName") or ""),
        source_village_id=str(row["sourceVillageId"]),
        source_village_name=str(row.get("sourceVillageName") or ""),
        target_village_id=str(row.get("targetVillageId") or ""),
        target_name=str(row.get("targetName") or ""),
        target_player=str(row.get("targetPlayer") or ""),
        target_x=row.get("targetX"),
        target_y=row.get("targetY"),
        departure=_timestamp(row["departureTime"]),
        arrival=_timestamp(row["arrivalTime"]),
        travel_seconds=_int(row.get("travelTimeSeconds")),
        move_speed=_int(row.get("moveSpeed")),
        pt_level=_int(row.get("ptLevel")),
        is_siege=bool(row.get("isSiege")),
        state=str(row.get("state") or "NONE"),
        waves=waves,
        strict_arrival=bool(row.get("strictArrival", True)),
    )


def parse_planner(rows: list[dict], own_player_id: str) -> tuple[list[PlannedAttack], list[str]]:
    """Return the player's own attacks from the planner plus parse problems."""
    attacks: list[PlannedAttack] = []
    problems: list[str] = []
    for row in rows:
        if str(row.get("sourcePlayerId") or "") != str(own_player_id):
            continue
        if row.get("isSitted"):
            continue
        try:
            attacks.append(parse_planned_attack(row))
        except (KeyError, PlanError, ValueError) as error:
            problems.append(f"Pominięto atak {row.get('id')}: {error}")
    attacks.sort(key=lambda attack: (attack.departure, attack.id))
    return attacks, problems


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def fake_total_units(wave: PlanWave, departure: float, rule: FakeRule) -> int:
    """Total fake size including the siege unit (plan value wins)."""
    if wave.fake_units:
        return wave.fake_units
    return rule.filler_units(departure) + 1


def fake_siege_unit(attack: PlannedAttack) -> int:
    unit = SIEGE_UNIT_BY_MOVE_SPEED.get(attack.move_speed)
    if unit is None:
        raise PlanError(
            f"Plan nie określa maszyny dla fejka (prędkość {attack.move_speed})."
        )
    return unit


def demand_of_wave(attack: PlannedAttack, wave: PlanWave, rule: FakeRule) -> Demand:
    demand = Demand()
    if wave.kind == KIND_FAKE:
        total = fake_total_units(wave, attack.departure, rule)
        demand.filler += max(0, total - 1)
        siege = SIEGE_UNIT_BY_MOVE_SPEED.get(attack.move_speed)
        if siege is not None:
            demand.add(siege, 1)
    elif wave.kind in (KIND_ATTACK, KIND_CONQUEST):
        demand.add(CATAPULT, wave.catapults)
        demand.add(CHIEF, wave.chiefs)
    return demand


def reserved_demand(
    later: list[tuple[PlannedAttack, PlanWave]],
    rule: FakeRule,
) -> Demand:
    total = Demand()
    for attack, wave in later:
        demand = demand_of_wave(attack, wave, rule)
        total.filler += demand.filler
        for unit, count in demand.units.items():
            total.add(unit, count)
    return total


def filler_order(army: VillageArmy) -> list[int]:
    """Filler unit types sorted from the cheapest to the most expensive."""
    candidates = [
        unit for unit in FAKE_FILLER_UNITS.get(army.tribe, ())
        if unit != SCOUT_UNITS.get(army.tribe)
    ]
    return sorted(candidates, key=lambda unit: (army.unit_costs.get(unit, 10**9), unit))


def _take_fillers(pool: dict[int, int], order: list[int], count: int) -> dict[int, int] | None:
    taken: dict[int, int] = {}
    remaining = count
    for unit in order:
        if remaining <= 0:
            break
        amount = min(pool.get(unit, 0), remaining)
        if amount > 0:
            taken[unit] = amount
            remaining -= amount
    return taken if remaining <= 0 else None


def catapult_targets(
    wave: PlanWave,
    rally_point_level: int,
    catapults: int = SECOND_TARGET_MIN_CATAPULTS,
) -> tuple[int, ...]:
    """Targets for ``troops/send``; random when the plan leaves them empty.

    A second target needs rally point level 20 and at least 20 catapults.
    """
    first = CATAPULT_TARGETS.get(wave.target1 or "", RANDOM_TARGET)
    if first == NO_TARGET:
        first = RANDOM_TARGET
    targets = [first]
    if (
        rally_point_level >= RALLY_POINT_MAX_LEVEL
        and catapults >= SECOND_TARGET_MIN_CATAPULTS
    ):
        second = CATAPULT_TARGETS.get(wave.target2 or "", RANDOM_TARGET)
        if second != NO_TARGET:
            targets.append(second)
    return tuple(targets)


def compose_wave(
    attack: PlannedAttack,
    wave: PlanWave,
    army: VillageArmy,
    pool: dict[int, int],
    reserve: Demand,
    rule: FakeRule,
) -> WaveSend:
    """Build one wave from *pool* (mutated) while keeping *reserve* untouched."""
    tribe = army.tribe
    order = filler_order(army)
    notes: list[str] = []

    def free(unit: int) -> int:
        return max(0, pool.get(unit, 0) - reserve.units.get(unit, 0))

    units: dict[int, int] = {}
    if wave.kind == KIND_FAKE:
        siege = fake_siege_unit(attack)
        if free(siege) < 1:
            raise PlanError(
                f"Brak jednostki {UNIT_NAMES[tribe][siege]} na fejka."
            )
        filler_count = fake_total_units(wave, attack.departure, rule) - 1
        fillers = _take_fillers(pool, order, filler_count)
        if fillers is None:
            raise PlanError(f"Za mało jednostek na fejka ({filler_count}+1).")
        units.update(fillers)
        units[siege] = units.get(siege, 0) + 1
    elif wave.kind in (KIND_ATTACK, KIND_CONQUEST):
        if wave.has_off:
            filler_reserve = reserve.filler
            free_fillers = {unit: pool.get(unit, 0) for unit in order}
            kept = _take_fillers(free_fillers, order, filler_reserve) or free_fillers
            for unit in OFFENSIVE_UNITS[tribe]:
                amount = pool.get(unit, 0) - kept.get(unit, 0)
                if amount > 0:
                    units[unit] = amount
            rams = free(RAM)
            if rams > 0:
                units[RAM] = rams
        if wave.catapults:
            catapults = min(wave.catapults, free(CATAPULT))
            if catapults < wave.catapults:
                notes.append(
                    f"Katapult tylko {catapults} z {wave.catapults} wymaganych."
                )
            if catapults > 0:
                units[CATAPULT] = catapults
        if wave.kind == KIND_CONQUEST and wave.chiefs:
            chiefs = min(wave.chiefs, pool.get(CHIEF, 0))
            if chiefs < wave.chiefs:
                raise PlanError(
                    f"Za mało zarządców: {chiefs} z {wave.chiefs}."
                )
            units[CHIEF] = chiefs
    else:
        raise PlanError(f"Typ fali {wave.kind} nie jest obsługiwany.")

    if not any(count > 0 for count in units.values()):
        raise PlanError("Fala nie ma żadnych jednostek do wysłania.")

    for unit, count in units.items():
        pool[unit] = pool.get(unit, 0) - count

    if wave.has_hero:
        if army.hero_home:
            units[HERO] = 1
            army.hero_home = False
        else:
            notes.append("Bohatera nie ma w wiosce — fala idzie bez niego.")

    total = sum(count for unit, count in units.items() if unit != HERO)
    movement = MOVEMENT_ATTACK
    if attack.is_siege:
        if total >= SIEGE_MIN_UNITS and units.get(RAM, 0) >= 1:
            movement = MOVEMENT_SIEGE
        else:
            notes.append("Oblężenie wymaga 1000 jednostek i tarana — wysyłam atak.")

    targets: tuple[int, ...] = ()
    if units.get(CATAPULT, 0) > 0:
        targets = catapult_targets(wave, army.rally_point_level, units[CATAPULT])

    return WaveSend(
        attack_id=attack.id,
        wave_index=wave.index,
        kind=wave.kind,
        units=units,
        movement_type=movement,
        catapult_targets=targets,
        notes=tuple(notes),
    )


def compose_attack(
    attack: PlannedAttack,
    army: VillageArmy,
    later: list[tuple[PlannedAttack, PlanWave]],
    rule: FakeRule,
) -> list[WaveSend]:
    """Compose all waves of *attack*; *later* lists the village's later sends."""
    pool = {unit: army.available(unit) for unit in range(1, 11)}
    sends: list[WaveSend] = []
    for position, wave in enumerate(attack.waves):
        following = [(attack, other) for other in attack.waves[position + 1:]]
        reserve = reserved_demand(following + later, rule)
        sends.append(compose_wave(attack, wave, army, pool, reserve, rule))
    return sends


def later_sends(
    attack: PlannedAttack,
    attacks: list[PlannedAttack],
    finished: set[str],
) -> list[tuple[PlannedAttack, PlanWave]]:
    """Waves from the same village that leave after *attack*."""
    return [
        (other, wave)
        for other in attacks
        if other.id != attack.id
        and other.id not in finished
        and other.source_village_id == attack.source_village_id
        and (other.departure, other.id) > (attack.departure, attack.id)
        for wave in other.waves
    ]


def exact_departure(arrival: float, units: dict[int, int], durations: dict) -> float:
    """Departure (server time) that lands *units* exactly at *arrival*."""
    slowest = 0
    for unit, count in units.items():
        if count <= 0:
            continue
        duration = durations.get(str(unit), durations.get(unit))
        if duration is None:
            raise PlanError(f"Gra nie podała czasu przemarszu dla jednostki {unit}.")
        slowest = max(slowest, int(duration))
    if slowest <= 0:
        raise PlanError("Nie udało się ustalić czasu przemarszu.")
    return arrival - slowest


def describe_units(units: dict[int, int], tribe: int) -> str:
    names = UNIT_NAMES.get(tribe, {})
    parts = [
        f"{names.get(unit, f'#{unit}')} {count}"
        for unit, count in sorted(units.items())
        if count > 0
    ]
    return ", ".join(parts) or "—"


# ---------------------------------------------------------------------------
# Hero evasion
# ---------------------------------------------------------------------------

def nearest_village(
    home: tuple[int, int],
    villages: dict[str, tuple[int, int]],
    exclude: str,
) -> str | None:
    best: tuple[float, str] | None = None
    for village_id, (x, y) in villages.items():
        if village_id == exclude:
            continue
        distance = hypot(x - home[0], y - home[1])
        if best is None or distance < best[0]:
            best = (distance, village_id)
    return best[1] if best else None


def hero_recall_time(departure: float, return_seconds: int, margin: int = 1) -> float:
    """Server time to recall the hero so it is home *margin* s before *departure*."""
    return departure - margin - return_seconds


def can_park_hero(
    now: float,
    next_hero_departure: float | None,
    one_way_seconds: int,
    safety_seconds: int = 60,
) -> bool:
    """True if the hero can go to the parking village and still return in time."""
    if next_hero_departure is None:
        return True
    return now + 2 * one_way_seconds + safety_seconds < next_hero_departure
