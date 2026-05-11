# My家計簿

Python + PySide6 で作る、資産管理もできる家計簿アプリのベースです。

## 起動

```powershell
.\.venv\Scripts\python.exe .\main.py
```

## できること

- 支出、収入、資金移動を記録
- 支出元の口座を選択
- 現金、普通預金、定期預金等、Pay支払の口座管理
- 普通預金は20件、定期預金等は20件、Pay支払は5件まで追加
- 15種類の支出カテゴリで今月の内訳を表示
- SQLite (`kakeibo.db`) へ自動保存
- 口座ごとの現在残高と総資産を表示

## 依存関係

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```
