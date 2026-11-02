"""
Request filter module for identifying and categorizing Travian Kingdoms API calls.

This module provides pattern matching for Travian game traffic, allowing the interceptor
to identify relevant requests/responses for parsing and analysis.
"""

import json
import re
from typing import Optional

# Domain patterns for Travian Kingdoms
TRAVIAN_DOMAIN_PATTERNS = [
    r".*\.traviangames\.com",
    r".*\.travian\..*",
    r".*\.kingdoms\.com",
    r"t[0-9]+\.[a-z]+\.(?:traviangames|me)",
]

# API endpoint patterns for different game data types
API_ENDPOINT_PATTERNS = {
    "village_state": r"/gameState\.php",
    "resources": r"/getResources\.php",
    "buildings": r"/getBuildings\.php|/updateBuilding\.php",
    "units": r"/getUnits\.php|/trainUnit\.php",
    "attack": r"/attack\.php|/sendAttack\.php",
    "coordinates": r"/getCoordinates\.php",
    "screen_switch": r"/switchScreen\.php",
    "notification": r"/getNotifications\.php",
    "alliance": r"/getAllianceData\.php",
}


class RequestFilter:
    """Filters network requests to identify Travian Kingdoms API traffic."""

    def __init__(self, domain_patterns: list[str] | None = None):
        self.domain_patterns = domain_patterns or TRAVIAN_DOMAIN_PATTERNS
        self._domain_regex = re.compile(
            "|".join(self.domain_patterns), re.IGNORECASE
        )
        self._endpoint_regexes = {
            key: re.compile(pattern, re.IGNORECASE)
            for key, pattern in API_ENDPOINT_PATTERNS.items()
        }

    def is_travian_request(self, url: str) -> bool:
        """Check if the URL belongs to a Travian game server.
        
        Args:
            url: The full request URL to check.
            
        Returns:
            True if this appears to be a Travian game request.
        """
        return bool(self._domain_regex.search(url))

    def categorize_request(self, url: str) -> Optional[str]:
        """Categorize a Travian request by its purpose.
        
        Args:
            url: The full request URL to categorize.
            
        Returns:
            A string category name (e.g., 'village_state', 'attack') or None.
        """
        for category, regex in self._endpoint_regexes.items():
            if regex.search(url):
                return category
        return "unknown"

    def should_capture_response(
        self,
        url: str,
        method: str = "GET",
        request_body: str | None = None,
    ) -> bool:
        """Determine if we should capture and store the response body.
        
        Args:
            url: The request URL.
            method: The HTTP method used.
            
        Returns:
            True if the response should be captured for parsing.
        """
        if not self.is_travian_request(url):
            return False
        
        # Don't capture large static assets
        static_patterns = [
            r"\.(?:png|jpg|jpeg|gif|css|js|woff2?|ttf|svg)$",
            r"/assets/",
            r"/images/",
        ]
        for pattern in static_patterns:
            if re.search(pattern, url):
                return False
        
        # Only capture JSON/XML responses from API endpoints
        api_categories = {
            "village_state",
            "resources",
            "buildings",
            "units",
            "attack",
            "notification",
            "alliance",
        }
        category = self.categorize_request(url)
        if (
            category == "unknown"
            and method.upper() == "POST"
            and re.search(r"/api/?(?:\?|$)", url, re.IGNORECASE)
            and request_body
        ):
            try:
                request_data = json.loads(request_body)
            except (TypeError, ValueError):
                return False
            return (
                isinstance(request_data, dict)
                and request_data.get("controller") == "map"
                and request_data.get("action") == "getByRegionIds"
            )
        return category in api_categories

    def is_attack_request(self, url: str) -> bool:
        """Check if this is an attack-related request."""
        return self.categorize_request(url) == "attack"

    def is_village_related(self, url: str) -> bool:
        """Check if this request relates to village data."""
        category = self.categorize_request(url)
        return category in ("village_state", "resources", "buildings", 
                          "coordinates", "screen_switch")

    def get_world_from_url(self, url: str) -> Optional[str]:
        """Extract the world/server identifier from URL.
        
        Examples:
            - t123.server.traviangames.com -> 't123'
            - https://world123.travian.com -> 'world123'
            
        Args:
            url: The full request URL.
            
        Returns:
            World identifier string or None if not extractable.
        """
        # Pattern for subdomain-based worlds (t123.server.traviangames.com)
        subdomain_match = re.match(r"^https://([^.]+)\.", url)
        if subdomain_match:
            return subdomain_match.group(1)
        
        # Pattern for path-based worlds (/world/123/)
        path_match = re.search(r"/(\d{3,})/", url)
        if path_match:
            return f"t{path_match.group(1)}"
        
        return None
