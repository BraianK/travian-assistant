"""Per-village resource and troop planning recommendations."""

import math
import re


RESOURCE_TYPES = ("wood", "clay", "iron", "grain")

RESOURCE_FIELD_NAMES = {
    "woodcutter": "wood",
    "wood": "wood",
    "clay pit": "clay",
    "clay": "clay",
    "iron mine": "iron",
    "iron": "iron",
    "cropland": "grain",
    "crop": "grain",
    "grain": "grain",
}

RESOURCE_FIELD_BASE_COST = {
    "wood": 70,
    "clay": 90,
    "iron": 70,
    "grain": 20,
}

GAUL_UNIT_COSTS = {
    "Phalanx": {"wood": 100, "clay": 130, "iron": 55, "grain": 30},
    "Theutates Thunder": {"wood": 350, "clay": 250, "iron": 150, "grain": 100},
}

_RESOURCE_FIELD_COST_GROWTH = 1.67
_MAX_RESOURCE_FIELD_LEVEL = 20


def _time_to_afford(
    costs: dict[str, int],
    resources: dict[str, float],
    production_rates: dict[str, float],
) -> float | None:
    """Return hours until all costs are available, or None if they cannot accrue."""
    hours = 0.0
    for resource_type, cost in costs.items():
        current = resources.get(resource_type, 0)
        rate = production_rates.get(resource_type, 0)
        if current >= cost:
            continue
        if rate <= 0:
            return None
        hours = max(hours, (cost - current) / rate)

    if any(
        resources.get(resource_type, 0)
        + production_rates.get(resource_type, 0) * hours
        < cost
        for resource_type, cost in costs.items()
    ):
        return None
    return hours


def _field_resource(name: str) -> str | None:
    normalized = re.sub(r"\s+", " ", name.casefold()).strip()
    return RESOURCE_FIELD_NAMES.get(normalized)


def get_village_plan(
    resources: dict[str, float],
    production_rates: dict[str, float],
    resource_fields: list[dict],
    buildings: list[dict],
) -> dict:
    """Calculate next resource-field upgrade, troop affordability, and reminders."""
    normalized_resources = {
        key: max(0, float(resources.get(key, 0) or 0))
        for key in RESOURCE_TYPES
    }
    normalized_rates = {
        key: float(production_rates.get(key, 0) or 0)
        for key in RESOURCE_TYPES
    }
    upgrading_slots = {
        int(building.get("slot", 0) or 0)
        for building in buildings
        if building.get("upgrading")
    }

    field_upgrades = []
    for field in resource_fields:
        name = str(field.get("name", ""))
        resource_type = _field_resource(name)
        level = int(field.get("level", 0) or 0)
        slot = int(field.get("slot", 0) or 0)
        if (
            resource_type is None
            or level < 1
            or level >= _MAX_RESOURCE_FIELD_LEVEL
            or slot in upgrading_slots
        ):
            continue

        costs = {
            key: math.ceil(
                base_cost * _RESOURCE_FIELD_COST_GROWTH ** (level - 1)
            )
            for key, base_cost in RESOURCE_FIELD_BASE_COST.items()
        }
        wait_hours = _time_to_afford(
            costs, normalized_resources, normalized_rates
        )
        field_upgrades.append({
            "name": name,
            "slot": slot,
            "level": level,
            "target_level": level + 1,
            "costs": costs,
            "wait_hours": wait_hours,
        })

    next_field = min(
        field_upgrades,
        key=lambda item: (
            item["wait_hours"] is None,
            item["wait_hours"] if item["wait_hours"] is not None else math.inf,
            item["level"],
            item["slot"],
        ),
        default=None,
    )

    units = []
    for unit_name, costs in GAUL_UNIT_COSTS.items():
        affordable_count = min(
            int(normalized_resources[resource_type] // cost)
            for resource_type, cost in costs.items()
        )
        units.append({
            "name": unit_name,
            "costs": costs,
            "affordable_count": affordable_count,
            "next_unit_hours": _time_to_afford(
                costs, normalized_resources, normalized_rates
            ),
        })

    building_names = {
        str(building.get("name", "")).casefold()
        for building in buildings
    }
    return {
        "next_field": next_field,
        "units": units,
        "reminders": [
            {
                "building": "Koszary",
                "unit": "Phalanx",
                "available": any("barrack" in name for name in building_names),
            },
            {
                "building": "Stajnia",
                "unit": "Theutates Thunder",
                "available": any("stable" in name for name in building_names),
            },
        ],
    }
