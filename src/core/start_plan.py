"""Game start plan ("Start" tab): a fixed build order for the first village.

The plan is a list of steps worked through in order in one village:

* ``build``: a building of ``type`` to ``level`` (built on a free site when
  missing); for resource fields (types 1-4) ``scope`` is ``all`` (every field
  of the type, or of all four types with ``type`` 0) or ``one`` (one field);
* ``reward``: collect the quest reward that raises a building (``type``) to
  ``level`` (the residence 5 -> 10 trick) instead of building those levels;
* ``settlers``: have ``count`` settlers (home + in training);
* ``troops``: train ``count`` of the cheapest barracks unit;
* ``manual``: something the player does (demolish, move with the menhir,
  send the settlers …); with ``blocking`` (the default) the plan waits
  until it is marked done, otherwise it is only shown.

Steps of the two building queues (fields / buildings) run side by side: a
step waiting for its queue lets a later step of the other queue go, but a
step waiting for resources keeps them (nothing later starts before it). A
reward, settlers or manual step that is not done stops everything after it.
The clicks are made by :class:`BuildClickExecutor`.
"""

from __future__ import annotations

from dataclasses import dataclass

RESOURCES = (1, 2, 3, 4)
QUEUE_VILLAGE = 1
QUEUE_RESOURCES = 2
SPECIAL_SITES = (39, 40)  # rally point, wall
RESIDENCE, PALACE, BARRACKS = 25, 26, 19

NAMES = {
    0: "Wszystkie pola", 1: "Las", 2: "Glinianka", 3: "Kopalnia żelaza", 4: "Pole zboża",
    10: "Magazyn", 11: "Spichlerz", 13: "Kuźnia", 15: "Budynek główny", 16: "Plac zbiórki",
    17: "Targowisko", 18: "Ambasada", 19: "Koszary", 20: "Stajnia", 21: "Warsztat",
    22: "Akademia", 23: "Kryjówka", 24: "Ratusz", 25: "Rezydencja", 26: "Pałac",
}


def _b(type_, level, scope="all"):
    return {"kind": "build", "type": type_, "level": level, "scope": scope}


def _m(text, blocking=True):
    return {"kind": "manual", "text": text, "blocking": blocking}


# The player's plan for two quick villages.
DEFAULT_STEPS = [
    {"kind": "troops", "count": 5},
    _b(10, 3), _b(11, 3), _b(18, 1),
    _m("Przyłącz oazę w ambasadzie (jeśli możliwe)", blocking=False),
    _b(17, 1), _b(23, 1),
    _b(4, 1, "all"), _b(4, 3, "one"), _b(4, 3, "all"),
    _b(0, 2, "all"),
    _b(15, 7), _b(25, 1), _b(10, 5), _b(25, 5),
    {"kind": "reward", "type": 25, "level": 10},
    {"kind": "settlers", "count": 1},
    {"kind": "settlers", "count": 2},
    _m("Poczekaj na 3. i 4. kryjówkę rozbójników (ok. 1:27 h po wyczyszczeniu pierwszej) i wyczyść je"),
    {"kind": "settlers", "count": 3},
    _b(15, 10),
    _m("Wyburz rezydencję (najpierw upewnij się, że 3 osadników jest wyszkolonych)", blocking=False),
    _b(19, 3), _b(22, 10), _b(24, 1), _b(21, 1), _b(11, 7),
    _m("Zależnie od surowców: wyburz warsztat, akademię, koszary", blocking=False),
    _m("Małe święto w ratuszu", blocking=False),
    _m("Przenieś się menhirem jak najbliżej miejsca na nową osadę"),
    _m("Wyślij osadników"),
]

# Shown above the plan: what the player keeps doing the whole time.
ONGOING = (
    "Cały czas: czyść kryjówki rozbójników (najbliższą raz po raz, aż będzie pusta, potem następną), "
    "rób wyzwania bohatera, zbieraj rumowiska, graj w karty (paczki surowców), odbieraj nagrody "
    "(zaznacz „Zadania…” w zakładce Budowa), zadania poboczne: zmień nazwę osady, dosiądź konia, "
    "przyłącz oazę, wystaw i cofnij ofertę na targu (NPC)."
)


def label(step: dict) -> str:
    kind = step.get("kind")
    if kind == "build":
        name = NAMES.get(int(step.get("type", 0)), f"Budynek {step.get('type')}")
        level = int(step.get("level", 1))
        if int(step.get("type", 0)) in (0, 1, 2, 3, 4):
            scope = " (jedno)" if step.get("scope") == "one" else " (wszystkie)"
            return f"{name} na {level}{scope}"
        return f"{name} {level}"
    if kind == "reward":
        name = NAMES.get(int(step.get("type", 0)), f"Budynek {step.get('type')}")
        return f"Odbierz nagrodę: {name.lower()} na {int(step.get('level', 0))}"
    if kind == "settlers":
        return f"Osadnicy: {int(step.get('count', 1))}"
    if kind == "troops":
        return f"Wyszkol {int(step.get('count', 5))} żołnierzy (najtańsza jednostka z koszar)"
    return str(step.get("text") or "Krok ręczny")


@dataclass
class Action:
    kind: str  # upgrade, construct, collect, settlers, troops
    location: int = 0
    type: int = 0
    from_level: int = 0
    is_field: bool = False
    costs: dict | None = None
    quest_id: int = 0
    unit: int = 0
    quantity: int = 0


@dataclass
class StepState:
    index: int
    status: str  # done, act, wait, manual, blocked, later
    text: str
    action: Action | None = None


def _fields(village, type_: int) -> list:
    types = RESOURCES if type_ == 0 else (type_,)
    return [b for b in village.fields if b.type in types]


def _buildings(village, type_: int) -> list:
    return [b for b in getattr(village, "all_buildings", []) if b.type == type_]


def _costs(building, level: int) -> dict[int, int] | None:
    if level not in building.costs:
        return None
    return {r: int(building.costs[level].get(r, 0)) for r in RESOURCES}


def _config_costs(info: dict) -> dict[int, int]:
    costs = info.get("costs") or {}
    return {r: int(float(costs.get(str(r), costs.get(r, 0)) or 0)) for r in RESOURCES}


def _requirements_met(village, requirements) -> bool:
    for requirement in requirements or []:
        building, level = requirement.get("building"), requirement.get("level")
        if building and level is not None and village.type_levels.get(int(building), 0) < int(level):
            return False
    return True


def build_done(step: dict, village) -> bool:
    type_, level = int(step.get("type", 0)), int(step.get("level", 1))
    if type_ in (0, 1, 2, 3, 4):
        fields = _fields(village, type_)
        if not fields:
            return False
        if step.get("scope") == "one":
            return any(b.level >= level for b in fields)
        return all(b.level >= level for b in fields)
    return village.type_levels.get(type_, 0) >= level


def _build_state(index: int, step: dict, village, config: dict, depth: int = 0) -> tuple[StepState, int | None]:
    """State of a build step and the queue it needs."""
    type_, level = int(step.get("type", 0)), int(step.get("level", 1))
    text = label(step)
    if type_ in (0, 1, 2, 3, 4):
        fields = _fields(village, type_)
        if step.get("scope") == "one" and any(b.planned_level >= level for b in fields):
            return StepState(index, "wait", f"{text}: w kolejce budowy"), None
        pending = [b for b in fields if b.planned_level < level]
        if not pending:
            return StepState(index, "wait", f"{text}: w kolejce budowy"), None
        # One field: the highest (closest to the level); all: the lowest first.
        target = max(pending, key=lambda b: b.planned_level) if step.get("scope") == "one" \
            else min(pending, key=lambda b: (b.planned_level, b.location))
        costs = _costs(target, target.planned_level)
        if costs is None:
            return StepState(index, "blocked", f"{text}: brak danych kolejnego poziomu"), None
        return StepState(index, "act", text, Action(
            "upgrade", location=target.location, type=target.type,
            from_level=target.planned_level, is_field=True, costs=costs,
        )), QUEUE_RESOURCES
    existing = _buildings(village, type_)
    if existing:
        target = max(existing, key=lambda b: b.planned_level)
        if target.planned_level >= level:
            return StepState(index, "wait", f"{text}: w kolejce budowy"), None
        if target.planned_level >= target.max_level:
            return StepState(index, "blocked", f"{text}: budynek ma już najwyższy poziom"), None
        costs = _costs(target, target.planned_level)
        if costs is None:
            return StepState(index, "blocked", f"{text}: brak danych kolejnego poziomu"), None
        return StepState(index, "act", text, Action(
            "upgrade", location=target.location, type=type_,
            from_level=target.planned_level, costs=costs,
        )), QUEUE_VILLAGE
    if any(e.building_type == type_ for e in village.queue):
        return StepState(index, "wait", f"{text}: budowa w kolejce"), None
    info = config.get(type_)
    if not info:
        return StepState(index, "blocked", f"{text}: gra nie podała danych budynku"), None
    if not _requirements_met(village, info.get("requirements")):
        # Build what it needs first (e.g. the main building for the marketplace).
        for requirement in info.get("requirements") or []:
            building, level = requirement.get("building"), requirement.get("level")
            if not building or level is None or village.type_levels.get(int(building), 0) >= int(level):
                continue
            if depth < 3:
                sub_step = {"kind": "build", "type": int(building), "level": int(level), "scope": "all"}
                state, queue = _build_state(index, sub_step, village, config, depth + 1)
                state.text = f"{text}: najpierw {state.text}"
                return state, queue
        needs = ", ".join(
            f"{NAMES.get(int(r['building']), r['building'])} {r['level']}"
            for r in info.get("requirements") or [] if r.get("building")
        )
        return StepState(index, "blocked", f"{text}: wymaga {needs}"), None
    sites = [s for s in village.empty_slots if s not in SPECIAL_SITES]
    if not sites:
        return StepState(index, "blocked", f"{text}: brak wolnego placu"), None
    return StepState(index, "act", text, Action(
        "construct", location=sites[0], type=type_, costs=_config_costs(info),
    )), QUEUE_VILLAGE


def _affordable(costs: dict, stock: dict) -> bool:
    return all(stock.get(r, 0) >= costs.get(r, 0) for r in RESOURCES)


def plan_states(steps: list[dict], village, quests: list, troops: dict, config: dict) -> list[StepState]:
    """What every step waits for, and the actions to click now.

    *quests* are :class:`quests.Quest`, *troops* unit -> {costs,
    building_types} of the player's tribe, *config* building type -> its
    construction data.
    """
    states: list[StepState] = []
    stock = {r: float(village.resources.get(r, 0)) for r in RESOURCES}
    used_queues: set[int] = set()
    stopped: str | None = None
    for index, step in enumerate(steps):
        text = label(step)
        if step.get("done"):
            states.append(StepState(index, "done", text))
            continue
        if stopped:
            states.append(StepState(index, "later", text))
            continue
        kind = step.get("kind")
        if kind == "build":
            if build_done(step, village):
                states.append(StepState(index, "done", text))
                continue
            state, queue = _build_state(index, step, village, config)
            if state.action is None:
                states.append(state)
                if state.status == "blocked":
                    stopped = state.text
                continue
            if queue in used_queues or village.free_slots.get(queue, 0) <= 0:
                state.status, state.action = "wait", None
                state.text = f"{text}: czeka na wolną kolejkę"
                used_queues.add(queue)
                states.append(state)
                continue
            if not _affordable(state.action.costs, stock):
                missing = {r: max(0, state.action.costs.get(r, 0) - stock[r]) for r in RESOURCES}
                state.status, state.action = "wait", None
                state.text = f"{text}: czeka na surowce (brakuje " + " / ".join(
                    f"{missing[r]:,.0f}" for r in RESOURCES) + ")"
                states.append(state)
                stopped = state.text  # the resources stay for this step
                continue
            for r in RESOURCES:
                stock[r] -= state.action.costs.get(r, 0)
            used_queues.add(queue)
            states.append(state)
            continue
        if kind == "troops":
            count = int(step.get("count", 5))
            units = [
                (sum(info["costs"].values()), unit) for unit, info in troops.items()
                if BARRACKS in info.get("building_types", []) and sum(info["costs"].values()) > 0
            ]
            if BARRACKS not in village.recruit_buildings or not units:
                states.append(StepState(index, "wait", f"{text}: brak koszar"))
                continue  # does not hold the plan up
            unit = min(units)[1]
            costs = {r: troops[unit]["costs"].get(r, 0) * count for r in RESOURCES}
            if not _affordable(costs, stock):
                states.append(StepState(index, "wait", f"{text}: czeka na surowce"))
                continue
            for r in RESOURCES:
                stock[r] -= costs[r]
            states.append(StepState(index, "act", text, Action("troops", unit=unit, quantity=count, costs=costs)))
            continue
        if kind == "reward":
            type_, level = int(step.get("type", 0)), int(step.get("level", 0))
            if village.type_levels.get(type_, 0) >= level:
                states.append(StepState(index, "done", text))
                continue
            quest = next((q for q in quests if q.done and (q.building_reward or (0,))[0] == type_), None)
            if quest is None:
                states.append(StepState(index, "wait", f"{text}: zadanie jeszcze niewykonane"))
                stopped = text
                continue
            states.append(StepState(index, "act", text, Action("collect", type=type_, quest_id=quest.id)))
            stopped = text
            continue
        if kind == "settlers":
            count = int(step.get("count", 1))
            if village.settlers >= count:
                states.append(StepState(index, "done", text))
                continue
            residence = village.residence
            if residence is None or residence.level < 10 or not village.settler_unit:
                states.append(StepState(index, "wait", f"{text}: potrzebna rezydencja 10"))
                stopped = text
                continue
            costs = village.settler_costs
            afford = min(
                (int(stock[r] // costs[r]) for r in RESOURCES if costs.get(r, 0) > 0), default=0,
            )
            quantity = min(count - village.settlers, afford)
            if quantity <= 0:
                states.append(StepState(index, "wait", f"{text}: czeka na surowce"))
                stopped = text
                continue
            states.append(StepState(index, "act", text, Action("settlers", unit=village.settler_unit, quantity=quantity)))
            stopped = text
            continue
        if step.get("blocking", True):
            states.append(StepState(index, "manual", f"{text} — teraz Ty (kliknij „Zrobione”)"))
            stopped = text
        else:
            states.append(StepState(index, "manual", f"{text} — zrób, gdy możesz (plan idzie dalej)"))
    return states
