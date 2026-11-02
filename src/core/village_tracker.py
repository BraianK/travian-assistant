"""Village tracking module."""

import ast
import logging
from typing import Optional, Callable, Any
from datetime import datetime

try:
    from src.models.village import Village, ResourcePoint
    from src.database.repository import VillageRepository
    from src.interceptor.browser_cdp import ChromeCDPInterceptor, NetworkRequest
    from src.parsing.response_parser import ResponseParser
    from src.parsing.game_data_normalizer import GameDataNormalizer
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from models.village import Village, ResourcePoint
    from database.repository import VillageRepository
    from interceptor.browser_cdp import ChromeCDPInterceptor, NetworkRequest
    from parsing.response_parser import ResponseParser
    from parsing.game_data_normalizer import GameDataNormalizer

logger = logging.getLogger(__name__)


class VillageTracker:
    """Tracks village state and resource production over time."""
    
    def __init__(self, repo: VillageRepository):
        self.repo = repo
        self._villages: dict[str, Village] = {}  # village_id -> Village
        self._response_parser = ResponseParser()
        self._data_normalizer = GameDataNormalizer()
        self._selected_village_id = ""

    @staticmethod
    def _datetime_from_database(value):
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    @classmethod
    def _village_from_database_row(cls, row: dict) -> Village:
        coordinates = row.get("coordinates", (0, 0, 0))
        if isinstance(coordinates, str):
            coordinates = ast.literal_eval(coordinates)
        if isinstance(coordinates, dict):
            coordinates = (
                coordinates.get("row", 0),
                coordinates.get("col", 0),
                coordinates.get("field", coordinates.get("field_num", 0)),
            )
        if not isinstance(coordinates, (list, tuple)):
            coordinates = (0, 0, 0)

        return Village(
            village_id=str(row.get("village_id", "")),
            account_id=str(row.get("account_id", "")),
            name=row.get("name") or "Unknown",
            coordinates=tuple(coordinates),
            population=row.get("population") or 0,
            farm_level=row.get("farm_level") or 0,
            chief_level=row.get("chief_level") or 0,
            temple_level=row.get("temple_level") or 0,
            created_at=cls._datetime_from_database(row.get("created_at")),
            updated_at=cls._datetime_from_database(row.get("updated_at")),
        )

    def load_from_database(self) -> list[Village]:
        """Load persisted villages and their latest resources into memory."""
        rows = self.repo.get_all("villages")
        self._villages = {}
        for row in rows:
            village = self._village_from_database_row(row)
            self._villages[village.village_id] = village

        for row in self.repo.get_latest_resources():
            village = self._villages.get(str(row["village_id"]))
            resource_type = row["resource_type"]
            if village is None or resource_type not in {"wood", "clay", "iron", "grain"}:
                continue

            resource = getattr(village, resource_type)
            resource.current_amount = row["current_amount"]
            resource.max_capacity = row["max_capacity"]
            resource.production_rate = row["production_rate"]
            resource.last_updated = self._datetime_from_database(row["recorded_at"])

        return list(self._villages.values())
    
    def add_village(
        self,
        village_id: str,
        account_id: str,
        name: str = "",
        coordinates: tuple = (0, 0, 0),
    ) -> Village:
        """Register a new village for tracking."""
        village = Village(
            village_id=village_id,
            account_id=account_id,
            name=name,
            coordinates=coordinates,
        )
        self._villages[village_id] = village
        
        # Save to database
        self.repo.upsert_village(village_id, account_id, name, coordinates)
        
        logger.info(f"Village added: {name} ({village_id})")
        return village
    
    def get_village(self, village_id: str) -> Optional[Village]:
        """Get a village by ID."""
        if village_id in self._villages:
            return self._villages[village_id]
        
        # Try database lookup
        data = self.repo.get_by_id(village_id)
        if data:
            village = self._village_from_database_row(data)
            self._villages[village_id] = village
            return village
        
        return None
    
    def get_all_villages(self) -> list[Village]:
        """Get all tracked villages."""
        if not self._villages:
            rows = self.repo.get_all("villages")
            for row in rows:
                village = self._village_from_database_row(row)
                self._villages[row["village_id"]] = village
        
        return list(self._villages.values())
    
    def update_village_state(self, village_id: str, state: dict) -> bool:
        """Update village state from game data."""
        if village_id not in self._villages:
            logger.warning(f"Village {village_id} not registered, adding automatically")
            # Auto-add with minimal info
            self.add_village(village_id, account_id="unknown", name="Unknown")
        
        village = self._villages[village_id]
        village.update_state(state)
        return True
    
    def sync_live_state(self, state: dict) -> list[str]:
        """Persist the read-only live game state into the in-memory tracker and SQLite DB."""
        if not isinstance(state, dict):
            return []

        synced: list[str] = []
        resources_state = state.get("resources") or {}
        capacities_state = state.get("capacities") or {}
        production_rates = state.get("production_rates") or {}
        selected_village_id = str(state.get("selected_village_id") or "")
        self._selected_village_id = selected_village_id

        for item in state.get("villages", []):
            village_id = str(item.get("id") or item.get("village_id") or "")
            if not village_id:
                continue

            existing = self.get_village(village_id)
            if existing is None:
                village = self.add_village(
                    village_id=village_id,
                    account_id=str(state.get("account_id") or "unknown"),
                    name=str(item.get("name") or "Unknown"),
                    coordinates=tuple(item.get("coordinates", (0, 0, 0))),
                )
            else:
                village = existing
                village.name = str(item.get("name") or village.name or "Unknown")
                if item.get("coordinates") is not None:
                    village.coordinates = tuple(item.get("coordinates", village.coordinates))

            if item.get("name"):
                village.name = str(item.get("name"))

            resource_payload = {
                name: {
                    "current_amount": int(resources_state.get(name, 0) or 0),
                    "max_capacity": int(capacities_state.get(name, 0) or 0),
                    "production_rate": float(production_rates.get(name, 0) or 0.0),
                }
                for name in ["wood", "clay", "iron", "grain"]
            }
            if (
                village_id == selected_village_id
                and any(
                    v.get("current_amount", 0) or v.get("max_capacity", 0)
                    for v in resource_payload.values()
                )
            ):
                self.update_resources(village_id, resource_payload)

            synced.append(village_id)

        if state.get("selected_village_id"):
            selected = str(state["selected_village_id"])
            if selected not in self._villages:
                self.add_village(
                    village_id=selected,
                    account_id=str(state.get("account_id") or "unknown"),
                    name=str(state.get("account") or "Selected village"),
                )

        return synced

    @staticmethod
    def _find_village_id(data: Any) -> str:
        if isinstance(data, dict):
            for key in ("village_id", "villageId", "villageID"):
                value = data.get(key)
                if isinstance(value, (str, int)) and str(value).isdigit():
                    return str(value)
            for value in data.values():
                village_id = VillageTracker._find_village_id(value)
                if village_id:
                    return village_id
        elif isinstance(data, list):
            for value in data:
                village_id = VillageTracker._find_village_id(value)
                if village_id:
                    return village_id
        return ""

    def process_network_response(self, url: str, raw_data: dict) -> bool:
        """Parse, normalize, and save a captured game API response."""
        try:
            try:
                from src.interceptor.request_filter import RequestFilter
            except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
                from interceptor.request_filter import RequestFilter

            category = RequestFilter().categorize_request(url)
            if not category or category == "unknown":
                return False

            parsed = self._response_parser.parse(category, raw_data)
            if parsed is None:
                logger.warning("Could not parse captured %s response from %s", category, url)
                return False

            normalizers = {
                "village_state": self._data_normalizer.normalize_village_state,
                "resources": self._data_normalizer.normalize_resources,
                "buildings": self._data_normalizer.normalize_buildings,
                "units": self._data_normalizer.normalize_units,
                "attack": self._data_normalizer.normalize_attack,
                "notification": self._data_normalizer.normalize_notification,
                "alliance": self._data_normalizer.normalize_alliance,
            }
            normalize = normalizers.get(category)
            normalized = normalize(parsed) if normalize else parsed
            if not normalized:
                logger.warning("Normalization returned no data for %s response", category)
                return False

            village_id = (
                self._find_village_id(raw_data)
                or self._find_village_id(parsed)
                or self._selected_village_id
            )
            if not village_id:
                logger.debug(
                    "Parsed %s response without a village context; not persisted",
                    category,
                )
                return False

            self.repo.save_dashboard_snapshot(
                village_id,
                f"api_{category}",
                normalized,
            )
            if category == "resources":
                resource_data = raw_data.get("resources")
                normalized_resources = normalized.get("resources", {})
                if (
                    "storage_max" in raw_data
                    and isinstance(resource_data, dict)
                    and isinstance(normalized_resources, dict)
                ):
                    resource_aliases = {
                        "wood": "wood",
                        "clay": "clay",
                        "iron": "iron",
                        "grain": "crop",
                    }
                    resource_payload = {}
                    for resource_type, raw_key in resource_aliases.items():
                        if raw_key not in resource_data:
                            continue
                        resource = normalized_resources[resource_type]
                        resource_payload[resource_type] = {
                            "current_amount": resource["current"],
                            "max_capacity": resource["max_capacity"],
                            "production_rate": resource["production_per_hour"],
                        }
                    if resource_payload:
                        self.update_resources(village_id, resource_payload)
            elif category == "buildings":
                buildings = normalized.get("buildings", {})
                self.repo.save_dashboard_snapshot(
                    village_id,
                    "buildings",
                    [
                        {
                            "name": item["name"],
                            "slot": item["id"],
                            "level": item["level"],
                            "upgrading": item["is_upgrading"],
                            "upgrade_complete_at": item["upgrade_complete_at"],
                            "upgrade_duration_seconds": item[
                                "upgrade_duration_seconds"
                            ],
                        }
                        for item in buildings.values()
                    ],
                )
            elif category == "units":
                units = normalized.get("units", {})
                self.repo.save_dashboard_snapshot(
                    village_id,
                    "troops",
                    [
                        {
                            "village_id": village_id,
                            "village": self.get_village(village_id).name
                            if self.get_village(village_id)
                            else village_id,
                            "unit": item["name"],
                            "count": item["count"],
                        }
                        for item in units.values()
                    ],
                )

            logger.info("Parsed and saved %s response for village %s", category, village_id)
            return True
        except Exception:
            logger.exception("Failed to process captured game response from %s", url)
            return False

    def update_resources(self, village_id: str, resources: dict) -> bool:
        """Update resource data and persist to database."""
        if village_id not in self._villages:
            self.add_village(village_id=village_id, account_id="unknown", name="Unknown")

        village = self._villages[village_id]
        village.update_resources(resources)

        # Persist to database
        self.repo.upsert_resources(village_id, {
            "wood": village.wood.__dict__,
            "clay": village.clay.__dict__,
            "iron": village.iron.__dict__,
            "grain": village.grain.__dict__,
        })

        return True
    
    def get_total_resources(self) -> dict:
        """Get aggregated resource totals across all villages."""
        totals = {
            "wood": 0,
            "clay": 0,
            "iron": 0,
            "grain": 0,
            "capacity_wood": 0,
            "capacity_clay": 0,
            "capacity_iron": 0,
            "capacity_grain": 0,
        }
        
        for village in self._villages.values():
            totals["wood"] += village.wood.current_amount
            totals["clay"] += village.clay.current_amount
            totals["iron"] += village.iron.current_amount
            totals["grain"] += village.grain.current_amount
            
            totals["capacity_wood"] += village.wood.max_capacity
            totals["capacity_clay"] += village.clay.max_capacity
            totals["capacity_iron"] += village.iron.max_capacity
            totals["capacity_grain"] += village.grain.max_capacity
        
        return totals
    
    def get_production_rates(self) -> dict:
        """Get total production rates across all villages."""
        rates = {
            "wood": 0.0,
            "clay": 0.0,
            "iron": 0.0,
            "grain": 0.0,
        }
        
        for village in self._villages.values():
            rates["wood"] += village.wood.production_rate
            rates["clay"] += village.clay.production_rate
            rates["iron"] += village.iron.production_rate
            rates["grain"] += village.grain.production_rate
        return rates
    
    def setup_auto_refresh(
        self,
        interceptor: ChromeCDPInterceptor,
        refresh_interval: float = 30.0,
    ) -> None:
        """Setup automatic data refresh from the browser interceptor.
        
        Connects event handlers to the CDP interceptor so that when
        network requests are captured, they are automatically parsed
        and stored in the village tracker.
        
        Args:
            interceptor: The ChromeCDPInterceptor instance to monitor.
            refresh_interval: Interval in seconds for full data refresh.
        """
        self.interceptor = interceptor
        self.refresh_interval = refresh_interval
        
        # Handle request sent events
        @interceptor.on("request_sent")
        def on_request(request: NetworkRequest):
            logger.debug(f"Request captured: {request.url}")
        
        # Handle response received events
        @interceptor.on("data_received")
        def on_response(request: NetworkRequest):
            if not request.is_json_response or not request.response_body:
                return
            raw_data = request.get_json()
            if isinstance(raw_data, dict):
                self.process_network_response(request.url, raw_data)
        
        logger.info(
            f"Auto-refresh setup complete (interval: {refresh_interval}s)"
        )
    
    def get_dashboard_data(self) -> dict:
        """Get aggregated data for dashboard display.
        
        Returns:
            Dictionary containing all data needed for the main dashboard.
        """
        villages = self.get_all_villages()
        total_resources = self.get_total_resources()
        production_rates = self.get_production_rates()
        
        village_summaries = []
        for village in villages:
            village_summaries.append({
                "id": village.village_id,
                "name": village.name,
                "coordinates": f"{village.coordinates[0]}/{village.coordinates[1]}/0",
                "resources": {
                    "wood": village.wood.current_amount,
                    "clay": village.clay.current_amount,
                    "iron": village.iron.current_amount,
                    "grain": village.grain.current_amount,
                },
                "population": village.total_population,
            })
        
        return {
            "villages": village_summaries,
            "total_resources": total_resources,
            "production_rates": production_rates,
            "timestamp": datetime.now().isoformat(),
        }
