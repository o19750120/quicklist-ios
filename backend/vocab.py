"""替一集逐字稿預先建好詞表與詞邊界。

斷詞用 SudachiPy、釋義查 `dictionary.py` 建的離線 SQLite，
**查意思這條路完全不打 API**，都在 runner 本機跑完。

唯一會打 API 的是假名標注的覆核，而且是整集一次、用最便宜的模型
（見 `verify_readings`）—— 那是因為實測發現 Sudachi 對「私」「方」「何」
這些最高頻的字有系統性誤標，佔含漢字詞的 2.9%。沒有金鑰就跳過，
只是假名照 Sudachi 標，不影響查字典。

為什麼要在後端做而不是 App 自己來：

1. **日文沒有空格，App 無法自己斷詞。** 「市場に行く」要切成
   「市場／に／行く」需要形態素解析，那是一整套詞典與模型，
   不可能塞進 App。所以詞邊界一定要後端給。
2. **字典有 293 MB。** 綁進 App 太大、放 Supabase 會吃掉免費額度，
   但一集實際用到的詞只有三五百個，查好之後才 90–370 KB。
3. **同形異讀要靠語境。** 「市場」讀 しじょう 還是 いちば 決定了中文
   意思完全不同，而語境只有在轉錄當下才有 —— Sudachi 給的
   `reading_form()` 是這句話裡的實際讀音，App 事後拿不到。

產物存進 `kikitori_transcripts.vocab`，跟逐字稿一起下載，App 零額外請求。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dictionary import Dictionary, to_hiragana  # noqa: E402
from segment import _get_tokenizer  # noqa: E402

# 助詞、助動詞、標點不進詞表 —— 它們不是「單字」，點下去也沒東西好查。
# 用詞性判斷而不是列清單，理由同 benchmark.py 的 _FILLER_POS。
SKIP_POS = ("助詞", "助動詞", "補助記号", "空白")

# Sudachi 一次最多吃 49149 bytes
CHUNK = 8000

# 讀音覆核用的模型，依偏好排序。實測 flash-lite 跟大模型一樣好
# （83% vs 82%），所以刻意排在前面 —— 這是小任務，不該吃大模型額度。
READING_MODELS = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3-flash-preview"]

# 兩個模型意見不同時放進讀音欄位的記號。App 看到就顯示候選讓使用者自己判斷，
# 而不是默默挑一個 —— 有些詞本來就有多種正確讀法，連日本人都會分歧。
DISPUTED = "?"

# 一批送幾個詞去覆核。不是為了省呼叫次數 —— 一次送太多模型會變隨便，
# 實測 655 個詞一次送，兩個模型的分歧率從 4% 飆到 22%，
# 而且同一集跑兩次會得到不一樣的結果（temperature=0 也一樣）。
READING_BATCH = 60


def _lemma_reading(tokenizer, token) -> str:
    """原形的讀音。

    `token.reading_form()` 給的是**表面形**的讀音 —— 「言わない」的
    「言わ」會回 イワ、「いて」會回 イ。那個讀音拿去跟字典裡的原形讀音
    （いう、いる）比對永遠對不上，於是每個活用過的動詞都會被判定成
    「讀音靠猜」。實測一集 306 個詞裡有 83 個中招。

    所以查字典要用原形自己的讀音，把 dictionary_form 再斷一次詞取得。
    表面形的讀音仍然有用（標假名要標在實際出現的字上），只是用途不同。
    """
    lemma = token.dictionary_form()
    if lemma == token.surface():
        return token.reading_form()
    parts = [m.reading_form() for m in tokenizer.tokenize(lemma)]
    return "".join(parts) or token.reading_form()


def _has_kanji(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def verify_readings(cases: list[dict]) -> dict[int, str]:
    """拿 LLM 覆核有多讀音疑慮的詞。整集一次呼叫，關閉思考模式。

    **為什麼值得打這一次 API**（實測，不是猜的）：Sudachi 在同形異音詞上
    有系統性偏差，而且集中在最高頻的字 ——

        私  Sudachi 給 わたくし，實際口語是 わたし
        方  Sudachi 給 ほう，「怒っている方」其實是 かた
        何  Sudachi 給 なん，「何が問題」其實是 なに

    四集實測，光這幾個字就佔含漢字詞的 **2.9%** 會標錯。
    三個模型對這類分歧的判斷 12/12 全部正確。

    **用最便宜的模型就夠**：`gemini-3.1-flash-lite` 一致率 83%、
    `gemini-3-flash-preview` 82% —— 大模型沒有比較好，不要浪費額度。

    一集約 120–650 個疑慮詞，**問兩個模型、每 READING_BATCH 個一批**。
    沒有金鑰或呼叫失敗就回空的，保留 Sudachi 的判斷，不讓整集報廢。

    回傳的讀音若是 DISPUTED，代表兩個模型講的不一樣 —— 那個位置是真的
    有歧義，交給 App 顯示候選。實測分歧率約 10%（139 個疑慮詞裡 14 個），
    而且分批之後跑兩次結果完全相同。都是真歧義：皆（みな／みんな）、
    街中（まちなか／まちじゅう，意思不同）、避け（さける／よける，
    是兩個不同的動詞）、他（た／ほか）。
    """
    if not cases:
        return {}

    import json
    import time
    import urllib.error
    import urllib.request

    import providers

    # 金鑰倒過來用。
    #
    # 轉錄與覆核共用同一組 Gemini 免費配額，而轉錄重要得多 ——
    # 它失敗會退回 Deepgram，覆核失敗只是假名少修幾處。
    # 兩邊從相反方向取金鑰，撞在一起的機會小一些，成本是零。
    #
    # 注意：這是預防，不是在修一個已知的問題。曾經以為「轉錄退回 Deepgram
    # 是被覆核吃掉配額」，查了完整日誌才發現真正的原因是涵蓋檢查沒過
    # （Gemini 中間空白 29%）。真的要判斷有沒有撞配額，看
    # `providers.call_summary()` 的逐次紀錄，不要用猜的。
    keys = list(reversed(providers._keys("GEMINI_API_KEYS")))
    if not keys:
        return {}

    schema = {"type": "object", "properties": {"r": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "y": {"type": "string"}},
        "required": ["i", "y"]}}}, "required": ["r"]}

    def ask(model: str, chunk: list[tuple[int, dict]]) -> dict[int, str] | None:
        prompt = ("以下每一列是一個日文詞，以及它出現的句子。請判斷該詞在"
                  "**那個句子裡**的實際讀音，用平假名回答。"
                  "只看句子語境，不要給字典的預設讀音。\n"
                  "回傳 JSON：{\"r\":[{\"i\":序號,\"y\":\"讀音\"}]}，每一列都要有。\n\n")
        for index, case in chunk:
            prompt += f"{index}\t{case['w']}\t{case['sent'][:60]}\n"

        def build(thinking: dict | None) -> dict:
            config = {"temperature": 0, "responseMimeType": "application/json",
                      "responseSchema": schema}
            if thinking is not None:
                config["thinkingConfig"] = thinking
            return {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": config}

        # 變數不要叫 index —— 上面組 prompt 時已經用它當詞的序號了
        for slot, key in enumerate(keys):
            # 這把金鑰沒有這個模型（404 過），不用再撞一次
            if providers.is_missing(model, slot):
                continue
            # 關掉思考的寫法每個模型不一樣（3.6 只吃 thinkingLevel、
            # 2.5 只吃 thinkingBudget），所以用 providers 共用的判斷，
            # 不要在這裡自己寫死一種 —— 寫死的話這條路會默默開著思考跑，
            # 而使用者明確要求所有模型都要關掉。
            for thinking in providers.thinking_candidates(model):
                started = time.time()
                try:
                    request = urllib.request.Request(
                        "https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{model}:generateContent",
                        data=json.dumps(build(thinking)).encode(),
                        headers={"x-goog-api-key": key, "Content-Type": "application/json",
                                 "User-Agent": providers.USER_AGENT})
                    with urllib.request.urlopen(request, timeout=300) as response:
                        payload = json.loads(response.read())
                    providers.remember_thinking(model, thinking)
                    providers.check_thinking(model, payload)
                    text = payload["candidates"][0]["content"]["parts"][0]["text"]
                    answers = {a["i"]: a["y"] for a in json.loads(text)["r"] if a.get("y")}
                    providers.record("gemini(讀音)", slot, model, True,
                                     time.time() - started)
                    return answers
                except urllib.error.HTTPError as exc:
                    providers.record("gemini(讀音)", slot, model, False,
                                     time.time() - started,
                                     "配額 429" if exc.code == 429 else f"HTTP {exc.code}")
                    if exc.code == 400:
                        continue      # 這種關思考的寫法不被接受，換下一種
                    if exc.code == 404:
                        providers.mark_missing(model, slot)
                    break             # 其他錯誤是金鑰或服務的問題，換金鑰
                except Exception as exc:
                    providers.record("gemini(讀音)", slot, model, False,
                                     time.time() - started, f"{type(exc).__name__}: {exc}")
                    break
        return None

    def run(model: str) -> dict[int, str] | None:
        """整份跑完，但分批送。

        一次塞太多會讓模型變隨便 —— 實測 655 個詞一次送，兩個模型的
        分歧率飆到 22%，而同一集分兩次跑還會得到 6 vs 12 個不同的結果。
        分成 READING_BATCH 一批之後才穩定。這跟 `providers.py` 的翻譯
        早就記過的教訓一樣（120 詞一批時 flash-lite 整筆漏掉 15%）。
        """
        indexed = list(enumerate(cases))
        merged: dict[int, str] = {}
        for start in range(0, len(indexed), READING_BATCH):
            answers = ask(model, indexed[start:start + READING_BATCH])
            if answers is None:
                return None          # 這個模型整個不通，換下一個
            merged.update(answers)
        return merged

    # 問兩個模型，不是為了更準，是為了**知道自己什麼時候不確定**。
    #
    # 使用者對假名標注的要求是零容忍，但有些詞本來就有多種正確讀法
    # （街中 まちなか／まちじゅう），連日本人都會分歧。那種情況正確的做法
    # 是顯示候選，不是默默挑一個。而「兩個模型講的不一樣」正是最好的訊號 ——
    # 實測三個模型跑同一批，16/120 是模型之間就吵起來的。
    #
    # 一次呼叫才 2,700 tokens，問兩次仍然便宜到不影響任何事。
    votes: list[dict[int, str]] = []
    used: list[str] = []
    for model in READING_MODELS:
        answers = run(model)
        if answers is None:
            continue
        votes.append(answers)
        used.append(model)
        if len(votes) == 2:
            break

    if not votes:
        providers.log("讀音覆核跳過（所有金鑰與模型都失敗），沿用 Sudachi 的判斷")
        return {}

    if len(votes) == 1:
        providers.log(f"讀音覆核：只有 {used[0]} 回應，無法判斷不確定性")
        return votes[0]

    merged: dict[int, str] = {}
    disputed = 0
    for index in range(len(cases)):
        first, second = votes[0].get(index), votes[1].get(index)
        if first and first == second:
            merged[index] = first
        elif first and second:
            merged[index] = DISPUTED          # 兩邊不同 → 標成不確定
            disputed += 1
        elif first or second:
            merged[index] = first or second
    providers.log(f"讀音覆核：{used[0]} + {used[1]}，"
                  f"{len(merged)}/{len(cases)} 筆，其中 {disputed} 筆兩邊不一致")
    return merged


def build(lines: list[dict], db_path: Path | None = None,
          cross_check: bool = True) -> dict:
    """回傳 {"vocab": {...}, "tokens": [...]}，查不到字典就回空的。

    tokens 與 lines 一一對應，每個元素是這一句的 [start, end, lemma] 清單，
    start/end 是字元位置。lemma 為 null 代表那段不是可查的詞（助詞、標點），
    App 照樣要拿到它才能正確畫出詞的邊界。
    """
    tokenizer = _get_tokenizer()
    if not tokenizer:
        return {}
    try:
        dictionary = Dictionary(db_path) if db_path else Dictionary()
    except FileNotFoundError:
        return {}

    vocab: dict[str, dict] = {}
    tokens: list[list] = []
    # 有多讀音疑慮的位置，收集起來最後一次送去覆核
    doubtful: list[dict] = []

    for line in lines:
        text = line.get("text") or ""
        spans: list[list] = []
        cursor = 0

        for offset in range(0, len(text), CHUNK):
            for token in tokenizer.tokenize(text[offset:offset + CHUNK]):
                surface = token.surface()
                if not surface.strip():
                    continue
                # 用 find 對回原文的位置，因為 Sudachi 的 offset 是分段內的
                index = text.find(surface, cursor)
                if index < 0:
                    continue
                cursor = index + len(surface)

                if token.part_of_speech()[0] in SKIP_POS:
                    spans.append([index, cursor, None])
                    continue

                lemma = token.dictionary_form()
                # 查字典用原形的讀音，標假名用表面形的（見 _lemma_reading）
                # normalized_form 是最強的線索：Sudachi 會把「いる」正規化成
                # 「居る」、「すごい」正規化成「凄い」。純假名的高頻詞
                # （いう／いる／こと）讀音與詞性都分不出來，只有這個分得出。
                entry = dictionary.lookup(lemma, _lemma_reading(tokenizer, token),
                                          token.part_of_speech()[0],
                                          token.normalized_form())
                span = [index, cursor, lemma if entry else None]
                spans.append(span)

                if not entry:
                    continue
                if lemma not in vocab:
                    vocab[lemma] = _entry(dictionary, entry, token)

                # 同一個詞在不同句子可能讀音不同（「方」可以是かた也可以是ほう），
                # 所以疑慮要記在**出現的位置**上，不是記在詞表上。
                if _has_kanji(surface) and vocab[lemma].get("alt"):
                    doubtful.append({
                        "w": surface,
                        "sent": text,
                        "span": span,
                        "sudachi": to_hiragana(token.reading_form()),
                        "valid": {to_hiragana(r)
                                  for c in dictionary.lookup_all(lemma)
                                  if c["source"] == "jmdict" for r in c["readings"]},
                    })

        tokens.append(spans)

    if cross_check and doubtful:
        _apply_readings(doubtful)

    return {"vocab": vocab, "tokens": tokens}


def _apply_readings(doubtful: list[dict]) -> None:
    """把覆核結果寫回各個出現位置。

    只在 LLM 給的讀音**確實是這個詞在 JMdict 裡的合法讀音**時才採用 ——
    這道檢查讓模型不可能憑空編出一個讀音，等於用字典當防護網。
    採用時把讀音接在該位置的 span 後面（第四個元素），
    App 看到就用它覆蓋詞表的預設讀音。
    """
    answers = verify_readings(doubtful)
    if not answers:
        return
    changed = rejected = disputed = 0
    for index, case in enumerate(doubtful):
        raw = answers.get(index, "")
        if raw == DISPUTED:
            # 兩個模型講的不一樣 —— 這個位置標成不確定，App 顯示候選。
            # 這比默默挑一個好：使用者看到「這裡有兩種讀法」本身就是資訊。
            case["span"].append(DISPUTED)
            disputed += 1
            continue
        reading = to_hiragana(raw)
        if not reading or reading == case["sudachi"]:
            continue
        if reading not in case["valid"]:
            rejected += 1        # 不是合法讀音，當作模型看錯，保留 Sudachi 的
            continue
        case["span"].append(reading)
        changed += 1
    if changed or rejected or disputed:
        import providers
        providers.log(f"讀音覆核：改了 {changed} 處、標成不確定 {disputed} 處、"
                      f"擋掉 {rejected} 個不在字典裡的讀音")


def _entry(dictionary: Dictionary, entry: dict, token) -> dict:
    """一個詞在詞表裡的樣子。欄位名縮短是因為這份要整包下載到 App。"""
    lemma = token.dictionary_form()
    row = {
        "r": to_hiragana(entry["readings"][0] if entry.get("matched_reading")
                         else token.reading_form()),  # 原形的讀音
        "p": token.part_of_speech()[0],               # 詞性，給 App 分色用
        "zh": entry["zh"][:4],                        # 繁體中文釋義
        "en": [g for s in entry["senses"][:2] for g in s["en"][:3]],
    }

    # 同一個詞形有多種讀法時標記出來。這不是瑕疵而是有價值的資訊 ——
    # 「市場」有いちば與しじょう，學習者知道「這裡有兩種讀法」比
    # 默默顯示其中一個好。App 可以據此顯示候選。
    #
    # 只看 JMdict，不看 JMnedict。人名地名的讀音混進來會變成純雜訊 ——
    # 「日」會列出あきら、くさなぎ，「風」會列出かおる，那些都是人名讀法，
    # 對讀這句話的人沒有意義。實測不濾的話 306 個詞裡有 137 個被標成多讀音。
    #
    # 也要在平假名層級比對再去掉自己，否則外來語會自己跟自己並列
    # （主讀音 おふぃす、候選 オフィス）。
    others = {to_hiragana(r)
              for candidate in dictionary.lookup_all(lemma)
              if candidate["source"] == "jmdict"
              for r in candidate["readings"]}
    others.discard(row["r"])
    if others:
        row["alt"] = sorted(others)[:3]

    # 讀音是不是靠語境挑出來的。False 代表字典裡沒有這個讀音、
    # 我們退回用常用度猜的 —— App 要顯示假名時這個旗標決定敢不敢標。
    if not entry.get("matched_reading"):
        row["guess"] = True

    # 人名地名要標出來。Tomoshi 只涵蓋 JMdict，從來沒有 JMnedict，
    # 所以人名沒有中文釋義是**本來就這樣**，不是資料缺漏。
    # 實測四集裡沒有中文的 51 個詞有 47 個是人名（野村、ジョブズ）——
    # 對那些顯示「中文釋義缺漏」會誤導，使用者本來就不需要人名的中文釋義。
    if entry.get("source") == "jmnedict":
        row["name"] = True
    return row


def stats(payload: dict) -> str:
    if not payload:
        return "沒有詞表（缺 sudachipy 或字典還沒建）"
    vocab = payload.get("vocab", {})
    all_spans = [s for line in payload.get("tokens", []) for s in line]
    ambiguous = sum(1 for v in vocab.values() if v.get("alt"))
    guessed = sum(1 for v in vocab.values() if v.get("guess"))
    # 第四個元素是覆核後改掉的讀音
    fixed = sum(1 for s in all_spans if len(s) > 3)
    return (f"{len(vocab)} 個詞、{len(all_spans)} 個詞邊界"
            f"（多讀音 {ambiguous}、讀音靠猜 {guessed}、覆核改掉 {fixed} 處）")


def main() -> int:
    """拿資料庫裡現有的逐字稿補建詞表。"""
    import argparse
    import env
    env.load()
    from supabase_client import Supabase

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--all", action="store_true", help="連已經有詞表的也重建")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    supabase = Supabase()
    query = f"select=id,episode_id,lines,vocab&limit={args.limit}&order=created_at.desc"
    rows = supabase.select("kikitori_transcripts", query)
    if not rows:
        print("資料庫裡沒有逐字稿")
        return 1

    done = 0
    for row in rows:
        if row.get("vocab") and not args.all:
            continue
        payload = build(row["lines"])
        if not payload:
            print("建不出詞表，檢查 sudachipy 與 backend/data/jadict.sqlite")
            return 1
        print(f"  {row['episode_id'][:8]}　{stats(payload)}")
        if not args.dry_run:
            supabase.update("kikitori_transcripts", f"id=eq.{row['id']}",
                            {"vocab": payload})
        done += 1

    print(f"\n{'（試跑，沒有寫入）' if args.dry_run else '已寫入'} {done} 集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
