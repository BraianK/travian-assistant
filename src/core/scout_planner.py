"""Find the real target of an attacker by scouting at his possible departures.

For an attacker whose slowest unit is known (from the travel time of his
attack) but whose tournament square level and real target are not, every
possible target (name, position, arrival time) and every square level gives
one possible departure second ``D`` of his army:

* two scouts land in his village at ``D - 1`` and two at ``D + 1``;
* his hero's experience is read from the hero ranking before the first pair
  lands (``D - 3``), after it (``D``) and after the second pair (``D + 2``).

A hero at home kills the scouts and gains experience. If the experience grew
after the first pair but not after the second, the hero left with the army
in that second: that target and square level are the real ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

try:
    from src.core.tournament_square import travel_seconds
except ModuleNotFoundError:  # pragma: no cover - script fallback
    from core.tournament_square import travel_seconds

MOVEMENT_ATTACK = 3
MOVEMENT_SIEGE = 47
MOVEMENT_SPY = 6
SIEGE_FACTOR = 2.0
SCOUT_OFFSETS = (-1, 1)
XP_READ_OFFSETS = (-3, 0, 2)

VERDICT_LEFT = "left"
VERDICT_HOME = "home"
VERDICT_UNKNOWN = "unknown"
VERDICT_LABELS = {
    VERDICT_LEFT: "BOHATER WYSZEDŁ — prawdziwy cel",
    VERDICT_HOME: "bohater w domu — nie ten cel/PT",
    VERDICT_UNKNOWN: "bez wniosku (EXP nie wzrósł po pierwszej parze)",
}


@dataclass
class ScoutTarget:
    name: str
    x: int
    y: int
    arrival: float


@dataclass
class Candidate:
    """One possible departure of the attacker's army."""

    target: str
    level: int
    departure: float
    sends: list[float] = field(default_factory=list)   # our send times (server)
    send_status: list[str] = field(default_factory=list)
    xp: dict[int, int | None] = field(default_factory=dict)  # offset -> xp
    verdict: str | None = None


def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def attack_travel(distance_fields: float, speed: float, level: int, siege: bool) -> float:
    """Travel time of the attacker's army as the game counts it.

    A siege takes twice as long; the game truncates the (doubled) time to
    whole seconds — this matches its departure times to the second.
    """
    seconds = travel_seconds(distance_fields, speed, level)
    if siege:
        seconds *= SIEGE_FACTOR
    return float(math.floor(seconds + 1e-6))


def plan_candidates(
    source: tuple[int, int],
    targets: list[ScoutTarget],
    speed: float,
    levels: list[int],
    siege: bool,
    scout_travel: float,
    now: float,
    lead_seconds: float = 5.0,
) -> list[Candidate]:
    """Departures for every target and level whose scouts can still be sent."""
    candidates = []
    for target in targets:
        fields = distance(source[0], source[1], target.x, target.y)
        for level in levels:
            departure = round(target.arrival - attack_travel(fields, speed, level, siege))
            sends = [departure + offset - scout_travel for offset in SCOUT_OFFSETS]
            if min(sends) < now + lead_seconds:
                continue
            candidates.append(Candidate(
                target=target.name, level=level, departure=departure,
                sends=sends, send_status=["zaplanowany"] * len(sends),
            ))
    candidates.sort(key=lambda candidate: candidate.departure)
    return candidates


def judge(xp: dict[int, int | None]) -> str | None:
    """Verdict from the three experience readings (None while incomplete)."""
    before, middle, after = (xp.get(offset) for offset in XP_READ_OFFSETS)
    if before is None or middle is None or after is None:
        return None
    if middle <= before:
        return VERDICT_UNKNOWN
    return VERDICT_HOME if after > middle else VERDICT_LEFT


def level_range(estimate: int | None, spread: int, maximum: int = 20) -> list[int]:
    """Square levels to check: the estimate ± spread, or every level."""
    if estimate is None:
        return list(range(0, maximum + 1))
    return list(range(max(0, estimate - spread), min(maximum, estimate + spread) + 1))
