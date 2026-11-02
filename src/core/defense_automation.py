"""Defensive automation module for village protection and threat detection."""

import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum

try:
    from src.models.troop import TroopUnit, TROOP_BONUSES
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from models.troop import TroopUnit, TROOP_BONUSES

# Building base times dictionary (also in models.building)
BUILDING_BASE_TIMES = {
    "farm": 10,
    "warehouse": 30,
    "main": 60,
    "troop": 45,
    "wall": 60,
    "market": 90,
    "resource_wood": 30,
    "resource_clay": 30,
    "resource_iron": 30,
    "resource_grain": 30,
}

logger = logging.getLogger(__name__)


class ThreatLevel(Enum):
    """Threat levels for defensive responses."""
    
    NONE = "none"           # No threats detected
    LOW = "low"             # Minor threat, minimal response needed
    MEDIUM = "medium"       # Moderate threat, reinforce defenses
    HIGH = "high"           # High threat, full defense mode
    CRITICAL = "critical"   # Immediate defense required


class DefenseStrategy(Enum):
    """Defense strategies for different scenarios."""
    
    PASSIVE = "passive"         # Just walls and basic troops
    BALANCED = "balanced"       # Mix of walls and troops
    AGGRESSIVE = "aggressive"   # Heavy troop presence, active defense


@dataclass
class ThreatDetection:
    """Represents a detected threat against a village."""
    
    threat_id: str = field(default_factory=lambda: f"threat_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    source_village: str = ""
    source_coordinates: tuple = (0, 0, 0)
    detected_at: datetime = field(default_factory=datetime.now)
    
    # Threat assessment
    estimated_attacking_troops: int = 0
    estimated_attack_power: int = 0
    threat_level: ThreatLevel = ThreatLevel.NONE
    
    # Attack prediction
    predicted_arrival: Optional[datetime] = None
    predicted_target: str = ""
    
    # Response status
    response_active: bool = False
    defense_strength: int = 0


@dataclass
class DefensePlan:
    """Represents a defensive plan for a village."""
    
    defense_id: str = field(default_factory=lambda: f"defense_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    village_id: str = ""
    
    # Strategy
    strategy: DefenseStrategy = DefenseStrategy.BALANCED
    
    # Troop deployment
    stationed_troops: dict[str, int] = field(default_factory=dict)  # unit_type -> count
    reinforcement_queue: list[dict] = field(default_factory=list)
    
    # Building upgrades
    wall_level_target: int = 0
    warehouse_priority: bool = True
    
    # Status
    status: str = "active"  # active, paused, completed
    created_at: datetime = field(default_factory=datetime.now)


class DefenseAutomation:
    """Automates defensive measures for village protection.
    
    This module handles:
    - Threat detection from incoming attacks
    - Auto-reinforcement of villages
    - Wall building and upgrade management
    - Troop positioning strategies
    """

    # Base wall defense bonus per level
    WALL_DEFENSE_BONUS = 10
    
    # Minimum troops to keep stationed for defense
    MIN_DEFENSE_TROOPS = {
        "scout": 50,
        "spear": 100,
        "shield": 75,
    }

    # Wall upgrade priorities (higher = more important)
    WALL_PRIORITY = 8
    
    def __init__(self):
        """Initialize the defense automation module."""
        self._active_defenses: dict[str, DefensePlan] = {}
        self._threats: list[ThreatDetection] = []
        self._defense_log: list[dict] = []

    def detect_threats(
        self,
        village_id: str,
        incoming_attacks: list[dict],
        village_troops: dict[str, int],
        village_wall_level: int = 0,
    ) -> list[ThreatDetection]:
        """Detect and assess threats against a village.
        
        Analyzes incoming attack data to determine threat levels and
        recommend defensive responses.
        
        Args:
            village_id: ID of the village being protected.
            incoming_attacks: List of incoming attack dictionaries with:
                - source_village, source_coordinates, estimated_troops
            village_troops: Current stationed troops {unit_type: count}.
            village_wall_level: Current wall building level.
            
        Returns:
            List of detected threats (sorted by severity).
        """
        threats = []
        
        for attack in incoming_attacks:
            # Calculate estimated attack power
            attacking_troops = attack.get("estimated_troops", 0)
            attack_power = self._calculate_attack_power(attack.get("troop_composition", {}))
            
            # Calculate village defense strength
            defense_strength = self._calculate_defense_strength(
                village_troops,
                village_wall_level,
            )
            
            # Determine threat level
            threat_ratio = attack_power / max(defense_strength, 1)
            threat_level = self._assess_threat_level(threat_ratio, attacking_troops)
            
            # Create threat detection
            threat = ThreatDetection(
                source_village=attack.get("source_village", ""),
                source_coordinates=tuple(attack.get("source_coordinates", (0, 0, 0))),
                estimated_attacking_troops=attacking_troops,
                estimated_attack_power=attack_power,
                threat_level=threat_level,
                predicted_target=village_id,
                defense_strength=defense_strength,
            )
            
            threats.append(threat)
            logger.info(
                "Threat detected: %s -> %s (level: %s, power: %d)",
                attack.get("source_village"),
                village_id,
                threat_level.value,
                attack_power,
            )
        
        # Sort by threat level (critical first)
        priority_order = {
            ThreatLevel.CRITICAL: 0,
            ThreatLevel.HIGH: 1,
            ThreatLevel.MEDIUM: 2,
            ThreatLevel.LOW: 3,
            ThreatLevel.NONE: 4,
        }
        threats.sort(key=lambda t: priority_order.get(t.threat_level, 5))
        
        self._threats.extend(threats)
        return threats

    def create_defense_plan(
        self,
        village_id: str,
        strategy: DefenseStrategy = DefenseStrategy.BALANCED,
        available_troops: dict[str, int] | None = None,
        wall_level: int = 0,
        target_wall_level: int | None = None,
    ) -> DefensePlan:
        """Create a defense plan for a village.
        
        Args:
            village_id: ID of the village to defend.
            strategy: Defense strategy to use.
            available_troops: Available troops for deployment.
            wall_level: Current wall level.
            target_wall_level: Desired wall level (None = max possible).
            
        Returns:
            Created DefensePlan object.
        """
        plan = DefensePlan(
            village_id=village_id,
            strategy=strategy,
            wall_level_target=target_wall_level or wall_level + 3,
        )
        
        # Configure troop deployment based on strategy
        if available_troops:
            if strategy == DefenseStrategy.PASSIVE:
                # Minimal troops, rely on walls
                plan.stationed_troops = self._select_minimal_defense(available_troops)
            elif strategy == DefenseStrategy.AGGRESSIVE:
                # Maximum troops for counter-attack
                plan.stationed_troops = self._select_max_defense(available_troops)
            else:  # BALANCED
                plan.stationed_troops = self._select_balanced_defense(available_troops)
        
        self._active_defenses[village_id] = plan
        logger.info(
            "Defense plan created for %s (strategy: %s)",
            village_id,
            strategy.value,
        )
        
        return plan

    def auto_reinforce(
        self,
        village_id: str,
        available_troops: dict[str, int],
        threat_level: ThreatLevel = ThreatLevel.MEDIUM,
    ) -> dict[str, int]:
        """Automatically reinforce a village based on threat level.
        
        Args:
            village_id: ID of the village to reinforce.
            available_troops: Available troops for reinforcement.
            threat_level: Current threat level.
            
        Returns:
            Dictionary of deployed troops {unit_type: count}.
        """
        # Get existing defense plan or create one
        if village_id not in self._active_defenses:
            self.create_defense_plan(village_id, DefenseStrategy.BALANCED, available_troops)
        
        plan = self._active_defenses[village_id]
        
        # Adjust deployment based on threat level
        if threat_level in (ThreatLevel.HIGH, ThreatLevel.CRITICAL):
            # Switch to aggressive defense
            plan.stationed_troops = self._select_max_defense(available_troops)
        elif threat_level == ThreatLevel.MEDIUM:
            plan.stationed_troops = self._select_balanced_defense(available_troops)
        else:
            plan.stationed_troops = self._select_minimal_defense(available_troops)
        
        deployed = plan.stationed_troops.copy()
        
        # Log the reinforcement action
        self._defense_log.append({
            "action": "reinforcement",
            "village_id": village_id,
            "threat_level": threat_level.value,
            "deployed_troops": deployed,
            "timestamp": datetime.now().isoformat(),
        })
        
        logger.info(
            "Auto-reinforced %s: %d troops (threat: %s)",
            village_id,
            sum(deployed.values()),
            threat_level.value,
        )
        
        return deployed

    def recommend_wall_upgrade(
        self,
        village_id: str,
        current_wall_level: int,
        warehouse_level: int,
        resources_available: dict = None,
    ) -> dict:
        """Recommend wall upgrade based on defensive needs.
        
        Args:
            village_id: ID of the village.
            current_wall_level: Current wall building level.
            warehouse_level: Warehouse level (determines storage capacity).
            resources_available: Available resources for construction.
            
        Returns:
            Upgrade recommendation with cost analysis.
        """
        target_level = current_wall_level + 1
        
        # Calculate upgrade cost (simplified formula)
        base_time = BUILDING_BASE_TIMES.get("wall", 60)
        upgrade_time = base_time * (1.5 ** (target_level - 1))
        
        # Resource costs (approximate)
        wood_cost = int(80 * target_level * (1.2 ** (target_level - 1)))
        clay_cost = int(60 * target_level * (1.2 ** (target_level - 1)))
        iron_cost = int(70 * target_level * (1.2 ** (target_level - 1)))
        
        # Defense bonus from upgrade
        defense_bonus = self.WALL_DEFENSE_BONUS
        
        recommendation = {
            "village_id": village_id,
            "current_level": current_wall_level,
            "target_level": target_level,
            "upgrade_time_seconds": upgrade_time,
            "resource_cost": {
                "wood": wood_cost,
                "clay": clay_cost,
                "iron": iron_cost,
            },
            "defense_bonus": defense_bonus,
            "priority": "high" if current_wall_level < 3 else "medium",
        }
        
        logger.info(
            "Wall upgrade recommended for %s: level %d -> %d",
            village_id,
            current_wall_level,
            target_level,
        )
        
        return recommendation

    def get_threat_summary(self, village_id: str) -> dict:
        """Get a summary of active threats against a village.
        
        Args:
            village_id: ID of the village to check.
            
        Returns:
            Dictionary with threat summary.
        """
        # Filter recent threats (last 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        recent_threats = [
            t for t in self._threats
            if t.predicted_target == village_id and t.detected_at >= cutoff
        ]
        
        if not recent_threats:
            return {
                "village_id": village_id,
                "active_threats": 0,
                "max_threat_level": ThreatLevel.NONE.value,
                "recommended_strategy": DefenseStrategy.PASSIVE.value,
            }
        
        max_threat = max(recent_threats, key=lambda t: t.threat_level.value)
        
        # Recommend strategy based on threat level
        if max_threat.threat_level in (ThreatLevel.CRITICAL, ThreatLevel.HIGH):
            recommended = DefenseStrategy.AGGRESSIVE
        elif max_threat.threat_level == ThreatLevel.MEDIUM:
            recommended = DefenseStrategy.BALANCED
        else:
            recommended = DefenseStrategy.PASSIVE
        
        return {
            "village_id": village_id,
            "active_threats": len(recent_threats),
            "max_threat_level": max_threat.threat_level.value,
            "total_attacking_troops": sum(t.estimated_attacking_troops for t in recent_threats),
            "recommended_strategy": recommended.value,
        }

    def get_active_defense_plan(self, village_id: str) -> Optional[DefensePlan]:
        """Get the active defense plan for a village.
        
        Args:
            village_id: ID of the village.
            
        Returns:
            DefensePlan if exists, None otherwise.
        """
        return self._active_defenses.get(village_id)

    def update_wall_level(self, village_id: str, new_level: int) -> bool:
        """Update wall level in defense plan after upgrade completes.
        
        Args:
            village_id: ID of the village.
            new_level: New wall level after upgrade.
            
        Returns:
            True if successfully updated.
        """
        if village_id not in self._active_defenses:
            return False
        
        plan = self._active_defenses[village_id]
        plan.wall_level_target = new_level
        
        logger.info(
            "Wall level updated for %s: %d",
            village_id,
            new_level,
        )
        
        return True

    def _calculate_attack_power(self, troop_composition: dict[str, int]) -> int:
        """Calculate attack power from a troop composition."""
        total = 0
        for unit_type, count in troop_composition.items():
            bonus = TROOP_BONUSES.get(unit_type, {})
            total += count * bonus.get("attack", 0)
        return total

    def _calculate_defense_strength(
        self,
        stationed_troops: dict[str, int],
        wall_level: int,
    ) -> int:
        """Calculate total defense strength of a village."""
        # Defense from troops
        troop_defense = 0
        for unit_type, count in stationed_troops.items():
            bonus = TROOP_BONUSES.get(unit_type, {})
            troop_defense += count * bonus.get("defense", 0)
        
        # Defense from walls
        wall_defense = wall_level * self.WALL_DEFENSE_BONUS
        
        return troop_defense + wall_defense

    def _assess_threat_level(self, threat_ratio: float, attacking_troops: int) -> ThreatLevel:
        """Assess threat level based on attack/defense ratio and troop count."""
        if threat_ratio >= 3.0 or attacking_troops >= 10000:
            return ThreatLevel.CRITICAL
        elif threat_ratio >= 2.0 or attacking_troops >= 5000:
            return ThreatLevel.HIGH
        elif threat_ratio >= 1.5 or attacking_troops >= 1000:
            return ThreatLevel.MEDIUM
        elif threat_ratio >= 1.0:
            return ThreatLevel.LOW
        return ThreatLevel.NONE

    def _select_minimal_defense(self, available: dict[str, int]) -> dict[str, int]:
        """Select minimal defense troops (walls do most of the work)."""
        deployed = {}
        for unit_type, min_count in self.MIN_DEFENSE_TROOPS.items():
            available_count = available.get(unit_type, 0)
            deployed[unit_type] = min(min_count, available_count)
        return deployed

    def _select_max_defense(self, available: dict[str, int]) -> dict[str, int]:
        """Select maximum defense troops for counter-attack."""
        # Prioritize defensive units (shield, spear)
        priority_order = ["shield", "spear", "marauder", "scout"]
        deployed = {}
        
        remaining = dict(available)
        for unit_type in priority_order:
            if unit_type in remaining:
                deployed[unit_type] = remaining.pop(unit_type)
        
        # Add any remaining troops
        for unit_type, count in remaining.items():
            deployed[unit_type] = count
        
        return deployed

    def _select_balanced_defense(self, available: dict[str, int]) -> dict[str, int]:
        """Select balanced defense troop composition."""
        deployed = {}
        total_available = sum(available.values())
        
        if total_available == 0:
            return deployed
        
        # Deploy 60% of troops for defense
        deploy_percentage = 0.6
        
        for unit_type, count in available.items():
            deployed[unit_type] = int(count * deploy_percentage)
        
        return deployed

    def get_defense_log(self, limit: int = 50) -> list[dict]:
        """Get defense automation log.
        
        Args:
            limit: Maximum number of entries to return.
            
        Returns:
            List of defense log entries.
        """
        return self._defense_log[-limit:]
