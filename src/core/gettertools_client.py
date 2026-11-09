"""Read and update GetterTools (gettertools.com) through the user's logged-in tab.

Everything runs as ``fetch`` inside the GetterTools tab of the same browser as
the game, so the GetterTools session cookie never leaves the browser.

* Group "Member details" (``<group>?detailList``) lists every member with the
  last troop update: the cell's class carries the exact Unix time
  (``sort1794237354``), its span the colour GetterTools uses, and the player
  link the game player id (``showOpenTipPlayer(<uid>, ...)``).
* "My troops" (``22-…``) saves one village with the same AJAX call as the
  page's own troop table: ``saveCityT2`` with x, y, units 1-9, tournament
  square level and the village type (kept as it is).
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import pychrome

GT_HOST = "gettertools.com"
DEFAULT_GROUP_PATH = "ILOVEPL,groups117"
MY_TROOPS_PATH = "22-Askerlerim"

_READ_GROUP_UPDATES = r"""(async (serverPath, groupPath) => {
    const response = await fetch("/" + serverPath + "/" + groupPath + "?detailList",
        {credentials: "include", cache: "no-store"});
    if (!response.ok) throw new Error("GT_HTTP_" + response.status);
    const html = await response.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    if (!doc.querySelector("#logoutForm")) throw new Error("GT_LOGIN_REQUIRED");
    const table = [...doc.querySelectorAll("table")].find(t =>
        t.rows.length && /L\.troop update/i.test(t.rows[0].textContent));
    if (!table) throw new Error("GT_NO_ACCESS");
    const header = [...table.rows[0].cells].map(c => c.textContent.trim());
    const playerColumn = header.indexOf("Player");
    const updateColumn = header.findIndex(text => /L\.troop update/i.test(text));
    const members = [];
    for (let i = 1; i < table.rows.length; i++) {
        const cells = table.rows[i].cells;
        const playerCell = cells[playerColumn], updateCell = cells[updateColumn];
        if (!playerCell || !updateCell) continue;
        const link = playerCell.querySelector("[onmouseover*='showOpenTipPlayer']");
        const uid = link && (link.getAttribute("onmouseover").match(/showOpenTipPlayer\((\d+)/) || [])[1];
        const sort = (updateCell.className.match(/sort(\d+)/) || [])[1];
        const span = updateCell.querySelector("span");
        members.push({
            playerId: uid || "",
            name: playerCell.textContent.trim(),
            timestamp: sort ? Number(sort) : 0,
            text: updateCell.textContent.trim(),
            color: span ? (span.style.color || "") : ""
        });
    }
    return JSON.stringify({members});
})"""

_UPDATE_TROOPS = r"""(async (serverPath, troopsPath, villages) => {
    const url = "/" + serverPath + "/" + troopsPath;
    const page = await fetch(url, {credentials: "include", cache: "no-store"});
    if (!page.ok) throw new Error("GT_HTTP_" + page.status);
    const html = await page.text();
    if (!/id=.logoutForm/.test(html)) throw new Error("GT_LOGIN_REQUIRED");
    const token = (html.match(/var sectok='([^']*)'/) || [])[1];
    const config = (html.match(/TroopTable2\("#troopTable2",\s*(\{.*?\})\);\s*\}/s) || [])[1];
    if (!token || !config) throw new Error("GT_PAGE_CHANGED");
    const existing = JSON.parse(config).troops || [];
    const byPosition = new Map(existing.map(v => [v.x + "|" + v.y, v]));
    const updated = [], missing = [], failed = [];
    for (const village of villages) {
        const known = byPosition.get(village.x + "|" + village.y);
        if (!known) { missing.push(village.name); continue; }
        const body = new URLSearchParams();
        body.append("saveCityT2", "1");
        body.append("x", String(village.x));
        body.append("y", String(village.y));
        for (const count of village.units) body.append("units[]", String(count));
        body.append("tp", String(village.tp));
        body.append("type", String(known.type ?? 0));
        for (const part of token.split("&")) {
            const [key, value] = part.split("=");
            if (key) body.append(key, value || "");
        }
        const response = await fetch(url, {
            method: "POST", credentials: "include", body,
            headers: {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                      "X-Requested-With": "XMLHttpRequest"}
        });
        let ok = response.ok;
        try { const answer = await response.json(); ok = ok && answer && answer.transmitted; }
        catch (error) { ok = false; }
        (ok ? updated : failed).push(village.name);
    }
    return JSON.stringify({updated, missing, failed});
})"""


class GetterToolsError(RuntimeError):
    """GetterTools could not be read or updated."""


def _is_gt_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == GT_HOST or host.endswith("." + GT_HOST)


def gt_units(units: dict[int, int]) -> list[int]:
    """Game units 1-9 (no settlers, no hero) in GetterTools order."""
    return [max(0, int(units.get(unit, 0))) for unit in range(1, 10)]


class GetterToolsSession:
    """Talk to GetterTools from the user's logged-in tab via Chrome DevTools."""

    def __init__(self, cdp_url: str):
        self._cdp_url = cdp_url
        self._tab = None

    def _ensure_tab(self):
        if self._tab is not None:
            return self._tab
        browser = pychrome.Browser(url=self._cdp_url)
        tabs = [
            tab for tab in browser.list_tab(timeout=3)
            if _is_gt_url(getattr(tab, "_kwargs", {}).get("url") or "")
        ]
        if not tabs:
            raise GetterToolsError(
                "Nie znaleziono karty GetterTools. Otwórz gettertools.com w tej "
                "samej przeglądarce co gra i zaloguj się."
            )
        tab = tabs[0]
        tab.start()
        self._tab = tab
        return tab

    def _evaluate(self, expression: str, timeout: int = 30):
        tab = self._ensure_tab()
        for method, params in (
            ("Page.setWebLifecycleState", {"state": "active"}),
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
        ):
            try:
                tab.call_method(method, _timeout=5, **params)
            except Exception:
                pass
        try:
            result = tab.call_method(
                "Runtime.evaluate", expression=expression, awaitPromise=True,
                returnByValue=True, _timeout=timeout,
            )
        except Exception as error:
            self.disconnect()
            raise GetterToolsError(f"Brak odpowiedzi z karty GetterTools: {error}") from error
        details = result.get("exceptionDetails")
        if details:
            text = str(
                (details.get("exception") or {}).get("description") or details.get("text") or ""
            )
            if "GT_LOGIN_REQUIRED" in text:
                raise GetterToolsError("Zaloguj się do GetterTools w przeglądarce.")
            if "GT_NO_ACCESS" in text:
                raise GetterToolsError("Brak dostępu do listy członków grupy w GetterTools.")
            if "GT_PAGE_CHANGED" in text:
                raise GetterToolsError("Strona GetterTools zmieniła wygląd — nie rozpoznano tabeli wojsk.")
            match = re.search(r"GT_HTTP_(\d+)", text)
            if match:
                raise GetterToolsError(f"GetterTools zwróciło błąd HTTP {match.group(1)}.")
            raise GetterToolsError("Nie udało się odczytać GetterTools.")
        value = result.get("result", {}).get("value")
        try:
            data = json.loads(value)
        except (TypeError, ValueError) as error:
            raise GetterToolsError("GetterTools zwróciło nieprawidłowe dane.") from error
        if not isinstance(data, dict):
            raise GetterToolsError("GetterTools zwróciło nieprawidłowe dane.")
        return data

    @staticmethod
    def _server_path(server_name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", server_name or ""):
            raise ValueError("Invalid server name.")
        return f"{server_name.lower()}.kingdoms.com"

    def read_group_updates(self, server_name: str, group_path: str) -> dict[str, dict]:
        """Last troop update per game player id: ``{"timestamp", "text", "color"}``."""
        if not re.fullmatch(r"[A-Za-z0-9_\-]+,groups\d+", group_path or ""):
            raise ValueError("Invalid GetterTools group.")
        data = self._evaluate(
            f"{_READ_GROUP_UPDATES}({json.dumps(self._server_path(server_name))}, "
            f"{json.dumps(group_path)})"
        )
        updates = {}
        for member in data.get("members") or []:
            if not isinstance(member, dict):
                continue
            player_id = str(member.get("playerId") or "")
            if not player_id.isdigit():
                continue
            timestamp = member.get("timestamp")
            updates[player_id] = {
                "timestamp": int(timestamp) if isinstance(timestamp, (int, float)) and timestamp > 0 else None,
                "text": str(member.get("text") or ""),
                "color": str(member.get("color") or "").strip().lower(),
            }
        return updates

    def update_troops(self, server_name: str, army: dict) -> dict:
        """Save the player's troops (from the game) in GetterTools "My troops"."""
        villages = [
            {
                "x": int(village["x"]),
                "y": int(village["y"]),
                "name": str(village.get("name") or ""),
                "units": gt_units(village.get("units") or {}),
                "tp": max(0, min(20, int(village.get("tournament_square_level") or 0))),
            }
            for village in army.get("villages") or []
        ]
        return self._evaluate(
            f"{_UPDATE_TROOPS}({json.dumps(self._server_path(server_name))}, "
            f"{json.dumps(MY_TROOPS_PATH)}, {json.dumps(villages, ensure_ascii=False)})",
            timeout=60,
        )

    def disconnect(self) -> None:
        if self._tab is not None:
            try:
                self._tab.stop()
            except Exception:
                pass
            self._tab = None
