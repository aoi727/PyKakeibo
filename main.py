from __future__ import annotations

import sqlite3
import sys
from datetime import date
from html import escape
from pathlib import Path

from PySide6.QtCore import QByteArray, QDate, QSettings, QSize, Qt
from PySide6.QtGui import QIcon, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
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
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from calculator_dialog import CalculatorDialog
from models import (
    ACCOUNT_TYPES,
    CATEGORY_COLORS,
    CATEGORY_FALLBACK_COLORS,
    CATEGORY_TYPES,
    MEMO_USAGE_TYPES,
    Account,
    CategoryMaster,
    Transaction,
    TrialBalanceRow,
)
from store import KakeiboStore


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "kakeibo.db"
ICON_PATH = APP_DIR / "assets" / "app_icon.svg"


class SummaryCard(QFrame):
    def __init__(self, title: str, value: str, accent: str | None = None) -> None:
        super().__init__()
        self.setObjectName("summaryCard")
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("cardValue")
        self.value_label.setAlignment(Qt.AlignCenter)
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
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(2)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class KakeiboWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = KakeiboStore(DB_PATH)
        self.accounts: list[Account] = []
        self.managed_accounts: list[Account] = []
        self.transactions: list[Transaction] = []
        self.expense_categories: list[str] = []
        self.income_categories: list[str] = []
        self.category_records: list[CategoryMaster] = []
        self.settings = QSettings("Honlabo", "PyKakeibo")
        self.editing_transaction_id: int | None = None
        self.editing_transaction_type: str | None = None
        self.return_to_ledger_after_transaction = False
        self.selected_month = date.today().replace(day=1)

        self.setWindowTitle("家計管理")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1160, 760)

        self.assets_card = SummaryCard("純資産", "¥0", "#7EC8A4")
        self.expense_card = SummaryCard("今月の支出", "¥0", "#F28C8C")
        self.income_card = SummaryCard("今月の収入", "¥0", "#7AA7E8")
        self.month_label = QLabel()
        self.month_label.setObjectName("monthLabel")
        self.history_label = QLabel()
        self.history_label.setObjectName("sectionTitle")
        self.category_title = QLabel()
        self.category_title.setObjectName("sectionTitle")
        self.ledger_account = QComboBox()
        self.ledger_account.currentIndexChanged.connect(lambda _index: self._render_account_ledger())
        self.ledger_table = self._ledger_table()
        self.category_ledger_type = QComboBox()
        for key, label in CATEGORY_TYPES.items():
            self.category_ledger_type.addItem(label, key)
        self.category_ledger_type.currentIndexChanged.connect(
            lambda _index: self._refresh_category_ledger_category_combo()
        )
        self.category_ledger_category = QComboBox()
        self.category_ledger_category.currentIndexChanged.connect(lambda _index: self._render_category_ledger())
        self.category_ledger_table = self._category_ledger_table()
        self.trial_balance_table = self._trial_balance_table()

        self.account_container = QWidget()
        self.account_scroll_content = QWidget()
        self.account_panel = QVBoxLayout(self.account_scroll_content)
        self.account_panel.setContentsMargins(0, 0, 0, 0)
        self.account_panel.setSpacing(8)
        self.account_scroll = QScrollArea()
        self.account_scroll.setObjectName("accountScroll")
        self.account_scroll.setWidgetResizable(True)
        self.account_scroll.setFrameShape(QFrame.NoFrame)
        self.account_scroll.setMinimumHeight(1)
        self.account_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.account_scroll.setWidget(self.account_scroll_content)
        account_layout = QVBoxLayout(self.account_container)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.addWidget(self.account_scroll)

        self.category_container = QWidget()
        self.category_panel = QVBoxLayout(self.category_container)
        self.category_panel.setContentsMargins(0, 0, 0, 0)
        self.category_panel.setSpacing(8)

        self.expense_date = self._date_input()
        self.expense_account = QComboBox()
        self.expense_category = QComboBox()
        self.expense_memo = self._memo_input("例: スーパー、電車、コーヒー")
        self.expense_amount = self._amount_input()

        self.income_date = self._date_input()
        self.income_account = QComboBox()
        self.income_category = QComboBox()
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
        self.account_name.setPlaceholderText("例: 普通預金3、Pay支払2、クレジットカード")
        self.account_opening = self._balance_input()

        self.category_type = QComboBox()
        for key, label in CATEGORY_TYPES.items():
            self.category_type.addItem(label, key)
        self.category_name = QLineEdit()
        self.category_name.setPlaceholderText("例: 食費、給与、雑収入")

        self.memo_template_input = QLineEdit()
        self.memo_template_input.setPlaceholderText("例: スーパー、家賃、カード引落")
        self.memo_template_usage = QComboBox()
        for key, label in MEMO_USAGE_TYPES.items():
            self.memo_template_usage.addItem(label, key)
        self.transaction_table = self._transaction_table()
        self.transaction_table.cellDoubleClicked.connect(
            lambda _row, _column: self.load_selected_transaction_for_edit()
        )
        self.account_table = self._account_table()
        self.category_table = self._category_table()
        self.memo_template_table = self._memo_template_table()

        self.setCentralWidget(self._build_ui())
        self.apply_style()
        self.restore_window_settings()
        self.refresh()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.save_window_settings()
        super().closeEvent(event)

    def save_window_settings(self) -> None:
        self.settings.setValue("window/size", self.size())
        self.settings.setValue("splitter/main", self.main_splitter.saveState())
        self.settings.setValue("splitter/left", self.left_splitter.saveState())
        self.settings.setValue("splitter/right", self.right_splitter.saveState())
        self.settings.sync()

    def restore_window_settings(self) -> None:
        saved_size = self.settings.value("window/size")
        if not isinstance(saved_size, QSize):
            return

        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available_size = screen.availableGeometry().size()
        if saved_size.width() > available_size.width() or saved_size.height() > available_size.height():
            return

        self.resize(saved_size)
        self._restore_splitter_state(self.main_splitter, "splitter/main")
        self._restore_splitter_state(self.left_splitter, "splitter/left")
        self._restore_splitter_state(self.right_splitter, "splitter/right")

    def _restore_splitter_state(self, splitter: QSplitter, key: str) -> None:
        state = self.settings.value(key)
        if isinstance(state, QByteArray):
            splitter.restoreState(state)
        elif isinstance(state, bytes):
            splitter.restoreState(QByteArray(state))

    def _date_input(self) -> QDateEdit:
        widget = QDateEdit()
        widget.setCalendarPopup(True)
        widget.setDate(QDate.currentDate())
        return widget

    def _amount_input(self, allow_zero: bool = True) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(0 if allow_zero else 1, 100_000_000)
        widget.setPrefix("¥ ")
        widget.setSingleStep(1000)
        return widget

    def _amount_calculator_layout(self, amount_input: QSpinBox) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        calculator_button = QPushButton("電卓")
        calculator_button.setFixedWidth(64)
        calculator_button.clicked.connect(lambda: self.open_amount_calculator(amount_input))
        layout.addWidget(amount_input)
        layout.addWidget(calculator_button)
        return layout

    def open_amount_calculator(self, amount_input: QSpinBox) -> None:
        dialog = CalculatorDialog(amount_input.value(), self)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.result_amount < amount_input.minimum() or dialog.result_amount > amount_input.maximum():
            QMessageBox.information(self, "電卓", "計算結果が入力可能な金額の範囲外です。")
            return
        amount_input.setValue(dialog.result_amount)

    def confirm_positive_transaction_amount(self, amount: int, title: str) -> bool:
        if amount > 0:
            return True
        QMessageBox.information(self, title, "取引金額は1円以上で入力してください。")
        return False

    def _balance_input(self) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(-100_000_000, 100_000_000)
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
        title = QLabel("家計管理")
        title.setObjectName("appTitle")
        hero.addWidget(title)
        hero.addSpacing(8)
        for card in (self.assets_card, self.expense_card, self.income_card):
            card.setFixedWidth(275)
            card.setFixedHeight(74)
            hero.addWidget(card)
        hero.addStretch()
        previous_month_button = QPushButton("前月")
        previous_month_button.clicked.connect(self.show_previous_month)
        current_month_button = QPushButton("今月")
        current_month_button.clicked.connect(self.show_current_month)
        next_month_button = QPushButton("翌月")
        next_month_button.clicked.connect(self.show_next_month)
        hero.addWidget(current_month_button)
        hero.addWidget(previous_month_button)
        hero.addWidget(self.month_label)
        hero.addWidget(next_month_button)
        layout.addLayout(hero)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.addWidget(self._build_left_panel())
        self.main_splitter.addWidget(self._build_right_panel())
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([700, 430])
        layout.addWidget(self.main_splitter, 1)
        return root

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        self.transaction_tabs = QTabWidget()
        self.transaction_tabs.addTab(self._expense_tab(), "支出")
        self.transaction_tabs.addTab(self._income_tab(), "収入")
        self.transaction_tabs.addTab(self._transfer_tab(), "資金移動")
        self.transaction_tabs.addTab(self._ledger_tab(), "口座元帳")
        self.transaction_tabs.addTab(self._category_ledger_tab(), "カテゴリ元帳")
        self.transaction_tabs.addTab(self._trial_balance_tab(), "試算表")
        self.transaction_tabs.addTab(self._account_tab(), "口座管理")
        self.transaction_tabs.addTab(self._category_tab(), "カテゴリ管理")
        self.transaction_tabs.addTab(self._memo_dictionary_tab(), "摘要辞書")

        history_panel = QWidget()
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(10)
        history_header = QHBoxLayout()
        edit_button = QPushButton("選択取引を編集")
        edit_button.clicked.connect(self.load_selected_transaction_for_edit)
        delete_button = QPushButton("選択行を削除")
        delete_button.setObjectName("deleteButton")
        delete_button.clicked.connect(self.delete_selected_transaction)
        history_header.addWidget(self.history_label)
        history_header.addStretch()
        history_header.addWidget(edit_button)
        history_header.addWidget(delete_button)
        history_layout.addLayout(history_header)
        history_layout.addWidget(self.transaction_table)

        self.left_splitter = QSplitter(Qt.Vertical)
        self.left_splitter.setObjectName("leftSplitter")
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.addWidget(self.transaction_tabs)
        self.left_splitter.addWidget(history_panel)
        self.left_splitter.setStretchFactor(0, 1)
        self.left_splitter.setStretchFactor(1, 2)
        self.left_splitter.setSizes([260, 420])
        layout.addWidget(self.left_splitter, 1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        asset_section = QWidget()
        asset_layout = QVBoxLayout(asset_section)
        asset_layout.setContentsMargins(0, 0, 0, 0)
        asset_layout.setSpacing(10)

        asset_header = QHBoxLayout()
        title = QLabel("口座残高")
        title.setObjectName("sectionTitle")
        asset_header.addWidget(title)
        asset_header.addStretch()

        category_section = QWidget()
        category_layout = QVBoxLayout(category_section)
        category_layout.setContentsMargins(0, 0, 0, 0)
        category_layout.setSpacing(10)

        category_scroll = QScrollArea()
        category_scroll.setObjectName("categoryScroll")
        category_scroll.setWidgetResizable(True)
        category_scroll.setFrameShape(QFrame.NoFrame)
        category_scroll.setWidget(self.category_container)

        asset_layout.addLayout(asset_header)
        asset_layout.addWidget(self.account_container, 1)

        category_layout.addWidget(self.category_title)
        category_layout.addWidget(category_scroll, 1)

        self.right_splitter = QSplitter(Qt.Vertical)
        self.right_splitter.setObjectName("rightSplitter")
        self.right_splitter.setChildrenCollapsible(False)
        self.right_splitter.addWidget(asset_section)
        self.right_splitter.addWidget(category_section)
        self.right_splitter.setStretchFactor(0, 1)
        self.right_splitter.setStretchFactor(1, 2)
        self.right_splitter.setSizes([220, 420])
        layout.addWidget(self.right_splitter, 1)
        return panel

    def _expense_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        self.expense_add_button = QPushButton("＋ 支出を追加")
        self.expense_add_button.clicked.connect(self.add_expense)
        self.expense_update_button = QPushButton("更新の確定")
        self.expense_update_button.clicked.connect(self.update_editing_transaction)
        self.expense_cancel_edit_button = QPushButton("編集取りやめ")
        self.expense_cancel_edit_button.clicked.connect(self.cancel_transaction_edit)
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
        layout.addLayout(self._amount_calculator_layout(self.expense_amount), 2, 1)
        layout.addWidget(self.expense_add_button, 2, 3)
        layout.addWidget(self.expense_update_button, 2, 4)
        layout.addWidget(self.expense_cancel_edit_button, 2, 5)
        return tab

    def _income_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        self.income_add_button = QPushButton("＋ 収入を追加")
        self.income_add_button.clicked.connect(self.add_income)
        self.income_update_button = QPushButton("更新の確定")
        self.income_update_button.clicked.connect(self.update_editing_transaction)
        self.income_cancel_edit_button = QPushButton("編集取りやめ")
        self.income_cancel_edit_button.clicked.connect(self.cancel_transaction_edit)
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
        layout.addLayout(self._amount_calculator_layout(self.income_amount), 2, 1)
        layout.addWidget(self.income_add_button, 2, 3)
        layout.addWidget(self.income_update_button, 2, 4)
        layout.addWidget(self.income_cancel_edit_button, 2, 5)
        return tab

    def _transfer_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        self.transfer_add_button = QPushButton("⇄ 移動を記録")
        self.transfer_add_button.clicked.connect(self.add_transfer)
        self.transfer_update_button = QPushButton("更新の確定")
        self.transfer_update_button.clicked.connect(self.update_editing_transaction)
        self.transfer_cancel_edit_button = QPushButton("編集取りやめ")
        self.transfer_cancel_edit_button.clicked.connect(self.cancel_transaction_edit)
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
        layout.addLayout(self._amount_calculator_layout(self.transfer_amount), 2, 1)
        layout.addWidget(self.transfer_add_button, 2, 3)
        layout.addWidget(self.transfer_update_button, 2, 4)
        layout.addWidget(self.transfer_cancel_edit_button, 2, 5)
        return tab

    def _ledger_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("口座"))
        controls.addWidget(self.ledger_account)
        controls.addStretch()
        add_button = QPushButton("追加")
        add_button.clicked.connect(self.add_transaction_from_ledger)
        edit_button = QPushButton("変更")
        edit_button.clicked.connect(self.load_selected_ledger_transaction_for_edit)
        delete_button = QPushButton("削除")
        delete_button.setObjectName("deleteButton")
        delete_button.clicked.connect(self.delete_selected_ledger_transaction)
        export_button = QPushButton("PDF保存")
        export_button.clicked.connect(self.export_ledger_pdf)
        controls.addWidget(add_button)
        controls.addWidget(edit_button)
        controls.addWidget(delete_button)
        controls.addWidget(export_button)
        layout.addLayout(controls)
        layout.addWidget(self.ledger_table)
        return tab

    def _category_ledger_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("種別"))
        controls.addWidget(self.category_ledger_type)
        controls.addWidget(QLabel("カテゴリ"))
        controls.addWidget(self.category_ledger_category)
        controls.addStretch()
        export_button = QPushButton("PDF保存")
        export_button.clicked.connect(self.export_category_ledger_pdf)
        controls.addWidget(export_button)
        layout.addLayout(controls)
        layout.addWidget(self.category_ledger_table)
        return tab

    def _trial_balance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        controls.addStretch()
        export_button = QPushButton("PDF保存")
        export_button.clicked.connect(self.export_trial_balance_pdf)
        controls.addWidget(export_button)
        layout.addLayout(controls)
        layout.addWidget(self.trial_balance_table)
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
        up_button = QPushButton("上へ")
        up_button.clicked.connect(self.move_selected_account_up)
        down_button = QPushButton("下へ")
        down_button.clicked.connect(self.move_selected_account_down)
        delete_button = QPushButton("未使用口座を削除")
        delete_button.setObjectName("deleteButton")
        delete_button.clicked.connect(self.delete_selected_account)
        for button in (
            add_button,
            update_button,
            visibility_button,
            up_button,
            down_button,
            delete_button,
        ):
            button.setFixedSize(160, 38)
        form.addWidget(QLabel("種別"), 0, 0)
        form.addWidget(self.account_type, 0, 1)
        form.addWidget(QLabel("口座名"), 0, 2)
        form.addWidget(self.account_name, 0, 3)
        form.addWidget(QLabel("開始残高"), 0, 4)
        form.addWidget(self.account_opening, 0, 5)
        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(update_button)
        button_row.addWidget(visibility_button)
        button_row.addWidget(delete_button)
        button_row.addWidget(up_button)
        button_row.addWidget(down_button)
        button_row.addStretch()
        form.addLayout(button_row, 1, 0, 1, 6)
        layout.addLayout(form)
        layout.addWidget(self.account_table)
        return tab

    def _category_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QGridLayout()
        add_button = QPushButton("＋ カテゴリを追加")
        add_button.clicked.connect(self.add_category_from_master)
        update_button = QPushButton("選択カテゴリを更新")
        update_button.clicked.connect(self.update_selected_category)
        up_button = QPushButton("上へ")
        up_button.clicked.connect(self.move_selected_category_up)
        down_button = QPushButton("下へ")
        down_button.clicked.connect(self.move_selected_category_down)
        delete_button = QPushButton("未使用カテゴリを削除")
        delete_button.setObjectName("deleteButton")
        delete_button.clicked.connect(self.delete_selected_category)

        form.addWidget(QLabel("種別"), 0, 0)
        form.addWidget(self.category_type, 0, 1)
        form.addWidget(QLabel("カテゴリ名"), 0, 2)
        form.addWidget(self.category_name, 0, 3)
        form.addWidget(add_button, 0, 4)
        form.addWidget(update_button, 0, 5)
        form.addWidget(up_button, 1, 0)
        form.addWidget(down_button, 1, 1)
        form.addWidget(delete_button, 1, 5)
        layout.addLayout(form)
        layout.addWidget(self.category_table)
        return tab

    def _memo_dictionary_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QGridLayout()
        add_button = QPushButton("＋ 摘要を登録")
        add_button.clicked.connect(self.add_memo_template_from_master)
        delete_button = QPushButton("選択行を削除")
        delete_button.setObjectName("deleteButton")
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

    def _ledger_table(self) -> QTableWidget:
        table = QTableWidget(0, 8)
        table.setHorizontalHeaderLabels(["ID", "日付", "種類", "内容", "メモ", "入金", "出金", "残高"])
        table.setColumnHidden(0, True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        return table

    def _category_ledger_table(self) -> QTableWidget:
        table = QTableWidget(0, 8)
        table.setHorizontalHeaderLabels(["ID", "日付", "種類", "カテゴリ", "口座", "メモ", "金額", "累計"])
        table.setColumnHidden(0, True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        return table

    def _trial_balance_table(self) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["区分", "科目", "借方", "貸方"])
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        return table

    def _category_table(self) -> QTableWidget:
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["ID", "種別", "カテゴリ名"])
        table.setColumnHidden(0, True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.itemSelectionChanged.connect(self.load_selected_category)
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

    def confirm_registration_date(self, target_date: QDate) -> bool:
        today = QDate.currentDate()
        target_days = target_date.toJulianDay()
        future_days = today.addMonths(1).toJulianDay()
        past_days = today.addMonths(-6).toJulianDay()
        if target_days < future_days and target_days > past_days:
            return True

        direction = "未来" if target_days >= future_days else "過去"
        reply = QMessageBox.question(
            self,
            "日付確認",
            (
                f"登録日が本日から{direction}に大きく離れています。\n\n"
                f"登録日: {target_date.toString('yyyy年M月d日')}\n"
                "この日付で登録してよろしいですか？"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def add_expense(self) -> None:
        if self.editing_transaction_id is not None:
            QMessageBox.information(self, "取引編集", "編集中は新規追加できません。")
            return
        if not self.confirm_positive_transaction_amount(self.expense_amount.value(), "支出"):
            return
        if not self.confirm_registration_date(self.expense_date.date()):
            return
        self.store.add_expense(
            self.expense_date.date().toString("yyyy-MM-dd"),
            self.expense_account.currentData(),
            self.expense_category.currentText(),
            self.expense_memo.currentText().strip() or "メモなし",
            self.expense_amount.value(),
        )
        self.expense_memo.clearEditText()
        self.expense_amount.setValue(0)
        self.finish_transaction_entry()

    def add_income(self) -> None:
        if self.editing_transaction_id is not None:
            QMessageBox.information(self, "取引編集", "編集中は新規追加できません。")
            return
        if not self.confirm_positive_transaction_amount(self.income_amount.value(), "収入"):
            return
        if not self.confirm_registration_date(self.income_date.date()):
            return
        self.store.add_income(
            self.income_date.date().toString("yyyy-MM-dd"),
            self.income_account.currentData(),
            self.income_category.currentText(),
            self.income_memo.currentText().strip() or "メモなし",
            self.income_amount.value(),
        )
        self.income_memo.clearEditText()
        self.income_amount.setValue(0)
        self.finish_transaction_entry()

    def add_transfer(self) -> None:
        if self.editing_transaction_id is not None:
            QMessageBox.information(self, "取引編集", "編集中は新規追加できません。")
            return
        if not self.confirm_positive_transaction_amount(self.transfer_amount.value(), "資金移動"):
            return
        if not self.confirm_registration_date(self.transfer_date.date()):
            return
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
        self.transfer_amount.setValue(0)
        self.finish_transaction_entry()

    def finish_transaction_entry(self) -> None:
        return_to_ledger = self.return_to_ledger_after_transaction
        self.return_to_ledger_after_transaction = False
        self.refresh()
        if return_to_ledger:
            self.transaction_tabs.setCurrentIndex(3)

    def add_transaction_from_ledger(self) -> None:
        if self.editing_transaction_id is not None:
            QMessageBox.information(self, "口座元帳", "取引編集中は新規追加できません。")
            return
        account_id = self.ledger_account.currentData()
        if account_id is None:
            QMessageBox.information(self, "口座元帳", "取引を追加する口座を選択してください。")
            return

        ledger_date = QDate(self.selected_month.year, self.selected_month.month, 1)
        self.return_to_ledger_after_transaction = True

        self.expense_date.setDate(ledger_date)
        self._set_combo_data(self.expense_account, account_id)
        self.expense_memo.clearEditText()
        self.expense_amount.setValue(0)

        self.income_date.setDate(ledger_date)
        self._set_combo_data(self.income_account, account_id)
        self.income_memo.clearEditText()
        self.income_amount.setValue(0)

        self.transfer_date.setDate(ledger_date)
        self._set_combo_data(self.transfer_from, account_id)
        if self.transfer_to.currentData() == account_id:
            for index in range(self.transfer_to.count()):
                if self.transfer_to.itemData(index) != account_id:
                    self.transfer_to.setCurrentIndex(index)
                    break
        self.transfer_memo.clearEditText()
        self.transfer_amount.setValue(0)

        self.transaction_tabs.setCurrentIndex(0)
        self._sync_transaction_edit_controls()

    def _sync_transaction_edit_controls(self) -> None:
        editing_type = self.editing_transaction_type
        is_editing = editing_type is not None

        for button in (self.expense_add_button, self.income_add_button, self.transfer_add_button):
            button.setEnabled(not is_editing)

        edit_controls = {
            "expense": (self.expense_update_button, self.expense_cancel_edit_button),
            "income": (self.income_update_button, self.income_cancel_edit_button),
            "transfer": (self.transfer_update_button, self.transfer_cancel_edit_button),
        }
        for transaction_type, (update_button, cancel_button) in edit_controls.items():
            update_button.setEnabled(editing_type == transaction_type)
            cancel_button.setEnabled(
                editing_type == transaction_type or (self.return_to_ledger_after_transaction and not is_editing)
            )

    def cancel_transaction_edit(self) -> None:
        self.editing_transaction_id = None
        self.editing_transaction_type = None
        return_to_ledger = self.return_to_ledger_after_transaction
        self.return_to_ledger_after_transaction = False
        self._sync_transaction_edit_controls()
        if return_to_ledger:
            self.transaction_tabs.setCurrentIndex(3)

    def selected_transaction_id(self) -> int | None:
        row = self.transaction_table.currentRow()
        if row < 0:
            return None
        item = self.transaction_table.item(row, 0)
        return int(item.text()) if item else None

    def selected_ledger_transaction_id(self) -> int | None:
        row = self.ledger_table.currentRow()
        if row < 0:
            return None
        item = self.ledger_table.item(row, 0)
        if item is None or not item.text():
            return None
        return int(item.text())

    def selected_transaction(self) -> Transaction | None:
        transaction_id = self.selected_transaction_id()
        return self.transaction_by_id(transaction_id)

    def selected_ledger_transaction(self) -> Transaction | None:
        transaction_id = self.selected_ledger_transaction_id()
        return self.transaction_by_id(transaction_id)

    def transaction_by_id(self, transaction_id: int | None) -> Transaction | None:
        if transaction_id is None:
            return None
        return next(
            (transaction for transaction in self.transactions if transaction.id == transaction_id),
            None,
        )

    def _set_combo_data(self, combo: QComboBox, data: int | None) -> None:
        if data is None:
            return
        index = combo.findData(data)
        if index < 0:
            account = next((account for account in self.managed_accounts if account.id == data), None)
            if account is not None:
                status = "" if account.is_active else "（非表示）"
                combo.addItem(f"{account.name}{status}（¥{account.balance:,}）", account.id)
                index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_combo_text(self, combo: QComboBox, text: str) -> None:
        index = combo.findText(text)
        if index < 0 and text:
            combo.addItem(text)
            index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)

    def load_selected_transaction_for_edit(self) -> None:
        tx = self.selected_transaction()
        if tx is None:
            QMessageBox.information(self, "取引編集", "編集する取引を選択してください。")
            return
        self.load_transaction_for_edit(tx, return_to_ledger=False)

    def load_selected_ledger_transaction_for_edit(self) -> None:
        tx = self.selected_ledger_transaction()
        if tx is None:
            QMessageBox.information(self, "口座元帳", "変更する取引行を選択してください。")
            return
        self.load_transaction_for_edit(tx, return_to_ledger=True)

    def load_transaction_for_edit(self, tx: Transaction, return_to_ledger: bool) -> None:
        self.editing_transaction_id = tx.id
        self.editing_transaction_type = tx.transaction_type
        self.return_to_ledger_after_transaction = return_to_ledger
        tx_date = QDate.fromString(tx.occurred_on, "yyyy-MM-dd")
        if not tx_date.isValid():
            tx_date = QDate.currentDate()

        if tx.transaction_type == "expense":
            self.transaction_tabs.setCurrentIndex(0)
            self.expense_date.setDate(tx_date)
            self._set_combo_data(self.expense_account, tx.from_account_id)
            self._set_combo_text(self.expense_category, tx.category)
            self.expense_memo.setEditText(tx.memo)
            self.expense_amount.setValue(tx.amount)
        elif tx.transaction_type == "income":
            self.transaction_tabs.setCurrentIndex(1)
            self.income_date.setDate(tx_date)
            self._set_combo_data(self.income_account, tx.to_account_id)
            self._set_combo_text(self.income_category, tx.category)
            self.income_memo.setEditText(tx.memo)
            self.income_amount.setValue(tx.amount)
        elif tx.transaction_type == "transfer":
            self.transaction_tabs.setCurrentIndex(2)
            self.transfer_date.setDate(tx_date)
            self._set_combo_data(self.transfer_from, tx.from_account_id)
            self._set_combo_data(self.transfer_to, tx.to_account_id)
            self.transfer_memo.setEditText(tx.memo)
            self.transfer_amount.setValue(tx.amount)
        self._sync_transaction_edit_controls()

    def update_editing_transaction(self) -> None:
        if self.editing_transaction_id is None or self.editing_transaction_type is None:
            QMessageBox.information(self, "取引編集", "先に編集する取引を選択してください。")
            return

        try:
            if self.editing_transaction_type == "expense":
                if not self.confirm_positive_transaction_amount(self.expense_amount.value(), "取引編集"):
                    return
                self.store.update_transaction(
                    self.editing_transaction_id,
                    self.expense_date.date().toString("yyyy-MM-dd"),
                    "expense",
                    self.expense_category.currentText(),
                    self.expense_account.currentData(),
                    None,
                    self.expense_memo.currentText().strip() or "メモなし",
                    self.expense_amount.value(),
                )
                self.expense_memo.clearEditText()
                self.expense_amount.setValue(0)
            elif self.editing_transaction_type == "income":
                if not self.confirm_positive_transaction_amount(self.income_amount.value(), "取引編集"):
                    return
                self.store.update_transaction(
                    self.editing_transaction_id,
                    self.income_date.date().toString("yyyy-MM-dd"),
                    "income",
                    self.income_category.currentText(),
                    None,
                    self.income_account.currentData(),
                    self.income_memo.currentText().strip() or "メモなし",
                    self.income_amount.value(),
                )
                self.income_memo.clearEditText()
                self.income_amount.setValue(0)
            elif self.editing_transaction_type == "transfer":
                if not self.confirm_positive_transaction_amount(self.transfer_amount.value(), "取引編集"):
                    return
                self.store.update_transaction(
                    self.editing_transaction_id,
                    self.transfer_date.date().toString("yyyy-MM-dd"),
                    "transfer",
                    "資金移動",
                    self.transfer_from.currentData(),
                    self.transfer_to.currentData(),
                    self.transfer_memo.currentText().strip() or "資金移動",
                    self.transfer_amount.value(),
                )
                self.transfer_memo.clearEditText()
                self.transfer_amount.setValue(0)
        except ValueError as exc:
            QMessageBox.information(self, "取引編集", str(exc))
            return

        self.editing_transaction_id = None
        self.editing_transaction_type = None
        self.finish_transaction_entry()

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

    def add_category_from_master(self) -> None:
        try:
            self.store.add_category(self.category_type.currentData(), self.category_name.text())
        except ValueError as exc:
            QMessageBox.information(self, "カテゴリ管理", str(exc))
            return
        self.category_name.clear()
        self.refresh()

    def selected_category_id(self) -> int | None:
        row = self.category_table.currentRow()
        if row < 0:
            return None
        item = self.category_table.item(row, 0)
        return int(item.text()) if item else None

    def selected_category(self) -> CategoryMaster | None:
        category_id = self.selected_category_id()
        if category_id is None:
            return None
        return next(
            (category for category in self.category_records if category.id == category_id),
            None,
        )

    def load_selected_category(self) -> None:
        category = self.selected_category()
        if category is None:
            return
        self.category_name.setText(category.name)
        index = self.category_type.findData(category.transaction_type)
        if index >= 0:
            self.category_type.setCurrentIndex(index)

    def update_selected_category(self) -> None:
        category = self.selected_category()
        if category is None:
            QMessageBox.information(self, "カテゴリ管理", "編集するカテゴリを選択してください。")
            return
        if self.category_type.currentData() != category.transaction_type:
            QMessageBox.information(self, "カテゴリ管理", "カテゴリの種別は変更できません。")
            return
        try:
            self.store.update_category(category.id, self.category_name.text())
        except ValueError as exc:
            QMessageBox.information(self, "カテゴリ管理", str(exc))
            return
        self.refresh()

    def delete_selected_category(self) -> None:
        category = self.selected_category()
        if category is None:
            QMessageBox.information(self, "カテゴリ管理", "削除するカテゴリを選択してください。")
            return
        transaction_count = self.store.category_transaction_count(category.id)
        if transaction_count > 0:
            QMessageBox.information(
                self,
                "カテゴリ管理",
                (
                    f"{category.name} は {transaction_count} 件の取引で使われているため削除できません。\n"
                    "該当取引を削除するか、別カテゴリへ変更してから削除してください。"
                ),
            )
            return
        reply = QMessageBox.question(
            self,
            "カテゴリ削除",
            f"{category.type_label}カテゴリ「{category.name}」を削除しますか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.store.delete_category(category.id)
        self.category_name.clear()
        self.refresh()

    def move_selected_category_up(self) -> None:
        category = self.selected_category()
        if category is None:
            QMessageBox.information(self, "カテゴリ表示順", "移動するカテゴリを選択してください。")
            return
        self.store.move_category(category.id, -1)
        self.refresh()

    def move_selected_category_down(self) -> None:
        category = self.selected_category()
        if category is None:
            QMessageBox.information(self, "カテゴリ表示順", "移動するカテゴリを選択してください。")
            return
        self.store.move_category(category.id, 1)
        self.refresh()

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

    def move_selected_account_up(self) -> None:
        account = self.selected_account()
        if account is None:
            QMessageBox.information(self, "口座表示順", "移動する口座を選択してください。")
            return
        self.store.move_account(account.id, -1)
        self.refresh()

    def move_selected_account_down(self) -> None:
        account = self.selected_account()
        if account is None:
            QMessageBox.information(self, "口座表示順", "移動する口座を選択してください。")
            return
        self.store.move_account(account.id, 1)
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
        transaction = self.selected_transaction()
        if transaction is None:
            QMessageBox.information(self, "削除", "削除する取引を選択してください。")
            return
        self.delete_transaction_with_confirmation(transaction)

    def delete_selected_ledger_transaction(self) -> None:
        transaction = self.selected_ledger_transaction()
        if transaction is None:
            QMessageBox.information(self, "口座元帳", "削除する取引行を選択してください。")
            return
        self.delete_transaction_with_confirmation(transaction)

    def delete_transaction_with_confirmation(self, transaction: Transaction) -> None:
        labels = {"expense": "支出", "income": "収入", "transfer": "移動"}
        reply = QMessageBox.question(
            self,
            "取引削除",
            (
                "この取引を削除しますか？\n\n"
                f"日付: {transaction.occurred_on}\n"
                f"種類: {labels.get(transaction.transaction_type, transaction.transaction_type)}\n"
                f"カテゴリ: {transaction.category}\n"
                f"メモ: {transaction.memo}\n"
                f"金額: ¥{transaction.amount:,}"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        transaction_id = transaction.id
        self.store.delete_transaction(transaction_id)
        if self.editing_transaction_id == transaction_id:
            self.editing_transaction_id = None
            self.editing_transaction_type = None
        self.refresh()

    def export_ledger_pdf(self) -> None:
        account_name = self.ledger_account.currentText().strip() or "口座"
        title = f"{self.selected_month_text()} 口座元帳 - {account_name}"
        default_name = f"{self.selected_month.year}-{self.selected_month.month:02d}_ledger.pdf"
        self._export_table_pdf(self.ledger_table, title, default_name)

    def export_category_ledger_pdf(self) -> None:
        type_label = self.category_ledger_type.currentText().strip() or "カテゴリ"
        category = self.category_ledger_category.currentText().strip() or "カテゴリ"
        title = f"{self.selected_month.year}年1月1日〜{self.selected_month_text()} カテゴリ元帳 - {type_label} {category}"
        default_name = (
            f"{self.selected_month.year}-{self.selected_month.month:02d}_category_ledger.pdf"
        )
        self._export_table_pdf(self.category_ledger_table, title, default_name)

    def export_trial_balance_pdf(self) -> None:
        title = f"{self.selected_month_text()} 試算表"
        default_name = f"{self.selected_month.year}-{self.selected_month.month:02d}_trial_balance.pdf"
        self._export_table_pdf(self.trial_balance_table, title, default_name)

    def _export_table_pdf(self, table: QTableWidget, title: str, default_name: str) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "PDF保存",
            str(APP_DIR / default_name),
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        output_path = Path(path)
        if output_path.suffix.lower() != ".pdf":
            output_path = output_path.with_suffix(".pdf")
        self._write_table_pdf(output_path, title, table)
        QMessageBox.information(self, "PDF保存", f"PDFを保存しました。\n{output_path}")

    def _write_table_pdf(self, output_path: Path, title: str, table: QTableWidget) -> None:
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(str(output_path))

        document = QTextDocument()
        document.setDefaultFont(self.font())
        document.setHtml(self._table_pdf_html(title, table))
        document.print_(printer)

    def _table_pdf_html(self, title: str, table: QTableWidget) -> str:
        visible_columns = [
            column
            for column in range(table.columnCount())
            if not table.isColumnHidden(column)
        ]
        headers = []
        for column in visible_columns:
            header_item = table.horizontalHeaderItem(column)
            headers.append(escape(header_item.text() if header_item else ""))

        body_rows = []
        for row in range(table.rowCount()):
            cells = []
            for column in visible_columns:
                item = table.item(row, column)
                text = escape(item.text() if item else "")
                align = "right" if text.startswith("¥") else "left"
                cells.append(f'<td class="{align}">{text}</td>')
            body_rows.append(f"<tr>{''.join(cells)}</tr>")

        return f"""
        <!doctype html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: "Yu Gothic UI", "Meiryo", sans-serif;
                    color: #263238;
                }}
                h1 {{
                    font-size: 18pt;
                    margin-bottom: 14px;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    font-size: 9pt;
                }}
                th {{
                    background: #F0E8DE;
                    font-weight: 700;
                }}
                th, td {{
                    border: 1px solid #D6CDC2;
                    padding: 5px 7px;
                }}
                td.right {{
                    text-align: right;
                    white-space: nowrap;
                }}
            </style>
        </head>
        <body>
            <h1>{escape(title)}</h1>
            <table>
                <thead><tr>{''.join(f"<th>{header}</th>" for header in headers)}</tr></thead>
                <tbody>{''.join(body_rows)}</tbody>
            </table>
        </body>
        </html>
        """

    def selected_month_range(self) -> tuple[date, date]:
        next_month = (
            date(self.selected_month.year + 1, 1, 1)
            if self.selected_month.month == 12
            else date(self.selected_month.year, self.selected_month.month + 1, 1)
        )
        return self.selected_month, next_month

    def selected_month_text(self) -> str:
        return f"{self.selected_month.year}年{self.selected_month.month}月"

    def show_previous_month(self) -> None:
        self.selected_month = (
            date(self.selected_month.year - 1, 12, 1)
            if self.selected_month.month == 1
            else date(self.selected_month.year, self.selected_month.month - 1, 1)
        )
        self.refresh()

    def show_current_month(self) -> None:
        self.selected_month = date.today().replace(day=1)
        self.refresh()

    def show_next_month(self) -> None:
        self.selected_month = (
            date(self.selected_month.year + 1, 1, 1)
            if self.selected_month.month == 12
            else date(self.selected_month.year, self.selected_month.month + 1, 1)
        )
        self.refresh()

    def refresh(self) -> None:
        month_start, next_month_start = self.selected_month_range()
        self.accounts = self.store.list_accounts()
        self.managed_accounts = self.store.list_accounts(active_only=False)
        self.transactions = self.store.list_transactions(month_start, next_month_start)
        self.category_records = self.store.list_category_records()
        self.expense_categories = [
            category.name for category in self.category_records if category.transaction_type == "expense"
        ]
        self.income_categories = [
            category.name for category in self.category_records if category.transaction_type == "income"
        ]
        self._refresh_category_combos()
        self._refresh_category_ledger_category_combo()
        self._refresh_account_combos()
        self._refresh_ledger_account_combo()
        self._refresh_memo_combos()
        self._refresh_month_labels()
        self._render_transactions()
        self._render_account_ledger()
        self._render_category_ledger()
        self._render_trial_balance()
        self._render_accounts()
        self._render_categories()
        self._render_memo_templates()
        self._render_account_panel()
        self._render_summary()
        self._sync_transaction_edit_controls()

    def _refresh_month_labels(self) -> None:
        month_text = self.selected_month_text()
        self.month_label.setText(month_text)
        self.expense_card.set_title(f"{month_text}の支出（資金移動を含む）")
        self.income_card.set_title(f"{month_text}の収入（資金移動を含む）")
        self.history_label.setText(f"{month_text}の取引")
        self.category_title.setText(f"{month_text}の支出内訳")

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

    def _refresh_category_combos(self) -> None:
        for combo, categories in (
            (self.expense_category, self.expense_categories),
            (self.income_category, self.income_categories),
        ):
            current_text = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(categories)
            if current_text:
                index = combo.findText(current_text)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _refresh_category_ledger_category_combo(self) -> None:
        transaction_type = self.category_ledger_type.currentData() or "expense"
        categories = self.expense_categories if transaction_type == "expense" else self.income_categories
        current_text = self.category_ledger_category.currentText()
        self.category_ledger_category.blockSignals(True)
        self.category_ledger_category.clear()
        self.category_ledger_category.addItems(categories)
        if current_text:
            index = self.category_ledger_category.findText(current_text)
            if index >= 0:
                self.category_ledger_category.setCurrentIndex(index)
        self.category_ledger_category.blockSignals(False)
        self._render_category_ledger()

    def _refresh_ledger_account_combo(self) -> None:
        current_value = self.ledger_account.currentData()
        self.ledger_account.blockSignals(True)
        self.ledger_account.clear()
        for account in self.managed_accounts:
            status = "" if account.is_active else "（非表示）"
            self.ledger_account.addItem(f"{account.name}{status}", account.id)
        if current_value is not None:
            index = self.ledger_account.findData(current_value)
            if index >= 0:
                self.ledger_account.setCurrentIndex(index)
        self.ledger_account.blockSignals(False)

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
        month_start, next_month_start = self.selected_month_range()
        expense_total, income_total, by_category = self.store.monthly_totals(
            month_start,
            next_month_start,
        )
        net_assets = sum(account.balance for account in self.accounts)

        self.assets_card.set_value(f"¥{net_assets:,}")
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

    def _render_account_ledger(self) -> None:
        account_id = self.ledger_account.currentData()
        if account_id is None:
            self.ledger_table.setRowCount(0)
            return

        month_start, next_month_start = self.selected_month_range()
        entries = self.store.account_ledger(account_id, month_start, next_month_start)
        labels = {
            "opening": "繰越",
            "expense": "支出",
            "income": "収入",
            "transfer": "移動",
        }
        self.ledger_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                "" if entry.transaction_id is None else str(entry.transaction_id),
                entry.occurred_on,
                labels.get(entry.transaction_type, entry.transaction_type),
                entry.description,
                entry.memo,
                "" if entry.deposit == 0 else f"¥{entry.deposit:,}",
                "" if entry.withdrawal == 0 else f"¥{entry.withdrawal:,}",
                f"¥{entry.balance:,}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (5, 6, 7):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.ledger_table.setItem(row, column, item)

    def _render_category_ledger(self) -> None:
        transaction_type = self.category_ledger_type.currentData()
        category = self.category_ledger_category.currentText()
        if transaction_type is None or not category:
            self.category_ledger_table.setRowCount(0)
            return

        _month_start, next_month_start = self.selected_month_range()
        year_start = date(self.selected_month.year, 1, 1)
        entries = self.store.category_ledger(transaction_type, category, year_start, next_month_start)
        labels = {
            "opening": "起点",
            "expense": "支出",
            "income": "収入",
        }
        self.category_ledger_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                "" if entry.transaction_id is None else str(entry.transaction_id),
                entry.occurred_on,
                labels.get(entry.transaction_type, entry.transaction_type),
                entry.category,
                entry.account_name,
                entry.memo,
                "" if entry.amount == 0 else f"¥{entry.amount:,}",
                f"¥{entry.balance:,}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (6, 7):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.category_ledger_table.setItem(row, column, item)

    def _render_trial_balance(self) -> None:
        month_start, next_month_start = self.selected_month_range()
        rows = self.store.trial_balance(month_start, next_month_start)
        debit_total = sum(row.debit for row in rows)
        credit_total = sum(row.credit for row in rows)
        display_rows = rows + [
            TrialBalanceRow(
                section="合計",
                name="合計",
                debit=debit_total,
                credit=credit_total,
            )
        ]

        self.trial_balance_table.setRowCount(len(display_rows))
        for row_index, row in enumerate(display_rows):
            values = [
                row.section,
                row.name,
                "" if row.debit == 0 else f"¥{row.debit:,}",
                "" if row.credit == 0 else f"¥{row.credit:,}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (2, 3):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if row.section == "合計":
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.trial_balance_table.setItem(row_index, column, item)

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

    def _render_categories(self) -> None:
        current_id = self.selected_category_id()
        selected_row = -1
        self.category_table.blockSignals(True)
        self.category_table.setRowCount(len(self.category_records))
        for row, category in enumerate(self.category_records):
            values = [str(category.id), category.type_label, category.name]
            if category.id == current_id:
                selected_row = row
            for column, value in enumerate(values):
                self.category_table.setItem(row, column, QTableWidgetItem(value))
        self.category_table.blockSignals(False)
        if selected_row >= 0:
            self.category_table.selectRow(selected_row)

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
        categories = list(self.expense_categories)
        categories.extend(category for category in by_category if category not in categories)
        for index, category in enumerate(categories):
            amount = by_category.get(category, 0)
            ratio = 0 if total == 0 else amount / total
            color = CATEGORY_COLORS.get(category, CATEGORY_FALLBACK_COLORS[index % len(CATEGORY_FALLBACK_COLORS)])
            self.category_panel.addWidget(CategoryBar(category, amount, ratio, color))

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
                font-size: 28px;
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
            #monthLabel {
                font-size: 18px;
                font-weight: 800;
                color: #25324A;
                background: transparent;
                padding: 0 8px;
                min-width: 112px;
            }
            #cardValue {
                font-size: 20px;
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
            QPushButton:disabled {
                background: #E2DED7;
                color: #5F6670;
            }
            QPushButton#deleteButton {
                background: #9B4A3F;
                color: #FFFFFF;
            }
            QPushButton#deleteButton:hover {
                background: #7F3B32;
            }
            QPushButton#deleteButton:disabled {
                background: #E2DED7;
                color: #5F6670;
            }
            QSplitter#mainSplitter, QSplitter#leftSplitter, QSplitter#rightSplitter {
                background: transparent;
            }
            QSplitter#mainSplitter::handle, QSplitter#leftSplitter::handle, QSplitter#rightSplitter::handle {
                background: #DED6CC;
                border-radius: 2px;
                margin: 4px 6px;
            }
            QSplitter#mainSplitter::handle:hover, QSplitter#leftSplitter::handle:hover, QSplitter#rightSplitter::handle:hover {
                background: #B9AFA3;
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
    app.setApplicationName("家計管理")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = KakeiboWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
