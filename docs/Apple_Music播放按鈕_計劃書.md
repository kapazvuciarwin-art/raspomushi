# rasporuno「Apple Music 播放按鈕」計劃書與可行性評估

## 一、需求摘要

在 rasporuno **歌詞部分**加一個**播放按鈕**，行為為：

1. 用**歌名**（歌詞標題）去查詢對應的歌曲。
2. 取得 **Apple Music** 的播放／商店連結。
3. 使用者點按鈕後**開啟該連結**（新分頁），在 Apple Music（網頁或 App）播放該首歌曲。

---

## 二、技術方案

### 2.1 如何用歌名找到 Apple Music 對應的歌？

**採用：iTunes Search API（Apple 官方、免 API Key）**

- **網址**：`https://itunes.apple.com/search`
- **參數**：`term`（搜尋關鍵字，例如歌名）、`media=music`、`limit=1`（或 5 取第一筆）、可選 `country=tw`（地區）。
- **回傳**：JSON，內含 `results[]`，每筆有 `trackId`、`trackViewUrl`、`trackName`、`artistName` 等。
- **連結**：直接使用回傳的 **`trackViewUrl`**，即為該曲在 iTunes/Apple Music 的頁面，點開即可播放（依使用者環境開啟網頁版或 App）。

**優點**：免註冊、免 API Key、不需 Apple Developer 付費帳號；實作簡單。  
**限制**：僅能「歌名（＋可選藝人）」搜尋，若歌名太籠統可能搜到別首；無授權時有合理使用與 rate limit，一般個人使用通常可接受。

### 2.2 前端還是後端呼叫？

- **建議：後端代為呼叫**  
  瀏覽器直接 `fetch('https://itunes.apple.com/search?...')` 可能受 **CORS** 限制；由 rasporuno 後端（例如 Flask）代為請求 iTunes API，再回傳「一個播放連結」給前端，可避開 CORS，且可統一加 log、錯誤處理、未來若有需要可換成其他搜尋來源。

### 2.3 API 設計建議

- **新增**：`GET /api/apple-music-link?title=歌名`（或 `q=歌名`）。
- **後端**：用 `title` 呼叫 `https://itunes.apple.com/search?term={title}&media=music&limit=1&country=tw`，解析 JSON，取 `results[0].trackViewUrl`；若無結果則回 404 或 `{ "url": null }`。
- **前端**：歌詞區（列表或詳情）加「在 Apple Music 播放」按鈕，點擊時呼叫上述 API，若有 `url` 則 `window.open(url)` 開新分頁。

### 2.4 按鈕放哪裡？

| 位置 | 說明 |
|------|------|
| **歌詞詳情頁** | 進入單首歌詞後，在標題旁（或標題下方）加「在 Apple Music 播放」按鈕，用該筆歌詞的 **title** 查詢。 |
| **歌詞列表** | 每一列歌詞旁加小圖示／按鈕，點擊用該列的 **title** 查詢並開連結。 |

可先做**詳情頁**，再視需要加列表；或兩處都做。

---

## 三、可行性評估

| 項目 | 評估 |
|------|------|
| **技術可行性** | **高**。iTunes Search API 公開、免 Key，後端用 `requests` 即可；回傳之 `trackViewUrl` 即為 Apple Music 播放頁，無需再轉換。 |
| **資料是否足夠** | **足夠**。目前歌詞有 **title**（歌名），足以作為搜尋關鍵字；若未來有「藝人」欄位，可一併帶入 `term` 提高準確度。 |
| **準確度** | **中**。僅用歌名搜尋時，若歌名常見或與他曲重名，可能命中別首；可接受「多數情況正確、偶爾需使用者自行辨識」。若要提高準確度，可日後加「藝人」欄位或讓使用者從多筆結果中選擇。 |
| **依賴與風險** | 依賴 iTunes Search API 可用性與回應格式不變；Apple 若變更或停用 API，需改為其他來源（例如 Apple Music API 需開發者帳號）。目前該 API 已存在多年，風險可接受。 |
| **法律／使用規範** | iTunes Search API 為對外公開之查詢介面，用於「提供連結至商店」屬合理使用；若日後要放 App Store 或商業用途，建議再確認 Apple 現行條款。 |

**結論**：**可行**。建議實作順序：後端新增 `GET /api/apple-music-link?title=...`，前端在歌詞詳情（及可選列表）加「在 Apple Music 播放」按鈕，點擊後呼叫 API 並以回傳之 URL 開新分頁。

---

## 四、實作要點（供後續開發參考，本次不改程式）

1. **後端**（Flask）  
   - 新增路由：例如 `@app.route('/api/apple-music-link', methods=['GET'])`。  
   - 取得 query：`title = request.args.get('title')` 或 `request.args.get('q')`。  
   - 若 `title` 為空則回 400。  
   - 使用 `requests.get('https://itunes.apple.com/search', params={'term': title, 'media': 'music', 'limit': 1, 'country': 'tw'})`。  
   - 解析 JSON，取 `results[0]['trackViewUrl']`；若 `results` 為空則回 `{ "ok": false, "url": null }` 或 404。  
   - 回傳 `{ "ok": true, "url": "..." }`。

2. **前端**  
   - 歌詞詳情：在標題旁或標題下方加按鈕「在 Apple Music 播放」（或圖示）。  
   - 點擊：`fetch('/api/apple-music-link?title=' + encodeURIComponent(lyricTitle))`，若回傳有 `url` 則 `window.open(data.url)`。  
   - 無結果時：可顯示「找不到對應歌曲」或禁用按鈕。

3. **可選**  
   - 地區參數 `country` 可由前端傳入或後端預設 `tw`。  
   - 未來若歌詞有「藝人」欄位，可改為 `term=藝人+歌名` 提高準確度。

---

## 五、小結

- **功能**：歌詞區加播放按鈕 → 用歌名查 iTunes Search API → 取得 Apple Music 連結 → 點擊開新分頁播放。  
- **可行性**：高；免 API Key、現有資料（title）即可實作。  
- **建議**：先做後端 API + 歌詞詳情頁按鈕，再視需要加列表按鈕或藝人欄位以提升準確度。

以上為計劃與評估，**尚未修改任何程式**。
