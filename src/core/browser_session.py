"""Read-only access to the Travian page in a local Chrome session."""

import ipaddress
import base64
import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from typing import Callable
from pathlib import Path
from urllib.parse import urlsplit

import pychrome

try:
    from src.interceptor.request_filter import RequestFilter
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from interceptor.request_filter import RequestFilter

logger = logging.getLogger(__name__)


_GAME_DOMAINS = (
    "traviangames.com",
    "game-travian.com",
    "travian.com",
    "kingdoms.com",
)

_BUILDING_NAMES = {
    1: "Woodcutter",
    2: "Clay pit",
    3: "Iron mine",
    4: "Cropland",
    5: "Sawmill",
    6: "Brickyard",
    7: "Iron foundry",
    8: "Grain mill",
    9: "Bakery",
    10: "Warehouse",
    11: "Granary",
    12: "Smithy",
    13: "Armoury",
    14: "Tournament square",
    15: "Main building",
    16: "Rally point",
    17: "Marketplace",
    18: "Embassy",
    19: "Barracks",
    20: "Stable",
    21: "Workshop",
    22: "Academy",
    23: "Cranny",
    24: "Town hall",
    25: "Residence",
    26: "Palace",
    27: "Treasury",
    28: "Trade office",
    29: "Great barracks",
    30: "Great stable",
    31: "City wall",
    32: "Earth wall",
    33: "Palisade",
    34: "Stonemason's lodge",
    35: "Brewery",
    36: "Trapper",
    37: "Hero's mansion",
    38: "Great warehouse",
    39: "Great granary",
    40: "Wonder of the World",
}

_GAUL_UNIT_NAMES = {
    1: "Phalanx",
    2: "Swordsman",
    3: "Pathfinder",
    4: "Theutates Thunder",
    5: "Druidrider",
    6: "Haeduan",
    7: "Ram",
    8: "Trebuchet",
    9: "Chieftain",
    10: "Settler",
}

_TROOP_NAMES_BY_TYPE = {
    1: "Legionnaire",
    2: "Praetorian",
    3: "Imperian",
    4: "Equites Legati",
    5: "Equites Imperatoris",
    6: "Equites Caesaris",
    7: "Battering Ram",
    8: "Fire Catapult",
    9: "Senator",
    10: "Settler",
    11: "Clubswinger",
    12: "Spearman",
    13: "Axeman",
    14: "Scout",
    15: "Paladin",
    16: "Teutonic Knight",
    17: "Ram",
    18: "Catapult",
    19: "Chief",
    20: "Settler",
    **{20 + unit: name for unit, name in _GAUL_UNIT_NAMES.items()},
    31: "Slave Militia",
    32: "Ash Warden",
    33: "Khopesh Warrior",
    34: "Sopdu Explorer",
    35: "Resheph Chariot",
    36: "Anhur Guard",
    37: "Ram",
    38: "Stone Catapult",
    39: "Nomarch",
    40: "Settler",
    41: "Mercenary",
    42: "Bowman",
    43: "Spotter",
    44: "Steppe Rider",
    45: "Marksman",
    46: "Marauder",
    47: "Ram",
    48: "Mangonel",
    49: "Logades",
    50: "Settler",
}

_RECRUITMENT_BUILDING_NAMES = {
    19: "Koszary",
    20: "Stajnia",
    21: "Warsztat",
    25: "Rezydencja",
    26: "Pałac",
    29: "Wielkie koszary",
    30: "Wielka stajnia",
}

_RECRUITMENT_BUILDING_TYPES = (19, 20, 21, 25, 26, 29, 30)

NPC_TRADER_PRICE = 5

_READ_GAME_STATE = """(() => {
    const resourceIds = {wood: "l1", clay: "l2", iron: "l3", grain: "l4"};
    const stockSelectors = {
        wood: ".stockContainer.wood",
        clay: ".stockContainer.clay",
        iron: ".stockContainer.iron",
        grain: ".stockContainer.crop"
    };
    const resources = {};
    const capacities = {};
    const stockPoints = {};
    for (const [name, selector] of Object.entries(stockSelectors)) {
        const container = document.querySelector(selector);
        const progress = container && container.querySelector(".progressbar");
        if (!container || !progress) continue;
        const current = Number(progress.getAttribute("value"));
        const capacity = Number(progress.getAttribute("max-value"));
        if (Number.isFinite(current) && current >= 0) resources[name] = current;
        if (Number.isFinite(capacity) && capacity >= 0) capacities[name] = capacity;
        const rect = container.getBoundingClientRect();
        if (rect.width && rect.height) {
            stockPoints[name] = {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        }
    }
    for (const [name, id] of Object.entries(resourceIds)) {
        if (resources[name] !== undefined) continue;
        const node = document.getElementById(id)
            || document.querySelector(`[data-resource="${name}"]`)
            || document.querySelector(`.resource.${name}, .resource-${name}`);
        if (node) {
            const valueNode = node.querySelector(
                ".value, .amount, [data-value], .resourceValue"
            ) || node;
            const raw = valueNode.getAttribute("data-value")
                || valueNode.getAttribute("title")
                || valueNode.textContent
                || "";
            const match = raw.match(/[0-9][0-9.,\\s\\u00a0]*/);
            if (match) {
                const value = Number(match[0].replace(/\\D/g, ""));
                if (Number.isFinite(value)) resources[name] = value;
            }
        }
    }

    const resourceTerms = {
        wood: /wood|timber|lumber/i,
        clay: /clay/i,
        iron: /iron/i,
        grain: /grain|crop|wheat|corn/i
    };
    for (const node of document.querySelectorAll(
        '[id*="wood" i], [class*="wood" i], [id*="timber" i], [class*="timber" i], '
        + '[id*="lumber" i], [class*="lumber" i], [id*="clay" i], [class*="clay" i], '
        + '[id*="iron" i], [class*="iron" i], [id*="grain" i], [class*="grain" i], '
        + '[id*="crop" i], [class*="crop" i], [data-resource]'
    )) {
        const label = [
            node.id,
            typeof node.className === "string" ? node.className : "",
            node.getAttribute("data-resource"),
            node.getAttribute("aria-label"),
            node.getAttribute("title")
        ].join(" ");
        const entry = Object.entries(resourceTerms).find(
            ([, pattern]) => pattern.test(label)
        );
        if (!entry || resources[entry[0]] !== undefined) continue;
        const valueNode = node.querySelector(
            ".value, .amount, [data-value], .resourceValue"
        ) || node;
        const raw = valueNode.getAttribute("data-value")
            || valueNode.getAttribute("title")
            || valueNode.textContent
            || "";
        const match = raw.match(/[0-9][0-9.,\\s\\u00a0]*/);
        if (match) {
            const value = Number(match[0].replace(/\\D/g, ""));
            if (Number.isFinite(value)) resources[entry[0]] = value;
        }
    }

    const accountNode = document.querySelector(
        "#playerName, #header_user_name, .playerName, .player-name, "
        + ".header-user-name, .headerUserName, "
        + "[data-player-name], [data-playername]"
    );
    const passwordForm = Boolean(document.querySelector('input[type="password"]'));
    const hasGameUi = Boolean(
        accountNode
        || Object.keys(resources).length
        || document.querySelector("#l1, #l2, #l3, #l4, [data-resource]")
    );
    const buildings = [...document.querySelectorAll(
        ".buildingStatusButton .buildingLevel"
    )].map(levelNode => {
        const statusNode = levelNode.closest(".buildingStatus");
        const button = levelNode.closest(".buildingStatusButton");
        const classes = button ? button.className : "";
        const typeMatch = classes.match(/\\btype_(\\d+)\\b/);
        const locationMatch = classes.match(/\\blocation_(\\d+)\\b/);
        if (!typeMatch) return null;
        const type = Number(typeMatch[1]);
        const statusClasses = statusNode ? statusNode.className : "";
        return {
            type,
            level: Number(levelNode.textContent.trim()),
            slot: locationMatch ? Number(locationMatch[1]) : 0,
            upgrading: /\\b(busy|queued|master)\\b/.test(statusClasses)
        };
    }).filter(Boolean);
    const resourceFields = [...document.querySelectorAll(
        ".resources.viewBackground .buildingStatusButton .buildingLevel"
    )].map(levelNode => {
        const button = levelNode.closest(".buildingStatusButton");
        const classes = button ? button.className : "";
        const typeMatch = classes.match(/\\btype_(\\d+)\\b/);
        const locationMatch = classes.match(/\\blocation_(\\d+)\\b/);
        if (!typeMatch) return null;
        const type = Number(typeMatch[1]);
        return {
            type,
            name: ({
                1: "Woodcutter",
                2: "Clay pit",
                3: "Iron mine",
                4: "Cropland"
            })[type] || `Resource field ${type}`,
            level: Number(levelNode.textContent.trim()),
            slot: locationMatch ? Number(locationMatch[1]) : 0
        };
    }).filter(Boolean);
    const selectedOption = document.querySelector(
        ".villageListDropDown .dropdownOption.selected"
    );
    const selectedIdMatch = selectedOption
        ? selectedOption.className.match(/\\bdropDownItem(\\d+)\\b/)
        : null;
    const selectedVillageId = selectedIdMatch ? selectedIdMatch[1] : "";
    const hasVillageModels = typeof Village !== "undefined";
    const currentVillage = hasVillageModels && selectedVillageId
        ? Village.get(selectedVillageId)
        : null;
    const playerId = currentVillage && currentVillage.data.playerId;
    const player = playerId && typeof Player !== "undefined"
        ? Player.get(playerId)
        : null;
    const tribeId = player && Number(player.data.tribeId);
    const mapCollection = hasVillageModels
        ? Village.getCollection(Village.COLLECTION_KINGDOM) || {data: {}}
        : {data: {}};
    const mapVillages = Object.values(mapCollection.data || {}).map(village => {
        const data = village.data || {};
        const mapPlayer = data.playerId && typeof Player !== "undefined"
            ? Player.get(data.playerId)
            : null;
        const kingdomId = mapPlayer && Number(mapPlayer.data.kingdomId)
            || Number(data.belongsToKingdom)
            || 0;
        const kingdom = kingdomId && typeof Kingdom !== "undefined"
            ? Kingdom.get(kingdomId)
            : null;
        return {
            id: String(data.villageId || ""),
            name: String(data.name || ""),
            x: Number(data.x),
            y: Number(data.y),
            player: mapPlayer && mapPlayer.data.name
                ? String(mapPlayer.data.name)
                : "",
            kingdom: kingdom && kingdom.data.tag
                ? String(kingdom.data.tag)
                : ""
        };
    }).filter(village => (
        village.id
        && Number.isFinite(village.x)
        && Number.isFinite(village.y)
        && village.player
    ));
    const villages = [...document.querySelectorAll(
        ".villageListDropDown .dropdownOption"
    )].map(option => {
        const villageIdMatch = option.className.match(/\\bdropDownItem(\\d+)\\b/);
        const nameNode = option.querySelector(".villageEntry");
        const name = nameNode ? nameNode.textContent.trim() : "";
        if (!villageIdMatch || !name) return null;
        return {
            id: villageIdMatch[1],
            name,
            selected: option.classList.contains("selected")
        };
    }).filter(Boolean);
    return {
        title: document.title,
        url: location.href,
        world: location.host,
        account: accountNode
            ? (accountNode.getAttribute("data-player-name")
                || accountNode.getAttribute("data-playername")
                || accountNode.textContent.trim())
            : "",
        loggedIn: !passwordForm && hasGameUi
            && !/login|login\\.php/i.test(location.href),
        resources,
        capacities,
        stockPoints,
        buildings,
        resource_fields: resourceFields,
        selected_village_id: selectedVillageId,
        selected_village: currentVillage ? {
            name: String(currentVillage.data.name || ""),
            x: Number(currentVillage.data.x),
            y: Number(currentVillage.data.y)
        } : null,
        map_villages: mapVillages,
        tribe_id: Number.isFinite(tribeId) ? tribeId : 0,
        villages
    };
})()"""

_READ_TROOPS = """(() => {
    const selectedOption = document.querySelector(
        ".villageListDropDown .dropdownOption.selected"
    );
    const selectedIdMatch = selectedOption
        ? selectedOption.className.match(/\\bdropDownItem(\\d+)\\b/)
        : null;
    const villageId = selectedIdMatch ? selectedIdMatch[1] : "";
    const village = villageId ? Village.get(villageId) : null;
    const tribeId = village
        ? Number(Player.get(village.data.playerId).data.tribeId)
        : 0;
    const units = {};
    let hero = 0;
    for (const group of document.querySelectorAll(
        "#troopsStationed ul[ng-repeat]"
    )) {
        const tribeLabel = group.querySelector("li.tribe[tooltip-translate]");
        const match = tribeLabel
            ? tribeLabel.getAttribute("tooltip-translate").match(/Tribe_(\\d+)/)
            : null;
        if (!match || Number(match[1]) !== tribeId) continue;
        for (const icon of group.querySelectorAll(".unitIcon")) {
            const item = icon.closest("li");
            const countNode = item && item.querySelector(".count");
            const count = countNode
                ? Number(countNode.textContent.replace(/\\D/g, ""))
                : 0;
            if (icon.classList.contains("hero_illu")) {
                hero += count;
                continue;
            }
            const unitMatch = icon.className.match(/\\bunitType(\\d+)\\b/);
            if (unitMatch) {
                const unitNumber = Number(unitMatch[1]);
                units[unitNumber] = (units[unitNumber] || 0) + count;
            }
        }
    }
    return {village_id: villageId, tribe_id: tribeId, units, hero};
})()"""


def _cdp_url(address: str, port: int) -> str:
    """Build a loopback-only Chrome DevTools URL."""
    candidate = address.strip()
    if not candidate:
        raise ValueError("A local CDP address is required.")
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CDP must use a local HTTP address.")

    hostname = parsed.hostname.lower()
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname == "localhost"
    if not is_loopback:
        raise ValueError("For safety, CDP connections must use localhost.")

    resolved_port = parsed.port or port
    if not 1 <= resolved_port <= 65535:
        raise ValueError("CDP port must be between 1 and 65535.")
    return f"http://{parsed.netloc if parsed.port else f'{hostname}:{resolved_port}'}"


def _is_game_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in _GAME_DOMAINS
    )


def _tab_url(tab: pychrome.Tab) -> str:
    """Read the URL from pychrome's initial CDP target metadata."""
    metadata = getattr(tab, "_kwargs", {})
    return str(metadata.get("url", "")) if isinstance(metadata, dict) else ""


def _parse_game_state(result: dict) -> dict:
    """Return sanitized, displayable state from a Chrome Runtime.evaluate result."""
    remote_result = result.get("result", {})
    if remote_result.get("subtype") == "error":
        raise RuntimeError("The Travian page did not return readable game data.")
    state = remote_result.get("value")
    if not isinstance(state, dict):
        raise RuntimeError("The Travian page returned an unexpected response.")
    if not _is_game_url(str(state.get("url", ""))):
        raise RuntimeError("The selected browser tab is not on a Travian game page.")

    resources = state.get("resources", {})
    if not isinstance(resources, dict):
        resources = {}
    clean_resources = {
        key: value
        for key, value in resources.items()
        if key in {"wood", "clay", "iron", "grain"}
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }
    capacities = state.get("capacities", {})
    if not isinstance(capacities, dict):
        capacities = {}
    clean_capacities = {
        key: value
        for key, value in capacities.items()
        if key in {"wood", "clay", "iron", "grain"}
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }
    account = re.sub(r"\s+", " ", str(state.get("account", ""))).strip()
    buildings = state.get("buildings", [])
    if not isinstance(buildings, list):
        buildings = []
    clean_buildings = []
    for building in buildings:
        if not isinstance(building, dict):
            continue
        building_type = building.get("type")
        level = building.get("level")
        slot = building.get("slot", 0)
        if (
            not isinstance(building_type, int)
            or isinstance(building_type, bool)
            or not isinstance(level, int)
            or isinstance(level, bool)
            or level < 0
            or not isinstance(slot, int)
            or isinstance(slot, bool)
            or slot < 0
        ):
            continue
        clean_buildings.append({
            "name": _BUILDING_NAMES.get(building_type, f"Building {building_type}"),
            "level": level,
            "slot": slot,
            "upgrading": bool(building.get("upgrading")),
        })
    villages = state.get("villages", [])
    if not isinstance(villages, list):
        villages = []
    clean_villages = [
        {
            "id": village["id"],
            "name": re.sub(r"\s+", " ", village["name"]).strip(),
            "selected": bool(village.get("selected")),
        }
        for village in villages
        if isinstance(village, dict)
        and isinstance(village.get("id"), str)
        and village["id"].isdigit()
        and isinstance(village.get("name"), str)
        and village["name"].strip()
    ]
    resource_fields = state.get("resource_fields", [])
    if not isinstance(resource_fields, list):
        resource_fields = []
    clean_resource_fields = [
        {
            "name": _BUILDING_NAMES.get(field["type"], field["name"]),
            "slot": field["slot"],
            "level": field["level"],
        }
        for field in resource_fields
        if isinstance(field, dict)
        and isinstance(field.get("type"), int)
        and isinstance(field.get("name"), str)
        and isinstance(field.get("slot"), int)
        and isinstance(field.get("level"), int)
        and field["slot"] >= 0
        and field["level"] >= 0
    ]
    selected_village = state.get("selected_village")
    if not isinstance(selected_village, dict):
        selected_village = {}
    selected_village_x = selected_village.get("x")
    selected_village_y = selected_village.get("y")
    clean_map_villages = []
    map_villages = state.get("map_villages", [])
    if isinstance(map_villages, list):
        for village in map_villages:
            if not isinstance(village, dict):
                continue
            x = village.get("x")
            y = village.get("y")
            if (
                not isinstance(village.get("id"), str)
                or not village["id"].isdigit()
                or not isinstance(village.get("name"), str)
                or not isinstance(village.get("player"), str)
                or not village["player"].strip()
                or not isinstance(x, (int, float))
                or isinstance(x, bool)
                or not isinstance(y, (int, float))
                or isinstance(y, bool)
            ):
                continue
            distance = None
            if (
                isinstance(selected_village_x, (int, float))
                and not isinstance(selected_village_x, bool)
                and isinstance(selected_village_y, (int, float))
                and not isinstance(selected_village_y, bool)
            ):
                distance = round(math.hypot(
                    x - selected_village_x,
                    y - selected_village_y,
                ), 2)
            clean_map_villages.append({
                "name": village["name"].strip() or "Unnamed village",
                "player": village["player"].strip(),
                "coordinates": f"{int(x)}/{int(y)}",
                "kingdom": str(village.get("kingdom", "")).strip() or "—",
                "distance": distance,
            })
    return {
        "title": str(state.get("title", "")).strip(),
        "url": str(state.get("url", "")),
        "world": str(state.get("world", "")).strip(),
        "account": account,
        "logged_in": bool(state.get("loggedIn")),
        "resources": clean_resources,
        "capacities": clean_capacities,
        "production_rates": {},
        "buildings": sorted(clean_buildings, key=lambda item: (item["slot"], item["name"])),
        "resource_fields": sorted(
            clean_resource_fields,
            key=lambda item: (item["slot"], item["name"]),
        ),
        "map_villages": sorted(
            clean_map_villages,
            key=lambda item: (
                item["distance"] is None,
                item["distance"] if item["distance"] is not None else 0,
                item["player"].casefold(),
            ),
        ),
        "selected_village_id": str(state.get("selected_village_id", "")),
        "villages": clean_villages,
    }


class TravianBrowserSession:
    """Connect to an already-open Travian tab and read its visible game state."""

    def __init__(self, address: str, port: int):
        self._cdp_url = _cdp_url(address, port)
        self._browser = None
        self._tab = None
        self._network_response_handler: Callable[[str, dict], bool] | None = None
        self._network_responses: dict[str, dict] = {}
        self._request_filter = RequestFilter()
        self._incoming_attack_handler: Callable[[dict], None] | None = None
        self._village_names_handler: Callable[[dict], None] | None = None
        self._incoming_attack_binding = (
            f"travianAssistantAttack_{uuid.uuid4().hex}"
        )
        self._incoming_attack_registry = (
            f"__{self._incoming_attack_binding}_subscriptions"
        )
        self._incoming_attack_listener_active = False
        self._village_names_by_id: dict[str, str] = {}

    def set_network_response_handler(
        self, handler: Callable[[str, dict], bool] | None
    ) -> None:
        """Set the consumer for parsed game API responses."""
        self._network_response_handler = handler

    def set_incoming_attack_handler(
        self, handler: Callable[[dict], None] | None
    ) -> None:
        """Set the consumer for live incoming-attack snapshots."""
        self._incoming_attack_handler = handler

    def set_village_names_handler(
        self, handler: Callable[[dict], None] | None
    ) -> None:
        """Set the consumer for newly resolved map village names."""
        self._village_names_handler = handler

    def _attach_network_listeners(self) -> None:
        if self._tab is None:
            raise RuntimeError("Cannot monitor network traffic before connecting.")

        self._tab.set_listener(
            "Runtime.bindingCalled",
            self._on_incoming_attack_binding_called,
        )
        self._tab.set_listener(
            "Network.requestWillBeSent",
            self._on_network_request_sent,
        )
        self._tab.set_listener(
            "Network.responseReceived",
            self._on_network_response_received,
        )
        self._tab.set_listener(
            "Network.loadingFinished",
            self._on_network_loading_finished,
        )
        self._tab.set_listener(
            "Network.loadingFailed",
            self._on_network_loading_failed,
        )

    def _on_incoming_attack_binding_called(
        self, params: dict | None = None, **event_params: object
    ) -> None:
        params = params if params is not None else event_params
        if (
            params.get("name") != self._incoming_attack_binding
            or self._incoming_attack_handler is None
        ):
            return
        try:
            snapshot = json.loads(str(params.get("payload", "")))
            if not isinstance(snapshot, dict):
                raise ValueError("The live attack event is not an object.")
            village_id = snapshot.get("village_id")
            server_time = snapshot.get("server_time")
            attacks = snapshot.get("attacks")
            if (
                not isinstance(village_id, str)
                or not village_id.isdigit()
                or isinstance(server_time, bool)
                or not isinstance(server_time, (int, float))
                or not math.isfinite(server_time)
                or not isinstance(attacks, list)
            ):
                raise ValueError("The live attack event has invalid village data.")

            normalized_attacks = []
            for attack in attacks:
                if not isinstance(attack, dict):
                    raise ValueError("The live attack event contains invalid data.")
                movement_id = attack.get("movement_id")
                arrival_time = attack.get("arrival_time")
                if (
                    not isinstance(movement_id, str)
                    or not movement_id
                    or str(attack.get("village_id")) != village_id
                    or isinstance(arrival_time, bool)
                    or not isinstance(arrival_time, (int, float))
                    or not math.isfinite(arrival_time)
                    or arrival_time <= 0
                ):
                    raise ValueError("The live attack event is incomplete.")
                source_village_id = str(
                    attack.get("source_village_id") or ""
                ).strip()
                if source_village_id and not source_village_id.isdigit():
                    source_village_id = ""
                source_village_name = (
                    str(attack.get("source_village") or "").strip()
                    or self._village_names_by_id.get(source_village_id, "")
                    or (
                        f"Wioska {source_village_id}"
                        if source_village_id
                        else "Nieznana wioska"
                    )
                )
                normalized_attacks.append({
                    "movement_id": movement_id,
                    "village_id": village_id,
                    "village_name": (
                        str(attack.get("village_name") or "").strip()
                        or f"Wioska {village_id}"
                    ),
                    "attacker_name": (
                        str(attack.get("attacker_name") or "").strip()
                        or "Nieznany gracz"
                    ),
                    "source_village_id": source_village_id or None,
                    "source_village": source_village_name,
                    "arrival_time": float(arrival_time),
                    "seconds_remaining": max(
                        0.0,
                        float(arrival_time) - float(server_time),
                    ),
                    "movement_type": (
                        str(attack.get("movement_type") or "").strip()
                        or "Atak"
                    ),
                    "masked": bool(attack.get("masked")),
                })
            self._incoming_attack_handler({
                "village_id": village_id,
                "server_time": float(server_time),
                "attacks": normalized_attacks,
            })
        except (TypeError, ValueError) as error:
            logger.warning(
                "Ignoring malformed live incoming-attack update: %s",
                error,
            )
        except Exception:
            logger.exception("Could not forward live incoming-attack update")

    def start_incoming_attack_listener(self, villages: list[dict]) -> int:
        """Subscribe to game movement collections without switching villages."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")
        if self._incoming_attack_listener_active:
            raise RuntimeError("Incoming attack listener is already active.")

        village_ids = [
            str(village.get("id") or village.get("village_id") or "")
            for village in villages
        ]
        village_names = {
            village_id: str(village.get("name") or "").strip()
            for village_id, village in zip(village_ids, villages)
            if str(village.get("name") or "").strip()
        }
        village_names = {
            village_id: str(village.get("name") or "").strip()
            for village_id, village in zip(village_ids, villages)
            if str(village.get("name") or "").strip()
        }
        if not village_ids or any(not village_id.isdigit() for village_id in village_ids):
            raise ValueError("Invalid village identifier in live attack listener.")
        if len(set(village_ids)) != len(village_ids):
            raise ValueError("Duplicate village identifier in live attack listener.")

        self._tab.call_method("Runtime.enable")
        self._tab.call_method(
            "Runtime.addBinding",
            name=self._incoming_attack_binding,
        )
        self._incoming_attack_listener_active = True
        registry_key = json.dumps(self._incoming_attack_registry)
        binding_name = json.dumps(self._incoming_attack_binding)
        expression = """(villageIds, registryKey, bindingName, villageNames) => {
            if (typeof Troops === "undefined"
                || !window.angular
                || typeof Troops.getCollection !== "function") {
                throw new Error("Troop movement collections are unavailable.");
            }
            if (window[registryKey]) {
                throw new Error("A live attack listener is already installed.");
            }
            const injector = angular.element(document.body).injector();
            const villageService = injector
                && injector.get("VillageService");
            const ownedVillages = villageService && villageService.U();
            if (!ownedVillages || !Array.isArray(ownedVillages.data)) {
                throw new Error("The game's owned village list is unavailable.");
            }
            const ownedIds = new Set(ownedVillages.data.map(village =>
                String(village.data && village.data.villageId || "")
            ));
            if (villageIds.some(id => !ownedIds.has(String(id)))) {
                throw new Error("A selected village is not owned by this account.");
            }
            const publish = villageId => {
                const collection = Troops.getCollection(
                    Troops.COLLECTION_MOVING,
                    String(villageId)
                );
                if (!collection || !collection.filled
                    || !Array.isArray(collection.data)) {
                    throw new Error(
                        `Movement collection is unavailable for village ${villageId}.`
                    );
                }
                const serverTime = Number(
                    injector.get("$rootScope").currentServerTime
                );
                if (!Number.isFinite(serverTime) || serverTime <= 0) return;
                const movementType = type => {
                    if (type === Number(Troops.MOVEMENT_TYPE_RAID)) return "Napad";
                    if (type === Number(Troops.MOVEMENT_TYPE_SIEGE)) return "Oblężenie";
                    if (type === Number(Troops.MOVEMENT_TYPE_SPY)) return "Zwiad";
                    return "Atak";
                };
                const attacks = collection.data.filter(row =>
                    row && typeof row.getMovementGroup === "function"
                    && row.getMovementGroup(Number(villageId))
                        === "incoming_attacks"
                ).map(row => {
                    const data = row.data || {};
                    const movement = data.movement || {};
                    const sourceVillageId = String(
                        movement.villageIdStart || data.villageIdStart || ""
                    );
                    const sourceVillage = sourceVillageId
                        && typeof Village !== "undefined"
                        ? Village.get(sourceVillageId)
                        : null;
                    return {
                        movement_id: String(
                            movement.troopId || data.troopId || row.serverId || ""
                        ),
                        village_id: String(
                            movement.villageIdTarget || villageId
                        ),
                        village_name: String(
                            movement.villageNameTarget
                            || villageNames[String(
                                movement.villageIdTarget || villageId
                            )]
                            || ""
                        ),
                        attacker_name: String(
                            movement.playerNameStart || data.playerName || ""
                        ),
                        source_village: String(
                            movement.villageNameStart
                            || data.villageName
                            || sourceVillage && sourceVillage.data
                                && sourceVillage.data.name
                            || villageNames[sourceVillageId]
                            || ""
                        ),
                        source_village_id: sourceVillageId,
                        arrival_time: Number(movement.timeFinish),
                        movement_type: movementType(
                            Number(movement.movementType)
                        ),
                        masked: Boolean(data.isMasked)
                    };
                });
                window[bindingName](JSON.stringify({
                    village_id: String(villageId),
                    server_time: serverTime,
                    attacks
                }));
            };
            const subscriptions = window[registryKey] = [];
            for (const rawVillageId of villageIds) {
                const villageId = String(rawVillageId);
                const collection = Troops.getCollection(
                    Troops.COLLECTION_MOVING,
                    villageId
                );
                if (!collection || typeof collection.subscribe !== "function") {
                    throw new Error(
                        `Movement updates are unavailable for village ${villageId}.`
                    );
                }
                const subscriptionId = collection.subscribe(
                    () => publish(villageId)
                );
                subscriptions.push({collection, subscriptionId});
            }
            window[registryKey] = subscriptions;
            return subscriptions.length;
        }"""
        try:
            result = self._tab.call_method(
                "Runtime.evaluate",
                expression=(
                    f"({expression})({json.dumps(village_ids)}, "
                    f"{registry_key}, {binding_name}, "
                    f"{json.dumps(village_names, ensure_ascii=False)})"
                ),
                returnByValue=True,
                _timeout=5,
            )
            remote_result = result.get("result", {})
            if remote_result.get("subtype") == "error":
                raise RuntimeError(
                    "The Travian page could not start live attack monitoring."
                )
            count = remote_result.get("value")
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count != len(village_ids)
            ):
                raise RuntimeError(
                    "The Travian page returned an invalid listener count."
                )
            return count
        except Exception:
            self.stop_incoming_attack_listener()
            raise

    def stop_incoming_attack_listener(self) -> None:
        if not self._incoming_attack_listener_active:
            return
        self._incoming_attack_listener_active = False
        if self._tab is None:
            return

        try:
            result = self._tab.call_method(
                "Runtime.evaluate",
                expression=f"""(() => {{
                    const subscriptions = window[
                        {json.dumps(self._incoming_attack_registry)}
                    ] || [];
                    for (const item of subscriptions) {{
                        item.collection.unsubscribe(item.subscriptionId);
                    }}
                    delete window[
                        {json.dumps(self._incoming_attack_registry)}
                    ];
                    return true;
                }})()""",
                returnByValue=True,
                _timeout=3,
            )
            if result.get("result", {}).get("value") is not True:
                logger.warning("The game did not confirm attack listener cleanup")
        except Exception:
            logger.warning(
                "Could not remove live incoming-attack subscriptions",
                exc_info=True,
            )
        try:
            self._tab.call_method(
                "Runtime.removeBinding",
                name=self._incoming_attack_binding,
                _timeout=3,
            )
        except Exception:
            logger.warning(
                "Could not remove the live incoming-attack browser binding",
                exc_info=True,
            )

    def _on_network_request_sent(
        self, params: dict | None = None, **event_params: object
    ) -> None:
        params = params if params is not None else event_params
        request_id = str(params.get("requestId", ""))
        request_data = params.get("request", {})
        url = str(request_data.get("url", ""))
        method = str(request_data.get("method", "GET"))
        request_body = request_data.get("postData")
        if (
            not isinstance(request_body, str)
            and method.upper() == "POST"
            and "/api/" in url
        ):
            try:
                body = self._tab.call_method(
                    "Network.getRequestPostData",
                    requestId=request_id,
                    _timeout=2,
                )
                request_body = body.get("postData")
            except Exception:
                logger.debug(
                    "Could not read request body for game API request %s",
                    request_id,
                    exc_info=True,
                )
        if request_id and self._request_filter.should_capture_response(
            url,
            method,
            request_body if isinstance(request_body, str) else None,
        ):
            self._network_responses[request_id] = {
                "url": url,
                "status": 0,
                "content_type": "",
            }

    def _on_network_response_received(
        self, params: dict | None = None, **event_params: object
    ) -> None:
        params = params if params is not None else event_params
        request_id = str(params.get("requestId", ""))
        response = params.get("response", {})
        request = self._network_responses.get(request_id)
        if request is None:
            return
        request["status"] = response.get("status", 0)
        request["content_type"] = response.get("mimeType", "")

    def _on_network_loading_finished(
        self, params: dict | None = None, **event_params: object
    ) -> None:
        params = params if params is not None else event_params
        request_id = str(params.get("requestId", ""))
        request = self._network_responses.pop(request_id, None)
        if request is None or self._network_response_handler is None:
            return
        if request.get("status", 0) < 200 or request.get("status", 0) >= 300:
            return
        content_type = str(request.get("content_type", "")).lower()
        if "json" not in content_type and "text/plain" not in content_type:
            return

        try:
            body = self._tab.call_method(
                "Network.getResponseBody",
                requestId=request_id,
                _timeout=3,
            )
            response_body = body.get("body", "")
            if body.get("base64Encoded"):
                response_body = base64.b64decode(response_body).decode("utf-8")
            raw_data = json.loads(response_body)
            if not isinstance(raw_data, dict):
                logger.debug("Ignoring non-object game response from %s", request["url"])
                return
            self._remember_village_names(raw_data)
            self._network_response_handler(request["url"], raw_data)
        except (ValueError, TypeError):
            logger.warning(
                "Could not decode captured game response from %s",
                request["url"],
                exc_info=True,
            )
        except Exception:
            logger.exception(
                "Failed to process captured game response from %s",
                request["url"],
            )

    def _remember_village_names(self, payload: dict) -> None:
        """Cache village names exposed by map/cache API responses."""
        resolved_names = {}
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                village_id = value.get("villageId")
                name = value.get("name")
                if (
                    str(village_id or "").isdigit()
                    and isinstance(name, str)
                    and name.strip()
                ):
                    self._village_names_by_id[str(village_id)] = name.strip()
                    resolved_names[str(village_id)] = name.strip()
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        if resolved_names and self._village_names_handler is not None:
            try:
                self._village_names_handler(resolved_names)
            except Exception:
                logger.exception("Could not forward resolved village names")

    def _on_network_loading_failed(
        self, params: dict | None = None, **event_params: object
    ) -> None:
        params = params if params is not None else event_params
        request_id = str(params.get("requestId", ""))
        request = self._network_responses.pop(request_id, None)
        if request:
            logger.warning(
                "Game request failed before response parsing: %s (%s)",
                request["url"],
                params.get("errorText", "unknown error"),
            )

    @property
    def is_connected(self) -> bool:
        return self._tab is not None

    def connect(self) -> dict:
        """Attach to an existing Travian tab without creating or navigating tabs."""
        self._browser = pychrome.Browser(url=self._cdp_url)
        tabs = self._browser.list_tab(timeout=3)
        game_tabs = [tab for tab in tabs if _is_game_url(_tab_url(tab))]
        if not game_tabs:
            self._browser = None
            raise RuntimeError(
                "No Travian tab found. Open the game in your browser and sign in first."
            )

        self._tab = game_tabs[0]
        try:
            self._attach_network_listeners()
            self._tab.start()
            self._tab.call_method("Network.enable")
            return self.read_game_state()
        except Exception:
            self.disconnect()
            raise

    def read_game_state(self) -> dict:
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=_READ_GAME_STATE,
            returnByValue=True,
            _timeout=3,
        )
        state = _parse_game_state(result)
        state["production_rates"] = self._read_production_rates(
            result.get("result", {}).get("value", {}).get("stockPoints", {}),
            state["resources"],
            state["capacities"],
        )
        return state

    def read_incoming_attacks(self, villages: list[dict]) -> list[dict]:
        """Read cached incoming attacks for every owned village."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")

        village_ids = [
            str(village.get("id") or village.get("village_id") or "")
            for village in villages
        ]
        if any(not village_id.isdigit() for village_id in village_ids):
            raise ValueError("Invalid village identifier in attack scan.")
        if len(set(village_ids)) != len(village_ids):
            raise ValueError("Duplicate village identifier in attack scan.")

        village_names = {
            village_id: str(village.get("name") or "").strip()
            for village_id, village in zip(village_ids, villages)
            if str(village.get("name") or "").strip()
        }
        expression = """(villageIds, villageNames) => {
            if (typeof Troops === "undefined"
                || !window.angular) {
                throw new Error("Troop movement data is unavailable.");
            }
            const injector = angular.element(document.body).injector();
            if (!injector) {
                throw new Error("The game data injector is unavailable.");
            }
            const villageService = injector.get("VillageService");
            const ownedVillages = villageService.U();
            if (!ownedVillages || !Array.isArray(ownedVillages.data)) {
                throw new Error("The game's owned village list is unavailable.");
            }
            const ownedIds = new Set(ownedVillages.data.map(village =>
                String(village.data && village.data.villageId || "")
            ));
            const missing = villageIds.filter(id => !ownedIds.has(String(id)));
            if (missing.length) {
                throw new Error(
                    `Village data is not available for ${missing.join(", ")}.`
                );
            }
            const serverTime = Number(
                injector.get("$rootScope").currentServerTime
            );
            if (!Number.isFinite(serverTime) || serverTime <= 0) {
                throw new Error("The game server time is unavailable.");
            }
            const movementType = type => {
                if (type === Number(Troops.MOVEMENT_TYPE_RAID)) return "Napad";
                if (type === Number(Troops.MOVEMENT_TYPE_SIEGE)) return "Oblężenie";
                if (type === Number(Troops.MOVEMENT_TYPE_SPY)) return "Zwiad";
                return "Atak";
            };
            const villages = villageIds.map(rawVillageId => {
                const villageId = String(rawVillageId);
                const collection = Troops.getCollection(
                    Troops.COLLECTION_MOVING,
                    villageId
                );
                if (!collection || !collection.filled
                    || !Array.isArray(collection.data)) {
                    throw new Error(
                        `Troop movement data is not loaded for village ${villageId}.`
                    );
                }
                const attacks = collection.data.filter(row =>
                    row && typeof row.getMovementGroup === "function"
                    && row.getMovementGroup(Number(villageId))
                        === "incoming_attacks"
                ).map(row => {
                    const data = row.data || {};
                    const movement = data.movement || {};
                    const sourceVillageId = String(
                        movement.villageIdStart || data.villageIdStart || ""
                    );
                    const sourceVillage = sourceVillageId
                        && typeof Village !== "undefined"
                        ? Village.get(sourceVillageId)
                        : null;
                    return {
                        movement_id: String(
                            movement.troopId || data.troopId || row.serverId || ""
                        ),
                        village_id: String(
                            movement.villageIdTarget || villageId
                        ),
                        village_name: String(
                            movement.villageNameTarget
                            || villageNames[String(
                                movement.villageIdTarget || villageId
                            )]
                            || ""
                        ),
                        attacker_name: String(
                            movement.playerNameStart || data.playerName || ""
                        ),
                        source_village: String(
                            movement.villageNameStart
                            || data.villageName
                            || sourceVillage && sourceVillage.data
                                && sourceVillage.data.name
                            || villageNames[sourceVillageId]
                            || ""
                        ),
                        source_village_id: sourceVillageId,
                        arrival_time: Number(movement.timeFinish),
                        movement_type: movementType(
                            Number(movement.movementType)
                        ),
                        masked: Boolean(data.isMasked)
                    };
                });
                return {village_id: villageId, attacks};
            });
            return {server_time: serverTime, villages};
        }"""

        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=(
                f"({expression})({json.dumps(village_ids)}, "
                f"{json.dumps(village_names, ensure_ascii=False)})"
            ),
            returnByValue=True,
            _timeout=5,
        )
        remote_result = result.get("result", {})
        if remote_result.get("subtype") == "error":
            raise RuntimeError("The Travian page could not read incoming attacks.")
        response = remote_result.get("value")
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("server_time"), (int, float))
            or isinstance(response.get("server_time"), bool)
            or not math.isfinite(response["server_time"])
            or not isinstance(response.get("villages"), list)
            or len(response["villages"]) != len(village_ids)
        ):
            raise RuntimeError("The Travian page returned invalid attack data.")

        village_names = {
            str(village.get("id") or village.get("village_id")):
                str(village.get("name") or "")
            for village in villages
        }
        returned_ids = set()
        attacks = []
        for village in response["villages"]:
            if (
                not isinstance(village, dict)
                or not isinstance(village.get("village_id"), str)
                or village["village_id"] not in village_names
                or village["village_id"] in returned_ids
                or not isinstance(village.get("attacks"), list)
            ):
                raise RuntimeError("The Travian page returned invalid village attack data.")
            village_id = village["village_id"]
            returned_ids.add(village_id)
            village_name = village_names[village_id] or f"Wioska {village_id}"
            for attack in village["attacks"]:
                if not isinstance(attack, dict):
                    raise RuntimeError(
                        f"The game returned an invalid attack for village {village_id}."
                    )
                movement_id = attack.get("movement_id")
                target_id = attack.get("village_id")
                arrival_time = attack.get("arrival_time")
                if (
                    not isinstance(movement_id, str)
                    or not movement_id
                    or str(target_id) != village_id
                    or isinstance(arrival_time, bool)
                    or not isinstance(arrival_time, (int, float))
                    or not math.isfinite(arrival_time)
                    or arrival_time <= 0
                ):
                    raise RuntimeError(
                        f"The game returned incomplete attack data for village {village_id}."
                    )
                attacks.append({
                    "movement_id": movement_id,
                    "village_id": village_id,
                    "village_name": (
                        str(attack.get("village_name") or "").strip()
                        or village_name
                    ),
                    "attacker_name": (
                        str(attack.get("attacker_name") or "").strip()
                        or "Nieznany gracz"
                    ),
                    "source_village_id": (
                        str(attack.get("source_village_id") or "").strip()
                        or None
                    ),
                    "source_village": (
                        str(attack.get("source_village") or "").strip()
                        or self._village_names_by_id.get(
                            str(attack.get("source_village_id") or ""),
                            "",
                        )
                        or (
                            f"Wioska {attack['source_village_id']}"
                            if str(attack.get("source_village_id") or "").isdigit()
                            else "Nieznana wioska"
                        )
                    ),
                    "arrival_time": float(arrival_time),
                    "seconds_remaining": max(
                        0.0,
                        float(arrival_time) - float(response["server_time"]),
                    ),
                    "movement_type": (
                        str(attack.get("movement_type") or "").strip()
                        or "Atak"
                    ),
                    "masked": bool(attack.get("masked")),
                })
        return attacks

    def read_troops_for_villages(self, villages: list[dict]) -> list[dict]:
        """Read stationed own-tribe units in each village, restoring the selection."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")
        initial_state = self.read_game_state()
        original_village_id = initial_state["selected_village_id"]
        rows = []
        try:
            for village in villages:
                village_id = village.get("id", "")
                if not isinstance(village_id, str) or not village_id.isdigit():
                    continue
                if village_id != original_village_id:
                    self.select_village(village_id)
                    self._wait_for_selected_village(village_id)
                result = self._tab.call_method(
                    "Runtime.evaluate",
                    expression=_READ_TROOPS,
                    returnByValue=True,
                    _timeout=3,
                )
                troops = result.get("result", {}).get("value", {})
                if not isinstance(troops, dict) or troops.get("village_id") != village_id:
                    raise RuntimeError(
                        f"Could not read troop counts for {village.get('name', 'village')}."
                    )
                tribe_id = troops.get("tribe_id", 0)
                units = troops.get("units", {})
                if not isinstance(units, dict):
                    units = {}
                for unit_number in range(1, 11):
                    rows.append({
                        "village_id": village_id,
                        "village": village["name"],
                        "unit": _GAUL_UNIT_NAMES.get(
                            unit_number,
                            f"Tribe {tribe_id} unit {unit_number}",
                        ),
                        "count": int(units.get(str(unit_number), 0)),
                    })
                rows.append({
                    "village_id": village_id,
                    "village": village["name"],
                    "unit": "Hero",
                    "count": int(troops.get("hero", 0)),
                })
        finally:
            if original_village_id and original_village_id != self._selected_village_id():
                self.select_village(original_village_id)
                self._wait_for_selected_village(original_village_id)
        return rows

    def read_recruitment_queues(self, villages: list[dict]) -> list[dict]:
        """Discover recruitable troop types and read their queues per village."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")

        village_ids = [
            str(village.get("id") or village.get("village_id") or "")
            for village in villages
        ]
        if any(not village_id.isdigit() for village_id in village_ids):
            raise ValueError("Invalid village identifier in recruitment queue scan.")

        expression = """(villageIds, buildingNames, troopNames) => {
            if (typeof UnitQueue === "undefined"
                || typeof Building === "undefined"
                || typeof Village === "undefined"
                || !window.Travian || !Travian.Config
                || !Travian.Config.troopConfig) {
                throw new Error("Troop configuration or queue models are unavailable.");
            }
            const troopConfig = Travian.Config.troopConfig;
            const buildingTypes = [19, 20, 21, 25, 26, 29, 30];
            const getTribeId = (village) => {
                const player = village && village.data
                    && typeof Player !== "undefined"
                    ? Player.get(String(village.data.playerId)) : null;
                const tribeId = Number(village && village.data
                    && village.data.tribeId || player && player.data
                    && player.data.tribeId);
                return Number.isFinite(tribeId) && tribeId > 0
                    ? tribeId : null;
            };
            const troopBuildingTypes = (unitType, troop) => {
                const configured = Number(
                    troop.buildingType || troop.recruitmentBuildingType
                );
                if (configured === 19) return [19, 29];
                if (configured === 20) return [20, 30];
                if (configured === 25 || configured === 26) return [25, 26];
                if (buildingTypes.includes(configured)) return [configured];
                const position = (unitType - 1) % 10 + 1;
                if (position <= 3) return [19, 29];
                if (position <= 6) return [20, 30];
                if (position <= 8) return [21];
                return [25, 26];
            };
            return villageIds.flatMap(villageId => {
                const model = UnitQueue.get(villageId);
                if (!model || !model.filled || !model.data
                    || String(model.data.villageId) !== String(villageId)
                    || !model.data.buildingTypes
                    || typeof model.data.buildingTypes !== "object") {
                    throw new Error(
                        `Recruitment queue data is not loaded for village ${villageId}.`
                    );
                }
                const village = Village.get(villageId);
                const tribeId = getTribeId(village);
                if (!village || tribeId === null) {
                    throw new Error(`Could not detect the tribe for village ${villageId}.`);
                }
                const buildings = Building.getCollection(villageId);
                const villageBuildings = buildings
                    && Array.isArray(buildings.data)
                    ? buildings.data : [];
                const availableBuildingTypes = villageBuildings.map(building =>
                    building.data ? Number(building.data.buildingType) : 0
                );
                return Object.entries(troopConfig).flatMap(([rawUnitType, troop]) => {
                    const unitType = Number(rawUnitType);
                    if (!Number.isInteger(unitType) || unitType <= 0
                        || !troop || typeof troop !== "object"
                        || !troop.costs || typeof troop.costs !== "object") {
                        return [];
                    }
                    const tribeIdValue = Number(troop.tribeId);
                    const tribeValue = Number(troop.tribe);
                    const configuredTribe = Number.isFinite(tribeIdValue)
                        && tribeIdValue > 0
                        ? tribeIdValue
                        : Number.isFinite(tribeValue) && tribeValue > 0
                            ? tribeValue : Math.ceil(unitType / 10);
                    if (configuredTribe !== tribeId) return [];

                    const candidates = troopBuildingTypes(unitType, troop);
                    const existing = candidates.filter(type =>
                        availableBuildingTypes.includes(type));
                    const targetBuildingTypes = existing.length
                        ? existing : [candidates[0]];
                    const buildingType = targetBuildingTypes[0];
                    const queueTypes = targetBuildingTypes.filter(type =>
                        Object.prototype.hasOwnProperty.call(
                            model.data.buildingTypes, String(type)
                        ));
                    const countedTypes = queueTypes.length
                        ? queueTypes : targetBuildingTypes;
                    let count = 0;
                    let otherCount = 0;
                    for (const type of countedTypes) {
                        const entries = model.data.buildingTypes[String(type)];
                        if (!Array.isArray(entries)) continue;
                        for (const entry of entries) {
                            const entryCount = Math.max(
                                0,
                                Number(entry.count) || 0
                            );
                            if (Number(entry.unitType) === unitType) {
                                count += entryCount;
                            } else {
                                otherCount += entryCount;
                            }
                        }
                    }
                    const configuredName = [
                        troop.name, troop.label, troop.title
                    ].find(value =>
                        typeof value === "string" && value.trim()
                    );
                    const baseTrainingTime = Number(troop.time);
                    const troopSpeed = Number(Travian.Config.TG_TROOP_SPEED);
                    const trainingTimesSeconds = existing.map(type => {
                        const building = villageBuildings.find(item =>
                            item.data
                            && Number(item.data.buildingType) === type
                        );
                        const timeModifier = Number(
                            building && building.data
                                && building.data.currentEffect
                                && building.data.currentEffect.values
                                && building.data.currentEffect.values[0]
                        );
                        if (!Number.isFinite(baseTrainingTime)
                            || baseTrainingTime <= 0
                            || !Number.isFinite(troopSpeed)
                            || troopSpeed <= 0
                            || !Number.isFinite(timeModifier)
                            || timeModifier <= 0) {
                            return null;
                        }
                        return baseTrainingTime / troopSpeed
                            * timeModifier / 100;
                    });
                    const costs = Object.fromEntries(
                        Object.entries(troop.costs).map(
                            ([resourceId, amount]) =>
                                [resourceId, Number(amount)]
                        )
                    );
                    const unitName = configuredName
                        || troopNames[String(unitType)]
                        || `Jednostka ${unitType}`;
                    return [{
                        village_id: String(villageId),
                        tribe_id: tribeId,
                        building_type: buildingType,
                        building_types: targetBuildingTypes,
                        building: buildingNames[String(buildingType)]
                            || `Building ${buildingType}`,
                        unit_type: unitType,
                        unit: unitName,
                        building_available: existing.length > 0,
                        costs,
                        training_times_seconds: trainingTimesSeconds,
                        count,
                        other_count: otherCount
                    }];
                });
            });
        }"""
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=(
                f"({expression})("
                f"{json.dumps(village_ids)}, "
                f"{json.dumps(_RECRUITMENT_BUILDING_NAMES)}, "
                f"{json.dumps(_TROOP_NAMES_BY_TYPE)}"
                ")"
            ),
            returnByValue=True,
            _timeout=5,
        )
        remote_result = result.get("result", {})
        if remote_result.get("subtype") == "error":
            raise RuntimeError(
                "The Travian page could not read recruitment queue data."
            )
        queue_rows = remote_result.get("value")
        if not isinstance(queue_rows, list) or not queue_rows:
            raise RuntimeError(
                "The Travian page returned an incomplete recruitment queue scan."
            )

        village_names = {
            str(village.get("id") or village.get("village_id")): str(
                village.get("name") or "Nieznana wioska"
            )
            for village in villages
        }
        normalized_rows = []
        for row in queue_rows:
            if not isinstance(row, dict):
                raise RuntimeError(
                    "The Travian page returned an invalid recruitment queue row."
                )
            village_id = str(row.get("village_id") or "")
            building_type = row.get("building_type")
            unit_type = row.get("unit_type")
            count = row.get("count")
            other_count = row.get("other_count")
            building_types = row.get("building_types")
            unit_name = row.get("unit")
            raw_costs = row.get("costs")
            costs = {}
            if isinstance(raw_costs, dict):
                for resource_id, resource_name in {
                    "1": "wood",
                    "2": "clay",
                    "3": "iron",
                    "4": "grain",
                }.items():
                    amount = raw_costs.get(resource_id)
                    if (
                        isinstance(amount, (int, float))
                        and not isinstance(amount, bool)
                        and math.isfinite(amount)
                        and amount >= 0
                    ):
                        costs[resource_name] = float(amount)
            raw_training_times = row.get("training_times_seconds")
            training_times_seconds = (
                [
                    float(value)
                    if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value > 0
                    else None
                    for value in raw_training_times
                ]
                if isinstance(raw_training_times, list)
                else []
            )
            if (
                village_id not in village_names
                or isinstance(unit_type, bool)
                or not isinstance(unit_type, int)
                or unit_type <= 0
                or building_type not in _RECRUITMENT_BUILDING_TYPES
                or not isinstance(building_types, list)
                or not building_types
                or any(
                    item not in _RECRUITMENT_BUILDING_TYPES
                    for item in building_types
                )
                or not isinstance(unit_name, str)
                or not unit_name.strip()
                or not isinstance(count, (int, float))
                or isinstance(count, bool)
                or count < 0
                or not isinstance(other_count, (int, float))
                or isinstance(other_count, bool)
                or other_count < 0
            ):
                raise RuntimeError(
                    "The Travian page returned invalid recruitment queue data."
                )
            normalized_rows.append({
                "village_id": village_id,
                "village": village_names[village_id],
                "building": str(row.get("building") or "Budynek"),
                "unit": unit_name.strip(),
                "building_type": building_type,
                "building_types": building_types,
                "unit_type": unit_type,
                "unit_id": str(unit_type),
                "tribe_id": row.get("tribe_id"),
                "building_available": row.get("building_available") is True,
                "costs": costs,
                "training_times_seconds": training_times_seconds,
                "count": int(count),
                "other_count": int(other_count),
            })

        if len({
            (row["village_id"], row["unit_id"])
            for row in normalized_rows
        }) != len(normalized_rows) or {
            row["village_id"] for row in normalized_rows
        } != set(village_ids):
            raise RuntimeError(
                "The Travian page returned duplicate recruitment queue data."
            )
        return normalized_rows

    def read_resource_milling_state(self, villages: list[dict]) -> list[dict]:
        """Read resources, storage capacities, and gold for every village."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")

        village_ids = [
            str(village.get("id") or village.get("village_id") or "")
            for village in villages
        ]
        if any(not village_id.isdigit() for village_id in village_ids):
            raise ValueError("Invalid village identifier in milling scan.")

        expression = """villageIds => {
            if (typeof Village === "undefined"
                || typeof Player === "undefined") {
                throw new Error("Village or player models are unavailable.");
            }
            return villageIds.map(villageId => {
                const village = Village.get(villageId);
                if (!village || !village.data
                    || String(village.data.villageId) !== String(villageId)
                    || !village.data.storage
                    || !village.data.storageCapacity
                    || typeof village.getResources !== "function") {
                    throw new Error(
                        `Resource data is not loaded for village ${villageId}.`
                    );
                }
                const currentResources = village.getResources();
                if (!currentResources || typeof currentResources !== "object") {
                    throw new Error(
                        `Current resources are unavailable for village ${villageId}.`
                    );
                }
                const playerId = String(village.data.playerId);
                const player = Player.get(playerId);
                const gold = player && player.data
                    ? Number(player.data.gold) : NaN;
                if (!Number.isFinite(gold) || gold < 0) {
                    throw new Error("Current gold balance is unavailable.");
                }
                const resourceIds = {wood: "1", clay: "2", iron: "3", grain: "4"};
                const resources = {};
                const capacities = {};
                for (const [name, id] of Object.entries(resourceIds)) {
                    const amount = Number(currentResources[id]);
                    const capacity = Number(village.data.storageCapacity[id]);
                    if (!Number.isFinite(amount) || amount < 0
                        || !Number.isFinite(capacity) || capacity < 0) {
                        throw new Error(
                            `Invalid ${name} storage data for village ${villageId}.`
                        );
                    }
                    resources[name] = amount;
                    capacities[name] = capacity;
                }
                return {
                    village_id: String(villageId),
                    resources,
                    capacities,
                    gold
                };
            });
        }"""
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=(
                f"({expression})({json.dumps(village_ids)})"
            ),
            returnByValue=True,
            _timeout=5,
        )
        remote_result = result.get("result", {})
        if remote_result.get("subtype") == "error":
            raise RuntimeError(
                "The Travian page could not read resource milling state."
            )
        rows = remote_result.get("value")
        if not isinstance(rows, list) or len(rows) != len(village_ids):
            raise RuntimeError(
                "The Travian page returned an incomplete milling scan."
            )

        normalized_rows = []
        expected_resources = {"wood", "clay", "iron", "grain"}
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(
                    "The Travian page returned invalid milling data."
                )
            resources = row.get("resources")
            capacities = row.get("capacities")
            gold = row.get("gold")
            if (
                str(row.get("village_id", "")) not in village_ids
                or not isinstance(resources, dict)
                or not isinstance(capacities, dict)
                or not expected_resources.issubset(resources)
                or not expected_resources.issubset(capacities)
                or not isinstance(gold, (int, float))
                or isinstance(gold, bool)
                or gold < 0
            ):
                raise RuntimeError(
                    "The Travian page returned invalid milling data."
                )
            clean_resources = {}
            clean_capacities = {}
            for name in expected_resources:
                amount = resources[name]
                capacity = capacities[name]
                if (
                    not isinstance(amount, (int, float))
                    or isinstance(amount, bool)
                    or not math.isfinite(amount)
                    or amount < 0
                    or not isinstance(capacity, (int, float))
                    or isinstance(capacity, bool)
                    or not math.isfinite(capacity)
                    or capacity < 0
                ):
                    raise RuntimeError(
                        "The Travian page returned invalid milling storage data."
                    )
                clean_resources[name] = float(amount)
                clean_capacities[name] = float(capacity)
            normalized_rows.append({
                "village_id": str(row["village_id"]),
                "resources": clean_resources,
                "capacities": clean_capacities,
                "gold": float(gold),
            })

        if len({row["village_id"] for row in normalized_rows}) != len(village_ids):
            raise RuntimeError(
                "The Travian page returned duplicate milling data."
            )
        return normalized_rows

    def queue_resource_milling(
        self,
        village_id: str,
        settings: dict,
        minimum_gold_balance: int,
    ) -> dict:
        """Submit one confirmed-format NPC trader exchange for a village."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")
        if (
            not isinstance(village_id, str)
            or not village_id.isdigit()
            or not isinstance(settings, dict)
            or isinstance(minimum_gold_balance, bool)
            or not isinstance(minimum_gold_balance, int)
            or minimum_gold_balance < 0
        ):
            raise ValueError("Invalid resource milling request.")

        trigger_percent = settings.get("trigger_percent")
        refill_trigger_percent = settings.get("refill_trigger_percent", 5)
        mode = settings.get("mode", "empty")
        clay_percent = settings.get("clay_percent")
        iron_percent = settings.get("iron_percent")
        wood_percent = settings.get("wood_percent")
        values = (
            trigger_percent,
            refill_trigger_percent,
            clay_percent,
            iron_percent,
            wood_percent,
        )
        if (
            not isinstance(mode, str)
            or mode not in {"empty", "refill"}
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in values
            )
            or not (
                1 <= trigger_percent <= 100
                and 1 <= refill_trigger_percent <= 100
                and all(0 <= value <= 100 for value in values[2:])
                and clay_percent + iron_percent + wood_percent == 100
            )
        ):
            raise ValueError("Invalid resource milling distribution.")

        request_id = uuid.uuid4().hex
        expression = """(
            villageId, triggerPercent, refillTriggerPercent, mode,
            clayPercent, ironPercent, woodPercent, minimumGoldBalance,
            traderPrice, requestId
        ) => {
            if (typeof Village === "undefined"
                || typeof Player === "undefined"
                || !window.Travian
                || typeof Travian.writeRequest !== "function") {
                throw new Error("NPC trader models are unavailable.");
            }
            const village = Village.get(villageId);
            if (!village || !village.data
                || !village.data.storage
                || !village.data.storageCapacity
                || typeof village.getResources !== "function") {
                throw new Error("Village resource data is unavailable.");
            }
            const player = Player.get(String(village.data.playerId));
            const gold = player && player.data
                ? Number(player.data.gold) : NaN;
            if (!Number.isFinite(gold) || gold < minimumGoldBalance + traderPrice) {
                return {
                    status: "below_gold_reserve",
                    gold: Number.isFinite(gold) ? gold : null
                };
            }
            const currentResources = village.getResources();
            if (!currentResources || typeof currentResources !== "object") {
                throw new Error("Current resources are unavailable.");
            }
            const grain = Number(currentResources["4"]);
            const granaryCapacity = Number(village.data.storageCapacity["4"]);
            if (!Number.isFinite(grain) || grain < 0
                || !Number.isFinite(granaryCapacity) || granaryCapacity <= 0) {
                throw new Error("Granary data is unavailable.");
            }
            const grainPercent = grain / granaryCapacity * 100;
            const activeThreshold = mode === "refill"
                ? refillTriggerPercent : triggerPercent;
            if (mode === "empty" && grainPercent < activeThreshold
                || mode === "refill" && grainPercent > activeThreshold) {
                return {status: "below_threshold", grainPercent};
            }

            const resources = ["1", "2", "3", "4"].map(id =>
                Number(currentResources[id]));
            if (resources.some(value => !Number.isFinite(value) || value < 0)) {
                throw new Error("Current resource amounts are invalid.");
            }
            const total = Math.floor(resources.reduce((sum, value) => sum + value, 0));
            if (total <= 0) return {status: "nothing_to_mill"};
            const targetGrain = mode === "refill"
                ? Math.min(Math.floor(granaryCapacity), total) : 0;
            const remainder = total - targetGrain;

            const shares = [
                {id: "1", percent: woodPercent},
                {id: "2", percent: clayPercent},
                {id: "3", percent: ironPercent}
            ];
            const distribution = {};
            let distributed = 0;
            let remainderShare = shares[0];
            for (const share of shares) {
                const amount = Math.floor(remainder * share.percent / 100);
                distribution[share.id] = amount;
                distributed += amount;
                if (share.percent > remainderShare.percent) {
                    remainderShare = share;
                }
            }
            distribution[remainderShare.id] += remainder - distributed;
            distribution["4"] = targetGrain;

            const accepted = Travian.writeRequest(
                "premiumFeature/bookFeature",
                {
                    featureName: "NPCTrader",
                    params: {
                        villageId: Number(villageId),
                        distributeRes: distribution,
                        price: traderPrice
                    }
                },
                response => {
                    const villageCache = response && Array.isArray(response.cache)
                        ? response.cache.find(item =>
                            item.name === `Village:${villageId}`)
                        : null;
                    const playerId = String(village.data.playerId);
                    const playerCache = response && Array.isArray(response.cache)
                        ? response.cache.find(item =>
                            item.name === `Player:${playerId}`)
                        : null;
                    window[
                        "__travianAssistantMillingResponse_" + requestId
                    ] = {
                        requestId,
                        targetGrain,
                        storage: villageCache && villageCache.data
                            ? villageCache.data.storage || null : null,
                        grainCapacity: villageCache && villageCache.data
                            && villageCache.data.storageCapacity
                            ? villageCache.data.storageCapacity["4"] : null,
                        gold: playerCache && playerCache.data
                            ? Number(playerCache.data.gold) : null
                    };
                }
            );
            return {
                status: accepted ? "submitted" : "request_busy",
                request_id: requestId,
                distribution,
                target_grain: targetGrain
            };
        }"""
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=(
                f"({expression})("
                f"{json.dumps(village_id)}, {trigger_percent}, "
                f"{refill_trigger_percent}, {json.dumps(mode)}, "
                f"{clay_percent}, {iron_percent}, {wood_percent}, "
                f"{minimum_gold_balance}, {NPC_TRADER_PRICE}, "
                f"{json.dumps(request_id)}"
                ")"
            ),
            returnByValue=True,
            _timeout=5,
        )
        remote_result = result.get("result", {})
        if remote_result.get("subtype") == "error":
            raise RuntimeError(
                "The Travian page could not prepare the NPC trader exchange."
            )
        outcome = remote_result.get("value")
        if not isinstance(outcome, dict) or outcome.get("status") not in {
            "below_gold_reserve",
            "below_threshold",
            "nothing_to_mill",
            "submitted",
            "request_busy",
        }:
            raise RuntimeError(
                "The Travian page returned an invalid milling result."
            )
        if outcome["status"] == "submitted" and (
            outcome.get("request_id") != request_id
            or isinstance(outcome.get("target_grain"), bool)
            or not isinstance(outcome.get("target_grain"), (int, float))
            or outcome["target_grain"] < 0
        ):
            raise RuntimeError(
                "The Travian page returned an invalid milling request ID."
            )
        outcome["village_id"] = village_id
        return outcome

    def read_resource_milling_response(
        self,
        request_id: str,
        mode: str,
        trigger_percent: int,
        target_grain: int,
    ) -> dict:
        """Read the asynchronous NPC trader response for one milling order."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")
        if (
            not isinstance(request_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", request_id)
            or not isinstance(mode, str)
            or mode not in {"empty", "refill"}
            or isinstance(trigger_percent, bool)
            or not isinstance(trigger_percent, int)
            or not 1 <= trigger_percent <= 100
            or isinstance(target_grain, bool)
            or not isinstance(target_grain, int)
            or target_grain < 0
        ):
            raise ValueError("Invalid milling response lookup.")

        expression = """(requestId, mode, triggerPercent, targetGrain) => {
            const key = "__travianAssistantMillingResponse_" + requestId;
            const response = window[key];
            if (!response || response.requestId !== requestId) {
                return {status: "pending"};
            }
            delete window[key];
            const storage = response.storage;
            const amounts = storage
                ? ["1", "2", "3", "4"].map(id => Number(storage[id]))
                : [];
            const capacity = Number(response.grainCapacity);
            if (amounts.length !== 4
                || amounts.some(value => !Number.isFinite(value) || value < 0)
                || !Number.isFinite(capacity) || capacity <= 0) {
                return {status: "unconfirmed"};
            }
            return {
                status: mode === "empty"
                    ? (amounts[3] / capacity * 100 < triggerPercent
                        ? "confirmed" : "unconfirmed")
                    : (amounts[3] >= targetGrain - 1
                        ? "confirmed" : "unconfirmed"),
                resources: {
                    wood: amounts[0],
                    clay: amounts[1],
                    iron: amounts[2],
                    grain: amounts[3]
                },
                grain_capacity: capacity,
                target_grain: response.targetGrain,
                gold: response.gold
            };
        }"""
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=(
                f"({expression})("
                f"{json.dumps(request_id)}, {json.dumps(mode)}, "
                f"{trigger_percent}, {target_grain}"
                ")"
            ),
            returnByValue=True,
            _timeout=5,
        )
        remote_result = result.get("result", {})
        if remote_result.get("subtype") == "error":
            raise RuntimeError(
                "The Travian page could not read the milling response."
            )
        outcome = remote_result.get("value")
        if not isinstance(outcome, dict) or outcome.get("status") not in {
            "pending",
            "confirmed",
            "unconfirmed",
        }:
            raise RuntimeError(
                "The Travian page returned an invalid milling response."
            )
        if outcome["status"] != "pending":
            resources = outcome.get("resources")
            if outcome["status"] == "confirmed" and (
                not isinstance(resources, dict)
                or set(resources) != {"wood", "clay", "iron", "grain"}
                or not isinstance(outcome.get("grain_capacity"), (int, float))
                or isinstance(outcome.get("grain_capacity"), bool)
                or not math.isfinite(outcome["grain_capacity"])
                or outcome["grain_capacity"] <= 0
                or not isinstance(outcome.get("target_grain"), (int, float))
                or isinstance(outcome.get("target_grain"), bool)
                or not math.isfinite(outcome["target_grain"])
                or outcome["target_grain"] < 0
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or value < 0
                    for value in resources.values()
                )
                or (
                    mode == "empty"
                    and (
                        resources.get("grain", float("inf"))
                        / outcome["grain_capacity"] * 100
                        >= trigger_percent
                    )
                )
                or (
                    mode == "refill"
                    and (
                        resources.get("grain", -1)
                        < outcome["target_grain"] - 1
                    )
                )
            ):
                raise RuntimeError(
                    "The Travian page returned invalid milling resources."
                )
            if outcome.get("gold") is not None and (
                not isinstance(outcome["gold"], (int, float))
                or isinstance(outcome["gold"], bool)
                or not math.isfinite(outcome["gold"])
                or outcome["gold"] < 0
            ):
                raise RuntimeError(
                    "The Travian page returned an invalid gold balance."
                )
        return outcome

    def queue_recruitment(
        self,
        row: dict,
        batch_size: int,
        queue_threshold: int = 1,
    ) -> dict:
        """Queue a target troop batch after rechecking the live queue and resources."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")
        if (
            not isinstance(row, dict)
            or not str(row.get("village_id", "")).isdigit()
            or row.get("building_type") not in _RECRUITMENT_BUILDING_TYPES
            or isinstance(row.get("unit_type"), bool)
            or not isinstance(row.get("unit_type"), int)
            or row["unit_type"] <= 0
            or isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 100
            or isinstance(queue_threshold, bool)
            or not isinstance(queue_threshold, int)
            or not 0 <= queue_threshold <= 100
        ):
            raise ValueError("Invalid automatic recruitment request.")

        village_id = str(row["village_id"])
        building_type = int(row["building_type"])
        unit_type = int(row["unit_type"])
        building_types = row.get("building_types", [building_type])
        if (
            not isinstance(building_types, list)
            or not building_types
            or building_type not in building_types
            or any(
                item not in _RECRUITMENT_BUILDING_TYPES
                for item in building_types
            )
        ):
            raise ValueError("Invalid recruitment building choices.")

        request_id = uuid.uuid4().hex
        expression = """(
            villageId, buildingType, buildingTypes, unitType, quantity,
            queueThreshold, requestId
        ) => {
            if (typeof UnitQueue === "undefined"
                || typeof Building === "undefined"
                || typeof Village === "undefined"
                || !window.Travian
                || typeof Travian.writeRequest !== "function") {
                throw new Error("Recruitment models are unavailable.");
            }
            const queue = UnitQueue.get(villageId);
            if (!queue || !queue.filled || !queue.data
                || String(queue.data.villageId) !== String(villageId)
                || !queue.data.buildingTypes
                || typeof queue.data.buildingTypes !== "object") {
                throw new Error("Recruitment queue data is unavailable.");
            }
            const count = buildingTypes.reduce((total, type) => {
                const entries = queue.data.buildingTypes[String(type)];
                return total + (Array.isArray(entries)
                    ? entries.reduce((sum, entry) =>
                        Number(entry.unitType) === unitType
                            ? sum + Math.max(0, Number(entry.count) || 0)
                            : sum, 0)
                    : 0);
            }, 0);
            if (count > queueThreshold) return {status: "queue_not_low"};

            const collection = Building.getCollection(villageId);
            const building = collection && Array.isArray(collection.data)
                ? buildingTypes.map(type => collection.data.find(item =>
                    item.data
                    && Number(item.data.buildingType) === type
                    && Number.isInteger(Number(item.data.locationId))
                )).find(Boolean)
                : null;
            if (!building || !Number.isInteger(Number(building.data.locationId))) {
                return {status: "building_missing"};
            }

            const village = Village.get(villageId);
            const troop = Travian.Config
                && Travian.Config.troopConfig
                && Travian.Config.troopConfig[unitType];
            if (!village || typeof village.getResources !== "function"
                || !troop || !troop.costs) {
                throw new Error("Recruitment costs or village resources are unavailable.");
            }
            const availableResources = village.getResources();
            for (const [resourceId, unitCost] of Object.entries(troop.costs)) {
                const required = Number(unitCost) * quantity;
                const available = Number(availableResources[resourceId]);
                if (!Number.isFinite(required) || required < 0
                    || !Number.isFinite(available) || available < required) {
                    return {status: "insufficient_resources"};
                }
            }

            const accepted = Travian.writeRequest(
                "building/recruitUnits",
                {
                    villageId: Number(villageId),
                    locationId: Number(building.data.locationId),
                    units: {[unitType]: quantity}
                },
                response => {
                    const queueCache = response && Array.isArray(response.cache)
                        ? response.cache.find(item =>
                            item.name === `UnitQueue:${villageId}`)
                        : null;
                    const queueData = queueCache && queueCache.data;
                    const responseCount = queueData
                        && queueData.buildingTypes
                        ? buildingTypes.reduce((total, type) => {
                            const entries = queueData.buildingTypes[String(type)];
                            return total + (Array.isArray(entries)
                                ? entries.reduce((sum, entry) =>
                                    Number(entry.unitType) === unitType
                                        ? sum + Math.max(0, Number(entry.count) || 0)
                                        : sum, 0)
                                : 0);
                        }, 0)
                        : null;
                    window[
                        "__travianAssistantRecruitmentResponse_" + requestId
                    ] = {
                        requestId,
                        count: responseCount
                    };
                }
            );
            return {
                status: accepted ? "submitted" : "request_busy",
                request_id: requestId
            };
        }"""
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=(
                f"({expression})("
                f"{json.dumps(village_id)}, "
                f"{building_type}, {json.dumps(building_types)}, "
                f"{unit_type}, {batch_size}, "
                f"{queue_threshold}, {json.dumps(request_id)}"
                ")"
            ),
            returnByValue=True,
            _timeout=5,
        )
        remote_result = result.get("result", {})
        if remote_result.get("subtype") == "error":
            raise RuntimeError(
                "The Travian page could not prepare automatic recruitment."
            )
        outcome = remote_result.get("value")
        if not isinstance(outcome, dict) or outcome.get("status") not in {
            "queue_not_low",
            "building_missing",
            "insufficient_resources",
            "submitted",
            "request_busy",
        }:
            raise RuntimeError(
                "The Travian page returned an invalid automatic recruitment result."
            )
        if outcome["status"] == "submitted" and (
            outcome.get("request_id") != request_id
        ):
            raise RuntimeError(
                "The Travian page returned an invalid recruitment request ID."
            )
        outcome["village_id"] = village_id
        outcome["unit"] = str(
            row.get("unit")
            or _TROOP_NAMES_BY_TYPE.get(
                unit_type,
                f"Jednostka {unit_type}",
            )
        )
        return outcome

    def read_recruitment_order_response(
        self,
        request_id: str,
        queue_threshold: int,
    ) -> dict:
        """Read the asynchronous game response for one recruitment request."""
        if self._tab is None:
            raise RuntimeError("Not connected to Chrome.")
        if (
            not isinstance(request_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", request_id)
            or isinstance(queue_threshold, bool)
            or not isinstance(queue_threshold, int)
            or not 0 <= queue_threshold <= 100
        ):
            raise ValueError("Invalid recruitment response lookup.")

        expression = """(requestId, queueThreshold) => {
            const key = "__travianAssistantRecruitmentResponse_" + requestId;
            const response = window[key];
            if (!response || response.requestId !== requestId) {
                return {status: "pending"};
            }
            delete window[key];
            if (!Number.isInteger(response.count) || response.count < 0) {
                return {status: "unconfirmed"};
            }
            return {
                status: response.count > queueThreshold
                    ? "confirmed" : "unconfirmed",
                count: response.count
            };
        }"""
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=(
                f"({expression})("
                f"{json.dumps(request_id)}, {queue_threshold}"
                ")"
            ),
            returnByValue=True,
            _timeout=5,
        )
        remote_result = result.get("result", {})
        if remote_result.get("subtype") == "error":
            raise RuntimeError(
                "The Travian page could not read the recruitment response."
            )
        outcome = remote_result.get("value")
        if not isinstance(outcome, dict) or outcome.get("status") not in {
            "pending",
            "confirmed",
            "unconfirmed",
        }:
            raise RuntimeError(
                "The Travian page returned an invalid recruitment response."
            )
        if outcome["status"] == "confirmed" and (
            not isinstance(outcome.get("count"), int)
            or isinstance(outcome.get("count"), bool)
            or outcome["count"] < 0
        ):
            raise RuntimeError(
                "The Travian page returned an invalid recruitment count."
            )
        if outcome["status"] == "unconfirmed" and outcome.get("count") is not None and (
            not isinstance(outcome["count"], int)
            or isinstance(outcome["count"], bool)
            or outcome["count"] < 0
        ):
            raise RuntimeError(
                "The Travian page returned an invalid recruitment count."
            )
        return outcome

    def _selected_village_id(self) -> str:
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression="""(() => {
                const option = document.querySelector(
                    ".villageListDropDown .dropdownOption.selected"
                );
                const match = option
                    ? option.className.match(/\\bdropDownItem(\\d+)\\b/)
                    : null;
                return match ? match[1] : "";
            })()""",
            returnByValue=True,
            _timeout=3,
        )
        return str(result.get("result", {}).get("value", ""))

    def _wait_for_selected_village(self, village_id: str) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self._selected_village_id() == village_id:
                time.sleep(0.35)
                return
            time.sleep(0.1)
        raise RuntimeError(f"Timed out while selecting village {village_id}.")

    def _read_production_rates(
        self,
        stock_points: dict,
        resources: dict,
        capacities: dict,
    ) -> dict[str, int | None]:
        """Estimate net hourly change from the game's live full/empty countdown."""
        rates: dict[str, int | None] = {
            name: None for name in ("wood", "clay", "iron", "grain")
        }
        if not isinstance(stock_points, dict):
            return rates

        for name in rates:
            point = stock_points.get(name)
            current = resources.get(name)
            capacity = capacities.get(name)
            if (
                not isinstance(point, dict)
                or not isinstance(current, int)
                or not isinstance(capacity, int)
            ):
                continue
            try:
                expected_label = "crop" if name == "grain" else name
                self._dispatch_mouse_move(point["x"], point["y"])
                tooltip = ""
                deadline = time.monotonic() + 0.8
                while time.monotonic() < deadline:
                    result = self._tab.call_method(
                        "Runtime.evaluate",
                        expression="""(() => {
                            const tooltip = document.querySelector(
                                ".tooltip .resourceTooltip"
                            );
                            return tooltip ? tooltip.innerText : "";
                        })()""",
                        returnByValue=True,
                        _timeout=2,
                    )
                    tooltip = str(result.get("result", {}).get("value", ""))
                    if (
                        expected_label in tooltip.lower()
                        and re.search(r"\d{1,3}:\d{2}:\d{2}", tooltip)
                    ):
                        break
                    time.sleep(0.05)

                normalized = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", tooltip)
                countdown = re.search(
                    r"(Full in|Empty in)\s*(\d{1,3}):(\d{2}):(\d{2})",
                    normalized,
                    re.IGNORECASE,
                )
                if not countdown:
                    continue
                seconds = (
                    int(countdown.group(2)) * 3600
                    + int(countdown.group(3)) * 60
                    + int(countdown.group(4))
                )
                if seconds <= 0:
                    continue
                if countdown.group(1).lower() == "full in":
                    amount = capacity - current
                    sign = 1
                else:
                    amount = current
                    sign = -1
                if amount <= 0:
                    continue
                rates[name] = sign * round(amount * 3600 / seconds)
            except Exception:
                logger.warning("Could not read live %s production rate", name, exc_info=True)
        return rates

    def _dispatch_mouse_move(self, x: float, y: float) -> None:
        self._tab.call_method(
            "Input.dispatchMouseEvent",
            type="mouseMoved",
            x=x,
            y=y,
        )

    def select_village(self, village_id: str) -> None:
        """Select one of the user's villages through the game's village dropdown."""
        if self._tab is None:
            raise RuntimeError("Not connected to the browser.")
        if not village_id.isdigit():
            raise ValueError("Invalid village identifier.")

        dropdown_state = self._tab.call_method(
            "Runtime.evaluate",
            expression="""(() => {
                const body = document.querySelector(".villageListDropDown");
                const head = document.querySelector("#villageList .dropdownHead");
                return {
                    available: Boolean(body && head),
                    open: Boolean(head && head.classList.contains("opened"))
                };
            })()""",
            returnByValue=True,
            _timeout=3,
        ).get("result", {}).get("value", {})
        if not dropdown_state.get("available"):
            raise RuntimeError("The game's village selector is unavailable.")
        if not dropdown_state["open"]:
            self._click_element(
                'document.querySelector("#villageList .dropdownHead")'
            )
            time.sleep(0.2)

        expression = """(() => {
            const option = [...document.querySelectorAll(
                ".villageListDropDown .dropdownOption"
            )].find(item => {
                const match = item.className.match(/\\bdropDownItem(\\d+)\\b/);
                return match && match[1] === %s;
            });
            if (!option) return null;
            const rect = option.getBoundingClientRect();
            return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        })()""" % json.dumps(village_id)
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            _timeout=3,
        ).get("result", {}).get("value")
        if not isinstance(result, dict):
            raise RuntimeError("The selected village is no longer available.")
        self._dispatch_click(result["x"], result["y"])

    def _click_element(self, expression: str) -> None:
        """Click a page element using its visible screen coordinates."""
        result = self._tab.call_method(
            "Runtime.evaluate",
            expression=f"""(() => {{
                const element = {expression};
                if (!element) return null;
                const rect = element.getBoundingClientRect();
                if (!rect.width || !rect.height) return null;
                return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
            }})()""",
            returnByValue=True,
            _timeout=3,
        ).get("result", {}).get("value")
        if not isinstance(result, dict):
            raise RuntimeError("The game's village selector is not visible.")
        self._dispatch_click(result["x"], result["y"])

    def _dispatch_click(self, x: float, y: float) -> None:
        self._tab.call_method(
            "Input.dispatchMouseEvent",
            type="mousePressed",
            x=x,
            y=y,
            button="left",
            clickCount=1,
        )
        self._tab.call_method(
            "Input.dispatchMouseEvent",
            type="mouseReleased",
            x=x,
            y=y,
            button="left",
            clickCount=1,
        )

    def disconnect(self) -> None:
        if self._tab is not None:
            tab = self._tab
            tab.status = tab.status_stopped
            tab._stopped.set()
            tab._recv_th.join(timeout=1.5)
            tab._handle_event_th.join(timeout=1.5)
            if tab._ws is not None:
                tab._ws.close()
        self._tab = None
        self._browser = None


def launch_chrome_for_game(address: str, port: int) -> None:
    """Open a Chromium browser with an isolated profile and loopback-only CDP."""
    cdp_url = _cdp_url(address, port)
    resolved_port = urlsplit(cdp_url).port
    root = Path(__file__).resolve().parents[2]
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
    candidates = [
        shutil.which("chrome"),
        shutil.which("msedge"),
        shutil.which("opera"),
        shutil.which("opera-gx"),
        os.path.join(
            program_files,
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            program_files_x86,
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            local_app_data,
            "Google",
            "Chrome",
            "Application",
            "chrome.exe",
        ),
        os.path.join(
            program_files_x86,
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        ),
        os.path.join(
            program_files,
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        ),
        os.path.join(
            local_app_data,
            "Microsoft",
            "Edge",
            "Application",
            "msedge.exe",
        ),
        os.path.join(program_files, "Opera GX", "opera.exe"),
        os.path.join(program_files_x86, "Opera GX", "opera.exe"),
        os.path.join(local_app_data, "Programs", "Opera GX", "opera.exe"),
        os.path.join(local_app_data, "Programs", "Opera", "opera.exe"),
    ]
    browser_path = next(
        (Path(path) for path in candidates if path and Path(path).is_file()),
        None,
    )
    if browser_path is None:
        raise FileNotFoundError(
            "Nie znaleziono Chrome, Edge ani Opery GX. "
            "Sprawdź, czy przeglądarka jest zainstalowana."
        )

    profile_dir = root / "data" / "travian-browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    subprocess.Popen(
        [
            str(browser_path),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={resolved_port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        close_fds=True,
    )
