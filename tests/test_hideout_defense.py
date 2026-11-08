"""Robber hideout counter-strike: planning helpers and engine flow."""

import unittest

from src.config.settings import HideoutDefenseSettings
from src.core.hideout_defense import (
    MOVEMENT_RAID,
    STATUS_COUNTER_FIRED,
    STATUS_DONE,
    STATUS_RAID_RETURNING,
    STATUS_RAID_SCHEDULED,
    HideoutDefense,
    can_siege,
    choose_counter_movement,
    fit_to_loot,
    hideout_display_name,
    plan_raid,
    share_of_units,
)

ATTACK, SIEGE = 3, 47


class HelperTests(unittest.TestCase):
    def test_share_of_units_floors_and_skips_hero(self):
        self.assertEqual(
            {4: 499, 1: 294},
            share_of_units({"1": 294, "4": 998, "11": 1}, {"1": 100, "4": 50, "11": 100}),
        )

    def test_siege_needs_rams_and_1000_units(self):
        self.assertFalse(can_siege({4: 997, 7: 2, 11: 1}))
        self.assertTrue(can_siege({4: 998, 7: 2, 11: 1} | {1: 10}))
        self.assertFalse(can_siege({4: 2000}))

    def test_siege_only_when_it_lands_before_robbers(self):
        units = {4: 1000, 7: 10}
        self.assertEqual(SIEGE, choose_counter_movement(units, True, 0, 600, 700)[0])
        movement, reason = choose_counter_movement(units, True, 0, 800, 700)
        self.assertEqual(ATTACK, movement)
        self.assertIn("nie zdąży", reason)
        self.assertEqual(ATTACK, choose_counter_movement(units, False, 0, 10, 700)[0])

    def test_raid_lands_after_counter_or_as_soon_as_possible(self):
        # Wanted 1001, travel 300 -> leave at 701.
        self.assertEqual((701, 1001, ""), plan_raid(1000, 1, 300, 0, 5000))
        # Too slow to land at 1001: leave now, land later.
        self.assertEqual((900, 1300), plan_raid(1000, 1, 400, 900, 5000)[:2])
        # Would land after the robbers hit: skipped, troops stay home.
        departure, arrival, reason = plan_raid(1000, 1, 400, 900, 1200)
        self.assertIsNone(departure)
        self.assertIn("do obrony", reason)

    def test_hideout_display_name(self):
        self.assertEqual("Kryjówka I", hideout_display_name("Haydut sığınağı I"))
        self.assertEqual("Kryjówka II", hideout_display_name("Robber hideout II"))
        self.assertEqual("Kryjówka II", hideout_display_name("Haydut sÄ±ÄźÄ±naÄźÄ± II"))
        self.assertEqual("Kryjówka", hideout_display_name(None))

    def test_fit_to_loot_uses_largest_carriers_first(self):
        carry = {"1": 35, "2": 45, "4": 75}
        self.assertEqual({4: 14}, fit_to_loot({1: 294, 4: 499}, carry, 1000))
        self.assertEqual({4: 499, 1: 294}, fit_to_loot({1: 294, 4: 499}, carry, 110245))
        self.assertEqual({}, fit_to_loot({1: 294}, carry, 0))


class FakeBridge:
    def __init__(self):
        self.now = 10_000.0
        self.units = {"1": 300, "4": 1000, "7": 12}
        self.fired = []
        self.loot = {"1": 1000, "2": 0, "3": 0, "4": 500}
        self.garrison = {}
        self.gone = False
        self.attacks = [{
            "movement_id": "504867", "target_village_id": "538001407",
            "target_village_name": "00.Aden", "hideout_id": "-2294",
            "hideout_cell": "538066946", "hideout_name": "Haydut I",
            "arrival": self.now + 3600, "movement_type": 3,
        }]

    def keep_awake(self):
        pass

    def read_context(self):
        return {
            "tribe": 3, "server_offset_ms": 0,
            "villages": {"538001407": {"units": dict(self.units), "hero_home": True,
                                       "rally_point_level": 20}},
            "hero": {"home_village_id": "538001407"},
        }

    def _evaluate(self, expression, **kwargs):
        if "npcInfo" in expression:
            if self.gone:
                return {"found": False, "refreshed": True, "carry": {}}
            return {"found": True, "refreshed": True, "loot": dict(self.loot),
                    "garrison": dict(self.garrison), "carry": {"1": 35, "2": 45, "4": 75}}
        return {"capital_id": "538001407", "capital_name": "00.Aden", "attacks": self.attacks}

    def check_target(self, source, target, movement):
        factor = 2 if movement == SIEGE else 1
        return {"durations": {"1": 900 * factor, "4": 340 * factor,
                              "7": 1600 * factor, "11": 340 * factor}}

    send_request = staticmethod(
        lambda village, target, movement, units, catapults=(): {
            "controller": "troops/send",
            "params": {"movementType": movement, "units": dict(units), "destVillageId": target},
        }
    )

    def fire(self, requests, latest):
        self.fired.append(requests)
        return f"{len(self.fired):032x}", {"status": "fired", "server_time": self.now}

    def read_fire(self, batch_id):
        return {"results": [{"ok": True, "error": None, "fallback": False}]}

    def read_movements(self, village_id):
        return []


class EngineTests(unittest.TestCase):
    def make(self, **overrides):
        bridge = FakeBridge()
        settings = HideoutDefenseSettings(**{"enabled": True, "counter_units": {"4": 100}, **overrides})
        handled = []
        defense = HideoutDefense(
            bridge, lambda: settings, lambda _text: None, handled.append,
            clock=lambda: bridge.now,
        )
        return bridge, settings, defense, handled

    def tick(self, defense):
        defense.last_scan = None
        defense.tick()

    def test_counter_attack_with_hero_then_raid_sized_to_loot(self):
        bridge, settings, defense, handled = self.make(
            raid_enabled=True, raid_units={"1": 100, "4": 100}, raid_delay_seconds=1,
            raid_repeat=False,
        )
        self.tick(defense)
        case = defense.cases[0]
        self.assertEqual(STATUS_COUNTER_FIRED, case.status)
        params = bridge.fired[0][0]["params"]
        self.assertEqual(ATTACK, params["movementType"])
        self.assertEqual({4: 1000, 11: 1}, params["units"])
        self.assertEqual("538066946", params["destVillageId"])

        bridge.units = {"1": 300, "7": 12}  # thunders left with the counter
        self.tick(defense)
        self.assertEqual(STATUS_RAID_SCHEDULED, case.status)
        self.assertEqual([{"504867"}], handled)
        # Loot 1500 -> 43 phalanxes (35 each). Phalanxes (900 s) are slower
        # than the counter (340 s), so the raid leaves right away.
        self.assertEqual({1: 43}, case.raid_units)
        self.assertEqual(bridge.now, case.raid_departure)

        self.tick(defense)
        self.assertEqual(2, len(bridge.fired))
        raid = bridge.fired[1][0]["params"]
        self.assertEqual(MOVEMENT_RAID, raid["movementType"])
        self.tick(defense)
        self.assertEqual(STATUS_DONE, case.status)

        # The same robber movement never triggers a second counter-attack.
        settings.handled_movements = sorted(handled[0])
        defense.cases.clear()
        self.tick(defense)
        self.assertEqual(2, len(bridge.fired))

    def test_repeats_raids_until_loot_is_gone(self):
        bridge, _settings, defense, _handled = self.make(
            raid_enabled=True, raid_units={"1": 10},
        )
        bridge.units = {"1": 10, "4": 1000}
        for _ in range(3):
            self.tick(defense)  # counter, verify + schedule, raid
        self.tick(defense)      # verify raid
        case = defense.cases[0]
        self.assertEqual(STATUS_RAID_RETURNING, case.status)
        self.assertEqual(bridge.now + 900 + 2, case.raid_return_at)

        self.tick(defense)      # troops not back yet: nothing happens
        self.assertEqual(STATUS_RAID_RETURNING, case.status)
        bridge.now += 905
        bridge.loot = {"1": 600, "2": 0, "3": 0, "4": 0}
        self.tick(defense)      # back home, loot left, hideout empty -> next raid
        self.assertEqual(STATUS_RAID_SCHEDULED, case.status)
        self.tick(defense)
        self.tick(defense)
        self.assertEqual(3, len(bridge.fired))
        self.assertEqual(STATUS_RAID_RETURNING, case.status)
        self.assertEqual(2, case.raids_sent)

        bridge.now += 905
        bridge.loot = {"1": 50, "2": 0, "3": 0, "4": 0}
        self.tick(defense)
        self.assertEqual(STATUS_DONE, case.status)
        self.assertIn("Łup zebrany", case.raid_message)

    def test_next_raid_waits_for_the_robber_attack_when_it_cannot_beat_it(self):
        bridge, _settings, defense, _handled = self.make(
            raid_enabled=True, raid_units={"1": 10},
        )
        bridge.units = {"1": 10, "4": 1000}
        bridge.attacks[0]["arrival"] = bridge.now + 1500
        for _ in range(4):
            self.tick(defense)
        case = defense.cases[0]
        bridge.now += 905   # raid landed, next one would hit after the robbers
        self.tick(defense)
        self.assertEqual(STATUS_RAID_RETURNING, case.status)
        self.assertEqual(2, len(bridge.fired))
        self.assertIn("po ataku", case.raid_message)
        bridge.now += 600   # robbers have hit: raid again
        self.tick(defense)
        self.tick(defense)
        self.assertEqual(3, len(bridge.fired))

    def test_vanished_robber_attack_lifts_the_time_limit(self):
        bridge, _settings, defense, _handled = self.make(
            raid_enabled=True, raid_units={"1": 10},
        )
        bridge.units = {"1": 10, "4": 1000}
        bridge.attacks[0]["arrival"] = bridge.now + 1500
        for _ in range(4):
            self.tick(defense)
        bridge.attacks = []          # counter destroyed the robbers
        bridge.now += 905
        self.tick(defense)
        self.tick(defense)
        self.assertEqual(3, len(bridge.fired))

    def test_waits_for_own_troops_and_resumes_after_restart(self):
        bridge, settings, defense, _handled = self.make(
            raid_enabled=True, raid_units={"1": 100},
        )
        settings.active_hideouts = {"538066946": {"id": "-2294", "name": "Haydut I"}}
        bridge.attacks = []
        bridge.units = {"4": 1000}   # phalanxes still on their way back
        returning = [{"source": "-2294", "target": "538001407", "arrival": bridge.now + 300,
                      "movement_type": 4}]
        bridge.read_movements = lambda _village: returning
        self.tick(defense)
        case = defense.cases[0]
        self.assertEqual(STATUS_RAID_RETURNING, case.status)
        self.assertEqual(bridge.now + 305, case.raid_return_at)
        self.assertEqual([], bridge.fired)
        bridge.now += 305
        bridge.units = {"1": 500}
        returning.clear()
        self.tick(defense)
        self.tick(defense)
        self.assertEqual(4, bridge.fired[0][0]["params"]["movementType"])
        self.assertEqual({1: 43}, bridge.fired[0][0]["params"]["units"])

    def test_hideout_removed_from_map_ends_looting(self):
        bridge, _settings, defense, _handled = self.make(
            raid_enabled=True, raid_units={"1": 10},
        )
        bridge.units = {"1": 10, "4": 1000}
        for _ in range(4):
            self.tick(defense)
        bridge.now += 905
        bridge.gone = True
        self.tick(defense)
        case = defense.cases[0]
        self.assertEqual(STATUS_DONE, case.status)
        self.assertIn("zniknęła", case.raid_message)

    def test_stops_raiding_when_hideout_garrison_is_back(self):
        bridge, _settings, defense, _handled = self.make(
            raid_enabled=True, raid_units={"1": 10},
        )
        bridge.units = {"1": 10, "4": 1000}
        for _ in range(4):
            self.tick(defense)
        bridge.now += 3600
        bridge.garrison = {"1": "40"}
        self.tick(defense)
        case = defense.cases[0]
        self.assertEqual(STATUS_DONE, case.status)
        self.assertIn("obsada", case.raid_message)
        self.assertEqual(2, len(bridge.fired))

    def test_siege_when_in_time_with_attack_fallback(self):
        bridge, _settings, defense, _handled = self.make(
            siege_enabled=True, counter_units={"4": 100, "7": 100},
        )
        bridge.units = {"4": 1000, "7": 12}
        self.tick(defense)
        request = bridge.fired[0][0]
        self.assertEqual(SIEGE, request["params"]["movementType"])
        self.assertEqual(ATTACK, request["fallback"]["movementType"])

    def test_raid_skipped_when_it_cannot_beat_the_robbers(self):
        bridge, _settings, defense, _handled = self.make(
            raid_enabled=True, raid_units={"1": 100}, raid_fit_loot=False,
        )
        bridge.attacks[0]["arrival"] = bridge.now + 600
        self.tick(defense)
        self.tick(defense)
        case = defense.cases[0]
        self.assertEqual(STATUS_DONE, case.status)
        self.assertIn("do obrony", case.raid_message)
        self.assertEqual(1, len(bridge.fired))

    def test_disabled_only_watches(self):
        bridge, settings, defense, _handled = self.make()
        settings.enabled = False
        self.tick(defense)
        self.assertEqual(1, len(defense.cases))
        self.assertEqual([], bridge.fired)


class SettingsTests(unittest.TestCase):
    def test_unit_percent_validation(self):
        settings = HideoutDefenseSettings(counter_units={"4": 100, "1": 0})
        self.assertEqual({"4": 100}, settings.counter_units)
        with self.assertRaises(ValueError):
            HideoutDefenseSettings(counter_units={"4": 101})
        with self.assertRaises(ValueError):
            HideoutDefenseSettings(raid_units={"11": 50})


class WidgetTests(unittest.TestCase):
    def test_widget_round_trips_settings(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from src.app import HideoutDefenseWidget

        QApplication.instance() or QApplication([])
        widget = HideoutDefenseWidget()
        emitted = []
        widget.setting_changed.connect(lambda field, value: emitted.append((field, value)))
        widget.load_settings(HideoutDefenseSettings(
            counter_units={"4": 100}, raid_units={"1": 50}, raid_delay_seconds=120,
        ))
        self.assertEqual([], emitted)
        self.assertEqual(100, widget.counter_spins[4].value())
        self.assertEqual(2, widget.raid_delay.value())
        self.assertEqual("min", widget.raid_delay_unit.currentText())
        widget.raid_spins[4].setValue(30)
        self.assertEqual(("raid_units", {"1": 50, "4": 30}), emitted[-1])
        widget.raid_delay_unit.setCurrentIndex(0)
        self.assertEqual(("raid_delay_seconds", 2), emitted[-1])
        widget.set_capital(3, "00.Aden", {"4": 998})
        self.assertEqual("Grom Teutatesa", widget.unit_labels[4].text())
        self.assertEqual("998", widget.home_labels[4].text())
        widget.update_rows([{
            "hideout": "Haydut I", "target": "00.Aden", "deadline": 1794099421.0,
            "counter_arrival": None, "raid_arrival": None, "status_key": "detected",
            "status": "Wykryto", "message": "", "loot": 110245, "garrison": "Legionista 104",
        }])
        self.assertIn("110 245", widget.table.item(0, 6).text())


if __name__ == "__main__":
    unittest.main()
