"""Main application module with PySide6 UI."""

import logging
import json
import sys
import tempfile
import threading
from datetime import date, datetime
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
        HideoutDefenseSettings,
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
    from src.core.attack_operations import UNIT_NAMES
    from src.core.attack_clicker import ClickAttackExecutor
    from src.core.tktools_client import TkToolsSession
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
        HideoutDefenseSettings,
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
    from core.attack_operations import UNIT_NAMES
    from core.attack_clicker import ClickAttackExecutor
    from core.tktools_client import TkToolsSession
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
        elif queue.get("other_count", 0):
            status = ("Budynek zajęty innymi", "waiting")
            status_label.setToolTip(
                f"W budynku szkoli się {queue['other_count']} szt. "
                "innych jednostek."
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


class IncomingAttacksWidget(QWidget):
    """Display the persisted history of attacks detected in owned villages."""

    delete_requested = Signal(int)

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

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Cel", "Atakujący", "Wioska źródłowa",
            "Rodzaj", "Dotarcie", "Pozostało", "Status", "Wykryto",
        ])
        DashboardWidget._configure_table(
            self.table,
            stretch_columns=(0, 1, 2, 6, 7),
        )
        self.table.cellDoubleClicked.connect(self._show_attack_details)
        layout.addWidget(self.table)

        self.delete_button = QPushButton("Usuń wybrany wpis")
        self.delete_button.clicked.connect(self._request_delete_selected)
        layout.addWidget(self.delete_button, alignment=Qt.AlignRight)

        hint = QLabel(
            "Historia ataków jest zapisywana lokalnie. Podwójne kliknięcie "
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
                record.get("movement_type", ""),
                datetime.fromtimestamp(arrival_time).strftime("%d.%m %H:%M:%S"),
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
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(
                self,
                "Historia ataków",
                "Najpierw wybierz rekord do usunięcia.",
            )
            return
        record_id = self.table.item(selected[0].row(), 0).data(Qt.UserRole)
        if isinstance(record_id, int):
            self.delete_requested.emit(record_id)

    def refresh_eta(self):
        now = monotonic()
        for attack in self._attacks:
            attack_key = f"{attack['village_id']}:{attack['movement_id']}"
            row = self._row_by_attack_key.get(attack_key)
            if row is None:
                continue
            item = self.table.item(row, 5)
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
        self._milling_order_active = False
        self._milling_scan_active = False
        self._pending_milling_responses: dict[str, int] = {}
        self._connected_milling_controls: set[int] = set()
        self._seen_incoming_attack_ids: set[str] = set()
        self._incoming_attacks_by_key: dict[str, dict] = {}
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
            else:
                self.recruitment_queue_timer.stop()
                self.resource_milling_timer.stop()
                self.vacation_timer.stop()
                self.incoming_attack_eta_timer.stop()
        except Exception as error:
            self.browser_session = None
            logger.warning("Travian browser connection failed: %s", error)
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

    def _delete_incoming_attack_record(self, record_id: int):
        if self.incoming_attack_repo is None:
            QMessageBox.warning(
                self,
                "Historia ataków",
                "Historia ataków nie jest dostępna.",
            )
            return
        record = self.incoming_attack_repo.get_attack(record_id)
        if record is None:
            self.incoming_attacks_tab.update_history(
                self.incoming_attack_repo.get_recent_attacks()
            )
            return
        confirmation = QMessageBox.question(
            self,
            "Usuń rekord ataku",
            "Czy na pewno usunąć wybrany rekord z historii?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return
        try:
            self.incoming_attack_repo.delete_attack(record_id)
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
            logger.exception("Could not delete incoming attack history record")
            QMessageBox.warning(
                self,
                "Błąd historii ataków",
                f"Nie udało się usunąć wybranego rekordu: {error}",
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
            for resource, label in resource_labels.items():
                resource_point = (
                    getattr(tracked_village, resource)
                    if tracked_village is not None
                    else None
                )
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
                        resource_point.last_updated
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
            result = self.browser_session.queue_recruitment(
                row,
                batch_size,
                queue_threshold,
                min_quantity=1 if duration_mode else batch_size,
                resource_share=1.0 / same_village,
            )
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
