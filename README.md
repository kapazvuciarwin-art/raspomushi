# rasporuno - 日文歌詞資料庫

日文歌詞管理系統，支援斷詞、假名顯示、關鍵字搜尋，並可將單字加入 rasword 單字庫。

## 功能

- 📝 **新增歌詞**：手動輸入歌名和歌詞內容
- 📦 **批次匯入**：支援批次格式匯入多首歌詞
- 🔍 **關鍵字搜尋**：搜尋歌名或歌詞內容
- ✂️ **日文斷詞**：自動將歌詞斷詞，方便點擊單字
- 📖 **假名顯示**：點擊日文單字顯示假名讀音
- ➕ **加入單字庫**：點擊單字可加入 rasword 單字庫

## 安裝

```bash
# 建立虛擬環境
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 或 .venv\Scripts\activate  # Windows

# 安裝依賴
pip install -r requirements.txt
```

## 執行

```bash
python app.py
```

預設運行在 `http://localhost:5002`

## 環境變數

- `RASWORD_BASE_URL`：rasword 服務地址（預設：`http://127.0.0.1:5000`）

## 批次匯入格式

```
=== 歌名1.txt ===
歌詞內容

=== 歌名2.txt ===
歌詞內容
```

## 資料庫

使用 SQLite，資料庫檔案：`rasporuno.db`

## 授權

MIT License
