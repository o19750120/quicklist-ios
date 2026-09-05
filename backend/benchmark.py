"""拿人工校對過的語料，量我們的轉錄到底錯多少。

在這之前所有的品質判斷都是相對的（這家比那家多幾個填充詞），
沒有絕對數字。有了標準答案才知道離「對」還有多遠。

用 TEDxJP-10K：273 場 TEDx 演講，人工校對過字幕與時間軸。
它特別適合這個專案 —— 人工修正的方向是把 YouTube 字幕清掉的填充詞加回去：

    YouTube 原字幕  それについて今日紹介したいと思っています
    人工修正後      それについて今日あのまー紹介したいとあー思ってます

所以它同時能量兩件事：聽錯多少，以及吃掉多少口語。後者正是這支 App 最在意的。

    python backend/benchmark.py --corpus <資料夾> [--limit 80]

資料夾要有 Kaldi 格式的 text / segments 與 wav/。
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import env  # noqa: E402
env.load()

import providers  # noqa: E402
from segment import Word, refine_word_boundaries  # noqa: E402

_PUNCT = re.compile(r"[\s、。，．,.！!？?「」『』（）()・…ー〜~]+")

# 口語標記用詞性判斷，不列清單。
#
# 列清單有兩個問題：列不完（方言、個人口癖無窮無盡），
# 而且分不出同形異用 —— 「あの」在「あの、えーっと」是填充詞，
# 在「あの小さい例」是指示詞（連体詞）。詞性標註分得出來，字串比對分不出來。
_FILLER_POS = ("フィラー", "感動詞")
_ENDING_POS = ("終助詞",)


def normalize(text: str) -> str:
    """比對前抹平無關差異：標點、空白、全半形。不動填充詞，那正是要量的。"""
    return _PUNCT.sub("", unicodedata.normalize("NFKC", text))


def cer(reference: str, hypothesis: str) -> tuple[float, int, int]:
    """字元錯誤率。日文沒有詞界，用字元算比詞錯誤率合理。"""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0, 0, 0
    matcher = SequenceMatcher(None, ref, hyp, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    distance = max(len(ref), len(hyp)) - matched
    return distance / len(ref), distance, len(ref)


def count_spoken_markers(text: str) -> tuple[int, int]:
    """數口語標記：填充詞與語尾助詞，各回一個數字。

    用形態素解析的詞性，不用字串比對 —— 見上面 _FILLER_POS 的說明。
    沒裝 sudachipy 時回 (0, 0)，統計會顯示為零而不是給出錯的數字。
    """
    from segment import _get_tokenizer

    tokenizer = _get_tokenizer()
    if not tokenizer or not text.strip():
        return 0, 0

    fillers = endings = 0
    for token in tokenizer.tokenize(text):
        pos = ",".join(token.part_of_speech())
        if any(tag in pos for tag in _FILLER_POS):
            fillers += 1
        if any(tag in pos for tag in _ENDING_POS):
            endings += 1
    return fillers, endings


def load_corpus(folder: Path) -> list[dict]:
    texts = {}
    for line in io.open(folder / "text", encoding="utf-8"):
        utt, _, content = line.strip().partition(" ")
        if utt:
            texts[utt] = content

    rows = []
    for line in io.open(folder / "segments", encoding="utf-8"):
        parts = line.split()
        if len(parts) != 4:
            continue
        utt, wav, start, end = parts
        if utt in texts:
            rows.append({"utt": utt, "wav": wav, "start": float(start),
                         "end": float(end), "reference": texts[utt]})
    return rows


def transcribe_whole(engine: str, path: Path) -> list[Word]:
    """轉錄整個音檔。

    不逐句切開送 —— 那樣每段只有一兩秒、沒有前後文，
    Deepgram 的 multi 模式會把「周りをみると」聽成 "Vario Mito."。
    實際使用是整集轉錄，評估也要照同樣方式做，
    否則量到的是「短片段辨識」而不是我們真正在用的東西。
    """
    if engine == "gemini":
        keys = providers._keys("GEMINI_API_KEYS")
        last = None
        for key in keys:
            try:
                return providers._gemini_transcribe_clip(path, key)
            except Exception as exc:
                last = exc
                continue
        raise RuntimeError(f"所有金鑰都失敗：{last}")

    if engine == "deepgram":
        import json
        import os
        import urllib.request
        key = os.environ["DEEPGRAM_API_KEY"]
        params = (f"model={providers.DEEPGRAM_MODEL}&language=multi"
                  "&punctuate=true&smart_format=true")
        request = urllib.request.Request(
            f"https://api.deepgram.com/v1/listen?{params}",
            data=path.read_bytes(),
            headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav",
                     "User-Agent": providers.USER_AGENT})
        with urllib.request.urlopen(request, timeout=1800) as response:
            payload = json.loads(response.read())
        raw = payload["results"]["channels"][0]["alternatives"][0].get("words", [])
        return [Word(text=(w.get("punctuated_word") or w.get("word") or ""),
                     start=float(w["start"]), end=float(w["end"]))
                for w in raw if (w.get("punctuated_word") or w.get("word"))]

    raise ValueError(engine)


def slice_words(words: list[Word], start: float, end: float,
                reference: str = "", margin: float = 2.0) -> str:
    """取這段時間對應的轉錄文字。

    不能直接用時間窗切 —— 兩邊的句子邊界本來就不會對齊，
    窗口多抓或少抓幾個字都會灌水到錯誤率上，
    那量到的是「邊界差異」而不是「聽錯」。

    做法是把窗口放寬，再在那段文字裡找出與標準答案最相符的子字串。
    這樣算出來的才是真正的辨識錯誤。
    """
    inside = [w for w in words if w.end > start - margin and w.start < end + margin]
    window = "".join(w.text for w in inside)
    if not reference or not window:
        return window

    target = normalize(reference)
    flat = normalize(window)
    if not target or not flat:
        return window

    # 以標準答案的長度為基準滑動，找相似度最高的位置
    best, best_score = flat, -1.0
    span = len(target)
    step = max(1, span // 8)
    for begin in range(0, max(1, len(flat) - span + 1), step):
        for width in (span, int(span * 1.15) + 1):
            candidate = flat[begin:begin + width]
            if not candidate:
                continue
            score = SequenceMatcher(None, target, candidate, autojunk=False).ratio()
            if score > best_score:
                best, best_score = candidate, score
    return best


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--engines", default="gemini,deepgram")
    args = parser.parse_args()

    folder = Path(args.corpus)
    rows = load_corpus(folder)[:args.limit]
    if not rows:
        print("語料是空的")
        return 1

    total_seconds = sum(r["end"] - r["start"] for r in rows)
    print(f"素材：{len(rows)} 句 / {total_seconds/60:.1f} 分鐘（人工校對過的標準答案）\n")

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    results = {e: {"distance": 0, "length": 0, "fillers": 0, "endings": 0,
                   "samples": []} for e in engines}
    reference_fillers = reference_endings = 0
    for row in rows:
        f, e = count_spoken_markers(row["reference"])
        reference_fillers += f
        reference_endings += e

    by_wav: dict[str, list[dict]] = {}
    for row in rows:
        by_wav.setdefault(row["wav"], []).append(row)

    for wav_name, group in by_wav.items():
        source = folder / "wav" / f"{wav_name}.16k.wav"
        if not source.exists():
            print(f"找不到音檔 {source.name}")
            continue
        print(f"{wav_name}（{len(group)} 句要比對）")

        for engine in engines:
            try:
                words = transcribe_whole(engine, source)
            except Exception as exc:
                print(f"  {engine} 失敗：{exc}")
                continue
            words = refine_word_boundaries(words)

            for row in group:
                hypothesis = slice_words(words, row["start"], row["end"], row["reference"])
                rate, distance, length = cer(row["reference"], hypothesis)
                bucket = results[engine]
                bucket["distance"] += distance
                bucket["length"] += length
                f, e = count_spoken_markers(hypothesis)
                bucket["fillers"] += f
                bucket["endings"] += e
                if len(bucket["samples"]) < 8:
                    bucket["samples"].append((row["reference"], hypothesis, rate))
            print(f"  {engine} 完成（{len(words)} 個語素）")

    print(f"\n{'引擎':<12} {'字元錯誤率':>10} {'填充詞':>18}")
    print("─" * 44)
    print(f"{'標準答案':<12} {'—':>10} {reference_fillers:>10} 個")
    for engine in engines:
        bucket = results[engine]
        if not bucket["length"]:
            print(f"{engine:<12} {'（無資料）':>10}")
            continue
        rate = bucket["distance"] / bucket["length"]
        kept_f = bucket["fillers"] / reference_fillers * 100 if reference_fillers else 0
        kept_e = bucket["endings"] / reference_endings * 100 if reference_endings else 0
        print(f"{engine:<12} {rate*100:>9.1f}% "
              f"{bucket['fillers']:>8} 個（{kept_f:>3.0f}%） "
              f"{bucket['endings']:>8} 個（{kept_e:>3.0f}%）")

    for engine in engines:
        samples = results[engine]["samples"]
        if not samples:
            continue
        print(f"\n=== {engine} 錯最多的幾句 ===")
        for reference, hypothesis, rate in sorted(samples, key=lambda s: -s[2])[:3]:
            print(f"  錯誤率 {rate*100:.0f}%")
            print(f"    標準：{reference[:56]}")
            print(f"    實際：{hypothesis[:56]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
