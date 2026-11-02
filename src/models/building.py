"""Building queue model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# Building types and their upgrade time multipliers (base time * level)
BUILDING_BASE_TIMES = {
    "farm": 10,
    "woodcutters": 15,
    "claypit": 15,
    "ironmine": 20,
    "warehouse": 30,
    "chief": 60,
    "temple": 120,
    "barracks": 45,
    "training_camp": 45,
    "wall": 20,
    "building_site": 15,
}


@dataclass
class BuildingQueue:
    """Represents a building upgrade in progress."""
    
    village_id: str = ""
    building_type: str = ""  # farm, warehouse, etc.
    target_level: int = 0
    start_time: Optional[datetime] = None
    completion_time: Optional[datetime] = None
    is_active: bool = True
    
    @property
    def remaining_time(self) -> Optional[float]:
        """Get remaining seconds until completion (None if not active)."""
        if not self.is_active or self.completion_time is None:
            return None
        now = datetime.now()
        diff = (self.completion_time - now).total_seconds()
        return max(0, diff)
    
    @property
    def progress_percentage(self) -> float:
        """Calculate completion percentage."""
        if not self.is_active or not self.start_time or not self.completion_time:
            return 0.0
        total = (self.completion_time - self.start_time).total_seconds()
        elapsed = (datetime.now() - self.start_time).total_seconds()
        if total <= 0:
            return 100.0
        progress = min(100.0, (elapsed / total) * 100)
        return progress
    
    @property
    def is_complete(self) -> bool:
        """Check if building upgrade is complete."""
        if self.completion_time is None:
            return False
        return datetime.now() >= self.completion_time
    
    def mark_complete(self):
        """Mark the building as completed."""
        self.is_active = False
        self.completion_time = datetime.now()
    
    @classmethod
    def estimate_completion(cls, building_type: str, target_level: int) -> float:
        """Estimate completion time in seconds for a building upgrade.
        
        Args:
            building_type: Type of building (farm, warehouse, etc.)
            target_level: Target level for upgrade
            
        Returns:
            Estimated time in seconds
        """
        base_time = BUILDING_BASE_TIMES.get(building_type, 30)
        # Time increases exponentially with level
        return base_time * (1.5 ** (target_level - 1))
    
    def __repr__(self) -> str:
        status = "active" if self.is_active else "complete"
        return (
            f"BuildingQueue(type='{self.building_type}', level={self.target_level}, "
            f"status='{status}')"
        )
