"""一次看完 Kikitori 的所有狀況。

    python backend/status.py            # 總覽
    python backend/status.py --logs 40  # 多看一點 App 紀錄
    python backend/status.py --errors   # 只看錯誤

包含：App 回報的執行紀錄、轉錄任務、逐字稿存量、CI 最近狀態，
以及免費憑證還剩幾天（用該裝置第一筆啟動紀錄推算）。

只用專案層級的 service key（.env.local 裡的 SUPABASE_SERVICE_KEY），
不需要帳號層級的管理權杖 —— 那把能碰帳號底下所有專案，
不該為了看個狀態就散佈到每一台開發機器上。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import env  # noqa: E402
from supabase_client import Supabase  # noqa: E402

FREE_CERT_DAYS = 7


def section(title: str) -> None:
    print(f"\n{'─' * 66}\n{title}\n{'─' * 66}")


def parse_time(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def relative(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        moment = parse_time(iso)
    except ValueError:
        return iso[:19]
    seconds = int((datetime.now(timezone.utc) - moment).total_seconds())
    if seconds < 60:
        return f"{seconds} 秒前"
    if seconds < 3600:
        return f"{seconds // 60} 分鐘前"
    if seconds < 86400:
        return f"{seconds // 3600} 小時前"
    return f"{seconds // 86400} 天前"


def show_jobs(db: Supabase) -> None:
    section("轉錄任務")
    rows = db.select(
        "kikitori_jobs",
        "select=show_name,episode_title,status,stage,error,created_at"
        "&order=created_at.desc&limit=8",
    )
    if not rows:
        print("  （沒有任務）")
        return
    for r in rows:
        mark = {"done": "✓", "failed": "✗", "running": "…", "queued": "·"}.get(r["status"], "?")
        stage = f" / {r['stage']}" if r.get("stage") else ""
        print(f"  {mark} {r['status']:<8}{stage:<14} {relative(r['created_at']):<10} "
              f"{r['show_name'][:16]:<18} {r['episode_title'][:28]}")
        if r.get("error"):
            print(f"      錯誤：{r['error'][:100]}")


def show_transcripts(db: Supabase) -> None:
    section("已完成的逐字稿")
    rows = db.select(
        "kikitori_transcripts",
        "select=line_count,source_model,created_at,"
        "kikitori_episodes(show_name,episode_title)"
        "&order=created_at.desc&limit=10",
    )
    if not rows:
        print("  （還沒有）")
        return
    for r in rows:
        episode = r.get("kikitori_episodes") or {}
        if isinstance(episode, list):
            episode = episode[0] if episode else {}
        print(f"  {r.get('line_count') or 0:>4} 句  {relative(r['created_at']):<10} "
              f"{(episode.get('show_name') or '')[:16]:<18} "
              f"{(episode.get('episode_title') or '')[:30]}")


def show_logs(db: Supabase, limit: int, errors_only: bool) -> None:
    section("App 回報的紀錄" + ("（只看錯誤）" if errors_only else ""))
    query = f"select=level,category,message,created_at&order=created_at.desc&limit={limit}"
    if errors_only:
        query += "&level=eq.error"
    rows = db.select("kikitori_logs", query)
    if not rows:
        print("  （還沒收到）")
        return
    for r in rows:
        mark = {"error": "✗", "warn": "!", "info": "·"}.get(r["level"], "·")
        print(f"  {mark} {relative(r['created_at']):<10} "
              f"[{r.get('category') or '-'}] {r['message'][:70]}")


def show_devices(db: Supabase) -> None:
    section("憑證與裝置")
    rows = db.select(
        "kikitori_logs",
        "select=device_id,app_version,created_at&order=created_at.desc&limit=1000",
    )
    if not rows:
        print("  （還沒有裝置回報過）")
        return

    # 由新到舊掃過一遍：第一次遇到的是最後活動，最後留下的是最早那筆
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    version: dict[str, str] = {}
    events: dict[str, int] = {}

    for r in rows:
        device = r["device_id"]
        if device not in last_seen:
            last_seen[device] = r["created_at"]
            version[device] = r.get("app_version") or "?"
        first_seen[device] = r["created_at"]
        events[device] = events.get(device, 0) + 1

    now = datetime.now(timezone.utc)
    for device in sorted(last_seen, key=lambda d: last_seen[d], reverse=True)[:4]:
        days_left = FREE_CERT_DAYS - (now - parse_time(first_seen[device])).days
        warning = "  ← 快到期了，記得重新匯入" if days_left <= 2 else ""
        print(f"  裝置 {device[:8]}  版本 {version[device]}  事件 {events[device]} 筆")
        print(f"    首次啟動 {relative(first_seen[device])}，"
              f"最後活動 {relative(last_seen[device])}")
        print(f"    憑證推估還剩 {max(0, days_left)} 天{warning}")


def show_ci() -> None:
    section("CI 最近狀態")
    try:
        raw = subprocess.run(
            ["gh", "run", "list", "--limit", "6", "--json",
             "name,status,conclusion,createdAt,displayTitle"],
            capture_output=True, text=True, timeout=30, cwd=Path(__file__).parent.parent,
        ).stdout
        runs = json.loads(raw)
    except Exception as exc:
        print(f"  查不到（{exc}）")
        return

    for run in runs:
        mark = {"success": "✓", "failure": "✗"}.get(run.get("conclusion"), "…")
        print(f"  {mark} {run['name'][:20]:<22} {relative(run['createdAt']):<10} "
              f"{run['displayTitle'][:36]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=int, default=15)
    parser.add_argument("--errors", action="store_true")
    args = parser.parse_args()

    env.require("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
    db = Supabase()

    show_jobs(db)
    show_transcripts(db)
    show_logs(db, args.logs, args.errors)
    show_devices(db)
    show_ci()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
