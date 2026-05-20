from __future__ import annotations

import sqlite3
import unicodedata
from datetime import date
from pathlib import Path

from models import (
    ACCOUNT_TYPES,
    CATEGORY_TYPES,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    MEMO_USAGE_TYPES,
    Account,
    CategoryLedgerEntry,
    CategoryMaster,
    LedgerEntry,
    MemoTemplate,
    Transaction,
    TrialBalanceRow,
)


class KakeiboStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_schema()
        self._seed_accounts()
        self._seed_categories()
        self._migrate_legacy_entries()
        self._seed_categories()
        self._normalize_sort_orders()

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
                    sort_order INTEGER NOT NULL DEFAULT 0,
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_type TEXT NOT NULL CHECK(transaction_type IN ('expense', 'income')),
                    name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(transaction_type, name)
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
            account_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(accounts)").fetchall()
            }
            if "sort_order" not in account_columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
                conn.execute(
                    """
                    UPDATE accounts
                    SET sort_order =
                        CASE account_type
                            WHEN 'cash' THEN 0
                            WHEN 'bank' THEN 1000
                            WHEN 'deposit' THEN 2000
                            WHEN 'pay' THEN 3000
                            WHEN 'credit_card' THEN 4000
                            ELSE 5000
                        END + id
                    """
                )

    def _seed_accounts(self) -> None:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            if count:
                return
            defaults = [
                ("現金", "cash", 0, 0),
                ("普通預金1", "bank", 0, 1),
                ("普通預金2", "bank", 0, 2),
                ("定期預金等1", "deposit", 0, 3),
                ("Pay支払1", "pay", 0, 4),
            ]
            conn.executemany(
                "INSERT INTO accounts (name, account_type, opening_balance, sort_order) VALUES (?, ?, ?, ?)",
                defaults,
            )

    def _seed_categories(self) -> None:
        defaults = [
            *[("expense", name, index) for index, name in enumerate(EXPENSE_CATEGORIES)],
            *[("income", name, index) for index, name in enumerate(INCOME_CATEGORIES)],
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO categories (transaction_type, name, sort_order)
                VALUES (?, ?, ?)
                """,
                defaults,
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO categories (transaction_type, name, sort_order)
                SELECT transaction_type, category, 1000
                FROM transactions
                WHERE transaction_type IN ('expense', 'income')
                  AND TRIM(COALESCE(category, '')) <> ''
                GROUP BY transaction_type, category
                """
            )

    def _normalize_sort_orders(self) -> None:
        with self._connect() as conn:
            account_rows = conn.execute(
                "SELECT id FROM accounts ORDER BY sort_order, id"
            ).fetchall()
            for index, row in enumerate(account_rows):
                conn.execute(
                    "UPDATE accounts SET sort_order = ? WHERE id = ?",
                    (index, row["id"]),
                )

            category_types = [
                row["transaction_type"]
                for row in conn.execute(
                    "SELECT DISTINCT transaction_type FROM categories ORDER BY transaction_type"
                ).fetchall()
            ]
            for transaction_type in category_types:
                category_rows = conn.execute(
                    """
                    SELECT id
                    FROM categories
                    WHERE transaction_type = ?
                    ORDER BY sort_order, id
                    """,
                    (transaction_type,),
                ).fetchall()
                for index, row in enumerate(category_rows):
                    conn.execute(
                        "UPDATE categories SET sort_order = ? WHERE id = ?",
                        (index, row["id"]),
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

    def add_account(self, name: str, account_type: str, opening_balance: int | str) -> None:
        opening_balance_value = self.normalize_amount(opening_balance, allow_negative=True)
        with self._connect() as conn:
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM accounts"
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO accounts (name, account_type, opening_balance, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (name, account_type, opening_balance_value, sort_order),
            )

    def update_account(self, account_id: int, name: str, account_type: str, opening_balance: int | str) -> None:
        opening_balance_value = self.normalize_amount(opening_balance, allow_negative=True)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE accounts
                SET name = ?, account_type = ?, opening_balance = ?
                WHERE id = ?
                """,
                (name, account_type, opening_balance_value, account_id),
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

    def list_category_records(self) -> list[CategoryMaster]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, transaction_type, name
                FROM categories
                ORDER BY
                    CASE transaction_type
                        WHEN 'expense' THEN 0
                        WHEN 'income' THEN 1
                        ELSE 2
                    END,
                    sort_order,
                    id
                """
            ).fetchall()
        return [CategoryMaster(**dict(row)) for row in rows]

    def list_categories(self, transaction_type: str) -> list[str]:
        return [
            category.name
            for category in self.list_category_records()
            if category.transaction_type == transaction_type
        ]

    def add_category(self, transaction_type: str, name: str) -> None:
        text = name.strip()
        if transaction_type not in CATEGORY_TYPES:
            raise ValueError("カテゴリ種別が不正です。")
        if not text:
            raise ValueError("カテゴリ名を入力してください。")
        with self._connect() as conn:
            sort_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE transaction_type = ?",
                (transaction_type,),
            ).fetchone()[0]
            try:
                conn.execute(
                    """
                    INSERT INTO categories (transaction_type, name, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (transaction_type, text, sort_order),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("同じカテゴリがすでに登録されています。") from exc

    def update_category(self, category_id: int, name: str) -> None:
        text = name.strip()
        if not text:
            raise ValueError("カテゴリ名を入力してください。")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT transaction_type, name FROM categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if row is None:
                raise ValueError("更新するカテゴリが見つかりません。")
            try:
                conn.execute("UPDATE categories SET name = ? WHERE id = ?", (text, category_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError("同じカテゴリがすでに登録されています。") from exc
            conn.execute(
                """
                UPDATE transactions
                SET category = ?
                WHERE transaction_type = ? AND category = ?
                """,
                (text, row["transaction_type"], row["name"]),
            )

    def category_transaction_count(self, category_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT transaction_type, name FROM categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if row is None:
                return 0
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM transactions
                    WHERE transaction_type = ? AND category = ?
                    """,
                    (row["transaction_type"], row["name"]),
                ).fetchone()[0]
            )

    def delete_category(self, category_id: int) -> None:
        if self.category_transaction_count(category_id) > 0:
            raise ValueError("取引で使われているカテゴリは削除できません。")
        with self._connect() as conn:
            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    def move_category(self, category_id: int, direction: int) -> None:
        if direction == 0:
            return
        with self._connect() as conn:
            current = conn.execute(
                "SELECT id, transaction_type, sort_order FROM categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if current is None:
                return
            comparator = "<" if direction < 0 else ">"
            order = "DESC" if direction < 0 else "ASC"
            target = conn.execute(
                f"""
                SELECT id, sort_order
                FROM categories
                WHERE transaction_type = ? AND sort_order {comparator} ?
                ORDER BY sort_order {order}, id {order}
                LIMIT 1
                """,
                (current["transaction_type"], current["sort_order"]),
            ).fetchone()
            if target is None:
                return
            conn.execute(
                "UPDATE categories SET sort_order = ? WHERE id = ?",
                (target["sort_order"], current["id"]),
            )
            conn.execute(
                "UPDATE categories SET sort_order = ? WHERE id = ?",
                (current["sort_order"], target["id"]),
            )

    def list_accounts(self, active_only: bool = True) -> list[Account]:
        where = "WHERE is_active = 1" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, name, account_type, opening_balance, is_active
                FROM accounts
                {where}
                ORDER BY sort_order, id
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

    def move_account(self, account_id: int, direction: int) -> None:
        if direction == 0:
            return
        with self._connect() as conn:
            current = conn.execute(
                "SELECT id, sort_order FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if current is None:
                return
            comparator = "<" if direction < 0 else ">"
            order = "DESC" if direction < 0 else "ASC"
            target = conn.execute(
                f"""
                SELECT id, sort_order
                FROM accounts
                WHERE sort_order {comparator} ?
                ORDER BY sort_order {order}, id {order}
                LIMIT 1
                """,
                (current["sort_order"],),
            ).fetchone()
            if target is None:
                return
            conn.execute(
                "UPDATE accounts SET sort_order = ? WHERE id = ?",
                (target["sort_order"], current["id"]),
            )
            conn.execute(
                "UPDATE accounts SET sort_order = ? WHERE id = ?",
                (current["sort_order"], target["id"]),
            )

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

    def account_balance_before(self, account_id: int, before_date: date) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT opening_balance FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                return 0
            balance = int(row["opening_balance"])
            rows = conn.execute(
                """
                SELECT transaction_type, from_account_id, to_account_id, amount
                FROM transactions
                WHERE occurred_on < ?
                  AND (from_account_id = ? OR to_account_id = ?)
                ORDER BY occurred_on, id
                """,
                (before_date.isoformat(), account_id, account_id),
            ).fetchall()

        for row in rows:
            amount = int(row["amount"])
            if row["transaction_type"] == "expense" and row["from_account_id"] == account_id:
                balance -= amount
            elif row["transaction_type"] == "income" and row["to_account_id"] == account_id:
                balance += amount
            elif row["transaction_type"] == "transfer":
                if row["from_account_id"] == account_id:
                    balance -= amount
                elif row["to_account_id"] == account_id:
                    balance += amount
        return balance

    def account_ledger(self, account_id: int, month_start: date, next_month_start: date) -> list[LedgerEntry]:
        balance = self.account_balance_before(account_id, month_start)
        entries = [
            LedgerEntry(
                transaction_id=None,
                occurred_on=month_start.isoformat(),
                transaction_type="opening",
                description="前月繰越",
                memo="",
                withdrawal=0,
                deposit=0,
                balance=balance,
            )
        ]
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    t.id,
                    t.occurred_on,
                    t.transaction_type,
                    COALESCE(t.category, '') AS category,
                    t.from_account_id,
                    t.to_account_id,
                    COALESCE(f.name, '') AS from_account_name,
                    COALESCE(ta.name, '') AS to_account_name,
                    t.memo,
                    t.amount
                FROM transactions t
                LEFT JOIN accounts f ON f.id = t.from_account_id
                LEFT JOIN accounts ta ON ta.id = t.to_account_id
                WHERE t.occurred_on >= ?
                  AND t.occurred_on < ?
                  AND (t.from_account_id = ? OR t.to_account_id = ?)
                ORDER BY t.occurred_on, t.id
                """,
                (
                    month_start.isoformat(),
                    next_month_start.isoformat(),
                    account_id,
                    account_id,
                ),
            ).fetchall()

        for row in rows:
            amount = int(row["amount"])
            withdrawal = 0
            deposit = 0
            tx_type = row["transaction_type"]
            if tx_type == "expense":
                withdrawal = amount
                description = row["category"] or "支出"
            elif tx_type == "income":
                deposit = amount
                description = row["category"] or "収入"
            elif row["from_account_id"] == account_id:
                withdrawal = amount
                description = f"振替 → {row['to_account_name']}"
            else:
                deposit = amount
                description = f"振替 ← {row['from_account_name']}"

            balance += deposit - withdrawal
            entries.append(
                LedgerEntry(
                    transaction_id=int(row["id"]),
                    occurred_on=row["occurred_on"],
                    transaction_type=tx_type,
                    description=description,
                    memo=row["memo"],
                    withdrawal=withdrawal,
                    deposit=deposit,
                    balance=balance,
                )
            )
        return entries

    def category_ledger(
        self,
        transaction_type: str,
        category: str,
        year_start: date,
        next_month_start: date,
    ) -> list[CategoryLedgerEntry]:
        if transaction_type not in CATEGORY_TYPES:
            return []
        balance = 0
        entries = [
            CategoryLedgerEntry(
                transaction_id=None,
                occurred_on=year_start.isoformat(),
                transaction_type="opening",
                category=category,
                account_name="",
                memo="年初",
                amount=0,
                balance=balance,
            )
        ]
        account_join = "LEFT JOIN accounts a ON a.id = t.from_account_id"
        if transaction_type == "income":
            account_join = "LEFT JOIN accounts a ON a.id = t.to_account_id"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    t.id,
                    t.occurred_on,
                    t.transaction_type,
                    COALESCE(t.category, '') AS category,
                    COALESCE(a.name, '') AS account_name,
                    t.memo,
                    t.amount
                FROM transactions t
                {account_join}
                WHERE t.occurred_on >= ?
                  AND t.occurred_on < ?
                  AND t.transaction_type = ?
                  AND COALESCE(t.category, '') = ?
                ORDER BY t.occurred_on, t.id
                """,
                (
                    year_start.isoformat(),
                    next_month_start.isoformat(),
                    transaction_type,
                    category,
                ),
            ).fetchall()

        for row in rows:
            amount = int(row["amount"])
            balance += amount
            entries.append(
                CategoryLedgerEntry(
                    transaction_id=int(row["id"]),
                    occurred_on=row["occurred_on"],
                    transaction_type=row["transaction_type"],
                    category=row["category"],
                    account_name=row["account_name"],
                    memo=row["memo"],
                    amount=amount,
                    balance=balance,
                )
            )
        return entries

    def add_expense(self, occurred_on: str, account_id: int, category: str, memo: str, amount: int | str) -> None:
        self._add_transaction(occurred_on, "expense", category, account_id, None, memo, amount)

    def add_income(self, occurred_on: str, account_id: int, category: str, memo: str, amount: int | str) -> None:
        self._add_transaction(occurred_on, "income", category, None, account_id, memo, amount)

    def add_transfer(self, occurred_on: str, from_account_id: int, to_account_id: int, memo: str, amount: int | str) -> None:
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
        amount: int | str,
    ) -> None:
        amount_value = self.normalize_amount(amount)
        if amount_value <= 0:
            raise ValueError("取引金額は1円以上で入力してください。")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO transactions (
                    occurred_on, transaction_type, category, from_account_id, to_account_id, memo, amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (occurred_on, transaction_type, category, from_account_id, to_account_id, memo, amount_value),
            )

    def delete_transaction(self, transaction_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    def update_transaction(
        self,
        transaction_id: int,
        occurred_on: str,
        transaction_type: str,
        category: str,
        from_account_id: int | None,
        to_account_id: int | None,
        memo: str,
        amount: int | str,
    ) -> None:
        if transaction_type == "transfer" and from_account_id == to_account_id:
            raise ValueError("移動元と移動先は別の口座を選んでください。")
        amount_value = self.normalize_amount(amount)
        if amount_value <= 0:
            raise ValueError("取引金額は1円以上で入力してください。")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE transactions
                SET occurred_on = ?,
                    transaction_type = ?,
                    category = ?,
                    from_account_id = ?,
                    to_account_id = ?,
                    memo = ?,
                    amount = ?
                WHERE id = ?
                """,
                (
                    occurred_on,
                    transaction_type,
                    category,
                    from_account_id,
                    to_account_id,
                    memo,
                    amount_value,
                    transaction_id,
                ),
            )

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

    def list_transactions(
        self,
        month_start: date | None = None,
        next_month_start: date | None = None,
    ) -> list[Transaction]:
        where = ""
        params: tuple[str, ...] = ()
        if month_start is not None and next_month_start is not None:
            where = "WHERE t.occurred_on >= ? AND t.occurred_on < ?"
            params = (month_start.isoformat(), next_month_start.isoformat())
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    t.id,
                    t.occurred_on,
                    t.transaction_type,
                    COALESCE(t.category, '') AS category,
                    t.from_account_id,
                    t.to_account_id,
                    COALESCE(f.name, '') AS from_account_name,
                    COALESCE(ta.name, '') AS to_account_name,
                    t.memo,
                    t.amount
                FROM transactions t
                LEFT JOIN accounts f ON f.id = t.from_account_id
                LEFT JOIN accounts ta ON ta.id = t.to_account_id
                {where}
                ORDER BY t.occurred_on DESC, t.id DESC
                """,
                params,
            ).fetchall()
        return [Transaction(**dict(row)) for row in rows]

    def search_transactions(
        self,
        keyword: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Transaction]:
        conditions: list[str] = []
        params: list[str] = []

        if start_date is not None and end_date is not None:
            conditions.append("t.occurred_on >= ? AND t.occurred_on < ?")
            params.extend([start_date.isoformat(), end_date.isoformat()])

        text = unicodedata.normalize("NFKC", keyword).strip()
        if text:
            like_text = f"%{text}%"
            search_conditions = [
                "t.occurred_on LIKE ?",
                "COALESCE(t.category, '') LIKE ?",
                "COALESCE(t.memo, '') LIKE ?",
                "COALESCE(f.name, '') LIKE ?",
                "COALESCE(ta.name, '') LIKE ?",
                """
                CASE t.transaction_type
                    WHEN 'expense' THEN '支出'
                    WHEN 'income' THEN '収入'
                    WHEN 'transfer' THEN '資金移動'
                    ELSE t.transaction_type
                END LIKE ?
                """,
            ]
            params.extend([like_text] * len(search_conditions))

            amount_text = self._amount_search_text(text)
            if amount_text:
                search_conditions.append("t.amount = ?")
                params.append(amount_text)
                search_conditions.append("CAST(t.amount AS TEXT) LIKE ?")
                params.append(f"%{amount_text}%")

            conditions.append(f"({' OR '.join(search_conditions)})")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    t.id,
                    t.occurred_on,
                    t.transaction_type,
                    COALESCE(t.category, '') AS category,
                    t.from_account_id,
                    t.to_account_id,
                    COALESCE(f.name, '') AS from_account_name,
                    COALESCE(ta.name, '') AS to_account_name,
                    t.memo,
                    t.amount
                FROM transactions t
                LEFT JOIN accounts f ON f.id = t.from_account_id
                LEFT JOIN accounts ta ON ta.id = t.to_account_id
                {where}
                ORDER BY t.occurred_on DESC, t.id DESC
                """,
                tuple(params),
            ).fetchall()
        return [Transaction(**dict(row)) for row in rows]

    @staticmethod
    def _amount_search_text(text: str) -> str:
        return "".join(char for char in text if char.isdecimal())

    @staticmethod
    def normalize_amount(value: int | str, allow_negative: bool = False) -> int:
        if isinstance(value, int):
            return value

        text = unicodedata.normalize("NFKC", str(value)).strip()
        for removable in ("¥", "円", ","):
            text = text.replace(removable, "")
        text = "".join(char for char in text if not char.isspace())

        if allow_negative and text.startswith("-"):
            number_text = text[1:]
            sign = -1
        else:
            number_text = text
            sign = 1

        if not number_text.isdecimal():
            raise ValueError("金額は整数で入力してください。")
        return sign * int(number_text)

    def get_transaction(self, transaction_id: int) -> Transaction | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    t.id,
                    t.occurred_on,
                    t.transaction_type,
                    COALESCE(t.category, '') AS category,
                    t.from_account_id,
                    t.to_account_id,
                    COALESCE(f.name, '') AS from_account_name,
                    COALESCE(ta.name, '') AS to_account_name,
                    t.memo,
                    t.amount
                FROM transactions t
                LEFT JOIN accounts f ON f.id = t.from_account_id
                LEFT JOIN accounts ta ON ta.id = t.to_account_id
                WHERE t.id = ?
                """,
                (transaction_id,),
            ).fetchone()
        return Transaction(**dict(row)) if row is not None else None

    def monthly_totals(self, month_start: date, next_month_start: date) -> tuple[int, int, dict[str, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT transaction_type, COALESCE(category, '') AS category, SUM(amount) AS total
                FROM transactions
                WHERE occurred_on >= ? AND occurred_on < ?
                    AND transaction_type IN ('expense', 'income')
                GROUP BY transaction_type, COALESCE(category, '')
                """,
                (month_start.isoformat(), next_month_start.isoformat()),
            ).fetchall()

        expense_total = 0
        income_total = 0
        by_category: dict[str, int] = {}
        for row in rows:
            total = int(row["total"] or 0)
            if row["transaction_type"] == "expense":
                expense_total += total
                by_category[row["category"]] = by_category.get(row["category"], 0) + total
            elif row["transaction_type"] == "income":
                income_total += total
        return expense_total, income_total, by_category

    def trial_balance(self, month_start: date, next_month_start: date) -> list[TrialBalanceRow]:
        rows: list[TrialBalanceRow] = []
        opening_net_assets = 0
        for account in self.list_accounts(active_only=False):
            opening_net_assets += self.account_balance_before(account.id, month_start)
            ending_balance = self.account_balance_before(account.id, next_month_start)
            if account.account_type == "credit_card" and ending_balance < 0:
                rows.append(
                    TrialBalanceRow(
                        section="負債",
                        name=account.name,
                        debit=0,
                        credit=-ending_balance,
                    )
                )
            elif ending_balance != 0:
                rows.append(
                    TrialBalanceRow(
                        section="資産",
                        name=account.name,
                        debit=max(ending_balance, 0),
                        credit=max(-ending_balance, 0),
                    )
                )

        if opening_net_assets > 0:
            rows.append(
                TrialBalanceRow(
                    section="純資産",
                    name="期首純資産",
                    debit=0,
                    credit=opening_net_assets,
                )
            )
        elif opening_net_assets < 0:
            rows.append(
                TrialBalanceRow(
                    section="純資産",
                    name="期首純資産",
                    debit=-opening_net_assets,
                    credit=0,
                )
            )

        with self._connect() as conn:
            category_rows = conn.execute(
                """
                SELECT transaction_type, COALESCE(category, '') AS category, SUM(amount) AS total
                FROM transactions
                WHERE occurred_on >= ? AND occurred_on < ?
                  AND transaction_type IN ('expense', 'income')
                GROUP BY transaction_type, COALESCE(category, '')
                ORDER BY transaction_type, category
                """,
                (month_start.isoformat(), next_month_start.isoformat()),
            ).fetchall()

        for row in category_rows:
            amount = int(row["total"] or 0)
            category = row["category"] or "未分類"
            if row["transaction_type"] == "expense":
                rows.append(
                    TrialBalanceRow(
                        section="費用",
                        name=category,
                        debit=amount,
                        credit=0,
                    )
                )
            elif row["transaction_type"] == "income":
                rows.append(
                    TrialBalanceRow(
                        section="収益",
                        name=category,
                        debit=0,
                        credit=amount,
                    )
                )
        return rows
