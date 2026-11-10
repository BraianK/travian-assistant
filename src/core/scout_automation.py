"""Run scout operations: send scouts to the second and read the hero's EXP.

See :mod:`src.core.scout_planner` for the idea. Each operation belongs to one
incoming attack; its candidates (target x square level) each schedule two
scout sends (landing 1 s before / after the possible departure) and three
experience readings of the attacker's hero from the hero ranking.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

try:
    from src.core.attack_automation import PreciseTimer
    from src.core.scout_planner import (
        MOVEMENT_SPY,
        SCOUT_OFFSETS,
        VERDICT_LABELS,
        VERDICT_LEFT,
        XP_READ_OFFSETS,
        Candidate,
        ScoutTarget,
        judge,
        plan_candidates,
    )
except ModuleNotFoundError:  # pragma: no cover - script fallback
    from core.attack_automation import PreciseTimer
    from core.scout_planner import (
        MOVEMENT_SPY,
        SCOUT_OFFSETS,
        VERDICT_LABELS,
        VERDICT_LEFT,
        XP_READ_OFFSETS,
        Candidate,
        ScoutTarget,
        judge,
        plan_candidates,
    )

# Aim requests at the middle of the target second (as the attack automation).
AIM_FRACTION = 0.45
XP_READ_DELAY = 0.6
SCHEDULE_AHEAD_SECONDS = 20.0
LATE_XP_CHECK_SECONDS = 30

READ_HERO_XP_JS = r"""((playerId) => new Promise(resolve => {
    const rootScope = angular.element(document.body).injector().get("$rootScope");
    const timer = setTimeout(() => resolve({error: "timeout"}), 8000);
    const request = (controller, params, callback) => {
        Travian.request(controller, params, callback);
        if (!rootScope.$$phase) rootScope.$apply();
    };
    request("ranking/getRankAndCount", {
        id: playerId, rankingType: "ranking_Player", rankingSubtype: "heroes", ignoreCount: true
    }, rank => {
        if (!rank || !(rank.rank >= 0)) { clearTimeout(timer); resolve({error: "no_rank"}); return; }
        request("ranking/getRanking", {
            start: rank.rank, end: rank.rank, rankingType: "ranking_Player", rankingSubtype: "heroes"
        }, page => {
            clearTimeout(timer);
            const row = ((page && page.results) || []).find(r => String(r.playerId) === String(playerId));
            resolve(row ? {xp: Number(row.points), level: Number(row.level)} : {error: "not_found"});
        });
    });
}))"""


def read_hero_xp(bridge, player_id: str) -> dict:
    """Hero experience of a player from the hero ranking."""
    if not str(player_id).isdigit():
        raise ValueError("Invalid player id.")
    value = bridge._evaluate(
        f"{READ_HERO_XP_JS}({json.dumps(str(player_id))})", await_promise=True, timeout=12,
    )
    if not isinstance(value, dict) or value.get("error"):
        raise RuntimeError(f"Nie odczytano EXP bohatera ({(value or {}).get('error')}).")
    return {"xp": int(value["xp"]), "level": int(value.get("level") or 0)}


_ids = itertools.count(1)


@dataclass
class ScoutOperation:
    attack_key: str
    attacker_id: str
    attacker_name: str
    source_village_id: str
    source: tuple[int, int]
    unit_label: str
    siege: bool
    scout_village_id: str
    scout_village_name: str
    scout_unit: int
    scouts_per_send: int
    scout_travel: float
    pair_xp: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "aktywna"
    found: str | None = None
    scheduled: set = field(default_factory=set)


class ScoutAutomation:
    def __init__(
        self,
        bridge,
        settings_getter: Callable[[], object],
        log: Callable[[str], None],
        clock: Callable[[], float] = time.time,
        timer: PreciseTimer | None = None,
        on_found: Callable[[dict], None] | None = None,
    ):
        self.bridge = bridge
        self._on_found = on_found or (lambda _result: None)
        self._settings = settings_getter
        self._log = log
        self._clock = clock
        self._timer = timer or PreciseTimer()
        self._lock = threading.RLock()
        self.operations: list[ScoutOperation] = []
        self.server_offset_ms = 0.0
        self.context: dict | None = None
        self._context_at = 0.0

    @property
    def settings(self):
        return self._settings()

    def server_now(self) -> float:
        return self._clock() - self.server_offset_ms / 1000.0

    def to_local(self, server_time: float) -> float:
        return server_time + self.server_offset_ms / 1000.0

    def refresh_context(self, force: bool = False) -> dict:
        if force or self.context is None or time.monotonic() - self._context_at > 60:
            self.context = self.bridge.read_context()
            self.server_offset_ms = float(self.context.get("server_offset_ms") or 0.0)
            self._context_at = time.monotonic()
        return self.context

    # ------------------------------------------------------------ planning
    def create_operation(
        self,
        *,
        attack_key: str,
        attacker_id: str,
        attacker_name: str,
        source_village_id: str,
        source: tuple[int, int],
        unit_label: str,
        unit_speed: float,
        siege: bool,
        levels: list[int],
        targets: list[ScoutTarget],
        scout_village_id: str,
        scout_unit: int,
        scouts_per_send: int,
    ) -> ScoutOperation:
        with self._lock:
            previous = [
                op for op in self.operations
                if op.attack_key == attack_key and op.status == "aktywna"
            ]
            if any(
                status != "zaplanowany"
                for op in previous for candidate in op.candidates
                for status in candidate.send_status
            ):
                raise RuntimeError(
                    "dla tego atakującego część skanów już wyszła — anuluj najpierw "
                    "obecną operację, jeśli chcesz zaplanować od nowa."
                )
        context = self.refresh_context(force=True)
        village = context["villages"].get(str(scout_village_id))
        if village is None:
            raise RuntimeError("Nie znaleziono wioski do wysyłki zwiadu.")
        result = self.bridge.check_target(scout_village_id, source_village_id, MOVEMENT_SPY)
        scout_travel = float(result["durations"].get(str(scout_unit)) or 0)
        if scout_travel <= 0:
            raise RuntimeError("Gra nie podała czasu przemarszu zwiadowców.")
        supply = self.bridge._evaluate(
            "(() => { const t = (config.balancing.troopConfig || {})["
            f"{json.dumps(str((int(context.get('tribe') or 0) - 1) * 10 + int(scout_unit)))}"
            "]; return t ? Number(t.supply) || 0 : 0; })()"
        )
        pair_xp = int(supply or 0) * int(scouts_per_send)
        candidates = plan_candidates(
            source, targets, unit_speed, levels, siege, scout_travel, self.server_now(),
        )
        if not candidates:
            raise RuntimeError("Żadne z możliwych wyjść nie jest już do zdążenia ze zwiadem.")
        needed = len(candidates) * len(SCOUT_OFFSETS) * scouts_per_send
        available = int((village.get("units") or {}).get(str(scout_unit), 0))
        operation = ScoutOperation(
            attack_key=attack_key, attacker_id=str(attacker_id), attacker_name=attacker_name,
            source_village_id=str(source_village_id), source=source, unit_label=unit_label,
            siege=siege, scout_village_id=str(scout_village_id),
            scout_village_name=str(village.get("name") or scout_village_id),
            scout_unit=scout_unit, scouts_per_send=scouts_per_send,
            scout_travel=scout_travel, pair_xp=pair_xp, candidates=candidates,
        )
        with self._lock:
            # Planning the same attacker again replaces its not-yet-started plan.
            replaced = [
                op for op in self.operations
                if op.attack_key == attack_key and op.status == "aktywna"
            ]
            self.operations = [op for op in self.operations if op not in replaced]
            self.operations.append(operation)
        if replaced:
            self._log(f"Plan zwiadu na {attacker_name} zastąpiony nowym.")
        self._log(
            f"Zwiad na {attacker_name}: {len(candidates)} możliwych wyjść, potrzeba "
            f"{needed} zwiadowców z {operation.scout_village_name} (jest {available}). "
            f"Zabicie jednej pary zwiadu daje bohaterowi ok. {pair_xp} EXP."
        )
        if available < needed:
            self._log("Uwaga: za mało zwiadowców — część wysyłek się nie uda.")
        return operation

    def cancel(self, operation_id: int) -> None:
        with self._lock:
            for operation in self.operations:
                if operation.id == operation_id and operation.status == "aktywna":
                    operation.status = "anulowana"
                    self._log(f"Zwiad na {operation.attacker_name} anulowany.")

    # ------------------------------------------------------------ execution
    def tick(self) -> None:
        if not self.settings.enabled:
            return
        with self._lock:
            active = [op for op in self.operations if op.status == "aktywna"]
        if not active:
            return
        self.refresh_context()
        now = self.server_now()
        for operation in active:
            for index, candidate in enumerate(operation.candidates):
                for send_index, send_at in enumerate(candidate.sends):
                    key = ("send", index, send_index)
                    if key not in operation.scheduled and send_at - now <= SCHEDULE_AHEAD_SECONDS:
                        operation.scheduled.add(key)
                        self._timer.schedule(
                            self.to_local(send_at + AIM_FRACTION),
                            lambda op=operation, c=candidate, i=send_index: self._send(op, c, i),
                        )
                for offset in XP_READ_OFFSETS:
                    key = ("xp", index, offset)
                    at = candidate.departure + offset
                    if key not in operation.scheduled and at - now <= SCHEDULE_AHEAD_SECONDS:
                        operation.scheduled.add(key)
                        self._timer.schedule(
                            self.to_local(at + XP_READ_DELAY),
                            lambda op=operation, c=candidate, o=offset: self._read_xp(op, c, o),
                        )
            last = max(c.departure for c in operation.candidates) + LATE_XP_CHECK_SECONDS
            if now > last:
                operation.status = "zakończona"
                if not operation.found:
                    self._log(
                        f"Zwiad na {operation.attacker_name} zakończony bez znalezienia "
                        "prawdziwego celu."
                    )

    def _send(self, operation: ScoutOperation, candidate: Candidate, index: int) -> None:
        if operation.status != "aktywna":
            return
        request = self.bridge.send_request(
            operation.scout_village_id, operation.source_village_id, MOVEMENT_SPY,
            {operation.scout_unit: operation.scouts_per_send}, spy_mission="defence",
        )
        send_at = candidate.sends[index]
        try:
            _batch, result = self.bridge.fire([request], send_at + 0.95)
            candidate.send_status[index] = (
                "wysłany" if result.get("status") == "fired" else "za późno"
            )
        except Exception as error:
            candidate.send_status[index] = f"błąd: {error}"

    def _read_xp(self, operation: ScoutOperation, candidate: Candidate, offset: int) -> None:
        try:
            candidate.xp[offset] = read_hero_xp(self.bridge, operation.attacker_id)["xp"]
        except Exception as error:
            candidate.xp[offset] = None
            self._log(f"{operation.attacker_name}: {error}")
            return
        verdict = judge(candidate.xp)
        if verdict is None:
            return
        candidate.verdict = verdict
        if verdict == VERDICT_LEFT and not operation.found:
            operation.found = f"{candidate.target}, PT {candidate.level}"
            self._on_found({
                "attacker": operation.attacker_name,
                "unit": operation.unit_label,
                "siege": operation.siege,
                "target": candidate.target,
                "level": candidate.level,
                "departure": candidate.departure,
                "xp": dict(candidate.xp),
            })
        self._log(
            f"{operation.attacker_name} → {candidate.target}, PT {candidate.level} "
            f"(wyjście {time.strftime('%H:%M:%S', time.localtime(candidate.departure))}): "
            f"EXP {candidate.xp.get(-3)} → {candidate.xp.get(0)} → {candidate.xp.get(2)} — "
            f"{VERDICT_LABELS[verdict]}."
        )

    def attack_withdrawn(self, attack_key: str) -> None:
        with self._lock:
            for operation in self.operations:
                if operation.attack_key == attack_key and operation.status == "aktywna":
                    self._log(
                        f"Atak {operation.attacker_name} został wycofany — możliwe, że "
                        "wróci z innym PT; zwiad leci dalej według planu."
                    )

    def rows(self) -> list[dict]:
        with self._lock:
            rows = []
            for operation in self.operations:
                for candidate in operation.candidates:
                    rows.append({
                        "operation": operation.id,
                        "attacker": operation.attacker_name,
                        "target": candidate.target,
                        "level": candidate.level,
                        "departure": candidate.departure,
                        "sends": list(candidate.sends),
                        "send_status": list(candidate.send_status),
                        "xp": dict(candidate.xp),
                        "verdict": candidate.verdict,
                        "status": operation.status,
                        "pair_xp": operation.pair_xp,
                    })
            return rows
