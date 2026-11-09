"""Kingdom tab: member list read from the game and its table."""

import os
import unittest
from datetime import datetime

from src.core.browser_session import TravianBrowserSession


class KingdomReadTests(unittest.TestCase):
    def test_sanitizes_members(self):
        class FakeTab:
            def call_method(self, method, **kwargs):
                self.kwargs = kwargs
                return {"result": {"value": {
                    "kingdom_id": 48, "tag": "ILOVEPL",
                    "members": [
                        {"player_id": 146, "name": "rafal92f", "is_king": True,
                         "population": 4176, "villages": 5, "victory_points": 15292,
                         "fealty_level": 16},
                        {"player_id": 199, "name": "Dzogurt", "population": "x"},
                        {"name": ""},
                    ],
                }}}

        session = TravianBrowserSession("127.0.0.1", 9222)
        session._tab = FakeTab()
        kingdom = session.read_kingdom_members()
        self.assertTrue(session._tab.kwargs["awaitPromise"])
        self.assertEqual("ILOVEPL", kingdom["tag"])
        self.assertEqual(["rafal92f", "Dzogurt"], [m["name"] for m in kingdom["members"]])
        self.assertEqual(0, kingdom["members"][1]["population"])
        self.assertTrue(kingdom["members"][0]["is_king"])


class TeamUpdatesTests(unittest.TestCase):
    def test_reads_only_ids_names_and_last_update(self):
        import json
        from src.core.tktools_client import TkToolsSession

        session = TkToolsSession("http://127.0.0.1:9222")
        captured = {}

        def fake_evaluate(expression, timeout=20):
            captured["expression"] = expression
            return json.dumps({"teamName": "ILOVEPL", "members": [
                {"playerId": "199", "name": "Dzogurt", "lastUpdate": "2026-11-09T19:00:00.000Z"},
                {"playerId": "", "name": "ghost", "lastUpdate": None},
            ]})

        session._evaluate = fake_evaluate
        result = session.read_team_updates("93a85d51-bad4-40f6-a780-7a70b04eae9a")
        self.assertEqual({"199"}, set(result["members"]))
        self.assertIn("/overview", captured["expression"])
        self.assertNotIn("syncAccounts", captured["expression"])
        with self.assertRaises(ValueError):
            session.read_team_updates("../admin")


class TroopsUpdateTests(unittest.TestCase):
    def test_unit_codes_follow_game_order(self):
        from src.core.tktools_client import tktools_units

        units = tktools_units(3, {1: 5627, 4: 1523, 7: 11, 8: 1, 11: 1})
        self.assertEqual(5627, units["phalanx"])
        self.assertEqual(1523, units["theutatesThunder"])
        self.assertEqual(11, units["ram"])
        self.assertEqual(1, units["trebuchet"])
        self.assertEqual(1, units["hero"])
        self.assertEqual(0, units["haeduan"])
        self.assertEqual(11, len(units))

    def test_update_sends_only_own_player_and_mapped_units(self):
        import json
        from src.core.tktools_client import TkToolsSession

        session = TkToolsSession("http://127.0.0.1:9222")
        captured = {}

        def fake_evaluate(expression, timeout=20):
            captured["expression"] = expression
            return json.dumps({"updated": ["00.Aden"], "kept": [], "missing": ["04.Gludio"],
                               "lastUpdate": "2026-11-09T22:00:00Z"})

        session._evaluate = fake_evaluate
        result = session.update_troops("tr2nx3", {
            "player_name": "Dzogurt", "tribe": 3,
            "villages": [{"name": "00.Aden", "x": -1, "y": 34, "units": {1: 10, 11: 1},
                          "tournament_square_level": 3}],
        })
        self.assertEqual(["04.Gludio"], result["missing"])
        expression = captured["expression"]
        self.assertIn('"Dzogurt"', expression)
        self.assertIn('"GAULS"', expression)
        self.assertIn('"phalanx": 10', expression)
        self.assertIn('"tournamentSquareLevel": 3', expression)
        self.assertIn('[0] || {}).players', expression)
        self.assertIn('"PUT"', expression)
        with self.assertRaises(ValueError):
            session.update_troops("bad server!", {"tribe": 3})


class GetterToolsTests(unittest.TestCase):
    def test_units_and_requests(self):
        import json
        from src.core.gettertools_client import GetterToolsSession, gt_units

        self.assertEqual([5630, 42, 5, 1524, 23, 0, 11, 1, 0], gt_units(
            {1: 5630, 2: 42, 3: 5, 4: 1524, 5: 23, 7: 11, 8: 1, 10: 3, 11: 1}
        ))
        session = GetterToolsSession("http://127.0.0.1:9222")
        captured = []

        def fake_evaluate(expression, timeout=30):
            captured.append(expression)
            if "detailList" in expression:
                return {"members": [
                    {"playerId": "199", "timestamp": 1793609444, "text": "2/11/26", "color": "orange"},
                    {"playerId": "", "timestamp": 1, "text": "x", "color": "green"},
                ]}
            return {"updated": ["00.Aden"], "missing": [], "failed": []}

        session._evaluate = fake_evaluate
        updates = session.read_group_updates("tr2nx3", "ILOVEPL,groups117")
        self.assertEqual({"199"}, set(updates))
        self.assertEqual("orange", updates["199"]["color"])
        self.assertIn('"tr2nx3.kingdoms.com"', captured[0])
        result = session.update_troops("tr2nx3", {"villages": [
            {"name": "00.Aden", "x": -1, "y": 34, "units": {1: 10, 11: 1},
             "tournament_square_level": 3},
        ]})
        self.assertEqual(["00.Aden"], result["updated"])
        self.assertIn('"units": [10, 0, 0, 0, 0, 0, 0, 0, 0]', captured[1])
        self.assertIn('"tp": 3', captured[1])
        self.assertIn("saveCityT2", captured[1])
        with self.assertRaises(ValueError):
            session.read_group_updates("tr2nx3", "../evil")


class KingdomWidgetTests(unittest.TestCase):
    def test_viceking_role_and_sorting_every_column(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from src.app import KingdomWidget

        QApplication.instance() or QApplication([])
        widget = KingdomWidget()
        widget.resize(1300, 500)
        widget.show()
        widget.update_members({"kingdom_id": 48, "tag": "X", "members": [
            {"player_id": 156, "name": "RejeZ", "population": 4851, "kingdom_role": 3},
            {"player_id": 146, "name": "rafal92f", "population": 4176, "is_king": True,
             "kingdom_role": 1},
            {"player_id": 290, "name": "Tosiek", "population": 4060, "is_duke": True,
             "kingdom_role": 2},
            {"player_id": 199, "name": "Dzogurt", "population": 2538},
        ]}, datetime(2026, 11, 9, 22, 0))
        table, header = widget.table, widget.table.horizontalHeader()
        roles = {table.item(r, 1).text(): table.item(r, 2).text() for r in range(4)}
        self.assertEqual("Wicekról", roles["RejeZ"])

        def click(column):
            QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier,
                             QPoint(header.sectionViewportPosition(column) + 5, 5))
            return [table.item(r, 1).text() for r in range(4)]

        self.assertEqual(["rafal92f", "RejeZ", "Tosiek", "Dzogurt"], click(2))
        self.assertEqual(["Dzogurt", "Tosiek", "RejeZ", "rafal92f"], click(2))
        self.assertEqual(["RejeZ", "rafal92f", "Tosiek", "Dzogurt"], click(3))
        self.assertEqual(["Dzogurt", "rafal92f", "RejeZ", "Tosiek"], click(1))
        for column in range(table.columnCount()):
            click(column)
            self.assertEqual(column, header.sortIndicatorSection())

    def test_gt_column_colours(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from src.app import KingdomWidget

        QApplication.instance() or QApplication([])
        widget = KingdomWidget()
        now = datetime(2026, 11, 9, 22, 0)
        widget.update_members({
            "kingdom_id": 48, "tag": "X", "tk_updates": None,
            "gt_updates": {"1": {"timestamp": int(now.timestamp()) - 7200, "color": "green"},
                           "2": {"timestamp": None, "color": "red"}},
            "members": [{"player_id": i, "name": f"p{i}", "population": 10 - i} for i in (1, 2, 3)],
        }, now)
        table = widget.table
        self.assertEqual("GT - Aktualność", table.horizontalHeaderItem(7).text())
        self.assertEqual("09.11 20:00 (2 h temu)", table.item(0, 7).text())
        self.assertEqual("#D7F3EF", table.item(0, 7).background().color().name().upper())
        self.assertEqual("nigdy", table.item(1, 7).text())
        self.assertEqual("#F1F1F4", table.item(1, 7).background().color().name().upper())
        self.assertEqual("brak danych", table.item(2, 7).text())

    def test_freshness_colors_match_tktools(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from datetime import timedelta, timezone
        from PySide6.QtWidgets import QApplication

        from src.app import KingdomWidget

        QApplication.instance() or QApplication([])
        widget = KingdomWidget()
        now = datetime(2026, 11, 9, 22, 0)

        def iso(days):
            return (now - timedelta(days=days)).astimezone().astimezone(timezone.utc).isoformat()

        widget.update_members({
            "kingdom_id": 48, "tag": "X",
            "tk_updates": {"1": {"last_update": iso(1)}, "2": {"last_update": iso(5)},
                           "3": {"last_update": iso(9)}, "4": {"last_update": None}},
            "members": [
                {"player_id": i, "name": f"p{i}", "population": 100 - i} for i in (1, 2, 3, 4, 5)
            ],
        }, now)
        colors = {widget.table.item(r, 1).text(): widget.table.item(r, 6).background().color().name().upper()
                  for r in range(5)}
        self.assertEqual("#D7F3EF", colors["p1"])
        self.assertEqual("#FFF3D6", colors["p2"])
        self.assertEqual("#FCE7EC", colors["p3"])
        self.assertEqual("#F1F1F4", colors["p4"])
        self.assertEqual("brak danych", widget.table.item(4, 6).text())

    def test_tk_update_column_and_missing_data(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from datetime import timezone
        from PySide6.QtWidgets import QApplication

        from src.app import KingdomWidget

        QApplication.instance() or QApplication([])
        widget = KingdomWidget()
        now = datetime(2026, 11, 9, 22, 0)
        iso = now.astimezone().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        widget.update_members({
            "kingdom_id": 48, "tag": "ILOVEPL",
            "tk_updates": {"199": {"name": "Dzogurt", "last_update": iso}},
            "members": [
                {"player_id": 199, "name": "Dzogurt", "population": 900},
                {"player_id": 146, "name": "rafal92f", "population": 4176},
            ],
        }, now)
        table = widget.table
        self.assertEqual("TK - Aktualność", table.horizontalHeaderItem(6).text())
        self.assertEqual(8, table.columnCount())
        texts = {table.item(r, 1).text(): table.item(r, 6).text() for r in range(2)}
        self.assertEqual("09.11 22:00 (0 min temu)", texts["Dzogurt"])
        self.assertEqual("brak danych", texts["rafal92f"])

        widget.update_members({
            "kingdom_id": 48, "tag": "ILOVEPL", "tk_updates": None,
            "members": [{"player_id": 199, "name": "Dzogurt", "population": 900}],
        }, now)
        self.assertEqual("brak danych", table.item(0, 6).text())

    def test_lists_members_sorted_and_filterable(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from src.app import KingdomWidget

        QApplication.instance() or QApplication([])
        widget = KingdomWidget()
        widget.update_members({
            "kingdom_id": 48, "tag": "ILOVEPL",
            "members": [
                {"name": "Dzogurt", "population": 900, "villages": 5,
                 "victory_points": 100, "fealty_level": 3},
                {"name": "rafal92f", "is_king": True, "population": 4176, "villages": 5,
                 "victory_points": 15292, "fealty_level": 16},
                {"name": "Tosiek", "is_duke": True, "population": 4060, "villages": 5,
                 "victory_points": 6276, "fealty_level": 14},
            ],
        }, datetime(2026, 11, 9, 22, 0))
        table = widget.table
        self.assertEqual("Królestwo ILOVEPL", widget.title.text())
        self.assertEqual(3, table.rowCount())
        self.assertEqual("rafal92f", table.item(0, 1).text())
        self.assertEqual("Król", table.item(0, 2).text())
        self.assertEqual("Książę", table.item(1, 2).text())
        self.assertEqual("Gubernator", table.item(2, 2).text())
        self.assertEqual("4 176", table.item(0, 3).text())
        # Numeric sort by victory points, not by text.
        table.sortItems(5, Qt.AscendingOrder)
        self.assertEqual("Dzogurt", table.item(0, 1).text())
        widget.filter_edit.setText("tos")
        visible = [table.item(r, 1).text() for r in range(3) if not table.isRowHidden(r)]
        self.assertEqual(["Tosiek"], visible)


if __name__ == "__main__":
    unittest.main()
