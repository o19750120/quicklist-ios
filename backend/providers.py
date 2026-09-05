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

from segment import Word

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Kikitori/0.1"

DEEPGRAM_MODEL = "nova-2"
WHISPER_MODEL = "whisper-large-v3"
GEMINI_MODEL = "gemini-3.5-flash"
GROQ_CHAT_MODEL = "llama-3.3-70b-versatile"

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


def _gemini_batch(prompt: str, key: str) -> str:
    payload = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}",
        json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }).encode(),
        {"Content-Type": "application/json"},
        timeout=300,
    )
    return payload["candidates"][0]["content"]["parts"][0]["text"]


def _groq_batch(prompt: str, key: str) -> str:
    payload = _post(
        "https://api.groq.com/openai/v1/chat/completions",
        json.dumps({
            "model": GROQ_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode(),
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=300,
    )
    return payload["choices"][0]["message"]["content"]


def translate(lines: list[str], target: str = "繁體中文") -> list[str]:
    """分批翻譯。撞到配額就換下一把金鑰，整批都失敗才留空。"""
    gemini_keys = _keys("GEMINI_API_KEYS")
    groq_keys = _keys("GROQ_API_KEYS")

    if not gemini_keys and not groq_keys:
        log("沒有翻譯用的金鑰，跳過翻譯")
        return ["" for _ in lines]

    output: list[str] = []
    gemini_cursor = 0

    for start in range(0, len(lines), TRANSLATE_BATCH):
        batch = lines[start:start + TRANSLATE_BATCH]
        prompt = _translate_prompt(batch, target)
        log(f"翻譯 {start + 1}–{start + len(batch)} / {len(lines)}")

        raw = None
        # 每把 Gemini 金鑰都試一輪，配額滿了就換下一把
        for attempt in range(len(gemini_keys)):
            key = gemini_keys[(gemini_cursor + attempt) % len(gemini_keys)]
            try:
                raw = _gemini_batch(prompt, key)
                gemini_cursor = (gemini_cursor + attempt) % len(gemini_keys)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 403):
                    continue
                log(f"Gemini 錯誤 {exc.code}，換下一把")
            except Exception:
                continue

        if raw is None and groq_keys:
            for key in groq_keys:
                try:
                    raw = _groq_batch(prompt, key)
                    break
                except Exception:
                    continue

        if raw is None:
            log("這批翻譯全部失敗，留空")
            output.extend("" for _ in batch)
            continue

        try:
            parsed = json.loads(raw)
            output.extend(str(parsed.get(str(i), "")) for i in range(len(batch)))
        except json.JSONDecodeError:
            log("翻譯回應不是合法 JSON，這批留空")
            output.extend("" for _ in batch)

    return output
