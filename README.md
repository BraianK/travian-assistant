# Travian Kingdoms Assistant

A desktop helper for tracking village state, resources, and game activity in Travian Kingdoms.

Autor: **Dzogurt**

## Project status

This project is currently in a functional foundation / MVP stage:

- desktop UI shell is implemented,
- browser integration reads game state and supports explicitly enabled recruitment automation,
- database models and repositories are in place,
- core planning analytics modules are partially implemented.

## Gotowa aplikacja Windows

Użytkownik nie musi instalować Pythona ani konfigurować bazy danych. Pobierz
`TravianAssistant-Windows-x64.zip` z [GitHub Releases](https://github.com/BraianK/travian-assistant/releases),
rozpakuj cały folder i uruchom `TravianAssistant.exe`. Następnie kliknij
**Uruchom przeglądarkę**, zaloguj się ręcznie do Traviana i wybierz **Połącz**.
Wymagana jest zainstalowana przeglądarka Chrome, Edge, Opera lub Opera GX.

SQLite, ustawienia, logi, kopie zapasowe i dedykowany profil przeglądarki
tworzą się automatycznie w `%LOCALAPPDATA%\Travian Kingdoms Assistant`.
Nie usuwaj ani nie udostępniaj tego folderu — profil zawiera prywatną sesję
gry. Paczka wymaga rozpakowania; nie przenoś samego pliku `.exe` poza folder.

Wydanie powstaje automatycznie z taga `v*` przez workflow Windows release.
Ręczne uruchomienie workflow buduje tę samą paczkę jako artefakt do pobrania.

## Instalacja ze źródeł (Windows)

Wymagany jest **Python 3.12 lub nowszy**. Zainstaluj go z [python.org](https://www.python.org/downloads/windows/) i zaznacz opcję dodania Pythona do `PATH`.

1. Sklonuj repozytorium i przejdź do katalogu projektu:

   ```powershell
   git clone https://github.com/BraianK/travian-assistant.git
   cd travian-assistant
   ```

2. Utwórz środowisko wirtualne i zainstaluj aplikację wraz z zależnościami:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -e .
   ```

   Alternatywnie można użyć `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.

3. Uruchom program z katalogu repozytorium:

   ```powershell
   .\.venv\Scripts\python.exe -m src.main
   ```

   Aplikację można też uruchomić poleceniem `travian-assistant` po aktywowaniu środowiska `.venv`.

4. W aplikacji otwórz przeglądarkę przyciskiem **Uruchom przeglądarkę**, zaloguj się ręcznie do własnego konta Travian i wybierz **Połącz**.

Nie trzeba tworzyć pliku `.env` ani podawać hasła do gry aplikacji. Baza danych, ustawienia i profil przeglądarki powstają lokalnie. Automaty rekrutacji i mielenia są domyślnie wyłączone — włączaj je osobno dopiero po sprawdzeniu ustawień.

### Aktualizacja

W katalogu repozytorium pobierz zmiany i zaktualizuj aplikację:

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -e .
```

### Uruchomienie testów

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Bezpieczeństwo przy publikowaniu zmian

Nie dodawaj do repozytorium plików `.env`, `config/settings.json`, katalogu `data/`, profilu przeglądarki, bazy SQLite, kopii zapasowych ani plików `*.devtools`. Mogą zawierać dane logowania, aktywną sesję, dane konta lub przechwycone żądania gry. Reguły w `.gitignore` pomijają te lokalne pliki. Przed każdym wypchnięciem sprawdź listę plików dodawanych do commita i upewnij się, że nie ma w niej lokalnych danych ani sekretów.

Automatyzacja wykonuje działania w grze po ręcznym włączeniu. Używaj jej zgodnie z zasadami gry i na własną odpowiedzialność.

### Pierwsze wypchnięcie kodu

Zainstaluj Git for Windows, otwórz PowerShell w katalogu projektu i najpierw sprawdź ignorowane dane oraz listę plików:

```powershell
git check-ignore -v .env config/settings.json data/travian.db koszary.devtools
git status --short
```

Jeśli lista zawiera wyłącznie kod i pliki dokumentacji, zainicjalizuj lokalne repozytorium i wypchnij je do pustego repozytorium GitHub:

```powershell
git init -b main
git add .
git diff --cached --stat
git status --short
git commit -m "Prepare Travian assistant for sharing"
git remote add origin https://github.com/BraianK/travian-assistant.git
git push -u origin main
```

Sprawdź `git status` przed commitem i upewnij się, że baza, profil przeglądarki, ustawienia lokalne oraz pliki przechwyconych żądań nie są dodane. Repozytorium nie zawiera obecnie pliku licencji; przed publicznym udostępnieniem kodu wybierz licencję odpowiednią do planowanego użycia.

## Notes

- The project uses SQLite for local state storage.
- On startup, the dashboard restores all saved villages, their latest resource values, and the most recently captured building, resource-field, troop, and map details. Resource history is shown for the selected village.
- Connect to the manually signed-in browser to refresh those snapshots; saved details remain available offline and represent the last successful read.
- The village dashboard includes a per-village plan: it estimates when the next resource-field upgrade is affordable and how many Phalanx and Theutates Thunder can be paid for from current stocks. While connected to the game, it reads recruitment queues for every troop type available to each village's tribe every three minutes without changing the selected village.
- Recruitment monitoring and automatic replenishment are available in a dedicated top-level "Rekrutacja" tab, separate from the village overview and village details.
- Automatic recruitment is off by default. The recruitment panel detects the troop types available to each village and keeps its unit selection and target queue duration separately for every village and unit; newly detected villages start with no units selected. Each village card has its own rule: "Stała partia" adds a fixed batch when a queue reaches the village's count threshold, while "Docelowy czas kolejki" uses a target duration in minutes per unit and fills only the estimated shortfall, up to the village's maximum batch size. "Kopiuj do innych wiosek" copies a village's mode, threshold and batch size to all other villages. Villages without their own rule inherit the previous account-wide values. Queue duration estimates use the game's training time, speed, and building-level modifiers, including parallel training buildings. If that timing data is unavailable, duration-based recruitment skips the affected unit and reports the missing estimate. Each village has its own automation switch; units requiring an unavailable building are disabled. The app checks resources, submits orders sequentially, and checks the game's asynchronous response before reporting success. An order is not retried until the queue rises above its active count/time threshold; uncertain orders remain visibly pending to avoid duplicates. The pending-order reset turns automation off and requires confirmation.
- The recruitment tab also shows each village's recorded hourly production, estimated hourly costs for selected units under continuous training, and net hourly balance by resource. The table uses one row per village, with a column for each resource; each cell shows production, recruitment cost, and net balance per hour. Hourly cost is unit cost multiplied by units trained per hour, calculated from the game's `troop.time`, `TG_TROOP_SPEED`, and the current training-time modifier of each available barracks/stable. Parallel standard and large training buildings are included independently. If the game does not provide complete cost or duration data, or multiple selected unit types share a training building queue, costs and net balances are shown as unavailable rather than treated as zero. Production figures use the latest saved reading for each village.
- The "Ataki" tab listens for live changes to the game's movement collections in all owned villages while connected and saves detected attacks to the local SQLite database. It keeps a history after movements disappear, marks attacks removed before their expected arrival as withdrawn and those removed at/after arrival as arrived, and supports deleting an individual record. Double-click a row to inspect the stored details and status events. Village names are resolved from loaded village models, map responses, and targeted `cache.get` responses for village models (`villageId` to name); when that data arrives later, unresolved source names are backfilled in both the record and history. The live countdown updates locally and a desktop warning is shown once per newly detected movement. Masked troop compositions are identified as hidden. Monitoring is read-only and never sends defensive orders. Several records can be selected (Ctrl/Shift) and deleted with one confirmation. The table shows the source village coordinates and estimates the attacker's tournament square from the travel time (arrival minus the game's start time or the live detection time; +10% speed per level beyond 20 fields): with the composition hidden it lists the levels matching each unit type, e.g. "10 (Taran)"; within 20 fields it names the slowest unit instead, which tells rams from catapults. Robber hideouts have no tournament square ("0 (kryjówka)"). Columns can be widened by dragging the header.
- The "Wakacje" tab runs a daily rolling vacation-mode safeguard (off by default). Vacation mode starts 24 hours after it is planned and can be cancelled until then with the vacation day refunded (`player/changeVacationState`). The app plans vacation every day at the configured local time; a configurable number of minutes before activation it cancels the plan unless at least the configured number of incoming hostile movements of the selected types (attack, siege, raid, scout) is detected, in which case vacation is left to activate. If attacks cannot be read, the plan is kept; cancellation is never sent within 60 seconds of activation. Every change is rechecked in the page before sending and confirmed from the returned player state. The app must be running and connected at decision time, otherwise vacation activates on its own.
- The "Plan ataków" tab executes TK-Tools attack plans (off by default, enabling asks for confirmation). Sign in to https://tk-tools.fr in the same browser as the game: the app reads the planner through that tab (`/api/off-operations/planner`), keeps only the needed fields (the response also carries API keys and login tokens, which are never stored or logged) and reports each attack back with `confirm-sending` / `deny-sending`. The plan is refreshed until the first send of an operation. Fakes use the plan's unit count (otherwise 32/36/44 + 1 depending on the configured Tier 2/Tier 3 dates), the cheapest filler units (never scouts) and the siege unit matching the plan speed (3 = catapult, 4 = ram). Real attacks take all offensive units of the tribe plus the planned catapults, minus the reserve that later sends from the same village still need. Catapults aim at the plan's targets or at random buildings (two targets with rally point 20 and at least 20 catapults); sieges fall back to attacks when they lack 1000 units and a ram. The departure is recomputed from the game's own travel durations (`troops/checkTarget`) so the planned arrival is kept; requests are aimed at the middle of the departure second using the game's server clock offset, and a send that would land more than one second late is skipped and reported as `TIME_LAG`. Two execution modes are available: direct game requests (`troops/send`, all waves in parallel) or click simulation, where each wave gets its own background game tab with the send window prepared in advance and "Send" clicked in the target second (DevTools input events, never the system cursor). Optional hero evasion parks the hero as reinforcement in the nearest own village and recalls it (`troops/moveTroopsHome`) to be home before the attack it joins; if it is late the wave is resent without the hero. A lower tournament square than planned is reported or, if chosen, upgraded in advance.
- The "Obrona przed kryjówkami" tab counter-strikes robber hideouts (off by default, enabling asks for confirmation). A robber attack (owner id -1, hideout village type 5) is detected in any owned village and the capital immediately sends the configured share of each unit type, optionally with the hero (or waits for the hero only if that option is set). With siege enabled it goes as a siege (rams, at least 1000 units) when it still lands before the robbers hit; otherwise as an attack. A raid follows with the configured delay or as soon as possible, never landing after the robbers hit, and by default sized to the hideout's loot from the map details (percentages act as limits). Raids repeat after the troops land/return while loot remains and the hideout garrison is empty; a vanished robber attack lifts the time limit and a hideout removed from the map ends the looting. Hideouts being looted are remembered across restarts. Hideouts are shown as "Kryjówka I"/"Kryjówka II".
- The "Królestwo" tab lists every member of the player's kingdom from the game's `Kingdom` model (role, population, villages, victory points), sortable and searchable, refreshed every 10 minutes. "TK - Aktualność" shows each member's last troop update from the TK-Tools team overview (`/api/teams/{id}/overview`, team id in settings) and "GT - Aktualność" the "L.troop update" from the GetterTools group member list (`<group>?detailList`); both use the TK-Tools colours (green / amber / red / grey) and show "brak danych" when the player is not in the team/group or the account has no access. "Aktualizuj TK" and "Aktualizuj GT" (with confirmation) declare the player's own current troops from the game — home, reinforcing and moving, per home village — and tournament square levels: in TK-Tools via `PUT /players/{id}/troops` (only the signed-in player's own account; smithy levels and orientations kept) and in GetterTools via the "My troops" table save (`saveCityT2`, units 1–9, village type kept). Both run inside the user's logged-in TK-Tools / GetterTools tabs of the same browser.
- The attacks tab backfills the attacker's tournament square for older records ("≈"), using the first live detection time; records detected right after an app start are skipped because the attack was already on its way.
- The "Zwiad" tab looks for an attacker's real target. When an attack appears, the attacker's hero experience is read from the hero ranking (`ranking/getRanking`, subtype `heroes`) and saved. For a selected attack, its slowest unit and square levels (prefilled from the estimate ± a configurable spread), siege flag and a list of possible targets (name, position, arrival — empty means the attack's own arrival) give every possible departure second of his army (the game truncates the doubled siege time to whole seconds). With the scout automation enabled (confirmation) two scouts land in his village 1 s before and two 1 s after each departure (spy mission "defence", sent to the second) and the hero experience is read 3 s before, at and 2 s after it: growth after the first pair but not after the second means the hero left with the army — that target and square level are the real ones. Withdrawn attacks are logged; the method relies on the hero ranking updating right after a fight. Attackers can also be added by hand from their village coordinates (player and tribe are read from the game); manual attackers with their unit, siege flag, square-level range and arrival time, and the possible targets, are saved. Plans can be made for the selected attacker, for all selected ones or for all; planning an attacker again replaces its not-yet-started plan. A found real target shows a non-blocking notification (with sound and taskbar flash) and a banner in the tab. Departure times match the game to the second (verified against an operation spreadsheet: the game truncates the — for sieges doubled — travel time to whole seconds).
- Recruitment queues: the training building of each unit comes from its requirements in the game config (e.g. the Gaul pathfinder trains in the stable).
- Attack history keeps the first detection facts across reconnects; batches of different players' attacks first seen in the same second (an app connection) are not used for estimates, and connections are logged for that.
- The app keeps the game tab awake: Edge and Chrome freeze background tabs, which stops queued game requests (this also protected the vacation safeguard). Because this PC's clock may differ from the server by tens of seconds, all timing uses the game server clock.
- Grain milling is available in its own "Mielenie" tab and both methods are off per village by default. The first method empties the granary when grain reaches its configured threshold (default 100%), distributing the full stock among clay/iron/wood (default 40/30/30; the shares must total 100%). The second method refills grain when it falls to its configured low threshold (default 5%), converting available stock to grain up to granary capacity; any surplus stays distributed among the other resources using the same shares. Each exchange uses the captured `premiumFeature/bookFeature` request at a cost of 5 gold; the app requires the current account balance to be at least the global reserve plus 5 (default reserve: 300) before sending. Milling is checked every 30 seconds, rechecks the threshold and gold immediately before sending, and verifies each asynchronous response before it sends another village's order. Uncertain or unconfirmed orders remain blocked to prevent duplicate gold charges.
- While connected, the app also captures eligible JSON responses from the game's network requests, parses and normalizes supported response categories, and stores them with the relevant village for inspection.
- SQLite is backed up automatically on startup when no recent backup exists and then at the configured interval. Backups are integrity-checked and stored next to the database in a `backups` folder; restore through `DatabaseManager.restore_backup()` first verifies the selected backup and safeguards the current database.
- The browser connection requires a manually logged-in Travian session. Game actions are limited to recruitment orders and NPC Trader exchanges when the user explicitly enables their separate automations.
- The app is intended as a local assistant, not as a full automated bot.
