"""把詞級時間戳重新組成適合逐句學習的句子。

語音辨識服務給的分句是照靜音切的，常常切在句子中間
（「私はナミコ」「。どう」「ですかね。最近相変わらず」），
一句又可能長達十幾秒。拿來當學習用的字幕很難讀。

這裡改用標點與停頓重新斷句，並限制單句長度。

Deepgram 的日文結果有個特性：句號會黏在**下一個詞**的開頭
（例如 '。どう'），所以判斷斷點要看詞首而不是詞尾。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 句子結束的標點。中日文與半形都收。
SENTENCE_END = "。．.！!？?"
# 次要斷點，句子太長時才用
CLAUSE_BREAK = "、，,"

MAX_SECONDS = 10.0      # 單句最長秒數
MAX_CHARS = 48          # 單句最長字數
MIN_SECONDS = 0.6       # 太短的句子併回前一句
SILENCE_BREAK = 1.0     # 詞間靜音超過這個秒數就斷句
MIN_GAP_TO_SPLIT = 0.2  # 沒有標點時，至少要有這麼長的停頓才敢切


@dataclass
class Word:
    text: str
    start: float
    end: float
    # 說話者編號。轉錄服務有開 diarization 才有值，獨白節目全是 0。
    speaker: int | None = None


@dataclass
class Line:
    text: str
    start: float
    end: float
    speaker: int | None = None

    def as_row(self, offset_ms: int = 0) -> dict:
        row = {
            "start_ms": int(self.start * 1000) + offset_ms,
            "end_ms": int(self.end * 1000) + offset_ms,
            "text": self.text,
        }
        # 只有真的辨識到才寫，獨白節目不必多這個欄位
        if self.speaker is not None:
            row["speaker"] = self.speaker
        return row


def from_deepgram(words: list[dict]) -> list[Word]:
    return [
        Word(
            text=(w.get("punctuated_word") or w.get("word") or ""),
            start=float(w.get("start", 0)),
            end=float(w.get("end", 0)),
        )
        for w in words
        if (w.get("punctuated_word") or w.get("word"))
    ]


_LATIN_END = re.compile(r"[A-Za-z0-9]$")
_LATIN_START = re.compile(r"^[A-Za-z0-9]")


def join_words(words: list[Word]) -> str:
    """把詞接成句子。

    日文詞之間不加空格，但英文要 —— 節目裡混英文時直接串起來會變成
    "thinkitismaybeaonecause" 這種讀不出來的東西。
    只有前後都是拉丁字母或數字時才補空格。
    """
    parts: list[str] = []
    for word in words:
        if parts and _LATIN_END.search(parts[-1]) and _LATIN_START.match(word.text):
            parts.append(" ")
        parts.append(word.text)
    return "".join(parts)


def _flush(buffer: list[Word], lines: list[Line]) -> None:
    if not buffer:
        return
    text = join_words(buffer).strip()
    if not text:
        buffer.clear()
        return

    speakers = [w.speaker for w in buffer if w.speaker is not None]
    dominant = max(set(speakers), key=speakers.count) if speakers else None

    line = Line(text=text, start=buffer[0].start, end=buffer[-1].end, speaker=dominant)

    # 太短的碎片（辨識雜訊、單一語助詞）併進前一句，避免畫面一直跳。
    # 但只有在時間上真的接得起來時才併，不然會把隔了幾十秒的碎片黏過去，
    # 讓那一句的跨度整個爆掉。
    if (
        lines
        and (line.end - line.start) < MIN_SECONDS
        and len(text) <= 4
        and (line.start - lines[-1].end) < SILENCE_BREAK
    ):
        previous = lines[-1]
        lines[-1] = Line(previous.text + text, previous.start, line.end, previous.speaker)
    else:
        lines.append(line)

    buffer.clear()


def _best_split_index(buffer: list[Word]) -> int | None:
    """一段話講太久又沒有標點時，挑一個最不傷語意的位置切開。

    優先在逗號後面切；沒有逗號就找詞與詞之間停最久的地方，
    那通常是換氣點。兩邊都至少要留三個詞，免得切出碎片。
    """
    if len(buffer) < 8:
        return None

    # 兩邊各留五分之一，切點才會落在中段，不會削出碎片
    margin = max(3, len(buffer) // 5)
    candidates = list(range(margin, len(buffer) - margin))
    if not candidates:
        return None

    middle = len(buffer) / 2

    comma_positions = [i for i in candidates if buffer[i - 1].text[-1:] in CLAUSE_BREAK]
    if comma_positions:
        return min(comma_positions, key=lambda i: abs(i - middle))

    # 沒有逗號就找換氣點，而且停頓要夠明顯才切。
    # 語速快的段落詞間根本沒有空隙，硬切會把「どんな方法」
    # 切成「どんな方」「法」，比留一句長的還糟。
    # 寧可讓句子長一點，也不要切在詞中間。
    gap_at = max(candidates, key=lambda i: buffer[i].start - buffer[i - 1].end)
    if buffer[gap_at].start - buffer[gap_at - 1].end >= MIN_GAP_TO_SPLIT:
        return gap_at
    return None


def resegment(words: list[Word]) -> list[Line]:
    """依標點、停頓與長度上限重新斷句。"""
    lines: list[Line] = []
    buffer: list[Word] = []

    for word in words:
        text = word.text

        # 句末標點會黏在下一個詞的開頭（'。どう'），
        # 那個標點在語意上屬於前一句，要先還回去再斷。
        leading = ""
        while text and text[0] in SENTENCE_END:
            leading += text[0]
            text = text[1:]

        if leading and buffer:
            tail = buffer[-1]
            buffer[-1] = Word(tail.text + leading, tail.start, tail.end, tail.speaker)
            _flush(buffer, lines)

        # 逗號同樣黏在下一個詞的開頭，也要還給前一句。
        # 這裡不斷句，只是把逗號歸位，好讓後面找切點時看得到它。
        leading_clause = ""
        while text and text[0] in CLAUSE_BREAK:
            leading_clause += text[0]
            text = text[1:]

        if leading_clause and buffer:
            tail = buffer[-1]
            buffer[-1] = Word(tail.text + leading_clause, tail.start, tail.end, tail.speaker)

        if not text:
            continue

        current = Word(text, word.start, word.end, word.speaker)

        # 明顯的停頓也是自然的斷點
        if buffer and (current.start - buffer[-1].end) >= SILENCE_BREAK:
            _flush(buffer, lines)

        buffer.append(current)

        # 這個詞自己以句號收尾
        if current.text[-1:] in SENTENCE_END:
            _flush(buffer, lines)
            continue

        # 超過長度上限就切，寧可切在逗號或換氣點，也不要留一句 30 秒的
        while True:
            duration = buffer[-1].end - buffer[0].start
            length = sum(len(w.text) for w in buffer)
            if duration < MAX_SECONDS and length < MAX_CHARS:
                break

            split_at = _best_split_index(buffer)
            if split_at is None:
                break

            head, tail = buffer[:split_at], buffer[split_at:]
            _flush(head, lines)
            buffer = tail

    _flush(buffer, lines)
    return lines


def stats(lines: list[Line]) -> dict:
    if not lines:
        return {"count": 0}
    durations = [line.end - line.start for line in lines]
    lengths = [len(line.text) for line in lines]
    return {
        "count": len(lines),
        "avg_seconds": round(sum(durations) / len(durations), 2),
        "max_seconds": round(max(durations), 2),
        "avg_chars": round(sum(lengths) / len(lengths), 1),
        "max_chars": max(lengths),
        "over_15s": sum(1 for d in durations if d > 15),
    }
