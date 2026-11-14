"""Split of a village's stock for the "Mielenie" tab (NPC merchant).

``empty`` turns all crop into wood, clay and iron in the set proportions;
``refill`` fills the granary with crop taken from the other resources and
spreads the rest in the same proportions. The split is typed into the
marketplace's NPC merchant form by :class:`BuildClickExecutor`.
"""

from __future__ import annotations


def milling_plan(
    current: list[int],
    granary_capacity: float,
    mode: str,
    wood_percent: int,
    clay_percent: int,
    iron_percent: int,
) -> list[int]:
    """Wanted [wood, clay, iron, crop] for the current [wood, clay, iron, crop]."""
    if mode not in ("empty", "refill"):
        raise ValueError("Invalid milling mode.")
    percents = (int(wood_percent), int(clay_percent), int(iron_percent))
    if any(p < 0 for p in percents) or sum(percents) != 100:
        raise ValueError("The split must add up to 100%.")
    if len(current) != 4 or any(int(v) < 0 for v in current):
        raise ValueError("Invalid resources.")
    total = sum(int(v) for v in current)
    crop = min(int(granary_capacity), total) if mode == "refill" else 0
    remainder = total - crop
    split = [remainder * p // 100 for p in percents]
    # The rounding rest goes to the largest share (the first one on a tie).
    largest = max(range(3), key=lambda i: (percents[i], -i))
    split[largest] += remainder - sum(split)
    return split + [crop]
