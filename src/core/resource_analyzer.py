"""Resource analysis module for tracking and optimizing production."""

import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ResourceHistoryPoint:
    """A single point in resource history."""
    
    timestamp: datetime
    wood: int = 0
    clay: int = 0
    iron: int = 0
    grain: int = 0
    
    @property
    def total(self) -> int:
        """Total resources at this point."""
        return self.wood + self.clay + self.iron + self.grain


@dataclass
class ProductionRate:
    """Tracks production rates for a resource type."""
    
    resource_type: str
    current_rate: float = 0.0  # per hour
    peak_rate: float = 0.0
    average_rate: float = 0.0
    
    # Historical data
    samples: list[float] = field(default_factory=list)
    
    def add_sample(self, rate: float) -> None:
        """Add a production rate sample."""
        self.samples.append(rate)
        self.current_rate = rate
        
        if rate > self.peak_rate:
            self.peak_rate = rate
        
        if self.samples:
            self.average_rate = sum(self.samples) / len(self.samples)


@dataclass
class ResourcePrediction:
    """Predicts future resource values."""
    
    resource_type: str
    current_amount: int
    predicted_amount: int = 0
    prediction_hours: float = 0.0
    fill_time_estimate: Optional[float] = None  # hours until storage full
    
    # Trends
    trend: str = "stable"  # increasing, decreasing, stable


class ResourceAnalyzer:
    """Analyzes resource production and consumption patterns.
    
    This module tracks resource levels over time, calculates production
    rates, predicts future values, and provides optimization recommendations.
    """

    # Maximum history points to store (for memory efficiency)
    MAX_HISTORY_POINTS = 1000
    
    # Storage capacity defaults per resource type
    DEFAULT_STORAGE_CAPACITY = {
        "wood": 1000,
        "clay": 1000,
        "iron": 1000,
        "grain": 1000,
    }

    def __init__(self):
        """Initialize the resource analyzer."""
        self._history: dict[str, list[ResourceHistoryPoint]] = {}
        self._production_rates: dict[str, ProductionRate] = {
            "wood": ProductionRate("wood"),
            "clay": ProductionRate("clay"),
            "iron": ProductionRate("iron"),
            "grain": ProductionRate("grain"),
        }
        self._storage_caps: dict[str, int] = dict(self.DEFAULT_STORAGE_CAPACITY)

    def record_resource_snapshot(
        self,
        village_id: str,
        resources: dict[str, int],
        production_rates: dict[str, float] | None = None,
    ) -> None:
        """Record a resource snapshot for a village.
        
        Args:
            village_id: ID of the village.
            resources: Current resource amounts {type: amount}.
            production_rates: Current production rates {type: per_hour}.
        """
        # Create history point
        point = ResourceHistoryPoint(
            timestamp=datetime.now(),
            wood=resources.get("wood", 0),
            clay=resources.get("clay", 0),
            iron=resources.get("iron", 0),
            grain=resources.get("grain", 0),
        )
        
        # Add to history
        if village_id not in self._history:
            self._history[village_id] = []
        
        self._history[village_id].append(point)
        
        # Trim history if too long (keep oldest half)
        if len(self._history[village_id]) > self.MAX_HISTORY_POINTS:
            self._history[village_id] = self._history[village_id][-self.MAX_HISTORY_POINTS // 2:]
        
        # Update production rates if provided
        if production_rates:
            for resource_type, rate in production_rates.items():
                if resource_type in self._production_rates:
                    self._production_rates[resource_type].add_sample(rate)
        
        logger.debug(
            "Resource snapshot recorded for %s: W=%d C=%d I=%d G=%d",
            village_id,
            point.wood,
            point.clay,
            point.iron,
            point.grain,
        )

    def get_production_rate(self, resource_type: str) -> float:
        """Get the current production rate for a resource type.
        
        Args:
            resource_type: Type of resource (wood, clay, iron, grain).
            
        Returns:
            Production rate per hour.
        """
        rate = self._production_rates.get(resource_type)
        return rate.current_rate if rate else 0.0

    def predict_resource_level(
        self,
        village_id: str,
        resource_type: str,
        hours_ahead: float = 1.0,
    ) -> int:
        """Predict resource level after a given time period.
        
        Args:
            village_id: ID of the village.
            resource_type: Type of resource to predict.
            hours_ahead: Hours into the future.
            
        Returns:
            Predicted resource amount.
        """
        history = self._history.get(village_id, [])
        if not history:
            return 0
        
        # Get current amount from latest snapshot
        latest = history[-1]
        current = getattr(latest, resource_type, 0)
        
        # Calculate predicted amount
        rate = self.get_production_rate(resource_type)
        storage_cap = self._storage_caps.get(resource_type, 1000)
        
        predicted = min(current + (rate * hours_ahead), storage_cap)
        return int(predicted)

    def calculate_fill_time(
        self,
        village_id: str,
        resource_type: str,
        target_capacity: int | None = None,
    ) -> Optional[float]:
        """Calculate time until storage is full.
        
        Args:
            village_id: ID of the village.
            resource_type: Type of resource.
            target_capacity: Target capacity (None = current max).
            
        Returns:
            Hours until storage is full, or None if already full or rate is 0.
        """
        history = self._history.get(village_id, [])
        if not history:
            return None
        
        latest = history[-1]
        current = getattr(latest, resource_type, 0)
        rate = self.get_production_rate(resource_type)
        
        if rate <= 0 or current >= (target_capacity or 1000):
            return None
        
        capacity = target_capacity or self._storage_caps.get(resource_type, 1000)
        deficit = capacity - current
        
        return deficit / rate

    def get_resource_trends(
        self,
        village_id: str,
        resource_type: str,
        hours_window: float = 24.0,
    ) -> dict:
        """Get resource trend analysis for a time window.
        
        Args:
            village_id: ID of the village.
            resource_type: Type of resource.
            hours_window: Hours to look back.
            
        Returns:
            Dictionary with trend analysis.
        """
        history = self._history.get(village_id, [])
        if len(history) < 2:
            return {
                "resource_type": resource_type,
                "trend": "insufficient_data",
                "change_percentage": 0.0,
                "min_value": 0,
                "max_value": 0,
                "average_value": 0,
            }
        
        # Filter history within window
        cutoff = datetime.now() - timedelta(hours=hours_window)
        filtered = [p for p in history if p.timestamp >= cutoff]
        
        if len(filtered) < 2:
            filtered = history[-10:]  # Fall back to last 10 points
        
        values = [getattr(p, resource_type, 0) for p in filtered]
        
        # Calculate trend
        first_half = values[:len(values) // 2]
        second_half = values[len(values) // 2:]
        
        if first_half and second_half:
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            
            if avg_first > 0:
                change_pct = ((avg_second - avg_first) / avg_first) * 100
            else:
                change_pct = 0.0
            
            if change_pct > 5:
                trend = "increasing"
            elif change_pct < -5:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"
            change_pct = 0.0
        
        return {
            "resource_type": resource_type,
            "trend": trend,
            "change_percentage": round(change_pct, 2),
            "min_value": min(values) if values else 0,
            "max_value": max(values) if values else 0,
            "average_value": int(sum(values) / len(values)) if values else 0,
            "current_value": values[-1] if values else 0,
        }

    def get_optimization_recommendations(
        self,
        village_id: str,
        building_levels: dict[str, int] | None = None,
    ) -> list[dict]:
        """Get resource optimization recommendations.
        
        Args:
            village_id: ID of the village.
            building_levels: Current building levels.
            
        Returns:
            List of optimization recommendations.
        """
        recommendations = []
        
        # Check storage efficiency
        history = self._history.get(village_id, [])
        if not history:
            return recommendations
        
        latest = history[-1]
        
        # Recommendation 1: Storage management
        for resource_type in ["wood", "clay", "iron", "grain"]:
            current = getattr(latest, resource_type, 0)
            capacity = self._storage_caps.get(resource_type, 1000)
            
            fill_pct = (current / capacity * 100) if capacity > 0 else 0
            
            if fill_pct > 90:
                recommendations.append({
                    "type": "storage_warning",
                    "resource": resource_type,
                    "message": f"Storage {fill_pct:.0f}% full - consider using market for storage",
                    "priority": "high",
                })
            elif fill_pct < 30:
                recommendations.append({
                    "type": "low_storage",
                    "resource": resource_type,
                    "message": f"Storage only {fill_pct:.0f}% full - production may be limited",
                    "priority": "medium",
                })
        
        # Recommendation 2: Production optimization
        if building_levels:
            farm_level = building_levels.get("farm", 0)
            if farm_level > 0 and self.get_production_rate("grain") == 0:
                recommendations.append({
                    "type": "production_issue",
                    "resource": "grain",
                    "message": f"Farm level {farm_level} but no grain production detected",
                    "priority": "high",
                })
        
        # Recommendation 3: Balance optimization
        wood_rate = self.get_production_rate("wood")
        clay_rate = self.get_production_rate("clay")
        iron_rate = self.get_production_rate("iron")
        
        rates = [wood_rate, clay_rate, iron_rate]
        non_zero_rates = [r for r in rates if r > 0]
        
        if len(non_zero_rates) >= 2:
            max_rate = max(non_zero_rates)
            min_rate = min(non_zero_rates)
            
            if max_rate > 0 and min_rate / max_rate < 0.3:
                recommendations.append({
                    "type": "unbalanced_production",
                    "message": f"Production rates vary significantly (max: {max_rate:.0f}, min: {min_rate:.0f})",
                    "priority": "low",
                    "suggestion": "Consider upgrading resource buildings to balance production",
                })
        
        return recommendations

    def get_aggregated_stats(self, village_ids: list[str]) -> dict:
        """Get aggregated resource statistics across multiple villages.
        
        Args:
            village_ids: List of village IDs to aggregate.
            
        Returns:
            Dictionary with aggregated statistics.
        """
        totals = {
            "wood": 0,
            "clay": 0,
            "iron": 0,
            "grain": 0,
        }
        
        for village_id in village_ids:
            history = self._history.get(village_id, [])
            if history:
                latest = history[-1]
                totals["wood"] += latest.wood
                totals["clay"] += latest.clay
                totals["iron"] += latest.iron
                totals["grain"] += latest.grain
        
        # Calculate total production rates
        production_rates = {
            "wood": self.get_production_rate("wood"),
            "clay": self.get_production_rate("clay"),
            "iron": self.get_production_rate("iron"),
            "grain": self.get_production_rate("grain"),
        }
        
        # Calculate fill times
        fill_times = {}
        for resource_type in ["wood", "clay", "iron", "grain"]:
            total_capacity = self._storage_caps.get(resource_type, 1000) * len(village_ids)
            rate = production_rates[resource_type]
            
            if rate > 0 and totals[resource_type] < total_capacity:
                fill_times[resource_type] = (
                    (total_capacity - totals[resource_type]) / rate
                )
            else:
                fill_times[resource_type] = None
        
        return {
            "village_count": len(village_ids),
            "total_resources": totals,
            "production_rates_per_hour": production_rates,
            "fill_time_hours": fill_times,
            "timestamp": datetime.now().isoformat(),
        }

    def clear_history(self, village_id: str | None = None) -> None:
        """Clear resource history.
        
        Args:
            village_id: Clear only this village's history (None = all).
        """
        if village_id:
            self._history.pop(village_id, None)
            logger.info("Cleared history for village: %s", village_id)
        else:
            self._history.clear()
            logger.info("Cleared all resource history")
