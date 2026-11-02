"""Core services for account management, village tracking, and game automation."""

from .account_manager import AccountManager
from .village_tracker import VillageTracker
from .attack_planner import AttackPlanner
from .coalition_planner import CoalitionPlanner
from .defense_automation import DefenseAutomation, ThreatLevel, DefenseStrategy
from .resource_analyzer import ResourceAnalyzer

__all__ = [
    "AccountManager",
    "VillageTracker",
    "AttackPlanner",
    "CoalitionPlanner",
    "DefenseAutomation",
    "ThreatLevel",
    "DefenseStrategy",
    "ResourceAnalyzer",
]
