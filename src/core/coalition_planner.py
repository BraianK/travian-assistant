"""Coalition attack planning module for coordinated alliance attacks."""

import logging
import random
import time
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

try:
    from src.models.attack import AttackPlan, AttackType, AttackOutcome
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from models.attack import AttackPlan, AttackType, AttackOutcome

logger = logging.getLogger(__name__)


@dataclass
class CoalitionMember:
    """Represents an alliance member participating in a coalition attack."""
    
    village_id: str
    village_name: str
    coordinates: tuple = (0, 0, 0)
    available_units: dict = field(default_factory=dict)  # unit_type -> count
    
    # Timing for synchronized arrival
    planned_arrival: Optional[datetime] = None
    march_time_minutes: float = 0.0
    
    # Attack composition from this village
    attack_composition: dict = field(default_factory=dict)  # unit_type -> count
    
    # Status tracking
    status: str = "pending"  # pending, ready, en_route, arrived, completed
    attack_sent: bool = False


@dataclass
class CoalitionAttack:
    """Represents a coordinated coalition attack plan."""
    
    coalition_id: str = ""
    attack_id: str = field(default_factory=lambda: f"coalition_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    target_village: str = ""
    target_coordinates: tuple = (0, 0, 0)
    
    members: list[CoalitionMember] = field(default_factory=list)
    
    # Attack coordination
    planned_arrival: Optional[datetime] = None
    attack_type: AttackType = AttackType.COALITION
    
    # Status
    status: str = "planning"  # planning, preparing, en_route, executing, completed, failed
    
    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    scheduled_start: Optional[datetime] = None
    
    @property
    def total_attacking_troops(self) -> int:
        """Total troops across all coalition members."""
        total = 0
        for member in self.members:
            total += sum(member.attack_composition.values())
        return total
    
    @property
    def is_ready(self) -> bool:
        """Check if all members are ready to attack."""
        return all(m.status == "ready" for m in self.members) and len(self.members) > 0
    
    @property
    def attack_power(self) -> int:
        """Calculate total attack power of coalition."""
        try:
            from src.models.troop import TROOP_BONUSES
        except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
            from models.troop import TROOP_BONUSES
        total_power = 0
        for member in self.members:
            for unit_type, count in member.attack_composition.items():
                bonus = TROOP_BONUSES.get(unit_type, {})
                total_power += count * bonus.get("attack", 0)
        return total_power


class CoalitionPlanner:
    """Plans and coordinates coalition attacks across alliance members.
    
    This module handles synchronized multi-village attacks where multiple
    alliance members coordinate their attacks to arrive simultaneously.
    """

    # Minimum coordination time (minutes) before attack execution
    MIN_COORDINATION_TIME = 15.0
    
    # Maximum allowed deviation for arrival times (percentage)
    MAX_DEVIATION = 0.10  # 10%

    def __init__(self):
        """Initialize the coalition planner."""
        self._active_coalitions: dict[str, CoalitionAttack] = {}
        self._coalition_history: list[dict] = []
        
        # Synchronization timer
        self._timers: dict[str, object] = {}

    def create_coalition_attack(
        self,
        coalition_id: str,
        target_village: str,
        target_coordinates: tuple,
        members: list[dict],
        attack_type: AttackType = AttackType.COALITION,
    ) -> CoalitionAttack:
        """Create a new coalition attack plan.
        
        Args:
            coalition_id: Unique identifier for the coalition/alliance.
            target_village: ID of the target village.
            target_coordinates: Target coordinates (row, col).
            members: List of member dictionaries with village info.
            attack_type: Type of coordinated attack.
            
        Returns:
            Created CoalitionAttack object.
        """
        coalition = CoalitionAttack(
            coalition_id=coalition_id,
            target_village=target_village,
            target_coordinates=target_coordinates,
            attack_type=attack_type,
        )
        
        # Add members
        for member_data in members:
            member = CoalitionMember(
                village_id=member_data.get("village_id", ""),
                village_name=member_data.get("village_name", ""),
                coordinates=tuple(member_data.get("coordinates", (0, 0, 0))),
                available_units=member_data.get("available_units", {}),
            )
            coalition.members.append(member)
        
        self._active_coalitions[coalition.attack_id] = coalition
        logger.info(
            "Coalition attack created: %s (target: %s, members: %d)",
            coalition.attack_id,
            target_village,
            len(members),
        )
        
        return coalition

    def calculate_march_times(self, coalition: CoalitionAttack) -> dict[str, float]:
        """Calculate march times for each coalition member.
        
        Args:
            coalition: The coalition attack plan.
            
        Returns:
            Dictionary mapping village_id to march time in minutes.
        """
        try:
            from src.core.attack_planner import AttackPlanner
        except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
            from core.attack_planner import AttackPlanner
        
        planner = AttackPlanner.__new__(AttackPlanner)
        planner.TROOP_SPEEDS = AttackPlanner.TROOP_SPEEDS
        
        march_times = {}
        for member in coalition.members:
            distance = abs(member.coordinates[0] - coalition.target_coordinates[0]) + \
                       abs(member.coordinates[1] - coalition.target_coordinates[1])
            
            # Use slowest unit in composition
            units = []
            for unit_type, count in member.attack_composition.items():
                try:
                    from src.models.troop import TroopUnit
                except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
                    from models.troop import TroopUnit
                units.append(TroopUnit(unit_type=unit_type, count=count))
            
            march_time = planner.calculate_march_time(distance, units) if units else distance * 2
            march_times[member.village_id] = march_time
        
        return march_times

    def synchronize_arrivals(
        self,
        coalition: CoalitionAttack,
        target_arrival: Optional[datetime] = None,
    ) -> dict[str, datetime]:
        """Synchronize attack arrivals to happen simultaneously.
        
        Adjusts departure times so all attacking forces arrive at the
        same moment, accounting for different march times.
        
        Args:
            coalition: The coalition attack plan.
            target_arrival: Desired arrival time (defaults to now + coordination_time).
            
        Returns:
            Dictionary mapping village_id to scheduled departure time.
        """
        if target_arrival is None:
            target_arrival = datetime.now() + timedelta(
                minutes=self.MIN_COORDINATION_TIME * 2
            )
        
        coalition.planned_arrival = target_arrival
        
        march_times = self.calculate_march_times(coalition)
        departure_times = {}
        
        for member in coalition.members:
            march_time = march_times.get(member.village_id, 10.0)
            
            # Departure = arrival - march_time
            departure = target_arrival - timedelta(minutes=march_time)
            departure_times[member.village_id] = departure
            
            member.planned_arrival = target_arrival
            member.march_time_minutes = march_time
        
        logger.info(
            "Synchronized arrivals: %d members, target arrival: %s",
            len(coalition.members),
            target_arrival.isoformat(),
        )
        
        return departure_times

    def prepare_member_attack(
        self,
        coalition_id: str,
        village_id: str,
        attack_composition: dict[str, int],
    ) -> bool:
        """Prepare a coalition member's attack composition.
        
        Args:
            coalition_id: The coalition identifier.
            village_id: The attacking village ID.
            attack_composition: Dictionary of unit_type -> count.
            
        Returns:
            True if preparation was successful.
        """
        coalition = self._active_coalitions.get(coalition_id)
        if not coalition:
            logger.error("Coalition not found: %s", coalition_id)
            return False
        
        member = next(
            (m for m in coalition.members if m.village_id == village_id),
            None,
        )
        
        if not member:
            logger.error("Member not found: %s", village_id)
            return False
        
        member.attack_composition = attack_composition
        member.status = "ready"
        
        logger.info(
            "Member %s ready for coalition attack (troops: %d)",
            village_id,
            sum(attack_composition.values()),
        )
        
        return True

    def execute_coalition_attack(
        self,
        coalition_id: str,
        interceptor=None,
        timing_buffer_seconds: int = 5,
    ) -> bool:
        """Execute a synchronized coalition attack.
        
        Sends attacks from all coalition members with timing synchronization
        to ensure simultaneous arrival.
        
        Args:
            coalition_id: The coalition identifier (attack_id).
            interceptor: Browser CDP interceptor for automation.
            timing_buffer_seconds: Buffer time in seconds before actual arrival.
            
        Returns:
            True if attack was executed successfully.
        """
        coalition = self._active_coalitions.get(coalition_id)
        if not coalition:
            logger.error("Coalition not found: %s", coalition_id)
            return False
        
        if not coalition.is_ready:
            logger.warning("Coalition not ready: %d/%d members ready",
                         sum(1 for m in coalition.members if m.status == "ready"),
                         len(coalition.members))
            return False
        
        coalition.status = "en_route"
        
        # Send attacks with timing synchronization
        success_count = 0
        for member in coalition.members:
            if interceptor and member.attack_composition:
                try:
                    params = {
                        "target_x": coalition.target_coordinates[0],
                        "target_y": coalition.target_coordinates[1],
                        "units": member.attack_composition,
                        "coalition_id": coalition.coalition_id,
                    }
                    
                    # Add random delay for anti-detection
                    delay_ms = random.randint(500, 2000)
                    time.sleep(delay_ms / 1000)
                    
                    result = interceptor.execute_game_action("sendAttack", params)
                    
                    if result:
                        member.attack_sent = True
                        member.status = "en_route"
                        success_count += 1
                        
                except Exception as e:
                    logger.error("Failed to send attack from %s: %s", member.village_id, e)
        
        coalition.status = "completed" if success_count == len(coalition.members) else "failed"
        
        logger.info(
            "Coalition attack %s executed: %d/%d members successful",
            coalition_id,
            success_count,
            len(coalition.members),
        )
        
        self._coalition_history.append({
            "attack_id": coalition.attack_id,
            "coalition_id": coalition.coalition_id,
            "target": coalition.target_village,
            "members": len(coalition.members),
            "success_count": success_count,
            "status": coalition.status,
            "completed_at": datetime.now().isoformat(),
        })
        
        return success_count == len(coalition.members)

    def get_coalition_status(self, coalition_id: str) -> dict:
        """Get the current status of a coalition attack.
        
        Args:
            coalition_id: The coalition identifier.
            
        Returns:
            Dictionary with coalition status details.
        """
        coalition = self._active_coalitions.get(coalition_id)
        if not coalition:
            return {"error": "Coalition not found"}
        
        member_status = []
        for member in coalition.members:
            member_status.append({
                "village_id": member.village_id,
                "village_name": member.village_name,
                "status": member.status,
                "troops": sum(member.attack_composition.values()),
                "march_time_minutes": member.march_time_minutes,
                "attack_sent": member.attack_sent,
            })
        
        return {
            "attack_id": coalition.attack_id,
            "coalition_id": coalition.coalition_id,
            "target_village": coalition.target_village,
            "target_coordinates": coalition.target_coordinates,
            "status": coalition.status,
            "total_troops": coalition.total_attacking_troops,
            "attack_power": coalition.attack_power,
            "members": member_status,
            "planned_arrival": coalition.planned_arrival.isoformat() if coalition.planned_arrival else None,
        }

    def cancel_coalition(self, coalition_id: str) -> bool:
        """Cancel a coalition attack plan.
        
        Args:
            coalition_id: The coalition identifier.
            
        Returns:
            True if successfully cancelled.
        """
        coalition = self._active_coalitions.get(coalition_id)
        if not coalition:
            return False
        
        coalition.status = "cancelled"
        
        for member in coalition.members:
            member.status = "cancelled"
        
        del self._active_coalitions[coalition_id]
        
        logger.info("Coalition attack cancelled: %s", coalition_id)
        
        return True

    def get_active_coalitions(self) -> list[dict]:
        """Get all active coalition attacks.
        
        Returns:
            List of active coalition status dictionaries.
        """
        return [
            self.get_coalition_status(cid)
            for cid in self._active_coalitions
            if self._active_coalitions[cid].status not in ("completed", "failed", "cancelled")
        ]

    def get_coalition_history(self, limit: int = 50) -> list[dict]:
        """Get coalition attack history.
        
        Args:
            limit: Maximum number of results to return.
            
        Returns:
            List of coalition attack history entries.
        """
        return self._coalition_history[-limit:]
