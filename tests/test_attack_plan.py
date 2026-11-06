"""Tests for the TK-Tools attack plan automation."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.app import MainWindow
from src.config.settings import AppSettings, AttackSettings
from src.core.account_manager import AccountManager
from src.core.attack_automation import (
    STATUS_FIRED,
    STATUS_PREPARED,
    STATUS_SENT,
    STATUS_SKIPPED,
    AttackAutomation,
)
from src.core.attack_bridge import GameAttackBridge
from src.core.attack_operations import (
    CATAPULT,
    HERO,
    MOVEMENT_ATTACK,
    MOVEMENT_SIEGE,
    RAM,
    RANDOM_TARGET,
    FakeRule,
    PlanError,
    VillageArmy,
    can_park_hero,
    catapult_targets,
    compose_attack,
    exact_departure,
    hero_recall_time,
    later_sends,
    nearest_village,
    parse_planner,
    parse_wave,
)
from src.core.tktools_client import TkToolsSession
from src.core.village_tracker import VillageTracker
from src.database.connection import DatabaseManager
from src.database.repository import AccountRepository, VillageRepository

ARRIVAL = "2026-11-07T05:01:00.000Z"
ARRIVAL_TS = datetime(2026, 11, 7, 5, 1, tzinfo=timezone.utc).timestamp()
RULE = FakeRule(
    tier2_at=datetime(2026, 11, 10, 14, 0),
    tier3_at=datetime(2026, 11, 23, 14, 0),
)


def _row(attack_id, departure, waves, **extra):
    row = {
        "id": attack_id,
        "operationId": "op-1",
        "operationName": "Dzogurt test",
        "sourceVillageId": "538001407",
        "sourceVillageName": "00.Aden",
        "sourcePlayerId": "199",
        "targetVillageId": "538427428",
        "targetName": "01 OLIWKA",
        "targetPlayer": "Oliwkaaa",
        "targetX": 36,
        "targetY": 47,
        "departureTime": departure,
        "arrivalTime": ARRIVAL,
        "travelTimeSeconds": 23530,
        "moveSpeed": 3,
        "ptLevel": 0,
        "isSiege": False,
        "state": "NONE",
        "isSitted": False,
        "strictArrival": True,
        "waves": waves,
    }
    row.update(extra)
    return row


def _fake(index, units=33):
    return {"waveIndex": index, "attackType": "FAKE", "fakeNbUnits": units}


def _attack_wave(index, off=False, hero=False, catapults=0, t1=None, t2=None):
    return {
        "waveIndex": index, "attackType": "ATTACK", "attackHasOff": off,
        "attackHasHero": hero, "attackNbCatapults": catapults,
        "attackTarget1": t1, "attackTarget2": t2,
    }


def _plan_rows():
    """The real 'Dzogurt test' operation from TK-Tools (anonymised ids)."""
    return [
        _row("a1", "2026-11-06T20:35:06.000Z",
             [_attack_wave(0, off=True, hero=True, catapults=120)],
             moveSpeed=4, ptLevel=4, isSiege=True),
        _row("a2", "2026-11-06T22:28:50.000Z",
             [_attack_wave(i, catapults=300) for i in range(3)]),
        _row("a3", "2026-11-06T22:41:32.000Z", [_fake(i) for i in range(3)],
             targetVillageId="538427429", targetName="01. Srutututu"),
        _row("a4", "2026-11-07T02:10:43.000Z", [_fake(i) for i in range(3)],
             targetVillageId="538427430", targetName="01.UTMB"),
    ]


def _gaul_army(**units):
    defaults = {1: 400, 2: 5000, 4: 3000, 6: 800, 7: 50, 8: 1200}
    defaults.update({int(k[1:]): v for k, v in units.items()})
    return VillageArmy(
        village_id="538001407",
        tribe=3,
        units=defaults,
        hero_home=True,
        rally_point_level=20,
        unit_costs={1: 235, 2: 295, 3: 270, 4: 610, 5: 760, 6: 1120, 7: 1340, 8: 2190},
    )


class PlanParsingTests(unittest.TestCase):
    def test_parses_own_attacks_sorted_and_skips_others(self):
        rows = _plan_rows() + [
            _row("x", "2026-11-06T20:00:00.000Z", [_fake(0)], sourcePlayerId="5"),
            _row("y", "2026-11-06T20:00:00.000Z", [_fake(0)], isSitted=True),
        ]
        attacks, problems = parse_planner(rows, "199")
        self.assertEqual(["a1", "a2", "a3", "a4"], [a.id for a in attacks])
        self.assertEqual([], problems)
        self.assertTrue(attacks[0].is_siege)
        self.assertTrue(attacks[0].has_hero)
        self.assertEqual(ARRIVAL_TS, attacks[0].arrival)
        self.assertEqual(4, attacks[0].pt_level)

    def test_conquest_wave(self):
        wave = parse_wave({
            "waveIndex": 1, "attackType": "CONQUEST", "conquestHasOff": True,
            "conquestNbChiefs": 3, "conquestNbCatapults": 10,
            "conquestTarget1": "building_mainBuilding",
        })
        self.assertEqual(("CONQUEST", 3, 10), (wave.kind, wave.chiefs, wave.catapults))

    def test_fake_rule_by_date(self):
        november_9 = datetime(2026, 11, 9, 12, 0).timestamp()
        november_11 = datetime(2026, 11, 11, 12, 0).timestamp()
        november_24 = datetime(2026, 11, 24, 12, 0).timestamp()
        self.assertEqual(32, RULE.filler_units(november_9))
        self.assertEqual(36, RULE.filler_units(november_11))
        self.assertEqual(44, RULE.filler_units(november_24))


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.attacks, _ = parse_planner(_plan_rows(), "199")

    def _compose(self, attack, army):
        later = later_sends(attack, self.attacks, set())
        return compose_attack(attack, army, later, RULE)

    def test_fake_uses_cheapest_filler_and_plan_siege_unit(self):
        fakes = self.attacks[2]
        sends = self._compose(fakes, _gaul_army())
        self.assertEqual(3, len(sends))
        for send in sends:
            self.assertEqual({1: 32, CATAPULT: 1}, send.units)
            self.assertEqual(MOVEMENT_ATTACK, send.movement_type)
            self.assertEqual((RANDOM_TARGET,), send.catapult_targets)

    def test_fake_falls_back_to_next_filler_and_never_scouts(self):
        sends = self._compose(self.attacks[3], _gaul_army(u1=40, u3=999))
        self.assertEqual({1: 32, CATAPULT: 1}, sends[0].units)
        self.assertEqual({1: 8, 2: 24, CATAPULT: 1}, sends[1].units)
        self.assertNotIn(3, sends[2].units)

    def test_fake_without_siege_unit_fails(self):
        with self.assertRaises(PlanError):
            self._compose(self.attacks[3], _gaul_army(u8=0))

    def test_main_off_keeps_reserve_for_later_sends(self):
        off = self.attacks[0]
        sends = self._compose(off, _gaul_army())
        units = sends[0].units
        # 6 later fakes need 6 x 32 fillers: 192 phalanxes stay home.
        self.assertNotIn(1, units)
        self.assertEqual(5000, units[2])
        self.assertEqual(3000, units[4])
        self.assertEqual(800, units[6])
        self.assertEqual(50, units[RAM])
        # 120 catapults from the plan; 900 + 6 stay for later waves and fakes.
        self.assertEqual(120, units[CATAPULT])
        self.assertEqual(1, units[HERO])
        self.assertEqual(MOVEMENT_SIEGE, sends[0].movement_type)
        self.assertEqual((RANDOM_TARGET, RANDOM_TARGET), sends[0].catapult_targets)

    def test_off_filler_reserve_takes_swordsmen_when_phalanxes_run_out(self):
        sends = self._compose(self.attacks[0], _gaul_army(u1=100))
        self.assertEqual(5000 - 92, sends[0].units[2])

    def test_catapult_only_waves(self):
        sends = self._compose(self.attacks[1], _gaul_army())
        self.assertEqual([{CATAPULT: 300}] * 3, [send.units for send in sends])

    def test_rams_are_reserved_for_later_ram_fakes(self):
        rows = _plan_rows()
        rows[2]["moveSpeed"] = 4
        attacks, _ = parse_planner(rows, "199")
        later = later_sends(attacks[0], attacks, set())
        sends = compose_attack(attacks[0], _gaul_army(), later, RULE)
        self.assertEqual(47, sends[0].units[RAM])

    def test_siege_falls_back_to_attack_without_enough_units(self):
        army = _gaul_army(u2=0, u4=0, u6=0, u1=0, u7=1)
        sends = self._compose(self.attacks[0], army)
        self.assertEqual(MOVEMENT_ATTACK, sends[0].movement_type)
        self.assertTrue(sends[0].notes)

    def test_hero_missing_is_noted(self):
        army = _gaul_army()
        army.hero_home = False
        sends = self._compose(self.attacks[0], army)
        self.assertNotIn(HERO, sends[0].units)
        self.assertIn("Bohatera nie ma", sends[0].notes[0])

    def test_catapult_targets(self):
        wave = parse_wave(_attack_wave(0, catapults=50, t1="building_granary", t2="building_none"))
        self.assertEqual((11,), catapult_targets(wave, 20, 50))
        wave = parse_wave(_attack_wave(0, catapults=50, t1="building_granary"))
        self.assertEqual((11, RANDOM_TARGET), catapult_targets(wave, 20, 50))
        self.assertEqual((11,), catapult_targets(wave, 19, 50))
        self.assertEqual((11,), catapult_targets(wave, 20, 19))

    def test_exact_departure_uses_slowest_unit(self):
        durations = {"1": 100, "7": 17647, "8": 23530, "11": 10084}
        self.assertEqual(
            ARRIVAL_TS - 23530,
            exact_departure(ARRIVAL_TS, {1: 5, 8: 1, HERO: 1}, durations),
        )
        with self.assertRaises(PlanError):
            exact_departure(ARRIVAL_TS, {5: 1}, durations)


class HeroTests(unittest.TestCase):
    def test_nearest_village_and_timing(self):
        villages = {"a": (0, 0), "b": (5, 5), "c": (1, 1)}
        self.assertEqual("c", nearest_village((0, 0), villages, "a"))
        self.assertEqual(1000 - 1 - 60, hero_recall_time(1000, 60))
        self.assertTrue(can_park_hero(0, None, 600))
        self.assertTrue(can_park_hero(0, 2000, 600))
        self.assertFalse(can_park_hero(0, 1200, 600))


class _FakeBridge:
    def __init__(self, server_time):
        self.server_time = server_time
        self.fired = []
        self.upgrades = []
        self.context = {
            "player_id": "199",
            "tribe": 3,
            "unit_costs": {"1": 235, "2": 295, "4": 610, "8": 2190},
            "server_offset_ms": 0,
            "server_time": server_time,
            "villages": {
                "538001407": {
                    "name": "00.Aden", "x": -1, "y": 34,
                    "units": {"1": 400, "2": 5000, "4": 3000, "7": 50, "8": 1200},
                    "hero_home": True, "rally_point_level": 20,
                    "tournament_square_level": 2, "tournament_square_location": 30,
                },
                "537968639": {
                    "name": "01.Giran", "x": -1, "y": 33, "units": {},
                    "hero_home": False, "rally_point_level": 1,
                    "tournament_square_level": 0, "tournament_square_location": None,
                },
            },
            "hero": {
                "home_village_id": "538001407", "status": 0, "alive": True,
                "until": 0, "support_troop_id": None, "support_village_id": None,
            },
        }

    def keep_awake(self, force=False):
        pass

    def read_context(self):
        return json.loads(json.dumps(self.context))

    def check_target(self, source, target, movement):
        return {
            "rtt_ms": 80, "server_offset_ms": 0,
            "durations": {"1": 10084, "2": 11765, "4": 4152, "7": 17647, "8": 23530, "11": 300},
        }

    send_request = staticmethod(GameAttackBridge.send_request)
    recall_request = staticmethod(GameAttackBridge.recall_request)

    def fire(self, requests, latest):
        self.fired.append((requests, latest))
        return "b" * 32, {"status": "fired", "server_time": self.server_time}

    def read_fire(self, batch_id):
        requests = self.fired[-1][0]
        return {"results": [{"ok": True, "error": None} for _ in requests]}

    def read_movements(self, village_id):
        return [
            {"target": "538427428", "movement_type": 3, "arrival": ARRIVAL_TS, "units": {}}
            for _ in range(3)
        ]

    def upgrade_building(self, village_id, location, building_type):
        self.upgrades.append((village_id, location, building_type))
        return True


class _FakeTk:
    def __init__(self):
        self.confirmed = []
        self.denied = []

    def read_planner(self, server_name):
        return {"serverId": "s", "rows": _plan_rows()}

    def confirm_sent(self, attack_id, comment=""):
        self.confirmed.append(attack_id)

    def deny_sent(self, attack_id, reason, comment):
        self.denied.append((attack_id, reason))

    def disconnect(self):
        pass


class _ImmediateTimer:
    def __init__(self):
        self.scheduled = []

    def schedule(self, at_local, callback):
        self.scheduled.append(at_local)
        callback()


class AutomationTests(unittest.TestCase):
    def _automation(self, now, **settings):
        bridge = _FakeBridge(now)
        tk = _FakeTk()
        logs = []
        config = AttackSettings(enabled=True, server_name="tr2nx3", **settings)
        timer = _ImmediateTimer()
        automation = AttackAutomation(
            bridge, tk, lambda: config, logs.append,
            clock=lambda: bridge.server_time, timer=timer,
        )
        return automation, bridge, tk, logs, timer

    def test_refresh_previews_departure_from_arrival(self):
        automation, *_ = self._automation(ARRIVAL_TS - 40000)
        automation.refresh_plan()
        run = automation.runs["a2"]
        self.assertEqual(ARRIVAL_TS - 23530, run.preview_departure)
        self.assertTrue(any("plac turniejowy 4" in text for text in automation.pt_warnings))

    def test_prepares_fires_verifies_and_reports(self):
        departure = ARRIVAL_TS - 23530
        automation, bridge, tk, logs, timer = self._automation(departure - 30)
        automation.tick()
        run = automation.runs["a2"]
        self.assertEqual(STATUS_FIRED, run.status)
        # The off (a1) leaves in the same second; a2 is its own batch.
        requests, latest = next(item for item in bridge.fired if len(item[0]) == 3)
        self.assertEqual(3, len(requests))
        self.assertEqual({"8": 300}, requests[0]["params"]["units"])
        self.assertEqual([RANDOM_TARGET, RANDOM_TARGET], requests[0]["params"]["catapultTargets"])
        aimed = timer.scheduled[-1]
        self.assertAlmostEqual(departure + 0.5 - 0.04, aimed, delta=0.05)
        self.assertLess(latest, departure + 2)
        bridge.server_time = departure + 5
        automation.tick()
        self.assertEqual(STATUS_SENT, run.status)
        self.assertIn("a2", tk.confirmed)

    def test_skips_and_reports_when_too_late(self):
        departure = ARRIVAL_TS - 23530
        automation, bridge, tk, *_ = self._automation(departure + 5)
        automation.refresh_plan()
        automation.runs["a2"].preview_departure = departure
        automation.tick()
        self.assertEqual(STATUS_SKIPPED, automation.runs["a2"].status)
        self.assertIn(("a2", "TIME_LAG"), tk.denied)
        self.assertEqual([], [r for r in bridge.fired if len(r[0]) == 3])

    def test_off_with_hero_has_fallback_without_hero(self):
        departure = ARRIVAL_TS - 23530
        automation, bridge, *_ = self._automation(departure - 30)
        automation.refresh_plan()
        run = automation.runs["a1"]
        self.assertEqual(departure, run.preview_departure)
        automation.tick()
        requests = bridge.fired[0][0]
        self.assertEqual(1, requests[0]["params"]["units"]["11"])
        self.assertNotIn("11", requests[0]["fallback"]["units"])

    def test_pt_upgrade_policy(self):
        automation, bridge, *_ = self._automation(ARRIVAL_TS - 40000, pt_policy="upgrade")
        automation.refresh_plan()
        self.assertEqual([("538001407", 30, 14)], bridge.upgrades)

    def test_hero_is_parked_when_no_hero_attack_is_close(self):
        automation, bridge, *_ = self._automation(ARRIVAL_TS - 40000, hero_evasion=True)
        automation.refresh_plan()
        automation._manage_hero(automation.server_now())
        requests = bridge.fired[-1][0]
        self.assertEqual(5, requests[0]["params"]["movementType"])
        self.assertEqual({"11": 1}, requests[0]["params"]["units"])
        self.assertEqual(537968639, requests[0]["params"]["destVillageId"])

    def test_hero_is_recalled_before_hero_attack(self):
        departure = ARRIVAL_TS - 23530
        automation, bridge, *_ = self._automation(departure - 320, hero_evasion=True)
        bridge.context["hero"].update(support_troop_id=77, support_village_id="537968639")
        bridge.context["villages"]["538001407"]["hero_home"] = False
        automation.refresh_plan()
        automation._manage_hero(automation.server_now())
        requests = bridge.fired[-1][0]
        self.assertEqual("troops/moveTroopsHome", requests[0]["controller"])
        self.assertEqual(77, requests[0]["params"]["troopId"])
        self.assertTrue(automation.runs["a1"].hero_recall_scheduled)

    def test_frozen_operation_keeps_its_plan(self):
        departure = ARRIVAL_TS - 23530
        automation, *_ = self._automation(departure - 30)
        automation.tick()
        self.assertIn(automation.runs["a2"].status, (STATUS_FIRED, STATUS_PREPARED))
        automation.tktools.read_planner = lambda name: {"serverId": "s", "rows": []}
        automation.refresh_plan()
        self.assertIn("a2", automation.runs)


class TkToolsClientTests(unittest.TestCase):
    def test_reads_sanitized_planner_and_reports(self):
        class FakeTab:
            def __init__(self):
                self.expressions = []

            def call_method(self, method, **kwargs):
                self.expressions.append(kwargs["expression"])
                if "/off-operations/planner" in kwargs["expression"]:
                    return {"result": {"value": json.dumps({"serverId": "s", "rows": []})}}
                return {"result": {"value": "ok"}}

        session = TkToolsSession("http://127.0.0.1:9222")
        session._tab = FakeTab()
        self.assertEqual([], session.read_planner("tr2nx3")["rows"])
        expression = session._tab.expressions[0]
        self.assertIn('"tr2nx3"', expression)
        self.assertNotIn("privateApiKey", expression)
        session.confirm_sent("22bb6ef3-7068-4bc8-811c-4cd7d36b5b43", "ok")
        self.assertIn("confirm-sending", session._tab.expressions[-1])
        with self.assertRaises(ValueError):
            session.deny_sent("22bb6ef3-7068-4bc8-811c-4cd7d36b5b43", "BAD", "")
        with self.assertRaises(ValueError):
            session.read_planner("bad name!")

    def test_login_error_is_reported(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                return {"exceptionDetails": {"exception": {"description": "Error: TK_LOGIN_REQUIRED"}}}

            def stop(self):
                pass

        session = TkToolsSession("http://127.0.0.1:9222")
        session._tab = FakeTab()
        with self.assertRaisesRegex(Exception, "Zaloguj"):
            session.read_planner("tr2nx3")


class BridgeTests(unittest.TestCase):
    def test_send_request_shape(self):
        request = GameAttackBridge.send_request("1", "2", 47, {1: 5, 7: 1, 11: 0}, (99, 11))
        self.assertEqual("troops/send", request["controller"])
        self.assertEqual({"1": 5, "7": 1}, request["params"]["units"])
        self.assertEqual([99, 11], request["params"]["catapultTargets"])
        self.assertEqual(47, request["params"]["movementType"])

    def test_fire_rejects_unknown_controllers(self):
        class Session:
            _tab = object()

        bridge = GameAttackBridge(Session())
        with self.assertRaises(ValueError):
            bridge.fire([{"controller": "player/changeVacationState", "params": {}}], 0)


class AttackWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_settings_round_trip_and_enable_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "attacks.db"))
            db.init()
            window = MainWindow(
                AppSettings(),
                AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            try:
                tab = window.attack_tab
                with patch.object(AppSettings, "save", return_value=None):
                    tab.mode_buttons["clicks"].click()
                    self.assertEqual("clicks", window.settings.attacks.execution_mode)
                    tab.prepare_seconds.setValue(60)
                    self.assertEqual(60, window.settings.attacks.prepare_seconds)
                    tab.fake_tier2_at.setText("bad")
                    tab.fake_tier2_at.editingFinished.emit()
                    self.assertEqual("2026-11-10 14:00", window.settings.attacks.fake_tier2_at)
                    with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                        tab.enabled_toggle.click()
                    self.assertFalse(window.settings.attacks.enabled)
                tab.update_rows([{
                    "id": "a", "label": "00.Aden → X (FAKE)", "operation": "Op",
                    "departure": ARRIVAL_TS - 100, "arrival": ARRIVAL_TS, "waves": 3,
                    "status": "Oczekuje", "status_key": "waiting", "message": "",
                    "composition": "",
                }], ARRIVAL_TS - 200)
                self.assertIn("za 0:01:40", tab.table.item(0, 2).text())
            finally:
                window.close()
                db.close()


if __name__ == "__main__":
    unittest.main()
