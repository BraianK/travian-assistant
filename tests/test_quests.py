import unittest

from src.core import quests as qb


def _storage(vid, stock, warehouse, granary, need=0.0):
    return qb.Storage(vid, vid, {r: float(stock) for r in qb.RESOURCES},
                      {1: warehouse, 2: warehouse, 3: warehouse, 4: granary}, need)


class QuestParsingTests(unittest.TestCase):
    def test_adventures_and_envoy_are_left_out_and_daily_marked(self):
        quests = qb.quests_from_raw({"quests": [
            {"id": 639, "status": 3, "rewards": {"2": {"1": 100000, "2": 100000, "3": 100000, "4": 0}}},
            {"id": 991, "status": 3, "rewards": []},
            {"id": 101, "status": 2, "rewards": {}},
            {"id": 10006, "status": 3, "rewards": {"4": 200}},
        ]})
        self.assertEqual([639, 10006], [q.id for q in quests])
        self.assertEqual({1: 100000, 2: 100000, 3: 100000, 4: 0}, quests[0].resources)
        self.assertFalse(quests[0].daily)
        self.assertTrue(quests[1].daily)
        self.assertEqual({}, quests[1].resources)
        self.assertEqual("Applied research", quests[1].label)

    def test_building_level_reward_is_recognised(self):
        quest = qb.quests_from_raw({"quests": [{"id": 5, "status": 4, "rewards": {"11": {"buildingType": 25}}}]})[0]
        self.assertTrue(quest.building_level)


class FieldQuestTests(unittest.TestCase):
    def test_field_quests_are_read_from_the_titles(self):
        quests = qb.quests_from_raw({"quests": [
            {"id": 639, "status": 3, "name": "Fields to 10 in 5",
             "rewards": {"2": {"1": 100000, "2": 100000, "3": 100000, "4": 0}}},
            {"id": 640, "status": 3, "name": "Croplands to 10 in 5", "rewards": {"2": {"4": 100000}}},
            {"id": 30, "status": 3, "name": "All resource fields to 12", "rewards": {"2": {"1": 66000}}},
            {"id": 526, "status": 3, "name": "Cranny to level 5", "rewards": {"2": {"1": 150}}},
            {"id": 641, "status": 2, "name": "Fields to 10 in 12", "rewards": {"2": {"1": 150000}}},
        ]})
        found = [(q.types, q.level, q.villages, q.reward) for q in qb.field_quests(quests)]
        self.assertEqual([((1, 2, 3), 10, 5, 300000.0), ((4,), 10, 5, 100000.0),
                          ((1, 2, 3, 4), 12, 1, 66000.0)], found)


class RewardVillageTests(unittest.TestCase):
    def test_goes_where_it_fits_and_is_needed_most(self):
        reward = {1: 10000, 2: 11000, 3: 11000, 4: 1500}
        full = _storage("full", 70000, 80000, 80000)
        roomy = _storage("roomy", 10000, 80000, 80000, need=5000)
        needy = _storage("needy", 20000, 80000, 80000, need=40000)
        village, lost = qb.reward_village(reward, [full, roomy, needy])
        self.assertEqual("needy", village.village_id)
        self.assertEqual(0, lost)

    def test_waits_while_every_storage_would_overflow(self):
        reward = {1: 100000, 2: 100000, 3: 100000, 4: 0}
        a = _storage("a", 60000, 120000, 100000)
        b = _storage("b", 70000, 150000, 100000)
        village, lost = qb.reward_village(reward, [a, b])
        self.assertIsNone(village)
        self.assertGreater(lost, 0)

    def test_storage_smaller_than_the_reward_takes_it_when_empty(self):
        reward = {1: 100000, 2: 100000, 3: 100000, 4: 0}
        small = _storage("small", 1000, 80000, 80000)
        village, lost = qb.reward_village(reward, [small])
        self.assertEqual("small", village.village_id)
        self.assertEqual(3 * 21000, lost)


class DailyExchangeTests(unittest.TestCase):
    def test_exchanges_an_undoable_quest_once_a_day(self):
        quests = qb.quests_from_raw({"quests": [
            {"id": 10006, "status": 3, "rewards": {}},
            {"id": 10016, "status": 3, "rewards": {}},
            {"id": 10013, "status": 3, "rewards": {}},
        ]})
        self.assertEqual(10016, qb.daily_to_exchange(quests, 0, 1).id)
        self.assertIsNone(qb.daily_to_exchange(quests, 1, 1))

    def test_nothing_to_exchange_when_all_are_doable_or_done(self):
        quests = qb.quests_from_raw({"quests": [
            {"id": 10004, "status": 3, "rewards": {}},
            {"id": 10016, "status": 4, "rewards": {}},
        ]})
        self.assertIsNone(qb.daily_to_exchange(quests, 0, 1))


class _QuestExecutor:
    def __init__(self):
        self.calls = []

    def activate_quest(self, quest_id, daily):
        self.calls.append(("activate", quest_id))
        return "activated"

    def collect_quest(self, quest_id, village_id, daily):
        self.calls.append(("collect", quest_id, village_id))
        return "collected"

    def exchange_daily(self, quest_id):
        self.calls.append(("exchange", quest_id))
        return "exchanged"

    def open_premium_menu(self):
        self.calls.append(("premium",))
        return "opened"

    def close(self):
        pass


class QuestAutomationTests(unittest.TestCase):
    def test_one_pass_over_the_quest_book(self):
        from tests.test_village_builder import _field, _village
        from src.config.settings import BuildSettings
        from src.core import village_builder as vb
        from src.core.build_automation import BuildAutomation, BuildSnapshot

        settings = BuildSettings(enabled=True, quests_auto=True)
        executor = _QuestExecutor()
        logs = []
        automation = BuildAutomation(None, lambda: settings, logs.append, executor=executor)
        small = _village([_field(1, 1, 3)], vid="1", resources=70_000, capacity=80_000)
        big = _village([_field(1, 1, 3)], vid="2", resources=10_000, capacity=80_000)
        attacked = _village([_field(1, 1, 3)], vid="3", resources=0, capacity=80_000)
        quests = qb.quests_from_raw({"quests": [
            {"id": 834, "status": 2, "rewards": {"10": 5}},
            {"id": 742, "status": 4, "rewards": {"2": {"1": 10000, "2": 11000, "3": 11000, "4": 1500}, "8": 2000}},
            {"id": 820, "status": 4, "rewards": {"1": 150}},
            {"id": 7, "status": 4, "rewards": {"11": {"buildingType": 25}}},
            {"id": 10015, "status": 3, "rewards": {"4": 100}},
            {"id": 10016, "status": 3, "rewards": {"4": 100}},
        ]})
        snapshot = BuildSnapshot(
            plans=[vb.plan_village(v, 10, 0) for v in (small, big, attacked)],
            threats={"3": 1e12}, quests=quests, daily_exchanged=0, daily_exchange_max=1,
        )
        self.assertTrue(automation._quests(snapshot))
        self.assertIn(("activate", 834), executor.calls)
        self.assertIn(("collect", 742, "2"), executor.calls)  # fits there, village 3 is attacked
        self.assertIn(("collect", 820, "1"), executor.calls)
        self.assertNotIn(7, [c[1] for c in executor.calls if len(c) > 1])
        self.assertIn(("premium",), executor.calls)
        self.assertIn(("exchange", 10016), executor.calls)
        self.assertTrue(any("ręcznie" in line for line in logs))


    def test_reward_waits_for_its_storage_upgrades(self):
        from tests.test_village_builder import _reward_village
        from src.config.settings import BuildSettings
        from src.core import village_builder as vb
        from src.core.build_automation import BuildAutomation, BuildSnapshot

        settings = BuildSettings(enabled=True, quests_auto=True)
        executor = _QuestExecutor()
        logs = []
        automation = BuildAutomation(None, lambda: settings, logs.append, executor=executor)
        village = _reward_village()
        quests = qb.quests_from_raw({"quests": [
            {"id": 639, "status": 4, "rewards": {"2": {"1": 130000, "2": 130000, "3": 130000, "4": 0}}},
        ]})
        goals = automation._reward_storage_goals([village], quests, settings)
        self.assertEqual({"7": {1: 130000}}, goals)
        self.assertTrue(any("rozbudowuje magazyn" in line for line in logs))
        snapshot = BuildSnapshot(
            plans=[vb.plan_village(village, 10, 0, storage_goal=goals["7"])], quests=quests,
        )
        automation._quests(snapshot)
        self.assertEqual([], executor.calls)


if __name__ == "__main__":
    unittest.main()
