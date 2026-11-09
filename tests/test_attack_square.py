"""Attacker tournament square estimate and multi-delete in the attacks tab."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.tournament_square import (
    ROBBER_TEXT,
    describe,
    estimate_levels,
    travel_seconds,
)


class EstimateTests(unittest.TestCase):
    def test_travel_formula_matches_game_durations(self):
        # Phalanx 14 fields/h over 3.6056 fields took 927 s in the game.
        self.assertEqual(927, int(travel_seconds(3.605551275463989, 14, 0)))
        self.assertEqual(13500, round(travel_seconds(40, 8, 10)))

    def test_finds_level_for_units_that_fit(self):
        speeds = {1: 14.0, 7: 8.0}
        self.assertEqual({10: [7]}, estimate_levels(40, 13500, speeds))
        # A few seconds of detection delay still gives the same level.
        self.assertEqual({10: [7]}, estimate_levels(40, 13497, speeds))

    def test_short_distance_and_robbers(self):
        names = {7: "Taran"}
        self.assertEqual("bez wpływu (≤20 pól)", describe(12, 5000, {7: 8.0}, names))
        # Within 20 fields the time names the slowest unit exactly:
        # ram 8 fields/h, catapult 6 fields/h over 12 fields.
        siege_names = {7: "Taran", 8: "Katapulta"}
        speeds = {7: 8.0, 8: 6.0}
        self.assertEqual("bez wpływu (≤20 pól); najwolniejsze: Taran",
                         describe(12, 5400, speeds, siege_names))
        self.assertEqual("bez wpływu (≤20 pól); najwolniejsze: Katapulta",
                         describe(12, 7202, speeds, siege_names))
        self.assertEqual(ROBBER_TEXT, describe(None, None, {}, {}, robber=True))
        self.assertEqual("—", describe(40, None, {7: 8.0}, names))
        self.assertEqual("10 (Taran)", describe(40, 13500, {7: 8.0}, names))


class AttacksTabTests(unittest.TestCase):
    def make_window(self, tmpdir):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from src.app import MainWindow
        from src.config.settings import AppSettings
        from src.core.account_manager import AccountManager
        from src.core.village_tracker import VillageTracker
        from src.database.connection import DatabaseManager
        from src.database.repository import (
            AccountRepository, IncomingAttackRepository, VillageRepository,
        )

        QApplication.instance() or QApplication([])
        db = DatabaseManager(str(Path(tmpdir) / "attacks.db"))
        db.init()
        repo = IncomingAttackRepository(db)
        window = MainWindow(
            AppSettings(), AccountManager(AccountRepository(db)),
            VillageTracker(VillageRepository(db)), repo,
        )
        window._unit_speeds = {2: {1: 14.0, 7: 8.0}}
        return window, repo, db

    @staticmethod
    def attack(movement_id, arrival, **extra):
        return {
            "movement_id": movement_id, "village_id": "101", "village_name": "Aden",
            "attacker_name": "BIKO", "source_village_id": "202",
            "source_village": "AKACJA", "arrival_time": arrival,
            "seconds_remaining": arrival - 1000, "movement_type": "Atak",
            "masked": True, "time_start": None, "tribe_id": 2, "robber": False,
            "distance": 40.0, **extra,
        }

    def test_square_level_shown_next_to_arrival_and_multi_delete(self):
        from PySide6.QtWidgets import QMessageBox

        with tempfile.TemporaryDirectory() as tmpdir:
            window, repo, db = self.make_window(tmpdir)
            try:
                with patch("src.app.QMessageBox.warning"):
                    # Baseline snapshot: already on its way, cannot be timed.
                    window._on_incoming_attack_update({
                        "village_id": "101", "server_time": 1000,
                        "attacks": [self.attack("1", 20000)],
                    })
                    # Live detection: sent now, lands after 13500 s.
                    window._on_incoming_attack_update({
                        "village_id": "101", "server_time": 1000,
                        "attacks": [self.attack("1", 20000), self.attack("2", 14500)],
                    })
                    window._on_incoming_attack_update({
                        "village_id": "101", "server_time": 1001,
                        "attacks": [
                            self.attack("1", 20000), self.attack("2", 14500),
                            self.attack("3", 9000, robber=True, attacker_name="Robber",
                                        source_village_id=None, source_x=2, source_y=36),
                        ],
                    })
                table = window.incoming_attacks_tab.table
                self.assertEqual("Koordynaty", table.horizontalHeaderItem(3).text())
                self.assertEqual("Plac turniejowy", table.horizontalHeaderItem(6).text())
                squares = {table.item(row, 5).text()[-8:]: table.item(row, 6).text()
                           for row in range(table.rowCount())}
                coordinates = {table.item(row, 3).text() for row in range(table.rowCount())}
                self.assertIn("(2|36)", coordinates)     # from the game
                self.assertIn("(-16182|-16384)", coordinates)  # from village id 202
                from PySide6.QtWidgets import QHeaderView
                self.assertEqual(
                    QHeaderView.Interactive,
                    table.horizontalHeader().sectionResizeMode(6),
                )
                values = sorted(squares.values())
                self.assertIn("10 (Taran)", values)
                self.assertIn(ROBBER_TEXT, values)
                self.assertIn("—", values)
                stored = {r["movement_id"]: r["details"].get("tournament_square")
                          for r in repo.get_recent_attacks()}
                self.assertEqual("10 (Taran)", stored["2"])

                # The estimate survives later snapshots without the field.
                window._square_estimates.clear()
                with patch("src.app.QMessageBox.warning"):
                    window._on_incoming_attack_update({
                        "village_id": "101", "server_time": 1002,
                        "attacks": [self.attack("2", 14500)],
                    })
                stored = {r["movement_id"]: r["details"].get("tournament_square")
                          for r in repo.get_recent_attacks()}
                self.assertEqual("10 (Taran)", stored["2"])

                # Select several rows and delete them with one confirmation.
                table.clearSelection()
                table.selectRow(0)
                from PySide6.QtCore import QItemSelectionModel
                table.selectionModel().select(
                    table.model().index(1, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
                emitted = []
                window.incoming_attacks_tab.delete_requested.connect(emitted.append)
                with patch("src.app.QMessageBox.question", return_value=QMessageBox.Yes) as ask:
                    window.incoming_attacks_tab._request_delete_selected()
                self.assertEqual(1, ask.call_count)
                self.assertEqual(2, len(emitted[0]))
                self.assertEqual(1, len(repo.get_recent_attacks()))
            finally:
                window.close()
                db.close()


class BackfillTests(unittest.TestCase):
    def test_old_records_get_estimate_unless_detected_at_app_start(self):
        from datetime import datetime
        from src.core.tournament_square import distance_between, travel_seconds

        with tempfile.TemporaryDirectory() as tmpdir:
            window, repo, db = AttacksTabTests().make_window(tmpdir)
            try:
                source = str(538001407 + 30)          # 30 fields east of target
                target = "538001407"
                self.assertEqual(30.0, distance_between(source, target))
                live_detected = datetime(2026, 11, 9, 20, 0, 0)
                duration = travel_seconds(30, 8.0, 10)  # ram, square 10
                offset = 41.0
                arrival = live_detected.timestamp() + offset + duration
                base = {
                    "village_id": target, "village_name": "Aden",
                    "attacker_name": "BIKO", "source_village_id": source,
                    "source_village": "AKACJA", "arrival_time": arrival,
                    "seconds_remaining": 0, "movement_type": "Atak", "masked": True,
                }
                repo.record_attack({**base, "movement_id": "1"}, live_detected)
                started = datetime(2026, 11, 9, 21, 0, 0)
                repo.record_attack({**base, "movement_id": "2", "arrival_time": arrival + 9999},
                                   datetime(2026, 11, 9, 21, 0, 40))
                repo.record_attack({**base, "movement_id": "3", "attacker_name": "Robber",
                                    "source_village_id": None}, live_detected)

                class Session:
                    def read_server_clock_offset(self):
                        return offset

                    def read_village_tribes(self, ids):
                        return {source: 2}

                window.browser_session = Session()
                window._unit_speeds = {2: {1: 14.0, 7: 8.0}}
                with patch("src.app._app_start_times", return_value=[started]):
                    window._backfill_attacker_squares()
                squares = {r["movement_id"]: r["details"].get("tournament_square")
                           for r in repo.get_recent_attacks()}
                self.assertEqual("≈ 10 (Taran)", squares["1"])
                self.assertIsNone(squares["2"])          # detected 40 s after an app start
                self.assertEqual(ROBBER_TEXT, squares["3"])
            finally:
                window.browser_session = None
                window.close()
                db.close()


if __name__ == "__main__":
    unittest.main()
