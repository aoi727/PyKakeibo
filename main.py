from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "kakeibo.db"
ICON_PATH = APP_DIR / "assets" / "app_icon.svg"


ACCOUNT_TYPES = {
    "cash": "現金",
    "bank": "普通預金",
    "deposit": "定期預金等",
    "pay": "Pay支払",
}


EXPENSE_CATEGORIES = [
    "食費",
    "日用品",
    "交通",
    "住まい",
    "水道光熱",
    "通信",
    "医療",
    "保険",
    "教育",
    "美容",
    "衣服",
    "趣味",
    "交際",
    "税金",
    "その他",
]


CATEGORY_COLORS = {
    "食費": "#F28C8C",
    "日用品": "#F2B36D",
    "交通": "#7AA7E8",
    "住まい": "#7EC8A4",
    "水道光熱": "#F6C85F",
    "通信": "#5FB3B3",
    "医療": "#E87AA6",
    "保険": "#7D8CC4",
    "教育": "#B990E8",
    "美容": "#F39BC3",
    "衣服": "#C49A6C",
    "趣味": "#9F86C0",
    "交際": "#EF9F76",
    "税金": "#8FA3AD",
    "その他": "#9AA3AF",
}


MEMO_USAGE_TYPES = {
    "common": "共通",
    "expense": "支出",
    "income": "収入",
    "transfer": "資金移動",
}


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    account_type: str
    opening_balance: int
    is_active: int
    balance: int = 0

    @property
    def type_label(self) -> str:
        return ACCOUNT_TYPES.get(self.account_type, self.account_type)


@dataclass(frozen=True)
class Transaction:
    id: int
    occurred_on: str
    transaction_type: str
    category: str
    from_account_name: str
    to_account_name: str
    memo: str
    amount: int


@dataclass(frozen=True)
class MemoTemplate:
    id: int
    memo: str
    usage_type: str

    @property
    def usage_label(self) -> str:
        return MEMO_USAGE_TYPES.get(self.usage_type, self.usage_type)


class KakeiboStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_schema()
        self._seed_accounts()
        self._migrate_legacy_entries()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    account_type TEXT NOT NULL,
                    opening_balance INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_on TEXT NOT NULL,
                    transaction_type TEXT NOT NULL CHECK(transaction_type IN ('expense', 'income', 'transfer')),
                    category TEXT,
                    from_account_id INTEGER,
                    to_account_id INTEGER,
                    memo TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(from_account_id) REFERENCES accounts(id),
                    FOREIGN KEY(to_account_id) REFERENCES accounts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memo_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memo TEXT NOT NULL UNIQUE,
                    usage_type TEXT NOT NULL DEFAULT 'common',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(memo_templates)").fetchall()
            }
            if "usage_type" not in columns:
                conn.execute(
                    "ALTER TABLE memo_templates ADD COLUMN usage_type TEXT NOT NULL DEFAULT 'common'"
                )

    def _seed_accounts(self) -> None:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            if count:
                return
            defaults = [
                ("現金", "cash", 0),
                ("普通預金1", "bank", 0),
                ("普通預金2", "bank", 0),
                ("定期預金等1", "deposit", 0),
                ("Pay支払1", "pay", 0),
            ]
            conn.executemany(
                "INSERT INTO accounts (name, account_type, opening_balance) VALUES (?, ?, ?)",
                defaults,
            )

    def _migrate_legacy_entries(self) -> None:
        with self._connect() as conn:
            has_entries = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
            ).fetchone()[0]
            tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            if not has_entries or tx_count:
                return

            cash_id = self.get_or_create_account("現金", "cash")
            legacy_rows = conn.execute(
                "SELECT spent_on, category, memo, amount FROM entries ORDER BY id"
            ).fetchall()
            conn.executemany(
                """
                INSERT INTO transactions (
                    occurred_on, transaction_type, category, from_account_id, to_account_id, memo, amount
                )
                VALUES (?, 'expense', ?, ?, NULL, ?, ?)
                """,
                [(row["spent_on"], row["category"], cash_id, row["memo"], row["amount"]) for row in legacy_rows],
            )

    def get_or_create_account(self, name: str, account_type: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO accounts (name, account_type, opening_balance) VALUES (?, ?, 0)",
                (name, account_type),
            )
            return int(cur.lastrowid)

    def add_account(self, name: str, account_type: str, opening_balance: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO accounts (name, account_type, opening_balance) VALUES (?, ?, ?)",
                (name, account_type, opening_balance),
            )

    def update_account(self, account_id: int, name: str, account_type: str, opening_balance: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET name = ?, account_type = ?, opening_balance = ?
                WHERE id = ?
                """,
                (name, account_type, opening_balance, account_id),
            )

    def set_account_active(self, account_id: int, is_active: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE accounts SET is_active = ? WHERE id = ?",
                (1 if is_active else 0, account_id),
            )

    def account_transaction_count(self, account_id: int) -> int:
        with self._connect() as conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM transactions
                    WHERE from_account_id = ? OR to_account_id = ?
                    """,
                    (account_id, account_id),
                ).fetchone()[0]
            )

    def delete_account(self, account_id: int) -> None:
        if self.account_transaction_count(account_id) > 0:
            raise ValueError("取引で使われている口座は削除できません。非表示にしてください。")
        with self._connect() as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def list_accounts(self, active_only: bool = True) -> list[Account]:
        where = "WHERE is_active = 1" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, name, account_type, opening_balance, is_active
                FROM accounts
                {where}
                ORDER BY
                    CASE account_type
                        WHEN 'cash' THEN 0
                        WHEN 'bank' THEN 1
                        WHEN 'deposit' THEN 2
                        WHEN 'pay' THEN 3
                        ELSE 4
                    END,
                    id
                """
            ).fetchall()
        balances = self.account_balances()
        return [
            Account(
                id=row["id"],
                name=row["name"],
                account_type=row["account_type"],
                opening_balance=row["opening_balance"],
                is_active=row["is_active"],
                balance=balances.get(row["id"], row["opening_balance"]),
            )
            for row in rows
        ]

    def account_balances(self) -> dict[int, int]:
        with self._connect() as conn:
            accounts = conn.execute("SELECT id, opening_balance FROM accounts").fetchall()
            balances = {row["id"]: int(row["opening_balance"]) for row in accounts}
            rows = conn.execute(
                "SELECT transaction_type, from_account_id, to_account_id, amount FROM transactions"
            ).fetchall()

        for row in rows:
            amount = int(row["amount"])
            tx_type = row["transaction_type"]
            from_id = row["from_account_id"]
            to_id = row["to_account_id"]
            if tx_type == "expense" and from_id:
                balances[from_id] = balances.get(from_id, 0) - amount
            elif tx_type == "income" and to_id:
                balances[to_id] = balances.get(to_id, 0) + amount
            elif tx_type == "transfer":
                if from_id:
                    balances[from_id] = balances.get(from_id, 0) - amount
                if to_id:
                    balances[to_id] = balances.get(to_id, 0) + amount
        return balances

    def add_expense(self, occurred_on: str, account_id: int, category: str, memo: str, amount: int) -> None:
        self._add_transaction(occurred_on, "expense", category, account_id, None, memo, amount)

    def add_income(self, occurred_on: str, account_id: int, category: str, memo: str, amount: int) -> None:
        self._add_transaction(occurred_on, "income", category, None, account_id, memo, amount)

    def add_transfer(self, occurred_on: str, from_account_id: int, to_account_id: int, memo: str, amount: int) -> None:
        if from_account_id == to_account_id:
            raise ValueError("移動元と移動先は別の口座を選んでください。")
        self._add_transaction(occurred_on, "transfer", "資金移動", from_account_id, to_account_id, memo, amount)

    def _add_transaction(
        self,
        occurred_on: str,
        transaction_type: str,
        category: str,
        from_account_id: int | None,
        to_account_id: int | None,
        memo: str,
        amount: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO transactions (
                    occurred_on, transaction_type, category, from_account_id, to_account_id, memo, amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (occurred_on, transaction_type, category, from_account_id, to_account_id, memo, amount),
            )

    def delete_transaction(self, transaction_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    def add_memo_template(self, memo: str, usage_type: str = "common") -> bool:
        text = memo.strip()
        if not text:
            raise ValueError("保存する摘要を入力してください。")
        if usage_type not in MEMO_USAGE_TYPES:
            raise ValueError("摘要の用途が不正です。")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO memo_templates (memo, usage_type) VALUES (?, ?)",
                (text, usage_type),
            )
            return cur.rowcount > 0

    def delete_memo_template(self, template_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memo_templates WHERE id = ?", (template_id,))

    def list_memo_template_records(self) -> list[MemoTemplate]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, memo, usage_type
                FROM memo_templates
                ORDER BY
                    CASE usage_type
                        WHEN 'common' THEN 0
                        WHEN 'expense' THEN 1
                        WHEN 'income' THEN 2
                        WHEN 'transfer' THEN 3
                        ELSE 4
                    END,
                    memo
                """
            ).fetchall()
        return [
            MemoTemplate(id=row["id"], memo=row["memo"], usage_type=row["usage_type"])
            for row in rows
        ]

    def list_memo_templates(self, usage_type: str) -> list[str]:
        return [
            template.memo
            for template in self.list_memo_template_records()
            if template.usage_type in ("common", usage_type)
        ]

    def list_transactions(self) -> list[Transaction]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.id,
                    t.occurred_on,
                    t.transaction_type,
                    COALESCE(t.category, '') AS category,
                    COALESCE(f.name, '') AS from_account_name,
                    COALESCE(ta.name, '') AS to_account_name,
                    t.memo,
                    t.amount
                FROM transactions t
                LEFT JOIN accounts f ON f.id = t.from_account_id
                LEFT JOIN accounts ta ON ta.id = t.to_account_id
                ORDER BY t.occurred_on DESC, t.id DESC
                """
            ).fetchall()
        return [Transaction(**dict(row)) for row in rows]


class SummaryCard(QFrame):
    def __init__(self, title: str, value: str, accent: str | None = None) -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("cardValue")
        if accent:
            self.setStyleSheet(
                f"""
                QFrame#summaryCard {{
                    background: #FFFFFF;
                    border: 1px solid #E5DED4;
                    border-top: 4px solid {accent};
                    border-radius: 8px;
                }}
                """
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(6)
        layout.addWidget(QLabel(title))
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class ToggleSwitch(QCheckBox):
    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.sizeHint())
        self.setText("")

    def sizeHint(self) -> QSize:
        return QSize(104, 48)

    def hitButton(self, pos) -> bool:  # type: ignore[override]
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        checked = self.isChecked()
        track = QRectF(2, 5, 100, 38)
        radius = track.height() / 2
        track_color = QColor("#00D46A") if checked else QColor("#F1F1F1")
        border_color = QColor("#16AF5E") if checked else QColor("#D3D3D3")

        painter.setPen(QPen(border_color, 1.4))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, radius, radius)

        knob_size = 42
        knob_x = 59 if checked else 3
        knob_y = 3
        shadow = QRectF(knob_x + 1, knob_y + 4, knob_size, knob_size)
        knob = QRectF(knob_x, knob_y, knob_size, knob_size)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 36))
        painter.drawEllipse(shadow)

        painter.setPen(QPen(QColor("#CFCFCF"), 1))
        painter.setBrush(QColor("#FAFAFA"))
        painter.drawEllipse(knob)

        label_color = QColor("#069D54") if checked else QColor("#B8B8B8")
        if checked:
            self._draw_on_label(painter, label_color)
        else:
            self._draw_off_label(painter, label_color)

    def _draw_on_label(self, painter: QPainter, color: QColor) -> None:
        painter.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(18, 18, 13, 13))
        painter.drawLine(36, 31, 36, 18)
        painter.drawLine(36, 18, 49, 31)
        painter.drawLine(49, 31, 49, 18)

    def _draw_off_label(self, painter: QPainter, color: QColor) -> None:
        painter.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(54, 18, 12, 12))
        for x in (72, 86):
            painter.drawLine(x, 31, x, 18)
            painter.drawLine(x, 18, x + 10, 18)
            painter.drawLine(x, 24, x + 8, 24)


class KakeiboWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = KakeiboStore(DB_PATH)
        self.accounts: list[Account] = []
        self.managed_accounts: list[Account] = []
        self.transactions: list[Transaction] = []

        self.setWindowTitle("My家計簿")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1160, 760)

        self.assets_card = SummaryCard("総資産", "¥0", "#7EC8A4")
        self.expense_card = SummaryCard("今月の支出", "¥0", "#F28C8C")
        self.income_card = SummaryCard("今月の収入", "¥0", "#7AA7E8")

        self.account_container = QWidget()
        self.account_scroll_content = QWidget()
        self.account_panel = QVBoxLayout(self.account_scroll_content)
        self.account_panel.setContentsMargins(0, 0, 0, 0)
        self.account_panel.setSpacing(8)
        self.account_scroll = QScrollArea()
        self.account_scroll.setObjectName("accountScroll")
        self.account_scroll.setWidgetResizable(True)
        self.account_scroll.setFrameShape(QFrame.NoFrame)
        self.account_scroll.setMaximumHeight(190)
        self.account_scroll.setWidget(self.account_scroll_content)
        account_layout = QVBoxLayout(self.account_container)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.addWidget(self.account_scroll)
        self.account_container.setVisible(False)
        self.asset_toggle = ToggleSwitch()
        self.asset_toggle.toggled.connect(self.account_container.setVisible)

        self.category_container = QWidget()
        self.category_panel = QVBoxLayout(self.category_container)
        self.category_panel.setContentsMargins(0, 0, 0, 0)
        self.category_panel.setSpacing(8)

        self.expense_date = self._date_input()
        self.expense_account = QComboBox()
        self.expense_category = QComboBox()
        self.expense_category.addItems(EXPENSE_CATEGORIES)
        self.expense_memo = self._memo_input("例: スーパー、電車、コーヒー")
        self.expense_amount = self._amount_input()

        self.income_date = self._date_input()
        self.income_account = QComboBox()
        self.income_category = QComboBox()
        self.income_category.addItems(["給与", "賞与", "利息", "臨時収入", "その他"])
        self.income_memo = self._memo_input("例: 給与、利息")
        self.income_amount = self._amount_input()

        self.transfer_date = self._date_input()
        self.transfer_from = QComboBox()
        self.transfer_to = QComboBox()
        self.transfer_memo = self._memo_input("例: 普通預金から定期へ")
        self.transfer_amount = self._amount_input()

        self.account_type = QComboBox()
        for key, label in ACCOUNT_TYPES.items():
            self.account_type.addItem(label, key)
        self.account_name = QLineEdit()
        self.account_name.setPlaceholderText("例: 普通預金3、Pay支払2")
        self.account_opening = self._amount_input(allow_zero=True)

        self.memo_template_input = QLineEdit()
        self.memo_template_input.setPlaceholderText("例: スーパー、家賃、カード引落")
        self.memo_template_usage = QComboBox()
        for key, label in MEMO_USAGE_TYPES.items():
            self.memo_template_usage.addItem(label, key)
        self.transaction_table = self._transaction_table()
        self.account_table = self._account_table()
        self.memo_template_table = self._memo_template_table()

        self.setCentralWidget(self._build_ui())
        self.apply_style()
        self.refresh()

    def _date_input(self) -> QDateEdit:
        widget = QDateEdit()
        widget.setCalendarPopup(True)
        widget.setDate(QDate.currentDate())
        return widget

    def _amount_input(self, allow_zero: bool = False) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(0 if allow_zero else 1, 100_000_000)
        widget.setPrefix("¥ ")
        widget.setSingleStep(1000)
        return widget

    def _memo_input(self, placeholder: str) -> QComboBox:
        widget = QComboBox()
        widget.setEditable(True)
        widget.setInsertPolicy(QComboBox.NoInsert)
        widget.lineEdit().setPlaceholderText(placeholder)
        return widget

    def _build_ui(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(18)

        hero = QHBoxLayout()
        title_group = QVBoxLayout()
        title = QLabel("My家計簿")
        title.setObjectName("appTitle")
        title_group.addWidget(title)
        hero.addLayout(title_group)
        hero.addStretch()
        layout.addLayout(hero)

        cards = QGridLayout()
        cards.setHorizontalSpacing(14)
        cards.addWidget(self.assets_card, 0, 0)
        cards.addWidget(self.expense_card, 0, 1)
        cards.addWidget(self.income_card, 0, 2)
        layout.addLayout(cards)

        content = QHBoxLayout()
        content.setSpacing(18)
        content.addWidget(self._build_left_panel(), 3)
        content.addWidget(self._build_right_panel(), 2)
        layout.addLayout(content, 1)
        return root

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        tabs = QTabWidget()
        tabs.addTab(self._expense_tab(), "支出")
        tabs.addTab(self._income_tab(), "収入")
        tabs.addTab(self._transfer_tab(), "資金移動")
        tabs.addTab(self._account_tab(), "口座管理")
        tabs.addTab(self._memo_dictionary_tab(), "摘要辞書")

        layout.addWidget(tabs)
        history_header = QHBoxLayout()
        history_label = QLabel("履歴")
        history_label.setObjectName("sectionTitle")
        delete_button = QPushButton("選択行を削除")
        delete_button.clicked.connect(self.delete_selected_transaction)
        history_header.addWidget(history_label)
        history_header.addStretch()
        history_header.addWidget(delete_button)
        layout.addLayout(history_header)
        layout.addWidget(self.transaction_table, 1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        asset_header = QHBoxLayout()
        title = QLabel("資産残高")
        title.setObjectName("smallSectionTitle")
        asset_header.addWidget(title)
        asset_header.addStretch()
        asset_header.addWidget(self.asset_toggle)

        category_title = QLabel("今月の支出内訳")
        category_title.setObjectName("sectionTitle")

        category_scroll = QScrollArea()
        category_scroll.setObjectName("categoryScroll")
        category_scroll.setWidgetResizable(True)
        category_scroll.setFrameShape(QFrame.NoFrame)
        category_scroll.setWidget(self.category_container)

        layout.addLayout(asset_header)
        layout.addWidget(self.account_container)
        layout.addSpacing(8)
        layout.addWidget(category_title)
        layout.addWidget(category_scroll, 1)
        return panel

    def _expense_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        add_button = QPushButton("＋ 支出を追加")
        add_button.clicked.connect(self.add_expense)
        save_memo_button = QPushButton("辞書へ保存")
        save_memo_button.clicked.connect(lambda: self.save_memo_template(self.expense_memo, "expense"))

        layout.addWidget(QLabel("日付"), 0, 0)
        layout.addWidget(self.expense_date, 0, 1)
        layout.addWidget(QLabel("支出元"), 0, 2)
        layout.addWidget(self.expense_account, 0, 3)
        layout.addWidget(QLabel("カテゴリ"), 1, 0)
        layout.addWidget(self.expense_category, 1, 1)
        layout.addWidget(QLabel("メモ"), 1, 2)
        layout.addWidget(self.expense_memo, 1, 3)
        layout.addWidget(save_memo_button, 1, 4)
        layout.addWidget(QLabel("金額"), 2, 0)
        layout.addWidget(self.expense_amount, 2, 1)
        layout.addWidget(add_button, 2, 3)
        return tab

    def _income_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        add_button = QPushButton("＋ 収入を追加")
        add_button.clicked.connect(self.add_income)
        save_memo_button = QPushButton("辞書へ保存")
        save_memo_button.clicked.connect(lambda: self.save_memo_template(self.income_memo, "income"))

        layout.addWidget(QLabel("日付"), 0, 0)
        layout.addWidget(self.income_date, 0, 1)
        layout.addWidget(QLabel("入金先"), 0, 2)
        layout.addWidget(self.income_account, 0, 3)
        layout.addWidget(QLabel("カテゴリ"), 1, 0)
        layout.addWidget(self.income_category, 1, 1)
        layout.addWidget(QLabel("メモ"), 1, 2)
        layout.addWidget(self.income_memo, 1, 3)
        layout.addWidget(save_memo_button, 1, 4)
        layout.addWidget(QLabel("金額"), 2, 0)
        layout.addWidget(self.income_amount, 2, 1)
        layout.addWidget(add_button, 2, 3)
        return tab

    def _transfer_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        add_button = QPushButton("⇄ 移動を記録")
        add_button.clicked.connect(self.add_transfer)
        save_memo_button = QPushButton("辞書へ保存")
        save_memo_button.clicked.connect(lambda: self.save_memo_template(self.transfer_memo, "transfer"))

        layout.addWidget(QLabel("日付"), 0, 0)
        layout.addWidget(self.transfer_date, 0, 1)
        layout.addWidget(QLabel("移動元"), 0, 2)
        layout.addWidget(self.transfer_from, 0, 3)
        layout.addWidget(QLabel("移動先"), 1, 0)
        layout.addWidget(self.transfer_to, 1, 1)
        layout.addWidget(QLabel("メモ"), 1, 2)
        layout.addWidget(self.transfer_memo, 1, 3)
        layout.addWidget(save_memo_button, 1, 4)
        layout.addWidget(QLabel("金額"), 2, 0)
        layout.addWidget(self.transfer_amount, 2, 1)
        layout.addWidget(add_button, 2, 3)
        return tab

    def _account_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QGridLayout()
        add_button = QPushButton("＋ 口座を追加")
        add_button.clicked.connect(self.add_account)
        update_button = QPushButton("選択口座を更新")
        update_button.clicked.connect(self.update_selected_account)
        visibility_button = QPushButton("表示/非表示")
        visibility_button.clicked.connect(self.toggle_selected_account_visibility)
        delete_button = QPushButton("未使用口座を削除")
        delete_button.clicked.connect(self.delete_selected_account)
        form.addWidget(QLabel("種別"), 0, 0)
        form.addWidget(self.account_type, 0, 1)
        form.addWidget(QLabel("口座名"), 0, 2)
        form.addWidget(self.account_name, 0, 3)
        form.addWidget(QLabel("開始残高"), 1, 0)
        form.addWidget(self.account_opening, 1, 1)
        form.addWidget(add_button, 1, 2)
        form.addWidget(update_button, 1, 3)
        form.addWidget(visibility_button, 2, 2)
        form.addWidget(delete_button, 2, 3)
        layout.addLayout(form)
        layout.addWidget(self.account_table)
        return tab

    def _memo_dictionary_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QGridLayout()
        add_button = QPushButton("＋ 摘要を登録")
        add_button.clicked.connect(self.add_memo_template_from_master)
        delete_button = QPushButton("選択行を削除")
        delete_button.clicked.connect(self.delete_selected_memo_template)

        form.addWidget(QLabel("摘要"), 0, 0)
        form.addWidget(self.memo_template_input, 0, 1)
        form.addWidget(QLabel("用途"), 0, 2)
        form.addWidget(self.memo_template_usage, 0, 3)
        form.addWidget(add_button, 0, 4)
        form.addWidget(delete_button, 0, 5)
        layout.addLayout(form)
        layout.addWidget(self.memo_template_table)
        return tab

    def _transaction_table(self) -> QTableWidget:
        table = QTableWidget(0, 8)
        table.setHorizontalHeaderLabels(["ID", "日付", "種類", "カテゴリ", "出金元", "入金先", "メモ", "金額"])
        table.setColumnHidden(0, True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        return table

    def _account_table(self) -> QTableWidget:
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["ID", "口座名", "種別", "開始残高", "現在残高", "表示"])
        table.setColumnHidden(0, True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        table.itemSelectionChanged.connect(self.load_selected_account)
        return table

    def _memo_template_table(self) -> QTableWidget:
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["ID", "用途", "摘要"])
        table.setColumnHidden(0, True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        return table

    def add_expense(self) -> None:
        self.store.add_expense(
            self.expense_date.date().toString("yyyy-MM-dd"),
            self.expense_account.currentData(),
            self.expense_category.currentText(),
            self.expense_memo.currentText().strip() or "メモなし",
            self.expense_amount.value(),
        )
        self.expense_memo.clearEditText()
        self.expense_amount.setValue(1)
        self.refresh()

    def add_income(self) -> None:
        self.store.add_income(
            self.income_date.date().toString("yyyy-MM-dd"),
            self.income_account.currentData(),
            self.income_category.currentText(),
            self.income_memo.currentText().strip() or "メモなし",
            self.income_amount.value(),
        )
        self.income_memo.clearEditText()
        self.income_amount.setValue(1)
        self.refresh()

    def add_transfer(self) -> None:
        try:
            self.store.add_transfer(
                self.transfer_date.date().toString("yyyy-MM-dd"),
                self.transfer_from.currentData(),
                self.transfer_to.currentData(),
                self.transfer_memo.currentText().strip() or "資金移動",
                self.transfer_amount.value(),
            )
        except ValueError as exc:
            QMessageBox.information(self, "資金移動", str(exc))
            return
        self.transfer_memo.clearEditText()
        self.transfer_amount.setValue(1)
        self.refresh()

    def save_memo_template(self, memo_input: QComboBox, usage_type: str) -> None:
        try:
            added = self.store.add_memo_template(memo_input.currentText(), usage_type)
        except ValueError as exc:
            QMessageBox.information(self, "摘要辞書", str(exc))
            return
        self._refresh_memo_combos()
        self._render_memo_templates()
        message = "摘要辞書へ保存しました。" if added else "その摘要は既に辞書にあります。"
        QMessageBox.information(self, "摘要辞書", message)

    def add_memo_template_from_master(self) -> None:
        try:
            added = self.store.add_memo_template(
                self.memo_template_input.text(),
                self.memo_template_usage.currentData(),
            )
        except ValueError as exc:
            QMessageBox.information(self, "摘要辞書", str(exc))
            return
        self.memo_template_input.clear()
        self._refresh_memo_combos()
        self._render_memo_templates()
        if not added:
            QMessageBox.information(self, "摘要辞書", "その摘要は既に辞書にあります。")

    def delete_selected_memo_template(self) -> None:
        row = self.memo_template_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "摘要辞書", "削除する摘要を選択してください。")
            return
        template_id = int(self.memo_template_table.item(row, 0).text())
        self.store.delete_memo_template(template_id)
        self._refresh_memo_combos()
        self._render_memo_templates()

    def add_account(self) -> None:
        name = self.account_name.text().strip()
        if not name:
            QMessageBox.information(self, "口座追加", "口座名を入力してください。")
            return
        try:
            self.store.add_account(name, self.account_type.currentData(), self.account_opening.value())
        except sqlite3.IntegrityError:
            QMessageBox.information(self, "口座追加", "同じ名前の口座が既にあります。")
            return
        except ValueError as exc:
            QMessageBox.information(self, "口座追加", str(exc))
            return
        self.account_name.clear()
        self.account_opening.setValue(0)
        self.refresh()

    def selected_account_id(self) -> int | None:
        row = self.account_table.currentRow()
        if row < 0:
            return None
        item = self.account_table.item(row, 0)
        return int(item.text()) if item else None

    def selected_account(self) -> Account | None:
        account_id = self.selected_account_id()
        if account_id is None:
            return None
        return next(
            (account for account in self.managed_accounts if account.id == account_id),
            None,
        )

    def load_selected_account(self) -> None:
        account = self.selected_account()
        if account is None:
            return
        self.account_name.setText(account.name)
        self.account_opening.setValue(account.opening_balance)
        index = self.account_type.findData(account.account_type)
        if index >= 0:
            self.account_type.setCurrentIndex(index)

    def update_selected_account(self) -> None:
        account_id = self.selected_account_id()
        if account_id is None:
            QMessageBox.information(self, "口座編集", "編集する口座を選択してください。")
            return
        name = self.account_name.text().strip()
        if not name:
            QMessageBox.information(self, "口座編集", "口座名を入力してください。")
            return
        try:
            self.store.update_account(
                account_id,
                name,
                self.account_type.currentData(),
                self.account_opening.value(),
            )
        except sqlite3.IntegrityError:
            QMessageBox.information(self, "口座編集", "同じ名前の口座が既にあります。")
            return
        self.refresh()

    def toggle_selected_account_visibility(self) -> None:
        account = self.selected_account()
        if account is None:
            QMessageBox.information(self, "口座表示", "表示/非表示を切り替える口座を選択してください。")
            return
        self.store.set_account_active(account.id, not bool(account.is_active))
        self.refresh()

    def delete_selected_account(self) -> None:
        account = self.selected_account()
        if account is None:
            QMessageBox.information(self, "口座削除", "削除する口座を選択してください。")
            return
        if self.store.account_transaction_count(account.id) > 0:
            QMessageBox.information(self, "口座削除", "取引で使われている口座は削除できません。非表示にしてください。")
            return
        reply = QMessageBox.question(
            self,
            "口座削除",
            f"{account.name} を削除しますか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.store.delete_account(account.id)
        self.account_name.clear()
        self.account_opening.setValue(0)
        self.refresh()

    def delete_selected_transaction(self) -> None:
        row = self.transaction_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "削除", "削除する取引を選択してください。")
            return
        transaction_id = int(self.transaction_table.item(row, 0).text())
        self.store.delete_transaction(transaction_id)
        self.refresh()

    def refresh(self) -> None:
        self.accounts = self.store.list_accounts()
        self.managed_accounts = self.store.list_accounts(active_only=False)
        self.transactions = self.store.list_transactions()
        self._refresh_account_combos()
        self._refresh_memo_combos()
        self._render_transactions()
        self._render_accounts()
        self._render_memo_templates()
        self._render_account_panel()
        self._render_summary()

    def _refresh_account_combos(self) -> None:
        combos = [self.expense_account, self.income_account, self.transfer_from, self.transfer_to]
        current_values = {combo: combo.currentData() for combo in combos}
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
            for account in self.accounts:
                combo.addItem(f"{account.name}（¥{account.balance:,}）", account.id)
            previous = current_values[combo]
            if previous is not None:
                index = combo.findData(previous)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)
        if self.transfer_to.count() > 1 and self.transfer_to.currentIndex() == self.transfer_from.currentIndex():
            self.transfer_to.setCurrentIndex(1)

    def _refresh_memo_combos(self) -> None:
        combo_map = {
            self.expense_memo: "expense",
            self.income_memo: "income",
            self.transfer_memo: "transfer",
        }
        for combo, usage_type in combo_map.items():
            templates = self.store.list_memo_templates(usage_type)
            current_text = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(templates)
            combo.setEditText(current_text)
            combo.blockSignals(False)

    def _render_summary(self) -> None:
        this_month = date.today().strftime("%Y-%m")
        month_transactions = [tx for tx in self.transactions if tx.occurred_on.startswith(this_month)]
        expense_total = sum(tx.amount for tx in month_transactions if tx.transaction_type == "expense")
        income_total = sum(tx.amount for tx in month_transactions if tx.transaction_type == "income")
        total_assets = sum(account.balance for account in self.accounts)

        by_category = {category: 0 for category in EXPENSE_CATEGORIES}
        for tx in month_transactions:
            if tx.transaction_type == "expense":
                by_category[tx.category] = by_category.get(tx.category, 0) + tx.amount
        self.assets_card.set_value(f"¥{total_assets:,}")
        self.expense_card.set_value(f"¥{expense_total:,}")
        self.income_card.set_value(f"¥{income_total:,}")
        self._render_category_breakdown(by_category, expense_total)

    def _render_transactions(self) -> None:
        labels = {"expense": "支出", "income": "収入", "transfer": "移動"}
        self.transaction_table.setRowCount(len(self.transactions))
        for row, tx in enumerate(self.transactions):
            values = [
                str(tx.id),
                tx.occurred_on,
                labels.get(tx.transaction_type, tx.transaction_type),
                tx.category,
                tx.from_account_name,
                tx.to_account_name,
                tx.memo,
                f"¥{tx.amount:,}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 7:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.transaction_table.setItem(row, column, item)

    def _render_accounts(self) -> None:
        current_id = self.selected_account_id()
        self.account_table.blockSignals(True)
        self.account_table.setRowCount(len(self.managed_accounts))
        selected_row = -1
        for row, account in enumerate(self.managed_accounts):
            values = [
                str(account.id),
                account.name,
                account.type_label,
                f"¥{account.opening_balance:,}",
                f"¥{account.balance:,}",
                "表示" if account.is_active else "非表示",
            ]
            if account.id == current_id:
                selected_row = row
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.account_table.setItem(row, column, item)
        self.account_table.blockSignals(False)
        if selected_row >= 0:
            self.account_table.selectRow(selected_row)

    def _render_memo_templates(self) -> None:
        templates = self.store.list_memo_template_records()
        self.memo_template_table.setRowCount(len(templates))
        for row, template in enumerate(templates):
            values = [str(template.id), template.usage_label, template.memo]
            for column, value in enumerate(values):
                self.memo_template_table.setItem(row, column, QTableWidgetItem(value))

    def _render_account_panel(self) -> None:
        while self.account_panel.count():
            item = self.account_panel.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for account in self.accounts:
            self.account_panel.addWidget(AccountBalanceRow(account))

    def _render_category_breakdown(self, by_category: dict[str, int], total: int) -> None:
        while self.category_panel.count():
            item = self.category_panel.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for category in EXPENSE_CATEGORIES:
            amount = by_category.get(category, 0)
            ratio = 0 if total == 0 else amount / total
            self.category_panel.addWidget(CategoryBar(category, amount, ratio, CATEGORY_COLORS[category]))

    def apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #F7F4EF;
                color: #263238;
                font-family: "Yu Gothic UI", "Meiryo", sans-serif;
                font-size: 14px;
            }
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E5DED4;
                border-radius: 8px;
            }
            #panel {
                border: 1px solid #DED6CC;
            }
            #appTitle {
                font-size: 34px;
                font-weight: 800;
                color: #25324A;
                background: transparent;
            }
            #subtitle {
                color: #68717A;
                background: transparent;
            }
            #sectionTitle {
                font-size: 18px;
                font-weight: 700;
                background: transparent;
            }
            #smallSectionTitle {
                font-size: 13px;
                font-weight: 700;
                color: #68717A;
                background: transparent;
            }
            #cardValue {
                font-size: 26px;
                font-weight: 800;
                color: #25324A;
                background: transparent;
            }
            QLabel {
                background: transparent;
            }
            QTabWidget::pane {
                border: 1px solid #E5DED4;
                border-radius: 8px;
                background: #FFFFFF;
            }
            QTabBar::tab {
                background: #EFE8DF;
                border: 1px solid #DED6CC;
                padding: 8px 14px;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                color: #25324A;
                font-weight: 700;
            }
            QLineEdit, QSpinBox, QComboBox, QDateEdit {
                background: #FBFAF7;
                border: 1px solid #D6CDC2;
                border-radius: 7px;
                padding: 8px 10px;
                min-height: 20px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus {
                border: 1px solid #7AA7E8;
            }
            QPushButton {
                background: #25324A;
                color: #FFFFFF;
                border: none;
                border-radius: 7px;
                padding: 9px 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #334465;
            }
            QScrollArea#categoryScroll, QScrollArea#accountScroll {
                background: transparent;
                border: none;
            }
            QScrollArea#categoryScroll > QWidget > QWidget,
            QScrollArea#accountScroll > QWidget > QWidget {
                background: transparent;
            }
            QTableWidget {
                background: #FFFFFF;
                border: 1px solid #E5DED4;
                border-radius: 8px;
                gridline-color: #EFE8DF;
                selection-background-color: #DDEBFF;
                selection-color: #25324A;
            }
            QHeaderView::section {
                background: #F0E8DE;
                color: #3D4651;
                border: none;
                padding: 8px;
                font-weight: 700;
            }
            """
        )


class AccountBalanceRow(QWidget):
    def __init__(self, account: Account) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        name = QLabel(f"{account.name} / {account.type_label}")
        value = QLabel(f"¥{account.balance:,}")
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(name)
        layout.addWidget(value)


class CategoryBar(QWidget):
    def __init__(self, category: str, amount: int, ratio: float, color: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        row = QHBoxLayout()
        name = QLabel(category)
        value = QLabel(f"¥{amount:,}")
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(name)
        row.addWidget(value)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(ratio * 100))
        bar.setTextVisible(False)
        bar.setFixedHeight(12)
        bar.setStyleSheet(
            f"""
            QProgressBar {{
                background: #EFE8DF;
                border: none;
                border-radius: 6px;
            }}
            QProgressBar::chunk {{
                background: {color};
                border-radius: 6px;
            }}
            """
        )

        layout.addLayout(row)
        layout.addWidget(bar)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("My家計簿")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = KakeiboWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
