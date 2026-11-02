"""Helper utility functions."""

import hashlib
import random
import string
from typing import Optional


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    raw = f"{prefix}{random.randint(10000, 99999)}-{random.randint(100, 999)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def format_resources(amount: int) -> str:
    """Format resource amount with appropriate units."""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"{amount / 1_000:.1f}k"
    return str(amount)


def calculate_distance(coord1: tuple, coord2: tuple) -> float:
    """Calculate distance between two coordinates.
    
    Coordinates are (row, col, field_num).
    Uses Manhattan distance for row/col, ignores field number.
    """
    if len(coord1) < 2 or len(coord2) < 2:
        return float('inf')
    
    row_diff = abs(coord1[0] - coord2[0])
    col_diff = abs(coord1[1] - coord2[1])
    return row_diff + col_diff


def calculate_march_time(distance: float, troop_speed: float = 1.0) -> float:
    """Calculate approximate march time in seconds.
    
    Args:
        distance: Distance between coordinates (Manhattan)
        troop_speed: Base troop speed factor (1.0 = average)
    
    Returns:
        Approximate march time in seconds
    """
    # Base Travian travel time formula approximation
    base_time = 30 + (distance * 15)  # seconds
    return base_time / troop_speed


def random_delay(min_ms: int, max_ms: int) -> int:
    """Generate a random delay in milliseconds."""
    return random.randint(min_ms, max_ms)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(value, max_val))
