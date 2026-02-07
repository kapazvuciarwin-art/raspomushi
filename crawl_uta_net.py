#!/usr/bin/env python3
"""
爬取 uta-net.com 指定歌手的歌名與歌詞，寫入 raspomushi.db。
預設：ポルノグラフィティ（artist_id=1686），頁面 1 與 2。

用法：
  python crawl_uta_net.py           # 爬取全部
  python crawl_uta_net.py --limit 5 # 只爬前 5 首（測試用）
"""
import argparse
import os
import re
import sqlite3
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# 與 app.py 共用同一個資料庫
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(SCRIPT_DIR, "raspomushi.db")

BASE_URL = "https://www.uta-net.com"
ARTIST_ID = 1686  # ポルノグラフィティ
# 歌手列表頁：/artist/1686/ 為第1頁，/artist/1686/0/2/ 為第2頁（約 263 曲）
ARTIST_PAGE_PATHS = [
    "/artist/{}/".format(ARTIST_ID),
    "/artist/{}/0/2/".format(ARTIST_ID),
]
REQUEST_DELAY = 1.5  # 每次請求間隔（秒）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
}


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lyrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lyrics_title ON lyrics(title)")
    conn.commit()


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def extract_song_ids_from_artist_page(html):
    """從歌手一覽頁解析出所有歌曲頁的 ID（/song/12345/ -> 12345）。"""
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for a in soup.find_all("a", href=True):
        m = re.match(r"/song/(\d+)/?", a.get("href", ""))
        if m:
            sid = m.group(1)
            if sid not in ids:
                ids.append(sid)
    return ids


def extract_title_and_lyrics(html, song_url):
    """從歌曲頁解析歌名與歌詞本文。"""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    # 歌名：常見在 h2（曲名）或 title 裡 "歌手名 曲名 歌詞"
    h2 = soup.find("h2")
    if h2:
        title = (h2.get_text(strip=True) or "").strip()
    if not title and soup.title:
        # title 多為 "ポルノグラフィティ 曲名 歌詞 - 歌ネット"
        t = soup.title.string or ""
        t = re.sub(r"\s*歌詞\s*-\s*歌ネット\s*$", "", t).strip()
        if t:
            parts = t.split(None, 1)
            title = parts[1] if len(parts) > 1 else parts[0]
    if not title:
        title = "unknown"

    # 歌詞：uta-net 常見在 id="kashi_area" 或 class 含 kashi 的 div
    lyrics_div = soup.find("div", id="kashi_area") or soup.find("div", id="kashi")
    if not lyrics_div:
        for c in ("kashi_area", "kashi", "song_table"):
            lyrics_div = soup.find("div", class_=lambda x: x and c in (x or ""))
            if lyrics_div:
                break
    if not lyrics_div:
        # 備援：取第一個含大量文字的 div（排除 nav/footer）
        for div in soup.find_all("div"):
            if div.get("id") in ("header", "footer", "nav", "menu"):
                continue
            text = div.get_text(separator="\n", strip=True)
            if len(text) > 100 and "歌詞" in text or "作詞" in div.get_text():
                # 可能含歌詞區，取該 div 內純文字
                lyrics_div = div
                break

    content = ""
    if lyrics_div:
        # 移除 script/style，取文字並保留換行
        for tag in lyrics_div.find_all(["script", "style"]):
            tag.decompose()
        content = lyrics_div.get_text(separator="\n", strip=True)
        # 常見干擾：去掉「この歌詞をマイ歌ネットに登録」之後的內容
        if "この歌詞をマイ歌ネットに登録" in content:
            content = content.split("この歌詞をマイ歌ネットに登録")[0].strip()
        if "この曲のフレーズを投稿" in content:
            content = content.split("この曲のフレーズを投稿")[0].strip()
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

    return title, content


def main():
    parser = argparse.ArgumentParser(description="爬取 uta-net 歌手歌詞寫入 raspomushi.db")
    parser.add_argument("--limit", type=int, default=0, help="只爬前 N 首（0=全部）")
    args = parser.parse_args()

    conn = get_db()
    init_db(conn)
    now = datetime.now().isoformat()

    all_ids = []
    for path in ARTIST_PAGE_PATHS:
        url = urljoin(BASE_URL, path)
        print("Fetching list:", url)
        try:
            html = fetch(url)
            ids = extract_song_ids_from_artist_page(html)
            all_ids.extend(ids)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print("Error fetching list:", e)
            continue

    # 去重並保持順序
    seen = set()
    unique_ids = []
    for i in all_ids:
        if i not in seen:
            seen.add(i)
            unique_ids.append(i)

    if args.limit > 0:
        unique_ids = unique_ids[: args.limit]
        print("Limited to first {} song(s).".format(len(unique_ids)))
    else:
        print("Found {} song(s).".format(len(unique_ids)))
    if not unique_ids:
        print("No song IDs found. Check artist page URL or EU/GDPR gate.")
        conn.close()
        return

    inserted = 0
    updated = 0
    for i, sid in enumerate(unique_ids):
        url = urljoin(BASE_URL, "/song/{}/".format(sid))
        try:
            html = fetch(url)
            title, content = extract_title_and_lyrics(html, url)
        except Exception as e:
            print("  [{}] Error {}: {}".format(sid, url, e))
            time.sleep(REQUEST_DELAY)
            continue

        if not content or len(content) < 10:
            print("  [{}] Skip (no lyrics): {}".format(sid, title[:40]))
            time.sleep(REQUEST_DELAY)
            continue

        try:
            cur = conn.execute(
                "SELECT id FROM lyrics WHERE title = ?", (title,)
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    "UPDATE lyrics SET content = ?, updated_at = ? WHERE id = ?",
                    (content, now, row["id"]),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO lyrics (title, content, created_at, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (title, content, now, now),
                )
                inserted += 1
        except Exception as e:
            print("  DB error for {}: {}".format(title[:30], e))

        print("  [{}/{}] {}: {} chars".format(i + 1, len(unique_ids), title[:50], len(content)))
        time.sleep(REQUEST_DELAY)

    conn.commit()
    conn.close()
    print("Done. Inserted: {}, Updated: {}.".format(inserted, updated))


if __name__ == "__main__":
    main()
