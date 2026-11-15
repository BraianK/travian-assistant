import unittest

from src.core import quests as qb
from src.core import start_plan as sp
from src.core import village_builder as vb


def _building(location, type_, level, cost=100, max_level=20, queued=0):
    levels = range(0, 25)
    return vb.Building(
        location=location, type=type_, level=level, max_level=max_level,
        costs={lvl: {r: cost for r in vb.RESOURCES} for lvl in levels},
        times={lvl: 600 for lvl in levels},
        effects={lvl: lvl for lvl in levels},
        supply={lvl: lvl for lvl in levels},
        queued=queued,
    )


def _village(fields=(), buildings=(), resources=10_000, free=1, empty=(20, 21, 39)):
    all_buildings = list(fields) + list(buildings)
    return vb.Village(
        id="1", name="Start", is_capital=True, x=0, y=0, tribe=3,
        resources={r: resources for r in vb.RESOURCES},
        capacity={r: 100_000 for r in vb.RESOURCES},
        production={r: 100 for r in vb.RESOURCES},
        fields=list(fields), storages=[], free_slots={1: free, 2: free, 4: 0},
        empty_slots=list(empty), all_buildings=all_buildings,
        type_levels={b.type: b.level for b in all_buildings if b.type > 4},
    )


CONFIG = {
    23: {"costs": {"1": 40, "2": 50, "3": 30, "4": 10}, "requirements": []},
    22: {"costs": {"1": 220, "2": 160, "3": 90, "4": 40},
         "requirements": [{"building": 19, "level": 3}, {"building": 15, "level": 3}]},
}


class StartPlanTests(unittest.TestCase):
    def test_fields_and_buildings_go_side_by_side(self):
        village = _village(
            fields=[_building(1, 4, 0), _building(2, 4, 0), _building(3, 1, 0)],
            buildings=[_building(26, 15, 1)],
        )
        steps = [sp._b(4, 1, "all"), sp._b(15, 3)]
        states = sp.plan_states(steps, village, [], {}, CONFIG)
        self.assertEqual(["act", "act"], [s.status for s in states])
        self.assertEqual(("upgrade", 1, True), (states[0].action.kind, states[0].action.location, states[0].action.is_field))
        self.assertEqual(("upgrade", 26, 1), (states[1].action.kind, states[1].action.location, states[1].action.from_level))

    def test_waiting_for_resources_holds_later_steps(self):
        village = _village(fields=[_building(1, 4, 0, cost=50_000)], buildings=[_building(26, 15, 1)])
        states = sp.plan_states([sp._b(4, 1, "all"), sp._b(15, 3)], village, [], {}, CONFIG)
        self.assertEqual("wait", states[0].status)
        self.assertIn("surowce", states[0].text)
        self.assertEqual("later", states[1].status)

    def test_busy_queue_lets_the_other_queue_go(self):
        village = _village(fields=[_building(1, 4, 0)], buildings=[_building(26, 15, 1)])
        village.free_slots = {1: 1, 2: 0, 4: 0}
        states = sp.plan_states([sp._b(4, 1, "all"), sp._b(15, 3)], village, [], {}, CONFIG)
        self.assertEqual(["wait", "act"], [s.status for s in states])

    def test_missing_building_is_constructed_on_a_free_site_when_allowed(self):
        village = _village(buildings=[_building(26, 15, 3)])
        states = sp.plan_states([sp._b(23, 1)], village, [], {}, CONFIG)
        self.assertEqual(("construct", 20, 23), (states[0].action.kind, states[0].action.location, states[0].action.type))
        # The academy needs barracks 3: those come first (built on a free site).
        first = sp.plan_states([sp._b(22, 10)], village, [], {}, CONFIG)[0]
        self.assertEqual("blocked", first.status)  # no data for the barracks in CONFIG
        config = {**CONFIG, 19: {"costs": {"1": 210, "2": 140, "3": 260, "4": 120}, "requirements": []}}
        first = sp.plan_states([sp._b(22, 10)], village, [], {}, config)[0]
        self.assertEqual(("construct", 19), (first.action.kind, first.action.type))
        self.assertIn("najpierw Koszary", first.text)

    def test_one_field_and_done_detection(self):
        fields = [_building(1, 4, 3), _building(2, 4, 1)]
        village = _village(fields=fields)
        states = sp.plan_states([sp._b(4, 3, "one"), sp._b(4, 3, "all")], village, [], {}, CONFIG)
        self.assertEqual("done", states[0].status)
        self.assertEqual(2, states[1].action.location)

    def test_reward_step_collects_the_residence_quest_and_holds_the_rest(self):
        village = _village(buildings=[_building(26, 15, 7), _building(27, 25, 5)])
        quests = qb.quests_from_raw({"quests": [
            {"id": 77, "status": 4, "rewards": {"11": {"buildingType": 25, "minLevel": 5, "rewardedLevel": 10}}},
        ]})
        steps = [{"kind": "reward", "type": 25, "level": 10}, sp._b(15, 10)]
        states = sp.plan_states(steps, village, quests, {}, CONFIG)
        self.assertEqual(("act", "collect", 77), (states[0].status, states[0].action.kind, states[0].action.quest_id))
        self.assertEqual("later", states[1].status)
        states = sp.plan_states(steps, village, [], {}, CONFIG)
        self.assertEqual("wait", states[0].status)

    def test_settlers_and_manual_steps(self):
        residence = _building(27, 25, 10)
        village = _village(buildings=[residence])
        village.residence = residence
        village.settler_unit = 30
        village.settler_costs = {1: 4000, 2: 4000, 3: 4000, 4: 1000}
        steps = [{"kind": "settlers", "count": 3}, {"kind": "manual", "text": "Wyburz"}, sp._b(15, 1)]
        states = sp.plan_states(steps, village, [], {}, CONFIG)
        self.assertEqual(("settlers", 2), (states[0].action.kind, states[0].action.quantity))
        village.settlers = 3
        states = sp.plan_states(steps, village, [], {}, CONFIG)
        self.assertEqual(["done", "manual", "later"], [s.status for s in states])
        steps[1]["done"] = True
        states = sp.plan_states(steps, village, [], {}, CONFIG)
        self.assertEqual("done", states[1].status)

    def test_optional_manual_step_does_not_hold_the_plan(self):
        village = _village(buildings=[_building(26, 15, 1)])
        steps = [sp._m("Oaza", blocking=False), sp._b(15, 2)]
        self.assertEqual(["manual", "act"], [s.status for s in sp.plan_states(steps, village, [], {}, CONFIG)])

    def test_troops_need_barracks_and_do_not_hold_the_plan(self):
        village = _village(buildings=[_building(26, 15, 1)])
        troops = {21: {"costs": {1: 75, 2: 50, 3: 100, 4: 0}, "building_types": [19]},
                  22: {"costs": {1: 120, 2: 100, 3: 150, 4: 0}, "building_types": [19]}}
        steps = [{"kind": "troops", "count": 5}, sp._b(15, 2)]
        states = sp.plan_states(steps, village, [], troops, CONFIG)
        self.assertEqual(["wait", "act"], [s.status for s in states])
        village.recruit_buildings = {19}
        states = sp.plan_states(steps, village, [], troops, CONFIG)
        self.assertEqual(("troops", 21, 5), (states[0].action.kind, states[0].action.unit, states[0].action.quantity))

    def test_default_plan_labels(self):
        labels = [sp.label(step) for step in sp.DEFAULT_STEPS]
        self.assertIn("Magazyn 3", labels)
        self.assertIn("Pole zboża na 3 (jedno)", labels)
        self.assertIn("Odbierz nagrodę: rezydencja na 10", labels)
        self.assertTrue(any(label.startswith("Wyburz rezydencję") for label in labels))


class StartAutomationTests(unittest.TestCase):
    def test_start_clicks_actions_and_marks_done(self):
        from src.config.settings import BuildSettings
        from src.core.build_automation import BuildAutomation, BuildSnapshot

        class Executor:
            def __init__(self):
                self.calls = []

            def upgrade(self, *args):
                self.calls.append(("upgrade",) + args)
                return "started"

            def construct(self, *args):
                self.calls.append(("construct",) + args)
                return "started"

            def close(self):
                pass

        steps = [sp._b(10, 1), sp._b(4, 1, "all"), sp._b(23, 1)]
        settings = BuildSettings(start_enabled=True, start_village="1", start_steps=steps)
        executor = Executor()
        logs = []
        automation = BuildAutomation(None, lambda: settings, logs.append, executor=executor)
        automation._construct_config = CONFIG
        village = _village(fields=[_building(1, 4, 0)], buildings=[_building(25, 10, 1), _building(26, 15, 3)])
        snapshot = BuildSnapshot(plans=[vb.plan_village(village, 10, 0)])
        snapshot.start_states = automation._start_states(snapshot, settings)
        self.assertTrue(automation._start(snapshot, settings))
        self.assertTrue(settings.start_steps[0]["done"])  # warehouse 1 already stands
        self.assertEqual([("upgrade", "1", 1, 0, True), ("construct", "1", 20, 23)], executor.calls)
        self.assertTrue(automation.budget_changed)


if __name__ == "__main__":
    unittest.main()
