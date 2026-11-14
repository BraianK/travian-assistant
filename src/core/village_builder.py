"""Resource-field build planner, transfer advisor and gold helpers.

Pure calculations on the data the game client already keeps for every own
village (``Building`` collection, ``BuildingQueue``, ``Village``, ``Merchants``),
read by :mod:`src.core.build_clicker`:

* every field carries the server's exact upgrade costs, build times, hourly
  production (``effect``) and population (``upgradeSupplyUsageSums``) for the
  next levels, so nothing here is estimated from formulas;
* a field step is ranked by its payback time: total cost / net production
  gain per hour (production gain after bonuses minus the extra crop eaten by
  the added population). Resources are valued 1:1 (the NPC trader converts
  them 1:1);
* fields go up to the target level (capital and other villages separately);
* when a step costs more than the warehouse/granary holds, upgrading the
  storage comes first; when net crop would drop below zero, a cropland comes
  first;
* a village is *queue-bound* when building its remaining fields takes longer
  than producing their costs. There the build queue, not resources, decides
  when it is done, so the main building (shorter build times) goes first
  while it saves clearly more queue time than it takes, and its spare
  resources are better spent in resource-bound villages;
* :func:`plan_transfers` recommends shipments: spare resources of queue-bound
  (or overflowing) villages to resource-bound ones, and any shipment that
  lets a village start its next step sooner (merchant travel included);
* :func:`finish_price` / NPC timing support the gold options (finish a build
  instantly, NPC trader).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

RESOURCES = (1, 2, 3, 4)  # wood, clay, iron, crop
RESOURCE_NAMES = {1: "Drewno", 2: "Glina", 3: "Żelazo", 4: "Zboże"}
FIELD_NAMES = {1: "Las", 2: "Glinianka", 3: "Kopalnia żelaza", 4: "Pole zboża"}
BUILDING_NAMES = {
    5: "Tartak", 6: "Cegielnia", 7: "Huta żelaza", 8: "Młyn", 9: "Piekarnia",
    10: "Magazyn", 11: "Spichlerz", 15: "Budynek główny", 17: "Targowisko",
    19: "Koszary", 20: "Stajnia", 21: "Warsztat", 25: "Rezydencja", 26: "Pałac",
    29: "Wielkie koszary", 30: "Wielka stajnia",
    38: "Wielki magazyn", 39: "Wielki spichlerz",
}
# Production bonus buildings and the resource they boost (effect = % bonus).
BONUS_BUILDINGS = {5: 1, 6: 2, 7: 3, 8: 4, 9: 4}
MARKETPLACE = 17
MAIN_BUILDING = 15
WAREHOUSE_TYPES = (10, 38)
GRANARY_TYPES = (11, 39)
QUEUE_VILLAGE = 1
QUEUE_RESOURCES = 2

PLAN_DEPTH = 8
# Crop bank: warehouse room for this many hours of production on top of a
# third of the granaries (what milling turns into each resource).
WAREHOUSE_BANK_HOURS = 2.0
# The main building is upgraded while it saves this many times its own
# build time on the remaining field queue.
MAIN_BUILDING_MIN_GAIN = 2.0
# Non queue-bound villages only give away what would overflow this soon.
OVERFLOW_HORIZON_HOURS = 2.0


@dataclass
class Building:
    """One field or building with the game's per-level tables."""

    location: int
    type: int
    level: int
    max_level: int
    costs: dict[int, dict[int, int]]  # from level -> {resource: amount}
    times: dict[int, int]  # from level -> seconds
    effects: dict[int, float]  # level -> production/h, capacity or time %
    supply: dict[int, int]  # level -> population sum
    queued: int = 0  # levels already waiting in any building queue

    @property
    def planned_level(self) -> int:
        return self.level + self.queued

    def can_plan(self, level: int) -> bool:
        return (
            level in self.costs
            and level + 1 in self.effects
            and level in self.effects
            and level + 1 in self.supply
            and level in self.supply
        )


@dataclass
class QueueEntry:
    queue_type: int
    location: int
    building_type: int
    finished: float  # server time; 0 while waiting
    id: str = ""
    paid: bool = True  # master builder: resources already reserved


@dataclass
class Village:
    id: str
    name: str
    is_capital: bool
    x: int
    y: int
    tribe: int
    resources: dict[int, float]
    capacity: dict[int, float]
    production: dict[int, float]  # net per hour (crop after upkeep)
    fields: list[Building]
    storages: list[Building]
    free_slots: dict[int, int]  # queue type -> free slots
    busy_until: float = 0.0  # server time the building queue frees up
    merchants_free: int = 0
    merchant_carry: int = 0
    main_building: Building | None = None
    queue: list[QueueEntry] = field(default_factory=list)
    # Resources on their way here (raid loot, merchants): (server time, amounts).
    incoming: list[tuple[float, dict[int, float]]] = field(default_factory=list)
    bonus: list[Building] = field(default_factory=list)
    marketplace: Building | None = None
    # Hourly resources eaten by continuous recruitment (app's recruitment tab).
    recruitment: dict[int, float] = field(default_factory=dict)
    # Stock kept back for recruitment (not spent on building or shipped).
    recruitment_reserve: dict[int, float] = field(default_factory=dict)
    # Recruitment buildings present (barracks 19/29, stable 20/30, workshop 21).
    recruit_buildings: set[int] = field(default_factory=set)
    unit_times: dict[int, float] = field(default_factory=dict)  # seconds per unit
    empty_slots: list[int] = field(default_factory=list)  # free building sites
    military: list[Building] = field(default_factory=list)  # barracks, stable, ...
    type_levels: dict[int, int] = field(default_factory=dict)  # building type -> highest level
    unit_queue_end: dict[int, float] = field(default_factory=dict)  # building type -> server time
    protected: float = 0.0  # per resource the cranny keeps from raiders
    # Hero production above its "all resources" setting (a temporary switch
    # to one resource): not counted when sizing storage.
    hero_excess: dict[int, float] = field(default_factory=dict)
    is_town: bool = False  # a town: fields can go above 10 (the game's lvlMax)
    residence: Building | None = None  # residence or palace (settlers)
    units_home: dict[int, int] = field(default_factory=dict)  # unit number -> count (no hero)
    # Own reinforcements standing elsewhere: [{troop_id, location, units}].
    units_elsewhere: list[dict] = field(default_factory=list)
    # Settlers (home + in training), their unit type, costs and seconds each.
    settlers: int = 0
    settler_unit: int = 0
    settler_costs: dict[int, float] = field(default_factory=dict)
    settler_seconds: float = 0.0

    def build_production(self) -> dict[int, float]:
        """Production left for building after continuous recruitment."""
        return {
            r: self.production.get(r, 0) - self.recruitment.get(r, 0) for r in RESOURCES
        }

    def build_resources(self) -> dict[int, float]:
        """Stock available for building (the recruitment reserve is kept)."""
        return {
            r: max(0.0, self.resources.get(r, 0) - self.recruitment_reserve.get(r, 0))
            for r in RESOURCES
        }

    def buildings(self) -> list[Building]:
        extra = [self.main_building] if self.main_building else []
        if self.residence is not None:
            extra.append(self.residence)
        return self.fields + self.storages + self.bonus + self.military + extra

    def incoming_total(self) -> dict[int, float]:
        total = {r: 0.0 for r in RESOURCES}
        for _at, amounts in self.incoming:
            for r, amount in amounts.items():
                total[r] = total.get(r, 0.0) + amount
        return total


@dataclass
class Step:
    village_id: str
    location: int
    type: int
    from_level: int
    costs: dict[int, int]
    seconds: int
    gain: float  # net production gain per hour (resource-equivalent)
    payback_hours: float | None
    reason: str
    raw_gain: float = 0.0  # production gain per hour of its resource
    construct: bool = False  # a new building on an empty site
    train: bool = False  # not a building: units to train (settlers)
    upkeep: float = 0.0  # crop eaten by a new building (construct only)

    @property
    def is_field(self) -> bool:
        return self.type in RESOURCES

    @property
    def name(self) -> str:
        if self.is_field:
            return FIELD_NAMES[self.type]
        return BUILDING_NAMES.get(self.type, f"Budynek {self.type}")

    @property
    def total_cost(self) -> int:
        return sum(self.costs.values())


@dataclass
class VillagePlan:
    village: Village
    target: int
    steps: list[Step] = field(default_factory=list)
    blocked: str | None = None
    afford_hours: float | None = None  # until each resource of step 1 is in stock
    afford_hours_npc: float | None = None  # until the total is (NPC trade)
    queue_hours: float = 0.0  # until the building queue is free
    finished: bool = False
    queue_bound: bool = False
    remaining_queue_hours: float = 0.0  # build time of all remaining fields
    produce_hours: float = 0.0  # time to produce their total cost
    weights: dict[int, float] | None = None  # resource value used for step 1

    @property
    def next_step(self) -> Step | None:
        return self.steps[0] if self.steps else None

    @property
    def ready_hours(self) -> float | None:
        if self.afford_hours is None:
            return None
        return max(self.afford_hours, self.queue_hours)

    @property
    def queue_free(self) -> bool:
        step = self.next_step
        if step is None:
            return False
        queue = QUEUE_RESOURCES if step.is_field else QUEUE_VILLAGE
        return self.village.free_slots.get(queue, 0) > 0

    @property
    def ready_now(self) -> bool:
        return (
            self.next_step is not None
            and self.afford_hours is not None
            and self.afford_hours <= 0
            and self.queue_free
        )

    @property
    def npc_ready_now(self) -> bool:
        """An NPC trade would make step 1 affordable right now."""
        return (
            self.next_step is not None
            and self.queue_free
            and not self.ready_now
            and self.afford_hours_npc is not None
            and self.afford_hours_npc <= 0
        )


def target_level(
    village: Village, capital_target: int, village_target: int,
    town_target: int | None = None, towns: set[str] | None = None,
) -> int:
    """Field target: the capital's, a town's (the game says so, or the player
    marked it), or a plain village's. The game's own maximum caps it anyway."""
    if village.is_capital:
        return capital_target
    if town_target and (village.is_town or village.id in (towns or set())):
        return town_target
    return village_target


def production_multipliers(village: Village) -> dict[int, float]:
    """Actual production per unit of the fields' base production.

    Bonuses (bonus buildings, oases, gold, hero) scale the fields' base output;
    the game's ``production`` already contains them. Crop production is net of
    upkeep, so crop uses the average multiplier of the other resources.
    """
    multipliers = {}
    for resource in (1, 2, 3):
        base = sum(
            building.effects.get(building.level, 0)
            for building in village.fields
            if building.type == resource
        )
        actual = village.production.get(resource, 0)
        multipliers[resource] = actual / base if base > 0 and actual > 0 else 1.0
    multipliers[4] = sum(multipliers[r] for r in (1, 2, 3)) / 3
    return multipliers


def _afford_simple(costs, resources, production) -> float | None:
    hours = 0.0
    for resource, cost in costs.items():
        missing = cost - resources.get(resource, 0)
        if missing <= 0:
            continue
        rate = production.get(resource, 0)
        if rate <= 0:
            return None
        hours = max(hours, missing / rate)
    return hours


def _with_incoming(afford, costs, resources, production, incoming, capacity):
    """Run *afford* piecewise between arrivals of incoming resources.

    *incoming* is a list of (hours from now, amounts); stocks never exceed
    *capacity* (what does not fit is lost, as in the game).
    """
    stock = dict(resources)
    now = 0.0
    events = sorted((h, amounts) for h, amounts in (incoming or []) if h >= 0)
    for at, amounts in events + [(math.inf, {})]:
        hours = afford(costs, stock, production)
        if hours is not None and now + hours <= at:
            return now + hours
        if at == math.inf:
            return None
        for r in RESOURCES:
            grown = stock.get(r, 0) + max(0.0, production.get(r, 0)) * (at - now)
            if production.get(r, 0) < 0:
                grown = max(0.0, stock.get(r, 0) + production[r] * (at - now))
            stock[r] = grown + amounts.get(r, 0)
            if capacity and capacity.get(r):
                stock[r] = min(stock[r], capacity[r])
        now = at
    return None


def time_to_afford(
    costs: dict[int, float],
    resources: dict[int, float],
    production: dict[int, float],
    incoming: list[tuple[float, dict[int, float]]] | None = None,
    capacity: dict[int, float] | None = None,
) -> float | None:
    """Hours until every cost is in stock; None when it never accrues.

    Resources on their way (raid loot, merchants) count from their arrival.
    """
    if not incoming:
        return _afford_simple(costs, resources, production)
    return _with_incoming(_afford_simple, costs, resources, production, incoming, capacity)


def _afford_total(costs, resources, production) -> float | None:
    missing = sum(costs.values()) - sum(resources.get(r, 0) for r in RESOURCES)
    if missing <= 0:
        return 0.0
    rate = sum(production.get(r, 0) for r in RESOURCES)
    return missing / rate if rate > 0 else None


def time_to_afford_total(
    costs: dict[int, float],
    resources: dict[int, float],
    production: dict[int, float],
    incoming: list[tuple[float, dict[int, float]]] | None = None,
    capacity: dict[int, float] | None = None,
) -> float | None:
    """Hours until the *sum* of resources covers the sum of costs (NPC trade)."""
    if not incoming:
        return _afford_total(costs, resources, production)
    return _with_incoming(_afford_total, costs, resources, production, incoming, capacity)


# Scarcity weights are kept within these bounds.
MIN_WEIGHT = 0.25
MAX_WEIGHT = 4.0


def resource_weights(
    demand: dict[int, float], production: dict[int, float],
) -> dict[int, float]:
    """Value of one unit of each resource, by how scarce it is.

    Compares each resource's share in the remaining costs (*demand*) with
    its share in production. A resource produced in surplus (e.g. crop that
    only fills the granary) is worth less, a bottleneck more. Weighted by
    production the average stays 1, so paybacks remain in hours.
    """
    total_demand = sum(max(0.0, demand.get(r, 0)) for r in RESOURCES)
    supply = {r: max(production.get(r, 0), 1.0) for r in RESOURCES}
    total_supply = sum(supply.values())
    if total_demand <= 0 or total_supply <= 0:
        return {r: 1.0 for r in RESOURCES}
    weights = {}
    for r in RESOURCES:
        demand_share = max(0.0, demand.get(r, 0)) / total_demand
        supply_share = supply[r] / total_supply
        weights[r] = min(MAX_WEIGHT, max(MIN_WEIGHT, demand_share / supply_share))
    return weights


def _field_step(
    village: Village,
    building: Building,
    level: int,
    multipliers: dict[int, float],
    time_factor: float = 1.0,
    reason: str = "najszybszy zwrot",
    weights: dict[int, float] | None = None,
) -> Step:
    weights = weights or {r: 1.0 for r in RESOURCES}
    gain = (building.effects[level + 1] - building.effects[level]) * multipliers[building.type]
    upkeep = building.supply[level + 1] - building.supply[level]
    net = gain * weights[building.type] - upkeep * weights[4]
    costs = {r: int(building.costs[level].get(r, 0)) for r in RESOURCES}
    value = sum(costs[r] * weights[r] for r in RESOURCES)
    payback = value / net if net > 0 else None
    return Step(
        village_id=village.id,
        location=building.location,
        type=building.type,
        from_level=level,
        costs=costs,
        seconds=int(building.times.get(level, 0) * time_factor),
        gain=net,
        payback_hours=payback,
        reason=reason,
    )


def _gross_production(village: Village, resource: int, production: dict[int, float],
                      multipliers: dict[int, float]) -> float:
    """Hourly production before upkeep (crop production in the game is net)."""
    if resource != 4:
        return max(0.0, production.get(resource, 0))
    fields = sum(
        building.effects.get(building.level, 0)
        for building in village.fields
        if building.type == 4
    )
    return max(production.get(4, 0), fields * multipliers[4], 0.0)


def _bonus_step(
    village: Village,
    building: Building,
    level: int,
    production: dict[int, float],
    multipliers: dict[int, float],
    time_factor: float,
    weights: dict[int, float] | None,
) -> Step | None:
    """Upgrade of a production bonus building (sawmill, …).

    Its effect is the % bonus on the resource's base production; the
    production already contains the current bonus, so one more percentage
    point adds ``production / (100 + current %)``.
    """
    if (
        level >= building.max_level
        or level not in building.costs
        or level not in building.effects
        or level + 1 not in building.effects
    ):
        return None
    resource = BONUS_BUILDINGS[building.type]
    weights = weights or {r: 1.0 for r in RESOURCES}
    current = building.effects[level]
    delta = building.effects[level + 1] - current
    gross = _gross_production(village, resource, production, multipliers)
    raw = gross * delta / (100 + current) if delta > 0 else 0.0
    upkeep = 0
    if level in building.supply and level + 1 in building.supply:
        upkeep = building.supply[level + 1] - building.supply[level]
    net = raw * weights[resource] - upkeep * weights[4]
    costs = {r: int(building.costs[level].get(r, 0)) for r in RESOURCES}
    value = sum(costs[r] * weights[r] for r in RESOURCES)
    return Step(
        village_id=village.id,
        location=building.location,
        type=building.type,
        from_level=level,
        costs=costs,
        seconds=int(building.times.get(level, 0) * time_factor),
        gain=net,
        payback_hours=value / net if net > 0 else None,
        reason=f"+{delta:g}% {RESOURCE_NAMES[resource].lower()} (+{raw:.0f}/h)",
        raw_gain=raw,
    )


def _storage_for(village: Village, resource: int) -> list[Building]:
    types = GRANARY_TYPES if resource == 4 else WAREHOUSE_TYPES
    return [building for building in village.storages if building.type in types]


def _effect_delta(building: Building, from_level: int, to_level: int) -> float:
    """Change of a building's effect (e.g. capacity) between two levels."""
    if from_level in building.effects and to_level in building.effects:
        return float(building.effects[to_level] - building.effects[from_level])
    return 0.0


def remaining_field_work(
    village: Village, target: int, levels: dict[int, int] | None = None,
) -> tuple[float, float]:
    """(build hours, total cost) of all field levels still missing to *target*.

    Levels beyond the game's look-ahead tables are left out, so far-away
    targets are slightly underestimated.
    """
    hours, costs = _remaining(village, target, levels)
    return hours, sum(costs.values())


def _remaining(
    village: Village, target: int, levels: dict[int, int] | None = None,
) -> tuple[float, dict[int, float]]:
    hours = 0.0
    costs = {r: 0.0 for r in RESOURCES}
    for building in village.fields:
        start = levels[building.location] if levels else building.planned_level
        for level in range(start, min(target, building.max_level)):
            if level not in building.costs:
                break
            hours += building.times.get(level, 0) / 3600
            for r in RESOURCES:
                costs[r] += building.costs[level].get(r, 0)
    return hours, costs


def _produce_hours(cost: float, production: dict[int, float]) -> float:
    rate = sum(production.get(r, 0) for r in RESOURCES)
    return cost / rate if rate > 0 else math.inf


def plan_village(
    village: Village,
    target: int,
    server_time: float,
    depth: int = PLAN_DEPTH,
    main_building: bool = True,
    balanced: bool = True,
    storage_hours: float = 0.0,
    bonus_buildings: bool = True,
    construct_config: dict[int, dict] | None = None,
    military: dict[int, int] | None = None,
    military_first: bool = False,
    granary_max: bool = False,
    settle_level: int = 0,
    settle_count: int = 0,
) -> VillagePlan:
    """Order the next field, storage, bonus and main building upgrades.

    With *settle_level* the village prepares *settle_count* settlers first:
    main building up to the residence's requirement, a residence up to that
    level, then nothing else until the settlers are in training (the plan
    holds a training step whose cost the other villages ship in).

    With *granary_max* (the crop bank village) every granary goes up to its
    maximum level before any other step.

    *military* maps barracks/stable types to target levels. They do not
    produce anything, so they cannot compete by payback: with
    *military_first* (the village has resources to spare) they alternate
    with the other steps, otherwise they come once the fields are done.

    With *balanced* the resources are valued by scarcity (see
    :func:`resource_weights`), otherwise 1:1 (e.g. when the NPC trader
    converts any surplus automatically). With *storage_hours* the warehouse
    and granary are kept large enough for that many hours of production.
    Existing production bonus buildings (sawmill …) compete with the fields
    by payback time.
    """
    plan = VillagePlan(village=village, target=target)
    multipliers = production_multipliers(village)
    levels = {b.location: b.planned_level for b in village.buildings()}
    production = village.build_production()
    queued_locations = {b.location for b in village.buildings() if b.queued}
    # The game reports the real capacity; add what queued storage levels bring.
    capacity = {
        1: float(village.capacity.get(1, 0)),
        4: float(village.capacity.get(4, 0)),
    }
    for resource in (1, 4):
        for building in _storage_for(village, resource):
            if building.queued:
                capacity[resource] += _effect_delta(building, building.level, building.planned_level)

    remaining_hours, remaining_cost = remaining_field_work(village, target, levels)
    plan.remaining_queue_hours = remaining_hours
    plan.produce_hours = _produce_hours(remaining_cost, production)
    plan.queue_bound = remaining_hours > 0 and remaining_hours > plan.produce_hours
    time_factor = 1.0
    taken_slots: set[int] = set()  # empty sites already used by planned new buildings
    planned_types: set[int] = set()  # building types planned as new buildings
    # Field build time still ahead in this plan, at the current speed.
    queue_left = remaining_hours

    def candidates() -> list[Step]:
        weights = None
        if balanced:
            _hours, demand = _remaining(village, target, levels)
            weights = resource_weights(demand, production)
            plan.weights = weights
        steps = []
        for building in village.fields:
            level = levels[building.location]
            limit = min(target, building.max_level)
            if level >= limit or not building.can_plan(level):
                continue
            # A field already in the queue cannot be clicked again right now.
            if building.location in queued_locations and not plan.steps:
                continue
            steps.append(_field_step(
                village, building, level, multipliers, time_factor, weights=weights,
            ))
        if bonus_buildings and construct_config:
            steps.extend(construction_steps(
                village, construct_config, production, multipliers, weights, levels, taken_slots,
                planned_types,
            ))
        if bonus_buildings:
            for building in village.bonus:
                step = _bonus_step(
                    village, building, levels[building.location], production,
                    multipliers, time_factor, weights,
                )
                if step is None:
                    continue
                if building.location in queued_locations and not plan.steps:
                    continue
                steps.append(step)
        return steps

    def storage_reserve_step() -> Step | None:
        """Storage for *storage_hours* of production (e.g. over the night)."""
        if storage_hours <= 0:
            return None
        steady = {r: production.get(r, 0) - village.hero_excess.get(r, 0) for r in RESOURCES}
        checks = (
            (1, max(steady[r] for r in (1, 2, 3))),
            (4, max(0.0, steady[4])),
        )
        for resource, rate in checks:
            wanted = rate * storage_hours
            if wanted <= capacity[resource]:
                continue
            step = storage_step(resource, int(wanted))
            if step is None:
                continue
            step.reason = (
                f"{'spichlerz' if resource == 4 else 'magazyn'} mieści "
                f"{capacity[resource] / rate:.1f} h produkcji (cel {storage_hours:g} h)"
            )
            return step
        return None

    def main_building_step() -> Step | None:
        building = village.main_building
        if not main_building or building is None or not plan.queue_bound:
            return None
        level = levels[building.location]
        if (
            level >= building.max_level
            or level not in building.costs
            or level not in building.effects
            or level + 1 not in building.effects
            or building.effects[level] <= 0
            or (building.location in queued_locations and not plan.steps)
        ):
            return None
        ratio = building.effects[level + 1] / building.effects[level]
        saved = queue_left * (1 - ratio)
        own = building.times.get(level, 0) * time_factor / 3600
        if saved < MAIN_BUILDING_MIN_GAIN * own or saved <= 0:
            return None
        return Step(
            village_id=village.id,
            location=building.location,
            type=MAIN_BUILDING,
            from_level=level,
            costs={r: int(building.costs[level].get(r, 0)) for r in RESOURCES},
            seconds=int(building.times.get(level, 0) * time_factor),
            gain=0.0,
            payback_hours=None,
            reason=(
                f"kolejka to wąskie gardło — skraca budowę pól o {saved:.1f} h "
                f"(sam trwa {own:.1f} h)"
            ),
        )

    def storage_step(resource: int, needed: int) -> Step | None:
        best = None
        for building in _storage_for(village, resource):
            level = levels[building.location]
            if level >= building.max_level or level not in building.costs:
                continue
            if building.location in queued_locations and not plan.steps:
                continue
            costs = {r: int(building.costs[level].get(r, 0)) for r in RESOURCES}
            step = Step(
                village_id=village.id,
                location=building.location,
                type=building.type,
                from_level=level,
                costs=costs,
                seconds=int(building.times.get(level, 0) * time_factor),
                gain=0.0,
                payback_hours=None,
                reason=(
                    f"{'spichlerz' if resource == 4 else 'magazyn'} za mały "
                    f"(potrzeba {needed:,})".replace(",", " ")
                ),
            )
            if best is None or step.total_cost < best.total_cost:
                best = step
        if best is None and construct_config:
            # No warehouse/granary at all (a new village): put one up.
            best = storage_construction(
                village, resource, construct_config, levels, taken_slots, planned_types, needed,
            )
        return best

    last_military = False

    def settle_step() -> Step | None:
        """Settlers: main building to the residence's minimum, a residence,
        then its levels up to *settle_level*."""
        if not settle_level:
            return None
        residence = village.residence
        if residence is None:
            if RESIDENCE in planned_types:
                return None
            main = village.main_building
            if main is not None and levels[main.location] < RESIDENCE_MAIN_LEVEL:
                level = levels[main.location]
                if level not in main.costs:
                    return None
                return Step(
                    village_id=village.id, location=main.location, type=MAIN_BUILDING,
                    from_level=level,
                    costs={r: int(main.costs[level].get(r, 0)) for r in RESOURCES},
                    seconds=int(main.times.get(level, 0) * time_factor),
                    gain=0.0, payback_hours=None,
                    reason=f"osadnicy: budynek główny do {RESIDENCE_MAIN_LEVEL} (wymóg rezydencji)",
                )
            info = (construct_config or {}).get(RESIDENCE)
            sites = free_sites(village, taken_slots)
            if info is None or not sites:
                return None
            return Step(
                village_id=village.id, location=sites[0], type=RESIDENCE, from_level=0,
                costs=_config_costs(info),
                seconds=int(float(info.get("time") or 0) or NEW_BUILDING_HOURS * 3600),
                gain=0.0, payback_hours=None,
                reason=f"osadnicy: nowa rezydencja (cel poziom {settle_level})",
                construct=True, upkeep=float(info.get("supply") or 0),
            )
        level = levels[residence.location]
        if level >= settle_level or level not in residence.costs:
            return None
        return Step(
            village_id=village.id, location=residence.location, type=residence.type,
            from_level=level,
            costs={r: int(residence.costs[level].get(r, 0)) for r in RESOURCES},
            seconds=int(residence.times.get(level, 0) * time_factor),
            gain=0.0, payback_hours=None,
            reason=f"osadnicy: rezydencja do poziomu {settle_level}",
        )

    def bank_step() -> Step | None:
        """Crop bank: the existing granaries up to their maximum level, and
        the warehouse along with them, so milled crop fits in."""
        if not granary_max:
            return None
        # A third of the granaries' crop becomes wood, clay and iron each,
        # on top of the stock and a couple of hours of production.
        wanted = capacity[4] / 3 + WAREHOUSE_BANK_HOURS * max(production.get(r, 0) for r in (1, 2, 3))
        if wanted > capacity[1]:
            step = storage_step(1, int(wanted))
            if step is not None and not step.construct:
                step.reason = (
                    f"bank zboża: magazyn na zmielone zboże ({capacity[1]:,.0f}, potrzeba {wanted:,.0f})"
                ).replace(",", " ")
                return step
        granaries = _storage_for(village, 4)
        if not any(levels[b.location] < b.max_level for b in granaries):
            return None
        step = storage_step(4, 0)
        if step is None or step.construct:
            return None
        top = sum(b.effects.get(b.max_level, 0) for b in granaries)
        step.reason = (
            f"bank zboża: spichlerze do maksimum ({capacity[4]:,.0f} z {top:,.0f})"
        ).replace(",", " ")
        return step

    def military_step() -> Step | None:
        # The lowest of them first, so barracks and stable grow together.
        for building in sorted(village.military, key=lambda b: levels[b.location]):
            target = (military or {}).get(building.type)
            if not target:
                continue
            level = levels[building.location]
            if level >= min(target, building.max_level) or level not in building.costs:
                continue
            if building.location in queued_locations and not plan.steps:
                continue
            return Step(
                village_id=village.id,
                location=building.location,
                type=building.type,
                from_level=level,
                costs={r: int(building.costs[level].get(r, 0)) for r in RESOURCES},
                seconds=int(building.times.get(level, 0) * time_factor),
                gain=0.0,
                payback_hours=None,
                reason=f"wojsko: do poziomu {target} (szybsze szkolenie)",
            )
        return None

    missing_settlers = max(0, settle_count - village.settlers) if settle_level else 0
    ready_residence = (
        village.residence is not None and village.residence.level >= settle_level
    )
    if settle_level and ready_residence and missing_settlers and village.settler_costs:
        # Residence ready: nothing else until the settlers are in training.
        costs = {r: int(village.settler_costs.get(r, 0)) * missing_settlers for r in RESOURCES}
        plan.steps.append(Step(
            village_id=village.id, location=village.residence.location, type=village.residence.type,
            from_level=village.residence.level, costs=costs,
            seconds=int(settler_seconds_at(village.settler_seconds, village.residence.level) * missing_settlers),
            gain=0.0, payback_hours=None,
            reason=f"osadnicy: {missing_settlers} do wyszkolenia w rezydencji", train=True,
        ))
        depth = 0
    for _ in range(depth):
        options = candidates()
        settle = settle_step()
        if settle is not None:
            plan.steps.append(settle)
            if settle.construct:
                taken_slots.add(settle.location)
                planned_types.add(settle.type)
                production[4] = production.get(4, 0) - _upkeep(village, settle)
                continue
            building = _building(village, settle.location)
            if settle.type == MAIN_BUILDING:
                lvl = settle.from_level
                if lvl in building.effects and lvl + 1 in building.effects and building.effects[lvl]:
                    ratio = building.effects[lvl + 1] / building.effects[lvl]
                    time_factor *= ratio
                    queue_left *= ratio
            levels[settle.location] += 1
            production[4] = production.get(4, 0) - _upkeep(village, settle)
            continue
        # Crop bank first: granaries (and the warehouse for the milled crop)
        # before anything else until they are at their maximum.
        bank = bank_step()
        if bank is not None:
            plan.steps.append(bank)
            building = _building(village, bank.location)
            levels[bank.location] += 1
            stored = 4 if bank.type in GRANARY_TYPES else 1
            capacity[stored] += _effect_delta(building, bank.from_level, bank.from_level + 1)
            production[4] = production.get(4, 0) - _upkeep(village, bank)
            continue
        military_choice = military_step() if military else None
        if military_choice is not None and (not options or (military_first and not last_military)):
            plan.steps.append(military_choice)
            levels[military_choice.location] += 1
            production[4] = production.get(4, 0) - _upkeep(village, military_choice)
            last_military = True
            continue
        last_military = False
        if not options:
            if bank_step() is not None:
                continue  # fields done: the granaries still go up
            break
        reserve = storage_reserve_step()
        if reserve is not None:
            resource = 4 if reserve.type in GRANARY_TYPES else 1
            plan.steps.append(reserve)
            if reserve.construct:
                taken_slots.add(reserve.location)
                planned_types.add(reserve.type)
                production[4] = production.get(4, 0) - _upkeep(village, reserve)
                continue
            building = _building(village, reserve.location)
            levels[reserve.location] += 1
            capacity[resource] += _effect_delta(building, reserve.from_level, reserve.from_level + 1)
            production[4] = production.get(4, 0) - _upkeep(village, reserve)
            continue
        main = main_building_step()
        if main is not None:
            plan.steps.append(main)
            building = village.main_building
            ratio = building.effects[main.from_level + 1] / building.effects[main.from_level]
            levels[main.location] += 1
            time_factor *= ratio
            queue_left *= ratio
            production[4] = production.get(4, 0) - _upkeep(village, main)
            continue
        if plan.queue_bound:
            # Resources are in surplus, build time is scarce: the most
            # production per hour of queue first.
            def per_queue_hour(s: Step) -> float:
                return s.gain / max(s.seconds / 3600, 1 / 60) if s.gain > 0 else 0.0

            ranked = sorted(options, key=lambda s: (-per_queue_hour(s), s.total_cost))
            best = ranked[0]
            note = f"najwięcej produkcji na godzinę kolejki (+{per_queue_hour(best):.0f})"
            best.reason = note if best.is_field else f"{best.reason}; {note}"
        else:
            ranked = sorted(
                options,
                key=lambda s: (s.payback_hours is None, s.payback_hours or math.inf, s.total_cost),
            )
        choice = ranked[0]
        # Keep net crop above zero: the extra population eats crop.
        crop_after = production.get(4, 0) - _upkeep(village, choice)
        if choice.type == 4:
            crop_after += _raw_gain(village, choice, multipliers)
        if crop_after < 0 and choice.type != 4:
            crops = [s for s in ranked if s.type == 4]
            if crops:
                choice = crops[0]
                choice.reason = "bilans zboża spadłby poniżej zera"
        upkeep = _upkeep(village, choice)
        # Storage first when the step does not fit.
        storage_needed = None
        for resource, group in ((1, (1, 2, 3)), (4, (4,))):
            need = max(choice.costs[r] for r in group)
            if need > capacity[resource]:
                storage_needed = (resource, need)
                break
        if storage_needed is not None:
            resource, need = storage_needed
            step = storage_step(resource, need)
            if step is None:
                if not plan.steps:
                    plan.blocked = (
                        f"{choice.name} {choice.from_level}→{choice.from_level + 1} "
                        f"wymaga {'spichlerza' if resource == 4 else 'magazynu'} "
                        f"na {need:,} — brak budynku do rozbudowy".replace(",", " ")
                    )
                break
            plan.steps.append(step)
            if step.construct:
                # Its capacity counts once it stands (next read).
                taken_slots.add(step.location)
                planned_types.add(step.type)
                production[4] = production.get(4, 0) - _upkeep(village, step)
                continue
            building = _building(village, step.location)
            levels[step.location] += 1
            capacity[resource] += _effect_delta(building, step.from_level, step.from_level + 1)
            production[4] = production.get(4, 0) - _upkeep(village, step)
            continue
        plan.steps.append(choice)
        if choice.construct:
            taken_slots.add(choice.location)
            planned_types.add(choice.type)
            resource = BONUS_BUILDINGS.get(choice.type)
            if resource:
                production[resource] = production.get(resource, 0) + choice.raw_gain
            production[4] = production.get(4, 0) - upkeep
            continue
        levels[choice.location] += 1
        if choice.is_field:
            queue_left = max(0.0, queue_left - choice.seconds / 3600)
            raw = _raw_gain(village, choice, multipliers)
            production[choice.type] = production.get(choice.type, 0) + raw
        elif choice.type in BONUS_BUILDINGS:
            resource = BONUS_BUILDINGS[choice.type]
            production[resource] = production.get(resource, 0) + choice.raw_gain
        production[4] = production.get(4, 0) - upkeep

    if not plan.steps and plan.blocked is None:
        plan.finished = all(
            levels[b.location] >= min(target, b.max_level) for b in village.fields
        )
        if not plan.finished:
            plan.blocked = "pola w kolejce budowy lub brak danych kolejnych poziomów"

    step = plan.next_step
    if step is not None:
        incoming = [
            ((at - server_time) / 3600, amounts) for at, amounts in village.incoming
        ]
        stock = village.build_resources()
        rates = village.build_production()
        plan.afford_hours = time_to_afford(step.costs, stock, rates, incoming, village.capacity)
        plan.afford_hours_npc = time_to_afford_total(
            step.costs, stock, rates, incoming, village.capacity,
        )
    plan.queue_hours = max(0.0, (village.busy_until - server_time) / 3600) if village.busy_until else 0.0
    return plan


def _building(village: Village, location: int) -> Building:
    return next(b for b in village.buildings() if b.location == location)


def _upkeep(village: Village, step: Step) -> int:
    """Extra crop per hour eaten by the population the step adds."""
    if step.construct:
        return int(step.upkeep)
    building = _building(village, step.location)
    level = step.from_level
    if level in building.supply and level + 1 in building.supply:
        return int(building.supply[level + 1] - building.supply[level])
    return 0


def _raw_gain(village: Village, step: Step, multipliers: dict[int, float]) -> float:
    if step.construct:
        return step.raw_gain
    building = _building(village, step.location)
    level = step.from_level
    if step.type not in RESOURCES:
        return 0.0
    return (building.effects[level + 1] - building.effects[level]) * multipliers[step.type]


def plan_all(
    villages: list[Village],
    capital_target: int,
    village_target: int,
    server_time: float,
    main_building: bool = True,
    balanced: bool = True,
    storage_hours: float = 0.0,
    bonus_buildings: bool = True,
    construct_config: dict[int, dict] | None = None,
    military: dict[str, dict[int, int]] | None = None,
    military_first: set[str] | None = None,
    granary_max: set[str] | None = None,
    settle: dict[str, tuple[int, int]] | None = None,
    town_target: int | None = None,
    towns: set[str] | None = None,
) -> list[VillagePlan]:
    return [
        plan_village(
            v,
            target_level(v, capital_target, village_target, town_target, towns),
            server_time,
            main_building=main_building,
            balanced=balanced,
            storage_hours=storage_hours,
            bonus_buildings=bonus_buildings,
            construct_config=construct_config,
            military=(military or {}).get(v.id),
            military_first=v.id in (military_first or set()),
            granary_max=v.id in (granary_max or set()),
            settle_level=(settle or {}).get(v.id, (0, 0))[0],
            settle_count=(settle or {}).get(v.id, (0, 0))[1],
        )
        for v in villages
    ]


# Gold: instant finish ------------------------------------------------------------


@dataclass
class FinishTiers:
    """Instant-finish prices and the windows in which paying is allowed.

    Prices follow the game (``finishNow`` config): free below
    *free_seconds*, *reduced_price* below *reduced_seconds*, else
    *full_price*. Paying is only allowed inside the player's windows:

    * reduced price while ``reduced_min_seconds <= remaining < reduced_seconds``
      (shorter builds wait for the free finish);
    * full price while ``remaining >= full_min_seconds`` (between
      *reduced_seconds* and *full_min_seconds* the build waits until it
      gets cheaper).
    """

    free_seconds: int = 300
    reduced_seconds: int = 7200
    reduced_price: int = 1
    full_price: int = 2
    allow_free: bool = True
    allow_reduced: bool = False
    allow_full: bool = False
    reduced_min_seconds: int = 3600
    full_min_seconds: int = 10800

    def price(self, remaining_seconds: float) -> int:
        if remaining_seconds < self.free_seconds:
            return 0
        if remaining_seconds < self.reduced_seconds:
            return self.reduced_price
        return self.full_price

    def allowed(self, remaining_seconds: float) -> bool:
        if remaining_seconds < self.free_seconds:
            return self.allow_free
        if remaining_seconds < self.reduced_seconds:
            return self.allow_reduced and remaining_seconds >= self.reduced_min_seconds
        return self.allow_full and remaining_seconds >= max(self.full_min_seconds, self.reduced_seconds)

    def wait_seconds(self, remaining_seconds: float) -> float | None:
        """Seconds until the build enters an allowed window (0 = now, None = never)."""
        if self.allowed(remaining_seconds):
            return 0.0
        waits = []
        reduced_low = max(self.reduced_min_seconds, self.free_seconds)
        if (
            self.allow_reduced
            and reduced_low < self.reduced_seconds
            and remaining_seconds >= self.reduced_seconds
        ):
            waits.append(remaining_seconds - self.reduced_seconds + 1)
        if self.allow_free and remaining_seconds >= self.free_seconds:
            waits.append(remaining_seconds - self.free_seconds + 1)
        return min(waits) if waits else None


@dataclass
class FinishOffer:
    village_id: str
    queue_type: int
    location: int
    building_type: int
    remaining_seconds: float
    price: int
    allowed: bool
    useful: bool  # finishing lets the next step start now
    reason: str
    wait_seconds: float | None = 0.0  # until an allowed window (None = never)
    next_price: int | None = None  # price once the window is reached


def finish_offers(plan: VillagePlan, tiers: FinishTiers, server_time: float) -> list[FinishOffer]:
    """Running builds of a village that could be finished instantly.

    A free finish is always useful. A paid one only when the next planned
    step is already affordable (also with an NPC trade), i.e. the queue is
    what holds the village back.
    """
    offers = []
    village = plan.village
    waiting = plan.next_step is not None and (
        (plan.afford_hours is not None and plan.afford_hours <= 0)
        or (plan.afford_hours_npc is not None and plan.afford_hours_npc <= 0)
    )
    # "Finish now" finishes the running build of a queue type: one offer each.
    running: dict[int, QueueEntry] = {}
    for entry in village.queue:
        if entry.queue_type not in (QUEUE_VILLAGE, QUEUE_RESOURCES) or entry.finished <= server_time:
            continue
        current = running.get(entry.queue_type)
        if current is None or entry.finished < current.finished:
            running[entry.queue_type] = entry
    for entry in running.values():
        remaining = entry.finished - server_time
        if remaining <= 0:
            continue
        price = tiers.price(remaining)
        wait = tiers.wait_seconds(remaining)
        next_price = None if wait is None else tiers.price(remaining - wait)
        # Paying only helps when the next step's resources are waiting.
        useful = price == 0 or waiting
        if wait and next_price:
            useful = waiting
        elif wait:
            useful = True  # the free finish is worth waiting for
        if wait:
            reason = (
                f"czeka {math.ceil(wait / 60)} min na "
                + ("darmowe dokończenie" if next_price == 0 else f"cenę {next_price} złota")
            )
        elif price == 0:
            reason = "za darmo (poniżej progu)"
        elif waiting:
            reason = "surowce na następny krok czekają, kolejka zajęta"
        else:
            reason = "następny krok i tak czeka na surowce"
        offers.append(FinishOffer(
            village_id=village.id,
            queue_type=entry.queue_type,
            location=entry.location,
            building_type=entry.building_type,
            remaining_seconds=remaining,
            price=price,
            allowed=tiers.allowed(remaining),
            useful=useful,
            reason=reason,
            wait_seconds=wait,
            next_price=next_price,
        ))
    return offers


# Transfers -------------------------------------------------------------------


@dataclass
class Transfer:
    source_id: str
    source_name: str
    target_id: str
    target_name: str
    resources: dict[int, int]
    merchants: int
    travel_hours: float
    wait_before_hours: float | None  # receiver alone (None = never)
    wait_after_hours: float
    step: Step
    reason: str = ""

    @property
    def saved_hours(self) -> float | None:
        if self.wait_before_hours is None:
            return None
        return self.wait_before_hours - self.wait_after_hours

    @property
    def total(self) -> int:
        return sum(self.resources.values())


def merchant_fields_per_hour(tribe: int, merchant_speed: dict[int, float], world_speed: float) -> float:
    return float(merchant_speed.get(tribe, 0)) * float(world_speed or 1)


def distance(a: Village, b: Village) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _spare(plan: VillagePlan, overflow_hours: float = OVERFLOW_HORIZON_HOURS) -> dict[int, float]:
    """What a village can give away without delaying its own building.

    Its next step is always kept. A queue-bound (or finished) village gives
    the rest — its queue cannot use it in time anyway. Other villages give
    what exceeds all their planned steps, or what would overflow their
    storage within *overflow_hours*.
    """
    village = plan.village
    # The next step and the next unreserved master builder entry stay, or
    # the donor's own queue would stand still.
    reserve = build_reserve(plan)
    free = {
        r: max(0.0, village.build_resources()[r] - reserve.get(r, 0)) for r in RESOURCES
    }
    if plan.queue_bound or plan.finished:
        spare = free
    else:
        planned = {r: sum(step.costs[r] for step in plan.steps) for r in RESOURCES}
        spare = {}
        for r in RESOURCES:
            stock = village.build_resources()[r]
            future = (
                village.resources.get(r, 0)
                + max(0.0, village.build_production()[r]) * overflow_hours
            )
            overflow = future - village.capacity.get(r, 0)
            beyond_plan = stock - planned[r]
            spare[r] = min(free[r], max(0.0, overflow, beyond_plan))
    # Never strip crop that the village eats (keep at least 2 h of negative crop).
    crop_rate = village.build_production()[4]
    if crop_rate < 0:
        spare[4] = max(0.0, spare[4] + 2 * crop_rate)
    return spare


def _need(plan: VillagePlan) -> dict[int, float]:
    """Resources a village can put to use: all planned steps, up to its storage.

    What is already on its way (merchants, loot) is subtracted, so the same
    need is not shipped twice, and so is what the village produces itself
    while its queue is still busy (see :func:`idle_hours`).
    """
    village = plan.village
    steps = plan.steps if not plan.queue_bound else plan.steps[:1]
    need = {r: 0.0 for r in RESOURCES}
    for step in steps:
        for r in RESOURCES:
            need[r] += step.costs[r]
    coming = village.incoming_total()
    room = _room(plan)
    idle = idle_hours(plan)
    rates = village.build_production()
    return {
        r: max(0.0, min(
            need[r] - village.build_resources()[r] - coming.get(r, 0) - max(0.0, rates[r]) * idle,
            room[r],
        ))
        for r in RESOURCES
    }


# A receiver keeps this much of its own production as free storage.
RECEIVER_HEADROOM_HOURS = 1.0
# Surplus goes only to villages that can spend within this many hours.
SURPLUS_MAX_IDLE_HOURS = 1.0
# Loans from a donor's own building reserve, to keep a neighbour's queue
# going: the donor re-produces a loan within this many hours ...
LOAN_MAX_DONOR_HOURS = 0.5
# ... and the receiver's queue stands at least this many times longer
# without it than the donor's next build is delayed.
LOAN_MIN_GAIN = 2.0


def idle_hours(plan: VillagePlan) -> float:
    """Hours until the village can start spending on something new.

    With the building queue busy and no free master builder place (or all
    of it already reserved), nothing new can start before the running
    build ends — and the master builder entries already paid for run one
    after another before anything else.
    """
    village = plan.village
    if plan.queue_free:
        return 0.0
    if village.free_slots.get(4, 0) > 0:
        return 0.0  # a new master builder entry can take resources now
    # An unreserved entry only takes resources once its whole cost is there.
    if reservable_master_entry(village) is not None:
        return 0.0
    hours = plan.queue_hours
    for entry, _costs, build_hours in master_entry_costs(village):
        if not entry.paid:
            break  # it needs resources when the queue reaches it
        hours += build_hours
    return hours


def _overflow(plan: VillagePlan, hours: float) -> dict[int, float]:
    """Resources the village will lose to a full storage within *hours*."""
    village = plan.village
    reserve = plan.next_step.costs if plan.next_step else {r: 0 for r in RESOURCES}
    lost = {}
    for r in RESOURCES:
        future = (
            village.resources.get(r, 0)
            + max(0.0, village.build_production()[r]) * hours
        )
        lost[r] = max(0.0, min(
            village.build_resources()[r] - reserve.get(r, 0),
            future - village.capacity.get(r, 0),
        ))
    return lost


def _room(plan: VillagePlan) -> dict[int, float]:
    """Storage a receiver can take without overflowing itself.

    Its own production keeps filling the storage until it can spend again
    (see :func:`idle_hours`), plus an hour of headroom.
    """
    village = plan.village
    coming = village.incoming_total()
    hours = idle_hours(plan) + RECEIVER_HEADROOM_HOURS
    rates = village.build_production()
    return {
        r: max(0.0, village.capacity.get(r, 0) - village.resources.get(r, 0) - coming.get(r, 0)
               - max(0.0, rates[r]) * hours)
        for r in RESOURCES
    }


def _stall_hours(missing: dict[int, float], rates: dict[int, float]) -> float:
    """How long the receiver needs to produce what it lacks itself."""
    hours = 0.0
    for r in RESOURCES:
        if missing.get(r, 0) <= 0:
            continue
        rate = max(0.0, rates.get(r, 0))
        if rate <= 0:
            return math.inf
        hours = max(hours, missing[r] / rate)
    return hours


def _loan(
    missing: dict[int, float],
    receiver_rates: dict[int, float],
    donor_rates: dict[int, float],
    donor_stock: dict[int, float],
    free_space: dict[int, float],
    carry_limit: float,
    travel_hours: float,
    needed_in: float,
) -> tuple[dict[int, int], float, float] | None:
    """A loan from the donor's reserve: (shipment, donor delay h, receiver
    stall saved h), or None when it is not worth it.

    The donor lends at most what it re-produces within LOAN_MAX_DONOR_HOURS;
    the receiver's stall must shrink LOAN_MIN_GAIN times more than that.
    """
    shipment = {}
    for r in RESOURCES:
        rate = max(0.0, donor_rates.get(r, 0))
        shipment[r] = int(max(0.0, min(
            missing.get(r, 0), donor_stock.get(r, 0), free_space.get(r, 0),
            rate * LOAN_MAX_DONOR_HOURS,
        )))
    total = sum(shipment.values())
    if total > carry_limit > 0:
        scale = carry_limit / total
        shipment = {r: int(a * scale) for r, a in shipment.items()}
        total = sum(shipment.values())
    if total < UNBLOCK_MIN_SHIPMENT:
        return None
    before = _stall_hours(missing, receiver_rates)
    rest = {r: missing.get(r, 0) - shipment[r] for r in RESOURCES}
    # It only helps from the moment the merchants arrive.
    after = max(_stall_hours(rest, receiver_rates), travel_hours - needed_in, 0.0)
    saved = before - after
    donor_delay = max(
        (shipment[r] / donor_rates[r] for r in RESOURCES if shipment[r] > 0 and donor_rates.get(r, 0) > 0),
        default=0.0,
    )
    if saved < UNBLOCK_IGNORE_HOURS or saved < LOAN_MIN_GAIN * donor_delay:
        return None
    return shipment, donor_delay, saved


def plan_transfers(
    plans: list[VillagePlan],
    merchant_speed: dict[int, float],
    world_speed: float,
    min_saved_minutes: float = 30,
    min_surplus_shipment: int = 1000,
    overflow_hours: float = OVERFLOW_HORIZON_HOURS,
    crop_bank: str | None = None,
) -> list[Transfer]:
    """Recommend shipments between own villages.

    Donors never give their own next step's resources. A shipment is listed
    when it either moves surplus (queue-bound or overflowing donor) to a
    village that is not queue-bound, or lets the receiver start its next step
    at least *min_saved_minutes* sooner (merchant travel included).
    """
    spares = {plan.village.id: _spare(plan) for plan in plans}
    if crop_bank in spares:
        # The crop bank keeps its crop for the NPC merchant.
        spares[crop_bank][4] = 0.0
    merchants_left = {plan.village.id: plan.village.merchants_free for plan in plans}
    # What each village physically still has after the shipments planned here.
    stock_left = {plan.village.id: dict(plan.village.build_resources()) for plan in plans}
    # village id -> what its own next master builder entry still lacks.
    blocked_missing = {}
    for plan in plans:
        deficit = blocking_deficit(plan)
        if deficit is not None:
            blocked_missing[plan.village.id] = deficit[1]
    transfers: list[Transfer] = []
    # First: villages whose queue would stand idle because the next master
    # builder entry lacks resources (any village, also queue-bound ones).
    for receiver in plans:
        if receiver.next_step is not None and receiver.next_step.train:
            continue  # waiting for settlers: their cost is shipped as the next step
        blocked = blocking_deficit(receiver)
        if blocked is None:
            continue
        entry, missing, needed_in = blocked
        village = receiver.village
        # Not worth a merchant trip when the village produces it in minutes.
        rates = village.build_production()
        if all(missing[r] <= max(0.0, rates[r]) * UNBLOCK_IGNORE_HOURS for r in RESOURCES):
            continue
        if sum(max(0.0, value) for value in missing.values()) < UNBLOCK_MIN_SHIPMENT:
            # A tiny deficit (a young village, cheap levels): no trip would
            # ever be worth it, so it is shipped for the next planned steps too.
            ahead = _need(receiver)
            missing = {r: max(missing[r], ahead[r]) for r in RESOURCES}
        free_space = {
            r: max(0.0, village.capacity.get(r, 0) - village.resources.get(r, 0)
                   - village.incoming_total().get(r, 0))
            for r in RESOURCES
        }
        donors = sorted(
            (p for p in plans if p.village.id != village.id),
            key=lambda p: distance(p.village, village),
        )
        for donor_plan in donors:
            donor = donor_plan.village
            if merchants_left[donor.id] <= 0 or donor.merchant_carry <= 0:
                continue
            speed = merchant_fields_per_hour(donor.tribe, merchant_speed, world_speed)
            if speed <= 0:
                continue
            shipment = {
                r: int(min(missing[r], spares[donor.id][r], free_space[r])) for r in RESOURCES
            }
            total = sum(shipment.values())
            if total <= 0:
                continue
            limit = merchants_left[donor.id] * donor.merchant_carry
            if total > limit:
                scale = limit / total
                shipment = {r: int(a * scale) for r, a in shipment.items()}
                total = sum(shipment.values())
            if total < UNBLOCK_MIN_SHIPMENT:
                continue
            name = FIELD_NAMES.get(entry.building_type) or BUILDING_NAMES.get(
                entry.building_type, f"budynek {entry.building_type}"
            )
            step = receiver.next_step or Step(
                village.id, entry.location, entry.building_type, 0,
                {r: 0 for r in RESOURCES}, 0, 0.0, None, "",
            )
            travel = distance(donor, village) / speed
            transfers.append(Transfer(
                source_id=donor.id,
                source_name=donor.name,
                target_id=village.id,
                target_name=village.name,
                resources={r: a for r, a in shipment.items() if a > 0},
                merchants=math.ceil(total / donor.merchant_carry),
                travel_hours=travel,
                wait_before_hours=needed_in,
                wait_after_hours=max(travel, needed_in),
                step=step,
                reason=f"kolejka stanie — brakuje na {name} u budowniczego",
            ))
            merchants_left[donor.id] -= transfers[-1].merchants
            for r, amount in shipment.items():
                spares[donor.id][r] -= amount
                stock_left[donor.id][r] -= amount
                missing[r] -= amount
                free_space[r] -= amount
            if sum(max(0.0, value) for value in missing.values()) <= 0:
                break
        # Still short: borrow from a neighbour's own reserve when it makes
        # that back in minutes, while this queue would stand for hours.
        for donor_plan in donors:
            if sum(max(0.0, value) for value in missing.values()) <= 0:
                break
            donor = donor_plan.village
            if merchants_left[donor.id] <= 0 or donor.merchant_carry <= 0:
                continue
            speed = merchant_fields_per_hour(donor.tribe, merchant_speed, world_speed)
            if speed <= 0:
                continue
            # A donor whose own queue waits lends only what that wait does not need.
            own_missing = blocked_missing.get(donor.id) or {}
            lendable = {
                r: 0.0 if own_missing.get(r, 0) > 0 else stock_left[donor.id][r] for r in RESOURCES
            }
            loan = _loan(
                missing, rates, donor.build_production(), lendable, free_space,
                merchants_left[donor.id] * donor.merchant_carry,
                distance(donor, village) / speed, needed_in,
            )
            if loan is None:
                continue
            shipment, donor_delay, saved = loan
            total = sum(shipment.values())
            name = FIELD_NAMES.get(entry.building_type) or BUILDING_NAMES.get(
                entry.building_type, f"budynek {entry.building_type}"
            )
            step = receiver.next_step or Step(
                village.id, entry.location, entry.building_type, 0,
                {r: 0 for r in RESOURCES}, 0, 0.0, None, "",
            )
            travel = distance(donor, village) / speed
            transfers.append(Transfer(
                source_id=donor.id,
                source_name=donor.name,
                target_id=village.id,
                target_name=village.name,
                resources={r: a for r, a in shipment.items() if a > 0},
                merchants=math.ceil(total / donor.merchant_carry),
                travel_hours=travel,
                wait_before_hours=needed_in,
                wait_after_hours=max(travel, needed_in),
                step=step,
                reason=(
                    f"pożyczka na {name} u budowniczego: kolejka stałaby o "
                    f"{saved * 60:.0f} min dłużej, {donor.name} odrobi to w "
                    f"{donor_delay * 60:.0f} min"
                ),
            ))
            merchants_left[donor.id] -= transfers[-1].merchants
            for r, amount in shipment.items():
                spares[donor.id][r] = max(0.0, spares[donor.id][r] - amount)
                stock_left[donor.id][r] -= amount
                missing[r] -= amount
                free_space[r] -= amount
    receivers = sorted(
        (p for p in plans if p.next_step),
        # Settlers (a dated goal) come first, then the fastest payback.
        key=lambda p: (not p.next_step.train, p.queue_bound, p.next_step.payback_hours or math.inf),
    )
    for receiver in receivers:
        village = receiver.village
        step = receiver.next_step
        need = _need(receiver)
        # Nothing new starts before the busy queue frees: a shipment can
        # only speed up what would otherwise wait longer than that.
        idle = idle_hours(receiver)
        before = None if receiver.afford_hours is None else max(receiver.afford_hours, idle)
        best = None
        for donor_plan in plans:
            donor = donor_plan.village
            if donor.id == village.id or merchants_left[donor.id] <= 0 or donor.merchant_carry <= 0:
                continue
            speed = merchant_fields_per_hour(donor.tribe, merchant_speed, world_speed)
            if speed <= 0:
                continue
            travel = distance(donor, village) / speed
            limit = merchants_left[donor.id] * donor.merchant_carry
            shipment = {r: int(min(need[r], spares[donor.id][r])) for r in RESOURCES}
            total = sum(shipment.values())
            if total <= 0:
                continue
            if total > limit:
                scale = limit / total
                shipment = {r: int(amount * scale) for r, amount in shipment.items()}
                total = sum(shipment.values())
            remaining = {r: step.costs[r] - shipment[r] for r in RESOURCES}
            own = time_to_afford(remaining, village.build_resources(), village.build_production())
            after = max(idle, travel if own is None else max(travel, own))
            if own is None and any(remaining[r] > village.build_resources()[r] for r in RESOURCES):
                after = math.inf
            saved = math.inf if before is None else before - after
            speeds_up = after < math.inf and saved * 60 >= min_saved_minutes
            # Spare resources are never needed by the donor, so moving them to
            # a village held back by resources is always a gain.
            # ... but only to a village that can start spending them soon:
            # otherwise they would lie idle there while the busy queue runs
            # (overflow at the donor is handled by the overflow pass).
            moves_surplus = (
                not receiver.queue_bound
                and idle <= SURPLUS_MAX_IDLE_HOURS
                and total >= min_surplus_shipment
            )
            if not (speeds_up or moves_surplus):
                continue
            if speeds_up and moves_surplus:
                reason = "nadwyżka nadawcy, przyspiesza następny krok"
            elif speeds_up:
                reason = "przyspiesza następny krok"
            else:
                reason = "nadwyżka nadawcy — odbiorcę ogranicza ilość surowców"
            candidate = Transfer(
                source_id=donor.id,
                source_name=donor.name,
                target_id=village.id,
                target_name=village.name,
                resources={r: a for r, a in shipment.items() if a > 0},
                merchants=math.ceil(total / donor.merchant_carry),
                travel_hours=travel,
                wait_before_hours=before,
                wait_after_hours=min(after, before) if before is not None else after,
                step=step,
                reason=reason,
            )
            key = (candidate.wait_after_hours, -candidate.total)
            if best is None or key < (best.wait_after_hours, -best.total):
                best = candidate
        if best is not None:
            transfers.append(best)
            merchants_left[best.source_id] -= best.merchants
            for r, amount in best.resources.items():
                spares[best.source_id][r] -= amount

    by_id = {plan.village.id: plan for plan in plans}
    # Crop bank: the other villages' spare crop fills its granaries.
    bank_plan = by_id.get(crop_bank) if crop_bank else None
    if bank_plan is not None:
        bank = bank_plan.village
        bank_rate = max(0.0, bank.build_production()[4])
        bank_room = (
            bank.capacity.get(4, 0) - bank.resources.get(4, 0) - bank.incoming_total().get(4, 0)
            - sum(t.resources.get(4, 0) for t in transfers if t.target_id == bank.id)
        )
        donors = sorted(
            (p for p in plans if p.village.id != bank.id),
            key=lambda p: distance(p.village, bank),
        )
        for donor_plan in donors:
            donor = donor_plan.village
            if merchants_left[donor.id] <= 0 or donor.merchant_carry <= 0:
                continue
            speed = merchant_fields_per_hour(donor.tribe, merchant_speed, world_speed)
            if speed <= 0:
                continue
            travel = distance(donor, bank) / speed
            # The bank keeps filling itself while the merchants travel.
            # Filled to the brim: when it is full, the crop is milled.
            room = bank_room - bank_rate * travel
            amount = int(min(
                max(0.0, spares[donor.id][4]), room,
                merchants_left[donor.id] * donor.merchant_carry,
            ))
            if amount < min_surplus_shipment:
                continue
            step = bank_plan.next_step or Step(
                bank.id, 0, 11, 0, {r: 0 for r in RESOURCES}, 0, 0.0, None, "",
            )
            transfers.append(Transfer(
                source_id=donor.id,
                source_name=donor.name,
                target_id=bank.id,
                target_name=bank.name,
                resources={4: amount},
                merchants=math.ceil(amount / donor.merchant_carry),
                travel_hours=travel,
                wait_before_hours=None,
                wait_after_hours=0.0,
                step=step,
                reason=f"nadwyżka zboża do banku w {bank.name} (zmieli je handlarz NPC)",
            ))
            merchants_left[donor.id] -= transfers[-1].merchants
            spares[donor.id][4] -= amount
            bank_room -= amount

    # Resources that would be lost to a full storage go to any village that
    # has room, even a queue-bound one: anything beats losing them.
    sent_to = {(t.source_id, t.target_id) for t in transfers}
    for donor_plan in plans:
        donor = donor_plan.village
        lost = _overflow(donor_plan, overflow_hours)
        if donor.id == crop_bank:
            lost[4] = 0.0  # milled by the NPC merchant instead
        already = {r: 0.0 for r in RESOURCES}
        for t in transfers:
            if t.source_id == donor.id:
                for r, amount in t.resources.items():
                    already[r] += amount
        lost = {r: max(0.0, lost[r] - already[r]) for r in RESOURCES}
        if sum(lost.values()) < min_surplus_shipment or merchants_left[donor.id] <= 0 or donor.merchant_carry <= 0:
            continue
        speed = merchant_fields_per_hour(donor.tribe, merchant_speed, world_speed)
        if speed <= 0:
            continue
        # Saving resources from overflow is about merchant throughput: the
        # nearest village with room first (merchants are back soonest).
        receivers = sorted(
            (p for p in plans if p.village.id != donor.id and (donor.id, p.village.id) not in sent_to),
            key=lambda p: (distance(donor, p.village), p.queue_bound),
        )
        for receiver in receivers:
            room = _room(receiver)
            shipment = {r: int(min(lost[r], room[r])) for r in RESOURCES}
            total = sum(shipment.values())
            limit = merchants_left[donor.id] * donor.merchant_carry
            if total > limit:
                scale = limit / total
                shipment = {r: int(a * scale) for r, a in shipment.items()}
                total = sum(shipment.values())
            if total < min_surplus_shipment:
                continue
            step = receiver.next_step or donor_plan.next_step
            if step is None:
                continue
            transfer = Transfer(
                source_id=donor.id,
                source_name=donor.name,
                target_id=receiver.village.id,
                target_name=receiver.village.name,
                resources={r: a for r, a in shipment.items() if a > 0},
                merchants=math.ceil(total / donor.merchant_carry),
                travel_hours=distance(donor, receiver.village) / speed,
                wait_before_hours=receiver.afford_hours,
                wait_after_hours=receiver.afford_hours if receiver.afford_hours is not None else 0.0,
                step=step,
                reason=f"magazyn nadawcy przepełni się w ciągu {overflow_hours:g} h",
            )
            transfers.append(transfer)
            merchants_left[donor.id] -= transfer.merchants
            for r, amount in transfer.resources.items():
                lost[r] -= amount
            if sum(lost.values()) < min_surplus_shipment or merchants_left[donor.id] <= 0:
                break
    return transfers


# Parsing of the game read ------------------------------------------------------


def _int_keys(mapping) -> dict:
    # The game sends a level table as a list when its keys run 0, 1, 2 ...
    # (a building just placed, at level 0): the index is the level.
    if isinstance(mapping, list):
        mapping = dict(enumerate(mapping))
    if not isinstance(mapping, dict):
        return {}
    result = {}
    for key, value in mapping.items():
        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            continue
    return result


def _building_from_raw(raw: dict) -> Building:
    level = int(raw.get("lvl") or 0)
    effects = {}
    effect = raw.get("effect")
    if isinstance(effect, list):
        for index, value in enumerate(effect):
            try:
                effects[level + index] = float(value)
            except (TypeError, ValueError):
                continue
    costs = {
        lvl: {r: int(float(v)) for r, v in _int_keys(row).items()}
        for lvl, row in _int_keys(raw.get("nextUpgradeCosts")).items()
        if isinstance(row, dict)
    }
    supply = {lvl: int(float(v)) for lvl, v in _int_keys(raw.get("upgradeSupplyUsageSums")).items()}
    return Building(
        location=int(raw.get("locationId") or 0),
        type=int(raw.get("buildingType") or 0),
        level=level,
        max_level=int(raw.get("lvlMax") or 0),
        costs=costs,
        times={lvl: int(float(v)) for lvl, v in _int_keys(raw.get("nextUpgradeTimes")).items()},
        effects=effects,
        supply=supply,
        queued=int(raw.get("queued") or 0),
    )


def village_from_raw(raw: dict) -> Village:
    buildings = [_building_from_raw(item) for item in raw.get("buildings") or []]
    queues = raw.get("queues") or {}
    entries = []
    for queue_type, items in queues.items():
        try:
            queue_type = int(queue_type)
        except (TypeError, ValueError):
            continue
        for entry in items or []:
            entries.append(QueueEntry(
                queue_type=queue_type,
                location=int(entry.get("locationId") or 0),
                building_type=int(entry.get("buildingType") or 0),
                finished=float(entry.get("finished") or 0),
                id=str(entry.get("id") or ""),
                paid=bool(entry.get("paid")),
            ))
    busy = [
        e.finished for e in entries
        if e.queue_type in (QUEUE_VILLAGE, QUEUE_RESOURCES) and e.finished > 0
    ]
    main = [b for b in buildings if b.type == MAIN_BUILDING]
    markets = [b for b in buildings if b.type == MARKETPLACE]
    incoming = []
    for item in raw.get("incoming") or []:
        try:
            amounts = {r: float(v) for r, v in _int_keys(item.get("resources")).items() if float(v) > 0}
            at = float(item.get("arrival") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if amounts and at > 0:
            incoming.append((at, amounts))
    return Village(
        id=str(raw["id"]),
        name=str(raw.get("name") or raw["id"]),
        is_capital=bool(raw.get("is_capital")),
        x=int(raw.get("x") or 0),
        y=int(raw.get("y") or 0),
        tribe=int(raw.get("tribe") or 0),
        resources={r: float(v) for r, v in _int_keys(raw.get("resources")).items()},
        capacity={r: float(v) for r, v in _int_keys(raw.get("capacity")).items()},
        production={r: float(v) for r, v in _int_keys(raw.get("production")).items()},
        fields=[b for b in buildings if b.type in RESOURCES],
        storages=[b for b in buildings if b.type in WAREHOUSE_TYPES + GRANARY_TYPES],
        free_slots={int(k): int(v) for k, v in (raw.get("free_slots") or {}).items()},
        busy_until=min(busy) if busy else 0.0,
        # Unknown merchants (model not loaded): one per marketplace level.
        merchants_free=(
            int(raw["merchants_free"]) if raw.get("merchants_free") is not None
            else (markets[0].level if markets else 0)
        ),
        merchant_carry=int(raw.get("merchant_carry") or 0) if markets else 0,
        main_building=main[0] if main else None,
        queue=entries,
        incoming=sorted(incoming, key=lambda item: item[0]),
        bonus=[b for b in buildings if b.type in BONUS_BUILDINGS],
        marketplace=markets[0] if markets else None,
        recruit_buildings={b.type for b in buildings if b.type in (19, 20, 21, 29, 30)},
        unit_times={int(k): float(v) for k, v in (raw.get("unit_times") or {}).items() if v},
        empty_slots=[int(x) for x in raw.get("empty_slots") or []],
        military=[b for b in buildings if b.type in (19, 20, 21, 29, 30)],
        type_levels={int(k): int(v) for k, v in (raw.get("type_levels") or {}).items()},
        unit_queue_end={int(k): float(v) for k, v in (raw.get("unit_queue_end") or {}).items()},
        protected=float(raw.get("protected") or 0),
        is_town=bool(raw.get("is_town")),
        residence=next((b for b in buildings if b.type in (RESIDENCE, PALACE)), None),
        units_home={int(k): int(v) for k, v in (raw.get("units_home") or {}).items() if int(v) > 0},
        units_elsewhere=list(raw.get("units_elsewhere") or []),
        settlers=int((raw.get("settlers") or {}).get("home") or 0)
        + int((raw.get("settlers") or {}).get("queued") or 0),
        settler_unit=int((raw.get("settlers") or {}).get("unit") or 0),
        settler_costs={
            int(k): float(v) for k, v in ((raw.get("settlers") or {}).get("costs") or {}).items()
        },
        settler_seconds=float((raw.get("settlers") or {}).get("seconds") or 0),
    )


# Gold budget -------------------------------------------------------------------


@dataclass
class GoldAllocation:
    """How a gold budget is best spent on instant finishes."""

    threshold: float  # minimum queue hours saved per gold worth paying for
    gold_by_village: dict[str, int]
    hours_by_village: dict[str, float]
    finishes_by_village: dict[str, int]

    @property
    def gold(self) -> int:
        return sum(self.gold_by_village.values())

    @property
    def hours(self) -> float:
        return sum(self.hours_by_village.values())


def finish_value(remaining_seconds: float, tiers: FinishTiers) -> tuple[float, int, bool]:
    """Best paid finish for a build: (queue hours saved per gold, price, pay now).

    Either pay the full price now (saves all remaining time) or wait until it
    drops below the reduced-price threshold and pay the reduced price (saves
    that threshold). Free finishes are not counted here.
    """
    hours = remaining_seconds / 3600
    free = tiers.free_seconds / 3600
    reduced = tiers.reduced_seconds / 3600
    options = []
    if hours >= reduced and tiers.full_price > 0:
        options.append(((hours - free) / tiers.full_price, tiers.full_price, True))
    if hours > free and tiers.reduced_price > 0:
        saved = min(hours, reduced) - free
        options.append((saved / tiers.reduced_price, tiers.reduced_price, hours < reduced))
    if not options:
        return 0.0, 0, False
    return max(options, key=lambda item: item[0])


def plan_gold_budget(plans: list[VillagePlan], budget: int, tiers: FinishTiers) -> GoldAllocation:
    """Spread *budget* gold over instant finishes where they save the most.

    Only queue-bound villages gain from finishing builds (elsewhere builds
    wait for resources anyway), and only until their queue is no longer the
    bottleneck: queue time beyond production time. Every remaining field
    level is one build; the cheapest gold per saved hour goes first.
    """
    candidates = []
    for plan in plans:
        if budget <= 0 or plan.finished:
            continue
        village = plan.village
        factor = 1.0
        building = village.main_building
        if building is not None:
            for step in plan.steps:
                if step.type == MAIN_BUILDING:
                    low, high = step.from_level, step.from_level + 1
                    if low in building.effects and high in building.effects and building.effects[low]:
                        factor *= building.effects[high] / building.effects[low]
        # Production grows while the fields are built: use the average of
        # today's and the final production for the time to produce the rest.
        multipliers = production_multipliers(village)
        final = 0.0
        for field_building in village.fields:
            level = min(plan.target, field_building.max_level)
            effect = field_building.effects.get(level)
            if effect is None:
                effect = field_building.effects.get(max(field_building.effects), 0) if field_building.effects else 0
            final += effect * multipliers[field_building.type]
        now = sum(max(0.0, rate) for rate in village.build_production().values())
        average = (now + max(final, now)) / 2
        _hours, remaining_cost = remaining_field_work(village, plan.target)
        produce = remaining_cost / average if average > 0 else math.inf
        excess = max(0.0, plan.remaining_queue_hours * factor - produce)
        for field_building in village.fields:
            for level in range(field_building.planned_level, min(plan.target, field_building.max_level)):
                if level not in field_building.times:
                    break
                seconds = field_building.times[level] * factor
                value, price, _now = finish_value(seconds, tiers)
                if price > 0 and value > 0:
                    candidates.append((value, price, seconds, village.id, excess))
    candidates.sort(key=lambda item: -item[0])
    gold_left = budget
    saved = {}
    gold = {}
    count = {}
    threshold = math.inf
    for value, price, seconds, village_id, excess in candidates:
        if price > gold_left:
            continue
        if saved.get(village_id, 0.0) >= excess:
            continue
        gold_left -= price
        hours = value * price
        saved[village_id] = saved.get(village_id, 0.0) + hours
        gold[village_id] = gold.get(village_id, 0) + price
        count[village_id] = count.get(village_id, 0) + 1
        threshold = min(threshold, value)
        if gold_left <= 0:
            break
    return GoldAllocation(
        threshold=0.0 if threshold == math.inf else threshold,
        gold_by_village=gold,
        hours_by_village=saved,
        finishes_by_village=count,
    )


# Master builder re-planning ------------------------------------------------------

MASTER_REPLAN_SLACK = 2
# A field entry is swapped for another field only when it pays back this much slower.
MASTER_REPLAN_MARGIN = 1.15


def master_replacements(
    village: Village,
    target: int,
    server_time: float,
    slots: int,
    **plan_options,
) -> list[QueueEntry]:
    """Unreserved master builder entries that the current plan would not pick.

    The village is planned as if its unreserved (unpaid) master builder
    entries did not exist; an entry stays when its building is still among
    that plan's next *slots* steps (in any order, so small shifts do not
    cause churn). Entries with reserved resources are never touched.
    Returned latest first, so cancelling them keeps the others in order.
    """
    unpaid = [entry for entry in village.queue if entry.queue_type == 4 and not entry.paid]
    if not unpaid or slots <= 0:
        return []
    import copy

    free = copy.deepcopy(village)
    for entry in unpaid:
        for building in free.buildings():
            if building.location == entry.location and building.queued > 0:
                building.queued -= 1
                break
    free.queue = [entry for entry in free.queue if not (entry.queue_type == 4 and not entry.paid)]
    paid_master = sum(1 for entry in village.queue if entry.queue_type == 4 and entry.paid)
    window = max(0, slots - paid_master)
    # A little slack: an entry only a couple of steps further down the plan
    # is kept, so near-equal options do not cause cancel/re-queue churn.
    # The crop bank pushes its granaries: no slack, or an older entry
    # (e.g. the warehouse) would stay in front of them.
    keep_window = window + (0 if plan_options.get("granary_max") else MASTER_REPLAN_SLACK)
    first = plan_options.pop("first_steps", None) or []
    plan = plan_village(free, target, server_time, depth=max(keep_window + 1, 2), **plan_options)
    plan.steps = list(first) + plan.steps
    if plan.steps and plan.steps[0].train:
        return []  # waiting for settlers: leave the queue as it is
    wanted: dict[int, int] = {}
    for step in plan.steps[:keep_window]:
        wanted[step.location] = wanted.get(step.location, 0) + 1
    if len(plan.steps) < min(window, len(unpaid)):
        return []  # nothing better to put there
    queued_locations = {entry.location for entry in unpaid}
    # Something more urgent than fields (main building, storage, bonus
    # building) that is not lined up yet: make room for it.
    urgent = any(
        not step.is_field and step.location not in queued_locations
        for step in plan.steps[:window]
    )
    field_paybacks = [
        step.payback_hours for step in plan.steps[:keep_window]
        if step.is_field and step.payback_hours is not None
    ]
    worst = max(field_paybacks) if field_paybacks else None
    multipliers = production_multipliers(free)
    keep_counts = dict(wanted)
    replace = []
    settling = bool(plan_options.get("settle_level")) and village.residence is not None
    for entry in unpaid:  # queue order
        if settling and entry.location == village.residence.location:
            continue  # settlers: residence levels are never taken out
        if keep_counts.get(entry.location, 0) > 0:
            keep_counts[entry.location] -= 1
            continue
        if not urgent:
            building = next((b for b in free.fields if b.location == entry.location), None)
            if building is None:
                # A building the plan no longer wants at all (e.g. a warehouse
                # lined up while the village was queue-bound): it only holds
                # the queue up. Still further down the plan: keep it.
                if any(step.location == entry.location for step in plan.steps):
                    continue
                replace.append(entry)
                continue
            if worst is None:
                continue
            level = building.planned_level
            if not building.can_plan(level):
                continue
            own = _field_step(free, building, level, multipliers, weights=plan.weights)
            if own.payback_hours is not None and own.payback_hours <= worst * MASTER_REPLAN_MARGIN:
                continue  # nearly as good: keep, no churn
        replace.append(entry)
    return list(reversed(replace))


# Resource network: surplus, deficit and merchant capacity -------------------------


@dataclass
class NetworkBalance:
    surplus: dict[str, float]  # village id -> resources/h its queue cannot use
    deficit: dict[str, float]  # village id -> resources/h it lacks for its queue
    flow: float  # what can actually be moved and used (min of both)
    merchant_capacity: dict[str, float]  # donor -> resources/h its merchants move
    extra_merchants: dict[str, int]  # donor -> merchants missing for its share

    @property
    def unused(self) -> float:
        """Surplus nobody can use: goes to storage, recruitment or is lost."""
        return max(0.0, sum(self.surplus.values()) - self.flow)


def spend_rate(plan: VillagePlan) -> float:
    """Resources per hour the village's build queue uses on average."""
    hours, cost = remaining_field_work(plan.village, plan.target)
    return cost / hours if hours > 0 else 0.0


def network_balance(
    plans: list[VillagePlan], merchant_speed: dict[int, float], world_speed: float,
) -> NetworkBalance:
    """Who has resources to spare, who lacks them, and can merchants keep up.

    A donor's share of the useful flow is carried to the nearest village that
    lacks resources; when its merchants cannot make enough round trips in an
    hour, the missing merchants are reported (one per marketplace level).
    """
    surplus = {}
    deficit = {}
    for plan in plans:
        production = sum(max(0.0, rate) for rate in plan.village.build_production().values())
        balance = production - spend_rate(plan)
        if plan.finished:
            balance = production
        if balance > 0:
            surplus[plan.village.id] = balance
        elif balance < 0:
            deficit[plan.village.id] = -balance
    total_surplus = sum(surplus.values())
    flow = min(total_surplus, sum(deficit.values()))
    capacity = {}
    extra = {}
    by_id = {plan.village.id: plan.village for plan in plans}
    for donor_id, amount in surplus.items():
        donor = by_id[donor_id]
        speed = merchant_fields_per_hour(donor.tribe, merchant_speed, world_speed)
        receivers = [by_id[v] for v in deficit if v != donor_id]
        if not receivers or speed <= 0 or donor.merchant_carry <= 0 or donor.marketplace is None:
            continue
        round_trip = 2 * min(distance(donor, r) for r in receivers) / speed
        round_trip = max(round_trip, 1 / 60)
        merchants = donor.marketplace.level
        capacity[donor_id] = merchants * donor.merchant_carry / round_trip
        share = flow * amount / total_surplus if total_surplus else 0.0
        needed = math.ceil(share * round_trip / donor.merchant_carry)
        if needed > merchants:
            extra[donor_id] = needed - merchants
    return NetworkBalance(surplus, deficit, flow, capacity, extra)


def marketplace_step(village: Village, missing: int, levels_queued: int = 0) -> Step | None:
    """Upgrade of the marketplace: one more merchant per level."""
    market = village.marketplace
    if market is None or missing <= 0:
        return None
    level = market.planned_level
    if level >= market.max_level or level not in market.costs or market.queued:
        return None
    return Step(
        village_id=village.id,
        location=market.location,
        type=MARKETPLACE,
        from_level=level,
        costs={r: int(market.costs[level].get(r, 0)) for r in RESOURCES},
        seconds=int(market.times.get(level, 0)),
        gain=0.0,
        payback_hours=None,
        reason=f"kupcy nie nadążają z wywozem nadwyżki (brakuje {missing})",
    )


# Burning overflow in recruitment ----------------------------------------------------


def overflow_recruitment(
    plan: VillagePlan,
    unit_costs: dict[int, float],
    hours: float,
    shipped: dict[int, float] | None = None,
    master_slots: int = 4,
) -> int:
    """Units to train so no resources are lost to a full storage.

    Only when building cannot use them: the next step is not ready to start
    (queue busy) and the master builder has no free place. What the planned
    shipments take away counts first. The next step's resources stay.
    """
    village = plan.village
    if plan.ready_now:
        return 0
    in_master = sum(1 for entry in village.queue if entry.queue_type == 4)
    if village.free_slots.get(4, 0) > 0 and in_master < master_slots:
        return 0  # the master builder can still take (and reserve) a step
    shipped = shipped or {}
    costs = {r: float(unit_costs.get(r, 0) or 0) for r in RESOURCES}
    rates = village.build_production()
    needed = 0
    for r in RESOURCES:
        if costs[r] <= 0:
            continue
        future = village.resources.get(r, 0) + max(0.0, rates[r]) * hours - shipped.get(r, 0)
        excess = future - village.capacity.get(r, 0)
        if excess > 0:
            needed = max(needed, math.ceil(excess / costs[r]))
    if needed <= 0:
        return 0
    reserve = build_reserve(plan)
    afford = min(
        int(max(0.0, village.resources.get(r, 0) - shipped.get(r, 0) - reserve.get(r, 0)) // costs[r])
        for r in RESOURCES if costs[r] > 0
    )
    return max(0, min(needed, afford))


def master_next_step(
    village: Village,
    target: int,
    server_time: float,
    slots: int,
    avoid: set[int] | None = None,
    **plan_options,
) -> Step | None:
    """The step to add to the master builder, consistent with replacements.

    Plans the village without its unreserved master entries (the same view
    :func:`master_replacements` uses) and returns the first planned step that
    the existing unreserved entries do not already cover. Locations in
    *avoid* (recently cancelled) are skipped.
    """
    import copy

    unpaid = [entry for entry in village.queue if entry.queue_type == 4 and not entry.paid]
    free = copy.deepcopy(village)
    for entry in unpaid:
        for building in free.buildings():
            if building.location == entry.location and building.queued > 0:
                building.queued -= 1
                break
    free.queue = [entry for entry in free.queue if not (entry.queue_type == 4 and not entry.paid)]
    first = plan_options.pop("first_steps", None) or []
    plan = plan_village(free, target, server_time, depth=max(slots + 2, 2), **plan_options)
    plan.steps = list(first) + plan.steps
    covered: dict[int, int] = {}
    for entry in unpaid:
        covered[entry.location] = covered.get(entry.location, 0) + 1
    for step in plan.steps:
        if covered.get(step.location, 0) > 0:
            covered[step.location] -= 1
            continue
        if avoid and step.location in avoid:
            continue
        if step.train:
            return None  # waiting for settlers: nothing else goes in
        if step.construct:
            return step  # a new building on an empty site
        # The level the game will show once the covered entries are built.
        building = next((b for b in village.buildings() if b.location == step.location), None)
        if building is None:
            continue
        return step
    return None


# Gold against overflow -----------------------------------------------------------


def overflow_loss(village: Village, hours: float, extra_capacity: dict[int, float] | None = None,
                  shipped: dict[int, float] | None = None) -> float:
    """Resources lost to full storage within *hours* (production only)."""
    extra = extra_capacity or {}
    shipped = shipped or {}
    rates = village.build_production()
    lost = 0.0
    for r in RESOURCES:
        future = village.resources.get(r, 0) + max(0.0, rates[r]) * hours - shipped.get(r, 0)
        lost += max(0.0, future - village.capacity.get(r, 0) - extra.get(r, 0))
    return lost


def storage_finish_saving(
    plan: VillagePlan, server_time: float, shipped: dict[int, float] | None = None,
) -> list[tuple[QueueEntry, float]]:
    """Running warehouse/granary builds and the resources finishing them now saves.

    Without finishing, the bigger capacity only arrives when the build ends;
    whatever overflows until then is lost. Finishing now adds the capacity
    at once.
    """
    village = plan.village
    result = []
    for entry in village.queue:
        if (
            entry.queue_type not in (QUEUE_VILLAGE, QUEUE_RESOURCES)
            or entry.building_type not in WAREHOUSE_TYPES + GRANARY_TYPES
            or entry.finished <= server_time
        ):
            continue
        building = next((b for b in village.storages if b.location == entry.location), None)
        if building is None:
            continue
        gain = _effect_delta(building, building.level, building.level + 1)
        if gain <= 0:
            continue
        group = (4,) if entry.building_type in GRANARY_TYPES else (1, 2, 3)
        hours = (entry.finished - server_time) / 3600
        before = overflow_loss(village, hours, shipped=shipped)
        after = overflow_loss(village, hours, {r: gain for r in group}, shipped=shipped)
        if before - after > 0:
            result.append((entry, before - after))
    return result


def reservable_master_entry(village: Village) -> tuple[QueueEntry, dict[int, int]] | None:
    """The first unreserved master builder entry, if the stock covers it.

    The game only lets entries be reserved in queue order. Its cost is the
    level the building will have when the entry runs (current level plus
    the entries for that building ahead of it).
    """
    ahead: dict[int, int] = {}
    order = [e for e in village.queue if e.queue_type in (QUEUE_VILLAGE, QUEUE_RESOURCES)]
    order += [e for e in village.queue if e.queue_type == 4]
    for entry in order:
        if entry.queue_type == 4 and not entry.paid:
            building = next((b for b in village.buildings() if b.location == entry.location), None)
            if building is None:
                return None
            level = building.level + ahead.get(entry.location, 0)
            costs = building.costs.get(level)
            if not costs:
                return None
            if all(village.resources.get(r, 0) >= costs.get(r, 0) for r in RESOURCES):
                return entry, {r: int(costs.get(r, 0)) for r in RESOURCES}
            return None
        ahead[entry.location] = ahead.get(entry.location, 0) + 1
    return None



def running_storage_gain(plan: VillagePlan) -> tuple[dict[int, float], float]:
    """Capacity a running warehouse/granary build adds, and in how many hours."""
    village = plan.village
    gain = {r: 0.0 for r in RESOURCES}
    for entry in village.queue:
        if (
            entry.queue_type not in (QUEUE_VILLAGE, QUEUE_RESOURCES)
            or entry.finished <= 0
            or entry.building_type not in WAREHOUSE_TYPES + GRANARY_TYPES
        ):
            continue
        building = next((b for b in village.storages if b.location == entry.location), None)
        if building is None:
            continue
        delta = _effect_delta(building, building.level, building.level + 1)
        for r in ((4,) if entry.building_type in GRANARY_TYPES else (1, 2, 3)):
            gain[r] += delta
    return gain, plan.queue_hours


def overflow_excess(plan: VillagePlan, hours: float, shipped: dict[int, float] | None = None) -> dict[int, float]:
    """Resources that would overflow within *hours*.

    Counts what is already on its way here, takes off planned shipments and
    lets a running warehouse/granary build add its capacity when it ends.
    """
    village = plan.village
    shipped = shipped or {}
    rates = village.build_production()
    coming = village.incoming_total()
    gain, ready_in = running_storage_gain(plan)
    excess = {}
    for r in RESOURCES:
        start = village.resources.get(r, 0) + coming.get(r, 0) - shipped.get(r, 0)
        rate = max(0.0, rates[r])
        capacity = village.capacity.get(r, 0)
        if gain[r] > 0 and ready_in < hours:
            before = max(0.0, start + rate * ready_in - capacity)
            after = max(0.0, start + rate * hours - capacity - gain[r])
            excess[r] = max(before, after)
        else:
            excess[r] = max(0.0, start + rate * hours - capacity)
    return excess


def choose_overflow_unit(
    village: Village, troops: dict[int, dict], excess: dict[int, float],
) -> int | None:
    """The unit that burns the overflow wasting the least of other resources.

    For each unit trainable here: units needed to absorb the worst excess,
    and how much of the resources that are *not* overflowing that would
    use up. The smallest waste wins (ties: the faster unit).
    """
    best = None
    for unit, info in troops.items():
        if not set(info["building_types"]) & village.recruit_buildings:
            continue
        costs = info["costs"]
        if not any(costs.get(r, 0) > 0 for r in RESOURCES):
            continue
        needed = max(
            (math.ceil(excess[r] / costs[r]) for r in RESOURCES if costs.get(r, 0) > 0 and excess[r] > 0),
            default=0,
        )
        if needed <= 0:
            continue
        waste = sum(max(0.0, needed * costs.get(r, 0) - excess[r]) for r in RESOURCES)
        key = (waste, village.unit_times.get(unit, 1e9))
        if best is None or key < best[0]:
            best = (key, unit)
    return best[1] if best else None


@dataclass
class StorageChain:
    """Finishing the queue up to a waiting warehouse/granary to stop overflow."""

    first: QueueEntry  # the running build: finish it now
    steps: int  # finishes needed until the storage upgrade is done
    price: int  # gold for all of them (at today's remaining times)
    saved: float  # resources that would otherwise overflow meanwhile
    storage_type: int


def storage_chain(
    plan: VillagePlan,
    server_time: float,
    tiers: FinishTiers,
    shipped: dict[int, float] | None = None,
) -> StorageChain | None:
    """Cheapest way to get a queued storage upgrade done before storage overflows.

    The queue runs in order: the running build, then the master builder
    entries (reserved ones). To have the warehouse/granary sooner, every
    build before it and the storage build itself must be finished. Each
    finish costs the price for its remaining time (a build that has just
    started still has its whole build time left).
    """
    village = plan.village
    running = [e for e in village.queue if e.queue_type in (QUEUE_VILLAGE, QUEUE_RESOURCES)
               and e.finished > server_time]
    if not running:
        return None
    first = min(running, key=lambda e: e.finished)
    chain = [(first, first.finished - server_time)]
    levels_ahead: dict[int, int] = {first.location: 1}
    for entry in [e for e in village.queue if e.queue_type == 4]:
        if not entry.paid:
            break  # an unreserved entry would not start: the chain stops
        building = next((b for b in village.buildings() if b.location == entry.location), None)
        if building is None:
            break
        level = building.level + levels_ahead.get(entry.location, 0)
        levels_ahead[entry.location] = levels_ahead.get(entry.location, 0) + 1
        chain.append((entry, float(building.times.get(level, 0))))
    best = None
    for index, (entry, _seconds) in enumerate(chain):
        if entry.building_type not in WAREHOUSE_TYPES + GRANARY_TYPES:
            continue
        building = next((b for b in village.storages if b.location == entry.location), None)
        if building is None:
            continue
        level = building.level + sum(
            1 for previous, _s in chain[:index] if previous.location == entry.location
        )
        gain = _effect_delta(building, level, level + 1)
        if gain <= 0:
            continue
        group = (4,) if entry.building_type in GRANARY_TYPES else (1, 2, 3)
        natural = sum(seconds for _e, seconds in chain[: index + 1]) / 3600
        saved = (
            overflow_loss(village, natural, shipped=shipped)
            - overflow_loss(village, natural, {r: gain for r in group}, shipped=shipped)
        )
        if saved <= 0:
            continue
        price = sum(tiers.price(seconds) for _e, seconds in chain[: index + 1])
        candidate = StorageChain(chain[0][0], index + 1, price, saved, entry.building_type)
        if best is None or (price and saved / price > best.saved / max(best.price, 1)):
            best = candidate
        break  # the nearest storage upgrade in the queue
    return best


# Keeping the queue running: ship what the next master builder entry lacks ----------

# A shortage the village itself produces within this time is not shipped.
UNBLOCK_IGNORE_HOURS = 0.25
# Smaller unblock shipments are not worth a merchant trip.
UNBLOCK_MIN_SHIPMENT = 300


def master_entry_costs(village: Village) -> list[tuple[QueueEntry, dict[int, int], float]]:
    """Master builder entries in order with their cost and build hours."""
    ahead: dict[int, int] = {}
    for entry in village.queue:
        if entry.queue_type in (QUEUE_VILLAGE, QUEUE_RESOURCES):
            ahead[entry.location] = ahead.get(entry.location, 0) + 1
    result = []
    for entry in [e for e in village.queue if e.queue_type == 4]:
        building = next((b for b in village.buildings() if b.location == entry.location), None)
        if building is None:
            break
        level = building.level + ahead.get(entry.location, 0)
        ahead[entry.location] = ahead.get(entry.location, 0) + 1
        costs = {r: int(building.costs.get(level, {}).get(r, 0)) for r in RESOURCES}
        result.append((entry, costs, building.times.get(level, 0) / 3600))
    return result


# A master builder entry that would hold the queue up at least this long for
# missing resources is taken out when something else can start right away.
MASTER_STALL_CANCEL_HOURS = 0.5


def stalled_master_entry(plan: VillagePlan) -> tuple[QueueEntry, float] | None:
    """The first unreserved master builder entry when the queue is about to
    stand idle for it (missing resources, at least MASTER_STALL_CANCEL_HOURS)
    while another planned step or entry could start now. Returns the entry
    and how long it would hold the queue up (hours)."""
    blocked = blocking_deficit(plan)
    if blocked is None:
        return None
    entry, missing, needed_in = blocked
    if needed_in > 0.1:
        return None  # the queue is busy for a while yet
    village = plan.village
    stall = _stall_hours(missing, village.build_production())
    if stall < MASTER_STALL_CANCEL_HOURS:
        return None
    stock = village.build_resources()
    # Only what the automation would really start instead: other entries the
    # game accepted, or one of the next few planned upgrades (a new building
    # may still lack requirements the game checks).
    others = [
        costs for other, costs, _hours in master_entry_costs(village)
        if other is not entry and not other.paid
    ] + [
        step.costs for step in plan.steps[:IDLE_ALTERNATIVE_DEPTH]
        if step.location != entry.location and not step.construct and not step.train
    ]
    if not any(all(costs.get(r, 0) <= stock[r] for r in RESOURCES) for costs in others):
        return None
    return entry, stall


def blocking_deficit(plan: VillagePlan) -> tuple[QueueEntry, dict[int, float], float] | None:
    """What the first unreserved master builder entry will still lack when the
    queue reaches it (the queue would stand idle until it is there).

    Returns (entry, missing resources, hours until it is needed).
    """
    village = plan.village
    hours = plan.queue_hours
    rates = village.build_production()
    stock = village.build_resources()
    coming = village.incoming_total()
    for entry, costs, build_hours in master_entry_costs(village):
        if entry.paid:
            hours += build_hours
            continue
        missing = {
            r: max(0.0, costs[r] - stock[r] - coming.get(r, 0) - max(0.0, rates[r]) * hours)
            for r in RESOURCES
        }
        if sum(missing.values()) <= 0:
            return None
        return entry, missing, hours
    return None



# New buildings ---------------------------------------------------------------------

# Production bonus per level of a bonus building (sawmill, ...).
BONUS_PER_LEVEL = 5.0
# Build time of a new level-1 building used for ranking when unknown (hours).
NEW_BUILDING_HOURS = 1.0
# Rally point and wall sites: only their own building fits there.
SPECIAL_SITES = (39, 40)
WAREHOUSE = 10
GRANARY = 11
RESIDENCE = 25
PALACE = 26
# The residence needs the main building at this level.
RESIDENCE_MAIN_LEVEL = 5


def free_sites(village: Village, taken: set[int] | None = None) -> list[int]:
    """Empty building sites a general building can go on."""
    taken = taken or set()
    return [s for s in village.empty_slots if s not in taken and s not in SPECIAL_SITES]


def _config_costs(info: dict) -> dict[int, int]:
    costs = info.get("costs") or {}
    return {r: int(float(costs.get(str(r), costs.get(r, 0)) or 0)) for r in RESOURCES}


def storage_construction(
    village: Village,
    resource: int,
    config: dict[int, dict],
    levels: dict[int, int] | None = None,
    taken: set[int] | None = None,
    planned_types: set[int] | None = None,
    needed: int = 0,
) -> Step | None:
    """A new warehouse (or granary for crop) when the village has none."""
    building_type = GRANARY if resource == 4 else WAREHOUSE
    info = (config or {}).get(building_type)
    if (
        info is None
        or _storage_for(village, resource)
        or village.type_levels.get(building_type)
        or building_type in (planned_types or set())
        or not requirements_met(village, info.get("requirements"), levels)
    ):
        return None
    sites = free_sites(village, taken)
    if not sites:
        return None
    name = "spichlerz" if resource == 4 else "magazyn"
    return Step(
        village_id=village.id,
        location=sites[0],
        type=building_type,
        from_level=0,
        costs=_config_costs(info),
        seconds=int(float(info.get("time") or 0) or NEW_BUILDING_HOURS * 3600),
        gain=0.0,
        payback_hours=None,
        reason=(
            f"nowy {name}: wioska go nie ma, a potrzeba miejsca na {needed:,}".replace(",", " ")
        ),
        construct=True,
        upkeep=float(info.get("supply") or 0),
    )


def requirements_met(village: Village, requirements: list[dict], levels: dict[int, int] | None = None) -> bool:
    """Whether the building requirements (building type at a minimum level) hold."""
    current = dict(village.type_levels)
    if levels:
        for building in village.buildings():
            if building.location in levels:
                current[building.type] = max(current.get(building.type, 0), levels[building.location])
    for requirement in requirements or []:
        building = requirement.get("building")
        level = requirement.get("level")
        if not building or level is None:
            continue
        if current.get(int(building), 0) < int(level):
            return False
    return True


def construction_steps(
    village: Village,
    config: dict[int, dict],
    production: dict[int, float],
    multipliers: dict[int, float],
    weights: dict[int, float] | None,
    levels: dict[int, int],
    taken: set[int],
    planned_types: set[int] | None = None,
) -> list[Step]:
    """New bonus buildings this village may put on an empty site now."""
    planned_types = planned_types or set()
    if not village.empty_slots or not config:
        return []
    free = free_sites(village, taken)
    if not free:
        return []
    weights = weights or {r: 1.0 for r in RESOURCES}
    steps = []
    for building_type, resource in BONUS_BUILDINGS.items():
        info = config.get(building_type)
        if (
            info is None
            or village.type_levels.get(building_type)
            or building_type in planned_types
            or building_type in {b.type for b in village.bonus}
        ):
            continue
        if not requirements_met(village, info.get("requirements"), levels):
            continue
        gross = _gross_production(village, resource, production, multipliers)
        raw = gross * BONUS_PER_LEVEL / 100
        upkeep = float(info.get("supply") or 0)
        net = raw * weights[resource] - upkeep * weights[4]
        costs = {r: int(float((info.get("costs") or {}).get(str(r), (info.get("costs") or {}).get(r, 0)) or 0))
                 for r in RESOURCES}
        value = sum(costs[r] * weights[r] for r in RESOURCES)
        steps.append(Step(
            village_id=village.id,
            location=free[0],
            type=building_type,
            from_level=0,
            costs=costs,
            seconds=int(NEW_BUILDING_HOURS * 3600),
            gain=net,
            payback_hours=value / net if net > 0 else None,
            reason=f"nowy budynek: +{BONUS_PER_LEVEL:g}% {RESOURCE_NAMES[resource].lower()} (+{raw:.0f}/h)",
            raw_gain=raw,
            construct=True,
            upkeep=upkeep,
        ))
    return steps


def marketplace_construction(village: Village, config: dict[int, dict], reason: str) -> Step | None:
    """A new marketplace (merchants), when the village has none."""
    info = (config or {}).get(MARKETPLACE)
    if (
        info is None
        or village.marketplace is not None
        or village.type_levels.get(MARKETPLACE)
        or not free_sites(village)
        or not requirements_met(village, info.get("requirements"))
    ):
        return None
    costs = _config_costs(info)
    return Step(
        village_id=village.id,
        location=free_sites(village)[0],
        type=MARKETPLACE,
        from_level=0,
        costs=costs,
        seconds=int(NEW_BUILDING_HOURS * 3600),
        gain=0.0,
        payback_hours=None,
        reason=reason,
        construct=True,
    )



def build_reserve(plan: VillagePlan) -> dict[int, float]:
    """Resources building will need next: the next planned step plus the first
    master builder entry without reserved resources (what the queue runs
    next). Recruitment, shipments and overflow burning leave these alone."""
    reserve = {r: float(plan.next_step.costs.get(r, 0)) if plan.next_step else 0.0 for r in RESOURCES}
    for entry, costs, _hours in master_entry_costs(plan.village):
        if not entry.paid:
            for r in RESOURCES:
                reserve[r] += costs.get(r, 0)
            break
    return reserve


# Hero production ---------------------------------------------------------------

# Hero production settings in the game: 0 = all resources, 1-4 = one resource.
HERO_ALL = 0
# A single resource gives this many times the per-resource amount of "all".
HERO_SINGLE_FACTOR = 4
# Switch only when the bottleneck improves by at least this share.
HERO_SWITCH_MIN_GAIN = 0.03


def hero_contribution(choice: int, amount_all: float) -> dict[int, float]:
    """Resources per hour the hero adds with *choice*."""
    if choice == HERO_ALL:
        return {r: amount_all for r in RESOURCES}
    return {r: amount_all * HERO_SINGLE_FACTOR if r == choice else 0.0 for r in RESOURCES}


def network_demand(plans: list[VillagePlan]) -> dict[int, float]:
    """Resources still needed to reach the targets: the remaining field
    levels plus the planned non-field steps (storage, army, ...)."""
    demand = {r: 0.0 for r in RESOURCES}
    for plan in plans:
        _hours, costs = _remaining(plan.village, plan.target)
        for r in RESOURCES:
            demand[r] += costs[r]
        for step in plan.steps:
            if not step.is_field:
                for r in RESOURCES:
                    demand[r] += step.costs.get(r, 0)
    return demand


def hero_bottlenecks(
    plans: list[VillagePlan], current: int, amount_all: float,
) -> dict[int, float]:
    """For each hero setting: hours of network production the scarcest
    resource needs to cover the remaining demand (lower is better)."""
    demand = network_demand(plans)
    production = {r: 0.0 for r in RESOURCES}
    for plan in plans:
        for r, rate in plan.village.build_production().items():
            production[r] += max(0.0, rate)
    # The game's production already holds the current hero setting.
    for r, amount in hero_contribution(current, amount_all).items():
        production[r] = max(0.0, production[r] - amount)
    result = {}
    for choice in (HERO_ALL, 1, 2, 3, 4):
        extra = hero_contribution(choice, amount_all)
        worst = 0.0
        for r in RESOURCES:
            if demand[r] <= 0:
                continue
            rate = production[r] + extra[r]
            worst = max(worst, demand[r] / rate if rate > 0 else math.inf)
        result[choice] = worst
    return result


def choose_hero_resource(
    plans: list[VillagePlan], current: int, amount_all: float,
) -> tuple[int, dict[int, float]] | None:
    """The hero setting that shortens the network's bottleneck the most,
    or None when the current one is as good (within HERO_SWITCH_MIN_GAIN)
    or nothing is left to build."""
    if amount_all <= 0 or current not in (HERO_ALL, 1, 2, 3, 4):
        return None
    hours = hero_bottlenecks(plans, current, amount_all)
    if not any(value > 0 for value in hours.values()):
        return None
    best = min(hours, key=lambda choice: (hours[choice], choice != HERO_ALL))
    if best == current or hours[best] > hours[current] * (1 - HERO_SWITCH_MIN_GAIN):
        return None
    return best, hours


# Raid protection ----------------------------------------------------------------

# Cranny (hideout): its stock cannot be looted.
CRANNY = 23
# Shipments smaller than this are not worth a merchant trip before a raid.
RAID_MIN_SHIPMENT = 300
# With negative crop production this many hours of crop stay home.
RAID_CROP_BUFFER_HOURS = 2.0


@dataclass
class Evacuation:
    """Resources sent away from a village about to be raided."""

    source_id: str
    source_name: str
    target_id: str
    target_name: str
    resources: dict[int, int]
    merchants: int
    travel_hours: float


def raid_exposed(village: Village) -> dict[int, float]:
    """Stock an attacker could take: everything above the cranny's protection
    (and a crop buffer when crop production is negative)."""
    exposed = {}
    for r in RESOURCES:
        keep = village.protected
        if r == 4 and village.production.get(4, 0) < 0:
            keep = max(keep, -village.production[4] * RAID_CROP_BUFFER_HOURS)
        exposed[r] = max(0.0, village.resources.get(r, 0) - keep)
    return exposed


def plan_evacuation(
    plans: list[VillagePlan],
    donors: set[str],
    unsafe: set[str],
    merchant_speed: dict[int, float],
    world_speed: float,
) -> list[Evacuation]:
    """Shipments that take the exposed stock of the *donors* (attack about to
    land) to the nearest own villages with free storage that are not under
    attack themselves (*unsafe*). Resources on a merchant cannot be looted."""
    villages = [plan.village for plan in plans]
    room = {
        v.id: {
            r: max(0.0, v.capacity.get(r, 0) - v.resources.get(r, 0) - v.incoming_total()[r])
            for r in RESOURCES
        }
        for v in villages
    }
    result = []
    for donor in villages:
        if donor.id not in donors or donor.marketplace is None:
            continue
        if donor.merchants_free <= 0 or donor.merchant_carry <= 0:
            continue
        speed = merchant_fields_per_hour(donor.tribe, merchant_speed, world_speed)
        if speed <= 0:
            continue
        exposed = raid_exposed(donor)
        merchants_left = donor.merchants_free
        receivers = sorted(
            (v for v in villages if v.id != donor.id and v.id not in unsafe and v.id not in donors),
            key=lambda v: distance(donor, v),
        )
        for receiver in receivers:
            if sum(exposed.values()) < RAID_MIN_SHIPMENT or merchants_left <= 0:
                break
            travel = distance(donor, receiver) / speed
            # The receiver keeps producing while the merchants are on the way.
            space = {
                r: max(0.0, room[receiver.id][r] - max(0.0, receiver.production.get(r, 0)) * travel)
                for r in RESOURCES
            }
            wanted = {r: min(exposed[r], space[r]) for r in RESOURCES}
            # Too few merchants: wood, clay and iron first (crop is cheap and
            # plentiful), each group shared in proportion.
            limit = merchants_left * donor.merchant_carry
            shipment = {r: 0 for r in RESOURCES}
            for group in ((1, 2, 3), (4,)):
                need = sum(wanted[r] for r in group)
                if need <= 0 or limit <= 0:
                    continue
                scale = min(1.0, limit / need)
                for r in group:
                    shipment[r] = int(wanted[r] * scale)
                limit -= sum(shipment[r] for r in group)
            total = sum(shipment.values())
            if total < RAID_MIN_SHIPMENT:
                continue
            merchants = math.ceil(total / donor.merchant_carry)
            result.append(Evacuation(
                source_id=donor.id,
                source_name=donor.name,
                target_id=receiver.id,
                target_name=receiver.name,
                resources={r: a for r, a in shipment.items() if a > 0},
                merchants=merchants,
                travel_hours=travel,
            ))
            merchants_left -= merchants
            for r, amount in shipment.items():
                exposed[r] -= amount
                room[receiver.id][r] -= amount
    return result


def raid_recruitment(exposed: dict[int, float], unit_costs: dict[int, float]) -> int:
    """Units of one kind the exposed stock pays for (the scarcest resource
    of the unit's costs limits it)."""
    counts = [
        int(max(0.0, exposed.get(r, 0)) // cost)
        for r, cost in unit_costs.items()
        if r in RESOURCES and cost and cost > 0
    ]
    return min(counts) if counts else 0


def shipment_raided(
    threats: dict[str, float], target_id: str, server_time: float, travel_hours: float,
    window_hours: float = 2.0,
) -> bool:
    """A shipment would land shortly before an attack on its target: what it
    brings would just be looted."""
    attack = threats.get(target_id)
    if attack is None:
        return False
    arrival = server_time + travel_hours * 3600
    return arrival <= attack <= arrival + window_hours * 3600


# Crop bank ----------------------------------------------------------------------

# Free warehouse kept for this many hours of the bank's own production when
# milling (the converted resources must not overflow right away).
CROP_BANK_ROOM_HOURS = 1.0
# Milled resources first cover this many hours of the villages' building.
MILL_NEAR_HOURS = 6.0
# A granary upgrade finishing this long after the bank would be full is
# still waited for (a little crop lost beats milling a small batch).
MILL_WAIT_GRACE_HOURS = 0.25


def crop_bank_village(villages: list[Village], chosen: str = "") -> str | None:
    """The crop bank: the chosen village, else the capital."""
    ids = {v.id for v in villages}
    if chosen and chosen in ids:
        return chosen
    return next((v.id for v in villages if v.is_capital), None)


def _near_deficit(plan: VillagePlan, hours: float) -> dict[int, float]:
    """Wood, clay and iron the village will lack when its planned steps of
    the next *hours* are due to start: for each step, everything planned up
    to it against stock, what is on the way and production until then (the
    worst moment counts)."""
    village = plan.village
    coming = village.incoming_total()
    rates = village.build_production()
    stock = village.build_resources()
    need = {r: 0.0 for r in (1, 2, 3)}
    deficit = {r: 0.0 for r in (1, 2, 3)}
    start = plan.queue_hours
    for step in plan.steps:
        if start >= hours:
            break
        for r in need:
            need[r] += step.costs.get(r, 0)
            have = stock[r] + coming.get(r, 0) + max(0.0, rates[r]) * start
            deficit[r] = max(deficit[r], need[r] - have)
        start += step.seconds / 3600
    return deficit


def _fill(amount: float, weights: dict[int, float], room: dict[int, float],
          split: dict[int, float]) -> float:
    """Share *amount* in proportion to *weights*, never above *room* (what
    *split* already holds counts); returns what did not fit."""
    left = amount
    open_ = {r for r in weights if weights[r] > 0 and room[r] - split[r] > 1}
    while left > 1 and open_:
        weight = sum(weights[r] for r in open_)
        shares = {r: left * weights[r] / weight for r in open_}
        full = False
        for r, share in shares.items():
            take = min(share, room[r] - split[r])
            split[r] += take
            left -= take
            if room[r] - split[r] <= 1:
                open_.discard(r)
                full = True
        if not full:
            break
    return left


def _level_hours(
    amount: float, lacking: dict[int, float], rate: dict[int, float],
    room: dict[int, float], split: dict[int, float],
) -> float:
    """Give *amount* to the resources whose shortage takes the network the
    most hours to produce, until those hours are level (never above *room*);
    returns what did not fit."""
    def given(hours: float) -> dict[int, float]:
        return {
            r: min(max(0.0, room[r] - split[r]),
                   max(0.0, lacking[r] - max(rate[r], 1.0) * hours))
            for r in lacking
        }

    top = max((lacking[r] / max(rate[r], 1.0) for r in lacking), default=0.0)
    if sum(given(0.0).values()) <= amount:
        extra = given(0.0)
    else:
        low, high = 0.0, top
        for _ in range(60):
            mid = (low + high) / 2
            if sum(given(mid).values()) > amount:
                low = mid
            else:
                high = mid
        extra = given(high)
    for r, value in extra.items():
        split[r] += value
    return amount - sum(extra.values())


def crop_bank_mill(
    bank: VillagePlan,
    plans: list[VillagePlan],
    fill_ratio: float,
    min_amount: float,
    server_time: float = 0.0,
    merchant_speed: dict[int, float] | None = None,
    world_speed: float = 1.0,
    hero_type: int = HERO_ALL,
    hero_amount_all: float = 0.0,
) -> dict[int, int] | None:
    """Wood, clay and iron the NPC merchant should make of the bank's crop;
    None while it is not time yet.

    Milling costs the same gold for any amount, so it waits for the biggest
    batch: the granaries filled to *fill_ratio*, and no granary upgrade
    finishing before they would overflow (the bigger granary takes more).
    The crop building and recruitment need next stays.

    The milled resources go first to what the villages' next hours of
    building lack (the bank's own fully, the others' as far as the bank's
    merchants can carry it in that time), the rest in proportion to what
    the network lacks for its targets; never above the free warehouse.
    """
    village = bank.village
    capacity = village.capacity.get(4, 0)
    crop = village.resources.get(4, 0)
    if capacity <= 0 or crop < fill_ratio * capacity:
        return None
    rates = village.build_production()
    crop_rate = max(0.0, rates[4])
    full_in = (capacity - crop) / crop_rate if crop_rate > 0 else math.inf
    growing = [
        entry.finished for entry in village.queue
        if entry.queue_type in (QUEUE_VILLAGE, QUEUE_RESOURCES)
        and entry.building_type in GRANARY_TYPES and entry.finished > server_time
    ]
    if growing and (min(growing) - server_time) / 3600 <= full_in + MILL_WAIT_GRACE_HOURS:
        return None  # a bigger granary is moments away: mill a bigger batch then
    keep = build_reserve(bank).get(4, 0) + village.recruitment_reserve.get(4, 0)
    if rates[4] < 0:
        keep += -rates[4] * 2
    room = {
        r: max(0.0, village.capacity.get(r, 0) - village.resources.get(r, 0)
               - max(0.0, rates[r]) * CROP_BANK_ROOM_HOURS)
        for r in (1, 2, 3)
    }
    convert = min(crop - keep, sum(room.values()))
    if convert < min_amount:
        return None
    # What the next hours of building lack: the bank's own ...
    near = _near_deficit(bank, MILL_NEAR_HOURS)
    # ... and the other villages', as much as the bank's merchants deliver.
    speed = merchant_fields_per_hour(village.tribe, merchant_speed or {}, world_speed)
    if village.marketplace is not None and village.merchant_carry > 0 and speed > 0:
        others = {}
        trips = []
        for plan in plans:
            if plan.village.id == village.id:
                continue
            deficit = _near_deficit(plan, MILL_NEAR_HOURS)
            if sum(deficit.values()) <= 0:
                continue
            others[plan.village.id] = deficit
            trips.append(max(2 * distance(village, plan.village) / speed, 1 / 60))
        if others:
            deliver = (
                village.marketplace.level * village.merchant_carry * MILL_NEAR_HOURS / max(trips)
            )
            wanted = sum(sum(d.values()) for d in others.values())
            scale = min(1.0, deliver / wanted) if wanted > 0 else 0.0
            for deficit in others.values():
                for r in near:
                    near[r] += deficit[r] * scale
    split = {r: 0.0 for r in (1, 2, 3)}
    first = min(convert, sum(near.values()))
    left = convert - first + _fill(first, near, room, split)
    # The rest shortens the network's slowest resource: what each one still
    # lacks for the targets, in hours of the network's production, levelled.
    demand = network_demand(plans)
    stock = {
        r: sum(p.village.resources.get(r, 0) + p.village.incoming_total().get(r, 0) for p in plans)
        for r in (1, 2, 3)
    }
    rate = {
        r: sum(max(0.0, p.village.build_production()[r]) for p in plans)
        - hero_contribution(hero_type, hero_amount_all)[r]
        + hero_contribution(HERO_ALL, hero_amount_all)[r]
        for r in (1, 2, 3)
    }
    lacking = {r: max(0.0, demand[r] - stock[r] - split[r]) for r in (1, 2, 3)}
    left = _level_hours(left, lacking, rate, room, split)
    if left > 1:
        _fill(left, {r: 1.0 for r in (1, 2, 3)}, room, split)
    gains = {r: int(split[r]) for r in (1, 2, 3)}
    if sum(gains.values()) < min_amount:
        return None
    return gains


# Spare crop and crop production a village's merchants should take to the
# crop bank within this many hours.
BANK_MERCHANT_HOURS = 4.0


def bank_merchants_missing(
    plan: VillagePlan, bank: Village, merchant_speed: dict[int, float], world_speed: float,
) -> int:
    """Merchants (marketplace levels) the village lacks to carry its spare
    crop and its crop production to the bank within BANK_MERCHANT_HOURS."""
    village = plan.village
    market = village.marketplace
    speed = merchant_fields_per_hour(village.tribe, merchant_speed, world_speed)
    if market is None or village.merchant_carry <= 0 or speed <= 0:
        return 0
    round_trip = max(2 * distance(village, bank) / speed, 1 / 60)
    crop = max(0.0, _spare(plan)[4]) + max(0.0, village.build_production()[4]) * BANK_MERCHANT_HOURS
    needed = math.ceil(crop * round_trip / (village.merchant_carry * BANK_MERCHANT_HOURS))
    return max(0, needed - market.level)


# Hero against a stalled queue -------------------------------------------------------

# Switch for a stall only when it ends at least this much sooner.
HERO_STALL_MIN_SAVED_HOURS = 3 / 60


def hero_stall_waits(
    plan: VillagePlan, current: int, amount_all: float,
) -> dict[int, float] | None:
    """Hours the hero's home village, waiting with an empty queue for its
    next step's resources, still waits with each hero setting; None when it
    is not waiting."""
    step = plan.next_step
    if (
        step is None or not plan.queue_free or plan.afford_hours is None
        or plan.afford_hours <= 0 or amount_all <= 0 or current not in (HERO_ALL, 1, 2, 3, 4)
    ):
        return None
    village = plan.village
    stock = village.build_resources()
    coming = village.incoming_total()
    rates = village.build_production()
    own = hero_contribution(current, amount_all)
    base = {r: rates[r] - own[r] for r in RESOURCES}
    missing = {
        r: max(0.0, step.costs.get(r, 0) - stock[r] - coming.get(r, 0)) for r in RESOURCES
    }

    def wait(choice: int) -> float:
        extra = hero_contribution(choice, amount_all)
        hours = 0.0
        for r in RESOURCES:
            if missing[r] <= 0:
                continue
            rate = base[r] + extra[r]
            if rate <= 0:
                return math.inf
            hours = max(hours, missing[r] / rate)
        return hours

    return {choice: wait(choice) for choice in (HERO_ALL, 1, 2, 3, 4)}


def hero_stall_choice(
    plan: VillagePlan, current: int, amount_all: float,
) -> tuple[int, float, float] | None:
    """The hero setting that ends a stall of its home village's queue
    soonest, with the wait now and with it (hours); None when it is not
    worth a switch."""
    waits = hero_stall_waits(plan, current, amount_all)
    if waits is None:
        return None
    best = min(waits, key=lambda choice: (waits[choice], choice != current))
    if best == current or waits[current] - waits[best] < HERO_STALL_MIN_SAVED_HOURS:
        return None
    return best, waits[current], waits[best]


# Settlers ---------------------------------------------------------------------------

# Residence build seconds per level (from level -> level+1) with the main
# building at 100 %, from the game's tables (7-9) and extrapolated below.
RESIDENCE_BASE_SECONDS = {0: 450, 1: 600, 2: 900, 3: 1390, 4: 2290, 5: 3190, 6: 4090,
                          7: 4990, 8: 5880, 9: 6780, 10: 8090, 11: 9480, 12: 11030}
# Start this much earlier than the pure build and training time: resources
# have to be gathered and shipped.
SETTLE_TIME_MARGIN = 1.25
# Each residence level trains settlers this much faster (like the barracks:
# level 1 100 %, level 3 81 %, ... level 10 ~39 %).
RESIDENCE_TRAIN_FACTOR = 0.9


def settler_seconds_at(base_seconds: float, residence_level: int) -> float:
    """Training time of one settler with the residence at *residence_level*."""
    return base_seconds * RESIDENCE_TRAIN_FACTOR ** max(0, residence_level - 1)
SETTLE_BUFFER_HOURS = 4.0


def settle_hours(village: Village, level: int, count: int, settler_seconds: float) -> float:
    """Build and training hours still needed for *count* settlers: main
    building to the residence's minimum, the residence to *level*, then the
    settlers one after another (resources assumed to be there)."""
    hours = 0.0
    main = village.main_building
    factor = 1.0
    residence = village.residence
    if residence is None and main is not None:
        for lvl in range(main.planned_level, RESIDENCE_MAIN_LEVEL):
            hours += main.times.get(lvl, 600) / 3600
        top = max(main.planned_level, RESIDENCE_MAIN_LEVEL)
        if top in main.effects:
            factor = main.effects[top] / 100
    elif main is not None and main.planned_level in main.effects:
        factor = main.effects[main.planned_level] / 100
    start = residence.planned_level if residence is not None else 0
    for lvl in range(start, level):
        if residence is not None and lvl in residence.times:
            hours += residence.times[lvl] / 3600
        else:
            hours += RESIDENCE_BASE_SECONDS.get(lvl, 8000) * factor / 3600
    missing = max(0, count - village.settlers)
    return hours + missing * settler_seconds_at(settler_seconds, level) / 3600


# Troop evasion ----------------------------------------------------------------------


def evasion_target(villages: list[Village], source: Village, unsafe: set[str]) -> Village | None:
    """The nearest own village not under attack, for the evading troops."""
    options = [v for v in villages if v.id != source.id and v.id not in unsafe]
    return min(options, key=lambda v: distance(source, v)) if options else None


def evasion_travel_seconds(units: dict[int, int], speeds: dict[int, float], fields: float) -> float:
    """Travel time of the slowest unit sent over *fields*."""
    slowest = min((speeds.get(nr, 0) for nr, count in units.items() if count > 0), default=0)
    return fields / slowest * 3600 if slowest > 0 else 0.0


def raid_lead_seconds(
    village: Village, villages: list[Village], unsafe: set[str],
    merchant_speed: dict[int, float], world_speed: float,
    min_seconds: float, max_seconds: float,
) -> float:
    """How long before an attack lands the evacuation has to start so the
    merchants can carry all lootable stock to the nearest safe village: the
    trips needed times the round trip (merchants come back as long as they
    went), plus a couple of minutes; between *min_seconds* and *max_seconds*."""
    market = village.marketplace
    speed = merchant_fields_per_hour(village.tribe, merchant_speed, world_speed)
    target = evasion_target(villages, village, unsafe)
    if market is None or village.merchant_carry <= 0 or speed <= 0 or target is None:
        return min_seconds
    per_trip = market.level * village.merchant_carry
    trips = math.ceil(sum(raid_exposed(village).values()) / per_trip) if per_trip > 0 else 0
    round_trip = max(2 * distance(village, target) / speed * 3600, 60.0)
    return max(min_seconds, min(max_seconds, trips * round_trip + 120))


# An idle queue: wait at least this long for step 1 before a later step goes first.
IDLE_ALTERNATIVE_HOURS = 0.25
# Only this many planned steps are looked at for an alternative.
IDLE_ALTERNATIVE_DEPTH = 4
# Master builder entries given up for an alternative come back to the top of
# the plan and push it down: an idle queue looks this much deeper for it.
IDLE_ALTERNATIVE_SLACK = 4


def affordable_alternative(plan: VillagePlan) -> Step | None:
    """With the queue free and step 1 waiting at least IDLE_ALTERNATIVE_HOURS
    for resources: the first of the next planned steps (on a building not
    planned before it, so its level is the current one) that the village can
    pay right now."""
    step = plan.next_step
    if (
        step is None or not plan.queue_free or plan.afford_hours is None
        or plan.afford_hours < IDLE_ALTERNATIVE_HOURS
    ):
        return None
    stock = plan.village.build_resources()
    queue = QUEUE_RESOURCES if step.is_field else QUEUE_VILLAGE
    seen = {step.location}
    for other in plan.steps[1:IDLE_ALTERNATIVE_DEPTH + IDLE_ALTERNATIVE_SLACK]:
        if other.location in seen or other.train:
            seen.add(other.location)
            continue
        seen.add(other.location)
        other_queue = QUEUE_RESOURCES if other.is_field else QUEUE_VILLAGE
        if plan.village.free_slots.get(other_queue, 0) <= 0 and other_queue != queue:
            continue
        if all(other.costs.get(r, 0) <= stock[r] for r in RESOURCES):
            return other
    return None
