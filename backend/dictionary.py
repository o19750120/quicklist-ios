"""離線日文字典：建置與查詢。

不打任何 API。字典是靜態資料，查一個詞的意思不需要模型 ——
所以整本字典直接放進資料庫，App 查表就好。

三個來源，全部 CC BY-SA 4.0：

    JMdict    一般詞彙，21.8 萬條目（EDRDG）—— 英文釋義、讀音、詞性
    JMnedict  人名地名，74.3 萬條目（EDRDG）
    Tomoshi   JMdict 全量的**繁體中文**釋義，21.7 萬條 / 25.1 萬義項（Y1Z）

中文釋義沒有現成的開放資源這件事查過很多次，Tomoshi 是唯一一個
規模夠、授權乾淨、而且是繁體的。它的產製過程用了 LLM 輔助翻譯，
但成品是靜態資料表 —— 我們查詢時純查表，不碰任何模型。

出處標示是 CC BY-SA 的義務，App 裡要放（見 ATTRIBUTION）。

用法：
    python backend/dictionary.py --build          # 下載並建成 SQLite
    python backend/dictionary.py --lookup 名古屋   # 查一個詞
    python backend/dictionary.py --coverage       # 拿真實逐字稿量涵蓋率
    python backend/dictionary.py --audit          # 稽核中文釋義的簡繁轉換品質
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DEFAULT_DB = Path(__file__).parent / "data" / "jadict.sqlite"

# scriptin/jmdict-simplified 把 EDRDG 的 XML 轉成 JSON，內容一樣但好處理得多
RELEASE = "3.6.2+20260831182826"
BASE = ("https://github.com/scriptin/jmdict-simplified/releases/download/"
        + RELEASE.replace("+", "%2B"))
SOURCES = {
    "jmdict": BASE + "/jmdict-eng-" + RELEASE + ".json.zip",
    "jmnedict": BASE + "/jmnedict-all-" + RELEASE + ".json.zip",
}

TOMOSHI_URL = ("https://github.com/tomoshi-app/tomoshi-dict-data/releases/"
               "download/v2026-08-12/tomoshi-dict-open.db.zst")
# 下載後核對，確保拿到的是驗證過的那一份
TOMOSHI_SHA256 = "cd4e6b710fa4fca0672471b64bf251256dde7eba807b9bee2ab88b3fc71554c1"

# 這裡曾經有一張 _ZHTW_FIXES 的字串替換表，修上游殘留的簡繁轉換瑕疵
# （巖手→岩手、鐳射→雷射、相親物件→相親對象……）。已經拿掉，原因是：
#
# 那張表是「我抽樣猜關鍵字猜出來的九條」，不是量出來的。修掉九條之後
# 檢查會顯示「乾淨」，但真實的殘留量無從得知 —— 製造出已經修好的錯覺，
# 比誠實承認上游有瑕疵更糟。
#
# 也確認過沒有規則性的解法：OpenCC 的 t2tw 只做字形標準化，
# 修不掉「巖手」這種選字錯誤 —— 巖與岩都是合法繁體字，
# 錯只錯在「岩手県」這個詞該用哪個，那是詞彙知識不是轉換規則。
#
# 上游已知瑕疵的量級：0.01% 以下（25.1 萬個義項）。就這樣記著。

ATTRIBUTION = [
    "JMdict / JMnedict / KANJIDIC © Electronic Dictionary Research and "
    "Development Group — CC BY-SA 4.0",
    "中文釋義 © Y1Z (Tomoshi) — CC BY-SA 4.0",
]

# 五段動詞可能形是 e 段 + る（歩く→歩ける）。Sudachi 把可能形當成獨立動詞、
# 不還原成原形，而 JMdict 大多沒收可能形，所以「導き出せる」這種詞會查不到，
# 而且查不到的原因從結果上完全看不出來。這張表把 e 段還原回 u 段。
_E_TO_U = dict(zip("けせてねへめれげぜでべぺえ", "くすつぬふむるぐずづぶぷう"))

# JMnedict 的假名是片假名，Sudachi 給的讀音也是片假名，但 JMdict 是平假名
_KATA_TO_HIRA = str.maketrans({chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)})

# Sudachi 的詞性 → JMdict 的詞性標記。
#
# 同一個詞形＋同一個讀音仍然可能是好幾個條目：「あ」讀作あ的就有
# 代名詞（I）、感動詞（ah）、接頭辭（sub-）三個。只靠常用度排序的話
# 兩個都常用時就變成看資料庫順序，等於擲骰子。
#
# Sudachi 已經在斷詞時判斷過詞性了，拿來當第三個判斷依據。
# 兩邊都是封閉的標記集，不是「想到什麼補什麼」的個案清單。
_POS_TO_JMDICT = {
    "感動詞": ("int",),
    "代名詞": ("pn",),
    "名詞": ("n", "n-suf", "n-pref", "vs"),
    "動詞": ("v1", "v5", "vk", "vs", "vi", "vt"),
    "形容詞": ("adj-i", "adj-na"),
    "形状詞": ("adj-na",),
    "副詞": ("adv", "adv-to"),
    "連体詞": ("adj-pn",),
    "接続詞": ("conj",),
    "接頭辞": ("pref",),
    "接尾辞": ("suf", "n-suf"),
}


def _pos_matches(entry: dict, sudachi_pos: str) -> bool:
    """這個條目的詞性跟 Sudachi 判斷的一致嗎。"""
    wanted = _POS_TO_JMDICT.get(sudachi_pos)
    if not wanted:
        return False
    tags = {t for sense in entry["senses"] for t in sense["pos"]}
    return any(t.startswith(w) for t in tags for w in wanted)


def to_hiragana(text: str) -> str:
    return (text or "").translate(_KATA_TO_HIRA)


def reading_signature(readings) -> str:
    """把一個條目的讀音組合成指紋，用來跨資料來源認出同一個條目。

    中文釋義（Tomoshi）與英文釋義（JMdict）是兩份資料，各自有自己的編號，
    沒辦法直接對應。但兩邊都源自 JMdict，所以**讀音組合是可靠的指紋** ——
    「あ」有七個條目，讀音組合分別是 {おし,あ,おうし}、{われ,わ,あ,…}、
    {あっ,あ} 等等，兩邊完全對得起來。

    沒有這個的話，英文會挑到「唖（muteness）」而中文挑到「我（I）」，
    兩個都掛在同一個詞上 —— 那正是 Mac 那邊回報的症狀。
    """
    return "／".join(sorted({to_hiragana(r) for r in readings if r}))


def _download(url: str, target: Path) -> Path:
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"下載 {url.rsplit('/', 1)[-1]}")
    request = urllib.request.Request(url, headers={"User-Agent": "kikitori/1.0"})
    with urllib.request.urlopen(request, timeout=600) as response:
        target.write_bytes(response.read())
    return target


def _entries(archive: Path) -> list[dict]:
    with zipfile.ZipFile(archive) as bundle:
        return json.loads(bundle.read(bundle.namelist()[0]))["words"]


def _fetch_tomoshi(cache: Path) -> Path:
    """下載並解壓 Tomoshi 的中文釋義資料庫（77 MB 壓縮 / 570 MB 解開）。"""
    import hashlib

    plain = cache / "tomoshi.db"
    if plain.exists():
        return plain

    packed = _download(TOMOSHI_URL, cache / "tomoshi.db.zst")
    digest = hashlib.sha256(packed.read_bytes()).hexdigest()
    if digest != TOMOSHI_SHA256:
        packed.unlink()
        raise RuntimeError(f"Tomoshi 檔案校驗失敗：{digest}")

    import zstandard
    print("解壓 tomoshi.db")
    with packed.open("rb") as source, plain.open("wb") as target:
        zstandard.ZstdDecompressor().copy_stream(source, target)
    return plain


ZHTW_RULES_URL = ("https://raw.githubusercontent.com/sysprog21/zhtw-mcp/"
                  "main/assets/ruleset.json")


def _load_zhtw_rules(cache: Path) -> tuple[dict, dict]:
    """載入陸→台用詞對照，切成「可自動替換」與「要人判斷」兩份。

    主表是 sysprog21/zhtw-mcp（MIT），1,673 條 cross_strait 規則。
    Tomoshi 的繁體是從簡體轉來的，而 s2twp 只有 819 條台灣詞彙映射，
    轉不到的大陸用語就以繁體字形留著（賬戶、綜合徵、摩托車、營銷……）。

    要進「可自動替換」那份要同時滿足三個條件，任一不合就只標記不動：

    1. **沒有 context_clues** —— 這是規則作者自己標的消歧需求，不是我判斷的。
       帶 clues 的那批就是有歧義的（質量可能是 mass 也可能是 quality）。
    2. **不是單字規則** —— 「頭」「類」在 25 萬個義項裡會匹配到爆炸。
    3. **不是恆等映射** —— 簡繁轉換表的副產品，當偵測器只會產生雜訊。

    試過再加一條「至少要有第二份詞表背書」（六份公開詞表裡有 77% 的詞只有
    單一來源，品質參差），實測後**收回了**：它篩的不是正確性，是「剛好有沒有
    在 OpenCC 的 IT 詞表裡」。`低級→低階`、`彙編→組譯` 這兩條可疑的反而
    有背書而通過，`賬戶→帳戶`、`摩托車→機車`、`綜合徵→症候群` 這些
    無疑問該換的卻被擋掉。加了反而更糟。

    取而代之的是：套用了哪些規則全部寫進 data/zhtw_applied.json，
    一條一條可以審。規則是有界的（1,353 條），不像錯字清單是無界的。

    每條規則都帶 english 欄位，套用時再拿它跟 JMdict 的英文釋義比對做語境
    確認 ——「質量」只有在英文釋義真的講 quality 時才算命中。純字面比對會
    誤判 5.38% 的條目，加上英文確認降到 2.41%。
    """
    import re as _re

    path = _download(ZHTW_RULES_URL, cache / "zhtw_ruleset.json")
    payload = json.loads(path.read_text(encoding="utf-8"))


    stop = {"the", "a", "an", "of", "to", "in", "for", "and", "or",
            "on", "with", "by", "at", "as", "is", "be"}

    safe: dict[str, tuple[str, set]] = {}
    manual: dict[str, tuple[str, set]] = {}
    for rule in payload.get("spelling_rules", []):
        source = rule.get("from") or ""
        target = rule.get("to") or []
        if rule.get("type") != "cross_strait" or len(source) < 2 or not target:
            continue
        if source == target[0]:
            continue  # 恆等映射是簡繁轉換表的副產品，當偵測器只會產生雜訊
        english = (rule.get("english") or "").lower()
        keywords = {w for w in _re.findall(r"[a-z][a-z-]+", english)
                    if w not in stop and len(w) > 2}
        if not keywords:
            continue
        bucket = manual if rule.get("context_clues") else safe
        bucket[source] = (target[0], keywords)
    return safe, manual


def _import_chinese(connection: sqlite3.Connection, cache: Path) -> int:
    """把 Tomoshi 的繁體釋義併進來，用 **(詞形, 讀音)** 當鍵。

    不沿用 Tomoshi 的 entry_id —— 那是它自己的編號，跟我們從 JMdict
    建的編號無關，硬對會給出別的詞的意思。

    也不能只用詞形當鍵：「市場」有いちば（菜市場）與しじょう（金融市場）
    兩個條目，只用詞形會固定拿到其中一個，於是讀音挑對了、中文卻是另一個
    意思 —— 而且畫面上看起來完全正常。讀音一起存進去才對得起來。
    """
    tomoshi = _fetch_tomoshi(cache)
    source = sqlite3.connect(f"file:{tomoshi}?mode=ro", uri=True)

    # 每個條目的假名寫法就是它的讀音（is_kana=1）
    readings: dict[int, list[str]] = {}
    for entry_id, text in source.execute(
            "SELECT entry_id, text FROM forms WHERE is_kana = 1"):
        readings.setdefault(entry_id, []).append(text)

    # zh_defs_zhtw 是繁體。entries.data 裡另有內嵌的 lang:"zho" gloss，
    # 那個是簡體，不要用。
    # 去掉重複的釋義。
    #
    # 簡轉繁的詞彙替換會讓兩個原本不同的詞撞成同一個
    # （「通信情报；通讯情报」→「通訊情報；通訊情報」、
    #  「默认值；缺省值」→「預設值；預設值」），實測有 1,831 個條目這樣，
    # 其中 904 個是常用詞。這不影響意思，但畫面上看起來像出錯。
    #
    # 只刪**完全相同**的字串，所以不會損失任何資訊 ——
    # 這是規則不是個案清單，不需要維護。
    # 要在「詞」的層級去重，不是字串層級 —— 單一個 gloss 自己就可能帶分號
    # （"通訊；傳輸" 與 "通訊" 是兩筆資料，串起來才變成 "通訊；通訊；傳輸"）。
    def dedupe(items: list[str]) -> list[str]:
        return list(dict.fromkeys(items))

    def merge(glosses: list[str]) -> str:
        terms = [t.strip() for g in glosses for t in g.split("；") if t.strip()]
        return "；".join(dedupe(terms))

    # 陸→台用詞替換要靠英文釋義當語境，所以先把英文抓出來
    safe_rules, _ = _load_zhtw_rules(cache)
    english_of: dict[int, str] = {}
    for entry_id, data in source.execute("SELECT id, data FROM entries"):
        payload = json.loads(data)
        parts = []
        for sense in payload.get("senses") or []:
            for gloss in (sense.get("glosses") or sense.get("gloss") or []):
                parts.append(gloss.get("text", "") if isinstance(gloss, dict) else str(gloss))
        english_of[entry_id] = " ".join(parts).lower()

    replaced = 0
    applied: dict[str, list] = {}

    def localise(text: str, english: str) -> str:
        """把殘留的大陸用語換成台灣說法。兩個條件都成立才換。

        一是英文釋義要支持這條規則（「質量」只有在英文講 quality 時才算）。

        二是台灣說法不能已經在同一個義項裡。辭典會刻意並列兩岸說法
        （「計程車；出租車」），那不是簡轉繁的污染而是編者的意圖，
        換掉會先變成「計程車；計程車」再被去重吃掉，等於刪資料。
        更糟的是「話筒；麥克風」這種 —— 話筒還有聽筒的意思，
        換掉是實質的意思損失。實測這種並列有 415 處、涉及 121 條規則。
        """
        nonlocal replaced
        for word, (taiwanese, keywords) in safe_rules.items():
            if word not in text or taiwanese in text:
                continue
            if any(k in english for k in keywords):
                before = text
                text = text.replace(word, taiwanese)
                replaced += 1
                # 記下每一次替換，讓這件事可以被逐條檢查而不是只能相信我
                log = applied.setdefault(f"{word}→{taiwanese}", [])
                if len(log) < 5:
                    log.append(f"{before[:34]}　→　{text[:34]}")
        return text

    glosses_by_entry: dict[int, list[str]] = {}
    for entry_id, data in source.execute("SELECT entry_id, data FROM zh_defs_zhtw"):
        senses = json.loads(data).get("senses", {})
        english = english_of.get(entry_id, "")
        glosses = []
        for key in sorted(senses, key=int):
            joined = merge([g["text"] for g in senses[key].get("glosses", [])])
            if joined:
                glosses.append(localise(joined, english))
        if glosses:
            # 替換後可能又撞出重複（數碼／數字 都變成 數位），所以再去重一次
            glosses_by_entry[entry_id] = dedupe([merge([g]) for g in glosses])
    if replaced:
        report = cache.parent / "zhtw_applied.json"
        report.write_text(json.dumps(applied, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"  陸→台用詞替換 {replaced} 處，實際開火 {len(applied)} 條規則"
              f"（共 {len(safe_rules)} 條）→ {report.name}")

    rows = []
    for entry_id, text, is_common in source.execute(
            "SELECT entry_id, text, is_common FROM forms"):
        glosses = glosses_by_entry.get(entry_id)
        if not glosses:
            continue
        form = unicodedata.normalize("NFKC", text)
        payload = json.dumps(glosses, ensure_ascii=False)
        # 讀音留空的那筆是 fallback，給沒有語境讀音時用
        signature = reading_signature(readings.get(entry_id, ()))
        rows.append((form, "", signature, is_common, payload))
        for reading in readings.get(entry_id, ()):
            rows.append((form, to_hiragana(reading), signature, is_common, payload))
    source.close()

    connection.executescript("""
        CREATE TABLE chinese (
            form    TEXT NOT NULL,
            reading TEXT NOT NULL,   -- 平假名；空字串是該詞形的預設
            sig     TEXT NOT NULL,   -- 讀音組合，跨來源認條目用（見 reading_signature）
            common  INTEGER NOT NULL,
            senses  TEXT NOT NULL
        );
    """)
    connection.executemany("INSERT INTO chinese VALUES (?,?,?,?,?)", rows)
    connection.execute("CREATE INDEX idx_chinese ON chinese(form, reading)")
    connection.execute("CREATE INDEX idx_chinese_sig ON chinese(form, sig)")
    connection.commit()
    return connection.execute(
        "SELECT COUNT(DISTINCT form) FROM chinese").fetchone()[0]


def build(db_path: Path = DEFAULT_DB, cache: Path | None = None) -> None:
    """下載兩份字典，攤平成「詞形 → 條目」寫進 SQLite。

    索引的是**詞形**不是條目：一個條目可能有好幾種寫法
    （沢山／たくさん、市場／いちば／しじょう），每一種都要查得到。
    只索引漢字的話「たくさん」會查不到 —— 而口語裡這類詞多半就寫假名。
    """
    cache = cache or db_path.parent / "cache"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    connection.executescript("""
        CREATE TABLE entries (
            id       INTEGER PRIMARY KEY,
            source   TEXT NOT NULL,      -- jmdict | jmnedict
            common   INTEGER NOT NULL,   -- JMdict 的常用標記
            readings TEXT NOT NULL,      -- JSON 陣列
            senses   TEXT NOT NULL       -- JSON 陣列
        );
        CREATE TABLE forms (
            form     TEXT NOT NULL,
            entry_id INTEGER NOT NULL REFERENCES entries(id)
        );
    """)

    entry_id = 0
    for source, url in SOURCES.items():
        archive = _download(url, cache / f"{source}.zip")
        words = _entries(archive)
        print(f"{source}：{len(words):,} 條目")

        rows, links = [], []
        for word in words:
            kanji = [k["text"] for k in word.get("kanji", [])]
            kana = [k["text"] for k in word.get("kana", [])]

            if source == "jmdict":
                common = (any(k.get("common") for k in word.get("kanji", []))
                          or any(k.get("common") for k in word.get("kana", [])))
                senses = [{"pos": s.get("partOfSpeech", []),
                           "en": [g["text"] for g in s.get("gloss", [])]}
                          for s in word.get("sense", [])]
            else:
                # 人名地名沒有常用標記，但也不需要 —— 挑哪一個靠語境讀音
                common = False
                senses = [{"pos": t.get("type", []),
                           "en": [g["text"] for g in t.get("translation", [])]}
                          for t in word.get("translation", [])]

            entry_id += 1
            rows.append((entry_id, source, int(common),
                         json.dumps(kana, ensure_ascii=False),
                         json.dumps(senses, ensure_ascii=False)))
            links += [(form, entry_id) for form in set(kanji) | set(kana)]

        connection.executemany("INSERT INTO entries VALUES (?,?,?,?,?)", rows)
        connection.executemany("INSERT INTO forms VALUES (?,?)", links)
        connection.commit()

    connection.execute("CREATE INDEX idx_forms ON forms(form)")
    connection.commit()

    chinese = _import_chinese(connection, cache)
    print(f"tomoshi：{chinese:,} 個詞形有繁體釋義")

    forms = connection.execute("SELECT COUNT(DISTINCT form) FROM forms").fetchone()[0]
    total = connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    connection.close()
    size = db_path.stat().st_size / 1048576
    print(f"\n{total:,} 條目、{forms:,} 個詞形 → {db_path.name}（{size:.0f} MB）")
    for line in ATTRIBUTION:
        print(f"  出處：{line}")


class Dictionary:
    """查詢介面。開一次連線重複用，查一個詞是單純的索引查找。"""

    def __init__(self, db_path: Path = DEFAULT_DB):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"字典還沒建，先跑 --build：{db_path}")
        self.connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def _raw(self, form: str) -> list[dict]:
        cursor = self.connection.execute(
            "SELECT e.source, e.common, e.readings, e.senses FROM forms f "
            "JOIN entries e ON e.id = f.entry_id WHERE f.form = ?", (form,))
        return [{"source": source, "common": bool(common),
                 "readings": json.loads(readings), "senses": json.loads(senses)}
                for source, common, readings, senses in cursor]

    def lookup(self, word: str, reading: str = "", pos: str = "") -> dict | None:
        """查一個詞。給了語境讀音就用它挑同形異讀。

        reading 傳 Sudachi 的 reading_form()（片假名）。這件事很重要 ——
        「名古屋」在 JMnedict 有多個同形異讀而且**沒有頻率標記**，
        不挑的話會拿到「たなごや」（某個罕見姓氏）而不是名古屋市。
        讀音是轉錄流程裡本來就有的東西，不必額外算。
        """
        form = unicodedata.normalize("NFKC", word)
        entries = self._raw(form)
        matched = form

        if not entries:
            # 可能形還原。只在直接查不到時才做 ——
            # 「いける」自己就在字典裡，不該被還原成「行く」。
            base = self._deconjugate(form)
            if base:
                entries = self._raw(base)
                matched = base

        if not entries:
            return None

        # **先排序再比對讀音**，不是只在對不上時才排。
        #
        # 「あ」有七個條目，其中「唖（muteness，不常用）」與
        # 「あっ（ah／oh，常用，感動詞）」的讀音都含「あ」。照資料庫順序
        # 取第一個對得上的會拿到「唖」—— 明明有個常用得多的正解在後面。
        entries.sort(key=lambda e: (not e["common"], e["source"] != "jmdict"))

        chosen = None
        # 兩邊都轉平假名再比。JMdict 的外來語讀音存的是片假名
        # （トイレ、オフィス、ニュース），只把輸入轉成平假名的話永遠對不上，
        # 而外來語在 podcast 裡非常多，這個錯會讓一大票詞的讀音變成「用猜的」。
        # 詞性對得上的排前面。Sudachi 已經判斷過這個詞在這句話裡是什麼詞性，
        # 那是免費的第三個判斷依據 —— 「あ」讀作あ的有代名詞、感動詞、接頭辭
        # 三個條目而且兩個都常用，只靠常用度排序等於擲骰子。
        if pos:
            entries.sort(key=lambda e: (not _pos_matches(e, pos),
                                        not e["common"], e["source"] != "jmdict"))

        target = to_hiragana(reading)
        if target:
            for entry in entries:
                hit = next((r for r in entry["readings"]
                            if to_hiragana(r) == target), None)
                if hit:
                    # 回報**實際對上的**讀音，不是 readings[0]。
                    # 「あ」對上的是あ，但 readings[0] 是おし —— 直接拿第一個
                    # 會標出一個跟這句話無關的讀音。
                    chosen = {"form": matched, "matched_reading": True,
                              "reading": to_hiragana(hit), **entry}
                    break

        if chosen is None:
            chosen = {"form": matched, "matched_reading": False,
                      "reading": to_hiragana(entries[0]["readings"][0])
                      if entries[0]["readings"] else "", **entries[0]}

        # 中文要認**同一個條目**，不是只認同一個讀音。
        # 中英文是兩份資料、各自有編號，但都源自 JMdict，
        # 所以用讀音組合當指紋（見 reading_signature）。
        chosen["zh"] = self.chinese(matched, chosen["reading"],
                                    reading_signature(chosen["readings"]))
        return chosen

    def chinese(self, form: str, reading: str = "",
                signature: str = "") -> list[str]:
        """繁體中文釋義，一個義項一條。查不到回空陣列。

        三層由嚴到寬：

        1. **讀音組合**（指紋）—— 認的是同一個 JMdict 條目，最可靠。
           少了這層會出現「英文是唖 muteness、中文是我 I」掛在同一個詞上。
        2. **單一讀音** —— 市場【しじょう】是金融市場、【いちば】是菜市場。
        3. **只看詞形** —— 最後的退路。
        """
        form = unicodedata.normalize("NFKC", form)
        if signature:
            row = self.connection.execute(
                "SELECT senses FROM chinese WHERE form = ? AND sig = ? "
                "ORDER BY common DESC LIMIT 1", (form, signature)).fetchone()
            if row:
                return json.loads(row[0])
        target = to_hiragana(reading)
        if target:
            # 中文表建索引時讀音已經統一轉成平假名，所以這裡只要轉輸入
            row = self.connection.execute(
                "SELECT senses FROM chinese WHERE form = ? AND reading = ? "
                "ORDER BY common DESC LIMIT 1", (form, target)).fetchone()
            if row:
                return json.loads(row[0])
        row = self.connection.execute(
            "SELECT senses FROM chinese WHERE form = ? ORDER BY common DESC LIMIT 1",
            (form,)).fetchone()
        return json.loads(row[0]) if row else []

    def lookup_all(self, word: str) -> list[dict]:
        """回傳所有同形條目，給「這裡有多種讀法」的介面用。"""
        return self._raw(unicodedata.normalize("NFKC", word))

    @staticmethod
    def _deconjugate(word: str) -> str | None:
        if len(word) >= 4 and word.endswith("られる"):    # 食べられる→食べる
            return word[:-3] + "る"
        if len(word) >= 3 and word.endswith("る") and word[-2] in _E_TO_U:
            return word[:-2] + _E_TO_U[word[-2]]          # 歩ける→歩く
        return None


# s2twp 的 TWPhrases 詞彙替換裡，**簡體原詞在非科技語境有別的意思**的那些。
# 這是 s2twp 唯一會主動改變詞彙的地方，也是它最容易出錯的地方 ——
# 規則本身沒錯（電腦的 file 就是檔案），錯在它不看語境無差別套用。
#
# 這張表跟被拿掉的 _ZHTW_FIXES 不一樣：那張是「我想得到的錯字」，
# 這張是「已知會產生歧義的替換規則」—— TWPhrases 總共只有 819 條，
# 是有界的、檢查得完的；錯字則是無界的，永遠不知道漏了多少。
_AMBIGUOUS_PHRASES = {
    "文件": ("檔案", "文件也指公文、文書"),
    "程序": ("程式", "程序也指步驟、法律程序"),
    "通信": ("通訊", "通信也指書信往來"),
    "对象": ("物件", "对象也指交往、研究對象"),
    "支持": ("支援", "支持也指贊成、擁護"),
    "保存": ("儲存", "保存也指保鮮、留存"),
    "默认": ("預設", "默认也指默許、預設同意"),
    "搜索": ("搜尋", "搜索也指搜救、搜查"),
    "智能": ("智慧", "智能是 AI，智慧是 wisdom"),
}


def audit_chinese(cache: Path | None = None, out: Path | None = None) -> int:
    """全量稽核中文釋義。每一條都檢查，不抽樣。

    抽樣跟個案補丁是同一個毛病 —— 你永遠不知道沒看到的那些有沒有問題。
    這裡掃過全部 21.7 萬條目 / 25.1 萬義項，把每一筆有問題的都寫進報告檔，
    所以「總共有多少問題」是確定的數字而不是估計值。

    五類都是確定性的判斷，可以做到 100% 掌握：

        字形與官方 s2twp 不一致   簡繁轉換選字
        有歧義的詞彙替換         TWPhrases 套錯語境（文件→檔案）
        重複義項               替換撞在一起（通信情报／通讯情报 → 都變通訊情報）
        義項數與英文不符         翻譯漏掉整個義項
        空釋義                 根本沒有內容

    **有一類做不到**：翻譯的語意正確性。試過用 ECDICT（370 萬條英中詞典）
    當樞紐逐詞比對，全量跑出來 15.4% 被標記，看過內容全是偽陽性
    （「說不定」「整整」「左右」都是對的，只是 ECDICT 沒有字面對應）。
    改成只抓「整體有依據的義項裡孤立的無依據詞」也一樣。

    根本原因是沒有對照基準：要驗證英譯中對不對需要獨立的第二本日中字典，
    而那正是一開始就找不到的東西。這一類只能靠換資料來源或人工回報。
    """
    import re as _re

    try:
        import opencc
    except ImportError:
        print("要先 pip install opencc（不是 opencc-python-reimplemented，"
              "那個內建舊字典，會把「岩手県」轉成「巖手縣」）")
        return 1

    cache = cache or DEFAULT_DB.parent / "cache"
    tomoshi = cache / "tomoshi.db"
    if not tomoshi.exists():
        print(f"找不到 {tomoshi}，先跑 --build")
        return 1

    convert = opencc.OpenCC("s2twp").convert
    kana = _re.compile(r"[ぁ-ゟァ-ヶ]")  # 日文原文不套中文規則

    source = sqlite3.connect(f"file:{tomoshi}?mode=ro", uri=True)

    def senses(data: str) -> list[str]:
        blocks = json.loads(data).get("senses", {})
        return ["；".join(g["text"] for g in blocks[k].get("glosses", []))
                for k in sorted(blocks, key=int)]

    simplified = {e: senses(d) for e, d in
                  source.execute("SELECT entry_id, data FROM zh_defs")}
    common = {e for (e,) in
              source.execute("SELECT DISTINCT entry_id FROM forms WHERE is_common = 1")}
    english = {e: len(json.loads(d).get("senses") or [])
               for e, d in source.execute("SELECT id, data FROM entries")}
    label = {}
    for entry_id, text in source.execute(
            "SELECT entry_id, text FROM forms ORDER BY is_common DESC"):
        label.setdefault(entry_id, text)

    # 陸→台用詞：無歧義的那批建置時已經換掉，這裡查的是需要人判斷的那批
    _, manual_rules = _load_zhtw_rules(cache)
    english_of: dict[int, str] = {}
    for entry_id, data in source.execute("SELECT id, data FROM entries"):
        parts = []
        for sense in json.loads(data).get("senses") or []:
            for gloss in (sense.get("glosses") or sense.get("gloss") or []):
                parts.append(gloss.get("text", "") if isinstance(gloss, dict) else str(gloss))
        english_of[entry_id] = " ".join(parts).lower()

    found: dict[str, dict[int, str]] = {k: {} for k in (
        "字形與官方 s2twp 不一致", "有歧義的詞彙替換", "重複義項",
        "陸用語（需語境判斷）", "義項數與英文不符", "空釋義")}
    entries = 0

    for entry_id, data in source.execute("SELECT entry_id, data FROM zh_defs_zhtw"):
        entries += 1
        current = senses(data)
        joined = "／".join(current)
        origin = simplified.get(entry_id)
        word = label.get(entry_id, "?")

        if not joined.strip():
            found["空釋義"][entry_id] = word
        if entry_id in english and english[entry_id] != len(current):
            found["義項數與英文不符"][entry_id] = (
                f"{word}：中 {len(current)} vs 英 {english[entry_id]}")

        terms = [t for block in current for t in block.split("；") if t]
        if len(terms) != len(set(terms)):
            found["重複義項"][entry_id] = f"{word}：{joined[:40]}"

        gloss_en = english_of.get(entry_id, "")
        for phrase, (taiwanese, keywords) in manual_rules.items():
            if phrase in joined and any(k in gloss_en for k in keywords):
                found["陸用語（需語境判斷）"][entry_id] = (
                    f"{word}：{phrase}→{taiwanese}？　{joined[:30]}")
                break

        if not origin or len(origin) != len(current):
            continue
        for got, raw in zip(current, origin):
            if kana.search(got):
                continue
            want = convert(raw)
            if got != want and len(got) == len(want):
                found["字形與官方 s2twp 不一致"][entry_id] = f"{word}：{got[:30]} ←→ {want[:30]}"
                break
        source_text = "／".join(origin)
        for phrase, (replacement, _) in _AMBIGUOUS_PHRASES.items():
            if phrase in source_text and replacement in joined:
                found["有歧義的詞彙替換"][entry_id] = (
                    f"{word}：{phrase}→{replacement}　{joined[:34]}")
                break
    source.close()

    print(f"全量掃描 {entries:,} 條目（常用 {len(common):,}）\n")
    print(f"{'問題類型':<26}{'條目':>8}{'佔全庫':>9}{'常用詞':>8}{'佔常用':>9}")
    print("─" * 62)
    everything: set[int] = set()
    for name, rows in found.items():
        everything |= set(rows)
        hit = set(rows) & common
        print(f"{name:<26}{len(rows):>8,}{len(rows) / entries * 100:>8.2f}%"
              f"{len(hit):>8,}{len(hit) / len(common) * 100:>8.2f}%")
    print("─" * 62)
    both = everything & common
    print(f"{'至少有一項（去重）':<26}{len(everything):>8,}"
          f"{len(everything) / entries * 100:>8.2f}%{len(both):>8,}"
          f"{len(both) / len(common) * 100:>8.2f}%")

    print("\n說明：")
    print("  · 「重複義項」建置時已自動去重（詞層級），不會進到成品。")
    print("  · 「有歧義的詞彙替換」開火不等於出錯 —— 歸檔程序→歸檔程式是對的，")
    print("    將軍的簽署文件→簽署檔案才是錯的。要人判斷，所以只列不改。")
    print("  · 翻譯的語意正確性不在這份清單裡，那沒有確定性的驗證方法（見 docstring）。")

    out = out or (DEFAULT_DB.parent / "audit.json")
    payload = {name: [{"entry_id": k, "detail": v} for k, v in sorted(rows.items())]
               for name, rows in found.items()}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整清單（每一筆，不是範例）寫進 {out}")
    return 0


def _coverage(db_path: Path, limit: int) -> int:
    """拿真實逐字稿量涵蓋率。抽樣詞表量不出實際會不會查不到。"""
    import env
    env.load()
    from segment import _get_tokenizer
    from supabase_client import Supabase

    tokenizer = _get_tokenizer()
    if not tokenizer:
        print("要先裝 sudachipy")
        return 1

    dictionary = Dictionary(db_path)
    rows = Supabase().select(
        "kikitori_transcripts", f"select=lines&limit={limit}&order=created_at.desc")
    if not rows:
        print("資料庫裡還沒有逐字稿")
        return 1

    # 助詞、助動詞、標點不查字典，它們不是「單字」
    skip = ("助詞", "助動詞", "補助記号", "空白")
    hit = with_zh = total = 0
    missing: dict[str, int] = {}

    for index, row in enumerate(rows, 1):
        text = "".join(line["text"] for line in row["lines"])
        found = chinese = count = 0
        # Sudachi 一次最多吃 49149 bytes，切段送
        for offset in range(0, len(text), 8000):
            for token in tokenizer.tokenize(text[offset:offset + 8000]):
                if token.part_of_speech()[0] in skip:
                    continue
                count += 1
                lemma = token.dictionary_form()
                entry = dictionary.lookup(lemma, token.reading_form())
                if entry:
                    found += 1
                    chinese += bool(entry["zh"])
                else:
                    missing[lemma] = missing.get(lemma, 0) + 1
        hit += found
        with_zh += chinese
        total += count
        print(f"  第 {index} 集：{count:>5} 詞次，查得到 {found / count * 100:5.1f}%"
              f"，其中有中文釋義 {chinese / count * 100:5.1f}%")

    print(f"\n合計 {total:,} 詞次：查得到 {hit / total * 100:.1f}%、"
          f"有中文釋義 {with_zh / total * 100:.1f}%")
    top = sorted(missing.items(), key=lambda kv: -kv[1])[:12]
    print("查不到最多的：" + "、".join(f"{w}({n})" for w, n in top))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--lookup")
    parser.add_argument("--reading", default="", help="語境讀音（片假名）")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--audit", action="store_true",
                        help="稽核中文釋義的簡繁轉換品質（只報告不修改）")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    db_path = Path(args.db)

    if args.build:
        build(db_path)
        return 0

    if args.audit:
        return audit_chinese()

    if args.coverage:
        return _coverage(db_path, args.limit)

    if args.lookup:
        dictionary = Dictionary(db_path)
        best = dictionary.lookup(args.lookup, args.reading)
        if not best:
            print(f"查不到 {args.lookup}")
            return 1
        entries = dictionary.lookup_all(args.lookup)
        if not entries:
            print(f"（原形還原成 {best['form']}）")
            entries = [best]
        for entry in entries:
            mark = "←" if entry["senses"] == best["senses"] else " "
            tag = "常用" if entry["common"] else "    "
            print(f"{mark} 【{'／'.join(entry['readings'][:3])}】{tag} {entry['source']}")
            for sense in entry["senses"][:3]:
                print(f"     ({','.join(sense['pos'][:2])}) {'; '.join(sense['en'][:4])}")
        for index, gloss in enumerate(best.get("zh", [])[:6], 1):
            print(f"  中文 {index}. {gloss}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
