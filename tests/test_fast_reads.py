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


class VillageProductionTests(unittest.TestCase):
    def test_reads_production_of_every_village(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.expression = kwargs["expression"]
                return {"result": {"value": {
                    "537968638": {"wood": 3131, "clay": 3000, "iron": 2246, "grain": 417},
                    "538001407": {"wood": 10268, "clay": None, "iron": 1, "grain": 1},
                }}}

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        production = session.read_village_production([
            {"id": "537968638", "name": "Dion"}, {"id": "538001407", "name": "Aden"},
        ])
        self.assertEqual(
            {"537968638": {"wood": 3131, "clay": 3000, "iron": 2246, "grain": 417}},
            production,
        )
        self.assertIn('["537968638", "538001407"]', session._tab.expression)

    def test_balance_prefers_live_production_over_stale_record(self):
        import os
        import tempfile
        from pathlib import Path

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        import src.app as app_module
        from src.config.settings import AppSettings
        from src.core.account_manager import AccountManager
        from src.core.village_tracker import VillageTracker
        from src.database.connection import DatabaseManager
        from src.database.repository import AccountRepository, VillageRepository

        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "balance.db"))
            db.init()
            window = app_module.MainWindow(
                AppSettings(), AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            try:
                window.dashboard_tab._live_state = {
                    "villages": [{"id": "537968638", "name": "Dion"}],
                }
                window._village_production = {
                    "537968638": {"wood": 3131, "clay": 3000, "iron": 2246, "grain": 417},
                }
                captured = []
                window.recruitment_tab.update_recruitment_economics = captured.extend
                window._render_recruitment_economics([])
                rates = {row["resource"]: row["production_rate"] for row in captured}
                self.assertEqual(
                    {"Drewno": 3131, "Glina": 3000, "Żelazo": 2246, "Zboże": 417}, rates,
                )
                self.assertEqual(3131, captured[0]["net_rate"])
            finally:
                window.close()
                db.close()


class SharedBuildingStatusTests(unittest.TestCase):
    def test_status_explains_village_switch_and_shared_building(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from src.app import RecruitmentWidget
        from src.config.settings import RecruitmentSettings, VillageRecruitmentSettings

        QApplication.instance() or QApplication([])
        settings = RecruitmentSettings(
            automatic_enabled=True,
            queue_strategy="duration",
            villages={"101": VillageRecruitmentSettings(
                enabled=False, units={"26"}, queue_minutes_by_unit={"26": 720},
            )},
        )
        widget = RecruitmentWidget()
        widget.bind_settings(settings)
        row = {
            "village_id": "101", "village": "Aden", "unit": "Haeduan",
            "unit_id": "26", "unit_type": 26, "building": "Stajnia",
            "building_type": 20, "building_available": True,
            "count": 0, "other_count": 164, "training_times_seconds": [900.0],
        }
        widget.update_recruitment_queues([row], set())
        status = widget.unit_rows[("101", "26")]["status"]
        self.assertEqual("Automat wioski wyłączony", status.text())
        widget.village_recruitment_controls["101"]["enabled"].setChecked(True)
        self.assertEqual("Dołączy do kolejki", status.text())


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


class PartialRecruitmentTests(unittest.TestCase):
    def test_passes_minimum_and_share_and_validates_quantity(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                import re
                self.expression = kwargs["expression"]
                request_id = re.findall(r'"([a-f0-9]{32})"', self.expression)[-1]
                return {"result": {"value": {
                    "status": "submitted", "request_id": request_id, "quantity": 37,
                }}}

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        row = {"village_id": "101", "building_type": 19, "unit_type": 21, "unit": "Phalanx"}
        outcome = session.queue_recruitment(row, 500, 3, min_quantity=1, resource_share=0.5)
        self.assertEqual(37, outcome["quantity"])
        self.assertIn(", 1, 0.5)", session._tab.expression)
        self.assertIn("Math.floor(available * resourceShare / cost)", session._tab.expression)
        with self.assertRaises(ValueError):
            session.queue_recruitment(row, 5, 3, min_quantity=6)
        with self.assertRaises(ValueError):
            session.queue_recruitment(row, 5, 3, resource_share=0)


class RecruitmentLockExpiryTests(unittest.TestCase):
    def test_unconfirmed_locks_expire_after_restart(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        import src.app as app_module
        from src.config.settings import AppSettings
        from src.core.account_manager import AccountManager
        from src.core.village_tracker import VillageTracker
        from src.database.connection import DatabaseManager
        from src.database.repository import AccountRepository, VillageRepository

        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "locks.db"))
            db.init()
            settings = AppSettings()
            settings.recruitment.pending_orders = {"101:21", "101:24"}
            with patch.object(app_module, "monotonic", return_value=1000.0):
                window = app_module.MainWindow(
                    settings,
                    AccountManager(AccountRepository(db)),
                    VillageTracker(VillageRepository(db)),
                )
            try:
                window._pending_recruitment_responses["101:24"] = (0, 1)
                with patch.object(AppSettings, "save", return_value=None):
                    with patch.object(app_module, "monotonic", return_value=1100.0):
                        window._expire_stale_recruitment_locks()
                    self.assertEqual({"101:21", "101:24"}, settings.recruitment.pending_orders)
                    with patch.object(app_module, "monotonic", return_value=1400.0):
                        window._expire_stale_recruitment_locks()
                # The lock with a verification still in flight is kept.
                self.assertEqual({"101:24"}, settings.recruitment.pending_orders)
            finally:
                window.close()
                db.close()


if __name__ == "__main__":
    unittest.main()
