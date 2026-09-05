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

DEEPGRAM_MODEL = "nova-2"
WHISPER_MODEL = "whisper-large-v3"
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
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-20b",
    "groq/compound",
]

_model_cache: dict[str, str | None] = {}

TRANSLATE_BATCH = 40
SEGMENT_SECONDS = 900


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


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

    params = (
        f"model={DEEPGRAM_MODEL}&language={language}"
        "&punctuate=true&smart_format=true&utterances=true"
    )
    log(f"Deepgram 轉錄（{language}），直接讀取音檔網址")

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
        )
        for w in words
        if (w.get("punctuated_word") or w.get("word"))
    ]


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


def _whisper_one(path: Path, language: str, key: str) -> list[dict]:
    boundary = "----KikitoriBoundary7MA4YWxkTrZu0gW"
    fields = {
        "model": WHISPER_MODEL,
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
            raw_words = _whisper_one(part, language, keys[index % len(keys)])
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


def transcribe(audio_url: str, language: str) -> tuple[list[Word], str]:
    """先試 Deepgram，不行才退回 Whisper。回傳詞清單與實際用了哪一家。"""
    try:
        return transcribe_deepgram(audio_url, language), f"deepgram:{DEEPGRAM_MODEL}"
    except Exception as exc:
        log(f"Deepgram 失敗（{exc}），改用 Groq Whisper")
        return transcribe_groq(audio_url, language), f"groq:{WHISPER_MODEL}"


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


def _list_models(provider: str, key: str) -> set[str]:
    if provider == "gemini":
        payload = _get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200", {}
        )
        return {m["name"].replace("models/", "") for m in payload.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])}
    payload = _get("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})
    return {m["id"] for m in payload.get("data", [])}


def pick_model(provider: str, keys: list[str]) -> str | None:
    """從偏好清單挑第一個服務商現在真的還有的模型。

    每個行程只問一次就記住。問不到（金鑰全失效之類）就回 None，
    讓呼叫端明確知道這條路不通，而不是拿一個不存在的模型去撞 404。
    """
    if provider in _model_cache:
        return _model_cache[provider]

    preference = GEMINI_MODEL_PREFERENCE if provider == "gemini" else GROQ_MODEL_PREFERENCE
    chosen = None

    for key in keys:
        try:
            available = _list_models(provider, key)
        except Exception:
            continue
        chosen = next((m for m in preference if m in available), None)
        if chosen is None and available:
            # 偏好清單全落空，至少挑一個看起來是對話模型的，別直接放棄
            fallback = sorted(m for m in available
                              if not any(x in m for x in ("whisper", "tts", "embed", "guard")))
            chosen = fallback[0] if fallback else None
        if chosen:
            log(f"{provider} 使用模型 {chosen}")
            break

    _model_cache[provider] = chosen
    return chosen


def _gemini_batch(prompt: str, key: str, model: str) -> str:
    payload = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }).encode(),
        {"Content-Type": "application/json"},
        timeout=300,
    )
    return payload["candidates"][0]["content"]["parts"][0]["text"]


def _groq_batch(prompt: str, key: str, model: str) -> str:
    payload = _post(
        "https://api.groq.com/openai/v1/chat/completions",
        json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode(),
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=300,
    )
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


def translate(lines: list[str], target: str = "繁體中文") -> list[str]:
    """分批翻譯。撞到配額就換下一把金鑰，整批都失敗才留空。"""
    gemini_keys = _keys("GEMINI_API_KEYS")
    groq_keys = _keys("GROQ_API_KEYS")

    if not gemini_keys and not groq_keys:
        log("沒有翻譯用的金鑰，跳過翻譯")
        return ["" for _ in lines]

    gemini_model = pick_model("gemini", gemini_keys) if gemini_keys else None
    groq_model = pick_model("groq", groq_keys) if groq_keys else None

    output: list[str] = []
    gemini_cursor = 0

    for start in range(0, len(lines), TRANSLATE_BATCH):
        batch = lines[start:start + TRANSLATE_BATCH]
        prompt = _translate_prompt(batch, target)
        log(f"翻譯 {start + 1}–{start + len(batch)} / {len(lines)}")

        raw = None
        # 失敗原因一定要留下來。之前這裡把例外整個吞掉，
        # 結果 Groq 下架模型後翻譯全斷，卻沒有任何人知道。
        failures: list[str] = []

        if gemini_model:
            for attempt in range(len(gemini_keys)):
                key = gemini_keys[(gemini_cursor + attempt) % len(gemini_keys)]
                try:
                    raw = _gemini_batch(prompt, key, gemini_model)
                    gemini_cursor = (gemini_cursor + attempt) % len(gemini_keys)
                    break
                except urllib.error.HTTPError as exc:
                    failures.append(f"gemini#{attempt + 1} HTTP {exc.code}")
                except Exception as exc:
                    failures.append(f"gemini#{attempt + 1} {type(exc).__name__}")
        else:
            failures.append("gemini 沒有可用模型")

        if raw is None and groq_model:
            for index, key in enumerate(groq_keys, 1):
                try:
                    raw = _groq_batch(prompt, key, groq_model)
                    break
                except urllib.error.HTTPError as exc:
                    failures.append(f"groq#{index} HTTP {exc.code}")
                except Exception as exc:
                    failures.append(f"groq#{index} {type(exc).__name__}")
        elif raw is None:
            failures.append("groq 沒有可用模型")

        if raw is None:
            # 只印前幾個，同一種錯誤重複幾十次沒有意義
            unique = list(dict.fromkeys(failures))[:4]
            log(f"這批翻譯全部失敗（{'；'.join(unique)}），留空")
            output.extend("" for _ in batch)
            continue

        try:
            parsed = json.loads(raw)
            output.extend(str(parsed.get(str(i), "")) for i in range(len(batch)))
        except json.JSONDecodeError:
            log("翻譯回應不是合法 JSON，這批留空")
            output.extend("" for _ in batch)

    return output
