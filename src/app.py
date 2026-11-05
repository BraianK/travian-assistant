"""Main application module with PySide6 UI."""

import logging
import json
import sys
from datetime import datetime
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
    QGridLayout,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon

try:
    from src.config.settings import (
        AppSettings,
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
"""

_TROOP_REFRESH_INTERVAL_MS = 5 * 60 * 1000
_RECRUITMENT_QUEUE_REFRESH_INTERVAL_MS = 3 * 60 * 1000
_RESOURCE_MILLING_REFRESH_INTERVAL_MS = 30 * 1000
_AUTO_RECRUITMENT_ORDER_INTERVAL_MS = 6 * 1000
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


class RecruitmentWidget(QWidget):
    """Dedicated view for village recruitment queues and automation controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Rekrutacja")
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        layout.addWidget(title)

        description = QLabel(
            "Automatycznie wykrywaj jednostki dostępne dla plemienia każdej "
            "wioski i monitoruj ich kolejki. Stan odświeża się co 3 minuty."
        )
        description.setObjectName("secondaryText")
        description.setWordWrap(True)
        layout.addWidget(description)

        automation_group = QGroupBox("Automatyczne uzupełnianie")
        automation_layout = QVBoxLayout(automation_group)
        automation_layout.setContentsMargins(14, 16, 14, 14)
        automation_layout.setSpacing(10)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(12)
        self.auto_recruitment_checkbox = QCheckBox(
            "Włącz automatyczne uzupełnianie"
        )
        self.auto_recruitment_checkbox.setToolTip(
            "Włącza główny przełącznik automatu. Wybierz osobno wioski "
            "i typy jednostek, które automat ma uzupełniać."
        )
        controls_layout.addWidget(self.auto_recruitment_checkbox)
        controls_layout.addWidget(QLabel("Tryb:"))
        self.queue_strategy_combobox = QComboBox()
        self.queue_strategy_combobox.addItem("Liczba jednostek", "count")
        self.queue_strategy_combobox.addItem("Czas kolejki", "duration")
        self.queue_strategy_combobox.setFixedWidth(150)
        self.queue_strategy_combobox.setToolTip(
            "Liczba jednostek: dodaje stałą partię, gdy w kolejce zostanie "
            "podana liczba jednostek lub mniej.\n"
            "Czas kolejki: utrzymuje kolejkę o docelowej długości w minutach, "
            "ustawianej osobno dla każdej wioski i jednostki."
        )
        controls_layout.addWidget(self.queue_strategy_combobox)
        self.auto_recruitment_batch_size_label = QLabel("Liczebność partii:")
        controls_layout.addWidget(self.auto_recruitment_batch_size_label)
        self.auto_recruitment_batch_size = QSpinBox()
        self.auto_recruitment_batch_size.setRange(1, 100)
        self.auto_recruitment_batch_size.setSuffix(" jednostek")
        self.auto_recruitment_batch_size.setFixedWidth(150)
        self.auto_recruitment_batch_size.setToolTip(
            "Zlecenie zostanie wysłane tylko, jeśli wioska ma zasoby "
            "na całą partię."
        )
        controls_layout.addWidget(self.auto_recruitment_batch_size)
        self.auto_recruitment_threshold_label = QLabel("Uzupełnij, gdy zostanie:")
        controls_layout.addWidget(self.auto_recruitment_threshold_label)
        self.auto_recruitment_threshold = QSpinBox()
        self.auto_recruitment_threshold.setRange(0, 100)
        self.auto_recruitment_threshold.setSuffix(" szt. lub mniej")
        self.auto_recruitment_threshold.setFixedWidth(130)
        self.auto_recruitment_threshold.setToolTip(
            "Automat doda partię, gdy liczba docelowych jednostek w kolejce "
            "spadnie do tej wartości lub niżej. Wartość 0 oznacza pustą kolejkę."
        )
        controls_layout.addWidget(self.auto_recruitment_threshold)
        controls_layout.addStretch()
        self.clear_recruitment_pending_btn = QPushButton(
            "Wyczyść oczekujące zlecenia…"
        )
        self.clear_recruitment_pending_btn.setToolTip(
            "Wyłącza automat i usuwa blokady oczekujących zleceń. "
            "Najpierw sprawdź stan kolejek w grze."
        )
        self.clear_recruitment_pending_btn.setEnabled(False)
        controls_layout.addWidget(self.clear_recruitment_pending_btn)
        automation_layout.addLayout(controls_layout)

        automation_help = QLabel(
            "Dla każdej wioski wybierz typy jednostek, które automat ma "
            "uzupełniać. Aplikacja wykrywa jednostki i wymagane budynki "
            "na podstawie plemienia wioski. Niedostępne budynki "
            "są wyłączone. Partia zostanie dodana tylko przy wystarczających "
            "surowcach. Główny automat jest domyślnie wyłączony."
        )
        automation_help.setObjectName("secondaryText")
        automation_help.setWordWrap(True)
        automation_layout.addWidget(automation_help)

        village_settings_group = QGroupBox("Zakres automatu — osobno dla wioski")
        village_settings_layout = QVBoxLayout(village_settings_group)
        village_settings_layout.setContentsMargins(10, 12, 10, 10)
        self.village_settings_scroll = QScrollArea()
        self.village_settings_scroll.setWidgetResizable(True)
        self.village_settings_scroll.setMinimumHeight(105)
        self.village_settings_scroll.setMaximumHeight(180)
        self.village_settings_content = QWidget()
        self.village_settings_content_layout = QVBoxLayout(
            self.village_settings_content
        )
        self.village_settings_content_layout.setContentsMargins(4, 4, 4, 4)
        self.village_settings_content_layout.setSpacing(6)
        self.village_settings_empty_label = QLabel(
            "Połącz z grą, aby skonfigurować rekrutację dla każdej wioski."
        )
        self.village_settings_empty_label.setObjectName("secondaryText")
        self.village_settings_scroll.setWidget(self.village_settings_content)
        village_settings_layout.addWidget(self.village_settings_scroll)
        automation_layout.addWidget(village_settings_group)
        layout.addWidget(automation_group)

        economics_group = QGroupBox("Bilans surowców przy ciągłej rekrutacji")
        economics_layout = QVBoxLayout(economics_group)
        economics_layout.setContentsMargins(14, 14, 14, 12)
        self.recruitment_economics_status = QLabel(
            "Połącz z grą, aby oszacować koszt rekrutacji na godzinę."
        )
        self.recruitment_economics_status.setObjectName("secondaryText")
        self.recruitment_economics_status.setWordWrap(True)
        economics_layout.addWidget(self.recruitment_economics_status)
        self.recruitment_economics_table = QTableWidget(0, 5)
        self.recruitment_economics_table.setHorizontalHeaderLabels(
            [
                "Wioska",
                "Drewno",
                "Glina",
                "Żelazo",
                "Zboże",
            ]
        )
        DashboardWidget._configure_table(
            self.recruitment_economics_table,
            stretch_columns=(0, 1, 2, 3, 4),
        )
        self.recruitment_economics_table.setMinimumHeight(150)
        self.recruitment_economics_table.setMaximumHeight(280)
        self.recruitment_economics_table.setWordWrap(True)
        economics_layout.addWidget(self.recruitment_economics_table)
        layout.addWidget(economics_group)

        queues_group = QGroupBox("Stan kolejek")
        queues_layout = QVBoxLayout(queues_group)
        queues_layout.setContentsMargins(14, 16, 14, 14)
        queues_layout.setSpacing(8)
        self.recruitment_queue_status = QLabel(
            "Połącz z grą, aby odczytać kolejki."
        )
        self.recruitment_queue_status.setObjectName("secondaryText")
        self.recruitment_queue_status.setWordWrap(True)
        queues_layout.addWidget(self.recruitment_queue_status)

        self.recruitment_queue_table = QTableWidget(0, 5)
        self.recruitment_queue_table.setHorizontalHeaderLabels(
            ["Wioska", "Jednostka · budynek", "W kolejce", "Czas kolejki", "Status"]
        )
        DashboardWidget._configure_table(
            self.recruitment_queue_table,
            stretch_columns=(0, 1, 4),
        )
        self.recruitment_queue_table.setMinimumHeight(180)
        queues_layout.addWidget(self.recruitment_queue_table, stretch=1)

        queue_actions_layout = QHBoxLayout()
        self.check_recruitment_queues_btn = QPushButton("Odśwież kolejki")
        self.check_recruitment_queues_btn.setToolTip(
            "Odczytaj aktualne kolejki we wszystkich wioskach."
        )
        queue_actions_layout.addStretch()
        queue_actions_layout.addWidget(self.check_recruitment_queues_btn)
        queues_layout.addLayout(queue_actions_layout)
        layout.addWidget(queues_group, stretch=1)

        self._recruitment_pending_keys: set[str] = set()
        self.village_recruitment_controls: dict[str, dict[str, QCheckBox]] = {}
        self.village_queue_duration_controls: dict[str, dict[str, QSpinBox]] = {}
        self.queue_strategy_combobox.currentIndexChanged.connect(
            self._apply_recruitment_strategy_visibility
        )
        self._apply_recruitment_strategy_visibility()

    def recruitment_strategy(self) -> str:
        return self.queue_strategy_combobox.currentData() or "count"

    def set_recruitment_strategy(self, strategy: str):
        index = self.queue_strategy_combobox.findData(strategy)
        self.queue_strategy_combobox.blockSignals(True)
        self.queue_strategy_combobox.setCurrentIndex(max(0, index))
        self.queue_strategy_combobox.blockSignals(False)
        self._apply_recruitment_strategy_visibility()

    def _apply_recruitment_strategy_visibility(self, *_args):
        duration = self.recruitment_strategy() == "duration"
        self.auto_recruitment_threshold_label.setHidden(duration)
        self.auto_recruitment_threshold.setHidden(duration)
        self.auto_recruitment_batch_size_label.setText(
            "Maks. partia:" if duration else "Liczebność partii:"
        )
        self.auto_recruitment_batch_size.setToolTip(
            "Maksymalna liczba jednostek w jednym zleceniu. Automat dodaje "
            "tylko brakujący czas kolejki, nie więcej niż ta wartość."
            if duration else
            "Zlecenie zostanie wysłane tylko, jeśli wioska ma zasoby "
            "na całą partię."
        )
        for controls in self.village_queue_duration_controls.values():
            for spin_box in controls.values():
                spin_box.setHidden(not duration)

    def update_village_recruitment_settings(
        self,
        villages: list[dict],
        settings_by_village: dict,
    ):
        while self.village_settings_content_layout.count():
            item = self.village_settings_content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self.village_settings_empty_label:
                    widget.hide()
                    widget.setParent(self.village_settings_content)
                else:
                    widget.deleteLater()

        self.village_recruitment_controls.clear()
        self.village_queue_duration_controls.clear()
        if not villages:
            self.village_settings_content_layout.addWidget(
                self.village_settings_empty_label
            )
            self.village_settings_empty_label.show()
            return

        self.village_settings_empty_label.hide()
        for village in villages:
            village_id = str(village["id"])
            village_settings = settings_by_village[village_id]
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(2, 0, 2, 0)
            row_layout.setSpacing(12)
            header_layout = QHBoxLayout()
            header_layout.setContentsMargins(0, 0, 0, 0)
            name_label = QLabel(village.get("name") or f"Wioska {village_id}")
            name_label.setMinimumWidth(110)
            header_layout.addWidget(name_label)

            enabled = QCheckBox("Automat")
            enabled.setChecked(village_settings.enabled)
            enabled.setToolTip(
                "Włącz lub wyłącz automatyczną rekrutację w tej wiosce."
            )
            header_layout.addWidget(enabled)
            header_layout.addStretch()
            row_layout.addLayout(header_layout)

            units = {}
            duration_inputs = {}
            units_layout = QGridLayout()
            units_layout.setContentsMargins(0, 0, 0, 0)
            units_layout.setHorizontalSpacing(8)
            columns_per_row = 3
            for index, unit in enumerate(village.get("recruitment_units", [])):
                unit_id = str(unit["unit_id"])
                building_available = unit.get("building_available") is True
                selected = unit_id in village_settings.units
                checkbox = QCheckBox(f"{unit['building']} · {unit['unit']}")
                checkbox.setChecked(selected)
                # A selected unit stays editable so it can be deselected even
                # when its building is missing; the automation skips it.
                checkbox.setEnabled(building_available or selected)
                if not building_available:
                    checkbox.setToolTip(
                        "W tej wiosce nie ma odpowiedniego budynku — "
                        "automat pominie tę jednostkę."
                    )

                spin_box = QSpinBox()
                spin_box.setRange(1, 1440)
                spin_box.setSuffix(" min")
                spin_box.setFixedWidth(90)
                spin_box.setValue(
                    village_settings.queue_minutes_by_unit.get(
                        unit_id,
                        DEFAULT_RECRUITMENT_QUEUE_MINUTES,
                    )
                )
                spin_box.setEnabled(building_available)
                spin_box.setToolTip(
                    f"Docelowa długość kolejki dla {unit['unit']} w tej "
                    "wiosce. Automat dobierze liczbę jednostek tak, aby "
                    "uzupełnić brakujący czas."
                )

                grid_row = index // columns_per_row
                grid_column = (index % columns_per_row) * 2
                units_layout.addWidget(checkbox, grid_row, grid_column)
                units_layout.addWidget(spin_box, grid_row, grid_column + 1)
                units[unit_id] = checkbox
                duration_inputs[unit_id] = spin_box

            row_layout.addLayout(units_layout)
            self.village_settings_content_layout.addWidget(row)
            self.village_recruitment_controls[village_id] = {
                "enabled": enabled,
                **units,
            }
            self.village_queue_duration_controls[village_id] = duration_inputs

        self._apply_recruitment_strategy_visibility()

    def update_recruitment_queues(
        self,
        rows: list[dict],
        pending_keys: set[str] | None = None,
    ):
        self._recruitment_pending_keys = set(pending_keys or ())
        self.recruitment_queue_table.setRowCount(len(rows))
        inactive_queues = 0
        unavailable_buildings = 0
        for row_index, queue in enumerate(rows):
            self.recruitment_queue_table.setItem(
                row_index, 0, QTableWidgetItem(queue["village"])
            )
            self.recruitment_queue_table.setItem(
                row_index,
                1,
                QTableWidgetItem(f"{queue['unit']} · {queue['building']}"),
            )
            key = _recruitment_queue_key(
                queue["village_id"],
                queue.get("unit_id", queue["unit"]),
            )
            legacy_key = _recruitment_queue_key(
                queue["village_id"],
                queue["unit"],
            )
            if (
                key in self._recruitment_pending_keys
                or legacy_key in self._recruitment_pending_keys
            ):
                count_text = "—"
                status = "Wysłano · potwierdzanie"
                color = QColor("#fff3cd")
            elif queue.get("building_available") is False:
                count_text = "—"
                status = "Brak budynku"
                color = QColor("#edf0f4")
                unavailable_buildings += 1
            elif queue["count"]:
                count_text = f"{queue['count']} szt."
                status = "Rekrutacja trwa"
                color = QColor("#e8f5e9")
            elif queue.get("other_count", 0):
                count_text = f"{queue['other_count']} szt."
                status = "Inna jednostka"
                color = QColor("#fff3cd")
            else:
                count_text = "0 szt."
                status = "Brak rekrutacji"
                color = QColor("#fff3cd")
            if (
                queue.get("building_available") is not False
                and not queue["count"]
                and not queue.get("other_count", 0)
            ):
                inactive_queues += 1
            self.recruitment_queue_table.setItem(
                row_index, 2, QTableWidgetItem(count_text)
            )
            queue_seconds = (
                recruitment_queue_duration_seconds(queue)
                if count_text != "—"
                else None
            )
            self.recruitment_queue_table.setItem(
                row_index,
                3,
                QTableWidgetItem(
                    f"{queue_seconds / 60:.0f} min"
                    if queue_seconds is not None
                    else "—"
                ),
            )
            status_item = QTableWidgetItem(status)
            status_item.setBackground(color)
            if (
                key in self._recruitment_pending_keys
                or legacy_key in self._recruitment_pending_keys
            ):
                status_item.setToolTip(
                    "Zlecenie wysłano. Automat nie ponowi go, dopóki "
                    "kolejka nie potwierdzi przyjęcia jednostek."
                )
            self.recruitment_queue_table.setItem(row_index, 4, status_item)

        village_count = len({row["village_id"] for row in rows})
        active_queues = len(rows) - inactive_queues
        summary_parts = [
            f"Kontrola {datetime.now():%H:%M}",
            f"{village_count} wiosek",
            f"{active_queues} aktywnych kolejek",
        ]
        if inactive_queues:
            summary_parts.append(f"{inactive_queues} bez rekrutacji")
        if unavailable_buildings:
            summary_parts.append(f"{unavailable_buildings} bez budynku")
        if self._recruitment_pending_keys:
            summary_parts.append(
                f"{len(self._recruitment_pending_keys)} zleceń do potwierdzenia"
            )
        self.recruitment_queue_status.setText(
            "  ·  ".join(summary_parts) + "  ·  następna kontrola za 3 min"
        )
        self.clear_recruitment_pending_btn.setEnabled(
            bool(self._recruitment_pending_keys)
        )

    def update_recruitment_economics(self, rows: list[dict]):
        resource_order = ("Drewno", "Glina", "Żelazo", "Zboże")
        grouped_rows: dict[str, dict] = {}
        unknown_costs = 0
        unknown_production = 0
        for row in rows:
            village_id = row.get("village_id", row["village"])
            village_row = grouped_rows.setdefault(
                village_id,
                {"name": row["village"], "resources": {}},
            )
            village_row["resources"][row["resource"]] = row
            if row["recruitment_cost"] is None:
                unknown_costs += 1
            if row["production_rate"] is None:
                unknown_production += 1

        self.recruitment_economics_table.setRowCount(len(grouped_rows))
        for row_index, village_row in enumerate(grouped_rows.values()):
            village_item = QTableWidgetItem(village_row["name"])
            village_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.recruitment_economics_table.setItem(
                row_index,
                0,
                village_item,
            )
            for column, resource in enumerate(resource_order, start=1):
                resource_row = village_row["resources"].get(resource)
                if resource_row is None:
                    continue
                details = (
                    f"Przychód  {_format_hourly_rate(resource_row['production_rate'])}\n"
                    "Rekrutacja  "
                    f"{_format_hourly_rate(resource_row['recruitment_cost'], signed=False)}\n"
                    f"Bilans  {_format_hourly_rate(resource_row['net_rate'])}"
                )
                item = QTableWidgetItem(details)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                item.setToolTip(
                    f"Przychód i koszt rekrutacji na godzinę — {resource}."
                )
                if resource_row.get("last_updated"):
                    item.setToolTip(
                        f"Ostatni odczyt przychodu: "
                        f"{resource_row['last_updated']:%Y-%m-%d %H:%M}"
                    )
                if resource_row["net_rate"] is not None:
                    item.setForeground(
                        QColor(
                            "#b42318"
                            if resource_row["net_rate"] < 0
                            else "#237a45"
                        )
                    )
                self.recruitment_economics_table.setItem(
                    row_index,
                    column,
                    item,
                )
            self.recruitment_economics_table.setRowHeight(row_index, 72)

        status = (
            "Szacunek dla zaznaczonych jednostek przy nieprzerwanym szkoleniu. "
            "Koszt jednostki mnożony jest przez liczbę jednostek możliwych "
            "do wyszkolenia w godzinę, z uwzględnieniem bazowego czasu gry, "
            "szybkości świata i efektu poziomu budynku. "
            "Jeśli kilka zaznaczonych typów korzysta z tych samych koszar "
            "lub stajni, koszt jest nieokreślony ze względu na współdzieloną kolejkę."
        )
        if unknown_costs or unknown_production:
            status += (
                f" Brak danych: koszt rekrutacji w {unknown_costs} wierszach, "
                f"przychód w {unknown_production} wierszach."
            )
        self.recruitment_economics_status.setText(status)

    def clear_recruitment_economics(self):
        self.recruitment_economics_table.setRowCount(0)
        self.recruitment_economics_status.setText(
            "Połącz z grą, aby oszacować koszt rekrutacji na godzinę."
        )

    def clear_recruitment_queues(self):
        self._recruitment_pending_keys.clear()
        self.recruitment_queue_table.setRowCount(0)
        self.clear_recruitment_pending_btn.setEnabled(False)
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
        self._milling_candidates: list[dict] = []
        self._milling_order_active = False
        self._milling_scan_active = False
        self._pending_milling_responses: dict[str, int] = {}
        self._connected_milling_controls: set[int] = set()
        self._seen_incoming_attack_ids: set[str] = set()
        self._incoming_attacks_by_key: dict[str, dict] = {}
        self._dismissed_incoming_attack_ids: set[str] = set()
        
        self.setWindowTitle(settings.app_name)
        self.resize(1280, 860)
        self.setMinimumSize(980, 680)
        self.setStyleSheet(_APP_STYLESHEET)
        
        self._create_menu_bar()
        self._createCentralWidget()
        self.recruitment_tab.auto_recruitment_checkbox.setChecked(
            settings.recruitment.automatic_enabled
        )
        self.recruitment_tab.auto_recruitment_batch_size.setValue(
            settings.recruitment.batch_size
        )
        self.recruitment_tab.auto_recruitment_threshold.setValue(
            settings.recruitment.queue_threshold
        )
        self.recruitment_tab.set_recruitment_strategy(
            settings.recruitment.queue_strategy
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
        self.recruitment_tab.auto_recruitment_batch_size.valueChanged.connect(
            self._set_recruitment_batch_size
        )
        self.recruitment_tab.auto_recruitment_threshold.valueChanged.connect(
            self._set_recruitment_queue_threshold
        )
        self.recruitment_tab.queue_strategy_combobox.currentIndexChanged.connect(
            self._set_queue_strategy
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
            else:
                self.recruitment_queue_timer.stop()
                self.resource_milling_timer.stop()
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

    def _set_recruitment_batch_size(self, batch_size: int):
        previous = self.settings.recruitment.batch_size
        self.settings.recruitment.batch_size = batch_size
        if self._save_recruitment_settings():
            return

        self.settings.recruitment.batch_size = previous
        spin_box = self.recruitment_tab.auto_recruitment_batch_size
        spin_box.blockSignals(True)
        spin_box.setValue(previous)
        spin_box.blockSignals(False)

    def _set_recruitment_queue_threshold(self, threshold: int):
        previous = self.settings.recruitment.queue_threshold
        self.settings.recruitment.queue_threshold = threshold
        if self._save_recruitment_settings():
            if (
                self.settings.recruitment.automatic_enabled
                and self.browser_session is not None
            ):
                self._check_recruitment_queues()
            return

        self.settings.recruitment.queue_threshold = previous
        spin_box = self.recruitment_tab.auto_recruitment_threshold
        spin_box.blockSignals(True)
        spin_box.setValue(previous)
        spin_box.blockSignals(False)

    def _set_village_queue_minutes(
        self,
        village_id: str,
        unit_id: str,
        minutes: int,
    ):
        village_settings = self.settings.recruitment.villages[village_id]
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

    def _set_queue_strategy(self, _index: int):
        previous = self.settings.recruitment.queue_strategy
        self.settings.recruitment.queue_strategy = (
            self.recruitment_tab.recruitment_strategy()
        )
        if not self._save_recruitment_settings():
            self.settings.recruitment.queue_strategy = previous
            self.recruitment_tab.set_recruitment_strategy(previous)
            return
        if (
            self.settings.recruitment.automatic_enabled
            and self.browser_session is not None
        ):
            self._check_recruitment_queues()

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
        for village_id, controls in (
            self.recruitment_tab.village_recruitment_controls.items()
        ):
            controls["enabled"].toggled.connect(
                lambda enabled, current_id=village_id:
                    self._set_village_recruitment_enabled(current_id, enabled)
            )
            for unit_id, spin_box in (
                self.recruitment_tab.village_queue_duration_controls
                .get(village_id, {})
                .items()
            ):
                spin_box.valueChanged.connect(
                    lambda minutes, current_id=village_id,
                    current_unit_id=unit_id:
                        self._set_village_queue_minutes(
                            current_id,
                            current_unit_id,
                            minutes,
                        )
                )
            for unit_id, checkbox in controls.items():
                if unit_id == "enabled":
                    continue
                checkbox.toggled.connect(
                    lambda enabled, current_id=village_id,
                    current_unit_id=unit_id:
                        self._set_village_recruitment_unit(
                            current_id,
                            current_unit_id,
                            enabled,
                        )
                )

    def _set_village_recruitment_enabled(
        self,
        village_id: str,
        enabled: bool,
    ):
        village_settings = self.settings.recruitment.villages[village_id]
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
        village_settings = self.settings.recruitment.villages[village_id]
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
        if self.settings.recruitment.queue_strategy != "duration":
            return self.settings.recruitment.queue_threshold
        return recruitment_queue_threshold_for_duration(
            row,
            self._recruitment_target_minutes(row),
        )

    def _recruitment_queue_is_low(self, row: dict) -> bool | None:
        """Return whether the row needs replenishing, or None if unknown."""
        if self.settings.recruitment.queue_strategy != "duration":
            return row["count"] <= self.settings.recruitment.queue_threshold
        duration = recruitment_queue_duration_seconds(row)
        threshold = self._recruitment_queue_threshold_for_row(row)
        if duration is None or threshold is None:
            return None
        return duration < self._recruitment_target_minutes(row) * 60

    def _recruitment_batch_size_for_row(self, row: dict) -> int | None:
        """Return the number of units to order for a recruitment row."""
        if self.settings.recruitment.queue_strategy != "duration":
            return self.settings.recruitment.batch_size
        return recruitment_batch_for_duration(
            row,
            self._recruitment_target_minutes(row),
            self.settings.recruitment.batch_size,
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

    def _begin_automatic_recruitment(self, rows: list[dict]):
        if (
            not self.settings.recruitment.automatic_enabled
            or self._auto_recruitment_active
        ):
            return

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
            result = self.browser_session.queue_recruitment(
                row,
                batch_size,
                queue_threshold,
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
                f"Wysłano zlecenie: {batch_size} "
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
            "insufficient_resources": "za mało surowców na całą partię",
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
            f"Version: {self.settings.version}\n\n"
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
