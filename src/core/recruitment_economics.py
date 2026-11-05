"""Estimates resource use from continuously running recruitment queues."""

from collections.abc import Mapping
from math import ceil, isfinite

RESOURCE_NAMES_BY_ID = {
    "1": "wood",
    "2": "clay",
    "3": "iron",
    "4": "grain",
}


def _is_positive_number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
        and value > 0
    )


def effective_training_interval_seconds(row: Mapping) -> float | None:
    """Return seconds per queued unit across available parallel buildings.

    Every building able to train the unit (e.g. barracks and great barracks)
    works through its own queue, so the combined rate is the sum of the
    individual rates.  Missing or invalid timing for any building makes the
    estimate unavailable instead of silently over- or under-estimating it.
    """
    training_times = row.get("training_times_seconds")
    if not isinstance(training_times, list) or not training_times:
        legacy_time = row.get("training_time_seconds")
        training_times = [legacy_time] if legacy_time is not None else []
    if not training_times or not all(
        _is_positive_number(value) for value in training_times
    ):
        return None
    return 1 / sum(1 / value for value in training_times)


def recruitment_queue_duration_seconds(row: Mapping) -> float | None:
    """Return the remaining queue time for one unit type.

    Uses the exact remaining time read from the game when available,
    otherwise estimates it from the queued count and training interval.
    """
    remaining = row.get("queue_seconds_remaining")
    if (
        not isinstance(remaining, bool)
        and isinstance(remaining, (int, float))
        and isfinite(remaining)
        and remaining >= 0
    ):
        return float(remaining)

    count = row.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    if count == 0:
        return 0.0
    interval = effective_training_interval_seconds(row)
    if interval is None:
        return None
    return count * interval


def recruitment_queue_threshold_for_duration(
    row: Mapping,
    target_minutes: int,
) -> int | None:
    """Return the largest unit count whose estimated queue is below target."""
    interval = effective_training_interval_seconds(row)
    if interval is None:
        return None
    return max(0, ceil(target_minutes * 60 / interval) - 1)


def recruitment_batch_for_duration(
    row: Mapping,
    target_minutes: int,
    maximum_batch_size: int,
) -> int | None:
    """Return units needed to bring the estimated queue up to its target.

    The result is at least one unit and never exceeds *maximum_batch_size*.
    Affordability is checked live in the game right before the order is sent.
    """
    interval = effective_training_interval_seconds(row)
    current_duration = recruitment_queue_duration_seconds(row)
    if interval is None or current_duration is None:
        return None
    missing_duration = target_minutes * 60 - current_duration
    required_units = ceil(missing_duration / interval)
    return max(1, min(maximum_batch_size, required_units))


def estimate_recruitment_costs_per_hour(
    queue_rows: list[dict],
    village_settings: Mapping,
    automation_enabled: bool,
) -> dict[str, dict[str, float | None]]:
    """Estimate hourly resource costs for enabled units training continuously."""
    village_ids = {str(row.get("village_id", "")) for row in queue_rows}
    estimates = {
        village_id: {resource: 0.0 for resource in RESOURCE_NAMES_BY_ID.values()}
        for village_id in village_ids
    }
    incomplete_villages: set[str] = set()
    active_rows = []

    if not automation_enabled:
        return estimates

    for row in queue_rows:
        village_id = str(row.get("village_id", ""))
        settings = village_settings.get(village_id)
        if settings is None or not _setting(settings, "enabled", False):
            continue
        selected_units = _setting(settings, "units", set())
        if str(row.get("unit_id", row.get("unit_type", ""))) not in {
            str(unit) for unit in selected_units
        }:
            continue
        if row.get("building_available") is False:
            continue
        active_rows.append(row)

    units_by_building: dict[tuple[str, int], set[str]] = {}
    for row in active_rows:
        building_types = row.get("building_types")
        if not isinstance(building_types, list):
            building_types = [row.get("building_type")]
        for building_type in building_types:
            if (
                isinstance(building_type, int)
                and not isinstance(building_type, bool)
            ):
                key = (str(row.get("village_id", "")), building_type)
                units_by_building.setdefault(key, set()).add(
                    str(row.get("unit_id", row.get("unit_type", "")))
                )
    incomplete_villages.update(
        key[0]
        for key, unit_ids in units_by_building.items()
        if len(unit_ids) > 1
    )

    for row in active_rows:
        village_id = str(row.get("village_id", ""))
        if village_id in incomplete_villages:
            continue
        costs = row.get("costs")
        training_times = row.get("training_times_seconds")
        if not isinstance(training_times, list):
            legacy_time = row.get("training_time_seconds")
            training_times = [legacy_time] if legacy_time is not None else []
        if (
            not isinstance(costs, Mapping)
            or not training_times
            or any(
                isinstance(training_time, bool)
                or not isinstance(training_time, (int, float))
                or not isfinite(training_time)
                or training_time <= 0
                for training_time in training_times
            )
            or any(
                resource not in costs
                or isinstance(costs[resource], bool)
                or not isinstance(costs[resource], (int, float))
                or not isfinite(costs[resource])
                or costs[resource] < 0
                for resource in RESOURCE_NAMES_BY_ID.values()
            )
        ):
            incomplete_villages.add(village_id)
            continue

        units_per_hour = sum(3600 / value for value in training_times)
        for resource in RESOURCE_NAMES_BY_ID.values():
            estimates[village_id][resource] += costs[resource] * units_per_hour

    for village_id in incomplete_villages:
        estimates[village_id] = {
            resource: None for resource in RESOURCE_NAMES_BY_ID.values()
        }
    return estimates


def _setting(settings, name: str, default):
    if isinstance(settings, Mapping):
        return settings.get(name, default)
    return getattr(settings, name, default)
