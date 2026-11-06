"""Fast model-based reads that replaced UI-driven ones (no hovering/switching)."""

import unittest

from src.core.browser_session import TravianBrowserSession, _model_production_rates


class ModelProductionTests(unittest.TestCase):
    def test_uses_model_values(self):
        self.assertEqual(
            {"wood": 9068, "clay": 8155, "iron": 7030, "grain": 13957},
            _model_production_rates(
                {"wood": 9068.0, "clay": 8155, "iron": 7030, "grain": 13957.4}
            ),
        )

    def test_incomplete_model_falls_back(self):
        self.assertIsNone(_model_production_rates(None))
        self.assertIsNone(
            _model_production_rates({"wood": 1, "clay": 2, "iron": float("nan"), "grain": 4})
        )


class TroopReadTests(unittest.TestCase):
    def test_reads_all_villages_in_one_call_without_switching(self):
        class FakeTab:
            def __init__(self):
                self.calls = []

            def call_method(self, method, **kwargs):
                self.calls.append(method)
                return {"result": {"value": {
                    "complete": True,
                    "tribe_id": 3,
                    "villages": {
                        "101": {"units": {"1": 176, "4": 776}, "hero": 1},
                        "102": {"units": {}, "hero": 0},
                    },
                }}}

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        rows = session.read_troops_for_villages([
            {"id": "101", "name": "Aden"},
            {"id": "102", "name": "Giran"},
        ])
        self.assertEqual(["Runtime.evaluate"], session._tab.calls)
        counts = {(row["village"], row["unit"]): row["count"] for row in rows}
        self.assertEqual(176, counts[("Aden", "Phalanx")])
        self.assertEqual(776, counts[("Aden", "Theutates Thunder")])
        self.assertEqual(1, counts[("Aden", "Hero")])
        self.assertEqual(0, counts[("Giran", "Phalanx")])
        self.assertEqual(22, len(rows))

    def test_falls_back_when_game_data_is_missing(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                return {"result": {"value": {"complete": False}}}

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        called = []
        session._read_troops_by_switching = lambda villages: called.append(villages) or []
        session.read_troops_for_villages([{"id": "101", "name": "Aden"}])
        self.assertEqual(1, len(called))


if __name__ == "__main__":
    unittest.main()
