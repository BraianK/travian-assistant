"""Application configuration settings."""

import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

try:
    from src.config.paths import get_data_dir, get_settings_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from config.paths import get_data_dir, get_settings_path

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class TravianCredentials(BaseModel):
    """Credentials loaded from the local .env file."""

    username: str = Field(
        default_factory=lambda: os.getenv("TRAVIAN_USERNAME", ""),
        repr=False,
        exclude=True,
    )
    password: str = Field(
        default_factory=lambda: os.getenv("TRAVIAN_PASSWORD", ""),
        repr=False,
        exclude=True,
    )


class BrowserSettings(BaseModel):
    """Chrome browser connection settings."""
    cdp_address: str = Field(default="127.0.0.1:9222", description="Chrome DevTools Protocol address")
    cdp_port: int = Field(default=9222, description="Chrome DevTools Protocol port")
    timeout_seconds: int = Field(default=30, description="Connection timeout in seconds")


class DatabaseSettings(BaseModel):
    """Database connection settings."""
    db_path: str = Field(
        default_factory=lambda: str(get_data_dir() / "travian.db"),
        description="Path to SQLite database file",
    )
    backup_interval_hours: int = Field(default=24, description="Auto-backup interval in hours")


class InterceptorSettings(BaseModel):
    """HTTP interception settings."""
    polling_interval_seconds: int = Field(default=30, description="Data refresh interval")
    allowed_domains: list[str] = Field(
        default=["*.traviangames.com", "*.game-travian.com"],
        description="Domains to intercept"
    )


class AutomationSettings(BaseModel):
    """Automation safety settings."""
    max_actions_per_minute: int = Field(default=10, description="Maximum automation actions per minute")
    daily_action_limit: int = Field(default=100, description="Maximum actions per day")
    min_action_delay_ms: int = Field(default=500, description="Minimum delay between actions (ms)")
    max_action_delay_ms: int = Field(default=3000, description="Maximum delay between actions (ms)")


class VillageRecruitmentSettings(BaseModel):
    """Automatic recruitment choices for one village."""

    enabled: bool = True
    units: set[str] = Field(default_factory=set)
    queue_minutes_by_unit: dict[str, int] = Field(default_factory=dict)
    # ``None`` inherits the account-wide value from RecruitmentSettings.
    queue_strategy: Literal["count", "duration"] | None = None
    batch_size: int | None = Field(default=None, ge=1, le=100)
    queue_threshold: int | None = Field(default=None, ge=0, le=100)

    @field_validator("units", mode="before")
    @classmethod
    def validate_units(cls, units: set[str] | list[str]) -> set[str]:
        if not isinstance(units, (set, list)):
            raise ValueError("Automatic recruitment unit IDs must be a set.")
        legacy_ids = {
            "Phalanx": "21",
            "Theutates Thunder": "24",
        }
        normalized = {
            legacy_ids.get(str(unit), str(unit))
            for unit in units
        }
        if any(not unit.isdigit() or int(unit) <= 0 for unit in normalized):
            raise ValueError("Automatic recruitment unit IDs must be positive.")
        return normalized

    @field_validator("queue_minutes_by_unit", mode="before")
    @classmethod
    def validate_queue_minutes(
        cls,
        values: dict[str, int],
    ) -> dict[str, int]:
        if not isinstance(values, dict):
            raise ValueError("Recruitment queue targets must be a mapping.")
        normalized = {}
        for unit, minutes in values.items():
            unit_id = str(unit)
            if not unit_id.isdigit() or int(unit_id) <= 0:
                raise ValueError("Recruitment queue target unit IDs must be positive.")
            if (
                isinstance(minutes, bool)
                or not isinstance(minutes, int)
                or not 1 <= minutes <= 1440
            ):
                raise ValueError(
                    "Recruitment queue targets must be between 1 and 1440 minutes."
                )
            normalized[unit_id] = minutes
        return normalized


class RecruitmentSettings(BaseModel):
    """Optional automatic replenishment of target troop queues."""
    automatic_enabled: bool = False
    queue_strategy: Literal["count", "duration"] = "count"
    batch_size: int = Field(default=5, ge=1, le=100)
    queue_threshold: int = Field(default=1, ge=0, le=100)
    pending_orders: set[str] = Field(default_factory=set)
    villages: dict[str, VillageRecruitmentSettings] = Field(default_factory=dict)

    def _village_value(self, village_id: str, field: str):
        village = self.villages.get(str(village_id))
        value = getattr(village, field) if village is not None else None
        return getattr(self, field) if value is None else value

    def strategy_for(self, village_id: str) -> str:
        """Return the replenishment mode used in one village."""
        return self._village_value(village_id, "queue_strategy")

    def batch_size_for(self, village_id: str) -> int:
        """Return the (maximum) batch size used in one village."""
        return self._village_value(village_id, "batch_size")

    def queue_threshold_for(self, village_id: str) -> int:
        """Return the count threshold used in one village."""
        return self._village_value(village_id, "queue_threshold")


class VillageMillingSettings(BaseModel):
    """Automatic NPC grain conversion preferences for one village."""

    enabled: bool = False
    trigger_percent: int = Field(default=100, ge=1, le=100)
    refill_enabled: bool = False
    refill_trigger_percent: int = Field(default=5, ge=1, le=100)
    clay_percent: int = Field(default=40, ge=0, le=100)
    iron_percent: int = Field(default=30, ge=0, le=100)
    wood_percent: int = Field(default=30, ge=0, le=100)

    @property
    def distribution_total(self) -> int:
        return self.clay_percent + self.iron_percent + self.wood_percent


class ResourceMillingSettings(BaseModel):
    """NPC trader automation and gold reserve settings."""

    minimum_gold_balance: int = Field(default=300, ge=0, le=1000000)
    pending_villages: set[str] = Field(default_factory=set)
    pending_refill_villages: set[str] = Field(default_factory=set)
    villages: dict[str, VillageMillingSettings] = Field(default_factory=dict)


VACATION_MOVEMENT_TYPES = ("Atak", "Oblężenie", "Napad", "Zwiad")


class VacationSettings(BaseModel):
    """Daily rolling vacation mode that only activates under attack.

    Vacation mode starts 24 hours after it is planned and can be cancelled
    (with the vacation day refunded) until then. The automation plans it
    every day and, shortly before activation, cancels it unless enough
    hostile movements are incoming.
    """

    enabled: bool = False
    plan_time: str = "21:00"
    plan_window_minutes: int = Field(default=30, ge=1, le=180)
    decision_minutes_before: int = Field(default=10, ge=2, le=1440)
    vacation_days: int = Field(default=1, ge=1, le=14)
    attack_threshold: int = Field(default=1, ge=1, le=1000)
    counted_movement_types: set[str] = Field(
        default_factory=lambda: {"Atak", "Oblężenie", "Napad"}
    )
    last_plan_date: str | None = None

    @field_validator("plan_time")
    @classmethod
    def validate_plan_time(cls, value: str) -> str:
        parts = str(value).split(":")
        if (
            len(parts) != 2
            or not all(part.isdigit() for part in parts)
            or not 0 <= int(parts[0]) <= 23
            or not 0 <= int(parts[1]) <= 59
        ):
            raise ValueError("Vacation plan time must use HH:MM format.")
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"

    @field_validator("counted_movement_types", mode="before")
    @classmethod
    def validate_movement_types(cls, values) -> set[str]:
        if not isinstance(values, (set, list, tuple)):
            raise ValueError("Counted movement types must be a collection.")
        normalized = {str(value) for value in values}
        if not normalized <= set(VACATION_MOVEMENT_TYPES):
            raise ValueError("Unknown movement type for vacation decisions.")
        return normalized


class AppSettings(BaseModel):
    """Main application settings."""
    app_name: str = Field(default="Travian Kingdoms Assistant")
    version: str = Field(default="0.2.0")
    
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    credentials: TravianCredentials = Field(default_factory=TravianCredentials, exclude=True)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    interceptor: InterceptorSettings = Field(default_factory=InterceptorSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    recruitment: RecruitmentSettings = Field(default_factory=RecruitmentSettings)
    resource_milling: ResourceMillingSettings = Field(
        default_factory=ResourceMillingSettings
    )
    vacation: VacationSettings = Field(default_factory=VacationSettings)
    
    # Data directory
    data_dir: Path = Field(default_factory=get_data_dir, repr=False)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Create necessary directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "backups").mkdir(exist_ok=True)
    
    def save(self, filepath: str | Path | None = None):
        """Save settings to JSON file."""
        settings_path = Path(filepath) if filepath is not None else get_settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))
    
    @classmethod
    def load(cls, filepath: str | Path | None = None) -> "AppSettings":
        """Load settings from JSON file."""
        settings_path = Path(filepath) if filepath is not None else get_settings_path()
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**data)
        return cls()
