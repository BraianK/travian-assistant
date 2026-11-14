"""Tests for the daily rolling vacation-mode safeguard."""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.app import MainWindow
from src.config.settings import AppSettings, VacationSettings
from src.core.account_manager import AccountManager
from src.core.browser_session import TravianBrowserSession
from src.core.vacation_planner import (
    ACTION_CANCEL,
    ACTION_KEEP,
    ACTION_NONE,
    ACTION_PLAN,
    ACTION_WAIT,
    decide_vacation_action,
)
from src.core.village_tracker import VillageTracker
from src.database.connection import DatabaseManager
from src.database.repository import AccountRepository, VillageRepository

NOW = datetime(2026, 11, 5, 21, 5)
SERVER_NOW = NOW.timestamp()


def _decide(state, attacks=(), now=NOW, **overrides):
    options = {
        "plan_time": "21:00",
        "plan_window_minutes": 30,
        "decision_minutes_before": 10,
        "attack_threshold": 1,
        "counted_movement_types": {"Atak", "Oblężenie", "Napad"},
        "last_plan_date": None,
    }
    options.update(overrides)
    return decide_vacation_action(
        state,
        now,
        None if attacks is None else list(attacks),
        **options,
    )


def _planned(seconds_to_start: float) -> dict:
    return {
        "vacation_state": 1,
        "start": SERVER_NOW + seconds_to_start,
        "end": SERVER_NOW + seconds_to_start + 86400,
        "server_time": SERVER_NOW,
    }


IDLE = {"vacation_state": 0, "start": 0, "end": 0, "server_time": SERVER_NOW}


def _attack(movement_type="Atak", arrival_in=600):
    return {"movement_type": movement_type, "arrival_time": SERVER_NOW + arrival_in}


class VacationPlannerTests(unittest.TestCase):
    def test_plans_inside_daily_window_once(self):
        self.assertEqual(ACTION_PLAN, _decide(IDLE).action)
        self.assertEqual(
            ACTION_WAIT,
            _decide(IDLE, last_plan_date=NOW.date().isoformat()).action,
        )

    def test_waits_outside_planning_window(self):
        before = _decide(IDLE, now=NOW.replace(hour=20, minute=0))
        after = _decide(IDLE, now=NOW.replace(hour=21, minute=31))
        self.assertEqual(ACTION_WAIT, before.action)
        self.assertEqual(ACTION_WAIT, after.action)
        self.assertEqual(
            NOW.replace(hour=21, minute=0) + timedelta(days=1),
            after.next_check,
        )

    def test_waits_until_decision_time(self):
        decision = _decide(_planned(3600), attacks=[])
        self.assertEqual(ACTION_WAIT, decision.action)

    def test_cancels_without_attacks_at_decision_time(self):
        decision = _decide(_planned(300), attacks=[_attack("Zwiad")])
        self.assertEqual(ACTION_CANCEL, decision.action)
        self.assertEqual(0, decision.counted_attacks)

    def test_keeps_vacation_when_threshold_is_reached(self):
        attacks = [_attack("Atak"), _attack("Napad"), _attack("Zwiad")]
        self.assertEqual(
            ACTION_KEEP,
            _decide(_planned(300), attacks=attacks, attack_threshold=2).action,
        )
        self.assertEqual(
            ACTION_CANCEL,
            _decide(_planned(300), attacks=attacks, attack_threshold=3).action,
        )

    def test_ignores_movements_that_already_arrived(self):
        decision = _decide(_planned(300), attacks=[_attack(arrival_in=-5)])
        self.assertEqual(ACTION_CANCEL, decision.action)

    def test_keeps_vacation_when_attacks_cannot_be_read(self):
        self.assertEqual(ACTION_KEEP, _decide(_planned(300), attacks=None).action)

    def test_never_cancels_right_before_activation(self):
        self.assertEqual(ACTION_KEEP, _decide(_planned(30), attacks=[]).action)

    def test_active_vacation_is_left_alone(self):
        self.assertEqual(ACTION_NONE, _decide(_planned(-10), attacks=[]).action)
        self.assertEqual(
            ACTION_NONE,
            _decide({**_planned(500), "vacation_state": 2}, attacks=[]).action,
        )

    def test_does_not_plan_when_own_attacks_are_due(self):
        decision = _decide(IDLE, next_own_attack=SERVER_NOW + 3600)
        self.assertEqual(ACTION_WAIT, decision.action)
        self.assertIn("ataki", decision.reason)
        far = _decide(IDLE, next_own_attack=SERVER_NOW + 4 * 86400)
        self.assertEqual(ACTION_PLAN, far.action)

    def test_cancels_planned_vacation_that_blocks_own_attacks(self):
        decision = _decide(_planned(20 * 3600), next_own_attack=SERVER_NOW + 600)
        self.assertEqual(ACTION_CANCEL, decision.action)
        self.assertEqual(
            ACTION_KEEP,
            _decide(_planned(30), next_own_attack=SERVER_NOW + 10).action,
        )
        later = _decide(_planned(20 * 3600), next_own_attack=SERVER_NOW + 5 * 86400)
        self.assertEqual(ACTION_WAIT, later.action)

    def test_settings_validation(self):
        self.assertEqual("07:05", VacationSettings(plan_time="7:5").plan_time)
        with self.assertRaises(ValueError):
            VacationSettings(plan_time="25:00")
        with self.assertRaises(ValueError):
            VacationSettings(counted_movement_types={"Handel"})
        with self.assertRaises(ValueError):
            VacationSettings(decision_minutes_before=1)


class _FakeVacationSession:
    def __init__(self, state, attacks):
        self.state = state
        self.attacks = attacks
        self.changes = []

    def read_vacation_state(self):
        return {"player_id": "199", "used_days": 0, **self.state}

    def read_incoming_attacks(self, villages):
        return self.attacks

    # The clicker stand-in shares the fake game state.
    def change_vacation(self, days):
        self.changes.append(days)
        self.state = _planned(86400) if days else dict(IDLE)
        return {"state": self.state["vacation_state"], "start": self.state["start"]}

    def stop_incoming_attack_listener(self):
        pass

    def disconnect(self):
        pass


class VacationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def _window(self, tmpdir, session):
        db = DatabaseManager(str(Path(tmpdir) / "vacation.db"))
        db.init()
        settings = AppSettings()
        settings.vacation.enabled = True
        window = MainWindow(
            settings,
            AccountManager(AccountRepository(db)),
            VillageTracker(VillageRepository(db)),
        )
        window.browser_session = session
        window._click_executor = lambda: session
        window._run_click_job = lambda target, name: target()
        window.dashboard_tab._live_state = {
            "villages": [{"id": "101", "name": "Testonia"}],
        }
        return window, db

    def test_cancels_planned_vacation_without_attacks(self):
        session = _FakeVacationSession(_planned(300), [])
        with tempfile.TemporaryDirectory() as tmpdir:
            window, db = self._window(tmpdir, session)
            try:
                with patch.object(AppSettings, "save", return_value=None), \
                        patch("src.app.datetime") as fake_datetime:
                    fake_datetime.now.return_value = NOW
                    fake_datetime.fromtimestamp = datetime.fromtimestamp
                    window._check_vacation()
                    self.assertEqual([None], session.changes)
                self.assertEqual(0, session.state["vacation_state"])
                self.assertIn(
                    "anulowane",
                    window.vacation_tab.log_list.item(0).text(),
                )
            finally:
                window.close()
                db.close()

    def test_keeps_planned_vacation_under_attack(self):
        session = _FakeVacationSession(_planned(300), [_attack("Atak")])
        with tempfile.TemporaryDirectory() as tmpdir:
            window, db = self._window(tmpdir, session)
            try:
                with patch.object(AppSettings, "save", return_value=None):
                    window._check_vacation()
                self.assertEqual([], session.changes)
                self.assertIn(
                    "wrogich ruchów",
                    window.vacation_tab.detail_labels["next_action"].text(),
                )
                self.assertEqual(
                    "1",
                    window.vacation_tab.detail_labels["attacks"].text(),
                )
            finally:
                window.close()
                db.close()

    def test_plans_in_window_and_records_the_day(self):
        session = _FakeVacationSession(dict(IDLE), [])
        with tempfile.TemporaryDirectory() as tmpdir:
            window, db = self._window(tmpdir, session)
            try:
                with patch.object(AppSettings, "save", return_value=None), \
                        patch("src.app.datetime") as fake_datetime, \
                        patch("src.app.date") as fake_date:
                    fake_datetime.now.return_value = NOW
                    fake_datetime.fromtimestamp = datetime.fromtimestamp
                    fake_date.today.return_value = NOW.date()
                    window._check_vacation()
                    self.assertEqual([1], session.changes)
                self.assertEqual(
                    NOW.date().isoformat(),
                    window.settings.vacation.last_plan_date,
                )
            finally:
                window.close()
                db.close()

    def test_disabled_automation_only_reports_state(self):
        session = _FakeVacationSession(_planned(300), [])
        with tempfile.TemporaryDirectory() as tmpdir:
            window, db = self._window(tmpdir, session)
            window.settings.vacation.enabled = False
            try:
                window._check_vacation()
                self.assertEqual([], session.changes)
                self.assertIn(
                    "zaplanowane",
                    window.vacation_tab.state_chip.text(),
                )
            finally:
                window.close()
                db.close()

    def test_enabling_requires_confirmation(self):
        session = _FakeVacationSession(dict(IDLE), [])
        with tempfile.TemporaryDirectory() as tmpdir:
            window, db = self._window(tmpdir, session)
            window.settings.vacation.enabled = False
            window.vacation_tab.load_settings(window.settings.vacation)
            try:
                with patch.object(
                    QMessageBox, "question", return_value=QMessageBox.No,
                ), patch.object(AppSettings, "save", return_value=None):
                    window.vacation_tab.enabled_toggle.click()
                self.assertFalse(window.settings.vacation.enabled)
                self.assertFalse(window.vacation_tab.enabled_toggle.isChecked())
            finally:
                window.close()
                db.close()


if __name__ == "__main__":
    unittest.main()
