"""Quest book: which rewards to collect where, which daily quest to exchange.

The quest book holds the general quests (Wren, Marcus, the chieftain …)
and three daily quests (silver). A finished quest's reward goes to the
village selected when it is collected; a large resource reward (up to
150 000 of each) overflows most storages, so it is collected where it fits.
The clicks themselves are made by :class:`BuildClickExecutor`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATUS_ACTIVATABLE = 2
STATUS_ACTIVE = 3
STATUS_DONE = 4

REWARD_XP = 1
REWARD_RESOURCES = 2
REWARD_SILVER = 4
REWARD_BUILDING_LEVEL = 11

RESOURCES = (1, 2, 3, 4)
# Adventures and the envoy's dialogs live in the quest collection too.
ADVENTURE_QUESTS = {991, 992}
ENVOY_QUESTS = {101, 102}
DAILY_FIRST_ID = 10000

# Daily quests the automation cannot finish (attacks, other players, the
# robbers, animals …): the one daily exchange goes to one of these.
DAILY_UNDOABLE = {
    10001,  # win an auction
    10002,  # spy on another player
    10003,  # sell an item
    10005,  # attack targets
    10007,  # celebration
    10008,  # animals
    10009,  # reinforce another player
    10010,  # defeat troops
    10011,  # steal resources
    10016,  # stolen goods / tributes
    10017,  # adventures
    10018,  # annex an oasis
}
# Opening the premium menu (the shop window) finishes this one; nothing is bought.
DAILY_PREMIUM_MENU = 10015

DAILY_NAMES = {
    10000: "Fair trade", 10001: "Outbid everyone", 10002: "Top secret",
    10003: "Cunning auctioneer", 10004: "Master architect", 10005: "Glorious attacker",
    10006: "Applied research", 10007: "Party time", 10008: "Wild hunt",
    10009: "Helping hand", 10010: "Great warrior", 10011: "Devious raider",
    10012: "To arms!", 10013: "Supply and demand", 10014: "Population growth",
    10015: "Premium features", 10016: "Precious goods", 10017: "Adventure time",
    10018: "Oasis",
}

# A resource reward is collected once at most this share of it would be
# lost above what the best storage loses even when empty.
REWARD_LOSS_TOLERANCE = 0.05


@dataclass
class Quest:
    id: int
    status: int
    rewards: dict
    daily: bool = False
    name: str = ""

    @property
    def done(self) -> bool:
        return self.status == STATUS_DONE

    @property
    def resources(self) -> dict[int, int]:
        raw = self.rewards.get(str(REWARD_RESOURCES)) or self.rewards.get(REWARD_RESOURCES) or {}
        if not isinstance(raw, dict):
            return {}
        return {int(k): int(float(v or 0)) for k, v in raw.items() if int(k) in RESOURCES}

    @property
    def building_level(self) -> bool:
        return str(REWARD_BUILDING_LEVEL) in self.rewards or REWARD_BUILDING_LEVEL in self.rewards

    @property
    def building_reward(self) -> tuple[int, int, int] | None:
        """(building type, minimum level, rewarded level) of a building-level reward."""
        raw = self.rewards.get(str(REWARD_BUILDING_LEVEL)) or self.rewards.get(REWARD_BUILDING_LEVEL)
        if not isinstance(raw, dict):
            return None
        try:
            return (int(raw.get("buildingType") or 0), int(raw.get("minLevel") or 0),
                    int(raw.get("rewardedLevel") or 0))
        except (TypeError, ValueError):
            return None

    @property
    def label(self) -> str:
        return self.name or DAILY_NAMES.get(self.id) or f"zadanie {self.id}"


def quests_from_raw(raw: dict | None) -> list[Quest]:
    """Quests of the book (adventures and envoy dialogs left out)."""
    quests = []
    for item in (raw or {}).get("quests") or []:
        try:
            quest_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if quest_id in ADVENTURE_QUESTS or quest_id in ENVOY_QUESTS:
            continue
        rewards = item.get("rewards")
        quests.append(Quest(
            id=quest_id,
            status=int(item.get("status") or 0),
            rewards=rewards if isinstance(rewards, dict) else {},
            daily=quest_id >= DAILY_FIRST_ID,
            name=str(item.get("name") or ""),
        ))
    return quests


@dataclass
class Storage:
    village_id: str
    name: str
    stock: dict[int, float]  # resources plus what merchants bring
    capacity: dict[int, float]  # per resource (warehouse for 1-3, granary for 4)
    need: float = 0.0  # what the village's plan still lacks (ranking only)


def _lost(reward: dict[int, int], storage: Storage, empty: bool = False) -> float:
    lost = 0.0
    for r, amount in reward.items():
        room = storage.capacity.get(r, 0.0) - (0.0 if empty else storage.stock.get(r, 0.0))
        lost += max(0.0, amount - max(0.0, room))
    return lost


def reward_village(reward: dict[int, int], storages: list[Storage]) -> tuple[Storage | None, float]:
    """Where to collect a resource reward now: (village, amount lost).

    The village that loses the least (ideally nothing), then the one whose
    plan needs the most. None while waiting is better: every village would
    lose clearly more than the biggest storage loses when empty (the stock
    is built or shipped away meanwhile).
    """
    total = sum(reward.values())
    if total <= 0 or not storages:
        return (storages[0] if storages else None), 0.0
    best = min(storages, key=lambda s: (_lost(reward, s), -s.need))
    lost = _lost(reward, best)
    floor = min(_lost(reward, s, empty=True) for s in storages)
    if lost > floor + REWARD_LOSS_TOLERANCE * total:
        return None, lost
    return best, lost


def daily_to_exchange(quests: list[Quest], exchanged: int, max_exchanges: int) -> Quest | None:
    """The daily quest to exchange today (one the automation cannot do)."""
    if exchanged >= max_exchanges:
        return None
    for quest in sorted(quests, key=lambda q: q.id):
        if quest.daily and not quest.done and quest.id in DAILY_UNDOABLE:
            return quest
    return None


# Field quests (the game's English titles): (title pattern, field types, one village).
_FIELD_QUESTS = (
    (re.compile(r"^Fields to (\d+) in (\d+)$", re.I), (1, 2, 3), False),
    (re.compile(r"^Croplands to (\d+) in (\d+)$", re.I), (4,), False),
    (re.compile(r"^All resource fields to (\d+)$", re.I), (1, 2, 3, 4), True),
    (re.compile(r"^All Croplands to (\d+)$", re.I), (4,), True),
)


@dataclass
class FieldQuest:
    """All fields of *types* to *level* in *villages* villages."""
    label: str
    types: tuple[int, ...]
    level: int
    villages: int
    reward: float  # total resources


def field_quests(quests: list[Quest]) -> list[FieldQuest]:
    """Active quests about field levels whose reward is resources."""
    out = []
    for quest in quests:
        if quest.status != STATUS_ACTIVE or quest.daily:
            continue
        reward = sum(quest.resources.values())
        if reward <= 0:
            continue
        for pattern, types, single in _FIELD_QUESTS:
            match = pattern.match(quest.name.strip())
            if match:
                count = 1 if single else int(match.group(2))
                out.append(FieldQuest(quest.label, types, int(match.group(1)), count, float(reward)))
                break
    return out
