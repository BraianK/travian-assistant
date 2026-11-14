"""Village building: read the game state and upgrade fields by clicking.

Reading uses the models the game client already keeps for every own village
(no requests are sent). Upgrading is done like a player would do it: in a
background game tab (so the player's own tab is left alone) the assistant
picks the village in the village list, opens the resource (or village) view
and clicks the level bubble of the field. The game only starts an upgrade
from the bubble when it shows the green "possible" state, which is checked
before the click; the building queue is read afterwards to confirm it.
"""

from __future__ import annotations

import contextlib
import functools
import json
import threading
import logging
import time

import pychrome

try:
    from src.core.attack_clicker import ClickModeError, _Lane
    from src.core.browser_session import _is_game_url, _tab_url
except ModuleNotFoundError:  # pragma: no cover - script fallback
    from core.attack_clicker import ClickModeError, _Lane
    from core.browser_session import _is_game_url, _tab_url

logger = logging.getLogger(__name__)

# Every call into the background tab gets a timeout: a call that never
# returns would otherwise freeze the whole automation.
INPUT_TIMEOUT = 5

READ_BUILD_STATE_JS = r"""(() => {
    if (typeof Village === "undefined" || typeof Building === "undefined"
        || typeof BuildingQueue === "undefined" || !window.Travian || !Travian.Player) {
        throw new Error("Game models are unavailable.");
    }
    const injector = angular.element(document.body).injector();
    const rootScope = injector.get("$rootScope");
    const serverTime = Number(rootScope.currentServerTime) || Date.now() / 1000;
    const options = [...document.querySelectorAll(".villageListDropDown .dropdownOption")];
    const ids = options.map(o => (o.className.match(/\bdropDownItem(\d+)\b/) || [])[1]).filter(Boolean);
    const keep = new Set([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 15, 17, 19, 20, 21, 25, 26, 29, 30, 38, 39]);
    const villages = [];
    // Resources on their way to own villages: raid loot, merchants.
    const incoming = {};
    if (typeof Troops !== "undefined") {
        const seen = new Set();
        for (const id of ids) {
            const moving = Troops.getCollection("moving", id);
            for (const row of (moving && moving.data) || []) {
                const d = row.data || {};
                const m = d.movement || {};
                const key = String(d.troopId || "") + ":" + String(m.timeFinish || "");
                const target = String(m.villageIdTarget || "");
                const arrival = Number(m.timeFinish) || 0;
                // Merchants coming home still list what they delivered.
                const homecoming = Number(m.movementType) === Number(Troops.MOVEMENT_TYPE_TRANSPORT_RETURN || 33);
                if (seen.has(key) || !ids.includes(target) || arrival <= serverTime || homecoming
                    || !m.resources || typeof m.resources !== "object") continue;
                seen.add(key);
                const resources = {};
                let total = 0;
                for (const r of ["1", "2", "3", "4"]) {
                    const amount = Number(m.resources[r]) || 0;
                    if (amount > 0) { resources[r] = amount; total += amount; }
                }
                if (total > 0) (incoming[target] = incoming[target] || []).push({arrival, resources});
            }
        }
    }
    for (const id of [...new Set(ids)]) {
        const village = Village.get(id);
        if (!village || !village.data || !village.data.villageId) continue;
        const collection = Building.getCollection(id);
        const queue = BuildingQueue.get(id);
        if (!collection || !collection.filled || !queue || !queue.data || !queue.data.queues) {
            villages.push({id, missing: true, name: village.data.name});
            continue;
        }
        const queued = {};
        for (const type of ["1", "2", "4"]) {
            for (const entry of queue.data.queues[type] || []) {
                const loc = Number(entry.locationId);
                queued[loc] = (queued[loc] || 0) + 1;
            }
        }
        const buildings = [];
        for (const row of collection.data || []) {
            const d = row.data || {};
            if (!keep.has(Number(d.buildingType))) continue;
            buildings.push({
                locationId: Number(d.locationId), buildingType: Number(d.buildingType),
                lvl: Number(d.lvl), lvlMax: Number(d.lvlMax),
                nextUpgradeCosts: d.nextUpgradeCosts, nextUpgradeTimes: d.nextUpgradeTimes,
                upgradeSupplyUsageSums: d.upgradeSupplyUsageSums, effect: d.effect,
                queued: queued[Number(d.locationId)] || 0
            });
        }
        // The Merchants model is only filled once the marketplace was opened.
        let merchantsFree = null, merchantCarry = 0;
        if (typeof Merchants !== "undefined") {
            const merchants = Merchants.get(id);
            if (merchants && merchants.data) {
                merchantCarry = Number(merchants.data.carry) || 0;
                if (Number(merchants.data.max) > 0) {
                    merchantsFree = Number(merchants.data.merchantsFree) || 0;
                }
            }
        }
        const resources = typeof village.getResources === "function"
            ? village.getResources() : village.data.storage;
        villages.push({
            id: String(id), name: village.data.name,
            is_capital: !!village.data.isMainVillage, is_town: !!village.data.isTown,
            tribe: Number(village.data.tribeId) || 0,
            x: Number(village.data.coordinates && village.data.coordinates.x),
            y: Number(village.data.coordinates && village.data.coordinates.y),
            resources, capacity: village.data.storageCapacity,
            production: village.data.production,
            buildings, queues: queue.data.queues, free_slots: queue.data.freeSlots,
            incoming: incoming[String(id)] || [],
            // For new buildings: empty building sites and the highest level
            // of each building type (requirements).
            empty_slots: (collection.data || []).map(r => r.data)
                .filter(d => Number(d.buildingType) === 0 && Number(d.locationId) >= 19)
                .map(d => Number(d.locationId)),
            // Cranny: stock per resource raiders cannot take.
            protected: (collection.data || []).map(r => r.data)
                .filter(d => Number(d.buildingType) === 23)
                .reduce((sum, d) => sum + (Number(d.effect && d.effect[0]) || 0), 0),
            type_levels: (() => {
                const out = {};
                for (const d of (collection.data || []).map(r => r.data)) {
                    const t = Number(d.buildingType);
                    if (t > 0) out[t] = Math.max(out[t] || 0, Number(d.lvl) || 0);
                }
                return out;
            })(),
            // Recruitment: queue end per building and estimated unit times.
            unit_queue_end: (() => {
                const out = {};
                const uq = typeof UnitQueue !== "undefined" ? UnitQueue.get(id) : null;
                const types = (uq && uq.data && uq.data.buildingTypes) || {};
                for (const [type, entries] of Object.entries(types)) {
                    let end = 0;
                    for (const e of entries || []) end = Math.max(end, Number(e.timeFinishedLast) || 0);
                    out[type] = end;
                }
                return out;
            })(),
            unit_times: (() => {
                const out = {};
                const speed = Number(Travian.Config.TG_TROOP_SPEED) || 1;
                const rows = (collection.data || []).map(r => r.data);
                const uq = typeof UnitQueue !== "undefined" ? UnitQueue.get(id) : null;
                const types = (uq && uq.data && uq.data.buildingTypes) || {};
                for (const [unit, troop] of Object.entries(Travian.Config.troopConfig || {})) {
                    if (Number(troop.tribe) !== Number(village.data.tribeId)) continue;
                    // Exact time while the unit is in the queue.
                    let live = 0;
                    for (const entries of Object.values(types))
                        for (const e of entries || [])
                            if (Number(e.unitType) === Number(unit) && Number(e.durationPerUnit) > 0)
                                live = Number(e.durationPerUnit);
                    if (live) { out[unit] = live; continue; }
                    const need = (troop.requirements || []).map(r => Number(r.buildingType));
                    // Settlers and chiefs train in the residence or palace.
                    if ([9, 10].includes(Number(troop.nr)) && !need.length) need.push(25, 26);
                    const b = rows.find(d => need.includes(Number(d.buildingType))
                        || (need.includes(19) && Number(d.buildingType) === 29)
                        || (need.includes(20) && Number(d.buildingType) === 30));
                    const effect = b && b.currentEffect && b.currentEffect.values && Number(b.currentEffect.values[0]);
                    if (b && effect) out[unit] = Number(troop.time) / speed * effect / 100;
                    else if (b && [9, 10].includes(Number(troop.nr))) out[unit] = Number(troop.time) / speed;
                }
                return out;
            })(),
            // Own units at home (unit number 1-10 -> count, hero excluded) and
            // own units standing as reinforcement elsewhere (to bring back).
            units_home: (() => {
                const out = {};
                const stationary = typeof Troops !== "undefined" ? Troops.getCollection("stationary", id) : null;
                for (const row of (stationary && stationary.data) || []) {
                    const d = row.data || {};
                    if (String(d.playerId) !== String(Travian.Player.data.playerId) || d.status !== "home") continue;
                    for (const [nr, count] of Object.entries(d.units || {})) {
                        if (Number(nr) >= 1 && Number(nr) <= 10 && Number(count) > 0)
                            out[nr] = (out[nr] || 0) + Number(count);
                    }
                }
                return out;
            })(),
            units_elsewhere: (() => {
                const out = [];
                const elsewhere = typeof Troops !== "undefined" ? Troops.getCollection("elsewhere", id) : null;
                for (const row of (elsewhere && elsewhere.data) || []) {
                    const d = row.data || {};
                    if (d.status !== "support") continue;
                    out.push({troop_id: Number(d.troopId), location: String(d.villageIdLocation), units: d.units || {}});
                }
                return out;
            })(),
            // Settlers of this village: at home and in the training queue.
            settlers: (() => {
                const unit = Object.entries(Travian.Config.troopConfig || {}).find(([, t]) =>
                    Number(t.tribe) === Number(village.data.tribeId) && Number(t.nr) === 10);
                if (!unit) return null;
                let home = 0, queued = 0;
                const stationary = typeof Troops !== "undefined" ? Troops.getCollection("stationary", id) : null;
                for (const row of (stationary && stationary.data) || []) {
                    const d = row.data || {};
                    if (String(d.playerId) !== String(Travian.Player.data.playerId) || d.status !== "home") continue;
                    home += Number((d.units || {})["10"]) || 0;
                }
                const uq = typeof UnitQueue !== "undefined" ? UnitQueue.get(id) : null;
                for (const entries of Object.values((uq && uq.data && uq.data.buildingTypes) || {}))
                    for (const e of entries || [])
                        if (Number(e.unitType) === Number(unit[0])) queued += Number(e.count) || 0;
                const t = unit[1];
                return {unit: Number(unit[0]), home, queued, costs: t.costs,
                        seconds: Number(t.time) / (Number(Travian.Config.TG_TROOP_SPEED) || 1)};
            })(),
            merchants_free: merchantsFree, merchant_carry: merchantCarry
        });
    }
    const heroData = (() => {
        try { const h = Hero.get(Travian.Player.data.playerId); return h && h.data; } catch (e) { return null; }
    })();
    return {
        server_time: serverTime,
        hero: heroData ? {
            village_id: String(heroData.villageId), type: Number(heroData.resBonusType),
            points: Number(heroData.resBonusPoints) || 0, dead: !!heroData.notAlive,
            // Hourly amount per resource of the current setting.
            value: Number(heroData.resBonusValue) || 0,
        } : null,
        gold: Number(Travian.Player.data.gold) || 0,
        gold_confirmation: Number(
            (Settings.get(Travian.Player.data.playerId).data || {}).premiumConfirmation
        ) || 0,
        finish_config: (Travian.Config.PremiumFeatures || {}).finishNow || null,
        npc_price: Number(((Travian.Config.PremiumFeatures || {}).NPCTrader || {}).price) || 0,
        // Base data of buildings that may be constructed (bonus, storage, marketplace).
        building_config: (() => {
            const out = {};
            for (const t of [5, 6, 7, 8, 9, 10, 11, 17, 25]) {
                const c = (Travian.Config.buildingConfig || {})[t];
                if (!c) continue;
                out[t] = {
                    costs: c.costs, time: Number(c.time) || 0, supply: Number(c.supply) || 0,
                    requirements: (c.requirements || []).map(r => ({
                        type: Number(r.type), building: Number(r.buildingType), level: Number(r.minLvl)
                    }))
                };
            }
            return out;
        })(),
        // Units of the player's tribe: costs and the building that trains them.
        troops: (() => {
            const out = {};
            const tribe = Number(Travian.Player.data.tribeId) || 0;
            const recruiting = [19, 20, 21, 29, 30];
            for (const [id, troop] of Object.entries(Travian.Config.troopConfig || {})) {
                if (Number(troop.tribe) !== tribe) continue;
                const need = (troop.requirements || []).map(r => Number(r.buildingType))
                    .filter(t => recruiting.includes(t));
                if (!need.length) continue;
                const types = need.includes(19) ? [19, 29] : need.includes(20) ? [20, 30] : need;
                out[id] = {costs: troop.costs, building_types: types};
            }
            return out;
        })(),
        merchant_speed: Travian.Config.merchantSpeed || {},
        world_speed: Number(Travian.Config.TG_SPEED) || 1,
        // Speed (fields per hour, world speed included) of the tribe's units by number.
        unit_speeds: (() => {
            const out = {};
            const tribe = Number(Travian.Player.data.tribeId) || 0;
            const factor = Number(Travian.Config.TG_TROOP_SPEED) || 1;
            for (const troop of Object.values(Travian.Config.troopConfig || {})) {
                if (Number(troop.tribe) === tribe) out[Number(troop.nr)] = Number(troop.speed) * factor;
            }
            return out;
        })(),
        // Building types the game does not let finish instantly (residence, ...).
        no_instant_finish: Object.entries(Travian.Config.buildingConfig || {})
            .filter(([, c]) => c && c.canFinishInstantly === false).map(([t]) => Number(t)),
        // Hostile movements on the way to own villages (read every time, so
        // attacks already flying when the app started count too).
        attacks: (() => {
            const out = [];
            if (typeof Troops === "undefined" || typeof Troops.getCollection !== "function") return out;
            for (const id of [...new Set(ids)]) {
                const c = Troops.getCollection(Troops.COLLECTION_MOVING, String(id));
                for (const row of (c && c.data) || []) {
                    if (typeof row.getMovementGroup !== "function"
                        || row.getMovementGroup(Number(id)) !== "incoming_attacks") continue;
                    const m = (row.data || {}).movement || {};
                    out.push({
                        village_id: String(m.villageIdTarget || id),
                        arrival_time: Number(m.timeFinish) || 0,
                        movement_type: Number(m.movementType) === Number(Troops.MOVEMENT_TYPE_SPY) ? "Zwiad" : "Atak",
                    });
                }
            }
            return out;
        })(),
        villages
    };
})()"""


LANE_MARKER = "travian-assistant-lane"
# The background tab is reloaded this often, so its game models stay fresh.
LANE_REFRESH_SECONDS = 30 * 60


def is_lane_tab(tab) -> bool:
    """Whether a browser tab is a background tab opened by the assistant."""
    try:
        tab.start()
        try:
            value = tab.call_method(
                "Runtime.evaluate", expression="window.name", returnByValue=True, _timeout=3,
            )
        finally:
            tab.stop()
        return value.get("result", {}).get("value") == LANE_MARKER
    except Exception:
        return False


def close_stale_lanes(browser, keep=None) -> int:
    """Close background tabs left behind by earlier runs of the assistant."""
    closed = 0
    try:
        tabs = browser.list_tab(timeout=3)
    except Exception:
        return 0
    for tab in tabs:
        if getattr(tab, "id", None) == keep:
            continue
        url = _tab_url(tab)
        if not _is_game_url(url) or not is_lane_tab(tab):
            continue
        try:
            browser.close_tab(tab.id)
            closed += 1
        except Exception:
            pass
    if closed:
        logger.info("Closed %d leftover background game tabs", closed)
    return closed


def _exclusive(method):
    """One action at a time in the shared background tab (build + recruitment)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def read_build_state(session) -> dict:
    """Read every own village's fields, storages, queue and merchants."""
    tab = getattr(session, "_tab", None)
    if tab is None:
        raise RuntimeError("Nie połączono z grą.")
    result = tab.call_method(
        "Runtime.evaluate", expression=READ_BUILD_STATE_JS, returnByValue=True, _timeout=10,
    )
    if result.get("exceptionDetails"):
        details = result["exceptionDetails"]
        text = (details.get("exception") or {}).get("description") or details.get("text")
        raise RuntimeError(f"Odczyt budynków nie powiódł się: {str(text)[:200]}")
    value = result.get("result", {}).get("value")
    if not isinstance(value, dict) or not isinstance(value.get("villages"), list):
        raise RuntimeError("Gra zwróciła nieprawidłowe dane budynków.")
    return value


_SELECTED_JS = (
    "((document.querySelector('.villageListDropDown .dropdownOption.selected')"
    " || {className: ''}).className.match(/dropDownItem(\\d+)/) || [])[1] || ''"
)

_BUBBLE_STATE_JS = r"""((page, location) => {
    const node = document.querySelector(`.${page}.viewBackground .buildingStatus.location${location}`)
        || document.querySelector(`.buildingStatus.location${location}`);
    if (!node) return null;
    const layer = node.querySelector(".colorLayer");
    const level = node.querySelector(".buildingLevel");
    return {
        status: node.className,
        layer: layer ? layer.className : "",
        level: level ? Number(level.textContent.trim()) : null
    };
})"""

_QUEUE_HAS_JS = r"""((villageId, location, queueType, before) => {
    const queue = BuildingQueue.get(villageId);
    if (!queue || !queue.data || !queue.data.queues) return false;
    const count = (queue.data.queues[queueType] || [])
        .filter(entry => Number(entry.locationId) === location).length;
    return count > before;
})"""

_QUEUE_COUNT_JS = r"""((villageId, location, queueType) => {
    const queue = BuildingQueue.get(villageId);
    if (!queue || !queue.data || !queue.data.queues) return 0;
    return (queue.data.queues[queueType] || [])
        .filter(entry => Number(entry.locationId) === location).length;
})"""


class StaleStateError(ClickModeError):
    """The game shows another level than the plan: the read was outdated."""


class GoldGuardError(ClickModeError):
    """Gold went down by more than the action may cost: stop everything."""


_NO_LOADING_JS = r"""(() => {
    const screens = [...document.querySelectorAll('.loadingScreen, .windowLoading')];
    return screens.every(node => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width === 0 || rect.height === 0 || style.display === 'none'
            || style.visibility === 'hidden' || Number(style.opacity) === 0;
    });
})()"""

_TARGET_AT_POINT_JS = r"""((selector, index, x, y, accept) => {
    const nodes = [...document.querySelectorAll(selector)].filter(node => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    });
    const target = nodes[index || 0];
    const hit = document.elementFromPoint(x, y);
    if (!target || !hit) return {ok: false, hit: hit ? hit.tagName + "." + hit.className : null};
    if (target === hit || target.contains(hit)) return {ok: true};
    const clickable = (hit.getAttribute && (hit.getAttribute("clickable") || "")) || "";
    if (accept && clickable.indexOf(accept) >= 0) return {ok: true};
    return {ok: false, hit: hit.tagName + "." + String(hit.className).slice(0, 60)
        + (clickable ? " [" + clickable.slice(0, 60) + "]" : "")};
})"""


# Player.vacationState values.
VACATION_INACTIVE = 0
VACATION_ACTIVATING = 1


class BuildClickExecutor:
    """Upgrades buildings by clicking in a background game tab.

    Every click first checks that the element under the cursor is the
    intended one, and every action compares the gold balance before and
    after: losing more than the action may cost raises
    :class:`GoldGuardError`.
    """

    def __init__(self, session):
        self._session = session
        self._lane: _Lane | None = None
        self._lock = threading.RLock()

    def _game_url(self) -> str:
        tab = getattr(self._session, "_tab", None)
        if tab is None:
            raise ClickModeError("Nie połączono z grą.")
        url = _tab_url(tab)
        if not _is_game_url(url):
            raise ClickModeError("Karta gry nie jest otwarta.")
        return url

    def _ensure_lane(self) -> _Lane:
        lane = self._lane
        if lane is not None and lane.tab is not None:
            try:
                if lane.evaluate("!!document.querySelector('#villageList')"):
                    return lane
            except Exception:
                pass
            lane.close()
        browser = pychrome.Browser(url=self._session._cdp_url)
        close_stale_lanes(browser, keep=getattr(getattr(self._session, "_tab", None), "id", None))
        lane = _Lane(browser, self._game_url())
        lane.open()
        # Mark the tab, so a later start can close it if it is left behind.
        lane.evaluate(f"(() => {{ window.name = {json.dumps(LANE_MARKER)}; return 1; }})()")
        self._lane = lane
        self._lane_opened = time.monotonic()
        return lane

    @_exclusive
    def read_state(self) -> dict:
        """Read the game state from the background tab. The player's own tab
        can stop updating its models (e.g. idle behind the welcome screen),
        which once hid a build about to finish; this tab is kept awake and
        reloaded every LANE_REFRESH_SECONDS."""
        lane = self._lane
        if lane is not None and time.monotonic() - getattr(self, "_lane_opened", 0) > LANE_REFRESH_SECONDS:
            lane.close()
            self._lane = None
        lane = self._ensure_lane()
        lane.wake()
        value = lane.evaluate(READ_BUILD_STATE_JS, timeout=10)
        if not isinstance(value, dict) or not isinstance(value.get("villages"), list):
            raise RuntimeError("Gra zwróciła nieprawidłowe dane budynków.")
        return value

    @_exclusive
    def close(self) -> None:
        if self._lane is not None:
            self._lane.close()
            self._lane = None

    def _close_windows(self, lane: _Lane) -> None:
        """Close open game windows with the Escape key (no aiming needed)."""
        # A fresh background tab can get the site's cookie banner, which
        # covers the bottom bar (master builder slots). Only hidden in this
        # tab: the player's own consent choice stays untouched.
        lane.evaluate(
            "(() => { document.querySelectorAll('.cmpwrapper, #cmpwrapper')"
            ".forEach(e => { e.style.setProperty('display', 'none', 'important');"
            " e.style.setProperty('pointer-events', 'none', 'important'); }); return 1; })()"
        )
        for _ in range(4):
            if not lane.evaluate("!!document.querySelector('.modalWrapper')"):
                return
            for kind in ("keyDown", "keyUp"):
                lane.tab.call_method(
                    "Input.dispatchKeyEvent", type=kind, key="Escape",
                    code="Escape", windowsVirtualKeyCode=27, _timeout=INPUT_TIMEOUT,
                )
            time.sleep(0.6)
        if lane.evaluate("!!document.querySelector('.modalWrapper')"):
            raise ClickModeError("Nie udało się zamknąć okna gry.")

    def _select_village(self, lane: _Lane, village_id: str) -> None:
        if lane.evaluate(_SELECTED_JS) == village_id:
            return
        option = f".villageListDropDown .dropdownOption.dropDownItem{int(village_id)}"
        # A tab that sat idle for a while renders the list slowly: wait for
        # the village to show up, and open the list once more if it does not.
        for _attempt in range(2):
            if not lane.evaluate(
                "!!(document.querySelector('#villageList .dropdownHead.opened'))"
            ):
                self._click(lane, "#villageList .dropdownHead")
            try:
                lane.wait_for(
                    f"(e => !!e && e.getBoundingClientRect().width > 0)(document.querySelector('{option}'))",
                    timeout=6,
                )
                break
            except ClickModeError:
                self._close_windows(lane)
        time.sleep(0.3)
        self._click(lane, option)
        try:
            lane.wait_for(f"({_SELECTED_JS}) === {json.dumps(village_id)}", timeout=8)
        except ClickModeError:
            raise ClickModeError("Nie udało się wybrać wioski z listy.") from None
        time.sleep(0.8)

    def _open_page(self, lane: _Lane, page: str, location: int) -> None:
        self._show_page(lane, page)
        try:
            lane.wait_for(
                f"!!document.querySelector('.{page}.viewBackground "
                f".buildingStatus.location{int(location)}')",
                timeout=10,
            )
        except ClickModeError:
            raise ClickModeError(
                f"Nie znaleziono budynku na miejscu {location} w widoku wioski."
            ) from None
        time.sleep(0.8)

    def _upgrade(self, village_id: str, location: int, expected_level: int, is_field: bool) -> str:
        """Click the level bubble of one building; returns a short status.

        Raises :class:`ClickModeError` when the game does not offer the
        upgrade (not enough resources, busy queue, changed level) or the
        queue does not show it afterwards.
        """
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        village_id = str(village_id)
        location = int(location)
        page = "resources" if is_field else "village"
        queue_type = "2" if is_field else "1"
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._open_page(lane, page, location)
        state = lane.evaluate(f"{_BUBBLE_STATE_JS}({json.dumps(page)}, {location})")
        if not state:
            raise ClickModeError("Nie znaleziono pola na widoku wioski.")
        if state.get("level") != int(expected_level):
            raise StaleStateError(
                f"Poziom w grze to {state.get('level')}, a plan zakładał {expected_level}."
            )
        if "possible" not in (state.get("layer") or "").split():
            raise ClickModeError("Gra nie pozwala teraz rozbudować (surowce lub kolejka).")
        # With the option menu open the bubble would book the master builder.
        self._close_option_menu(lane)
        before = lane.evaluate(
            f"{_QUEUE_COUNT_JS}({json.dumps(village_id)}, {location}, {json.dumps(queue_type)})"
        ) or 0
        self._click(lane, 
            f".{page}.viewBackground .buildingStatus.location{location} > .buildingBubble"
        )
        try:
            lane.wait_for(
                f"{_QUEUE_HAS_JS}({json.dumps(village_id)}, {location}, "
                f"{json.dumps(queue_type)}, {int(before)})",
                timeout=10,
            )
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Po kliknięciu rozbudowa nie pojawiła się w kolejce.")
        return "started"

    # -- helpers -----------------------------------------------------------------
    def _show_page(self, lane: _Lane, page: str) -> None:
        """Switch to the village or resource view and wait until it is drawn."""
        # Both views stay in the page; the hidden one has no size.
        shown = (
            f"[...document.querySelectorAll('.{page}.viewBackground')]"
            ".some(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"
        )
        if not lane.evaluate(shown):
            self._click(lane, f"a.navi_{page}", accept=f"Page('{page}')")
        try:
            lane.wait_for(shown, timeout=10)
        except ClickModeError:
            raise StaleStateError("Widok gry się nie przełączył — ponowię.") from None
        self._wait_loaded(lane)
        time.sleep(0.8)

    def _wait_loaded(self, lane: _Lane, timeout: float = 20.0) -> None:
        """Wait until the game's loading screen is gone."""
        try:
            lane.wait_for(_NO_LOADING_JS, timeout=timeout)
        except ClickModeError:
            raise StaleStateError("Gra wciąż się ładuje — ponowię.") from None

    def _click(self, lane: _Lane, selector: str, index: int = 0, *, accept: str | None = None) -> None:
        """Click an element only when it is really what lies under the cursor."""
        self._wait_loaded(lane)
        position = lane.rect(selector, index)
        if not position:
            raise ClickModeError(f"Nie znaleziono elementu {selector}.")
        if position.get("disabled"):
            raise ClickModeError(f"Element {selector} jest nieaktywny.")
        check = lane.evaluate(
            f"{_TARGET_AT_POINT_JS}({json.dumps(selector)}, {int(index)}, "
            f"{float(position['x'])}, {float(position['y'])}, {json.dumps(accept)})"
        ) or {}
        if not check.get("ok"):
            hit = str(check.get("hit") or "")
            if "loadingScreen" in hit or "windowLoading" in hit:
                raise StaleStateError("Gra wciąż się ładuje — ponowię.")
            raise ClickModeError(
                f"Kliknięcie wstrzymane — pod kursorem jest inny element ({hit})."
            )
        for event in ("mouseMoved", "mousePressed", "mouseReleased"):
            lane.tab.call_method(
                "Input.dispatchMouseEvent",
                type=event, x=position["x"], y=position["y"],
                button="left" if event != "mouseMoved" else "none",
                clickCount=1, _timeout=INPUT_TIMEOUT,
            )

    @contextlib.contextmanager
    def _gold_guard(self, lane: _Lane, allowed: int, action: str):
        """Fail loudly when gold drops by more than *allowed* during *action*."""
        before = self._gold(lane)
        try:
            yield
        finally:
            try:
                after = self._gold(lane)
            except Exception:
                after = before
            if before - after > allowed:
                raise GoldGuardError(
                    f"Saldo złota spadło o {before - after} podczas akcji „{action}” "
                    f"(dozwolone {allowed}). Automat zatrzymany."
                )

    def _move_mouse(self, lane: _Lane, x: float, y: float) -> None:
        lane.tab.call_method(
            "Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, _timeout=INPUT_TIMEOUT,
        )

    def _hover(self, lane: _Lane, selector: str) -> None:
        position = lane.rect(selector)
        if not position:
            raise ClickModeError(f"Nie znaleziono elementu {selector}.")
        # Leave first: moving onto the spot the cursor already rests on does
        # not fire the game's "pointer over" handler.
        self._move_mouse(lane, 5, 5)
        time.sleep(0.3)
        self._move_mouse(lane, position["x"], position["y"])

    def _close_option_menu(self, lane: _Lane) -> None:
        """Move the mouse away until no premium option menu is open."""
        if not lane.evaluate("!!document.querySelector('.premiumOptionMenu')"):
            return
        self._move_mouse(lane, 5, 5)
        try:
            lane.wait_for("!document.querySelector('.premiumOptionMenu')", timeout=4)
        except ClickModeError:
            raise ClickModeError("Menu opcji budynku nie zamknęło się.") from None
        time.sleep(0.3)

    def _confirm_gold(self, lane: _Lane, feature: str, max_price: int) -> None:
        """Press OK in the game's gold confirmation, if it appears.

        Only when the window is about *feature* (its icon), asks at most
        *max_price* and "don't show confirmation screens" is not ticked;
        otherwise the window is cancelled and the action fails.
        """
        window = ".modalWrapper.confirmGoldUsage"
        try:
            lane.wait_for(f"!!document.querySelector('{window}')", timeout=2.5)
        except ClickModeError:
            return  # confirmations are off: the click already paid
        ok = f"{window} button[clickable='confirm()']"
        info = lane.evaluate(
            "(() => { const w = document.querySelector('" + window + "');"
            " const b = w && w.querySelector(\"button[clickable='confirm()']\");"
            # The icon's case differs per window (premium_npcTrader_… vs NPCTrader).
            " return w ? {icon: [...w.querySelectorAll('i')].some(i => (i.className || '').toLowerCase()"
            ".includes('premium_" + feature.lower() + "_medium_illu')),"
            " price: b ? Number(b.getAttribute('price')) : null,"
            " dontShow: [...w.querySelectorAll('input[type=checkbox]')].some(i => i.checked)} : null; })()"
        ) or {}
        price = info.get("price")
        if (
            not info.get("icon")
            or price is None
            or price > max_price
            or info.get("dontShow")
        ):
            self._close_windows(lane)
            raise ClickModeError(
                f"Okno potwierdzenia złota nie pasuje do akcji (cena {price}, limit {max_price})."
            )
        self._click(lane, ok)
        time.sleep(1.0)

    def _gold(self, lane: _Lane) -> int:
        return int(lane.evaluate("Number(Travian.Player.data.gold) || 0") or 0)

    # -- NPC trader --------------------------------------------------------------
    def _npc_trade(
        self,
        village_id: str,
        location: int,
        expected_level: int,
        is_field: bool,
        costs: dict[int, int],
        max_price: int,
    ) -> str:
        """Use the NPC trader from the building's option menu.

        Hovering the level bubble of a building that cannot be upgraded yet
        opens the game's option menu; its NPC trader button redistributes the
        village's resources to cover that upgrade (the game picks the split).
        The upgrade itself is clicked separately with :meth:`upgrade`.
        """
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        village_id = str(village_id)
        location = int(location)
        page = "resources" if is_field else "village"
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._open_page(lane, page, location)
        state = lane.evaluate(f"{_BUBBLE_STATE_JS}({json.dumps(page)}, {location})")
        if not state or state.get("level") != int(expected_level):
            raise ClickModeError("Poziom budynku w grze różni się od planu.")
        if "possible" in (state.get("layer") or "").split():
            return "not_needed"
        if "notNow" not in (state.get("layer") or "").split():
            raise ClickModeError("Gra nie oferuje handlarza dla tego budynku.")
        status = f".{page}.viewBackground .buildingStatus.location{location}"
        self._hover(lane, f"{status} > .buildingBubble")
        try:
            lane.wait_for(f"!!document.querySelector('{status} .premiumOptionMenu .npcTrader')", timeout=4)
        except ClickModeError:
            raise ClickModeError("Menu z handlarzem NPC się nie otworzyło.") from None
        button = lane.evaluate(
            f"(document.querySelector('{status} .premiumOptionMenu .npcTrader') || {{}}).className || ''"
        )
        classes = button.split()
        if "disabled" in classes:
            self._close_option_menu(lane)
            raise ClickModeError("Handlarz NPC niedostępny dla tej rozbudowy.")
        if "optional" in classes:
            self._close_option_menu(lane)
            raise ClickModeError("Po wymianie u handlarza nadal brakowałoby surowców.")
        price = int(lane.evaluate(
            "Number(((Travian.Config.PremiumFeatures || {}).NPCTrader || {}).price) || 0"
        ) or 0)
        if price > max_price:
            self._close_option_menu(lane)
            raise ClickModeError(f"Handlarz kosztuje {price} złota (limit {max_price}).")
        gold_before = self._gold(lane)
        self._click(lane, f"{status} .premiumOptionMenu .npcTrader")
        self._confirm_gold(lane, "NPCTrader", max_price)
        cost_js = json.dumps({str(k): int(v) for k, v in costs.items()})
        try:
            lane.wait_for(
                "(() => { const r = Village.get(" + json.dumps(village_id) + ").getResources();"
                f" const c = {cost_js};"
                " return Object.keys(c).every(k => Number(r[k]) >= c[k]); })()",
                timeout=10,
            )
        except ClickModeError:
            self._close_option_menu(lane)
            self._close_windows(lane)
            raise ClickModeError("Wymiana u handlarza nie została potwierdzona.") from None
        self._close_option_menu(lane)
        spent = gold_before - self._gold(lane)
        return f"traded:{spent}"

    # -- troops ---------------------------------------------------------------------
    def _send_troops(
        self, source_id, target_x, target_y, movement_type, units,
        catapult_targets=(), spy_mission=None,
    ) -> dict:
        """Send own troops from the rally point window, like a player: pick
        the mission, type the units, check the confirmation, press send."""
        units = {int(nr): int(count) for nr, count in units.items() if int(count) > 0}
        if not units or not str(source_id).isdigit():
            raise ValueError("No units to send.")
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, str(source_id))
        lane.prepare(
            str(source_id), int(target_x), int(target_y), int(movement_type), units,
            tuple(int(t) for t in catapult_targets), spy_mission,
        )
        lane.fire()
        try:
            lane.wait_for(
                "!document.querySelector('.modalWrapper.sendTroops button.sendTroops')", timeout=8,
            )
        except ClickModeError:
            pass
        state = lane.read()
        self._close_windows(lane)
        if not state.get("ok"):
            raise ClickModeError(f"Wojska nie wyruszyły: {state.get('error')}")
        return units

    def _recall_troops(self, village_id: str, location_village_id: str, units: dict[int, int]) -> dict:
        """Bring own reinforcements home from another village, like a player:
        rally point -> Stationed -> Withdraw at that village, type exactly
        *units* (others standing there stay), press Withdraw."""
        village_id = str(village_id)
        location_village_id = str(location_village_id)
        units = {int(nr): int(count) for nr, count in units.items() if int(count) > 0}
        if not units or not village_id.isdigit() or not location_village_id.isdigit():
            raise ValueError("Invalid recall.")
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._show_page(lane, "village")
        location = lane.evaluate(
            "((id) => { const d = ((Building.getCollection(id) || {}).data || []).map(r => r.data)"
            ".find(d => Number(d.buildingType) === 16); return d ? Number(d.locationId) : null; })"
            f"({json.dumps(village_id)})"
        )
        if not location:
            raise ClickModeError("Wioska nie ma placu zbiórki.")
        try:
            lane.wait_for(f"!!document.querySelector('#buildingImage{int(location)}')", timeout=10)
        except ClickModeError:
            raise ClickModeError("Nie znaleziono placu zbiórki w widoku wioski.") from None
        time.sleep(0.6)
        self._close_option_menu(lane)
        self._click(lane, f"#buildingImage{int(location)}", accept=f"openBuildingDialog({int(location)})")
        window = ".modalWrapper.building"
        try:
            lane.wait_for(f"!!document.querySelector('{window} .naviTabDeployed')", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno placu zbiórki się nie otworzyło.") from None
        self._click(lane, f"{window} .naviTabDeployed")
        button = f"{window} .jsButtonSendTroopsBack"
        find = (
            "((target) => { const buttons = [...document.querySelectorAll(" + json.dumps(button) + ")];"
            " return buttons.findIndex(b => { let p = b; for (let i = 0; i < 8 && p; i++) {"
            " if (p.querySelector && p.querySelector(\"[clickable*=\\\"'cellId': \" + target + \"}\\\"]\"))"
            " return true; p = p.parentElement; } return false; }); })"
            f"({json.dumps(location_village_id)})"
        )
        try:
            lane.wait_for(f"({find}) >= 0", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Nie znaleziono posiłków w tej wiosce.") from None
        time.sleep(1.5)  # let the list finish rendering
        self._click(lane, button, int(lane.evaluate(find)))
        dialog = ".modalWrapper.sendTroops"
        withdraw = f"{dialog} button.jsButtonSendTroopsBack"
        try:
            lane.wait_for(f"!!document.querySelector('{withdraw}')", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno wycofania wojsk się nie otworzyło.") from None
        time.sleep(0.5)
        inputs = f"{dialog} input"
        for nr in range(1, 12):
            selector = f"{dialog} input.unitInput{nr - 1}"
            position = lane.rect(selector)
            wanted = units.get(nr, 0)
            if position is None or position.get("disabled"):
                if wanted:
                    self._close_windows(lane)
                    raise ClickModeError(f"Pole jednostki {nr} jest niedostępne.")
                continue
            self._type_at(lane, selector, 0, str(wanted))
        time.sleep(0.4)
        typed = lane.evaluate(
            "[...Array(11).keys()].map(i => { const e = document.querySelector("
            + json.dumps(dialog) + " + ' input.unitInput' + i);"
            " return e ? Number((e.value || '').replace(/\\D/g, '')) || 0 : 0; })"
        ) or []
        if any(int(typed[nr - 1] if nr - 1 < len(typed) else 0) != units.get(nr, 0) for nr in range(1, 12)):
            self._close_windows(lane)
            raise ClickModeError(f"Okno wycofania pokazuje inne jednostki ({typed}) niż plan — przerwano.")
        self._click(lane, withdraw)
        try:
            lane.wait_for(f"!document.querySelector('{withdraw}')", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Gra nie potwierdziła wycofania wojsk.") from None
        self._close_windows(lane)
        return units

    @_exclusive
    def recall_troops(self, village_id, location_village_id, units) -> dict:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "wycofanie wojsk"):
            return self._recall_troops(village_id, location_village_id, units)

    @_exclusive
    def send_troops(
        self, source_id, target_x, target_y, movement_type, units,
        catapult_targets=(), spy_mission=None,
    ) -> dict:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "wysyłka wojsk"):
            return self._send_troops(
                source_id, target_x, target_y, movement_type, units,
                catapult_targets, spy_mission,
            )

    # -- NPC merchant in the marketplace ---------------------------------------------
    def _npc_convert(
        self, village_id: str, marketplace_location: int, gains: dict[int, int], max_price: int,
    ) -> dict:
        """Turn crop into wood, clay and iron in the marketplace's "NPC merchant"
        tab, like a player (see :meth:`_npc_exchange`)."""
        gains = {r: max(0, int(gains.get(r, 0))) for r in (1, 2, 3)}
        total = sum(gains.values())
        if total <= 0:
            raise ValueError("Nothing to convert.")

        def plan(current: list[int]) -> list[int]:
            crop = current[3] - total
            if crop < 0:
                raise ClickModeError("Za mało zboża do wymiany.")
            return [current[0] + gains[1], current[1] + gains[2], current[2] + gains[3], crop]

        outcome = self._npc_exchange(village_id, marketplace_location, plan, max_price)
        before, after = outcome["before"], outcome["after"]
        return {
            "spent": outcome["spent"],
            "gains": {1: after[0] - before[0], 2: after[1] - before[1], 3: after[2] - before[2]},
            "crop": before[3] - after[3],
        }

    def _npc_exchange(self, village_id: str, marketplace_location: int, plan, max_price: int) -> dict:
        """Redistribute the stock in the marketplace's "NPC merchant" tab, like
        a player: type the crop, lock it, type the wood, lock it, type the clay
        (iron takes the rest), check the form, press Convert.

        *plan* gets the form's current amounts [wood, clay, iron, crop] and
        returns the wanted ones (same total)."""
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        village_id = str(village_id)
        location = int(marketplace_location)
        window = ".modalWrapper.building"
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._show_page(lane, "village")
        try:
            lane.wait_for(f"!!document.querySelector('#buildingImage{location}')", timeout=10)
        except ClickModeError:
            raise ClickModeError("Nie znaleziono targowiska w widoku wioski.") from None
        time.sleep(0.6)
        self._close_option_menu(lane)
        self._click(lane, f"#buildingImage{location}", accept=f"openBuildingDialog({location})")
        try:
            lane.wait_for(f"!!document.querySelector('{window} .naviTabNpcTrade')", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno targowiska się nie otworzyło.") from None
        self._click(lane, f"{window} .naviTabNpcTrade")
        form = f"{window} .npcTrader"
        inputs = f"{form} .resSlider input.value"
        try:
            lane.wait_for(f"document.querySelectorAll('{inputs}').length === 4", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Formularz handlarza NPC nie jest dostępny.") from None
        time.sleep(0.8)
        read = (
            f"[...document.querySelectorAll('{inputs}')]"
            ".map(i => Number((i.value || '').replace(/\\D/g, '')) || 0)"
        )
        current = [int(v) for v in (lane.evaluate(read) or [])]
        if len(current) != 4:
            self._close_windows(lane)
            raise ClickModeError("Nie odczytano stanu surowców w formularzu handlarza.")
        try:
            expected = [int(v) for v in plan(current)]
        except Exception:
            self._close_windows(lane)
            raise
        if len(expected) != 4 or min(expected) < 0 or abs(sum(expected) - sum(current)) > 3:
            self._close_windows(lane)
            raise ClickModeError(f"Niepoprawny podział surowców ({expected}) — przerwano.")
        lock = f"{form} .lockSymbol"
        locked = (
            f"(i => !document.querySelectorAll('{form} .lockButtonContainer')[i]"
            ".classList.contains('open'))"
        )
        self._type_at(lane, inputs, 3, str(expected[3]))
        time.sleep(0.6)
        self._click(lane, lock, 3)
        time.sleep(0.5)
        self._type_at(lane, inputs, 0, str(expected[0]))
        time.sleep(0.6)
        self._click(lane, lock, 0)
        time.sleep(0.5)
        self._type_at(lane, inputs, 1, str(expected[1]))
        time.sleep(0.8)
        if not (lane.evaluate(f"{locked}(3)") and lane.evaluate(f"{locked}(0)")):
            self._close_windows(lane)
            raise ClickModeError("Nie udało się zablokować surowców w formularzu handlarza.")
        typed = [int(v) for v in (lane.evaluate(read) or [])]
        if len(typed) != 4 or any(abs(typed[i] - expected[i]) > 2 for i in range(4)):
            self._close_windows(lane)
            raise ClickModeError(
                f"Formularz handlarza pokazuje inny podział ({typed}) niż plan ({expected}) — przerwano."
            )
        price = int(lane.evaluate(
            "Number(((Travian.Config.PremiumFeatures || {}).NPCTrader || {}).price) || 0"
        ) or 0)
        if price > max_price:
            self._close_windows(lane)
            raise ClickModeError(f"Handlarz kosztuje {price} złota (limit {max_price}).")
        button = f"{form} button[premium-feature='NPCTrader']"
        if lane.evaluate(f"document.querySelector(\"{button}\").classList.contains('disabled')"):
            self._close_windows(lane)
            raise ClickModeError("Przycisk wymiany jest nieaktywny.")
        # Watch the resource that changes most, to see the game took the trade.
        watch = max(range(4), key=lambda i: abs(typed[i] - current[i]))
        delta = typed[watch] - current[watch]
        bound = current[watch] + 0.8 * delta
        gold_before = self._gold(lane)
        self._click(lane, button)
        self._confirm_gold(lane, "NPCTrader", max_price)
        try:
            lane.wait_for(
                "Number(Village.get(" + json.dumps(village_id) + f").getResources()[{watch + 1}]) "
                + ("<= " if delta < 0 else ">= ") + str(bound),
                timeout=10,
            )
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Gra nie potwierdziła wymiany u handlarza.") from None
        self._close_windows(lane)
        return {"spent": gold_before - self._gold(lane), "before": current, "after": typed}

    def _marketplace(self, lane: _Lane, village_id: str) -> int:
        location = lane.evaluate(
            "((id) => { const d = ((Building.getCollection(id) || {}).data || []).map(r => r.data)"
            ".find(d => Number(d.buildingType) === 17 && Number(d.lvl) > 0);"
            " return d ? Number(d.locationId) : null; })"
            f"({json.dumps(str(village_id))})"
        )
        if not location:
            raise ClickModeError("Wioska nie ma targowiska.")
        return int(location)

    @_exclusive
    def npc_mill(self, village_id: str, plan, max_price: int, minimum_gold: int) -> dict:
        """Mill a village's stock at the NPC merchant by clicking.

        *plan* maps the current [wood, clay, iron, crop] to the wanted split;
        nothing is clicked when gold would drop under *minimum_gold*."""
        lane = self._ensure_lane()
        if self._gold(lane) < int(minimum_gold) + int(max_price):
            raise ClickModeError("Saldo złota nie pozwala zachować rezerwy po wymianie.")
        location = self._marketplace(lane, str(village_id))
        with self._gold_guard(lane, int(max_price), "mielenie u handlarza NPC"):
            return self._npc_exchange(str(village_id), location, plan, max_price)

    @_exclusive
    def npc_convert(self, village_id, marketplace_location, gains, max_price) -> dict:
        lane = self._ensure_lane()
        with self._gold_guard(lane, int(max_price), "mielenie zboża u handlarza"):
            return self._npc_convert(village_id, marketplace_location, gains, max_price)

    # -- instant finish -----------------------------------------------------------
    def _finish_now(self, village_id: str, queue_type: int, max_price: int) -> int:
        """Finish the running build of *queue_type* from the building queue panel.

        Hovering the running slot shows the game's "finish now" button with
        its price; it is clicked only when the shown price is within
        *max_price*. Returns the price paid.
        """
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        village_id = str(village_id)
        queue_type = int(queue_type)
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._show_page(lane, "resources")
        entry_js = (
            "(() => { const q = BuildingQueue.get(" + json.dumps(village_id) + ");"
            f" const e = ((q && q.data && q.data.queues[{queue_type}]) || [])[0];"
            " return e ? String(e.id) : ''; })()"
        )
        entry_id = lane.evaluate(entry_js)
        if not entry_id:
            return 0
        selector = f'button[premium-feature="finishNow"][premium-callback-param="finishNowQueueType:{queue_type}"]'
        slots = int(lane.evaluate(
            "document.querySelectorAll('.constructionContainer .buildingQueueSlot').length"
        ) or 0)
        found = False
        for index in range(slots):
            position = lane.rect(".constructionContainer .buildingQueueSlot", index)
            if not position:
                continue
            self._move_mouse(lane, position["x"], position["y"])
            try:
                lane.wait_for(f"!!document.querySelector('{selector}')", timeout=2)
                found = True
                break
            except ClickModeError:
                continue
        if not found:
            raise ClickModeError("Nie znaleziono przycisku „Dokończ teraz”.")
        price_text = lane.evaluate(
            f"((document.querySelector('{selector} .price') || {{}}).innerText || '').replace(/\\D/g, '')"
        )
        price = int(price_text) if price_text else 0
        if price > max_price:
            self._move_mouse(lane, 5, 5)
            raise ClickModeError(f"Dokończenie kosztuje {price} złota (limit {max_price}).")
        self._click(lane, selector)
        if price > 0:
            self._confirm_gold(lane, "finishNow", max_price)
        try:
            lane.wait_for(f"({entry_js}) !== {json.dumps(entry_id)}", timeout=10)
        except ClickModeError:
            self._move_mouse(lane, 5, 5)
            self._close_windows(lane)
            raise ClickModeError(
                "Budowa nie została dokończona (być może gra prosi o potwierdzenie złota)."
            ) from None
        self._move_mouse(lane, 5, 5)
        return price

    # -- marketplace -------------------------------------------------------------
    def _type_at(self, lane: _Lane, selector: str, index: int, text: str) -> None:
        self._click(lane, selector, index)
        for kind in ("keyDown", "keyUp"):
            lane.tab.call_method(
                "Input.dispatchKeyEvent", type=kind, modifiers=2, key="a",
                code="KeyA", windowsVirtualKeyCode=65, _timeout=INPUT_TIMEOUT,
            )
        for kind in ("keyDown", "keyUp"):
            lane.tab.call_method(
                "Input.dispatchKeyEvent", type=kind, key="Backspace",
                code="Backspace", windowsVirtualKeyCode=8, _timeout=INPUT_TIMEOUT,
            )
        if text:
            lane.tab.call_method("Input.insertText", text=text, _timeout=INPUT_TIMEOUT)
        time.sleep(0.25)

    def _send_resources(
        self,
        source_id: str,
        marketplace_location: int,
        target_name: str,
        target_x: int,
        target_y: int,
        amounts: dict[int, int],
    ) -> dict[int, int]:
        """Send resources to an own village from the marketplace "Send" tab.

        Opens the marketplace by clicking its building, types the amounts,
        picks the target from the game's own-village list and presses
        "Send resources". Amounts are cut to what the free merchants carry.
        Returns the amounts the game accepted (read back from the form).
        """
        if not str(source_id).isdigit():
            raise ValueError("Invalid village id.")
        source_id = str(source_id)
        location = int(marketplace_location)
        window = ".modalWrapper.building"
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, source_id)
        self._show_page(lane, "village")
        try:
            lane.wait_for(f"!!document.querySelector('#buildingImage{location}')", timeout=10)
        except ClickModeError:
            raise ClickModeError("Nie znaleziono targowiska w widoku wioski.") from None
        time.sleep(0.6)
        self._close_option_menu(lane)
        self._click(lane, f"#buildingImage{location}", accept=f"openBuildingDialog({location})")
        try:
            lane.wait_for(f"!!document.querySelector('{window} .naviTabSend')", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno targowiska się nie otworzyło.") from None
        if "active" not in (lane.evaluate(
            f"(document.querySelector('{window} .naviTabSend') || {{}}).className || ''"
        ) or "").split():
            self._click(lane, f"{window} .naviTabSend")
        inputs = f"{window} .inputContainer input.value"
        try:
            lane.wait_for(f"document.querySelectorAll('{inputs}').length === 4", timeout=6)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Formularz wysyłki nie jest dostępny.") from None

        # Merchants are loaded with the window: cut to what they can carry.
        merchants = lane.evaluate(
            "(() => { const m = Merchants.get(" + json.dumps(source_id) + ");"
            " return m && m.data ? {free: Number(m.data.merchantsFree) || 0,"
            " carry: Number(m.data.carry) || 0} : null; })()"
        ) or {}
        capacity = int(merchants.get("free", 0)) * int(merchants.get("carry", 0))
        wanted = {r: max(0, int(amounts.get(r, 0))) for r in (1, 2, 3, 4)}
        total = sum(wanted.values())
        if capacity <= 0:
            self._close_windows(lane)
            raise ClickModeError("Brak wolnych kupców.")
        if total > capacity:
            scale = capacity / total
            wanted = {r: int(a * scale) for r, a in wanted.items()}
        for index, resource in enumerate((1, 2, 3, 4)):
            self._type_at(lane, inputs, index, str(wanted[resource]) if wanted[resource] else "")

        # Target from the game's list of own villages (opened if hidden).
        label = f"({int(target_x)}|{int(target_y)})"
        own = f"{window} [clickable^='selectOwnVillage']"
        pick = (
            "(() => { const items = [...document.querySelectorAll(\"" + own + "\")]"
            ".filter(e => e.getBoundingClientRect().width > 0);"
            " const clean = t => (t || '').replace(/[\\u200e\\u202a-\\u202e]/g, '').replace(/\\s+/g, '');"
            " return items.findIndex(e => clean((e.closest('li') || e).innerText)"
            ".indexOf(" + json.dumps(label) + ") >= 0); })()"
        )
        if lane.evaluate(pick) is None or int(lane.evaluate(pick)) < 0:
            self._click(lane, f"{window} a.villageQuickSelect")
        try:
            lane.wait_for(f"({pick}) >= 0", timeout=5)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError(f"Nie znaleziono wioski {target_name} na liście.") from None
        index = int(lane.evaluate(pick))
        self._click(lane, own, index)
        send_button = f"{window} button[clickable='sendResources();']"
        try:
            lane.wait_for(
                f"!!document.querySelector(\"{send_button}\")"
                f" && !document.querySelector(\"{send_button}\").classList.contains('disabled')",
                timeout=8,
            )
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Gra nie przyjęła celu wysyłki.") from None
        typed = lane.evaluate(
            f"[...document.querySelectorAll('{inputs}')].map(i => Number((i.value || '').replace(/\\D/g, '')) || 0)"
        ) or []
        accepted = {r: int(typed[i]) for i, r in enumerate((1, 2, 3, 4)) if i < len(typed)}
        sent_total = sum(accepted.values())
        if sent_total <= 0:
            self._close_windows(lane)
            raise ClickModeError("Formularz wysyłki jest pusty.")
        if any(accepted[r] > wanted[r] for r in accepted):
            self._close_windows(lane)
            raise ClickModeError("Gra wpisała inne ilości niż plan — wysyłka przerwana.")
        before_js = (
            "(() => { const r = Village.get(" + json.dumps(source_id) + ").getResources();"
            " return [1, 2, 3, 4].reduce((s, k) => s + (Number(r[k]) || 0), 0); })()"
        )
        before = float(lane.evaluate(before_js) or 0)
        self._click(lane, send_button)
        try:
            lane.wait_for(f"({before_js}) <= {before - 0.8 * sent_total}", timeout=10)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Gra nie potwierdziła wysyłki.") from None
        self._close_windows(lane)
        return accepted

    # -- guarded public actions ----------------------------------------------------
    @_exclusive
    def upgrade(self, village_id: str, location: int, expected_level: int, is_field: bool) -> str:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "rozbudowa"):
            return self._upgrade(village_id, location, expected_level, is_field)

    def _set_hero_production(self, choice: int) -> dict:
        """Pick the hero's resource production in the hero window
        (Attributes) and save, like a player. Returns the amounts shown."""
        choice = int(choice)
        if choice not in range(5):
            raise ValueError("Invalid hero production.")
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._click(lane, "a.heroLink.attributesLink")
        box = ".modalWrapper .resourceProductionBox .resourceOptions a.resourceType"
        try:
            lane.wait_for(f"document.querySelectorAll('{box}').length === 5", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno atrybutów bohatera się nie otworzyło.") from None
        amounts = lane.evaluate(
            f"[...document.querySelectorAll('{box}')].map(e => Number(e.innerText.replace(/[^0-9]/g, '')) || 0)"
        ) or []
        lane.evaluate(
            f"document.querySelectorAll('{box}')[{choice}].scrollIntoView({{block: 'center'}})"
        )
        time.sleep(0.4)
        self._click(lane, box, choice)
        time.sleep(0.5)
        if not lane.evaluate(f"document.querySelectorAll('{box}')[{choice}].classList.contains('active')"):
            self._close_windows(lane)
            raise ClickModeError("Nie udało się wybrać produkcji bohatera.")
        save = ".modalWrapper .saveChanges button"
        lane.evaluate(f"document.querySelector('{save}').scrollIntoView({{block: 'center'}})")
        time.sleep(0.4)
        self._click(lane, save)
        try:
            lane.wait_for(
                "Number(Hero.get(Travian.Player.data.playerId).data.resBonusType) === " + str(choice),
                timeout=10,
            )
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Gra nie zapisała produkcji bohatera.") from None
        self._close_windows(lane)
        return {"all": amounts[0] if amounts else 0, "single": max(amounts[1:], default=0)}

    @_exclusive
    def set_hero_production(self, choice: int) -> dict:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "produkcja bohatera"):
            return self._set_hero_production(choice)

    @_exclusive
    def npc_trade(self, village_id, location, expected_level, is_field, costs, max_price) -> str:
        lane = self._ensure_lane()
        with self._gold_guard(lane, int(max_price), "handlarz NPC"):
            return self._npc_trade(village_id, location, expected_level, is_field, costs, max_price)

    @_exclusive
    def finish_now(self, village_id: str, queue_type: int, max_price: int) -> int:
        lane = self._ensure_lane()
        with self._gold_guard(lane, int(max_price), "dokończ teraz"):
            before = self._gold(lane)
            price = self._finish_now(village_id, queue_type, max_price)
            # Count what really left the balance (it may update a moment later).
            spent = before - self._gold(lane)
            deadline = time.monotonic() + 3
            while price > 0 and spent <= 0 and time.monotonic() < deadline:
                time.sleep(0.3)
                spent = before - self._gold(lane)
            # No visible change yet: count the shown price (never undercount).
            return spent if spent > 0 else price

    @_exclusive
    def send_resources(self, source_id, marketplace_location, target_name, target_x, target_y, amounts):
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "wysyłka surowców"):
            return self._send_resources(
                source_id, marketplace_location, target_name, target_x, target_y, amounts,
            )

    # -- master builder -------------------------------------------------------------
    def _queue_master(self, village_id: str, location: int, current_level: int, is_field: bool) -> str:
        """Add the next level of a building to the master builder queue.

        While the building queue is busy, hovering a building's level bubble
        opens the game's option menu; its builder bubble ("possible" with a
        free master builder slot) queues the upgrade, which then starts on its
        own as soon as the queue frees. Never used to buy a slot.
        """
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        village_id = str(village_id)
        location = int(location)
        page = "resources" if is_field else "village"
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._open_page(lane, page, location)
        free = lane.evaluate(
            "(() => { const q = BuildingQueue.get(" + json.dumps(village_id) + ");"
            " return q && q.data ? Number(q.data.freeSlots[4]) || 0 : 0; })()"
        )
        if not free:
            raise ClickModeError("Brak wolnego miejsca u budowniczego.")
        state = lane.evaluate(f"{_BUBBLE_STATE_JS}({json.dumps(page)}, {location})")
        if not state or state.get("level") != int(current_level):
            raise StaleStateError(
                f"Poziom w grze to {state and state.get('level')}, a plan zakładał {current_level}."
            )
        if "notNow" not in (state.get("layer") or "").split():
            # The queue is free (or the read is outdated): nothing to line up.
            raise StaleStateError("Kolejka jest już wolna — dane do odświeżenia.")
        status = f".{page}.viewBackground .buildingStatus.location{location}"
        count_js = (
            "(() => { const q = BuildingQueue.get(" + json.dumps(village_id) + ");"
            f" return ((q && q.data && q.data.queues[4]) || []).filter(e => Number(e.locationId) === {location}).length; }})()"
        )
        before = int(lane.evaluate(count_js) or 0)
        self._hover(lane, f"{status} > .buildingBubble")
        menu_bubble = f"{status} .premiumOptionMenu .buildingBubble"
        try:
            lane.wait_for(f"!!document.querySelector('{menu_bubble}')", timeout=4)
        except ClickModeError:
            raise StaleStateError("Menu budowniczego się nie otworzyło — ponowię.") from None
        layer = lane.evaluate(
            f"(document.querySelector('{menu_bubble} .colorLayer') || {{}}).className || ''"
        ) or ""
        if "possible" not in layer.split():
            self._close_option_menu(lane)
            raise ClickModeError("Budowniczy niedostępny (to mogłoby być kupno miejsca za złoto).")
        self._click(lane, menu_bubble)
        try:
            lane.wait_for(f"({count_js}) > {before}", timeout=10)
        except ClickModeError:
            self._close_option_menu(lane)
            self._close_windows(lane)
            raise ClickModeError("Rozbudowa nie pojawiła się w kolejce budowniczego.") from None
        self._close_option_menu(lane)
        # With enough resources the game reserves them right away ("paid").
        paid = lane.evaluate(
            "(() => { const q = BuildingQueue.get(" + json.dumps(village_id) + ");"
            f" const e = ((q && q.data && q.data.queues[4]) || []).filter(x => Number(x.locationId) === {location});"
            " return e.length ? !!e[e.length - 1].paid : false; })()"
        )
        return "reserved" if paid else "queued"

    @_exclusive
    def queue_master(self, village_id: str, location: int, current_level: int, is_field: bool) -> str:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "kolejka budowniczego"):
            return self._queue_master(village_id, location, current_level, is_field)

    def _cancel_master(self, village_id: str, entry_id: str) -> str:
        """Remove one unreserved entry from the master builder queue.

        Hovering a master builder slot shows the game's cancel cross for that
        entry; only an entry without reserved resources is ever cancelled.
        """
        if not str(village_id).isdigit() or not str(entry_id).isdigit():
            raise ValueError("Invalid id.")
        village_id = str(village_id)
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._show_page(lane, "resources")
        entry_js = (
            "(() => { const q = BuildingQueue.get(" + json.dumps(village_id) + ");"
            " const e = ((q && q.data && q.data.queues[4]) || [])"
            f".find(x => String(x.id) === {json.dumps(str(entry_id))});"
            " return e ? {paid: !!e.paid} : null; })()"
        )
        entry = lane.evaluate(entry_js)
        if not entry:
            raise StaleStateError("Tego wpisu nie ma już u budowniczego.")
        if entry.get("paid"):
            raise ClickModeError("Wpis ma zarezerwowane surowce — nie anuluję.")
        cancel = f"[clickable='cancelBuilding({int(entry_id)})']"
        found = False
        for slot in range(1, 5):
            selector = f".masterBuilderContainer .buildingQueueSlot.slot{slot}"
            if not lane.rect(selector):
                continue
            self._hover(lane, selector)
            try:
                lane.wait_for(
                    f"[...document.querySelectorAll(\"{cancel}\")].some(e => e.getBoundingClientRect().width > 0)",
                    timeout=2,
                )
                found = True
                break
            except ClickModeError:
                continue
        if not found:
            self._move_mouse(lane, 5, 5)
            raise StaleStateError("Nie znaleziono krzyżyka anulowania wpisu.")
        self._click(lane, cancel)
        try:
            lane.wait_for(f"({entry_js}) === null", timeout=8)
        except ClickModeError:
            self._move_mouse(lane, 5, 5)
            self._close_windows(lane)
            raise ClickModeError("Wpis nie zniknął z kolejki budowniczego.") from None
        self._move_mouse(lane, 5, 5)
        return "cancelled"

    @_exclusive
    def cancel_master(self, village_id: str, entry_id: str) -> str:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "anulowanie u budowniczego"):
            return self._cancel_master(village_id, entry_id)

    # -- recruitment --------------------------------------------------------------
    def _recruit(
        self,
        village_id: str,
        building_types: list[int],
        unit_type: int,
        max_quantity: int,
        min_quantity: int,
        queue_threshold: int,
        resource_share: float,
        max_queue_seconds: float = 0,
        unit_seconds: float = 0,
    ) -> dict:
        """Train units like a player: open the barracks/stable, pick the unit,
        type the amount and press "Train".

        The amount is what the village can afford with *resource_share* of
        its stock, at most *max_quantity*, never below *min_quantity*. The
        queue is re-checked first (*queue_threshold*). Returns a status dict
        like the old request-based order ("confirmed" with the new count).
        """
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        village_id = str(village_id)
        unit_type = int(unit_type)
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._show_page(lane, "village")
        info = lane.evaluate(
            "((villageId, types) => { const c = Building.getCollection(villageId);"
            " const b = ((c && c.data) || []).map(r => r.data).find(d => types.includes(Number(d.buildingType)));"
            " return b ? {location: Number(b.locationId), type: Number(b.buildingType)} : null; })"
            f"({json.dumps(village_id)}, {json.dumps([int(t) for t in building_types])})"
        )
        if not info:
            return {"status": "building_missing"}
        location = int(info["location"])
        try:
            lane.wait_for(f"!!document.querySelector('#buildingImage{location}')", timeout=10)
        except ClickModeError:
            raise StaleStateError("Budynek rekrutacji nie jest widoczny.") from None
        self._close_option_menu(lane)
        self._click(lane, f"#buildingImage{location}", accept=f"openBuildingDialog({location})")
        window = ".modalWrapper.building"
        item = f"{window} .item.unit .clickableContainer img[data='{unit_type}']"
        try:
            lane.wait_for(f"!!document.querySelector(\"{item}\")", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno rekrutacji nie pokazało tej jednostki.") from None
        # Queue and affordable amount, read after the window loaded its data.
        state = lane.evaluate(
            "((villageId, types, unitType, share) => {"
            " const q = UnitQueue.get(villageId);"
            " const count = types.reduce((sum, t) => sum + (((q && q.data && q.data.buildingTypes"
            " && q.data.buildingTypes[String(t)]) || []).filter(e => Number(e.unitType) === unitType)"
            ".reduce((s, e) => s + (Number(e.count) || 0), 0)), 0);"
            " const troop = Travian.Config.troopConfig[unitType];"
            " const res = Village.get(villageId).getResources();"
            " let afford = 1e9;"
            " for (const [k, v] of Object.entries(troop.costs || {})) {"
            "   if (Number(v) > 0) afford = Math.min(afford, Math.floor(Number(res[k]) * share / Number(v))); }"
            " return {count, afford}; })"
            f"({json.dumps(village_id)}, {json.dumps([int(t) for t in building_types])}, {unit_type}, {float(resource_share)})"
        ) or {}
        count = int(state.get("count") or 0)
        if count > queue_threshold:
            self._close_windows(lane)
            return {"status": "queue_not_low", "count": count}
        quantity = min(int(max_quantity), int(state.get("afford") or 0))
        if max_queue_seconds:
            queue = lane.evaluate(
                "((villageId, types, unitType, fallback) => {"
                " const q = UnitQueue.get(villageId);"
                " const now = Number(angular.element(document.body).injector().get('$rootScope').currentServerTime);"
                " let end = now, per = fallback;"
                " for (const t of types) for (const e of ((q && q.data && q.data.buildingTypes"
                " && q.data.buildingTypes[String(t)]) || [])) {"
                "   end = Math.max(end, Number(e.timeFinishedLast) || 0);"
                "   if (Number(e.unitType) === unitType && Number(e.durationPerUnit) > 0) per = Number(e.durationPerUnit); }"
                " return {left: end - now, per}; })"
                f"({json.dumps(village_id)}, {json.dumps([int(t) for t in building_types])}, {unit_type}, {float(unit_seconds or 0)})"
            ) or {}
            per = float(queue.get("per") or 0)
            if per > 0:
                room = int((float(max_queue_seconds) - float(queue.get("left") or 0)) // per)
                quantity = min(quantity, room)
        if quantity < int(min_quantity) or quantity <= 0:
            self._close_windows(lane)
            return {"status": "insufficient_resources", "count": count}
        container = f"{window} .item.unit .clickableContainer"
        index = lane.evaluate(
            f"[...document.querySelectorAll(\"{container}\")].filter(e => e.getBoundingClientRect().width > 0)"
            f".findIndex(e => !!e.querySelector(\"img[data='{unit_type}']\"))"
        )
        if index is None or int(index) < 0:
            self._close_windows(lane)
            raise ClickModeError("Nie znaleziono jednostki w oknie rekrutacji.")
        self._click(lane, container, int(index))
        time.sleep(0.5)
        if not lane.evaluate(
            f"!!document.querySelector(\"{container}.active img[data='{unit_type}']\")"
        ):
            self._close_windows(lane)
            raise ClickModeError("Nie udało się wybrać jednostki.")
        self._type_at(lane, f"{window} .inputContainer input.value", 0, str(quantity))
        typed = lane.evaluate(
            f"Number(((document.querySelector('{window} .inputContainer input.value') || {{}}).value || '').replace(/\\D/g, '')) || 0"
        )
        if int(typed or 0) != quantity:
            self._close_windows(lane)
            raise ClickModeError(f"Gra wpisała {typed} zamiast {quantity}.")
        button = f"{window} button[clickable='startTraining(activeItem)']"
        self._click(lane, button)
        count_js = (
            "((villageId, types, unitType) => { const q = UnitQueue.get(villageId);"
            " return types.reduce((sum, t) => sum + (((q && q.data && q.data.buildingTypes"
            " && q.data.buildingTypes[String(t)]) || []).filter(e => Number(e.unitType) === unitType)"
            ".reduce((s, e) => s + (Number(e.count) || 0), 0)), 0); })"
            f"({json.dumps(village_id)}, {json.dumps([int(t) for t in building_types])}, {unit_type})"
        )
        try:
            lane.wait_for(f"({count_js}) > {count}", timeout=10)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Jednostki nie pojawiły się w kolejce.") from None
        new_count = int(lane.evaluate(count_js) or 0)
        # How long the building's training queue now is (seconds).
        queue_seconds = lane.evaluate(
            "((villageId, types) => { const q = UnitQueue.get(villageId);"
            " const now = Number(angular.element(document.body).injector().get('$rootScope').currentServerTime);"
            " let end = now; for (const t of types) for (const e of ((q && q.data && q.data.buildingTypes"
            " && q.data.buildingTypes[String(t)]) || [])) end = Math.max(end, Number(e.timeFinishedLast) || 0);"
            " return end - now; })"
            f"({json.dumps(village_id)}, {json.dumps([int(t) for t in building_types])})"
        )
        self._close_windows(lane)
        return {"status": "confirmed", "count": new_count, "quantity": quantity,
                "queue_seconds": float(queue_seconds or 0)}

    @_exclusive
    def recruit(self, village_id, building_types, unit_type, max_quantity, min_quantity,
                queue_threshold, resource_share, max_queue_seconds=0, unit_seconds=0) -> dict:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "rekrutacja"):
            return self._recruit(
                village_id, building_types, unit_type, max_quantity, min_quantity,
                queue_threshold, resource_share, max_queue_seconds, unit_seconds,
            )

    def _reserve_master(self, village_id: str, entry_id: str) -> str:
        """Reserve resources for a master builder entry (its slot's button)."""
        if not str(village_id).isdigit() or not str(entry_id).isdigit():
            raise ValueError("Invalid id.")
        village_id = str(village_id)
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._show_page(lane, "resources")
        paid_js = (
            "(() => { const q = BuildingQueue.get(" + json.dumps(village_id) + ");"
            " const e = ((q && q.data && q.data.queues[4]) || [])"
            f".find(x => String(x.id) === {json.dumps(str(entry_id))});"
            " return e ? !!e.paid : null; })()"
        )
        paid = lane.evaluate(paid_js)
        if paid is None:
            raise StaleStateError("Tego wpisu nie ma już u budowniczego.")
        if paid:
            return "already"
        button = f"button[clickable='reserveResources({int(entry_id)})']"
        found = False
        for slot in range(1, 5):
            selector = f".masterBuilderContainer .buildingQueueSlot.slot{slot}"
            if not lane.rect(selector):
                continue
            self._hover(lane, selector)
            try:
                lane.wait_for(
                    f"[...document.querySelectorAll(\"{button}\")].some(e => e.getBoundingClientRect().width > 0)",
                    timeout=2,
                )
                found = True
                break
            except ClickModeError:
                continue
        if not found:
            self._move_mouse(lane, 5, 5)
            raise StaleStateError("Nie znaleziono przycisku rezerwacji.")
        if lane.evaluate(f"document.querySelector(\"{button}\").classList.contains('disabled')"):
            self._move_mouse(lane, 5, 5)
            return "not_affordable"
        self._click(lane, button)
        try:
            lane.wait_for(f"({paid_js}) === true", timeout=8)
        except ClickModeError:
            self._move_mouse(lane, 5, 5)
            raise ClickModeError("Gra nie zarezerwowała surowców.") from None
        self._move_mouse(lane, 5, 5)
        return "reserved"

    @_exclusive
    def reserve_master(self, village_id: str, entry_id: str) -> str:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "rezerwacja u budowniczego"):
            return self._reserve_master(village_id, entry_id)

    # -- new buildings ---------------------------------------------------------------
    def _click_site(self, lane: _Lane, location: int) -> None:
        """Click an empty building site (its area on the village image map)."""
        point = lane.evaluate(
            "((loc) => { const area = [...document.querySelectorAll('area')]"
            ".find(a => (a.getAttribute('clickable') || '').indexOf('openBuildingDialog(' + loc + ')') >= 0);"
            " if (!area) return null; const map = area.closest('map');"
            " const img = document.querySelector('img[usemap=\"#' + map.name + '\"]');"
            " if (!img) return null; const r = img.getBoundingClientRect();"
            " const c = area.getAttribute('coords').split(',').map(Number); let x = 0, y = 0;"
            " for (let i = 0; i < c.length; i += 2) { x += c[i]; y += c[i + 1]; }"
            " const n = c.length / 2; const px = r.left + x / n, py = r.top + y / n;"
            " const hit = document.elementFromPoint(px, py);"
            " return {x: px, y: py, ok: !!hit && (hit.getAttribute('clickable') || '')"
            ".indexOf('openBuildingDialog(' + loc + ')') >= 0}; })"
            f"({int(location)})"
        )
        if not point:
            raise StaleStateError(f"Nie znaleziono pustej działki {location}.")
        if not point.get("ok"):
            raise ClickModeError("Kliknięcie wstrzymane — pod kursorem nie ma tej działki.")
        for event in ("mouseMoved", "mousePressed", "mouseReleased"):
            lane.tab.call_method(
                "Input.dispatchMouseEvent", type=event, x=point["x"], y=point["y"],
                button="left" if event != "mouseMoved" else "none", clickCount=1,
                _timeout=INPUT_TIMEOUT,
            )

    def _construct(self, village_id: str, location: int, building_type: int) -> str:
        """Put a new building on an empty site, like a player.

        Clicks the empty site, picks the building in the construction window
        and presses "Build" (or "Add to building queue" when the queue is
        busy — the master builder; never buys a slot). The building queue is
        checked afterwards.
        """
        if not str(village_id).isdigit():
            raise ValueError("Invalid village id.")
        village_id = str(village_id)
        location = int(location)
        building_type = int(building_type)
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        self._select_village(lane, village_id)
        self._show_page(lane, "village")
        free = lane.evaluate(
            "((villageId, loc) => { const c = Building.getCollection(villageId);"
            " const b = ((c && c.data) || []).map(r => r.data).find(d => Number(d.locationId) === loc);"
            " return !!b && Number(b.buildingType) === 0; })"
            f"({json.dumps(village_id)}, {location})"
        )
        if not free:
            raise StaleStateError("Działka nie jest już pusta.")
        self._close_option_menu(lane)
        self._click_site(lane, location)
        window = ".modalWrapper.building"
        item_img = f"{window} .item.building .clickableContainer img.buildingType{building_type}"
        try:
            lane.wait_for(f"!!document.querySelector('{window} .item.building')", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno budowy się nie otworzyło.") from None
        # The carousel shows a few buildings per page: turn pages until visible.
        visible_js = (
            f"[...document.querySelectorAll('{item_img}')]"
            ".some(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"
        )
        for _ in range(6):
            if lane.evaluate(visible_js):
                break
            if not lane.rect(f"{window} .arrowDirectionTo"):
                break
            self._click(lane, f"{window} .arrowDirectionTo")
            time.sleep(0.6)
        if not lane.evaluate(visible_js):
            self._close_windows(lane)
            raise ClickModeError("Tego budynku nie ma na liście do postawienia.")
        container = f"{window} .item.building .clickableContainer"
        index = lane.evaluate(
            f"[...document.querySelectorAll('{container}')].filter(e => e.getBoundingClientRect().width > 0)"
            f".findIndex(e => !!e.querySelector('img.buildingType{building_type}'))"
        )
        if index is None or int(index) < 0:
            self._close_windows(lane)
            raise ClickModeError("Nie znaleziono budynku w oknie budowy.")
        self._click(lane, container, int(index))
        time.sleep(0.6)
        if not lane.evaluate(f"!!document.querySelector('{container}.active img.buildingType{building_type}')"):
            self._close_windows(lane)
            raise ClickModeError("Nie udało się wybrać budynku.")
        button = (
            f"[...document.querySelectorAll('{window} button.startConstruction')]"
            ".filter(e => e.getBoundingClientRect().width > 0 && !e.classList.contains('disabled'))"
        )
        found = lane.evaluate(f"{button}.length")
        if not found:
            self._close_windows(lane)
            raise ClickModeError("Gra nie pozwala teraz postawić tego budynku (surowce lub kolejka).")
        master = bool(lane.evaluate(f"{button}[0].classList.contains('masterBuilder')"))
        selector = f"{window} button.startConstruction" + (".masterBuilder" if master else ":not(.masterBuilder)")
        queued_js = (
            "((villageId, loc, type) => { const q = BuildingQueue.get(villageId);"
            " return [1, 2, 4].some(t => ((q && q.data && q.data.queues[t]) || [])"
            ".some(e => Number(e.locationId) === loc && Number(e.buildingType) === type)); })"
            f"({json.dumps(village_id)}, {location}, {building_type})"
        )
        self._click(lane, selector)
        try:
            lane.wait_for(queued_js, timeout=10)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Nowy budynek nie pojawił się w kolejce.") from None
        self._close_windows(lane)
        return "master" if master else "started"

    @_exclusive
    def construct(self, village_id: str, location: int, building_type: int) -> str:
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "nowy budynek"):
            return self._construct(village_id, location, building_type)

    # -- vacation mode -------------------------------------------------------------
    def _vacation_state(self, lane: _Lane) -> dict:
        return lane.evaluate(
            "(() => { const d = Travian.Player.data; return {state: Number(d.vacationState),"
            " start: Number(d.vacationStateStart) || 0, used: Number(d.usedVacationDays) || 0}; })()"
        ) or {}

    def _change_vacation(self, days: int | None) -> dict:
        """Plan vacation mode for *days* or cancel a planned one (``None``),
        like a player: Settings -> Game -> duration list -> Confirm."""
        lane = self._ensure_lane()
        lane.wake()
        self._close_windows(lane)
        state = self._vacation_state(lane)
        wanted_from, wanted_to = (VACATION_INACTIVE, VACATION_ACTIVATING) if days else (
            VACATION_ACTIVATING, VACATION_INACTIVE)
        if state.get("state") != wanted_from:
            raise ClickModeError("Stan trybu urlopowego w grze nie pozwala na tę zmianę.")
        self._click(lane, ".headerButton[clickable*=\"'profileTab': 'settings'\"]", accept="openWindow('profile'")
        window = ".modalWrapper.profile"
        try:
            lane.wait_for(f"!!document.querySelector('{window} .naviTabGameplay')", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Okno ustawień się nie otworzyło.") from None
        if not lane.evaluate(
            f"document.querySelector('{window} .naviTabGameplay').classList.contains('active')"
        ):
            self._click(lane, f"{window} .naviTabGameplay")
        button = f"{window} button[clickable='changeVacationState()']"
        try:
            lane.wait_for(f"!!document.querySelector(\"{button}\")", timeout=8)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Nie znaleziono ustawień trybu urlopowego.") from None
        time.sleep(0.5)
        if days:
            head = f"{window} [data='vacationDaysDropdown'] .dropdownHead"
            if not lane.rect(head):
                self._close_windows(lane)
                raise ClickModeError("Gra nie pozwala zaplanować urlopu (brak wolnych dni).")
            self._click(lane, head)
            option = f".dropdownBody .dropDownItem{int(days) - 1}"
            visible = (
                f"[...document.querySelectorAll('{option}')]"
                ".some(e => e.getBoundingClientRect().width > 0)"
            )
            try:
                lane.wait_for(visible, timeout=4)
            except ClickModeError:
                self._close_windows(lane)
                raise ClickModeError(f"Nie można wybrać {days} dni urlopu.") from None
            self._click(lane, option)
            time.sleep(0.5)
            shown = lane.evaluate(
                f"(document.querySelector(\"{head} .selectedItem\") || {{}}).innerText || ''"
            ) or ""
            if str(int(days)) not in shown.split():
                self._close_windows(lane)
                raise ClickModeError(f"Lista pokazuje „{shown}” zamiast {days} dni — przerwano.")
        elif not lane.evaluate(f"document.querySelector(\"{button}\").classList.contains('cancel')"):
            self._close_windows(lane)
            raise ClickModeError("Przycisk nie anuluje urlopu — przerwano.")
        self._click(lane, button)
        try:
            lane.wait_for(f"Number(Travian.Player.data.vacationState) === {wanted_to}", timeout=10)
        except ClickModeError:
            self._close_windows(lane)
            raise ClickModeError("Gra nie potwierdziła zmiany trybu urlopowego.") from None
        self._close_windows(lane)
        return self._vacation_state(lane)

    @_exclusive
    def change_vacation(self, days: int | None) -> dict:
        if days is not None and not (isinstance(days, int) and 1 <= days <= 14):
            raise ValueError("Invalid vacation length.")
        lane = self._ensure_lane()
        with self._gold_guard(lane, 0, "tryb urlopowy"):
            return self._change_vacation(days)
