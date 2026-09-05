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
# 超過這麼久的空白就記錄下來，但單獨一段不代表有問題 ——
# 節目中間本來就有音樂與停頓。
GAP_THRESHOLD_SECONDS = 30.0
# 真正的判定看兩件事：空白總共佔多少，或有沒有單一一段長到不合理。
# 這組數字是實測校準的：雙語節目漏掉英文段落時空白佔 35.8%（要抓），
# 正常節目的音樂間隔佔 4%（要放行）。
MAX_GAP_RATIO = 0.15
MAX_SINGLE_GAP_SECONDS = 150.0


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

    # 反過來也要查：時間戳跑到音檔長度之外。
    # 這代表時間軸算錯了（切段偏移累加錯、或引擎回傳了壞值），
    # 逐字稿會停在使用者永遠聽不到的位置。
    # 原本只檢查「比音檔短」，這種「比音檔長」的情況會整個溜過去。
    if last_end > audio_seconds + 5.0:
        report.problems.append(
            f"時間戳超出音檔結尾 {last_end - audio_seconds:.0f} 秒，時間軸算錯了")

    # 中間的空洞。真的靜默半分鐘以上的節目很少見，多半是漏段。
    previous_end = ordered[0].start
    for word in ordered:
        if word.start - previous_end > GAP_THRESHOLD_SECONDS:
            report.gaps.append((previous_end, word.start))
        previous_end = max(previous_end, word.end)

    if report.gaps:
        worst = max(report.gaps, key=lambda g: g[1] - g[0])
        worst_length = worst[1] - worst[0]
        total_gap = sum(b - a for a, b in report.gaps)
        gap_ratio = total_gap / audio_seconds if audio_seconds > 0 else 0.0

        if gap_ratio > MAX_GAP_RATIO or worst_length > MAX_SINGLE_GAP_SECONDS:
            report.problems.append(
                f"中間有 {len(report.gaps)} 段空白共 {total_gap/60:.1f} 分鐘"
                f"（佔 {gap_ratio*100:.0f}%），最長的在 "
                f"{worst[0]/60:.1f}–{worst[1]/60:.1f} 分")

    # 開頭太晚也值得注意，可能前面一段被吃掉
    if ordered[0].start > GAP_THRESHOLD_SECONDS:
        report.problems.append(f"開頭 {ordered[0].start/60:.1f} 分鐘沒有內容")

    report.problems.extend(check_timeline(ordered))

    return report


def check_timeline(words: list[Word]) -> list[str]:
    """檢查時間軸本身合不合理。

    這些徵狀不需要標準答案也不需要懂日文，純粹從數字就看得出來，
    而它們都代表「音軌跟逐字稿沒有對齊」：

    - 時間倒退：後面的詞比前面的還早，通常是切段合併時偏移算錯
    - 單一詞橫跨十幾秒：那個詞的結束時間被錯誤地延伸了
    - 語速離譜：日文正常大約每秒 5～8 個字，差太多表示時間軸整段歪掉
    """
    problems: list[str] = []
    if len(words) < 10:
        return problems

    backwards = sum(1 for a, b in zip(words, words[1:]) if b.start < a.start - 0.05)
    if backwards:
        problems.append(f"有 {backwards} 處時間倒退")

    overlong = [w for w in words if w.end - w.start > 10.0]
    if overlong:
        worst = max(overlong, key=lambda w: w.end - w.start)
        problems.append(
            f"有 {len(overlong)} 個詞橫跨超過 10 秒，"
            f"最長的是「{worst.text[:10]}」{worst.end - worst.start:.0f} 秒")

    span = words[-1].end - words[0].start
    if span > 60:
        chars = sum(len(w.text) for w in words)
        rate = chars / span
        if rate < 1.5 or rate > 20:
            problems.append(f"語速異常：每秒 {rate:.1f} 字（日文口語正常約 5–8）")

    return problems


def detect_hallucination(lines: list) -> list[str]:
    """找出幻覺的典型徵狀。

    語音辨識在沒有語音的地方（音樂、長靜默、雜訊）不會安靜地跳過，
    而是會編造內容 —— 最常見的形式就是把同一句話重複很多次。
    這種東西寫進逐字稿後看起來像正常資料，不像錯誤，所以要主動找出來。

    傳入的是 segment.Line 清單。
    """
    problems: list[str] = []
    if len(lines) < 3:
        return problems

    # 短句重複是正常的口語現象，不是幻覺 ——
    # 對談節目裡「そうですね。」「はい。」「なるほど。」本來就會出現幾十次。
    # 只有長句被整段複製才是模型在編。
    MIN_LENGTH = 12

    longest_run, run_text, current = 1, "", 1
    for previous, line in zip(lines, lines[1:]):
        text = line.text.strip()
        if text == previous.text.strip() and len(text) >= MIN_LENGTH:
            current += 1
            if current > longest_run:
                longest_run, run_text = current, text
        else:
            current = 1

    if longest_run >= 4:
        problems.append(f"同一句連續重複 {longest_run} 次：「{run_text[:24]}」")

    # 整份裡同一個長句反覆出現
    counts: dict[str, int] = {}
    for line in lines:
        key = line.text.strip()
        if len(key) >= MIN_LENGTH:
            counts[key] = counts.get(key, 0) + 1
    if counts:
        text, count = max(counts.items(), key=lambda kv: kv[1])
        if count >= 8 and count / len(lines) > 0.03:
            problems.append(
                f"「{text[:24]}」出現 {count} 次，佔全部 {count/len(lines)*100:.0f}%")

    return problems


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
