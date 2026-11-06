"""Click-simulation execution mode for the attack automation.

Instead of sending game requests directly, this mode drives the game's own
"Send troops" window with mouse and keyboard events dispatched through Chrome
DevTools (the operating system cursor is never used). Every wave gets its own
background game tab ("lane") so all waves can be prepared in advance and their
"Send" buttons clicked one after another in the target second:

1. ``prepare``: open a lane tab, open the send window for the target, pick the
   movement type, type the unit counts, press "Next", choose catapult targets
   and check the confirmation screen shows exactly the planned units;
2. ``fire``: click "Send" in every lane;
3. ``read``: report which lanes the game accepted.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import pychrome

logger = logging.getLogger(__name__)

# Tabs opened by the assistant; the main game connection must ignore them.
DEDICATED_TAB_IDS: set[str] = set()

_READY_JS = (
    "!!(window.Travian && Travian.Player && Travian.Player.data"
    " && Travian.Player.data.playerId && document.querySelector('#villageList'))"
)

_RECT_JS = r"""((selector, index) => {
    const nodes = [...document.querySelectorAll(selector)].filter(node => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    });
    const node = nodes[index || 0];
    if (!node) return null;
    node.scrollIntoView({block: "center", inline: "center"});
    const rect = node.getBoundingClientRect();
    return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2,
            disabled: node.classList.contains("disabled") || !!node.disabled,
            text: (node.innerText || "").trim().slice(0, 80)};
})"""

_WINDOW = ".modalWrapper.sendTroops"


class ClickModeError(RuntimeError):
    """The game window did not behave as expected."""


class _Lane:
    """One background game tab prepared to send one wave."""

    def __init__(self, browser: pychrome.Browser, game_url: str):
        self.browser = browser
        self.game_url = game_url
        self.tab = None
        self.target_id = None
        self.result: dict | None = None

    # -- low level -----------------------------------------------------------
    def open(self) -> None:
        opener = self.browser.new_tab("about:blank")
        DEDICATED_TAB_IDS.add(opener.id)
        self.target_id = opener.id
        self.tab = opener
        self.tab.start()
        self.tab.call_method("Page.enable")
        self.tab.call_method("Page.navigate", url=self.game_url, _timeout=15)
        self.wake()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                self.wake()
                if self.evaluate(_READY_JS):
                    time.sleep(2.0)
                    return
            except Exception:
                continue
        raise ClickModeError("Karta gry do wysyłki nie załadowała się.")

    def wake(self) -> None:
        for method, params in (
            ("Page.setWebLifecycleState", {"state": "active"}),
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
        ):
            try:
                self.tab.call_method(method, _timeout=5, **params)
            except Exception:
                pass

    def evaluate(self, expression: str, timeout: int = 10):
        result = self.tab.call_method(
            "Runtime.evaluate", expression=expression, returnByValue=True, _timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise ClickModeError("Błąd skryptu w karcie wysyłki.")
        return result.get("result", {}).get("value")

    def rect(self, selector: str, index: int = 0) -> dict | None:
        return self.evaluate(f"{_RECT_JS}({json.dumps(selector)}, {int(index)})")

    def click(self, selector: str, index: int = 0, *, allow_disabled: bool = False) -> None:
        position = self.rect(selector, index)
        if not position:
            raise ClickModeError(f"Nie znaleziono elementu {selector}.")
        if position["disabled"] and not allow_disabled:
            raise ClickModeError(f"Element {selector} jest nieaktywny.")
        for event in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.tab.call_method(
                "Input.dispatchMouseEvent",
                type=event, x=position["x"], y=position["y"],
                button="left" if event != "mouseMoved" else "none",
                clickCount=1,
            )

    def type_into(self, selector: str, text: str) -> None:
        self.click(selector)
        self.tab.call_method(
            "Input.dispatchKeyEvent", type="keyDown", modifiers=2, key="a",
            code="KeyA", windowsVirtualKeyCode=65,
        )
        self.tab.call_method(
            "Input.dispatchKeyEvent", type="keyUp", modifiers=2, key="a",
            code="KeyA", windowsVirtualKeyCode=65,
        )
        self.tab.call_method(
            "Input.dispatchKeyEvent", type="keyDown", key="Backspace",
            code="Backspace", windowsVirtualKeyCode=8,
        )
        self.tab.call_method(
            "Input.dispatchKeyEvent", type="keyUp", key="Backspace",
            code="Backspace", windowsVirtualKeyCode=8,
        )
        self.tab.call_method("Input.insertText", text=text)

    def wait_for(self, expression: str, timeout: float = 8.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(0.15)
        raise ClickModeError("Gra nie pokazała oczekiwanego ekranu wysyłki.")

    def close(self) -> None:
        if self.tab is not None:
            try:
                self.tab.stop()
            except Exception:
                pass
        if self.target_id:
            try:
                self.browser.close_tab(self.target_id)
            except Exception:
                pass
            DEDICATED_TAB_IDS.discard(self.target_id)
        self.tab = None

    # -- game flow -------------------------------------------------------------
    def prepare(
        self,
        source_village_id: str,
        target_x: int,
        target_y: int,
        movement_type: int,
        units: dict[int, int],
        catapult_targets: tuple[int, ...],
    ) -> None:
        self.wake()
        # Make the source village active so the window sends from it.
        self.evaluate(
            "(() => { location.hash = location.hash.replace(/villId:\\d+/, "
            f"'villId:{int(source_village_id)}'); return 1; }})()"
        )
        time.sleep(1.0)
        self.evaluate(
            "(() => { const s = angular.element(document.body).injector()"
            ".get('$rootScope'); s.openWindow('sendTroops', "
            f"{{x: {int(target_x)}, y: {int(target_y)}}}); "
            "if (!s.$$phase) s.$apply(); return 1; })()"
        )
        self.wait_for(
            f"!!document.querySelector('{_WINDOW} .missionType{int(movement_type)}')"
            f" || !!document.querySelector('{_WINDOW} button.sendTroops')"
        )
        if self.rect(f"{_WINDOW} button.back"):
            self.click(f"{_WINDOW} button.back")
            time.sleep(0.4)
        active = self.evaluate(
            f"(document.querySelector('{_WINDOW} .villageListDropDown .selected, "
            f"{_WINDOW} .dropdownHead') || {{}}).innerText || ''"
        )
        logger.debug("Send window source: %s", active)
        self.wait_for(
            f"!!document.querySelector('{_WINDOW} .missionType{int(movement_type)}')"
        )
        self.click(f"{_WINDOW} .missionType{int(movement_type)}")
        time.sleep(0.3)
        for unit in range(1, 12):
            selector = f"{_WINDOW} input.unitInput{unit - 1}"
            count = int(units.get(unit, 0))
            position = self.rect(selector)
            if position is None or position["disabled"]:
                if count:
                    raise ClickModeError(f"Pole jednostki {unit} jest niedostępne.")
                continue
            self.type_into(selector, str(count) if count else "0")
        time.sleep(0.4)
        self.click(f"{_WINDOW} button.next")
        self.wait_for(f"!!document.querySelector('{_WINDOW} button.sendTroops')")
        if catapult_targets:
            self._choose_catapult_targets(catapult_targets)
        self._verify_confirmation(units)

    def _choose_catapult_targets(self, targets: tuple[int, ...]) -> None:
        """Pick catapult targets in the confirmation dropdowns.

        The game's dropdown keys are building types, with key 0 meaning a
        random building (sent to the server as 99).
        """
        for index, target in enumerate(targets[:2]):
            key = 0 if target == 99 else int(target)
            head = f"{_WINDOW} [data='catapultTargets{index + 1}'] .dropdownHead"
            if not self.rect(head):
                if index == 0 and key == 0:
                    return  # no dropdown (rally point < 3): the game aims at random
                raise ClickModeError("Nie znaleziono wyboru celu katapult.")
            self.click(head)
            time.sleep(0.35)
            option = f".dropdownBody .dropDownItem{key}, .dropdownBody .dropdownOption.dropDownItem{key}"
            if not self.rect(option):
                raise ClickModeError(f"Brak opcji celu katapult {target}.")
            self.click(option)
            time.sleep(0.3)

    def _verify_confirmation(self, units: dict[int, int]) -> None:
        shown = self.evaluate(
            "(() => { const out = {}; for (let n = 1; n <= 10; n++) {"
            f" const cells = document.querySelectorAll('{_WINDOW} .chosenUnits td.unit' + n);"
            " const cell = cells[cells.length - 1];"
            " const text = cell ? (cell.innerText || '').replace(/[^0-9]/g, '') : '';"
            " out[n] = text ? Number(text) : 0; } return out; })()"
        ) or {}
        mismatched = [
            unit for unit in range(1, 11)
            if int(shown.get(str(unit), shown.get(unit, 0)) or 0) != int(units.get(unit, 0))
        ]
        if mismatched:
            raise ClickModeError(
                "Ekran potwierdzenia pokazuje inne jednostki niż plan "
                f"(różnice: {', '.join(map(str, mismatched))}) — nie wysyłam."
            )

    def fire(self) -> None:
        self.click(f"{_WINDOW} button.sendTroops")

    def read(self) -> dict:
        state = self.evaluate(
            "(() => { const w = document.querySelector('" + _WINDOW + "');"
            " const error = w && w.querySelector('.error, .errorMessage');"
            " return {open: !!w, error: error ? error.innerText.trim() : null}; })()"
        ) or {}
        if state.get("error"):
            return {"ok": False, "error": state["error"]}
        if state.get("open") and self.rect(f"{_WINDOW} button.sendTroops"):
            return {"ok": False, "error": "Okno wysyłki nadal otwarte"}
        return {"ok": True, "error": None}


class ClickAttackExecutor:
    """Prepare and fire attack waves by clicking the game UI."""

    def __init__(self, session):
        self._session = session
        self._lanes: dict[str, list[_Lane]] = {}
        self._fired: dict[str, dict] = {}

    def _browser_and_url(self) -> tuple[pychrome.Browser, str]:
        tab = getattr(self._session, "_tab", None)
        browser = getattr(self._session, "_browser", None)
        if tab is None or browser is None:
            raise ClickModeError("Brak połączenia z grą.")
        url = (getattr(tab, "_kwargs", {}) or {}).get("url") or ""
        try:
            current = tab.call_method(
                "Runtime.evaluate", expression="location.href", returnByValue=True, _timeout=5,
            ).get("result", {}).get("value")
            url = current or url
        except Exception:
            pass
        if "kingdoms.com" not in url:
            raise ClickModeError("Nie znam adresu gry.")
        return browser, url

    def prepare(self, run) -> None:
        attack = run.attack
        if attack.target_x is None or attack.target_y is None:
            raise ClickModeError("Plan nie podaje współrzędnych celu.")
        browser, url = self._browser_and_url()
        lanes: list[_Lane] = []
        try:
            for send in run.sends:
                lane = _Lane(browser, url)
                lanes.append(lane)
                lane.open()
                lane.prepare(
                    attack.source_village_id,
                    int(attack.target_x),
                    int(attack.target_y),
                    send.movement_type,
                    send.units,
                    send.catapult_targets,
                )
        except Exception:
            for lane in lanes:
                lane.close()
            raise
        self._lanes[attack.id] = lanes

    def fire(self, run, latest_server_time: float) -> tuple[str, dict]:
        lanes = self._lanes.get(run.attack.id) or []
        if not lanes:
            raise ClickModeError("Wysyłka nie została przygotowana.")
        batch_id = uuid.uuid4().hex
        for lane in lanes:
            try:
                lane.fire()
            except Exception as error:
                lane.result = {"ok": False, "error": str(error)}
        return batch_id, {"status": "fired", "server_time": None}

    def read(self, run) -> dict:
        lanes = self._lanes.pop(run.attack.id, [])
        results = []
        for lane in lanes:
            if lane.result is None:
                try:
                    lane.result = lane.read()
                except Exception as error:
                    lane.result = {"ok": False, "error": str(error)}
            results.append(lane.result)
            lane.close()
        return {"results": results}

    def close(self) -> None:
        for lanes in self._lanes.values():
            for lane in lanes:
                lane.close()
        self._lanes.clear()
