"""產生「待人工校對」的評測集初稿。

日文 podcast 沒有人工校對的公開逐字稿（掃過 1,873 個 RSS 確認過），
所以標準答案只能自己標。但從零逐字打一小時音檔要花掉一整天。

這支腳本把工作量壓下來：用兩家 ASR 轉同一段音檔，兩邊講法一致的地方
通常就是對的，只有分歧處才需要人耳判斷。實測分歧大約佔兩成，
也就是說人工只需要處理五分之一的內容。

    python backend/make_eval_set.py --show "節目名" --start 0 --minutes 10

產出 JSON，每一句帶：
  - 主稿（Deepgram，填充詞保留得比較好）
  - 對照稿（Whisper）
  - agree：兩家是否一致
  - verified：人工確認過沒有，預設 false

校對時只要看 agree 為 false 的句子，改完把 verified 設成 true。
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import env  # noqa: E402
env.load()

import providers  # noqa: E402
import verify  # noqa: E402
from find_episode import search_show, parse_feed  # noqa: E402
from segment import resegment  # noqa: E402


def clip(audio_url: str, start_seconds: int, minutes: int, target: Path) -> Path:
    """從音檔中間擷取一段。直接讀網址，不必先下載整集。"""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", str(start_seconds), "-t", str(minutes * 60), "-i", audio_url,
         "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", str(target)],
        check=True, timeout=1800,
    )
    return target


import re
import unicodedata

_STRIP = re.compile(r"[\s、。，．,.！!？?「」『』（）()・…ー〜~]+")

# 兩家對數字的寫法不同 —— nova-3 傾向漢數字（二月）、Whisper 用阿拉伯數字（2月）。
# 那不是辨識差異，比對前要抹平，否則同一句話會被判成不一致。
_DIGITS = str.maketrans("〇零一二三四五六七八九", "00123456789")


def normalize(text: str) -> str:
    """比對前先抹平無關的差異：標點、空白、全半形、數字寫法。"""
    text = unicodedata.normalize("NFKC", text).translate(_DIGITS)
    return _STRIP.sub("", text).lower()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def align(primary: list, reference: list) -> list[dict]:
    """把兩家的句子按時間軸配對。

    兩家的斷句粒度差很多 —— Whisper 常把好幾句併成一段。
    所以不能直接比整段文字，那樣會把「斷句不同」誤判成「內容不同」。
    改成看這一句有沒有出現在對方那段裡面：有就算一致，
    沒有才用相似度衡量差多少。
    """
    rows = []
    for line in primary:
        # 時間上多抓一點，因為兩家的邊界不會完全對齊
        overlapping = [
            other for other in reference
            if other.end > line.start - 1.0 and other.start < line.end + 1.0
        ]
        counterpart = "".join(o.text for o in overlapping)

        mine, theirs = normalize(line.text), normalize(counterpart)
        if mine and theirs and mine in theirs:
            score = 1.0
        else:
            score = similarity(mine, theirs) if theirs else 0.0

        rows.append({
            "start_ms": int(line.start * 1000),
            "end_ms": int(line.end * 1000),
            "text": line.text,
            "reference": counterpart,
            "agree": score >= 0.85,
            "similarity": round(score, 3),
            "speaker": line.speaker,
            "verified": False,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", required=True)
    parser.add_argument("--episode", help="留空就取最新一集")
    parser.add_argument("--start", type=int, default=0, help="從第幾秒開始")
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--out", default="eval_set.json")
    args = parser.parse_args()

    shows = search_show(args.show)
    if not shows:
        print(f"找不到節目：{args.show}")
        return 1
    show = shows[0]

    episodes = parse_feed(show.feed_url)
    if args.episode:
        episode = max(episodes, key=lambda e: similarity(args.episode, e.title))
    else:
        episode = episodes[0]

    print(f"{show.name} / {episode.title[:50]}")
    print(f"擷取 {args.start//60} 分起 {args.minutes} 分鐘\n")

    with tempfile.TemporaryDirectory() as tmp:
        path = clip(episode.audio_url, args.start, args.minutes, Path(tmp) / "clip.mp3")
        seconds = verify.probe_duration(path)
        print(f"片段長度 {seconds/60:.1f} 分\n")

        print("第一家（Deepgram）…")
        primary_words = providers.transcribe_deepgram(str(path), "ja") \
            if str(path).startswith("http") else None
        if primary_words is None:
            # 本地檔案要用上傳的方式
            primary_words = _deepgram_file(path)
        primary = resegment(primary_words)

        print("第二家（Whisper）…")
        reference_words = providers.transcribe_groq(path.as_uri(), "ja")
        reference = resegment(reference_words)

    rows = align(primary, reference)
    disputed = [r for r in rows if not r["agree"]]

    payload = {
        "show": show.name,
        "episode": episode.title,
        "audio_url": episode.audio_url,
        "clip_start_seconds": args.start,
        "clip_minutes": args.minutes,
        "primary_engine": f"deepgram:{providers.DEEPGRAM_MODEL}",
        "reference_engine": f"groq:{providers.WHISPER_MODEL}",
        "lines": rows,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    print(f"\n共 {len(rows)} 句，其中 {len(disputed)} 句兩家不一致"
          f"（{len(disputed)/len(rows)*100:.0f}%）")
    print(f"人工只需要看這 {len(disputed)} 句，其餘兩家講法相同。")
    print(f"已寫出 {args.out}")

    print("\n=== 分歧最大的 8 句 ===")
    for row in sorted(disputed, key=lambda r: r["similarity"])[:8]:
        print(f"\n  [{row['start_ms']/1000:6.1f}s] 相似度 {row['similarity']:.2f}")
        print(f"    Deepgram：{row['text'][:64]}")
        print(f"    Whisper ：{row['reference'][:64]}")

    return 0


def _deepgram_file(path: Path):
    """本地檔案版的 Deepgram 呼叫。"""
    import os
    import urllib.request
    from segment import Word

    key = os.environ["DEEPGRAM_API_KEY"]
    params = (f"model={providers.DEEPGRAM_MODEL}&language=multi"
              "&punctuate=true&smart_format=true&utterances=true&diarize=true")
    request = urllib.request.Request(
        f"https://api.deepgram.com/v1/listen?{params}",
        data=path.read_bytes(),
        headers={"Authorization": f"Token {key}", "Content-Type": "audio/mpeg",
                 "User-Agent": providers.USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=1800) as response:
        payload = json.loads(response.read())

    words = payload["results"]["channels"][0]["alternatives"][0].get("words", [])
    return [
        Word(text=(w.get("punctuated_word") or w.get("word") or ""),
             start=float(w["start"]), end=float(w["end"]), speaker=w.get("speaker"))
        for w in words if (w.get("punctuated_word") or w.get("word"))
    ]


if __name__ == "__main__":
    sys.exit(main())
