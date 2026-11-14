"""Main application module with PySide6 UI."""

import logging
import json
import sys
import tempfile
import threading
from datetime import date, datetime
import time
from pathlib import Path
from time import monotonic

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QMenuBar, QMenu, QMessageBox, QDialog,
    QVBoxLayout, QHBoxLayout, QWidget, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QPushButton, QLineEdit, QComboBox,
    QGroupBox, QFormLayout, QCheckBox, QScrollArea, QSpinBox,
    QGridLayout, QFrame, QTimeEdit,
)
from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon

try:
    from src.config.settings import (
        AppSettings,
        AttackSettings,
        BuildSettings,
        HideoutDefenseSettings,
        ScoutSettings,
        RecruitmentSettings,
        VACATION_MOVEMENT_TYPES,
        VacationSettings,
        VillageMillingSettings,
        VillageRecruitmentSettings,
    )
    from src.database.connection import DatabaseManager
    from src.database.repository import (
        AccountRepository,
        VillageRepository,
        AttackLogRepository,
        IncomingAttackRepository,
    )
    from src.core.account_manager import AccountManager
    from src.core.browser_session import (
        NPC_TRADER_PRICE,
        TravianBrowserSession,
        launch_chrome_for_game,
    )
    from src.core.village_tracker import VillageTracker
    from src.core.attack_automation import AttackAutomation
    from src.core.attack_bridge import GameAttackBridge
    from src.core.hideout_defense import HideoutDefense, SCAN_INTERVAL_SECONDS
    from src.config.paths import get_data_dir
    from src.core.attack_operations import UNIT_NAMES
    from src.core.tournament_square import describe as describe_square_level
    from src.core.tournament_square import (
        ROBBER_TEXT as ROBBER_SQUARE_TEXT, batch_detections, detected_at_start,
        village_position,
        distance_between,
    )
    from src.core.attack_clicker import ClickAttackExecutor
    from src.core.tktools_client import TkToolsSession
    from src.core.gettertools_client import GetterToolsSession
    from src.core.scout_automation import ScoutAutomation, read_hero_xp
    from src.core.build_automation import BuildAutomation
    from src.core.scout_planner import ScoutTarget
    from src.core.tournament_square import estimate_levels as estimate_square_levels
    from src.core.attack_operations import SCOUT_UNITS
    from src.core.vacation_planner import (
        ACTION_CANCEL,
        ACTION_KEEP,
        ACTION_PLAN,
        CANCEL_SAFETY_SECONDS,
        count_hostile_movements,
        decide_vacation_action,
    )
    from src.core.village_advisor import get_village_plan
    from src.core.recruitment_economics import (
        estimate_recruitment_costs_per_hour,
        recruitment_batch_for_duration,
        recruitment_queue_duration_seconds,
        recruitment_queue_threshold_for_duration,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from config.settings import (
        AppSettings,
        AttackSettings,
        BuildSettings,
        HideoutDefenseSettings,
        ScoutSettings,
        RecruitmentSettings,
        VACATION_MOVEMENT_TYPES,
        VacationSettings,
        VillageMillingSettings,
        VillageRecruitmentSettings,
    )
    from database.connection import DatabaseManager
    from database.repository import (
        AccountRepository,
        VillageRepository,
        AttackLogRepository,
        IncomingAttackRepository,
    )
    from core.account_manager import AccountManager
    from core.browser_session import (
        NPC_TRADER_PRICE,
        TravianBrowserSession,
        launch_chrome_for_game,
    )
    from core.village_tracker import VillageTracker
    from core.attack_automation import AttackAutomation
    from core.attack_bridge import GameAttackBridge
    from core.hideout_defense import HideoutDefense, SCAN_INTERVAL_SECONDS
    from config.paths import get_data_dir
    from core.attack_operations import UNIT_NAMES
    from core.tournament_square import describe as describe_square_level
    from core.tournament_square import (
        ROBBER_TEXT as ROBBER_SQUARE_TEXT, batch_detections, detected_at_start,
        village_position,
        distance_between,
    )
    from core.attack_clicker import ClickAttackExecutor
    from core.tktools_client import TkToolsSession
    from core.gettertools_client import GetterToolsSession
    from core.scout_automation import ScoutAutomation, read_hero_xp
    from core.build_automation import BuildAutomation
    from core.scout_planner import ScoutTarget
    from core.tournament_square import estimate_levels as estimate_square_levels
    from core.attack_operations import SCOUT_UNITS
    from core.vacation_planner import (
        ACTION_CANCEL,
        ACTION_KEEP,
        ACTION_PLAN,
        CANCEL_SAFETY_SECONDS,
        count_hostile_movements,
        decide_vacation_action,
    )
    from core.village_advisor import get_village_plan
    from core.recruitment_economics import (
        estimate_recruitment_costs_per_hour,
        recruitment_batch_for_duration,
        recruitment_queue_duration_seconds,
        recruitment_queue_threshold_for_duration,
    )

logger = logging.getLogger(__name__)


def _format_hourly_rate(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "Brak danych"
    amount = f"{abs(value):,.0f}".replace(",", " ")
    if value < 0:
        prefix = "−"
    elif signed:
        prefix = "+"
    else:
        prefix = ""
    return f"{prefix}{amount}/h"


_APP_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #f3f5f8;
    color: #233044;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QGroupBox#sectionGroup {
    background: #ffffff;
    border: 1px solid #dfe5ec;
    border-radius: 9px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox#sectionGroup::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: #475569;
}
QWidget#resourceCard {
    background: #ffffff;
    border: 1px solid #e1e7ee;
    border-radius: 8px;
}
QLabel#connectionStatus {
    background: #e9f0f8;
    border-radius: 6px;
    padding: 8px 10px;
    color: #334155;
}
QLabel#secondaryText {
    color: #64748b;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 8px 12px;
}
QPushButton:hover {
    background: #f1f5f9;
}
QPushButton:disabled {
    color: #94a3b8;
    background: #edf0f4;
}
QPushButton#primaryButton {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: #1d4ed8;
}
QComboBox, QLineEdit {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 7px 9px;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f7f9fc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    gridline-color: transparent;
    selection-background-color: #dbeafe;
    selection-color: #1e3a8a;
}
QHeaderView::section {
    background: #f1f5f9;
    color: #475569;
    border: none;
    border-bottom: 1px solid #dbe2ea;
    padding: 8px;
    font-weight: 600;
}
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #dfe5ec;
    border-radius: 7px;
    top: -1px;
}
QTabBar::tab {
    background: #e9edf3;
    border: 1px solid #dfe5ec;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 9px 16px;
    margin-right: 3px;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #1d4ed8;
    font-weight: 600;
}
QProgressBar {
    background: #edf1f5;
    border: none;
    border-radius: 4px;
    height: 12px;
    text-align: center;
}
QProgressBar::chunk {
    background: #4b8bf4;
    border-radius: 4px;
}
QFrame#card, QFrame#villageCard {
    background: #ffffff;
    border: 1px solid #dfe5ec;
    border-radius: 10px;
}
QFrame#card QLabel, QFrame#card QCheckBox,
QFrame#villageCard QLabel, QFrame#villageCard QCheckBox,
QFrame#pendingBanner QLabel {
    background: transparent;
}
QFrame#divider {
    background: #edf1f5;
    border: none;
}
QFrame#pendingBanner {
    background: #fff8e1;
    border: 1px solid #f5d77a;
    border-radius: 8px;
}
QLabel#columnHeader {
    color: #8492a6;
    font-size: 8pt;
    font-weight: 600;
}
QLabel#pageTitle {
    font-size: 17pt;
    font-weight: 700;
    color: #1e293b;
}
QLabel#sectionTitle {
    font-size: 12pt;
    font-weight: 700;
    color: #1e293b;
}
QLabel#villageName {
    font-size: 12pt;
    font-weight: 700;
    color: #1e293b;
}
QCheckBox#unitUnselected {
    color: #64748b;
}
QFrame#ruleStrip {
    background: #f6f8fb;
    border: 1px solid #e6ebf1;
    border-radius: 8px;
}
QFrame#ruleStrip QLabel {
    background: transparent;
}
QPushButton#linkButton {
    background: transparent;
    border: none;
    color: #2563eb;
    padding: 4px 6px;
}
QPushButton#linkButton:hover {
    color: #1d4ed8;
    text-decoration: underline;
}
QLabel#fieldLabel {
    color: #475569;
    font-weight: 600;
}
QLabel#footnote {
    color: #94a3b8;
    font-size: 9pt;
}
QLabel#emptyState {
    background: #ffffff;
    border: 1px dashed #cbd5e1;
    border-radius: 10px;
    color: #64748b;
    padding: 28px;
}
QAbstractSpinBox {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px 6px;
}
QAbstractSpinBox:disabled {
    background: #f1f5f9;
    color: #a0aec0;
}
QCheckBox {
    spacing: 7px;
}
QPushButton#automationToggle {
    background: #eef1f5;
    border: 1px solid #cbd5e1;
    border-radius: 17px;
    color: #475569;
    font-weight: 600;
    padding: 8px 18px;
}
QPushButton#automationToggle:checked {
    background: #dcfce7;
    border-color: #86efac;
    color: #166534;
}
QPushButton#segmentLeft, QPushButton#segmentRight {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    color: #475569;
    padding: 7px 14px;
}
QPushButton#segmentLeft {
    border-top-left-radius: 7px;
    border-bottom-left-radius: 7px;
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
}
QPushButton#segmentRight {
    border-left: none;
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
    border-top-left-radius: 0;
    border-bottom-left-radius: 0;
}
QPushButton#segmentLeft:checked, QPushButton#segmentRight:checked {
    background: #2563eb;
    border-color: #2563eb;
    color: #ffffff;
    font-weight: 600;
}
"""

_UI_ICONS = {
    "check.svg": (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
        "<path d='M3.5 8.5l3 3 6-7' fill='none' stroke='#ffffff' "
        "stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    ),
    "up.svg": (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'>"
        "<path d='M1 5l4-4 4 4' fill='none' stroke='#64748b' "
        "stroke-width='1.6' stroke-linecap='round'/></svg>"
    ),
    "down.svg": (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'>"
        "<path d='M1 1l4 4 4-4' fill='none' stroke='#64748b' "
        "stroke-width='1.6' stroke-linecap='round'/></svg>"
    ),
}


def _icon_stylesheet() -> str:
    """Return QSS rules for check boxes and spin boxes with drawn icons."""
    try:
        icon_dir = Path(tempfile.gettempdir()) / "travian_assistant_ui"
        icon_dir.mkdir(parents=True, exist_ok=True)
        for name, svg in _UI_ICONS.items():
            path = icon_dir / name
            if not path.exists() or path.read_text(encoding="utf-8") != svg:
                path.write_text(svg, encoding="utf-8")
    except OSError:
        logger.warning("Could not prepare UI icons", exc_info=True)
        return ""
    base = icon_dir.as_posix()
    return f"""
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid #94a3b8;
    border-radius: 4px;
    background: #ffffff;
}}
QCheckBox::indicator:hover {{
    border-color: #2563eb;
}}
QCheckBox::indicator:checked {{
    background: #2563eb;
    border-color: #2563eb;
    image: url({base}/check.svg);
}}
QCheckBox::indicator:disabled {{
    background: #eef1f5;
    border-color: #cbd5e1;
}}
QAbstractSpinBox {{
    padding-right: 20px;
}}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border;
    width: 18px;
    border: none;
    border-left: 1px solid #e2e8f0;
    background: transparent;
}}
QAbstractSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: 6px;
}}
QAbstractSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 6px;
}}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
    background: #eef2f7;
}}
QAbstractSpinBox::up-arrow {{
    image: url({base}/up.svg);
    width: 9px;
    height: 6px;
}}
QAbstractSpinBox::down-arrow {{
    image: url({base}/down.svg);
    width: 9px;
    height: 6px;
}}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::down-arrow:disabled {{
    image: none;
}}
"""


_TROOP_REFRESH_INTERVAL_MS = 5 * 60 * 1000
_RECRUITMENT_QUEUE_REFRESH_INTERVAL_MS = 3 * 60 * 1000
_RESOURCE_MILLING_REFRESH_INTERVAL_MS = 30 * 1000
_AUTO_RECRUITMENT_ORDER_INTERVAL_MS = 6 * 1000
_RECRUITMENT_LOCK_EXPIRY_SECONDS = 5 * 60
_DURATION_MODE_MAX_BATCH = 10_000
_VACATION_CHECK_INTERVAL_MS = 30 * 1000
_KINGDOM_REFRESH_INTERVAL_MS = 10 * 60 * 1000
_BUILD_CHECK_INTERVAL_MS = 3 * 60 * 1000
# A build check running longer than this is considered stuck.
_BUILD_WORKER_STALL_SECONDS = 4 * 60
_AUTO_RECRUITMENT_MAX_ORDERS_PER_SCAN = 10
DEFAULT_RECRUITMENT_QUEUE_MINUTES = 60
_NPC_TRADER_ORDER_INTERVAL_MS = 6 * 1000


def _recruitment_queue_key(village_id: str, unit: str | int) -> str:
    return f"{village_id}:{unit}"


def _recruitment_row_key(row: dict) -> str:
    unit_key = row.get("unit_id", row.get("unit_type", row.get("unit", "")))
    return _recruitment_queue_key(row["village_id"], str(unit_key))


class ResourceCard(QWidget):
    """Display widget for a single resource."""
    
    def __init__(self, name: str, icon: str, color: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.color = color
        self.setObjectName("resourceCard")
        self.setMinimumHeight(126)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        
        # Header with icon and name
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        self.icon_label = QLabel(icon)
        self.icon_label.setFont(QFont("Segoe UI", 16))
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        header_layout.addWidget(self.icon_label)
        header_layout.addWidget(self.name_label)
        layout.addLayout(header_layout)
        
        # Amount display
        self.amount_label = QLabel("—")
        self.amount_label.setFont(QFont("Segoe UI", 18, QFont.DemiBold))
        layout.addWidget(self.amount_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Brak danych")
        layout.addWidget(self.progress_bar)
        
        # Production rate
        self.rate_label = QLabel("—")
        self.rate_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        layout.addWidget(self.rate_label)
    
    def update_display(self, current: int, max_cap: int, production_rate: float):
        """Update the resource card display."""
        self.amount_label.setText(f"{current:,}")
        self.progress_bar.setFormat("%p%")
        
        if max_cap > 0:
            percentage = int((current / max_cap) * 100)
            self.progress_bar.setValue(min(percentage, 100))
        
        self.rate_label.setText(f"{production_rate:.0f}/h")

    def update_live_value(
        self,
        current: int | None,
        capacity: int | None,
        production_rate: int | None,
    ):
        """Show live amount and net hourly production from the game."""
        self.amount_label.setText(
            f"{current:,} / {capacity:,}"
            if current is not None and capacity is not None
            else f"{current:,}" if current is not None else "—"
        )
        if current is not None and capacity and capacity > 0:
            percentage = int(current / capacity * 100)
            self.progress_bar.setValue(min(percentage, 100))
        else:
            self.progress_bar.setValue(0)
        self.progress_bar.setFormat(
            f"{int(current / capacity * 100)}% pojemności"
            if current is not None and capacity
            else "Brak danych"
        )
        self.rate_label.setText(
            f"Przyrost: ~{production_rate:+,}/h"
            if production_rate is not None
            else "Przyrost niedostępny"
        )


class DashboardWidget(QWidget):
    """Main dashboard widget showing overview."""
    
    def __init__(self, village_tracker: VillageTracker, parent=None):
        super().__init__(parent)
        self.tracker = village_tracker
        self._live_state = None
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)
        
        # Title
        title = QLabel("Przegląd wioski")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        layout.addWidget(title)

        connection_layout = QHBoxLayout()
        self.connection_status = QLabel("Nie połączono z grą")
        self.connection_status.setObjectName("connectionStatus")
        self.connection_status.setToolTip(
            "Read-only connection. Sign-in is performed manually in the browser."
        )
        self.village_selector = QComboBox()
        self.village_selector.setMinimumWidth(180)
        self.village_selector.setEnabled(False)
        self.village_selector.addItem("Wybierz wioskę", "")
        self.connect_button = QPushButton("Połącz z przeglądarką")
        self.connect_button.setObjectName("primaryButton")
        self.launch_browser_button = QPushButton("Otwórz Traviana")
        self.disconnect_button = QPushButton("Rozłącz")
        self.disconnect_button.setEnabled(False)
        connection_layout.addWidget(QLabel("Aktywna wioska:"))
        connection_layout.addWidget(self.village_selector)
        connection_layout.addWidget(self.connection_status, stretch=1)
        connection_layout.addWidget(self.launch_browser_button)
        connection_layout.addWidget(self.connect_button)
        connection_layout.addWidget(self.disconnect_button)
        layout.addLayout(connection_layout)
        
        # Resource overview section
        resources_group = QGroupBox("Surowce")
        resources_group.setObjectName("sectionGroup")
        resource_layout = QHBoxLayout(resources_group)
        resource_layout.setContentsMargins(12, 16, 12, 12)
        resource_layout.setSpacing(12)
        
        self.resource_cards = []
        self.resource_card_by_name = {}
        for name, icon, color in [
            ("Drewno", "🪵", "#8B4513"),
            ("Glina", "🏺", "#B22222"),
            ("Żelazo", "⚙️", "#696969"),
            ("Zboże", "🌾", "#B8860B"),
        ]:
            card = ResourceCard(name, icon, color)
            self.resource_cards.append(card)
            resource_key = {
                "drewno": "wood",
                "glina": "clay",
                "żelazo": "iron",
                "zboże": "grain",
            }[name.lower()]
            self.resource_card_by_name[resource_key] = card
            resource_layout.addWidget(card)
        
        layout.addWidget(resources_group)

        plan_group = QGroupBox("Plan i przypomnienia wioski")
        plan_group.setObjectName("sectionGroup")
        plan_layout = QVBoxLayout(plan_group)
        self.village_plan_label = QLabel(
            "Wybierz wioskę i odśwież dane, aby zobaczyć plan."
        )
        self.village_plan_label.setWordWrap(True)
        plan_layout.addWidget(self.village_plan_label)
        layout.addWidget(plan_group)

        self.details_tabs = QTabWidget()
        self.details_tabs.setDocumentMode(True)
        self.details_tabs.setMovable(False)

        buildings_page = QWidget()
        buildings_layout = QVBoxLayout(buildings_page)
        buildings_layout.setContentsMargins(10, 12, 10, 10)
        self.buildings_table = QTableWidget(0, 5)
        self.buildings_table.setHorizontalHeaderLabels(
            ["Budynek", "Pole", "Poziom", "Status", "Ukończenie"]
        )
        self._configure_table(self.buildings_table, stretch_columns=(0, 4))
        buildings_layout.addWidget(self.buildings_table)
        self.details_tabs.addTab(buildings_page, "Budynki")

        troops_page = QWidget()
        troops_layout = QVBoxLayout(troops_page)
        troops_layout.setContentsMargins(10, 12, 10, 10)
        self.troops_status = QLabel(
            "Wojska odczytują się automatycznie po połączeniu i co 5 minut."
        )
        self.troops_status.setObjectName("secondaryText")
        troops_layout.addWidget(self.troops_status)
        self.troops_table = QTableWidget(0, 3)
        self.troops_table.setHorizontalHeaderLabels(["Wioska", "Jednostka", "Liczba"])
        self._configure_table(self.troops_table, stretch_columns=(0, 1))
        troops_layout.addWidget(self.troops_table)
        self.details_tabs.addTab(troops_page, "Wojsko")

        fields_page = QWidget()
        fields_layout = QVBoxLayout(fields_page)
        fields_layout.setContentsMargins(10, 12, 10, 10)
        self.resource_fields_table = QTableWidget(0, 3)
        self.resource_fields_table.setHorizontalHeaderLabels(
            ["Pole surowca", "Pole", "Poziom"]
        )
        self._configure_table(self.resource_fields_table, stretch_columns=(0,))
        fields_layout.addWidget(self.resource_fields_table)
        self.details_tabs.addTab(fields_page, "Pola surowców")

        map_page = QWidget()
        map_layout = QVBoxLayout(map_page)
        map_layout.setContentsMargins(10, 12, 10, 10)
        map_toolbar = QHBoxLayout()
        self.map_status = QLabel(
            "Wioski wczytane przez grę, posortowane od najbliższej."
        )
        self.map_status.setObjectName("secondaryText")
        self.map_filter = QLineEdit()
        self.map_filter.setPlaceholderText("Szukaj wioski, gracza lub królestwa…")
        self.map_filter.setClearButtonEnabled(True)
        self.map_filter.textChanged.connect(self._filter_map_rows)
        self._map_total_count = 0
        map_toolbar.addWidget(self.map_status, stretch=1)
        map_toolbar.addWidget(self.map_filter)
        map_layout.addLayout(map_toolbar)
        self.map_table = QTableWidget(0, 5)
        self.map_table.setHorizontalHeaderLabels(
            ["Wioska", "Gracz", "Współrzędne", "Odległość", "Królestwo"]
        )
        self._configure_table(
            self.map_table,
            stretch_columns=(0, 1),
        )
        map_layout.addWidget(self.map_table)
        self.details_tabs.addTab(map_page, "Mapa")

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(10, 12, 10, 10)
        self.resource_history_status = QLabel("Wybierz wioskę, aby zobaczyć historię.")
        self.resource_history_status.setObjectName("secondaryText")
        history_layout.addWidget(self.resource_history_status)
        self.resource_history_figure = Figure(figsize=(8, 3), tight_layout=True)
        self.resource_history_axes = self.resource_history_figure.add_subplot(111)
        self.resource_history_canvas = FigureCanvasQTAgg(self.resource_history_figure)
        history_layout.addWidget(self.resource_history_canvas)
        self.details_tabs.addTab(history_page, "Historia surowców")

        self._selected_village_id = ""
        self._recruitment_queue_rows: list[dict] = []
        self._recruitment_queue_counts: dict[
            tuple[str, str], tuple[int, int]
        ] = {}
        layout.addWidget(self.details_tabs, stretch=1)
        
        # Action buttons
        button_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Odśwież dane")
        self.refresh_btn.clicked.connect(self._refresh_data)
        button_layout.addWidget(self.refresh_btn)
        self.refresh_troops_btn = QPushButton("Odczytaj teraz")
        self.refresh_troops_btn.setEnabled(False)
        button_layout.addWidget(self.refresh_troops_btn)
        
        layout.addLayout(button_layout)

    @staticmethod
    def _configure_table(
        table: QTableWidget,
        stretch_columns: tuple[int, ...] = (),
        sortable: bool = False,
    ):
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setSortingEnabled(sortable)
        for column in range(table.columnCount()):
            table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.Stretch
                if column in stretch_columns
                else QHeaderView.ResizeToContents,
            )

    def _filter_map_rows(self, text: str):
        query = text.casefold().strip()
        visible_rows = 0
        for row in range(self.map_table.rowCount()):
            searchable = " ".join(
                self.map_table.item(row, column).text()
                for column in range(self.map_table.columnCount())
                if self.map_table.item(row, column)
            ).casefold()
            matches = query in searchable
            self.map_table.setRowHidden(row, not matches)
            visible_rows += int(matches)
        self.map_status.setText(
            f"Widoczne wioski: {visible_rows} z {self._map_total_count}"
        )
    
    def _refresh_data(self):
        """Refresh dashboard data."""
        if self._live_state is not None:
            for name, card in self.resource_card_by_name.items():
                card.update_live_value(
                    self._live_state["resources"].get(name),
                    self._live_state["capacities"].get(name),
                    self._live_state["production_rates"].get(name),
                )
        else:
            totals = self.tracker.get_total_resources()
            rates = self.tracker.get_production_rates()
            for i, res_type in enumerate(["wood", "clay", "iron", "grain"]):
                current = totals[res_type]
                max_cap = totals[f"capacity_{res_type}"]
                card = self.resource_cards[i]
                card.update_display(current, max_cap, rates[res_type])
        if self._selected_village_id:
            self.refresh_resource_history(self._selected_village_id)
            self._update_village_plan(self._selected_village_id)

    @staticmethod
    def _format_wait(hours: float | None) -> str:
        if hours is None:
            return "czas nieoszacowany (brak danych o produkcji)"
        if hours <= 0:
            return "teraz"
        minutes = round(hours * 60)
        if minutes < 60:
            return f"za około {max(minutes, 1)} min"
        if hours < 24:
            return f"za około {hours:.1f} godz."
        return f"za około {hours / 24:.1f} dni"

    def _update_village_plan(self, village_id: str):
        village = self.tracker.get_village(village_id)
        if village is None:
            self.village_plan_label.setText(
                "Brak danych tej wioski. Połącz się z grą i odśwież widok."
            )
            return

        snapshots = self.tracker.repo.get_dashboard_snapshots(village_id)
        plan = get_village_plan(
            resources={
                resource_type: getattr(village, resource_type).current_amount
                for resource_type in ("wood", "clay", "iron", "grain")
            },
            production_rates={
                resource_type: getattr(village, resource_type).production_rate
                for resource_type in ("wood", "clay", "iron", "grain")
            },
            resource_fields=snapshots.get("resource_fields", []),
            buildings=snapshots.get("buildings", []),
        )

        field = plan["next_field"]
        if field is None:
            field_text = "Najbliższe ulepszenie pola: brak zapisanych pól surowców."
        else:
            field_text = (
                f"Najbliższe ulepszenie pola: {field['name']} "
                f"{field['level']} → {field['target_level']} "
                f"({self._format_wait(field['wait_hours'])})."
            )

        unit_lines = []
        for unit in plan["units"]:
            amount = unit["affordable_count"]
            readiness = (
                "możesz szkolić teraz"
                if amount
                else f"następna jednostka {self._format_wait(unit['next_unit_hours'])}"
            )
            unit_lines.append(
                f"{unit['name']}: {amount} szt. z obecnego zapasu; {readiness}."
            )

        reminder_lines = []
        for reminder in plan["reminders"]:
            building_status = (
                "budynek wykryty"
                if reminder["available"]
                else "budynek nieobecny w zapisanym odczycie"
            )
            queue_status_counts = self._recruitment_queue_counts.get(
                (village_id, reminder["unit"])
            )
            if queue_status_counts is None:
                queue_status = "kolejka jeszcze nieodczytana"
            else:
                queue_count, other_count = queue_status_counts
                queue_status = (
                    f"w kolejce: {queue_count} szt."
                    if queue_count
                    else f"kolejka zajęta inną jednostką ({other_count} szt.)"
                    if other_count
                    else "brak aktywnej rekrutacji tego typu"
                )
            reminder_lines.append(
                f"{reminder['building']} · {reminder['unit']}: {queue_status} "
                f"({building_status})."
            )

        self.village_plan_label.setText(
            "Priorytet: utrzymanie rekrutacji; nie odkładam rezerwy kosztem "
            "możliwego szkolenia.\n"
            + "\n".join(unit_lines)
            + "\n"
            + field_text
            + "\n"
            + "\n".join(reminder_lines)
            + "\nSzacunki dotyczą tylko tej wioski; wartości odświeżają się "
            "razem z danymi gry. Automat rekrutacji można włączyć w panelu "
            "kolejek; domyślnie jest wyłączony."
        )

    def update_recruitment_queues(
        self,
        rows: list[dict],
    ):
        """Store queue counts for the selected village's planning summary."""
        self._recruitment_queue_rows = list(rows)
        self._recruitment_queue_counts = {
            (row["village_id"], row["unit"]): (
                row["count"],
                row.get("other_count", 0),
            )
            for row in rows
        }

        if self._selected_village_id:
            self._update_village_plan(self._selected_village_id)

    def clear_recruitment_queues(self):
        self._recruitment_queue_rows.clear()
        self._recruitment_queue_counts.clear()
        if self._selected_village_id:
            self._update_village_plan(self._selected_village_id)


    def _render_buildings(self, buildings: list[dict]):
        self.buildings_table.setRowCount(len(buildings))
        for row, building in enumerate(buildings):
            self.buildings_table.setItem(
                row, 0, QTableWidgetItem(str(building.get("name", "Unknown")))
            )
            self.buildings_table.setItem(
                row, 1, QTableWidgetItem(str(building.get("slot", "")))
            )
            self.buildings_table.setItem(
                row, 2, QTableWidgetItem(str(building.get("level", "")))
            )
            self.buildings_table.setItem(
                row, 3,
                QTableWidgetItem("Ulepszanie" if building.get("upgrading") else ""),
            )
            completion = building.get("upgrade_complete_at") or ""
            self.buildings_table.setItem(
                row, 4, QTableWidgetItem(str(completion))
            )

    def _render_resource_fields(self, fields: list[dict]):
        self.resource_fields_table.setSortingEnabled(False)
        fields_by_name: dict[str, list[dict]] = {}
        for field in fields:
            name = str(field.get("name", "Unknown"))
            fields_by_name.setdefault(name, []).append(field)

        grouped_fields = [
            (
                name,
                sorted(
                    items,
                    key=lambda item: int(item.get("slot", 0) or 0),
                ),
            )
            for name, items in sorted(fields_by_name.items())
        ]
        self.resource_fields_table.clearContents()
        self.resource_fields_table.clearSpans()
        self.resource_fields_table.setRowCount(
            sum(1 + len(items) for _, items in grouped_fields)
        )
        row = 0
        for name, items in grouped_fields:
            average_level = (
                sum(int(item.get("level", 0) or 0) for item in items) / len(items)
            )
            self._set_group_header(
                self.resource_fields_table,
                row,
                f"{name} · {len(items)} pól · średni poziom {average_level:.1f}",
            )
            row += 1
            for field in items:
                self.resource_fields_table.setItem(
                    row, 0, QTableWidgetItem(f"  Pole {field.get('slot', '—')}")
                )
                self.resource_fields_table.setItem(
                    row, 1, QTableWidgetItem(str(field.get("slot", "")))
                )
                self.resource_fields_table.setItem(
                    row, 2, QTableWidgetItem(str(field.get("level", "")))
                )
                row += 1
        self.resource_fields_table.setSortingEnabled(False)

    @staticmethod
    def _set_group_header(table: QTableWidget, row: int, label: str):
        item = QTableWidgetItem(label)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setBackground(QColor("#eaf0f6"))
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        table.setItem(row, 0, item)
        table.setSpan(row, 0, 1, table.columnCount())
        table.setRowHeight(row, 32)

    def _render_troops(self, troops: list[dict]):
        troops_by_village: dict[tuple[str, str], list[dict]] = {}
        for troop in troops:
            village = str(troop.get("village", "Wioska"))
            village_id = str(troop.get("village_id", village))
            troops_by_village.setdefault((village, village_id), []).append(troop)

        grouped_troops = [
            (village, sorted(items, key=lambda item: str(item.get("unit", ""))))
            for (village, _), items in sorted(troops_by_village.items())
        ]
        self.troops_table.setSortingEnabled(False)
        self.troops_table.clearContents()
        self.troops_table.clearSpans()
        self.troops_table.setRowCount(
            sum(1 + len(items) for _, items in grouped_troops)
        )
        row = 0
        for village, items in grouped_troops:
            troop_total = sum(int(item.get("count", 0) or 0) for item in items)
            self._set_group_header(
                self.troops_table,
                row,
                f"{village} · {len(items)} typów · {troop_total:,} jednostek",
            )
            row += 1
            for troop in items:
                self.troops_table.setItem(row, 0, QTableWidgetItem(""))
                self.troops_table.setItem(
                    row, 1, QTableWidgetItem(f"  {troop.get('unit', 'Unknown')}")
                )
                self.troops_table.setItem(
                    row, 2, QTableWidgetItem(f"{int(troop.get('count', 0) or 0):,}")
                )
                row += 1

    def _render_map_villages(self, villages: list[dict]):
        self._map_total_count = len(villages)
        self.map_table.setRowCount(len(villages))
        for row, village in enumerate(villages):
            self.map_table.setItem(
                row, 0, QTableWidgetItem(str(village.get("name", "")))
            )
            self.map_table.setItem(
                row, 1, QTableWidgetItem(str(village.get("player", "")))
            )
            self.map_table.setItem(
                row, 2, QTableWidgetItem(str(village.get("coordinates", "")))
            )
            distance = village.get("distance")
            self.map_table.setItem(
                row, 3,
                QTableWidgetItem(f"{distance:.2f}" if distance is not None else "—"),
            )
            self.map_table.setItem(
                row, 4, QTableWidgetItem(str(village.get("kingdom", "")))
            )
        self._filter_map_rows(self.map_filter.text())

    def update_persisted_village(self, village_id: str):
        """Display the last saved details for an offline village selection."""
        self._selected_village_id = village_id
        snapshots = self.tracker.repo.get_dashboard_snapshots(village_id)
        self._render_buildings(snapshots.get("buildings", []))
        self._render_resource_fields(snapshots.get("resource_fields", []))
        self._render_map_villages(snapshots.get("map_villages", []))
        troops = snapshots.get("troops", [])
        self._render_troops(troops)
        self._update_troops_status(troops)
        village = self.tracker.get_village(village_id)
        self.resource_history_status.setText(
            f"Zapisana historia surowców: {village.name}"
            if village else "Brak zapisanej historii surowców."
        )
        self.refresh_resource_history(village_id)
        self._update_village_plan(village_id)

    def _update_troops_status(self, troops: list[dict]):
        self.troops_status.setText(
            f"Zapisany odczyt: {len(troops)} typów jednostek."
            if troops else "Brak zapisanego odczytu wojsk."
        )

    def refresh_resource_history(self, village_id: str):
        history = self.tracker.repo.get_resource_history(village_id)
        axes = self.resource_history_axes
        axes.clear()
        colors = {
            "wood": "#8B4513",
            "clay": "#B22222",
            "iron": "#696969",
            "grain": "#B8860B",
        }
        names = {
            "wood": "Drewno",
            "clay": "Glina",
            "iron": "Żelazo",
            "grain": "Zboże",
        }
        grouped: dict[str, list[tuple[datetime, int]]] = {}
        for item in history:
            recorded_at = item["recorded_at"]
            if isinstance(recorded_at, str):
                recorded_at = datetime.fromisoformat(recorded_at)
            grouped.setdefault(item["resource_type"], []).append(
                (recorded_at, item["current_amount"])
            )

        for resource_type, points in grouped.items():
            axes.plot(
                [point[0] for point in points],
                [point[1] for point in points],
                label=names.get(resource_type, resource_type),
                color=colors.get(resource_type),
            )
        if grouped:
            axes.set_ylabel("Ilość")
            axes.set_title("Historia surowców")
            axes.legend(loc="best")
            axes.grid(True, alpha=0.25)
            self.resource_history_status.setText(
                f"Pokazano {len(history)} ostatnich pomiarów."
            )
        else:
            axes.text(
                0.5, 0.5, "Brak zapisanych pomiarów",
                ha="center", va="center", transform=axes.transAxes,
            )
            self.resource_history_status.setText(
                "Historia pojawi się po odczytaniu danych z gry."
            )
        self.resource_history_figure.autofmt_xdate()
        self.resource_history_canvas.draw_idle()
        
    def update_live_state(self, state: dict):
        """Display read-only account and resource data from the game tab."""
        self._live_state = state
        if not state["logged_in"]:
            self.connection_status.setText(
                "Przeglądarka połączona, ale brak logowania do Traviana"
            )
        else:
            account = state["account"] or "konto Travian"
            identity = f"Zalogowano: {account} — {state['world'] or state['title']}"
            if state["resources"]:
                self.connection_status.setText(identity)
            else:
                self.connection_status.setText(
                    f"{identity}. Otwórz widok wioski, aby zobaczyć surowce."
                )

        for name, card in self.resource_card_by_name.items():
            card.update_live_value(
                state["resources"].get(name),
                state["capacities"].get(name),
                state["production_rates"].get(name),
            )

        self._selected_village_id = str(state.get("selected_village_id", ""))
        self._render_buildings(state.get("buildings", []))

        villages = state.get("villages", [])
        self.village_selector.blockSignals(True)
        self.village_selector.clear()
        selected_index = 0
        for index, village in enumerate(villages):
            self.village_selector.addItem(village["name"], village["id"])
            if village["selected"]:
                selected_index = index
        if not villages:
            self.village_selector.addItem("Nie znaleziono wiosek", "")
        self.village_selector.setCurrentIndex(selected_index)
        self.village_selector.setEnabled(bool(villages))
        self.village_selector.blockSignals(False)

        self._render_resource_fields(state.get("resource_fields", []))
        self._render_map_villages(state.get("map_villages", []))
        self.refresh_resource_history(self._selected_village_id)
        self._update_village_plan(self._selected_village_id)
        self.map_status.setToolTip(
            "Odległość w polach mapy, liczona od wybranej wioski."
        )

    def clear_live_state(self):
        self._live_state = None
        self._selected_village_id = ""
        self.village_plan_label.setText("Wybierz wioskę, aby zobaczyć plan.")
        self.buildings_table.setRowCount(0)
        self.resource_fields_table.setRowCount(0)
        self.map_table.setRowCount(0)
        self.troops_table.setRowCount(0)
        self.troops_status.setText(
            "Wybierz „Odczytaj wojska”, aby wyświetlić stan każdej wioski."
        )
        self.map_filter.clear()
        self._map_total_count = 0
        self.map_status.setText("Wioski wczytane przez grę, posortowane od najbliższej.")
        self.village_selector.blockSignals(True)
        self.village_selector.clear()
        self.village_selector.addItem("Wybierz wioskę", "")
        self.village_selector.setEnabled(False)
        self.village_selector.blockSignals(False)
        self.resource_history_status.setText("Wybierz wioskę, aby zobaczyć historię.")
        self.resource_history_axes.clear()
        self.resource_history_canvas.draw_idle()
    
    def update_dashboard(self):
        """Update all dashboard displays."""
        self._refresh_data()


_RECRUITMENT_STATUS_COLORS = {
    "active": ("#e7f6ec", "#1f7a3f"),
    "waiting": ("#fff4d6", "#8a5a00"),
    "pending": ("#e0ecff", "#1d4ed8"),
    "muted": ("#eef1f5", "#64748b"),
}

_RECRUITMENT_RESOURCE_LABELS = ("Drewno", "Glina", "Żelazo", "Zboże")


def _set_status_chip(label: QLabel, text: str, kind: str):
    background, foreground = _RECRUITMENT_STATUS_COLORS[kind]
    label.setText(text)
    label.setStyleSheet(
        f"background: {background}; color: {foreground};"
        "border-radius: 9px; padding: 2px 10px;"
        "font-size: 9pt; font-weight: 600;"
    )


def _format_queue_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    minutes = round(seconds / 60)
    if minutes >= 60:
        return f"{minutes // 60} h {minutes % 60:02d} min"
    return f"{minutes} min"


class _AutomationToggle(QPushButton):
    """Pill-shaped on/off switch for the main recruitment automation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("automationToggle")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.toggled.connect(self._refresh_text)
        self._refresh_text()

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._refresh_text()

    def _refresh_text(self, *_args):
        self.setText(
            "●  Automat włączony" if self.isChecked()
            else "○  Automat wyłączony"
        )


class RecruitmentWidget(QWidget):
    """Village-by-village recruitment automation and live queue overview."""

    village_enabled_toggled = Signal(str, bool)
    unit_toggled = Signal(str, str, bool)
    queue_minutes_changed = Signal(str, str, int)
    village_strategy_changed = Signal(str, str)
    village_batch_changed = Signal(str, int)
    village_threshold_changed = Signal(str, int)
    village_rule_copy_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recruitment_pending_keys: set[str] = set()
        self._settings_by_village: dict = {}
        self._recruitment_defaults = RecruitmentSettings()
        self._villages: dict[str, dict] = {}
        self._queue_rows: dict[tuple[str, str], dict] = {}
        self._economics: dict[str, dict] = {}
        self.village_recruitment_controls: dict[str, dict[str, QCheckBox]] = {}
        self.village_queue_duration_controls: dict[str, dict[str, QSpinBox]] = {}
        self.unit_rows: dict[tuple[str, str], dict] = {}
        self.village_summary_labels: dict[str, QLabel] = {}
        self.village_economics_labels: dict[str, dict[str, QLabel]] = {}
        self.village_rule_controls: dict[str, dict[str, QWidget]] = {}
        self._village_strategy: dict[str, str] = {}
        self._village_target_headers: dict[str, QLabel] = {}
        self._village_grids: dict[str, QGridLayout] = {}

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer_layout.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(14)

        # Header -------------------------------------------------------------
        header_layout = QHBoxLayout()
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title = QLabel("Rekrutacja")
        title.setObjectName("pageTitle")
        title_layout.addWidget(title)
        subtitle = QLabel(
            "Automat uzupełnia kolejki szkolenia w wybranych wioskach. "
            "Kolejki są sprawdzane co 3 minuty."
        )
        subtitle.setObjectName("secondaryText")
        subtitle.setWordWrap(True)
        title_layout.addWidget(subtitle)
        header_layout.addLayout(title_layout, stretch=1)
        self.check_recruitment_queues_btn = QPushButton("⟳  Odśwież kolejki")
        self.check_recruitment_queues_btn.setToolTip(
            "Odczytaj aktualne kolejki we wszystkich wioskach."
        )
        header_layout.addWidget(
            self.check_recruitment_queues_btn,
            alignment=Qt.AlignTop,
        )
        layout.addLayout(header_layout)

        # Automation settings card -----------------------------------------
        automation_card = QFrame()
        automation_card.setObjectName("card")
        automation_layout = QVBoxLayout(automation_card)
        automation_layout.setContentsMargins(18, 16, 18, 16)
        automation_layout.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        self.auto_recruitment_checkbox = _AutomationToggle()
        self.auto_recruitment_checkbox.setToolTip(
            "Główny przełącznik automatu. Poniżej wybierz wioski i jednostki, "
            "które mają być uzupełniane."
        )
        top_row.addWidget(self.auto_recruitment_checkbox)
        top_row.addSpacing(10)
        automation_hint = QLabel(
            "Sposób uzupełniania — <b>stałą partię</b> albo <b>docelowy czas "
            "kolejki</b> — ustawiasz osobno w karcie każdej wioski."
        )
        automation_hint.setObjectName("secondaryText")
        automation_hint.setWordWrap(True)
        top_row.addWidget(automation_hint, stretch=1)
        automation_layout.addLayout(top_row)
        layout.addWidget(automation_card)

        # Pending orders banner ----------------------------------------------
        self.pending_banner = QFrame()
        self.pending_banner.setObjectName("pendingBanner")
        banner_layout = QHBoxLayout(self.pending_banner)
        banner_layout.setContentsMargins(14, 10, 10, 10)
        self.pending_banner_label = QLabel()
        self.pending_banner_label.setWordWrap(True)
        banner_layout.addWidget(self.pending_banner_label, stretch=1)
        self.clear_recruitment_pending_btn = QPushButton("Wyczyść blokady…")
        self.clear_recruitment_pending_btn.setToolTip(
            "Wyłącza automat i usuwa blokady oczekujących zleceń. "
            "Najpierw sprawdź stan kolejek w grze."
        )
        self.clear_recruitment_pending_btn.setEnabled(False)
        banner_layout.addWidget(self.clear_recruitment_pending_btn)
        self.pending_banner.hide()
        layout.addWidget(self.pending_banner)

        # Villages -------------------------------------------------------------
        villages_header = QHBoxLayout()
        villages_title = QLabel("Wioski")
        villages_title.setObjectName("sectionTitle")
        villages_header.addWidget(villages_title)
        villages_header.addSpacing(8)
        self.recruitment_queue_status = QLabel(
            "Połącz z grą, aby odczytać kolejki."
        )
        self.recruitment_queue_status.setObjectName("secondaryText")
        self.recruitment_queue_status.setWordWrap(True)
        villages_header.addWidget(self.recruitment_queue_status, stretch=1)
        layout.addLayout(villages_header)

        self.village_cards_layout = QVBoxLayout()
        self.village_cards_layout.setSpacing(12)
        layout.addLayout(self.village_cards_layout)
        self.village_settings_empty_label = QLabel(
            "Połącz z grą, aby wykryć wioski i dostępne jednostki."
        )
        self.village_settings_empty_label.setObjectName("emptyState")
        self.village_settings_empty_label.setAlignment(Qt.AlignCenter)
        self.village_cards_layout.addWidget(self.village_settings_empty_label)

        self.recruitment_economics_status = QLabel(
            "Bilans na godzinę zakłada nieprzerwane szkolenie zaznaczonych "
            "jednostek (czas gry, szybkość świata i poziom budynku). "
            "Gdy kilka zaznaczonych typów dzieli koszary lub stajnię, "
            "koszt jest nieokreślony. Najedź na wartość, aby zobaczyć "
            "przychód i koszt rekrutacji."
        )
        self.recruitment_economics_status.setObjectName("footnote")
        self.recruitment_economics_status.setWordWrap(True)
        layout.addWidget(self.recruitment_economics_status)
        layout.addStretch()

    # Per-village rules ------------------------------------------------------
    def bind_settings(self, recruitment_settings: RecruitmentSettings):
        """Use account-wide values as defaults for villages without overrides."""
        self._recruitment_defaults = recruitment_settings
        self._settings_by_village = recruitment_settings.villages

    def _effective_rule(self, village_id: str, field: str):
        village_settings = self._settings_by_village.get(village_id)
        value = (
            getattr(village_settings, field)
            if village_settings is not None
            else None
        )
        return getattr(self._recruitment_defaults, field) if value is None else value

    def village_strategy(self, village_id: str) -> str:
        return self._village_strategy.get(
            village_id,
            self._effective_rule(village_id, "queue_strategy"),
        )

    def set_village_strategy(self, village_id: str, strategy: str):
        """Show a village's mode without emitting change signals."""
        if strategy not in ("count", "duration"):
            strategy = "count"
        self._village_strategy[village_id] = strategy
        controls = self.village_rule_controls.get(village_id)
        if controls is not None:
            controls["count"].setChecked(strategy == "count")
            controls["duration"].setChecked(strategy == "duration")
        self._apply_village_strategy(village_id)

    def _choose_village_strategy(self, village_id: str, strategy: str):
        previous = self.village_strategy(village_id)
        self.set_village_strategy(village_id, strategy)
        if previous != strategy:
            self.village_strategy_changed.emit(village_id, strategy)

    def _apply_village_strategy(self, village_id: str):
        controls = self.village_rule_controls.get(village_id)
        duration = self.village_strategy(village_id) == "duration"
        if controls is not None:
            controls["threshold"].setHidden(duration)
            controls["middle"].setHidden(duration)
            controls["prefix"].setText(
                "Uzupełniaj do celu z kolumny „Cel”, maks."
                if duration else "Gdy w kolejce zostanie"
            )
            controls["suffix"].setText("na zlecenie." if duration else "")
            controls["suffix"].setHidden(not duration)
        header = self._village_target_headers.get(village_id)
        if header is not None:
            header.setHidden(not duration)
        grid = self._village_grids.get(village_id)
        if grid is not None:
            grid.setColumnMinimumWidth(4, 105 if duration else 0)
        for row_village_id, unit_id in self.unit_rows:
            if row_village_id == village_id:
                self._refresh_unit_row(village_id, unit_id)

    # Village cards ----------------------------------------------------------
    def update_village_recruitment_settings(
        self,
        villages: list[dict],
        settings_by_village: dict,
    ):
        self._settings_by_village = settings_by_village
        structure = {
            str(village["id"]): {
                "name": village.get("name") or f"Wioska {village['id']}",
                "units": list(village.get("recruitment_units", [])),
            }
            for village in villages
        }
        if self._structure_signature(structure) != self._structure_signature(
            self._villages
        ):
            self._villages = structure
            self._rebuild_village_cards()
        else:
            self._sync_controls_with_settings()

    @staticmethod
    def _structure_signature(villages: dict[str, dict]) -> tuple:
        return tuple(
            (
                village_id,
                village["name"],
                tuple(
                    (
                        str(unit["unit_id"]),
                        unit.get("unit"),
                        unit.get("building"),
                        unit.get("building_available") is not False,
                    )
                    for unit in village["units"]
                ),
            )
            for village_id, village in villages.items()
        )

    def _village_settings(self, village_id: str):
        return self._settings_by_village.get(
            village_id,
            VillageRecruitmentSettings(),
        )

    def _merge_village(
        self,
        village_id: str,
        name: str | None,
        unit: dict | None = None,
    ) -> bool:
        changed = False
        village = self._villages.get(village_id)
        if village is None:
            village = {"name": name or f"Wioska {village_id}", "units": []}
            self._villages[village_id] = village
            changed = True
        if unit is not None:
            unit_id = str(unit["unit_id"])
            if all(
                str(existing["unit_id"]) != unit_id
                for existing in village["units"]
            ):
                village["units"].append({**unit, "unit_id": unit_id})
                changed = True
        return changed

    def _rebuild_village_cards(self):
        while self.village_cards_layout.count():
            item = self.village_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            if widget is self.village_settings_empty_label:
                widget.hide()
            else:
                widget.deleteLater()

        self.village_recruitment_controls.clear()
        self.village_queue_duration_controls.clear()
        self.unit_rows.clear()
        self.village_summary_labels.clear()
        self.village_economics_labels.clear()
        self.village_rule_controls.clear()
        self._village_strategy.clear()
        self._village_target_headers.clear()
        self._village_grids.clear()

        if not self._villages:
            self.village_cards_layout.addWidget(
                self.village_settings_empty_label
            )
            self.village_settings_empty_label.show()
            return

        for village_id, village in self._villages.items():
            self.village_cards_layout.addWidget(
                self._build_village_card(village_id, village)
            )
            self._apply_village_strategy(village_id)
        self._apply_queue_rows()
        self._apply_economics()

    def _build_village_card(self, village_id: str, village: dict) -> QFrame:
        village_settings = self._village_settings(village_id)
        card = QFrame()
        card.setObjectName("villageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        name_label = QLabel(village["name"])
        name_label.setObjectName("villageName")
        header.addWidget(name_label)
        header.addSpacing(10)
        summary = QLabel()
        summary.setObjectName("secondaryText")
        header.addWidget(summary, stretch=1)
        enabled = QCheckBox("Automat w tej wiosce")
        enabled.setChecked(village_settings.enabled)
        enabled.setToolTip(
            "Włącz lub wyłącz automatyczną rekrutację tylko w tej wiosce."
        )
        enabled.toggled.connect(
            lambda checked, current_id=village_id: (
                self._refresh_village_summary(current_id),
                [
                    self._refresh_unit_row(current_id, unit_id)
                    for unit_id in list(
                        self.village_recruitment_controls.get(current_id, {})
                    )
                    if unit_id != "enabled"
                ],
                self.village_enabled_toggled.emit(current_id, checked),
            )
        )
        header.addWidget(enabled)
        card_layout.addLayout(header)
        self.village_summary_labels[village_id] = summary
        card_layout.addWidget(self._build_rule_strip(village_id))
        header.insertWidget(
            header.count() - 1,
            self.village_rule_controls[village_id]["copy"],
        )
        header.insertSpacing(header.count() - 1, 8)

        units_grid = QGridLayout()
        units_grid.setHorizontalSpacing(18)
        units_grid.setVerticalSpacing(2)
        self._village_grids[village_id] = units_grid
        units_grid.setColumnStretch(0, 1)
        for column, width in enumerate((0, 90, 80, 95, 105, 180)):
            if width:
                units_grid.setColumnMinimumWidth(column, width)
        headers = (
            "Uzupełniaj", "Budynek", "W kolejce", "Czas kolejki",
            "Cel", "Status",
        )
        for column, text in enumerate(headers):
            header_label = QLabel(text.upper())
            header_label.setObjectName("columnHeader")
            units_grid.addWidget(header_label, 0, column)
            if text == "Cel":
                self._village_target_headers[village_id] = header_label

        controls = {"enabled": enabled}
        duration_controls = {}
        unavailable = []
        grid_row = 1
        for unit in village["units"]:
            unit_id = str(unit["unit_id"])
            available = unit.get("building_available") is not False
            selected = unit_id in village_settings.units
            if not available and not selected:
                unavailable.append(
                    f"{unit.get('unit', unit_id)} ({unit.get('building', '?')})"
                )
                continue

            checkbox = QCheckBox(str(unit.get("unit", unit_id)))
            checkbox.setChecked(selected)
            if not available:
                checkbox.setToolTip(
                    "W tej wiosce nie ma odpowiedniego budynku — "
                    "automat pominie tę jednostkę. Możesz ją odznaczyć."
                )
            building = QLabel(str(unit.get("building", "")))
            building.setObjectName("secondaryText")
            count = QLabel("—")
            duration = QLabel("—")
            target = QSpinBox()
            target.setRange(1, 1440)
            target.setSuffix(" min")
            target.setFixedWidth(95)
            target.setValue(
                village_settings.queue_minutes_by_unit.get(
                    unit_id,
                    DEFAULT_RECRUITMENT_QUEUE_MINUTES,
                )
            )
            target.setEnabled(available)
            target.setToolTip(
                "Docelowa długość kolejki tej jednostki w tej wiosce."
            )
            status = QLabel()
            _set_status_chip(status, "Nieodczytana", "muted")

            checkbox.toggled.connect(
                lambda checked, current_id=village_id,
                current_unit_id=unit_id: (
                    self._refresh_unit_row(current_id, current_unit_id),
                    self._refresh_village_summary(current_id),
                    self.unit_toggled.emit(
                        current_id, current_unit_id, checked
                    ),
                )
            )
            target.valueChanged.connect(
                lambda minutes, current_id=village_id,
                current_unit_id=unit_id:
                    self.queue_minutes_changed.emit(
                        current_id, current_unit_id, minutes
                    )
            )

            for column, widget in enumerate(
                (checkbox, building, count, duration, target, status)
            ):
                units_grid.addWidget(
                    widget,
                    grid_row,
                    column,
                    alignment=Qt.AlignLeft | Qt.AlignVCenter,
                )
            controls[unit_id] = checkbox
            duration_controls[unit_id] = target
            self.unit_rows[(village_id, unit_id)] = {
                "checkbox": checkbox,
                "building": building,
                "count": count,
                "duration": duration,
                "target": target,
                "status": status,
                "available": available,
            }
            units_grid.setRowMinimumHeight(grid_row, 30)
            grid_row += 1

        if grid_row == 1:
            empty = QLabel(
                "Brak wykrytych jednostek — połącz z grą, aby je odczytać."
                if not unavailable
                else "Żadna jednostka nie ma tu wymaganego budynku."
            )
            empty.setObjectName("secondaryText")
            card_layout.addWidget(empty)
        else:
            card_layout.addLayout(units_grid)
        if unavailable:
            note = QLabel("Brak budynku: " + ", ".join(unavailable))
            note.setObjectName("footnote")
            note.setWordWrap(True)
            card_layout.addWidget(note)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        card_layout.addWidget(divider)
        economics = QGridLayout()
        economics.setHorizontalSpacing(12)
        economics_title = QLabel("BILANS / H")
        economics_title.setObjectName("columnHeader")
        economics_title.setToolTip(self.recruitment_economics_status.text())
        economics.addWidget(economics_title, 0, 0, alignment=Qt.AlignVCenter)
        resource_labels = {}
        for column, resource in enumerate(_RECRUITMENT_RESOURCE_LABELS, 1):
            label = QLabel()
            label.setTextFormat(Qt.RichText)
            economics.addWidget(label, 0, column)
            economics.setColumnStretch(column, 1)
            resource_labels[resource] = label
        card_layout.addLayout(economics)

        self.village_recruitment_controls[village_id] = controls
        self.village_queue_duration_controls[village_id] = duration_controls
        self.village_economics_labels[village_id] = resource_labels
        self._refresh_village_summary(village_id)
        return card

    def _build_rule_strip(self, village_id: str) -> QFrame:
        strip = QFrame()
        strip.setObjectName("ruleStrip")
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(12, 8, 10, 8)
        strip_layout.setSpacing(8)

        count_button = QPushButton("Stała partia")
        count_button.setObjectName("segmentLeft")
        duration_button = QPushButton("Docelowy czas kolejki")
        duration_button.setObjectName("segmentRight")
        segment_layout = QHBoxLayout()
        segment_layout.setSpacing(0)
        for strategy, button in (
            ("count", count_button),
            ("duration", duration_button),
        ):
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            bold_font = QFont(button.font())
            bold_font.setBold(True)
            button.setMinimumWidth(
                QFontMetrics(bold_font).horizontalAdvance(button.text()) + 34
            )
            button.clicked.connect(
                lambda _checked=False, current_id=village_id, value=strategy:
                    self._choose_village_strategy(current_id, value)
            )
            segment_layout.addWidget(button)
        strip_layout.addLayout(segment_layout)
        strip_layout.addSpacing(12)

        prefix = QLabel()
        strip_layout.addWidget(prefix)
        threshold = QSpinBox()
        threshold.setRange(0, 100)
        threshold.setSuffix(" szt.")
        threshold.setFixedWidth(88)
        threshold.setValue(self._effective_rule(village_id, "queue_threshold"))
        threshold.setToolTip(
            "Wartość 0 oznacza: uzupełniaj dopiero, gdy kolejka jest pusta."
        )
        threshold.valueChanged.connect(
            lambda value, current_id=village_id:
                self.village_threshold_changed.emit(current_id, value)
        )
        strip_layout.addWidget(threshold)
        middle = QLabel("lub mniej — dodaj")
        strip_layout.addWidget(middle)
        batch = QSpinBox()
        batch.setRange(1, 100)
        batch.setSuffix(" jedn.")
        batch.setFixedWidth(92)
        batch.setValue(self._effective_rule(village_id, "batch_size"))
        batch.setToolTip(
            "Zlecenie zostanie wysłane tylko, gdy wioska ma surowce "
            "na całą partię."
        )
        batch.valueChanged.connect(
            lambda value, current_id=village_id:
                self.village_batch_changed.emit(current_id, value)
        )
        strip_layout.addWidget(batch)
        suffix = QLabel()
        strip_layout.addWidget(suffix)
        strip_layout.addStretch()

        copy_button = QPushButton("Kopiuj do innych wiosek")
        copy_button.setObjectName("linkButton")
        copy_button.setCursor(Qt.PointingHandCursor)
        copy_button.setToolTip(
            "Skopiuj sposób uzupełniania, próg i partię do wszystkich "
            "pozostałych wiosek. Zaznaczone jednostki i cele w minutach "
            "pozostaną bez zmian."
        )
        copy_button.clicked.connect(
            lambda _checked=False, current_id=village_id:
                self.village_rule_copy_requested.emit(current_id)
        )

        self.village_rule_controls[village_id] = {
            "count": count_button,
            "duration": duration_button,
            "prefix": prefix,
            "threshold": threshold,
            "middle": middle,
            "batch": batch,
            "suffix": suffix,
            "copy": copy_button,
        }
        strategy = self._effective_rule(village_id, "queue_strategy")
        self._village_strategy[village_id] = strategy
        count_button.setChecked(strategy == "count")
        duration_button.setChecked(strategy == "duration")
        return strip

    def _sync_controls_with_settings(self):
        for village_id, controls in self.village_rule_controls.items():
            for key, field in (
                ("threshold", "queue_threshold"),
                ("batch", "batch_size"),
            ):
                controls[key].blockSignals(True)
                controls[key].setValue(self._effective_rule(village_id, field))
                controls[key].blockSignals(False)
            self.set_village_strategy(
                village_id,
                self._effective_rule(village_id, "queue_strategy"),
            )
        for village_id, controls in self.village_recruitment_controls.items():
            village_settings = self._village_settings(village_id)
            controls["enabled"].blockSignals(True)
            controls["enabled"].setChecked(village_settings.enabled)
            controls["enabled"].blockSignals(False)
            for unit_id, checkbox in controls.items():
                if unit_id == "enabled":
                    continue
                selected = unit_id in village_settings.units
                checkbox.blockSignals(True)
                checkbox.setChecked(selected)
                checkbox.blockSignals(False)
                self._refresh_unit_row(village_id, unit_id)
            self._refresh_village_summary(village_id)

    def _village_automation_enabled(self, village_id: str) -> bool:
        controls = self.village_recruitment_controls.get(village_id)
        if controls is not None and "enabled" in controls:
            return controls["enabled"].isChecked()
        return self._village_settings(village_id).enabled

    def _refresh_village_summary(self, village_id: str):
        label = self.village_summary_labels.get(village_id)
        controls = self.village_recruitment_controls.get(village_id)
        if label is None or controls is None:
            return
        if not controls["enabled"].isChecked():
            label.setText("Automat wyłączony w tej wiosce")
            return
        selected = [
            checkbox.text()
            for unit_id, checkbox in controls.items()
            if unit_id != "enabled" and checkbox.isChecked()
        ]
        label.setText(
            "Uzupełniane: " + ", ".join(selected)
            if selected
            else "Nic nie wybrano — zaznacz jednostki do uzupełniania"
        )

    # Queue state ------------------------------------------------------------
    def update_recruitment_queues(
        self,
        rows: list[dict],
        pending_keys: set[str] | None = None,
    ):
        self._recruitment_pending_keys = set(pending_keys or ())
        self._queue_rows = {}
        structure_changed = False
        for row in rows:
            village_id = str(row["village_id"])
            unit_id = str(row.get("unit_id", row.get("unit_type", row["unit"])))
            self._queue_rows[(village_id, unit_id)] = row
            structure_changed |= self._merge_village(
                village_id,
                row.get("village"),
                {
                    "unit_id": unit_id,
                    "unit": row["unit"],
                    "building": row.get("building", ""),
                    "building_available": row.get("building_available"),
                },
            )
        if structure_changed:
            self._rebuild_village_cards()
        else:
            self._apply_queue_rows()

        inactive_queues = sum(
            1 for row in rows
            if row.get("building_available") is not False
            and not row["count"]
            and not row.get("other_count", 0)
        )
        unavailable_buildings = sum(
            1 for row in rows if row.get("building_available") is False
        )
        village_count = len({row["village_id"] for row in rows})
        summary_parts = [
            f"Kontrola {datetime.now():%H:%M}",
            f"{village_count} wiosek",
            f"{len(rows) - inactive_queues - unavailable_buildings} "
            "aktywnych kolejek",
        ]
        if inactive_queues:
            summary_parts.append(f"{inactive_queues} bez rekrutacji")
        self.recruitment_queue_status.setText(
            "  ·  ".join(summary_parts) + "  ·  następna za 3 min"
        )
        self._refresh_pending_banner()

    def _is_pending(self, village_id: str, unit_id: str, unit: str) -> bool:
        return bool(
            {
                _recruitment_queue_key(village_id, unit_id),
                _recruitment_queue_key(village_id, unit),
            } & self._recruitment_pending_keys
        )

    def _apply_queue_rows(self):
        for key in self.unit_rows:
            self._refresh_unit_row(*key)

    def _refresh_unit_row(self, village_id: str, unit_id: str):
        widgets = self.unit_rows.get((village_id, unit_id))
        if widgets is None:
            return
        checkbox = widgets["checkbox"]
        selected = checkbox.isChecked()
        checkbox.setObjectName("" if selected else "unitUnselected")
        checkbox.style().unpolish(checkbox)
        checkbox.style().polish(checkbox)
        widgets["target"].setHidden(
            self.village_strategy(village_id) != "duration" or not selected
        )

        queue = self._queue_rows.get((village_id, unit_id))
        count_text, duration_text = "—", "—"
        status_label = widgets["status"]
        status_label.setToolTip("")
        if queue is not None and widgets["available"]:
            count_text = f"{queue['count']} szt."
            duration_text = _format_queue_minutes(
                recruitment_queue_duration_seconds(queue)
            )
        if self._is_pending(village_id, unit_id, checkbox.text()):
            status = ("Wysłano · potwierdzanie", "pending")
            status_label.setToolTip(
                "Zlecenie wysłano. Automat nie ponowi go, dopóki "
                "kolejka nie potwierdzi przyjęcia jednostek."
            )
        elif not widgets["available"]:
            status = ("Brak budynku · pominięta", "muted")
        elif queue is None:
            status = ("Nieodczytana", "muted")
        elif queue["count"]:
            status = ("Rekrutacja trwa", "active")
        elif not selected:
            status = None
        elif not self._village_automation_enabled(village_id):
            status = ("Automat wioski wyłączony", "muted")
            status_label.setToolTip(
                "Włącz „Automat w tej wiosce”, aby uzupełniać tę kolejkę."
            )
        elif queue.get("other_count", 0):
            status = ("Dołączy do kolejki", "waiting")
            status_label.setToolTip(
                f"W budynku szkoli się {queue['other_count']} szt. innych "
                "jednostek. Automat i tak zamawia tę jednostkę — gra ustawi "
                "ją w kolejce budynku za nimi."
            )
        else:
            status = ("Brak rekrutacji", "waiting")
        widgets["count"].setText(count_text)
        widgets["duration"].setText(duration_text)
        if status is None:
            status_label.setText("")
            status_label.setStyleSheet("")
        else:
            _set_status_chip(status_label, *status)

    def _refresh_pending_banner(self):
        pending_count = len(self._recruitment_pending_keys)
        self.clear_recruitment_pending_btn.setEnabled(bool(pending_count))
        self.pending_banner.setVisible(bool(pending_count))
        if pending_count:
            noun = "zlecenie czeka" if pending_count == 1 else "zlecenia czekają"
            self.pending_banner_label.setText(
                f"<b>{pending_count} {noun}</b> na potwierdzenie przez grę. "
                "Automat nie ponowi tych jednostek, dopóki kolejka nie "
                "potwierdzi przyjęcia."
            )

    # Economics --------------------------------------------------------------
    def update_recruitment_economics(self, rows: list[dict]):
        self._economics = {}
        structure_changed = False
        for row in rows:
            village_id = str(row.get("village_id", row["village"]))
            self._economics.setdefault(village_id, {})[row["resource"]] = row
            structure_changed |= self._merge_village(village_id, row["village"])
        if structure_changed:
            self._rebuild_village_cards()
        else:
            self._apply_economics()

    def _apply_economics(self):
        for village_id, labels in self.village_economics_labels.items():
            resources = self._economics.get(village_id, {})
            for resource, label in labels.items():
                row = resources.get(resource)
                if row is None:
                    label.setText(
                        f"<span style='color:#64748b'>{resource}</span>&nbsp; "
                        "<span style='color:#94a3b8'>—</span>"
                    )
                    label.setToolTip("Brak danych o przychodzie.")
                    continue
                net = row["net_rate"]
                color = (
                    "#94a3b8" if net is None
                    else "#b42318" if net < 0
                    else "#237a45"
                )
                label.setText(
                    f"<span style='color:#64748b'>{resource}</span>&nbsp; "
                    f"<b style='color:{color}'>{_format_hourly_rate(net)}</b>"
                )
                tooltip = (
                    f"Przychód  {_format_hourly_rate(row['production_rate'])}\n"
                    "Rekrutacja  "
                    f"{_format_hourly_rate(row['recruitment_cost'], signed=False)}\n"
                    f"Bilans  {_format_hourly_rate(net)}"
                )
                if row.get("last_updated"):
                    tooltip += (
                        f"\nOstatni odczyt: {row['last_updated']:%Y-%m-%d %H:%M}"
                    )
                label.setToolTip(tooltip)

    def clear_recruitment_economics(self):
        self._economics = {}
        self._apply_economics()

    def clear_recruitment_queues(self):
        self._recruitment_pending_keys.clear()
        self._queue_rows = {}
        self._apply_queue_rows()
        self._refresh_pending_banner()
        self.recruitment_queue_status.setText(
            "Połącz z grą, aby odczytać kolejki."
        )


class MillingWidget(QWidget):
    """Per-village NPC grain conversion controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Mielenie zboża")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        layout.addWidget(title)

        description = QLabel(
            "Skonfiguruj osobno dwie metody dla każdej wioski: opróżnianie "
            "spichlerza przy wysokim poziomie zboża oraz uzupełnianie go, "
            "gdy zboże spadnie do niskiego progu. Uzupełnianie wykorzystuje "
            "dostępny zapas; jeśli nie mieści się cały w spichlerzu, "
            "nadwyżka pozostaje podzielona między inne surowce. Każda "
            "wymiana NPC kosztuje 5 złota."
        )
        description.setObjectName("secondaryText")
        description.setWordWrap(True)
        layout.addWidget(description)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Zachowaj co najmniej:"))
        self.minimum_gold_balance = QSpinBox()
        self.minimum_gold_balance.setRange(0, 1000000)
        self.minimum_gold_balance.setSuffix(" złota")
        self.minimum_gold_balance.setToolTip(
            "Wymiana za 5 złota nastąpi tylko wtedy, gdy po jej opłaceniu "
            "pozostanie co najmniej ustawiona rezerwa."
        )
        controls.addWidget(self.minimum_gold_balance)
        self.gold_balance_status = QLabel("Saldo złota: brak danych")
        self.gold_balance_status.setObjectName("secondaryText")
        controls.addWidget(self.gold_balance_status)
        controls.addStretch()
        self.clear_pending_btn = QPushButton("Wyczyść oczekujące wymiany…")
        self.clear_pending_btn.setEnabled(False)
        self.clear_pending_btn.setToolTip(
            "Usuń blokadę niepotwierdzonej wymiany. Przed wyczyszczeniem "
            "sprawdź stan surowców w grze, aby uniknąć ponownego kosztu."
        )
        controls.addWidget(self.clear_pending_btn)
        layout.addLayout(controls)

        self.status = QLabel("Połącz z grą, aby odczytać zapasy wiosek.")
        self.status.setObjectName("secondaryText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        settings_group = QGroupBox("Automat — ustawienia osobno dla wioski")
        settings_layout = QVBoxLayout(settings_group)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setMinimumHeight(190)
        self.settings_scroll.setMaximumHeight(330)
        self.settings_content = QWidget()
        self.settings_grid = QGridLayout(self.settings_content)
        self.settings_grid.setContentsMargins(6, 6, 6, 6)
        self.settings_grid.setHorizontalSpacing(12)
        self.settings_grid.setVerticalSpacing(8)
        headers = (
            "Wioska",
            "Opróżnij",
            "Próg opróżniania",
            "Spichlerz",
            "Uzupełnij",
            "Próg uzupełniania",
            "Glina %",
            "Żelazo %",
            "Drewno %",
            "Suma",
        )
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet("font-weight: 600; color: #475569;")
            self.settings_grid.addWidget(label, 0, column)
        self.empty_label = QLabel(
            "Połącz z grą, aby skonfigurować oba automaty dla każdej wioski."
        )
        self.empty_label.setObjectName("secondaryText")
        self.settings_scroll.setWidget(self.settings_content)
        settings_layout.addWidget(self.settings_scroll)
        layout.addWidget(settings_group)
        layout.addStretch()

        self.village_controls: dict[str, dict[str, QWidget]] = {}
        self._village_names: dict[str, str] = {}
        self._row_count = 1

    def update_villages(self, villages: list[dict], settings_by_village: dict):
        names = {
            str(village["id"]): (
                village.get("name") or f"Wioska {village['id']}"
            )
            for village in villages
        }
        grain_percentages = {
            str(village["id"]): village.get("grain_percent")
            for village in villages
        }
        if names and names == self._village_names:
            for village_id, village_settings in settings_by_village.items():
                controls = self.village_controls.get(village_id)
                if controls is None:
                    continue
                for field, value in (
                    ("enabled", village_settings.enabled),
                    ("trigger_percent", village_settings.trigger_percent),
                    ("refill_enabled", village_settings.refill_enabled),
                    (
                        "refill_trigger_percent",
                        village_settings.refill_trigger_percent,
                    ),
                    ("clay_percent", village_settings.clay_percent),
                    ("iron_percent", village_settings.iron_percent),
                    ("wood_percent", village_settings.wood_percent),
                ):
                    control = controls[field]
                    control.blockSignals(True)
                    if field in {"enabled", "refill_enabled"}:
                        control.setChecked(value)
                    else:
                        control.setValue(value)
                    control.blockSignals(False)
                percentage = grain_percentages.get(village_id)
                controls["grain_percent"].setText(
                    "—" if percentage is None else f"{percentage:.0f}%"
                )
                self.update_ratio_total(village_id)
            return

        for row in range(1, self._row_count):
            for column in range(10):
                item = self.settings_grid.itemAtPosition(row, column)
                if item is not None and item.widget() is not None:
                    widget = item.widget()
                    if widget is self.empty_label:
                        widget.hide()
                        widget.setParent(self.settings_content)
                    else:
                        widget.deleteLater()
        self.village_controls.clear()
        self._village_names = names
        self._row_count = 1

        if not villages:
            self.settings_grid.addWidget(self.empty_label, 1, 0, 1, 8)
            self.empty_label.show()
            self._row_count = 2
            return

        self.empty_label.hide()
        for row_index, village in enumerate(villages, start=1):
            village_id = str(village["id"])
            village_settings = settings_by_village[village_id]
            name = QLabel(names[village_id])
            self.settings_grid.addWidget(name, row_index, 0)

            enabled = QCheckBox("Włącz")
            enabled.setChecked(village_settings.enabled)
            enabled.setToolTip(
                "Opróżnia zboże według udziałów, gdy osiągnie ustawiony "
                "wysoki próg."
            )
            self.settings_grid.addWidget(enabled, row_index, 1)

            trigger = QSpinBox()
            trigger.setRange(1, 100)
            trigger.setSuffix(" %")
            trigger.setValue(village_settings.trigger_percent)
            trigger.setToolTip(
                "Uruchom wymianę, gdy zboże osiągnie ten procent pojemności "
                "spichlerza."
            )
            self.settings_grid.addWidget(trigger, row_index, 2)

            grain_percent = village.get("grain_percent")
            grain_status = QLabel(
                "—" if grain_percent is None else f"{grain_percent:.0f}%"
            )
            self.settings_grid.addWidget(grain_status, row_index, 3)

            refill_enabled = QCheckBox("Włącz")
            refill_enabled.setChecked(village_settings.refill_enabled)
            refill_enabled.setToolTip(
                "Przemiela dostępne surowce na zboże przy niskim poziomie "
                "zboża. Jeśli suma zapasów przekracza wolne miejsce, "
                "nadwyżka pozostaje w innych surowcach."
            )
            self.settings_grid.addWidget(refill_enabled, row_index, 4)

            refill_trigger = QSpinBox()
            refill_trigger.setRange(1, 100)
            refill_trigger.setSuffix(" %")
            refill_trigger.setValue(village_settings.refill_trigger_percent)
            refill_trigger.setToolTip(
                "Uzupełnij zboże, gdy jego poziom spadnie do tego procentu "
                "pojemności spichlerza lub niżej."
            )
            self.settings_grid.addWidget(refill_trigger, row_index, 5)

            ratios = {}
            for column, field, label in (
                (6, "clay_percent", "Glina"),
                (7, "iron_percent", "Żelazo"),
                (8, "wood_percent", "Drewno"),
            ):
                value = QSpinBox()
                value.setRange(0, 100)
                value.setSuffix(" %")
                value.setValue(getattr(village_settings, field))
                value.setToolTip(
                    f"Docelowy udział {label.lower()} w całym zapasie "
                    "po wymianie. Udziały muszą sumować się do 100%."
                )
                self.settings_grid.addWidget(value, row_index, column)
                ratios[field] = value

            ratio_total = QLabel()
            self.settings_grid.addWidget(ratio_total, row_index, 9)
            self.village_controls[village_id] = {
                "enabled": enabled,
                "grain_percent": grain_status,
                "trigger_percent": trigger,
                "refill_enabled": refill_enabled,
                "refill_trigger_percent": refill_trigger,
                **ratios,
                "ratio_total": ratio_total,
            }
            self.update_ratio_total(village_id)
            self._row_count = row_index + 1

    def update_ratio_total(self, village_id: str):
        controls = self.village_controls.get(village_id)
        if not controls:
            return
        total = sum(
            controls[field].value()
            for field in ("clay_percent", "iron_percent", "wood_percent")
        )
        label = controls["ratio_total"]
        label.setText(f"{total}%")
        label.setStyleSheet(
            "color: #15803d; font-weight: 600;"
            if total == 100
            else "color: #b91c1c; font-weight: 600;"
        )
        label.setToolTip(
            "Udziały są poprawne."
            if total == 100
            else "Suma udziałów musi wynosić dokładnie 100%."
        )

    def set_gold_balance(self, balance: int | None):
        self.gold_balance_status.setText(
            "Saldo złota: brak danych"
            if balance is None
            else f"Saldo złota: {balance:,}"
        )


class VacationWidget(QWidget):
    """Daily rolling vacation-mode safeguard controls and status."""

    enabled_toggled = Signal(bool)
    setting_changed = Signal(str, object)
    check_requested = Signal()
    plan_requested = Signal()
    cancel_requested = Signal()

    _STATE_CHIPS = {
        "none": ("Brak zaplanowanych wakacji", "muted"),
        "planned": ("Wakacje zaplanowane", "pending"),
        "active": ("Wakacje aktywne", "active"),
        "unknown": ("Stan nieznany", "muted"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer_layout.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(14)

        title = QLabel("Wakacje")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Automat codziennie planuje tryb wakacyjny (włącza się po 24 h) "
            "i tuż przed aktywacją anuluje go, jeśli nie nadchodzą ataki. "
            "Gdy nadchodzą — zostawia wakacje, więc atakujący zastaną Cię "
            "na wakacjach. Anulowanie zwraca dzień wakacji."
        )
        subtitle.setObjectName("secondaryText")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Status card ---------------------------------------------------------
        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(18, 16, 18, 16)
        status_layout.setSpacing(12)
        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        self.enabled_toggle = _AutomationToggle()
        self.enabled_toggle.toggled.connect(self.enabled_toggled.emit)
        top_row.addWidget(self.enabled_toggle)
        self.state_chip = QLabel()
        top_row.addWidget(self.state_chip)
        top_row.addStretch()
        self.check_button = QPushButton("⟳  Sprawdź teraz")
        self.check_button.clicked.connect(self.check_requested.emit)
        top_row.addWidget(self.check_button)
        status_layout.addLayout(top_row)

        details = QGridLayout()
        details.setHorizontalSpacing(18)
        details.setVerticalSpacing(6)
        self.detail_labels: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate((
            ("activation", "Aktywacja wakacji"),
            ("next_action", "Następny krok"),
            ("attacks", "Nadchodzące wrogie ruchy"),
            ("days", "Wykorzystane dni wakacji"),
        )):
            caption_label = QLabel(caption.upper())
            caption_label.setObjectName("columnHeader")
            details.addWidget(caption_label, row, 0)
            value = QLabel("—")
            value.setWordWrap(True)
            details.addWidget(value, row, 1)
            self.detail_labels[key] = value
        details.setColumnStretch(1, 1)
        status_layout.addLayout(details)

        manual_row = QHBoxLayout()
        manual_hint = QLabel("Ręcznie:")
        manual_hint.setObjectName("fieldLabel")
        manual_row.addWidget(manual_hint)
        self.plan_button = QPushButton("Zaplanuj wakacje teraz…")
        self.plan_button.clicked.connect(self.plan_requested.emit)
        manual_row.addWidget(self.plan_button)
        self.cancel_button = QPushButton("Anuluj zaplanowane…")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        manual_row.addWidget(self.cancel_button)
        manual_row.addStretch()
        status_layout.addLayout(manual_row)
        layout.addWidget(status_card)

        # Settings card -------------------------------------------------------
        settings_card = QFrame()
        settings_card.setObjectName("card")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(18, 16, 18, 16)
        settings_layout.setSpacing(10)
        settings_title = QLabel("Ustawienia")
        settings_title.setObjectName("sectionTitle")
        settings_layout.addWidget(settings_title)

        def sentence(*parts) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(8)
            for part in parts:
                row.addWidget(QLabel(part) if isinstance(part, str) else part)
            row.addStretch()
            settings_layout.addLayout(row)
            return row

        self.plan_time = QTimeEdit()
        self.plan_time.setDisplayFormat("HH:mm")
        self.plan_time.setFixedWidth(95)
        self.plan_window = self._spin(1, 180, " min", 90)
        sentence(
            "Planuj wakacje codziennie o", self.plan_time,
            "(czas komputera), w ciągu", self.plan_window, ".",
        )
        self.vacation_days = self._spin(1, 14, " dni", 80)
        sentence("Długość wakacji:", self.vacation_days)
        self.decision_minutes = self._spin(2, 1440, " min", 100)
        sentence(
            "Decyzję podejmij", self.decision_minutes,
            "przed aktywacją wakacji.",
        )
        self.attack_threshold = self._spin(1, 1000, "", 80)
        sentence(
            "Zostaw wakacje, gdy liczba nadchodzących wrogich ruchów wynosi "
            "co najmniej",
            self.attack_threshold, ".",
        )
        self.movement_checkboxes: dict[str, QCheckBox] = {}
        movement_row = QHBoxLayout()
        movement_row.setSpacing(14)
        movement_row.addWidget(QLabel("Liczone ruchy:"))
        for movement_type in VACATION_MOVEMENT_TYPES:
            checkbox = QCheckBox(movement_type)
            checkbox.toggled.connect(self._emit_movement_types)
            movement_row.addWidget(checkbox)
            self.movement_checkboxes[movement_type] = checkbox
        movement_row.addStretch()
        settings_layout.addLayout(movement_row)

        self.plan_time.timeChanged.connect(
            lambda value: self.setting_changed.emit(
                "plan_time", value.toString("HH:mm")
            )
        )
        for field, spin_box in (
            ("plan_window_minutes", self.plan_window),
            ("vacation_days", self.vacation_days),
            ("decision_minutes_before", self.decision_minutes),
            ("attack_threshold", self.attack_threshold),
        ):
            spin_box.valueChanged.connect(
                lambda value, current_field=field:
                    self.setting_changed.emit(current_field, value)
            )
        layout.addWidget(settings_card)

        warning = QLabel(
            "<b>Ważne:</b> aplikacja musi być włączona i połączona z grą "
            "w chwili decyzji — inaczej nikt nie anuluje wakacji i włączą się "
            "same. Gdy wakacje się włączą: zużywasz dni wakacji, oddajesz oazy, "
            "posiłki innych graczy wracają, a produkcja drewna, gliny i żelaza "
            "spada do zera. Atak wysłany już po decyzji o anulowaniu zastanie "
            "Cię bez wakacji."
        )
        warning.setObjectName("footnote")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        # Event log -------------------------------------------------------------
        log_title = QLabel("Dziennik")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(160)
        layout.addWidget(self.log_list)
        layout.addStretch()

        self.set_disconnected()

    @staticmethod
    def _spin(minimum: int, maximum: int, suffix: str, width: int) -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(minimum, maximum)
        spin_box.setSuffix(suffix)
        spin_box.setFixedWidth(width)
        return spin_box

    def _emit_movement_types(self, *_args):
        self.setting_changed.emit(
            "counted_movement_types",
            {
                movement_type
                for movement_type, checkbox in self.movement_checkboxes.items()
                if checkbox.isChecked()
            },
        )

    def load_settings(self, settings: VacationSettings):
        widgets = [
            self.enabled_toggle, self.plan_time, self.plan_window,
            self.vacation_days, self.decision_minutes, self.attack_threshold,
            *self.movement_checkboxes.values(),
        ]
        for widget in widgets:
            widget.blockSignals(True)
        self.enabled_toggle.setChecked(settings.enabled)
        self.plan_time.setTime(QTime.fromString(settings.plan_time, "HH:mm"))
        self.plan_window.setValue(settings.plan_window_minutes)
        self.vacation_days.setValue(settings.vacation_days)
        self.decision_minutes.setValue(settings.decision_minutes_before)
        self.attack_threshold.setValue(settings.attack_threshold)
        for movement_type, checkbox in self.movement_checkboxes.items():
            checkbox.setChecked(movement_type in settings.counted_movement_types)
        for widget in widgets:
            widget.blockSignals(False)

    def set_state(self, state: dict | None, counted_attacks: int | None):
        connected = state is not None
        for button in (self.check_button, self.plan_button, self.cancel_button):
            button.setEnabled(connected)
        if state is None:
            _set_status_chip(self.state_chip, "Nie połączono z grą", "muted")
            for key in ("activation", "attacks", "days"):
                self.detail_labels[key].setText("—")
            return
        vacation_state = state["vacation_state"]
        if vacation_state == 0:
            kind = "none"
        elif vacation_state == 1 and state["server_time"] < state["start"]:
            kind = "planned"
        elif vacation_state in (1, 2):
            kind = "active"
        else:
            kind = "unknown"
        _set_status_chip(self.state_chip, *self._STATE_CHIPS[kind])
        self.plan_button.setEnabled(kind == "none")
        self.cancel_button.setEnabled(kind == "planned")
        if kind == "planned":
            remaining = state["start"] - state["server_time"]
            hours, minutes = divmod(int(remaining // 60), 60)
            self.detail_labels["activation"].setText(
                f"{datetime.fromtimestamp(state['start']):%d.%m %H:%M:%S} "
                f"(za {hours} h {minutes:02d} min)"
            )
        elif kind == "active" and state["end"] > 0:
            self.detail_labels["activation"].setText(
                f"Trwają do {datetime.fromtimestamp(state['end']):%d.%m %H:%M}"
            )
        else:
            self.detail_labels["activation"].setText("—")
        self.detail_labels["attacks"].setText(
            "—" if counted_attacks is None else str(counted_attacks)
        )
        self.detail_labels["days"].setText(str(state["used_days"]))

    def set_next_action(self, text: str):
        self.detail_labels["next_action"].setText(text)

    def set_disconnected(self):
        self.set_state(None, None)
        self.set_next_action("Połącz z grą, aby sterować wakacjami.")

    def add_log(self, text: str):
        self.log_list.insertItem(0, f"{datetime.now():%d.%m %H:%M:%S}  {text}")
        while self.log_list.count() > 200:
            self.log_list.takeItem(self.log_list.count() - 1)


class AttackPlanWidget(QWidget):
    """TK-Tools attack plan execution: settings, live plan and log."""

    enabled_toggled = Signal(bool)
    setting_changed = Signal(str, object)
    refresh_requested = Signal()

    _COLUMNS = ("Operacja", "Atak", "Wyjście", "Dotarcie", "Fale", "Status", "Skład / uwagi")
    _STATUS_KINDS = {
        "waiting": "muted",
        "prepared": "pending",
        "fired": "pending",
        "sent": "active",
        "partial": "waiting",
        "failed": "waiting",
        "skipped": "waiting",
        "done_elsewhere": "muted",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer_layout.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(14)

        title = QLabel("Plan ataków")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Automat pobiera Twoje ataki z planera TK-Tools (zaloguj się do "
            "tk-tools.fr w tej samej przeglądarce co gra), składa fejki i offy, "
            "wysyła je tak, by dotarły w sekundzie z planu, i zgłasza wynik "
            "w TK-Tools."
        )
        subtitle.setObjectName("secondaryText")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Status card -----------------------------------------------------------
        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(18, 16, 18, 16)
        status_layout.setSpacing(12)
        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        self.enabled_toggle = _AutomationToggle()
        self.enabled_toggle.toggled.connect(self.enabled_toggled.emit)
        top_row.addWidget(self.enabled_toggle)
        self.state_chip = QLabel()
        top_row.addWidget(self.state_chip)
        top_row.addStretch()
        self.refresh_button = QPushButton("⟳  Odśwież plan")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        top_row.addWidget(self.refresh_button)
        status_layout.addLayout(top_row)

        details = QGridLayout()
        details.setHorizontalSpacing(18)
        details.setVerticalSpacing(6)
        self.detail_labels: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate((
            ("plan", "Plan TK-Tools"),
            ("clock", "Zegar serwera"),
            ("hero", "Bohater"),
            ("warnings", "Ostrzeżenia"),
        )):
            caption_label = QLabel(caption.upper())
            caption_label.setObjectName("columnHeader")
            details.addWidget(caption_label, row, 0, Qt.AlignTop)
            value = QLabel("—")
            value.setWordWrap(True)
            details.addWidget(value, row, 1)
            self.detail_labels[key] = value
        details.setColumnStretch(1, 1)
        status_layout.addLayout(details)
        layout.addWidget(status_card)

        # Attacks table ---------------------------------------------------------
        table_title = QLabel("Wysyłki")
        table_title.setObjectName("sectionTitle")
        layout.addWidget(table_title)
        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        for column in range(len(self._COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(self._COLUMNS) - 1, QHeaderView.Stretch)
        self.table.setMinimumHeight(300)
        layout.addWidget(self.table)

        # Settings card ---------------------------------------------------------
        settings_card = QFrame()
        settings_card.setObjectName("card")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(18, 16, 18, 16)
        settings_layout.setSpacing(10)
        settings_title = QLabel("Ustawienia")
        settings_title.setObjectName("sectionTitle")
        settings_layout.addWidget(settings_title)

        def sentence(*parts) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(8)
            for part in parts:
                row.addWidget(QLabel(part) if isinstance(part, str) else part)
            row.addStretch()
            settings_layout.addLayout(row)
            return row

        self.mode_buttons: dict[str, QPushButton] = {}
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        mode_label = QLabel("Sposób wysyłki:")
        mode_row.addWidget(mode_label)
        mode_row.addSpacing(8)
        for mode, text, object_name in (
            ("requests", "Polecenia gry", "segmentLeft"),
            ("clicks", "Klikanie w przeglądarce", "segmentRight"),
        ):
            button = QPushButton(text)
            button.setObjectName(object_name)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            bold_font = QFont(button.font())
            bold_font.setBold(True)
            button.setMinimumWidth(QFontMetrics(bold_font).horizontalAdvance(text) + 34)
            button.clicked.connect(
                lambda _checked=False, value=mode: self._choose_mode(value)
            )
            mode_row.addWidget(button)
            self.mode_buttons[mode] = button
        mode_row.addStretch()
        settings_layout.addLayout(mode_row)
        mode_hint = QLabel(
            "Polecenia gry: najdokładniej, wszystkie fale w tej samej sekundzie. "
            "Klikanie: aplikacja otwiera osobną kartę gry, wypełnia okna wysyłki "
            "i klika „Wyślij” w docelowej sekundzie."
        )
        mode_hint.setObjectName("footnote")
        mode_hint.setWordWrap(True)
        settings_layout.addWidget(mode_hint)

        self.server_name = QLineEdit()
        self.server_name.setPlaceholderText("np. tr2nx3")
        self.server_name.setFixedWidth(120)
        self.refresh_minutes = self._spin(1, 60, " min", 80)
        sentence(
            "Serwer w TK-Tools:", self.server_name,
            "  Odświeżaj plan co", self.refresh_minutes,
            "(do pierwszej wysyłki operacji).",
        )
        self.prepare_seconds = self._spin(15, 300, " s", 80)
        sentence("Przygotuj wysyłkę", self.prepare_seconds, "przed wyjściem.")
        self.report_checkbox = QCheckBox(
            "Zgłaszaj w TK-Tools wysłane i niewysłane ataki"
        )
        settings_layout.addWidget(self.report_checkbox)

        fake_title = QLabel("Fejki (gdy plan nie podaje liczby jednostek)")
        fake_title.setObjectName("fieldLabel")
        settings_layout.addWidget(fake_title)
        self.fake_base = self._spin(1, 5000, "", 70)
        self.fake_tier2 = self._spin(1, 5000, "", 70)
        self.fake_tier3 = self._spin(1, 5000, "", 70)
        self.fake_tier2_at = QLineEdit()
        self.fake_tier2_at.setFixedWidth(130)
        self.fake_tier3_at = QLineEdit()
        self.fake_tier3_at.setFixedWidth(130)
        sentence("Na start", self.fake_base, "+1 maszyna;  od", self.fake_tier2_at,
                 self.fake_tier2, "+1;  od", self.fake_tier3_at, self.fake_tier3, "+1.")

        self.pt_policy = QComboBox()
        self.pt_policy.addItem("tylko ostrzegaj", "warn")
        self.pt_policy.addItem("rozbudowuj zawczasu", "upgrade")
        sentence("Gdy plac turniejowy jest niższy niż w planie:", self.pt_policy)

        self.hero_checkbox = QCheckBox(
            "Ukrywaj bohatera: trzymaj go jako posiłek w innej wiosce i wycofuj "
            "tak, by wrócił przed wysyłką ataku z bohaterem"
        )
        settings_layout.addWidget(self.hero_checkbox)
        self.hero_village = QComboBox()
        self.hero_village.setMinimumWidth(180)
        self.hero_margin = self._spin(1, 60, " s", 70)
        sentence("Postój bohatera:", self.hero_village,
                 "  Powrót", self.hero_margin, "przed wysyłką.")

        self.server_name.editingFinished.connect(
            lambda: self.setting_changed.emit("server_name", self.server_name.text())
        )
        self.fake_tier2_at.editingFinished.connect(
            lambda: self.setting_changed.emit("fake_tier2_at", self.fake_tier2_at.text())
        )
        self.fake_tier3_at.editingFinished.connect(
            lambda: self.setting_changed.emit("fake_tier3_at", self.fake_tier3_at.text())
        )
        for field, spin_box in (
            ("refresh_minutes", self.refresh_minutes),
            ("prepare_seconds", self.prepare_seconds),
            ("fake_base_units", self.fake_base),
            ("fake_tier2_units", self.fake_tier2),
            ("fake_tier3_units", self.fake_tier3),
            ("hero_return_margin_seconds", self.hero_margin),
        ):
            spin_box.valueChanged.connect(
                lambda value, current_field=field:
                    self.setting_changed.emit(current_field, value)
            )
        self.report_checkbox.toggled.connect(
            lambda value: self.setting_changed.emit("report_to_tktools", value)
        )
        self.hero_checkbox.toggled.connect(
            lambda value: self.setting_changed.emit("hero_evasion", value)
        )
        self.pt_policy.currentIndexChanged.connect(
            lambda _index: self.setting_changed.emit("pt_policy", self.pt_policy.currentData())
        )
        self.hero_village.currentIndexChanged.connect(
            lambda _index: self.setting_changed.emit(
                "hero_parking_village_id", self.hero_village.currentData()
            )
        )
        layout.addWidget(settings_card)

        warning = QLabel(
            "<b>Ważne:</b> aplikacja i przeglądarka muszą być włączone przez cały "
            "czas operacji, a komputer nie może przejść w uśpienie. Aplikacja "
            "sama wybudza kartę gry, którą przeglądarka usypia w tle. Godziny "
            "w tabeli to czas serwera gry."
        )
        warning.setObjectName("footnote")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        log_title = QLabel("Dziennik")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(160)
        layout.addWidget(self.log_list)
        layout.addStretch()
        self.set_state("Nie połączono z grą", "muted")

    @staticmethod
    def _spin(minimum: int, maximum: int, suffix: str, width: int) -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(minimum, maximum)
        spin_box.setSuffix(suffix)
        spin_box.setFixedWidth(width)
        return spin_box

    def _choose_mode(self, mode: str):
        for value, button in self.mode_buttons.items():
            button.setChecked(value == mode)
        self.setting_changed.emit("execution_mode", mode)

    def set_villages(self, villages: list[dict], selected: str | None):
        self.hero_village.blockSignals(True)
        self.hero_village.clear()
        self.hero_village.addItem("najbliższa wioska (auto)", None)
        for village in villages:
            self.hero_village.addItem(str(village.get("name") or village["id"]), str(village["id"]))
        index = self.hero_village.findData(selected) if selected else 0
        self.hero_village.setCurrentIndex(max(0, index))
        self.hero_village.blockSignals(False)

    def load_settings(self, settings):
        widgets = [
            self.enabled_toggle, self.server_name, self.refresh_minutes,
            self.prepare_seconds, self.report_checkbox, self.fake_base,
            self.fake_tier2, self.fake_tier3, self.fake_tier2_at,
            self.fake_tier3_at, self.pt_policy, self.hero_checkbox,
            self.hero_margin, self.hero_village,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        self.enabled_toggle.setChecked(settings.enabled)
        self.server_name.setText(settings.server_name)
        self.refresh_minutes.setValue(settings.refresh_minutes)
        self.prepare_seconds.setValue(settings.prepare_seconds)
        self.report_checkbox.setChecked(settings.report_to_tktools)
        self.fake_base.setValue(settings.fake_base_units)
        self.fake_tier2.setValue(settings.fake_tier2_units)
        self.fake_tier3.setValue(settings.fake_tier3_units)
        self.fake_tier2_at.setText(settings.fake_tier2_at)
        self.fake_tier3_at.setText(settings.fake_tier3_at)
        self.pt_policy.setCurrentIndex(max(0, self.pt_policy.findData(settings.pt_policy)))
        self.hero_checkbox.setChecked(settings.hero_evasion)
        self.hero_margin.setValue(settings.hero_return_margin_seconds)
        index = self.hero_village.findData(settings.hero_parking_village_id)
        self.hero_village.setCurrentIndex(max(0, index))
        for value, button in self.mode_buttons.items():
            button.setChecked(value == settings.execution_mode)
        for widget in widgets:
            widget.blockSignals(False)

    def set_state(self, text: str, kind: str):
        _set_status_chip(self.state_chip, text, kind)

    def set_detail(self, key: str, text: str):
        self.detail_labels[key].setText(text)

    def update_rows(self, rows: list[dict], server_now: float | None):
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            departure = row["departure"]
            countdown = ""
            if server_now is not None and row["status_key"] in ("waiting", "prepared"):
                remaining = int(departure - server_now)
                if remaining >= 0:
                    hours, rest = divmod(remaining, 3600)
                    minutes, seconds = divmod(rest, 60)
                    countdown = f"  (za {hours}:{minutes:02d}:{seconds:02d})"
            values = (
                row["operation"],
                row["label"],
                f"{datetime.fromtimestamp(departure):%d.%m %H:%M:%S}{countdown}",
                f"{datetime.fromtimestamp(row['arrival']):%d.%m %H:%M:%S}",
                str(row["waves"]),
                row["status"],
                "  ".join(part for part in (row["composition"], row["message"]) if part),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 5:
                    background, foreground = _RECRUITMENT_STATUS_COLORS[
                        self._STATUS_KINDS.get(row["status_key"], "muted")
                    ]
                    item.setBackground(QColor(background))
                    item.setForeground(QColor(foreground))
                self.table.setItem(index, column, item)
        self.table.resizeRowsToContents()

    def add_log(self, text: str):
        self.log_list.insertItem(0, f"{datetime.now():%d.%m %H:%M:%S}  {text}")
        while self.log_list.count() > 300:
            self.log_list.takeItem(self.log_list.count() - 1)


class HideoutDefenseWidget(QWidget):
    """Counter-strike robber hideouts: settings, live cases and log."""

    enabled_toggled = Signal(bool)
    setting_changed = Signal(str, object)

    _UNIT_NUMBERS = tuple(range(1, 9))
    _COLUMNS = (
        "Kryjówka", "Atakuje", "Zbóje dotrą", "Kontratak dotrze",
        "Grabież dotrze", "Status", "Uwagi",
    )
    _STATUS_KINDS = {
        "detected": "pending",
        "waiting_hero": "pending",
        "counter_fired": "pending",
        "raid_scheduled": "pending",
        "raid_fired": "pending",
        "raid_returning": "pending",
        "done": "active",
        "failed": "waiting",
        "skipped": "muted",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tribe = 3
        self._loading = False
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer_layout.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(14)

        title = QLabel("Obrona przed kryjówkami")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Gdy kryjówka zbójów wyśle atak na którąś z Twoich wiosek, jej wojsko "
            "jest poza nią. Automat od razu wysyła ze stolicy kontratak (atak lub "
            "oblężenie), a po nim grabież po surowce."
        )
        subtitle.setObjectName("secondaryText")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Status card -----------------------------------------------------------
        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(18, 16, 18, 16)
        status_layout.setSpacing(12)
        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        self.enabled_toggle = _AutomationToggle()
        self.enabled_toggle.toggled.connect(self.enabled_toggled.emit)
        top_row.addWidget(self.enabled_toggle)
        self.state_chip = QLabel()
        top_row.addWidget(self.state_chip)
        top_row.addStretch()
        status_layout.addLayout(top_row)
        details = QGridLayout()
        details.setHorizontalSpacing(18)
        details.setVerticalSpacing(6)
        self.detail_labels: dict[str, QLabel] = {}
        for row, (key, caption) in enumerate((
            ("capital", "Stolica"),
            ("scan", "Ataki zbójów"),
        )):
            caption_label = QLabel(caption.upper())
            caption_label.setObjectName("columnHeader")
            details.addWidget(caption_label, row, 0, Qt.AlignTop)
            value = QLabel("—")
            value.setWordWrap(True)
            details.addWidget(value, row, 1)
            self.detail_labels[key] = value
        details.setColumnStretch(1, 1)
        status_layout.addLayout(details)
        layout.addWidget(status_card)

        table_title = QLabel("Ataki zbójów i odpowiedzi")
        table_title.setObjectName("sectionTitle")
        layout.addWidget(table_title)
        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        for column in range(len(self._COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(self._COLUMNS) - 1, QHeaderView.Stretch)
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table)

        # Settings card ---------------------------------------------------------
        settings_card = QFrame()
        settings_card.setObjectName("card")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(18, 16, 18, 16)
        settings_layout.setSpacing(10)
        settings_title = QLabel("Ustawienia")
        settings_title.setObjectName("sectionTitle")
        settings_layout.addWidget(settings_title)

        units_hint = QLabel(
            "Ile procent jednostek obecnych w stolicy wysłać. 100% = wszystkie, "
            "0% = żadne."
        )
        units_hint.setObjectName("footnote")
        units_hint.setWordWrap(True)
        settings_layout.addWidget(units_hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for column, caption in enumerate(("Jednostka", "W stolicy", "Kontratak", "Grabież")):
            label = QLabel(caption.upper())
            label.setObjectName("columnHeader")
            grid.addWidget(label, 0, column)
        self.unit_labels: dict[int, QLabel] = {}
        self.home_labels: dict[int, QLabel] = {}
        self.counter_spins: dict[int, QSpinBox] = {}
        self.raid_spins: dict[int, QSpinBox] = {}
        for row, unit in enumerate(self._UNIT_NUMBERS, start=1):
            self.unit_labels[unit] = QLabel()
            grid.addWidget(self.unit_labels[unit], row, 0)
            self.home_labels[unit] = QLabel("—")
            grid.addWidget(self.home_labels[unit], row, 1)
            for spins, column, field in (
                (self.counter_spins, 2, "counter_units"),
                (self.raid_spins, 3, "raid_units"),
            ):
                spin = AttackPlanWidget._spin(0, 100, " %", 80)
                spin.valueChanged.connect(
                    lambda _value, current_field=field: self._emit_units(current_field)
                )
                spins[unit] = spin
                grid.addWidget(spin, row, column)
        grid.setColumnStretch(4, 1)
        settings_layout.addLayout(grid)
        self._set_unit_names()

        self.hero_checkbox = QCheckBox("Bohater idzie z kontratakiem")
        settings_layout.addWidget(self.hero_checkbox)
        self.wait_hero_checkbox = QCheckBox(
            "Gdy bohatera nie ma w stolicy: wycofaj go i poczekaj, jeśli zdąży przed "
            "atakiem zbójów (inaczej kontratak idzie od razu bez niego)"
        )
        settings_layout.addWidget(self.wait_hero_checkbox)
        self.siege_checkbox = QCheckBox(
            "Oblężenie zamiast ataku, jeśli dotrze przed atakiem zbójów (mniejsze "
            "straty; wymaga taranów w kontrataku i co najmniej 1000 jednostek)"
        )
        settings_layout.addWidget(self.siege_checkbox)

        self.raid_checkbox = QCheckBox("Po kontrataku wyślij grabież po surowce")
        settings_layout.addWidget(self.raid_checkbox)
        delay_row = QHBoxLayout()
        delay_row.setSpacing(8)
        self.raid_delay = AttackPlanWidget._spin(0, 86400, "", 80)
        self.raid_delay_unit = QComboBox()
        self.raid_delay_unit.addItem("s", 1)
        self.raid_delay_unit.addItem("min", 60)
        delay_row.addWidget(QLabel("Grabież wchodzi"))
        delay_row.addWidget(self.raid_delay)
        delay_row.addWidget(self.raid_delay_unit)
        delay_row.addWidget(QLabel("po kontrataku (albo najszybciej, jak się da)."))
        delay_row.addStretch()
        settings_layout.addLayout(delay_row)
        self.fit_checkbox = QCheckBox(
            "Wysyłaj tylko tyle wojsk, ile trzeba do zabrania całego łupu kryjówki "
            "(procent grabieży jest wtedy limitem)"
        )
        settings_layout.addWidget(self.fit_checkbox)
        self.repeat_checkbox = QCheckBox(
            "Powtarzaj grabież po powrocie wojsk, dopóki w kryjówce jest łup i nie "
            "wróciła do niej obsada (najwyżej 30 razy)"
        )
        settings_layout.addWidget(self.repeat_checkbox)
        raid_hint = QLabel(
            "Grabież, która nie zdąży przed atakiem zbójów na Twoją wioskę, nie "
            "wychodzi — wojsko zostaje do obrony, gdyby kontratak nie wyczyścił kryjówki."
        )
        raid_hint.setObjectName("footnote")
        raid_hint.setWordWrap(True)
        settings_layout.addWidget(raid_hint)

        for field, checkbox in (
            ("send_hero", self.hero_checkbox),
            ("wait_for_hero", self.wait_hero_checkbox),
            ("siege_enabled", self.siege_checkbox),
            ("raid_enabled", self.raid_checkbox),
            ("raid_fit_loot", self.fit_checkbox),
            ("raid_repeat", self.repeat_checkbox),
        ):
            checkbox.toggled.connect(
                lambda value, current_field=field: self.setting_changed.emit(current_field, value)
            )
        self.raid_delay.valueChanged.connect(lambda _value: self._emit_delay())
        self.raid_delay_unit.currentIndexChanged.connect(lambda _index: self._emit_delay())
        layout.addWidget(settings_card)

        warning = QLabel(
            "<b>Ważne:</b> działa tylko przy włączonej aplikacji i przeglądarce z grą. "
            "Kontratak i grabież wychodzą zawsze ze stolicy. Godziny to czas serwera gry."
        )
        warning.setObjectName("footnote")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        log_title = QLabel("Dziennik")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(140)
        layout.addWidget(self.log_list)
        layout.addStretch()
        self.set_state("Nie połączono z grą", "muted")

    def _set_unit_names(self):
        names = UNIT_NAMES.get(self._tribe, {})
        for unit, label in self.unit_labels.items():
            label.setText(names.get(unit, f"Jednostka {unit}"))

    def set_capital(self, tribe: int | None, name: str | None, units: dict | None):
        if tribe and tribe != self._tribe:
            self._tribe = tribe
            self._set_unit_names()
        for unit, label in self.home_labels.items():
            label.setText(
                "—" if units is None
                else str(int(units.get(str(unit), units.get(unit, 0)) or 0))
            )
        self.detail_labels["capital"].setText(name or "Nie znaleziono stolicy.")

    @staticmethod
    def _units_value(spins: dict[int, QSpinBox]) -> dict[str, int]:
        return {str(unit): spin.value() for unit, spin in spins.items() if spin.value() > 0}

    def _emit_units(self, field: str):
        if self._loading:
            return
        spins = self.counter_spins if field == "counter_units" else self.raid_spins
        self.setting_changed.emit(field, self._units_value(spins))

    def _emit_delay(self):
        if self._loading:
            return
        seconds = self.raid_delay.value() * int(self.raid_delay_unit.currentData())
        self.setting_changed.emit("raid_delay_seconds", min(seconds, 86400))

    def load_settings(self, settings):
        self._loading = True
        widgets = [
            self.enabled_toggle, self.hero_checkbox, self.wait_hero_checkbox,
            self.siege_checkbox, self.raid_checkbox, self.fit_checkbox,
            self.repeat_checkbox,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.enabled_toggle.setChecked(settings.enabled)
            self.hero_checkbox.setChecked(settings.send_hero)
            self.wait_hero_checkbox.setChecked(settings.wait_for_hero)
            self.siege_checkbox.setChecked(settings.siege_enabled)
            self.raid_checkbox.setChecked(settings.raid_enabled)
            self.fit_checkbox.setChecked(settings.raid_fit_loot)
            self.repeat_checkbox.setChecked(settings.raid_repeat)
            for spins, values in (
                (self.counter_spins, settings.counter_units),
                (self.raid_spins, settings.raid_units),
            ):
                for unit, spin in spins.items():
                    spin.setValue(int(values.get(str(unit), 0)))
            delay = settings.raid_delay_seconds
            if delay and delay % 60 == 0:
                self.raid_delay_unit.setCurrentIndex(1)
                self.raid_delay.setValue(delay // 60)
            else:
                self.raid_delay_unit.setCurrentIndex(0)
                self.raid_delay.setValue(delay)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
            self._loading = False

    def set_state(self, text: str, kind: str):
        _set_status_chip(self.state_chip, text, kind)

    def set_detail(self, key: str, text: str):
        self.detail_labels[key].setText(text)

    def update_rows(self, rows: list[dict]):
        def clock(value):
            return "—" if value is None else f"{datetime.fromtimestamp(value):%H:%M:%S}"

        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            notes = [row["message"]]
            if row.get("loot") is not None:
                notes.append(f"Łup w kryjówce: {row['loot']:,}".replace(",", " "))
            if row.get("garrison"):
                notes.append(f"Obsada: {row['garrison']}")
            values = (
                row["hideout"], row["target"], clock(row["deadline"]),
                clock(row["counter_arrival"]), clock(row["raid_arrival"]),
                row["status"], "  ".join(part for part in notes if part),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 5:
                    background, foreground = _RECRUITMENT_STATUS_COLORS[
                        self._STATUS_KINDS.get(row["status_key"], "muted")
                    ]
                    item.setBackground(QColor(background))
                    item.setForeground(QColor(foreground))
                self.table.setItem(index, column, item)
        self.table.resizeRowsToContents()

    def add_log(self, text: str):
        self.log_list.insertItem(0, f"{datetime.now():%d.%m %H:%M:%S}  {text}")
        while self.log_list.count() > 300:
            self.log_list.takeItem(self.log_list.count() - 1)


def _format_hours(hours: float | None) -> str:
    if hours is None:
        return "nigdy (brak produkcji)"
    if hours <= 0:
        return "teraz"
    minutes = round(hours * 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    if hours < 48:
        return f"{int(hours)} h {round((hours - int(hours)) * 60):02d} min"
    return f"{hours / 24:.1f} dni"


def _format_costs(costs: dict) -> str:
    return " / ".join(f"{int(costs.get(r, 0)):,}".replace(",", " ") for r in (1, 2, 3, 4))


class VillageBuildWidget(QWidget):
    """Resource-field building plan, automation switch and transfer hints."""

    overflow_unit_changed = Signal(str, int)
    military_changed = Signal(object)
    enabled_toggled = Signal(bool)
    setting_changed = Signal(str, object)
    village_toggled = Signal(str, bool)
    refresh_requested = Signal()

    _PLAN_COLUMNS = (
        "Buduj", "Wioska", "Pola", "Następny krok", "Koszt (D / G / Ż / Z)",
        "Zwrot", "Gotowe za", "Złoto", "Kolejne kroki",
    )
    _MODES = (("off", "Wyłączone"), ("recommend", "Tylko zalecenia"), ("auto", "Automat"))
    _TRANSFER_COLUMNS = (
        "Z wioski", "Do wioski", "Surowce (D / G / Ż / Z)", "Kupcy",
        "Droga", "Bez transportu", "Z transportem", "Po co",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer_layout.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(14)

        title = QLabel("Budowa wiosek")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Pola surowców rozbudowywane w kolejności najszybszego zwrotu: koszt "
            "dzielony przez przyrost produkcji na godzinę (po bonusach, minus zboże "
            "zjadane przez nową populację). Gdy koszt nie mieści się w magazynie lub "
            "spichlerzu, najpierw rozbudowywany jest magazyn; gdy bilans zboża spadłby "
            "poniżej zera, najpierw pole zboża. Automat klika jak gracz — w osobnej "
            "karcie w tle wybiera wioskę i klika poziom pola."
        )
        subtitle.setObjectName("secondaryText")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        status_card = QFrame()
        status_card.setObjectName("card")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(18, 16, 18, 16)
        status_layout.setSpacing(12)
        top_row = QHBoxLayout()
        top_row.setSpacing(14)
        self.enabled_toggle = _AutomationToggle()
        self.enabled_toggle.toggled.connect(self.enabled_toggled.emit)
        top_row.addWidget(self.enabled_toggle)
        self.state_chip = QLabel()
        top_row.addWidget(self.state_chip)
        top_row.addStretch()
        self.refresh_button = QPushButton("Odśwież plan")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        top_row.addWidget(self.refresh_button)
        status_layout.addLayout(top_row)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(10)
        settings_row.addWidget(QLabel("Stolica do poziomu:"))
        self.capital_target = QSpinBox()
        self.capital_target.setRange(1, 20)
        self.capital_target.valueChanged.connect(
            lambda value: self._emit("capital_target", value)
        )
        settings_row.addWidget(self.capital_target)
        settings_row.addSpacing(16)
        settings_row.addWidget(QLabel("Pozostałe wioski do poziomu:"))
        self.village_target = QSpinBox()
        self.village_target.setRange(1, 20)
        self.village_target.setToolTip(
            "Wioski, które nie są miastami, gra ogranicza do poziomu 10."
        )
        self.village_target.valueChanged.connect(
            lambda value: self._emit("village_target", value)
        )
        settings_row.addWidget(self.village_target)
        settings_row.addSpacing(16)
        settings_row.addWidget(QLabel("Miasta do poziomu:"))
        self.town_target = QSpinBox()
        self.town_target.setRange(1, 20)
        self.town_target.setToolTip(
            "Wioska zmieniona w miasto (gra to pokazuje sama) albo oznaczona niżej rozwija "
            "pola do tego poziomu. Gra pozwala na więcej niż 10 dopiero w mieście."
        )
        self.town_target.valueChanged.connect(lambda value: self._emit("town_target", value))
        settings_row.addWidget(self.town_target)
        settings_row.addSpacing(16)
        settings_row.addWidget(QLabel("Transport, gdy oszczędza co najmniej:"))
        self.transfer_minutes = QSpinBox()
        self.transfer_minutes.setRange(5, 1440)
        self.transfer_minutes.setSuffix(" min")
        self.transfer_minutes.valueChanged.connect(
            lambda value: self._emit("transfer_min_saved_minutes", value)
        )
        settings_row.addWidget(self.transfer_minutes)
        settings_row.addStretch()
        status_layout.addLayout(settings_row)
        self.main_building_checkbox = QCheckBox(
            "Budynek główny najpierw, gdy wioskę ogranicza kolejka budowy"
        )
        self.main_building_checkbox.setToolTip(
            "Gdy budowa pozostałych pól trwa dłużej niż wyprodukowanie surowców na "
            "nie, budynek główny (krótsze czasy budowy) jest rozbudowywany, dopóki "
            "skraca budowę pól co najmniej dwa razy bardziej, niż sam trwa."
        )
        self.main_building_checkbox.toggled.connect(
            lambda checked: self._emit("main_building", checked)
        )
        status_layout.addWidget(self.main_building_checkbox)
        self.balance_checkbox = QCheckBox(
            "Wyceniaj surowce według niedoboru (nadmiar zboża liczy się mniej)"
        )
        self.balance_checkbox.setToolTip(
            "Porównuje udział każdego surowca w kosztach pozostałej rozbudowy z jego "
            "udziałem w produkcji. Surowiec produkowany w nadmiarze (np. zboże, które "
            "tylko zapełnia spichlerz) jest wart mniej, a ten, którego brakuje — więcej. "
            "Gdy handlarz NPC jest ustawiony na Automat, wszystkie surowce liczą się 1:1, "
            "bo nadwyżkę i tak da się wymienić."
        )
        self.balance_checkbox.toggled.connect(
            lambda checked: self._emit("balance_resources", checked)
        )
        status_layout.addWidget(self.balance_checkbox)
        self.bonus_checkbox = QCheckBox(
            "Rozbudowuj budynki bonusowe (tartak, cegielnia, huta, młyn, piekarnia)"
        )
        self.bonus_checkbox.setToolTip(
            "Istniejące budynki zwiększające produkcję konkurują z polami według "
            "czasu zwrotu. Nowych budynków automat nie stawia."
        )
        self.bonus_checkbox.toggled.connect(
            lambda checked: self._emit("bonus_buildings", checked)
        )
        status_layout.addWidget(self.bonus_checkbox)
        self.hero_checkbox = QCheckBox(
            "Przestawiaj produkcję bohatera na surowiec, którego najbardziej brakuje"
        )
        self.hero_checkbox.setToolTip(
            "Automat porównuje, ile zostało do zbudowania z produkcją wszystkich wiosek, "
            "i wybiera w oknie bohatera (Atrybuty → Hero production) wszystkie surowce "
            "albo jeden — ten, który najbardziej skraca czas do celów. Zmienia najwyżej "
            "co 2 h i tylko przy zysku co najmniej 3%."
        )
        self.hero_checkbox.toggled.connect(
            lambda checked: self._emit("hero_production_auto", checked)
        )
        status_layout.addWidget(self.hero_checkbox)
        raid_row = QHBoxLayout()
        self.raid_checkbox = QCheckBox("Przed nadchodzącym atakiem zabezpieczaj surowce na")
        self.raid_checkbox.setToolTip(
            "Gdy atak lub napad (nie zwiad) dotrze do wioski w tym czasie, automat najpierw "
            "buduje i rezerwuje u budowniczego, co się da, potem wysyła resztę surowców "
            "do najbliższych wiosek, które nie są atakowane i mają miejsce w magazynie, "
            "a czego nie da się wysłać, przeznacza na jednostkę z sekcji nadmiaru. "
            "Nie wysyła też transportów do wioski, którą zaraz po ich dotarciu ktoś zaatakuje. "
            "Kryjówka (to, co chroni) zostaje w wiosce."
        )
        self.raid_checkbox.toggled.connect(lambda checked: self._emit("raid_protection", checked))
        raid_row.addWidget(self.raid_checkbox)
        self.raid_minutes = QSpinBox()
        self.raid_minutes.setRange(2, 60)
        self.raid_minutes.setSuffix(" min przed")
        self.raid_minutes.valueChanged.connect(lambda v: self._emit("raid_lead_minutes", v))
        raid_row.addWidget(self.raid_minutes)
        raid_row.addStretch()
        status_layout.addLayout(raid_row)
        evade_row = QHBoxLayout()
        self.evade_checkbox = QCheckBox("Unik wojskami: przed atakiem wyprowadzaj wojsko na")
        self.evade_checkbox.setToolTip(
            "Gdy atak lub napad (nie zwiad) zaraz dotrze, wojsko wioski (bez bohatera) wychodzi "
            "jako posiłki do najbliższej własnej wioski, która nie jest atakowana. Po ostatnim "
            "ataku fali automat wycofuje dokładnie te jednostki z powrotem (plac zbiórki → "
            "Stacjonujące → Wycofaj). Wojska w drodze nie biorą udziału w walce."
        )
        self.evade_checkbox.toggled.connect(lambda checked: self._emit("troop_evasion", checked))
        evade_row.addWidget(self.evade_checkbox)
        self.evade_seconds = QSpinBox()
        self.evade_seconds.setRange(30, 600)
        self.evade_seconds.setSuffix(" s przed atakiem")
        self.evade_seconds.valueChanged.connect(lambda v: self._emit("troop_evasion_seconds", v))
        evade_row.addWidget(self.evade_seconds)
        evade_row.addStretch()
        status_layout.addLayout(evade_row)
        bank_row = QHBoxLayout()
        self.bank_checkbox = QCheckBox(
            "Bank zboża w stolicy: spichlerze do maks., nadwyżki zboża do stolicy, mielenie u handlarza przy"
        )
        self.bank_checkbox.setToolTip(
            "Spichlerze stolicy idą do maksymalnego poziomu przed innymi budynkami (razem z magazynem "
            "na zmielone zboże). Pozostałe wioski "
            "wysyłają tam zboże, którego same nie potrzebują. Gdy spichlerze są zapełnione do "
            "ustawionego progu, handlarz NPC w targowisku (za złoto, z potwierdzeniem) zamienia "
            "zboże na drewno, glinę i żelazo — w proporcji tego, czego sieci najbardziej brakuje "
            "do celów, w granicach wolnego miejsca w magazynie. Zostaje zboże na następne kroki "
            "budowy i rezerwa rekrutacji. Rezerwa złota jest zawsze zachowana."
        )
        self.bank_checkbox.toggled.connect(lambda checked: self._emit("crop_bank", checked))
        bank_row.addWidget(self.bank_checkbox)
        self.bank_fill = QSpinBox()
        self.bank_fill.setRange(50, 100)
        self.bank_fill.setSuffix(" % spichlerza")
        self.bank_fill.valueChanged.connect(lambda v: self._emit("crop_bank_fill_percent", v))
        bank_row.addWidget(self.bank_fill)
        self.bank_granaries = QCheckBox("spichlerze do maks.")
        self.bank_granaries.setToolTip(
            "Rozbudowuj spichlerze stolicy do maksymalnego poziomu przed innymi budynkami: "
            "większe, rzadsze mielenia (mniej złota), ale dłuższa kolejka w stolicy."
        )
        self.bank_granaries.toggled.connect(lambda checked: self._emit("crop_bank_granaries", checked))
        bank_row.addWidget(self.bank_granaries)
        bank_row.addStretch()
        status_layout.addLayout(bank_row)
        town_row = QHBoxLayout()
        town_row.addWidget(QLabel("Miasto:"))
        self.town_village = QComboBox()
        self.town_village.setMinimumWidth(140)
        town_row.addWidget(self.town_village)
        town_toggle = QPushButton("Oznacz / odznacz jako miasto")
        town_toggle.setToolTip(
            "Miasta gra rozpoznaje sama; to oznaczenie przydaje się, gdy chcesz planować "
            "jak dla miasta (np. tuż po zmianie)."
        )
        town_toggle.clicked.connect(self._toggle_town)
        town_row.addWidget(town_toggle)
        self.town_status = QLabel()
        self.town_status.setObjectName("secondaryText")
        town_row.addWidget(self.town_status)
        town_row.addStretch()
        status_layout.addLayout(town_row)
        self._towns_marked: set[str] = set()
        self._towns_game: dict[str, str] = {}
        settle_row = QHBoxLayout()
        settle_row.addWidget(QLabel("Osadnicy:"))
        self.settle_village = QComboBox()
        self.settle_village.setMinimumWidth(140)
        settle_row.addWidget(self.settle_village)
        self.settle_count = QSpinBox()
        self.settle_count.setRange(1, 6)
        self.settle_count.setSuffix(" osadn.")
        settle_row.addWidget(self.settle_count)
        settle_row.addWidget(QLabel("gotowi za"))
        self.settle_hours = QSpinBox()
        self.settle_hours.setRange(1, 240)
        self.settle_hours.setSuffix(" h")
        settle_row.addWidget(self.settle_hours)
        settle_set = QPushButton("Ustaw")
        settle_set.setToolTip(
            "Wioska zbuduje budynek główny do 5 i rezydencję do 10, a potem wyszkoli "
            "osadników — zaczynając tak wcześnie, żeby byli gotowi na ten czas "
            "(np. na punkty kultury). Pozostałe wioski dowiozą surowce."
        )
        settle_set.clicked.connect(self._set_settle_goal)
        settle_row.addWidget(settle_set)
        settle_off = QPushButton("Wyłącz")
        settle_off.clicked.connect(lambda: self._emit("settle_village", ""))
        settle_row.addWidget(settle_off)
        self.settle_status = QLabel()
        self.settle_status.setObjectName("secondaryText")
        settle_row.addWidget(self.settle_status)
        settle_row.addStretch()
        status_layout.addLayout(settle_row)
        storage_row = QHBoxLayout()
        storage_row.addWidget(QLabel("Magazyn i spichlerz na co najmniej:"))
        self.storage_hours = QSpinBox()
        self.storage_hours.setRange(0, 72)
        self.storage_hours.setSuffix(" h produkcji")
        self.storage_hours.setToolTip(
            "Np. 8 h, żeby magazyn nie przepełnił się w nocy. 0 = magazyn "
            "rozbudowywany tylko wtedy, gdy koszt rozbudowy się w nim nie mieści."
        )
        self.storage_hours.valueChanged.connect(lambda v: self._emit("storage_hours", v))
        storage_row.addWidget(self.storage_hours)
        storage_row.addSpacing(16)
        storage_row.addWidget(QLabel("Zapas dla rekrutacji:"))
        self.recruitment_reserve = QSpinBox()
        self.recruitment_reserve.setRange(0, 48)
        self.recruitment_reserve.setSuffix(" h")
        self.recruitment_reserve.setToolTip(
            "Ile godzin ciągłej rekrutacji (z zakładki Rekrutacja) zostaje w "
            "magazynie: budowa i transporty tego nie ruszają. Koszt rekrutacji na "
            "godzinę jest też odejmowany od produkcji przy planowaniu."
        )
        self.recruitment_reserve.valueChanged.connect(
            lambda v: self._emit("recruitment_reserve_hours", v)
        )
        storage_row.addWidget(self.recruitment_reserve)
        storage_row.addSpacing(16)
        storage_row.addWidget(QLabel("Kolejka budowniczego:"))
        self.master_builder_slots = QSpinBox()
        self.master_builder_slots.setRange(0, 4)
        self.master_builder_slots.setSuffix(" budowy")
        self.master_builder_slots.setSpecialValueText("nie używaj")
        self.master_builder_slots.setToolTip(
            "Gdy kolejka wioski jest zajęta, kolejne kroki planu trafiają do "
            "budowniczego i ruszają same, gdy kolejka się zwolni (także w nocy, "
            "bez aplikacji). Używane są tylko wolne miejsca — automat nigdy nie "
            "kupuje nowego. Kolejności u budowniczego nie da się potem zmienić, "
            "więc większa liczba to mniej elastyczny plan."
        )
        self.master_builder_slots.valueChanged.connect(
            lambda v: self._emit("master_builder_slots", v)
        )
        storage_row.addWidget(self.master_builder_slots)
        storage_row.addStretch()
        status_layout.addLayout(storage_row)
        self.summary = QLabel("Połącz z grą, aby wyliczyć plan.")
        self.summary.setObjectName("secondaryText")
        self.summary.setWordWrap(True)
        status_layout.addWidget(self.summary)
        layout.addWidget(status_card)

        gold_card = QFrame()
        gold_card.setObjectName("card")
        gold_layout = QGridLayout(gold_card)
        gold_layout.setContentsMargins(18, 16, 18, 16)
        gold_layout.setHorizontalSpacing(12)
        gold_layout.setVerticalSpacing(8)
        gold_title = QLabel("Złoto: handlarz NPC i dokończanie budowy")
        gold_title.setObjectName("sectionTitle")
        gold_layout.addWidget(gold_title, 0, 0, 1, 6)

        gold_layout.addWidget(QLabel("Handlarz NPC:"), 1, 0)
        self.npc_mode = QComboBox()
        for key, label in self._MODES:
            self.npc_mode.addItem(label, key)
        self.npc_mode.currentIndexChanged.connect(
            lambda _i: self._emit("npc_mode", self.npc_mode.currentData())
        )
        self.npc_mode.setToolTip(
            "Gdy łączna suma surowców pokrywa następny krok, ale brakuje jednego "
            "rodzaju (np. pełny spichlerz), wymiana u handlarza pozwala budować od razu."
        )
        gold_layout.addWidget(self.npc_mode, 1, 1)
        gold_layout.addWidget(QLabel("gdy oszczędza co najmniej:"), 1, 2)
        self.npc_minutes = QSpinBox()
        self.npc_minutes.setRange(0, 1440)
        self.npc_minutes.setSuffix(" min")
        self.npc_minutes.valueChanged.connect(
            lambda value: self._emit("npc_min_saved_minutes", value)
        )
        gold_layout.addWidget(self.npc_minutes, 1, 3)

        gold_layout.addWidget(QLabel("Dokończ teraz:"), 2, 0)
        self.finish_mode = QComboBox()
        for key, label in self._MODES:
            self.finish_mode.addItem(label, key)
        self.finish_mode.currentIndexChanged.connect(
            lambda _i: self._emit("finish_mode", self.finish_mode.currentData())
        )
        self.finish_mode.setToolTip(
            "Darmowe dokończenie automat wykonuje zawsze (poza trybem „Wyłączone”). "
            "Płatne — tylko w trybie „Automat” i tylko wtedy, gdy "
            "surowce na następny krok już czekają, a blokuje go zajęta kolejka."
        )
        gold_layout.addWidget(self.finish_mode, 2, 1)

        self.finish_free_checkbox = QCheckBox("za darmo poniżej")
        self.finish_free_checkbox.toggled.connect(lambda c: self._emit("finish_free", c))
        gold_layout.addWidget(self.finish_free_checkbox, 3, 0)
        self.finish_free_minutes = QSpinBox()
        self.finish_free_minutes.setRange(0, 1440)
        self.finish_free_minutes.setSuffix(" min")
        self.finish_free_minutes.valueChanged.connect(
            lambda v: self._emit("finish_free_minutes", v)
        )
        gold_layout.addWidget(self.finish_free_minutes, 3, 1)

        self.finish_reduced_checkbox = QCheckBox("tańsze dokończenie, gdy zostało od")
        self.finish_reduced_checkbox.setToolTip(
            "Krótsze budowy czekają na darmowe dokończenie, zamiast płacić."
        )
        self.finish_reduced_checkbox.toggled.connect(lambda c: self._emit("finish_reduced", c))
        gold_layout.addWidget(self.finish_reduced_checkbox, 4, 0)
        self.finish_reduced_min_minutes = QSpinBox()
        self.finish_reduced_min_minutes.setRange(0, 10080)
        self.finish_reduced_min_minutes.setSuffix(" min")
        self.finish_reduced_min_minutes.valueChanged.connect(
            lambda v: self._emit("finish_reduced_min_minutes", v)
        )
        gold_layout.addWidget(self.finish_reduced_min_minutes, 4, 1)
        gold_layout.addWidget(QLabel("do (próg ceny w grze):"), 4, 2)
        self.finish_reduced_minutes = QSpinBox()
        self.finish_reduced_minutes.setRange(0, 10080)
        self.finish_reduced_minutes.setSuffix(" min")
        self.finish_reduced_minutes.valueChanged.connect(
            lambda v: self._emit("finish_reduced_minutes", v)
        )
        gold_layout.addWidget(self.finish_reduced_minutes, 4, 3)
        gold_layout.addWidget(QLabel("cena:"), 4, 4)
        self.finish_reduced_price = QSpinBox()
        self.finish_reduced_price.setRange(0, 100)
        self.finish_reduced_price.setSuffix(" złota")
        self.finish_reduced_price.valueChanged.connect(
            lambda v: self._emit("finish_reduced_price", v)
        )
        gold_layout.addWidget(self.finish_reduced_price, 4, 5)

        self.finish_full_checkbox = QCheckBox("pełna cena, gdy zostało od")
        self.finish_full_checkbox.setToolTip(
            "Pomiędzy progiem ceny w grze a tą wartością budowa czeka, aż "
            "dokończenie stanieje."
        )
        self.finish_full_checkbox.toggled.connect(lambda c: self._emit("finish_full", c))
        gold_layout.addWidget(self.finish_full_checkbox, 5, 0)
        self.finish_full_min_minutes = QSpinBox()
        self.finish_full_min_minutes.setRange(0, 10080)
        self.finish_full_min_minutes.setSuffix(" min")
        self.finish_full_min_minutes.valueChanged.connect(
            lambda v: self._emit("finish_full_min_minutes", v)
        )
        gold_layout.addWidget(self.finish_full_min_minutes, 5, 1)
        gold_layout.addWidget(QLabel("cena:"), 5, 4)
        self.finish_full_price = QSpinBox()
        self.finish_full_price.setRange(0, 100)
        self.finish_full_price.setSuffix(" złota")
        self.finish_full_price.valueChanged.connect(
            lambda v: self._emit("finish_full_price", v)
        )
        gold_layout.addWidget(self.finish_full_price, 5, 5)

        gold_layout.addWidget(QLabel("Budżet złota:"), 8, 0)
        self.gold_budget = QSpinBox()
        self.gold_budget.setRange(0, 1_000_000)
        self.gold_budget.setSuffix(" złota")
        self.gold_budget.setSpecialValueText("brak (progi ręczne)")
        self.gold_budget.setToolTip(
            "Ile złota automat może wydać łącznie na dokończenia i handlarza. "
            "Złoto trafia tam, gdzie oszczędza najwięcej godzin kolejki na 1 złoto "
            "(wioski ograniczone kolejką), z wyborem: zapłać pełną cenę od razu albo "
            "poczekaj do tańszej. Progi ręczne powyżej są wtedy pomijane. Wymaga "
            "„Dokończ teraz: Automat”."
        )
        self.gold_budget.valueChanged.connect(lambda v: self._emit("gold_budget", v))
        gold_layout.addWidget(self.gold_budget, 8, 1)
        self.budget_status = QLabel("—")
        self.budget_status.setObjectName("secondaryText")
        self.budget_status.setWordWrap(True)
        gold_layout.addWidget(self.budget_status, 8, 2, 1, 4)
        gold_layout.addWidget(QLabel("Przeciw przepełnieniu:"), 9, 0)
        self.overflow_finish = QSpinBox()
        self.overflow_finish.setRange(0, 1_000_000)
        self.overflow_finish.setSingleStep(500)
        self.overflow_finish.setSuffix(" surowców / 1 zł")
        self.overflow_finish.setSpecialValueText("nie używaj")
        self.overflow_finish.setToolTip(
            "Gdy magazyn się przepełni, zanim skończy się trwająca rozbudowa magazynu "
            "lub spichlerza, automat dokończy ją za złoto, jeśli każde złoto uratuje "
            "co najmniej tyle surowców. Liczy się do budżetu i rezerwy złota."
        )
        self.overflow_finish.valueChanged.connect(lambda v: self._emit("overflow_finish_per_gold", v))
        gold_layout.addWidget(self.overflow_finish, 9, 1)

        self.budget_reset = QPushButton("Zeruj licznik")
        self.budget_reset.setToolTip("Zacznij liczyć wydane złoto od zera.")
        self.budget_reset.clicked.connect(lambda: self._emit("gold_budget_spent", 0))
        gold_layout.addWidget(self.budget_reset, 8, 6)

        gold_layout.addWidget(QLabel("Zachowaj co najmniej:"), 6, 0)
        self.gold_reserve = QSpinBox()
        self.gold_reserve.setRange(0, 1_000_000)
        self.gold_reserve.setSuffix(" złota")
        self.gold_reserve.valueChanged.connect(lambda v: self._emit("gold_reserve", v))
        gold_layout.addWidget(self.gold_reserve, 6, 1)
        self.gold_status = QLabel("Saldo złota: brak danych")
        self.gold_status.setObjectName("secondaryText")
        gold_layout.addWidget(self.gold_status, 6, 2, 1, 2)
        gold_hint = QLabel(
            "Ceny w grze: dokończenie za darmo poniżej 5 min, 1 złota poniżej 2 h, "
            "2 złota powyżej; handlarz NPC 5 złota. Przykład przy domyślnych "
            "ustawieniach (od 60 min za 1 złota, od 180 min za 2 złota): budowa z 20 min "
            "czeka do 5 min i kończy się za darmo; z 2 h 20 min czeka 21 min i kończy "
            "się za 1 złota; z 4 h kończy się od razu za 2 złota. Płatne dokończenie "
            "jest używane tylko wtedy, gdy surowce na następny krok już czekają. "
            "Automat budzi się dokładnie w chwili przekroczenia progu i przed "
            "kliknięciem sprawdza cenę pokazaną w grze. Uwaga: na tym koncie gra nie "
            "pyta o potwierdzenie wydatku złota."
        )
        gold_hint.setObjectName("footnote")
        gold_hint.setWordWrap(True)
        gold_layout.addWidget(gold_hint, 7, 0, 1, 7)
        gold_layout.setColumnStretch(6, 1)
        layout.addWidget(gold_card)

        plan_title = QLabel("Plan rozbudowy")
        plan_title.setObjectName("sectionTitle")
        layout.addWidget(plan_title)
        self.plan_table = QTableWidget(0, len(self._PLAN_COLUMNS))
        self.plan_table.setHorizontalHeaderLabels(self._PLAN_COLUMNS)
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.plan_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.plan_table.setWordWrap(True)
        header = self.plan_table.horizontalHeader()
        for column in range(len(self._PLAN_COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(self._PLAN_COLUMNS) - 1, QHeaderView.Stretch)
        self.plan_table.setMinimumHeight(200)
        self.plan_table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.plan_table)

        transfer_title = QLabel("Opłacalne transporty surowców")
        transfer_title.setObjectName("sectionTitle")
        layout.addWidget(transfer_title)
        transfer_hint = QLabel(
            "Wioska oddaje tylko to, co zostaje po zarezerwowaniu jej własnego "
            "następnego kroku, więc transport nie opóźnia nadawcy. Wioska, którą "
            "ogranicza kolejka (np. stolica), oddaje nadwyżkę wioskom, które "
            "ogranicza ilość surowców; inne wioski oddają tylko to, co w ciągu 2 h "
            "przepełniłoby magazyn. Gdy magazyn i tak by się przepełnił (wioska "
            "czeka na kolejkę lub inny surowiec), nadmiar idzie do dowolnej wioski "
            "z wolnym miejscem. Pokazywany jest też każdy transport, po którym "
            "odbiorca zacznie następny krok wcześniej (czas drogi kupców wliczony). "
            "Surowce już w drodze są odejmowane od potrzeb odbiorcy."
        )
        transfer_hint.setObjectName("footnote")
        transfer_hint.setWordWrap(True)
        layout.addWidget(transfer_hint)
        transfer_row = QHBoxLayout()
        transfer_row.addWidget(QLabel("Transporty:"))
        self.transfer_mode = QComboBox()
        for key, label in self._MODES:
            self.transfer_mode.addItem(label, key)
        self.transfer_mode.setToolTip(
            "Automat: wysyła kliknięciami z targowiska (zakładka Wyślij, lista "
            "własnych wiosek). Jedna wysyłka z wioski na 5 min."
        )
        self.transfer_mode.currentIndexChanged.connect(
            lambda _i: self._emit("transfer_mode", self.transfer_mode.currentData())
        )
        transfer_row.addWidget(self.transfer_mode)
        transfer_row.addSpacing(16)
        transfer_row.addWidget(QLabel("najmniejsza wysyłka:"))
        self.transfer_min_amount = QSpinBox()
        self.transfer_min_amount.setRange(100, 1_000_000)
        self.transfer_min_amount.setSingleStep(500)
        self.transfer_min_amount.setSuffix(" surowców")
        self.transfer_min_amount.valueChanged.connect(
            lambda v: self._emit("transfer_min_amount", v)
        )
        transfer_row.addWidget(self.transfer_min_amount)
        transfer_row.addStretch()
        layout.addLayout(transfer_row)
        self.transfer_table = QTableWidget(0, len(self._TRANSFER_COLUMNS))
        self.transfer_table.setHorizontalHeaderLabels(self._TRANSFER_COLUMNS)
        self.transfer_table.verticalHeader().setVisible(False)
        self.transfer_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.transfer_table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self.transfer_table.horizontalHeader()
        for column in range(len(self._TRANSFER_COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(self._TRANSFER_COLUMNS) - 1, QHeaderView.Stretch)
        self.transfer_table.setMinimumHeight(110)
        layout.addWidget(self.transfer_table)

        overflow_title = QLabel("Nadmiar surowców → rekrutacja")
        overflow_title.setObjectName("sectionTitle")
        layout.addWidget(overflow_title)
        overflow_hint = QLabel(
            "Gdy surowce i tak by przepadły w ciągu godziny (pełny magazyn, zajęta "
            "kolejka budowy i budowniczego, transporty nie wywiozą reszty), automat "
            "kliknięciem zleca tyle wybranych jednostek, ile trzeba — zawsze zostawiając "
            "surowce na następny krok budowy."
        )
        overflow_hint.setObjectName("footnote")
        overflow_hint.setWordWrap(True)
        layout.addWidget(overflow_hint)
        overflow_box = QWidget()
        self.overflow_grid = QGridLayout(overflow_box)
        self.overflow_grid.setContentsMargins(0, 0, 0, 0)
        self.overflow_grid.setHorizontalSpacing(12)
        layout.addWidget(overflow_box)
        self._overflow_combos: dict[str, QComboBox] = {}
        self._overflow_signature = None

        military_title = QLabel("Wojsko: koszary i stajnia")
        military_title.setObjectName("sectionTitle")
        layout.addWidget(military_title)
        military_hint = QLabel(
            "Wioski w podanej kolejności rozbudowują koszary i stajnię do docelowego "
            "poziomu i stale szkolą wybrane jednostki z nadwyżki (koszt następnej "
            "budowy zostaje). Kolejna wioska zaczyna, gdy poprzednia szkoli bez przerwy. "
            "Kolejność 0 = wioska wyłączona."
        )
        military_hint.setObjectName("footnote")
        military_hint.setWordWrap(True)
        layout.addWidget(military_hint)
        military_row = QHBoxLayout()
        military_row.addWidget(QLabel("Kolejka szkolenia do:"))
        self.military_queue_hours = QSpinBox()
        self.military_queue_hours.setRange(1, 72)
        self.military_queue_hours.setSuffix(" h")
        self.military_queue_hours.valueChanged.connect(
            lambda v: self._emit("military_queue_hours", v)
        )
        military_row.addWidget(self.military_queue_hours)
        military_row.addSpacing(16)
        military_row.addWidget(QLabel("następna wioska, gdy kolejki mają co najmniej:"))
        self.military_saturated_hours = QSpinBox()
        self.military_saturated_hours.setRange(1, 72)
        self.military_saturated_hours.setSuffix(" h")
        self.military_saturated_hours.valueChanged.connect(
            lambda v: self._emit("military_saturated_hours", v)
        )
        military_row.addWidget(self.military_saturated_hours)
        military_row.addStretch()
        layout.addLayout(military_row)
        continuous_row = QHBoxLayout()
        continuous_label = QLabel("Stała rekrutacja (przed budową): stajnia")
        continuous_label.setToolTip(
            "Część pełnego tempa szkolenia, którą automat trzyma bez przerwy w aktywnych "
            "wioskach wojskowych. Ten koszt na godzinę planer odlicza od produkcji, więc "
            "budowa i wysyłki liczą się z resztą. Nadwyżka nadal idzie na wojsko jak dotąd. "
            "Wstrzymane, gdy cała sieć ma mniej zboża na plusie niż próg."
        )
        continuous_row.addWidget(continuous_label)
        self.military_stable_percent = QSpinBox()
        self.military_stable_percent.setRange(0, 100)
        self.military_stable_percent.setSuffix(" %")
        self.military_stable_percent.valueChanged.connect(
            lambda v: self._emit("military_stable_percent", v)
        )
        continuous_row.addWidget(self.military_stable_percent)
        continuous_row.addWidget(QLabel("koszary"))
        self.military_barracks_percent = QSpinBox()
        self.military_barracks_percent.setRange(0, 100)
        self.military_barracks_percent.setSuffix(" %")
        self.military_barracks_percent.valueChanged.connect(
            lambda v: self._emit("military_barracks_percent", v)
        )
        continuous_row.addWidget(self.military_barracks_percent)
        continuous_row.addWidget(QLabel("tempa, maks."))
        self.military_max_percent = QSpinBox()
        self.military_max_percent.setRange(0, 100)
        self.military_max_percent.setSuffix(" % produkcji")
        self.military_max_percent.valueChanged.connect(
            lambda v: self._emit("military_max_percent", v)
        )
        continuous_row.addWidget(self.military_max_percent)
        continuous_row.addWidget(QLabel("stop przy zbożu sieci poniżej"))
        self.military_crop_floor = QSpinBox()
        self.military_crop_floor.setRange(0, 1_000_000)
        self.military_crop_floor.setSingleStep(500)
        self.military_crop_floor.setSuffix(" /h")
        self.military_crop_floor.valueChanged.connect(
            lambda v: self._emit("military_crop_floor", v)
        )
        continuous_row.addWidget(self.military_crop_floor)
        continuous_row.addStretch()
        layout.addLayout(continuous_row)
        military_box = QWidget()
        self.military_grid = QGridLayout(military_box)
        self.military_grid.setContentsMargins(0, 0, 0, 0)
        self.military_grid.setHorizontalSpacing(12)
        layout.addWidget(military_box)
        self._military_controls: dict[str, dict] = {}
        self._military_signature = None

        log_title = QLabel("Dziennik")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(140)
        layout.addWidget(self.log_list)
        self.set_state("Nie połączono z grą", "muted")

    def _emit(self, field: str, value):
        if not self._loading:
            self.setting_changed.emit(field, value)

    def _toggle_town(self):
        village_id = self.town_village.currentData()
        if not village_id:
            return
        marked = set(self._towns_marked)
        marked.symmetric_difference_update({str(village_id)})
        self._emit("town_villages", sorted(marked))

    def _show_towns(self, marked=None):
        if marked is not None:
            self._towns_marked = set(marked)
        names = {self.town_village.itemData(i): self.town_village.itemText(i)
                 for i in range(self.town_village.count())}
        game = sorted(self._towns_game.values())
        manual = sorted(names.get(v, v) for v in self._towns_marked)
        parts = []
        if game:
            parts.append("w grze: " + ", ".join(game))
        if manual:
            parts.append("oznaczone: " + ", ".join(manual))
        self.town_status.setText("; ".join(parts) or "brak miast poza stolicą")

    def _set_settle_goal(self):
        village_id = self.settle_village.currentData()
        if not village_id:
            return
        self._emit("settle_ready_at", time.time() + self.settle_hours.value() * 3600)
        self._emit("settle_settlers", self.settle_count.value())
        self._emit("settle_village", str(village_id))

    def _show_settle(self, settings):
        if settings.settle_village and settings.settle_ready_at:
            name = self.settle_village.itemText(max(0, self.settle_village.findData(settings.settle_village)))
            self.settle_status.setText(
                f"cel: {name or settings.settle_village}, {settings.settle_settlers} osadn. na "
                f"{datetime.fromtimestamp(settings.settle_ready_at):%d.%m %H:%M}"
            )
        else:
            self.settle_status.setText("brak celu")

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading or item.column() != 0:
            return
        village_id = item.data(Qt.UserRole)
        if village_id:
            self.village_toggled.emit(str(village_id), item.checkState() == Qt.Checked)

    def load_settings(self, settings):
        self._loading = True
        try:
            self.enabled_toggle.blockSignals(True)
            self.enabled_toggle.setChecked(settings.enabled)
            self.enabled_toggle.blockSignals(False)
            self.capital_target.setValue(settings.capital_target)
            self.village_target.setValue(settings.village_target)
            self.town_target.setValue(settings.town_target)
            self._show_towns(settings.town_villages)
            self.transfer_minutes.setValue(settings.transfer_min_saved_minutes)
            self.main_building_checkbox.setChecked(settings.main_building)
            self.balance_checkbox.setChecked(settings.balance_resources)
            self.bonus_checkbox.setChecked(settings.bonus_buildings)
            self.hero_checkbox.setChecked(settings.hero_production_auto)
            self.raid_checkbox.setChecked(settings.raid_protection)
            self.raid_minutes.setValue(settings.raid_lead_minutes)
            self.evade_checkbox.setChecked(settings.troop_evasion)
            self.evade_seconds.setValue(settings.troop_evasion_seconds)
            self.bank_checkbox.setChecked(settings.crop_bank)
            self.bank_fill.setValue(settings.crop_bank_fill_percent)
            self.bank_granaries.setChecked(settings.crop_bank_granaries)
            self.settle_count.setValue(settings.settle_settlers)
            if settings.settle_ready_at:
                self.settle_hours.setValue(max(1, min(240, round((settings.settle_ready_at - time.time()) / 3600))))
            self._show_settle(settings)
            self.storage_hours.setValue(settings.storage_hours)
            self.recruitment_reserve.setValue(settings.recruitment_reserve_hours)
            self.master_builder_slots.setValue(settings.master_builder_slots)
            self.military_queue_hours.setValue(int(settings.military_queue_hours))
            self.military_saturated_hours.setValue(int(settings.military_saturated_hours))
            self.military_stable_percent.setValue(settings.military_stable_percent)
            self.military_barracks_percent.setValue(settings.military_barracks_percent)
            self.military_max_percent.setValue(settings.military_max_percent)
            self.military_crop_floor.setValue(settings.military_crop_floor)
            self.transfer_mode.setCurrentIndex(
                max(0, self.transfer_mode.findData(settings.transfer_mode))
            )
            self.transfer_min_amount.setValue(settings.transfer_min_amount)
            self.npc_mode.setCurrentIndex(max(0, self.npc_mode.findData(settings.npc_mode)))
            self.npc_minutes.setValue(settings.npc_min_saved_minutes)
            self.finish_mode.setCurrentIndex(
                max(0, self.finish_mode.findData(settings.finish_mode))
            )
            self.finish_free_checkbox.setChecked(settings.finish_free)
            self.finish_reduced_checkbox.setChecked(settings.finish_reduced)
            self.finish_full_checkbox.setChecked(settings.finish_full)
            self.finish_free_minutes.setValue(settings.finish_free_minutes)
            self.finish_reduced_minutes.setValue(settings.finish_reduced_minutes)
            self.finish_reduced_min_minutes.setValue(settings.finish_reduced_min_minutes)
            self.finish_full_min_minutes.setValue(settings.finish_full_min_minutes)
            self.finish_reduced_price.setValue(settings.finish_reduced_price)
            self.finish_full_price.setValue(settings.finish_full_price)
            self.gold_reserve.setValue(settings.gold_reserve)
            self.gold_budget.setValue(settings.gold_budget)
            self.overflow_finish.setValue(settings.overflow_finish_per_gold)
        finally:
            self._loading = False

    def set_state(self, text: str, kind: str):
        _set_status_chip(self.state_chip, text, kind)

    def update_overflow(self, snapshot, chosen: dict[str, int], unit_names: dict[int, str]):
        """One unit picker per village that has a recruitment building."""
        rows = []
        for plan in snapshot.plans:
            village = plan.village
            units = [
                unit for unit, info in sorted(snapshot.troops.items())
                if set(info["building_types"]) & village.recruit_buildings
            ]
            if units:
                rows.append((village.id, village.name, tuple(units)))
        signature = tuple(rows)
        if signature != self._overflow_signature:
            self._overflow_signature = signature
            while self.overflow_grid.count():
                item = self.overflow_grid.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self._overflow_combos.clear()
            for row, (village_id, name, units) in enumerate(rows):
                self.overflow_grid.addWidget(QLabel(name), row, 0)
                combo = QComboBox()
                combo.addItem("— nie rekrutuj —", 0)
                for unit in units:
                    combo.addItem(unit_names.get(unit, f"Jednostka {unit}"), unit)
                combo.currentIndexChanged.connect(
                    lambda _i, vid=village_id, box=combo: None if self._loading
                    else self.overflow_unit_changed.emit(vid, int(box.currentData() or 0))
                )
                self.overflow_grid.addWidget(combo, row, 1)
                self._overflow_combos[village_id] = combo
            self.overflow_grid.setColumnStretch(2, 1)
        self._loading = True
        try:
            for village_id, combo in self._overflow_combos.items():
                index = combo.findData(int(chosen.get(village_id, 0) or 0))
                combo.setCurrentIndex(max(0, index))
        finally:
            self._loading = False

    def update_military(self, snapshot, entries: list[dict], unit_names: dict[int, str]):
        """One row per village with barracks or stable: order, units, level."""
        rows = []
        for plan in snapshot.plans:
            village = plan.village
            buildings = village.recruit_buildings
            if not buildings & {19, 20, 29, 30}:
                continue
            barracks = tuple(
                unit for unit, info in sorted(snapshot.troops.items())
                if 19 in info["building_types"] and buildings & {19, 29}
            )
            stable = tuple(
                unit for unit, info in sorted(snapshot.troops.items())
                if 20 in info["building_types"] and buildings & {20, 30}
            )
            rows.append((village.id, village.name, barracks, stable))
        signature = tuple(rows)
        if signature != self._military_signature:
            self._military_signature = signature
            while self.military_grid.count():
                item = self.military_grid.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self._military_controls.clear()
            for column, caption in enumerate(
                ("Kolejność", "Wioska", "Koszary", "Stajnia", "Do poziomu")
            ):
                label = QLabel(caption.upper())
                label.setObjectName("columnHeader")
                self.military_grid.addWidget(label, 0, column)
            for row, (village_id, name, barracks, stable) in enumerate(rows, start=1):
                order = QSpinBox()
                order.setRange(0, len(rows))
                order.setSpecialValueText("wył.")
                self.military_grid.addWidget(order, row, 0)
                self.military_grid.addWidget(QLabel(name), row, 1)
                combos = {}
                for column, (building, units) in enumerate(((19, barracks), (20, stable)), start=2):
                    combo = QComboBox()
                    combo.addItem("— nie szkol —", 0)
                    for unit in units:
                        combo.addItem(unit_names.get(unit, f"Jednostka {unit}"), unit)
                    combo.setEnabled(bool(units))
                    self.military_grid.addWidget(combo, row, column)
                    combos[building] = combo
                level = QSpinBox()
                level.setRange(1, 20)
                self.military_grid.addWidget(level, row, 4)
                controls = {"order": order, "units": combos, "level": level}
                self._military_controls[village_id] = controls
                order.valueChanged.connect(lambda _v: self._emit_military())
                level.valueChanged.connect(lambda _v: self._emit_military())
                for combo in combos.values():
                    combo.currentIndexChanged.connect(lambda _i: self._emit_military())
            self.military_grid.setColumnStretch(5, 1)
        self._loading = True
        try:
            by_id = {str(e.get("village_id")): (index, e) for index, e in enumerate(entries or [])}
            for village_id, controls in self._military_controls.items():
                index, entry = by_id.get(village_id, (None, None))
                controls["order"].setValue(0 if entry is None else index + 1)
                units = {int(k): int(v) for k, v in ((entry or {}).get("units") or {}).items()}
                for building, combo in controls["units"].items():
                    combo.setCurrentIndex(max(0, combo.findData(units.get(building, 0))))
                controls["level"].setValue(int((entry or {}).get("target_level") or 20))
        finally:
            self._loading = False

    def _emit_military(self):
        if self._loading:
            return
        chosen = []
        for village_id, controls in self._military_controls.items():
            order = controls["order"].value()
            if not order:
                continue
            units = {
                str(building): int(combo.currentData() or 0)
                for building, combo in controls["units"].items()
                if combo.currentData()
            }
            chosen.append((order, {
                "village_id": village_id,
                "units": units,
                "target_level": controls["level"].value(),
            }))
        chosen.sort(key=lambda item: item[0])
        self.military_changed.emit([entry for _order, entry in chosen])

    def update_snapshot(self, snapshot, disabled_villages: set[str]):
        self._loading = True
        try:
            current = self.settle_village.currentData()
            names = [(plan.village.id, plan.village.name) for plan in snapshot.plans]
            if [self.town_village.itemData(i) for i in range(self.town_village.count())] != [i for i, _ in names]:
                town_current = self.town_village.currentData()
                self.town_village.clear()
                for village_id, name in names:
                    self.town_village.addItem(name, village_id)
                if town_current:
                    self.town_village.setCurrentIndex(max(0, self.town_village.findData(town_current)))
            self._towns_game = {
                plan.village.id: plan.village.name for plan in snapshot.plans
                if plan.village.is_town and not plan.village.is_capital
            }
            self._show_towns()
            if [self.settle_village.itemData(i) for i in range(self.settle_village.count())] != [i for i, _ in names]:
                self.settle_village.clear()
                for village_id, name in names:
                    self.settle_village.addItem(name, village_id)
                if current:
                    self.settle_village.setCurrentIndex(max(0, self.settle_village.findData(current)))
            self._fill_plans(snapshot, disabled_villages)
            self._fill_transfers(snapshot)
        finally:
            self._loading = False
        ready = sum(1 for plan in snapshot.plans if plan.ready_now)
        done = sum(1 for plan in snapshot.plans if plan.finished)
        parts = [
            f"Odczyt {datetime.fromtimestamp(snapshot.read_at):%H:%M:%S}",
            f"wiosek: {len(snapshot.plans)}",
            f"gotowych do kliknięcia: {ready}",
            f"ukończonych: {done}",
            f"transportów: {len(snapshot.transfers)}",
        ]
        if snapshot.missing:
            parts.append("bez danych: " + ", ".join(snapshot.missing))
        network = snapshot.network
        if network is not None:
            names = {p.village.id: p.village.name for p in snapshot.plans}
            fmt = lambda value: f"{value:,.0f}".replace(",", " ")
            surplus = ", ".join(f"{names[v]} +{fmt(a)}/h" for v, a in network.surplus.items())
            deficit = ", ".join(f"{names[v]} −{fmt(a)}/h" for v, a in network.deficit.items())
            line = f"Bilans sieci (średnio do celu): nadwyżka {surplus or '—'}; niedobór {deficit or '—'}"
            if network.unused > 0:
                line += (
                    f". Około {fmt(network.unused)}/h nadwyżki nikt nie zużyje — trafi do "
                    "magazynów, rekrutacji albo przepadnie"
                )
            if network.extra_merchants:
                line += ". Brakuje kupców: " + ", ".join(
                    f"{names[v]} {n}" for v, n in network.extra_merchants.items()
                )
            parts.append(line)
        self.summary.setText(" · ".join(parts))
        self.gold_status.setText(f"Saldo złota: {snapshot.gold:,}".replace(",", " "))
        plan = snapshot.gold_plan
        if plan is None:
            self.budget_status.setText("Budżet wyłączony — płatne dokończenia według progów.")
        else:
            names = {p.village.id: p.village.name for p in snapshot.plans}
            parts = [
                f"{names.get(vid, vid)}: {gold} zł → ~{plan.hours_by_village[vid]:.0f} h"
                for vid, gold in sorted(plan.gold_by_village.items(), key=lambda i: -i[1])
            ]
            self.budget_status.setText(
                f"Zostało {snapshot.budget_left} zł. Plan: {plan.gold} zł skróci kolejki "
                f"o ~{plan.hours:.0f} h (opłaca się od {plan.threshold:.1f} h na 1 zł). "
                + "; ".join(parts)
            )

    def _fill_plans(self, snapshot, disabled_villages: set[str]):
        plans = snapshot.plans
        self.plan_table.setRowCount(len(plans))
        for row, plan in enumerate(plans):
            village = plan.village
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(
                Qt.Unchecked if village.id in disabled_villages else Qt.Checked
            )
            check.setData(Qt.UserRole, village.id)
            self.plan_table.setItem(row, 0, check)
            levels = [b.level for b in village.fields]
            name = village.name + (" (stolica)" if village.is_capital else "")
            fields_text = (
                f"{min(levels)}–{max(levels)} / cel {plan.target}" if levels else "—"
            )
            if not plan.finished and plan.remaining_queue_hours > 0:
                fields_text += (
                    f"\nogranicza: {'kolejka' if plan.queue_bound else 'surowce'} "
                    f"(budowa {_format_hours(plan.remaining_queue_hours)}, "
                    f"surowce {_format_hours(plan.produce_hours)})"
                )
            if plan.weights:
                fields_text += "\nwartość D/G/Ż/Z: " + " / ".join(
                    f"{plan.weights[r]:.2f}" for r in (1, 2, 3, 4)
                )
            if village.recruitment:
                fields_text += "\nrekrutacja: −" + " / ".join(
                    f"{village.recruitment.get(r, 0):,.0f}".replace(",", " ")
                    for r in (1, 2, 3, 4)
                ) + "/h (D/G/Ż/Z)"
            if village.incoming:
                total = sum(sum(amounts.values()) for _at, amounts in village.incoming)
                first = min(at for at, _amounts in village.incoming)
                fields_text += (
                    f"\nw drodze: {int(total):,} surowców".replace(",", " ")
                    + f" (pierwsze za {_format_hours(max(0.0, first - snapshot.server_time) / 3600)})"
                )
            step = plan.next_step
            if step is None:
                next_text = "Gotowe" if plan.finished else (plan.blocked or "—")
                cost_text = payback_text = ready_text = ""
            else:
                next_text = (
                    f"{step.name} {step.from_level}→{step.from_level + 1}"
                    f" (miejsce {step.location}) — {step.reason}"
                )
                cost_text = _format_costs(step.costs)
                payback_text = (
                    "—" if step.payback_hours is None else _format_hours(step.payback_hours)
                )
                if plan.ready_now:
                    ready_text = "teraz"
                else:
                    ready_text = _format_hours(plan.ready_hours)
                    if plan.queue_hours > 0 and (plan.afford_hours or 0) < plan.queue_hours:
                        ready_text += " (kolejka)"
            later = ", ".join(
                f"{s.name} {s.from_level}→{s.from_level + 1}" for s in plan.steps[1:6]
            )
            gold_parts = []
            for offer in snapshot.finish.get(village.id, []):
                left = _format_hours(offer.remaining_seconds / 3600)
                if offer.wait_seconds:
                    price = (
                        "za darmo" if offer.next_price == 0 else f"za {offer.next_price} zł"
                    )
                    gold_parts.append(
                        f"Dokończ {price} za {_format_hours(offer.wait_seconds / 3600)}"
                        f" (zostało {left})"
                    )
                else:
                    gold_parts.append(
                        "Dokończ teraz "
                        + ("za darmo" if offer.price == 0 else f"za {offer.price} zł")
                        + f" (zostało {left})"
                    )
            if village.id in snapshot.npc:
                saved = snapshot.npc[village.id]
                gold_parts.append(
                    f"Handlarz NPC za {snapshot.npc_price} zł → buduj teraz"
                    + ("" if saved is None else f" zamiast za {_format_hours(saved)}")
                )
            elif (
                step is not None
                and not plan.ready_now
                and plan.afford_hours_npc is not None
                and plan.afford_hours
                and plan.afford_hours_npc < plan.afford_hours
            ):
                gold_parts.append(
                    f"z handlarzem NPC gotowe za {_format_hours(plan.afford_hours_npc)}"
                )
            gold_text = "\n".join(gold_parts)
            for column, value in enumerate(
                (name, fields_text, next_text, cost_text, payback_text, ready_text,
                 gold_text, later),
                start=1,
            ):
                item = QTableWidgetItem(value)
                if column == 6 and plan.ready_now:
                    background, foreground = _RECRUITMENT_STATUS_COLORS["active"]
                    item.setBackground(QColor(background))
                    item.setForeground(QColor(foreground))
                self.plan_table.setItem(row, column, item)
        self.plan_table.resizeRowsToContents()

    def _fill_transfers(self, snapshot):
        transfers = snapshot.transfers
        self.transfer_table.setRowCount(len(transfers))
        for row, transfer in enumerate(transfers):
            step = transfer.step
            values = (
                transfer.source_name,
                transfer.target_name,
                _format_costs(transfer.resources),
                str(transfer.merchants),
                _format_hours(transfer.travel_hours),
                _format_hours(transfer.wait_before_hours),
                _format_hours(transfer.wait_after_hours),
                f"{transfer.reason}; następny krok: "
                f"{step.name} {step.from_level}→{step.from_level + 1}",
            )
            for column, value in enumerate(values):
                self.transfer_table.setItem(row, column, QTableWidgetItem(value))
        self.transfer_table.resizeRowsToContents()

    def add_log(self, text: str):
        self.log_list.insertItem(0, f"{datetime.now():%d.%m %H:%M:%S}  {text}")
        while self.log_list.count() > 300:
            self.log_list.takeItem(self.log_list.count() - 1)


def _app_start_times() -> list[datetime]:
    """When this app was started, from its log ("Database initialized")."""
    starts = []
    try:
        log_path = get_data_dir() / "travian.log"
        with open(log_path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if "Database initialized" in line or "Connected to the game" in line:
                    try:
                        starts.append(datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S"))
                    except ValueError:
                        pass
    except OSError:
        pass
    return starts


def _parse_iso_time(value) -> datetime | None:
    """Timezone-aware datetime from an ISO 8601 string (TK-Tools uses "Z")."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import timezone

        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class _NumberItem(QTableWidgetItem):
    """Table item that sorts by its numeric value instead of its text."""

    def __init__(self, value: int, text: str | None = None):
        super().__init__(text if text is not None else f"{value:,}".replace(",", " "))
        self._value = value
        self.setTextAlignment(Qt.AlignCenter)

    def __lt__(self, other):
        if isinstance(other, _NumberItem):
            return self._value < other._value
        return super().__lt__(other)


# TK-Tools freshness buckets and colours (light theme of tk-tools.fr):
# under 3 days green, under 7 days amber, older red, never grey.
_TK_FRESHNESS_COLORS = {
    "lt3": ("#D7F3EF", "#08655F"),
    "lt7": ("#FFF3D6", "#8A5A00"),
    "older": ("#FCE7EC", "#A31739"),
    "never": ("#F1F1F4", "#75747F"),
}

# Scout verdicts in the EXP and result columns: green = the hero left (real
# target), red = he stayed home, yellow = no conclusion, grey = no hero.
_SCOUT_VERDICT_COLORS = {
    "left": _TK_FRESHNESS_COLORS["lt3"],
    "home": _TK_FRESHNESS_COLORS["older"],
    "unknown": _TK_FRESHNESS_COLORS["lt7"],
    "absent": _TK_FRESHNESS_COLORS["never"],
}


# GetterTools marks "L.troop update" green / orange / red; shown with the
# same palette as the TK-Tools column so both columns read alike.
_GT_FRESHNESS_COLORS = {
    "green": _TK_FRESHNESS_COLORS["lt3"],
    "orange": _TK_FRESHNESS_COLORS["lt7"],
    "red": _TK_FRESHNESS_COLORS["older"],
}


def _tk_freshness_bucket(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "never"
    if age_seconds < 3 * 86400:
        return "lt3"
    if age_seconds < 7 * 86400:
        return "lt7"
    return "older"


class KingdomWidget(QWidget):
    """Members of the player's kingdom."""

    refresh_requested = Signal()
    update_tk_requested = Signal()
    update_gt_requested = Signal()

    COLUMNS = (
        "#", "Gracz", "Rola", "Populacja", "Wioski", "Punkty zwycięstwa",
        "TK - Aktualność", "GT - Aktualność",
    )
    NO_DATA = "brak danych"
    UPDATE_COLUMN = 6
    GT_COLUMN = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self.title = QLabel("Królestwo")
        self.title.setObjectName("pageTitle")
        top.addWidget(self.title)
        top.addStretch()
        self.update_tk_button = QPushButton("Aktualizuj TK")
        self.update_tk_button.setToolTip(
            "Zapisuje Twoje obecne wojska z gry (wszystkie wioski: w domu, "
            "posiłki i w drodze) oraz poziomy placu turniejowego na stronie "
            "Troops w TK-Tools. Kuźnia i orientacje zostają bez zmian."
        )
        self.update_tk_button.clicked.connect(self.update_tk_requested.emit)
        top.addWidget(self.update_tk_button)
        self.update_gt_button = QPushButton("Aktualizuj GT")
        self.update_gt_button.setToolTip(
            "Zapisuje Twoje obecne wojska z gry (jednostki 1–9, bez osadników "
            "i bohatera) oraz poziomy placu turniejowego w GetterTools "
            "(My troops). Typ wioski zostaje bez zmian."
        )
        self.update_gt_button.clicked.connect(self.update_gt_requested.emit)
        top.addWidget(self.update_gt_button)
        self.refresh_button = QPushButton("⟳  Odśwież")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)

        self.status = QLabel("Połącz z grą, aby wczytać członków królestwa.")
        self.status.setObjectName("secondaryText")
        layout.addWidget(self.status)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Szukaj gracza…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        DashboardWidget._configure_table(self.table)
        header = self.table.horizontalHeader()
        for column in range(len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        for column, width in enumerate((50, 200, 110, 110, 80, 150, 190, 190)):
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        header.setSortIndicator(0, Qt.AscendingOrder)
        self._last_sort_column = 0
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeaderItem(self.UPDATE_COLUMN).setToolTip(
            "Ostatnia aktualizacja wojsk gracza w TK-Tools (team z ustawień). "
            "„brak danych”: gracza nie ma w teamie albo konto nie ma dostępu."
        )
        self.table.horizontalHeaderItem(self.GT_COLUMN).setToolTip(
            "Ostatnia aktualizacja wojsk gracza w GetterTools (L.troop update "
            "z listy członków grupy). „brak danych”: gracza nie ma w grupie "
            "albo konto nie ma dostępu."
        )
        layout.addWidget(self.table, stretch=1)

        hint = QLabel(
            "Kliknij nagłówek kolumny, aby posortować. Lista odświeża się co 10 minut. "
            "„TK - Aktualność” pochodzi z TK-Tools (zaloguj się tam w tej samej "
            "przeglądarce co gra)."
        )
        hint.setObjectName("footnote")
        layout.addWidget(hint)

    # Player.KINGDOM_ROLE_*: 0 governor, 1 king, 2 duke, 3 viceking.
    _ROLES = {1: "Król", 3: "Wicekról", 2: "Książę", 0: "Gubernator"}
    # Higher value = higher rank, used to sort the role column.
    _ROLE_RANK = {1: 3, 3: 2, 2: 1, 0: 0}
    # Columns whose first click sorts from the highest value / newest date.
    _DESCENDING_FIRST = {2, 3, 4, 5, 6, 7}

    @staticmethod
    def _role_id(member: dict) -> int:
        if member.get("is_king"):
            return 1
        role = int(member.get("kingdom_role") or 0)
        if role in (2, 3):
            return role
        return 2 if member.get("is_duke") else 0

    @classmethod
    def _role(cls, member: dict) -> str:
        return cls._ROLES[cls._role_id(member)]

    def _on_header_clicked(self, column: int):
        """First click on a numeric column sorts descending (biggest first)."""
        if column == self._last_sort_column:
            return
        self._last_sort_column = column
        if column in self._DESCENDING_FIRST:
            self.table.horizontalHeader().setSortIndicator(column, Qt.DescendingOrder)

    def set_loading(self):
        self.status.setText("Wczytuję członków królestwa…")

    def set_error(self, message: str):
        self.status.setText(f"Nie udało się wczytać królestwa: {message}")

    def update_members(self, kingdom: dict, loaded_at: datetime):
        members = sorted(
            kingdom.get("members") or [],
            key=lambda member: -member.get("population", 0),
        )
        tag = kingdom.get("tag") or ""
        self.title.setText(f"Królestwo {tag}" if tag else "Królestwo")
        if not kingdom.get("kingdom_id"):
            self.status.setText("Nie należysz do żadnego królestwa.")
        else:
            self.status.setText(
                f"Członków: {len(members)} · populacja łącznie "
                f"{sum(m.get('population', 0) for m in members):,}".replace(",", " ")
                + f" · odczyt {loaded_at:%H:%M:%S}"
            )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(members))
        for row, member in enumerate(members):
            items = (
                _NumberItem(row + 1, str(row + 1)),
                QTableWidgetItem(member["name"]),
                _NumberItem(self._ROLE_RANK[self._role_id(member)], self._role(member)),
                _NumberItem(member.get("population", 0)),
                _NumberItem(member.get("villages", 0)),
                _NumberItem(member.get("victory_points", 0)),
                self._update_item(member, kingdom.get("tk_updates"), loaded_at),
                self._gt_item(member, kingdom.get("gt_updates"), loaded_at),
            )
            for column, item in enumerate(items):
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self._apply_filter(self.filter_edit.text())

    def _update_item(self, member: dict, updates: dict | None, now: datetime) -> QTableWidgetItem:
        """TK-Tools last troop update, "brak danych" when unknown."""
        entry = (updates or {}).get(str(member.get("player_id") or ""))
        stamp = _parse_iso_time(entry.get("last_update")) if entry else None
        if stamp is None:
            item = _NumberItem(-1, self.NO_DATA)
            if entry is not None:
                # In the team but never updated: grey like "Jamais" on TK-Tools.
                self._color_item(item, "never")
            return item
        local = stamp.astimezone().replace(tzinfo=None)
        age = max(0, int((now - local).total_seconds()))
        if age < 3600:
            ago = f"{age // 60} min temu"
        elif age < 48 * 3600:
            ago = f"{age // 3600} h temu"
        else:
            ago = f"{age // 86400} dni temu"
        item = _NumberItem(int(local.timestamp()), f"{local:%d.%m %H:%M} ({ago})")
        self._color_item(item, _tk_freshness_bucket(age))
        return item

    def _gt_item(self, member: dict, updates: dict | None, now: datetime) -> QTableWidgetItem:
        """GetterTools last troop update, coloured like on gettertools.com."""
        entry = (updates or {}).get(str(member.get("player_id") or ""))
        if not entry:
            return _NumberItem(-1, self.NO_DATA)
        timestamp = entry.get("timestamp")
        if timestamp:
            local = datetime.fromtimestamp(timestamp)
            age = max(0, int((now - local).total_seconds()))
            if age < 3600:
                ago = f"{age // 60} min temu"
            elif age < 48 * 3600:
                ago = f"{age // 3600} h temu"
            else:
                ago = f"{age // 86400} dni temu"
            item = _NumberItem(int(timestamp), f"{local:%d.%m %H:%M} ({ago})")
        else:
            item = _NumberItem(0, "nigdy")
        background, foreground = (
            _GT_FRESHNESS_COLORS.get(entry.get("color") or "", _TK_FRESHNESS_COLORS["never"])
            if timestamp else _TK_FRESHNESS_COLORS["never"]
        )
        item.setBackground(QColor(background))
        item.setForeground(QColor(foreground))
        return item

    @staticmethod
    def _color_item(item: QTableWidgetItem, bucket: str):
        background, foreground = _TK_FRESHNESS_COLORS[bucket]
        item.setBackground(QColor(background))
        item.setForeground(QColor(foreground))

    def _apply_filter(self, text: str):
        query = text.casefold().strip()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            name = item.text().casefold() if item is not None else ""
            self.table.setRowHidden(row, bool(query) and query not in name)


class ScoutWidget(QWidget):
    """Scout an attacker's possible departures to find his real target."""

    enabled_toggled = Signal(bool)
    setting_changed = Signal(str, object)
    plan_requested = Signal(dict)
    cancel_requested = Signal(int)
    manual_attacker_requested = Signal(int, int)

    ATTACK_COLUMNS = ("Atakujący", "Wioska (koord.)", "Atakuje", "Dotarcie", "Rodzaj",
                      "Plac turniejowy", "EXP bohatera")
    TARGET_COLUMNS = ("Możliwy cel", "X", "Y", "Dotarcie (dd.mm.rrrr gg:mm:ss)")
    PLAN_COLUMNS = ("#", "Atakujący", "Cel", "PT", "Wyjście wojsk", "Zwiad przed",
                    "Zwiad po", "EXP −3 / 0 / +2 s", "Wynik")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._attacks: list[dict] = []
        self._incoming: list[dict] = []
        self._manual: list[dict] = []
        self._villages: list[dict] = []
        self._speeds: dict[int, dict[int, float]] = {}
        self._loading_targets = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Zwiad")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Szukanie prawdziwego celu atakującego. Dla każdego możliwego celu i "
            "poziomu placu aplikacja wylicza sekundę wyjścia jego wojsk, wysyła po "
            "2 zwiadowców wchodzących 1 s przed i 1 s po tej sekundzie i sprawdza "
            "EXP jego bohatera w rankingu. Jeśli EXP wzrósł po pierwszej parze, a po "
            "drugiej już nie — bohater wyszedł z wojskiem: to prawdziwy cel i PT."
        )
        subtitle.setObjectName("secondaryText")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        top = QHBoxLayout()
        self.enabled_toggle = _AutomationToggle()
        self.enabled_toggle.toggled.connect(self.enabled_toggled.emit)
        top.addWidget(self.enabled_toggle)
        self.state_chip = QLabel()
        top.addWidget(self.state_chip)
        top.addStretch()
        layout.addLayout(top)
        self.found_banner = QLabel()
        self.found_banner.setWordWrap(True)
        self.found_banner.setTextFormat(Qt.RichText)
        self.found_banner.setStyleSheet(
            "background:#D7F3EF; color:#08655F; border:1px solid #0C9C93;"
            "border-radius:8px; padding:10px 12px; font-size:14px;"
        )
        self.found_banner.hide()
        layout.addWidget(self.found_banner)
        self._found: list[str] = []

        section = QLabel("1. Nadchodzący atak")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)
        self.attacks_table = QTableWidget(0, len(self.ATTACK_COLUMNS))
        self.attacks_table.setHorizontalHeaderLabels(list(self.ATTACK_COLUMNS))
        DashboardWidget._configure_table(self.attacks_table)
        self.attacks_table.setSelectionMode(QTableWidget.ExtendedSelection)
        header = self.attacks_table.horizontalHeader()
        for column in range(len(self.ATTACK_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        for column, width in enumerate((120, 190, 110, 140, 90, 280, 120)):
            self.attacks_table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.attacks_table.setMinimumHeight(170)
        self.attacks_table.itemSelectionChanged.connect(self._on_attack_selected)
        layout.addWidget(self.attacks_table)
        manual_row = QHBoxLayout()
        manual_row.addWidget(QLabel("Atakujący ręcznie — wioska (x|y):"))
        self.manual_coordinates = QLineEdit()
        self.manual_coordinates.setPlaceholderText("np. -34|29")
        self.manual_coordinates.setFixedWidth(120)
        self.manual_coordinates.returnPressed.connect(self._request_manual_attacker)
        manual_row.addWidget(self.manual_coordinates)
        manual_button = QPushButton("Dodaj atakującego")
        manual_button.clicked.connect(self._request_manual_attacker)
        manual_row.addWidget(manual_button)
        remove_manual = QPushButton("Usuń ręcznego atakującego")
        remove_manual.clicked.connect(self._remove_manual_attacker)
        manual_row.addWidget(remove_manual)
        manual_row.addStretch()
        layout.addLayout(manual_row)
        manual_hint = QLabel(
            "Gracza i plemię aplikacja odczyta z gry. Ręcznie dodani atakujący, ich "
            "jednostka, oblężenie, zakres PT i czas wejścia zapisują się na stałe."
        )
        manual_hint.setObjectName("footnote")
        manual_hint.setWordWrap(True)
        layout.addWidget(manual_hint)

        section = QLabel("2. Ustawienia zwiadu dla wybranego ataku")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.unit_combo = QComboBox()
        self.unit_combo.setMinimumWidth(220)
        self.siege_checkbox = QCheckBox("Oblężenie (czas przemarszu ×2)")
        self.pt_from = AttackPlanWidget._spin(0, 20, "", 70)
        self.pt_to = AttackPlanWidget._spin(0, 20, "", 70)
        self.pt_to.setValue(20)
        self.spread_spin = AttackPlanWidget._spin(0, 20, "", 70)
        self.scout_village = QComboBox()
        self.scout_village.setMinimumWidth(220)
        self.scouts_spin = AttackPlanWidget._spin(1, 50, "", 70)
        form.addWidget(QLabel("Najwolniejsza jednostka:"), 0, 0)
        form.addWidget(self.unit_combo, 0, 1)
        form.addWidget(self.siege_checkbox, 0, 2, 1, 2)
        pt_row = QHBoxLayout()
        pt_row.addWidget(self.pt_from)
        pt_row.addWidget(QLabel("do"))
        pt_row.addWidget(self.pt_to)
        pt_row.addStretch()
        form.addWidget(QLabel("Plac turniejowy od:"), 1, 0)
        form.addLayout(pt_row, 1, 1)
        form.addWidget(QLabel("Domyślnie ± od wyliczonego PT:"), 1, 2)
        form.addWidget(self.spread_spin, 1, 3)
        self.attacker_arrival = QLineEdit()
        self.attacker_arrival.setPlaceholderText("dd.mm.rrrr gg:mm:ss")
        self.attacker_arrival.setFixedWidth(170)
        self.attacker_arrival.setToolTip(
            "Czas wejścia ataku ręcznego atakującego — używany dla celów "
            "bez własnego czasu dotarcia."
        )
        form.addWidget(QLabel("Czas wejścia atakującego:"), 3, 0)
        form.addWidget(self.attacker_arrival, 3, 1)
        form.addWidget(QLabel("Zwiad wysyła wioska:"), 2, 0)
        form.addWidget(self.scout_village, 2, 1)
        form.addWidget(QLabel("Zwiadowców na wysyłkę:"), 2, 2)
        form.addWidget(self.scouts_spin, 2, 3)
        form.setColumnStretch(4, 1)
        layout.addLayout(form)
        self.spread_spin.valueChanged.connect(
            lambda value: self.setting_changed.emit("pt_spread", value)
        )
        # Remember the choices of a selected manual attacker.
        self._selecting = False
        self.unit_combo.currentIndexChanged.connect(lambda _i: self._store_manual_choices())
        self.siege_checkbox.toggled.connect(lambda _v: self._store_manual_choices())
        self.pt_from.valueChanged.connect(lambda _v: self._store_manual_choices())
        self.pt_to.valueChanged.connect(lambda _v: self._store_manual_choices())
        self.attacker_arrival.editingFinished.connect(self._store_manual_choices)
        self.scouts_spin.valueChanged.connect(
            lambda value: self.setting_changed.emit("scouts_per_send", value)
        )

        section = QLabel("3. Możliwe cele (gdzie ten gracz może uderzyć)")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)
        self.targets_table = QTableWidget(0, len(self.TARGET_COLUMNS))
        self.targets_table.setHorizontalHeaderLabels(list(self.TARGET_COLUMNS))
        self.targets_table.verticalHeader().setVisible(False)
        header = self.targets_table.horizontalHeader()
        for column, width in enumerate((220, 70, 70, 240)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.targets_table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.targets_table.setMinimumHeight(150)
        self.targets_table.itemChanged.connect(self._on_target_edited)
        layout.addWidget(self.targets_table)
        buttons = QHBoxLayout()
        add_button = QPushButton("Dodaj cel")
        add_button.clicked.connect(lambda: self._add_target_row("Nowy cel", 0, 0, None))
        buttons.addWidget(add_button)
        own_button = QPushButton("Dodaj atakowaną wioskę")
        own_button.setToolTip("Dodaje wioskę z wybranego ataku z jego czasem dotarcia.")
        own_button.clicked.connect(self._add_attacked_village)
        buttons.addWidget(own_button)
        remove_button = QPushButton("Usuń zaznaczony")
        remove_button.clicked.connect(self._remove_target)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        selected_button = QPushButton("Zaplanuj dla zaznaczonych")
        selected_button.setToolTip(
            "Planuje zwiad dla wszystkich zaznaczonych atakujących (Ctrl/Shift), "
            "każdego według jego zapisanych ustawień."
        )
        selected_button.clicked.connect(lambda: self._request_plan_many(selected_only=True))
        buttons.addWidget(selected_button)
        all_button = QPushButton("Zaplanuj dla wszystkich")
        all_button.clicked.connect(lambda: self._request_plan_many(selected_only=False))
        buttons.addWidget(all_button)
        self.plan_button = QPushButton("Zaplanuj zwiad")
        self.plan_button.setObjectName("primaryButton")
        self.plan_button.clicked.connect(self._request_plan)
        buttons.addWidget(self.plan_button)
        layout.addLayout(buttons)
        hint = QLabel(
            "Pusty czas dotarcia = czas wybranego ataku (ataki jednej operacji "
            "zwykle wchodzą razem); dla ręcznego atakującego czas jest wymagany. "
            "Cele zapisują się na stałe, czasy nie."
        )
        hint.setObjectName("footnote")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        section = QLabel("4. Plan i wyniki")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)
        self.plan_table = QTableWidget(0, len(self.PLAN_COLUMNS))
        self.plan_table.setHorizontalHeaderLabels(list(self.PLAN_COLUMNS))
        DashboardWidget._configure_table(self.plan_table)
        header = self.plan_table.horizontalHeader()
        for column, width in enumerate((40, 110, 160, 50, 120, 150, 150, 170, 260)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.plan_table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        # Sort by any column; the default is chronological (departure).
        header.setSortIndicator(4, Qt.AscendingOrder)
        self.plan_table.setSortingEnabled(True)
        self.plan_table.horizontalHeaderItem(7).setToolTip(
            "Zielony — EXP wzrósł po pierwszej parze, po drugiej już nie: bohater "
            "wyszedł (prawdziwy cel). Czerwony — wzrósł po obu parach: bohater "
            "został. Żółty — wzrósł dopiero po drugiej parze: bez wniosku. "
            "Szary — nie wzrósł wcale: bohatera nie było w wiosce."
        )
        self.plan_table.setMinimumHeight(240)
        layout.addWidget(self.plan_table)
        cancel_row = QHBoxLayout()
        cancel_row.addStretch()
        self.cancel_button = QPushButton("Anuluj zaznaczoną operację")
        self.cancel_button.clicked.connect(self._request_cancel)
        cancel_row.addWidget(self.cancel_button)
        layout.addLayout(cancel_row)

        warning = QLabel(
            "<b>Uwaga:</b> to działa tylko, gdy ranking bohaterów aktualizuje się od "
            "razu po walce — sprawdź pierwsze wyniki. Godziny to czas serwera."
        )
        warning.setObjectName("footnote")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        log_title = QLabel("Dziennik")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(140)
        layout.addWidget(self.log_list)
        self.set_state("Nie połączono z grą", "muted")

    # ------------------------------------------------------------ settings
    def load_settings(self, settings):
        for widget in (self.enabled_toggle, self.spread_spin, self.scouts_spin):
            widget.blockSignals(True)
        self.enabled_toggle.setChecked(settings.enabled)
        self.spread_spin.setValue(settings.pt_spread)
        self.scouts_spin.setValue(settings.scouts_per_send)
        for widget in (self.enabled_toggle, self.spread_spin, self.scouts_spin):
            widget.blockSignals(False)
        self._loading_targets = True
        self.targets_table.setRowCount(0)
        for target in settings.targets:
            self._add_target_row(target.name, target.x, target.y, target.arrival or None, emit=False)
        self._loading_targets = False
        self._manual = [self._manual_entry(attacker.model_dump()) for attacker in settings.attackers]
        self.update_attacks(self._incoming)

    @staticmethod
    def _manual_entry(saved: dict) -> dict:
        arrival = None
        if saved.get("arrival"):
            arrival = datetime.strptime(saved["arrival"], "%d.%m.%Y %H:%M:%S").timestamp()
        return {
            "key": f"manual:{saved['id']}", "manual": True, "saved": dict(saved),
            "attacker_name": saved.get("name") or "?",
            "attacker_player_id": saved.get("player_id") or "",
            "source_village": saved.get("village_name") or "",
            "source_village_id": saved.get("village_id") or "",
            "source_xy": (saved["x"], saved["y"]), "target_xy": None,
            "village_name": "(ręcznie)", "arrival_time": arrival,
            "movement_type": "Oblężenie" if saved.get("siege", True) else "Atak",
            "tribe_id": saved.get("tribe_id") or 0, "tournament_square": "—",
            "hero_xp": saved.get("hero_xp"),
        }

    def _emit_attackers(self):
        self.setting_changed.emit("attackers", [entry["saved"] for entry in self._manual])

    def _store_manual_choices(self):
        attack = self.selected_attack()
        if self._selecting or attack is None or not attack.get("manual"):
            return
        saved = attack["saved"]
        if self.unit_combo.currentData() is not None:
            saved["unit"] = int(self.unit_combo.currentData()[0])
        saved["siege"] = self.siege_checkbox.isChecked()
        saved["pt_from"], saved["pt_to"] = sorted((self.pt_from.value(), self.pt_to.value()))
        text = self.attacker_arrival.text().strip()
        try:
            arrival = datetime.strptime(text, "%d.%m.%Y %H:%M:%S").timestamp() if text else None
        except ValueError:
            QMessageBox.warning(self, "Zwiad", "Czas wejścia w formacie dd.mm.rrrr gg:mm:ss.")
            return
        saved["arrival"] = text
        attack["arrival_time"] = arrival
        attack["movement_type"] = "Oblężenie" if saved["siege"] else "Atak"
        self._emit_attackers()
        self.update_attacks(self._incoming)

    def set_state(self, text: str, kind: str):
        _set_status_chip(self.state_chip, text, kind)

    def add_log(self, text: str):
        self.log_list.insertItem(0, f"{datetime.now():%d.%m %H:%M:%S}  {text}")
        while self.log_list.count() > 300:
            self.log_list.takeItem(self.log_list.count() - 1)

    @staticmethod
    def found_text(result: dict) -> str:
        kind = "OBLĘŻENIE" if result.get("siege") else "ATAK"
        return (
            f"{kind} Z BOHATEREM: {result['attacker']} → {result['target']} "
            f"(PT {result['level']}, {result.get('unit', '')}, wyjście wojsk "
            f"{datetime.fromtimestamp(result['departure']):%d.%m %H:%M:%S})"
        )

    def add_found(self, result: dict):
        self._found.insert(0, self.found_text(result))
        self.found_banner.setText(
            "<b>Prawdziwy cel znaleziony</b><br>" + "<br>".join(self._found[:6])
        )
        self.found_banner.show()

    # ------------------------------------------------------------ attacks
    def set_context(self, villages: list[dict], speeds: dict[int, dict[int, float]]):
        """Own villages ({id, name, x, y, scouts}) and unit speeds per tribe."""
        self._villages = list(villages)
        self._speeds = dict(speeds or {})
        current = self.scout_village.currentData()
        self.scout_village.blockSignals(True)
        self.scout_village.clear()
        for village in self._villages:
            self.scout_village.addItem(
                f"{village['name']} ({village.get('scouts', 0)} zwiadowców)", village["id"],
            )
        index = self.scout_village.findData(current)
        if index >= 0:
            self.scout_village.setCurrentIndex(index)
        self.scout_village.blockSignals(False)
        self._on_attack_selected()

    def _request_manual_attacker(self):
        text = self.manual_coordinates.text().strip().strip("()").replace(" ", "")
        parts = text.replace(",", "|").replace(";", "|").split("|")
        try:
            x, y = int(parts[0]), int(parts[1])
        except (IndexError, ValueError):
            QMessageBox.warning(self, "Zwiad", "Wpisz współrzędne wioski jako x|y, np. -34|29.")
            return
        self.manual_attacker_requested.emit(x, y)

    def add_manual_attacker(self, village: dict, x: int, y: int):
        import uuid

        saved = {
            "id": uuid.uuid4().hex[:12], "name": village.get("player_name") or "",
            "village_name": village.get("village_name") or "", "x": x, "y": y,
            "player_id": village["player_id"], "village_id": village["village_id"],
            "tribe_id": int(village.get("tribe_id") or 0), "unit": 7, "siege": True,
            "pt_from": 0, "pt_to": 20, "arrival": "",
        }
        entry = self._manual_entry(saved)
        key = entry["key"]
        self._manual.append(entry)
        self._emit_attackers()
        self.update_attacks(self._incoming)
        for row, attack in enumerate(self._attacks):
            if attack["key"] == key:
                self.attacks_table.selectRow(row)

    def set_manual_hero_xp(self, key: str, xp: int):
        for entry in self._manual:
            if entry["key"] == key or entry.get("source_village_id") == key.split(":")[-1]:
                entry["hero_xp"] = xp
        self.update_attacks(self._incoming)

    def _remove_manual_attacker(self):
        attack = self.selected_attack()
        if attack is None or not attack.get("manual"):
            QMessageBox.information(self, "Zwiad", "Zaznacz ręcznie dodanego atakującego.")
            return
        self._manual = [entry for entry in self._manual if entry["key"] != attack["key"]]
        self._emit_attackers()
        self.update_attacks(self._incoming)

    def update_attacks(self, attacks: list[dict]):
        selected = self.selected_attack()
        selected_key = selected["key"] if selected else None
        self._incoming = list(attacks)
        self._attacks = self._manual + [a for a in attacks if not a.get("robber")]
        self.attacks_table.blockSignals(True)
        self.attacks_table.setRowCount(len(self._attacks))
        for row, attack in enumerate(self._attacks):
            source = attack.get("source_xy")
            xp = attack.get("hero_xp")
            values = (
                attack.get("attacker_name", ""),
                f"{attack.get('source_village', '')} "
                + (f"({source[0]}|{source[1]})" if source else ""),
                attack.get("village_name", ""),
                (
                    f"{datetime.fromtimestamp(attack['arrival_time']):%d.%m %H:%M:%S}"
                    if attack.get("arrival_time") else "—"
                ),
                (f"ręcznie · {attack.get('movement_type', '')}" if attack.get("manual")
                 else attack.get("movement_type", "")),
                attack.get("tournament_square") or "—",
                "—" if xp is None else f"{xp:,}".replace(",", " "),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 5:
                    item.setToolTip(str(value))
                self.attacks_table.setItem(row, column, item)
            if attack["key"] == selected_key:
                self.attacks_table.selectRow(row)
        self.attacks_table.blockSignals(False)

    def selected_attack(self) -> dict | None:
        rows = {item.row() for item in self.attacks_table.selectedItems()}
        if not rows:
            return None
        row = min(rows)
        return self._attacks[row] if row < len(self._attacks) else None

    def _on_attack_selected(self):
        attack = self.selected_attack()
        if attack is None:
            return
        self._selecting = True
        try:
            self._apply_attack_selection(attack)
        finally:
            self._selecting = False

    def _apply_attack_selection(self, attack: dict):
        tribe = int(attack.get("tribe_id") or 0)
        speeds = self._speeds.get(tribe, {})
        names = UNIT_NAMES.get(tribe, {})
        self.unit_combo.clear()
        for unit, speed in sorted(speeds.items(), key=lambda item: item[1]):
            self.unit_combo.addItem(f"{names.get(unit, unit)} ({speed:g} pól/h)", (unit, speed))
        levels = attack.get("square_levels") or {}
        best = None
        for level, units in levels.items():
            for unit in units:
                speed = speeds.get(int(unit))
                if speed and (best is None or speed < best[2]):
                    best = (int(level), int(unit), speed)
        if best is not None:
            for index in range(self.unit_combo.count()):
                if self.unit_combo.itemData(index)[0] == best[1]:
                    self.unit_combo.setCurrentIndex(index)
            spread = self.spread_spin.value()
            self.pt_from.setValue(max(0, best[0] - spread))
            self.pt_to.setValue(min(20, best[0] + spread))
        else:
            self.pt_from.setValue(0)
            self.pt_to.setValue(20)
        self.siege_checkbox.setChecked(attack.get("movement_type") == "Oblężenie")
        self.attacker_arrival.setText("")
        self.attacker_arrival.setEnabled(bool(attack.get("manual")))
        if attack.get("manual"):
            saved = attack["saved"]
            for index in range(self.unit_combo.count()):
                if self.unit_combo.itemData(index)[0] == int(saved.get("unit") or 7):
                    self.unit_combo.setCurrentIndex(index)
            self.siege_checkbox.setChecked(bool(saved.get("siege", True)))
            self.pt_from.setValue(int(saved.get("pt_from") or 0))
            self.pt_to.setValue(int(saved.get("pt_to", 20)))
            self.attacker_arrival.setText(saved.get("arrival") or "")
        source = attack.get("source_xy")
        if source and self._villages:
            nearest = min(
                (v for v in self._villages if v.get("scouts", 0) > 0),
                key=lambda v: (v["x"] - source[0]) ** 2 + (v["y"] - source[1]) ** 2,
                default=None,
            )
            if nearest is not None:
                self.scout_village.setCurrentIndex(self.scout_village.findData(nearest["id"]))

    # ------------------------------------------------------------ targets
    def _add_target_row(self, name, x, y, arrival, emit=True):
        self._loading_targets = True
        row = self.targets_table.rowCount()
        self.targets_table.insertRow(row)
        if arrival is None:
            arrival_text = ""
        elif isinstance(arrival, str):
            arrival_text = arrival
        else:
            arrival_text = f"{datetime.fromtimestamp(arrival):%d.%m.%Y %H:%M:%S}"
        for column, value in enumerate((name, str(x), str(y), arrival_text)):
            self.targets_table.setItem(row, column, QTableWidgetItem(value))
        self._loading_targets = False
        if emit:
            self._emit_targets()

    def _add_attacked_village(self):
        attack = self.selected_attack()
        if attack is None:
            QMessageBox.information(self, "Zwiad", "Najpierw wybierz atak z listy.")
            return
        if attack.get("manual") or not attack.get("target_xy"):
            QMessageBox.information(
                self, "Zwiad", "Dla ręcznego atakującego dodaj cele przyciskiem „Dodaj cel”.",
            )
            return
        target = attack["target_xy"]
        self._add_target_row(attack.get("village_name", "Cel"), target[0], target[1],
                             attack["arrival_time"])

    def _remove_target(self):
        rows = sorted({item.row() for item in self.targets_table.selectedItems()}, reverse=True)
        for row in rows:
            self.targets_table.removeRow(row)
        self._emit_targets()

    def _on_target_edited(self, _item):
        if not self._loading_targets:
            self._emit_targets()

    def _emit_targets(self):
        targets = []
        for row in range(self.targets_table.rowCount()):
            try:
                arrival_item = self.targets_table.item(row, 3)
                arrival_text = arrival_item.text().strip() if arrival_item else ""
                try:
                    if arrival_text:
                        datetime.strptime(arrival_text, "%d.%m.%Y %H:%M:%S")
                except ValueError:
                    arrival_text = ""      # keep the target, not a half-typed time
                targets.append({
                    "name": self.targets_table.item(row, 0).text().strip() or f"Cel {row + 1}",
                    "x": int(self.targets_table.item(row, 1).text()),
                    "y": int(self.targets_table.item(row, 2).text()),
                    "arrival": arrival_text,
                })
            except (AttributeError, ValueError):
                continue
        self.setting_changed.emit("targets", targets)

    def targets(self, default_arrival: float | None) -> list[tuple[str, int, int, float]]:
        result = []
        for row in range(self.targets_table.rowCount()):
            try:
                name = self.targets_table.item(row, 0).text().strip()
                x = int(self.targets_table.item(row, 1).text())
                y = int(self.targets_table.item(row, 2).text())
            except (AttributeError, ValueError):
                raise ValueError(f"Cel w wierszu {row + 1}: wpisz liczby X i Y.")
            text = (self.targets_table.item(row, 3).text() if self.targets_table.item(row, 3) else "").strip()
            arrival = default_arrival
            if text:
                try:
                    arrival = datetime.strptime(text, "%d.%m.%Y %H:%M:%S").timestamp()
                except ValueError:
                    raise ValueError(
                        f"Cel „{name}”: czas dotarcia w formacie dd.mm.rrrr gg:mm:ss."
                    )
            if arrival is None:
                raise ValueError(f"Cel „{name}”: podaj czas dotarcia (dd.mm.rrrr gg:mm:ss).")
            result.append((name, x, y, arrival))
        return result

    # ------------------------------------------------------------ plan
    def _request_plan(self):
        attack = self.selected_attack()
        if attack is None:
            QMessageBox.information(self, "Zwiad", "Najpierw wybierz atak z listy.")
            return
        if self.unit_combo.currentData() is None:
            QMessageBox.information(self, "Zwiad", "Brak danych o jednostkach atakującego.")
            return
        try:
            targets = self.targets(
                float(attack["arrival_time"]) if attack.get("arrival_time") else None
            )
        except ValueError as error:
            QMessageBox.warning(self, "Zwiad", str(error))
            return
        if not targets:
            QMessageBox.information(self, "Zwiad", "Dodaj przynajmniej jeden możliwy cel.")
            return
        low, high = sorted((self.pt_from.value(), self.pt_to.value()))
        unit, speed = self.unit_combo.currentData()
        self.plan_requested.emit({
            "attack": attack,
            "unit": unit,
            "unit_label": self.unit_combo.currentText(),
            "speed": speed,
            "siege": self.siege_checkbox.isChecked(),
            "levels": list(range(low, high + 1)),
            "targets": targets,
            "scout_village_id": self.scout_village.currentData(),
            "scouts_per_send": self.scouts_spin.value(),
        })

    def selected_attacks(self) -> list[dict]:
        rows = sorted({item.row() for item in self.attacks_table.selectedItems()})
        return [self._attacks[row] for row in rows if row < len(self._attacks)]

    def _nearest_scout_village(self, source) -> str | None:
        villages = [v for v in self._villages if v.get("scouts", 0) > 0]
        if not villages or not source:
            return self.scout_village.currentData()
        return min(
            villages, key=lambda v: (v["x"] - source[0]) ** 2 + (v["y"] - source[1]) ** 2,
        )["id"]

    def _plan_request_for(self, attack: dict) -> dict:
        """A plan request from an attacker's own saved settings (or estimate)."""
        tribe = int(attack.get("tribe_id") or 0)
        speeds = self._speeds.get(tribe, {})
        names = UNIT_NAMES.get(tribe, {})
        if attack.get("manual"):
            saved = attack["saved"]
            unit = int(saved.get("unit") or 7)
            siege = bool(saved.get("siege", True))
            low, high = sorted((int(saved.get("pt_from") or 0), int(saved.get("pt_to", 20))))
        else:
            best = None
            for level, units in (attack.get("square_levels") or {}).items():
                for candidate in units:
                    speed = speeds.get(int(candidate))
                    if speed and (best is None or speed < best[2]):
                        best = (int(level), int(candidate), speed)
            if best is None:
                raise ValueError("brak wyceny jednostki i PT — zaplanuj go pojedynczo")
            spread = self.spread_spin.value()
            unit, low, high = best[1], max(0, best[0] - spread), min(20, best[0] + spread)
            siege = attack.get("movement_type") == "Oblężenie"
        speed = speeds.get(unit)
        if not speed:
            raise ValueError("brak prędkości jednostki (poczekaj na odczyt danych gry)")
        targets = self.targets(float(attack["arrival_time"]) if attack.get("arrival_time") else None)
        if not targets:
            raise ValueError("dodaj przynajmniej jeden możliwy cel")
        return {
            "attack": attack, "unit": unit,
            "unit_label": f"{names.get(unit, unit)} ({speed:g} pól/h)", "speed": speed,
            "siege": siege, "levels": list(range(low, high + 1)), "targets": targets,
            "scout_village_id": self._nearest_scout_village(attack.get("source_xy")),
            "scouts_per_send": self.scouts_spin.value(),
        }

    def _request_plan_many(self, selected_only: bool):
        attacks = self.selected_attacks() if selected_only else list(self._attacks)
        if not attacks:
            QMessageBox.information(
                self, "Zwiad",
                "Zaznacz atakujących (Ctrl/Shift)." if selected_only else "Brak atakujących.",
            )
            return
        problems = []
        planned = 0
        for attack in attacks:
            try:
                request = self._plan_request_for(attack)
            except ValueError as error:
                problems.append(f"{attack.get('attacker_name', '?')}: {error}")
                continue
            self.plan_requested.emit(request)
            planned += 1
        self.add_log(f"Planuję zwiad dla {planned} atakujących.")
        if problems:
            QMessageBox.warning(self, "Zwiad", "Pominięto:\n" + "\n".join(problems))

    def _request_cancel(self):
        rows = {item.row() for item in self.plan_table.selectedItems()}
        if not rows:
            return
        item = self.plan_table.item(min(rows), 0)
        if item is not None and item.text().isdigit():
            self.cancel_requested.emit(int(item.text()))

    def update_plan(self, rows: list[dict]):
        def clock(value):
            return f"{datetime.fromtimestamp(value):%H:%M:%S}"

        verdict_labels = {
            "left": "BOHATER WYSZEDŁ — prawdziwy cel",
            "home": "bohater był i został",
            "absent": "bohatera nie było w wiosce",
            "unknown": "bez wniosku",
        }
        self.plan_table.setSortingEnabled(False)
        self.plan_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            xp = row["xp"]
            xp_text = " / ".join(
                "—" if xp.get(offset) is None else str(xp.get(offset)) for offset in (-3, 0, 2)
            )
            if all(xp.get(offset) is not None for offset in (-3, 0, 2)):
                xp_text += f"  (+{xp[0] - xp[-3]}, +{xp[2] - xp[0]}; para ≈ {row.get('pair_xp', 0)})"
            result = verdict_labels.get(row["verdict"], "")
            if not result:
                result = {"aktywna": "czeka", "anulowana": "anulowana",
                          "zakończona": "zakończona"}.get(row["status"], row["status"])
            values = (
                str(row["operation"]), row["attacker"], row["target"], str(row["level"]),
                clock(row["departure"]),
                f"{clock(row['sends'][0])} · {row['send_status'][0]}",
                f"{clock(row['sends'][1])} · {row['send_status'][1]}",
                xp_text, result,
            )
            # Numbers and times sort by value (times may cross midnight).
            sort_keys = {
                0: row["operation"], 3: row["level"], 4: row["departure"],
                5: row["sends"][0], 6: row["sends"][1],
            }
            for column, value in enumerate(values):
                if column in sort_keys:
                    item = _NumberItem(sort_keys[column], value)
                else:
                    item = QTableWidgetItem(value)
                colors = _SCOUT_VERDICT_COLORS.get(row["verdict"])
                if column in (7, 8) and colors:
                    item.setBackground(QColor(colors[0]))
                    item.setForeground(QColor(colors[1]))
                self.plan_table.setItem(index, column, item)
        self.plan_table.setSortingEnabled(True)


class IncomingAttacksWidget(QWidget):
    """Display the persisted history of attacks detected in owned villages."""

    delete_requested = Signal(list)

    _COLUMNS = (
        "Cel", "Atakujący", "Wioska źródłowa", "Koordynaty", "Rodzaj",
        "Dotarcie", "Plac turniejowy", "Pozostało", "Status", "Wykryto",
    )
    _COLUMN_WIDTHS = (110, 110, 150, 90, 80, 120, 300, 130, 90, 120)
    SQUARE_COLUMN = 6
    ETA_COLUMN = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._attacks: list[dict] = []
        self._history: list[dict] = []
        self._eta_references: dict[str, tuple[float, float]] = {}
        self._row_by_attack_key: dict[str, int] = {}
        layout = QVBoxLayout(self)
        self.status = QLabel(
            "Połącz z grą, aby rozpocząć monitorowanie ataków."
        )
        self.status.setObjectName("secondaryText")
        layout.addWidget(self.status)

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self._COLUMNS))
        DashboardWidget._configure_table(self.table)
        header = self.table.horizontalHeader()
        # Every column can be widened by dragging its edge in the header.
        for column, width in enumerate(self._COLUMN_WIDTHS):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.table.setWordWrap(True)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.horizontalHeaderItem(self.SQUARE_COLUMN).setToolTip(
            "Poziom placu turniejowego atakującego wyliczony z czasu przemarszu "
            "(+10% prędkości za poziom powyżej 20 pól). Skład wojsk jest ukryty, "
            "więc podano poziomy pasujące do jednostek, które mogą iść najwolniej."
        )
        self.table.cellDoubleClicked.connect(self._show_attack_details)
        layout.addWidget(self.table)

        self.delete_button = QPushButton("Usuń zaznaczone wpisy")
        self.delete_button.clicked.connect(self._request_delete_selected)
        layout.addWidget(self.delete_button, alignment=Qt.AlignRight)

        hint = QLabel(
            "Historia ataków jest zapisywana lokalnie. Zaznacz kilka wierszy "
            "z Ctrl lub Shift, aby usunąć je naraz. Podwójne kliknięcie "
            "pokazuje szczegóły i zdarzenia; aplikacja nie wysyła obrony "
            "ani innych działań."
        )
        hint.setObjectName("secondaryText")
        layout.addWidget(hint)

    @staticmethod
    def _format_eta(seconds_remaining: float) -> str:
        seconds = max(0, int(seconds_remaining))
        days, seconds = divmod(seconds, 24 * 60 * 60)
        hours, seconds = divmod(seconds, 60 * 60)
        minutes, seconds = divmod(seconds, 60)
        parts = []
        if days:
            parts.append(f"{days} d")
        if hours or days:
            parts.append(f"{hours} godz.")
        if minutes or hours or days:
            parts.append(f"{minutes} min")
        parts.append(f"{seconds} s")
        return " ".join(parts)

    def update_attacks(self, attacks: list[dict], checked_at: datetime):
        now = monotonic()
        self._attacks = [dict(attack) for attack in attacks]
        attack_keys = set()
        for attack in attacks:
            attack_key = (
                f"{attack['village_id']}:{attack['movement_id']}"
            )
            attack_keys.add(attack_key)
            remaining = float(attack["seconds_remaining"])
            previous = self._eta_references.get(attack_key)
            if previous is None or abs(
                previous[0] - max(0.0, remaining - (now - previous[1]))
            ) > 2:
                self._eta_references[attack_key] = (remaining, now)
        self._eta_references = {
            key: value
            for key, value in self._eta_references.items()
            if key in attack_keys
        }
        self._render_history()
        self.status.setText(
            f"Nasłuch live · nadchodzące ruchy: {len(attacks)} · "
            f"historia: {len(self._history)} · aktualizacja "
            f"{checked_at:%H:%M:%S}"
        )

    def update_history(self, records: list[dict]):
        self._history = [dict(record) for record in records]
        self._render_history()
        self.status.setText(
            f"Historia lokalna · rekordów: {len(self._history)}"
        )

    @staticmethod
    def _format_record_time(value: object) -> str:
        if isinstance(value, datetime):
            return value.strftime("%d.%m %H:%M:%S")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).strftime("%d.%m %H:%M:%S")
            except ValueError:
                return value
        return "—"

    def _render_history(self):
        active_by_key = {
            f"{attack['village_id']}:{attack['movement_id']}": attack
            for attack in self._attacks
        }
        records = list(self._history)
        known_keys = {
            record.get("movement_key")
            for record in records
            if record.get("movement_key")
        }
        for key, attack in active_by_key.items():
            if key not in known_keys:
                records.append({
                    **attack,
                    "movement_key": key,
                    "status": "incoming",
                    "first_detected_at": None,
                    "id": None,
                })

        self.table.setRowCount(len(records))
        self._row_by_attack_key = {}
        for row, record in enumerate(records):
            key = record.get("movement_key") or (
                f"{record['village_id']}:{record['movement_id']}"
            )
            attack = active_by_key.get(key)
            if attack is not None:
                self._row_by_attack_key[key] = row
            arrival_time = float(record["arrival_time"])
            status = record.get("status", "incoming")
            status_labels = {
                "incoming": "Nadchodzący",
                "withdrawn": "Wycofany",
                "arrived": "Dotarł",
            }
            values = (
                record.get("target_village_name", record.get("village_name", "")),
                record.get("attacker_name", ""),
                record.get(
                    "source_village_name",
                    record.get("source_village", ""),
                ),
                self._coordinates_text(record, attack),
                record.get("movement_type", ""),
                datetime.fromtimestamp(arrival_time).strftime("%d.%m %H:%M:%S"),
                self._square_text(record, attack),
                (
                    self._format_eta(attack["seconds_remaining"])
                    if attack is not None
                    else "—"
                ),
                status_labels.get(status, status),
                self._format_record_time(record.get("first_detected_at")),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, record.get("id"))
                if column == self.SQUARE_COLUMN and str(value) != "—":
                    item.setToolTip(str(value).replace("; ", "\n"))
                self.table.setItem(row, column, item)
            first_item = self.table.item(row, 0)
            first_item.setData(
                Qt.UserRole + 1,
                json.dumps({
                    "details": record.get("details", {}),
                    "events": record.get("event_log", []),
                }, ensure_ascii=False, indent=2),
            )
            if record.get("masked"):
                self.table.item(row, 2).setToolTip(
                    "Skład wojsk jest ukryty w danych gry."
                )
        self.refresh_eta()

    @staticmethod
    def _coordinates_text(record: dict, attack: dict | None) -> str:
        """"(x|y)" of the source village, from the game or its village id."""
        for source in (attack or {}, record, record.get("details") or {}):
            if not isinstance(source, dict):
                continue
            x, y = source.get("source_x"), source.get("source_y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return f"({int(x)}|{int(y)})"
        raw_id = str(record.get("source_village_id") or "")
        if raw_id.isdigit():
            # Travian Kingdoms village ids encode the map position.
            village_id = int(raw_id)
            return f"({village_id % 32768 - 16384}|{village_id // 32768 - 16384})"
        return "—"

    @staticmethod
    def _square_text(record: dict, attack: dict | None) -> str:
        for source in (attack or {}, record, record.get("details") or {}):
            value = source.get("tournament_square") if isinstance(source, dict) else None
            if value:
                return str(value)
        return "—"

    def _show_attack_details(self, row: int, _column: int):
        item = self.table.item(row, 0)
        if item is None:
            return
        details = item.data(Qt.UserRole + 1)
        if not details:
            return
        QMessageBox.information(
            self,
            "Szczegóły ataku",
            str(details),
        )

    def _request_delete_selected(self):
        rows = sorted({item.row() for item in self.table.selectedItems()})
        record_ids = []
        for row in rows:
            item = self.table.item(row, 0)
            record_id = item.data(Qt.UserRole) if item is not None else None
            if isinstance(record_id, int) and record_id not in record_ids:
                record_ids.append(record_id)
        if not record_ids:
            QMessageBox.information(
                self,
                "Historia ataków",
                "Najpierw zaznacz rekordy do usunięcia.",
            )
            return
        self.delete_requested.emit(record_ids)

    def refresh_eta(self):
        now = monotonic()
        for attack in self._attacks:
            attack_key = f"{attack['village_id']}:{attack['movement_id']}"
            row = self._row_by_attack_key.get(attack_key)
            if row is None:
                continue
            item = self.table.item(row, self.ETA_COLUMN)
            if item is not None:
                baseline, updated_at = self._eta_references[attack_key]
                item.setText(self._format_eta(
                    baseline - max(0.0, now - updated_at)
                ))

    def set_listening(self, village_count: int):
        self.status.setText(
            f"Nasłuch live aktywny · {village_count} wiosek"
        )

    def set_monitoring_error(self, message: str):
        self.status.setText(f"Nie udało się uruchomić nasłuchu live: {message}")

    def set_disconnected(self):
        self.status.setText(
            f"Brak połączenia z grą — zachowano historię "
            f"({len(self._history)} wpisów)."
        )


class VillageDetailWidget(QWidget):
    """Detailed view for a single village."""
    
    def __init__(self, village_tracker: VillageTracker, parent=None):
        super().__init__(parent)
        self.tracker = village_tracker
        
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("Village Details")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        layout.addWidget(title)
        
        # Village info section
        info_group = QGroupBox("Village Information")
        info_layout = QFormLayout(info_group)
        
        self.name_label = QLabel("-")
        self.coord_label = QLabel("-")
        self.pop_label = QLabel("-")
        self.farm_label = QLabel("-")
        self.chief_label = QLabel("-")
        self.temple_label = QLabel("-")
        self.resource_labels = {
            "wood": QLabel("-"),
            "clay": QLabel("-"),
            "iron": QLabel("-"),
            "grain": QLabel("-"),
        }
        
        info_layout.addRow("Name:", self.name_label)
        info_layout.addRow("Coordinates:", self.coord_label)
        info_layout.addRow("Population:", self.pop_label)
        info_layout.addRow("Farm Level:", self.farm_label)
        info_layout.addRow("Chief Level:", self.chief_label)
        info_layout.addRow("Temple Level:", self.temple_label)
        for resource_type, label in self.resource_labels.items():
            info_layout.addRow(f"{resource_type.title()}:", label)
        
        layout.addWidget(info_group)
        
        # Building queue section
        build_group = QGroupBox("Budynki i kolejka")
        build_layout = QVBoxLayout(build_group)
        
        self.build_status = QLabel("Brak zapisanych danych o budynkach.")
        build_layout.addWidget(self.build_status)
        self.buildings_table = QTableWidget(0, 5)
        self.buildings_table.setHorizontalHeaderLabels(
            ["Budynek", "Pole", "Poziom", "Status", "Ukończenie"]
        )
        DashboardWidget._configure_table(self.buildings_table, stretch_columns=(0, 4))
        build_layout.addWidget(self.buildings_table)
        
        layout.addWidget(build_group)
    
    def update_village(self, village_id: str):
        """Update display with village data."""
        try:
            from src.models.village import Village
        except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
            from models.village import Village
        
        village = self.tracker.get_village(village_id)
        if village:
            self.name_label.setText(village.name or "Unknown")
            self.coord_label.setText(f"[{village.row}/{village.col}/{village.field_num}]")
            self.pop_label.setText(str(village.population))
            self.farm_label.setText(str(village.farm_level))
            self.chief_label.setText(str(village.chief_level))
            self.temple_label.setText(str(village.temple_level))
            for resource_type, label in self.resource_labels.items():
                resource = getattr(village, resource_type)
                label.setText(
                    f"{resource.current_amount:,} / {resource.max_capacity:,} "
                    f"({resource.production_rate:+.0f}/h)"
                )

            snapshots = self.tracker.repo.get_dashboard_snapshots(village_id)
            buildings = snapshots.get("buildings", [])
            self.buildings_table.setRowCount(len(buildings))
            active_buildings = 0
            for row, building in enumerate(buildings):
                self.buildings_table.setItem(
                    row, 0, QTableWidgetItem(str(building.get("name", "Unknown")))
                )
                self.buildings_table.setItem(
                    row, 1, QTableWidgetItem(str(building.get("slot", "")))
                )
                self.buildings_table.setItem(
                    row, 2, QTableWidgetItem(str(building.get("level", "")))
                )
                upgrading = bool(building.get("upgrading"))
                active_buildings += int(upgrading)
                self.buildings_table.setItem(
                    row, 3, QTableWidgetItem("Ulepszanie" if upgrading else "")
                )
                self.buildings_table.setItem(
                    row, 4,
                    QTableWidgetItem(str(building.get("upgrade_complete_at") or "")),
                )
            next_completion = next(
                (
                    building.get("upgrade_complete_at")
                    for building in buildings
                    if building.get("upgrading")
                    and building.get("upgrade_complete_at")
                ),
                None,
            )
            self.build_status.setText(
                f"Aktywne ulepszenia: {active_buildings}; "
                f"najbliższe zakończenie: {next_completion}"
                if next_completion
                else f"Aktywne ulepszenia: {active_buildings}"
                if buildings
                else "Brak zapisanych danych o budynkach."
            )


class AccountDialog(QDialog):
    """Account creation/login dialog for adding a Travian account."""
    
    def __init__(self, account_mgr: 'AccountManager', parent=None):
        super().__init__(parent)
        self.account_mgr = account_mgr
        self.setWindowTitle("Add Game Account")
        self.setModal(True)
        self.resize(350, 320)
        
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("Add New Account")
        title.setFont(QFont("Segoe UI", 14, QFont.Bold))
        layout.addWidget(title)
        
        # Info label
        info = QLabel("Enter your Travian Kingdoms account details:")
        info.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(info)
        
        # Form fields
        form_group = QGroupBox("Account Details")
        form_layout = QFormLayout(form_group)
        
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Enter your username")
        form_layout.addRow("Username:", self.username_edit)
        
        self.world_edit = QLineEdit()
        self.world_edit.setPlaceholderText("e.g., alpha, beta, main")
        form_layout.addRow("World:", self.world_edit)
        
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("e.g., 1, 2, event")
        form_layout.addRow("Server:", self.server_edit)
        
        self.alliance_edit = QLineEdit()
        self.alliance_edit.setPlaceholderText("(optional)")
        form_layout.addRow("Alliance:", self.alliance_edit)
        
        layout.addWidget(form_group)
        
        # Existing accounts list
        if account_mgr.get_all_accounts():
            accounts_label = QLabel("Existing Accounts:")
            accounts_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
            layout.addWidget(accounts_label)
            
            self.accounts_list = QListWidget()
            for acc in account_mgr.get_all_accounts():
                item = QListWidgetItem(f"{acc.username} ({acc.world}/{acc.server})")
                item.setData(Qt.UserRole, acc.account_id)
                self.accounts_list.addItem(item)
            layout.addWidget(self.accounts_list)
        
        # Buttons
        button_layout = QHBoxLayout()
        save_btn = QPushButton("Add Account")
        save_btn.clicked.connect(self._save_account)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
    
    def _save_account(self):
        """Save the new account."""
        username = self.username_edit.text().strip()
        world = self.world_edit.text().strip()
        server = self.server_edit.text().strip()
        alliance = self.alliance_edit.text().strip() or None
        
        if not username or not world or not server:
            QMessageBox.warning(self, "Input Error", "Username, World, and Server are required!")
            return
        
        account_id = f"{username}-{world}-{server}".lower()
        
        try:
            self.account_mgr.add_account(
                account_id=account_id,
                username=username,
                world=world,
                server=server,
                alliance=alliance,
            )
            QMessageBox.information(self, "Success", f"Account '{username}' added successfully!")
            self.accept()
        except Exception as e:
            if "UNIQUE" in str(e):
                QMessageBox.warning(self, "Duplicate", "This account already exists!")
            else:
                QMessageBox.critical(self, "Error", f"Failed to add account: {e}")


class SettingsDialog(QDialog):
    """Application settings dialog."""
    
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(400, 300)
        
        layout = QVBoxLayout(self)
        
        # Browser settings
        browser_group = QGroupBox("Browser Connection")
        browser_layout = QFormLayout(browser_group)
        
        self.cdp_address_edit = QLineEdit(str(self.settings.browser.cdp_address))
        self.cdp_port_edit = QLineEdit(str(self.settings.browser.cdp_port))
        browser_layout.addRow("CDP Address:", self.cdp_address_edit)
        browser_layout.addRow("CDP Port:", self.cdp_port_edit)
        
        layout.addWidget(browser_group)
        
        # Database settings
        db_group = QGroupBox("Database")
        db_layout = QFormLayout(db_group)
        
        self.db_path_edit = QLineEdit(self.settings.database.db_path)
        db_layout.addRow("Database Path:", self.db_path_edit)
        
        layout.addWidget(db_group)
        
        # Buttons
        button_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_settings)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
    
    def _save_settings(self):
        """Save settings and close dialog."""
        self.settings.browser.cdp_address = self.cdp_address_edit.text()
        self.settings.browser.cdp_port = int(self.cdp_port_edit.text())
        self.settings.database.db_path = self.db_path_edit.text()
        self.settings.save()
        self.accept()


class MainWindow(QMainWindow):
    """Main application window."""

    incoming_attack_update = Signal(dict)
    village_names_update = Signal(dict)
    attack_log_message = Signal(str)
    hideout_log_message = Signal(str)
    hideout_handled = Signal(object)
    hideout_active = Signal(str, object)
    kingdom_loaded = Signal(object)
    tk_troops_updated = Signal(object)
    gt_troops_updated = Signal(object)
    scout_log_message = Signal(str)
    hero_xp_read = Signal(str, object)
    scout_context_ready = Signal(object, object)
    manual_attacker_resolved = Signal(object, int, int)
    manual_attacker_xp = Signal(str, int)
    scout_found = Signal(object)
    build_log_message = Signal(str)
    build_snapshot_ready = Signal(object)
    recruitment_click_done = Signal(str, object, object)
    
    def __init__(self, settings: AppSettings, account_mgr: AccountManager, 
                 village_tracker: VillageTracker,
                 incoming_attack_repo: IncomingAttackRepository | None = None):
        super().__init__()
        self.settings = settings
        self.account_mgr = account_mgr
        self.tracker = village_tracker
        self.incoming_attack_repo = incoming_attack_repo
        self.browser_session: TravianBrowserSession | None = None
        self._auto_recruitment_candidates: list[dict] = []
        self._auto_recruitment_active = False
        self._pending_recruitment_responses: dict[str, int] = {}
        # When each pending (unconfirmed) order lock was set in this session;
        # locks loaded from disk start counting at launch.
        self._pending_order_since: dict[str, float] = {
            key: monotonic() for key in settings.recruitment.pending_orders
        }
        self._milling_candidates: list[dict] = []
        # Hourly recruitment costs per village (recruitment tab estimate).
        self._recruitment_cost_rates: dict[str, dict] = {}
        # Hourly production of every village read from the game models.
        self._village_production: dict[str, dict[str, int]] = {}
        self._milling_order_active = False
        self._milling_scan_active = False
        self._pending_milling_responses: dict[str, int] = {}
        self._connected_milling_controls: set[int] = set()
        self._seen_incoming_attack_ids: set[str] = set()
        # Villages whose first (baseline) movement snapshot was processed.
        self._attack_listener_villages: set[str] = set()
        self._square_estimates: dict[str, str] = {}
        self._untimed_attacks: set[str] = set()
        self._square_levels: dict[str, dict] = {}
        self._hero_xp_requested: set[str] = set()
        self._unit_speeds: dict[int, dict[int, float]] | None = None
        self._incoming_attacks_by_key: dict[str, dict] = {}
        # Immutable copy of the live incoming attacks for the build worker thread.
        self._build_threats: tuple[dict, ...] = ()
        self._dismissed_incoming_attack_ids: set[str] = set()
        self._vacation_pending_request: tuple[str, bool, int] | None = None
        self._vacation_retry_after = 0.0
        self._vacation_last_logged: tuple | None = None
        
        self.setWindowTitle(settings.app_name)
        self.resize(1280, 860)
        self.setMinimumSize(980, 680)
        self.setStyleSheet(_APP_STYLESHEET + _icon_stylesheet())
        
        self._create_menu_bar()
        self._createCentralWidget()
        self.recruitment_tab.auto_recruitment_checkbox.setChecked(
            settings.recruitment.automatic_enabled
        )
        self.milling_tab.minimum_gold_balance.setValue(
            settings.resource_milling.minimum_gold_balance
        )
        
        # Auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._on_refresh)
        self.refresh_timer.start(30000)  # 30 seconds
        self.troops_refresh_timer = QTimer(self)
        self.troops_refresh_timer.setInterval(_TROOP_REFRESH_INTERVAL_MS)
        self.troops_refresh_timer.timeout.connect(
            lambda: self._read_all_village_troops(automatic=True)
        )
        self.recruitment_queue_timer = QTimer(self)
        self.recruitment_queue_timer.setInterval(
            _RECRUITMENT_QUEUE_REFRESH_INTERVAL_MS
        )
        self.recruitment_queue_timer.timeout.connect(
            self._check_recruitment_queues
        )
        self.resource_milling_timer = QTimer(self)
        self.resource_milling_timer.setInterval(
            _RESOURCE_MILLING_REFRESH_INTERVAL_MS
        )
        self.resource_milling_timer.timeout.connect(
            self._check_resource_milling
        )
        self.vacation_timer = QTimer(self)
        self.vacation_timer.setInterval(_VACATION_CHECK_INTERVAL_MS)
        self.vacation_timer.timeout.connect(self._check_vacation)
        self.attack_automation = None
        self._attack_worker = None
        self.attack_log_message.connect(self._on_attack_log)
        self.attack_timer = QTimer(self)
        self.attack_timer.setInterval(1000)
        self.attack_timer.timeout.connect(self._attack_tick)
        self.hideout_defense = None
        self._hideout_worker = None
        self.hideout_log_message.connect(self.hideout_tab.add_log)
        self.hideout_handled.connect(self._mark_hideout_movements_handled)
        self.hideout_active.connect(self._set_hideout_active)
        self.hideout_timer = QTimer(self)
        self.hideout_timer.setInterval(1000)
        self.hideout_timer.timeout.connect(self._hideout_tick)
        self._kingdom_worker = None
        self.kingdom_loaded.connect(self._on_kingdom_loaded)
        self.tk_troops_updated.connect(self._on_tktools_troops_updated)
        self.gt_troops_updated.connect(self._on_gettertools_troops_updated)
        self.scout_automation = None
        self._scout_worker = None
        self._hero_xp_by_attack: dict[str, int] = {}
        self._scout_context_at = monotonic()
        self.scout_log_message.connect(self.scout_tab.add_log)
        self.hero_xp_read.connect(self._on_hero_xp_read)
        self.scout_context_ready.connect(self.scout_tab.set_context)
        self.manual_attacker_resolved.connect(self._on_manual_attacker_resolved)
        self.manual_attacker_xp.connect(self.scout_tab.set_manual_hero_xp)
        self.scout_found.connect(self._on_scout_found)
        self.scout_timer = QTimer(self)
        self.scout_timer.setInterval(1000)
        self.scout_timer.timeout.connect(self._scout_tick)
        self._gt_update_worker = None
        self._tk_update_worker = None
        self.kingdom_timer = QTimer(self)
        self.kingdom_timer.setInterval(_KINGDOM_REFRESH_INTERVAL_MS)
        self.kingdom_timer.timeout.connect(self._refresh_kingdom)
        self.recruitment_click_done.connect(self._on_recruitment_click_done)
        self.build_automation = None
        self._build_worker = None
        self._build_worker_started = 0.0
        self.build_log_message.connect(self.build_tab.add_log)
        self.build_snapshot_ready.connect(self._on_build_snapshot)
        self.build_timer = QTimer(self)
        self.build_timer.setInterval(_BUILD_CHECK_INTERVAL_MS)
        self.build_timer.timeout.connect(lambda: self._run_build(click=True))
        self._build_wakeup = QTimer(self)
        self._build_wakeup.setSingleShot(True)
        self._build_wakeup.timeout.connect(lambda: self._run_build(click=True))
        self.incoming_attack_eta_timer = QTimer(self)
        self.incoming_attack_eta_timer.setInterval(1000)
        self.incoming_attack_eta_timer.timeout.connect(
            self.incoming_attacks_tab.refresh_eta
        )

        self.account_mgr.load_from_database()
        self.tracker.load_from_database()

        self.dashboard_tab.connect_button.clicked.connect(self._connect_browser)
        self.dashboard_tab.launch_browser_button.clicked.connect(self._launch_browser)
        self.dashboard_tab.disconnect_button.clicked.connect(self._disconnect_browser)
        self.dashboard_tab.refresh_btn.clicked.connect(self._on_refresh)
        self.dashboard_tab.village_selector.currentIndexChanged.connect(
            self._select_village
        )
        self.dashboard_tab.refresh_troops_btn.clicked.connect(
            lambda _checked=False: self._read_all_village_troops()
        )
        self.recruitment_tab.check_recruitment_queues_btn.clicked.connect(
            self._check_recruitment_queues
        )
        self.recruitment_tab.auto_recruitment_checkbox.toggled.connect(
            self._set_automatic_recruitment_enabled
        )
        self.recruitment_tab.village_strategy_changed.connect(
            self._set_village_recruitment_strategy
        )
        self.recruitment_tab.village_batch_changed.connect(
            self._set_village_recruitment_batch_size
        )
        self.recruitment_tab.village_threshold_changed.connect(
            self._set_village_recruitment_threshold
        )
        self.recruitment_tab.village_rule_copy_requested.connect(
            self._copy_village_recruitment_rule
        )
        self.recruitment_tab.village_enabled_toggled.connect(
            self._set_village_recruitment_enabled
        )
        self.recruitment_tab.unit_toggled.connect(
            self._set_village_recruitment_unit
        )
        self.recruitment_tab.queue_minutes_changed.connect(
            self._set_village_queue_minutes
        )
        self.recruitment_tab.clear_recruitment_pending_btn.clicked.connect(
            self._clear_pending_recruitment_orders
        )
        self.milling_tab.minimum_gold_balance.valueChanged.connect(
            self._set_milling_gold_reserve
        )
        self.milling_tab.clear_pending_btn.clicked.connect(
            self._clear_pending_milling_orders
        )
        self.incoming_attack_update.connect(
            self._on_incoming_attack_update
        )
        self.village_names_update.connect(
            self._on_village_names_update
        )
        self._refresh_data()
        self._auto_connecting = False
        self._auto_connect_attempts = 0
        self._auto_connect_timer = QTimer(self)
        self._auto_connect_timer.setInterval(5000)
        self._auto_connect_timer.timeout.connect(self._try_auto_connect)
        if self.settings.browser.auto_connect:
            QTimer.singleShot(1000, self._start_auto_connect)

    # Automatic connection on start -------------------------------------------------
    def _cdp_tabs(self) -> list[dict] | None:
        """Pages of the debug browser, or None when it is not running."""
        import urllib.request

        url = f"http://{self.settings.browser.cdp_address}/json/list"
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def _open_game_tab(self) -> None:
        """Open the remembered game world in the debug browser."""
        import urllib.parse
        import urllib.request

        url = self.settings.browser.game_url or "https://www.kingdoms.com/"
        if not url.startswith("https://") or "kingdoms.com" not in url:
            url = "https://www.kingdoms.com/"
        endpoint = (
            f"http://{self.settings.browser.cdp_address}/json/new?"
            + urllib.parse.quote(url, safe=":/#?=&")
        )
        try:
            request = urllib.request.Request(endpoint, method="PUT")
            urllib.request.urlopen(request, timeout=3).close()
            logger.info("Opened the game page %s", url)
        except Exception as error:
            logger.warning("Could not open the game page: %s", error)

    def _remember_game_url(self) -> None:
        """Store the game world's address for opening it automatically."""
        from urllib.parse import urlsplit

        try:
            url = self.browser_session._tab._kwargs.get("url") or ""
        except Exception:
            return
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname and parts.hostname.endswith("kingdoms.com")                 and not parts.hostname.startswith("www."):
            game_url = f"https://{parts.hostname}/"
            if self.settings.browser.game_url != game_url:
                self.settings.browser.game_url = game_url
                self._save_recruitment_settings()

    def _start_auto_connect(self):
        """Open the game browser if needed and connect as soon as the game is up."""
        if self.browser_session is not None:
            return
        self._game_tab_opened = False
        if self._cdp_tabs() is None:
            self._launch_browser()
        self._auto_connect_attempts = 0
        self.dashboard_tab.connection_status.setText(
            "Łączę automatycznie — czekam na zalogowaną kartę gry…"
        )
        self._auto_connect_timer.start()

    def _try_auto_connect(self):
        if self.browser_session is not None:
            self._auto_connect_timer.stop()
            return
        self._auto_connect_attempts += 1
        if self._auto_connect_attempts > 180:  # 15 minutes
            self._auto_connect_timer.stop()
            self.dashboard_tab.connection_status.setText(
                "Nie znaleziono zalogowanej gry. Zaloguj się i kliknij „Połącz”."
            )
            return
        tabs = self._cdp_tabs()
        if tabs is None:
            return  # the browser is still starting
        pages = [tab for tab in tabs if tab.get("type") == "page"]
        if not any("kingdoms.com" in tab.get("url", "") for tab in pages):
            if not getattr(self, "_game_tab_opened", False):
                self._game_tab_opened = True
                self._open_game_tab()
            return
        game = [
            tab for tab in pages
            if "kingdoms.com" in tab.get("url", "")
            and "www." not in tab.get("url", "")
        ]
        if not game:
            return
        self._auto_connecting = True
        try:
            self._connect_browser()
        finally:
            self._auto_connecting = False
        if self.browser_session is not None:
            self._auto_connect_timer.stop()
            logger.info("Connected automatically on start")

    
    def _create_menu_bar(self):
        """Create the application menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        file_menu.addAction("&Settings", self._show_settings)
        file_menu.addAction("E&xit", self.close)
        
        # Account menu
        account_menu = menubar.addMenu("&Account")
        account_menu.addAction("&Add Account", self._add_account)
        account_menu.addAction("&Refresh Data", self._refresh_data)
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        help_menu.addAction("&About", self._show_about)

        self.author_label = QLabel("Author: Dzogurt")
        self.author_label.setObjectName("secondaryText")
        self.author_label.setContentsMargins(0, 0, 12, 0)
        self.author_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.author_label.setFixedHeight(menubar.sizeHint().height())
        menubar.setCornerWidget(self.author_label, Qt.TopRightCorner)

    def _createCentralWidget(self):
        """Create the central widget with sidebar and content."""
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        
        # Sidebar
        sidebar = QGroupBox("Wioski z gry")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_hint = QLabel("Kliknij wioskę, aby ją wybrać")
        sidebar_hint.setObjectName("secondaryText")
        sidebar_layout.addWidget(sidebar_hint)
        
        self.village_list_widget = QListWidget()
        self.village_list_widget.setToolTip(
            "Wybierz wioskę, aby przełączyć widok gry i dane panelu."
        )
        self.village_list_widget.currentItemChanged.connect(self._on_village_selected)
        sidebar_layout.addWidget(self.village_list_widget)
        
        layout.addWidget(sidebar, stretch=1)
        
        # Content area with tabs
        self.content_tabs = QTabWidget()
        
        self.dashboard_tab = DashboardWidget(self.tracker)
        self.content_tabs.addTab(self.dashboard_tab, "Przegląd")

        self.recruitment_tab = RecruitmentWidget()
        self.recruitment_tab.bind_settings(self.settings.recruitment)
        self.content_tabs.addTab(self.recruitment_tab, "Rekrutacja")

        self.village_detail_tab = VillageDetailWidget(self.tracker)
        self.content_tabs.addTab(self.village_detail_tab, "Szczegóły wioski")

        self.milling_tab = MillingWidget()
        self.content_tabs.addTab(self.milling_tab, "Mielenie")

        self.incoming_attacks_tab = IncomingAttacksWidget()
        self.incoming_attacks_tab.delete_requested.connect(
            self._delete_incoming_attack_record
        )
        if self.incoming_attack_repo is not None:
            self.incoming_attacks_tab.update_history(
                self.incoming_attack_repo.get_recent_attacks()
            )
        self.content_tabs.addTab(self.incoming_attacks_tab, "Ataki")

        self.vacation_tab = VacationWidget()
        self.vacation_tab.load_settings(self.settings.vacation)
        self.vacation_tab.enabled_toggled.connect(self._set_vacation_enabled)
        self.vacation_tab.setting_changed.connect(self._set_vacation_setting)
        self.vacation_tab.check_requested.connect(self._check_vacation)
        self.vacation_tab.plan_requested.connect(self._plan_vacation_manually)
        self.vacation_tab.cancel_requested.connect(self._cancel_vacation_manually)
        self.content_tabs.addTab(self.vacation_tab, "Wakacje")

        self.attack_tab = AttackPlanWidget()
        self.attack_tab.load_settings(self.settings.attacks)
        self.attack_tab.enabled_toggled.connect(self._set_attacks_enabled)
        self.attack_tab.setting_changed.connect(self._set_attack_setting)
        self.attack_tab.refresh_requested.connect(self._refresh_attack_plan)
        self.content_tabs.addTab(self.attack_tab, "Plan ataków")

        self.hideout_tab = HideoutDefenseWidget()
        self.hideout_tab.load_settings(self.settings.hideout_defense)
        self.hideout_tab.enabled_toggled.connect(self._set_hideout_enabled)
        self.hideout_tab.setting_changed.connect(self._set_hideout_setting)
        self.content_tabs.addTab(self.hideout_tab, "Obrona przed kryjówkami")

        self.scout_tab = ScoutWidget()
        self.scout_tab.load_settings(self.settings.scout)
        self.scout_tab.enabled_toggled.connect(self._set_scout_enabled)
        self.scout_tab.setting_changed.connect(self._set_scout_setting)
        self.scout_tab.plan_requested.connect(self._plan_scouting)
        self.scout_tab.cancel_requested.connect(self._cancel_scouting)
        self.scout_tab.manual_attacker_requested.connect(self._resolve_manual_attacker)
        self.content_tabs.addTab(self.scout_tab, "Zwiad")

        self.build_tab = VillageBuildWidget()
        self.build_tab.load_settings(self.settings.build)
        self.build_tab.enabled_toggled.connect(self._set_build_enabled)
        self.build_tab.setting_changed.connect(self._set_build_setting)
        self.build_tab.village_toggled.connect(self._set_build_village)
        self.build_tab.refresh_requested.connect(lambda: self._run_build(click=False))
        self.build_tab.overflow_unit_changed.connect(self._set_build_overflow_unit)
        self.build_tab.military_changed.connect(self._set_build_military)
        self.content_tabs.addTab(self.build_tab, "Budowa")

        self.kingdom_tab = KingdomWidget()
        self.kingdom_tab.refresh_requested.connect(self._refresh_kingdom)
        self.kingdom_tab.update_tk_requested.connect(self._update_tktools_troops)
        self.kingdom_tab.update_gt_requested.connect(self._update_gettertools_troops)
        self.content_tabs.addTab(self.kingdom_tab, "Królestwo")
        
        layout.addWidget(self.content_tabs, stretch=3)
        
        self.setCentralWidget(central)
    
    def _on_refresh(self):
        """Handle timer refresh."""
        self._refresh_data()
        if self.browser_session is not None:
            self._read_live_state()

    def _connect_browser(self):
        """Connect to an already-authenticated local Travian browser tab."""
        try:
            self._seen_incoming_attack_ids.clear()
            self._incoming_attacks_by_key.clear()
            self._attack_listener_villages.clear()
            self.browser_session = TravianBrowserSession(
                self.settings.browser.cdp_address,
                self.settings.browser.cdp_port,
            )
            self.browser_session.set_network_response_handler(
                self.tracker.process_network_response
            )
            self.browser_session.set_village_names_handler(
                self.village_names_update.emit
            )
            state = self.browser_session.connect()
            logger.info("Connected to the game")
            self._remember_game_url()
            self.browser_session.set_incoming_attack_handler(
                self.incoming_attack_update.emit
            )
            self.tracker.sync_live_state(state)
            self._persist_live_dashboard_state(state)
            self.dashboard_tab.update_live_state(state)
            self._sync_live_village_list(state)
            if state.get("selected_village_id"):
                self.village_detail_tab.update_village(
                    str(state["selected_village_id"])
                )
            self.dashboard_tab.connect_button.setEnabled(False)
            self.dashboard_tab.disconnect_button.setEnabled(True)
            self.dashboard_tab.refresh_troops_btn.setEnabled(
                bool(state["villages"])
            )
            if state["villages"]:
                self.dashboard_tab.troops_status.setText(
                    "Pierwszy odczyt za chwilę. Kolejne automatycznie co 5 minut."
                )
                self.troops_refresh_timer.start()
                self.recruitment_queue_timer.start()
                self.resource_milling_timer.start()
                self.vacation_timer.start()
                try:
                    village_count = (
                        self.browser_session.start_incoming_attack_listener(
                            state["villages"]
                        )
                    )
                    self.incoming_attacks_tab.set_listening(village_count)
                    self.incoming_attack_eta_timer.start()
                except Exception as error:
                    logger.warning(
                        "Could not start live incoming-attack monitoring: %s",
                        error,
                    )
                    self.incoming_attacks_tab.set_monitoring_error(str(error))
                QTimer.singleShot(
                    1500,
                    lambda: self._read_all_village_troops(automatic=True),
                )
                QTimer.singleShot(2000, self._check_recruitment_queues)
                QTimer.singleShot(2500, self._check_resource_milling)
                QTimer.singleShot(3500, self._check_vacation)
                self._start_attack_automation()
                self._start_hideout_defense()
                self.kingdom_timer.start()
                self._start_scouting()
                self._start_building()
                QTimer.singleShot(4500, self._refresh_kingdom)
                QTimer.singleShot(7000, self._backfill_attacker_squares)
            else:
                self.recruitment_queue_timer.stop()
                self.resource_milling_timer.stop()
                self.vacation_timer.stop()
                self.incoming_attack_eta_timer.stop()
        except Exception as error:
            self.browser_session = None
            logger.warning("Travian browser connection failed: %s", error)
            if not getattr(self, "_auto_connecting", False):
                QMessageBox.warning(self, "Connection failed", str(error))

    def _launch_browser(self):
        """Launch a dedicated local browser profile for the game session."""
        try:
            launch_chrome_for_game(
                self.settings.browser.cdp_address,
                self.settings.browser.cdp_port,
            )
            self.dashboard_tab.connection_status.setText(
                "Przeglądarka otwarta. Zaloguj się do Traviana i wybierz „Połącz”."
            )
        except (FileNotFoundError, OSError) as error:
            QMessageBox.warning(self, "Could not open Chrome", str(error))

    def _read_live_state(self):
        """Refresh visible game data without triggering any in-game action."""
        if self.browser_session is None:
            return
        try:
            state = self.browser_session.read_game_state()
            self.tracker.sync_live_state(state)
            self._persist_live_dashboard_state(state)
            self.dashboard_tab.update_live_state(state)
            self._sync_live_village_list(state)
            village_id = str(state.get("selected_village_id", ""))
            if village_id:
                self.village_detail_tab.update_village(village_id)
        except Exception as error:
            logger.warning("Travian game state refresh failed: %s", error)
            self._disconnect_browser()
            self.dashboard_tab.connection_status.setText(
                f"Utracono połączenie: {error}"
            )
            if self.settings.browser.auto_connect:
                # Reopen the browser if it is gone and connect again by itself.
                QTimer.singleShot(5000, self._start_auto_connect)

    def _select_village(self, index: int):
        """Switch the game view to the village selected in the dashboard."""
        if self.browser_session is None or index < 0:
            return
        village_id = self.dashboard_tab.village_selector.itemData(index)
        if not village_id:
            return
        try:
            self.browser_session.select_village(str(village_id))
            self.dashboard_tab.connection_status.setText("Wczytywanie wybranej wioski…")
            QTimer.singleShot(1000, self._read_live_state)
        except Exception as error:
            logger.warning("Travian village selection failed: %s", error)
            QMessageBox.warning(self, "Village selection failed", str(error))

    def _read_all_village_troops(self, automatic: bool = False):
        """Read stationed troops for each village, manually or on a timer."""
        state = self.dashboard_tab._live_state
        if self.browser_session is None or state is None:
            return
        self.dashboard_tab.refresh_troops_btn.setEnabled(False)
        self.dashboard_tab.troops_status.setText(
            "Automatyczny odczyt wojsk…" if automatic
            else "Odczyt wojsk w każdej wiosce…"
        )
        try:
            rows = self.browser_session.read_troops_for_villages(
                state.get("villages", [])
            )
            self.dashboard_tab._render_troops(rows)
            troops_by_village: dict[str, list[dict]] = {}
            for troop in rows:
                troops_by_village.setdefault(troop["village_id"], []).append(troop)
            for village_id, village_troops in troops_by_village.items():
                self.tracker.repo.save_dashboard_snapshot(
                    village_id, "troops", village_troops
                )
            self.dashboard_tab.troops_status.setText(
                f"Odczytano {len(rows)} typów jednostek "
                f"w {len(state.get('villages', []))} wioskach. "
                "Kolejny odczyt za 5 minut."
            )
            self._read_live_state()
        except Exception as error:
            logger.warning("Travian troop overview failed: %s", error)
            self.dashboard_tab.troops_status.setText(
                f"Nie udało się odczytać wojsk: {error}"
            )
            if not automatic:
                QMessageBox.warning(self, "Błąd odczytu wojsk", str(error))
        finally:
            if self.browser_session is not None:
                self.troops_refresh_timer.start()
            self.dashboard_tab.refresh_troops_btn.setEnabled(
                self.browser_session is not None
            )

    def _on_incoming_attack_update(self, snapshot: dict):
        village_id = snapshot["village_id"]
        observed_at = datetime.now()
        server_time = float(snapshot.get("server_time", observed_at.timestamp()))
        previous_keys = {
            key
            for key, attack in self._incoming_attacks_by_key.items()
            if attack["village_id"] == village_id
        }
        active_movement_keys = {
            f"{village_id}:{attack['movement_id']}"
            for attack in snapshot["attacks"]
        }
        self._estimate_attacker_squares(village_id, snapshot["attacks"], server_time)
        try:
            if self.incoming_attack_repo is not None:
                for attack in snapshot["attacks"]:
                    attack_key = f"{village_id}:{attack['movement_id']}"
                    if attack_key not in self._dismissed_incoming_attack_ids:
                        self.incoming_attack_repo.record_attack(
                            attack,
                            observed_at,
                        )
                self.incoming_attack_repo.reconcile_village(
                    village_id,
                    active_movement_keys,
                    server_time,
                    observed_at,
                )
                self.incoming_attacks_tab.update_history(
                    self.incoming_attack_repo.get_recent_attacks()
                )
        except Exception as error:
            logger.exception("Could not save incoming attack history")
            QMessageBox.warning(
                self,
                "Błąd historii ataków",
                f"Nie udało się zapisać historii ataku: {error}",
            )

        self._incoming_attacks_by_key = {
            key: attack
            for key, attack in self._incoming_attacks_by_key.items()
            if attack["village_id"] != village_id
        }
        self._dismissed_incoming_attack_ids.difference_update(
            previous_keys - active_movement_keys
        )

        new_attacks = []
        for attack in snapshot["attacks"]:
            attack_key = f"{village_id}:{attack['movement_id']}"
            self._incoming_attacks_by_key[attack_key] = attack
            if attack_key not in self._seen_incoming_attack_ids:
                self._seen_incoming_attack_ids.add(attack_key)
                new_attacks.append(attack)

        current_attacks = sorted(
            (
                attack
                for key, attack in self._incoming_attacks_by_key.items()
                if key not in self._dismissed_incoming_attack_ids
            ),
            key=lambda attack: attack["seconds_remaining"],
        )
        self.incoming_attacks_tab.update_attacks(
            current_attacks,
            observed_at,
        )
        self._build_threats = tuple(dict(attack) for attack in current_attacks)
        self._wake_build_for_attacks(server_time)
        if self.scout_automation is not None:
            for key in previous_keys - active_movement_keys:
                self.scout_automation.attack_withdrawn(key)
        self._refresh_scout_attacks()

        if new_attacks:
            details = []
            for attack in new_attacks:
                line = (
                    f"{attack['attacker_name']} — "
                    f"{attack['source_village']} → "
                    f"{attack['village_name']} "
                    f"({attack['movement_type']}, za "
                    f"{IncomingAttacksWidget._format_eta(attack['seconds_remaining'])})"
                )
                if attack["masked"]:
                    line += " — skład wojsk ukryty"
                details.append(line)
            QMessageBox.warning(
                self,
                "Wykryto nadchodzący atak",
                "\n".join(details)
                + "\n\nTo tylko ostrzeżenie. Aplikacja nie wysyła "
                "obrony ani żadnych działań do gry.",
            )

    def _estimate_attacker_squares(
        self, village_id: str, attacks: list[dict], server_time: float,
    ):
        """Attach the attacker's tournament square estimate to new attacks.

        The travel time is ``arrival - timeStart`` when the game gives the
        start, otherwise ``arrival - first live detection``; attacks already
        on their way when the listener started cannot be timed that way.
        """
        baseline = village_id not in self._attack_listener_villages
        self._attack_listener_villages.add(village_id)
        for attack in attacks:
            key = f"{village_id}:{attack['movement_id']}"
            if key not in self._square_estimates and key not in self._untimed_attacks:
                robber = bool(attack.get("robber")) or attack.get("attacker_name") == "Robber"
                time_start = attack.get("time_start") or 0
                duration = None
                if time_start > 0:
                    duration = float(attack["arrival_time"]) - float(time_start)
                elif not baseline:
                    duration = float(attack["arrival_time"]) - server_time
                if not robber and duration is None:
                    # Already on its way when listening started: keep any
                    # estimate saved earlier, never guess from this snapshot.
                    self._untimed_attacks.add(key)
                    attack["detected_live"] = False
                else:
                    tribe = int(attack.get("tribe_id") or 0)
                    if attack.get("distance") and duration is not None and not robber:
                        self._square_levels[key] = {
                            str(level): units for level, units in estimate_square_levels(
                                float(attack["distance"]), duration,
                                self._attacker_unit_speeds(tribe),
                            ).items()
                        }
                    self._square_estimates[key] = describe_square_level(
                        attack.get("distance"),
                        duration,
                        self._attacker_unit_speeds(tribe),
                        UNIT_NAMES.get(tribe, {}),
                        robber=robber,
                    )
            if key in self._square_estimates:
                attack["tournament_square"] = self._square_estimates[key]
                attack["detected_live"] = True
            if key in self._square_levels:
                attack["square_levels"] = self._square_levels[key]
            if key in self._hero_xp_by_attack:
                attack["hero_xp"] = self._hero_xp_by_attack[key]
            elif (
                attack.get("attacker_player_id") and not attack.get("robber")
                and key not in self._hero_xp_requested
            ):
                self._hero_xp_requested.add(key)
                self._read_attacker_hero_xp(key, attack["attacker_player_id"])
            if attack.get("tribe_id"):
                attack["tribe_id"] = int(attack["tribe_id"])

    def _backfill_attacker_squares(self):
        """Estimate the tournament square for history records saved without it.

        The travel time is ``arrival - first detection``; that is only right
        when the app was already running when the attack was sent, so records
        detected right after an app start (or marked as not live) are skipped.
        """
        session = self.browser_session
        repo = self.incoming_attack_repo
        if session is None or repo is None:
            return
        history = repo.get_recent_attacks()
        records = [
            record for record in history
            if not (record.get("details") or {}).get("tournament_square")
            or str((record.get("details") or {}).get("tournament_square")).startswith("≈")
        ]
        if not records:
            return
        batched = batch_detections(history)
        try:
            offset = session.read_server_clock_offset()
            tribes = session.read_village_tribes([
                str(record.get("source_village_id") or "") for record in records
            ])
        except Exception as error:
            logger.warning("Could not backfill tournament squares: %s", error)
            return
        starts = _app_start_times()
        changed = 0
        for record in records:
            details = record.get("details") or {}
            robber = record.get("attacker_name") == "Robber" or details.get("robber")
            text = None
            if robber:
                text = ROBBER_SQUARE_TEXT
            elif details.get("detected_live") is not False:
                detected = record.get("first_detected_at")
                if isinstance(detected, str):
                    try:
                        detected = datetime.fromisoformat(detected)
                    except ValueError:
                        detected = None
                source = str(record.get("source_village_id") or "")
                tribe = int(details.get("tribe_id") or 0) or tribes.get(source)
                distance = distance_between(source, record.get("target_village_id"))
                if (
                    isinstance(detected, datetime) and tribe and distance is not None
                    and not detected_at_start(detected, starts)
                    and record["id"] not in batched
                ):
                    duration = float(record["arrival_time"]) - (detected.timestamp() + offset)
                    text = "≈ " + describe_square_level(
                        distance, duration, self._attacker_unit_speeds(tribe),
                        UNIT_NAMES.get(tribe, {}),
                    )
            if text is None and str(details.get("tournament_square") or "").startswith("≈"):
                # An earlier backfill guessed from a reconnect: drop it.
                text = "—"
            if text and text != details.get("tournament_square"):
                repo.set_attack_detail(record["id"], "tournament_square", text)
                changed += 1
        if changed:
            self.incoming_attacks_tab.update_history(repo.get_recent_attacks())

    def _attacker_unit_speeds(self, tribe: int) -> dict[int, float]:
        """Speeds (fields/hour) of units 1-9 of *tribe* from the game config."""
        if self._unit_speeds is None and self.browser_session is not None:
            try:
                self._unit_speeds = self.browser_session.read_unit_speeds()
            except Exception as error:
                logger.warning("Could not read unit speeds: %s", error)
        return dict((self._unit_speeds or {}).get(tribe, {}))

    def _delete_incoming_attack_record(self, record_ids):
        """Delete one or several history records after one confirmation."""
        if isinstance(record_ids, int):
            record_ids = [record_ids]
        if self.incoming_attack_repo is None:
            QMessageBox.warning(
                self,
                "Historia ataków",
                "Historia ataków nie jest dostępna.",
            )
            return
        records = [
            record for record in (
                self.incoming_attack_repo.get_attack(record_id)
                for record_id in record_ids
            )
            if record is not None
        ]
        if not records:
            self.incoming_attacks_tab.update_history(
                self.incoming_attack_repo.get_recent_attacks()
            )
            return
        question = (
            "Czy na pewno usunąć wybrany rekord z historii?"
            if len(records) == 1
            else f"Czy na pewno usunąć {len(records)} zaznaczone rekordy z historii?"
        )
        confirmation = QMessageBox.question(
            self,
            "Usuń rekordy ataków" if len(records) > 1 else "Usuń rekord ataku",
            question,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return
        try:
            for record in records:
                self.incoming_attack_repo.delete_attack(record["id"])
                if record["movement_key"] in self._incoming_attacks_by_key:
                    self._dismissed_incoming_attack_ids.add(
                        record["movement_key"]
                    )
            self.incoming_attacks_tab.update_history(
                self.incoming_attack_repo.get_recent_attacks()
            )
            current_attacks = [
                attack
                for key, attack in self._incoming_attacks_by_key.items()
                if key not in self._dismissed_incoming_attack_ids
            ]
            self.incoming_attacks_tab.update_attacks(
                current_attacks,
                datetime.now(),
            )
        except Exception as error:
            logger.exception("Could not delete incoming attack history records")
            QMessageBox.warning(
                self,
                "Błąd historii ataków",
                f"Nie udało się usunąć wybranych rekordów: {error}",
            )

    def _on_village_names_update(self, village_names: dict):
        if self.incoming_attack_repo is None:
            return
        try:
            resolved_count = self.incoming_attack_repo.resolve_source_village_names(
                village_names,
                datetime.now(),
            )
            if resolved_count:
                self.incoming_attacks_tab.update_history(
                    self.incoming_attack_repo.get_recent_attacks()
                )
                self.incoming_attacks_tab.update_attacks(
                    [
                        attack
                        for key, attack in self._incoming_attacks_by_key.items()
                        if key not in self._dismissed_incoming_attack_ids
                    ],
                    datetime.now(),
                )
        except Exception as error:
            logger.exception("Could not update incoming attack village names")
            QMessageBox.warning(
                self,
                "Błąd mapowania wioski",
                f"Nie udało się zaktualizować nazw wiosek w historii: {error}",
            )

    def _update_recruitment_queues(
        self,
        rows: list[dict],
        pending_keys: set[str] | None = None,
    ):
        self.dashboard_tab.update_recruitment_queues(rows)
        self.recruitment_tab.update_recruitment_queues(rows, pending_keys)
        self._render_recruitment_economics(rows)

    def _render_recruitment_economics(self, queue_rows: list[dict]):
        cost_rates = estimate_recruitment_costs_per_hour(
            queue_rows,
            self.settings.recruitment.villages,
            self.settings.recruitment.automatic_enabled,
        )
        # The building planner keeps these resources for recruitment.
        self._recruitment_cost_rates = cost_rates
        state = self.dashboard_tab._live_state
        villages = (
            state.get("villages", [])
            if isinstance(state, dict)
            else [
                {"id": village.village_id, "name": village.name}
                for village in self.tracker.get_all_villages()
            ]
        )
        resource_labels = {
            "wood": "Drewno",
            "clay": "Glina",
            "iron": "Żelazo",
            "grain": "Zboże",
        }
        balance_rows = []
        for village in villages:
            village_id = str(
                village.get("id") or village.get("village_id") or ""
            )
            tracked_village = self.tracker.get_village(village_id)
            live_production = self._village_production.get(village_id, {})
            for resource, label in resource_labels.items():
                resource_point = (
                    getattr(tracked_village, resource)
                    if tracked_village is not None
                    else None
                )
                production_rate = live_production.get(resource)
                if production_rate is None:
                    production_rate = (
                        resource_point.production_rate
                        if resource_point is not None
                        and resource_point.last_updated is not None
                        else None
                    )
                recruitment_cost = cost_rates.get(village_id, {}).get(
                    resource,
                    0.0,
                )
                net_rate = (
                    production_rate - recruitment_cost
                    if production_rate is not None
                    and recruitment_cost is not None
                    else None
                )
                balance_rows.append({
                    "village_id": village_id,
                    "village": str(
                        village.get("name") or f"Wioska {village_id}"
                    ),
                    "resource": label,
                    "production_rate": production_rate,
                    "recruitment_cost": recruitment_cost,
                    "net_rate": net_rate,
                    "last_updated": (
                        datetime.now() if resource in live_production
                        else resource_point.last_updated
                        if resource_point is not None
                        else None
                    ),
                })
        self.recruitment_tab.update_recruitment_economics(balance_rows)

    def _save_recruitment_settings(self) -> bool:
        try:
            self.settings.save()
            return True
        except Exception as error:
            logger.exception("Could not save automation settings")
            QMessageBox.warning(
                self,
                "Nie udało się zapisać ustawień automatyzacji",
                str(error),
            )
            return False

    def _set_automatic_recruitment_enabled(self, enabled: bool):
        if enabled and self.settings.build.enabled:
            # Recruitment automation takes over: stop the building automation.
            self.build_tab.enabled_toggle.setChecked(False)
            self.recruitment_tab.recruitment_queue_status.setText(
                "Automat budowy wyłączony — działa automat rekrutacji."
            )
        previous = self.settings.recruitment.automatic_enabled
        self.settings.recruitment.automatic_enabled = enabled
        if not self._save_recruitment_settings():
            self.settings.recruitment.automatic_enabled = previous
            checkbox = self.recruitment_tab.auto_recruitment_checkbox
            checkbox.blockSignals(True)
            checkbox.setChecked(previous)
            checkbox.blockSignals(False)
            return

        if enabled and self.browser_session is not None:
            self._check_recruitment_queues()
        elif not enabled:
            self._auto_recruitment_candidates.clear()
            self._auto_recruitment_active = False
            self._pending_recruitment_responses.clear()
        self._render_recruitment_economics(
            self.dashboard_tab._recruitment_queue_rows
        )

    def _set_village_recruitment_rule(
        self,
        village_id: str,
        field: str,
        value,
        recheck: bool,
    ):
        village_settings = self.settings.recruitment.villages.setdefault(
            village_id,
            VillageRecruitmentSettings(),
        )
        previous = getattr(village_settings, field)
        setattr(village_settings, field, value)
        if not self._save_recruitment_settings():
            setattr(village_settings, field, previous)
            self.recruitment_tab._sync_controls_with_settings()
            return
        if (
            recheck
            and self.settings.recruitment.automatic_enabled
            and self.browser_session is not None
        ):
            self._check_recruitment_queues()

    def _set_village_recruitment_strategy(self, village_id: str, strategy: str):
        self._set_village_recruitment_rule(
            village_id, "queue_strategy", strategy, recheck=True,
        )

    def _set_village_recruitment_batch_size(self, village_id: str, batch_size: int):
        self._set_village_recruitment_rule(
            village_id, "batch_size", batch_size, recheck=False,
        )

    def _set_village_recruitment_threshold(self, village_id: str, threshold: int):
        self._set_village_recruitment_rule(
            village_id, "queue_threshold", threshold, recheck=True,
        )

    def _copy_village_recruitment_rule(self, source_village_id: str):
        recruitment = self.settings.recruitment
        rule = {
            "queue_strategy": recruitment.strategy_for(source_village_id),
            "batch_size": recruitment.batch_size_for(source_village_id),
            "queue_threshold": recruitment.queue_threshold_for(source_village_id),
        }
        target_ids = [
            village_id
            for village_id in self.recruitment_tab.village_rule_controls
            if village_id != source_village_id
        ]
        previous = {
            village_id: recruitment.villages.get(village_id)
            for village_id in target_ids
        }
        for village_id in target_ids:
            village_settings = recruitment.villages.setdefault(
                village_id,
                VillageRecruitmentSettings(),
            )
            recruitment.villages[village_id] = village_settings.model_copy(
                update=rule
            )
        if not self._save_recruitment_settings():
            for village_id, village_settings in previous.items():
                if village_settings is None:
                    recruitment.villages.pop(village_id, None)
                else:
                    recruitment.villages[village_id] = village_settings
            return
        self.recruitment_tab._sync_controls_with_settings()
        self.recruitment_tab.recruitment_queue_status.setText(
            f"Skopiowano ustawienia do {len(target_ids)} wiosek."
        )
        if recruitment.automatic_enabled and self.browser_session is not None:
            self._check_recruitment_queues()

    def _set_village_queue_minutes(
        self,
        village_id: str,
        unit_id: str,
        minutes: int,
    ):
        village_settings = self.settings.recruitment.villages.setdefault(
            village_id,
            VillageRecruitmentSettings(),
        )
        previous = village_settings.queue_minutes_by_unit.get(unit_id)
        village_settings.queue_minutes_by_unit[unit_id] = minutes
        if not self._save_recruitment_settings():
            if previous is None:
                village_settings.queue_minutes_by_unit.pop(unit_id, None)
            else:
                village_settings.queue_minutes_by_unit[unit_id] = previous
            spin_box = self.recruitment_tab.village_queue_duration_controls[
                village_id
            ][unit_id]
            spin_box.blockSignals(True)
            spin_box.setValue(previous or DEFAULT_RECRUITMENT_QUEUE_MINUTES)
            spin_box.blockSignals(False)
            return
        self._update_recruitment_queues(
            self.dashboard_tab._recruitment_queue_rows,
            self.settings.recruitment.pending_orders,
        )

    def _set_milling_gold_reserve(self, minimum_gold_balance: int):
        previous = self.settings.resource_milling.minimum_gold_balance
        self.settings.resource_milling.minimum_gold_balance = minimum_gold_balance
        if self._save_recruitment_settings():
            return

        self.settings.resource_milling.minimum_gold_balance = previous
        spin_box = self.milling_tab.minimum_gold_balance
        spin_box.blockSignals(True)
        spin_box.setValue(previous)
        spin_box.blockSignals(False)

    def _render_resource_milling_villages(self, villages: list[dict]):
        self.milling_tab.update_villages(
            villages,
            self.settings.resource_milling.villages,
        )
        for village_id, controls in self.milling_tab.village_controls.items():
            controls_identity = id(controls)
            if controls_identity in self._connected_milling_controls:
                continue
            self._connected_milling_controls.add(controls_identity)
            controls["enabled"].toggled.connect(
                lambda enabled, current_id=village_id:
                    self._set_village_milling_enabled(current_id, enabled)
            )
            controls["trigger_percent"].valueChanged.connect(
                lambda value, current_id=village_id:
                    self._set_village_milling_value(
                        current_id,
                        "trigger_percent",
                        value,
                    )
            )
            controls["refill_enabled"].toggled.connect(
                lambda enabled, current_id=village_id:
                    self._set_village_refill_enabled(current_id, enabled)
            )
            controls["refill_trigger_percent"].valueChanged.connect(
                lambda value, current_id=village_id:
                    self._set_village_milling_value(
                        current_id,
                        "refill_trigger_percent",
                        value,
                    )
            )
            for field in ("clay_percent", "iron_percent", "wood_percent"):
                controls[field].valueChanged.connect(
                    lambda value, current_id=village_id, current_field=field:
                        self._set_village_milling_value(
                            current_id,
                            current_field,
                            value,
                        )
                )

    def _set_village_milling_enabled(
        self,
        village_id: str,
        enabled: bool,
    ):
        village_settings = self.settings.resource_milling.villages[village_id]
        previous = village_settings.enabled
        village_settings.enabled = enabled
        if not self._save_recruitment_settings():
            village_settings.enabled = previous
            checkbox = self.milling_tab.village_controls[village_id]["enabled"]
            checkbox.blockSignals(True)
            checkbox.setChecked(previous)
            checkbox.blockSignals(False)
            return

        if enabled and self.browser_session is not None:
            self._check_resource_milling()
        elif not enabled:
            self._milling_candidates = [
                row for row in self._milling_candidates
                if row["village_id"] != village_id
            ]

    def _set_village_refill_enabled(
        self,
        village_id: str,
        enabled: bool,
    ):
        village_settings = self.settings.resource_milling.villages[village_id]
        previous = village_settings.refill_enabled
        village_settings.refill_enabled = enabled
        if not self._save_recruitment_settings():
            village_settings.refill_enabled = previous
            checkbox = self.milling_tab.village_controls[village_id][
                "refill_enabled"
            ]
            checkbox.blockSignals(True)
            checkbox.setChecked(previous)
            checkbox.blockSignals(False)
            return

        if enabled and self.browser_session is not None:
            self._check_resource_milling()
        elif not enabled:
            self._milling_candidates = [
                row for row in self._milling_candidates
                if not (
                    row["village_id"] == village_id
                    and row["mode"] == "refill"
                )
            ]

    def _set_village_milling_value(
        self,
        village_id: str,
        field: str,
        value: int,
    ):
        village_settings = self.settings.resource_milling.villages[village_id]
        previous = getattr(village_settings, field)
        setattr(village_settings, field, value)
        if not self._save_recruitment_settings():
            setattr(village_settings, field, previous)
            control = self.milling_tab.village_controls[village_id][field]
            control.blockSignals(True)
            control.setValue(previous)
            control.blockSignals(False)
        self.milling_tab.update_ratio_total(village_id)

    def _clear_pending_milling_orders(self):
        pending = self.settings.resource_milling.pending_villages
        if not pending:
            return
        answer = QMessageBox.question(
            self,
            "Wyczyścić oczekujące wymiany?",
            "Niepotwierdzone wymiany zostaną odblokowane. Najpierw sprawdź "
            "stan surowców w grze: jeśli wymiana została wykonana, następna "
            "może ponownie pobrać 5 złota.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        previous = set(pending)
        pending.clear()
        previous_refill = set(
            self.settings.resource_milling.pending_refill_villages
        )
        self.settings.resource_milling.pending_refill_villages.clear()
        if not self._save_recruitment_settings():
            self.settings.resource_milling.pending_villages = previous
            self.settings.resource_milling.pending_refill_villages = (
                previous_refill
            )
            return
        self._pending_milling_responses.clear()
        self.milling_tab.clear_pending_btn.setEnabled(False)
        self.milling_tab.status.setText(
            "Wyczyszczono blokady wymian. Sprawdź zapasy przed kolejną kontrolą."
        )

    def _render_village_recruitment_settings(self, villages: list[dict]):
        self.recruitment_tab.update_village_recruitment_settings(
            villages,
            self.settings.recruitment.villages,
        )

    def _set_village_recruitment_enabled(
        self,
        village_id: str,
        enabled: bool,
    ):
        village_settings = self.settings.recruitment.villages.setdefault(
            village_id,
            VillageRecruitmentSettings(),
        )
        previous = village_settings.enabled
        village_settings.enabled = enabled
        if not self._save_recruitment_settings():
            village_settings.enabled = previous
            checkbox = self.recruitment_tab.village_recruitment_controls[
                village_id
            ]["enabled"]
            checkbox.blockSignals(True)
            checkbox.setChecked(previous)
            checkbox.blockSignals(False)
            return

        if enabled and self.settings.recruitment.automatic_enabled:
            self._check_recruitment_queues()
        elif not enabled:
            self._auto_recruitment_candidates = [
                row for row in self._auto_recruitment_candidates
                if row["village_id"] != village_id
            ]
        self._render_recruitment_economics(
            self.dashboard_tab._recruitment_queue_rows
        )

    def _set_village_recruitment_unit(
        self,
        village_id: str,
        unit: str,
        enabled: bool,
    ):
        village_settings = self.settings.recruitment.villages.setdefault(
            village_id,
            VillageRecruitmentSettings(),
        )
        previous = set(village_settings.units)
        if enabled:
            village_settings.units.add(unit)
        else:
            village_settings.units.discard(unit)
        if not self._save_recruitment_settings():
            village_settings.units = previous
            checkbox = self.recruitment_tab.village_recruitment_controls[
                village_id
            ][unit]
            checkbox.blockSignals(True)
            checkbox.setChecked(unit in previous)
            checkbox.blockSignals(False)
            return

        if enabled and self.settings.recruitment.automatic_enabled:
            self._check_recruitment_queues()
        elif not enabled:
            self._auto_recruitment_candidates = [
                row for row in self._auto_recruitment_candidates
                if not (
                    row["village_id"] == village_id
                    and str(
                        row.get("unit_id", row.get("unit_type", row["unit"]))
                    ) == unit
                )
            ]
        self._render_recruitment_economics(
            self.dashboard_tab._recruitment_queue_rows
        )

    def _is_village_recruitment_enabled(self, row: dict) -> bool:
        village_settings = self.settings.recruitment.villages.get(
            row["village_id"],
            VillageRecruitmentSettings(),
        )
        return (
            village_settings.enabled
            and str(
                row.get("unit_id", row.get("unit_type", row["unit"]))
            ) in village_settings.units
            and row.get("building_available", True) is not False
        )

    def _recruitment_target_minutes(self, row: dict) -> int:
        village_settings = self.settings.recruitment.villages.get(
            row["village_id"],
            VillageRecruitmentSettings(),
        )
        return village_settings.queue_minutes_by_unit.get(
            str(row.get("unit_id", row.get("unit_type", row["unit"]))),
            DEFAULT_RECRUITMENT_QUEUE_MINUTES,
        )

    def _recruitment_queue_threshold_for_row(self, row: dict) -> int | None:
        """Return the queue count at or below which the row is replenished.

        Returns ``None`` in duration mode when the game provided no usable
        training time for the unit.
        """
        recruitment = self.settings.recruitment
        if recruitment.strategy_for(row["village_id"]) != "duration":
            return recruitment.queue_threshold_for(row["village_id"])
        return recruitment_queue_threshold_for_duration(
            row,
            self._recruitment_target_minutes(row),
        )

    def _recruitment_queue_is_low(self, row: dict) -> bool | None:
        """Return whether the row needs replenishing, or None if unknown."""
        recruitment = self.settings.recruitment
        if recruitment.strategy_for(row["village_id"]) != "duration":
            return row["count"] <= recruitment.queue_threshold_for(
                row["village_id"]
            )
        duration = recruitment_queue_duration_seconds(row)
        threshold = self._recruitment_queue_threshold_for_row(row)
        if duration is None or threshold is None:
            return None
        return duration < self._recruitment_target_minutes(row) * 60

    def _recruitment_batch_size_for_row(self, row: dict) -> int | None:
        """Return the number of units to order for a recruitment row."""
        recruitment = self.settings.recruitment
        batch_size = recruitment.batch_size_for(row["village_id"])
        if recruitment.strategy_for(row["village_id"]) != "duration":
            return batch_size
        # Duration mode orders what the queue lacks; the page trims the
        # order to what the village can currently afford.
        return recruitment_batch_for_duration(
            row,
            self._recruitment_target_minutes(row),
            _DURATION_MODE_MAX_BATCH,
        )

    def _clear_pending_recruitment_orders(self):
        if not self.settings.recruitment.pending_orders:
            return
        answer = QMessageBox.question(
            self,
            "Wyczyścić oczekujące zlecenia?",
            "Automat zostanie wyłączony, a blokady oczekujących zleceń "
            "wyczyszczone. Najpierw sprawdź kolejki w grze: jeśli zlecenie "
            "zostało przyjęte, ponowne włączenie automatu może dodać "
            "następną partię.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        previous_enabled = self.settings.recruitment.automatic_enabled
        previous_pending = set(self.settings.recruitment.pending_orders)
        checkbox = self.recruitment_tab.auto_recruitment_checkbox
        checkbox.blockSignals(True)
        checkbox.setChecked(False)
        checkbox.blockSignals(False)
        self.settings.recruitment.automatic_enabled = False
        self.settings.recruitment.pending_orders.clear()
        if not self._save_recruitment_settings():
            self.settings.recruitment.automatic_enabled = previous_enabled
            self.settings.recruitment.pending_orders = previous_pending
            checkbox.blockSignals(True)
            checkbox.setChecked(previous_enabled)
            checkbox.blockSignals(False)
            return

        self._auto_recruitment_candidates.clear()
        self._auto_recruitment_active = False
        self._update_recruitment_queues(
            self.dashboard_tab._recruitment_queue_rows,
            set(),
        )

    def _expire_stale_recruitment_locks(self):
        """Release order locks that were never confirmed.

        A lock prevents a duplicate order while the game's answer is pending.
        Without an answer for a few minutes (for example after a restart the
        answer can never arrive) the fresh queue read is the better judge.
        """
        pending = self.settings.recruitment.pending_orders
        now = monotonic()
        stale = {
            key for key in pending
            if key not in self._pending_recruitment_responses
            and now - self._pending_order_since.setdefault(key, now)
            >= _RECRUITMENT_LOCK_EXPIRY_SECONDS
        }
        if not stale:
            return
        pending.difference_update(stale)
        for key in stale:
            self._pending_order_since.pop(key, None)
        if not self._save_recruitment_settings():
            pending.update(stale)
            return
        logger.info("Released stale recruitment locks: %s", sorted(stale))

    def _begin_automatic_recruitment(self, rows: list[dict]):
        if (
            not self.settings.recruitment.automatic_enabled
            or self._auto_recruitment_active
        ):
            return

        self._expire_stale_recruitment_locks()
        candidates = []
        missing_queue_times = []
        for row in rows:
            if (
                not self._is_village_recruitment_enabled(row)
                or _recruitment_row_key(row)
                in self.settings.recruitment.pending_orders
            ):
                continue
            queue_is_low = self._recruitment_queue_is_low(row)
            if queue_is_low is None:
                missing_queue_times.append(
                    f"{row['unit']} ({row.get('village', row['village_id'])})"
                )
            elif queue_is_low:
                candidates.append(row)
        if missing_queue_times:
            self.recruitment_tab.recruitment_queue_status.setText(
                "Pominięto — brak danych o czasie szkolenia: "
                + ", ".join(missing_queue_times)
                + "."
            )
        if not candidates:
            return

        action_limit = max(
            0,
            min(
                _AUTO_RECRUITMENT_MAX_ORDERS_PER_SCAN,
                self.settings.automation.max_actions_per_minute,
            ),
        )
        if action_limit == 0:
            self.recruitment_tab.recruitment_queue_status.setText(
                "Automatyczna rekrutacja wstrzymana: limit akcji wynosi 0."
            )
            return

        self._auto_recruitment_candidates = candidates[:action_limit]
        self._auto_recruitment_active = True
        QTimer.singleShot(0, self._run_next_automatic_recruitment)

    def _run_next_automatic_recruitment(self):
        if (
            not self.settings.recruitment.automatic_enabled
            or self.browser_session is None
        ):
            self._auto_recruitment_candidates.clear()
            self._auto_recruitment_active = False
            return
        if not self._auto_recruitment_candidates:
            self._auto_recruitment_active = False
            return

        row = self._auto_recruitment_candidates.pop(0)
        key = _recruitment_row_key(row)
        if (
            key in self.settings.recruitment.pending_orders
            or not self._is_village_recruitment_enabled(row)
        ):
            QTimer.singleShot(0, self._run_next_automatic_recruitment)
            return

        batch_size = self._recruitment_batch_size_for_row(row)
        queue_threshold = self._recruitment_queue_threshold_for_row(row)
        if batch_size is None or queue_threshold is None:
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Pominięto {row['unit']} w wiosce {row['village']}: "
                "brak danych o czasie szkolenia."
            )
            QTimer.singleShot(0, self._run_next_automatic_recruitment)
            return
        # The live recheck in the game only guards against the queue having
        # grown since this scan; the decision itself was made above.
        queue_threshold = max(queue_threshold, row["count"])

        self.settings.recruitment.pending_orders.add(key)
        self._pending_order_since[key] = monotonic()
        if not self._save_recruitment_settings():
            self.settings.recruitment.pending_orders.discard(key)
            self._auto_recruitment_candidates.clear()
            self._auto_recruitment_active = False
            return
        self._update_recruitment_queues(
            self.dashboard_tab._recruitment_queue_rows,
            self.settings.recruitment.pending_orders,
        )

        try:
            duration_mode = (
                self.settings.recruitment.strategy_for(row["village_id"])
                == "duration"
            )
            # Share the village's stock between the queues that still need
            # replenishing in this scan, so one unit does not take it all.
            same_village = 1 + sum(
                1 for other in self._auto_recruitment_candidates
                if other["village_id"] == row["village_id"]
            )
            min_quantity = 1 if duration_mode else batch_size
            executor = self._click_executor()
            building_types = row.get("building_types") or [row["building_type"]]

            def run():
                # Clicked like a player: barracks window, unit, amount, Train.
                try:
                    outcome = executor.recruit(
                        row["village_id"], building_types,
                        int(row["unit_type"]), batch_size, min_quantity,
                        queue_threshold, 1.0 / same_village,
                    )
                    self.recruitment_click_done.emit(key, row, outcome)
                except Exception as click_error:
                    logger.exception("Clicked recruitment failed")
                    self.recruitment_click_done.emit(key, row, click_error)

            threading.Thread(target=run, daemon=True, name="recruit-click").start()
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Zlecam w grze (klikając): {row['unit']} w wiosce {row['village']}…"
            )
            return
        except Exception as error:
            logger.exception("Automatic recruitment outcome is uncertain")
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Nie udało się potwierdzić zlecenia dla {row['village']} "
                f"({row['unit']}): {error}. Zlecenie nie będzie ponawiane "
                "automatycznie; sprawdź kolejkę w grze."
            )
            self._auto_recruitment_candidates.clear()
            self._auto_recruitment_active = False
            return

        status = result["status"]
        if status == "submitted":
            request_id = result["request_id"]
            # (verification attempts, minimum queue count proving acceptance)
            self._pending_recruitment_responses[key] = (0, row["count"] + 1)
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Wysłano zlecenie: {int(result.get('quantity') or batch_size)} "
                f"× {row['unit']} w wiosce {row['village']}. Sprawdzam "
                "odpowiedź serwera…"
            )
            QTimer.singleShot(
                1500,
                lambda: self._verify_automatic_recruitment_response(
                    key,
                    request_id,
                    row,
                ),
            )
            QTimer.singleShot(
                _AUTO_RECRUITMENT_ORDER_INTERVAL_MS,
                self._run_next_automatic_recruitment,
            )
            return

        self.settings.recruitment.pending_orders.discard(key)
        self._save_recruitment_settings()
        self._update_recruitment_queues(
            self.dashboard_tab._recruitment_queue_rows,
            self.settings.recruitment.pending_orders,
        )
        if status == "request_busy":
            self.recruitment_tab.recruitment_queue_status.setText(
                "Gra przetwarza już inne zlecenie. Automatyczne uzupełnianie "
                "zatrzymano; kolejki zostaną ponownie sprawdzone przy "
                "następnym odczycie."
            )
            self._auto_recruitment_candidates.clear()
            self._auto_recruitment_active = False
            return

        messages = {
            "queue_not_low": "kolejka wzrosła przed wysłaniem",
            "building_missing": "brak odpowiednich koszar lub stajni",
            "insufficient_resources": (
                "za mało surowców nawet na jedną jednostkę"
                if self.settings.recruitment.strategy_for(row["village_id"])
                == "duration"
                else "za mało surowców na całą partię"
            ),
        }
        self.recruitment_tab.recruitment_queue_status.setText(
            f"Nie dodano {row['unit']} w wiosce {row['village']}: "
            f"{messages.get(status, 'warunki gry nie pozwalają na rekrutację')}."
        )
        QTimer.singleShot(0, self._run_next_automatic_recruitment)

    def _click_executor(self):
        """The background-tab clicker shared with the building automation."""
        automation = self.build_automation
        if automation is not None:
            return automation._executor
        if getattr(self, "_recruit_executor", None) is None:
            from src.core.build_clicker import BuildClickExecutor

            self._recruit_executor = BuildClickExecutor(self.browser_session)
        return self._recruit_executor

    def _on_recruitment_click_done(self, key: str, row: dict, outcome):
        """Result of a clicked recruitment order (main thread)."""
        from src.core.build_clicker import GoldGuardError

        self.settings.recruitment.pending_orders.discard(key)
        self._pending_order_since.pop(key, None)
        self._save_recruitment_settings()
        if isinstance(outcome, Exception):
            if isinstance(outcome, GoldGuardError):
                self.settings.recruitment.automatic_enabled = False
                self._save_recruitment_settings()
                self.recruitment_tab.auto_recruitment_checkbox.setChecked(False)
                QMessageBox.critical(self, "Rekrutacja zatrzymana", str(outcome))
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Nie zlecono {row['unit']} w wiosce {row['village']}: {outcome}"
            )
            self._auto_recruitment_candidates.clear()
            self._auto_recruitment_active = False
            return
        status = outcome.get("status")
        if status == "confirmed":
            rows = [
                {**queue, "count": outcome["count"]}
                if queue["village_id"] == row["village_id"]
                and str(queue.get("unit_id", queue.get("unit_type", queue["unit"])))
                == str(row.get("unit_id", row.get("unit_type", row["unit"])))
                else queue
                for queue in self.dashboard_tab._recruitment_queue_rows
            ]
            self._update_recruitment_queues(rows, self.settings.recruitment.pending_orders)
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Zlecono (kliknięciem): {outcome.get('quantity')} × {row['unit']} "
                f"w wiosce {row['village']}; w kolejce {outcome['count']}."
            )
            logger.info("Recruitment clicked: %s x %s in %s", outcome.get("quantity"), row["unit"], row["village"])
        else:
            messages = {
                "queue_not_low": "kolejka wzrosła przed zleceniem",
                "building_missing": "brak odpowiednich koszar lub stajni",
                "insufficient_resources": "za mało surowców",
            }
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Nie dodano {row['unit']} w wiosce {row['village']}: "
                f"{messages.get(status, status)}."
            )
        QTimer.singleShot(_AUTO_RECRUITMENT_ORDER_INTERVAL_MS, self._run_next_automatic_recruitment)

    def _verify_automatic_recruitment_response(
        self,
        key: str,
        request_id: str,
        row: dict,
    ):
        if self.browser_session is None or key not in self._pending_recruitment_responses:
            return

        attempts, minimum_count = self._pending_recruitment_responses[key]
        try:
            result = self.browser_session.read_recruitment_order_response(
                request_id,
                max(0, minimum_count - 1),
            )
        except Exception as error:
            logger.exception("Could not verify automatic recruitment response")
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Nie udało się potwierdzić zlecenia dla {row['unit']} "
                f"w wiosce {row['village']}: {error}. Zlecenie pozostaje "
                "zablokowane, aby uniknąć duplikatu; sprawdź kolejkę w grze."
            )
            self._pending_recruitment_responses.pop(key, None)
            return

        if result["status"] == "pending":
            attempts += 1
            if attempts < 3:
                self._pending_recruitment_responses[key] = (
                    attempts,
                    minimum_count,
                )
                QTimer.singleShot(
                    1500,
                    lambda: self._verify_automatic_recruitment_response(
                        key,
                        request_id,
                        row,
                    ),
                )
                return

            self._pending_recruitment_responses.pop(key, None)
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Gra nie zwróciła odpowiedzi na zlecenie {row['unit']} "
                f"w wiosce {row['village']}. Nie ponawiam go automatycznie; "
                "sprawdź kolejkę w grze."
            )
            return

        self._pending_recruitment_responses.pop(key, None)
        if result["status"] == "confirmed":
            self.settings.recruitment.pending_orders.discard(key)
            if not self._save_recruitment_settings():
                self.settings.recruitment.pending_orders.add(key)
                return

            rows = [
                {
                    **queue,
                    "count": result["count"],
                }
                if queue["village_id"] == row["village_id"]
                and str(
                    queue.get(
                        "unit_id",
                        queue.get("unit_type", queue["unit"]),
                    )
                ) == str(
                    row.get("unit_id", row.get("unit_type", row["unit"]))
                )
                else queue
                for queue in self.dashboard_tab._recruitment_queue_rows
            ]
            self._update_recruitment_queues(
                rows,
                self.settings.recruitment.pending_orders,
            )
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Potwierdzono: {result['count']} × {row['unit']} "
                f"w wiosce {row['village']}."
            )
            return

        self.recruitment_tab.recruitment_queue_status.setText(
            f"Serwer odpowiedział, ale kolejka {row['unit']} w wiosce "
            f"{row['village']} nie potwierdziła partii "
            f"(stan: {result.get('count') if result.get('count') is not None else 'brak danych'}). "
            "Zlecenie pozostaje zablokowane, aby uniknąć duplikatu. "
            "Sprawdź kolejkę w grze."
        )

    # Attack plan automation -------------------------------------------------
    def _game_server_name(self) -> str:
        if self.settings.attacks.server_name:
            return self.settings.attacks.server_name
        tab = getattr(self.browser_session, "_tab", None)
        url = (getattr(tab, "_kwargs", {}) or {}).get("url") or ""
        host = url.split("//", 1)[-1].split("/", 1)[0]
        return host.split(".", 1)[0] if host.endswith("kingdoms.com") else ""

    def _start_attack_automation(self):
        if self.browser_session is None:
            return
        server_name = self._game_server_name()
        if server_name and not self.settings.attacks.server_name:
            self.settings.attacks.server_name = server_name
            self._save_recruitment_settings()
            self.attack_tab.load_settings(self.settings.attacks)
        bridge = GameAttackBridge(self.browser_session)
        tktools = TkToolsSession(self.browser_session._cdp_url)
        self.attack_automation = AttackAutomation(
            bridge,
            tktools,
            lambda: self.settings.attacks,
            self.attack_log_message.emit,
            click_executor=ClickAttackExecutor(self.browser_session),
        )
        villages = (self.dashboard_tab._live_state or {}).get("villages", [])
        self.attack_tab.set_villages(villages, self.settings.attacks.hero_parking_village_id)
        self.attack_timer.start()
        self.attack_tab.set_state("Połączono z grą", "active")

    # Scouting ------------------------------------------------------------------
    def _start_scouting(self):
        if self.browser_session is None:
            return
        self.scout_automation = ScoutAutomation(
            GameAttackBridge(self.browser_session),
            lambda: self.settings.scout,
            self.scout_log_message.emit,
            on_found=self.scout_found.emit,
        )
        self.scout_timer.start()
        self.scout_tab.set_state("Połączono z grą", "active")
        QTimer.singleShot(6000, self._refresh_scout_context)

    def _refresh_scout_context(self):
        automation = self.scout_automation
        if automation is None:
            return

        def run():
            try:
                context = automation.refresh_context(force=True)
            except Exception as error:
                self.scout_log_message.emit(f"Nie odczytano wiosek: {error}")
                return
            tribe = int(context.get("tribe") or 0)
            scout_unit = SCOUT_UNITS.get(tribe, 4)
            villages = [
                {
                    "id": village_id, "name": village.get("name") or village_id,
                    "x": int(village.get("x") or 0), "y": int(village.get("y") or 0),
                    "scouts": int((village.get("units") or {}).get(str(scout_unit), 0)),
                }
                for village_id, village in (context.get("villages") or {}).items()
            ]
            self.scout_context_ready.emit(
                villages, {t: self._attacker_unit_speeds(t) for t in (1, 2, 3)},
            )
            bridge = GameAttackBridge(self.browser_session) if self.browser_session else None
            seen = {}
            for attacker in list(self.settings.scout.attackers):
                if bridge is None or not attacker.player_id:
                    continue
                try:
                    if attacker.player_id not in seen:
                        seen[attacker.player_id] = read_hero_xp(bridge, attacker.player_id)["xp"]
                    self.manual_attacker_xp.emit(f"manual:{attacker.id}", seen[attacker.player_id])
                except Exception as error:
                    logger.warning("Could not read hero XP of %s: %s", attacker.name, error)

        threading.Thread(target=run, daemon=True, name="scout-context").start()

    def _on_scout_found(self, result: dict):
        """Make a found real target impossible to miss."""
        self.scout_tab.add_found(result)
        QApplication.beep()
        QApplication.alert(self, 0)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Zwiad — prawdziwy cel")
        box.setText(ScoutWidget.found_text(result))
        xp = result.get("xp") or {}
        box.setInformativeText(
            f"EXP bohatera: {xp.get(-3)} → {xp.get(0)} → {xp.get(2)} — po pierwszej "
            "parze zwiadu wzrósł, po drugiej już nie: bohater wyszedł z wojskiem."
        )
        box.setModal(False)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()

    def _resolve_manual_attacker(self, x: int, y: int):
        session = self.browser_session
        if session is None:
            QMessageBox.information(self, "Zwiad", "Najpierw połącz się z grą.")
            return

        def run():
            try:
                village = session.resolve_village(x, y)
            except Exception as error:
                self.scout_log_message.emit(f"Nie znaleziono atakującego: {error}")
                return
            self.manual_attacker_resolved.emit(village, x, y)
            try:
                xp = read_hero_xp(GameAttackBridge(session), village["player_id"])["xp"]
                self.manual_attacker_xp.emit(f"manual:{village['village_id']}", xp)
            except Exception as error:
                self.scout_log_message.emit(f"Nie odczytano EXP bohatera: {error}")

        threading.Thread(target=run, daemon=True, name="scout-manual").start()

    def _on_manual_attacker_resolved(self, village, x: int, y: int):
        self.scout_tab.add_manual_attacker(village, x, y)
        self.scout_tab.add_log(
            f"Dodano atakującego {village.get('player_name')} — "
            f"{village.get('village_name')} ({x}|{y})."
        )

    def _refresh_scout_attacks(self):
        attacks = []
        for key, attack in self._incoming_attacks_by_key.items():
            if key in self._dismissed_incoming_attack_ids:
                continue
            source_id = str(attack.get("source_village_id") or "")
            entry = dict(attack)
            entry["key"] = key
            entry["source_xy"] = village_position(source_id)
            entry["target_xy"] = village_position(attack.get("village_id"))
            entry["hero_xp"] = self._hero_xp_by_attack.get(key, attack.get("hero_xp"))
            attacks.append(entry)
        attacks.sort(key=lambda item: item["arrival_time"])
        self.scout_tab.update_attacks(attacks)

    def _read_attacker_hero_xp(self, key: str, player_id: str):
        session = self.browser_session
        if session is None:
            return

        def run():
            try:
                value = read_hero_xp(GameAttackBridge(session), player_id)["xp"]
            except Exception as error:
                logger.warning("Could not read attacker hero XP: %s", error)
                return
            self.hero_xp_read.emit(key, value)

        threading.Thread(target=run, daemon=True, name="hero-xp").start()

    def _on_hero_xp_read(self, key: str, value):
        self._hero_xp_by_attack[key] = int(value)
        attack = self._incoming_attacks_by_key.get(key)
        if attack is not None:
            attack["hero_xp"] = int(value)
        if self.incoming_attack_repo is not None:
            for record in self.incoming_attack_repo.get_recent_attacks():
                if record.get("movement_key") == key:
                    details = record.get("details") or {}
                    if details.get("hero_xp") is None:
                        self.incoming_attack_repo.set_attack_detail(record["id"], "hero_xp", int(value))
                        self.incoming_attack_repo.set_attack_detail(
                            record["id"], "hero_xp_at", datetime.now().isoformat(),
                        )
                    break
        self._refresh_scout_attacks()

    def _scout_tick(self):
        automation = self.scout_automation
        if automation is None:
            return
        worker = self._scout_worker
        if worker is None or not worker.is_alive():
            def run():
                try:
                    automation.tick()
                except Exception as error:
                    logger.exception("Scout tick failed")
                    self.scout_log_message.emit(f"Błąd: {error}")

            self._scout_worker = threading.Thread(target=run, daemon=True, name="scout")
            self._scout_worker.start()
        self.scout_tab.update_plan(automation.rows())
        if monotonic() - self._scout_context_at > 300:
            self._scout_context_at = monotonic()
            self._refresh_scout_context()

    def _plan_scouting(self, request: dict):
        automation = self.scout_automation
        if automation is None:
            QMessageBox.information(self, "Zwiad", "Najpierw połącz się z grą.")
            return
        attack = request["attack"]
        if not str(attack.get("attacker_player_id") or "").isdigit():
            QMessageBox.warning(self, "Zwiad", "Gra nie podała ID atakującego dla tego ataku.")
            return
        if not str(attack.get("source_village_id") or "").isdigit() or not attack.get("source_xy"):
            QMessageBox.warning(self, "Zwiad", "Brak wioski źródłowej tego ataku.")
            return
        if not request.get("scout_village_id"):
            QMessageBox.warning(self, "Zwiad", "Wybierz wioskę, która wyśle zwiad.")
            return
        tribe = int((automation.context or {}).get("tribe") or 0)
        targets = [ScoutTarget(name, x, y, arrival) for name, x, y, arrival in request["targets"]]

        def run():
            try:
                operation = automation.create_operation(
                    attack_key=attack["key"],
                    attacker_id=str(attack["attacker_player_id"]),
                    attacker_name=attack.get("attacker_name", ""),
                    source_village_id=str(attack["source_village_id"]),
                    source=tuple(attack["source_xy"]),
                    unit_label=request["unit_label"],
                    unit_speed=float(request["speed"]),
                    siege=bool(request["siege"]),
                    levels=request["levels"],
                    targets=targets,
                    scout_village_id=str(request["scout_village_id"]),
                    scout_unit=SCOUT_UNITS.get(tribe, 4),
                    scouts_per_send=int(request["scouts_per_send"]),
                )
                if not self.settings.scout.enabled:
                    self.scout_log_message.emit(
                        f"Plan #{operation.id} gotowy — włącz automat zwiadu, aby go wykonać."
                    )
            except Exception as error:
                self.scout_log_message.emit(f"Nie zaplanowano zwiadu: {error}")

        threading.Thread(target=run, daemon=True, name="scout-plan").start()

    def _cancel_scouting(self, operation_id: int):
        if self.scout_automation is not None:
            self.scout_automation.cancel(operation_id)

    def _set_scout_enabled(self, enabled: bool):
        if enabled:
            answer = QMessageBox.question(
                self,
                "Włączyć automat zwiadu?",
                "Automat będzie sam wysyłał zwiadowców według zaplanowanych "
                "operacji (co do sekundy) i odczytywał EXP bohatera atakującego.\n\n"
                "Wysłanych zwiadowców nie da się cofnąć. Włączyć?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.scout_tab.load_settings(self.settings.scout)
                return
        self.settings.scout.enabled = enabled
        if not self._save_recruitment_settings():
            self.settings.scout.enabled = not enabled
            self.scout_tab.load_settings(self.settings.scout)
            return
        self.scout_tab.add_log("Automat zwiadu włączony." if enabled else "Automat zwiadu wyłączony.")

    def _set_scout_setting(self, field: str, value):
        previous = getattr(self.settings.scout, field)
        try:
            updated = ScoutSettings.model_validate({**self.settings.scout.model_dump(), field: value})
        except ValueError as error:
            self.scout_tab.add_log(f"Nieprawidłowe ustawienie: {error}")
            return
        setattr(self.settings.scout, field, getattr(updated, field))
        if not self._save_recruitment_settings():
            setattr(self.settings.scout, field, previous)

    # Village building ----------------------------------------------------------
    def _start_building(self):
        if self.browser_session is None:
            return
        self.build_automation = BuildAutomation(
            self.browser_session,
            lambda: self.settings.build,
            self.build_log_message.emit,
            recruitment_getter=lambda: self._recruitment_cost_rates,
            threats_getter=lambda: self._build_threats,
        )
        self.build_timer.start()
        self.build_tab.set_state("Połączono z grą", "active")
        if self.settings.build.enabled:
            self._turn_off_recruitment_for_building()
        QTimer.singleShot(5500, lambda: self._run_build(click=True))

    def _stop_building(self):
        self.build_timer.stop()
        self._build_wakeup.stop()
        automation = self.build_automation
        self.build_automation = None
        if automation is not None:
            threading.Thread(target=automation.close, daemon=True, name="build-close").start()
        self.build_tab.set_state("Nie połączono z grą", "muted")

    def _wake_build_for_attacks(self, server_time: float):
        """A hostile attack lands within the protection window (or will soon
        enter it): check the villages then, not only at the next timer tick."""
        build = self.settings.build
        if self.build_automation is None or not build.enabled or not build.raid_protection:
            return
        lead = build.raid_lead_minutes * 60
        starts = [
            float(attack["arrival_time"]) - lead - server_time
            for attack in self._build_threats
            if attack.get("movement_type") != "Zwiad"
            and float(attack.get("arrival_time") or 0) > server_time
        ]
        if not starts:
            return
        wait = max(2.0, min(starts) + 5)
        if wait * 1000 < self.build_timer.remainingTime() and (
            not self._build_wakeup.isActive()
            or wait * 1000 < self._build_wakeup.remainingTime()
        ):
            self._build_wakeup.start(int(wait * 1000))

    def _run_build(self, click: bool):
        automation = self.build_automation
        if automation is None:
            self.build_tab.summary.setText("Połącz z grą, aby wyliczyć plan.")
            return
        worker = self._build_worker
        if (
            worker is not None and worker.is_alive()
            and monotonic() - self._build_worker_started > _BUILD_WORKER_STALL_SECONDS
        ):
            # A call into the browser never returned: drop that instance (it
            # will not click again) and continue with a fresh background tab.
            logger.warning("Village build worker stalled; starting a fresh automation")
            try:
                import traceback

                frame = sys._current_frames().get(worker.ident)
                if frame is not None:
                    logger.warning(
                        "Stalled worker stack:\n%s", "".join(traceback.format_stack(frame)),
                    )
            except Exception:
                pass
            self.build_tab.add_log(
                "Automat nie odpowiadał od kilku minut — uruchamiam go od nowa z nową kartą w tle."
            )
            automation.abandoned = True
            self.build_automation = automation = BuildAutomation(
                self.browser_session,
                lambda: self.settings.build,
                self.build_log_message.emit,
                recruitment_getter=lambda: self._recruitment_cost_rates,
                threats_getter=lambda: self._build_threats,
            )
            worker = None
        if worker is not None and worker.is_alive():
            if click and not self._build_wakeup.isActive():
                # Busy with a previous check: try again shortly, do not skip.
                self._build_wakeup.start(5000)
            return

        def run():
            logger.info("Village build check started (click=%s)", click)
            try:
                snapshot = automation.tick() if click else automation.refresh()
                logger.info("Village build check finished")
            except Exception as error:
                logger.exception("Village build check failed")
                self.build_log_message.emit(f"Błąd odczytu: {error}")
                return
            self.build_snapshot_ready.emit(snapshot)

        self._build_worker = threading.Thread(target=run, daemon=True, name="village-build")
        self._build_worker_started = monotonic()
        self._build_worker.start()

    def _on_build_snapshot(self, snapshot):
        self.build_tab.update_snapshot(snapshot, self.settings.build.disabled_villages)
        try:
            from src.core.browser_session import _TROOP_NAMES_BY_TYPE as unit_names
        except ModuleNotFoundError:  # pragma: no cover
            from core.browser_session import _TROOP_NAMES_BY_TYPE as unit_names
        self.build_tab.update_overflow(snapshot, self.settings.build.overflow_units, unit_names)
        self.build_tab.update_military(snapshot, self.settings.build.military_villages, unit_names)
        automation = self.build_automation
        if automation is not None and automation.budget_changed:
            automation.budget_changed = False
            self._save_recruitment_settings()
        if automation is not None and automation.halted and self.settings.build.enabled:
            # Gold went missing during an action: switch everything off.
            self.settings.build.enabled = False
            self._save_recruitment_settings()
            self.build_tab.load_settings(self.settings.build)
            self._build_wakeup.stop()
            self.build_tab.set_state("Zatrzymany — niespodziewany wydatek złota", "waiting")
            QApplication.beep()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Automat budowy zatrzymany")
            box.setText(automation.halted)
            box.setInformativeText(
                "Automat wyłączył się sam, bo saldo złota spadło bardziej, niż "
                "pozwalała wykonywana akcja. Sprawdź w grze, co się stało, zanim "
                "włączysz go ponownie."
            )
            box.setModal(False)
            box.setAttribute(Qt.WA_DeleteOnClose)
            box.show()
            return
        wait = snapshot.next_wakeup
        if (
            wait is not None
            and self.settings.build.enabled
            and wait * 1000 < self.build_timer.remainingTime()
        ):
            # A short pause after the threshold so the game shows the new price.
            self._build_wakeup.start(int(wait * 1000) + 2000)

    def _set_build_enabled(self, enabled: bool):
        if enabled:
            answer = QMessageBox.question(
                self,
                "Włączyć automat budowy?",
                "Automat co kilka minut otworzy w tle osobną kartę gry i — jak "
                "gracz — kliknie rozbudowę pola, magazynu, budynku bonusowego lub "
                "budynku głównego według planu, gdy wioska ma surowce i wolną "
                "kolejkę. Przy transportach ustawionych na Automat wyśle też "
                "surowce między wioskami przez targowisko.\n\n"
                "Jeśli handlarz NPC lub „Dokończ teraz” są ustawione na Automat, "
                "będzie też wydawał złoto (zawsze zostawiając ustawioną rezerwę). "
                "Na tym koncie gra nie pyta o potwierdzenie złota.\n\n"
                "Wydanych surowców ani złota nie da się odzyskać. Włączyć?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.build_tab.load_settings(self.settings.build)
                return
        self.settings.build.enabled = enabled
        if not self._save_recruitment_settings():
            self.settings.build.enabled = not enabled
            self.build_tab.load_settings(self.settings.build)
            return
        self.build_tab.add_log("Automat budowy włączony." if enabled else "Automat budowy wyłączony.")
        if enabled:
            self._turn_off_recruitment_for_building()
            self._run_build(click=True)

    def _set_build_setting(self, field: str, value):
        previous = getattr(self.settings.build, field)
        try:
            updated = BuildSettings.model_validate({**self.settings.build.model_dump(), field: value})
        except ValueError as error:
            self.build_tab.add_log(f"Nieprawidłowe ustawienie: {error}")
            return
        setattr(self.settings.build, field, getattr(updated, field))
        if not self._save_recruitment_settings():
            setattr(self.settings.build, field, previous)
            return
        if field.startswith("settle_"):
            self.build_tab._show_settle(self.settings.build)
        if field == "town_villages":
            self.build_tab._show_towns(self.settings.build.town_villages)
        self._run_build(click=False)

    def _set_build_overflow_unit(self, village_id: str, unit: int):
        units = self.settings.build.overflow_units
        previous = units.get(village_id)
        if unit:
            units[village_id] = int(unit)
        else:
            units.pop(village_id, None)
        if not self._save_recruitment_settings():
            if previous:
                units[village_id] = previous
            else:
                units.pop(village_id, None)
            return
        self.build_tab.add_log(
            "Nadmiar surowców: " + (f"rekrutacja jednostki {unit}" if unit else "bez rekrutacji")
            + f" w wiosce {village_id}."
        )

    def _set_build_military(self, entries: list):
        previous = list(self.settings.build.military_villages)
        self.settings.build.military_villages = list(entries)
        if not self._save_recruitment_settings():
            self.settings.build.military_villages = previous
            return
        automation = self.build_automation
        names = {
            plan.village.id: plan.village.name
            for plan in (automation.snapshot.plans if automation else [])
        }
        self.build_tab.add_log(
            "Wojsko: " + (", ".join(
                f"{i + 1}. {names.get(e['village_id'], e['village_id'])} "
                f"(do poziomu {e['target_level']})"
                for i, e in enumerate(entries)
            ) or "wyłączone") + "."
        )

    def _turn_off_recruitment_for_building(self):
        """The building automation and the recruitment tab must not compete."""
        if self.settings.recruitment.automatic_enabled:
            self.recruitment_tab.auto_recruitment_checkbox.setChecked(False)
            self.build_tab.add_log(
                "Automat rekrutacji wyłączony — przy włączonej budowie rekrutacja "
                "dostaje tylko nadmiar (sekcja „Nadmiar surowców → rekrutacja”)."
            )

    def _set_build_village(self, village_id: str, enabled: bool):
        disabled = self.settings.build.disabled_villages
        if enabled:
            disabled.discard(village_id)
        else:
            disabled.add(village_id)
        if not self._save_recruitment_settings():
            if enabled:
                disabled.add(village_id)
            else:
                disabled.discard(village_id)

    # Kingdom -------------------------------------------------------------------
    def _refresh_kingdom(self):
        session = self.browser_session
        if session is None:
            self.kingdom_tab.status.setText("Połącz z grą, aby wczytać członków królestwa.")
            return
        worker = self._kingdom_worker
        if worker is not None and worker.is_alive():
            return
        self.kingdom_tab.set_loading()

        team_id = self.settings.kingdom.tktools_team_id
        gt_group = self.settings.kingdom.gettertools_group
        server_name = self._game_server_name()
        cdp_url = session._cdp_url

        def run():
            try:
                kingdom = session.read_kingdom_members()
                kingdom["tk_updates"] = None
                kingdom["tk_error"] = None
                if team_id:
                    tktools = TkToolsSession(cdp_url)
                    try:
                        kingdom["tk_updates"] = tktools.read_team_updates(team_id)["members"]
                    except Exception as error:
                        # No TK-Tools tab, not logged in or no access to the
                        # team: every row shows "brak danych".
                        kingdom["tk_error"] = str(error)
                    finally:
                        tktools.disconnect()
                kingdom["gt_updates"] = None
                kingdom["gt_error"] = None
                if gt_group and server_name:
                    gettertools = GetterToolsSession(cdp_url)
                    try:
                        kingdom["gt_updates"] = gettertools.read_group_updates(
                            server_name, gt_group,
                        )
                    except Exception as error:
                        kingdom["gt_error"] = str(error)
                    finally:
                        gettertools.disconnect()
                self.kingdom_loaded.emit(kingdom)
            except Exception as error:
                logger.warning("Could not read kingdom members: %s", error)
                self.kingdom_loaded.emit({"error": str(error)})

        self._kingdom_worker = threading.Thread(target=run, daemon=True, name="kingdom")
        self._kingdom_worker.start()

    def _update_tktools_troops(self):
        session = self.browser_session
        if session is None:
            QMessageBox.information(self, "Aktualizuj TK", "Najpierw połącz się z grą.")
            return
        worker = self._tk_update_worker
        if worker is not None and worker.is_alive():
            return
        server_name = self._game_server_name()
        if not server_name:
            QMessageBox.warning(self, "Aktualizuj TK", "Nie rozpoznano serwera gry.")
            return
        answer = QMessageBox.question(
            self,
            "Aktualizować wojska w TK-Tools?",
            "Aplikacja zapisze w TK-Tools (strona Troops) Twoje obecne wojska "
            "z gry dla każdej wioski — w domu, w posiłkach i w drodze — oraz "
            "poziomy placu turniejowego. Poziomy kuźni i orientacje wiosek "
            "zostaną bez zmian.\n\nZaktualizować?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self.kingdom_tab.update_tk_button.setEnabled(False)
        self.kingdom_tab.status.setText("Aktualizuję wojska w TK-Tools…")
        cdp_url = session._cdp_url

        def run():
            tktools = TkToolsSession(cdp_url)
            try:
                army = session.read_own_army()
                self.tk_troops_updated.emit(tktools.update_troops(server_name, army))
            except Exception as error:
                logger.warning("TK-Tools troop update failed: %s", error)
                self.tk_troops_updated.emit({"error": str(error)})
            finally:
                tktools.disconnect()

        self._tk_update_worker = threading.Thread(target=run, daemon=True, name="tk-troops")
        self._tk_update_worker.start()

    def _on_tktools_troops_updated(self, result):
        self.kingdom_tab.update_tk_button.setEnabled(True)
        if not isinstance(result, dict) or result.get("error"):
            message = str((result or {}).get("error") or "brak odpowiedzi")
            self.kingdom_tab.status.setText(f"Nie zaktualizowano TK-Tools: {message}")
            QMessageBox.warning(self, "Aktualizuj TK", f"Nie udało się: {message}")
            return
        parts = [f"Zaktualizowano w TK-Tools: {', '.join(result.get('updated') or []) or '—'}."]
        if result.get("missing"):
            parts.append(
                "Wiosek nie ma jeszcze w TK-Tools (pominięto): "
                + ", ".join(result["missing"]) + "."
            )
        if result.get("kept"):
            parts.append(
                "Bez zmian (nie ma ich w grze): " + ", ".join(result["kept"]) + "."
            )
        QMessageBox.information(self, "Aktualizuj TK", "\n\n".join(parts))
        self._refresh_kingdom()

    def _update_gettertools_troops(self):
        session = self.browser_session
        if session is None:
            QMessageBox.information(self, "Aktualizuj GT", "Najpierw połącz się z grą.")
            return
        worker = self._gt_update_worker
        if worker is not None and worker.is_alive():
            return
        server_name = self._game_server_name()
        if not server_name:
            QMessageBox.warning(self, "Aktualizuj GT", "Nie rozpoznano serwera gry.")
            return
        answer = QMessageBox.question(
            self,
            "Aktualizować wojska w GetterTools?",
            "Aplikacja zapisze w GetterTools (My troops) Twoje obecne wojska "
            "z gry dla każdej wioski — w domu, w posiłkach i w drodze, "
            "jednostki 1–9 — oraz poziomy placu turniejowego. Typ wioski "
            "zostanie bez zmian.\n\nZaktualizować?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        self.kingdom_tab.update_gt_button.setEnabled(False)
        self.kingdom_tab.status.setText("Aktualizuję wojska w GetterTools…")
        cdp_url = session._cdp_url

        def run():
            gettertools = GetterToolsSession(cdp_url)
            try:
                army = session.read_own_army()
                self.gt_troops_updated.emit(gettertools.update_troops(server_name, army))
            except Exception as error:
                logger.warning("GetterTools troop update failed: %s", error)
                self.gt_troops_updated.emit({"error": str(error)})
            finally:
                gettertools.disconnect()

        self._gt_update_worker = threading.Thread(target=run, daemon=True, name="gt-troops")
        self._gt_update_worker.start()

    def _on_gettertools_troops_updated(self, result):
        self.kingdom_tab.update_gt_button.setEnabled(True)
        if not isinstance(result, dict) or result.get("error"):
            message = str((result or {}).get("error") or "brak odpowiedzi")
            self.kingdom_tab.status.setText(f"Nie zaktualizowano GetterTools: {message}")
            QMessageBox.warning(self, "Aktualizuj GT", f"Nie udało się: {message}")
            return
        parts = [f"Zaktualizowano w GetterTools: {', '.join(result.get('updated') or []) or '—'}."]
        if result.get("failed"):
            parts.append("Nie zapisano: " + ", ".join(result["failed"]) + ".")
        if result.get("missing"):
            parts.append(
                "Wiosek nie ma jeszcze w GetterTools (pominięto): "
                + ", ".join(result["missing"]) + "."
            )
        QMessageBox.information(self, "Aktualizuj GT", "\n\n".join(parts))
        self._refresh_kingdom()

    def _on_kingdom_loaded(self, kingdom):
        if not isinstance(kingdom, dict) or kingdom.get("error"):
            self.kingdom_tab.set_error(str((kingdom or {}).get("error") or "brak danych"))
            return
        self.kingdom_tab.update_members(kingdom, datetime.now())
        if kingdom.get("tk_error"):
            self.kingdom_tab.status.setText(
                self.kingdom_tab.status.text()
                + f" · TK-Tools: brak danych ({kingdom['tk_error']})"
            )
        if kingdom.get("gt_error"):
            self.kingdom_tab.status.setText(
                self.kingdom_tab.status.text()
                + f" · GetterTools: brak danych ({kingdom['gt_error']})"
            )

    # Robber hideout defense ----------------------------------------------------
    def _start_hideout_defense(self):
        if self.browser_session is None:
            return
        self.hideout_defense = HideoutDefense(
            GameAttackBridge(self.browser_session),
            lambda: self.settings.hideout_defense,
            self.hideout_log_message.emit,
            self.hideout_handled.emit,
            set_active=self.hideout_active.emit,
        )
        self.hideout_timer.start()
        self.hideout_tab.set_state("Połączono z grą", "active")

    def _stop_hideout_defense(self):
        self.hideout_timer.stop()
        self.hideout_defense = None
        self.hideout_tab.set_state("Nie połączono z grą", "muted")

    def _hideout_tick(self):
        defense = self.hideout_defense
        if defense is None:
            return
        worker = self._hideout_worker
        if worker is None or not worker.is_alive():
            def run():
                try:
                    defense.tick()
                except Exception as error:
                    logger.exception("Hideout defense tick failed")
                    self.hideout_log_message.emit(f"Błąd: {error}")

            self._hideout_worker = threading.Thread(
                target=run, daemon=True, name="hideout-defense",
            )
            self._hideout_worker.start()
        self._render_hideout_state()

    def _render_hideout_state(self):
        defense = self.hideout_defense
        if defense is None:
            return
        context = defense.context or {}
        capital = (context.get("villages") or {}).get(defense.capital_id or "")
        self.hideout_tab.set_capital(
            context.get("tribe"), defense.capital_name,
            None if capital is None else capital.get("units") or {},
        )
        rows = defense.rows()
        self.hideout_tab.update_rows(rows)
        if defense.last_error:
            text = f"Błąd odczytu: {defense.last_error}"
        elif defense.last_scan is None:
            text = "Pierwszy odczyt za chwilę."
        else:
            active = sum(1 for case in defense.cases if not case.finished)
            text = (
                f"Sprawdzam co {int(SCAN_INTERVAL_SECONDS)} s. "
                + (f"W toku: {active}." if active else "Brak aktywnych ataków zbójów.")
            )
            if not self.settings.hideout_defense.enabled:
                text += " Automat wyłączony — tylko podgląd."
        self.hideout_tab.set_detail("scan", text)

    def _mark_hideout_movements_handled(self, movement_ids):
        settings = self.settings.hideout_defense
        handled = [item for item in settings.handled_movements if item not in movement_ids]
        settings.handled_movements = (handled + sorted(movement_ids))[-500:]
        self._save_recruitment_settings()

    def _set_hideout_active(self, cell: str, info):
        active = dict(self.settings.hideout_defense.active_hideouts)
        if info is None:
            active.pop(cell, None)
        else:
            active[cell] = {key: str(value) for key, value in info.items()}
        self.settings.hideout_defense.active_hideouts = active
        self._save_recruitment_settings()

    def _set_hideout_enabled(self, enabled: bool):
        if enabled:
            answer = QMessageBox.question(
                self,
                "Włączyć obronę przed kryjówkami?",
                "Gdy kryjówka zbójów zaatakuje którąś z Twoich wiosek, automat "
                "sam wyśle ze stolicy kontratak (i grabież, jeśli ją włączysz) "
                "z ustawionymi wojskami.\n\nWysłanych wojsk nie da się cofnąć. "
                "Włączyć?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.hideout_tab.load_settings(self.settings.hideout_defense)
                return
        self.settings.hideout_defense.enabled = enabled
        if not self._save_recruitment_settings():
            self.settings.hideout_defense.enabled = not enabled
            self.hideout_tab.load_settings(self.settings.hideout_defense)
            return
        self.hideout_tab.add_log(
            "Obrona przed kryjówkami włączona." if enabled
            else "Obrona przed kryjówkami wyłączona."
        )

    def _set_hideout_setting(self, field: str, value):
        previous = getattr(self.settings.hideout_defense, field)
        try:
            updated = HideoutDefenseSettings.model_validate({
                **self.settings.hideout_defense.model_dump(),
                field: value,
            })
        except ValueError as error:
            self.hideout_tab.add_log(f"Nieprawidłowe ustawienie: {error}")
            self.hideout_tab.load_settings(self.settings.hideout_defense)
            return
        setattr(self.settings.hideout_defense, field, getattr(updated, field))
        if not self._save_recruitment_settings():
            setattr(self.settings.hideout_defense, field, previous)
            self.hideout_tab.load_settings(self.settings.hideout_defense)

    def _on_attack_log(self, text: str):
        self.attack_tab.add_log(text)

    def _stop_attack_automation(self):
        self.attack_timer.stop()
        if self.attack_automation is not None:
            self.attack_automation.tktools.disconnect()
            if self.attack_automation.click_executor is not None:
                self.attack_automation.click_executor.close()
        self.attack_automation = None
        self.attack_tab.set_state("Nie połączono z grą", "muted")

    def _run_attack_work(self, work, description: str) -> bool:
        """Run slow attack automation work off the GUI thread (one at a time)."""
        worker = self._attack_worker
        if worker is not None and worker.is_alive():
            return False

        def run():
            try:
                work()
            except Exception as error:
                logger.exception("Attack automation %s failed", description)
                self.attack_log_message.emit(f"Błąd ({description}): {error}")

        self._attack_worker = threading.Thread(
            target=run, daemon=True, name=f"attack-{description}",
        )
        self._attack_worker.start()
        return True

    def _attack_tick(self):
        automation = self.attack_automation
        if automation is None:
            return
        session = self.browser_session

        def work():
            if session is not None:
                session.keep_page_awake()
            automation.tick()

        self._run_attack_work(work, "tick")
        self._render_attack_state()

    def _render_attack_state(self):
        automation = self.attack_automation
        if automation is None:
            return
        settings = self.settings.attacks
        server_now = automation.server_now() if automation.context else None
        self.attack_tab.update_rows(automation.rows(), server_now)
        if automation.last_error:
            self.attack_tab.set_detail("plan", f"Błąd: {automation.last_error}")
        elif automation.last_refresh is not None:
            ago = int(monotonic() - automation.last_refresh)
            pending = sum(1 for run in automation.runs.values() if not run.finished)
            self.attack_tab.set_detail(
                "plan",
                f"Serwer {settings.server_name or '?'}: {len(automation.runs)} ataków "
                f"({pending} do wysłania). Odczyt {ago} s temu."
                + ("" if not automation.plan_problems else
                   " " + " ".join(automation.plan_problems)),
            )
        elif not settings.enabled:
            self.attack_tab.set_detail(
                "plan", "Włącz automat albo kliknij „Odśwież plan”, aby pobrać plan."
            )
        if server_now is not None:
            self.attack_tab.set_detail(
                "clock",
                f"{datetime.fromtimestamp(server_now):%H:%M:%S} "
                f"(komputer {automation.server_offset_ms / 1000:+.1f} s względem "
                f"serwera, ping {automation.rtt_ms:.0f} ms)",
            )
        self.attack_tab.set_detail(
            "hero",
            automation.hero_status_text if settings.hero_evasion else "Ukrywanie wyłączone.",
        )
        self.attack_tab.set_detail(
            "warnings", "\n".join(automation.pt_warnings) or "Brak.",
        )

    def _refresh_attack_plan(self):
        automation = self.attack_automation
        if automation is None:
            self.attack_tab.add_log("Najpierw połącz się z grą.")
            return

        def work():
            try:
                automation.refresh_plan()
                self.attack_log_message.emit(
                    f"Pobrano plan: {len(automation.runs)} ataków."
                )
            except Exception as error:
                automation.last_error = str(error)
                self.attack_log_message.emit(f"Nie udało się pobrać planu: {error}")

        if self._run_attack_work(work, "refresh"):
            self.attack_tab.set_detail("plan", "Pobieram plan z TK-Tools…")
        else:
            self.attack_tab.add_log("Automat jest zajęty — spróbuj za chwilę.")

    def _set_attacks_enabled(self, enabled: bool):
        if enabled:
            answer = QMessageBox.question(
                self,
                "Włączyć automat ataków?",
                "Automat będzie sam wysyłał wojska według planu TK-Tools "
                "(fejki, offy, katapulty, bohater) i zgłaszał wynik w TK-Tools.\n\n"
                "Wysłanych wojsk nie da się cofnąć. Upewnij się, że plan jest "
                "aktualny, a aplikacja i przeglądarka będą włączone przez całą "
                "operację.\n\nWłączyć?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.attack_tab.load_settings(self.settings.attacks)
                return
        self.settings.attacks.enabled = enabled
        if not self._save_recruitment_settings():
            self.settings.attacks.enabled = not enabled
            self.attack_tab.load_settings(self.settings.attacks)
            return
        self.attack_tab.add_log(
            "Automat ataków włączony." if enabled else "Automat ataków wyłączony."
        )

    def _set_attack_setting(self, field: str, value):
        previous = getattr(self.settings.attacks, field)
        try:
            updated = AttackSettings.model_validate({
                **self.settings.attacks.model_dump(),
                field: value,
            })
        except ValueError as error:
            self.attack_tab.add_log(f"Nieprawidłowe ustawienie: {error}")
            self.attack_tab.load_settings(self.settings.attacks)
            return
        setattr(self.settings.attacks, field, getattr(updated, field))
        if not self._save_recruitment_settings():
            setattr(self.settings.attacks, field, previous)
            self.attack_tab.load_settings(self.settings.attacks)
            return
        if field == "execution_mode":
            self.attack_tab.add_log(
                "Tryb wysyłki: "
                + ("klikanie w przeglądarce." if value == "clicks" else "polecenia gry.")
            )

    # Vacation safeguard --------------------------------------------------------
    def _set_vacation_enabled(self, enabled: bool):
        if enabled:
            answer = QMessageBox.question(
                self,
                "Włączyć automat wakacji?",
                "Automat będzie codziennie planował tryb wakacyjny i anulował "
                "go tuż przed aktywacją, jeśli nie nadchodzą ataki.\n\n"
                "Jeśli w chwili decyzji aplikacja nie będzie połączona z grą, "
                "wakacje włączą się same: zużyjesz dni wakacji, stracisz oazy, "
                "a posiłki innych graczy wrócą do nich.\n\nWłączyć?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.vacation_tab.load_settings(self.settings.vacation)
                return
        self.settings.vacation.enabled = enabled
        if not self._save_recruitment_settings():
            self.settings.vacation.enabled = not enabled
            self.vacation_tab.load_settings(self.settings.vacation)
            return
        self.vacation_tab.add_log(
            "Automat wakacji włączony." if enabled else "Automat wakacji wyłączony."
        )
        self._check_vacation()

    def _set_vacation_setting(self, field: str, value):
        previous = getattr(self.settings.vacation, field)
        try:
            updated = VacationSettings.model_validate({
                **self.settings.vacation.model_dump(),
                field: value,
            })
        except ValueError as error:
            self.vacation_tab.add_log(f"Nieprawidłowe ustawienie: {error}")
            self.vacation_tab.load_settings(self.settings.vacation)
            return
        setattr(self.settings.vacation, field, getattr(updated, field))
        if not self._save_recruitment_settings():
            setattr(self.settings.vacation, field, previous)
            self.vacation_tab.load_settings(self.settings.vacation)

    def _read_vacation_attacks(self) -> list[dict] | None:
        state = self.dashboard_tab._live_state
        villages = state.get("villages", []) if isinstance(state, dict) else []
        if not villages:
            return None
        try:
            return self.browser_session.read_incoming_attacks(villages)
        except Exception as error:
            logger.warning("Vacation safeguard could not read attacks: %s", error)
            return None

    def _check_vacation(self):
        if self.browser_session is None or self.dashboard_tab._live_state is None:
            self.vacation_tab.set_disconnected()
            return
        if self._vacation_pending_request is not None:
            return
        try:
            state = self.browser_session.read_vacation_state()
        except Exception as error:
            logger.warning("Could not read vacation state: %s", error)
            self.vacation_tab.set_state(None, None)
            self.vacation_tab.set_next_action(
                f"Nie udało się odczytać stanu wakacji: {error}"
            )
            return

        settings = self.settings.vacation
        attacks = self._read_vacation_attacks()
        counted = (
            None if attacks is None
            else count_hostile_movements(
                attacks,
                settings.counted_movement_types,
                state["server_time"],
            )
        )
        self.vacation_tab.set_state(state, counted)
        if not settings.enabled:
            self.vacation_tab.set_next_action(
                "Automat wyłączony — nic nie zmieniam."
            )
            return

        decision = decide_vacation_action(
            state,
            datetime.now(),
            attacks,
            plan_time=settings.plan_time,
            plan_window_minutes=settings.plan_window_minutes,
            decision_minutes_before=settings.decision_minutes_before,
            attack_threshold=settings.attack_threshold,
            counted_movement_types=settings.counted_movement_types,
            last_plan_date=settings.last_plan_date,
            next_own_attack=self._next_own_attack_departure(),
            vacation_days=settings.vacation_days,
        )
        self.vacation_tab.set_next_action(decision.reason)
        if decision.action in (ACTION_KEEP, ACTION_PLAN, ACTION_CANCEL):
            log_key = (decision.action, state["start"])
            if log_key != self._vacation_last_logged:
                self._vacation_last_logged = log_key
                self.vacation_tab.add_log(decision.reason)
        if (
            decision.action in (ACTION_PLAN, ACTION_CANCEL)
            and monotonic() >= self._vacation_retry_after
        ):
            self._send_vacation_change(
                settings.vacation_days if decision.action == ACTION_PLAN else None
            )

    def _send_vacation_change(self, days: int | None, manual: bool = False):
        planned = days is not None
        try:
            outcome = self.browser_session.change_vacation_state(
                days,
                CANCEL_SAFETY_SECONDS,
            )
        except Exception as error:
            logger.exception("Vacation change failed")
            self._vacation_retry_after = monotonic() + (300 if planned else 45)
            self.vacation_tab.add_log(f"Błąd wysyłania do gry: {error}")
            return

        status = outcome["status"]
        if status != "submitted":
            messages = {
                "not_idle": "wakacje są już zaplanowane lub aktywne",
                "not_planned": "nie ma zaplanowanych wakacji do anulowania",
                "too_late": "za mało czasu do aktywacji, by bezpiecznie anulować",
                "request_busy": "gra przetwarza inne zlecenie",
            }
            self._vacation_retry_after = monotonic() + 45
            self.vacation_tab.add_log(
                f"Nie wysłano ({'planowanie' if planned else 'anulowanie'}): "
                f"{messages[status]}."
            )
            self.vacation_tab.set_state(outcome["state"], None)
            return

        self._vacation_pending_request = (outcome["request_id"], planned, 0)
        self.vacation_tab.add_log(
            ("Wysłano planowanie wakacji" if planned else "Wysłano anulowanie wakacji")
            + (" (ręcznie)." if manual else ".")
        )
        QTimer.singleShot(1500, self._verify_vacation_change)

    def _next_own_attack_departure(self) -> float | None:
        """Server time of the next attack the attack automation will send."""
        automation = self.attack_automation
        if automation is None or not self.settings.attacks.enabled:
            return None
        departures = [
            run.departure for run in list(automation.runs.values())
            if run.status in ("waiting", "prepared")
        ]
        return min(departures) if departures else None

    def _verify_vacation_change(self):
        if self._vacation_pending_request is None or self.browser_session is None:
            self._vacation_pending_request = None
            return
        request_id, planned, attempts = self._vacation_pending_request
        try:
            result = self.browser_session.read_vacation_response(request_id, planned)
        except Exception as error:
            logger.exception("Could not verify vacation change")
            result = {"status": "unconfirmed", "error": str(error)}

        if result["status"] == "pending" and attempts < 5:
            self._vacation_pending_request = (request_id, planned, attempts + 1)
            QTimer.singleShot(1500, self._verify_vacation_change)
            return

        if result["status"] == "unconfirmed" and not result.get("error"):
            # The game's answer does not always carry the player data; check
            # the live state instead of assuming the change failed.
            try:
                state = self.browser_session.read_vacation_state()
                changed = (
                    state["vacation_state"] == 1 and state["start"] > 0
                    if planned else state["vacation_state"] == 0
                )
                if changed:
                    result = {**result, **state, "status": "confirmed"}
            except Exception as error:
                logger.warning("Could not re-read vacation state: %s", error)

        self._vacation_pending_request = None
        if result["status"] == "confirmed":
            if planned:
                self.settings.vacation.last_plan_date = date.today().isoformat()
                self._save_recruitment_settings()
                self.vacation_tab.add_log(
                    "Gra potwierdziła: wakacje zaplanowane na "
                    f"{datetime.fromtimestamp(result['start']):%d.%m %H:%M:%S}."
                )
            else:
                self.vacation_tab.add_log(
                    "Gra potwierdziła: wakacje anulowane, dzień wakacji zwrócony."
                )
            self._vacation_retry_after = 0.0
        else:
            reason = result.get("error") or (
                "brak odpowiedzi gry" if result["status"] == "pending"
                else "stan gracza się nie zmienił"
            )
            self._vacation_retry_after = monotonic() + (300 if planned else 45)
            self.vacation_tab.add_log(
                f"Gra nie potwierdziła {'planowania' if planned else 'anulowania'}: "
                f"{reason}. Ponowię przy kolejnej kontroli."
            )
        QTimer.singleShot(500, self._check_vacation)

    def _plan_vacation_manually(self):
        if self.browser_session is None or self._vacation_pending_request is not None:
            return
        days = self.settings.vacation.vacation_days
        answer = QMessageBox.question(
            self,
            "Zaplanować wakacje?",
            f"Zaplanować tryb wakacyjny na {days} d.? Włączy się za 24 h, "
            "o ile wcześniej go nie anulujesz.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._send_vacation_change(days, manual=True)

    def _cancel_vacation_manually(self):
        if self.browser_session is None or self._vacation_pending_request is not None:
            return
        answer = QMessageBox.question(
            self,
            "Anulować wakacje?",
            "Anulować zaplanowany tryb wakacyjny? Dzień wakacji zostanie zwrócony.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._send_vacation_change(None, manual=True)

    def _check_resource_milling(self):
        state = self.dashboard_tab._live_state
        if (
            self.browser_session is None
            or state is None
            or self._milling_scan_active
            or self._milling_order_active
        ):
            return

        villages = state.get("villages", [])
        if not villages:
            return
        self._milling_scan_active = True
        self.milling_tab.status.setText(
            "Sprawdzam pojemność spichlerzy, zapasy i rezerwę złota…"
        )
        try:
            rows = self.browser_session.read_resource_milling_state(villages)
            settings_changed = False
            added_village_ids = []
            village_names = {
                str(village.get("id") or village.get("village_id") or ""):
                str(village.get("name") or "Nieznana wioska")
                for village in villages
            }
            for row in rows:
                village_id = row["village_id"]
                if village_id not in self.settings.resource_milling.villages:
                    self.settings.resource_milling.villages[village_id] = (
                        VillageMillingSettings()
                    )
                    settings_changed = True
                    added_village_ids.append(village_id)

            if settings_changed and not self._save_recruitment_settings():
                for village_id in added_village_ids:
                    self.settings.resource_milling.villages.pop(
                        village_id,
                        None,
                    )
                self.milling_tab.status.setText(
                    "Nie zapisano ustawień dla nowych wiosek; mielenie "
                    "pozostaje wstrzymane."
                )
                return

            milling_villages = []
            for row in rows:
                capacity = row["capacities"]["grain"]
                milling_villages.append({
                    "id": row["village_id"],
                    "name": village_names[row["village_id"]],
                    "grain_percent": (
                        row["resources"]["grain"] / capacity * 100
                        if capacity > 0 else None
                    ),
                })
            self._render_resource_milling_villages(milling_villages)
            gold = int(min(row["gold"] for row in rows))
            self.milling_tab.set_gold_balance(gold)
            self.milling_tab.clear_pending_btn.setEnabled(
                bool(self.settings.resource_milling.pending_villages)
            )

            cleared_pending = []
            candidates = []
            blocked_reasons = []
            milling_settings = self.settings.resource_milling
            for row in rows:
                village_id = row["village_id"]
                village_settings = milling_settings.villages[village_id]
                grain = row["resources"]["grain"]
                capacity = row["capacities"]["grain"]
                grain_percent = (
                    grain / capacity * 100 if capacity > 0 else None
                )
                if (
                    village_id in milling_settings.pending_villages
                    and village_id
                    not in milling_settings.pending_refill_villages
                    and village_id not in self._pending_milling_responses
                    and grain_percent is not None
                    and grain_percent < village_settings.trigger_percent
                ):
                    milling_settings.pending_villages.discard(village_id)
                    cleared_pending.append(village_id)

                if (
                    not village_settings.enabled
                    and not village_settings.refill_enabled
                ):
                    continue
                if village_settings.distribution_total != 100:
                    blocked_reasons.append(
                        f"{village_names[village_id]}: udziały muszą sumować się do 100%"
                    )
                    continue
                if grain_percent is None or capacity <= 0:
                    blocked_reasons.append(
                        f"{village_names[village_id]}: brak danych spichlerza"
                    )
                    continue
                if village_id in milling_settings.pending_villages:
                    continue
                mode = None
                if (
                    village_settings.enabled
                    and grain > 0
                    and grain_percent >= village_settings.trigger_percent
                ):
                    mode = "empty"
                elif (
                    village_settings.refill_enabled
                    and grain_percent <= village_settings.refill_trigger_percent
                ):
                    mode = "refill"
                if mode is None:
                    continue
                if row["gold"] < (
                    milling_settings.minimum_gold_balance
                    + NPC_TRADER_PRICE
                ):
                    blocked_reasons.append(
                        f"{village_names[village_id]}: saldo nie pozwala "
                        "zachować rezerwy złota po wymianie"
                    )
                    continue
                candidates.append({
                    **row,
                    "village": village_names[village_id],
                    "mode": mode,
                    "settings": village_settings.model_dump(),
                })

            if cleared_pending and not self._save_recruitment_settings():
                milling_settings.pending_villages.update(cleared_pending)
                return

            if candidates:
                self._milling_candidates = candidates
                self._milling_order_active = True
                QTimer.singleShot(0, self._run_next_resource_milling)
                return

            if blocked_reasons:
                self.milling_tab.status.setText(
                    "Kontrola zakończona. " + " · ".join(blocked_reasons)
                )
            else:
                self.milling_tab.status.setText(
                    f"Kontrola {datetime.now():%H:%M}: zapasy sprawdzone; "
                    "żadna włączona metoda nie osiągnęła progu."
                )
        except Exception as error:
            logger.exception("Resource milling scan failed")
            self.milling_tab.status.setText(
                f"Nie udało się sprawdzić mielenia: {error}. "
                "Nie wysłano nowych wymian."
            )
        finally:
            self._milling_scan_active = False

    def _run_next_resource_milling(self):
        if self.browser_session is None or not self._milling_candidates:
            self._milling_order_active = False
            return

        row = self._milling_candidates.pop(0)
        village_id = row["village_id"]
        village_settings = self.settings.resource_milling.villages.get(
            village_id,
            VillageMillingSettings(),
        )
        mode = row["mode"]
        mode_enabled = (
            village_settings.refill_enabled
            if mode == "refill"
            else village_settings.enabled
        )
        if (
            not mode_enabled
            or village_settings.distribution_total != 100
            or village_id
            in self.settings.resource_milling.pending_villages
        ):
            QTimer.singleShot(0, self._run_next_resource_milling)
            return

        self.settings.resource_milling.pending_villages.add(village_id)
        if mode == "refill":
            self.settings.resource_milling.pending_refill_villages.add(
                village_id
            )
        if not self._save_recruitment_settings():
            self.settings.resource_milling.pending_villages.discard(village_id)
            self.settings.resource_milling.pending_refill_villages.discard(
                village_id
            )
            self._milling_candidates.clear()
            self._milling_order_active = False
            return
        self.milling_tab.clear_pending_btn.setEnabled(True)

        try:
            result = self.browser_session.queue_resource_milling(
                village_id,
                {
                    **village_settings.model_dump(),
                    "mode": mode,
                },
                self.settings.resource_milling.minimum_gold_balance,
            )
        except Exception as error:
            logger.exception("NPC trader exchange outcome is uncertain")
            self._milling_candidates.clear()
            self._milling_order_active = False
            self.milling_tab.status.setText(
                f"Nie udało się potwierdzić wymiany w wiosce "
                f"{row['village']}: {error}. Blokuję ponowienie, aby "
                "uniknąć podwójnego kosztu złota; sprawdź stan w grze."
            )
            return

        status = result["status"]
        if status == "submitted":
            request_id = result["request_id"]
            self._pending_milling_responses[village_id] = 0
            self.milling_tab.status.setText(
                f"Wysłano wymianę NPC dla {row['village']} za "
                f"{NPC_TRADER_PRICE} złota. Sprawdzam odpowiedź gry…"
            )
            QTimer.singleShot(
                1500,
                lambda: self._verify_resource_milling_response(
                    village_id,
                    request_id,
                    row["village"],
                    mode,
                    (
                        row["settings"]["refill_trigger_percent"]
                        if mode == "refill"
                        else row["settings"]["trigger_percent"]
                    ),
                    result["target_grain"],
                ),
            )
            return

        self.settings.resource_milling.pending_villages.discard(village_id)
        self.settings.resource_milling.pending_refill_villages.discard(
            village_id
        )
        self._save_recruitment_settings()
        self.milling_tab.clear_pending_btn.setEnabled(
            bool(self.settings.resource_milling.pending_villages)
        )
        messages = {
            "below_gold_reserve": (
                "saldo jest mniejsze niż rezerwa plus koszt 5 złota"
            ),
            "below_threshold": "zboże spadło poniżej ustawionego progu",
            "nothing_to_mill": "brak surowców do rozdzielenia",
            "request_busy": "gra przetwarza już inne zlecenie",
        }
        self.milling_tab.status.setText(
            f"Nie wykonano wymiany w wiosce {row['village']}: "
            f"{messages.get(status, 'warunki gry nie pozwalają na wymianę')}."
        )
        if status == "below_gold_reserve" or status == "request_busy":
            self._milling_candidates.clear()
            self._milling_order_active = False
            return
        QTimer.singleShot(0, self._run_next_resource_milling)

    def _verify_resource_milling_response(
        self,
        village_id: str,
        request_id: str,
        village_name: str,
        mode: str,
        trigger_percent: int,
        target_grain: int,
    ):
        if (
            self.browser_session is None
            or village_id not in self._pending_milling_responses
        ):
            return
        try:
            result = self.browser_session.read_resource_milling_response(
                request_id,
                mode,
                trigger_percent,
                target_grain,
            )
        except Exception as error:
            logger.exception("Could not verify NPC trader response")
            self._pending_milling_responses.pop(village_id, None)
            self._milling_candidates.clear()
            self._milling_order_active = False
            self.milling_tab.status.setText(
                f"Nie udało się potwierdzić wymiany w wiosce {village_name}: "
                f"{error}. Blokada pozostaje, by uniknąć ponownego kosztu."
            )
            return

        if result["status"] == "pending":
            attempts = self._pending_milling_responses[village_id] + 1
            if attempts < 3:
                self._pending_milling_responses[village_id] = attempts
                QTimer.singleShot(
                    1500,
                    lambda: self._verify_resource_milling_response(
                        village_id,
                        request_id,
                        village_name,
                        mode,
                        trigger_percent,
                        target_grain,
                    ),
                )
                return
            self._pending_milling_responses.pop(village_id, None)
            self._milling_candidates.clear()
            self._milling_order_active = False
            self.milling_tab.status.setText(
                f"Gra nie potwierdziła wymiany w wiosce {village_name}. "
                "Nie ponawiam jej, aby uniknąć ponownego kosztu 5 złota; "
                "sprawdź zapasy i złoto."
            )
            return

        self._pending_milling_responses.pop(village_id, None)
        if result["status"] == "confirmed":
            self.settings.resource_milling.pending_villages.discard(village_id)
            self.settings.resource_milling.pending_refill_villages.discard(
                village_id
            )
            if not self._save_recruitment_settings():
                self.settings.resource_milling.pending_villages.add(village_id)
                if mode == "refill":
                    self.settings.resource_milling.pending_refill_villages.add(
                        village_id
                    )
                self._milling_candidates.clear()
                self._milling_order_active = False
                return
            self.milling_tab.clear_pending_btn.setEnabled(
                bool(self.settings.resource_milling.pending_villages)
            )
            self.milling_tab.set_gold_balance(
                int(result["gold"])
                if result.get("gold") is not None
                else None
            )
            resources = result["resources"]
            grain_percent = (
                resources["grain"] / result["grain_capacity"] * 100
            )
            self.milling_tab.status.setText(
                f"Potwierdzono {'uzupełnianie' if mode == 'refill' else 'opróżnianie'} "
                f"spichlerza w wiosce {village_name}: "
                f"zboże {grain_percent:.1f}% pojemności "
                f"({resources['grain']:,.0f}), glina {resources['clay']:,.0f}, "
                f"żelazo {resources['iron']:,.0f}, "
                f"drewno {resources['wood']:,.0f}. "
                f"Saldo po wymianie: "
                f"{result.get('gold') if result.get('gold') is not None else 'brak danych'} złota."
            )
            QTimer.singleShot(
                _NPC_TRADER_ORDER_INTERVAL_MS,
                self._run_next_resource_milling,
            )
            return

        self._milling_candidates.clear()
        self._milling_order_active = False
        self._pending_milling_responses.pop(village_id, None)
        if mode == "refill":
            reason = (
                f"zboże nie osiągnęło oczekiwanych {target_grain:,.0f} "
                "po wymianie"
            )
        else:
            reason = (
                f"zboże nie spadło poniżej progu {trigger_percent}%"
            )
        self.milling_tab.status.setText(
            f"Serwer odpowiedział, ale {reason} w wiosce {village_name}. "
            "Blokada pozostaje, aby uniknąć ponownego kosztu. "
            "Sprawdź zapasy w grze."
        )

    def _check_recruitment_queues(self):
        """Check target troop queues in every village without changing the game view."""
        state = self.dashboard_tab._live_state
        if self.browser_session is None or state is None:
            return

        self.recruitment_tab.recruitment_queue_status.setText(
            "Wykrywam jednostki i sprawdzam kolejki we wszystkich wioskach…"
        )
        try:
            villages = state.get("villages", [])
            rows = self.browser_session.read_recruitment_queues(
                villages
            )
            try:
                self._village_production = (
                    self.browser_session.read_village_production(villages)
                )
            except Exception as error:
                logger.warning("Could not read village production: %s", error)
            recruitment_villages = []
            settings_changed = False
            added_village_settings = []
            for village in villages:
                village_id = str(village.get("id") or village.get("village_id") or "")
                if not village_id:
                    continue
                recruitment_villages.append({
                    "id": village_id,
                    "name": village.get("name") or "Nieznana wioska",
                    "recruitment_units": [
                        row for row in rows
                        if row["village_id"] == village_id
                    ],
                })
                if village_id not in self.settings.recruitment.villages:
                    self.settings.recruitment.villages[village_id] = (
                        VillageRecruitmentSettings()
                    )
                    settings_changed = True
                    added_village_settings.append(village_id)

            if settings_changed and not self._save_recruitment_settings():
                for village_id in added_village_settings:
                    self.settings.recruitment.villages.pop(village_id, None)
                self.recruitment_tab.recruitment_queue_status.setText(
                    "Nie zapisano ustawień dla nowych wiosek. "
                    "Automatyczna rekrutacja wstrzymana."
                )
                return
            self._render_village_recruitment_settings(recruitment_villages)

            pending_orders = self.settings.recruitment.pending_orders
            previous_pending_orders = set(pending_orders)
            legacy_order_keys = {
                _recruitment_queue_key(row["village_id"], row["unit"]):
                _recruitment_row_key(row)
                for row in rows
            }
            migrated_pending = False
            for legacy_key, stable_key in legacy_order_keys.items():
                if legacy_key in pending_orders and legacy_key != stable_key:
                    pending_orders.remove(legacy_key)
                    pending_orders.add(stable_key)
                    migrated_pending = True
            if migrated_pending and not self._save_recruitment_settings():
                pending_orders.clear()
                pending_orders.update(previous_pending_orders)
                self.recruitment_tab.recruitment_queue_status.setText(
                    "Nie zapisano migracji oczekujących zleceń. "
                    "Automatyczna rekrutacja wstrzymana."
                )
                return
            confirmed = {
                _recruitment_row_key(row)
                for row in rows
                if self._recruitment_queue_is_low(row) is False
            }
            cleared = pending_orders.intersection(confirmed)
            if cleared:
                pending_orders.difference_update(cleared)
                self._save_recruitment_settings()
            self._update_recruitment_queues(rows, pending_orders)
            self._begin_automatic_recruitment(rows)
        except Exception as error:
            logger.warning("Travian recruitment queue scan failed: %s", error)
            self.recruitment_tab.recruitment_queue_status.setText(
                f"Nie udało się odczytać kolejek: {error}. "
                "Ostatni poprawny odczyt pozostaje widoczny."
            )

    def _disconnect_browser(self):
        self._auto_recruitment_candidates.clear()
        self._auto_recruitment_active = False
        self._pending_recruitment_responses.clear()
        self._milling_candidates.clear()
        self._milling_order_active = False
        self._milling_scan_active = False
        self._pending_milling_responses.clear()
        self.troops_refresh_timer.stop()
        self.recruitment_queue_timer.stop()
        self.resource_milling_timer.stop()
        self.vacation_timer.stop()
        self._vacation_pending_request = None
        self._stop_attack_automation()
        self._stop_hideout_defense()
        self.kingdom_timer.stop()
        self.scout_timer.stop()
        self.scout_automation = None
        self.scout_tab.set_state("Nie połączono z grą", "muted")
        self._stop_building()
        self.incoming_attack_eta_timer.stop()
        if self.browser_session is not None:
            self.browser_session.stop_incoming_attack_listener()
            self.browser_session.disconnect()
            self.browser_session = None
        self.dashboard_tab.connect_button.setEnabled(True)
        self.dashboard_tab.disconnect_button.setEnabled(False)
        self.dashboard_tab.refresh_troops_btn.setEnabled(False)
        self.dashboard_tab.connection_status.setText("Nie połączono z grą")
        self.incoming_attacks_tab.set_disconnected()
        self.vacation_tab.set_disconnected()
        self.dashboard_tab.clear_live_state()
        self.dashboard_tab.clear_recruitment_queues()
        self.recruitment_tab.clear_recruitment_queues()
        self.recruitment_tab.clear_recruitment_economics()
        self.dashboard_tab.update_dashboard()
        self._refresh_data()

    def closeEvent(self, event):
        self.refresh_timer.stop()
        self._disconnect_browser()
        super().closeEvent(event)
    
    def _refresh_data(self):
        """Refresh all data displays."""
        self.dashboard_tab.update_dashboard()
        if self.dashboard_tab._live_state is not None:
            self._sync_live_village_list(self.dashboard_tab._live_state)
            return

        # Update village list from tracker
        villages = self.tracker.get_all_villages()
        selected_id = (
            self.village_list_widget.currentItem().data(Qt.UserRole)
            if self.village_list_widget.currentItem()
            else ""
        )
        self.village_list_widget.clear()
        selected_item = None
        for v in villages:
            item = QListWidgetItem(
                f"{v.name or 'Unknown'} [{v.row}/{v.col}/{v.field_num}]"
            )
            item.setData(Qt.UserRole, v.village_id)
            self.village_list_widget.addItem(item)
            if v.village_id == selected_id or (
                not selected_id and selected_item is None
            ):
                selected_item = item
        if selected_item is not None:
            self.village_list_widget.setCurrentItem(selected_item)
            self.village_detail_tab.update_village(
                str(selected_item.data(Qt.UserRole))
            )
            self.dashboard_tab.update_persisted_village(
                str(selected_item.data(Qt.UserRole))
            )

    def _persist_live_dashboard_state(self, state: dict):
        village_id = str(state.get("selected_village_id") or "")
        if not village_id:
            return
        for key in ("buildings", "resource_fields", "map_villages"):
            values = state.get(key)
            if isinstance(values, list) and values:
                self.tracker.repo.save_dashboard_snapshot(
                    village_id, key, values
                )

    def _sync_live_village_list(self, state: dict):
        villages = state.get("villages", [])
        selected_id = str(state.get("selected_village_id", ""))
        self.village_list_widget.blockSignals(True)
        self.village_list_widget.clear()
        for village in villages:
            village_id = str(village.get("id", ""))
            name = village.get("name", "Nieznana wioska")
            item = QListWidgetItem(
                f"{name}  ·  wybrana" if village_id == selected_id else name
            )
            item.setData(Qt.UserRole, village_id)
            item.setToolTip(
                "Aktualnie wybrana wioska"
                if village_id == selected_id
                else "Kliknij, aby przełączyć wioskę"
            )
            self.village_list_widget.addItem(item)
            if village_id == selected_id:
                self.village_list_widget.setCurrentItem(item)
        self.village_list_widget.blockSignals(False)
    
    def _on_village_selected(self, current, previous):
        """Switch the live game to the village clicked in the sidebar."""
        if current:
            village_id = current.data(Qt.UserRole)
            if village_id:
                if self.browser_session is not None:
                    combo = self.dashboard_tab.village_selector
                    index = combo.findData(village_id)
                    if index >= 0 and index != combo.currentIndex():
                        combo.setCurrentIndex(index)
                    return
                self.village_detail_tab.update_village(village_id)
                self.dashboard_tab.update_persisted_village(village_id)
    
    def _show_settings(self):
        """Show settings dialog."""
        dialog = SettingsDialog(self.settings, self)
        dialog.exec()
    
    def _add_account(self):
        """Show add account dialog."""
        dialog = AccountDialog(self.account_mgr, self)
        dialog.exec()
        
        if dialog.result() == QDialog.Accepted:
            self._refresh_data()
    
    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About Travian Kingdoms Assistant",
            f"Wersja: {self.settings.version}\n"
            "Autor: Dzogurt\n\n"
            "A desktop application for managing and tracking progress in Travian Kingdoms.\n\n"
            "Features:\n"
            "- Village management & resource tracking\n"
            "- Attack planning & coordination\n"
            "- Defense automation\n"
            "- Coalition attack support",
        )


class TravelAssistantApp(QApplication):
    """Main application class."""
    
    def __init__(self):
        super().__init__()
        
        # Load settings
        self.settings = AppSettings.load()
        
        # Initialize database
        self.db = DatabaseManager(self.settings.database.db_path)
        self.db.init()
        self.backup_timer = QTimer(self)
        backup_interval_hours = max(
            self.settings.database.backup_interval_hours, 1
        )
        self.backup_timer.setInterval(backup_interval_hours * 60 * 60 * 1000)
        self.backup_timer.timeout.connect(self._create_database_backup)
        
        # Initialize repositories
        self.account_repo = AccountRepository(self.db)
        self.village_repo = VillageRepository(self.db)
        self.attack_repo = AttackLogRepository(self.db)
        self.incoming_attack_repo = IncomingAttackRepository(self.db)
        
        # Initialize core services
        self.account_mgr = AccountManager(self.account_repo)
        self.village_tracker = VillageTracker(self.village_repo)
        
        # Create main window
        self.window = MainWindow(
            self.settings,
            self.account_mgr,
            self.village_tracker,
            self.incoming_attack_repo,
        )
        self.window.show()
        if self.db.backup_is_due(backup_interval_hours):
            QTimer.singleShot(0, self._create_database_backup)
        self.backup_timer.start()

    def _create_database_backup(self):
        try:
            self.db.create_backup()
        except Exception as error:
            logger.exception("Scheduled database backup failed")
            QMessageBox.warning(
                self.window,
                "Nie udało się wykonać kopii zapasowej",
                str(error),
            )
