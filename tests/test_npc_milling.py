import unittest

from src.core.npc_milling import milling_plan


class MillingPlanTests(unittest.TestCase):
    def test_empty_turns_all_crop_into_the_split(self):
        plan = milling_plan([1000, 2000, 3000, 94000], 100000, "empty", 40, 40, 20)
        self.assertEqual(0, plan[3])
        self.assertEqual(100000, sum(plan))
        self.assertEqual([40000, 40000, 20000, 0], plan)

    def test_refill_fills_the_granary_from_the_rest(self):
        plan = milling_plan([40000, 40000, 40000, 5000], 100000, "refill", 30, 30, 40)
        self.assertEqual(100000, plan[3])
        self.assertEqual([7500, 7500, 10000, 100000], plan)

    def test_refill_takes_everything_when_the_stock_is_small(self):
        self.assertEqual([0, 0, 0, 900], milling_plan([100, 200, 300, 300], 5000, "refill", 50, 25, 25))

    def test_rounding_rest_goes_to_the_largest_share(self):
        plan = milling_plan([0, 0, 0, 100], 1000, "empty", 33, 33, 34)
        self.assertEqual([33, 33, 34, 0], plan)
        plan = milling_plan([0, 0, 0, 101], 1000, "empty", 34, 33, 33)
        self.assertEqual(101, sum(plan))
        self.assertEqual(35, plan[0])

    def test_invalid_split_is_rejected(self):
        with self.assertRaises(ValueError):
            milling_plan([0, 0, 0, 100], 1000, "empty", 50, 50, 10)
        with self.assertRaises(ValueError):
            milling_plan([0, 0, 0, 100], 1000, "fill", 50, 25, 25)


if __name__ == "__main__":
    unittest.main()
