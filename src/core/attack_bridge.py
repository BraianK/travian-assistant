"""Game-side actions for the attack, scouting and hideout automations.

Reading uses the game's own models and the read-only ``troops/checkTarget``
request (travel durations per unit type). Every action is clicked in the
game window like a player would (see :class:`BuildClickExecutor`):

* troops are sent from the "Send troops" window (attack 3, raid 4, support 5,
  spy 6, siege 47; unit 11 is the hero; catapult target 99 means random);
* reinforcements (the hero) are withdrawn from the rally point's
  "Stationed" tab;
* the tournament square is upgraded with its level bubble.

``send_request``/``recall_request`` only describe an action; :meth:`fire`
performs the described actions one after another.
"""

from __future__ import annotations

import json
import re
import time
import uuid

try:
    from src.core.tournament_square import village_position
except ImportError:  # pragma: no cover - packaged layout
    from core.tournament_square import village_position

MOVEMENT_SPY = 6

_SERVER_CLOCK_JS = r"""
    const injector = angular.element(document.body).injector();
    const rootScope = injector.get("$rootScope");
    const kick = () => { if (!rootScope.$$phase) rootScope.$apply(); };
    const serverOffsetMs = (() => {
        const service = injector.get("ServertimeService");
        const rough = Date.now() - Number(rootScope.currentServerTime) * 1000;
        for (const key of Object.keys(service)) {
            const fn = service[key];
            if (typeof fn !== "function" || fn.length !== 0) continue;
            let value;
            try { value = fn.call(service); } catch (error) { continue; }
            if (typeof value === "number" && Math.abs(value - rough) < 1500) {
                return value;
            }
        }
        return rough;
    })();
    const serverNowMs = () => Date.now() - serverOffsetMs;
"""

_READ_CONTEXT_JS = "(() => {" + _SERVER_CLOCK_JS + r"""
    const villages = injector.get("VillageService").U().data.map(v => v.data);
    if (!villages.length) throw new Error("No villages.");
    const playerId = String(villages[0].playerId);
    const player = Player.get(playerId);
    const tribe = Number(player.data.tribeId);
    const troopConfig = config.balancing.troopConfig || {};
    const unitCosts = {};
    for (let nr = 1; nr <= 10; nr++) {
        const unit = troopConfig[String((tribe - 1) * 10 + nr)];
        if (unit && unit.costs) {
            unitCosts[nr] = Object.values(unit.costs).reduce((a, b) => a + Number(b), 0);
        }
    }
    const result = {
        player_id: playerId, tribe, unit_costs: unitCosts,
        server_offset_ms: serverOffsetMs, server_time: serverNowMs() / 1000,
        villages: {}, hero: null, truce: null
    };
    try {
        const truce = Gameworld.getNightTruceTimes();
        if (truce && Number(truce.start) && Number(truce.end)) {
            result.truce = {start: Number(truce.start), end: Number(truce.end)};
        }
    } catch (error) { /* no night truce on this world */ }
    for (const village of villages) {
        const id = String(village.villageId);
        const stationary = Troops.getCollection("stationary", id);
        const units = {};
        let heroHome = false;
        for (const row of (stationary && stationary.data) || []) {
            const data = row.data || {};
            if (String(data.playerId) !== playerId || data.status !== "home") continue;
            for (const [unit, count] of Object.entries(data.units || {})) {
                const value = Number(count) || 0;
                if (Number(unit) === 11) { heroHome = heroHome || value > 0; continue; }
                units[unit] = (units[unit] || 0) + value;
            }
        }
        let rallyPoint = 0, square = 0, squareLocation = null;
        const buildings = Building.getCollection(id);
        for (const row of (buildings && buildings.data) || []) {
            const b = row.data || {};
            if (Number(b.buildingType) === 16) rallyPoint = Number(b.lvl) || 0;
            if (Number(b.buildingType) === 14) {
                square = Number(b.lvl) || 0; squareLocation = Number(b.locationId);
            }
        }
        result.villages[id] = {
            name: village.name, x: Number(village.coordinates.x),
            y: Number(village.coordinates.y), units, hero_home: heroHome,
            rally_point_level: rallyPoint, tournament_square_level: square,
            tournament_square_location: squareLocation
        };
    }
    const hero = Hero.get(playerId);
    if (hero && hero.data) {
        const home = String(hero.data.villageId);
        let supportTroopId = null, supportLocation = null;
        const elsewhere = Troops.getCollection("elsewhere", home);
        for (const row of (elsewhere && elsewhere.data) || []) {
            const data = row.data || {};
            if (data.status === "support" && Number((data.units || {})["11"]) > 0) {
                supportTroopId = Number(data.troopId);
                supportLocation = String(data.villageIdLocation);
            }
        }
        result.hero = {
            home_village_id: home, status: Number(hero.data.status),
            alive: !hero.data.notAlive, until: Number(hero.data.untilTime) || 0,
            support_troop_id: supportTroopId, support_village_id: supportLocation
        };
    }
    return result;
})()"""

_CHECK_TARGET_JS = "((villageId, destVillageId, movementType) => new Promise(resolve => {" + _SERVER_CLOCK_JS + r"""
    const started = Date.now();
    const timer = setTimeout(() => resolve({error: "timeout"}), 8000);
    Travian.request("troops/checkTarget", {
        villageId, destVillageId, movementType, redeployHero: false
    }, response => {
        clearTimeout(timer);
        resolve({
            rtt_ms: Date.now() - started,
            server_offset_ms: serverOffsetMs,
            error: response && response.errors
                ? String(response.errors[0].message) : null,
            durations: response && response.durations || null,
            distance: response && response.distance || null
        });
    }, () => { clearTimeout(timer); resolve({error: "request_failed"}); });
    kick();
}))"""

_READ_MOVEMENTS_JS = r"""((villageId) => {
    const collection = Troops.getCollection("moving", String(villageId));
    return ((collection && collection.data) || []).map(row => {
        const data = row.data || {};
        const movement = data.movement || {};
        return {
            troop_id: Number(data.troopId),
            movement_type: Number(movement.movementType),
            source: String(movement.villageIdStart || ""),
            target: String(movement.villageIdTarget || ""),
            arrival: Number(movement.timeFinish) || 0,
            units: data.units || {}
        };
    });
})"""

_KEEP_AWAKE_PAUSE = 15.0


_SERVER_TIME_JS = "(() => {" + _SERVER_CLOCK_JS + "return serverNowMs() / 1000; })()"


class GameAttackBridge:
    """Thin wrapper over a connected ``TravianBrowserSession`` tab.

    *clicker* returns the shared :class:`BuildClickExecutor` that performs
    the actions.
    """

    def __init__(self, session, clicker=None):
        self._session = session
        self._clicker = clicker
        self._last_wake = 0.0
        self._fired: dict[str, dict] = {}

    @property
    def _tab(self):
        tab = getattr(self._session, "_tab", None)
        if tab is None:
            raise RuntimeError("Not connected to the game.")
        return tab

    def _evaluate(self, expression: str, *, await_promise: bool = False, timeout: int = 10):
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
            _timeout=timeout,
        )
        details = result.get("exceptionDetails")
        if details:
            text = (details.get("exception") or {}).get("description") or details.get("text")
            raise RuntimeError(f"Skrypt gry zgłosił błąd: {str(text)[:200]}")
        return result.get("result", {}).get("value")

    def keep_awake(self, force: bool = False) -> None:
        """Stop the browser from freezing the background game tab."""
        if not force and time.monotonic() - self._last_wake < _KEEP_AWAKE_PAUSE:
            return
        self._last_wake = time.monotonic()
        self._tab.call_method("Page.setWebLifecycleState", state="active", _timeout=5)
        self._tab.call_method("Emulation.setFocusEmulationEnabled", enabled=True, _timeout=5)

    def read_context(self) -> dict:
        value = self._evaluate(_READ_CONTEXT_JS)
        if not isinstance(value, dict) or not value.get("villages"):
            raise RuntimeError("Gra nie zwróciła danych o wioskach.")
        return value

    def check_target(self, village_id: str, dest_village_id: str, movement_type: int) -> dict:
        if not (str(village_id).isdigit() and str(dest_village_id).isdigit()):
            raise ValueError("Invalid village id.")
        value = self._evaluate(
            f"{_CHECK_TARGET_JS}({int(village_id)}, {int(dest_village_id)}, "
            f"{int(movement_type)})",
            await_promise=True,
            timeout=12,
        )
        if not isinstance(value, dict):
            raise RuntimeError("Gra nie zwróciła czasu przemarszu.")
        if value.get("error"):
            raise RuntimeError(f"Gra odrzuciła podgląd celu: {value['error']}")
        if not isinstance(value.get("durations"), dict):
            raise RuntimeError("Gra nie zwróciła czasu przemarszu.")
        return value

    @staticmethod
    def send_request(
        village_id: str,
        dest_village_id: str,
        movement_type: int,
        units: dict[int, int],
        catapult_targets: tuple[int, ...] = (),
        spy_mission: str = "resources",
    ) -> dict:
        if spy_mission not in ("resources", "defence"):
            raise ValueError("Invalid spy mission.")
        params = {
            "villageId": int(village_id),
            "destVillageId": int(dest_village_id),
            "movementType": int(movement_type),
            "redeployHero": False,
            "units": {str(unit): int(count) for unit, count in units.items() if count > 0},
            "spyMission": spy_mission,
        }
        if catapult_targets:
            params["catapultTargets"] = [int(target) for target in catapult_targets]
        return {"controller": "troops/send", "params": params}

    @staticmethod
    def recall_request(
        troop_id: int, units: dict[int, int] | None = None, *,
        village_id: str | None = None, location_village_id: str | None = None,
    ) -> dict:
        """Withdraw own troops (by default the hero) standing in another
        village; *village_id* is their home, *location_village_id* where
        they stand."""
        return {
            "controller": "troops/moveTroopsHome",
            "params": {
                "troopId": int(troop_id),
                "units": {str(k): int(v) for k, v in (units or {11: 1}).items()},
                "villageId": str(village_id or ""),
                "locationVillageId": str(location_village_id or ""),
            },
        }

    def server_time(self) -> float:
        value = self._evaluate(_SERVER_TIME_JS)
        if not isinstance(value, (int, float)):
            raise RuntimeError("Nie odczytano czasu serwera.")
        return float(value)

    def _executor(self):
        executor = self._clicker() if callable(self._clicker) else self._clicker
        if executor is None:
            raise RuntimeError("Brak karty do klikania w grze.")
        return executor

    def fire(self, requests: list[dict], latest_server_time: float) -> tuple[str, dict]:
        """Click every described action unless the server clock passed *latest*."""
        for request in requests:
            if request.get("controller") not in ("troops/send", "troops/moveTroopsHome"):
                raise ValueError("Unsupported request.")
        batch_id = uuid.uuid4().hex
        started = self.server_time()
        if started > latest_server_time:
            return batch_id, {"status": "too_late", "server_time": started}
        executor = self._executor()
        results = []
        for request in requests:
            if self.server_time() > latest_server_time:
                results.append({"ok": False, "error": "za późno"})
                continue
            results.append(self._click(executor, request))
        self._fired[batch_id] = {"results": results, "fired_at": started}
        return batch_id, {"status": "fired", "server_time": started}

    @staticmethod
    def _click(executor, request: dict) -> dict:
        params = request["params"]
        units = {int(k): int(v) for k, v in (params.get("units") or {}).items() if int(v) > 0}
        try:
            if request["controller"] == "troops/moveTroopsHome":
                if not (params.get("villageId") and params.get("locationVillageId")):
                    raise ValueError("Nie wiadomo, skąd wycofać wojska.")
                executor.recall_troops(params["villageId"], params["locationVillageId"], units)
                return {"ok": True, "error": None}
            attempts = [(params, False)]
            if request.get("fallback"):
                attempts.append((request["fallback"], True))
            error = None
            for attempt, is_fallback in attempts:
                position = village_position(attempt["destVillageId"])
                if position is None:
                    raise ValueError("Nieznane współrzędne celu.")
                movement = int(attempt["movementType"])
                try:
                    executor.send_troops(
                        str(attempt["villageId"]), position[0], position[1], movement,
                        {int(k): int(v) for k, v in attempt["units"].items()},
                        tuple(attempt.get("catapultTargets") or ()),
                        attempt.get("spyMission") if movement == MOVEMENT_SPY else None,
                    )
                    return {"ok": True, "error": None, "fallback": is_fallback}
                except Exception as send_error:
                    error = send_error
                    if getattr(send_error, "__class__", None).__name__ == "GoldGuardError":
                        break
            raise error
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def read_fire(self, batch_id: str) -> dict | None:
        if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
            raise ValueError("Invalid batch id.")
        return self._fired.get(batch_id)

    def read_movements(self, village_id: str) -> list[dict]:
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        value = self._evaluate(f"{_READ_MOVEMENTS_JS}({json.dumps(str(village_id))})")
        return value if isinstance(value, list) else []

    def upgrade_building(self, village_id: str, location_id: int, current_level: int) -> bool:
        """Click the level bubble of a building (the tournament square)."""
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        status = self._executor().upgrade(str(village_id), int(location_id), int(current_level), False)
        return status == "started"
