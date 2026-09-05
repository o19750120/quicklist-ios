"""把一集 podcast 轉成帶時間軸的逐字稿與翻譯，寫進 Supabase。

流程：
    Spotify 給的節目名 + 集名
      → iTunes 找公開 RSS → 比對出該集的音檔
      → 下載
      → ffmpeg 壓成 16kHz 單聲道（Whisper 只吃 16kHz，體積砍到十分之一）
      → 依實際長度切段（Groq 單檔上限 25MB，三小時的節目一定要切）
      → 每段送 Groq Whisper，取回逐句時間軸
      → 各段時間軸加上累積偏移後合併
      → 分批翻譯
      → 寫回 Supabase

用法：
    python backend/transcribe.py --job <job_id>
    python backend/transcribe.py --show "節目名" --episode "集名" [--spotify-id xxx]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from find_episode import find_episode  # noqa: E402
from supabase_client import Supabase  # noqa: E402

GROQ_ROOT = "https://api.groq.com/openai/v1"
WHISPER_MODEL = "whisper-large-v3"
TRANSLATE_MODEL = "llama-3.3-70b-versatile"

# 每段的目標長度。16kHz 單聲道 48kbps 之下，15 分鐘約 5.4MB，
# 離 Groq 的 25MB 上限有很大餘裕。
SEGMENT_SECONDS = 900
TRANSLATE_BATCH = 40

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Kikitori/0.1"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# 音檔處理
# ---------------------------------------------------------------------------

def download(url: str, target: Path) -> Path:
    log(f"下載音檔 {url[:80]}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as response, open(target, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    log(f"下載完成 {target.stat().st_size / 1048576:.1f} MB")
    return target


def compress(source: Path, target: Path) -> Path:
    """壓成 16kHz 單聲道 48kbps。Whisper 內部本來就重採樣到 16kHz，辨識率不受影響。"""
    log("壓縮成 16kHz 單聲道")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(source), "-ac", "1", "-ar", "16000",
         "-c:a", "libmp3lame", "-b:a", "48k", str(target)],
        check=True,
    )
    log(f"壓縮完成 {target.stat().st_size / 1048576:.1f} MB")
    return target


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def split(source: Path, workdir: Path) -> list[tuple[Path, float]]:
    """切段，並回傳每段的實際長度。

    回傳實際長度而不是用固定值推算，是因為 ffmpeg 會在音框邊界切，
    每段不會剛好等於 SEGMENT_SECONDS，差幾百毫秒累積下來會讓後半整個歪掉。
    """
    total = probe_duration(source)
    if total <= SEGMENT_SECONDS:
        return [(source, total)]

    log(f"總長 {total / 60:.1f} 分，切成每段 {SEGMENT_SECONDS // 60} 分")
    pattern = workdir / "part_%03d.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(source), "-f", "segment",
         "-segment_time", str(SEGMENT_SECONDS),
         "-reset_timestamps", "1", "-c", "copy", str(pattern)],
        check=True,
    )
    parts = sorted(workdir.glob("part_*.mp3"))
    log(f"切成 {len(parts)} 段")
    return [(part, probe_duration(part)) for part in parts]


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

def _multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = "----KikitoriBoundary7MA4YWxkTrZu0gW"
    buffer = bytearray()
    for name, value in fields.items():
        buffer += f"--{boundary}\r\n".encode()
        buffer += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        buffer += f"{value}\r\n".encode()

    buffer += f"--{boundary}\r\n".encode()
    buffer += (
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{file_path.name}"\r\n'
    ).encode()
    buffer += b"Content-Type: audio/mpeg\r\n\r\n"
    buffer += file_path.read_bytes()
    buffer += f"\r\n--{boundary}--\r\n".encode()
    return bytes(buffer), f"multipart/form-data; boundary={boundary}"


def groq_transcribe(path: Path, language: str, api_key: str) -> list[dict]:
    """把一段音檔送去 Whisper，回傳逐句（含時間軸，單位秒）。"""
    fields = {
        "model": WHISPER_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    if language:
        fields["language"] = language

    body, content_type = _multipart(fields, "file", path)
    request = urllib.request.Request(
        f"{GROQ_ROOT}/audio/transcriptions",
        method="POST",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = json.loads(response.read())
    return payload.get("segments", [])


def groq_chat(messages: list[dict], api_key: str, temperature: float = 0.2) -> str:
    request = urllib.request.Request(
        f"{GROQ_ROOT}/chat/completions",
        method="POST",
        data=json.dumps({
            "model": TRANSLATE_MODEL,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.loads(response.read())
    return payload["choices"][0]["message"]["content"]


def translate(lines: list[str], api_key: str, target: str = "繁體中文") -> list[str]:
    """分批翻譯，強制輸出與輸入等長的陣列，確保逐句對得起來。"""
    output: list[str] = []

    for start in range(0, len(lines), TRANSLATE_BATCH):
        batch = lines[start:start + TRANSLATE_BATCH]
        numbered = {str(i): text for i, text in enumerate(batch)}
        log(f"翻譯 {start + 1}–{start + len(batch)} / {len(lines)}")

        prompt = (
            f"你是逐句字幕翻譯。把下面 JSON 每個值翻成{target}。\n"
            "規則：\n"
            "1. 鍵完全保留，數量與順序不變。\n"
            "2. 一句對一句，不要合併或拆開，不要加註解。\n"
            "3. 這是口語對話，翻得自然，保留語氣。\n"
            "4. 只輸出 JSON 物件。\n\n"
            + json.dumps(numbered, ensure_ascii=False)
        )

        try:
            raw = groq_chat([{"role": "user", "content": prompt}], api_key)
            parsed = json.loads(raw)
            output.extend(parsed.get(str(i), "") for i in range(len(batch)))
        except Exception as exc:  # 單批失敗不要毀掉整份逐字稿
            log(f"這批翻譯失敗（{exc}），留空")
            output.extend("" for _ in batch)

    return output


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_transcript(show_name: str, episode_title: str, duration_ms: int | None,
                     language: str, api_key: str) -> tuple[dict, list[dict]]:
    match = find_episode(show_name, episode_title, duration_ms)
    if not match:
        raise RuntimeError(f"在公開 RSS 找不到這一集：{show_name} / {episode_title}")

    show, episode = match
    log(f"對到 RSS：{show.name} / {episode.title}（相似度 {episode.match_score:.2f}）")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        raw_audio = download(episode.audio_url, workdir / "raw_audio")
        compressed = compress(raw_audio, workdir / "compressed.mp3")
        raw_audio.unlink(missing_ok=True)

        parts = split(compressed, workdir)

        all_segments: list[dict] = []
        offset_seconds = 0.0
        for index, (part, part_duration) in enumerate(parts, 1):
            log(f"轉錄第 {index}/{len(parts)} 段（{part.stat().st_size / 1048576:.1f} MB）")
            for segment in groq_transcribe(part, language, api_key):
                text = (segment.get("text") or "").strip()
                if not text:
                    continue
                all_segments.append({
                    "start_ms": int((segment["start"] + offset_seconds) * 1000),
                    "end_ms": int((segment["end"] + offset_seconds) * 1000),
                    "text": text,
                })
            offset_seconds += part_duration

    log(f"轉錄完成，共 {len(all_segments)} 句")

    translations = translate([s["text"] for s in all_segments], api_key)
    for segment, translated in zip(all_segments, translations):
        segment["translation"] = translated

    episode_row = {
        "show_name": show.name,
        "episode_title": episode.title,
        "feed_url": show.feed_url,
        "audio_url": episode.audio_url,
        "duration_ms": int((episode.duration_seconds or 0) * 1000) or duration_ms,
        "language": language,
    }
    return episode_row, all_segments


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", help="Supabase 裡的 job id")
    parser.add_argument("--show")
    parser.add_argument("--episode")
    parser.add_argument("--spotify-id")
    parser.add_argument("--duration-ms", type=int)
    parser.add_argument("--language", default="ja")
    parser.add_argument("--dry-run", action="store_true", help="只找音檔不轉錄")
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY", "")
    # dry-run 只是查有沒有音檔，不需要資料庫
    needs_db = bool(args.job) or not args.dry_run
    supabase = Supabase() if needs_db else None

    job = None
    if args.job:
        rows = supabase.select("kikitori_jobs", f"id=eq.{args.job}&limit=1")
        if not rows:
            log(f"找不到任務 {args.job}")
            return 1
        job = rows[0]
        show_name = job["show_name"]
        episode_title = job["episode_title"]
        spotify_id = job["spotify_episode_id"]
        duration_ms = job.get("duration_ms")
    else:
        if not (args.show and args.episode):
            log("需要 --job，或同時給 --show 與 --episode")
            return 1
        show_name = args.show
        episode_title = args.episode
        spotify_id = args.spotify_id or f"manual:{show_name}:{episode_title}"
        duration_ms = args.duration_ms

    if args.dry_run:
        match = find_episode(show_name, episode_title, duration_ms)
        print(json.dumps({
            "found": bool(match),
            "show": match[0].name if match else None,
            "episode": match[1].title if match else None,
            "audio_url": match[1].audio_url if match else None,
            "score": round(match[1].match_score, 3) if match else None,
        }, ensure_ascii=False, indent=2))
        return 0 if match else 1

    if not api_key:
        log("沒有 GROQ_API_KEY")
        return 1

    def mark(status: str, stage: str = "", error: str = ""):
        if job:
            supabase.update("kikitori_jobs", f"id=eq.{job['id']}", {
                "status": status, "stage": stage, "error": error[:1000],
                "updated_at": "now()",
            })

    try:
        mark("running", "尋找音檔")
        episode_row, lines = build_transcript(
            show_name, episode_title, duration_ms, args.language, api_key
        )

        episode_row["spotify_episode_id"] = spotify_id
        saved = supabase.upsert("kikitori_episodes", episode_row, "spotify_episode_id")
        episode_id = saved[0]["id"]

        supabase.upsert("kikitori_transcripts", {
            "episode_id": episode_id,
            "lines": lines,
            "source_model": WHISPER_MODEL,
            "language": args.language,
            "translated_to": "zh-TW",
        }, "episode_id")

        mark("done", "完成")
        log(f"已寫入 Supabase，episode_id={episode_id}，共 {len(lines)} 句")
        return 0

    except Exception as exc:
        log(f"失敗：{exc}")
        mark("failed", "", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
