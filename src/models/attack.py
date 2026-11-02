"""Attack log and planning models."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class AttackOutcome(Enum):
    """Possible outcomes of an attack."""
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"
    FAILED = "failed"


class AttackType(Enum):
    """Types of attacks."""
    SCOUT = "scout"
    HARD_ATTACK = "hard_attack"
    SNIP = "snip"
    COALITION = "coalition"


@dataclass
class AttackLog:
    """Records an attack event."""
    
    attack_id: str = ""
    attacker_village: str = ""
    target_village: str = ""
    target_coordinates: tuple = field(default_factory=lambda: (0, 0, 0))
    timestamp: Optional[datetime] = None
    outcome: Optional[AttackOutcome] = None
    resources_looted: int = 0
    notes: str = ""
    
    # Attack details
    attack_type: AttackType = AttackType.SCOUT
    attacker_troops: dict = field(default_factory=dict)  # unit_type -> count
    defender_troops: Optional[dict] = None  # unit_type -> count
    
    @property
    def is_successful(self) -> bool:
        """Check if attack was successful."""
        return self.outcome == AttackOutcome.WIN
    
    @property
    def loot_ratio(self) -> float:
        """Calculate efficiency of loot collection."""
        if self.resources_looted == 0 or not self.target_village:
            return 0.0
        # Would need target's resource info for full calculation
        return float(self.resources_looted)


@dataclass
class AttackPlan:
    """Represents a planned attack."""
    
    plan_id: str = ""
    attacker_villages: list[str] = field(default_factory=list)
    target_village: str = ""
    target_coordinates: tuple = field(default_factory=lambda: (0, 0, 0))
    
    # Troop compositions per village
    troop_compositions: dict = field(default_factory=dict)
    
    # Timing
    estimated_march_time: Optional[timedelta] = None
    estimated_return_time: Optional[timedelta] = None
    
    # Predictions
    expected_loot: int = 0
    expected_losses: int = 0
    
    # Status
    attack_type: AttackType = AttackType.SCOUT
    status: str = "pending"  # pending, scheduled, executing, success, failed, cancelled
    
    @property
    def total_attacking_troops(self) -> dict:
        """Sum of all attacking troops."""
        totals = {}
        for composition in self.troop_compositions.values():
            if isinstance(composition, dict):
                for unit_type, count in composition.items():
                    totals[unit_type] = totals.get(unit_type, 0) + count
        return totals
    
    def __repr__(self) -> str:
        return (
            f"AttackPlan(target='{self.target_village}', "
            f"type='{self.attack_type.value}', status='{self.status}')"
        )
