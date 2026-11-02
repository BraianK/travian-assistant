"""
Game data normalizer for converting parsed responses into application models.

This module handles normalization of raw game data into consistent formats
compatible with the core data models (Village, ResourcePoint, TroopUnit, etc.).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class GameDataNormalizer:
    """Normalizes parsed Travian game data into application model formats.
    
    This class bridges the gap between raw API responses and the structured
    data models used throughout the application.
    """

    # Mapping of internal building names to display names
    BUILDING_NAMES = {
        "1": "Woodcutter",
        "2": "Clay pit",
        "3": "Iron mine",
        "4": "Cropland",
        "5": "Sawmill",
        "6": "Brickyard",
        "7": "Iron foundry",
        "8": "Grain mill",
        "9": "Bakery",
        "10": "Warehouse",
        "11": "Granary",
        "12": "Smithy",
        "13": "Armoury",
        "14": "Tournament square",
        "15": "Main building",
        "16": "Rally point",
        "17": "Marketplace",
        "18": "Embassy",
        "19": "Barracks",
        "20": "Stable",
        "21": "Workshop",
        "22": "Academy",
        "23": "Cranny",
        "24": "Town hall",
        "25": "Residence",
        "26": "Palace",
        "27": "Treasury",
        "28": "Trade office",
        "29": "Great barracks",
        "30": "Great stable",
        "31": "City wall",
        "32": "Earth wall",
        "33": "Palisade",
        "34": "Stonemason's lodge",
        "35": "Brewery",
        "36": "Trapper",
        "37": "Hero's mansion",
        "38": "Great warehouse",
        "39": "Great granary",
        "40": "Wonder of the World",
        "farm": "Farm",
        "warehouse": "Warehouse",
        "main": "Main Tent",
        "troop": "Training Camp",
        "wall": "Wall",
        "market": "Market",
        "trade": "Trade Route",
        "resource_wood": "Wooden Watchtower",
        "resource_clay": "Clay Stack",
        "resource_iron": "Iron Mine",
        "resource_grain": "Grain Store",
    }

    # Mapping of internal unit names to display names and types
    UNIT_NAMES = {
        "spear": {"name": "Swordsman", "type": "infantry"},
        "sword": {"name": "Spearman", "type": "infantry"},
        "axe": {"name": "Axeman", "type": "infantry"},
        "archer": {"name": "Arrow Shooter", "type": "ranged"},
        "sniper": {"name": "Sniper", "type": "ranged"},
        "light": {"name": "Light Cavalry", "type": "cavalry"},
        "heavy": {"name": "Heavy Cavalry", "type": "cavalry"},
        "catapult": {"name": "Catapult", "type": "siege"},
    }

    # Combat bonuses per unit type (base values)
    COMBAT_BONUSES = {
        "spear": {"attack": 10, "defense": 25, "strength": 15},
        "sword": {"attack": 25, "defense": 15, "strength": 40},
        "axe": {"attack": 35, "defense": 10, "strength": 45},
        "archer": {"attack": 30, "defense": 5, "strength": 35},
        "sniper": {"attack": 25, "defense": 10, "strength": 35},
        "light": {"attack": 20, "defense": 15, "strength": 35},
        "heavy": {"attack": 40, "defense": 20, "strength": 60},
        "catapult": {"attack": 50, "defense": 0, "strength": 50},
    }

    def normalize_village_state(
        self, parsed_data: dict
    ) -> dict:
        """Normalize parsed village state data into standard format.
        
        Args:
            parsed_data: Parsed village state from ResponseParser.
            
        Returns:
            Normalized village data dictionary.
        """
        if not parsed_data:
            return {}

        # Extract base coordinates
        x = parsed_data.get("x", 0)
        y = parsed_data.get("y", 0)
        
        # Calculate resource fill percentage
        resources = parsed_data.get("resources", {})
        storage_occupied = parsed_data.get("storage_occupied", 0)
        storage_max = parsed_data.get("storage_max", 1000)
        
        total_resources = sum(resources.values())
        fill_percentage = (total_resources / storage_max) if storage_max > 0 else 0

        return {
            "village_id": parsed_data.get("village_id", ""),
            "name": parsed_data.get("name", ""),
            "coordinates": {
                "x": x,
                "y": y,
                "display": f"{x}/{y}/0",
            },
            "resources": {
                k: {
                    "current": v,
                    "max": storage_max,
                    "percentage": (v / storage_max * 100) if storage_max > 0 else 0,
                }
                for k, v in resources.items()
            },
            "storage_fill_percentage": fill_percentage,
            "population": parsed_data.get("population", 0),
            "building_count": parsed_data.get("building_count", 0),
            "timestamp": datetime.now().isoformat(),
        }

    def normalize_resources(
        self, parsed_data: dict
    ) -> dict:
        """Normalize parsed resource data into standard format.
        
        Args:
            parsed_data: Parsed resources from ResponseParser.
            
        Returns:
            Normalized resource data dictionary.
        """
        if not parsed_data:
            return {}

        current = parsed_data.get("current", {})
        max_capacity = parsed_data.get("max_capacity", {})
        production = parsed_data.get("production_per_hour", {})

        normalized_resources = {}
        for resource_type in ["wood", "clay", "iron", "grain"]:
            curr = current.get(resource_type, 0)
            cap = max_capacity.get(resource_type, 1000)
            prod = production.get(resource_type, 0)

            # Calculate time until storage is full
            time_to_full = None
            if prod > 0 and curr < cap:
                deficit = cap - curr
                hours_needed = deficit / prod
                time_to_full = int(hours_needed * 3600)  # Convert to seconds

            normalized_resources[resource_type] = {
                "current": curr,
                "max_capacity": cap,
                "fill_percentage": (curr / cap * 100) if cap > 0 else 0,
                "production_per_hour": prod,
                "time_to_full_seconds": time_to_full,
            }

        return {
            "resources": normalized_resources,
            "total_storage_used": sum(r.get("current", 0) for r in normalized_resources.values()),
            "total_storage_capacity": sum(r.get("max_capacity", 0) for r in normalized_resources.values()),
            "timestamp": datetime.now().isoformat(),
        }

    def normalize_buildings(
        self, parsed_data: dict
    ) -> dict:
        """Normalize parsed buildings data into standard format.
        
        Args:
            parsed_data: Parsed buildings from ResponseParser.
            
        Returns:
            Normalized building data dictionary.
        """
        if not parsed_data:
            return {}

        buildings = parsed_data.get("buildings", {})
        normalized_buildings = {}

        for building_id, building_info in buildings.items():
            level = building_info.get("level", 0)
            max_level = building_info.get("max_level", 10)
            is_upgrading = building_info.get("is_upgrading", False)
            upgrade_time = building_info.get("upgrade_complete_time")

            # Calculate next level upgrade time (approximate)
            upgrade_duration = None
            if is_upgrading and upgrade_time:
                try:
                    upgrade_dt = datetime.fromtimestamp(upgrade_time)
                    upgrade_duration = int((upgrade_dt - datetime.now()).total_seconds())
                except (OSError, OverflowError, ValueError, TypeError):
                    pass

            try:
                upgrade_complete_at = (
                    datetime.fromtimestamp(upgrade_time).isoformat()
                    if upgrade_time and is_upgrading
                    else None
                )
            except (OSError, OverflowError, ValueError, TypeError):
                upgrade_complete_at = None

            normalized_buildings[building_id] = {
                "id": building_id,
                "name": self.BUILDING_NAMES.get(building_id, building_id.title()),
                "level": level,
                "max_level": max_level,
                "is_upgrading": is_upgrading,
                "upgrade_duration_seconds": upgrade_duration,
                "upgrade_complete_at": upgrade_complete_at,
            }

        return {
            "buildings": normalized_buildings,
            "total_count": len(normalized_buildings),
            "upgrading_count": sum(
                1 for b in normalized_buildings.values() if b["is_upgrading"]
            ),
            "timestamp": datetime.now().isoformat(),
        }

    def normalize_units(
        self, parsed_data: dict
    ) -> dict:
        """Normalize parsed military units data into standard format.
        
        Args:
            parsed_data: Parsed units from ResponseParser.
            
        Returns:
            Normalized unit data dictionary.
        """
        if not parsed_data:
            return {}

        units = parsed_data.get("units", {})
        normalized_units = {}

        for unit_id, unit_info in units.items():
            count = unit_info.get("count", 0)
            is_training = unit_info.get("is_training", False)
            training_time = unit_info.get("training_complete_time")

            # Calculate training duration
            training_duration = None
            if is_training and training_time:
                try:
                    training_dt = datetime.fromtimestamp(training_time)
                    training_duration = int(
                        (training_dt - datetime.now()).total_seconds()
                    )
                except (ValueError, TypeError):
                    pass

            # Get combat stats
            bonus = self.COMBAT_BONUSES.get(unit_id, {})

            normalized_units[unit_id] = {
                "id": unit_id,
                "name": self.UNIT_NAMES.get(
                    unit_id, {"name": unit_id.title(), "type": "unknown"}
                )["name"],
                "unit_type": self.UNIT_NAMES.get(
                    unit_id, {"name": "", "type": "other"}
                )["type"],
                "count": count,
                "is_training": is_training,
                "training_duration_seconds": training_duration,
                "combat_bonus": {
                    "attack": bonus.get("attack", 0),
                    "defense": bonus.get("defense", 0),
                    "strength": bonus.get("strength", 0),
                },
            }

        return {
            "units": normalized_units,
            "total_count": parsed_data.get("total_count", 0),
            "training_count": sum(
                1 for u in normalized_units.values() if u["is_training"]
            ),
            "timestamp": datetime.now().isoformat(),
        }

    def normalize_attack(
        self, parsed_data: dict
    ) -> dict:
        """Normalize parsed attack data into standard format.
        
        Args:
            parsed_data: Parsed attack from ResponseParser.
            
        Returns:
            Normalized attack data dictionary.
        """
        if not parsed_data:
            return {}

        target = parsed_data.get("target", {})
        attacker = parsed_data.get("attacker", {})

        # Calculate distance between coordinates
        distance = self._calculate_distance(
            (attacker.get("x", 0), attacker.get("y", 0)),
            (target.get("x", 0), target.get("y", 0)),
        )

        return {
            "attack_id": parsed_data.get("attack_id", ""),
            "timestamp": parsed_data.get("timestamp", 0),
            "attacker": {
                "name": attacker.get("name", ""),
                "coordinates": attacker.get("coordinates", ""),
            },
            "target": {
                "name": target.get("name", ""),
                "coordinates": target.get("coordinates", ""),
            },
            "type": parsed_data.get("type", "attack"),
            "result": parsed_data.get("result", "pending"),
            "loot": parsed_data.get("loot", 0),
            "distance": distance,
            "timestamp_iso": (
                datetime.fromtimestamp(parsed_data.get("timestamp", 0)).isoformat()
                if parsed_data.get("timestamp")
                else None
            ),
        }

    def normalize_notification(
        self, parsed_data: list | dict
    ) -> list:
        """Normalize parsed notifications into standard format.
        
        Args:
            parsed_data: Parsed notifications from ResponseParser.
            
        Returns:
            List of normalized notification dictionaries.
        """
        if isinstance(parsed_data, dict):
            parsed_data = parsed_data.get("notifications", [])

        normalized = []
        for notif in parsed_data:
            timestamp = notif.get("timestamp", 0)
            normalized.append({
                "id": notif.get("id", ""),
                "type": notif.get("type", "info"),
                "title": notif.get("title", ""),
                "message": notif.get("message", ""),
                "timestamp": timestamp,
                "timestamp_iso": (
                    datetime.fromtimestamp(timestamp).isoformat()
                    if timestamp
                    else None
                ),
                "is_read": notif.get("is_read", False),
            })

        return normalized

    def normalize_alliance(
        self, parsed_data: dict
    ) -> dict:
        """Normalize parsed alliance data into standard format.
        
        Args:
            parsed_data: Parsed alliance from ResponseParser.
            
        Returns:
            Normalized alliance data dictionary.
        """
        if not parsed_data:
            return {}

        members = parsed_data.get("members", [])
        normalized_members = []

        for member in members:
            normalized_members.append({
                "name": member.get("name", ""),
                "role": member.get("role", "member"),
                "village_count": member.get("village_count", 0),
                "online": member.get("online", False),
            })

        return {
            "id": parsed_data.get("id", ""),
            "name": parsed_data.get("name", ""),
            "description": parsed_data.get("description", ""),
            "member_count": len(normalized_members),
            "members": normalized_members,
            "online_count": sum(1 for m in normalized_members if m["online"]),
            "timestamp": datetime.now().isoformat(),
        }

    @staticmethod
    def _calculate_distance(coord1: tuple, coord2: tuple) -> float:
        """Calculate Manhattan distance between two Travian coordinates.
        
        Args:
            coord1: First coordinate (x, y).
            coord2: Second coordinate (x, y).
            
        Returns:
            Manhattan distance as float.
        """
        x1, y1 = coord1
        x2, y2 = coord2
        return abs(x1 - x2) + abs(y1 - y2)

    def get_summary(self, village_data: dict) -> dict:
        """Generate a summary of normalized village data for dashboard display.
        
        Args:
            village_data: Normalized village state data.
            
        Returns:
            Dashboard-ready summary dictionary.
        """
        resources = village_data.get("resources", {})
        resource_summary = {}

        for resource_type, data in resources.items():
            resource_summary[resource_type] = {
                "current": data.get("current", 0),
                "max": data.get("max", 1000),
                "fill_percentage": data.get("fill_percentage", 0),
            }

        return {
            "village_id": village_data.get("village_id", ""),
            "name": village_data.get("name", ""),
            "coordinates": village_data.get("coordinates", {}).get(
                "display", "0/0/0"
            ),
            "resource_summary": resource_summary,
            "storage_fill_percentage": village_data.get(
                "storage_fill_percentage", 0
            ),
            "population": village_data.get("population", 0),
            "building_count": village_data.get("building_count", 0),
            "last_updated": village_data.get("timestamp", ""),
        }
