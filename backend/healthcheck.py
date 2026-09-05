#!/usr/bin/env python3
"""每日健康檢查：只在有事的時候吵你。

    python backend/healthcheck.py             # 印出來看
    python backend/healthcheck.py --notify    # 有事才推 Discord

檢查三件事：

1. **憑證快到期** —— 免費 Apple 帳號的憑證 7 天就過期，過期得接電腦重裝。
   用該裝置第一筆啟動紀錄推算還剩幾天。
2. **卡住的任務** —— 轉錄排隊或跑太久沒結束，多半是 workflow 掛了。
3. **SideStore 修好沒** —— AltSign 的 PR 合併後就能無線續簽，
   不必再接線，所以值得每天問一次。

沒事就安靜結束。每天都收到「一切正常」等於沒人會看。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from supabase_client import Supabase  # noqa: E402

# 免費 Apple 開發者帳號簽出來的憑證只能用 7 天
FREE_CERT_DAYS = 7
CERT_WARN_DAYS = 3

# 排隊超過這麼久還沒開工，或跑這麼久還沒結束，就是卡住了
QUEUED_STUCK_MINUTES = 30
RUNNING_STUCK_MINUTES = 45

# 無線續簽卡在這個 PR。合併之後就不必接電腦重裝了。
ALTSIGN_PR = "https://api.github.com/repos/rileytestut/AltSign/pulls/47"


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_certificate(db: Supabase) -> list[str]:
    """憑證快到期就提醒。每台裝置各算各的。"""
    rows = db.select(
        "kikitori_logs",
        "select=device_id,created_at,app_version&order=created_at.desc&limit=1000",
    )
    if not rows:
        return []

    first_seen: dict[str, str] = {}
    version: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for row in rows:
        device = row["device_id"]
        created = row["created_at"]
        if device not in last_seen:
            last_seen[device] = created
            version[device] = row.get("app_version") or "?"
        # 由新到舊掃，最後留下的就是最早那筆
        first_seen[device] = created

    now = datetime.now(timezone.utc)
    issues = []
    for device, first in first_seen.items():
        # 兩週沒動靜的裝置就不用管了，多半已經不在用
        if now - parse_time(last_seen[device]) > timedelta(days=14):
            continue
        days_left = FREE_CERT_DAYS - (now - parse_time(first)).days
        if days_left <= CERT_WARN_DAYS:
            state = "已經過期" if days_left <= 0 else f"剩 {days_left} 天"
            issues.append(
                f"憑證{state}：裝置 {device[:8]}（版本 {version[device]}）"
                "，要接電腦用 iloader 重裝"
            )
    return issues


def check_stuck_jobs(db: Supabase) -> list[str]:
    """排隊太久或跑太久的轉錄任務。"""
    now = datetime.now(timezone.utc)
    issues = []

    for status, minutes in (("queued", QUEUED_STUCK_MINUTES),
                            ("running", RUNNING_STUCK_MINUTES)):
        # 用 Z 結尾而不是 +00:00 —— 「+」在 query string 裡會被當成空格
        cutoff = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = db.select(
            "kikitori_jobs",
            f"select=show_name,episode_title,stage,created_at"
            f"&status=eq.{status}&created_at=lt.{cutoff}&limit=10",
        )
        for row in rows:
            age = int((now - parse_time(row["created_at"])).total_seconds() // 60)
            where = f"，卡在「{row['stage']}」" if row.get("stage") else ""
            issues.append(
                f"任務卡住 {age} 分鐘（{status}{where}）："
                f"{row.get('show_name', '?')} / {(row.get('episode_title') or '?')[:40]}"
            )
    return issues


def check_sidestore() -> list[str]:
    """AltSign 的 PR 合併了就能無線續簽。查不到就當作沒消息。"""
    try:
        request = urllib.request.Request(
            ALTSIGN_PR,
            headers={"User-Agent": "Kikitori-healthcheck", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except Exception:
        return []

    if data.get("merged"):
        return ["AltSign PR #47 已經合併了 —— SideStore 的無線續簽可能修好了，"
                "值得試試看，成功的話就不用再接電腦"]
    return []


def notify(lines: list[str]) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK", "")
    if not webhook:
        print("沒有 DISCORD_WEBHOOK，只印出來")
        return

    payload = {
        "embeds": [{
            "title": "⚠️ Kikitori 每日健康檢查",
            "description": "\n".join("• " + line for line in lines),
            "color": 15105570,
        }]
    }
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()
    print("已推 Discord")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true", help="有事才推 Discord")
    args = parser.parse_args()

    db = Supabase()
    issues = check_certificate(db) + check_stuck_jobs(db) + check_sidestore()

    if not issues:
        print("一切正常，沒有要提醒的事")
        return 0

    for line in issues:
        print("• " + line)

    if args.notify:
        notify(issues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
