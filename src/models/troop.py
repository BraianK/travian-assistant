"""Troop unit model."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


# Troop types and their base attack/defense bonuses
TROOP_BONUSES = {
    "scout": {"attack": 3, "defense": 1},
    "spear": {"attack": 10, "defense": 25},
    "sword": {"attack": 25, "defense": 15},
    "axe": {"attack": 35, "defense": 10},
    "archer": {"attack": 18, "defense": 5},
    "snipper": {"attack": 15, "defense": 5},
    "shield": {"attack": 5, "defense": 30},
    "marauder": {"attack": 20, "defense": 15},
}


@dataclass
class TroopUnit:
    """Represents a single troop type with count and training status."""
    
    unit_type: str
    count: int = 0
    training_start: Optional[datetime] = None
    training_end: Optional[datetime] = None
    stationed_at: str = ""  # village_id
    
    @property
    def attack_bonus(self) -> int:
        """Get attack bonus for this troop type."""
        return TROOP_BONUSES.get(self.unit_type, {}).get("attack", 0)
    
    @property
    def defense_bonus(self) -> int:
        """Get defense bonus for this troop type."""
        return TROOP_BONUSES.get(self.unit_type, {}).get("defense", 0)
    
    @property
    def is_training(self) -> bool:
        """Check if troops are currently being trained."""
        return self.training_start is not None and self.training_end is not None
    
    @property
    def training_remaining(self) -> Optional[timedelta]:
        """Calculate remaining training time."""
        if not self.is_training:
            return None
        now = datetime.now()
        if now >= self.training_end:
            return timedelta(0)
        return self.training_end - now
    
    @property
    def total_bonus(self) -> int:
        """Calculate total combat bonus (attack + defense)."""
        return self.count * (self.attack_bonus + self.defense_bonus)
    
    def start_training(self, duration_seconds: float):
        """Start training timer."""
        self.training_start = datetime.now()
        self.training_end = self.training_start + timedelta(seconds=duration_seconds)
    
    def complete_training(self):
        """Mark training as complete."""
        self.training_start = None
        self.training_end = None
    
    def __repr__(self) -> str:
        status = "training" if self.is_training else "ready"
        return (
            f"TroopUnit(type='{self.unit_type}', count={self.count}, "
            f"status='{status}')"
        )
