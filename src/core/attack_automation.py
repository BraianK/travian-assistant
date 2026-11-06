"""Runtime engine that executes TK-Tools attack plans.

``AttackAutomation.tick()`` is called about once per second from the GUI.
It refreshes the plan, previews travel durations, composes every wave shortly
before departure and hands the actual send to a precise background timer so
the request reaches the server in the middle of the planned second.

Timing model
------------
The game knows the offset between the local clock and the server clock
(``server = local - offset``); this PC can be tens of seconds off, so all
scheduling uses server time. The departure is recomputed from the game's
travel durations so the *arrival* from the plan is kept. A request is aimed
at ``departure + 0.5 s`` minus half the measured round trip; if it cannot be
processed before ``departure + 2 s`` (one second late, as allowed) the send is
skipped and reported to TK-Tools instead.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

try:
    from src.core.attack_operations import (
        CATAPULT,
        CHIEF,
        HERO,
        KIND_FAKE,
        LATE_TOLERANCE_SECONDS,
        MOVEMENT_ATTACK,
        MOVEMENT_SIEGE,
        MOVEMENT_SUPPORT,
        OFFENSIVE_UNITS,
        RAM,
        SIEGE_UNIT_BY_MOVE_SPEED,
        SUPPORTED_KINDS,
        FakeRule,
        PlanError,
        PlannedAttack,
        VillageArmy,
        can_park_hero,
        compose_attack,
        describe_units,
        exact_departure,
        hero_recall_time,
        later_sends,
        nearest_village,
        parse_planner,
    )
except ModuleNotFoundError:  # pragma: no cover - script fallback
    from core.attack_operations import (
        CATAPULT,
        CHIEF,
        HERO,
        KIND_FAKE,
        LATE_TOLERANCE_SECONDS,
        MOVEMENT_ATTACK,
        MOVEMENT_SIEGE,
        MOVEMENT_SUPPORT,
        OFFENSIVE_UNITS,
        RAM,
        SIEGE_UNIT_BY_MOVE_SPEED,
        SUPPORTED_KINDS,
        FakeRule,
        PlanError,
        PlannedAttack,
        VillageArmy,
        can_park_hero,
        compose_attack,
        describe_units,
        exact_departure,
        hero_recall_time,
        later_sends,
        nearest_village,
        parse_planner,
    )

logger = logging.getLogger(__name__)

STATUS_WAITING = "waiting"
STATUS_PREPARED = "prepared"
STATUS_FIRED = "fired"
STATUS_SENT = "sent"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_DONE_ELSEWHERE = "done_elsewhere"
FINAL_STATUSES = {
    STATUS_SENT, STATUS_PARTIAL, STATUS_FAILED, STATUS_SKIPPED, STATUS_DONE_ELSEWHERE,
}

STATUS_LABELS = {
    STATUS_WAITING: "Oczekuje",
    STATUS_PREPARED: "Przygotowany",
    STATUS_FIRED: "Wysłano — sprawdzam",
    STATUS_SENT: "Wysłany",
    STATUS_PARTIAL: "Wysłany częściowo",
    STATUS_FAILED: "Nie wysłano",
    STATUS_SKIPPED: "Pominięty",
    STATUS_DONE_ELSEWHERE: "Zgłoszony w TK-Tools",
}

AIM_FRACTION = 0.5
VERIFY_DELAY_SECONDS = 3.0
PREVIEW_TTL_SECONDS = 600


@dataclass
class AttackRun:
    attack: PlannedAttack
    status: str = STATUS_WAITING
    message: str = ""
    preview_departure: float | None = None
    exact_departure: float | None = None
    sends: list = field(default_factory=list)
    requests: list = field(default_factory=list)
    fire_server_time: float | None = None
    batch_id: str | None = None
    fired_at: float | None = None
    reported: bool = False
    hero_recall_scheduled: bool = False
    tribe: int = 0
    deny_reason: str | None = None

    @property
    def departure(self) -> float:
        return self.exact_departure or self.preview_departure or self.attack.departure

    @property
    def finished(self) -> bool:
        return self.status in FINAL_STATUSES


class PreciseTimer:
    """Run callbacks at an exact local time on a background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def schedule(self, at_local: float, callback: Callable[[], None]) -> None:
        def run():
            while True:
                remaining = at_local - time.time()
                if remaining <= 0:
                    break
                time.sleep(min(remaining - 0.02, 0.5) if remaining > 0.03 else 0.0005)
            try:
                callback()
            except Exception:  # pragma: no cover - logged for diagnosis
                logger.exception("Scheduled attack action failed")

        thread = threading.Thread(target=run, daemon=True, name="attack-timer")
        with self._lock:
            self._threads = [item for item in self._threads if item.is_alive()]
            self._threads.append(thread)
        thread.start()


class AttackAutomation:
    def __init__(
        self,
        bridge,
        tktools,
        settings_provider: Callable[[], object],
        log: Callable[[str], None],
        *,
        clock: Callable[[], float] = time.time,
        timer: PreciseTimer | None = None,
        click_executor=None,
    ):
        self.bridge = bridge
        self.tktools = tktools
        self._settings = settings_provider
        self._log = log
        self._clock = clock
        self._timer = timer or PreciseTimer()
        self.click_executor = click_executor
        self._lock = threading.RLock()
        self.runs: dict[str, AttackRun] = {}
        self.context: dict | None = None
        self.server_offset_ms: float = 0.0
        self.rtt_ms: float = 150.0
        self.last_refresh: float | None = None
        self.last_error: str | None = None
        self.plan_problems: list[str] = []
        self.pt_warnings: list[str] = []
        self._durations: dict[tuple, tuple[float, dict]] = {}
        self._pt_attempts: dict[str, float] = {}
        self._hero_action_after = 0.0
        self.hero_status_text = "—"

    # ------------------------------------------------------------------ time
    def server_now(self) -> float:
        return self._clock() - self.server_offset_ms / 1000.0

    def to_local(self, server_time: float) -> float:
        return server_time + self.server_offset_ms / 1000.0

    @property
    def settings(self):
        return self._settings()

    def fake_rule(self) -> FakeRule:
        settings = self.settings
        return FakeRule(
            tier2_at=datetime.strptime(settings.fake_tier2_at, "%Y-%m-%d %H:%M"),
            tier3_at=datetime.strptime(settings.fake_tier3_at, "%Y-%m-%d %H:%M"),
            base_units=settings.fake_base_units,
            tier2_units=settings.fake_tier2_units,
            tier3_units=settings.fake_tier3_units,
        )

    # --------------------------------------------------------------- context
    def read_context(self) -> dict:
        context = self.bridge.read_context()
        self.context = context
        self.server_offset_ms = float(context.get("server_offset_ms") or 0.0)
        return context

    def army(self, village_id: str) -> VillageArmy:
        context = self.context or {}
        village = (context.get("villages") or {}).get(str(village_id))
        if village is None:
            raise PlanError("Wioska z planu nie należy do aktywnego konta.")
        return VillageArmy(
            village_id=str(village_id),
            tribe=int(context["tribe"]),
            units={int(unit): int(count) for unit, count in village["units"].items()},
            hero_home=bool(village.get("hero_home")),
            rally_point_level=int(village.get("rally_point_level") or 0),
            tournament_square_level=int(village.get("tournament_square_level") or 0),
            unit_costs={
                int(unit): int(cost)
                for unit, cost in (context.get("unit_costs") or {}).items()
            },
        )

    def durations(self, source: str, target: str, movement: int, max_age: float = PREVIEW_TTL_SECONDS) -> dict:
        key = (str(source), str(target), int(movement))
        cached = self._durations.get(key)
        if cached and time.monotonic() - cached[0] < max_age:
            return cached[1]
        result = self.bridge.check_target(source, target, movement)
        self.rtt_ms = 0.7 * self.rtt_ms + 0.3 * float(result.get("rtt_ms") or self.rtt_ms)
        if result.get("server_offset_ms") is not None:
            self.server_offset_ms = float(result["server_offset_ms"])
        self._durations[key] = (time.monotonic(), result["durations"])
        return result["durations"]

    # ------------------------------------------------------------------ plan
    def refresh_plan(self) -> None:
        settings = self.settings
        self.last_refresh = time.monotonic()
        context = self.read_context()
        server_name = settings.server_name
        data = self.tktools.read_planner(server_name)
        attacks, problems = parse_planner(data["rows"], context["player_id"])
        self.plan_problems = problems
        frozen = {
            run.attack.operation_id
            for run in self.runs.values()
            if run.status != STATUS_WAITING
        }
        with self._lock:
            fresh: dict[str, AttackRun] = {}
            for run in self.runs.values():
                if run.attack.operation_id in frozen:
                    fresh[run.attack.id] = run
            for attack in attacks:
                if attack.operation_id in frozen and attack.id not in fresh:
                    continue
                if attack.id in fresh:
                    continue
                previous = self.runs.get(attack.id)
                run = AttackRun(attack=attack, tribe=int(context["tribe"]))
                if previous is not None and previous.status != STATUS_WAITING:
                    run = previous
                if not attack.is_pending and run.status == STATUS_WAITING:
                    run.status = STATUS_DONE_ELSEWHERE
                    run.message = f"Stan w TK-Tools: {attack.state}"
                unsupported = {wave.kind for wave in attack.waves} - SUPPORTED_KINDS
                if unsupported and run.status == STATUS_WAITING:
                    run.status = STATUS_SKIPPED
                    run.message = f"Nieobsługiwany typ: {', '.join(sorted(unsupported))}"
                fresh[attack.id] = run
            self.runs = fresh
        self._preview_all()
        self._check_tournament_squares()
        self.last_error = None

    def _expected_units(self, run: AttackRun) -> dict[int, int]:
        """Units that decide the speed of the attack (for previews)."""
        attack = run.attack
        units: dict[int, int] = {}
        try:
            army = self.army(attack.source_village_id)
        except PlanError:
            return units
        for wave in attack.waves:
            if wave.kind == KIND_FAKE:
                siege = SIEGE_UNIT_BY_MOVE_SPEED.get(attack.move_speed)
                if siege:
                    units[siege] = 1
                continue
            if wave.catapults:
                units[CATAPULT] = 1
            if wave.chiefs:
                units[CHIEF] = 1
            if wave.has_off:
                if army.available(RAM):
                    units[RAM] = 1
                for unit in OFFENSIVE_UNITS.get(army.tribe, ()):
                    if army.available(unit):
                        units[unit] = 1
            if wave.has_hero:
                units[HERO] = 1
        return units

    def _movement_for(self, attack: PlannedAttack) -> int:
        return MOVEMENT_SIEGE if attack.is_siege else MOVEMENT_ATTACK

    def _preview_all(self) -> None:
        for run in sorted(self.runs.values(), key=lambda item: item.attack.departure):
            if run.status != STATUS_WAITING:
                continue
            attack = run.attack
            if not attack.target_village_id.isdigit():
                run.status = STATUS_SKIPPED
                run.message = "Cel bez wioski (puste pole) — nieobsługiwany."
                continue
            try:
                durations = self.durations(
                    attack.source_village_id,
                    attack.target_village_id,
                    self._movement_for(attack),
                )
                units = self._expected_units(run) or {CATAPULT: 1}
                run.preview_departure = exact_departure(attack.arrival, units, durations)
                run.message = ""
            except Exception as error:
                run.message = f"Podgląd: {error}"

    def _check_tournament_squares(self) -> None:
        warnings: list[str] = []
        villages = (self.context or {}).get("villages") or {}
        required: dict[str, int] = {}
        for run in self.runs.values():
            if run.finished:
                continue
            source = run.attack.source_village_id
            required[source] = max(required.get(source, 0), run.attack.pt_level)
        for village_id, level in required.items():
            village = villages.get(village_id)
            if not village:
                continue
            current = int(village.get("tournament_square_level") or 0)
            if current >= level:
                continue
            warnings.append(
                f"{village['name']}: plan zakłada plac turniejowy {level}, "
                f"w wiosce jest {current}."
            )
            if self.settings.pt_policy != "upgrade":
                continue
            location = village.get("tournament_square_location")
            if not location:
                warnings[-1] += " Brak placu — zbuduj go ręcznie."
                continue
            last = self._pt_attempts.get(village_id)
            if last is not None and time.monotonic() - last < 600:
                continue
            self._pt_attempts[village_id] = time.monotonic()
            try:
                accepted = self.bridge.upgrade_building(village_id, int(location), 14)
            except Exception as error:
                self._log(f"Rozbudowa placu turniejowego ({village['name']}) nieudana: {error}")
                continue
            self._log(
                f"Zlecono rozbudowę placu turniejowego w {village['name']} "
                f"do poziomu {current + 1}." if accepted else
                f"Gra nie przyjęła rozbudowy placu turniejowego w {village['name']} "
                "(zajęta kolejka lub brak surowców)."
            )
        self.pt_warnings = warnings

    # ------------------------------------------------------------------ tick
    def tick(self) -> None:
        settings = self.settings
        if not settings.enabled:
            return
        self.bridge.keep_awake()
        if (
            self.last_refresh is None
            or time.monotonic() - self.last_refresh >= settings.refresh_minutes * 60
        ):
            try:
                self.refresh_plan()
            except Exception as error:
                self.last_error = str(error)
                self.last_refresh = time.monotonic() - settings.refresh_minutes * 60 + 30
                logger.warning("Attack plan refresh failed: %s", error)
                return
        now = self.server_now()
        runs = sorted(self.runs.values(), key=lambda item: item.departure)
        for run in runs:
            if run.status == STATUS_WAITING:
                if run.departure - now <= settings.prepare_seconds:
                    self._prepare(run)
            elif run.status == STATUS_FIRED and run.fired_at is not None:
                if now >= run.fired_at + VERIFY_DELAY_SECONDS:
                    self._verify(run)
            elif run.status == STATUS_PREPARED and run.fire_server_time is not None:
                if now > run.fire_server_time + 10 and run.batch_id is None:
                    self._finish(run, STATUS_FAILED, "Nie wykonano wysyłki.", "NOTHING_SENT")
            if run.finished and not run.reported:
                self._report(run)
        if settings.hero_evasion:
            self._manage_hero(now)

    # --------------------------------------------------------------- prepare
    def _prepare(self, run: AttackRun) -> None:
        attack = run.attack
        try:
            self.read_context()
            army = self.army(attack.source_village_id)
            run.tribe = army.tribe
            hero = (self.context or {}).get("hero") or {}
            if (
                attack.has_hero
                and run.hero_recall_scheduled
                and hero.get("home_village_id") == attack.source_village_id
            ):
                army.hero_home = True
            finished = {item.attack.id for item in self.runs.values() if item.finished}
            later = later_sends(attack, [item.attack for item in self.runs.values()], finished)
            sends = compose_attack(attack, army, later, self.fake_rule())
            durations = self.durations(
                attack.source_village_id,
                attack.target_village_id,
                self._movement_for(attack),
                max_age=60,
            )
            all_units: dict[int, int] = {}
            for send in sends:
                for unit, count in send.units.items():
                    all_units[unit] = all_units.get(unit, 0) + count
            departure = exact_departure(attack.arrival, all_units, durations)
        except Exception as error:
            self._finish(run, STATUS_SKIPPED, f"Nie da się przygotować: {error}", "NOTHING_SENT")
            return

        one_way = self.rtt_ms / 2000.0
        now = self.server_now()
        fire_at = departure + AIM_FRACTION - one_way
        latest = departure + 1 + LATE_TOLERANCE_SECONDS - one_way - 0.1
        if now > latest:
            self._finish(
                run, STATUS_SKIPPED,
                f"Za późno: wysyłka miała ruszyć o "
                f"{datetime.fromtimestamp(departure):%H:%M:%S} (czas serwera).",
                "TIME_LAG",
            )
            return
        fire_at = max(fire_at, now + 0.05)

        requests = []
        for send in sends:
            request = self.bridge.send_request(
                attack.source_village_id,
                attack.target_village_id,
                send.movement_type,
                send.units,
                send.catapult_targets,
            )
            if send.units.get(HERO):
                fallback_units = {unit: count for unit, count in send.units.items() if unit != HERO}
                request["fallback"] = self.bridge.send_request(
                    attack.source_village_id,
                    attack.target_village_id,
                    send.movement_type,
                    fallback_units,
                    send.catapult_targets,
                )["params"]
            requests.append(request)

        run.sends = sends
        run.requests = requests
        run.exact_departure = departure
        run.fire_server_time = fire_at
        run.status = STATUS_PREPARED
        notes = [note for send in sends for note in send.notes]
        run.message = "; ".join(notes)
        self._log(
            f"Przygotowano {attack.label}: {len(sends)} fal, wyjście "
            f"{datetime.fromtimestamp(departure):%H:%M:%S} (czas serwera)."
            + (f" Uwagi: {run.message}" if notes else "")
        )
        mode = self.settings.execution_mode
        if mode == "clicks" and self.click_executor is not None:
            try:
                self.click_executor.prepare(run)
            except Exception as error:
                self._finish(run, STATUS_SKIPPED, f"Tryb klikania: {error}", "NOTHING_SENT")
                return
        self._timer.schedule(self.to_local(fire_at), lambda: self._fire(run, latest))

    def _fire(self, run: AttackRun, latest: float) -> None:
        with self._lock:
            if run.status != STATUS_PREPARED:
                return
            try:
                if self.settings.execution_mode == "clicks" and self.click_executor is not None:
                    batch_id, result = self.click_executor.fire(run, latest)
                else:
                    batch_id, result = self.bridge.fire(run.requests, latest)
            except Exception as error:
                run.status = STATUS_FAILED
                run.message = f"Błąd wysyłki: {error}"
                run.reported = False
                return
            if result.get("status") == "too_late":
                run.status = STATUS_SKIPPED
                run.message = "Za późno w chwili wysyłki — pominięto."
                run.batch_id = batch_id
                return
            run.batch_id = batch_id
            run.fired_at = float(result.get("server_time") or self.server_now())
            run.status = STATUS_FIRED

    # ---------------------------------------------------------------- verify
    def _verify(self, run: AttackRun) -> None:
        attack = run.attack
        try:
            if self.settings.execution_mode == "clicks" and self.click_executor is not None:
                fire = self.click_executor.read(run)
            else:
                fire = self.bridge.read_fire(run.batch_id)
            movements = self.bridge.read_movements(attack.source_village_id)
        except Exception as error:
            run.message = f"Weryfikacja: {error}"
            return
        results = (fire or {}).get("results") or []
        if any(result is None for result in results) and self.server_now() < (run.fired_at or 0) + 15:
            return
        errors = [
            result.get("error") or "brak odpowiedzi"
            for result in results
            if not result or not result.get("ok")
        ]
        matching = [
            movement for movement in movements
            if movement.get("target") == attack.target_village_id
            and movement.get("movement_type") in (MOVEMENT_ATTACK, MOVEMENT_SIEGE)
            and attack.arrival - 1 <= movement.get("arrival", 0) <= attack.arrival + 30
        ]
        lags = sorted({int(movement["arrival"] - attack.arrival) for movement in matching})
        sent = len(results) - len(errors)
        lag_text = (
            "" if not lags else
            " Dotarcie: " + ", ".join(f"{lag:+d} s" for lag in lags) + "."
        )
        if sent == 0:
            self._finish(run, STATUS_FAILED, "Gra odrzuciła wysyłkę: " + "; ".join(errors), "NOTHING_SENT")
        elif errors:
            self._finish(
                run, STATUS_PARTIAL,
                f"Wysłano {sent}/{len(results)} fal. Błędy: {'; '.join(errors)}.{lag_text}",
                "PARTIALLY_NOT_SENT",
            )
        elif lags and max(lags) > LATE_TOLERANCE_SECONDS:
            self._finish(run, STATUS_PARTIAL, f"Wysłano z opóźnieniem.{lag_text}", "TIME_LAG")
        else:
            self._finish(run, STATUS_SENT, f"Wysłano {sent} fal.{lag_text}", None)

    def _finish(self, run: AttackRun, status: str, message: str, deny_reason: str | None) -> None:
        run.status = status
        run.message = message
        run.deny_reason = deny_reason
        run.reported = False
        self._log(f"{run.attack.label}: {STATUS_LABELS[status]}. {message}")

    def _report(self, run: AttackRun) -> None:
        run.reported = True
        if run.status == STATUS_DONE_ELSEWHERE or not self.settings.report_to_tktools:
            return
        comment = f"Travian Assistant: {run.message}"[:500]
        try:
            if run.status == STATUS_SENT:
                self.tktools.confirm_sent(run.attack.id, comment)
            else:
                reason = getattr(run, "deny_reason", None) or "NOTHING_SENT"
                self.tktools.deny_sent(run.attack.id, reason, comment)
        except Exception as error:
            self._log(f"Nie udało się zgłosić w TK-Tools ({run.attack.label}): {error}")

    # ------------------------------------------------------------------ hero
    def parking_village(self) -> str | None:
        context = self.context or {}
        hero = context.get("hero") or {}
        home = hero.get("home_village_id")
        villages = context.get("villages") or {}
        chosen = self.settings.hero_parking_village_id
        if chosen and chosen in villages and chosen != home:
            return chosen
        if not home or home not in villages:
            return None
        coordinates = {
            village_id: (village["x"], village["y"])
            for village_id, village in villages.items()
        }
        return nearest_village(coordinates[home], coordinates, home)

    def _next_hero_run(self) -> AttackRun | None:
        hero = (self.context or {}).get("hero") or {}
        home = hero.get("home_village_id")
        candidates = [
            run for run in self.runs.values()
            if run.status == STATUS_WAITING
            and run.attack.has_hero
            and run.attack.source_village_id == home
        ]
        return min(candidates, key=lambda item: item.departure, default=None)

    def _manage_hero(self, now: float) -> None:
        if time.monotonic() < self._hero_action_after:
            return
        self._hero_action_after = time.monotonic() + 5
        try:
            context = self.read_context()
        except Exception as error:
            self.hero_status_text = f"Brak danych: {error}"
            return
        hero = context.get("hero") or {}
        if not hero or not hero.get("alive"):
            self.hero_status_text = "Bohater nie żyje lub się odradza."
            return
        home = hero["home_village_id"]
        parking = self.parking_village()
        if parking is None:
            self.hero_status_text = "Brak drugiej wioski na postój bohatera."
            return
        try:
            durations = self.durations(home, parking, MOVEMENT_SUPPORT)
        except Exception as error:
            self.hero_status_text = f"Brak czasu przemarszu bohatera: {error}"
            return
        one_way = int(durations.get(str(HERO)) or durations.get(HERO) or 0)
        next_run = self._next_hero_run()
        village_name = context["villages"][parking]["name"]

        if hero.get("support_troop_id") and hero.get("support_village_id"):
            if next_run is None:
                self.hero_status_text = f"Bohater stoi w {village_name}; brak ataków z bohaterem."
                return
            recall_at = hero_recall_time(
                next_run.departure, one_way, self.settings.hero_return_margin_seconds,
            )
            self.hero_status_text = (
                f"Bohater w {village_name}; wycofanie o "
                f"{datetime.fromtimestamp(recall_at):%H:%M:%S} (czas serwera)."
            )
            if not next_run.hero_recall_scheduled and recall_at - now <= 30:
                next_run.hero_recall_scheduled = True
                request = self.bridge.recall_request(hero["support_troop_id"])
                aim = recall_at + AIM_FRACTION - self.rtt_ms / 2000.0
                latest = recall_at + 3
                self._timer.schedule(
                    self.to_local(max(aim, now + 0.05)),
                    lambda: self._fire_hero(request, latest, "Wycofano bohatera"),
                )
            return

        if hero.get("status") == 0 and context["villages"].get(home, {}).get("hero_home"):
            next_departure = next_run.departure if next_run else None
            if can_park_hero(now, next_departure, one_way):
                request = self.bridge.send_request(home, parking, MOVEMENT_SUPPORT, {HERO: 1})
                self._fire_hero(request, now + 30, f"Bohater wysłany na postój do {village_name}")
                self._hero_action_after = time.monotonic() + 30
            else:
                self.hero_status_text = "Bohater czeka w domu na najbliższy atak."
            return
        self.hero_status_text = "Bohater w drodze."

    def _fire_hero(self, request: dict, latest: float, success: str) -> None:
        try:
            _batch, result = self.bridge.fire([request], latest)
        except Exception as error:
            self._log(f"Akcja bohatera nieudana: {error}")
            return
        if result.get("status") == "too_late":
            self._log("Akcja bohatera spóźniona — pominięto.")
        else:
            self._log(success + ".")

    # ------------------------------------------------------------------ view
    def rows(self) -> list[dict]:
        rows = []
        for run in sorted(self.runs.values(), key=lambda item: item.departure):
            tribe = run.tribe or int((self.context or {}).get("tribe") or 0)
            composition = "; ".join(
                f"F{send.wave_index + 1}: {describe_units(send.units, tribe)}"
                for send in run.sends
            )
            rows.append({
                "id": run.attack.id,
                "label": run.attack.label,
                "operation": run.attack.operation_name,
                "departure": run.departure,
                "arrival": run.attack.arrival,
                "waves": len(run.attack.waves),
                "status": STATUS_LABELS.get(run.status, run.status),
                "status_key": run.status,
                "message": run.message,
                "composition": composition,
            })
        return rows
