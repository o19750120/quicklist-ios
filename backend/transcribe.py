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
import trace as trace_module  # noqa: E402
import verify  # noqa: E402
import vocab  # noqa: E402
from segment import refine_word_boundaries, resegment, stats  # noqa: E402
from supabase_client import Supabase  # noqa: E402

log = providers.log


def build(show_name: str, episode_title: str, duration_ms: int | None,
          language: str, translate_to: str,
          on_stage=None, trace=None) -> tuple[dict, list[dict], str, dict]:
    # 每個階段都回報一次，App 那頭排隊畫面才看得到進度，不會只有「排隊中」三個字。
    # 同時記進 trace —— stage 原本每次覆蓋前一個，跑完只剩「完成」兩個字，
    # 事後問「這集為什麼花了 23 分鐘」完全答不出來。
    def stage(text: str) -> None:
        if trace:
            trace.stage(text)
        if on_stage:
            on_stage(text)

    stage("尋找音檔")
    match = find_episode(show_name, episode_title, duration_ms)
    if not match:
        raise RuntimeError(f"在公開 RSS 找不到這一集：{show_name} / {episode_title}")

    show, episode = match
    log(f"對到 RSS：{show.name} / {episode.title}（相似度 {episode.match_score:.2f}）")

    # 先量音檔實際長度，轉錄結果要拿它驗證涵蓋範圍。
    # ffprobe 讀網址就能拿到，不必先下載整個檔案。
    try:
        audio_seconds = verify.probe_duration(episode.audio_url)
        log(f"音檔長度 {audio_seconds/60:.1f} 分")
        if trace:
            trace.fact(audio_seconds=round(audio_seconds, 1))
    except Exception as exc:
        audio_seconds = None
        log(f"量不到音檔長度（{exc}），這次跳過涵蓋檢查")

    stage("轉錄中")
    words, model = providers.transcribe(
        episode.audio_url, language, audio_seconds,
        on_fallback=(trace.fallback if trace else None))
    if trace:
        trace.model("轉錄", model)

    stage("斷句")
    # 先用形態素解析找出日文真正的詞邊界。轉錄引擎給的「詞」不是語素 ——
    # 同一段音檔可能切成「皆さん」或「皆」+「さん」——
    # 直接拿它斷句會切出「どんな方」「法が」這種讀起來像亂碼的句子。
    words = refine_word_boundaries(words)
    lines = resegment(words)
    summary = stats(lines)
    log(f"斷句完成：{summary['count']} 句，平均 {summary['avg_seconds']} 秒、"
        f"{summary['avg_chars']} 字，最長 {summary['max_seconds']} 秒")

    # 語音辨識在沒有語音的地方會編造內容，最常見的形式是同一句重複很多次。
    # 那種東西寫進逐字稿後看起來像資料不像錯誤，所以主動找出來。
    for issue in verify.detect_hallucination(lines):
        log(f"疑似幻覺：{issue}")

    # 上面那道只看得出「文字重複」。幻覺還有一種更安靜的形式：
    # 在音樂或長靜默處生出**看起來完全正常**的句子，不重複、也通順，
    # 光看文字發現不了。要抓那種只能回去看音檔有沒有聲音。
    # ffmpeg 串流讀網址即可，11 分鐘節目 1 秒、40 分鐘 4 秒。
    for issue in verify.detect_silence_hallucination(words, episode.audio_url):
        # 括號開頭的是「檢查跑了、結果正常」的回報，不是問題。
        # 兩種都要印 —— 只印問題的話，分不出檢查通過與檢查沒跑。
        log(f"靜音檢查{issue}" if issue.startswith("（") else f"靜音處有文字：{issue}")

    rows = [line.as_row() for line in lines]

    stage(f"翻譯 {len(rows)} 句")
    translations = providers.translate([row["text"] for row in rows], translate_to)
    if trace:
        # 翻譯實際用了哪個模型只有 available_models 的快取知道
        picked = providers.pick_model("gemini", providers._keys("GEMINI_API_KEYS"))
        trace.model("翻譯", f"gemini/{picked}" if picked else "groq")
    for row, translated in zip(rows, translations):
        row["translation"] = translated

    # 詞表與詞邊界。純本機計算（Sudachi + 離線字典），不打 API，
    # 所以放在最後做也不會拖慢什麼，失敗了也不該讓整集報廢 ——
    # 沒有詞表只是不能點詞查意思，逐字稿本身仍然可用。
    # 變數不要叫 words —— 那個名字上面是詞級時間戳（segment.Word 清單），
    # 蓋掉它的話之後在這行後面加任何一道檢查都會拿到錯的東西。
    stage("建詞表")
    try:
        vocabulary = vocab.build(rows)
        log(f"詞表：{vocab.stats(vocabulary)}")
        if trace and vocab.READING_MODELS_USED:
            trace.model("讀音覆核", list(vocab.READING_MODELS_USED))
    except Exception as exc:
        vocabulary = {}
        log(f"建詞表失敗（{exc}），這集先不附詞表")

    # 這一趟打了哪些 API、用了哪幾把金鑰、哪些失敗、為什麼失敗。
    # 沒有這個的話「為什麼換手」只能用猜的 —— 已經因此誤判過一次。
    log(providers.call_summary())
    if trace:
        trace.fact(lines=len(rows),
                   vocab_words=len((vocabulary or {}).get("vocab", {})))

    episode_row = {
        "show_name": show.name,
        "episode_title": episode.title,
        "feed_url": show.feed_url,
        "audio_url": episode.audio_url,
        "duration_ms": int((episode.duration_seconds or 0) * 1000) or duration_ms,
        "language": language,
    }
    return episode_row, rows, model, vocabulary


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
        # **先認領再做。** Supabase 的觸發器在任務建立後兩秒就叫醒 CI，
        # 所以同一個任務很容易被兩邊同時撿走 —— 實測本機跑到一半，
        # CI 也在跑同一集，狀態互相覆蓋，而且整集轉了兩次、API 花雙倍。
        #
        # 認領方式是「只有還在 queued 的才能改成 running」。
        # PostgREST 的 PATCH 帶條件時，條件不成立就回空陣列，
        # 那就是「別人已經拿走了」。
        claimed = supabase.update(
            "kikitori_jobs", f"id=eq.{args.job}&status=eq.queued",
            {"status": "running", "stage": "認領"})
        if not claimed:
            rows = supabase.select("kikitori_jobs", f"select=status&id=eq.{args.job}&limit=1")
            state = rows[0]["status"] if rows else "不存在"
            log(f"任務 {args.job[:8]} 已經是 {state}，別人正在處理或已完成，這次跳過")
            return 0
        job = claimed[0]
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

    # 一集從按下按鈕到呈現在 App 之間發生過什麼，全部記下來。
    # 原本這些資訊分散在三個地方而且都留不住（見 trace.py 的說明）。
    run = trace_module.Trace()

    def mark(status: str, stage: str = "", error: str = ""):
        if job and supabase:
            supabase.update("kikitori_jobs", f"id=eq.{job['id']}", {
                "status": status, "stage": stage, "error": error[:1000],
            })

    def save_trace(status: str, error: str = "") -> None:
        """把完整紀錄寫進 kikitori_jobs.diagnostics。

        失敗也要寫 —— 「為什麼失敗」正是最需要查的時候。
        寫紀錄本身失敗不該讓整集報廢，所以吞掉例外但要講出來。
        """
        report = run.finish(status, error)
        log(trace_module.summarise(report))
        if not (job and supabase):
            return
        try:
            supabase.update("kikitori_jobs", f"id=eq.{job['id']}",
                            {"diagnostics": report})
        except Exception as exc:
            log(f"紀錄寫不進資料庫（{exc}），只留在這份日誌裡")

    try:
        # 先確認翻譯服務活著。沒有翻譯的逐字稿對學語言的人沒有用，
        # 而且那種失敗是靜默的 —— 任務顯示成功、通知報完成，
        # 要等使用者打開 App 才發現整份沒有中文。寧可現在就失敗。
        mark("running", "檢查翻譯服務")
        healthy, detail = providers.translation_health()
        if not healthy:
            raise RuntimeError(f"翻譯服務不可用：{detail}")
        log(f"翻譯服務可用：{detail}")

        mark("running", "準備中")
        episode_row, lines, model, vocabulary = build(
            show_name, episode_title, duration_ms, args.language, args.translate_to,
            on_stage=lambda text: mark("running", text), trace=run,
        )

        if args.preview:
            # 本機試跑也要印紀錄 —— 不然開發時看不到自己剛加的東西對不對
            save_trace("preview")
            print(f"\n=== 前 12 句（共 {len(lines)} 句）===")
            for row in lines[:12]:
                start = row["start_ms"] / 1000
                print(f"[{start:7.2f}] {row['text']}")
                if row.get("translation"):
                    print(f"           → {row['translation']}")
            return 0

        mark("running", "寫入資料庫")
        episode_row["spotify_episode_id"] = spotify_id
        saved = supabase.upsert("kikitori_episodes", episode_row, "spotify_episode_id")
        episode_id = saved[0]["id"]

        supabase.upsert("kikitori_transcripts", {
            "episode_id": episode_id,
            "lines": lines,
            "source_model": model,
            "language": args.language,
            "translated_to": "zh-TW",
            "vocab": vocabulary or None,
            # created_at 在 upsert 時不會更新，所以光看它分不出
            # 「這份資料是什麼時候產生的」。重轉之後仍顯示舊時間，
            # 我自己就被誤導過一次（以為重轉沒生效）。
            "updated_at": "now()",
        }, "episode_id")

        mark("done", "完成")
        save_trace("done")
        export_env(KIKITORI_RESULT=f"{len(lines)} 句")
        log(f"已寫入 Supabase，episode_id={episode_id}，共 {len(lines)} 句")
        return 0

    except Exception as exc:
        log(f"失敗：{exc}")
        mark("failed", "", str(exc))
        save_trace("failed", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
