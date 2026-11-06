"""Read TK-Tools attack plans through the user's logged-in browser tab.

The user signs in to https://tk-tools.fr in the same Chrome/Edge instance the
game runs in. All requests run inside that tab, so the session cookie and the
short-lived access token never leave the browser. The planner response also
contains secrets (server API keys, players' login tokens); the page script
copies only the fields the assistant needs before returning them.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit

import pychrome

logger = logging.getLogger(__name__)

TKTOOLS_HOST = "tk-tools.fr"
DENY_REASONS = {"NOTHING_SENT", "PARTIALLY_NOT_SENT", "TIME_LAG", "PARTIAL_TIME_LAG", "OTHER_AFTER"}

_API_HELPERS = r"""
    const api = "/api";
    const tokenKey = "__travianAssistantTkToken";
    const refresh = async () => {
        const response = await fetch(api + "/auth/refresh", {
            method: "POST", credentials: "include",
            headers: {"Content-Type": "application/json"}
        });
        if (!response.ok) throw new Error("TK_LOGIN_REQUIRED");
        const body = await response.json();
        if (!body || !body.accessToken) throw new Error("TK_LOGIN_REQUIRED");
        window[tokenKey] = body.accessToken;
    };
    const call = async (path, method, body) => {
        for (let attempt = 0; attempt < 2; attempt++) {
            if (!window[tokenKey]) await refresh();
            const response = await fetch(api + path, {
                method: method || "GET", credentials: "include", cache: "no-store",
                headers: {"Content-Type": "application/json",
                          Authorization: "Bearer " + window[tokenKey]},
                body: body === undefined ? undefined : JSON.stringify(body)
            });
            if (response.status === 401) { window[tokenKey] = null; continue; }
            if (!response.ok) throw new Error("TK_HTTP_" + response.status);
            const text = await response.text();
            return text ? JSON.parse(text) : null;
        }
        throw new Error("TK_LOGIN_REQUIRED");
    };
"""

_READ_PLANNER = "(async (serverName) => {" + _API_HELPERS + r"""
    const servers = await call("/game-data/servers/mine");
    const server = (servers || []).find(item =>
        String(item.name || "").toLowerCase() === serverName.toLowerCase());
    if (!server) throw new Error("TK_SERVER_NOT_FOUND");
    const planner = await call("/off-operations/planner?serverId=" + server.id);
    const rows = (planner && planner.waves || []).map(row => {
        const source = row.attacker && row.attacker.village || {};
        const target = row.target || {};
        const targetVillage = target.village || {};
        const operation = row.offOperation || {};
        return {
            id: row.id,
            operationId: row.offOperationId,
            operationName: operation.name || "",
            operationState: operation.state || "",
            sourceVillageId: String(source.externalId || ""),
            sourceVillageName: source.name || "",
            sourcePlayerId: String(source.player && source.player.externalId || ""),
            targetVillageId: String(targetVillage.externalId || ""),
            targetName: targetVillage.name
                || `(${target.positionX}|${target.positionY})`,
            targetPlayer: targetVillage.player && targetVillage.player.name || "",
            targetX: targetVillage.x ?? target.positionX ?? null,
            targetY: targetVillage.y ?? target.positionY ?? null,
            departureTime: row.departureTime,
            arrivalTime: row.arrivalTime,
            travelTimeSeconds: row.travelTimeSeconds,
            moveSpeed: row.moveSpeed,
            ptLevel: row.ptLevel,
            isSiege: !!row.isSiege,
            state: row.state || "NONE",
            isSitted: !!row.isSitted,
            strictArrival: target.strictlyRespectArrivalTimes !== false,
            waves: (row.waves || []).map(wave => ({
                waveIndex: wave.waveIndex,
                attackType: wave.attackType,
                fakeNbUnits: wave.fakeNbUnits,
                attackHasOff: wave.attackHasOff,
                attackHasHero: wave.attackHasHero,
                attackNbCatapults: wave.attackNbCatapults,
                attackTarget1: wave.attackTarget1,
                attackTarget2: wave.attackTarget2,
                conquestHasOff: wave.conquestHasOff,
                conquestHasHero: wave.conquestHasHero,
                conquestNbChiefs: wave.conquestNbChiefs,
                conquestNbCatapults: wave.conquestNbCatapults,
                conquestTarget1: wave.conquestTarget1,
                conquestTarget2: wave.conquestTarget2
            }))
        };
    });
    return JSON.stringify({serverId: server.id, rows});
})"""

_REPORT = "(async (attackId, action, body) => {" + _API_HELPERS + r"""
    await call("/off-operations/planned-attacks/" + attackId + "/" + action, "POST", body);
    return "ok";
})"""


class TkToolsError(RuntimeError):
    """TK-Tools could not be read or updated."""


def _is_tktools_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == TKTOOLS_HOST or host.endswith("." + TKTOOLS_HOST)


class TkToolsSession:
    """Talk to TK-Tools from the user's logged-in tab via Chrome DevTools."""

    def __init__(self, cdp_url: str):
        self._cdp_url = cdp_url
        self._tab = None

    def _ensure_tab(self):
        if self._tab is not None:
            return self._tab
        browser = pychrome.Browser(url=self._cdp_url)
        tabs = [
            tab for tab in browser.list_tab(timeout=3)
            if _is_tktools_url(getattr(tab, "_kwargs", {}).get("url") or "")
        ]
        if not tabs:
            raise TkToolsError(
                "Nie znaleziono karty TK-Tools. Otwórz tk-tools.fr w tej samej "
                "przeglądarce co gra i zaloguj się."
            )
        tab = tabs[0]
        tab.start()
        self._tab = tab
        return tab

    def _evaluate(self, expression: str, timeout: int = 20):
        tab = self._ensure_tab()
        try:
            result = tab.call_method(
                "Runtime.evaluate",
                expression=expression,
                awaitPromise=True,
                returnByValue=True,
                _timeout=timeout,
            )
        except Exception as error:
            self.disconnect()
            raise TkToolsError(f"Brak odpowiedzi z karty TK-Tools: {error}") from error
        details = result.get("exceptionDetails")
        if details:
            text = str(
                (details.get("exception") or {}).get("description")
                or details.get("text") or ""
            )
            if "TK_LOGIN_REQUIRED" in text:
                raise TkToolsError("Zaloguj się do TK-Tools w przeglądarce.")
            if "TK_SERVER_NOT_FOUND" in text:
                raise TkToolsError("TK-Tools nie zna tego serwera dla Twojego konta.")
            match = re.search(r"TK_HTTP_(\d+)", text)
            if match:
                raise TkToolsError(f"TK-Tools zwróciło błąd HTTP {match.group(1)}.")
            raise TkToolsError("Nie udało się odczytać TK-Tools.")
        return result.get("result", {}).get("value")

    def read_planner(self, server_name: str) -> dict:
        """Return ``{"serverId", "rows"}`` with sanitized planned attacks."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", server_name or ""):
            raise ValueError("Invalid server name.")
        value = self._evaluate(f"{_READ_PLANNER}({json.dumps(server_name)})")
        try:
            data = json.loads(value)
        except (TypeError, ValueError) as error:
            raise TkToolsError("TK-Tools zwróciło nieprawidłowe dane.") from error
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            raise TkToolsError("TK-Tools zwróciło nieprawidłowe dane.")
        return data

    def confirm_sent(self, attack_id: str, comment: str = "") -> None:
        self._report(attack_id, "confirm-sending", {"denyComment": comment[:500]})

    def deny_sent(self, attack_id: str, reason: str, comment: str) -> None:
        if reason not in DENY_REASONS:
            raise ValueError("Invalid deny reason.")
        self._report(
            attack_id,
            "deny-sending",
            {"denyReason": reason, "denyComment": comment[:500]},
        )

    def _report(self, attack_id: str, action: str, body: dict) -> None:
        if not re.fullmatch(r"[0-9a-f-]{36}", attack_id or ""):
            raise ValueError("Invalid planned attack id.")
        self._evaluate(
            f"{_REPORT}({json.dumps(attack_id)}, {json.dumps(action)}, "
            f"{json.dumps(body)})"
        )

    def disconnect(self) -> None:
        if self._tab is not None:
            try:
                self._tab.stop()
            except Exception:
                pass
            self._tab = None
