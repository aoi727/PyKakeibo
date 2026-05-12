from __future__ import annotations

from dataclasses import dataclass


ACCOUNT_TYPES = {
    "cash": "現金",
    "bank": "普通預金",
    "deposit": "定期預金等",
    "pay": "Pay支払",
    "credit_card": "クレジットカード",
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


INCOME_CATEGORIES = [
    "給与",
    "賞与",
    "利息",
    "臨時収入",
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


CATEGORY_FALLBACK_COLORS = [
    "#F28C8C",
    "#F2B36D",
    "#7AA7E8",
    "#7EC8A4",
    "#B990E8",
    "#8FA3AD",
]


CATEGORY_TYPES = {
    "expense": "支出",
    "income": "収入",
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
    from_account_id: int | None
    to_account_id: int | None
    from_account_name: str
    to_account_name: str
    memo: str
    amount: int


@dataclass(frozen=True)
class LedgerEntry:
    transaction_id: int | None
    occurred_on: str
    transaction_type: str
    description: str
    memo: str
    withdrawal: int
    deposit: int
    balance: int


@dataclass(frozen=True)
class CategoryLedgerEntry:
    transaction_id: int | None
    occurred_on: str
    transaction_type: str
    category: str
    account_name: str
    memo: str
    amount: int
    balance: int


@dataclass(frozen=True)
class TrialBalanceRow:
    section: str
    name: str
    debit: int
    credit: int


@dataclass(frozen=True)
class MemoTemplate:
    id: int
    memo: str
    usage_type: str

    @property
    def usage_label(self) -> str:
        return MEMO_USAGE_TYPES.get(self.usage_type, self.usage_type)


@dataclass(frozen=True)
class CategoryMaster:
    id: int
    transaction_type: str
    name: str

    @property
    def type_label(self) -> str:
        return CATEGORY_TYPES.get(self.transaction_type, self.transaction_type)
