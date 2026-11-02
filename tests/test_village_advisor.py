import unittest

from src.core.village_advisor import get_village_plan


class VillageAdvisorTests(unittest.TestCase):
    def test_selects_fastest_affordable_resource_field_upgrade(self):
        plan = get_village_plan(
            resources={"wood": 0, "clay": 0, "iron": 0, "grain": 0},
            production_rates={"wood": 10, "clay": 10, "iron": 10, "grain": 10},
            resource_fields=[
                {"name": "Woodcutter", "slot": 1, "level": 1},
                {"name": "Clay pit", "slot": 2, "level": 3},
            ],
            buildings=[],
        )

        self.assertEqual("Woodcutter", plan["next_field"]["name"])
        self.assertEqual(2, plan["next_field"]["target_level"])
        self.assertEqual(9, plan["next_field"]["wait_hours"])

    def test_skips_field_that_is_already_upgrading(self):
        plan = get_village_plan(
            resources={"wood": 1000, "clay": 1000, "iron": 1000, "grain": 1000},
            production_rates={},
            resource_fields=[
                {"name": "Woodcutter", "slot": 1, "level": 1},
                {"name": "Clay pit", "slot": 2, "level": 1},
            ],
            buildings=[{"name": "Woodcutter", "slot": 1, "upgrading": True}],
        )

        self.assertEqual("Clay pit", plan["next_field"]["name"])

    def test_counts_affordable_gaul_units_and_keeps_queue_reminders(self):
        plan = get_village_plan(
            resources={"wood": 500, "clay": 500, "iron": 500, "grain": 500},
            production_rates={},
            resource_fields=[],
            buildings=[{"name": "Barracks"}, {"name": "Stable"}],
        )

        self.assertEqual(
            {"Phalanx": 3, "Theutates Thunder": 1},
            {unit["name"]: unit["affordable_count"] for unit in plan["units"]},
        )
        self.assertEqual(
            [True, True],
            [reminder["available"] for reminder in plan["reminders"]],
        )
        self.assertEqual(
            ["Phalanx", "Theutates Thunder"],
            [reminder["unit"] for reminder in plan["reminders"]],
        )

    def test_does_not_estimate_affordability_without_production(self):
        plan = get_village_plan(
            resources={"wood": 0, "clay": 0, "iron": 0, "grain": 0},
            production_rates={},
            resource_fields=[{"name": "Woodcutter", "slot": 1, "level": 1}],
            buildings=[],
        )

        self.assertIsNone(plan["next_field"]["wait_hours"])
        self.assertIsNone(plan["units"][0]["next_unit_hours"])


if __name__ == "__main__":
    unittest.main()
