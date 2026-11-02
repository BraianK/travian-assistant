"""Estimates resource use from continuously running recruitment queues."""

from collections.abc import Mapping
from math import isfinite

RESOURCE_NAMES_BY_ID = {
    "1": "wood",
    "2": "clay",
    "3": "iron",
    "4": "grain",
}


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
