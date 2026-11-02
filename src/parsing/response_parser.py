"""
Response parser module for converting raw Travian API JSON responses into
structured data models.

Travian Kingdoms communicates via AJAX endpoints that return JSON objects.
This module handles the parsing and normalization of those responses.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GameMessageParser:
    """Parses individual game message types from Travian API responses.
    
    Each endpoint returns a JSON object with specific structure. This class
    handles parsing those structures into usable data dictionaries.
    """

    @staticmethod
    def parse_village_state(data: dict) -> dict:
        """Parse village state response.
        
        Expected JSON structure:
        {
            "id": "12345",
            "name": "My Village",
            "coordinates": {"x": 123, "y": 456},
            "resources": {
                "wood": 12345,
                "clay": 23456,
                "iron": 34567,
                "crop": 45678
            },
            "storage_occupied": 90000,
            "storage_max": 100000,
            ...
        }
        
        Args:
            data: Raw JSON response from the game server.
            
        Returns:
            Parsed village state dictionary.
        """
        if not isinstance(data, dict):
            logger.warning("Invalid village state data: not a dict")
            return {}

        # Extract coordinates
        coords = data.get("coordinates", {})
        coordinate_str = f"{coords.get('x', 0)}/{coords.get('y', 0)}/0" if isinstance(coords, dict) else "0/0/0"

        # Extract resources
        resources = data.get("resources", {})
        
        return {
            "village_id": str(data.get("id", "")),
            "name": data.get("name", ""),
            "coordinates": coordinate_str,
            "x": coords.get("x", 0) if isinstance(coords, dict) else 0,
            "y": coords.get("y", 0) if isinstance(coords, dict) else 0,
            "resources": {
                "wood": int(resources.get("wood", 0)),
                "clay": int(resources.get("clay", 0)),
                "iron": int(resources.get("iron", 0)),
                "grain": int(resources.get("crop", 0)),
            },
            "storage_occupied": int(data.get("storage_occupied", 0)),
            "storage_max": int(data.get("storage_max", 1000)),
            "population": int(data.get("total_units", 0)),
            "building_count": len(data.get("buildings", {})),
        }

    @staticmethod
    def parse_resources(data: dict) -> dict:
        """Parse resources response.
        
        Args:
            data: Raw JSON response from the game server.
            
        Returns:
            Parsed resources dictionary with production rates.
        """
        if not isinstance(data, dict):
            return {}

        resources = data.get("resources", {})
        
        # Calculate production per hour based on buildings
        buildings = data.get("buildings", {})
        wood_production = 0
        clay_production = 0
        iron_production = 0
        grain_production = 0

        # Extract building levels for production calculation
        if isinstance(buildings, dict):
            for key, level in buildings.items():
                if "farm" in key or "crop" in key:
                    grain_production = level * 100  # Simplified
                elif "lumber" in key or "timber" in key:
                    wood_production = level * 100
                elif "clay" in key:
                    clay_production = level * 100
                elif "iron" in key:
                    iron_production = level * 100

        return {
            "current": {
                "wood": int(resources.get("wood", 0)),
                "clay": int(resources.get("clay", 0)),
                "iron": int(resources.get("iron", 0)),
                "grain": int(resources.get("crop", 0)),
            },
            "max_capacity": {
                "wood": int(data.get("storage_max", 1000)),
                "clay": int(data.get("storage_max", 1000)),
                "iron": int(data.get("storage_max", 1000)),
                "grain": int(data.get("storage_max", 1000)),
            },
            "production_per_hour": {
                "wood": wood_production,
                "clay": clay_production,
                "iron": iron_production,
                "grain": grain_production,
            },
        }

    @staticmethod
    def parse_buildings(data: dict) -> dict:
        """Parse buildings response.
        
        Args:
            data: Raw JSON response from the game server.
            
        Returns:
            Parsed buildings dictionary with levels and upgrade info.
        """
        if not isinstance(data, dict):
            return {}

        buildings = data.get("buildings", {})
        parsed_buildings = {}

        for building_id, building_data in buildings.items():
            if isinstance(building_data, dict):
                parsed_buildings[building_id] = {
                    "id": building_id,
                    "level": int(building_data.get("level", 0)),
                    "max_level": int(building_data.get("maxLevel", 10)),
                    "is_upgrading": bool(building_data.get("isUpgrade", False)),
                    "upgrade_complete_time": (
                        building_data.get("endTime")
                        if building_data.get("isUpgrade")
                        else None
                    ),
                }

        return {
            "buildings": parsed_buildings,
            "total_buildings": len(parsed_buildings),
        }

    @staticmethod
    def parse_units(data: dict) -> dict:
        """Parse military units response.
        
        Args:
            data: Raw JSON response from the game server.
            
        Returns:
            Parsed units dictionary with troop counts and training info.
        """
        if not isinstance(data, dict):
            return {}

        units = data.get("units", {})
        parsed_units = {}
        total_count = 0

        for unit_id, unit_data in units.items():
            if isinstance(unit_data, dict):
                count = int(unit_data.get("count", 0))
                parsed_units[unit_id] = {
                    "id": unit_id,
                    "count": count,
                    "is_training": bool(unit_data.get("isTraining", False)),
                    "training_complete_time": (
                        unit_data.get("endTime")
                        if unit_data.get("isTraining")
                        else None
                    ),
                }
                total_count += count

        return {
            "units": parsed_units,
            "total_count": total_count,
        }

    @staticmethod
    def parse_attack(data: dict) -> dict:
        """Parse attack-related response.
        
        Args:
            data: Raw JSON response from the game server.
            
        Returns:
            Parsed attack dictionary with target info and outcome.
        """
        if not isinstance(data, dict):
            return {}

        # Extract attack details
        target = data.get("target", {})
        attacker = data.get("attacker", {})

        return {
            "attack_id": str(data.get("id", "")),
            "timestamp": data.get("time", 0),
            "attacker": {
                "name": attacker.get("name", ""),
                "coordinates": f"{attacker.get('x', 0)}/{attacker.get('y', 0)}/0" if isinstance(attacker, dict) else "",
            },
            "target": {
                "name": target.get("name", ""),
                "coordinates": f"{target.get('x', 0)}/{target.get('y', 0)}/0" if isinstance(target, dict) else "",
            },
            "type": data.get("type", "attack"),
            "result": data.get("result", "pending"),
            "loot": int(data.get("loot", 0)),
        }

    @staticmethod
    def parse_notification(data: dict) -> list[dict]:
        """Parse notifications response.
        
        Args:
            data: Raw JSON response from the game server.
            
        Returns:
            List of parsed notification dictionaries.
        """
        if not isinstance(data, dict):
            return []

        notifications = data.get("notifications", [])
        parsed = []

        for notif in notifications:
            if isinstance(notif, dict):
                parsed.append({
                    "id": str(notif.get("id", "")),
                    "type": notif.get("type", "info"),
                    "title": notif.get("title", ""),
                    "message": notif.get("message", ""),
                    "timestamp": notif.get("time", 0),
                    "is_read": bool(notif.get("read", False)),
                })

        return parsed

    @staticmethod
    def parse_alliance(data: dict) -> dict:
        """Parse alliance data response.
        
        Args:
            data: Raw JSON response from the game server.
            
        Returns:
            Parsed alliance dictionary with member info.
        """
        if not isinstance(data, dict):
            return {}

        members = data.get("members", [])
        parsed_members = []

        for member in members:
            if isinstance(member, dict):
                parsed_members.append({
                    "name": member.get("name", ""),
                    "role": member.get("role", "member"),
                    "village_count": len(member.get("villages", [])),
                    "online": bool(member.get("online", False)),
                })

        return {
            "id": str(data.get("id", "")),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "member_count": len(parsed_members),
            "members": parsed_members,
        }


class ResponseParser:
    """Main response parser that routes API responses to appropriate handlers.
    
    This class provides a unified interface for parsing different types of
    Travian API responses based on the endpoint category.
    """

    def __init__(self):
        self._message_parser = GameMessageParser()
        
        # Map endpoint categories to parser methods
        self._parsers = {
            "village_state": self._message_parser.parse_village_state,
            "resources": self._message_parser.parse_resources,
            "buildings": self._message_parser.parse_buildings,
            "units": self._message_parser.parse_units,
            "attack": self._message_parser.parse_attack,
            "notification": self._message_parser.parse_notification,
            "alliance": self._message_parser.parse_alliance,
        }

    def parse(self, category: str, raw_data: dict) -> Optional[dict | list]:
        """Parse a response based on its category.
        
        Args:
            category: The request category (e.g., 'village_state', 'resources').
            raw_data: Raw JSON response from the game server.
            
        Returns:
            Parsed data as dict or list, or None if parsing fails.
        """
        parser = self._parsers.get(category)
        
        if parser is None:
            logger.warning("No parser found for category: %s", category)
            return None
        
        try:
            result = parser(raw_data)
            logger.debug("Successfully parsed %s response", category)
            return result
        except Exception as e:
            logger.error("Failed to parse %s response: %s", category, e)
            return None

    def parse_all(self, raw_data: dict) -> dict:
        """Parse all available data from a multi-category response.
        
        Some Travian endpoints return multiple data types in a single response.
        This method attempts to extract and parse each type.
        
        Args:
            raw_data: Raw JSON response that may contain multiple data types.
            
        Returns:
            Dictionary of parsed data by category.
        """
        result = {}
        
        for category, parser in self._parsers.items():
            # Try to find relevant data for this category
            data = self._extract_category_data(raw_data, category)
            if data:
                try:
                    parsed = parser(data)
                    if parsed:
                        result[category] = parsed
                except Exception as e:
                    logger.debug(
                        "Failed to parse %s from response: %s", category, e
                    )
        
        return result

    @staticmethod
    def _extract_category_data(raw_data: dict, category: str) -> Optional[dict]:
        """Extract relevant data for a specific category from raw response."""
        # Direct key mapping
        direct_keys = {
            "village_state": ["id", "name", "coordinates", "resources"],
            "resources": ["resources", "storage_max"],
            "buildings": ["buildings"],
            "units": ["units"],
            "attack": ["target", "attacker", "result"],
            "notification": ["notifications"],
            "alliance": ["id", "name", "members"],
        }

        keys = direct_keys.get(category, [])
        
        # Check if any key exists in raw data
        for key in keys:
            if key in raw_data:
                return raw_data[key] if isinstance(raw_data[key], dict) else {key: raw_data[key]}
        
        return None
