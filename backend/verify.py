"""逐字稿的品質驗證。

順序是固定的，前一項不過就不必看後面：

1. **涵蓋完整** —— 逐字稿的時間範圍必須涵蓋整個音檔。
   只轉到一半卻回報成功，是最糟的失敗：App 上看起來一切正常，
   聽到中途逐字稿就沒了。
2. **沒有大洞** —— 中間不該出現長時間沒有任何詞的空白。
   那通常代表某一段被漏掉了，而不是真的有那麼久的靜默。
3. **內容合理** —— 重複、亂碼這類幻覺的跡象。

前兩項是硬指標，用時間軸就能算，不需要懂日文。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

from segment import Word

# 結尾容忍值。節目最後常有音樂或靜默，逐字稿短一點是正常的。
TAIL_TOLERANCE_SECONDS = 45.0
# 但短太多就不正常了，比例與絕對值同時看
MIN_COVERAGE_RATIO = 0.90
# 中間的空白超過這麼久就可疑
GAP_THRESHOLD_SECONDS = 30.0


@dataclass
class Report:
    audio_seconds: float
    transcript_seconds: float
    coverage_ratio: float
    tail_gap: float
    gaps: list[tuple[float, float]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        head = (f"音檔 {self.audio_seconds/60:.1f} 分，"
                f"逐字稿到 {self.transcript_seconds/60:.1f} 分"
                f"（涵蓋 {self.coverage_ratio*100:.1f}%）")
        if self.ok:
            return head + "　通過"
        return head + "　問題：" + "；".join(self.problems)


def probe_duration(source: str) -> float:
    """量音檔長度。可以直接讀網址，不必先下載整個檔案。"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", source],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe 讀不到長度：{result.stderr.strip()[:200]}")
    return float(result.stdout.strip())


def check(words: list[Word], audio_seconds: float) -> Report:
    """把轉錄結果對著音檔長度驗一遍。"""
    if not words:
        return Report(audio_seconds, 0.0, 0.0, audio_seconds,
                      problems=["逐字稿是空的"])

    ordered = sorted(words, key=lambda w: w.start)
    last_end = max(w.end for w in ordered)
    ratio = last_end / audio_seconds if audio_seconds > 0 else 0.0
    tail_gap = audio_seconds - last_end

    report = Report(
        audio_seconds=audio_seconds,
        transcript_seconds=last_end,
        coverage_ratio=ratio,
        tail_gap=tail_gap,
    )

    if tail_gap > TAIL_TOLERANCE_SECONDS and ratio < MIN_COVERAGE_RATIO:
        report.problems.append(
            f"結尾少了 {tail_gap/60:.1f} 分鐘沒轉到")

    # 中間的空洞。真的靜默半分鐘以上的節目很少見，多半是漏段。
    previous_end = ordered[0].start
    for word in ordered:
        if word.start - previous_end > GAP_THRESHOLD_SECONDS:
            report.gaps.append((previous_end, word.start))
        previous_end = max(previous_end, word.end)

    if report.gaps:
        worst = max(report.gaps, key=lambda g: g[1] - g[0])
        report.problems.append(
            f"中間有 {len(report.gaps)} 段空白，最長的在 "
            f"{worst[0]/60:.1f}–{worst[1]/60:.1f} 分（{(worst[1]-worst[0])/60:.1f} 分鐘）")

    # 開頭太晚也值得注意，可能前面一段被吃掉
    if ordered[0].start > GAP_THRESHOLD_SECONDS:
        report.problems.append(f"開頭 {ordered[0].start/60:.1f} 分鐘沒有內容")

    return report


def as_dict(report: Report) -> dict:
    return {
        "ok": report.ok,
        "audio_seconds": round(report.audio_seconds, 1),
        "transcript_seconds": round(report.transcript_seconds, 1),
        "coverage_ratio": round(report.coverage_ratio, 4),
        "tail_gap_seconds": round(report.tail_gap, 1),
        "gap_count": len(report.gaps),
        "problems": report.problems,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__file__.rsplit("\\", 1)[0]))
    import env  # noqa: E402
    env.load()
    import providers  # noqa: E402
    from find_episode import search_show, parse_feed  # noqa: E402

    show_name = sys.argv[1] if len(sys.argv) > 1 else "大人の日本語"
    show = search_show(show_name)[0]
    episode = parse_feed(show.feed_url)[0]
    print(f"{show.name} / {episode.title[:46]}")

    seconds = probe_duration(episode.audio_url)
    print(f"音檔實際長度：{seconds/60:.2f} 分\n")

    words, model = providers.transcribe(episode.audio_url, "ja")
    report = check(words, seconds)
    print(f"\n[{model}] {report.summary()}")
    print(json.dumps(as_dict(report), ensure_ascii=False, indent=2))
