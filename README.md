# raspomushi - 日文歌詞瀏覽

日文歌詞瀏覽（無手動輸入），支援斷詞、假名顯示、關鍵字搜尋，並可將單字加入 rasword 單字庫。

## 與 rasporuno 的差異

- **無手動輸入**：不提供「新增歌詞」與「批次匯入」介面，歌詞需由其他方式寫入資料庫（例如匯入 rasporuno.db、或透過 API/腳本）。
- 其餘功能與 rasporuno 相同：關鍵字搜尋、日文斷詞、假名顯示、加入 rasword 單字庫、Apple Music 連結。

## 功能

- 🔍 **關鍵字搜尋**：搜尋歌名或歌詞內容
- ✂️ **日文斷詞**：自動將歌詞斷詞，方便點擊單字
- 📖 **假名顯示**：點擊日文單字顯示假名讀音
- ➕ **加入單字庫**：點擊單字可加入 rasword 單字庫
- 🎵 **Apple Music**：歌詞詳情可開啟 Apple Music 播放

## 安裝

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

## 執行

```bash
python app.py
```

預設運行在 `http://localhost:5003`（rasporuno 為 5002，避免衝突）

## 環境變數

- `RASWORD_BASE_URL`：rasword 服務地址（預設：`http://127.0.0.1:5000`）

## 資料庫

使用 SQLite，資料庫檔案：`raspomushi.db`。

### 從 uta-net 爬取歌詞（ポルノグラフィティ）

專案內建爬蟲，可從 [uta-net ポルノグラフィティ一覽](https://www.uta-net.com/artist/1686/) 與第二頁抓取歌名與歌詞並寫入 `raspomushi.db`：

```bash
# 安裝依賴（含 beautifulsoup4）
pip install -r requirements.txt

# 爬取全部（約 263 首，每次請求間隔 1.5 秒，需數分鐘）
python crawl_uta_net.py

# 僅爬前 5 首測試
python crawl_uta_net.py --limit 5
```

爬蟲會請求：

- `https://www.uta-net.com/artist/1686/`（第 1 頁歌單）
- `https://www.uta-net.com/artist/1686/0/2/`（第 2 頁歌單）
- 各曲 `https://www.uta-net.com/song/<id>/` 取得歌名與歌詞

若從 EU 等區域連線，uta-net 可能顯示 GDPR 頁面，列表會取不到曲目，需從非受限環境執行爬蟲。

## 授權

MIT License
