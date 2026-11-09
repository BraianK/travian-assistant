"""Estimate an attacker's tournament square level from the travel time.

Travel time in Travian Kingdoms (the troop speeds in ``troopConfig`` already
include the world speed): the first 20 fields go at the base speed, every
field beyond 20 is ``1 + 0.1 * level`` times faster with a tournament square.

The composition of a hostile attack is hidden, so every unit type of the
attacker's tribe is tried; only the types that give a whole level (within the
timing uncertainty) are kept.
"""

from __future__ import annotations

import math

MIN_DISTANCE = 20.0
BONUS_PER_LEVEL = 0.1
MAX_LEVEL = 20
# Detection happens on the game's push; allow a few seconds of slack.
DURATION_TOLERANCE_SECONDS = 5.0
ROBBER_TEXT = "0 (kryjówka)"


def travel_seconds(distance: float, speed: float, level: int) -> float:
    """Travel time in seconds of a unit with *speed* fields/hour."""
    if distance <= MIN_DISTANCE:
        return distance / speed * 3600.0
    rest = (distance - MIN_DISTANCE) / (1.0 + BONUS_PER_LEVEL * level)
    return (MIN_DISTANCE + rest) / speed * 3600.0


def _level_range(distance: float, duration: float, speed: float) -> tuple[float, float] | None:
    base = MIN_DISTANCE / speed * 3600.0
    rest = (distance - MIN_DISTANCE) / speed * 3600.0
    levels = []
    for value in (duration - DURATION_TOLERANCE_SECONDS, duration + DURATION_TOLERANCE_SECONDS):
        remaining = value - base
        if remaining <= 0:
            return None
        levels.append((rest / remaining - 1.0) / BONUS_PER_LEVEL)
    return min(levels), max(levels)


def estimate_levels(
    distance: float,
    duration: float,
    unit_speeds: dict[int, float],
) -> dict[int, list[int]]:
    """Map tournament square level -> unit numbers consistent with *duration*."""
    result: dict[int, list[int]] = {}
    if distance <= MIN_DISTANCE or duration <= 0:
        return result
    for unit, speed in sorted(unit_speeds.items()):
        if not speed or speed <= 0:
            continue
        bounds = _level_range(distance, duration, float(speed))
        if bounds is None:
            continue
        low, high = bounds
        for level in range(max(0, math.ceil(low)), min(MAX_LEVEL, math.floor(high)) + 1):
            result.setdefault(level, []).append(unit)
    return result


def slowest_units(distance: float, duration: float, unit_speeds: dict[int, float]) -> list[int]:
    """Units whose travel time matches *duration* (no square within 20 fields)."""
    if distance <= 0 or duration <= 0:
        return []
    return [
        unit for unit, speed in sorted(unit_speeds.items())
        if speed and speed > 0
        and abs(travel_seconds(distance, float(speed), 0) - duration) <= DURATION_TOLERANCE_SECONDS
    ]


def describe(
    distance: float | None,
    duration: float | None,
    unit_speeds: dict[int, float],
    unit_names: dict[int, str],
    robber: bool = False,
) -> str:
    """Short text for the attacks table."""
    if robber:
        return ROBBER_TEXT
    if distance is None or duration is None:
        return "—"
    if distance <= MIN_DISTANCE:
        slowest = slowest_units(distance, duration, unit_speeds)
        if not slowest:
            return "bez wpływu (≤20 pól)"
        names = ", ".join(unit_names.get(unit, f"#{unit}") for unit in slowest)
        return f"bez wpływu (≤20 pól); najwolniejsze: {names}"
    levels = estimate_levels(distance, duration, unit_speeds)
    if not levels:
        return "nie pasuje do żadnej jednostki"
    parts = []
    for level, units in sorted(levels.items(), key=lambda item: -item[0]):
        names = ", ".join(unit_names.get(unit, f"#{unit}") for unit in units)
        parts.append(f"{level} ({names})")
    return "; ".join(parts)
