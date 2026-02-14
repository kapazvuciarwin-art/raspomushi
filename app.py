"""raspomushi - 日文歌詞瀏覽（無手動輸入），支援斷詞、假名顯示、關鍵字搜尋，並可加入 rasword 單字庫"""

import os
import re
import sys
import sqlite3
import time
import threading
from datetime import datetime
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raspomushi.db")

# rasword 單字庫服務：預設跑在本機 5000 port，可用環境變數覆蓋
RASWORD_BASE_URL = os.getenv("RASWORD_BASE_URL", "http://127.0.0.1:5000")

# AI API 設定
GEMINI_MODEL_PRIORITY = [
    "gemini-3.0-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# 保留此列表用於向後兼容，實際使用 preferred_models 和 fallback_models
GROQ_FREE_MODELS = [
    "gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instruct",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

# uta-net 爬蟲設定
UTA_NET_BASE_URL = "https://www.uta-net.com"
UTA_NET_ARTIST_ID = 1686  # ポルノグラフィティ
UTA_NET_PAGE_PATHS = [
    "/artist/{}/".format(UTA_NET_ARTIST_ID),
    "/artist/{}/0/2/".format(UTA_NET_ARTIST_ID),
]
UTA_NET_REQUEST_DELAY = 1.5
UTA_NET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
}

_TAGGER = None
_KAKASI = None


def _katakana_to_hiragana(s: str) -> str:
    """UniDic 回傳片假名，轉成平假名。"""
    if not s:
        return s
    result = []
    for c in s:
        code = ord(c)
        # 片假名 U+30A1..U+30F6 → 平假名 U+3041..U+3096
        if 0x30A1 <= code <= 0x30F6:
            result.append(chr(code - 0x60))
        else:
            result.append(c)
    return "".join(result)


def to_furigana(text: str) -> str:
    """
    將日文（含漢字）轉成假名（平假名為主）。
    優先使用 MeCab + UniDic（fugashi）以正確處理訓讀；失敗時 fallback pykakasi。
    """
    if not text:
        return ""
    # 1) MeCab + UniDic（fugashi）
    try:
        global _TAGGER
        if _TAGGER is None:
            from fugashi import Tagger
            _TAGGER = Tagger()
        parts = []
        for word in _TAGGER(text):
            kana = getattr(word.feature, "kana", None) or getattr(word.feature, "pron", None)
            if kana:
                parts.append(_katakana_to_hiragana(kana))
            else:
                parts.append(word.surface)
        if parts:
            return "".join(parts).strip()
    except Exception:
        pass
    # 2) fallback: pykakasi
    global _KAKASI
    if _KAKASI is None:
        from pykakasi import kakasi
        k = kakasi()
        k.setMode("J", "H")
        k.setMode("K", "H")
        k.setMode("H", "H")
        k.setMode("r", "Hepburn")
        _KAKASI = k.getConverter()
    return (_KAKASI.do(text) or "").strip()


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def _get_setting(key):
    """取得設定值"""
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def _set_setting(key, value):
    """設定值"""
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lyrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_opened_at TEXT,
            view_count INTEGER DEFAULT 0,
            saved_word_count INTEGER DEFAULT 0
        )
    """)
    # 為現有資料庫新增欄位（如果不存在）
    try:
        conn.execute("ALTER TABLE lyrics ADD COLUMN last_opened_at TEXT")
    except sqlite3.OperationalError:
        pass  # 欄位已存在
    try:
        conn.execute("ALTER TABLE lyrics ADD COLUMN view_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 欄位已存在
    try:
        conn.execute("ALTER TABLE lyrics ADD COLUMN saved_word_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 欄位已存在
    
    # 設定表（用於存儲 AI API 設定）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)
    """)
    
    # 翻譯表（存儲多個版本的翻譯）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lyric_id INTEGER NOT NULL,
            version_name TEXT NOT NULL,
            translation_data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (lyric_id) REFERENCES lyrics(id) ON DELETE CASCADE
        )
    """)
    
    conn.commit()
    conn.close()


def segment_japanese_text(text: str) -> list:
    """
    簡單的日文斷詞（以空白、標點符號分割，保留日文字元）。
    更精確的斷詞可以使用 janome 或 mecab，這裡先用簡單方式。
    會嘗試在助詞、動詞變化等位置分割，讓單字更容易點擊。
    保留原始空格和換行，空格維持為空格顯示。
    """
    if not text:
        return []
    
    # 保留原始空格和換行，不進行任何空白處理
    # 日文助詞和常見分割點
    # 助詞：は、が、を、に、で、と、から、まで、より、へ、の、も、など
    # 動詞變化：ます、です、だ、である、て、た、だ、など
    # 標點符號
    split_pattern = r'([\s，。、！？；：\n])|([はがをにでとからまでよりへのも]+)|([ますですだてた]+)'
    
    segments = []
    last_end = 0
    
    for match in re.finditer(split_pattern, text):
        # 添加匹配前的文字
        if match.start() > last_end:
            word = text[last_end:match.start()]
            if word:
                segments.append(word)
        
        # 添加分隔符（保留所有空白、換行和標點）
        if match.group(1):  # 空白、換行或標點
            if match.group(1) == '\n':
                segments.append('\n')
            elif match.group(1) in ' \t':
                # 空格和 tab：保留原樣，前端顯示為空格
                segments.append(match.group(1))
            elif match.group(1).strip():
                # 標點符號
                segments.append(match.group(1))
        elif match.group(2) or match.group(3):  # 助詞或動詞變化
            # 將助詞/動詞變化作為單獨的詞
            segments.append(match.group(0))
        
        last_end = match.end()
    
    # 添加剩餘文字
    if last_end < len(text):
        word = text[last_end:]
        if word:
            segments.append(word)
    
    # 如果沒有匹配到任何分割點，使用簡單分割
    if not segments:
        segments = [text]
    
    # 過濾空字串（但保留空格標記）
    segments = [s for s in segments if s]
    
    return segments


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/lyrics', methods=['GET'])
def get_lyrics():
    """取得所有歌詞列表，支援關鍵字搜尋和排序"""
    keyword = request.args.get('keyword', '').strip()
    sort_by = request.args.get('sort', 'recent')  # recent, popular, words
    
    conn = get_db()
    
    # 建立排序 SQL
    if sort_by == 'popular':
        order_by = 'view_count DESC, last_opened_at DESC'
    elif sort_by == 'words':
        order_by = 'saved_word_count DESC, last_opened_at DESC'
    else:  # recent (預設)
        order_by = 'last_opened_at DESC, created_at DESC'
    
    if keyword:
        # 關鍵字搜尋：在標題或內容中搜尋
        lyrics = conn.execute(f"""
            SELECT * FROM lyrics 
            WHERE title LIKE ? OR content LIKE ?
            ORDER BY {order_by}
        """, (f'%{keyword}%', f'%{keyword}%')).fetchall()
    else:
        lyrics = conn.execute(f"""
            SELECT * FROM lyrics 
            ORDER BY {order_by}
        """).fetchall()
    
    conn.close()
    return jsonify([dict(l) for l in lyrics])


@app.route('/api/lyrics/<int:lyric_id>', methods=['GET'])
def get_lyric(lyric_id):
    """取得單首歌詞詳情，並更新開啟時間和點閱次數"""
    conn = get_db()
    lyric = conn.execute("""
        SELECT * FROM lyrics WHERE id = ?
    """, (lyric_id,)).fetchone()
    
    if not lyric:
        conn.close()
        return jsonify({'error': '找不到歌詞'}), 404
    
    # 更新 last_opened_at 和 view_count
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE lyrics 
        SET last_opened_at = ?, view_count = view_count + 1
        WHERE id = ?
    """, (now, lyric_id))
    conn.commit()
    
    # 重新取得更新後的資料
    lyric = conn.execute("""
        SELECT * FROM lyrics WHERE id = ?
    """, (lyric_id,)).fetchone()
    conn.close()
    
    return jsonify(dict(lyric))


@app.route('/api/lyrics/<int:lyric_id>', methods=['DELETE'])
def delete_lyric(lyric_id):
    """刪除歌詞"""
    conn = get_db()
    conn.execute("DELETE FROM lyrics WHERE id = ?", (lyric_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


def _fetch_uta_net(url):
    """請求 uta-net 頁面"""
    r = requests.get(url, headers=UTA_NET_HEADERS, timeout=15)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _extract_song_ids_from_artist_page(html):
    """從歌手一覽頁解析出所有歌曲頁的 ID"""
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for a in soup.find_all("a", href=True):
        m = re.match(r"/song/(\d+)/?", a.get("href", ""))
        if m:
            sid = m.group(1)
            if sid not in ids:
                ids.append(sid)
    return ids


def _extract_title_and_lyrics(html):
    """從歌曲頁解析歌名與歌詞本文"""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    h2 = soup.find("h2")
    if h2:
        title = (h2.get_text(strip=True) or "").strip()
    if not title and soup.title:
        t = soup.title.string or ""
        t = re.sub(r"\s*歌詞\s*-\s*歌ネット\s*$", "", t).strip()
        if t:
            parts = t.split(None, 1)
            title = parts[1] if len(parts) > 1 else parts[0]
    if not title:
        title = "unknown"

    lyrics_div = soup.find("div", id="kashi_area") or soup.find("div", id="kashi")
    if not lyrics_div:
        for c in ("kashi_area", "kashi", "song_table"):
            lyrics_div = soup.find("div", class_=lambda x: x and c in (x or ""))
            if lyrics_div:
                break
    if not lyrics_div:
        for div in soup.find_all("div"):
            if div.get("id") in ("header", "footer", "nav", "menu"):
                continue
            text = div.get_text(separator="\n", strip=True)
            if len(text) > 100 and ("歌詞" in text or "作詞" in div.get_text()):
                lyrics_div = div
                break

    content = ""
    if lyrics_div:
        for tag in lyrics_div.find_all(["script", "style"]):
            tag.decompose()
        content = lyrics_div.get_text(separator="\n", strip=True)
        if "この歌詞をマイ歌ネットに登録" in content:
            content = content.split("この歌詞をマイ歌ネットに登録")[0].strip()
        if "この曲のフレーズを投稿" in content:
            content = content.split("この曲のフレーズを投稿")[0].strip()
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

    return title, content


def _do_check_new_songs():
    """背景執行：檢查新歌並寫入資料庫"""
    try:
        conn = get_db()
        now = datetime.now().isoformat()

        # 1) 取得資料庫中現有的歌名集合
        existing_titles = set()
        for row in conn.execute("SELECT title FROM lyrics").fetchall():
            existing_titles.add(row[0])

        # 2) 爬取歌手列表頁，取得所有 song IDs
        all_song_ids = []
        for path in UTA_NET_PAGE_PATHS:
            url = urljoin(UTA_NET_BASE_URL, path)
            try:
                html = _fetch_uta_net(url)
                ids = _extract_song_ids_from_artist_page(html)
                all_song_ids.extend(ids)
                time.sleep(UTA_NET_REQUEST_DELAY)
            except Exception as e:
                print(f"[raspomushi] 無法取得歌手列表頁 {url}: {e}", flush=True)
                conn.close()
                return

        # 去重
        seen = set()
        unique_ids = []
        for i in all_song_ids:
            if i not in seen:
                seen.add(i)
                unique_ids.append(i)

        if not unique_ids:
            print("[raspomushi] 未找到任何歌曲（可能遇到 EU/GDPR 限制）", flush=True)
            conn.close()
            return
        
        print(f"[raspomushi] 開始檢查 {len(unique_ids)} 首歌曲...", flush=True)

        # 3) 對每個 song ID，爬取歌詞頁，比對資料庫，只新增不重複的
        inserted = 0
        skipped = 0
        errors = []

        for i, sid in enumerate(unique_ids):
            url = urljoin(UTA_NET_BASE_URL, "/song/{}/".format(sid))
            try:
                html = _fetch_uta_net(url)
                title, content = _extract_title_and_lyrics(html)
            except Exception as e:
                errors.append({'song_id': sid, 'error': str(e)})
                time.sleep(UTA_NET_REQUEST_DELAY)
                continue

            if not content or len(content) < 10:
                skipped += 1
                time.sleep(UTA_NET_REQUEST_DELAY)
                continue

            # 檢查是否已存在
            cur = conn.execute("SELECT id FROM lyrics WHERE title = ?", (title,))
            row = cur.fetchone()
            if row:
                skipped += 1
            else:
                # 新歌，寫入資料庫
                try:
                    conn.execute(
                        """INSERT INTO lyrics (title, content, created_at, updated_at)
                           VALUES (?, ?, ?, ?)""",
                        (title, content, now, now),
                    )
                    conn.commit()  # 每首立即 commit，避免重載時丟失資料
                    inserted += 1
                    print(f"[raspomushi] [{i+1}/{len(unique_ids)}] 新增：{title}", flush=True)
                except Exception as e:
                    errors.append({'title': title, 'error': str(e)})

            time.sleep(UTA_NET_REQUEST_DELAY)

        conn.close()
        print(f"[raspomushi] 檢查完成：找到 {len(unique_ids)} 首，新增 {inserted} 首，跳過 {skipped} 首", flush=True)

    except Exception as e:
        print(f"[raspomushi] 檢查新歌錯誤：{e}", flush=True)
    finally:
        # 確保無論如何都會重置標記
        if hasattr(check_new_songs, '_running'):
            check_new_songs._running = False


@app.route('/settings')
def settings():
    return render_template('settings.html')


@app.route('/api/check-new-songs', methods=['POST'])
def check_new_songs():
    """檢查 uta-net 是否有新歌（資料庫中還沒有的），並自動爬取寫入（非同步背景執行）"""
    # 檢查是否已有爬蟲在執行
    if hasattr(check_new_songs, '_running') and check_new_songs._running:
        return jsonify({
            'success': False,
            'error': '檢查新歌正在執行中，請稍候'
        }), 409

    # 啟動背景執行
    check_new_songs._running = True
    thread = threading.Thread(target=_do_check_new_songs, daemon=True)
    thread.start()

    return jsonify({
        'success': True,
        'message': '已開始檢查新歌，請稍候數分鐘後重新整理頁面查看結果'
    })


# Apple Music 搜尋用藝人（本實例僅收錄 ポルノグラフティ）
APPLE_MUSIC_ARTIST = "ポルノグラフティ"


@app.route('/api/apple-music-link', methods=['GET'])
def get_apple_music_link():
    """依歌名（+ 固定藝人）查 iTunes Search API，回傳 Apple Music 播放連結"""
    title = (request.args.get('title') or request.args.get('q') or '').strip()
    if not title:
        return jsonify({'ok': False, 'url': None, 'error': 'missing title'}), 400
    term = f"{title} {APPLE_MUSIC_ARTIST}"
    try:
        r = requests.get(
            'https://itunes.apple.com/search',
            params={'term': term, 'media': 'music', 'limit': 1, 'country': 'tw'},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get('results') or []
        if not results:
            return jsonify({'ok': False, 'url': None})
        url = results[0].get('trackViewUrl')
        if not url:
            return jsonify({'ok': False, 'url': None})
        return jsonify({'ok': True, 'url': url})
    except Exception as e:
        return jsonify({'ok': False, 'url': None, 'error': str(e)}), 500


@app.route('/api/furigana', methods=['GET'])
def get_furigana():
    """取得日文詞的假名讀音"""
    text = request.args.get('text', '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'missing text'}), 400
    
    reading = to_furigana(text)
    # 確保 JSON 響應使用 UTF-8 編碼
    response = jsonify({'ok': True, 'text': text, 'reading': reading})
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response


@app.route('/api/segment', methods=['POST'])
def segment_text():
    """將日文文本斷詞"""
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    
    if not text:
        return jsonify({'ok': False, 'error': 'missing text'}), 400
    
    segments = segment_japanese_text(text)
    return jsonify({'ok': True, 'segments': segments})


def call_gemini(api_key, prompt):
    """Gemini：依優先順序嘗試模型。"""
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError("請安裝：pip install google-generativeai")
    genai.configure(api_key=api_key)
    last_err = None
    for model_name in GEMINI_MODEL_PRIORITY:
        try:
            model = genai.GenerativeModel(model_name)
            r = model.generate_content(prompt)
            return (r.text or "").strip(), model_name
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Gemini 無可用模型：{last_err}")


def call_groq(api_key, prompt):
    """Groq：優先使用 gpt-oss-120b，找不到則嘗試其他免費模型。會嘗試所有模型直到找到可用的。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    errors = []  # 記錄所有錯誤
    
    # 優先嘗試 gpt-oss-120b
    preferred_models = [
        "gpt-oss-120b",
    ]
    
    # 其他免費模型作為備選
    fallback_models = [
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instruct",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ]
    
    # 合併所有模型列表
    all_models = preferred_models + fallback_models
    
    # 嘗試所有模型
    for model in all_models:
        try:
            r = requests.post(
                GROQ_API_URL,
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                },
                timeout=60,
            )
            if r.status_code == 200:
                data = r.json()
                if "choices" in data and data["choices"]:
                    text = data["choices"][0].get("message", {}).get("content", "")
                    if text and text.strip():
                        return (text.strip(), model)
                    else:
                        errors.append(f"{model}: 返回空內容")
            else:
                # 記錄錯誤
                try:
                    error_data = r.json()
                    if "error" in error_data:
                        error_msg = error_data['error'].get('message', str(r.status_code))
                        errors.append(f"{model}: {error_msg}")
                    else:
                        errors.append(f"{model}: HTTP {r.status_code} - {r.text[:100]}")
                except:
                    errors.append(f"{model}: HTTP {r.status_code} - {r.text[:100] if r.text else '無回應'}")
        except Exception as e:
            errors.append(f"{model}: {str(e)}")
            continue
    
    # 所有模型都失敗
    error_summary = "\n".join(errors[:10])  # 最多顯示前10個錯誤
    if len(errors) > 10:
        error_summary += f"\n... 還有 {len(errors) - 10} 個模型失敗"
    
    error_summary += "\n\n建議：如果 Groq 模型都不可用，請考慮使用 AI Studio (Gemini) API。"
    
    raise RuntimeError(f"Groq 所有模型都失敗（已嘗試 {len(all_models)} 個模型）：\n{error_summary}")


def translate_japanese_to_chinese(text, api_provider, api_key):
    """
    將日文逐句翻譯成繁體中文。
    返回格式：每行一個句子，原文和翻譯對照。
    """
    # 將文本按行分割，保留原始結構
    lines = text.split('\n')
    sentences = []
    for line in lines:
        line = line.strip()
        if not line:
            sentences.append('')
            continue
        # 按句號、問號、驚嘆號分割
        parts = re.split(r'([。！？])', line)
        current = ''
        for i, part in enumerate(parts):
            current += part
            if part in ['。', '！', '？']:
                if current.strip():
                    sentences.append(current.strip())
                current = ''
        if current.strip():
            sentences.append(current.strip())
    
    # 過濾空句子
    sentences = [s for s in sentences if s.strip()]
    
    # 構建翻譯提示
    prompt = """請將以下日文歌詞逐句翻譯成繁體中文。請保持原文的格式和結構，每行一個句子，格式如下：

原文：日文句子
翻譯：中文翻譯

請確保翻譯準確、自然、符合中文表達習慣，並且逐句對照。以下是需要翻譯的內容：

""" + '\n'.join(sentences)
    
    try:
        if api_provider == 'gemini':
            result, model = call_gemini(api_key, prompt)
        elif api_provider == 'groq':
            result, model = call_groq(api_key, prompt)
        else:
            raise ValueError(f"不支援的 API 提供者：{api_provider}")
        
        return result, model
    except Exception as e:
        raise RuntimeError(f"翻譯失敗：{str(e)}")


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """取得 AI API 設定"""
    api_provider = _get_setting('api_provider') or 'gemini'
    gemini_api_key = _get_setting('gemini_api_key') or ''
    groq_api_key = _get_setting('groq_api_key') or ''
    return jsonify({
        'api_provider': api_provider,
        'gemini_api_key': gemini_api_key,
        'groq_api_key': groq_api_key,
    })


@app.route('/api/settings', methods=['POST'])
def save_settings():
    """儲存 AI API 設定"""
    data = request.get_json() or {}
    api_provider = data.get('api_provider', 'gemini')
    gemini_api_key = (data.get('gemini_api_key') or '').strip()
    groq_api_key = (data.get('groq_api_key') or '').strip()
    
    _set_setting('api_provider', api_provider)
    if gemini_api_key:
        _set_setting('gemini_api_key', gemini_api_key)
    if groq_api_key:
        _set_setting('groq_api_key', groq_api_key)
    
    return jsonify({'success': True})


@app.route('/api/lyrics/<int:lyric_id>/translate', methods=['POST'])
def translate_lyric(lyric_id):
    """翻譯歌詞"""
    data = request.get_json() or {}
    version_name = (data.get('version_name') or '').strip() or '預設版本'
    
    # 取得歌詞內容
    conn = get_db()
    lyric = conn.execute("SELECT content FROM lyrics WHERE id = ?", (lyric_id,)).fetchone()
    conn.close()
    
    if not lyric:
        return jsonify({'success': False, 'error': '找不到歌詞'}), 404
    
    # 取得 API 設定
    api_provider = _get_setting('api_provider') or 'gemini'
    if api_provider == 'gemini':
        api_key = os.getenv("GEMINI_API_KEY") or _get_setting('gemini_api_key') or ''
    else:
        api_key = _get_setting('groq_api_key') or ''
    
    if not api_key:
        return jsonify({'success': False, 'error': '請先在設定頁面配置 API Key'}), 400
    
    try:
        # 執行翻譯
        translation_text, model_used = translate_japanese_to_chinese(
            lyric['content'], api_provider, api_key
        )
        
        # 儲存翻譯
        now = datetime.now().isoformat()
        conn = get_db()
        conn.execute(
            """INSERT INTO translations (lyric_id, version_name, translation_data, created_at)
               VALUES (?, ?, ?, ?)""",
            (lyric_id, version_name, translation_text, now)
        )
        conn.commit()
        translation_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        
        return jsonify({
            'success': True,
            'translation_id': translation_id,
            'translation': translation_text,
            'model_used': model_used,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/lyrics/<int:lyric_id>/translations', methods=['GET'])
def get_translations(lyric_id):
    """取得歌詞的所有翻譯版本"""
    conn = get_db()
    translations = conn.execute(
        """SELECT id, version_name, translation_data, created_at
           FROM translations WHERE lyric_id = ? ORDER BY created_at DESC""",
        (lyric_id,)
    ).fetchall()
    conn.close()
    
    result = []
    for t in translations:
        result.append({
            'id': t['id'],
            'version_name': t['version_name'],
            'translation_data': t['translation_data'],
            'created_at': t['created_at'],
        })
    
    return jsonify(result)


@app.route('/api/translations/<int:translation_id>', methods=['DELETE'])
def delete_translation(translation_id):
    """刪除翻譯版本"""
    conn = get_db()
    conn.execute("DELETE FROM translations WHERE id = ?", (translation_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/rasword/add-word', methods=['POST'])
def api_rasword_add_word():
    """
    將歌詞中點擊到的日文詞，透過 rasword 服務
    （先檢查是否已存在，如無則呼叫 AI 生成並寫入 rasword 的資料庫）。
    """
    data = request.get_json() or {}
    word = (data.get("word") or "").strip()
    if not word:
        return jsonify({"ok": False, "error": "missing word"}), 400

    base = (RASWORD_BASE_URL or "").rstrip("/")
    if not base:
        return jsonify({"ok": False, "error": "RASWORD_BASE_URL 未設定"}), 500

    # 1) 先檢查 rasword 是否已存在該單字
    try:
        check_resp = requests.post(
            f"{base}/api/words/check",
            json={"japanese_word": word},
            timeout=15,
        )
        if check_resp.status_code == 200:
            payload = check_resp.json()
            if payload.get("exists"):
                w = payload.get("word") or {}
                brief = {
                    "japanese_word": w.get("japanese_word") or word,
                    "kana_form": w.get("kana_form") or "",
                    "kanji_form": w.get("kanji_form") or "",
                    "chinese_short": w.get("chinese_short") or "",
                    "chinese_meaning": "",
                }
                return jsonify(
                    {"ok": True, "exists": True, "created": False, "word": brief}
                )
    except Exception as e:
        # 檢查失敗不致命，繼續嘗試生成
        print(f"[raspomushi] rasword /api/words/check 失敗: {e}")

    # 2) 呼叫 rasword 的 /api/generate，用 rasword 內建的 GEMINI_API_KEY
    try:
        gen_resp = requests.post(
            f"{base}/api/generate",
            json={"japanese_word": word, "api_provider": "gemini"},
            timeout=90,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"呼叫 rasword /api/generate 失敗: {e}"}), 502

    try:
        gen_data = gen_resp.json()
    except Exception:
        gen_data = {}

    if not gen_resp.ok or gen_data.get("error"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": gen_data.get("error")
                    or f"rasword /api/generate 失敗（HTTP {gen_resp.status_code}）",
                }
            ),
            502,
        )

    # 3) 將生成結果寫入 rasword 的 /api/words
    add_payload = {
        "japanese_word": word,
        "part_of_speech": gen_data.get("part_of_speech", ""),
        "sentence1": gen_data.get("sentence1", ""),
        "sentence2": gen_data.get("sentence2", ""),
        "chinese_meaning": gen_data.get("chinese_meaning", ""),
        "chinese_short": gen_data.get("chinese_short", ""),
        "jlpt_level": gen_data.get("jlpt_level", ""),
        "kana_form": gen_data.get("kana_form", ""),
        "kanji_form": gen_data.get("kanji_form", ""),
        "common_form": gen_data.get("common_form", "kanji"),
        "source": "lyrics",  # 從歌詞新增
    }

    try:
        add_resp = requests.post(
            f"{base}/api/words",
            json=add_payload,
            timeout=30,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"呼叫 rasword /api/words 失敗: {e}"}), 502

    try:
        add_json = add_resp.json()
    except Exception:
        add_json = {}

    if not add_resp.ok or not add_json.get("success"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": add_json.get("error")
                    or f"rasword /api/words 新增失敗（HTTP {add_resp.status_code}）",
                }
            ),
            502,
        )

    brief = {
        "japanese_word": word,
        "kana_form": gen_data.get("kana_form", ""),
        "kanji_form": gen_data.get("kanji_form", ""),
        "chinese_short": gen_data.get("chinese_short", ""),
        "chinese_meaning": gen_data.get("chinese_meaning", ""),
    }
    return jsonify({"ok": True, "exists": False, "created": True, "word": brief})


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5003, debug=True)
