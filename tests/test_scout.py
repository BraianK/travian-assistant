"""Scouting an attacker's possible departures (Zwiad tab)."""

import os
import unittest

from src.config.settings import ScoutSettings
from src.core.scout_automation import ScoutAutomation
from src.core.scout_planner import (
    VERDICT_ABSENT,
    VERDICT_HOME,
    VERDICT_LEFT,
    VERDICT_UNKNOWN,
    ScoutTarget,
    attack_travel,
    distance,
    judge,
    level_range,
    plan_candidates,
)


class PlannerTests(unittest.TestCase):
    def test_matches_the_users_example(self):
        # KunS (-34|29) sieges Jessica (30|35) with rams (8 fields/h).
        fields = distance(-34, 29, 30, 35)
        self.assertEqual(57852, round(attack_travel(fields, 8.0, 0, True)))
        self.assertEqual(37926, round(attack_travel(fields, 8.0, 10, True)))
        # Every departure of the example (arrival 06:59:59) to the second.
        from datetime import datetime, timedelta
        arrival = datetime(2026, 11, 10, 6, 59, 59)
        expected = {0: "14:55:47", 5: "18:37:11", 6: "19:04:52", 7: "19:29:17",
                    8: "19:50:59", 9: "20:10:24", 10: "20:27:53", 11: "20:43:42",
                    12: "20:58:05", 13: "21:11:12", 14: "21:23:14"}
        for level, departure in expected.items():
            travel = attack_travel(fields, 8.0, level, True)
            self.assertEqual(departure, (arrival - timedelta(seconds=travel)).strftime("%H:%M:%S"))

    def test_candidates_and_scout_times(self):
        arrival = 1_000_000
        target = ScoutTarget("Jessica", 30, 35, arrival)
        candidates = plan_candidates((-34, 29), [target], 8.0, [10, 11], True, 600, 0)
        self.assertEqual([10, 11], [c.level for c in candidates])  # sorted by departure
        ten = [c for c in candidates if c.level == 10][0]
        self.assertEqual(arrival - 37926, ten.departure)
        self.assertEqual([ten.departure - 1 - 600, ten.departure + 1 - 600], ten.sends)
        # Departures whose scouts can no longer be sent are skipped.
        self.assertEqual([], plan_candidates((-34, 29), [target], 8.0, [10], True, 600, arrival))

    def test_judge(self):
        self.assertIsNone(judge({-3: 100, 0: 110}))
        self.assertEqual(VERDICT_LEFT, judge({-3: 100, 0: 110, 2: 110}))
        self.assertEqual(VERDICT_HOME, judge({-3: 100, 0: 110, 2: 125}))
        self.assertEqual(VERDICT_ABSENT, judge({-3: 100, 0: 100, 2: 100}))
        self.assertEqual(VERDICT_UNKNOWN, judge({-3: 100, 0: 100, 2: 104}))

    def test_level_range(self):
        self.assertEqual([9, 10, 11], level_range(10, 1))
        self.assertEqual([0, 1], level_range(0, 1))
        self.assertEqual(list(range(21)), level_range(None, 1))


class FakeTimer:
    def __init__(self):
        self.jobs = []

    def schedule(self, at_local, callback):
        self.jobs.append((at_local, callback))

    def run_all(self):
        for _at, callback in sorted(self.jobs, key=lambda job: job[0]):
            callback()
        self.jobs.clear()


class FakeBridge:
    def __init__(self, xp_values):
        self.xp_values = list(xp_values)
        self.sent = []

    def read_context(self):
        return {"server_offset_ms": 0, "tribe": 3,
                "villages": {"101": {"name": "Aden", "units": {"3": 20}}}}

    def check_target(self, source, target, movement):
        return {"durations": {"3": 600}}

    def send_request(self, village, target, movement, units, catapults=(), spy_mission="resources"):
        return {"controller": "troops/send", "params": {
            "villageId": village, "destVillageId": target, "movementType": movement,
            "units": units, "spyMission": spy_mission}}

    def fire(self, requests, latest):
        self.sent.append(requests[0]["params"])
        return "b" * 32, {"status": "fired"}

    def _evaluate(self, expression, **kwargs):
        if "supply" in expression:
            return 2
        return {"xp": self.xp_values.pop(0), "level": 30}


class AutomationTests(unittest.TestCase):
    def test_finds_the_departure_when_hero_left(self):
        now = [900_000.0]
        settings = ScoutSettings(enabled=True)
        # Two candidates (PT 10 then PT 11); XP for each: -3, 0, +2.
        bridge = FakeBridge([100, 110, 125, 125, 135, 135])
        timer = FakeTimer()
        logs = []
        found = []
        automation = ScoutAutomation(bridge, lambda: settings, logs.append,
                                     clock=lambda: now[0], timer=timer, on_found=found.append)
        operation = automation.create_operation(
            attack_key="1:9", attacker_id="777", attacker_name="KunS",
            source_village_id="537001000", source=(-34, 29), unit_label="Taran",
            unit_speed=8.0, siege=True, levels=[10, 11],
            targets=[ScoutTarget("Jessica", 30, 35, 1_000_000)],
            scout_village_id="101", scout_unit=3, scouts_per_send=2,
        )
        self.assertEqual(2, len(operation.candidates))
        now[0] = operation.candidates[-1].departure - 10
        automation.tick()
        timer.run_all()
        self.assertEqual(4, len(bridge.sent))
        self.assertTrue(all(p["spyMission"] == "defence" and p["movementType"] == 6
                            and p["units"] == {3: 2} for p in bridge.sent))
        verdicts = [c.verdict for c in operation.candidates]
        self.assertEqual([VERDICT_HOME, VERDICT_LEFT], verdicts)
        self.assertEqual("Jessica, PT 11", operation.found)
        self.assertEqual(4, operation.pair_xp)  # 2 pathfinders x 2 crop
        self.assertEqual(1, len(found))
        self.assertEqual(("Jessica", 11, True), (found[0]["target"], found[0]["level"], found[0]["siege"]))
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from src.app import ScoutWidget
        QApplication.instance() or QApplication([])
        widget = ScoutWidget()
        widget.add_found(found[0])
        self.assertIn("OBLĘŻENIE Z BOHATEREM: KunS → Jessica (PT 11", widget.found_banner.text())
        self.assertTrue(any("BOHATER WYSZEDŁ" in line for line in logs))

    def test_planning_again_replaces_instead_of_duplicating(self):
        settings = ScoutSettings(enabled=False)
        bridge = FakeBridge([])
        logs = []
        automation = ScoutAutomation(bridge, lambda: settings, logs.append,
                                     clock=lambda: 900_000.0, timer=FakeTimer())

        def plan(levels):
            return automation.create_operation(
                attack_key="manual:ivi01", attacker_id="777", attacker_name="KunS",
                source_village_id="537001000", source=(-34, 29), unit_label="Taran",
                unit_speed=8.0, siege=True, levels=levels,
                targets=[ScoutTarget("Jessica", 30, 35, 1_000_000)],
                scout_village_id="101", scout_unit=3, scouts_per_send=2,
            )

        plan([10, 11])
        second = plan([9, 10, 11])
        self.assertEqual([second], automation.operations)
        self.assertEqual(3, len(automation.rows()))
        self.assertTrue(any("zastąpiony" in line for line in logs))

        # Once scouts left, the plan is not silently replaced.
        second.candidates[0].send_status[0] = "wysłany"
        with self.assertRaises(RuntimeError):
            plan([10])
        self.assertEqual([second], automation.operations)

    def test_disabled_does_not_send(self):
        settings = ScoutSettings(enabled=False)
        bridge = FakeBridge([])
        timer = FakeTimer()
        automation = ScoutAutomation(bridge, lambda: settings, lambda _t: None,
                                     clock=lambda: 900_000.0, timer=timer)
        automation.create_operation(
            attack_key="1:9", attacker_id="777", attacker_name="KunS",
            source_village_id="537001000", source=(-34, 29), unit_label="Taran",
            unit_speed=8.0, siege=True, levels=[10],
            targets=[ScoutTarget("Jessica", 30, 35, 1_000_000)],
            scout_village_id="101", scout_unit=3, scouts_per_send=2,
        )
        automation.tick()
        self.assertEqual([], timer.jobs)


class WidgetTests(unittest.TestCase):
    def test_plan_request_from_selected_attack_and_targets(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from src.app import ScoutWidget

        QApplication.instance() or QApplication([])
        widget = ScoutWidget()
        widget.load_settings(ScoutSettings(targets=[{"name": "RejeZ stolica", "x": 5, "y": 6}]))
        widget.set_context(
            [{"id": "101", "name": "Aden", "x": -1, "y": 34, "scouts": 20}],
            {2: {1: 14.0, 7: 8.0, 8: 6.0}},
        )
        widget.update_attacks([{
            "key": "1:9", "attacker_name": "KunS", "attacker_player_id": "777",
            "source_village": "00 KunS", "source_village_id": "537001000",
            "source_xy": (-34, 29), "target_xy": (30, 35), "village_name": "Jessica",
            "arrival_time": 1_000_000.0, "movement_type": "Oblężenie", "tribe_id": 2,
            "tournament_square": "10 (Taran)", "square_levels": {"10": [7]}, "hero_xp": 5000,
        }])
        widget.attacks_table.selectRow(0)
        self.assertEqual(7, widget.unit_combo.currentData()[0])
        self.assertEqual((9, 11), (widget.pt_from.value(), widget.pt_to.value()))
        self.assertTrue(widget.siege_checkbox.isChecked())
        widget._add_attacked_village()
        requests = []
        widget.plan_requested.connect(requests.append)
        widget._request_plan()
        request = requests[0]
        self.assertEqual([9, 10, 11], request["levels"])
        self.assertEqual("101", request["scout_village_id"])
        self.assertEqual(
            [("RejeZ stolica", 5, 6, 1_000_000.0), ("Jessica", 30, 35, 1_000_000.0)],
            request["targets"],
        )


class ManualAttackerTests(unittest.TestCase):
    def test_manual_attacker_with_several_targets(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from datetime import datetime
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication

        from src.app import ScoutWidget

        QApplication.instance() or QApplication([])
        widget = ScoutWidget()
        widget.load_settings(ScoutSettings(targets=[
            {"name": "Jessica", "x": 30, "y": 35}, {"name": "RejeZ stolica", "x": 5, "y": 6},
        ]))
        widget.set_context([{"id": "101", "name": "Aden", "x": -1, "y": 34, "scouts": 20}],
                           {2: {1: 14.0, 7: 8.0, 8: 6.0}})
        widget.update_attacks([])
        widget.add_manual_attacker({"village_id": "537837534", "village_name": "00 TheKunS",
                                    "player_id": "131", "player_name": "TheKunS",
                                    "tribe_id": 2}, -34, 29)
        attack = widget.selected_attack()
        self.assertEqual("TheKunS", attack["attacker_name"])
        self.assertEqual(7, widget.unit_combo.currentData()[0])      # ram
        self.assertTrue(widget.siege_checkbox.isChecked())
        requests = []
        widget.plan_requested.connect(requests.append)
        with patch("src.app.QMessageBox.warning") as warning:
            widget._request_plan()                                   # no arrival times yet
        self.assertEqual([], requests)
        self.assertIn("podaj czas dotarcia", warning.call_args[0][2])
        from PySide6.QtWidgets import QTableWidgetItem
        widget.targets_table.setItem(0, 3, QTableWidgetItem("10.11.2026 06:59:59"))
        widget.targets_table.setItem(1, 3, QTableWidgetItem("10.11.2026 07:00:00"))
        widget._request_plan()
        request = requests[0]
        self.assertEqual("131", request["attack"]["attacker_player_id"])
        self.assertEqual("537837534", request["attack"]["source_village_id"])
        self.assertEqual(
            [datetime(2026, 11, 10, 6, 59, 59).timestamp(), datetime(2026, 11, 10, 7).timestamp()],
            [target[3] for target in request["targets"]],
        )


class PlanManyTests(unittest.TestCase):
    def test_plan_for_all_uses_each_attackers_saved_settings(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from src.app import ScoutWidget

        QApplication.instance() or QApplication([])
        widget = ScoutWidget()
        base = {"village_name": "v", "player_id": "131", "village_id": "537837534", "tribe_id": 2}
        widget.load_settings(ScoutSettings(
            targets=[{"name": "001: Jessica", "x": 30, "y": 35}],
            attackers=[
                {**base, "id": "a1", "name": "TheKunS", "x": -34, "y": 29, "unit": 7,
                 "siege": True, "pt_from": 9, "pt_to": 11, "arrival": "11.11.2026 06:59:59"},
                {**base, "id": "a2", "name": "Cozo", "x": -47, "y": 8, "unit": 8,
                 "siege": False, "pt_from": 2, "pt_to": 4, "arrival": "11.11.2026 07:00:00"},
                {**base, "id": "a3", "name": "Bez czasu", "x": 1, "y": 1},
            ],
        ))
        widget.set_context([{"id": "101", "name": "Dion", "x": -2, "y": 33, "scouts": 276}],
                           {2: {1: 14.0, 7: 8.0, 8: 6.0}})
        requests = []
        widget.plan_requested.connect(requests.append)
        from unittest.mock import patch
        with patch("src.app.QMessageBox.warning") as warning:
            widget._request_plan_many(selected_only=False)
        self.assertEqual(["TheKunS", "Cozo"], [r["attack"]["attacker_name"] for r in requests])
        self.assertEqual((7, True, [9, 10, 11]), (requests[0]["unit"], requests[0]["siege"], requests[0]["levels"]))
        self.assertEqual((8, False, [2, 3, 4]), (requests[1]["unit"], requests[1]["siege"], requests[1]["levels"]))
        self.assertEqual("101", requests[0]["scout_village_id"])
        self.assertIn("Bez czasu", warning.call_args[0][2])     # no arrival time -> skipped

        requests.clear()
        widget.attacks_table.selectRow(1)
        widget._request_plan_many(selected_only=True)
        self.assertEqual(["Cozo"], [r["attack"]["attacker_name"] for r in requests])


if __name__ == "__main__":
    unittest.main()
