"""轉錄與翻譯的供應商。

轉錄：
  - Deepgram（主力）能直接吃音檔網址，不必先下載，一集 11 分鐘約 5 秒完成。
  - Groq Whisper（備援）只收檔案上傳且單檔上限 25MB，
    要先下載、壓成 16kHz 單聲道再切段，慢很多，所以只在 Deepgram 失敗時才用。

翻譯：
  - Gemini（主力），手上有多把免費金鑰，撞到配額就換下一把。
  - Groq（備援）。

兩邊都回傳統一的 Word 清單，好交給 segment.resegment 重新斷句。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import env
from segment import Word

# 本機直接跑時從 .env.local 補齊金鑰；CI 上由 GitHub Secrets 提供。
env.load()

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Kikitori/0.1"

# 轉錄模型不能照翻譯那樣「有什麼用什麼」。
#
# 翻譯換個同級模型，輸出差異小；轉錄換一個，出來的東西根本不是同一件事 ——
# 填充詞會不會被吃掉、有沒有詞級時間戳、多語言混講會不會硬拼成假名，
# 每一項都是選這幾個模型的理由，而且都是實測比出來的（見下面各家的註解）。
#
# 所以這裡是**驗證過的候選清單**，執行時確認「這幾個之中還活著哪一個」，
# 而不是從服務商的全部模型裡隨便挑一個。全部都不在了就明確報錯換下一家，
# 不要靜默降級成品質未知的模型。
#
# 要往清單裡加新模型之前，先跑 backend/benchmark.py 量過 CER 與填充詞保留數。
GEMINI_TRANSCRIBE_PREFERENCE = ["gemini-3.5-transcribe"]
GROQ_TRANSCRIBE_PREFERENCE = ["whisper-large-v3", "whisper-large-v3-turbo"]

# Deepgram 沒有列出模型的 API，只能寫死。下架的話會在呼叫時 400，
# 那時 transcribe() 會記錄原因並換下一家 —— 不會靜默。
DEEPGRAM_MODEL = "nova-3"

# 節目裡可能出現的語言。Gemini 一定要顯式列出（理由見 _gemini_transcribe_clip）。
#
# **只放兩種，不要再加。** 試過把中文加進來（中日雙語的學習節目很常見），
# 結果更糟 —— Gemini 不是「多語言一起處理」，而是**挑一個主導語言、其餘丟掉**。
# 同一段 50 秒的中日混講實測：
#
#     ja-JP + en-US                     123 詞，涵蓋 49.9/50 秒，日文完整
#     ja-JP + en-US + zh-TW             117 詞，涵蓋 41.3 秒
#     ja-JP + en-US + cmn-Hans-CN        65 詞，涵蓋 26.9 秒
#     ja-JP + en-US + zh-TW + cmn-Hans   64 詞，**日文全丟了，只剩中文**
#
# 最後一種正好是最糟的：使用者要學的是日文。
# 中文段落轉不好是可以接受的代價（使用者本來就懂中文），
# 日文被丟掉不行。
LANGUAGE_CODES = ["ja-JP", "en-US"]
# 不寫死模型名稱 —— 服務商一下架就會靜默失敗。
# 實際上就發生過：llama-3.3-70b-versatile 被 Groq 下架，
# 翻譯整條斷掉但錯誤被吞掉，逐字稿照樣產出，只是整份沒有翻譯。
# 改成啟動時去問「你現在有哪些模型」，再從偏好清單挑第一個還在的。
GEMINI_MODEL_PREFERENCE = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
]
GROQ_MODEL_PREFERENCE = [
    # qwen 排前面：實測它們的 reasoning_effort 吃 "none"，推理 token 真的是 0；
    # gpt-oss 最低只到 "low"，關不掉，每次還是會燒 70 個推理 token。
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    # groq/compound 放最後：它不遵守 response_format 的 json_object
    # （會回散文），而且實測連短提示都回 413。留著只是當最後的救命稻草。
    "groq/compound",
]

_model_cache: dict[str, str | None] = {}

# 一次送幾句去翻譯。太小則往返次數暴增（一集上千句），
# 太大則單次輸出被截斷、而且一批失敗會連累很多句。
TRANSLATE_BATCH = 40
# Whisper 備援切段長度。Groq 單檔上限 25MB，
# 壓成 16kHz 單聲道 48kbps 之後 15 分鐘約 5.4MB，留了很大餘裕 ——
# 因為切段是為了不撞上限，不是為了塞滿。
SEGMENT_SECONDS = 900


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# 每一次 API 呼叫的紀錄：哪一把金鑰、哪個模型、成功與否、花多久、錯在哪。
#
# 加這個是因為踩過一次：一集轉錄退回了 Deepgram，我判斷是「撞到配額」，
# 但那是推論不是證據 —— 真正的原因是涵蓋檢查沒過。沒有逐次紀錄的話，
# 「為什麼換手」只能用猜的，而猜錯會把力氣花在不存在的問題上。
#
# 金鑰只記索引不記內容。
CALLS: list[dict] = []


def record(provider: str, key_index: int, model: str, ok: bool,
           seconds: float, error: str = "") -> None:
    CALLS.append({"provider": provider, "key": key_index, "model": model,
                  "ok": ok, "seconds": round(seconds, 1), "error": error[:120]})


def call_summary() -> str:
    """把這一趟所有 API 呼叫整理成一段可讀的報告。"""
    if not CALLS:
        return "這一趟沒有打過任何 API"

    lines = [f"API 呼叫共 {len(CALLS)} 次："]
    groups: dict[tuple, list[dict]] = {}
    for call in CALLS:
        groups.setdefault((call["provider"], call["model"]), []).append(call)

    for (provider, model), items in groups.items():
        ok = [c for c in items if c["ok"]]
        bad = [c for c in items if not c["ok"]]
        used = sorted({c["key"] for c in items})
        total = sum(c["seconds"] for c in items)
        lines.append(
            f"  {provider}/{model}：{len(ok)} 成功 {len(bad)} 失敗，"
            f"用了金鑰 {used}，共 {total:.0f} 秒")
        # 失敗的原因要看得到，而且要分得出配額、格式錯誤還是別的
        reasons: dict[str, int] = {}
        for c in bad:
            reasons[c["error"]] = reasons.get(c["error"], 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:3]:
            lines.append(f"      失敗 ×{count}：{reason}")
    return "\n".join(lines)


def _keys(name: str) -> list[str]:
    """讀一組金鑰。環境變數可以是 JSON 陣列，也可以是單一字串。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [k for k in json.loads(raw) if k]
        except json.JSONDecodeError:
            return []
    return [raw]


def _post(url: str, data: bytes, headers: dict, timeout: int = 900):
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"User-Agent": USER_AGENT, **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


# ---------------------------------------------------------------------------
# 轉錄
# ---------------------------------------------------------------------------

def transcribe_deepgram(audio_url: str, language: str) -> list[Word]:
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("沒有 DEEPGRAM_API_KEY")

    # 用 multi 而不是指定單一語言。
    #
    # 實測發現：指定 language=ja 去轉英文段落時，Deepgram 不會跳過，
    # 而是把英文音節硬拼成假名（"thankyouismaybeyouronecoose。。。"），
    # 那串垃圾會被當成正常內容寫進逐字稿，再拿去翻譯 —— 它看起來像資料不像錯誤。
    # バイリンガルニュース 那種雙語節目有 36% 的內容會變成這樣。
    #
    # multi 在純日文節目上也沒有變差：實測填充詞 16 vs 14、語尾助詞 51 vs 46，
    # 反而保留得更多，速度也更快。
    #
    # diarize 順便開著。它會自動判斷有幾個人，不必事先指定，
    # 純獨白節目就回報一位，沒有副作用。
    params = (
        f"model={DEEPGRAM_MODEL}&language=multi"
        "&punctuate=true&smart_format=true&utterances=true&diarize=true"
    )
    log(f"Deepgram 轉錄（{DEEPGRAM_MODEL} / multi），直接讀取音檔網址")

    payload = _post(
        f"https://api.deepgram.com/v1/listen?{params}",
        json.dumps({"url": audio_url}).encode(),
        {"Authorization": f"Token {key}", "Content-Type": "application/json"},
    )

    alternatives = payload.get("results", {}).get("channels", [{}])[0].get("alternatives", [])
    if not alternatives:
        raise RuntimeError("Deepgram 沒有回傳結果")

    words = alternatives[0].get("words", [])
    if not words:
        raise RuntimeError("Deepgram 沒有回傳詞級時間戳")

    log(f"Deepgram 完成，{len(words)} 個詞")
    return [
        Word(
            text=(w.get("punctuated_word") or w.get("word") or ""),
            start=float(w.get("start", 0)),
            end=float(w.get("end", 0)),
            speaker=w.get("speaker"),
        )
        for w in words
        if (w.get("punctuated_word") or w.get("word"))
    ]


# 一段音檔的轉錄逾時。
#
# 原本設 900 秒（怕 verbatim 太慢），實測一段 9.7 MB 只要 101 秒 ——
# 但有一次連線在 SSL 交握就卡住，於是**整整等了 900 秒才失敗**，
# 40 分鐘的節目總共花 23 分半，其中 15 分鐘是在等那個逾時。
#
# 300 秒對正常情況仍有三倍餘裕，卡住時損失只剩五分之一。
TRANSCRIBE_TIMEOUT = 300

# 開詞級時間戳之後的實測邊界：31 分以內乾淨，35 分開始有 annotation 缺尾，
# 45 分時間戳出現鬼值，55 分只轉到 63%，60 分直接 400。
# 留一點餘裕切在 28 分。
GEMINI_MAX_MINUTES = 28


def _parse_offset(value: str | None) -> float:
    """把 "0.400s" 這種格式轉成秒。"""
    if not value:
        return 0.0
    return float(str(value).rstrip("s") or 0)


def _gemini_transcribe_clip(path: Path, key: str, offset_seconds: float = 0.0,
                            model: str | None = None) -> list[Word]:
    """把一段音檔送去 gemini-3.5-transcribe。

    詞級時間戳只有 /v1beta/interactions 端點支援，
    generateContent 傳 transcription_config 會直接 400。

    音檔走 inline base64 而不是 File API，有兩個理由：
    一是 10 分鐘的節目壓縮後才 2MB 多，上傳那一步純屬多餘；
    二是 File API 上傳的檔案綁金鑰，用 A 金鑰上傳、B 金鑰呼叫會 403，
    那會跟我們的多金鑰輪替打架。
    """
    import base64

    payload = {
        "model": model or GEMINI_TRANSCRIBE_PREFERENCE[0],
        "input": [{
            "type": "audio",
            "data": base64.b64encode(path.read_bytes()).decode(),
            "mime_type": "audio/mp3",
        }],
        "generation_config": {"transcription_config": {
            "mode": {
                # verbatim 是保留口語填充詞的關鍵。實測同一集：
                # 這個模式下填充詞 60 個，Deepgram 只有 2 個、Whisper 3 個。
                "type": "verbatim",
                "timestamp_granularities": ["word"],
                # 講者資訊只掛在 word annotation 上，
                # 所以單獨開 diarization 會拿到空的，必須跟時間戳一起開。
                "diarization_mode": "speaker",
            },
            # 一定要顯式列出所有可能出現的語言，這是硬性條件。
            #
            # Gemini 沒有「多語言模式」這種開關：不指定語言的行為
            # 跟指定單一語言完全相同（實測都是 37.9% 空白），
            # 自動偵測只會挑一個主導語言，其餘整段靜默丟棄。
            #
            # 而且它的失敗比 Deepgram 更難察覺 —— Deepgram 會把英文
            # 硬拼成假名（垃圾但看得出來），Gemini 是直接沒有那些詞，
            # 但因為日文段落跨在前後，涵蓋率仍然顯示 99.8%。
            # 光看 coverage 這個失敗是隱形的，一定要靠 verify 的空白比例才抓得到。
            #
            # 中文也一定要列進來。這支 App 的使用者是台灣人，會聽的
            # 有一大類是「中文母語者學日文」的雙語節目 —— 少了中文，
            # 那些段落會被硬轉成日文假名，產出
            # 「hello但是好徐理是理붴를세요。」這種垃圾，跟當初少了英文時
            # 一模一樣（實測 EP45 那集有 5% 的句子整句是這種東西）。
            "language_codes": LANGUAGE_CODES,
        }},
    }

    response = _post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        json.dumps(payload).encode(),
        {"x-goog-api-key": key, "Content-Type": "application/json"},
        timeout=TRANSCRIBE_TIMEOUT,
    )

    steps = response.get("steps") or []
    if not steps:
        raise RuntimeError("Gemini 沒有回傳 steps")
    contents = steps[0].get("content") or []
    if not contents:
        raise RuntimeError("Gemini 沒有回傳內容")

    words: list[Word] = []
    for item in contents[0].get("annotations") or []:
        text = (item.get("text") or "").strip()
        if not text or item.get("type") not in (None, "word_info"):
            continue
        speaker = item.get("speaker")
        if isinstance(speaker, str) and ":" in speaker:
            # 標籤長這樣："spk:0"
            speaker = int(speaker.split(":")[-1]) if speaker.split(":")[-1].isdigit() else None
        words.append(Word(
            text=text,
            start=_parse_offset(item.get("start_offset")) + offset_seconds,
            end=_parse_offset(item.get("end_offset")) + offset_seconds,
            speaker=speaker if isinstance(speaker, int) else None,
        ))

    if not words:
        raise RuntimeError("Gemini 沒有回傳詞級時間戳")
    return _clean_gemini_words(words)


# Gemini 的時間戳有兩種固有噪音，兩者都不是轉錄錯誤，
# 但不處理的話每一集都會被 verify 判定失敗，等於這條路白開。
MAX_WORD_SECONDS = 5.0


def _clean_gemini_words(words: list[Word]) -> list[Word]:
    """把 Gemini 時間戳的噪音整理掉。

    兩種固有現象：
    - 有些詞被標成十幾秒長，實際上是把後面那段停頓算進了詞長
      （實測看過一個「は」佔 10.5 秒）
    - 有些詞是零長度（end == start），10 分鐘裡約 35 個

    這些不影響內容，只影響時間軸的合理性檢查，所以在這裡收斂：
    詞長超過上限就截到下一個詞的開頭，零長度就給一個最小值。
    """
    cleaned: list[Word] = []
    for index, word in enumerate(words):
        start = word.start
        end = word.end

        next_start = words[index + 1].start if index + 1 < len(words) else None

        if end <= start:
            # 零長度：撐到下一個詞開始之前，最多 0.3 秒
            end = min(start + 0.3, next_start) if next_start else start + 0.3
            end = max(end, start + 0.05)
        elif end - start > MAX_WORD_SECONDS:
            # 過長：多出來的是停頓不是發音，截掉
            limit = start + MAX_WORD_SECONDS
            end = min(limit, next_start) if next_start else limit
            end = max(end, start + 0.05)

        cleaned.append(Word(text=word.text, start=start, end=end, speaker=word.speaker))
    return cleaned


def transcribe_gemini(audio_url: str, language: str) -> list[Word]:
    """用 gemini-3.5-transcribe 轉錄。

    比 Deepgram 慢五倍以上，但填充詞保留度差三十倍 ——
    對「聽 podcast 學日語」來說那不是邊際改善，
    學習者要聽懂真實對話，靠的正是「あの」「えーと」「〜ね」這些東西。
    """
    keys = _keys("GEMINI_API_KEYS")
    if not keys:
        raise RuntimeError("沒有 GEMINI_API_KEYS")

    model = transcribe_model("gemini", keys)
    if not model:
        raise RuntimeError(
            "Gemini 的轉錄模型都不在了："
            + "、".join(GEMINI_TRANSCRIBE_PREFERENCE)
            + "。要換模型請先跑 benchmark.py 量過品質再加進 "
              "GEMINI_TRANSCRIBE_PREFERENCE")
    log(f"Gemini 轉錄（{model}）")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        log("下載音檔")
        raw = _download(audio_url, workdir / "raw_audio")

        compressed = workdir / "clip.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
             "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", str(compressed)],
            check=True,
        )
        raw.unlink(missing_ok=True)

        total = _probe_duration(compressed)
        segment_seconds = GEMINI_MAX_MINUTES * 60

        if total <= segment_seconds:
            parts = [compressed]
        else:
            log(f"共 {total/60:.1f} 分，超過 {GEMINI_MAX_MINUTES} 分的時間戳可信範圍，切段")
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(compressed),
                 "-f", "segment", "-segment_time", str(segment_seconds),
                 "-reset_timestamps", "1", "-c", "copy", str(workdir / "part_%03d.mp3")],
                check=True,
            )
            parts = sorted(workdir.glob("part_*.mp3"))

        words: list[Word] = []
        offset = 0.0
        cursor = 0
        for index, part in enumerate(parts):
            size_mb = part.stat().st_size / 1048576
            log(f"Gemini 轉錄第 {index+1}/{len(parts)} 段（{size_mb:.1f} MB）")
            # 時間軸用實際長度累加，用固定值推算會愈後面愈歪
            duration = _probe_duration(part)

            # 免費層的配額是會回填的水桶：打完一發就欠著，
            # 幾秒到幾十秒後才恢復。撞到就換下一把金鑰，
            # 手上有幾把就等於同時有幾個水桶。
            for attempt in range(len(keys)):
                index = (cursor + attempt) % len(keys)
                key = keys[index]
                started = time.time()
                try:
                    words.extend(_gemini_transcribe_clip(part, key, offset, model))
                    record("gemini", index, model, True, time.time() - started)
                    cursor = (cursor + attempt + 1) % len(keys)
                    break
                except urllib.error.HTTPError as exc:
                    # 429 才是配額，其他 HTTP 錯誤是別的問題 —— 分開記，
                    # 不然「為什麼換手」又只能用猜的
                    reason = "配額 429" if exc.code == 429 else f"HTTP {exc.code}"
                    record("gemini", index, model, False, time.time() - started, reason)
                    if exc.code == 429 and attempt < len(keys) - 1:
                        continue
                    raise
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    # 連線層的錯誤（SSL 交握卡住、逾時、對端斷線）也要換金鑰重試。
                    # 原本只有 429 會重試，於是一次 SSL 卡住就放棄整個 Gemini
                    # 退回 Deepgram —— 而 Gemini 的填充詞保留度是它的四倍，
                    # 為了一次連線問題丟掉那個差異不值得。
                    record("gemini", index, model, False, time.time() - started,
                           f"{type(exc).__name__}: {str(exc)[:60]}")
                    if attempt < len(keys) - 1:
                        continue
                    raise
                except Exception as exc:
                    record("gemini", index, model, False, time.time() - started,
                           f"{type(exc).__name__}: {exc}")
                    raise
            offset += duration

    log(f"Gemini 完成，{len(words)} 個詞")
    return words


def _download(url: str, target: Path) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=900) as response, open(target, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    return target


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _whisper_one(path: Path, language: str, key: str, model: str) -> list[dict]:
    boundary = "----KikitoriBoundary7MA4YWxkTrZu0gW"
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    if language:
        fields["language"] = language

    body = bytearray()
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode()
    body += b"Content-Type: audio/mpeg\r\n\r\n"
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()

    payload = _post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        bytes(body),
        {"Authorization": f"Bearer {key}",
         "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return payload.get("words") or payload.get("segments") or []


def transcribe_groq(audio_url: str, language: str) -> list[Word]:
    """備援路線：下載 → 壓縮 → 切段 → 逐段送 Whisper。"""
    keys = _keys("GROQ_API_KEYS")
    if not keys:
        raise RuntimeError("沒有 GROQ_API_KEYS")

    model = transcribe_model("groq", keys)
    if not model:
        raise RuntimeError(
            "Groq 的 Whisper 模型都不在了："
            + "、".join(GROQ_TRANSCRIBE_PREFERENCE))
    log(f"Whisper 轉錄（{model}）")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        log("下載音檔")
        raw = _download(audio_url, workdir / "raw_audio")
        log(f"下載完成 {raw.stat().st_size / 1048576:.1f} MB，壓成 16kHz 單聲道")

        compressed = workdir / "compressed.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
             "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", str(compressed)],
            check=True,
        )
        raw.unlink(missing_ok=True)

        total = _probe_duration(compressed)
        if total > SEGMENT_SECONDS:
            log(f"共 {total/60:.1f} 分，切成每段 {SEGMENT_SECONDS//60} 分")
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(compressed),
                 "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
                 "-reset_timestamps", "1", "-c", "copy", str(workdir / "part_%03d.mp3")],
                check=True,
            )
            parts = sorted(workdir.glob("part_*.mp3"))
        else:
            parts = [compressed]

        words: list[Word] = []
        offset = 0.0
        for index, part in enumerate(parts):
            log(f"Whisper 第 {index+1}/{len(parts)} 段")
            # 切段時間軸要用實際長度累加，用固定值推算會愈後面愈歪
            duration = _probe_duration(part)
            raw_words = _whisper_one(part, language, keys[index % len(keys)], model)
            for item in raw_words:
                text = (item.get("word") or item.get("text") or "").strip()
                if not text:
                    continue
                words.append(Word(
                    text=text,
                    start=float(item.get("start", 0)) + offset,
                    end=float(item.get("end", 0)) + offset,
                ))
            offset += duration

    log(f"Whisper 完成，{len(words)} 個詞")
    return words


def transcribe(audio_url: str, language: str,
               audio_seconds: float | None = None,
               on_fallback=None) -> tuple[list[Word], str]:
    """轉錄音檔。先試 Deepgram，不行才退回 Whisper。

    給了 audio_seconds 的話，每一家的結果都會先驗證涵蓋範圍才採用 ——
    只轉到一半卻回報成功是最糟的失敗，App 上看起來一切正常，
    聽到中途逐字稿就沒了。涵蓋不足就當作這家失敗，換下一家。
    """
    import verify

    # 順序是照「口語保真度」排的，不是照速度。
    #
    # Gemini 的 verbatim 模式填充詞保留 60 個，Deepgram 16 個、Whisper 14 個 ——
    # 對「聽 podcast 學日語」來說，「あの」「えーと」「〜ね」是內容不是雜訊，
    # 學習者要聽懂真實對話靠的就是這些。代價是慢十倍，值得。
    # 撞到配額或長度限制時，後面兩家仍然可用。
    attempts = (
        ("gemini", lambda: transcribe_gemini(audio_url, language)),
        ("deepgram", lambda: transcribe_deepgram(audio_url, language)),
        ("groq", lambda: transcribe_groq(audio_url, language)),
    )

    last_error: str | None = None

    for name, run in attempts:
        try:
            words = run()
        except Exception as exc:
            last_error = f"{name} {exc}"
            log(f"{name} 失敗（{exc}），換下一家")
            if on_fallback:
                on_fallback(f"{name} 失敗：{exc}")
            continue

        if audio_seconds:
            report = verify.check(words, audio_seconds)
            if not report.ok:
                last_error = f"{name} {report.summary()}"
                log(f"{name} 涵蓋檢查沒過：{report.summary()}，換下一家")
                if on_fallback:
                    on_fallback(f"{name} 涵蓋檢查沒過：{report.summary()}")
                continue
            log(f"{name} 涵蓋檢查通過（{report.coverage_ratio*100:.1f}%）")

        # 記下實際用到的模型，不只是哪一家 —— 之後回頭查某一集品質為什麼
        # 特別差時，要分得出是哪個模型轉的。模型名稱是執行時才解析出來的，
        # 所以到這裡才組。
        used = DEEPGRAM_MODEL if name == "deepgram" else _transcribe_cache.get(name)
        return words, f"{name}:{used}" if used else name

    raise RuntimeError(f"所有轉錄服務都不可用或結果不完整：{last_error}")


# ---------------------------------------------------------------------------
# 翻譯
# ---------------------------------------------------------------------------

def _translate_prompt(batch: list[str], target: str) -> str:
    numbered = {str(i): text for i, text in enumerate(batch)}
    return (
        f"你是逐句字幕翻譯。把下面 JSON 每個值翻成{target}。\n"
        "規則：\n"
        "1. 鍵完全保留，數量與順序不變。\n"
        "2. 一句對一句，不要合併或拆開，不要加註解。\n"
        "3. 這是口語對話，翻得自然，保留語氣。\n"
        "4. 遇到句子被切斷的殘句，就照字面翻，不要自行補完。\n"
        "5. 只輸出 JSON 物件。\n\n"
        + json.dumps(numbered, ensure_ascii=False)
    )


def _get(url: str, headers: dict, timeout: int = 30):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _list_models(provider: str, key: str, chat_only: bool = True) -> set[str]:
    """服務商目前有的模型。

    chat_only 只影響 Gemini：轉錄模型走 /v1beta/interactions，
    不支援 generateContent，所以查轉錄模型時不能套那個過濾條件，
    否則永遠找不到自己要的那個。
    """
    if provider == "gemini":
        payload = _get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200", {}
        )
        return {m["name"].replace("models/", "") for m in payload.get("models", [])
                if not chat_only
                or "generateContent" in m.get("supportedGenerationMethods", [])}
    payload = _get("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})
    return {m["id"] for m in payload.get("data", [])}


_transcribe_cache: dict[str, str | None] = {}


def transcribe_model(provider: str, keys: list[str]) -> str | None:
    """挑一個還活著的轉錄模型，只從驗證過的候選清單裡挑。

    回 None 代表清單裡的模型全都不在了 —— 呼叫端要據此換下一家，
    不要退而求其次拿沒量過品質的模型硬上。上次 Groq 下架
    llama-3.3-70b-versatile 時翻譯整條靜默斷掉，就是因為沒有這一步。
    """
    if provider in _transcribe_cache:
        return _transcribe_cache[provider]

    preference = (GEMINI_TRANSCRIBE_PREFERENCE if provider == "gemini"
                  else GROQ_TRANSCRIBE_PREFERENCE)
    chosen = None
    for key in keys:
        try:
            existing = _list_models(provider, key, chat_only=False)
        except Exception:
            continue
        chosen = next((m for m in preference if m in existing), None)
        if chosen:
            break
        # 問得到清單但候選一個都不在 —— 這是要知道的事，不是可以忽略的事
        log(f"{provider} 的轉錄模型都不在了（找過 {', '.join(preference)}）")
        break

    _transcribe_cache[provider] = chosen
    return chosen


def available_models(provider: str, keys: list[str]) -> list[str]:
    """這個服務商目前真的還有的模型，依偏好順序排。

    每個行程只問一次就記住。回空清單代表這條路不通，
    讓呼叫端明確知道，而不是拿不存在的模型名稱去撞 404。
    """
    if provider in _model_cache:
        return _model_cache[provider]

    preference = GEMINI_MODEL_PREFERENCE if provider == "gemini" else GROQ_MODEL_PREFERENCE
    ordered: list[str] = []

    for key in keys:
        try:
            existing = _list_models(provider, key)
        except Exception:
            continue

        ordered = [m for m in preference if m in existing]
        if not ordered and existing:
            # 偏好清單全落空，至少留下看起來能對話的，別直接放棄
            ordered = sorted(m for m in existing
                             if not any(x in m for x in ("whisper", "tts", "embed", "guard",
                                                         "transcribe", "image", "veo", "imagen")))
        if ordered:
            log(f"{provider} 可用模型：{', '.join(ordered[:3])}"
                + (f"（共 {len(ordered)} 個）" if len(ordered) > 3 else ""))
            break

    _model_cache[provider] = ordered
    return ordered


def pick_model(provider: str, keys: list[str]) -> str | None:
    models = available_models(provider, keys)
    return models[0] if models else None


# 關掉思考的寫法，依序試。**兩種語法是互補的，不是二選一。**
#
# 實測（看 usageMetadata.thoughtsTokenCount，那才是真的有沒有在思考）：
#
#     模型                    thinkingBudget:0   thinkingLevel:MINIMAL
#     gemini-3.7-flash        關掉               400
#     gemini-3.5-flash        關掉               關掉
#     gemini-2.5-flash        關掉               400
#     gemini-3.6-flash        **400**            **關掉**
#     gemini-3.1-flash-lite   關掉（本來就不思考）  關掉
#
# 所以 3.6 只吃 level、2.5 只吃 budget。寫死任一種都會有模型漏掉，
# 而漏掉的後果是它**默默開著思考跑**（3.6 實測燒 799 個思考 token
# 才產出幾十個字的翻譯）—— 那是使用者明確要求關掉的東西。
#
# 順序有意義：budget 支援的模型比較多，先試它。
#
# 官方文件補充了實測沒涵蓋的部分：Gemini 3 世代改用 thinkingLevel（字串），
# 2.5 世代才用 thinkingBudget（數字），而且**兩個不能同時傳，會 400**。
# 各模型能接受的最低等級也不一樣：3.6／3.5 有 minimal，3.8／3.7 最低只到 low。
#
# 所以順序是「最省的先試」：minimal → low → budget=0 → 放棄。
_THINKING_OFF = [
    {"thinkingLevel": "MINIMAL"},   # 3.6 / 3.5 / flash-lite 支援
    {"thinkingLevel": "LOW"},       # 3.8 / 3.7 最低只到這裡
    {"thinkingBudget": 0},          # 2.5 世代（3.x 傳這個有的會 400）
]

# 每個模型實際能用哪一種，執行時試出來記住。不寫死名稱 ——
# 關思考的語法每一代都在變（Gemini 3 已經不吃舊的寫法），清單一定會過期。
_THINKING_CONFIG: dict[str, dict | None] = {}


def thinking_candidates(model: str) -> list[dict | None]:
    """這個模型要依序試哪些「關掉思考」的寫法。

    已經試出來過就只回那一種，沒試過就回全部候選再加上 None（放棄）。
    給 vocab.verify_readings 之類的其他呼叫端共用 —— 每個地方各自寫死
    一種語法的話，總會有地方漏掉，而漏掉的後果是默默開著思考跑。
    """
    if model in _THINKING_CONFIG:
        return [_THINKING_CONFIG[model]]
    return [*_THINKING_OFF, None]


def remember_thinking(model: str, config: dict | None) -> None:
    """記住這個模型能用的寫法。config 是 None 代表關不掉。"""
    if model not in _THINKING_CONFIG:
        _THINKING_CONFIG[model] = config
        if config is None:
            log(f"⚠ {model} 沒有任何一種寫法能關掉思考，這個模型會開著思考跑")


def check_thinking(model: str, payload: dict) -> None:
    """回應裡若還有思考 token，就是沒關成功 —— 要講出來，不要靜默。"""
    used = (payload.get("usageMetadata") or {}).get("thoughtsTokenCount") or 0
    if used:
        log(f"⚠ {model} 思考模式沒關成功，這次燒了 {used} 個思考 token")


def _gemini_batch(prompt: str, key: str, model: str) -> str:
    """送一批文字給 Gemini，並確保思考模式是關的。

    翻譯不需要推理。實測 gemini-3.8-flash 預設會花 387 tokens 思考
    才產出 36 tokens 的答案 —— 九成額度燒在沒用的地方，速度也慢一倍。
    """
    def send(thinking: dict | None):
        config = {"responseMimeType": "application/json", "temperature": 0.2}
        if thinking is not None:
            config["thinkingConfig"] = thinking
        return _post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}",
            json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": config}).encode(),
            {"Content-Type": "application/json"},
            timeout=600,
        )

    payload = None
    for candidate in thinking_candidates(model):
        try:
            payload = send(candidate)
            remember_thinking(model, candidate)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
    if payload is None:
        raise RuntimeError(f"{model} 每一種請求寫法都被拒絕")

    check_thinking(model, payload)
    return payload["candidates"][0]["content"]["parts"][0]["text"]


# Groq 這邊的「關掉推理」候選，跟 Gemini 一樣依序試。
#
# 實測（數字是 usage.completion_tokens_details.reasoning_tokens）：
#
#     模型                  不送參數   reasoning_effort:"low"
#     openai/gpt-oss-120b   279       25
#     openai/gpt-oss-20b    327        9
#     qwen/qwen3.6-27b      1303      **400**
#     groq/compound         沒有推理   **400**
#
# 所以寫死 "low" 會讓 qwen3.6 與 compound **永遠失敗** ——
# 五個偏好模型裡兩個掛掉，而且失敗訊息被上層吞成「這個模型不通」，
# 看起來像模型有問題，其實是我們送錯參數。
# 另外實測 "none" / "default" / "minimal" 全部 400，Groq 只認 low/medium/high。
#
# 官方文件：qwen 系列吃 "none"（qwen3.8 預設就是 none），
# gpt-oss 只吃 low/medium/high、最低只到 low、關不掉。
# 所以順序是 none → low → 不送參數。
# 注意 reasoning_format:"hidden" 只是不回傳推理內容，token 照樣算錢，不是關閉。
_REASONING_OFF: list[dict] = [
    {"reasoning_effort": "none"},   # qwen 系列
    {"reasoning_effort": "low"},    # gpt-oss 系列的最低值
    {},                             # 都不接受就不送（推理會開著，會印警告）
]
_REASONING_CONFIG: dict[str, dict] = {}
# 關不掉的模型記一次，免得每次呼叫都洗版
_REASONING_FLOOR: dict[str, int] = {}

# 哪些（模型, 金鑰）組合是 404。不同金鑰能用的模型不一樣 ——
# 實測 gemini-2.5-flash-lite 在八把金鑰裡只有三把有，其餘回 404。
# 404 在同一次執行內不會變，記起來就不用每批都重撞一次
# （實測一集浪費 9 次呼叫）。
_MISSING: set[tuple[str, int]] = set()


def is_missing(model: str, key_index: int) -> bool:
    return (model, key_index) in _MISSING


def mark_missing(model: str, key_index: int) -> None:
    _MISSING.add((model, key_index))


def _groq_batch(prompt: str, key: str, model: str) -> str:
    def send(extra: dict):
        return _post(
            "https://api.groq.com/openai/v1/chat/completions",
            json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                **extra,
            }).encode(),
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=600,
        )

    candidates = ([_REASONING_CONFIG[model]] if model in _REASONING_CONFIG
                  else _REASONING_OFF)
    payload = None
    for candidate in candidates:
        try:
            payload = send(candidate)
            _REASONING_CONFIG.setdefault(model, candidate)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
    if payload is None:
        raise RuntimeError(f"{model} 每一種請求寫法都被拒絕")

    used = ((payload.get("usage") or {})
            .get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    # 有些模型本來就關不掉：官方文件寫明 gpt-oss 系列的 reasoning_effort
    # 最低只到 low，沒有 none。所以「還有推理 token」不一定是設定沒生效。
    #
    # 但這件事一定要講出來（使用者要求關掉思考），只是每個模型講一次就好，
    # 不要每次呼叫都洗版。
    if used > 50 and model not in _REASONING_FLOOR:
        _REASONING_FLOOR[model] = used
        setting = _REASONING_CONFIG.get(model) or "沒送參數"
        log(f"{model} 用了 {setting} 仍有 {used} 個推理 token —— "
            f"這是這個模型的下限，關不掉")
    return payload["choices"][0]["message"]["content"]


def translation_health() -> tuple[bool, str]:
    """轉錄開始前先確認翻譯真的能用。

    寧可讓整個任務失敗，也不要產出一份沒有翻譯的逐字稿 ——
    那種失敗是靜默的：CI 綠燈、通知報成功，但東西是壞的，
    而且要等使用者打開 App 才會發現。
    """
    for provider, env_name in (("gemini", "GEMINI_API_KEYS"), ("groq", "GROQ_API_KEYS")):
        keys = _keys(env_name)
        if not keys:
            continue
        model = pick_model(provider, keys)
        if not model:
            continue
        for key in keys:
            try:
                _batch = _gemini_batch if provider == "gemini" else _groq_batch
                raw = _batch('回傳這個 JSON，不要加任何其他東西：{"ok":"1"}', key, model)
                if "ok" in raw:
                    return True, f"{provider} / {model}"
            except Exception:
                continue
    return False, "沒有任何翻譯服務可用"


def _translate_once(lines: list[str], target: str) -> tuple[list[str] | None, list[str]]:
    """把一份句子送出去翻譯，回傳（結果, 失敗原因清單）。

    金鑰的用法是「一把用到不能用為止」：同一把金鑰會把它所有可用的模型
    都試過一輪，全部撞牆才換下一把。這樣額度是一把一把耗盡，
    而不是每把都只用掉一點點。同一時間只會有一個請求在跑，不並行。
    """
    prompt = _translate_prompt(lines, target)
    failures: list[str] = []

    for provider, env_name, caller in (
        ("gemini", "GEMINI_API_KEYS", _gemini_batch),
        ("groq", "GROQ_API_KEYS", _groq_batch),
    ):
        keys = _keys(env_name)
        if not keys:
            continue

        models = available_models(provider, keys)
        if not models:
            failures.append(f"{provider} 沒有可用模型")
            continue

        for index, key in enumerate(keys, 1):
            for model in models:
                started = time.time()
                try:
                    parsed = json.loads(caller(prompt, key, model))
                    result = [str(parsed.get(str(i), "")) for i in range(len(lines))]
                    if any(result):
                        record(provider, index, model, True, time.time() - started)
                        return result, failures
                    record(provider, index, model, False, time.time() - started, "回應是空的")
                    failures.append(f"{provider}#{index}/{model} 回應是空的")
                except urllib.error.HTTPError as exc:
                    reason = "配額 429" if exc.code == 429 else f"HTTP {exc.code}"
                    record(provider, index, model, False, time.time() - started, reason)
                    failures.append(f"{provider}#{index}/{model} {reason}")
                except json.JSONDecodeError:
                    record(provider, index, model, False, time.time() - started, "回應不是 JSON")
                    failures.append(f"{provider}#{index}/{model} 回應不是 JSON")
                except Exception as exc:
                    record(provider, index, model, False, time.time() - started,
                           f"{type(exc).__name__}: {exc}")
                    failures.append(f"{provider}#{index}/{model} {type(exc).__name__}")

    return None, failures


def translate(lines: list[str], target: str = "繁體中文") -> list[str]:
    """翻譯整份逐字稿。

    預設一次送完。一集約 4000 tokens，而模型的輸入上限是百萬級、
    輸出上限六萬多，分批只是多打幾次 API，沒有任何好處。
    整份失敗才退回分批 —— 那時候分批的價值是「至少救回一部分」。
    """
    if not lines:
        return []

    log(f"翻譯 {len(lines)} 句（整份一次送）")
    result, failures = _translate_once(lines, target)
    if result:
        return result

    unique = list(dict.fromkeys(failures))[:5]
    log(f"整份翻譯失敗（{'；'.join(unique)}），改成分批重試")

    output: list[str] = []
    for start in range(0, len(lines), TRANSLATE_BATCH):
        batch = lines[start:start + TRANSLATE_BATCH]
        log(f"  重試 {start + 1}–{start + len(batch)} / {len(lines)}")
        part, part_failures = _translate_once(batch, target)
        if part:
            output.extend(part)
        else:
            output.extend("" for _ in batch)
            log(f"  這批仍然失敗（{'；'.join(list(dict.fromkeys(part_failures))[:2])}）")

    return output
