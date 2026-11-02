"""Village and resource models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ResourcePoint:
    """Represents a single resource type with production tracking."""
    
    type: str  # wood, clay, iron, grain
    current_amount: int = 0
    max_capacity: int = 0
    production_rate: float = 0.0  # per hour
    last_updated: Optional[datetime] = None
    
    @property
    def fill_percentage(self) -> float:
        """Calculate storage fill percentage."""
        if self.max_capacity == 0:
            return 0.0
        return (self.current_amount / self.max_capacity) * 100
    
    @property
    def time_to_full(self) -> Optional[float]:
        """Hours until storage is full (None if already full)."""
        if self.current_amount >= self.max_capacity:
            return None
        if self.production_rate <= 0:
            return None
        remaining = self.max_capacity - self.current_amount
        return remaining / self.production_rate
    
    def update(self, current_amount: int, max_capacity: int, production_rate: float):
        """Update resource values."""
        self.current_amount = current_amount
        self.max_capacity = max_capacity
        self.production_rate = production_rate
        self.last_updated = datetime.now()


@dataclass
class Village:
    """Represents a Travian Kingdoms village."""
    
    village_id: str = ""
    account_id: str = ""
    name: str = ""
    coordinates: tuple = field(default_factory=lambda: (0, 0, 0))
    population: int = 0
    farm_level: int = 0
    chief_level: int = 0
    temple_level: int = 0
    
    # Resources
    wood: ResourcePoint = field(default_factory=lambda: ResourcePoint("wood"))
    clay: ResourcePoint = field(default_factory=lambda: ResourcePoint("clay"))
    iron: ResourcePoint = field(default_factory=lambda: ResourcePoint("iron"))
    grain: ResourcePoint = field(default_factory=lambda: ResourcePoint("grain"))
    
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @property
    def row(self) -> int:
        """Get row coordinate."""
        return self.coordinates[0]
    
    @property
    def col(self) -> int:
        """Get column coordinate."""
        return self.coordinates[1]
    
    @property
    def field_num(self) -> int:
        """Get field number."""
        return self.coordinates[2]
    
    @property
    def total_population(self) -> int:
        """Get current population (chief + troops)."""
        return self.population
    
    @property
    def resource_total(self) -> int:
        """Sum of all resources."""
        return (
            self.wood.current_amount
            + self.clay.current_amount
            + self.iron.current_amount
            + self.grain.current_amount
        )
    
    @property
    def resource_capacity(self) -> int:
        """Total storage capacity."""
        cap = max(self.wood.max_capacity, self.clay.max_capacity,
                  self.iron.max_capacity, self.grain.max_capacity)
        return cap if cap > 0 else 1
    
    def update_resources(self, resources: dict):
        """Update all resource values from dictionary."""
        for res_type in ["wood", "clay", "iron", "grain"]:
            if res_type in resources:
                data = resources[res_type]
                current = data.get("current_amount", 0)
                max_cap = data.get("max_capacity", 0)
                rate = data.get("production_rate", 0.0)
                getattr(self, res_type).update(current, max_cap, rate)
        self.updated_at = datetime.now()
    
    def update_state(self, state: dict):
        """Update village state from API response."""
        if "population" in state:
            self.population = state["population"]
        if "farm_level" in state:
            self.farm_level = state["farm_level"]
        if "chief_level" in state:
            self.chief_level = state["chief_level"]
        if "temple_level" in state:
            self.temple_level = state["temple_level"]
        self.updated_at = datetime.now()
    
    def __repr__(self) -> str:
        return (
            f"Village(id='{self.village_id}', name='{self.name}', "
            f"coords=({self.row},{self.col},{self.field_num}))"
        )
