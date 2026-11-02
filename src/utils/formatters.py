"""Formatting utilities for display."""

from datetime import timedelta
from typing import Optional


def format_time_remaining(seconds: Optional[float]) -> str:
    """Format seconds into human-readable time string.
    
    Args:
        seconds: Time in seconds
        
    Returns:
        Formatted time string (e.g., "2h 15m", "45s")
    """
    if seconds is None or seconds <= 0:
        return "Done"
    
    seconds = int(seconds)
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def format_coordinates(coords: tuple) -> str:
    """Format coordinates for display.
    
    Args:
        coords: (row, col, field_num) tuple
        
    Returns:
        Formatted coordinate string (e.g., "[5/12/3]")
    """
    if len(coords) >= 3:
        return f"[{coords[0]}/{coords[1]}/{coords[2]}]"
    elif len(coords) >= 2:
        return f"[{coords[0]}/{coords[1]}]"
    return str(coords)


def format_resource_amount(amount: int) -> str:
    """Format resource amount with unit suffix.
    
    Args:
        amount: Resource quantity
        
    Returns:
        Formatted string (e.g., "1.5k", "2.3M")
    """
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"{amount / 1_000:.1f}k"
    return str(amount)


def format_resource_bar(current: int, max_cap: int, width: int = 20) -> str:
    """Create a progress bar for resource display.
    
    Args:
        current: Current amount
        max_cap: Maximum capacity
        width: Width of the progress bar
        
    Returns:
        Formatted progress bar string
    """
    percentage = min(100, (current / max_cap * 100)) if max_cap > 0 else 0
    filled = int(width * percentage / 100)
    empty = width - filled
    
    bar = f"[{'#' * filled}{'.' * empty}]"
    amount_str = format_resource_amount(current)
    cap_str = format_resource_amount(max_cap)
    
    return f"{bar} {amount_str}/{cap_str}"


def format_troop_count(count: int) -> str:
    """Format troop count for display."""
    if count >= 10_000:
        return f"{count / 10_000:.1f}k"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)
