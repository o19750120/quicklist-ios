"""把一集 podcast 轉成帶時間軸的逐字稿與翻譯，寫進 Supabase。

流程：
    Spotify 給的節目名 + 集名
      → iTunes 找公開 RSS，比對出該集的音檔
      → Deepgram 直接讀那個網址轉錄（失敗才退回 Whisper：下載、壓縮、切段）
      → 依標點與停頓重新斷句成適合學習的長度
      → 分批翻譯
      → 寫回 Supabase

用法：
    python backend/transcribe.py --job <job_id>
    python backend/transcribe.py --show "節目名" --episode "集名"
    python backend/transcribe.py --show "節目名" --episode "集名" --preview   # 不寫資料庫
    python backend/transcribe.py --show "節目名" --episode "集名" --dry-run   # 只找音檔
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import providers  # noqa: E402
from find_episode import find_episode  # noqa: E402
from segment import resegment, stats  # noqa: E402
from supabase_client import Supabase  # noqa: E402

log = providers.log


def build(show_name: str, episode_title: str, duration_ms: int | None,
          language: str, translate_to: str) -> tuple[dict, list[dict], str]:
    match = find_episode(show_name, episode_title, duration_ms)
    if not match:
        raise RuntimeError(f"在公開 RSS 找不到這一集：{show_name} / {episode_title}")

    show, episode = match
    log(f"對到 RSS：{show.name} / {episode.title}（相似度 {episode.match_score:.2f}）")

    words, model = providers.transcribe(episode.audio_url, language)

    lines = resegment(words)
    summary = stats(lines)
    log(f"斷句完成：{summary['count']} 句，平均 {summary['avg_seconds']} 秒、"
        f"{summary['avg_chars']} 字，最長 {summary['max_seconds']} 秒")

    rows = [line.as_row() for line in lines]

    translations = providers.translate([row["text"] for row in rows], translate_to)
    for row, translated in zip(rows, translations):
        row["translation"] = translated

    episode_row = {
        "show_name": show.name,
        "episode_title": episode.title,
        "feed_url": show.feed_url,
        "audio_url": episode.audio_url,
        "duration_ms": int((episode.duration_seconds or 0) * 1000) or duration_ms,
        "language": language,
    }
    return episode_row, rows, model


def export_env(**values) -> None:
    """把處理中的節目資訊丟給 GitHub Actions，通知訊息才有東西可寫。"""
    path = os.environ.get("GITHUB_ENV")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            flat = str(value).replace("\n", " ").replace("\r", " ")[:200]
            handle.write(f"{key}={flat}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", help="Supabase 裡的 job id")
    parser.add_argument("--show")
    parser.add_argument("--episode")
    parser.add_argument("--spotify-id")
    parser.add_argument("--duration-ms", type=int)
    parser.add_argument("--language", default="ja")
    parser.add_argument("--translate-to", default="繁體中文")
    parser.add_argument("--dry-run", action="store_true", help="只確認找不找得到音檔")
    parser.add_argument("--preview", action="store_true", help="實際轉錄但不寫資料庫")
    args = parser.parse_args()

    needs_db = bool(args.job) or not (args.dry_run or args.preview)
    supabase = Supabase() if needs_db else None

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
        job = None
        if not (args.show and args.episode):
            log("需要 --job，或同時給 --show 與 --episode")
            return 1
        show_name = args.show
        episode_title = args.episode
        spotify_id = args.spotify_id or f"manual:{show_name}:{episode_title}"
        duration_ms = args.duration_ms

    export_env(KIKITORI_SHOW=show_name, KIKITORI_EPISODE=episode_title)

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

    def mark(status: str, stage: str = "", error: str = ""):
        if job and supabase:
            supabase.update("kikitori_jobs", f"id=eq.{job['id']}", {
                "status": status, "stage": stage, "error": error[:1000],
            })

    try:
        mark("running", "尋找音檔")
        episode_row, lines, model = build(
            show_name, episode_title, duration_ms, args.language, args.translate_to
        )

        if args.preview:
            print(f"\n=== 前 12 句（共 {len(lines)} 句）===")
            for row in lines[:12]:
                start = row["start_ms"] / 1000
                print(f"[{start:7.2f}] {row['text']}")
                if row.get("translation"):
                    print(f"           → {row['translation']}")
            return 0

        episode_row["spotify_episode_id"] = spotify_id
        saved = supabase.upsert("kikitori_episodes", episode_row, "spotify_episode_id")
        episode_id = saved[0]["id"]

        supabase.upsert("kikitori_transcripts", {
            "episode_id": episode_id,
            "lines": lines,
            "source_model": model,
            "language": args.language,
            "translated_to": "zh-TW",
        }, "episode_id")

        mark("done", "完成")
        export_env(KIKITORI_RESULT=f"{len(lines)} 句")
        log(f"已寫入 Supabase，episode_id={episode_id}，共 {len(lines)} 句")
        return 0

    except Exception as exc:
        log(f"失敗：{exc}")
        mark("failed", "", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
