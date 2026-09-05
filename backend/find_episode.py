"""從 Spotify 給的「節目名 + 集數名」反查公開 RSS 的音檔。

Spotify 只告訴我們正在播什麼，不給音檔。但逐字稿必須從音檔轉出來，
所以要用節目名去 iTunes 找到公開 RSS，再從 RSS 裡比對出同一集。

集名不會完全一樣（Spotify 和 RSS 常有細微差異：全形半形、集數編號、
節目名前綴），所以用正規化後的模糊比對。

單獨執行會跑一輪自我測試：
    python backend/find_episode.py
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Kikitori/0.1"
ITUNES_SEARCH = "https://itunes.apple.com/search"

NAMESPACES = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
}


@dataclass
class Show:
    name: str
    feed_url: str
    artwork_url: str | None = None


@dataclass
class Episode:
    title: str
    audio_url: str
    guid: str | None
    duration_seconds: int | None
    published: str | None
    size_bytes: int | None
    match_score: float = 0.0
    transcript_url: str | None = None


def _fetch(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def normalize(text: str) -> str:
    """把標題壓成可比對的形式。

    NFKC 會把全形英數、半形片假名等統一，這對日文標題特別重要
    （Spotify 常用全形數字，RSS 常用半形）。
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    # 集數編號的各種寫法拆掉，避免 "#12" 和 "第12回" 比不出來
    text = re.sub(r"[#＃]\s*(\d+)", r" \1 ", text)
    text = re.sub(r"(第\s*\d+\s*[回話集話])", r" \1 ", text)
    # 標點與空白一律移除，只留下文字與數字
    text = re.sub(r"[\s\-–—_|｜/／:：、。，,.!！?？'\"“”‘’()（）\[\]【】<>《》]+", "", text)
    return text


def search_show(name: str, limit: int = 5, country: str = "jp") -> list[Show]:
    """用節目名在 iTunes 找 podcast，回傳候選清單（含 RSS 網址）。"""
    query = urllib.parse.urlencode(
        {"term": name, "entity": "podcast", "limit": limit, "country": country}
    )
    payload = json.loads(_fetch(f"{ITUNES_SEARCH}?{query}"))

    shows: list[Show] = []
    for item in payload.get("results", []):
        feed = item.get("feedUrl")
        if not feed:
            continue
        shows.append(
            Show(
                name=item.get("collectionName", ""),
                feed_url=feed,
                artwork_url=item.get("artworkUrl600"),
            )
        )
    return shows


def _duration_to_seconds(raw: str | None) -> int | None:
    """itunes:duration 可能是 '1:02:03'、'62:03' 或純秒數。"""
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    parts = raw.split(":")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    seconds = 0
    for value in numbers:
        seconds = seconds * 60 + value
    return seconds


def parse_feed(feed_url: str) -> list[Episode]:
    """解析 RSS，取出每一集的音檔網址與基本資訊。"""
    root = ET.fromstring(_fetch(feed_url))
    channel = root.find("channel")
    if channel is None:
        return []

    episodes: list[Episode] = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.get("url"):
            continue

        title = (item.findtext("title") or "").strip()
        size = enclosure.get("length")
        transcript = item.find("podcast:transcript", NAMESPACES)

        episodes.append(
            Episode(
                title=title,
                audio_url=enclosure.get("url", ""),
                guid=(item.findtext("guid") or "").strip() or None,
                duration_seconds=_duration_to_seconds(
                    item.findtext("itunes:duration", namespaces=NAMESPACES)
                ),
                published=(item.findtext("pubDate") or "").strip() or None,
                size_bytes=int(size) if size and size.isdigit() else None,
                transcript_url=transcript.get("url") if transcript is not None else None,
            )
        )
    return episodes


def score(spotify_title: str, rss_title: str) -> float:
    """兩個標題的相似度，0～1。"""
    a, b = normalize(spotify_title), normalize(rss_title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # 一方完整包含另一方（例如 RSS 標題多了節目名前綴）算高分
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) < len(b) else (b, a)
        return 0.9 + 0.1 * (len(shorter) / len(longer))
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_episode(
    show_name: str,
    episode_title: str,
    duration_ms: int | None = None,
    min_score: float = 0.6,
) -> tuple[Show, Episode] | None:
    """完整流程：節目名 → RSS → 比對出集數 → 回傳音檔資訊。

    有 duration_ms 時會拿時長當佐證，避免標題相近但其實是不同集。
    """
    for show in search_show(show_name):
        try:
            episodes = parse_feed(show.feed_url)
        except Exception:
            continue
        if not episodes:
            continue

        for episode in episodes:
            episode.match_score = score(episode_title, episode.title)

            # 時長對得上就加分，差很多就扣分（容忍 90 秒，動態廣告會造成差異）
            if duration_ms and episode.duration_seconds:
                delta = abs(episode.duration_seconds - duration_ms / 1000)
                if delta <= 90:
                    episode.match_score = min(1.0, episode.match_score + 0.08)
                elif delta > 600:
                    episode.match_score -= 0.25

        best = max(episodes, key=lambda e: e.match_score)
        if best.match_score >= min_score:
            return show, best

    return None


# --------------------------------------------------------------------------
# 自我測試
# --------------------------------------------------------------------------

def _selftest() -> int:
    cases = [
        ("日本語の森", None),
        ("Nihongo con Teppei", None),
        ("バイリンガルニュース", None),
        ("ゆる言語学ラジオ", None),
    ]

    failures = 0
    for show_name, _ in cases:
        print(f"\n{'=' * 62}\n節目：{show_name}")
        shows = search_show(show_name)
        if not shows:
            print("  ✗ iTunes 找不到這個節目")
            failures += 1
            continue

        show = shows[0]
        print(f"  節目名：{show.name}")
        print(f"  RSS   ：{show.feed_url}")

        try:
            episodes = parse_feed(show.feed_url)
        except Exception as exc:
            print(f"  ✗ RSS 解析失敗：{exc}")
            failures += 1
            continue

        print(f"  集數  ：{len(episodes)} 集")
        if not episodes:
            failures += 1
            continue

        latest = episodes[0]
        size_mb = f"{latest.size_bytes / 1048576:.1f} MB" if latest.size_bytes else "未標示"
        minutes = f"{latest.duration_seconds // 60} 分" if latest.duration_seconds else "未標示"
        print(f"  最新集：{latest.title[:44]}")
        print(f"  音檔  ：{latest.audio_url[:70]}")
        print(f"  長度／大小：{minutes} ／ {size_mb}")
        print(f"  RSS 自帶逐字稿：{'有' if latest.transcript_url else '無'}")

        # 反查測試：拿這一集的標題去找，應該要找回同一集
        found = find_episode(show_name, latest.title, (latest.duration_seconds or 0) * 1000)
        if found and found[1].audio_url == latest.audio_url:
            print(f"  ✓ 反查成功（相似度 {found[1].match_score:.2f}）")
        else:
            print("  ✗ 反查失敗")
            failures += 1

    print(f"\n{'=' * 62}")
    print("全部通過" if failures == 0 else f"有 {failures} 項失敗")
    return failures


if __name__ == "__main__":
    sys.exit(1 if _selftest() else 0)
