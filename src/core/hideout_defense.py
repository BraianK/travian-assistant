"""Counter-strike robber hideouts that attack the player.

When a robber hideout (village type 5, owner id -1) sends an attack at one of
the player's villages, its troops are away. The automation reacts right away:

1. **Counter-attack** from the capital with the configured share of each unit
   type (and optionally the hero). With siege enabled it goes as a siege
   (movement 47, needs rams and at least 1000 units) when the siege still lands
   before the robbers hit the player; otherwise as a normal attack.
2. **Raid** (movement 4) with the configured share of units, landing the
   configured delay after the counter-attack — or as soon as possible after it.
   A raid that could not land before the robbers hit the player is not sent,
   so the troops stay home to defend in case the hideout was not cleared.

Pure planning lives in module-level functions; ``HideoutDefense`` drives the
game through ``GameAttackBridge`` and keeps per-hideout ``HideoutCase`` state.
"""

from __future__ import annotations


import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

try:
    from src.core.attack_operations import (
        CATAPULT,
        HERO,
        MOVEMENT_ATTACK,
        MOVEMENT_SIEGE,
        RAM,
        RANDOM_TARGET,
        SIEGE_MIN_UNITS,
        describe_units,
    )
except ModuleNotFoundError:  # pragma: no cover - script fallback
    from core.attack_operations import (
        CATAPULT,
        HERO,
        MOVEMENT_ATTACK,
        MOVEMENT_SIEGE,
        RAM,
        RANDOM_TARGET,
        SIEGE_MIN_UNITS,
        describe_units,
    )

MOVEMENT_RAID = 4
ROBBER_PLAYER_ID = -1
HIDEOUT_VILLAGE_TYPE = 5
SCAN_INTERVAL_SECONDS = 10.0
VERIFY_TIMEOUT_SECONDS = 15.0
MAX_HANDLED_MOVEMENTS = 500
CATAPULT_TARGET_MIN_RALLY_POINT = 3
MAX_RAIDS = 30
LOOT_CHECK_INTERVAL_SECONDS = 20
MIN_LOOT_FOR_RAID = 100
RETURN_MARGIN_SECONDS = 5

STATUS_DETECTED = "detected"
STATUS_WAITING_HERO = "waiting_hero"
STATUS_COUNTER_FIRED = "counter_fired"
STATUS_RAID_SCHEDULED = "raid_scheduled"
STATUS_RAID_FIRED = "raid_fired"
STATUS_RAID_RETURNING = "raid_returning"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
FINAL_STATUSES = {STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED}

STATUS_LABELS = {
    STATUS_DETECTED: "Wykryto",
    STATUS_WAITING_HERO: "Czeka na bohatera",
    STATUS_COUNTER_FIRED: "Kontratak wysłany",
    STATUS_RAID_SCHEDULED: "Grabież zaplanowana",
    STATUS_RAID_FIRED: "Grabież wysłana",
    STATUS_RAID_RETURNING: "Grabież wraca",
    STATUS_DONE: "Gotowe",
    STATUS_FAILED: "Błąd",
    STATUS_SKIPPED: "Pominięto",
}

_READ_ROBBER_ATTACKS_JS = r"""(() => {
    const injector = angular.element(document.body).injector();
    const villages = injector.get("VillageService").U().data.map(v => v.data);
    const capital = villages.find(v => v.isMainVillage) || null;
    const attacks = [];
    for (const village of villages) {
        const id = String(village.villageId);
        const collection = Troops.getCollection(Troops.COLLECTION_MOVING, id);
        for (const row of (collection && collection.data) || []) {
            if (!row || typeof row.getMovementGroup !== "function"
                || row.getMovementGroup(Number(id)) !== "incoming_attacks") continue;
            const data = row.data || {};
            const movement = data.movement || {};
            const source = String(movement.villageIdStart || data.villageIdSupply || "");
            const hideout = source ? Village.get(source) : null;
            const info = (hideout && hideout.data) || {};
            const robber = Number(data.playerId) === -1
                || String(movement.playerNameStart) === "Robber"
                || Number(info.type) === 5;
            if (!robber) continue;
            attacks.push({
                movement_id: String(movement.troopId || data.troopId || ""),
                target_village_id: String(movement.villageIdTarget || id),
                target_village_name: String(movement.villageNameTarget || village.name || ""),
                hideout_id: source,
                hideout_cell: info.position ? String(info.position) : null,
                hideout_type: Number(info.type) || null,
                hideout_name: String(movement.villageNameStart || info.name || "Kryjówka"),
                hideout_x: info.coordinates ? Number(info.coordinates.x) : null,
                hideout_y: info.coordinates ? Number(info.coordinates.y) : null,
                arrival: Number(movement.timeFinish) || 0,
                movement_type: Number(movement.movementType) || 0
            });
        }
    }
    return {
        capital_id: capital ? String(capital.villageId) : null,
        capital_name: capital ? String(capital.name) : null,
        attacks
    };
})()"""

_READ_HIDEOUT_JS = r"""((cellId, fresh) => new Promise(resolve => {
    const injector = angular.element(document.body).injector();
    const rootScope = injector.get("$rootScope");
    const details = MapDetails.get(cellId);
    const before = details.lastFilled;
    if (fresh) Cache.J(details, 0);  // force the server to send current data
    if (!rootScope.$$phase) rootScope.$apply();
    const villages = injector.get("VillageService").U().data.map(v => v.data);
    const tribe = Number(Player.get(String(villages[0].playerId)).data.tribeId);
    const troopConfig = config.balancing.troopConfig || {};
    const carry = {};
    for (let nr = 1; nr <= 10; nr++) {
        const unit = troopConfig[String((tribe - 1) * 10 + nr)];
        carry[nr] = unit ? Number(unit.carry) || 0 : 0;
    }
    let tries = 0;
    const done = () => {
        const info = details && details.data && details.data.npcInfo;
        const stale = fresh && details.lastFilled === before;
        if ((!info || stale) && tries++ < 25) { setTimeout(done, 200); return; }
        const troops = (info && info.troops) || {};
        resolve({
            found: Boolean(info),
            refreshed: !fresh || details.lastFilled !== before,
            loot: info && info.loot || null,
            treasures: info ? Number(info.treasures) || 0 : 0,
            garrison: troops.units || {},
            carry
        });
    };
    done();
}))"""


# ---------------------------------------------------------------------------
# Pure planning helpers
# ---------------------------------------------------------------------------

def _float(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def hideout_display_name(name) -> str:
    """Map "Haydut sığınağı II" (any language) to "Kryjówka II"."""
    match = re.search(r"(?:^|\s)([IVX]+)\s*$", str(name or ""))
    return f"Kryjówka {match.group(1)}" if match else "Kryjówka"


def share_of_units(home: dict, percents: dict) -> dict[int, int]:
    """Floor of ``percent`` % of each unit type at home (hero excluded)."""
    units: dict[int, int] = {}
    for unit, percent in (percents or {}).items():
        nr = int(unit)
        if nr == HERO or not 0 < int(percent) <= 100:
            continue
        available = int(home.get(str(nr), home.get(nr, 0)) or 0)
        count = available * int(percent) // 100
        if count > 0:
            units[nr] = count
    return units


def fit_to_loot(available: dict[int, int], carry: dict, loot: int) -> dict[int, int]:
    """Smallest selection (largest carriers first) that can carry *loot*.

    *available* is already limited by the configured percentages.
    """
    units: dict[int, int] = {}
    remaining = max(0, int(loot))
    order = sorted(
        (unit for unit in available if float(carry.get(str(unit), carry.get(unit, 0)) or 0) > 0),
        key=lambda unit: -float(carry.get(str(unit), carry.get(unit, 0))),
    )
    for unit in order:
        if remaining <= 0:
            break
        unit_carry = float(carry.get(str(unit), carry.get(unit, 0)))
        count = min(available[unit], math.ceil(remaining / unit_carry))
        if count > 0:
            units[unit] = count
            remaining -= int(count * unit_carry)
    return units


def loot_total(loot: dict | None) -> int:
    return sum(max(0, int(float(value))) for value in (loot or {}).values())


def travel_seconds(units: dict[int, int], durations: dict) -> int:
    """Travel time of the slowest unit in *units* (hero included)."""
    slowest = 0
    for unit, count in units.items():
        if count <= 0:
            continue
        duration = durations.get(str(unit), durations.get(unit))
        if duration is None:
            raise ValueError(f"Gra nie podała czasu przemarszu dla jednostki {unit}.")
        slowest = max(slowest, int(duration))
    if slowest <= 0:
        raise ValueError("Nie udało się ustalić czasu przemarszu.")
    return slowest


def can_siege(units: dict[int, int]) -> bool:
    """A siege needs rams and at least 1000 units (hero not counted)."""
    total = sum(count for unit, count in units.items() if unit != HERO)
    return units.get(RAM, 0) > 0 and total >= SIEGE_MIN_UNITS


def choose_counter_movement(
    units: dict[int, int],
    siege_enabled: bool,
    now: float,
    siege_travel: int | None,
    deadline: float,
) -> tuple[int, str]:
    """Return (movement type, reason) for the counter-attack."""
    if not siege_enabled:
        return MOVEMENT_ATTACK, ""
    if not can_siege(units):
        return MOVEMENT_ATTACK, "oblężenie wymaga taranów i co najmniej 1000 jednostek"
    if siege_travel is None or now + siege_travel > deadline:
        return MOVEMENT_ATTACK, "oblężenie nie zdąży przed atakiem zbójów"
    return MOVEMENT_SIEGE, ""


def plan_raid(
    counter_arrival: float,
    delay_seconds: int,
    travel: int,
    now: float,
    deadline: float,
) -> tuple[float | None, float | None, str]:
    """Return (departure, arrival, reason); ``None`` when the raid is skipped.

    The raid lands *delay_seconds* after the counter-attack, or as soon as
    possible after that; it never lands after the robbers hit the player.
    """
    wanted = counter_arrival + max(0, int(delay_seconds))
    departure = max(now, wanted - travel)
    arrival = departure + travel
    if arrival > deadline:
        return None, None, "grabież nie zdąży przed atakiem zbójów — wojsko zostaje do obrony"
    return departure, arrival, ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class HideoutCase:
    hideout_id: str
    hideout_cell: str | None
    hideout_name: str
    target_village_name: str
    deadline: float
    movement_ids: set[str] = field(default_factory=set)
    status: str = STATUS_DETECTED
    message: str = ""
    counter_units: dict[int, int] = field(default_factory=dict)
    counter_movement: int = MOVEMENT_ATTACK
    counter_travel: int = 0
    counter_arrival: float | None = None
    counter_batch: str | None = None
    counter_fired_at: float | None = None
    hero_recalled: bool = False
    raid_units: dict[int, int] = field(default_factory=dict)
    raid_departure: float | None = None
    raid_arrival: float | None = None
    raid_batch: str | None = None
    raid_fired_at: float | None = None
    raid_message: str = ""
    loot: int | None = None
    raids_sent: int = 0
    hideout_gone: bool = False
    raid_return_at: float | None = None
    garrison: dict[int, int] = field(default_factory=dict)

    @property
    def target_id(self) -> str:
        return self.hideout_cell or self.hideout_id

    @property
    def finished(self) -> bool:
        return self.status in FINAL_STATUSES


class HideoutDefense:
    def __init__(
        self,
        bridge,
        settings_getter: Callable[[], object],
        log: Callable[[str], None],
        mark_handled: Callable[[set[str]], None],
        clock: Callable[[], float] = time.time,
        set_active: Callable[[str, dict | None], None] | None = None,
    ):
        self.bridge = bridge
        self._settings = settings_getter
        self._log = log
        self._mark_handled = mark_handled
        self._set_active = set_active or (lambda _cell, _info: None)
        self._recovered = False
        self._clock = clock
        self._lock = threading.RLock()
        self.cases: list[HideoutCase] = []
        self.context: dict | None = None
        self.capital_id: str | None = None
        self.capital_name: str | None = None
        self.server_offset_ms = 0.0
        self.last_scan: float | None = None
        self.last_error: str | None = None
        self._durations: dict[tuple[str, int], dict] = {}
        self._carry: dict = {}

    @property
    def settings(self):
        return self._settings()

    def server_now(self) -> float:
        return self._clock() - self.server_offset_ms / 1000.0

    # ------------------------------------------------------------------ reads
    def _read_context(self) -> dict:
        context = self.bridge.read_context()
        self.context = context
        self.server_offset_ms = float(context.get("server_offset_ms") or 0.0)
        return context

    def _home_units(self) -> dict:
        village = (self.context or {}).get("villages", {}).get(self.capital_id or "", {})
        return dict(village.get("units") or {})

    def _hero_home(self) -> bool:
        village = (self.context or {}).get("villages", {}).get(self.capital_id or "", {})
        return bool(village.get("hero_home"))

    def _durations_for(self, target: str, movement: int) -> dict:
        key = (target, movement)
        if key not in self._durations:
            result = self.bridge.check_target(self.capital_id, target, movement)
            self._durations[key] = result["durations"]
        return self._durations[key]

    def _read_attacks(self) -> list[dict]:
        value = self.bridge._evaluate(_READ_ROBBER_ATTACKS_JS)
        if not isinstance(value, dict):
            raise RuntimeError("Gra nie zwróciła listy ruchów wojsk.")
        self.capital_id = value.get("capital_id")
        self.capital_name = value.get("capital_name")
        return list(value.get("attacks") or [])

    # ------------------------------------------------------------------- tick
    def tick(self) -> None:
        with self._lock:
            settings = self.settings
            now_local = time.monotonic()
            busy = any(
                case.status in (STATUS_COUNTER_FIRED, STATUS_RAID_FIRED, STATUS_WAITING_HERO)
                or (case.status == STATUS_RAID_RETURNING
                    and case.raid_return_at is not None
                    and case.raid_return_at - self.server_now() < 15)
                or (case.status == STATUS_RAID_SCHEDULED
                    and case.raid_departure is not None
                    and case.raid_departure - self.server_now() < 15)
                for case in self.cases
            )
            if not busy and self.last_scan is not None and now_local - self.last_scan < SCAN_INTERVAL_SECONDS:
                return
            self.last_scan = now_local
            try:
                self.bridge.keep_awake()
                attacks = self._read_attacks()
                self._read_context()
                self.last_error = None
            except Exception as error:
                self.last_error = str(error)
                return
            self._register(attacks, set(settings.handled_movements))
            self._robbers_vanished(attacks)
            if settings.enabled and not self._recovered and self.capital_id:
                self._recovered = True
                self._recover(attacks)
            for case in self.cases:
                if case.loot is None and not case.finished:
                    try:
                        self.read_hideout(case)
                    except Exception:
                        pass
            if not settings.enabled:
                return
            for case in list(self.cases):
                if case.finished:
                    continue
                try:
                    self._advance(case)
                except Exception as error:
                    case.status = STATUS_FAILED
                    case.message = f"Błąd: {error}"
                    self._log(f"{case.hideout_name}: {case.message}")
                    self._mark_handled(case.movement_ids)
                if case.finished and case.hideout_cell in settings.active_hideouts:
                    self._set_active(case.hideout_cell, None)

    def _robbers_vanished(self, attacks: list[dict]) -> None:
        """A robber attack disappears when the counter-attack destroyed them."""
        current = {str(attack.get("movement_id")) for attack in attacks}
        now = self.server_now()
        for case in self.cases:
            if case.finished or not case.movement_ids or case.deadline <= now + 5:
                continue
            if case.movement_ids & current:
                continue
            case.deadline = now
            self._log(
                f"{case.hideout_name}: atak zbójów zniknął — ich wojsko rozbite, "
                "grabię kryjówkę bez limitu czasu."
            )

    def _recover(self, attacks: list[dict]) -> None:
        """Resume looting hideouts remembered from before a restart."""
        for cell, info in dict(self.settings.active_hideouts).items():
            if any(case.hideout_cell == cell and not case.finished for case in self.cases):
                continue
            arrivals = [
                float(attack["arrival"]) for attack in attacks
                if str(attack.get("hideout_cell")) == cell
            ]
            case = HideoutCase(
                hideout_id=str(info.get("id") or cell),
                hideout_cell=cell,
                hideout_name=hideout_display_name(info.get("name")),
                target_village_name="",
                deadline=min(arrivals) if arrivals else 0.0,
                status=STATUS_RAID_RETURNING,
                counter_arrival=_float(info.get("counter_arrival")),
                message="Wznowiono grabienie po restarcie aplikacji.",
            )
            self.cases.append(case)
            self._log(f"{case.hideout_name}: {case.message}")

    def _register(self, attacks: list[dict], handled: set[str]) -> None:
        for attack in attacks:
            movement_id = attack.get("movement_id") or ""
            if not movement_id or movement_id in handled:
                continue
            if any(movement_id in case.movement_ids for case in self.cases):
                continue
            hideout_id = str(attack.get("hideout_id") or "")
            open_case = next(
                (case for case in self.cases
                 if case.hideout_id == hideout_id and not case.finished),
                None,
            )
            if open_case is not None:
                open_case.movement_ids.add(movement_id)
                open_case.deadline = min(open_case.deadline, float(attack["arrival"]))
                if attack.get("hideout_cell") and not open_case.hideout_cell:
                    open_case.hideout_cell = str(attack["hideout_cell"])
                continue
            case = HideoutCase(
                hideout_id=hideout_id,
                hideout_cell=attack.get("hideout_cell"),
                hideout_name=hideout_display_name(attack.get("hideout_name")),
                target_village_name=str(attack.get("target_village_name") or ""),
                deadline=float(attack["arrival"]),
                movement_ids={movement_id},
            )
            self.cases.append(case)
            self._log(
                f"Atak zbójów z „{case.hideout_name}” na {case.target_village_name} "
                f"(dotarcie {_clock_text(case.deadline)})."
            )
        # Forget finished cases whose robbers already hit, keep the table short.
        now = self.server_now()
        self.cases = [
            case for case in self.cases
            if not case.finished or case.deadline > now - 3600
        ][-30:]

    def _advance(self, case: HideoutCase) -> None:
        if not self.capital_id:
            raise RuntimeError("Nie znaleziono stolicy.")
        if not case.target_id.isdigit():
            # Hideout position not loaded yet; the game fills it shortly.
            case.message = "Czekam na położenie kryjówki."
            return
        if case.status in (STATUS_DETECTED, STATUS_WAITING_HERO):
            self._send_counter(case)
        elif case.status == STATUS_COUNTER_FIRED:
            self._verify_counter(case)
        elif case.status == STATUS_RAID_SCHEDULED:
            self._send_raid(case)
        elif case.status == STATUS_RAID_FIRED:
            self._verify_raid(case)
        elif case.status == STATUS_RAID_RETURNING:
            self._repeat_raid(case)

    # ---------------------------------------------------------------- counter
    def _send_counter(self, case: HideoutCase) -> None:
        settings = self.settings
        now = self.server_now()
        if now >= case.deadline:
            self._skip(case, "atak zbójów już dotarł")
            return
        if case.loot is None:
            try:
                self.read_hideout(case)
            except Exception:
                pass
        units = share_of_units(self._home_units(), settings.counter_units)
        attack_durations = self._durations_for(case.target_id, MOVEMENT_ATTACK)
        with_hero = False
        if settings.send_hero:
            if self._hero_home():
                with_hero = True
            elif settings.wait_for_hero:
                if self._wait_for_hero(case, units, attack_durations, now):
                    return
            else:
                case.message = "Bohatera nie ma w stolicy — idzie bez niego."
        if with_hero:
            units[HERO] = 1
        if not units:
            self._skip(case, "brak wojsk do kontrataku w stolicy")
            return
        siege_travel = None
        if settings.siege_enabled and can_siege(units):
            siege_travel = travel_seconds(units, self._durations_for(case.target_id, MOVEMENT_SIEGE))
        movement, reason = choose_counter_movement(
            units, settings.siege_enabled, now, siege_travel, case.deadline,
        )
        travel = siege_travel if movement == MOVEMENT_SIEGE else travel_seconds(units, attack_durations)
        catapults = ()
        if units.get(CATAPULT):
            village = self.context["villages"].get(self.capital_id, {})
            if int(village.get("rally_point_level") or 0) >= CATAPULT_TARGET_MIN_RALLY_POINT:
                catapults = (RANDOM_TARGET,)
        request = self.bridge.send_request(
            self.capital_id, case.target_id, movement, units, catapults,
        )
        if movement == MOVEMENT_SIEGE:
            request["fallback"] = self.bridge.send_request(
                self.capital_id, case.target_id, MOVEMENT_ATTACK, units, catapults,
            )["params"]
        batch_id, result = self.bridge.fire([request], now + 30)
        if result.get("status") != "fired":
            raise RuntimeError("gra nie przyjęła wysyłki")
        case.counter_units = units
        case.counter_movement = movement
        case.counter_travel = int(travel)
        case.counter_batch = batch_id
        case.counter_fired_at = float(result.get("server_time") or now)
        case.counter_arrival = case.counter_fired_at + travel
        case.status = STATUS_COUNTER_FIRED
        kind = "Oblężenie" if movement == MOVEMENT_SIEGE else "Atak"
        case.message = f"{kind}: {self._describe(units)}" + (f" ({reason})" if reason else "")

    def _wait_for_hero(self, case, units, durations, now) -> bool:
        """Recall the hero once and wait; ``False`` once waiting is pointless."""
        hero = (self.context or {}).get("hero") or {}
        probe = dict(units)
        probe[HERO] = 1
        latest = case.deadline - travel_seconds(probe, durations)
        if now >= latest:
            case.message = "Bohater nie wrócił na czas — idzie bez niego."
            return False
        if str(hero.get("home_village_id")) != str(self.capital_id):
            case.message = "Bohater nie należy do stolicy — idzie bez niego."
            return False
        if not case.hero_recalled and hero.get("support_troop_id"):
            request = self.bridge.recall_request(int(hero["support_troop_id"]))
            self.bridge.fire([request], now + 30)
            case.hero_recalled = True
            self._log(f"{case.hideout_name}: wycofuję bohatera do stolicy.")
        case.status = STATUS_WAITING_HERO
        case.message = f"Czekam na bohatera (najpóźniej do {_clock_text(latest)})."
        return True

    def _verify_counter(self, case: HideoutCase) -> None:
        results = self._fire_results(case.counter_batch, case.counter_fired_at)
        if results is None:
            return
        result = results[0] if results else None
        if not result or not result.get("ok"):
            error = (result or {}).get("error") or "brak odpowiedzi"
            case.status = STATUS_FAILED
            case.message = f"Gra odrzuciła kontratak: {error}"
            self._log(f"{case.hideout_name}: {case.message}")
            self._mark_handled(case.movement_ids)
            return
        if result.get("fallback") and case.counter_movement == MOVEMENT_SIEGE:
            case.counter_movement = MOVEMENT_ATTACK
            durations = self._durations_for(case.target_id, MOVEMENT_ATTACK)
            case.counter_travel = travel_seconds(case.counter_units, durations)
            case.message += " — gra nie przyjęła oblężenia, poszedł atak"
        case.counter_arrival = self._movement_arrival(
            case, (MOVEMENT_ATTACK, MOVEMENT_SIEGE), case.counter_fired_at,
            case.counter_travel,
        )
        self._log(
            f"{case.hideout_name}: kontratak ze stolicy wysłany, dotrze "
            f"{_clock_text(case.counter_arrival)}. {case.message}"
        )
        self._mark_handled(case.movement_ids)
        if not self.settings.raid_enabled:
            case.status = STATUS_DONE
            return
        if self.settings.raid_repeat and case.hideout_cell:
            self._set_active(case.hideout_cell, {
                "id": case.hideout_id,
                "name": case.hideout_name,
                # Raids must never land before the counter-attack, also
                # after a restart.
                "counter_arrival": f"{float(case.counter_arrival or 0):.3f}",
            })
        self._schedule_raid(case)

    # ------------------------------------------------------------------- raid
    def read_hideout(self, case: HideoutCase, fresh: bool = False) -> dict | None:
        if not case.hideout_cell or not str(case.hideout_cell).isdigit():
            return None
        value = self.bridge._evaluate(
            f"{_READ_HIDEOUT_JS}({int(case.hideout_cell)}, {'true' if fresh else 'false'})",
            await_promise=True, timeout=12,
        )
        if isinstance(value, dict) and fresh and value.get("refreshed") and not value.get("found"):
            case.hideout_gone = True
        if not isinstance(value, dict) or not value.get("found"):
            return None
        case.loot = loot_total(value.get("loot"))
        self._carry = value.get("carry") or self._carry
        case.garrison = {
            int(unit): int(count) for unit, count in (value.get("garrison") or {}).items()
            if re.fullmatch(r"\d+", str(unit)) and int(count) > 0
        }
        return value

    def _raid_plan(self, case: HideoutCase) -> tuple[dict[int, int], int | None]:
        units = share_of_units(self._home_units(), self.settings.raid_units)
        if units and self.settings.raid_fit_loot:
            hideout = self.read_hideout(case) if case.raids_sent == 0 else {"carry": self._carry}
            if hideout is not None:
                units = fit_to_loot(units, hideout.get("carry") or {}, case.loot or 0)
        if not units:
            return units, None
        durations = self._durations_for(case.target_id, MOVEMENT_RAID)
        return units, travel_seconds(units, durations)

    def _schedule_raid(self, case: HideoutCase) -> None:
        units, travel = self._raid_plan(case)
        if not units:
            case.status = STATUS_DONE
            case.raid_message = (
                "Grabież: kryjówka nie ma łupu." if self.settings.raid_fit_loot and case.loot == 0
                else "Grabież: brak wybranych wojsk w stolicy."
            )
            self._log(f"{case.hideout_name}: {case.raid_message}")
            return
        departure, arrival, reason = plan_raid(
            case.counter_arrival, self.settings.raid_delay_seconds, travel,
            self.server_now(), self._raid_deadline(case),
        )
        if departure is None:
            case.status = STATUS_DONE
            case.raid_message = f"Grabież pominięta: {reason}."
            self._log(f"{case.hideout_name}: {case.raid_message}")
            return
        case.raid_units = units
        case.raid_departure = departure
        case.raid_arrival = arrival
        case.status = STATUS_RAID_SCHEDULED
        case.raid_message = (
            f"Grabież wyjdzie {_clock_text(departure)}, dotrze {_clock_text(arrival)}."
        )
        self._log(f"{case.hideout_name}: {case.raid_message}")

    def _send_raid(self, case: HideoutCase) -> None:
        now = self.server_now()
        if case.raid_departure is not None and now < case.raid_departure:
            return
        units, travel = self._raid_plan(case)
        if not units:
            case.status = STATUS_DONE
            case.raid_message = "Grabież: w chwili wysyłki brak wybranych wojsk w stolicy."
            self._log(f"{case.hideout_name}: {case.raid_message}")
            return
        wanted = case.counter_arrival + max(0, int(self.settings.raid_delay_seconds))
        if now + travel < wanted:
            # Different (faster) units are home now: wait so it lands after.
            case.raid_departure = wanted - travel
            return
        if now + travel > self._raid_deadline(case):
            case.status = STATUS_DONE
            case.raid_message = (
                "Grabież pominięta: nie zdąży przed atakiem zbójów — wojsko zostaje do obrony."
            )
            self._log(f"{case.hideout_name}: {case.raid_message}")
            return
        request = self.bridge.send_request(self.capital_id, case.target_id, MOVEMENT_RAID, units)
        batch_id, result = self.bridge.fire([request], self._raid_deadline(case) - travel)
        if result.get("status") != "fired":
            case.status = STATUS_DONE
            case.raid_message = "Grabież pominięta: za późno w chwili wysyłki."
            self._log(f"{case.hideout_name}: {case.raid_message}")
            return
        case.raid_units = units
        case.raid_batch = batch_id
        case.raid_fired_at = float(result.get("server_time") or now)
        case.raid_arrival = case.raid_fired_at + travel
        case.status = STATUS_RAID_FIRED

    def _verify_raid(self, case: HideoutCase) -> None:
        results = self._fire_results(case.raid_batch, case.raid_fired_at)
        if results is None:
            return
        result = results[0] if results else None
        if not result or not result.get("ok"):
            error = (result or {}).get("error") or "brak odpowiedzi"
            case.status = STATUS_FAILED
            case.raid_message = f"Gra odrzuciła grabież: {error}"
        else:
            travel = int((case.raid_arrival or 0) - (case.raid_fired_at or 0))
            case.raid_arrival = self._movement_arrival(
                case, (MOVEMENT_RAID,), case.raid_fired_at, travel,
            )
            case.raids_sent += 1
            number = f" {case.raids_sent}" if self.settings.raid_repeat else ""
            case.raid_message = (
                f"Grabież{number} wysłana: {self._describe(case.raid_units)}, dotrze "
                f"{_clock_text(case.raid_arrival)}."
            )
            if self.settings.raid_repeat and case.raids_sent < MAX_RAIDS:
                case.status = STATUS_RAID_RETURNING
                case.raid_return_at = case.raid_arrival + 2
                case.raid_message += " Po jej dotarciu sprawdzę resztę łupu."
            else:
                case.status = STATUS_DONE
        self._log(f"{case.hideout_name}: {case.raid_message}")

    def _raid_deadline(self, case: HideoutCase) -> float:
        """Raids must land before the robbers hit; afterwards no limit."""
        now = self.server_now()
        return case.deadline if now < case.deadline else math.inf

    def _repeat_raid(self, case: HideoutCase) -> None:
        """Keep raiding an emptied hideout until its loot is gone."""
        now = self.server_now()
        if case.raid_return_at is not None and now < case.raid_return_at:
            return
        counter_landed = (case.counter_arrival or 0) + RETURN_MARGIN_SECONDS
        if now < counter_landed:
            case.raid_return_at = counter_landed
            case.raid_message = (
                f"Czekam na wejście kontrataku ({_clock_text(case.counter_arrival)})."
            )
            return
        movements = self._capital_movements()
        if movements is None:
            # Right after connecting the game may not have loaded the troop
            # movements yet; an empty list must not be read as "nothing out".
            case.raid_return_at = now + LOOT_CHECK_INTERVAL_SECONDS
            case.raid_message = "Czekam, aż gra wczyta ruchy wojsk."
            return
        targets = {case.hideout_id, str(case.hideout_cell or "")}
        outgoing = [m for m in movements if str(m.get("target")) in targets]
        returning = [m for m in movements if str(m.get("source")) in targets]
        if outgoing:
            case.raid_return_at = max(float(m.get("arrival") or 0) for m in outgoing) + 2
            case.raid_message = (
                f"Czekam, aż wojska dotrą do kryjówki ({_clock_text(case.raid_return_at)})."
            )
            return
        hideout = self.read_hideout(case, fresh=True)
        if case.hideout_gone:
            self._finish_looting(case, "Kryjówka zniknęła z mapy — cały łup zebrany.")
            return
        if hideout is None or not hideout.get("refreshed"):
            case.raid_return_at = now + LOOT_CHECK_INTERVAL_SECONDS
            case.raid_message = "Nie udało się odświeżyć kryjówki — spróbuję ponownie."
            return
        if (case.loot or 0) < MIN_LOOT_FOR_RAID:
            self._finish_looting(case, f"Łup zebrany ({case.raids_sent} grabieży).")
            return
        if case.garrison:
            self._finish_looting(
                case,
                f"W kryjówce znów jest obsada ({self._describe(case.garrison, robber=True)}) "
                f"— kończę grabieże. Zostało {case.loot} surowców.",
            )
            return
        if case.raids_sent >= MAX_RAIDS:
            self._finish_looting(case, f"Limit {MAX_RAIDS} grabieży. Zostało {case.loot} surowców.")
            return
        units, travel = self._raid_plan(case)
        if not units:
            if returning:
                case.raid_return_at = (
                    min(float(m.get("arrival") or 0) for m in returning) + RETURN_MARGIN_SECONDS
                )
                case.raid_message = (
                    f"Zostało {case.loot} surowców — czekam na powrót wojsk "
                    f"({_clock_text(case.raid_return_at)})."
                )
                return
            self._finish_looting(
                case, f"Brak wybranych wojsk w stolicy. Zostało {case.loot} surowców."
            )
            return
        if now < case.deadline and now + travel > case.deadline:
            # Keep the troops home for the robber attack, raid after it.
            case.raid_return_at = case.deadline + RETURN_MARGIN_SECONDS
            case.raid_message = (
                f"Zostało {case.loot} surowców — kolejna grabież po ataku "
                f"zbójów ({_clock_text(case.raid_return_at)})."
            )
            return
        case.raid_return_at = None
        self._schedule_raid(case)

    def _capital_movements(self) -> list[dict] | None:
        """Movements of the capital, or ``None`` while the game has not loaded them."""
        value = self.bridge._evaluate(
            "((villageId) => { const c = Troops.getCollection('moving', villageId);"
            " if (!c || !c.filled) return null;"
            " return (c.data || []).map(row => { const d = row.data || {};"
            " const m = d.movement || {}; return {"
            " source: String(m.villageIdStart || ''), target: String(m.villageIdTarget || ''),"
            " movement_type: Number(m.movementType), arrival: Number(m.timeFinish) || 0}; }); })"
            f"({json.dumps(str(self.capital_id))})"
        )
        return value if isinstance(value, list) else None

    def _finish_looting(self, case: HideoutCase, message: str) -> None:
        case.status = STATUS_DONE
        case.raid_message = message
        self._log(f"{case.hideout_name}: {message}")
        if case.hideout_cell:
            self._set_active(case.hideout_cell, None)

    # ---------------------------------------------------------------- helpers
    def _fire_results(self, batch_id: str | None, fired_at: float | None) -> list | None:
        fire = self.bridge.read_fire(batch_id) if batch_id else None
        results = (fire or {}).get("results") or []
        pending = not results or any(result is None for result in results)
        if pending and self.server_now() < (fired_at or 0) + VERIFY_TIMEOUT_SECONDS:
            return None
        return results

    def _movement_arrival(self, case, types, fired_at, travel) -> float:
        estimate = float(fired_at or self.server_now()) + int(travel)
        try:
            movements = self.bridge.read_movements(self.capital_id)
        except Exception:
            return estimate
        targets = {case.hideout_id, str(case.hideout_cell or "")}
        matching = [
            float(movement["arrival"]) for movement in movements
            if str(movement.get("target")) in targets
            and movement.get("movement_type") in types
            and abs(float(movement.get("arrival") or 0) - estimate) <= 5
        ]
        return min(matching) if matching else estimate

    def _skip(self, case: HideoutCase, reason: str) -> None:
        case.status = STATUS_SKIPPED
        case.message = f"Pominięto: {reason}."
        self._log(f"{case.hideout_name}: {case.message}")
        self._mark_handled(case.movement_ids)

    def _describe(self, units: dict[int, int], robber: bool = False) -> str:
        # Robbers are Romans (tribe 1) in every hideout seen so far.
        tribe = 1 if robber else int((self.context or {}).get("tribe") or 0)
        return describe_units(units, tribe)

    def tribe(self) -> int:
        return int((self.context or {}).get("tribe") or 0)

    def rows(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "hideout": case.hideout_name,
                    "target": case.target_village_name,
                    "deadline": case.deadline,
                    "counter_arrival": case.counter_arrival,
                    "raid_arrival": case.raid_arrival,
                    "status_key": case.status,
                    "status": STATUS_LABELS[case.status],
                    "loot": case.loot,
                    "garrison": self._describe(case.garrison, robber=True) if case.garrison else "",
                    "message": "  ".join(
                        part for part in (case.message, case.raid_message) if part
                    ),
                }
                for case in reversed(self.cases)
            ]


def _clock_text(server_time: float | None) -> str:
    if server_time is None or not math.isfinite(server_time):
        return "—"
    return time.strftime("%H:%M:%S", time.localtime(server_time))


__all__ = [
    "HideoutDefense",
    "HideoutCase",
    "share_of_units",
    "travel_seconds",
    "can_siege",
    "choose_counter_movement",
    "plan_raid",
    "fit_to_loot",
    "hideout_display_name",
    "loot_total",
    "MOVEMENT_RAID",
]
