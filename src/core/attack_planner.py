"""Attack planning and automation module for Travian Kingdoms."""

import logging
import math
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

try:
    from src.models.attack import AttackPlan, AttackLog, AttackOutcome, AttackType
    from src.models.troop import TroopUnit, TROOP_BONUSES
    from src.database.repository import AttackLogRepository
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from models.attack import AttackPlan, AttackLog, AttackOutcome, AttackType
    from models.troop import TroopUnit, TROOP_BONUSES
    from database.repository import AttackLogRepository

logger = logging.getLogger(__name__)


class AttackPriority(Enum):
    """Priority levels for attack planning."""
    
    HIGH = "high"       # Immediate execution
    MEDIUM = "medium"   # Schedule within 30 minutes
    LOW = "low"         # Queue for later execution


class AttackPlanner:
    """Plans and manages attacks against other villages.
    
    This module calculates optimal attack compositions, estimates march times,
    and tracks attack outcomes for strategic decision-making.
    """

    # Base troop speeds (tiles per minute) - Travian game values
    TROOP_SPEEDS = {
        "scout": 2.0,
        "spear": 1.5,
        "sword": 1.6,
        "axe": 1.4,
        "archer": 1.8,
        "snipper": 1.7,
        "shield": 1.3,
        "marauder": 1.9,
    }

    # March time modifier (base minutes per tile)
    MARCH_TIME_PER_TILE = 1.0  # minutes per tile at base speed

    def __init__(self, attack_log_repo: AttackLogRepository):
        """Initialize the attack planner.
        
        Args:
            attack_log_repo: Repository for storing attack logs.
        """
        self.attack_log_repo = attack_log_repo
        self._active_plans: dict[str, AttackPlan] = {}
        self._attack_history: list[dict] = []

    def calculate_distance(self, coord1: tuple, coord2: tuple) -> float:
        """Calculate Manhattan distance between two coordinates.
        
        Travian uses a coordinate system where distance = |x1-x2| + |y1-y2|
        
        Args:
            coord1: Source coordinate (row, col).
            coord2: Target coordinate (row, col).
            
        Returns:
            Distance in tiles.
        """
        return abs(coord1[0] - coord2[0]) + abs(coord1[1] - coord2[1])

    def calculate_march_time(
        self,
        distance: float,
        fastest_units: list[TroopUnit] | None = None
    ) -> float:
        """Calculate estimated march time in minutes.
        
        March time depends on the slowest unit in the attack composition.
        
        Args:
            distance: Distance in tiles.
            fastest_units: List of troops included in the attack.
            
        Returns:
            Estimated march time in minutes.
        """
        if not fastest_units or distance <= 0:
            return distance * self.MARCH_TIME_PER_TILE * 2
        
        # Find slowest unit speed (determines march time)
        min_speed = float('inf')
        for unit in fastest_units:
            speed = self.TROOP_SPEEDS.get(unit.unit_type, 1.5)
            if speed < min_speed:
                min_speed = speed
        
        # March time formula: distance / speed * base_modifier
        if min_speed > 0:
            return (distance / min_speed) * self.MARCH_TIME_PER_TILE * 60
        return distance * self.MARCH_TIME_PER_TILE * 60

    def calculate_attack_power(self, units: list[TroopUnit]) -> int:
        """Calculate total attack power of a unit composition.
        
        Attack power = sum(count * attack_bonus) for each unit type
        
        Args:
            units: List of troop units in the attack.
            
        Returns:
            Total attack power score.
        """
        total_power = 0
        for unit in units:
            bonus = TROOP_BONUSES.get(unit.unit_type, {})
            attack_bonus = bonus.get("attack", 0)
            total_power += unit.count * attack_bonus
        
        return total_power

    def calculate_defense_power(self, enemy_units: list[TroopUnit]) -> int:
        """Calculate total defense power of enemy troops.
        
        Args:
            enemy_units: List of enemy troop units.
            
        Returns:
            Total defense power score.
        """
        total_defense = 0
        for unit in enemy_units:
            bonus = TROOP_BONUSES.get(unit.unit_type, {})
            defense_bonus = bonus.get("defense", 0)
            total_defense += unit.count * defense_bonus
        
        return total_defense

    def plan_attack(
        self,
        target_village: str,
        target_coordinates: tuple,
        target_troops: list[TroopUnit],
        available_units: list[TroopUnit],
        attack_type: AttackType = AttackType.SCOUT,
        priority: AttackPriority = AttackPriority.MEDIUM,
    ) -> AttackPlan:
        """Create an attack plan with optimal troop composition.
        
        Args:
            target_village: ID of the target village.
            target_coordinates: Target village coordinates (row, col).
            target_troops: Enemy troops at the target location.
            available_units: Available troops for the attack.
            attack_type: Type of attack to plan.
            priority: Attack priority level.
            
        Returns:
            Completed AttackPlan object.
        """
        # Calculate distance and march time
        source_coords = (0, 0)  # Would come from village data
        distance = self.calculate_distance(source_coords, target_coordinates)
        
        # Determine required attack power based on attack type
        enemy_defense = self.calculate_defense_power(target_troops)
        
        # Attack type multipliers
        type_multipliers = {
            AttackType.SCOUT: 1.2,
            AttackType.HARD_ATTACK: 2.5,
            AttackType.SNIP: 1.5,
            AttackType.COALITION: 3.0,
        }
        multiplier = type_multipliers.get(attack_type, 1.5)
        
        required_power = int(enemy_defense * multiplier)
        
        # Select optimal troops
        selected_units = self._select_optimal_troops(
            available_units, required_power, attack_type
        )
        
        # Calculate march time
        march_time = self.calculate_march_time(distance, selected_units)
        
        # Create attack plan using the actual model
        plan_id = f"attack_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        plan = AttackPlan(
            plan_id=plan_id,
            attacker_villages=[],
            target_village=target_village,
            target_coordinates=target_coordinates,
            troop_compositions={plan_id: {u.unit_type: u.count for u in selected_units}},
            estimated_march_time=timedelta(minutes=march_time),
            status="pending",
            attack_type=attack_type,
        )
        
        self._active_plans[plan_id] = plan
        logger.info(
            "Attack plan created: %s (target: %s, distance: %d tiles, march time: %.1f min)",
            plan_id,
            target_village,
            distance,
            march_time,
        )
        
        return plan

    def _select_optimal_troops(
        self,
        available: list[TroopUnit],
        required_power: int,
        attack_type: AttackType,
    ) -> list[TroopUnit]:
        """Select optimal troop composition for an attack.
        
        Uses a greedy algorithm to maximize attack power while minimizing
        wasted troops.
        
        Args:
            available: Available troop units.
            required_power: Required attack power.
            attack_type: Type of attack being planned.
            
        Returns:
            List of selected troop units for the attack.
        """
        selected = []
        remaining_power = required_power
        
        # Sort troops by attack bonus (highest first)
        sorted_units = sorted(
            available,
            key=lambda u: TROOP_BONUSES.get(u.unit_type, {}).get("attack", 0),
            reverse=True,
        )
        
        for unit_template in sorted_units:
            if remaining_power <= 0:
                break
            
            bonus = TROOP_BONUSES.get(unit_template.unit_type, {})
            attack_bonus = bonus.get("attack", 0)
            
            # Calculate how many units needed
            if attack_bonus > 0:
                units_needed = math.ceil(remaining_power / attack_bonus)
            else:
                units_needed = 0
            
            # Don't exceed available count
            actual_count = min(units_needed, unit_template.count)
            
            if actual_count > 0:
                selected.append(TroopUnit(
                    unit_type=unit_template.unit_type,
                    count=actual_count,
                    stationed_at="",
                ))
                remaining_power -= (actual_count * attack_bonus)
        
        return selected

    def execute_attack(self, plan_id: str, interceptor=None) -> bool:
        """Execute an attack plan via the browser.
        
        Args:
            plan_id: ID of the attack plan to execute.
            interceptor: Optional CDP interceptor for automation.
            
        Returns:
            True if attack was initiated successfully.
        """
        plan = self._active_plans.get(plan_id)
        if not plan:
            logger.error("Attack plan not found: %s", plan_id)
            return False
        
        if plan.status != "pending":
            logger.warning("Attack already in progress or completed: %s", plan_id)
            return False
        
        # Update status
        plan.status = "scheduled"
        
        # If we have an interceptor, send the attack via browser automation
        if interceptor:
            try:
                # Build attack parameters
                units = {}
                for village_id, composition in plan.troop_compositions.items():
                    units[village_id] = composition
                
                params = {
                    "target_x": plan.target_coordinates[0],
                    "target_y": plan.target_coordinates[1],
                    "units": units,
                    "attack_type": plan.attack_type.value,
                }
                
                result = interceptor.execute_game_action("sendAttack", params)
                
                if result:
                    plan.status = "success"
                    logger.info("Attack %s sent successfully", plan_id)
                    return True
                else:
                    plan.status = "failed"
                    logger.error("Attack %s failed to send", plan_id)
                    return False
                    
            except Exception as e:
                logger.error("Error executing attack %s: %s", plan_id, e)
                plan.status = "failed"
                return False
        
        # Without interceptor, mark as queued for manual execution
        plan.status = "scheduled"
        logger.info("Attack %s queued for manual execution", plan_id)
        return True

    def log_attack_result(
        self,
        plan_id: str,
        outcome: AttackOutcome,
        loot_gained: int = 0,
        troops_lost: dict[str, int] | None = None,
        notes: str = "",
    ) -> None:
        """Log the result of an attack.
        
        Args:
            plan_id: ID of the executed attack plan.
            outcome: Outcome of the attack (victory, defeat, etc.).
            loot_gained: Amount of resources looted.
            troops_lost: Dictionary of unit types and counts lost.
            notes: Additional notes about the attack.
        """
        plan = self._active_plans.get(plan_id)
        
        log_entry = {
            "plan_id": plan_id,
            "attacker_village": plan.attacker_villages[0] if plan.attacker_villages else "",
            "target_village": plan.target_village if plan else "",
            "target_coordinates": str(plan.target_coordinates) if plan else "",
            "outcome": outcome.value,
            "resources_looted": loot_gained,
            "troops_lost": troops_lost or {},
            "notes": notes,
            "completed_at": datetime.now().isoformat(),
        }
        
        # Save to database
        self.attack_log_repo.insert("attack_logs", log_entry)
        
        # Update plan status
        if plan:
            plan.status = outcome.value
        
        self._attack_history.append(log_entry)
        logger.info(
            "Attack %s logged: %s (loot: %d)",
            plan_id,
            outcome.value,
            loot_gained,
        )

    def get_attack_history(
        self,
        limit: int = 50,
        outcome_filter: str | None = None
    ) -> list[dict]:
        """Get attack history with optional filtering.
        
        Args:
            limit: Maximum number of results to return.
            outcome_filter: Filter by outcome string (e.g., 'win').
            
        Returns:
            List of attack log entries.
        """
        if outcome_filter:
            return [
                entry for entry in self._attack_history[-limit:]
                if entry.get("outcome") == outcome_filter
            ]
        
        return self._attack_history[-limit:]

    def get_village_stats(self, village_id: str) -> dict:
        """Get attack statistics for a specific village.
        
        Args:
            village_id: ID of the village.
            
        Returns:
            Dictionary containing attack statistics.
        """
        # Filter attacks by village
        village_attacks = [
            a for a in self._attack_history
            if a.get("attacker_village") == village_id or
               a.get("target_village") == village_id
        ]
        
        total_attacks = len(village_attacks)
        victories = sum(1 for a in village_attacks if a.get("outcome") == "win")
        defeats = sum(1 for a in village_attacks if a.get("outcome") == "loss")
        total_loot = sum(a.get("resources_looted", 0) for a in village_attacks)
        
        return {
            "village_id": village_id,
            "total_attacks": total_attacks,
            "victories": victories,
            "defeats": defeats,
            "win_rate": (victories / total_attacks * 100) if total_attacks > 0 else 0,
            "total_loot": total_loot,
            "avg_loot_per_attack": (
                total_loot / total_attacks if total_attacks > 0 else 0
            ),
        }

    def cancel_plan(self, plan_id: str) -> bool:
        """Cancel an attack plan.
        
        Args:
            plan_id: ID of the plan to cancel.
            
        Returns:
            True if successfully cancelled.
        """
        plan = self._active_plans.get(plan_id)
        if not plan:
            return False
        
        plan.status = "cancelled"
        
        del self._active_plans[plan_id]
        logger.info("Attack plan cancelled: %s", plan_id)
        
        return True

    def get_active_plans(self) -> list[AttackPlan]:
        """Get all active (non-completed) attack plans.
        
        Returns:
            List of active AttackPlan objects.
        """
        return [
            plan for plan in self._active_plans.values()
            if plan.status in ("pending", "scheduled")
        ]
