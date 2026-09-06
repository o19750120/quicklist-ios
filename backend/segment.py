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

# 以下這組數字拿 4 集 1,047 句的實際輸出量過（2026-09）。要改的話先重跑一次，
# 別憑感覺調 —— 它們彼此會互相蓋掉，改一個看起來沒效果其實是被另一個擋住。
#
#            中位   p90   p95   p99   最大
#   秒數      3.4   6.8   7.7   9.2   11.9
#   字數     23.0  45.0  49.0  63.0   84.0
#
# 實際語速中位 6.9 字/秒，所以 48 字大約等於 7 秒 ——
# **真正生效的限制是 MAX_CHARS，MAX_SECONDS 幾乎不會觸發**
# （只有 0.4% 的句子超過 10 秒，超過 48 字的則有 5.3%）。
# 想讓句子短一點要調 MAX_CHARS，調 MAX_SECONDS 不會有感覺。
MAX_SECONDS = 10.0      # 單句最長秒數；防呆用，正常不會碰到
MAX_CHARS = 48          # 單句最長字數；這個才是實際的斷句依據
MIN_SECONDS = 0.6       # 太短的句子併回前一句（2.5% 的句子落在這裡）
SILENCE_BREAK = 1.0     # 詞間靜音超過這個秒數就斷句
MIN_GAP_TO_SPLIT = 0.2  # 沒有標點時，至少要有這麼長的停頓才敢切
                        # （理由見 _best_split_index：切在詞中間比留長句更糟）
#
# 超標卻沒被切開的句子（5.3% 超過 48 字）不是 bug：找不到安全切點時
# _best_split_index 會回 None，寧可留一句長的也不切在詞中間。


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


_tokenizer = None


def _get_tokenizer():
    """形態素解析器。沒裝就回 None，讓呼叫端安靜略過這一步。"""
    global _tokenizer
    if _tokenizer is None:
        try:
            from sudachipy import Dictionary
            _tokenizer = Dictionary(dict="core").create()
        except Exception:
            _tokenizer = False
    return _tokenizer or None


def refine_word_boundaries(words: list[Word]) -> list[Word]:
    """用形態素解析重新切詞。

    轉錄引擎給的「詞」不是語素邊界 —— 同一段音檔，Gemini 這次切成
    「皆さん」、下次切成「皆」+「さん」，Deepgram 則會給出「日」「本語」。
    拿這種邊界當斷句依據，就會切出「どんな方」「法が」這類讀起來像亂碼的句子。

    這裡把文字接起來重新斷詞，時間戳按字元位置對應回去。
    Sudachi 知道日文真正的詞邊界，所以之後的斷句只會切在合理的地方。

    沒裝 sudachipy 就原樣回傳，不影響既有流程。
    """
    tokenizer = _get_tokenizer()
    if not tokenizer or not words:
        return words

    # 每個字元對應到什麼時間。詞內用線性插值，夠準了 ——
    # 反正接下來只拿它決定「切在哪」，不是拿來對嘴。
    char_start: list[float] = []
    char_end: list[float] = []
    char_speaker: list[int | None] = []
    pieces: list[str] = []

    for word in words:
        text = word.text
        if not text:
            continue
        if pieces and _LATIN_END.search(pieces[-1]) and _LATIN_START.match(text):
            pieces.append(" ")
            char_start.append(word.start)
            char_end.append(word.start)
            char_speaker.append(word.speaker)
        pieces.append(text)
        span = max(word.end - word.start, 0.0)
        count = len(text)
        for index in range(count):
            char_start.append(word.start + span * index / count)
            char_end.append(word.start + span * (index + 1) / count)
            char_speaker.append(word.speaker)

    joined = "".join(pieces)
    if len(joined) != len(char_start):
        # 對不起來就別硬做，保持原樣比切壞好
        return words

    # Sudachi 一次最多吃 49149 bytes，日文一個字三個 byte，
    # 所以切成一萬字一段，而且盡量切在標點後面免得拆散句子。
    chunks: list[tuple[int, str]] = []
    position = 0
    limit = 10000
    while position < len(joined):
        stop = min(position + limit, len(joined))
        if stop < len(joined):
            for punctuation in SENTENCE_END + CLAUSE_BREAK:
                found = joined.rfind(punctuation, position + limit // 2, stop)
                if found > 0:
                    stop = found + 1
                    break
        chunks.append((position, joined[position:stop]))
        position = stop

    refined: list[Word] = []
    for base, chunk in chunks:
        cursor = 0
        for token in tokenizer.tokenize(chunk):
            surface = token.surface()
            if not surface:
                continue
            local = chunk.find(surface, cursor)
            if local < 0:
                continue
            cursor = local + len(surface)
            begin = base + local
            finish = begin + len(surface)
            if not surface.strip():
                continue

            # 說話者取這個語素涵蓋範圍內出現最多次的那位
            speakers = [s for s in char_speaker[begin:finish] if s is not None]
            refined.append(Word(
                text=surface,
                start=char_start[begin],
                end=char_end[finish - 1],
                speaker=max(set(speakers), key=speakers.count) if speakers else None,
            ))

    return refined or words


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
