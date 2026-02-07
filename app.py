"""rasporuno - 日文歌詞資料庫，支援斷詞、假名顯示、關鍵字搜尋，並可加入 rasword 單字庫"""

import os
import re
import sqlite3
from datetime import datetime
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rasporuno.db")

# rasword 單字庫服務：預設跑在本機 5000 port，可用環境變數覆蓋
RASWORD_BASE_URL = os.getenv("RASWORD_BASE_URL", "http://127.0.0.1:5000")

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


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lyrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def segment_japanese_text(text: str) -> list:
    """
    簡單的日文斷詞（以空白、標點符號分割，保留日文字元）。
    更精確的斷詞可以使用 janome 或 mecab，這裡先用簡單方式。
    會嘗試在助詞、動詞變化等位置分割，讓單字更容易點擊。
    保留原始空格和換行，空格會在前端轉換為換行顯示。
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
                # 空格和 tab：標記為特殊字串，前端會轉換為換行
                # 使用特殊標記 '__SPACE__' 以便前端識別
                segments.append('__SPACE__')
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
    """取得所有歌詞列表，支援關鍵字搜尋"""
    keyword = request.args.get('keyword', '').strip()
    conn = get_db()
    
    if keyword:
        # 關鍵字搜尋：在標題或內容中搜尋
        lyrics = conn.execute("""
            SELECT * FROM lyrics 
            WHERE title LIKE ? OR content LIKE ?
            ORDER BY updated_at DESC
        """, (f'%{keyword}%', f'%{keyword}%')).fetchall()
    else:
        lyrics = conn.execute("""
            SELECT * FROM lyrics 
            ORDER BY updated_at DESC
        """).fetchall()
    
    conn.close()
    return jsonify([dict(l) for l in lyrics])


@app.route('/api/lyrics', methods=['POST'])
def add_lyric():
    """新增單首歌詞"""
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    
    if not title or not content:
        return jsonify({'success': False, 'error': '標題和歌詞內容不能為空'}), 400
    
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO lyrics (title, content, created_at, updated_at)
        VALUES (?, ?, ?, ?)
    """, (title, content, now, now))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


@app.route('/api/lyrics/batch', methods=['POST'])
def batch_add_lyrics():
    """批次新增歌詞"""
    data = request.get_json() or {}
    batch_text = (data.get('batch_text') or '').strip()
    
    if not batch_text:
        return jsonify({'success': False, 'error': '批次資料不能為空'}), 400
    
    # 解析批次格式：=== 歌名1.txt ===\n歌詞內容\n\n=== 歌名2.txt ===\n歌詞內容
    pattern = r'=== (.+?)\.txt ===\s*\n(.*?)(?=\n=== |$)'
    matches = re.findall(pattern, batch_text, re.DOTALL)
    
    if not matches:
        return jsonify({'success': False, 'error': '無法解析批次格式，請確認格式為：=== 歌名.txt ===\\n歌詞內容'}), 400
    
    now = datetime.now().isoformat()
    conn = get_db()
    success_count = 0
    errors = []
    
    for title, content in matches:
        title = title.strip()
        content = content.strip()
        
        if not title or not content:
            errors.append({'title': title or '(無標題)', 'error': '標題或內容為空'})
            continue
        
        try:
            conn.execute("""
                INSERT INTO lyrics (title, content, created_at, updated_at)
                VALUES (?, ?, ?, ?)
            """, (title, content, now, now))
            success_count += 1
        except Exception as e:
            errors.append({'title': title, 'error': str(e)})
    
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'count': success_count,
        'errors': errors
    })


@app.route('/api/lyrics/<int:lyric_id>', methods=['GET'])
def get_lyric(lyric_id):
    """取得單首歌詞詳情"""
    conn = get_db()
    lyric = conn.execute("""
        SELECT * FROM lyrics WHERE id = ?
    """, (lyric_id,)).fetchone()
    conn.close()
    
    if not lyric:
        return jsonify({'error': '找不到歌詞'}), 404
    
    return jsonify(dict(lyric))


@app.route('/api/lyrics/<int:lyric_id>', methods=['DELETE'])
def delete_lyric(lyric_id):
    """刪除歌詞"""
    conn = get_db()
    conn.execute("DELETE FROM lyrics WHERE id = ?", (lyric_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


@app.route('/api/lyrics/delete-all', methods=['POST'])
def delete_all_lyrics():
    """刪除全部歌詞（需要確認碼）"""
    data = request.get_json() or {}
    confirm_code = (data.get('confirm_code') or '').strip()
    
    # 確認碼必須是 "DELETE_ALL"
    if confirm_code != 'DELETE_ALL':
        return jsonify({'success': False, 'error': '確認碼錯誤'}), 400
    
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM lyrics").fetchone()[0]
    conn.execute("DELETE FROM lyrics")
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'deleted_count': count
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
        print(f"[rasporuno] rasword /api/words/check 失敗: {e}")

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
    app.run(host='0.0.0.0', port=5002, debug=True)
