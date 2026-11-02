import os
import base64
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.app import MainWindow
from src.config.settings import (
    AppSettings,
    VillageMillingSettings,
    VillageRecruitmentSettings,
)
from src.core.browser_session import TravianBrowserSession
from src.interceptor.browser_cdp import ChromeCDPInterceptor
from src.database.connection import DatabaseManager
from src.database.models import Base
from src.database.repository import AccountRepository, VillageRepository
from src.core.account_manager import AccountManager
from src.core.village_tracker import VillageTracker
from src.core.recruitment_economics import estimate_recruitment_costs_per_hour


class BaselineSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_package_imports(self):
        import src.main
        import src.app

        self.assertTrue(hasattr(src.main, "main"))
        self.assertTrue(hasattr(src.app, "TravelAssistantApp"))

    def test_recruitment_settings_default_off_and_round_trip(self):
        settings = AppSettings()
        self.assertFalse(settings.recruitment.automatic_enabled)
        self.assertEqual(5, settings.recruitment.batch_size)
        self.assertEqual(1, settings.recruitment.queue_threshold)
        self.assertEqual(300, settings.resource_milling.minimum_gold_balance)
        self.assertFalse(
            VillageMillingSettings().enabled,
        )
        self.assertFalse(VillageMillingSettings().refill_enabled)
        self.assertEqual(
            5,
            VillageMillingSettings().refill_trigger_percent,
        )
        self.assertEqual(
            (100, 40, 30, 30),
            (
                VillageMillingSettings().trigger_percent,
                VillageMillingSettings().clay_percent,
                VillageMillingSettings().iron_percent,
                VillageMillingSettings().wood_percent,
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "settings.json")
            settings.recruitment.automatic_enabled = True
            settings.recruitment.batch_size = 8
            settings.recruitment.queue_threshold = 3
            settings.recruitment.pending_orders.add("101:Phalanx")
            settings.recruitment.villages["101"] = VillageRecruitmentSettings(
                enabled=False,
                units={"Phalanx"},
            )
            settings.resource_milling.villages["101"] = VillageMillingSettings(
                enabled=True,
                trigger_percent=85,
                refill_enabled=True,
                refill_trigger_percent=7,
                clay_percent=50,
                iron_percent=25,
                wood_percent=25,
            )
            settings.resource_milling.minimum_gold_balance = 450
            settings.resource_milling.pending_villages.add("202")
            settings.resource_milling.pending_refill_villages.add("202")
            settings.save(path)

            restored = AppSettings.load(path)
            self.assertTrue(restored.recruitment.automatic_enabled)
            self.assertEqual(8, restored.recruitment.batch_size)
            self.assertEqual(3, restored.recruitment.queue_threshold)
            self.assertEqual(
                {"101:Phalanx"},
                restored.recruitment.pending_orders,
            )
            self.assertFalse(restored.recruitment.villages["101"].enabled)
            self.assertEqual(
                {"21"},
                restored.recruitment.villages["101"].units,
            )
            milling = restored.resource_milling
            self.assertEqual(450, milling.minimum_gold_balance)
            self.assertTrue(milling.villages["101"].enabled)
            self.assertEqual(85, milling.villages["101"].trigger_percent)
            self.assertTrue(milling.villages["101"].refill_enabled)
            self.assertEqual(
                7,
                milling.villages["101"].refill_trigger_percent,
            )
            self.assertEqual(
                {"202"},
                milling.pending_villages,
            )
            self.assertEqual(
                {"202"},
                milling.pending_refill_villages,
            )

        with self.assertRaises(ValueError):
            AppSettings(recruitment={"batch_size": 0})
        with self.assertRaises(ValueError):
            AppSettings(recruitment={"queue_threshold": 101})
        with self.assertRaises(ValueError):
            VillageMillingSettings(trigger_percent=101)
        with self.assertRaises(ValueError):
            VillageMillingSettings(refill_trigger_percent=101)

    def test_database_manager_initializes_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "travian-test.db")
            db = DatabaseManager(db_path)
            db.init()

            self.assertIsNotNone(db.engine)
            self.assertIsNotNone(db.SessionLocal)
            self.assertTrue({"accounts", "villages", "resources", "troops"}.issubset(set(Base.metadata.tables.keys())))

            db.close()

    def test_database_backup_and_restore_preserve_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "backup-test.db"))
            db.init()
            repo = AccountRepository(db)
            repo.insert("accounts", {
                "account_id": "account-1",
                "username": "Before backup",
                "world": "Alpha",
                "server": "1",
            })

            backup = db.create_backup()
            self.assertTrue(backup.is_file())
            db.execute_raw(
                "UPDATE accounts SET username = :username WHERE account_id = :account_id",
                {"username": "Changed after backup", "account_id": "account-1"},
            )
            self.assertFalse(db.backup_is_due(24))

            db.restore_backup(backup)

            restored = repo.get_by_account_id("account-1")
            self.assertEqual("Before backup", restored["username"])
            self.assertIsNotNone(db.latest_backup())
            db.close()

    def test_database_manager_resolves_project_relative_path(self):
        db = DatabaseManager("./data/travian.db")
        self.assertTrue(Path(db.db_path).is_absolute())
        self.assertIn("travian-assistant", db.db_path)
        db.close()

    def test_live_state_sync_persists_village_resources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "travian-live.db")
            db = DatabaseManager(db_path)
            db.init()

            tracker = VillageTracker(VillageRepository(db))
            live_state = {
                "account_id": "acc-42",
                "account": "TestPlayer",
                "resources": {"wood": 120, "clay": 90, "iron": 80, "grain": 70},
                "capacities": {"wood": 300, "clay": 260, "iron": 220, "grain": 210},
                "production_rates": {"wood": 12.5, "clay": 8.0, "iron": 6.5, "grain": 7.5},
                "villages": [{
                    "id": "101",
                    "name": "Testonia",
                    "coordinates": (1, 2, 3),
                }],
                "selected_village_id": "101",
            }

            synced = tracker.sync_live_state(live_state)

            self.assertEqual(["101"], synced)
            self.assertIn("101", tracker._villages)
            self.assertEqual(120, tracker.get_village("101").wood.current_amount)
            self.assertEqual(300, tracker.get_village("101").wood.max_capacity)

            reloaded = VillageTracker(VillageRepository(db))
            reloaded.load_from_database()
            self.assertIn("101", reloaded._villages)
            self.assertEqual("Testonia", reloaded.get_village("101").name)

            db.close()

    def test_dashboard_loads_all_persisted_villages_and_resources_on_startup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "travian-dashboard.db")
            db = DatabaseManager(db_path)
            db.init()

            village_repo = VillageRepository(db)
            tracker = VillageTracker(village_repo)
            tracker.sync_live_state({
                "account_id": "acc-42",
                "account": "TestPlayer",
                "resources": {"wood": 120, "clay": 90, "iron": 80, "grain": 70},
                "capacities": {"wood": 300, "clay": 260, "iron": 220, "grain": 210},
                "production_rates": {"wood": 12, "clay": 8, "iron": 6, "grain": 7},
                "selected_village_id": "101",
                "villages": [{
                    "id": "101",
                    "name": "Testonia",
                    "coordinates": (1, 2, 3),
                }],
            })
            village_repo.save_dashboard_snapshot("101", "buildings", [{
                "name": "Main building",
                "slot": 15,
                "level": 8,
                "upgrading": True,
                "upgrade_complete_at": "2026-11-01T01:00:00",
            }])
            village_repo.save_dashboard_snapshot("101", "resource_fields", [{
                "name": "Woodcutter",
                "slot": 1,
                "level": 6,
            }, {
                "name": "Woodcutter",
                "slot": 2,
                "level": 4,
            }, {
                "name": "Cropland",
                "slot": 4,
                "level": 5,
            }])
            village_repo.save_dashboard_snapshot("101", "map_villages", [{
                "name": "Neighbour",
                "player": "OtherPlayer",
                "coordinates": "2/4",
                "distance": 2.24,
                "kingdom": "North",
            }])
            village_repo.save_dashboard_snapshot("101", "troops", [{
                "village_id": "101",
                "village": "Testonia",
                "unit": "Phalanx",
                "unit_id": "21",
                "count": 42,
            }, {
                "village_id": "101",
                "village": "Testonia",
                "unit": "Swordsman",
                "count": 8,
            }])
            for index in range(100):
                village_repo.upsert_village(
                    f"extra-{index}",
                    "acc-42",
                    f"Extra {index}",
                    (0, index, 0),
                )
            db.close()

            restarted_db = DatabaseManager(db_path)
            restarted_db.init()
            window = MainWindow(
                AppSettings(),
                AccountManager(AccountRepository(restarted_db)),
                VillageTracker(VillageRepository(restarted_db)),
            )
            try:
                self.assertEqual(101, window.village_list_widget.count())
                self.assertEqual(
                    3 * 60 * 1000,
                    window.recruitment_queue_timer.interval(),
                )
                self.assertFalse(
                    window.recruitment_tab.auto_recruitment_checkbox.isChecked()
                )
                self.assertEqual(
                    5,
                    window.recruitment_tab.auto_recruitment_batch_size.value(),
                )
                self.assertEqual(
                    1,
                    window.recruitment_tab.auto_recruitment_threshold.value(),
                )
                self.assertEqual(
                    "Rekrutacja",
                    window.content_tabs.tabText(1),
                )
                self.assertEqual(
                    "Szczegóły wioski",
                    window.content_tabs.tabText(2),
                )
                self.assertEqual("Mielenie", window.content_tabs.tabText(3))
                self.assertEqual(
                    300,
                    window.milling_tab.minimum_gold_balance.value(),
                )
                self.assertEqual(
                    30 * 1000,
                    window.resource_milling_timer.interval(),
                )
                window.dashboard_tab.update_persisted_village("101")
                window.village_detail_tab.update_village("101")
                self.assertEqual(
                    "120",
                    window.dashboard_tab.resource_card_by_name["wood"].amount_label.text(),
                )
                self.assertEqual(
                    40,
                    window.dashboard_tab.resource_card_by_name["wood"].progress_bar.value(),
                )
                self.assertEqual(
                    "12/h",
                    window.dashboard_tab.resource_card_by_name["wood"].rate_label.text(),
                )
                self.assertEqual("Main building", window.dashboard_tab.buildings_table.item(0, 0).text())
                self.assertEqual(
                    "2026-11-01T01:00:00",
                    window.dashboard_tab.buildings_table.item(0, 4).text(),
                )
                self.assertEqual(
                    "Testonia · 2 typów · 50 jednostek",
                    window.dashboard_tab.troops_table.item(0, 0).text(),
                )
                self.assertEqual(
                    "Phalanx", window.dashboard_tab.troops_table.item(1, 1).text().strip()
                )
                self.assertEqual(
                    "Woodcutter · 2 pól · średni poziom 5.0",
                    window.dashboard_tab.resource_fields_table.item(2, 0).text(),
                )
                self.assertTrue(
                    window.dashboard_tab.resource_fields_table.columnSpan(2, 0) == 3
                )
                self.assertEqual("Neighbour", window.dashboard_tab.map_table.item(0, 0).text())
                self.assertEqual("Testonia", window.village_detail_tab.name_label.text())
                self.assertEqual(4, len(window.dashboard_tab.resource_history_figure.axes[0].lines))
                self.assertIn("Phalanx", window.dashboard_tab.village_plan_label.text())
                self.assertIn(
                    "kolejka jeszcze nieodczytana",
                    window.dashboard_tab.village_plan_label.text(),
                )
                queue_rows = [
                    {
                        "village_id": "101",
                        "village": "Testonia",
                        "building": "Koszary",
                        "unit": "Phalanx",
                        "unit_id": "21",
                        "unit_type": 21,
                        "building_type": 19,
                        "building_available": True,
                        "costs": {
                            "wood": 20,
                            "clay": 30,
                            "iron": 40,
                            "grain": 10,
                        },
                        "training_times_seconds": [3600],
                        "count": 0,
                        "other_count": 0,
                    },
                    {
                        "village_id": "101",
                        "village": "Testonia",
                        "building": "Stajnia",
                        "unit": "Theutates Thunder",
                        "unit_id": "24",
                        "unit_type": 24,
                        "count": 2,
                        "other_count": 0,
                    },
                ]
                window._update_recruitment_queues(queue_rows)
                self.assertIn(
                    "brak aktywnej rekrutacji tego typu",
                    window.dashboard_tab.village_plan_label.text(),
                )
                self.assertIn(
                    "w kolejce: 2 szt.",
                    window.dashboard_tab.village_plan_label.text(),
                )
                self.assertEqual(
                    "Brak rekrutacji",
                    window.recruitment_tab.recruitment_queue_table.item(0, 3).text(),
                )
                self.assertEqual(
                    "Phalanx · Koszary",
                    window.recruitment_tab.recruitment_queue_table.item(0, 1).text(),
                )
                self.assertEqual(
                    "0 szt.",
                    window.recruitment_tab.recruitment_queue_table.item(0, 2).text(),
                )
                self.assertEqual(
                    window.village_list_widget.count(),
                    window.recruitment_tab.recruitment_economics_table.rowCount(),
                )
                self.assertEqual(
                    5,
                    window.recruitment_tab.recruitment_economics_table.columnCount(),
                )
                self.assertEqual(
                    "Drewno",
                    window.recruitment_tab.recruitment_economics_table.horizontalHeaderItem(1).text(),
                )
                self.assertIn(
                    "Przychód  +12/h",
                    window.recruitment_tab.recruitment_economics_table.item(0, 1).text(),
                )
                window.settings.recruitment.automatic_enabled = True
                window.settings.recruitment.villages["101"] = (
                    VillageRecruitmentSettings(units={"21"})
                )
                window._render_recruitment_economics(queue_rows)
                self.assertEqual(
                    True,
                    "Rekrutacja  20/h"
                    in window.recruitment_tab.recruitment_economics_table.item(0, 1).text(),
                )
                self.assertEqual(
                    True,
                    "Bilans  −8/h"
                    in window.recruitment_tab.recruitment_economics_table.item(0, 1).text(),
                )
                self.assertIn(
                    "bez rekrutacji",
                    window.recruitment_tab.recruitment_queue_status.text(),
                )
                window._update_recruitment_queues(
                    window.dashboard_tab._recruitment_queue_rows,
                    {"101:Phalanx"},
                )
                self.assertIn(
                    "potwierdzanie",
                    window.recruitment_tab.recruitment_queue_table.item(0, 3).text(),
                )
                self.assertTrue(
                    window.recruitment_tab.clear_recruitment_pending_btn.isEnabled()
                )
                window.dashboard_tab.refresh_resource_history("101")
                self.assertEqual(1, len(window.dashboard_tab.resource_history_figure.axes))
            finally:
                window.close()
                restarted_db.close()

    def test_live_resource_sync_only_updates_selected_village(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "travian-selected-village.db"))
            db.init()
            tracker = VillageTracker(VillageRepository(db))
            tracker.sync_live_state({
                "account_id": "acc-42",
                "selected_village_id": "101",
                "resources": {"wood": 120},
                "capacities": {"wood": 300},
                "production_rates": {"wood": 12},
                "villages": [
                    {"id": "101", "name": "Selected"},
                    {"id": "202", "name": "Other"},
                ],
            })

            self.assertEqual(120, tracker.get_village("101").wood.current_amount)
            self.assertEqual(0, tracker.get_village("202").wood.current_amount)
            db.close()

    def test_captured_api_response_is_parsed_normalized_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "travian-api.db"))
            db.init()
            tracker = VillageTracker(VillageRepository(db))
            tracker.add_village("101", "acc-42", "Testonia")
            tracker.sync_live_state({"selected_village_id": "101", "villages": []})
            processed_resources = tracker.process_network_response(
                "https://ts1.x1.europe.traviangames.com/getResources.php",
                {
                    "villageId": "101",
                    "resources": {
                        "wood": 120,
                        "clay": 90,
                        "iron": 80,
                        "crop": 70,
                    },
                    "storage_max": 300,
                    "buildings": {"lumber": 3},
                },
            )

            processed = tracker.process_network_response(
                "https://ts1.x1.europe.traviangames.com/getBuildings.php",
                {
                    "villageId": "101",
                    "buildings": {
                        "15": {
                            "level": 8,
                            "maxLevel": 20,
                            "isUpgrade": True,
                            "endTime": int(time.time()) + 3600,
                        },
                    },
                },
            )

            try:
                self.assertTrue(processed_resources)
                self.assertTrue(processed)
                snapshots = VillageRepository(db).get_dashboard_snapshots("101")
                self.assertEqual(8, snapshots["api_buildings"]["buildings"]["15"]["level"])
                self.assertEqual("Main building", snapshots["buildings"][0]["name"])
                self.assertTrue(snapshots["buildings"][0]["upgrading"])
                self.assertIsNotNone(
                    snapshots["buildings"][0]["upgrade_complete_at"]
                )
                self.assertGreater(
                    snapshots["api_buildings"]["buildings"]["15"][
                        "upgrade_duration_seconds"
                    ],
                    0,
                )
                self.assertEqual(
                    8,
                    snapshots["buildings"][0]["level"],
                )
                reloaded = VillageTracker(VillageRepository(db))
                reloaded.load_from_database()
                self.assertEqual(120, reloaded.get_village("101").wood.current_amount)
                self.assertEqual(300, reloaded.get_village("101").wood.max_capacity)
            finally:
                db.close()

    def test_browser_session_forwards_decoded_captured_api_response(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.asserted_method = method
                return {
                    "body": base64.b64encode(
                        json.dumps({"villageId": "101", "buildings": {}}).encode()
                    ).decode(),
                    "base64Encoded": True,
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "travian-cdp-response.db"))
            db.init()
            tracker = VillageTracker(VillageRepository(db))
            tracker.add_village("101", "acc-42", "Testonia")
            session = TravianBrowserSession("127.0.0.1", 9222)
            session._tab = FakeTab()
            session.set_network_response_handler(tracker.process_network_response)
            session._on_network_request_sent(
                requestId="request-1",
                request={
                    "url": "https://ts1.x1.europe.traviangames.com/getBuildings.php",
                    "method": "GET",
                },
            )
            session._on_network_response_received(
                requestId="request-1",
                response={"status": 200, "mimeType": "application/json"},
            )
            session._on_network_loading_finished(requestId="request-1")

            self.assertEqual(
                "Network.getResponseBody",
                session._tab.asserted_method,
            )
            self.assertIn(
                "api_buildings",
                VillageRepository(db).get_dashboard_snapshots("101"),
            )
            db.close()

    def test_reads_target_recruitment_queue_for_each_village(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.method = method
                self.expression = kwargs["expression"]
                return {
                    "result": {
                        "value": [
                            {
                                "village_id": "101",
                                "tribe_id": 3,
                                "building_type": 19,
                                "building_types": [19],
                                "building": "Koszary",
                                "unit_type": 21,
                                "unit": "Phalanx",
                                "costs": {
                                    "1": 100,
                                    "2": 130,
                                    "3": 55,
                                    "4": 30,
                                },
                                "training_times_seconds": [1800],
                                "count": 0,
                                "other_count": 0,
                                "building_available": True,
                            },
                            {
                                "village_id": "101",
                                "tribe_id": 3,
                                "building_type": 20,
                                "building_types": [20],
                                "building": "Stajnia",
                                "unit_type": 24,
                                "unit": "Theutates Thunder",
                                "count": 3,
                                "other_count": 0,
                                "building_available": True,
                            },
                            {
                                "village_id": "202",
                                "tribe_id": 3,
                                "building_type": 19,
                                "building_types": [19],
                                "building": "Koszary",
                                "unit_type": 21,
                                "unit": "Phalanx",
                                "count": 5,
                                "other_count": 0,
                                "building_available": True,
                            },
                            {
                                "village_id": "202",
                                "tribe_id": 3,
                                "building_type": 20,
                                "building_types": [20],
                                "building": "Stajnia",
                                "unit_type": 24,
                                "unit": "Theutates Thunder",
                                "count": 0,
                                "other_count": 1,
                                "building_available": False,
                            },
                            {
                                "village_id": "303",
                                "tribe_id": 4,
                                "building_type": 20,
                                "building_types": [20],
                                "building": "Stajnia",
                                "unit_type": 34,
                                "unit": "Sopdu Explorer",
                                "count": 2,
                                "other_count": 0,
                                "building_available": True,
                            },
                        ]
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        rows = session.read_recruitment_queues([
            {"id": "101", "name": "Testonia"},
            {"id": "202", "name": "Secondonia"},
            {"id": "303", "name": "Thirdonia"},
        ])

        self.assertEqual("Runtime.evaluate", session._tab.method)
        self.assertIn("UnitQueue.get(villageId)", session._tab.expression)
        self.assertIn("Travian.Config.troopConfig", session._tab.expression)
        self.assertIn("Object.entries(troopConfig)", session._tab.expression)
        self.assertIn('"21": "Phalanx"', session._tab.expression)
        self.assertIn('"34": "Sopdu Explorer"', session._tab.expression)
        self.assertIn('"44": "Steppe Rider"', session._tab.expression)
        self.assertIn("building_available", session._tab.expression)
        self.assertIn("troop.time", session._tab.expression)
        self.assertIn("Travian.Config.TG_TROOP_SPEED", session._tab.expression)
        self.assertIn("currentEffect.values[0]", session._tab.expression)
        self.assertEqual(
            [
                ("Testonia", "Koszary", "Phalanx", 0),
                ("Testonia", "Stajnia", "Theutates Thunder", 3),
                ("Secondonia", "Koszary", "Phalanx", 5),
                ("Secondonia", "Stajnia", "Theutates Thunder", 0),
                ("Thirdonia", "Stajnia", "Sopdu Explorer", 2),
            ],
            [
                (row["village"], row["building"], row["unit"], row["count"])
                for row in rows
            ],
        )
        self.assertEqual(
            (19, 21),
            (
                rows[0]["building_type"],
                rows[0]["unit_type"],
            ),
        )
        self.assertFalse(rows[3]["building_available"])
        self.assertEqual("21", rows[0]["unit_id"])
        self.assertEqual(
            {"wood": 100.0, "clay": 130.0, "iron": 55.0, "grain": 30.0},
            rows[0]["costs"],
        )
        self.assertEqual([1800.0], rows[0]["training_times_seconds"])

    def test_estimates_continuous_recruitment_costs_per_village(self):
        rows = [
            {
                "village_id": "101",
                "unit_id": "21",
                "building_type": 19,
                "building_available": True,
                "costs": {
                    "wood": 100,
                    "clay": 130,
                    "iron": 55,
                    "grain": 30,
                },
                "training_times_seconds": [1800],
            },
            {
                "village_id": "101",
                "unit_id": "24",
                "building_type": 20,
                "building_available": True,
                "costs": {
                    "wood": 350,
                    "clay": 250,
                    "iron": 150,
                    "grain": 100,
                },
                "training_times_seconds": [3600],
            },
            {
                "village_id": "202",
                "unit_id": "21",
                "building_type": 19,
                "building_available": True,
                "costs": {
                    "wood": 100,
                    "clay": 130,
                    "iron": 55,
                    "grain": 30,
                },
                "training_times_seconds": [None],
            },
        ]
        settings = {
            "101": VillageRecruitmentSettings(units={"21", "24"}),
            "202": VillageRecruitmentSettings(units={"21"}),
        }

        costs = estimate_recruitment_costs_per_hour(rows, settings, True)

        self.assertEqual(
            {"wood": 550.0, "clay": 510.0, "iron": 260.0, "grain": 160.0},
            costs["101"],
        )
        self.assertEqual(
            {"wood": None, "clay": None, "iron": None, "grain": None},
            costs["202"],
        )

    def test_shared_building_types_are_not_counted_as_parallel_training(self):
        rows = [
            {
                "village_id": "101",
                "unit_id": "21",
                "building_type": 19,
                "building_available": True,
                "costs": {
                    "wood": 100,
                    "clay": 130,
                    "iron": 55,
                    "grain": 30,
                },
                "training_times_seconds": [1800],
            },
            {
                "village_id": "101",
                "unit_id": "22",
                "building_type": 19,
                "building_available": True,
                "costs": {
                    "wood": 70,
                    "clay": 90,
                    "iron": 50,
                    "grain": 30,
                },
                "training_times_seconds": [1200],
            },
        ]
        settings = {
            "101": VillageRecruitmentSettings(units={"21", "22"}),
        }

        costs = estimate_recruitment_costs_per_hour(rows, settings, True)

        self.assertEqual(
            {"wood": None, "clay": None, "iron": None, "grain": None},
            costs["101"],
        )

    def test_sums_training_rates_from_standard_and_large_buildings(self):
        rows = [{
            "village_id": "101",
            "unit_id": "21",
            "building_type": 19,
            "building_types": [19, 29],
            "building_available": True,
            "costs": {
                "wood": 100,
                "clay": 0,
                "iron": 0,
                "grain": 0,
            },
            "training_times_seconds": [1800, 900],
        }]
        settings = {
            "101": VillageRecruitmentSettings(units={"21"}),
        }

        costs = estimate_recruitment_costs_per_hour(rows, settings, True)

        self.assertEqual(600.0, costs["101"]["wood"])

    def test_disabled_automation_has_no_recruitment_cost(self):
        costs = estimate_recruitment_costs_per_hour(
            [{
                "village_id": "101",
                "unit_id": "21",
                "building_available": True,
                "costs": {},
                "training_times_seconds": None,
            }],
            {"101": VillageRecruitmentSettings(units={"21"})},
            False,
        )

        self.assertEqual(
            {"wood": 0.0, "clay": 0.0, "iron": 0.0, "grain": 0.0},
            costs["101"],
        )

    def test_reads_resource_milling_state_and_gold_per_village(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.method = method
                self.expression = kwargs["expression"]
                return {
                    "result": {
                        "value": [{
                            "village_id": "101",
                            "resources": {
                                "wood": 30000,
                                "clay": 40000,
                                "iron": 30000,
                                "grain": 100000,
                            },
                            "capacities": {
                                "wood": 100000,
                                "clay": 100000,
                                "iron": 100000,
                                "grain": 100000,
                            },
                            "gold": 305,
                        }]
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        rows = session.read_resource_milling_state([
            {"id": "101", "name": "Testonia"},
        ])

        self.assertEqual("Runtime.evaluate", session._tab.method)
        self.assertIn("Village.get(villageId)", session._tab.expression)
        self.assertIn("Player.get(playerId)", session._tab.expression)
        self.assertEqual(100000.0, rows[0]["resources"]["grain"])
        self.assertEqual(100000.0, rows[0]["capacities"]["grain"])
        self.assertEqual(305.0, rows[0]["gold"])

    def test_queue_resource_milling_uses_npc_trader_request_contract(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.method = method
                self.expression = kwargs["expression"]
                request_id = re.findall(r'"([a-f0-9]{32})"', self.expression)[-1]
                return {
                    "result": {
                        "value": {
                            "status": "submitted",
                            "request_id": request_id,
                            "target_grain": 0,
                        }
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        result = session.queue_resource_milling(
            "101",
            {
                "trigger_percent": 100,
                "clay_percent": 40,
                "iron_percent": 30,
                "wood_percent": 30,
            },
            300,
        )

        self.assertEqual("Runtime.evaluate", session._tab.method)
        self.assertIn('"premiumFeature/bookFeature"', session._tab.expression)
        self.assertIn('"NPCTrader"', session._tab.expression)
        self.assertIn("distributeRes", session._tab.expression)
        self.assertIn("minimumGoldBalance + traderPrice", session._tab.expression)
        self.assertIn("grainPercent < activeThreshold", session._tab.expression)
        self.assertIn('storageCapacity["4"]', session._tab.expression)
        self.assertIn("price: traderPrice", session._tab.expression)
        self.assertIn(", 300, 5,", session._tab.expression)
        self.assertEqual("submitted", result["status"])
        self.assertRegex(result["request_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(0, result["target_grain"])

    def test_queue_resource_milling_refills_granary_and_preserves_surplus(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.expression = kwargs["expression"]
                request_id = re.findall(r'"([a-f0-9]{32})"', self.expression)[-1]
                return {
                    "result": {
                        "value": {
                            "status": "submitted",
                            "request_id": request_id,
                            "target_grain": 100000,
                        }
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        result = session.queue_resource_milling(
            "101",
            {
                "mode": "refill",
                "trigger_percent": 100,
                "refill_trigger_percent": 5,
                "clay_percent": 40,
                "iron_percent": 30,
                "wood_percent": 30,
            },
            300,
        )

        self.assertIn('"refill"', session._tab.expression)
        self.assertIn("grainPercent > activeThreshold", session._tab.expression)
        self.assertIn("Math.min(Math.floor(granaryCapacity), total)", session._tab.expression)
        self.assertIn("const remainder = total - targetGrain", session._tab.expression)
        self.assertEqual(100000, result["target_grain"])

    def test_queue_resource_milling_rejects_non_100_percent_split(self):
        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = object()

        with self.assertRaisesRegex(ValueError, "distribution"):
            session.queue_resource_milling(
                "101",
                {
                    "trigger_percent": 100,
                    "clay_percent": 40,
                    "iron_percent": 30,
                    "wood_percent": 20,
                },
                300,
            )

    def test_reads_confirmed_resource_milling_response(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.method = method
                self.expression = kwargs["expression"]
                return {
                    "result": {
                        "value": {
                            "status": "confirmed",
                            "resources": {
                                "wood": 30000,
                                "clay": 40000,
                                "iron": 30000,
                                "grain": 2000,
                            },
                            "grain_capacity": 100000,
                            "target_grain": 0,
                            "gold": 300,
                        }
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        result = session.read_resource_milling_response(
            "a" * 32,
            "empty",
            90,
            0,
        )

        self.assertIn(
            "__travianAssistantMillingResponse_",
            session._tab.expression,
        )
        self.assertIn("triggerPercent", session._tab.expression)
        self.assertIn(
            "amounts[3] / capacity * 100 < triggerPercent",
            session._tab.expression,
        )
        self.assertEqual("confirmed", result["status"])
        self.assertEqual(2000, result["resources"]["grain"])
        self.assertEqual(100000, result["grain_capacity"])
        self.assertEqual(300, result["gold"])

    def test_reads_confirmed_granary_refill_response(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.expression = kwargs["expression"]
                return {
                    "result": {
                        "value": {
                            "status": "confirmed",
                            "resources": {
                                "wood": 10000,
                                "clay": 10000,
                                "iron": 10000,
                                "grain": 100000,
                            },
                            "grain_capacity": 100000,
                            "target_grain": 100000,
                            "gold": 300,
                        }
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        result = session.read_resource_milling_response(
            "b" * 32,
            "refill",
            5,
            100000,
        )

        self.assertIn('mode === "empty"', session._tab.expression)
        self.assertIn("amounts[3] >= targetGrain - 1", session._tab.expression)
        self.assertEqual("confirmed", result["status"])
        self.assertEqual(100000, result["resources"]["grain"])

    def test_queue_recruitment_uses_live_checked_game_request(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.method = method
                self.expression = kwargs["expression"]
                self.arguments = kwargs
                request_id = re.findall(r'"([a-f0-9]{32})"', self.expression)[-1]
                return {
                    "result": {
                        "value": {
                            "status": "submitted",
                            "request_id": request_id,
                        }
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        outcome = session.queue_recruitment(
            {
                "village_id": "101",
                "building_type": 19,
                "unit_type": 21,
                "unit": "Phalanx",
            },
            5,
            3,
        )

        self.assertEqual("Runtime.evaluate", session._tab.method)
        self.assertTrue(session._tab.arguments["returnByValue"])
        self.assertIn("queue.data.buildingTypes", session._tab.expression)
        self.assertIn("insufficient_resources", session._tab.expression)
        self.assertIn("village.getResources()", session._tab.expression)
        self.assertIn('"building/recruitUnits"', session._tab.expression)
        self.assertIn("Travian.writeRequest", session._tab.expression)
        self.assertIn('"101"', session._tab.expression)
        self.assertIn("19, [19], 21, 5, 3", session._tab.expression)
        self.assertIn("count > queueThreshold", session._tab.expression)
        self.assertIn("response.cache", session._tab.expression)
        self.assertIn("UnitQueue:${villageId}", session._tab.expression)
        self.assertEqual("submitted", outcome["status"])
        self.assertRegex(outcome["request_id"], r"^[a-f0-9]{32}$")
        self.assertEqual("Phalanx", outcome["unit"])

    def test_queue_recruitment_accepts_detected_unit_ids_from_other_tribes(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.expression = kwargs["expression"]
                request_id = re.findall(r'"([a-f0-9]{32})"', self.expression)[-1]
                return {
                    "result": {
                        "value": {
                            "status": "submitted",
                            "request_id": request_id,
                        }
                    }
                }

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        outcome = session.queue_recruitment(
            {
                "village_id": "303",
                "building_type": 20,
                "building_types": [20],
                "unit_type": 34,
                "unit": "Sopdu Explorer",
            },
            5,
            1,
        )

        self.assertIn("20, [20], 34, 5, 1", session._tab.expression)
        self.assertEqual("Sopdu Explorer", outcome["unit"])
        self.assertEqual("submitted", outcome["status"])

    def test_reads_async_recruitment_response_status(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.method = method
                self.expression = kwargs["expression"]
                return {"result": {"value": {"status": "confirmed", "count": 6}}}

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        result = session.read_recruitment_order_response("a" * 32, 1)

        self.assertEqual("Runtime.evaluate", session._tab.method)
        self.assertIn(
            "__travianAssistantRecruitmentResponse_",
            session._tab.expression,
        )
        self.assertIn("response.count > queueThreshold", session._tab.expression)
        self.assertEqual({"status": "confirmed", "count": 6}, result)

    def test_confirmed_automatic_recruitment_updates_queue_and_clears_pending(self):
        class FakeSession:
            def read_recruitment_order_response(
                self,
                request_id,
                queue_threshold,
            ):
                self.request_id = request_id
                self.queue_threshold = queue_threshold
                return {"status": "confirmed", "count": 6}

            def disconnect(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "confirmed-order.db"))
            db.init()
            settings = AppSettings()
            key = "101:21"
            settings.recruitment.pending_orders.add(key)
            window = MainWindow(
                settings,
                AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            window.browser_session = FakeSession()
            window._pending_recruitment_responses[key] = 0
            row = {
                "village_id": "101",
                "village": "Testonia",
                "building": "Koszary",
                "unit": "Phalanx",
                "building_type": 19,
                "unit_type": 21,
                "count": 0,
                "other_count": 0,
            }
            window.dashboard_tab._recruitment_queue_rows = [row]

            try:
                with patch.object(AppSettings, "save", return_value=None):
                    window._verify_automatic_recruitment_response(
                        key,
                        "a" * 32,
                        row,
                    )

                self.assertNotIn(key, settings.recruitment.pending_orders)
                self.assertNotIn(key, window._pending_recruitment_responses)
                self.assertEqual(
                    6,
                    window.dashboard_tab._recruitment_queue_counts[
                        ("101", "Phalanx")
                    ][0],
                )
                self.assertIn(
                    "Potwierdzono",
                    window.recruitment_tab.recruitment_queue_status.text(),
                )
            finally:
                window.close()
                db.close()

    def test_unconfirmed_recruitment_response_without_count_is_reported(self):
        class FakeSession:
            def read_recruitment_order_response(
                self,
                request_id,
                queue_threshold,
            ):
                return {"status": "unconfirmed"}

            def disconnect(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "missing-count-order.db"))
            db.init()
            settings = AppSettings()
            key = "101:24"
            settings.recruitment.pending_orders.add(key)
            window = MainWindow(
                settings,
                AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            window.browser_session = FakeSession()
            window._pending_recruitment_responses[key] = 0
            row = {
                "village_id": "101",
                "village": "Giran",
                "unit": "Theutates Thunder",
            }

            try:
                with patch.object(AppSettings, "save", return_value=None):
                    window._verify_automatic_recruitment_response(
                        key,
                        "b" * 32,
                        row,
                    )

                self.assertIn(key, settings.recruitment.pending_orders)
                self.assertIn(
                    "brak danych",
                    window.recruitment_tab.recruitment_queue_status.text(),
                )
            finally:
                window.close()
                db.close()

    def test_queue_recruitment_rejects_mismatched_unit_and_building(self):
        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = object()

        with self.assertRaisesRegex(ValueError, "Invalid automatic"):
            session.queue_recruitment(
                {
                    "village_id": "101",
                    "building_type": 999,
                    "unit_type": 21,
                },
                5,
            )

    def test_auto_recruitment_only_queues_low_target_queue(self):
        class FakeSession:
            def __init__(self):
                self.queued = []

            def read_recruitment_queues(self, villages):
                return [
                    {
                        "village_id": "101",
                        "village": "Testonia",
                        "building": "Koszary",
                        "unit": "Phalanx",
                        "unit_id": "21",
                        "building_type": 19,
                        "building_types": [19],
                        "unit_type": 21,
                        "count": 1,
                        "other_count": 0,
                        "building_available": True,
                    },
                    {
                        "village_id": "101",
                        "village": "Testonia",
                        "building": "Stajnia",
                        "unit": "Theutates Thunder",
                        "unit_id": "24",
                        "building_type": 20,
                        "building_types": [20],
                        "unit_type": 24,
                        "count": 2,
                        "other_count": 0,
                        "building_available": True,
                    },
                ]

            def queue_recruitment(self, row, batch_size, queue_threshold):
                self.queued.append(
                    (row["unit"], batch_size, queue_threshold)
                )
                return {"status": "submitted", "request_id": "a" * 32}

            def read_recruitment_order_response(
                self,
                request_id,
                queue_threshold,
            ):
                return {"status": "pending"}

            def disconnect(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "auto-recruitment.db"))
            db.init()
            settings = AppSettings()
            settings.recruitment.automatic_enabled = True
            settings.recruitment.batch_size = 5
            settings.recruitment.queue_threshold = 2
            settings.recruitment.villages["101"] = (
                VillageRecruitmentSettings(
                    enabled=True,
                    units={"24"},
                )
            )
            window = MainWindow(
                settings,
                AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            fake_session = FakeSession()
            window.browser_session = fake_session
            window.dashboard_tab._live_state = {
                "villages": [{"id": "101", "name": "Testonia"}],
            }
            try:
                with patch.object(AppSettings, "save", return_value=None):
                    window._check_recruitment_queues()
                    self.qt_app.processEvents()

                self.assertEqual(
                    [("Theutates Thunder", 5, 2)],
                    fake_session.queued,
                )
                self.assertIn(
                    "101:24",
                    settings.recruitment.pending_orders,
                )
                self.assertNotIn(
                    "101:21",
                    settings.recruitment.pending_orders,
                )
            finally:
                window.close()
                db.close()

    def test_auto_recruitment_skips_unavailable_buildings_per_village(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "village-recruitment.db"))
            db.init()
            settings = AppSettings()
            settings.recruitment.automatic_enabled = True
            settings.recruitment.villages["303"] = (
                VillageRecruitmentSettings(units={"21", "34"})
            )
            window = MainWindow(
                settings,
                AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            rows = [
                {
                    "village_id": "303",
                    "unit": "Phalanx",
                    "unit_id": "21",
                    "count": 0,
                    "building_available": True,
                },
                {
                    "village_id": "303",
                    "unit": "Theutates Thunder",
                    "unit_id": "24",
                    "count": 0,
                    "building_available": False,
                },
                {
                    "village_id": "303",
                    "unit": "Sopdu Explorer",
                    "unit_id": "34",
                    "count": 0,
                    "building_available": True,
                },
            ]

            try:
                window._begin_automatic_recruitment(rows)
                self.assertEqual(["Phalanx", "Sopdu Explorer"], [
                    row["unit"]
                    for row in window._auto_recruitment_candidates
                ])
            finally:
                window.close()
                db.close()

    def test_resource_milling_respects_global_gold_reserve_before_order(self):
        class FakeSession:
            def __init__(self):
                self.gold = 304
                self.milling_requests = []

            def read_resource_milling_state(self, villages):
                return [{
                    "village_id": "101",
                    "resources": {
                        "wood": 0,
                        "clay": 0,
                        "iron": 0,
                        "grain": 100000,
                    },
                    "capacities": {
                        "wood": 100000,
                        "clay": 100000,
                        "iron": 100000,
                        "grain": 100000,
                    },
                    "gold": self.gold,
                }]

            def queue_resource_milling(
                self,
                village_id,
                settings,
                minimum_gold_balance,
            ):
                self.milling_requests.append(
                    (village_id, settings, minimum_gold_balance)
                )
                return {
                    "status": "submitted",
                    "request_id": "c" * 32,
                    "target_grain": 0,
                }

            def disconnect(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "milling-reserve.db"))
            db.init()
            settings = AppSettings()
            settings.resource_milling.villages["101"] = (
                VillageMillingSettings(enabled=True)
            )
            window = MainWindow(
                settings,
                AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            fake_session = FakeSession()
            window.browser_session = fake_session
            window.dashboard_tab._live_state = {
                "villages": [{"id": "101", "name": "Testonia"}],
            }

            try:
                with patch.object(AppSettings, "save", return_value=None):
                    window._check_resource_milling()
                    self.assertEqual([], fake_session.milling_requests)
                    self.assertIn(
                        "rezerwy złota",
                        window.milling_tab.status.text(),
                    )

                    fake_session.gold = 305
                    window._check_resource_milling()
                    self.qt_app.processEvents()

                self.assertEqual(1, len(fake_session.milling_requests))
                self.assertEqual(
                    ("101", 300),
                    (
                        fake_session.milling_requests[0][0],
                        fake_session.milling_requests[0][2],
                    ),
                )
                self.assertIn(
                    "101",
                    settings.resource_milling.pending_villages,
                )
            finally:
                window.close()
                db.close()

    def test_resource_milling_selects_refill_automation_per_village(self):
        class FakeSession:
            def __init__(self):
                self.milling_requests = []

            def read_resource_milling_state(self, villages):
                return [{
                    "village_id": "101",
                    "resources": {
                        "wood": 40000,
                        "clay": 40000,
                        "iron": 40000,
                        "grain": 5000,
                    },
                    "capacities": {
                        "wood": 100000,
                        "clay": 100000,
                        "iron": 100000,
                        "grain": 100000,
                    },
                    "gold": 305,
                }]

            def queue_resource_milling(
                self,
                village_id,
                settings,
                minimum_gold_balance,
            ):
                self.milling_requests.append(
                    (village_id, settings, minimum_gold_balance)
                )
                return {
                    "status": "submitted",
                    "request_id": "d" * 32,
                    "target_grain": 100000,
                }

            def disconnect(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(str(Path(tmpdir) / "milling-refill.db"))
            db.init()
            settings = AppSettings()
            settings.resource_milling.villages["101"] = (
                VillageMillingSettings(refill_enabled=True)
            )
            window = MainWindow(
                settings,
                AccountManager(AccountRepository(db)),
                VillageTracker(VillageRepository(db)),
            )
            fake_session = FakeSession()
            window.browser_session = fake_session
            window.dashboard_tab._live_state = {
                "villages": [{"id": "101", "name": "Testonia"}],
            }

            try:
                with patch.object(AppSettings, "save", return_value=None):
                    window._check_resource_milling()
                    self.qt_app.processEvents()

                self.assertEqual(1, len(fake_session.milling_requests))
                village_id, request_settings, reserve = (
                    fake_session.milling_requests[0]
                )
                self.assertEqual("101", village_id)
                self.assertEqual("refill", request_settings["mode"])
                self.assertEqual(
                    5,
                    request_settings["refill_trigger_percent"],
                )
                self.assertEqual(300, reserve)
                self.assertTrue(
                    window.milling_tab.village_controls["101"][
                        "refill_enabled"
                    ].isChecked()
                )
                self.assertEqual(
                    5,
                    window.milling_tab.village_controls["101"][
                        "refill_trigger_percent"
                    ].value(),
                )
                self.assertIn(
                    "101",
                    settings.resource_milling.pending_refill_villages,
                )
            finally:
                window.close()
                db.close()

    def test_rejects_incomplete_recruitment_queue_scan(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                return {"result": {"value": []}}

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            session.read_recruitment_queues([{"id": "101", "name": "Testonia"}])

    def test_network_interceptor_can_be_initialized_before_connecting(self):
        interceptor = ChromeCDPInterceptor()
        self.assertIsNone(interceptor._session)

    def test_account_manager_persists_and_reloads_accounts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "travian-accounts.db")
            db = DatabaseManager(db_path)
            db.init()

            repo = AccountRepository(db)
            manager = AccountManager(repo)
            manager.add_account("player-1", "Alice", "Alpha", "1", "Legion")

            reloaded = AccountManager(AccountRepository(db))
            reloaded.load_from_database()
            account = reloaded.get_account("player-1")

            self.assertIsNotNone(account)
            self.assertEqual("Alice", account.username)
            self.assertEqual("Alpha", account.world)
            self.assertEqual("Legion", account.alliance)

            db.close()


if __name__ == "__main__":
    unittest.main()
