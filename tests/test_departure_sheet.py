"""Departure times against the IVI spreadsheet (kirilloid / game rounding).

The spreadsheet computes the exact travel time; the game truncates it to
whole seconds, so the real departure is the spreadsheet's exact departure
rounded *up* to the next second. Speed 4 in the sheet is a siege with rams
(8 fields/h, twice as long); speed 6 is a normal attack with catapults.
"""

import json
import math
import unittest
from pathlib import Path

from src.core.scout_planner import attack_travel, distance

DATA = json.loads(
    (Path(__file__).parent / "data" / "ivi_departures_sheet.json").read_text(encoding="utf-8")
)


class SheetDepartureTests(unittest.TestCase):
    def test_every_row_matches_the_sheet_rounded_up(self):
        target = DATA["target"]
        checked = 0
        for attacker in DATA["attackers"]:
            siege = attacker["sheet_speed"] == 4.0
            unit_speed = 8.0 if siege else attacker["sheet_speed"]
            fields = distance(attacker["x"], attacker["y"], target["x"], target["y"])
            arrival = round(attacker["arrival_day_fraction"] * 86400)
            for level, travel_days in attacker["travel_days"].items():
                exact_departure = arrival - travel_days * 86400
                app_departure = arrival - attack_travel(fields, unit_speed, int(level), siege)
                with self.subTest(attacker=attacker["attacker"], pt=level, siege=siege):
                    self.assertEqual(math.ceil(exact_departure - 1e-6), app_departure)
                checked += 1
        self.assertEqual(210, checked)

    def test_known_game_departures(self):
        # TheKunS sieges Jessica, arrival 06:59:59 (checked against the game).
        attacker = DATA["attackers"][0]
        fields = distance(attacker["x"], attacker["y"], 30, 35)
        arrival = 6 * 3600 + 59 * 60 + 59

        def clock(level):
            seconds = int(arrival - attack_travel(fields, 8.0, level, True)) % 86400
            return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

        self.assertEqual("14:55:47", clock(0))
        self.assertEqual("20:27:53", clock(10))
        self.assertEqual("21:23:14", clock(14))


if __name__ == "__main__":
    unittest.main()
