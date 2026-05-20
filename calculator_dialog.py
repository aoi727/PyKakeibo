from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget


class CalculatorDialog(QDialog):
    def __init__(self, initial_value: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result_amount = initial_value
        self.setWindowTitle("電卓")
        self.setModal(True)
        self.resize(300, 340)

        self.expression_input = QLineEdit()
        self.expression_input.setAlignment(Qt.AlignRight)
        self.expression_input.setText(str(initial_value))

        grid = QGridLayout()
        buttons = [
            ("C", 0, 0), ("BS", 0, 1), ("(", 0, 2), (")", 0, 3),
            ("7", 1, 0), ("8", 1, 1), ("9", 1, 2), ("/", 1, 3),
            ("4", 2, 0), ("5", 2, 1), ("6", 2, 2), ("*", 2, 3),
            ("1", 3, 0), ("2", 3, 1), ("3", 3, 2), ("-", 3, 3),
            ("0", 4, 0), ("00", 4, 1), (".", 4, 2), ("+", 4, 3),
        ]
        for text, row, column in buttons:
            button = QPushButton(text)
            button.setMinimumHeight(38)
            button.clicked.connect(lambda _checked=False, value=text: self.handle_button(value))
            grid.addWidget(button, row, column)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept_result)
        cancel_button = QPushButton("キャンセル")
        cancel_button.clicked.connect(self.reject)

        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(ok_button)
        actions.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.expression_input)
        layout.addLayout(grid)
        layout.addLayout(actions)

    def handle_button(self, text: str) -> None:
        if text == "C":
            self.expression_input.clear()
            return
        if text == "BS":
            current = self.expression_input.text()
            self.expression_input.setText(current[:-1])
            return
        self.expression_input.setText(self.expression_input.text() + text)

    def accept_result(self) -> None:
        try:
            result = self.evaluate_expression(self.expression_input.text())
        except ValueError as exc:
            QMessageBox.information(self, "電卓", str(exc))
            return
        if result < 0:
            QMessageBox.information(self, "電卓", "金額は0円以上で入力してください。")
            return
        self.result_amount = int(result.to_integral_value(rounding=ROUND_HALF_UP))
        self.accept()

    @classmethod
    def evaluate_expression(cls, expression: str) -> Decimal:
        text = expression.strip()
        if not text:
            raise ValueError("計算式を入力してください。")
        try:
            tree = ast.parse(text, mode="eval")
            return cls._eval_node(tree.body)
        except (SyntaxError, InvalidOperation, ZeroDivisionError) as exc:
            raise ValueError("計算式を確認してください。") from exc

    @classmethod
    def _eval_node(cls, node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = cls._eval_node(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left = cls._eval_node(node.left)
            right = cls._eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right
        raise ValueError("数字と四則演算だけを入力してください。")
